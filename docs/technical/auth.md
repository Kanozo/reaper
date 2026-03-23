# Reaper — Referencia Técnica: Módulo de Autenticación y Rotación de Cuentas

> **Versión**: 0.2.0 | **Python**: 3.11+ | **Módulo**: `reaper.auth`

---

## Tabla de Contenidos

1. [Visión general de la arquitectura](#1-visión-general-de-la-arquitectura)
2. [Estructura del módulo](#2-estructura-del-módulo)
3. [Modelos de datos](#3-modelos-de-datos)
4. [Sistema de almacenamiento](#4-sistema-de-almacenamiento)
5. [Algoritmo de rotación](#5-algoritmo-de-rotación)
6. [AccountManager — API completa](#6-accountmanager--api-completa)
7. [Flujo de login interactivo](#7-flujo-de-login-interactivo)
8. [Integración con el pipeline de scraping](#8-integración-con-el-pipeline-de-scraping)
9. [Ciclo de vida de una cuenta](#9-ciclo-de-vida-de-una-cuenta)
10. [Migración a base de datos](#10-migración-a-base-de-datos)
11. [Referencia de constantes](#11-referencia-de-constantes)
12. [Diagramas de flujo](#12-diagramas-de-flujo)
13. [Testing del módulo](#13-testing-del-módulo)

---

## 1. Visión general de la arquitectura

El módulo `reaper.auth` añade gestión de sesiones autenticadas al pipeline de scraping existente sin romper ninguna interfaz pública. El diseño sigue tres principios:

**Compatibilidad hacia atrás total.** Cualquier código que usara Reaper 0.1.x sigue funcionando sin modificaciones. El campo `account_manager` en `ScraperConfig` es `None` por defecto; cuando es `None`, el comportamiento es idéntico al original.

**Separación de responsabilidades.** Cada componente tiene un propósito único: los modelos solo representan datos, el storage solo persiste, el rotador solo puntúa, el manager coordina. Ninguna capa conoce los detalles internos de la anterior.

**Extensibilidad explícita.** La interfaz `BaseAccountStorage` está diseñada para ser implementada. Migrar de archivos JSON a PostgreSQL o MongoDB requiere implementar cinco métodos abstractos y pasar la nueva instancia al constructor del manager.

```
┌─────────────────────────────────────────────────────────────────┐
│  API pública  reaper/__init__.py                                │
│  scrape(account_manager=...)  |  Reaper(account_manager=...)   │
├─────────────────────────────────────────────────────────────────┤
│  AccountManager  auth/account_manager.py                        │
│  add_account | import_cookies | get_account_for_request         │
│  record_success | record_failure | pool_summary                 │
├──────────────────────┬──────────────────────────────────────────┤
│  AccountRotator      │  BaseAccountStorage                      │
│  auth/rotator.py     │  auth/storage/base.py                    │
│  _score()            │       ↓                                  │
│  _rank()             │  LocalFileStorage                        │
│  select()            │  auth/storage/local.py                   │
├──────────────────────┴──────────────────────────────────────────┤
│  Modelos  auth/models.py                                        │
│  AccountProfile | AccountActivity | AccountStatus               │
├─────────────────────────────────────────────────────────────────┤
│  Login interactivo  auth/login.py                               │
│  run_login_new_account | run_login_refresh | _cli_main          │
└─────────────────────────────────────────────────────────────────┘
```

### Principio de flujo de datos

```
scrape(url, account_manager=manager)
  → Reaper(account_manager)
    → ScraperConfig(account_manager=manager)
      → FacebookScraper._fetch_with_account("facebook")
          → manager.get_account_for_request("facebook")   ← rotador
          → local_storage.load_cookies(platform, id)      ← disco
          → ContentFetcher.fetch(cookies=cookies)          ← Playwright
          → manager.record_success/failure(id)            ← actividad
```

---

## 2. Estructura del módulo

```
src/reaper/auth/
├── __init__.py              Re-exportaciones del API público del módulo
├── models.py                Modelos de datos: AccountProfile, AccountActivity, AccountStatus
├── rotator.py               Algoritmo de scoring y selección de cuenta
├── account_manager.py       Gestor central: CRUD, cookies, rotación
├── login.py                 Flujo interactivo de login con Playwright
└── storage/
    ├── __init__.py
    ├── base.py              ABC: BaseAccountStorage, StorageError
    └── local.py             Implementación en archivos JSON: LocalFileStorage
```

### Archivos de la librería modificados

| Archivo | Cambio |
|---|---|
| `reaper/__init__.py` | Exporta `AccountManager`, `AccountProfile`, `AccountStatus`. `scrape()` acepta `account_manager`. Versión → 0.2.0 |
| `reaper/core.py` | `Reaper.__init__` acepta `account_manager` y lo propaga a `ScraperConfig` |
| `reaper/config.py` | Campo `account_manager: Any = None` + propiedad `has_account_manager` |
| `reaper/scrapers/base.py` | Método nuevo `_fetch_with_account()`. `_fetch()` ahora es abstracto con parámetro `cookies` |
| `reaper/scrapers/facebook.py` | `run()` llama `_fetch_with_account("facebook")`. `_fetch()` acepta `cookies` |
| `reaper/scrapers/instagram.py` | Mismo patrón que Facebook |
| `reaper/network/content_fetcher.py` | `fetch()` acepta `cookies: list[dict] \| None`. Inyección vía `context.add_cookies()` |

---

## 3. Modelos de datos

### `AccountStatus` — estado operativo

```python
class AccountStatus(str, Enum):
    ACTIVE        = "active"          # Operativa, cookies válidas
    SUSPENDED     = "suspended"       # Bloqueada por la plataforma — NO seleccionable
    RATE_LIMITED  = "rate_limited"    # Recibió señales de throttling — penalizada en score
    COOKIE_EXPIRED= "cookie_expired"  # Cookies inválidas — NO seleccionable
    NEEDS_REFRESH = "needs_refresh"   # Umbral de refresco alcanzado — penalizada
    UNKNOWN       = "unknown"         # Estado inicial, sin verificar
```

Los estados `SUSPENDED` y `COOKIE_EXPIRED` son los únicos que impiden la selección de la cuenta por el rotador. Los demás son seleccionables con distintos grados de penalización en el score.

### `AccountActivity` — historial y métricas

```python
@dataclass
class AccountActivity:
    total_requests: int                    # Total acumulado de peticiones
    successful_requests: int               # Peticiones con datos extraídos
    failed_requests: int                   # Peticiones con error
    last_used_at: str | None               # ISO-8601 del último uso
    last_success_at: str | None            # ISO-8601 del último éxito
    last_error_at: str | None              # ISO-8601 del último error
    recent_errors: list[str]               # FIFO últimos 20 errores con timestamp
    requests_since_cookie_refresh: int     # Contador reseteado al importar cookies
```

Propiedades derivadas:

```python
activity.success_rate   # float 0.0–1.0
activity.failure_rate   # float 0.0–1.0
```

Métodos de mutación:

```python
activity.record_success()                    # Incrementa contadores de éxito
activity.record_failure("HTTP 429")          # Incrementa contadores de fallo, añade error a FIFO
activity.reset_cookie_refresh_counter()      # Resetea requests_since_cookie_refresh a 0
```

### `AccountProfile` — perfil completo de una cuenta

```python
@dataclass
class AccountProfile:
    account_id: str              # UUID4 autogenerado
    platform: str                # "facebook" | "instagram"
    username: str                # Nombre legible del operador
    email: str | None            # Email informativo
    status: AccountStatus        # Estado operativo actual
    cookies_path: str | None     # Ruta al cookies.json en disco (inferida por LocalFileStorage)
    activity: AccountActivity    # Historial de actividad
    created_at: str              # ISO-8601 de creación
    updated_at: str              # ISO-8601 de última modificación
    notes: str                   # Campo libre del operador
```

Propiedades de conveniencia:

```python
profile.has_cookies     # True si cookies_path existe en disco
profile.is_selectable   # True si has_cookies AND status not in NON_SELECTABLE_STATUSES
```

### Serialización

Todos los modelos implementan `to_dict()` / `from_dict()`:

```python
# Serializar
data = profile.to_dict()           # dict con tipos primitivos
json_str = json.dumps(data)        # serializable sin default=str

# Deserializar
profile = AccountProfile.from_dict(data)
# Tolerante a versiones: campos ausentes usan valores por defecto
```

El campo `cookies_path` **no se persiste** en `account.json`. `LocalFileStorage` lo infiere de la ubicación del archivo en disco al deserializar. Esto evita rutas absolutas hardcodeadas que se rompan al mover el proyecto.

---

## 4. Sistema de almacenamiento

### Estructura en disco (`LocalFileStorage`)

```
data/accounts/                          ← accounts_dir (configurable)
├── facebook/
│   ├── 3f2e1a-uuid.../
│   │   ├── account.json               ← perfil (sin cookies_path)
│   │   └── cookies.json               ← cookies en formato Playwright
│   └── otro-uuid.../
│       ├── account.json
│       └── cookies.json
└── instagram/
    └── uuid.../
        ├── account.json
        └── cookies.json
```

### Interfaz abstracta `BaseAccountStorage`

```python
class BaseAccountStorage(ABC):
    async def save(self, account: AccountProfile) -> None: ...
    async def load(self, account_id: str) -> AccountProfile | None: ...
    async def delete(self, account_id: str) -> bool: ...
    async def list_all(self, platform: str | None = None) -> list[AccountProfile]: ...
    async def update_activity(self, account_id: str, activity: AccountActivity) -> bool: ...

    # Implementado en base (sobreescribible):
    async def exists(self, account_id: str) -> bool: ...
```

La distinción entre `save()` y `update_activity()` es intencionada. `update_activity()` es una operación de alta frecuencia (se llama en cada petición) y los backends de BD pueden optimizarla con un `UPDATE` parcial. `save()` es para cambios de estado completos (raro).

### `LocalFileStorage` — métodos adicionales para cookies

`LocalFileStorage` extiende la interfaz abstracta con operaciones de cookies que no pertenecen a `BaseAccountStorage` (ya que en BD las cookies se guardarían como campos):

```python
storage = LocalFileStorage("data/accounts")

# Ruta canónica de cookies (predecible, no requiere leer el JSON)
path = storage.cookies_path_for("facebook", account_id)

# Guardar cookies brutas desde memoria
storage.save_cookies("facebook", account_id, cookies_list)

# Leer cookies para inyectarlas en Playwright
cookies = storage.load_cookies("facebook", account_id)
```

---

## 5. Algoritmo de rotación

El `AccountRotator` es **stateless**: recibe el pool completo de cuentas en cada llamada a `select()` y calcula el ranking en el momento. No necesita ser notificado de cambios.

### Fórmula de puntuación

```
score = (success_rate × 0.6)
      + (lru_score    × 0.3)
      - (cookie_age   × 0.1)
```

Donde:

- **`success_rate`**: `successful_requests / total_requests`. Cuentas sin historial = 1.0 (máximo).
- **`lru_score`**: antigüedad de uso normalizada entre 0.0 y 1.0. La cuenta usada hace más tiempo recibe 1.0; la más reciente recibe 0.0. Cuentas nunca usadas reciben el máximo.
- **`cookie_age`**: `requests_since_cookie_refresh / threshold`. Normalizado 0.0–1.0. Incentiva usar otras cuentas antes de que esta alcance el umbral.

### Penalizaciones multiplicativas por estado

| Estado | Multiplicador |
|---|---|
| `ACTIVE` | ×1.0 (sin penalización) |
| `NEEDS_REFRESH` | ×0.5 |
| `RATE_LIMITED` | ×0.2 |

Los estados `SUSPENDED` y `COOKIE_EXPIRED` se filtran antes de puntuar: nunca llegan al algoritmo.

### Ejemplo de ranking con 3 cuentas

| Cuenta | success_rate | lru_score | cookie_age | score bruto | penalización | score final |
|---|---|---|---|---|---|---|
| cuenta_nueva | 1.00 | 1.00 | 0.00 | 0.90 | ×1.0 | **0.90** |
| cuenta_activa | 0.85 | 0.40 | 0.60 | 0.57 | ×1.0 | **0.57** |
| rate_limited | 0.70 | 0.60 | 0.20 | 0.58 | ×0.2 | **0.12** |

### Ajuste de pesos

Los pesos son constantes en `rotator.py` y pueden modificarse sin tocar el algoritmo:

```python
# reaper/auth/rotator.py
WEIGHT_SUCCESS_RATE: float = 0.6
WEIGHT_LEAST_RECENTLY_USED: float = 0.3
WEIGHT_COOKIE_AGE: float = 0.1
RATE_LIMITED_PENALTY: float = 0.2
NEEDS_REFRESH_PENALTY: float = 0.5
```

---

## 6. AccountManager — API completa

```python
from reaper.auth import AccountManager

manager = AccountManager(
    storage=None,                    # None = usa LocalFileStorage
    accounts_dir="data/accounts",    # Ignorado si storage != None
    cookie_refresh_threshold=50,     # Peticiones exitosas antes de NEEDS_REFRESH
)
```

### CRUD de cuentas

```python
# Crear cuenta nueva (sin cookies todavía)
profile = await manager.add_account(
    platform="facebook",
    username="mi_usuario@gmail.com",
    email="mi_usuario@gmail.com",    # opcional
    notes="cuenta principal",         # opcional
)

# Crear cuenta con cookies inline
profile = await manager.add_account(
    platform="instagram",
    username="ig_user",
    cookies=[{"name": "sessionid", "value": "abc123", ...}],
)

# Crear cuenta con cookies desde archivo externo
profile = await manager.add_account(
    platform="facebook",
    username="otro_usuario",
    cookies_file="exports/facebook_cookies.json",
)

# Leer un perfil
profile = await manager.get_account(account_id)

# Listar todas (o filtrar por plataforma)
todas   = await manager.list_accounts()
solo_fb = await manager.list_accounts("facebook")
solo_ig = await manager.list_accounts("instagram")

# Actualizar estado manualmente
await manager.update_status(account_id, AccountStatus.ACTIVE, notes="Reactivada")

# Eliminar (perfil + cookies)
deleted = await manager.remove_account(account_id)  # bool
```

### Gestión de cookies

```python
# Importar cookies desde lista en memoria (formato Playwright)
await manager.import_cookies(account_id, cookies_list)

# Importar desde archivo JSON externo
await manager.import_cookies_from_file(account_id, "ruta/al/cookies.json")

# Exportar cookies actuales
cookies = await manager.export_cookies(account_id)  # list[dict] | None
```

`import_cookies()` siempre:
1. Guarda las cookies en disco bajo `{accounts_dir}/{platform}/{account_id}/cookies.json`.
2. Actualiza `profile.cookies_path` con la ruta canónica.
3. Resetea `activity.requests_since_cookie_refresh` a 0.
4. Establece `profile.status = AccountStatus.ACTIVE`.

### Rotación y registro de actividad

```python
# Seleccionar la mejor cuenta para una petición (llamado por los scrapers)
account = await manager.get_account_for_request("facebook")
# Retorna None si no hay cuentas disponibles → modo anónimo

# Registrar resultado (llamado por BaseScraper._fetch_with_account)
await manager.record_success(account_id)
await manager.record_failure(account_id, "HTTP 429 Too Many Requests")
```

`record_success()` verifica automáticamente si `requests_since_cookie_refresh >= threshold`. Si se alcanza, marca la cuenta como `NEEDS_REFRESH` y emite un `WARNING` en los logs con el comando exacto para refrescar.

`record_failure()` evalúa si la tasa de fallos en la ventana reciente supera el 80% y marca la cuenta como `RATE_LIMITED` si se cumplen `CONSECUTIVE_FAILURES_FOR_RATE_LIMIT` fallos.

### Diagnóstico

```python
# Resumen agregado del pool
summary = await manager.pool_summary()
# {
#     "total": 5, "active": 3, "needs_refresh": 1,
#     "rate_limited": 1, "suspended": 0, ...
#     "by_platform": {"facebook": {...}, "instagram": {...}}
# }

# Ranking detallado con scores
ranked = await manager.get_ranked_accounts("facebook")
# [
#     {"rank": 1, "username": "cuenta_a", "score": 0.87, "success_rate": 0.95, ...},
#     {"rank": 2, "username": "cuenta_b", "score": 0.61, ...},
# ]
```

---

## 7. Flujo de login interactivo

### Por qué es manual

Facebook e Instagram detectan con alta precisión los intentos de login automatizado mediante Playwright. El enfoque manual es intrínsecamente más robusto: el usuario interactúa con el navegador de forma normal, incluyendo 2FA, verificaciones de dispositivo y CAPTCHAs que no pueden resolverse programáticamente.

### API programática

```python
from reaper.auth.login import run_login_new_account, run_login_refresh
from reaper.auth import AccountManager

manager = AccountManager()

# Registrar cuenta nueva completa (abre Firefox, espera login, guarda en manager)
profile = await run_login_new_account(
    manager=manager,
    platform="facebook",
    username="usuario@gmail.com",
    email="usuario@gmail.com",   # opcional
    notes="cuenta A",             # opcional
)
if profile:
    print(f"Registrada: {profile.account_id}")

# Refrescar cookies de cuenta existente
ok = await run_login_refresh(
    manager=manager,
    account_id="3f2e1a-...",
)
```

### Detección de login exitoso

`_wait_for_login_success()` sondea `page.url` cada segundo buscando estos patrones:

**Facebook:**
```
facebook.com/?
facebook.com/home
facebook.com/me
facebook.com/feed
facebook.com/?sk=h_chr
```

**Instagram:**
```
instagram.com/?
instagram.com/accounts/onetap
instagram.com/accounts/login/two_factor
```

El timeout por defecto es 300 segundos (5 minutos), configurable vía `LOGIN_TIMEOUT_SECONDS`.

### Inyección de cookies en Playwright

Las cookies capturadas con `context.cookies()` son una lista de dicts en formato Playwright:

```python
[
    {
        "name": "c_user",
        "value": "123456789",
        "domain": ".facebook.com",
        "path": "/",
        "expires": 1735689600.0,
        "httpOnly": false,
        "secure": true,
        "sameSite": "None"
    },
    ...
]
```

Este formato es el mismo que acepta `context.add_cookies()` en `ContentFetcher.fetch()`, por lo que no requiere ninguna transformación.

### Inyección en el navegador durante el scraping

En `ContentFetcher.fetch()`:

```python
if cookies:
    await context.add_cookies(cookies)
    # Inyección en el contexto (no en la página): aplica a todas las
    # páginas del contexto y persiste entre navegaciones.
```

La inyección se hace en el **contexto**, no en la página, lo que garantiza que las cookies estén disponibles desde la primera petición de red y evita cualquier redirect al muro de login.

---

## 8. Integración con el pipeline de scraping

### `BaseScraper._fetch_with_account()`

Este método es el único punto de integración entre el sistema de cuentas y el pipeline de scraping:

```python
async def _fetch_with_account(self, platform: str, override_url: str | None = None):
    manager = self.config.account_manager

    if manager is None:
        return await self._fetch(override_url=override_url)    # Modo anónimo

    account = await manager.get_account_for_request(platform)
    if account is None:
        return await self._fetch(override_url=override_url)    # Pool vacío → anónimo

    cookies = manager._local_storage.load_cookies(platform, account.account_id)
    if not cookies:
        return await self._fetch(override_url=override_url)    # Sin cookies → anónimo

    self._active_account_id = account.account_id
    fetch_result = await self._fetch(override_url=override_url, cookies=cookies)

    if fetch_result.success:
        await manager.record_success(account.account_id)
    else:
        await manager.record_failure(account.account_id, fetch_result.error or "unknown")

    return fetch_result
```

Los **fetches secundarios** (merge reel↔video, re-fetch de álbum) llaman a `_fetch()` directamente sin pasar por `_fetch_with_account()`. Estos son parte de la misma sesión y no deben cambiar de cuenta ni contar como peticiones independientes.

### Cambios en `ScraperConfig`

```python
@dataclass
class ScraperConfig:
    # ... campos existentes sin cambios ...

    # Nuevo campo opcional — None por defecto
    account_manager: Any = field(default=None, repr=False)

    @property
    def has_account_manager(self) -> bool:
        return self.account_manager is not None
```

### Cambios en `Reaper`

```python
class Reaper:
    def __init__(self, account_manager: AccountManager | None = None) -> None:
        self._account_manager = account_manager

    async def scrape(self, url, **kwargs) -> dict:
        config = ScraperConfig(
            url=url,
            account_manager=self._account_manager,
            **kwargs,
        )
        ...
```

---

## 9. Ciclo de vida de una cuenta

```
Registro inicial
    add_account() → status=UNKNOWN (sin cookies)
         ↓
    import_cookies() / run_login_new_account()
         ↓
    status=ACTIVE, requests_since_refresh=0

Uso normal
    get_account_for_request() → seleccionada por rotador
         ↓
    record_success() × N
         ↓ (cuando N >= cookie_refresh_threshold)
    status=NEEDS_REFRESH  ← WARNING en logs con comando de refresco
         ↓
    (operador llama run_login_refresh() o import_cookies())
         ↓
    status=ACTIVE, requests_since_refresh=0

Fallo en cadena
    record_failure() × CONSECUTIVE_FAILURES_FOR_RATE_LIMIT
         ↓ (cuando failure_rate > 80% en ventana reciente)
    status=RATE_LIMITED  ← penalización ×0.2 en score
         ↓
    (operador puede forzar: update_status(id, ACTIVE))

Suspensión
    update_status(id, SUSPENDED)
         ↓
    is_selectable=False  ← excluida del rotador definitivamente
         ↓
    remove_account(id)  ← eliminar si ya no se va a rehabilitar
```

---

## 10. Migración a base de datos

Para migrar de almacenamiento local a una base de datos, implementa `BaseAccountStorage`:

```python
# my_app/storage.py
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.auth.models import AccountActivity, AccountProfile
from sqlalchemy.ext.asyncio import AsyncSession

class PostgresAccountStorage(BaseAccountStorage):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, account: AccountProfile) -> None:
        # INSERT ... ON CONFLICT DO UPDATE (upsert)
        data = account.to_dict()
        await self.session.execute(
            "INSERT INTO accounts ... ON CONFLICT (account_id) DO UPDATE SET ...",
            data,
        )
        await self.session.commit()

    async def load(self, account_id: str) -> AccountProfile | None:
        row = await self.session.execute(
            "SELECT * FROM accounts WHERE account_id = :id",
            {"id": account_id},
        )
        if not row:
            return None
        return AccountProfile.from_dict(dict(row))

    async def delete(self, account_id: str) -> bool:
        result = await self.session.execute(
            "DELETE FROM accounts WHERE account_id = :id",
            {"id": account_id},
        )
        await self.session.commit()
        return result.rowcount > 0

    async def list_all(self, platform: str | None = None) -> list[AccountProfile]:
        query = "SELECT * FROM accounts"
        params = {}
        if platform:
            query += " WHERE platform = :platform"
            params["platform"] = platform
        rows = await self.session.execute(query, params)
        return [AccountProfile.from_dict(dict(r)) for r in rows]

    async def update_activity(self, account_id: str, activity: AccountActivity) -> bool:
        # UPDATE solo el campo activity — más eficiente que save() completo
        result = await self.session.execute(
            "UPDATE accounts SET activity = :activity, updated_at = NOW() "
            "WHERE account_id = :id",
            {"activity": json.dumps(activity.to_dict()), "id": account_id},
        )
        await self.session.commit()
        return result.rowcount > 0
```

Uso:

```python
from my_app.storage import PostgresAccountStorage

storage = PostgresAccountStorage(session=async_session)
manager = AccountManager(
    storage=storage,
    accounts_dir="data/accounts",   # aún necesario para cookies en disco
    cookie_refresh_threshold=50,
)
```

**Nota:** Los archivos de cookies siempre se almacenan en disco local (`LocalFileStorage`), independientemente del backend de perfiles. El constructor de `AccountManager` siempre inicializa un `LocalFileStorage` para gestionar el acceso físico a los archivos de cookies.

---

## 11. Referencia de constantes

### `reaper/auth/models.py`

| Constante | Valor | Descripción |
|---|---|---|
| `MAX_RECENT_ERRORS` | `20` | Entradas máximas en `recent_errors` (FIFO) |
| `SUPPORTED_PLATFORMS` | `{"facebook", "instagram"}` | Plataformas válidas para `AccountProfile.platform` |

### `reaper/auth/rotator.py`

| Constante | Valor | Descripción |
|---|---|---|
| `WEIGHT_SUCCESS_RATE` | `0.6` | Peso de la tasa de éxito en el score |
| `WEIGHT_LEAST_RECENTLY_USED` | `0.3` | Peso de la antigüedad de uso |
| `WEIGHT_COOKIE_AGE` | `0.1` | Peso negativo de la antigüedad de cookies |
| `RATE_LIMITED_PENALTY` | `0.2` | Multiplicador de penalización para `RATE_LIMITED` |
| `NEEDS_REFRESH_PENALTY` | `0.5` | Multiplicador de penalización para `NEEDS_REFRESH` |

### `reaper/auth/account_manager.py`

| Constante | Valor | Descripción |
|---|---|---|
| `DEFAULT_COOKIE_REFRESH_THRESHOLD` | `50` | Peticiones exitosas antes de `NEEDS_REFRESH` |
| `CONSECUTIVE_FAILURES_FOR_RATE_LIMIT` | `5` | Fallos consecutivos antes de `RATE_LIMITED` |

### `reaper/auth/login.py`

| Constante | Valor | Descripción |
|---|---|---|
| `LOGIN_TIMEOUT_SECONDS` | `300` | Tiempo máximo de espera del login (segundos) |
| `URL_POLL_INTERVAL` | `1.0` | Segundos entre comprobaciones de URL |
| `PROGRESS_REPORT_INTERVAL` | `30` | Segundos entre mensajes de progreso al usuario |

---

## 12. Diagramas de flujo

### Selección de cuenta en `_fetch_with_account()`

```
_fetch_with_account("facebook")
         │
         ▼
  account_manager is None?
    ├─ SÍ → _fetch() anónimo ──────────────────────────────────► return
    └─ NO ↓
         │
  get_account_for_request("facebook")
         │
  ¿Ninguna cuenta disponible?
    ├─ SÍ → WARNING + _fetch() anónimo ────────────────────────► return
    └─ NO ↓
         │
  load_cookies(platform, account_id)
         │
  ¿Cookies en disco?
    ├─ NO → WARNING + _fetch() anónimo ────────────────────────► return
    └─ SÍ ↓
         │
  _fetch(cookies=cookies)   ← Playwright con sesión autenticada
         │
  fetch.success?
    ├─ SÍ → record_success(account_id)
    └─ NO → record_failure(account_id, error)
         │
         ▼
       return fetch_result
```

### Ciclo de refresco automático

```
record_success(account_id)
         │
  requests_since_refresh >= threshold?
    ├─ NO → update_activity() → fin
    └─ SÍ ↓
         │
  status == ACTIVE?
    ├─ NO → update_activity() → fin
    └─ SÍ ↓
         │
  status = NEEDS_REFRESH
  touch_updated()
  WARNING en logs: "Importa cookies frescas con import_cookies(id, cookies)"
  save(profile)
  update_activity()
         │
         ▼
        fin
```

---

## 13. Testing del módulo

### Tests unitarios incluidos (ejecutados durante el desarrollo)

El módulo fue validado con 14 tests de integración que cubren:

- `add_account` sin cookies → `status=UNKNOWN`
- `import_cookies` → `status=ACTIVE`, cookies en disco, contador=0
- `get_account_for_request` con pool de 1 cuenta
- `record_success` × threshold → `NEEDS_REFRESH`
- `NEEDS_REFRESH` sigue seleccionable (score penalizado)
- `import_cookies` resetea estado → `ACTIVE`, contador=0
- `record_failure` con alta tasa de fallos → `RATE_LIMITED`
- `RATE_LIMITED` sigue seleccionable (score muy penalizado)
- `SUSPENDED` bloquea selección → `None`
- `pool_summary` con estado correcto
- Ranking de 2 cuentas: la menos usada tiene mayor score
- `list_accounts` con filtro por plataforma
- `remove_account` existente y no existente
- `export_cookies` devuelve la lista original

### Cómo añadir tests

```python
# tests/test_account_manager.py
import asyncio
import pytest
from pathlib import Path
from reaper.auth import AccountManager, AccountStatus

@pytest.fixture
def tmp_manager(tmp_path):
    return AccountManager(accounts_dir=tmp_path / "accounts", cookie_refresh_threshold=5)

@pytest.mark.asyncio
async def test_add_account_creates_profile(tmp_manager):
    profile = await tmp_manager.add_account(platform="facebook", username="test")
    assert profile.account_id
    assert profile.status == AccountStatus.UNKNOWN

@pytest.mark.asyncio
async def test_import_cookies_activates_account(tmp_manager):
    profile = await tmp_manager.add_account(platform="facebook", username="test")
    cookies = [{"name": "c_user", "value": "123", "domain": ".facebook.com",
                "path": "/", "expires": 9999.0, "httpOnly": False,
                "secure": True, "sameSite": "None"}]
    await tmp_manager.import_cookies(profile.account_id, cookies)
    reloaded = await tmp_manager.get_account(profile.account_id)
    assert reloaded.status == AccountStatus.ACTIVE
    assert reloaded.has_cookies is True
```
