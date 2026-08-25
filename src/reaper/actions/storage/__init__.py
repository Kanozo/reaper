"""
reaper/actions/storage/__init__.py
==================================
Re-exportaciones y factory del almacenamiento de acciones de escritura.

El subsistema de acciones reutiliza la sección ``[storage]`` de ``reaper.toml``
de las cuentas: mismo backend, misma conexión y misma base de datos, pero una
tabla/colección/directorio separado para no mezclar los registros de acciones
con los perfiles de cuentas.

Backends disponibles (mismo patrón que ``reaper.auth.storage``):
    - ``LocalActionStorage``    — JSON en disco (default, sin dependencias).
    - ``PostgresActionStorage`` — tabla ``actions`` vía ``asyncpg``.
    - ``MongoActionStorage``    — colección ``actions`` vía ``motor``.

La factory :func:`build_action_storage` decide el backend leyendo la
configuración, igual que :func:`reaper.auth.storage.build_storage`.
"""

from reaper.actions.storage.base import BaseActionStorage, StorageError
from reaper.actions.storage.config import build_action_storage
from reaper.actions.storage.local import LocalActionStorage
from reaper.actions.storage.mongodb import MongoActionStorage
from reaper.actions.storage.postgres import PostgresActionStorage

__all__ = [
    "BaseActionStorage",
    "LocalActionStorage",
    "PostgresActionStorage",
    "MongoActionStorage",
    "StorageError",
    "build_action_storage",
]
