"""
reaper/actions/storage/base.py
==============================
Contrato abstracto para el almacenamiento de acciones de escritura.

Define la interfaz que cualquier backend debe cumplir para persistir los
registros de ``ActionResult`` generados por las acciones de escritura
(posts, comentarios, likes). Sigue el mismo patrón que
``reaper.auth.storage.BaseAccountStorage``:

- ``LocalActionStorage`` — JSON en disco (default, sin dependencias).
- ``PostgresActionStorage`` — tabla ``actions`` vía ``asyncpg``.
- ``MongoActionStorage`` — colección ``actions`` vía ``motor``.

El propósito de la abstracción es que ``ActionManager`` nunca dependa de un
backend concreto: solo interactúa con ``BaseActionStorage``. Migrar de
almacenamiento local a base de datos solo requiere instanciar otro backend,
sin tocar la lógica de ejecución de acciones.

Interfaz requerida::

    class BaseActionStorage(ABC):
        async def add(action)            → None
        async def get(action_id)         → ActionResult | None
        async def list_all(account_id?, action?, limit?) → list[ActionResult]
        async def delete(action_id)      → bool
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from reaper.actions.models import ActionResult
from reaper.auth.storage.base import StorageError


class BaseActionStorage(ABC):
    """Interfaz abstracta para backends de persistencia de acciones.

    Todos los métodos son asíncronos, igual que en el storage de cuentas,
    para ser compatibles con backends de archivos y de base de datos.

    Implementaciones concretas:
        - ``LocalActionStorage``   — JSON en disco (default).
        - ``PostgresActionStorage``— PostgreSQL 15+ vía ``asyncpg``.
        - ``MongoActionStorage``   — MongoDB vía ``motor``.
    """

    # ── Ciclo de vida ─────────────────────────────────────────────────────────
    # No-ops por defecto; los backends con pools/conexiones los implementan.

    async def connect(self) -> None:
        """Inicializa conexiones/esquema si el backend lo requiere.

        Por defecto no hace nada. ``PostgresActionStorage`` crea aquí su
        tabla; ``MongoActionStorage`` prepara sus índices.
        """
        return None

    async def close(self) -> None:
        """Libera los recursos (pools, clientes) del backend.

        Por defecto no hace nada.
        """
        return None

    # ── Operaciones de registros ──────────────────────────────────────────────

    @abstractmethod
    async def add(self, action: ActionResult) -> None:
        """Persiste un registro de acción.

        Si ya existe un registro con el mismo ``action_id``, lo sobreescribe.

        Args:
            action: Registro ``ActionResult`` a persistir.

        Raises:
            StorageError: Si el backend no puede completar la operación.
        """
        ...

    @abstractmethod
    async def get(self, action_id: str) -> ActionResult | None:
        """Carga un registro por su ``action_id``.

        Args:
            action_id: UUID4 del registro.

        Returns:
            ``ActionResult`` si existe, ``None`` si no se encuentra.

        Raises:
            StorageError: Si hay un error de lectura inesperado.
        """
        ...

    @abstractmethod
    async def list_all(
        self,
        account_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[ActionResult]:
        """Lista registros de acciones con filtros opcionales.

        Args:
            account_id: Si se indica, solo acciones de esa cuenta.
            action:     Si se indica, solo acciones de ese tipo
                        (valor de ``ActionType``).
            limit:      Número máximo de registros a retornar (más recientes
                        primero). Defecto: 50.

        Returns:
            Lista de ``ActionResult``. Puede estar vacía.

        Raises:
            StorageError: Si hay un error de lectura inesperado.
        """
        ...

    @abstractmethod
    async def delete(self, action_id: str) -> bool:
        """Elimina un registro de acción.

        Args:
            action_id: UUID4 del registro a eliminar.

        Returns:
            ``True`` si se eliminó, ``False`` si no existía.

        Raises:
            StorageError: Si hay un error durante la eliminación.
        """
        ...

    # ── Operaciones de conveniencia (implementadas en base) ───────────────────

    async def count(
        self,
        account_id: str | None = None,
        action: str | None = None,
    ) -> int:
        """Cuenta los registros que coinciden con los filtros.

        Implementación por defecto basada en :meth:`list_all`. Los backends
        eficientes pueden sobreescribirla con una consulta de agregación.

        Args:
            account_id: Filtro por cuenta.
            action:     Filtro por tipo de acción.

        Returns:
            Número de registros que coinciden.
        """
        return len(await self.list_all(account_id=account_id, action=action, limit=0))


__all__ = ["BaseActionStorage", "StorageError"]
