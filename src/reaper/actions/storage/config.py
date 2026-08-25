"""
reaper/actions/storage/config.py
================================
Factory del almacenamiento de acciones a partir de la configuración TOML.

Reutiliza la sección ``[storage]`` de ``reaper.toml`` definida para las
cuentas: el backend elegido (``local`` | ``postgres`` | ``mongodb``) se
comparte, y las acciones se persisten en un directorio/tabla/colección
separados.

Los nombres de esos recursos separados son configurables y opcionales::

    [storage]
    backend = "local"
    accounts_dir = "data/accounts"
    actions_dir  = "data/actions"

    [storage.postgres]
    dsn = "postgresql://user:pass@localhost:5432/reaper"
    table = "accounts"
    actions_table = "actions"

    [storage.mongodb]
    uri = "mongodb://localhost:27017"
    database = "reaper"
    collection = "accounts"
    actions_collection = "actions"

Si no se indica ``actions_dir``/``actions_table``/``actions_collection``, se
usan los valores por defecto del backend correspondiente.

Ejemplo::

    from reaper.actions.storage.config import build_action_storage

    storage = build_action_storage()   # lee reaper.toml (o defaults)
    await storage.connect()
    await storage.add(result)
"""

from __future__ import annotations

from reaper.actions.storage.base import BaseActionStorage, StorageError
from reaper.auth.storage.config import (
    StorageConfig,
    load_storage_config,
)
from reaper.utils.logger import get_logger

logger = get_logger(__name__)


def build_action_storage(
    config: StorageConfig | None = None,
) -> BaseActionStorage:
    """Instancia el backend de almacenamiento de acciones de la configuración.

    Args:
        config: Configuración de storage (la misma de las cuentas). Si es
            ``None``, la carga desde el fichero con
            :func:`reaper.auth.storage.load_storage_config`.

    Returns:
        ``LocalActionStorage``, ``PostgresActionStorage`` o
        ``MongoActionStorage`` según ``config.backend``.

    Raises:
        ValueError: Si el backend no está soportado o falta su configuración.
    """
    config = config or load_storage_config()

    match config.backend:
        case "local":
            from reaper.actions.storage.local import LocalActionStorage

            return LocalActionStorage(config.actions_dir)
        case "postgres":
            if config.postgres is None:
                raise ValueError(
                    "backend='postgres' requiere una sección [storage.postgres]."
                )
            from reaper.actions.storage.postgres import PostgresActionStorage

            return PostgresActionStorage(
                dsn=config.postgres.dsn,
                pool_size=config.postgres.pool_size,
                max_overflow=config.postgres.max_overflow,
                timeout=config.postgres.timeout,
                table=config.postgres.actions_table,
            )
        case "mongodb":
            if config.mongodb is None:
                raise ValueError(
                    "backend='mongodb' requiere una sección [storage.mongodb]."
                )
            from reaper.actions.storage.mongodb import MongoActionStorage

            return MongoActionStorage(
                uri=config.mongodb.uri,
                database=config.mongodb.database,
                collection=config.mongodb.actions_collection,
            )
        case _:
            raise ValueError(
                f"Backend '{config.backend}' no soportado. "
                f"Opciones: ['local', 'postgres', 'mongodb']"
            )


__all__ = ["BaseActionStorage", "StorageError", "build_action_storage"]
