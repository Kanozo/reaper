"""
reaper/actions/storage/postgres.py
==================================
Backend de almacenamiento de acciones PostgreSQL (15+) usando ``asyncpg``.

Cada ``ActionResult`` es una fila de la tabla ``actions``. Todos los campos
del registro se guardan en columnas tipadas, con los campos opcionales
permitiendo NULL. El ``text`` de la acción y el ``error`` se almacenan como
TEXT sin restricción de tamaño.

Diseño:
    - El pool de conexiones se crea en ``connect()`` (perezoso).
    - La tabla se crea automáticamente en ``connect()`` si no existe.
    - ``list_all`` ordena por ``executed_at DESC`` (más recientes primero).
    - La clave primaria es ``action_id``.

Esquema (creado automáticamente en ``connect()``)::

    CREATE TABLE IF NOT EXISTS actions (
        action_id        TEXT PRIMARY KEY,
        action           TEXT NOT NULL,
        platform         TEXT NOT NULL,
        account_id       TEXT,
        account_username TEXT,
        status           TEXT NOT NULL,
        post_url         TEXT,
        post_id          TEXT,
        comment_id       TEXT,
        group_id         TEXT,
        text             TEXT,
        note             TEXT,
        error            TEXT,
        executed_at      TEXT NOT NULL
    );

Ejemplo::

    from reaper.actions.storage.postgres import PostgresActionStorage

    storage = PostgresActionStorage(dsn="postgresql://user:pass@localhost:5432/reaper")
    await storage.connect()          # crea el pool y la tabla si no existen
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
    import asyncpg
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "El backend PostgresActionStorage requiere la dependencia 'asyncpg'. "
        "Instálala con: pip install 'reaper[db]'"
    ) from exc


logger = get_logger(__name__)


class PostgresActionStorage(BaseActionStorage):
    """Almacenamiento de acciones en PostgreSQL vía ``asyncpg``.

    Args:
        dsn: Cadena de conexión de PostgreSQL, p.ej.
            ``"postgresql://user:pass@localhost:5432/reaper"``.
        pool_size: Número de conexiones del pool. Defecto: 5.
        max_overflow: Conexiones extra temporales por encima de ``pool_size``
            bajo carga. Defecto: 10.
        timeout: Segundos de espera para obtener una conexión del pool.
            Defecto: 30.
        table: Nombre de la tabla de acciones. Defecto: ``"actions"``.

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
        table: str = "actions",
    ) -> None:
        self._dsn = dsn
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._timeout = timeout
        self._table = table
        self._pool: Any | None = None

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Crea el pool de conexiones y la tabla ``actions`` si no existe.

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
        logger.debug("PostgresActionStorage conectado | table=%s", self._table)

    async def close(self) -> None:
        """Cierra el pool de conexiones, liberando todos los recursos."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ── Operaciones de registros ──────────────────────────────────────────────

    async def add(self, action: ActionResult) -> None:
        """Inserta o actualiza (UPSERT) un registro de acción."""
        await self._require_pool()
        assert self._pool is not None
        query = f"""
            INSERT INTO {self._table}
                (action_id, action, platform, account_id, account_username,
                 status, post_url, post_id, comment_id, group_id, text,
                 note, error, executed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (action_id) DO UPDATE SET
                action           = EXCLUDED.action,
                platform         = EXCLUDED.platform,
                account_id       = EXCLUDED.account_id,
                account_username = EXCLUDED.account_username,
                status           = EXCLUDED.status,
                post_url         = EXCLUDED.post_url,
                post_id          = EXCLUDED.post_id,
                comment_id       = EXCLUDED.comment_id,
                group_id         = EXCLUDED.group_id,
                text             = EXCLUDED.text,
                note             = EXCLUDED.note,
                error            = EXCLUDED.error,
                executed_at      = EXCLUDED.executed_at
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    query,
                    action.action_id,
                    action.action,
                    action.platform,
                    action.account_id,
                    action.account_username,
                    action.status,
                    action.post_url,
                    action.post_id,
                    action.comment_id,
                    action.group,
                    action.text,
                    action.note,
                    action.error,
                    action.executed_at,
                )
        except (OSError, asyncpg.PostgresError) as exc:
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
        await self._require_pool()
        assert self._pool is not None
        query = f"""
            SELECT action_id, action, platform, account_id, account_username,
                   status, post_url, post_id, comment_id, group_id, text,
                   note, error, executed_at
            FROM {self._table}
            WHERE action_id = $1
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, action_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo cargar la acción '{action_id}': {exc}"
            ) from exc

        return self._action_from_row(row) if row else None

    async def list_all(
        self,
        account_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[ActionResult]:
        """Lista registros con filtros opcionales, más recientes primero."""
        await self._require_pool()
        assert self._pool is not None

        clauses: list[str] = []
        params: list[Any] = []
        if account_id:
            clauses.append("account_id = $1")
            params.append(account_id)
        if action:
            clauses.append(f"action = ${len(params) + 1}")
            params.append(action)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = " ORDER BY executed_at DESC"
        limit_clause = f" LIMIT {int(limit)}" if limit > 0 else ""

        query = f"""
            SELECT action_id, action, platform, account_id, account_username,
                   status, post_url, post_id, comment_id, group_id, text,
                   note, error, executed_at
            FROM {self._table}{where}{order}{limit_clause}
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo listar las acciones (account_id={account_id}, "
                f"action={action}): {exc}"
            ) from exc

        results = [self._action_from_row(row) for row in rows]
        logger.debug(
            "Acciones listadas | account_id=%s | action=%s | total=%d",
            account_id or "todas",
            action or "todas",
            len(results),
        )
        return results

    async def delete(self, action_id: str) -> bool:
        """Elimina la fila de una acción.

        Returns:
            ``True`` si se eliminó, ``False`` si no existía.
        """
        await self._require_pool()
        assert self._pool is not None
        query = f"DELETE FROM {self._table} WHERE action_id = $1"
        try:
            async with self._pool.acquire() as conn:
                status = await conn.execute(query, action_id)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo eliminar la acción '{action_id}': {exc}"
            ) from exc

        deleted = _rowcount_from_status(status) > 0
        if deleted:
            logger.info("Acción eliminada | action_id=%s", action_id)
        return deleted

    async def count(
        self,
        account_id: str | None = None,
        action: str | None = None,
    ) -> int:
        """Cuenta registros con filtros opcionales vía ``COUNT(*)``."""
        await self._require_pool()
        assert self._pool is not None

        clauses: list[str] = []
        params: list[Any] = []
        if account_id:
            clauses.append("account_id = $1")
            params.append(account_id)
        if action:
            clauses.append(f"action = ${len(params) + 1}")
            params.append(action)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT COUNT(*) AS n FROM {self._table}{where}"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, *params)
        except (OSError, asyncpg.PostgresError) as exc:
            raise StorageError(
                f"No se pudo contar las acciones: {exc}"
            ) from exc
        return int(row["n"]) if row else 0

    # ── Helpers privados ──────────────────────────────────────────────────────

    async def _require_pool(self) -> None:
        """Asegura que el pool exista; si no, lo crea (arranque perezoso)."""
        if self._pool is None:
            await self.connect()

    async def _ensure_schema(self) -> None:
        """Crea la tabla ``actions`` si no existe (y añade columnas nuevas)."""
        assert self._pool is not None
        ddl = f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                action_id        TEXT PRIMARY KEY,
                action           TEXT NOT NULL,
                platform         TEXT NOT NULL,
                account_id       TEXT,
                account_username TEXT,
                status           TEXT NOT NULL,
                post_url         TEXT,
                post_id          TEXT,
                comment_id       TEXT,
                group_id         TEXT,
                text             TEXT,
                note             TEXT,
                error            TEXT,
                executed_at      TEXT NOT NULL
            )
        """
        async with self._pool.acquire() as conn:
            await conn.execute(ddl)
            # Migración para tablas creadas antes de la columna ``note``.
            await conn.execute(
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS note TEXT"
            )

    @staticmethod
    def _action_from_row(row: Any) -> ActionResult:
        """Reconstruye un ``ActionResult`` desde una fila de la BD."""
        return ActionResult.from_dict(
            {
                "action_id": row["action_id"],
                "action": row["action"],
                "platform": row["platform"],
                "account_id": row["account_id"],
                "account_username": row["account_username"],
                "status": row["status"],
                "post_url": row["post_url"],
                "post_id": row["post_id"],
                "comment_id": row["comment_id"],
                "group": row["group_id"],
                "text": row["text"],
                "note": row["note"],
                "error": row["error"],
                "executed_at": row["executed_at"],
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
