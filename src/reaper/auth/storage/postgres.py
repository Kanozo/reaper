"""
reaper/auth/storage/postgres.py
===============================
Backend de almacenamiento PostgreSQL (15+) usando ``asyncpg`` directamente.

Cada ``AccountProfile`` es una fila de la tabla ``accounts``. El perfil
completo y la actividad se persisten en columnas tipadas; las cookies de
sesión se guardan como ``JSONB`` en la misma fila para poder operar todo
el ciclo de vida de una cuenta desde un único backend.

Diseño:
    - ``update_activity`` hace un ``UPDATE`` parcial de la columna
      ``activity`` (alta frecuencia) sin reescribir el resto de la fila.
    - ``save_cookies``/``load_cookies`` operan únicamente sobre la columna
      ``cookies``, de modo que ``save()`` (perfil) nunca toca las cookies.
    - El pool de conexiones se crea en ``connect()`` (perezoso). Es
      recomendable llamar a ``close()`` al finalizar el proceso.

Esquema (creado automáticamente en ``connect()``)::

    CREATE TABLE IF NOT EXISTS accounts (
        account_id   TEXT PRIMARY KEY,
        platform     TEXT NOT NULL,
        username     TEXT NOT NULL,
        email        TEXT,
        status       TEXT NOT NULL,
        cookies      JSONB,
        cookies_path TEXT,
        activity     JSONB NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        notes        TEXT NOT NULL DEFAULT ''
    );

Ejemplo::

    from reaper.auth.storage.postgres import PostgresStorage

    storage = PostgresStorage(dsn="postgresql://user:pass@localhost:5432/reaper")
    await storage.connect()   # crea el pool y el esquema si no existen
    await storage.save(account_profile)
    cuentas = await storage.list_all(platform="facebook")
    await storage.close()
"""

from __future__ import annotations

import json
from typing import Any

from reaper.auth.models import AccountActivity, AccountProfile
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.utils.logger import get_logger

try:
    import asyncpg
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "El backend PostgresStorage requiere la dependencia 'asyncpg'. "
        "Instálala con: pip install 'reaper[db]'"
    ) from exc


logger = get_logger(__name__)

# Locator simbólico que se guarda en ``AccountProfile.cookies_path``.
COOKIES_LOCATOR_TEMPLATE: str = "db://postgres/{account_id}#cookies"


class PostgresStorage(BaseAccountStorage):
    """Almacenamiento de cuentas en PostgreSQL vía ``asyncpg``.

    Args:
        dsn: Cadena de conexión de PostgreSQL, p.ej.
            ``"postgresql://user:pass@localhost:5432/reaper"``.
        pool_size: Número de conexiones del pool. Defecto: 5.
        max_overflow: Conexiones extra temporales por encima de ``pool_size``
            bajo carga. Defecto: 10.
        timeout: Segundos de espera para obtener una conexión del pool.
            Defecto: 30.
        table: Nombre de la tabla de cuentas. Defecto: ``"accounts"``.

    Raises:
        StorageError: Si no se puede conectar/crear el esquema en ``connect()``
            o si falla una operación de lectura/escritura.
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        timeout: float = 30.0,
        table: str = "accounts",
    ) -> None:
        self._dsn = dsn
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._timeout = timeout
        self._table = table
        self._pool: Any | None = None

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Crea el pool de conexiones y el esquema si no existe.

        Raises:
            StorageError: Si la conexión falla o el DSN es inválido.
        """
        if self._pool is not None:
            return
        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=min(self._pool_size, 1),
                max_size=self._pool_size + self._max_overflow,
                timeout=self._timeout,
            )
            await self._ensure_schema()
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo conectar a PostgreSQL: {exc}"
            ) from exc
        logger.debug("PostgresStorage conectado | table=%s", self._table)

    async def close(self) -> None:
        """Cierra el pool de conexiones, liberando todos los recursos."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ── Operaciones de perfil completo ────────────────────────────────────────

    async def save(self, account: AccountProfile) -> None:
        """Inserta o actualiza el perfil completo (sin tocar las cookies)."""
        await self._require_pool()
        assert self._pool is not None
        query = f"""
            INSERT INTO {self._table}
                (account_id, platform, username, email, status, cookies_path,
                 activity, created_at, updated_at, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)
            ON CONFLICT (account_id) DO UPDATE SET
                platform     = EXCLUDED.platform,
                username     = EXCLUDED.username,
                email        = EXCLUDED.email,
                status       = EXCLUDED.status,
                cookies_path = EXCLUDED.cookies_path,
                activity     = EXCLUDED.activity,
                created_at   = EXCLUDED.created_at,
                updated_at   = EXCLUDED.updated_at,
                notes        = EXCLUDED.notes
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    query,
                    account.account_id,
                    account.platform,
                    account.username,
                    account.email,
                    account.status.value,
                    account.cookies_path,
                    json.dumps(account.activity.to_dict()),
                    account.created_at,
                    account.updated_at,
                    account.notes,
                )
        except (OSError, asyncpg.PostgresError) as exc:
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
        await self._require_pool()
        assert self._pool is not None
        query = f"""
            SELECT account_id, platform, username, email, status, cookies_path,
                   activity, created_at, updated_at, notes
            FROM {self._table}
            WHERE account_id = $1
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, account_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo cargar la cuenta '{account_id}': {exc}"
            ) from exc

        return self._profile_from_row(row) if row else None

    async def delete(self, account_id: str) -> bool:
        """Elimina la fila completa de la cuenta."""
        await self._require_pool()
        assert self._pool is not None
        query = f"DELETE FROM {self._table} WHERE account_id = $1"
        try:
            async with self._pool.acquire() as conn:
                status = await conn.execute(query, account_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo eliminar la cuenta '{account_id}': {exc}"
            ) from exc

        # asyncpg devuelve una etiqueta tipo "DELETE <count>".
        deleted = _rowcount_from_status(status) > 0
        if deleted:
            logger.info("Cuenta eliminada | account_id=%s", account_id)
        return deleted

    async def list_all(
        self,
        platform: str | None = None,
    ) -> list[AccountProfile]:
        """Lista todos los perfiles, opcionalmente filtrados por plataforma."""
        await self._require_pool()
        assert self._pool is not None
        if platform:
            query = (
                f"SELECT account_id, platform, username, email, status, "
                f"cookies_path, activity, created_at, updated_at, notes "
                f"FROM {self._table} WHERE platform = $1"
            )
            params: tuple[Any, ...] = (platform,)
        else:
            query = (
                f"SELECT account_id, platform, username, email, status, "
                f"cookies_path, activity, created_at, updated_at, notes "
                f"FROM {self._table}"
            )
            params = ()

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo listar las cuentas (platform={platform}): {exc}"
            ) from exc

        profiles = [self._profile_from_row(row) for row in rows]
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
        """Actualiza solo la columna ``activity`` (UPDATE parcial)."""
        await self._require_pool()
        assert self._pool is not None
        query = (
            f"UPDATE {self._table} SET activity = $1::jsonb "
            f"WHERE account_id = $2"
        )
        try:
            async with self._pool.acquire() as conn:
                status = await conn.execute(
                    query, json.dumps(activity.to_dict()), account_id
                )
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo actualizar la actividad de '{account_id}': {exc}"
            ) from exc
        return _rowcount_from_status(status) > 0

    # ── Operaciones de cookies ────────────────────────────────────────────────

    async def save_cookies(
        self,
        platform: str,
        account_id: str,
        cookies: list[dict[str, Any]],
    ) -> str:
        """Guarda las cookies como JSONB en la fila de la cuenta."""
        await self._require_pool()
        assert self._pool is not None
        locator = COOKIES_LOCATOR_TEMPLATE.format(account_id=account_id)
        query = (
            f"UPDATE {self._table} SET cookies = $1::jsonb, "
            f"cookies_path = $2 WHERE account_id = $3"
        )
        try:
            async with self._pool.acquire() as conn:
                status = await conn.execute(
                    query, json.dumps(cookies), locator, account_id
                )
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudieron guardar las cookies de '{account_id}': {exc}"
            ) from exc
        if _rowcount_from_status(status) == 0:
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
        """Lee las cookies de la fila, o ``None`` si la cuenta no tiene."""
        await self._require_pool()
        assert self._pool is not None
        query = f"SELECT cookies FROM {self._table} WHERE account_id = $1"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, account_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudieron leer las cookies de '{account_id}': {exc}"
            ) from exc

        if row is None or row["cookies"] is None:
            return None
        return list(row["cookies"])

    async def delete_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> bool:
        """Pone la columna ``cookies`` a NULL."""
        await self._require_pool()
        assert self._pool is not None
        query = (
            f"UPDATE {self._table} SET cookies = NULL, cookies_path = NULL "
            f"WHERE account_id = $1"
        )
        try:
            async with self._pool.acquire() as conn:
                status = await conn.execute(query, account_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudieron eliminar las cookies de '{account_id}': {exc}"
            ) from exc
        return _rowcount_from_status(status) > 0

    # ── Helpers privados ──────────────────────────────────────────────────────

    async def _require_pool(self) -> None:
        """Asegura que el pool exista; si no, lo crea (arranque perezoso)."""
        if self._pool is None:
            await self.connect()

    async def _ensure_schema(self) -> None:
        """Crea la tabla ``accounts`` si no existe."""
        assert self._pool is not None
        ddl = f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                account_id   TEXT PRIMARY KEY,
                platform     TEXT NOT NULL,
                username     TEXT NOT NULL,
                email        TEXT,
                status       TEXT NOT NULL,
                cookies      JSONB,
                cookies_path TEXT,
                activity     JSONB NOT NULL,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                notes        TEXT NOT NULL DEFAULT ''
            )
        """
        async with self._pool.acquire() as conn:
            await conn.execute(ddl)

    @staticmethod
    def _profile_from_row(row: Any) -> AccountProfile:
        """Reconstruye un ``AccountProfile`` desde una fila de la BD."""
        return AccountProfile.from_dict(
            {
                "account_id": row["account_id"],
                "platform": row["platform"],
                "username": row["username"],
                "email": row["email"],
                "status": row["status"],
                "cookies_path": row["cookies_path"],
                "activity": row["activity"] or {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "notes": row["notes"],
            }
        )


def _rowcount_from_status(status: str) -> int:
    """Extrae el número de filas afectadas de la etiqueta asyncpg."""
    # asyncpg devuelve etiquetas como "UPDATE 1", "DELETE 0", "INSERT 0 1".
    parts = status.split(" ")
    for part in parts:
        if part.isdigit():
            return int(part)
    return 0
