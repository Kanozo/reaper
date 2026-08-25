"""
reaper/actions/__init__.py
==========================
API pública del módulo de acciones de escritura.

Incluye:

- ``ActionManager`` — orquestador que ejecuta posts, comentarios y likes
  sobre una sesión autenticada y persiste cada resultado.
- ``ActionType`` / ``ActionResult`` / ``ActionError`` — modelos de datos.
- ``SessionActor`` — lanzamiento de sesiones de navegador autenticadas.
- Storage de acciones (``BaseActionStorage``, ``build_action_storage`` y los
  tres backends: local, PostgreSQL y MongoDB).

Ejemplo::

    from reaper.actions import ActionManager
    from reaper.auth import AccountManager

    manager = ActionManager(account_manager=AccountManager())
    result = await manager.create_post(text="Hola", images=["foto.jpg"])
    print(result.to_dict())
"""

from reaper.actions.actor import SessionActor, SessionHandle
from reaper.actions.manager import ActionManager
from reaper.actions.models import (
    ActionError,
    ActionResult,
    ActionType,
)
from reaper.actions.storage import (
    BaseActionStorage,
    LocalActionStorage,
    MongoActionStorage,
    PostgresActionStorage,
    StorageError,
    build_action_storage,
)

__all__ = [
    "ActionManager",
    "ActionType",
    "ActionResult",
    "ActionError",
    "SessionActor",
    "SessionHandle",
    # Storage de acciones
    "BaseActionStorage",
    "LocalActionStorage",
    "PostgresActionStorage",
    "MongoActionStorage",
    "StorageError",
    "build_action_storage",
]
