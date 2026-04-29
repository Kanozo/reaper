# Gestión del pool de cuentas

## Estados de una cuenta

| Estado | Descripción | ¿Se usa en peticiones? |
|---|---|---|
| `active` | Operativa, cookies válidas | Sí — prioridad normal |
| `needs_refresh` | Umbral de peticiones alcanzado sin auto-refresh | Sí — prioridad reducida (×0.5) |
| `rate_limited` | Muchos errores consecutivos detectados | Sí — prioridad muy reducida (×0.2) |
| `cookie_expired` | Las cookies ya no son válidas | No — requiere refresco manual |
| `suspended` | Cuenta bloqueada por la plataforma | No — requiere intervención manual |
| `unknown` | Estado inicial, sin cookies importadas | No |

## Cómo Reaper elige la cuenta en cada petición

El `AccountRotator` puntúa cada cuenta candidata con esta fórmula:

```
score = (tasa_de_éxito × 0.6)
      + (antigüedad_de_uso × 0.3)
      - (edad_de_cookies × 0.1)
```

- **Tasa de éxito**: porcentaje histórico de peticiones exitosas.
- **Antigüedad de uso**: favorece las cuentas que llevan más tiempo sin usarse.
  Las cuentas nunca usadas reciben el score máximo en este componente.
- **Edad de cookies**: penaliza levemente las cuentas próximas al umbral. Con
  el auto-refresh activo este componente tiende a 0 en condiciones normales.

## Refresco automático de cookies

Después de cada petición exitosa, Reaper compara las cookies que inyectó
con las que el servidor devolvió en la respuesta. Si difieren — porque el
servidor extendió el TTL o rotó un token de seguridad — las persiste en disco
automáticamente. **No necesitas hacer nada**: las sesiones se mantienen activas
de forma indefinida mientras la plataforma no las invalide remotamente.

El aviso `NEEDS_REFRESH` en los logs solo aparece si el servidor no devuelve
cookies actualizadas durante N peticiones consecutivas (situación inusual).
Cuando aparezca, usa el [login interactivo](login.md) para renovar la sesión.

## Ciclo de vida de una cuenta

```
add_account() sin cookies        → UNKNOWN
    ↓ import_cookies() / run_login_new_account()
cookies importadas               → ACTIVE, contador=0

Operación normal con auto-refresh:
    petición exitosa
        → servidor devuelve Set-Cookie actualizados
        → cookies.json sobrescrito con versión fresca
        → contador reseteado a 0
        → sesión se mantiene indefinidamente

Caso degradado (servidor no actualiza cookies):
    petición exitosa sin Set-Cookie
        → contador sube
        → al llegar al threshold → NEEDS_REFRESH + WARNING
        → run_login_refresh() → ACTIVE, contador=0

Si muchos fallos seguidos     → RATE_LIMITED (sigue usable, score ×0.2)
    → update_status(ACTIVE) o run_login_refresh()

Si bloqueada por plataforma   → SUSPENDED (excluida del rotador)
    → remove_account() si ya no se va a rehabilitar
```

## Ver el estado del pool

```python
import asyncio
from reaper import AccountManager

async def main():
    manager = AccountManager()

    resumen = await manager.pool_summary()
    print(f"Total     : {resumen['total']}")
    print(f"Activas   : {resumen['active']}")
    print(f"Refresco  : {resumen['needs_refresh']}")
    print(f"Rate-limit: {resumen['rate_limited']}")
    print(f"Suspendidas:{resumen['suspended']}")
    print(f"Expiradas : {resumen['cookie_expired']}")

asyncio.run(main())
```

## Ver el ranking de cuentas

```python
async def ver_ranking():
    manager = AccountManager()

    ranking = await manager.get_ranked_accounts("facebook")
    print(f"{'#':>3}  {'Usuario':25}  {'Score':>6}  {'Éxitos':>7}  {'Total':>6}  Estado")
    print("-" * 70)
    for r in ranking:
        print(
            f"{r['rank']:>3}. {r['username']:25}  "
            f"{r['score']:>6.3f}  "
            f"{r['success_rate']:>6.0%}  "
            f"{r['total_requests']:>6}  "
            f"{r['status']}"
        )
```

Ejemplo de salida:

```
  #  Usuario                    Score  Éxitos   Total  Estado
----------------------------------------------------------------------
  1. cuenta_nueva               0.900   100%       0  unknown
  2. cuenta_activa              0.612    95%      47  active
  3. cuenta_vieja               0.301    72%     124  needs_refresh
  4. cuenta_problemas           0.115    60%      31  rate_limited
```

## Ver errores recientes de una cuenta

```python
async def ver_errores():
    manager = AccountManager()

    cuentas = await manager.list_accounts()
    for cuenta in cuentas:
        if cuenta.activity.recent_errors:
            print(f"\n{cuenta.username} — últimos errores ({len(cuenta.activity.recent_errors)}):")
            for error in cuenta.activity.recent_errors[:5]:
                print(f"  {error}")
```
