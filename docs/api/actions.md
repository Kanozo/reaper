# API Reference — `reaper.actions`

Acciones de escritura autenticadas sobre Facebook. Todos los símbolos
pueden importarse desde `reaper.actions` y, los principales, desde el paquete
raíz `reaper`.

```python
from reaper import ActionManager, ActionType, ActionResult, ActionError
from reaper.actions import SessionActor, build_action_storage
```

---

## `ActionManager`

Orquesta el ciclo completo de una acción: resuelve la cuenta, abre la sesión,
ejecuta el flujo de UI, confirma por DOM, persiste el resultado y actualiza la
actividad/cookies de la cuenta.

::: reaper.actions.manager.ActionManager
    options:
      members:
        - __init__
        - create_post
        - share_post
        - comment
        - like
        - close
        - storage

---

## Modelos

### `ActionType`

::: reaper.actions.models.ActionType

### `ActionResult`

::: reaper.actions.models.ActionResult
    options:
      members:
        - to_dict
        - from_dict

### `ActionError`

::: reaper.actions.models.ActionError

---

## `SessionActor` y `SessionHandle`

Lanzamiento de sesiones de navegador autenticadas (Camoufox) en las que se
ejecutan los flujos de UI.

::: reaper.actions.actor.SessionActor
    options:
      members:
        - __init__
        - use_browser_factory
        - force_fingerprint
        - session

::: reaper.actions.actor.SessionHandle

---

## Storage de acciones

Reutiliza la sección `[storage]` de `reaper.toml`: mismo backend que las
cuentas, e `actions_dir` / `actions_table` / `actions_collection` separados
para no mezclar registros.

::: reaper.actions.storage.base.BaseActionStorage
    options:
      members:
        - connect
        - close
        - add
        - get
        - list_all
        - delete
        - count

::: reaper.actions.storage.base.StorageError

::: reaper.actions.storage.local.LocalActionStorage
    options:
      members:
        - __init__
        - add
        - get
        - list_all
        - delete
        - count

::: reaper.actions.storage.postgres.PostgresActionStorage
    options:
      members:
        - __init__
        - connect
        - close
        - add
        - get
        - list_all
        - delete
        - count

::: reaper.actions.storage.mongodb.MongoActionStorage
    options:
      members:
        - __init__
        - connect
        - close
        - add
        - get
        - list_all
        - delete
        - count

::: reaper.actions.storage.config.build_action_storage

---

## Utilidades

::: reaper.actions.utils.resolve_group

::: reaper.actions.utils.extract_group_id

::: reaper.actions.utils.resolve_profile_url

::: reaper.actions.utils.validate_image_paths

::: reaper.actions.utils.post_id_from_url

::: reaper.actions.utils.cookies_differ

---

## Configuración (`reaper.toml`)

El backend de acciones se elige con la misma sección `[storage]` de las
cuentas. Los nombres de los recursos separados son opcionales:

```toml
[storage]
backend = "local"
accounts_dir = "data/accounts"
actions_dir  = "data/actions"        # default de LocalActionStorage

[storage.postgres]
dsn = "postgresql://user:pass@localhost:5432/reaper"
actions_table = "actions"            # default de PostgresActionStorage

[storage.mongodb]
uri = "mongodb://localhost:27017"
actions_collection = "actions"       # default de MongoActionStorage
```

Si no se indica el nombre separado, se usa el valor por defecto de cada
backend. La conexión y credenciales se comparten con el storage de cuentas.