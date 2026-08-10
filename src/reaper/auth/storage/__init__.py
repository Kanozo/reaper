"""
reaper/auth/storage/__init__.py
===============================
Re-exportaciones y factory del subsistema de almacenamiento.
"""

from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.auth.storage.config import (
    MongoConfig,
    PostgresConfig,
    StorageConfig,
    build_storage,
    find_config_file,
    load_storage_config,
)
from reaper.auth.storage.local import LocalFileStorage
from reaper.auth.storage.mongodb import MongoStorage
from reaper.auth.storage.postgres import PostgresStorage

__all__ = [
    "BaseAccountStorage",
    "LocalFileStorage",
    "PostgresStorage",
    "MongoStorage",
    "StorageError",
    # Configuración y factory
    "StorageConfig",
    "PostgresConfig",
    "MongoConfig",
    "build_storage",
    "load_storage_config",
    "find_config_file",
]
