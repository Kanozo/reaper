"""
reaper/actions/storage/mongodb.py
=================================
Backend de almacenamiento de acciones MongoDB usando ``motor``.

Cada ``ActionResult`` es un documento de la colección ``actions`` con
``_id = action_id`` y el resto de campos del registro como claves planas.

Diseño:
    - ``add`` usa ``replace_one(..., upsert=True)``: idempotente y no pisa
      campos extra que otros procesos pudieran haber añadido al documento.
    - ``list_all`` aplica filtros con ``$and`` y ordena por
      ``executed_at DESC`` (más recientes primero).
    - El cliente ``AsyncIOMotorClient`` es perezoso: no abre sockets hasta la
      primera operación. ``close()`` libera los recursos de forma explícita.
    - ``connect()`` crea un índice sobre ``(executed_at)`` para acelerar los
      listados por recencia.

Ejemplo::

    from reaper.actions.storage.mongodb import MongoActionStorage

    storage = MongoActionStorage(uri="mongodb://localhost:27017", database="reaper")
    await storage.connect()
    await storage.add(result)
    historial = await storage.list_all(action="post")
    await storage.close()
"""

from __future__ import annotations

from typing import Any

from reaper.actions.models import ActionResult
from reaper.actions.storage.base import BaseActionStorage, StorageError
from reaper.utils.logger import get_logger

try:
    import motor.motor_asyncio as aiomotor
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "El backend MongoActionStorage requiere la dependencia 'motor'. "
        "Instálala con: pip install 'reaper[db]'"
    ) from exc


logger = get_logger(__name__)

COLLECTION_DEFAULT: str = "actions"


class MongoActionStorage(BaseActionStorage):
    """Almacenamiento de acciones en MongoDB vía ``motor``.

    Args:
        uri: Cadena de conexión de MongoDB, p.ej.
            ``"mongodb://localhost:27017"``. Soporta auth y replica sets.
        database: Nombre de la base de datos. Defecto: ``"reaper"``.
        collection: Nombre de la colección de acciones. Defecto: ``"actions"``.

    Raises:
        StorageError: Si falla una operación de lectura/escritura.
    """

    def __init__(
        self,
        uri: str,
        database: str = "reaper",
        collection: str = COLLECTION_DEFAULT,
    ) -> None:
        self._client: aiomotor.AsyncIOMotorClient = aiomotor.AsyncIOMotorClient(uri)  # type: ignore[type-arg]
        self._database = database
        self._collection = collection
        self._indexes_ready = False

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Prepara índices de la colección (idempotente, perezoso)."""
        if self._indexes_ready:
            return
        try:
            await self._coll.create_index("executed_at")
        except Exception as exc:
            raise StorageError(
                f"No se pudo preparar la colección MongoDB: {exc}"
            ) from exc
        self._indexes_ready = True
        logger.debug(
            "MongoActionStorage conectado | db=%s | collection=%s",
            self._database,
            self._collection,
        )

    async def close(self) -> None:
        """Cierra el cliente, liberando sockets y pools internos."""
        self._client.close()

    # ── Operaciones de registros ──────────────────────────────────────────────

    async def add(self, action: ActionResult) -> None:
        """Inserta o reemplaza el documento de la acción (upsert)."""
        try:
            await self._coll.replace_one(
                {"_id": action.action_id},
                self._doc_from_action(action),
                upsert=True,
            )
        except Exception as exc:
            raise StorageError(
                f"No se pudo guardar la acción '{action.action_id}': {exc}"
            ) from exc
        logger.debug(
            "Acción guardada | action_id=%s | action=%s | status=%s",
            action.action_id,
            action.action,
            action.status,
        )

    async def get(self, action_id: str) -> ActionResult | None:
        """Carga un registro por su ``action_id`` o ``None`` si no existe."""
        try:
            doc = await self._coll.find_one({"_id": action_id})
        except Exception as exc:
            raise StorageError(
                f"No se pudo cargar la acción '{action_id}': {exc}"
            ) from exc
        return self._action_from_doc(doc) if doc else None

    async def list_all(
        self,
        account_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[ActionResult]:
        """Lista registros con filtros opcionales, más recientes primero."""
        filter_doc: dict[str, Any] = {}
        if account_id:
            filter_doc["account_id"] = account_id
        if action:
            filter_doc["action"] = action

        try:
            cursor = (
                self._coll.find(filter_doc)
                .sort("executed_at", -1)
                .limit(int(limit))
                if limit > 0
                else self._coll.find(filter_doc).sort("executed_at", -1)
            )
            docs = await cursor.to_list(length=None)
        except Exception as exc:
            raise StorageError(
                f"No se pudo listar las acciones (account_id={account_id}, "
                f"action={action}): {exc}"
            ) from exc

        results = [self._action_from_doc(doc) for doc in docs]
        logger.debug(
            "Acciones listadas | account_id=%s | action=%s | total=%d",
            account_id or "todas",
            action or "todas",
            len(results),
        )
        return results

    async def delete(self, action_id: str) -> bool:
        """Elimina el documento de la acción.

        Returns:
            ``True`` si se eliminó, ``False`` si no existía.
        """
        try:
            result = await self._coll.delete_one({"_id": action_id})
        except Exception as exc:
            raise StorageError(
                f"No se pudo eliminar la acción '{action_id}': {exc}"
            ) from exc

        deleted = int(result.deleted_count) > 0
        if deleted:
            logger.info("Acción eliminada | action_id=%s", action_id)
        return deleted

    async def count(
        self,
        account_id: str | None = None,
        action: str | None = None,
    ) -> int:
        """Cuenta documentos con filtros opcionales vía ``count_documents``."""
        filter_doc: dict[str, Any] = {}
        if account_id:
            filter_doc["account_id"] = account_id
        if action:
            filter_doc["action"] = action
        try:
            return int(await self._coll.count_documents(filter_doc))
        except Exception as exc:
            raise StorageError(
                f"No se pudo contar las acciones: {exc}"
            ) from exc

    # ── Helpers privados ──────────────────────────────────────────────────────

    @property
    def _coll(self) -> Any:
        """Colección activa (el cliente motor es perezoso)."""
        return self._client[self._database][self._collection]

    @staticmethod
    def _doc_from_action(action: ActionResult) -> dict[str, Any]:
        """Convierte un ``ActionResult`` en documento plano con ``_id``."""
        data = action.to_dict()
        data["_id"] = action.action_id
        data.pop("action_id")
        return data

    @staticmethod
    def _action_from_doc(doc: dict[str, Any]) -> ActionResult:
        """Reconstruye un ``ActionResult`` desde un documento MongoDB."""
        data = dict(doc)
        data["action_id"] = doc.get("_id")
        data.pop("_id", None)
        return ActionResult.from_dict(data)
