"""
reaper/auth/storage/base.py
===========================
Contrato abstracto para todos los backends de almacenamiento de cuentas.

Define la interfaz que cualquier implementación de almacenamiento debe cumplir,
ya sea almacenamiento local en archivos JSON (``LocalFileStorage``) o una
base de datos relacional/NoSQL (implementaciones futuras).

El propósito de esta abstracción es que ``AccountManager`` nunca dependa de
un backend concreto: solo interactúa con ``BaseAccountStorage``. Migrar de
almacenamiento local a base de datos requiere únicamente:

1. Crear una nueva clase que implemente ``BaseAccountStorage``.
2. Instanciar esa clase al construir ``AccountManager``.

No hay que modificar ``AccountManager`` ni ninguna otra capa.

Interfaz requerida::

    class BaseAccountStorage(ABC):
        # Operaciones de perfil
        async def save(account)     → None
        async def load(account_id)  → AccountProfile | None
        async def delete(account_id)→ bool
        async def list_all(platform?)→ list[AccountProfile]
        # Operaciones atómicas de actividad (optimización)
        async def update_activity(account_id, activity) → bool

Diseño de las operaciones atómicas:
    ``update_activity`` existe como operación separada de ``save`` porque
    la actividad se actualiza en cada petición (alta frecuencia), mientras
    que el perfil completo raramente cambia. Los backends de BD pueden
    optimizar esto con un UPDATE parcial en lugar de reescribir toda la fila.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from reaper.auth.models import AccountActivity, AccountProfile


class BaseAccountStorage(ABC):
    """Interfaz abstracta para backends de persistencia de cuentas.

    Todos los métodos son asíncronos para ser compatibles tanto con
    backends de archivos (asyncio + aiofiles) como con ORMs asíncronos
    (SQLAlchemy async, Motor para MongoDB, etc.).

    Implementaciones concretas:
        - ``LocalFileStorage``   — JSON en disco (incluida en la librería)
        - ``SqlAlchemyStorage``  — (ejemplo de extensión futura)
        - ``MongoStorage``       — (ejemplo de extensión futura)
    """

    # ── Operaciones de perfil completo ────────────────────────────────────────

    @abstractmethod
    async def save(self, account: AccountProfile) -> None:
        """Guarda o sobreescribe el perfil completo de una cuenta.

        Si la cuenta ya existe (mismo ``account_id``), la sobreescribe.
        Si no existe, la crea.

        Args:
            account: Perfil completo a persistir.

        Raises:
            StorageError: Si el backend no puede completar la operación.
        """
        ...

    @abstractmethod
    async def load(self, account_id: str) -> AccountProfile | None:
        """Carga un perfil por su identificador único.

        Args:
            account_id: UUID4 de la cuenta a cargar.

        Returns:
            ``AccountProfile`` si existe, ``None`` si no se encuentra.

        Raises:
            StorageError: Si hay un error de lectura inesperado.
        """
        ...

    @abstractmethod
    async def delete(self, account_id: str) -> bool:
        """Elimina una cuenta y todos sus datos asociados del backend.

        Nota: El archivo de cookies se elimina por ``AccountManager``,
        no por el storage, para mantener la separación de responsabilidades.

        Args:
            account_id: UUID4 de la cuenta a eliminar.

        Returns:
            ``True`` si se eliminó, ``False`` si no existía.

        Raises:
            StorageError: Si hay un error durante la eliminación.
        """
        ...

    @abstractmethod
    async def list_all(
        self,
        platform: str | None = None,
    ) -> list[AccountProfile]:
        """Retorna todos los perfiles almacenados, opcionalmente filtrados.

        Args:
            platform: Si se especifica (``"facebook"`` o ``"instagram"``),
                      retorna solo las cuentas de esa plataforma.
                      ``None`` retorna todas las plataformas.

        Returns:
            Lista de ``AccountProfile``. Puede estar vacía.

        Raises:
            StorageError: Si hay un error de lectura inesperado.
        """
        ...

    # ── Operaciones atómicas de actividad (optimización) ─────────────────────

    @abstractmethod
    async def update_activity(
        self,
        account_id: str,
        activity: AccountActivity,
    ) -> bool:
        """Actualiza solo el bloque de actividad de una cuenta existente.

        Esta operación es más eficiente que ``save()`` completo cuando
        solo ha cambiado la actividad (caso más frecuente: post-petición).

        Los backends de BD pueden implementarla como un UPDATE parcial
        (``UPDATE accounts SET activity=? WHERE account_id=?``) en lugar
        de reescribir todo el registro.

        Args:
            account_id: UUID4 de la cuenta a actualizar.
            activity:   Objeto ``AccountActivity`` con los valores actualizados.

        Returns:
            ``True`` si se actualizó, ``False`` si la cuenta no existe.

        Raises:
            StorageError: Si hay un error durante la actualización.
        """
        ...

    # ── Operaciones de conveniencia (implementadas en base) ───────────────────

    async def exists(self, account_id: str) -> bool:
        """Comprueba si una cuenta existe en el backend.

        Implementación por defecto basada en ``load()``. Los backends
        eficientes pueden sobreescribirla con una query de existencia.

        Args:
            account_id: UUID4 de la cuenta a comprobar.

        Returns:
            ``True`` si existe, ``False`` si no.
        """
        return await self.load(account_id) is not None


class StorageError(Exception):
    """Error en una operación de almacenamiento.

    Envuelve excepciones de bajo nivel (IOError, sqlalchemy.exc, etc.)
    para que ``AccountManager`` no dependa de implementaciones concretas.
    """
