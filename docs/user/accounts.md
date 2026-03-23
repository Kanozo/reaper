# Cuentas autenticadas

Las cuentas autenticadas permiten a Reaper realizar peticiones usando sesiones
de Facebook o Instagram reales. Puedes registrar tantas cuentas como necesites
y Reaper las rotará automáticamente eligiendo siempre la más adecuada.

!!! info "Compatibilidad"
    Si no usas cuentas, Reaper funciona exactamente igual que en versiones
    anteriores. El parámetro `account_manager` es siempre opcional.

## Configuración inicial

```python
from reaper import AccountManager

# Almacena todo en data/accounts/ (se crea automáticamente)
manager = AccountManager()

# Directorio personalizado
manager = AccountManager(accounts_dir="/mis/cuentas")

# Umbral de refresco: peticiones antes de pedir cookies nuevas
manager = AccountManager(cookie_refresh_threshold=100)
```

## Añadir una cuenta

El flujo recomendado es usar el [login interactivo](login.md). Si ya tienes
las cookies exportadas, puedes importarlas directamente:

=== "Con login interactivo"

    ```python
    import asyncio
    from reaper import AccountManager
    from reaper.auth.login import run_login_new_account

    async def main():
        manager = AccountManager()
        cuenta = await run_login_new_account(
            manager=manager,
            platform="facebook",
            username="mi_usuario@gmail.com",
        )
        print(f"Registrada: {cuenta.account_id}")

    asyncio.run(main())
    ```

=== "Con archivo de cookies"

    ```python
    cuenta = await manager.add_account(
        platform="facebook",
        username="mi_usuario",
        cookies_file="exports/fb_cookies.json",
    )
    ```

=== "Con cookies en memoria"

    ```python
    cookies = [
        {"name": "c_user", "value": "123456", "domain": ".facebook.com",
         "path": "/", "expires": 9999999999.0, "httpOnly": False,
         "secure": True, "sameSite": "None"},
        # ... resto de cookies
    ]
    cuenta = await manager.add_account(
        platform="facebook",
        username="mi_usuario",
        cookies=cookies,
    )
    ```

## Usar cuentas en el scraping

```python
import asyncio
from reaper import scrape, Reaper, AccountManager

async def main():
    manager = AccountManager()

    # Con la función scrape()
    result = await scrape(
        "https://www.facebook.com/reel/123456",
        account_manager=manager,
    )

    # Con la clase Reaper (reutiliza el manager entre peticiones)
    reaper = Reaper(account_manager=manager)
    result1 = await reaper.scrape("https://www.facebook.com/reel/111")
    result2 = await reaper.scrape("https://www.instagram.com/p/abc/")

asyncio.run(main())
```

Reaper selecciona la cuenta óptima automáticamente. No necesitas indicar
cuál usar en cada petición.

## Gestionar el pool de cuentas

```python
async def gestionar():
    manager = AccountManager()

    # Ver todas las cuentas
    todas = await manager.list_accounts()
    for c in todas:
        print(f"[{c.platform}] {c.username} — {c.status.value}")

    # Filtrar por plataforma
    fb = await manager.list_accounts("facebook")
    ig = await manager.list_accounts("instagram")

    # Ver ranking con scores del rotador
    ranking = await manager.get_ranked_accounts("facebook")
    for r in ranking:
        print(
            f"#{r['rank']} {r['username']:25} "
            f"score={r['score']:.3f}  "
            f"éxitos={r['success_rate']:.0%}  "
            f"peticiones={r['total_requests']}"
        )

    # Resumen del estado del pool
    resumen = await manager.pool_summary()
    print(f"Total: {resumen['total']} | Activas: {resumen['active']}")

    # Eliminar una cuenta (borra perfil y cookies del disco)
    await manager.remove_account("account-id-completo")
```

## Cambiar el estado de una cuenta manualmente

```python
from reaper import AccountStatus

# Reactivar tras solucionar un problema
await manager.update_status(
    account_id="uuid...",
    status=AccountStatus.ACTIVE,
    notes="Reactivada el 2026-01-15 tras verificar sesión",
)

# Suspender definitivamente
await manager.update_status(
    account_id="uuid...",
    status=AccountStatus.SUSPENDED,
    notes="Cuenta bloqueada por la plataforma",
)
```

## Exportar cookies de una cuenta

```python
cookies = await manager.export_cookies(account_id)
# Retorna list[dict] en formato Playwright, o None si no hay cookies
```
