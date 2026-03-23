# Sistema de autenticación

## Estructura del módulo

```
reaper/auth/
├── models.py            Modelos de datos puros (AccountProfile, etc.)
├── rotator.py           Algoritmo de scoring y selección
├── account_manager.py   Gestor central: coordina todos los subsistemas
├── login.py             Flujo interactivo con Playwright
└── storage/
    ├── base.py          ABC: BaseAccountStorage
    └── local.py         Implementación en JSON: LocalFileStorage
```

## Modelos de datos

Los tres modelos están documentados en detalle en la [API Reference](../api/auth.md).

**`AccountStatus`** — enum `str` con seis estados operativos. Los estados
`SUSPENDED` y `COOKIE_EXPIRED` son los únicos que excluyen la cuenta del
rotador; el resto son seleccionables con distintos grados de penalización.

| Estado | Seleccionable | Penalización en score |
|---|---|---|
| `active` | Sí | Ninguna |
| `needs_refresh` | Sí | ×0.5 |
| `rate_limited` | Sí | ×0.2 |
| `cookie_expired` | No | — |
| `suspended` | No | — |
| `unknown` | No (sin cookies) | — |

**`AccountActivity`** — dataclass con contadores de éxito/fallo, timestamps
ISO-8601 del último uso, y una cola FIFO de los últimos 20 mensajes de error.
El campo `requests_since_cookie_refresh` se resetea a 0 en cada auto-refresh
de cookies, por lo que en condiciones normales el sistema nunca llega al umbral.

**`AccountProfile`** — perfil completo. El campo `cookies_path` no se persiste
en disco; `LocalFileStorage` lo infiere de la ubicación del archivo al
deserializar, evitando rutas absolutas hardcodeadas.

## Algoritmo de rotación

El `AccountRotator` es **stateless**: recibe el pool completo en cada llamada
y calcula el ranking en el momento, sin estado entre llamadas.

### Fórmula de puntuación

```
score = (success_rate  × 0.6)
      + (lru_score     × 0.3)
      - (cookie_age    × 0.1)
```

- **`success_rate`**: `successful_requests / total_requests`. Cuentas sin historial reciben 1.0.
- **`lru_score`**: antigüedad de uso normalizada 0.0–1.0. La cuenta usada hace más tiempo obtiene 1.0.
- **`cookie_age`**: `requests_since_cookie_refresh / threshold`. Con auto-refresh activo
  este valor se mantiene cercano a 0 en condiciones normales, haciendo que este componente
  tenga impacto mínimo.

Después del score bruto se aplican los multiplicadores por estado.

### Ejemplo con tres cuentas

| Cuenta | success_rate | lru_score | cookie_age | score bruto | ×estado | score final |
|---|---|---|---|---|---|---|
| cuenta_nueva | 1.00 | 1.00 | 0.00 | 0.90 | ×1.0 | **0.90** |
| cuenta_activa | 0.85 | 0.40 | 0.60 | 0.57 | ×1.0 | **0.57** |
| rate_limited | 0.70 | 0.60 | 0.20 | 0.58 | ×0.2 | **0.12** |

## Gestor de cuentas

`AccountManager` coordina storage, rotador y login. Es el único componente
que el resto de la librería necesita para trabajar con cuentas. Su API
completa está en [api/auth.md](../api/auth.md#accountmanager).

### Distinción entre `save()` y `update_activity()`

`BaseAccountStorage` expone estas dos operaciones por separado.
`update_activity()` se llama en cada petición (alta frecuencia) y los
backends de BD pueden implementarla como un `UPDATE` parcial.
`save()` es para cambios de estado completos, que ocurren raramente.

## Refresco automático de cookies

### Cómo funciona

Después de cada navegación autenticada exitosa, el servidor de Facebook o
Instagram emite cabeceras `Set-Cookie` que actualizan los tokens de seguridad
de corta vida y extienden el TTL de las cookies de sesión. Este comportamiento
es idéntico al de cualquier navegador de usuario real.

Playwright acumula estos cambios en el contexto durante la sesión. Tras
finalizar `page.goto()`, `context.cookies()` ya devuelve las versiones
actualizadas que el servidor emitió. `ContentFetcher.fetch()` lee este
estado post-navegación y lo expone en `FetchResult.updated_cookies`.

### Flujo de auto-refresh en `BaseScraper._fetch_with_account()`

```
fetch con cookies_originales
    → Playwright navega la URL
    → servidor responde con Set-Cookie actualizados
    → context.cookies() → updated_cookies

_cookies_differ(cookies_originales, updated_cookies)?
    ├─ NO → nada que hacer, las cookies no cambiaron
    └─ SÍ → manager.import_cookies(account_id, updated_cookies)
                ├─ sobreescribe cookies.json en disco
                ├─ status → ACTIVE (si estaba NEEDS_REFRESH)
                └─ requests_since_cookie_refresh → 0
```

### `_cookies_differ()` — qué se compara

Solo se comparan los campos que afectan a la validez de la sesión:

```python
def _cookies_differ(original, updated) -> bool:
    original_map = {c["name"]: (c.get("value"), c.get("expires")) for c in original}
    updated_map  = {c["name"]: (c.get("value"), c.get("expires")) for c in updated}
    return original_map != updated_map
```

Se ignoran `httpOnly`, `sameSite`, `path` y otros campos de metadatos que
no afectan a la autenticación. Dos casos considerados como cambio:

- **Token rotado**: el `value` de una cookie cambió (e.g. `xs` de Facebook
  que rota frecuentemente).
- **TTL extendido**: el `expires` aumentó, lo que extiende la vida de la sesión.

### Rol del `cookie_refresh_threshold` con auto-refresh activo

Con el auto-refresh funcionando correctamente, `requests_since_cookie_refresh`
se resetea a 0 después de cada petición exitosa donde el servidor devuelva
cookies actualizadas. El contador nunca alcanza el threshold en condiciones normales.

El threshold actúa como **safety net** para casos degradados:

| Escenario | Comportamiento |
|---|---|
| Servidor devuelve cookies actualizadas | Auto-refresh → contador a 0, threshold nunca se alcanza |
| Servidor no devuelve `Set-Cookie` (raro) | Contador sube, threshold emite WARNING tras N peticiones |
| `context.cookies()` falla (error de red) | Se loggea como DEBUG, auto-refresh omitido, contador sube |
| Plataforma invalida la sesión remotamente | Cookies dejan de funcionar → `COOKIE_EXPIRED` o fallos → login manual |

## Flujo de login interactivo

### Por qué es manual

Facebook e Instagram detectan con alta precisión los intentos de login
automatizado. El enfoque manual garantiza que las verificaciones 2FA,
CAPTCHAs y comprobaciones de dispositivo las completa un humano usando
el navegador de forma normal.

### Proceso técnico

1. `async_playwright()` lanza Firefox con `headless=False`.
2. Se crea un contexto estándar (viewport 1280×800, locale `en-US`).
3. `page.goto(LOGIN_URL)` navega a la página de login.
4. `_wait_for_login_success()` sondea `page.url` cada segundo buscando los patrones de URL que indican feed post-login.
5. Al detectar el login, `context.cookies()` extrae todas las cookies de sesión.
6. Las cookies se pasan a `AccountManager.import_cookies()` o `add_account()`.
7. El navegador se cierra con `browser.close()`.

### Patrones de URL detectados

```python
SUCCESS_URL_PATTERNS = {
    "facebook": [
        "facebook.com/?", "facebook.com/home",
        "facebook.com/me", "facebook.com/feed", "facebook.com/?sk=h_chr",
    ],
    "instagram": [
        "instagram.com/?",
        "instagram.com/accounts/onetap",
        "instagram.com/accounts/login/two_factor",
    ],
}
```

### Inyección de cookies en Playwright

Las cookies capturadas tienen el formato exacto que acepta `context.add_cookies()`
en `ContentFetcher.fetch()`. La inyección ocurre **antes** de `page.goto()`:

```python
if cookies:
    await context.add_cookies(cookies)
    # Scope: contexto (no página) → aplica a todos los dominios
    # y persiste entre navegaciones dentro de la misma sesión.
```

## Sistema de almacenamiento

### Estructura en disco

```
data/accounts/
├── facebook/
│   └── {account_id}/
│       ├── account.json    ← perfil (sin cookies_path)
│       └── cookies.json    ← cookies en formato Playwright
└── instagram/
    └── {account_id}/
        ├── account.json
        └── cookies.json
```

El campo `cookies_path` no se persiste en `account.json`.
`LocalFileStorage._load_from_file()` lo infiere comprobando si existe
`{directorio}/cookies.json` en el momento de deserializar.

### Migración a base de datos

Implementa los cinco métodos abstractos de `BaseAccountStorage`
(documentados en [api/auth.md](../api/auth.md#reaper.auth.storage.base.BaseAccountStorage)):

```python
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.auth.models import AccountActivity, AccountProfile

class PostgresAccountStorage(BaseAccountStorage):

    async def save(self, account: AccountProfile) -> None:
        # upsert — INSERT ... ON CONFLICT DO UPDATE
        ...

    async def load(self, account_id: str) -> AccountProfile | None:
        # SELECT WHERE account_id = ?
        ...

    async def delete(self, account_id: str) -> bool:
        # DELETE WHERE account_id = ?
        ...

    async def list_all(self, platform: str | None = None) -> list[AccountProfile]:
        # SELECT con WHERE platform opcional
        ...

    async def update_activity(self, account_id: str, activity: AccountActivity) -> bool:
        # UPDATE parcial — solo el campo activity
        ...

manager = AccountManager(
    storage=PostgresAccountStorage(session),
    accounts_dir="data/accounts",   # sigue necesario para cookies en disco
)
```

!!! note "Cookies siempre en disco"
    Independientemente del backend de perfiles, los archivos de cookies siempre
    se almacenan en disco local. `AccountManager` instancia siempre un
    `LocalFileStorage` interno para la gestión física de cookies.

## Serialización de modelos

Todos los modelos son 100% serializables sin `default=str`:

```python
# Serializar
data = profile.to_dict()        # dict con solo tipos primitivos
json.dumps(data)                # sin kwargs adicionales

# Deserializar — tolerante a versiones anteriores del schema
profile = AccountProfile.from_dict(data)
activity = AccountActivity.from_dict(data["activity"])
```
