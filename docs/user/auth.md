# Reaper — Manual de Usuario: Cuentas Autenticadas

> **Versión**: 0.2.0 | Módulo: Gestión de cuentas con sesión

---

## Tabla de Contenidos

1. [¿Para qué sirven las cuentas autenticadas?](#1-para-qué-sirven-las-cuentas-autenticadas)
2. [Instalación y requisitos](#2-instalación-y-requisitos)
3. [Primeros pasos: añadir tu primera cuenta](#3-primeros-pasos-añadir-tu-primera-cuenta)
4. [Uso con la función `scrape()`](#4-uso-con-la-función-scrape)
5. [Gestionar múltiples cuentas](#5-gestionar-múltiples-cuentas)
6. [Refrescar cookies de una cuenta](#6-refrescar-cookies-de-una-cuenta)
7. [Monitorear el estado del pool](#7-monitorear-el-estado-del-pool)
8. [Entendiendo los estados de las cuentas](#8-entendiendo-los-estados-de-las-cuentas)
9. [Uso desde la línea de comandos](#9-uso-desde-la-línea-de-comandos)
10. [Preguntas frecuentes](#10-preguntas-frecuentes)
11. [Resolución de problemas](#11-resolución-de-problemas)

---

## 1. ¿Para qué sirven las cuentas autenticadas?

Reaper siempre ha podido extraer contenido **público** de Facebook e Instagram sin necesitar credenciales. Esta nueva funcionalidad añade soporte para **sesiones autenticadas**: puedes registrar tus propias cuentas de Facebook o Instagram y Reaper las usará al hacer las peticiones.

**¿Cuándo tiene sentido usarlas?**

- Cuando quieras acceder a contenido que requiere estar logueado para verlo en el feed.
- Cuando hagas muchas peticiones seguidas y quieras distribuirlas entre varias cuentas para evitar que una sola acumule demasiada actividad.
- Cuando quieras un registro detallado de qué cuenta realizó cada petición y su historial de éxitos y fallos.

**¿Qué NO hacen?**

- No acceden a contenido privado de otros usuarios (mensajes, perfiles privados).
- No hacen login automatizado: tú siempre introduces las credenciales manualmente en el navegador.
- No evitan bloqueos o bans si usas las cuentas de forma agresiva.

---

## 2. Instalación y requisitos

No se requieren dependencias adicionales. Firefox y Playwright ya son parte de Reaper.

Si todavía no has instalado Reaper:

```bash
pip install -e .
playwright install firefox
```

Verifica que todo funciona:

```python
from reaper import AccountManager
print("OK")
```

---

## 3. Primeros pasos: añadir tu primera cuenta

El proceso tiene dos pasos:

1. Ejecutar el flujo de login — se abre Firefox para que inicies sesión manualmente.
2. Las cookies de sesión se guardan automáticamente — Reaper las usará en el futuro.

### Opción A: desde Python (recomendado)

```python
import asyncio
from reaper import AccountManager
from reaper.auth.login import run_login_new_account

async def main():
    # El AccountManager guarda todo en data/accounts/ por defecto
    manager = AccountManager()

    # Esto abre Firefox y espera a que inicies sesión manualmente
    cuenta = await run_login_new_account(
        manager=manager,
        platform="facebook",           # o "instagram"
        username="mi_usuario@gmail.com",
    )

    if cuenta:
        print(f"Cuenta registrada con éxito.")
        print(f"ID: {cuenta.account_id}")
        print(f"Estado: {cuenta.status.value}")
    else:
        print("El login no se completó.")

asyncio.run(main())
```

Cuando ejecutes este código:
1. Se abrirá una ventana de Firefox con la página de login de Facebook.
2. Introduce tus credenciales, completa el 2FA si lo tienes activado.
3. Una vez que el navegador te lleve al feed principal, el script lo detecta, guarda las cookies y cierra el navegador automáticamente.
4. No cierres el navegador tú mismo — espera a que el script lo haga.

### Opción B: desde la línea de comandos

```bash
# Modo interactivo (hace preguntas)
python -m reaper.auth.login

# Especificando plataforma y usuario
python -m reaper.auth.login --platform facebook --username mi_usuario@gmail.com

# Para Instagram
python -m reaper.auth.login --platform instagram --username mi_usuario_ig
```

### ¿Dónde se guardan las cookies?

Por defecto en `data/accounts/`, con esta estructura:

```
data/accounts/
└── facebook/
    └── 3f2e1a-[uuid]/
        ├── account.json   ← información de la cuenta (sin contraseñas)
        └── cookies.json   ← cookies de sesión
```

Puedes cambiar esta ubicación:

```python
manager = AccountManager(accounts_dir="/ruta/personalizada/cuentas")
```

---

## 4. Uso con la función `scrape()`

Una vez que tienes cuentas registradas, simplemente pasa el `AccountManager` al hacer el scraping:

```python
import asyncio
from reaper import scrape, AccountManager

async def main():
    manager = AccountManager()   # carga las cuentas guardadas en data/accounts/

    resultado = await scrape(
        url="https://www.facebook.com/reel/123456",
        account_manager=manager,
    )
    print(resultado["author"]["name"])
    print(resultado["text"])

asyncio.run(main())
```

**Reaper selecciona automáticamente la mejor cuenta disponible.** No necesitas indicar cuál usar; el sistema evalúa el historial de cada cuenta y elige la más adecuada.

### Scraping de múltiples URLs

Cuando usas la clase `Reaper` directamente, el mismo `AccountManager` se reutiliza entre peticiones y el historial de actividad se acumula correctamente:

```python
import asyncio
from reaper import Reaper, AccountManager

async def main():
    manager = AccountManager()
    reaper  = Reaper(account_manager=manager)

    urls = [
        "https://www.facebook.com/reel/111111",
        "https://www.instagram.com/p/abc123/",
        "https://www.facebook.com/reel/222222",
        "https://www.instagram.com/reel/def456/",
    ]

    resultados = []
    for url in urls:
        resultado = await reaper.scrape(url)
        if resultado.get("raw_data_available"):
            resultados.append(resultado)
            print(f"OK: {url}")
        else:
            print(f"Sin datos: {url} — {resultado.get('error')}")

    print(f"\nExtraídos: {len(resultados)}/{len(urls)}")

asyncio.run(main())
```

### Sin cuentas: modo anónimo (igual que antes)

Si no pasas `account_manager`, Reaper funciona exactamente igual que en la versión 0.1.x:

```python
# Sin cuentas — funciona igual que antes
resultado = await scrape("https://www.facebook.com/reel/123456")
```

---

## 5. Gestionar múltiples cuentas

Puedes registrar tantas cuentas como necesites, tanto de Facebook como de Instagram.

### Añadir más cuentas

```python
import asyncio
from reaper import AccountManager
from reaper.auth.login import run_login_new_account

async def main():
    manager = AccountManager()

    # Segunda cuenta de Facebook
    await run_login_new_account(manager, "facebook", "otra_cuenta@gmail.com")

    # Cuenta de Instagram
    await run_login_new_account(manager, "instagram", "mi_ig_profesional")

asyncio.run(main())
```

### Ver todas tus cuentas

```python
import asyncio
from reaper import AccountManager

async def main():
    manager = AccountManager()

    # Todas las cuentas
    todas = await manager.list_accounts()
    for cuenta in todas:
        print(f"[{cuenta.platform:10s}] {cuenta.username:30s} | estado={cuenta.status.value}")

    # Solo Facebook
    fb = await manager.list_accounts("facebook")
    print(f"\nCuentas de Facebook: {len(fb)}")

    # Solo Instagram
    ig = await manager.list_accounts("instagram")
    print(f"Cuentas de Instagram: {len(ig)}")

asyncio.run(main())
```

### Eliminar una cuenta

```python
async def main():
    manager = AccountManager()

    # Primero, ver las cuentas para obtener el account_id
    cuentas = await manager.list_accounts()
    for c in cuentas:
        print(f"{c.account_id[:8]}...  {c.username}")

    # Eliminar (borra el perfil y las cookies del disco)
    eliminada = await manager.remove_account("account_id_completo_aqui")
    print("Eliminada:" if eliminada else "No se encontró esa cuenta")

asyncio.run(main())
```

### Ver el ranking de cuentas (cómo las puntúa el sistema)

```python
async def main():
    manager = AccountManager()

    ranking = await manager.get_ranked_accounts("facebook")
    print(f"{'#':>3}  {'Usuario':30}  {'Score':>6}  {'Éxitos':>7}  {'Peticiones':>10}  Estado")
    print("-" * 75)
    for r in ranking:
        print(
            f"{r['rank']:>3}. {r['username']:30}  "
            f"{r['score']:>6.3f}  {r['success_rate']:>6.0%}  "
            f"{r['total_requests']:>10}  {r['status']}"
        )

asyncio.run(main())
```

Ejemplo de salida:

```
  #  Usuario                         Score  Éxitos  Peticiones  Estado
---------------------------------------------------------------------------
  1. cuenta_nueva                    0.900   100%           0  unknown
  2. cuenta_activa_fb                0.612    95%          47  active
  3. cuenta_vieja                    0.301    72%         124  needs_refresh
  4. cuenta_con_problemas            0.115    60%          31  rate_limited
```

---

## 6. Refrescar cookies de una cuenta

Las cookies de sesión tienen una vida útil limitada. Reaper hace un seguimiento del número de peticiones realizadas con cada sesión y te avisa cuando es momento de refrescarlas.

### ¿Cuándo refrescar?

Verás este aviso en los logs cuando una cuenta necesite refresco:

```
WARNING  Umbral de refresco alcanzado | username=mi_usuario | peticiones_desde_refresco=50
         — Importa cookies frescas con account_manager.import_cookies('3f2e1a-...', new_cookies)
```

Por defecto el umbral es **50 peticiones exitosas**. Puedes ajustarlo:

```python
manager = AccountManager(cookie_refresh_threshold=100)  # refresca cada 100 peticiones
```

### Cómo refrescar

**Opción A: desde Python**

```python
import asyncio
from reaper import AccountManager
from reaper.auth.login import run_login_refresh

async def main():
    manager = AccountManager()

    # Ver las cuentas que necesitan refresco
    cuentas = await manager.list_accounts()
    necesitan_refresco = [c for c in cuentas if c.status.value in ("needs_refresh", "cookie_expired")]

    for cuenta in necesitan_refresco:
        print(f"Refrescando: {cuenta.username} ({cuenta.platform})...")
        ok = await run_login_refresh(manager, cuenta.account_id)
        if ok:
            print(f"  OK — Cookies actualizadas.")
        else:
            print(f"  Error — Refresco no completado.")

asyncio.run(main())
```

**Opción B: desde la línea de comandos**

```bash
# Modo interactivo — muestra las cuentas disponibles y pregunta cuál refrescar
python -m reaper.auth.login --refresh

# Especificando el account_id directamente
python -m reaper.auth.login --refresh --account-id 3f2e1a-uuid-completo
```

Cuando ejecutes el refresco:
1. Se abrirá Firefox con la página de login.
2. Introduce tus credenciales (o si el navegador recuerda la sesión, puede que ya estés logueado).
3. Una vez en el feed, el script captura las cookies nuevas, reemplaza las antiguas y cierra el navegador.
4. El estado de la cuenta vuelve a `active` y el contador se resetea a 0.

### Importar cookies desde un archivo externo

Si exportaste las cookies manualmente desde tu navegador con una extensión como Cookie-Editor:

```python
async def main():
    manager = AccountManager()
    cuentas = await manager.list_accounts("facebook")
    cuenta = cuentas[0]  # la primera cuenta de Facebook

    # Importar desde archivo JSON externo
    ok = await manager.import_cookies_from_file(
        account_id=cuenta.account_id,
        cookies_file="mis_cookies_exportadas.json",
    )
    print("Importadas correctamente" if ok else "Error al importar")

asyncio.run(main())
```

---

## 7. Monitorear el estado del pool

### Resumen rápido del pool

```python
async def main():
    manager = AccountManager()

    resumen = await manager.pool_summary()
    print(f"Total de cuentas  : {resumen['total']}")
    print(f"Activas           : {resumen['active']}")
    print(f"Necesitan refresco: {resumen['needs_refresh']}")
    print(f"Con rate-limit    : {resumen['rate_limited']}")
    print(f"Suspendidas       : {resumen['suspended']}")
    print(f"Expiradas         : {resumen['cookie_expired']}")

asyncio.run(main())
```

Salida de ejemplo:

```
Total de cuentas  : 5
Activas           : 3
Necesitan refresco: 1
Con rate-limit    : 1
Suspendidas       : 0
Expiradas         : 0
```

### Ver el historial de errores de una cuenta

```python
async def main():
    manager = AccountManager()
    cuentas = await manager.list_accounts()

    for cuenta in cuentas:
        if cuenta.activity.recent_errors:
            print(f"\n{cuenta.username} — últimos errores:")
            for error in cuenta.activity.recent_errors[:5]:
                print(f"  {error}")

asyncio.run(main())
```

### Marcar manualmente el estado de una cuenta

```python
from reaper import AccountManager, AccountStatus

async def main():
    manager = AccountManager()

    # Reactivar una cuenta suspendida (cuando ya sabes que está bien)
    await manager.update_status(
        account_id="3f2e1a-uuid...",
        status=AccountStatus.ACTIVE,
        notes="Reactivada manualmente — verificada el 2026-01-15",
    )

    # Marcar como suspendida una cuenta que ya no funciona
    await manager.update_status(
        account_id="otro-uuid...",
        status=AccountStatus.SUSPENDED,
        notes="Cuenta bloqueada por la plataforma",
    )

asyncio.run(main())
```

---

## 8. Entendiendo los estados de las cuentas

| Estado | Significado | ¿Se usa en peticiones? |
|---|---|---|
| `active` | La cuenta está operativa y sus cookies son válidas. | Sí, con prioridad normal |
| `needs_refresh` | Se alcanzó el umbral de peticiones. Sigue funcionando pero las cookies son viejas. | Sí, con prioridad reducida |
| `rate_limited` | La plataforma devolvió señales de throttling o muchos errores seguidos. | Sí, con prioridad muy reducida |
| `cookie_expired` | Las cookies ya no son válidas (sesión cerrada o expirada). | No — requiere refresco |
| `suspended` | La cuenta fue suspendida. | No — requiere intervención manual |
| `unknown` | Estado inicial. La cuenta no tiene cookies todavía. | No — requiere importar cookies |

**Flujo normal de una cuenta:**

```
unknown → (importar cookies) → active → (50 peticiones) → needs_refresh
                                                              ↓
                                                   (refrescar login)
                                                              ↓
                                                           active
```

**Si hay muchos errores seguidos:**

```
active → (5+ fallos consecutivos con >80% tasa de fallo) → rate_limited
rate_limited → (update_status manual o run_login_refresh) → active
```

---

## 9. Uso desde la línea de comandos

### Login y registro de cuenta nueva

```bash
# Modo interactivo (hace preguntas en la terminal)
python -m reaper.auth.login

# Especificando todos los parámetros
python -m reaper.auth.login --platform facebook --username usuario@gmail.com

# Con directorio personalizado
python -m reaper.auth.login --platform instagram --username mi_usuario \
    --accounts-dir /datos/mis_cuentas

# Umbral de refresco personalizado
python -m reaper.auth.login --platform facebook --username usuario@gmail.com \
    --threshold 100
```

### Refresco de cookies

```bash
# Modo interactivo — muestra lista de cuentas y pregunta cuál refrescar
python -m reaper.auth.login --refresh

# Especificando el account_id
python -m reaper.auth.login --refresh \
    --account-id 3f2e1a-0000-0000-0000-000000000000
```

### Códigos de salida del CLI

| Código | Significado |
|---|---|
| `0` | Éxito — login completado y cookies guardadas |
| `1` | Error — login no completado, plataforma inválida, o cuenta no encontrada |

---

## 10. Preguntas frecuentes

**¿Mis credenciales se guardan en algún lado?**
No. Reaper nunca almacena tu contraseña ni tu email. Solo guarda las cookies de sesión que el navegador genera después de que tú inicias sesión manualmente. Las contraseñas nunca pasan por el código de Reaper.

**¿Cuántas cuentas puedo registrar?**
Sin límite técnico. Puedes registrar todas las cuentas que necesites de Facebook y/o Instagram.

**¿Cómo decide Reaper qué cuenta usar?**
Evalúa cada cuenta con un score basado en tres factores: su tasa de éxito histórica (peso 60%), cuánto tiempo lleva sin usarse — favorece las menos recientes (peso 30%), y cuántas peticiones lleva desde el último refresco de cookies (peso 10%). La cuenta con el score más alto se selecciona.

**¿Qué pasa si no tengo cuentas registradas?**
Reaper funciona en modo anónimo exactamente igual que en la versión anterior. No registrar cuentas no rompe nada.

**¿El sistema cambia de cuenta en cada petición?**
Sí, selecciona la mejor cuenta disponible en cada petición. Con varias cuentas activas la carga se distribuye automáticamente favoreciendo las menos usadas recientemente.

**¿Puedo usar cuentas solo para Facebook y modo anónimo para Instagram?**
Sí. El rotador filtra por plataforma. Si tienes cuentas de Facebook pero no de Instagram, Reaper usará las cuentas para peticiones de Facebook y modo anónimo para Instagram.

**¿El umbral de refresco me desconecta de las cuentas?**
No. Cuando se alcanza el umbral la cuenta pasa a estado `needs_refresh` pero sigue siendo utilizable (con prioridad reducida). Solo deja de funcionar si las cookies realmente expiran (`cookie_expired`), algo que la plataforma controla independientemente del umbral.

**¿Qué formato tienen las cookies exportadas?**
Formato Playwright, que es idéntico al formato Netscape/JSON estándar que exportan extensiones como Cookie-Editor. Es una lista JSON de objetos con campos `name`, `value`, `domain`, `path`, `expires`, `httpOnly`, `secure` y `sameSite`.

---

## 11. Resolución de problemas

### El navegador se abre pero no detecta que inicié sesión

Verifica que la URL tras el login contiene uno de los patrones esperados. Para Facebook debe contener `facebook.com/?` o `facebook.com/feed`. Para Instagram, `instagram.com/?`.

Si usas una cuenta en otro idioma, el feed puede tener una URL diferente. En ese caso puedes esperar a que el script agote el tiempo (5 minutos) y luego usar `import_cookies_from_file()` con las cookies exportadas manualmente desde tu navegador.

### `status=cookie_expired` aunque acabo de hacer login

Las cookies pueden expirar si la plataforma cierra la sesión de forma remota. Realiza el flujo de refresco (`run_login_refresh`) para capturar una sesión nueva:

```bash
python -m reaper.auth.login --refresh --account-id tu-account-id
```

### `status=rate_limited` — la cuenta no se usa

El sistema detectó demasiados errores consecutivos. Primero, comprueba que la cuenta funciona correctamente en el navegador. Luego puedes reactivarla:

```python
await manager.update_status(account_id, AccountStatus.ACTIVE, notes="Reactivada manualmente")
```

O refrescar las cookies si las actuales ya no son válidas:

```bash
python -m reaper.auth.login --refresh --account-id tu-account-id
```

### `get_account_for_request()` devuelve `None` teniendo cuentas registradas

Comprueba el estado de tus cuentas:

```python
cuentas = await manager.list_accounts("facebook")
for c in cuentas:
    print(c.username, c.status.value, c.has_cookies)
```

Los estados `suspended` y `cookie_expired` son los únicos que bloquean la selección. También es necesario que el archivo `cookies.json` exista en disco (`has_cookies=True`).

### Las cookies no se guardan (`has_cookies=False`)

Si creaste una cuenta con `add_account()` sin pasar `cookies` ni `cookies_file`, la cuenta queda en estado `unknown` sin cookies. Necesitas importarlas:

```python
# Opción 1: flujo de login interactivo
from reaper.auth.login import run_login_refresh
await run_login_refresh(manager, cuenta.account_id)

# Opción 2: desde archivo JSON externo
await manager.import_cookies_from_file(cuenta.account_id, "mis_cookies.json")
```

### Ver los logs detallados

Para ver exactamente qué cuenta está seleccionando Reaper y cómo evoluciona la actividad:

```python
from reaper.utils.logger import setup_logging
setup_logging(level="DEBUG")

manager = AccountManager()
resultado = await scrape("https://www.facebook.com/reel/123", account_manager=manager)
```

Los logs mostrarán líneas como:

```
INFO   Fetch autenticado | platform=facebook | username=mi_usuario | cookies=28 | peticiones_cuenta=12 | score_aprox=91%
INFO   Extracción completada | parser=ReelParser | url=https://...
WARNING Umbral de refresco alcanzado | username=mi_usuario | peticiones_desde_refresco=50
       — Importa cookies frescas con account_manager.import_cookies('uuid', new_cookies)
```
