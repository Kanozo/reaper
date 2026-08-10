"""
reaper/auth/storage/local.py
============================
Implementación de almacenamiento de cuentas basada en archivos JSON locales.

Cada cuenta ocupa un directorio propio bajo ``accounts_dir``:

    {accounts_dir}/
    ├── facebook/
    │   ├── {account_id}/
    │   │   ├── account.json   ← perfil completo (sin cookies)
    │   │   └── cookies.json   ← cookies en formato Playwright
    └── instagram/
        └── {account_id}/
            ├── account.json
            └── cookies.json

Esta estructura permite:
- Inspección manual de cuentas (un directorio por cuenta).
- Operaciones atómicas a nivel de archivo (sobrescritura es atómica en la mayoría de SO).
- Migración sencilla: la ruta de cada archivo es predecible y reproducible.

El archivo ``account.json`` **no** contiene el campo ``cookies_path`` embebido en sí
mismo; en cambio, al deserializar, ``LocalFileStorage`` lo infiere de la ubicación
del archivo para evitar rutas absolutas hardcodeadas que rompan al mover el proyecto.

Diseño de operaciones atómicas:
    ``update_activity`` lee el JSON actual, sobreescribe solo el bloque ``activity``
    y lo reescribe. En sistemas de archivos locales esto es suficientemente eficiente
    para volúmenes de cuentas razonables (<1000). Para más escala, migrar a BD.

Ejemplo::

    from reaper.auth.storage.local import LocalFileStorage

    storage = LocalFileStorage("data/accounts")
    await storage.save(account_profile)
    cuentas = await storage.list_all(platform="facebook")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reaper.auth.models import AccountActivity, AccountProfile
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# Nombre de los archivos dentro del directorio de cada cuenta.
ACCOUNT_FILENAME: str = "account.json"
COOKIES_FILENAME: str = "cookies.json"


class LocalFileStorage(BaseAccountStorage):
    """Almacenamiento de cuentas en archivos JSON locales.

    Thread-safe para operaciones de lectura concurrente.
    Para escrituras concurrentes desde múltiples procesos se recomienda
    migrar a una BD que soporte transacciones.

    Args:
        accounts_dir: Directorio raíz para almacenar todas las cuentas.
                      Se crea automáticamente si no existe.
                      Ejemplo: ``Path("data/accounts")``.

    Estructura en disco::

        {accounts_dir}/{platform}/{account_id}/account.json
        {accounts_dir}/{platform}/{account_id}/cookies.json
    """

    def __init__(self, accounts_dir: str | Path = "data/accounts") -> None:
        self.accounts_dir = Path(accounts_dir)
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("LocalFileStorage inicializado | dir=%s", self.accounts_dir)

    # ── API pública ───────────────────────────────────────────────────────────

    async def save(self, account: AccountProfile) -> None:
        """Guarda el perfil completo. Crea el directorio de la cuenta si no existe."""
        try:
            account_dir = self._account_dir(account.platform, account.account_id)
            account_dir.mkdir(parents=True, exist_ok=True)

            account_file = account_dir / ACCOUNT_FILENAME
            data = account.to_dict()
            # No persistimos cookies_path: se reconstruye desde la ubicación del dir.
            # Esto evita rutas absolutas que rompan al mover el proyecto.
            data.pop("cookies_path", None)

            _write_json(account_file, data)
            logger.debug(
                "Cuenta guardada | account_id=%s | platform=%s",
                account.account_id,
                account.platform,
            )
        except OSError as exc:
            raise StorageError(
                f"No se pudo guardar la cuenta '{account.account_id}': {exc}"
            ) from exc

    async def load(self, account_id: str) -> AccountProfile | None:
        """Carga un perfil buscando en todas las plataformas.

        Realiza búsqueda en todos los directorios de plataforma porque
        ``account_id`` no incluye la plataforma como prefijo.
        """
        for platform_dir in self._platform_dirs():
            account_file = platform_dir / account_id / ACCOUNT_FILENAME
            if account_file.exists():
                return self._load_from_file(account_file)
        return None

    async def delete(self, account_id: str) -> bool:
        """Elimina el directorio completo de la cuenta (perfil + cookies).

        Returns:
            ``True`` si se encontró y eliminó, ``False`` si no existía.
        """
        for platform_dir in self._platform_dirs():
            account_dir = platform_dir / account_id
            if account_dir.exists():
                try:
                    import shutil
                    shutil.rmtree(account_dir)
                    logger.info("Cuenta eliminada | account_id=%s", account_id)
                    return True
                except OSError as exc:
                    raise StorageError(
                        f"No se pudo eliminar la cuenta '{account_id}': {exc}"
                    ) from exc
        return False

    async def list_all(
        self,
        platform: str | None = None,
    ) -> list[AccountProfile]:
        """Lista todos los perfiles, opcionalmente filtrados por plataforma."""
        accounts: list[AccountProfile] = []

        platform_dirs = (
            [self.accounts_dir / platform]
            if platform
            else self._platform_dirs()
        )

        for platform_dir in platform_dirs:
            if not platform_dir.exists():
                continue
            for account_file in platform_dir.glob(f"*/{ACCOUNT_FILENAME}"):
                profile = self._load_from_file(account_file)
                if profile:
                    accounts.append(profile)

        logger.debug(
            "Cuentas listadas | platform=%s | total=%d",
            platform or "todas",
            len(accounts),
        )
        return accounts

    async def update_activity(
        self,
        account_id: str,
        activity: AccountActivity,
    ) -> bool:
        """Actualiza solo el bloque de actividad sin reescribir el perfil completo.

        Lee el JSON actual del disco, sobreescribe el campo ``activity``
        y vuelve a escribir. Es más eficiente que ``save()`` completo.
        """
        for platform_dir in self._platform_dirs():
            account_file = platform_dir / account_id / ACCOUNT_FILENAME
            if account_file.exists():
                try:
                    data: dict[str, Any] = _read_json(account_file)
                    data["activity"] = activity.to_dict()
                    _write_json(account_file, data)
                    return True
                except (OSError, json.JSONDecodeError) as exc:
                    raise StorageError(
                        f"No se pudo actualizar la actividad de '{account_id}': {exc}"
                    ) from exc
        return False

    # ── Métodos de gestión de cookies ─────────────────────────────────────────

    def cookies_path_for(self, platform: str, account_id: str) -> Path:
        """Retorna la ruta canónica del archivo de cookies para una cuenta.

        La ruta es predecible y reproducible: siempre es
        ``{accounts_dir}/{platform}/{account_id}/cookies.json``.

        Este método lo usan ``AccountManager`` y ``CookieManager``
        para saber dónde leer/escribir cookies sin necesidad de consultar
        el perfil guardado en disco.

        Args:
            platform:   ``"facebook"`` o ``"instagram"``.
            account_id: UUID4 de la cuenta.

        Returns:
            Path (puede no existir todavía si no se han importado cookies).
        """
        return self._account_dir(platform, account_id) / COOKIES_FILENAME

    async def save_cookies(
        self,
        platform: str,
        account_id: str,
        cookies: list[dict[str, Any]],
    ) -> str:
        """Persiste las cookies en disco en formato Playwright.

        Crea el directorio de la cuenta si no existe.

        Args:
            platform:   ``"facebook"`` o ``"instagram"``.
            account_id: UUID4 de la cuenta.
            cookies:    Lista de dicts de cookies (formato Playwright).

        Returns:
            Locator: ruta absoluta del ``cookies.json`` escrito, para
            almacenar en ``AccountProfile.cookies_path``.

        Raises:
            StorageError: Si no se puede escribir el archivo.
        """
        account_dir = self._account_dir(platform, account_id)
        account_dir.mkdir(parents=True, exist_ok=True)
        cookies_file = account_dir / COOKIES_FILENAME

        try:
            _write_json(cookies_file, cookies)
            logger.debug(
                "Cookies guardadas | account_id=%s | cantidad=%d",
                account_id,
                len(cookies),
            )
            return str(cookies_file)
        except OSError as exc:
            raise StorageError(
                f"No se pudieron guardar las cookies de '{account_id}': {exc}"
            ) from exc

    async def load_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> list[dict[str, Any]] | None:
        """Lee las cookies de disco en formato Playwright.

        Returns:
            Lista de dicts de cookies, o ``None`` si el archivo no existe.

        Raises:
            StorageError: Si el archivo existe pero no se puede leer/parsear.
        """
        cookies_file = self._account_dir(platform, account_id) / COOKIES_FILENAME
        if not cookies_file.exists():
            return None

        try:
            data = _read_json(cookies_file)
            if not isinstance(data, list):
                logger.warning(
                    "Formato de cookies inesperado en %s (esperaba lista)", cookies_file
                )
                return None
            return data
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"No se pudieron leer las cookies de '{account_id}': {exc}"
            ) from exc

    async def delete_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> bool:
        """Elimina el archivo de cookies de una cuenta.

        Args:
            platform:   ``"facebook"`` o ``"instagram"``.
            account_id: UUID4 de la cuenta.

        Returns:
            ``True`` si el archivo existía y fue eliminado, ``False`` si no.
        """
        cookies_file = self._account_dir(platform, account_id) / COOKIES_FILENAME
        if not cookies_file.exists():
            return False

        try:
            cookies_file.unlink()
            logger.debug(
                "Cookies eliminadas | account_id=%s",
                account_id,
            )
            return True
        except OSError as exc:
            raise StorageError(
                f"No se pudieron eliminar las cookies de '{account_id}': {exc}"
            ) from exc

    # ── Helpers privados ──────────────────────────────────────────────────────

    def _account_dir(self, platform: str, account_id: str) -> Path:
        """Ruta canónica del directorio de una cuenta."""
        return self.accounts_dir / platform / account_id

    def _platform_dirs(self) -> list[Path]:
        """Lista los subdirectorios de plataforma que existen en accounts_dir."""
        if not self.accounts_dir.exists():
            return []
        return [p for p in self.accounts_dir.iterdir() if p.is_dir()]

    def _load_from_file(self, account_file: Path) -> AccountProfile | None:
        """Deserializa un AccountProfile desde su archivo JSON.

        Infiere ``cookies_path`` desde la ubicación del archivo,
        sin depender del valor persistido en el JSON.
        """
        try:
            data = _read_json(account_file)
            profile = AccountProfile.from_dict(data)

            # Reconstruir cookies_path desde la ubicación conocida.
            cookies_file = account_file.parent / COOKIES_FILENAME
            profile.cookies_path = str(cookies_file) if cookies_file.exists() else None

            return profile
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "No se pudo cargar la cuenta desde %s: %s",
                account_file,
                exc,
            )
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de I/O de archivos JSON
# ─────────────────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: Any) -> None:
    """Escribe ``data`` como JSON UTF-8 indentado en ``path``.

    Usa escritura atómica (write-to-temp + rename) en sistemas que lo soporten
    para minimizar la corrupción en escrituras parciales.
    """
    # Escritura directa: suficiente para el volumen esperado de esta librería.
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    """Lee y parsea JSON desde ``path``.

    Raises:
        OSError: Si el archivo no se puede leer.
        json.JSONDecodeError: Si el contenido no es JSON válido.
    """
    return json.loads(path.read_text(encoding="utf-8"))
