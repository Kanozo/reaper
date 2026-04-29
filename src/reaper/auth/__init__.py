"""
reaper/auth/__init__.py
=======================
API pública del módulo de gestión de cuentas autenticadas.

Importaciones recomendadas para el consumidor::

    from reaper.auth import AccountManager, AccountProfile, AccountStatus

    manager = AccountManager()
    account = await manager.add_account("facebook", "mi_usuario", cookies=[...])
"""

from reaper.auth.account_manager import AccountManager
from reaper.auth.models import (
    AccountActivity,
    AccountProfile,
    AccountStatus,
    NON_SELECTABLE_STATUSES,
    SUPPORTED_PLATFORMS,
)
from reaper.auth.rotator import AccountRotator
from reaper.auth.login import run_login_new_account, run_login_refresh
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.auth.storage.local import LocalFileStorage

__all__ = [
    # Manager principal
    "AccountManager",
    # Modelos
    "AccountProfile",
    "AccountActivity",
    "AccountStatus",
    "NON_SELECTABLE_STATUSES",
    "SUPPORTED_PLATFORMS",
    # Rotador (para uso avanzado / extensión)
    "AccountRotator",
    # Login interactivo
    "run_login_new_account",
    "run_login_refresh",
    # Storage
    "BaseAccountStorage",
    "LocalFileStorage",
    "StorageError",
]
