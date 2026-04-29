# Login interactivo

El login en Reaper es siempre **manual e interactivo**. Esto es una decisión
de diseño deliberada: Facebook e Instagram detectan los intentos de login
automatizado con alta precisión. El enfoque manual es intrínsecamente más
robusto porque el usuario interactúa con el navegador de forma completamente
normal, incluidas verificaciones 2FA, SMS y CAPTCHAs.

Reaper solo interviene una vez que el login ya fue completado: captura las
cookies de sesión y las persiste para uso futuro.

## Registrar una cuenta nueva

### Desde Python

```python
import asyncio
from reaper import AccountManager
from reaper.auth.login import run_login_new_account

async def main():
    manager = AccountManager()

    cuenta = await run_login_new_account(
        manager=manager,
        platform="facebook",          # o "instagram"
        username="usuario@gmail.com", # identificador legible (no se usa para auth)
        notes="Cuenta principal",     # opcional
    )

    if cuenta:
        print(f"OK — account_id: {cuenta.account_id}")
        print(f"Estado: {cuenta.status.value}")    # "active"
    else:
        print("Login no completado")

asyncio.run(main())
```

### Desde la terminal

```bash
# Modo interactivo (hace preguntas)
python -m reaper.auth.login

# Especificando plataforma y usuario
python -m reaper.auth.login --platform facebook --username usuario@gmail.com

# Instagram
python -m reaper.auth.login --platform instagram --username mi_usuario

# Directorio personalizado
python -m reaper.auth.login --platform facebook --username usuario \
    --accounts-dir /datos/mis_cuentas

# Umbral de refresco personalizado
python -m reaper.auth.login --platform facebook --username usuario \
    --threshold 100
```

## Lo que ocurre paso a paso

1. Se abre Firefox **visible** en la página de login de la plataforma.
2. El usuario introduce sus credenciales manualmente.
3. Si hay 2FA, SMS o verificación de dispositivo, el usuario los completa.
4. El script monitoriza la URL del navegador cada segundo buscando la URL
   del feed principal (señal de login exitoso).
5. Al detectar el login, extrae las cookies del contexto de Playwright.
6. Las cookies se guardan en disco y se registran en el `AccountManager`.
7. El navegador se cierra automáticamente.

!!! warning "No cierres el navegador manualmente"
    El script detecta el login por la URL. Si cierras el navegador antes de
    que lo detecte, el proceso termina sin guardar cookies.

!!! tip "Tiempo disponible"
    Tienes **5 minutos** para completar el login. Más que suficiente para
    cualquier verificación de seguridad de la plataforma.

## Refrescar cookies de una cuenta existente

Las cookies tienen una vida útil limitada. Cuando una cuenta alcanza el
umbral de peticiones, aparece este aviso en los logs:

```
WARNING  Umbral de refresco alcanzado | username=mi_usuario
         — Importa cookies frescas con import_cookies('uuid', new_cookies)
```

La cuenta pasa a estado `needs_refresh` y sigue funcionando con prioridad
reducida. Para restaurarla completamente:

### Desde Python

```python
import asyncio
from reaper import AccountManager
from reaper.auth.login import run_login_refresh

async def main():
    manager = AccountManager()

    # Ver qué cuentas necesitan refresco
    cuentas = await manager.list_accounts()
    para_refrescar = [
        c for c in cuentas
        if c.status.value in ("needs_refresh", "cookie_expired")
    ]

    for cuenta in para_refrescar:
        print(f"Refrescando: {cuenta.username} ({cuenta.platform})")
        ok = await run_login_refresh(manager, cuenta.account_id)
        print("OK" if ok else "No completado")

asyncio.run(main())
```

### Desde la terminal

```bash
# Modo interactivo — muestra las cuentas y pregunta cuál refrescar
python -m reaper.auth.login --refresh

# Especificando el account_id
python -m reaper.auth.login --refresh \
    --account-id 3f2e1a-0000-0000-0000-000000000000
```

El refresco sigue el mismo flujo que el login nuevo: abre Firefox, espera
a que el usuario inicie sesión, captura las cookies y las importa.
Al terminar el estado vuelve a `active` y el contador de peticiones se resetea a 0.

## Importar cookies sin login interactivo

Si exportaste las cookies desde tu navegador con una extensión como
[Cookie-Editor](https://cookie-editor.com/), puedes importarlas directamente:

```python
# Desde archivo JSON externo
ok = await manager.import_cookies_from_file(
    account_id="uuid-de-la-cuenta",
    cookies_file="cookies_exportadas.json",
)

# Desde lista en memoria
ok = await manager.import_cookies(
    account_id="uuid-de-la-cuenta",
    cookies=lista_de_cookies,
)
```

El archivo debe ser una lista JSON con objetos en formato Playwright:

```json
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
  }
]
```
