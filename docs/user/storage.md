# Almacenamiento de cuentas

Reaper puede guardar tus cuentas (perfil, actividad y cookies de sesión) en
tres backends distintos, elegibles desde un único fichero de configuración:

| Backend | Descripción | Requiere |
|---------|-------------|----------|
| `local` | Archivos JSON en disco (comportamiento original) | nada |
| `postgres` | PostgreSQL vía `asyncpg` | `pip install "reaper[db]"` y un servidor PG |
| `mongodb` | MongoDB vía `motor` | `pip install "reaper[db]"` y un servidor Mongo |

La elección es **todo o nada**: si eliges `postgres`, tanto los perfiles como
las cookies de sesión viven en PostgreSQL. No hay mezcla de backends.

## ¿Dónde están mis sesiones?

Con el backend `local`, cada cuenta ocupa un directorio propio:

```
data/accounts/
├── facebook/
│   └── {account_id}/
│       ├── account.json   ← perfil (sin cookies)
│       └── cookies.json   ← cookies de sesión (formato Playwright)
└── instagram/
    └── {account_id}/...
```

!!! warning "Rutas relativas"
    Por defecto, `data/accounts` se resuelve **respecto al directorio de
    trabajo** desde el que se ejecuta el proceso. Si lanzas Reaper desde
    `$HOME`, las sesiones caen en `~/data/accounts`; desde `/tmp`, en
    `/tmp/data/accounts`. Para ubicaciones estables, indica una ruta
    absoluta en el fichero de configuración.

Con `postgres` o `mongodb`, las cuentas viven en la base de datos; no se
crea ningún directorio local de cuentas.

## Configuración (fichero `reaper.toml`)

Reaper busca `reaper.toml` automáticamente (primer hallazgo gana):

1. Ruta explícita: `AccountManager(storage=...)` o `build_storage(ruta)`.
2. Variable de entorno `REAPER_CONFIG`.
3. `./reaper.toml` (directorio de trabajo actual).
4. `~/.config/reaper/reaper.toml`.
5. `/etc/reaper/reaper.toml`.

### Local (por defecto)

```toml
[storage]
backend = "local"
accounts_dir = "/var/lib/reaper/accounts"   # ruta absoluta recomendada
```

### PostgreSQL

```toml
[storage]
backend = "postgres"

[storage.postgres]
dsn = "postgresql://usuario:clave@localhost:5432/reaper"
pool_size = 5        # conexiones del pool
max_overflow = 10    # conexiones extra bajo carga
timeout = 30.0       # segundos de espera de conexión
table = "accounts"   # nombre de la tabla (se crea sola al conectar)
```

### MongoDB

```toml
[storage]
backend = "mongodb"

[storage.mongodb]
uri = "mongodb://localhost:27017"
database = "reaper"
collection = "accounts"
```

## Uso desde Python

El `AccountManager` respeta automáticamente la configuración:

```python
import asyncio
from reaper import AccountManager

async def main():
    # Lee reaper.toml y usa el backend indicado (default: local)
    manager = AccountManager()

    cuenta = await manager.add_account(
        platform="facebook",
        username="mi_usuario",
        cookies_file="exports/fb_cookies.json",
    )
    # Todo el ciclo de vida (perfil + cookies) va al backend configurado.

    await manager.close()   # libera pools de conexión (PG/Mongo)
    return cuenta

asyncio.run(main())
```

También puedes instanciar el backend directamente o ignorar el fichero:

```python
from reaper import AccountManager
from reaper.auth.storage import PostgresStorage

manager = AccountManager(
    storage=PostgresStorage(dsn="postgresql://user:pass@localhost:5432/reaper")
)
await manager.close()
```

## Notas de seguridad

- Las cookies contienen **tokens de autenticación**. En los backends de BD se
  guardan como JSONB/BSON **sin cifrar**: protege el acceso a la base de datos
  y no comprometas credenciales.
- Si `reaper.toml` contiene credenciales, restringe permisos:
  `chmod 600 reaper.toml` — y no lo subas a control de versiones.
