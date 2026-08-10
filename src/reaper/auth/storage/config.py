"""
reaper/auth/storage/config.py
=============================
Carga de configuración de almacenamiento desde un fichero TOML y factory.

El usuario final elige el backend (``local``, ``postgres`` o ``mongodb``) en
un fichero de configuración ``reaper.toml``. Todas las variables de conexión
y parámetros de cada backend se leen de ahí; ningún valor queda hardcodeado.

Formato del fichero::

    [storage]
    backend = "postgres"            # local | postgres | mongodb
    accounts_dir = "data/accounts"  # solo se usa con backend="local"

    [storage.postgres]
    dsn = "postgresql://user:pass@localhost:5432/reaper"
    pool_size = 5
    max_overflow = 10
    timeout = 30.0
    table = "accounts"

    [storage.mongodb]
    uri = "mongodb://localhost:27017"
    database = "reaper"
    collection = "accounts"

Búsqueda del fichero (por orden):
    1. Ruta explícita pasada a :func:`load_storage_config`.
    2. ``REAPER_CONFIG`` (variable de entorno).
    3. ``./reaper.toml`` (directorio de trabajo).
    4. ``~/.config/reaper/reaper.toml``.
    5. ``/etc/reaper/reaper.toml``.

Ejemplo de uso::

    from reaper.auth.storage.config import build_storage, load_storage_config

    config = load_storage_config()          # lee reaper.toml (o defaults)
    storage = build_storage(config)         # instancia el backend elegido
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from reaper.auth.storage.base import BaseAccountStorage
from reaper.auth.storage.local import LocalFileStorage
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_BACKENDS: frozenset[str] = frozenset({"local", "postgres", "mongodb"})

# Rutas por defecto en las que buscar reaper.toml (en orden de precedencia).
DEFAULT_CONFIG_PATHS: tuple[Path, ...] = (
    Path.cwd() / "reaper.toml",
    Path.home() / ".config" / "reaper" / "reaper.toml",
    Path("/etc/reaper/reaper.toml"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses de configuración
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PostgresConfig:
    """Parámetros de conexión del backend PostgreSQL.

    Attributes:
        dsn: Cadena de conexión asyncpg, p.ej.
            ``"postgresql://user:pass@localhost:5432/reaper"``.
        pool_size: Conexiones del pool. Defecto: 5.
        max_overflow: Conexiones extra bajo carga. Defecto: 10.
        timeout: Segundos de espera de conexión. Defecto: 30.0.
        table: Nombre de la tabla de cuentas. Defecto: ``"accounts"``.
    """

    dsn: str
    pool_size: int = 5
    max_overflow: int = 10
    timeout: float = 30.0
    table: str = "accounts"


@dataclass(frozen=True)
class MongoConfig:
    """Parámetros de conexión del backend MongoDB.

    Attributes:
        uri: Cadena de conexión, p.ej. ``"mongodb://localhost:27017"``.
        database: Nombre de la base de datos. Defecto: ``"reaper"``.
        collection: Nombre de la colección. Defecto: ``"accounts"``.
    """

    uri: str
    database: str = "reaper"
    collection: str = "accounts"


@dataclass(frozen=True)
class StorageConfig:
    """Configuración completa del subsistema de almacenamiento.

    Attributes:
        backend: ``"local"``, ``"postgres"`` o ``"mongodb"``.
            Defecto: ``"local"`` (comportamiento original, JSON en disco).
        accounts_dir: Directorio raíz usado por ``LocalFileStorage``.
            Solo se aplica con ``backend="local"``. Defecto: ``"data/accounts"``.
        postgres: Parámetros del backend PostgreSQL (si se elige).
        mongodb: Parámetros del backend MongoDB (si se elige).
    """

    backend: str = "local"
    accounts_dir: str = "data/accounts"
    postgres: PostgresConfig | None = None
    mongodb: MongoConfig | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Carga y construcción
# ─────────────────────────────────────────────────────────────────────────────


def load_storage_config(path: str | Path | None = None) -> StorageConfig:
    """Carga la configuración de almacenamiento desde un fichero TOML.

    Si no se encuentra ningún fichero, retorna la configuración por defecto
    (``backend="local"``), preservando el comportamiento original.

    Args:
        path: Ruta explícita al fichero. Si es ``None``, busca en
            ``REAPER_CONFIG`` y luego en ``DEFAULT_CONFIG_PATHS``.

    Returns:
        ``StorageConfig`` con los valores del fichero o los defaults.

    Raises:
        ValueError: Si ``backend`` no es soportado o falta su sección de
            configuración requerida.
    """
    file_path = find_config_file(path)
    if file_path is None:
        logger.debug("No se encontró fichero de configuración; usando defaults")
        return StorageConfig()

    with file_path.open("rb") as fh:
        data = tomllib.load(fh)

    storage = data.get("storage", {})
    backend = str(storage.get("backend", "local")).lower()
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"Backend '{backend}' no soportado en {file_path}. "
            f"Opciones: {sorted(SUPPORTED_BACKENDS)}"
        )

    accounts_dir = str(storage.get("accounts_dir", "data/accounts"))

    postgres_cfg: PostgresConfig | None = None
    if backend == "postgres":
        pg_section = storage.get("postgres", {})
        if not pg_section.get("dsn"):
            raise ValueError(
                f"Se eligió backend='postgres' pero falta 'dsn' en "
                f"[storage.postgres] de {file_path}."
            )
        postgres_cfg = PostgresConfig(
            dsn=str(pg_section["dsn"]),
            pool_size=int(pg_section.get("pool_size", 5)),
            max_overflow=int(pg_section.get("max_overflow", 10)),
            timeout=float(pg_section.get("timeout", 30.0)),
            table=str(pg_section.get("table", "accounts")),
        )

    mongo_cfg: MongoConfig | None = None
    if backend == "mongodb":
        mongo_section = storage.get("mongodb", {})
        if not mongo_section.get("uri"):
            raise ValueError(
                f"Se eligió backend='mongodb' pero falta 'uri' en "
                f"[storage.mongodb] de {file_path}."
            )
        mongo_cfg = MongoConfig(
            uri=str(mongo_section["uri"]),
            database=str(mongo_section.get("database", "reaper")),
            collection=str(mongo_section.get("collection", "accounts")),
        )

    logger.debug(
        "Configuración de storage cargada | backend=%s | file=%s",
        backend,
        file_path,
    )
    return StorageConfig(
        backend=backend,
        accounts_dir=accounts_dir,
        postgres=postgres_cfg,
        mongodb=mongo_cfg,
    )


def build_storage(config: StorageConfig | None = None) -> BaseAccountStorage:
    """Instancia el backend de almacenamiento indicado por la configuración.

    Args:
        config: Configuración de storage. Si es ``None``, la carga desde el
            fichero de configuración con :func:`load_storage_config`.

    Returns:
        ``LocalFileStorage``, ``PostgresStorage`` o ``MongoStorage`` según
        ``config.backend``.

    Raises:
        ValueError: Si el backend no está soportado o falta su configuración.
    """
    config = config or load_storage_config()

    match config.backend:
        case "local":
            return LocalFileStorage(config.accounts_dir)
        case "postgres":
            if config.postgres is None:
                raise ValueError(
                    "backend='postgres' requiere una sección [storage.postgres]."
                )
            from reaper.auth.storage.postgres import PostgresStorage

            return PostgresStorage(
                dsn=config.postgres.dsn,
                pool_size=config.postgres.pool_size,
                max_overflow=config.postgres.max_overflow,
                timeout=config.postgres.timeout,
                table=config.postgres.table,
            )
        case "mongodb":
            if config.mongodb is None:
                raise ValueError(
                    "backend='mongodb' requiere una sección [storage.mongodb]."
                )
            from reaper.auth.storage.mongodb import MongoStorage

            return MongoStorage(
                uri=config.mongodb.uri,
                database=config.mongodb.database,
                collection=config.mongodb.collection,
            )
        case _:
            raise ValueError(
                f"Backend '{config.backend}' no soportado. "
                f"Opciones: {sorted(SUPPORTED_BACKENDS)}"
            )


def find_config_file(path: str | Path | None = None) -> Path | None:
    """Resuelve la ruta del fichero de configuración o ``None`` si no existe.

    El orden de búsqueda es: ruta explícita, variable de entorno
    ``REAPER_CONFIG``, y luego ``DEFAULT_CONFIG_PATHS``.

    Args:
        path: Ruta explícita al fichero, o ``None`` para búsqueda automática.

    Returns:
        ``Path`` del primer fichero existente, o ``None``.
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        env_path = os.environ.get("REAPER_CONFIG")
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend(DEFAULT_CONFIG_PATHS)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
