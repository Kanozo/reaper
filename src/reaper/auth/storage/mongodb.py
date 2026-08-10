"""
reaper/auth/storage/mongodb.py
==============================
Backend de almacenamiento MongoDB usando ``motor`` (driver async oficial).

Cada ``AccountProfile`` es un documento de la colección ``accounts`` con
``_id = account_id``. El perfil, la actividad y las cookies de sesión viven
en el mismo documento, de modo que todo el ciclo de vida de una cuenta se
opera contra MongoDB.

Diseño:
    - ``save()`` usa ``$set`` sobre los campos de perfil con ``upsert=True``:
      nunca toca el campo ``cookies``, evitando pisarlas al guardar el perfil.
    - ``update_activity`` usa ``$set`` parcial del subdocumento ``activity``
      (alta frecuencia) sin reescribir el resto del documento.
    - ``save_cookies``/``load_cookies`` operan sobre los campos ``cookies``
      y ``cookies_path`` del mismo documento.
    - El cliente ``AsyncIOMotorClient`` es perezoso: no abre sockets hasta la
      primera operación. ``close()`` libera los recursos de forma explícita.

Ejemplo::

    from reaper.auth.storage.mongodb import MongoStorage

    storage = MongoStorage(uri="mongodb://localhost:27017", database="reaper")
    await storage.connect()
    await storage.save(account_profile)
    cuentas = await storage.list_all(platform="facebook")
    await storage.close()
"""

from __future__ import annotations

import logging
from typing import Any

from reaper.auth.models import AccountActivity, AccountProfile
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.utils.logger import get_logger

try:
    import motor.motor_asyncio as aiomotor
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "El backend MongoStorage requiere la dependencia 'motor'. "
        "Instálala con: pip install 'reaper[db]'"
    ) from exc


logger = get_logger(__name__)

COLLECTION_DEFAULT: str = "accounts"

# Locator simbólico que se guarda en ``AccountProfile.cookies_path``.
COOKIES_LOCATOR_TEMPLATE: str = "db://mongodb/{account_id}#cookies"


class MongoStorage(BaseAccountStorage):
    """Almacenamiento de cuentas en MongoDB vía ``motor``.

    Args:
        uri: Cadena de conexión de MongoDB, p.ej.
            ``"mongodb://localhost:27017"``. Soporta auth y replica sets.
        database: Nombre de la base de datos. Defecto: ``"reaper"``.
        collection: Nombre de la colección de cuentas. Defecto: ``"accounts"``.

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
            await self._coll.create_index("platform")
        except Exception as exc:
            raise StorageError(
                f"No se pudo preparar la colección MongoDB: {exc}"
            ) from exc
        self._indexes_ready = True
        logger.debug(
            "MongoStorage conectado | db=%s | collection=%s",
            self._database,
            self._collection,
        )

    async def close(self) -> None:
        """Cierra el cliente, liberando sockets y pools internos."""
        self._client.close()

    # ── Operaciones de perfil completo ────────────────────────────────────────

    async def save(self, account: AccountProfile) -> None:
        """Inserta o actualiza los campos de perfil (sin tocar ``cookies``)."""
        update: dict[str, Any] = {
            "$set": {
                "platform": account.platform,
                "username": account.username,
                "email": account.email,
                "status": account.status.value,
                "cookies_path": account.cookies_path,
                "activity": account.activity.to_dict(),
                "created_at": account.created_at,
                "updated_at": account.updated_at,
                "notes": account.notes,
            }
        }
        try:
            await self._coll.update_one(
                {"_id": account.account_id}, update, upsert=True
            )
        except Exception as exc:
            raise StorageError(
                f"No se pudo guardar la cuenta '{account.account_id}': {exc}"
            ) from exc
        logger.debug(
            "Cuenta guardada | account_id=%s | platform=%s",
            account.account_id,
            account.platform,
        )

    async def load(self, account_id: str) -> AccountProfile | None:
        """Carga un perfil por ``account_id`` o ``None`` si no existe."""
        try:
            doc = await self._coll.find_one({"_id": account_id})
        except Exception as exc:
            raise StorageError(
                f"No se pudo cargar la cuenta '{account_id}': {exc}"
            ) from exc
        return self._profile_from_doc(doc) if doc else None

    async def delete(self, account_id: str) -> bool:
        """Elimina el documento completo de la cuenta."""
        try:
            result = await self._coll.delete_one({"_id": account_id})
        except Exception as exc:
            raise StorageError(
                f"No se pudo eliminar la cuenta '{account_id}': {exc}"
            ) from exc

        deleted = int(result.deleted_count) > 0
        if deleted:
            logger.info("Cuenta eliminada | account_id=%s", account_id)
        return deleted

    async def list_all(
        self,
        platform: str | None = None,
    ) -> list[AccountProfile]:
        """Lista todos los perfiles, opcionalmente filtrados por plataforma."""
        filter_doc: dict[str, Any] = {"platform": platform} if platform else {}
        try:
            cursor = self._coll.find(filter_doc)
            docs = await cursor.to_list(length=None)
        except Exception as exc:
            raise StorageError(
                f"No se pudo listar las cuentas (platform={platform}): {exc}"
            ) from exc

        profiles = [self._profile_from_doc(doc) for doc in docs]
        logger.debug(
            "Cuentas listadas | platform=%s | total=%d",
            platform or "todas",
            len(profiles),
        )
        return profiles

    async def update_activity(
        self,
        account_id: str,
        activity: AccountActivity,
    ) -> bool:
        """Actualiza solo el subdocumento ``activity`` (``$set`` parcial)."""
        try:
            result = await self._coll.update_one(
                {"_id": account_id},
                {"$set": {"activity": activity.to_dict()}},
            )
        except Exception as exc:
            raise StorageError(
                f"No se pudo actualizar la actividad de '{account_id}': {exc}"
            ) from exc
        return int(result.matched_count) > 0

    # ── Operaciones de cookies ────────────────────────────────────────────────

    async def save_cookies(
        self,
        platform: str,
        account_id: str,
        cookies: list[dict[str, Any]],
    ) -> str:
        """Guarda las cookies en el documento de la cuenta."""
        locator = COOKIES_LOCATOR_TEMPLATE.format(account_id=account_id)
        try:
            result = await self._coll.update_one(
                {"_id": account_id},
                {
                    "$set": {
                        "cookies": cookies,
                        "cookies_path": locator,
                        "platform": platform,
                    }
                },
            )
        except Exception as exc:
            raise StorageError(
                f"No se pudieron guardar las cookies de '{account_id}': {exc}"
            ) from exc
        if result.matched_count == 0:
            raise StorageError(
                f"save_cookies: cuenta no encontrada | account_id={account_id}"
            )
        logger.debug(
            "Cookies guardadas | account_id=%s | cantidad=%d",
            account_id,
            len(cookies),
        )
        return locator

    async def load_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> list[dict[str, Any]] | None:
        """Lee las cookies del documento, o ``None`` si no tiene."""
        try:
            doc = await self._coll.find_one({"_id": account_id})
        except Exception as exc:
            raise StorageError(
                f"No se pudieron leer las cookies de '{account_id}': {exc}"
            ) from exc

        if doc is None or not isinstance(doc.get("cookies"), list):
            return None
        return list(doc["cookies"])

    async def delete_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> bool:
        """Elimina los campos ``cookies`` y ``cookies_path`` del documento."""
        try:
            result = await self._coll.update_one(
                {"_id": account_id},
                {"$unset": {"cookies": "", "cookies_path": ""}},
            )
        except Exception as exc:
            raise StorageError(
                f"No se pudieron eliminar las cookies de '{account_id}': {exc}"
            ) from exc
        return int(result.matched_count) > 0

    # ── Helpers privados ──────────────────────────────────────────────────────

    @property
    def _coll(self) -> Any:
        """Colección activa (el cliente motor es perezoso)."""
        return self._client[self._database][self._collection]

    @staticmethod
    def _profile_from_doc(doc: dict[str, Any]) -> AccountProfile:
        """Reconstruye un ``AccountProfile`` desde un documento MongoDB."""
        return AccountProfile.from_dict(
            {
                "account_id": doc.get("_id"),
                "platform": doc.get("platform", "facebook"),
                "username": doc.get("username", ""),
                "email": doc.get("email"),
                "status": doc.get("status"),
                "cookies_path": doc.get("cookies_path"),
                "activity": doc.get("activity", {}),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
                "notes": doc.get("notes", ""),
            }
        )
