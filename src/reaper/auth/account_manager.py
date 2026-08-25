"""
reaper/auth/account_manager.py
==============================
Gestor de cuentas autenticadas: CRUD, rotación y ciclo de vida de cookies.

``AccountManager`` es el único punto de entrada que el resto de la librería
(scrapers, CLI) necesita para trabajar con cuentas. Coordina dos subsistemas:

- ``BaseAccountStorage``  — persistencia de perfiles, actividad y cookies
  (LocalFileStorage en disco, PostgresStorage o MongoStorage según config).
- ``AccountRotator``      — selección inteligente de la cuenta a usar.

Backend de almacenamiento:
    El backend se selecciona inyectándolo con ``storage=...`` o mediante el
    fichero de configuración ``reaper.toml`` (sección ``[storage]``). Sin
    fichero ni inyección, se usa ``LocalFileStorage`` en ``data/accounts``.

Flujo de una petición con cuentas::

    scraper.run()
        → account_manager.get_account_for_request(platform)  # rotación
        → ContentFetcher.fetch(cookies=account.cookies)       # uso
        → account_manager.record_success/failure(account_id)  # registro
        → [si umbral alcanzado] → notifica necesidad de refresco

Refresco automático de cookies:
    Cuando ``requests_since_cookie_refresh`` supera ``cookie_refresh_threshold``,
    el manager marca la cuenta como ``NEEDS_REFRESH`` y emite un log de aviso.
    No implementa login automático (responsabilidad del operador).
    El operador puede llamar a ``import_cookies()`` con cookies frescas
    para resetear el contador y restaurar el estado a ``ACTIVE``.

Modo sin cuentas (compatibilidad hacia atrás):
    Si no hay cuentas configuradas, ``get_account_for_request()`` retorna ``None``
    y el scraper funciona en modo anónimo, exactamente como antes de esta feature.

Ejemplo de uso básico::

    from reaper.auth.account_manager import AccountManager

    manager = AccountManager()

    # Añadir una cuenta nueva
    account = await manager.add_account(
        platform="facebook",
        username="mi_usuario",
        cookies=[{"name": "c_user", "value": "...", ...}],
    )

    # Obtener la mejor cuenta para hacer una petición
    cuenta = await manager.get_account_for_request("facebook")
    if cuenta:
        # usar cuenta.cookies_path en el fetcher
        await manager.record_success(cuenta.account_id)

    # Listar todas las cuentas con su estado
    for acc in await manager.list_accounts("facebook"):
        print(acc.username, acc.status, acc.activity.success_rate)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from reaper.auth.models import (
    AccountActivity,
    AccountProfile,
    AccountStatus,
    PLATFORM_USER_ID_COOKIE,
    SUPPORTED_PLATFORMS,
)
from reaper.auth.rotator import AccountRotator
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

# Número de peticiones exitosas tras el cual se solicita refresco de cookies.
DEFAULT_COOKIE_REFRESH_THRESHOLD: int = 50

# Número de fallos consecutivos para marcar una cuenta como RATE_LIMITED.
CONSECUTIVE_FAILURES_FOR_RATE_LIMIT: int = 5


class AccountManager:
    """Gestor central de cuentas autenticadas para Reaper.

    Responsabilidades:
    - CRUD de cuentas (add, remove, get, list).
    - Importación y exportación de cookies (formato Playwright).
    - Actualización de estado y actividad tras cada petición.
    - Selección de la cuenta óptima para la siguiente petición.
    - Notificación de umbral de refresco de cookies.

    Args:
        storage: Backend de almacenamiento. Si ``None``, usa ``LocalFileStorage``
                 con la ruta ``accounts_dir``.
        accounts_dir: Directorio raíz para ``LocalFileStorage``.
                      Ignorado si se pasa ``storage`` explícitamente.
        cookie_refresh_threshold: Peticiones exitosas antes de marcar la cuenta
                                  como ``NEEDS_REFRESH``. Defecto: 50.

    Ejemplo de inyección de storage personalizado (migración a BD)::

        from myapp.storage import PostgresAccountStorage

        manager = AccountManager(storage=PostgresAccountStorage(db_url="..."))
    """

    def __init__(
        self,
        storage: BaseAccountStorage | None = None,
        accounts_dir: str | Path = "data/accounts",
        cookie_refresh_threshold: int = DEFAULT_COOKIE_REFRESH_THRESHOLD,
    ) -> None:
        # Si no se inyecta storage, usar el backend definido en el fichero
        # de configuración (default: LocalFileStorage en "data/accounts").
        if storage is None:
            from reaper.auth.storage.config import build_storage, find_config_file
            from reaper.auth.storage.local import LocalFileStorage

            if find_config_file() is None and accounts_dir != "data/accounts":
                # Compatibilidad hacia atrás: sin fichero de configuración y
                # con accounts_dir explícito, se usa LocalFileStorage con esa ruta.
                self._storage: BaseAccountStorage = LocalFileStorage(accounts_dir)
            else:
                self._storage = build_storage()
        else:
            self._storage = storage

        self._rotator = AccountRotator(
            cookie_refresh_threshold=cookie_refresh_threshold
        )
        self.cookie_refresh_threshold = cookie_refresh_threshold

        logger.debug(
            "AccountManager inicializado | storage=%s | threshold=%d",
            type(self._storage).__name__,
            cookie_refresh_threshold,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Sección 1: CRUD de cuentas
    # ─────────────────────────────────────────────────────────────────────────

    async def add_account(
        self,
        platform: str,
        username: str,
        cookies: list[dict[str, Any]] | None = None,
        cookies_file: str | Path | None = None,
        name: str = "",
        avatar: str | None = None,
        description: str = "",
        biography: str = "",
        followers_count: int = 0,
        following_count: int = 0,
        friends_count: int = 0,
        password: str | None = None,
        notes: str = "",
    ) -> AccountProfile:
        """Registra una nueva cuenta en el sistema.

        Las cookies pueden proporcionarse de dos formas mutuamente excluyentes:
        - ``cookies``:       Lista de dicts (formato Playwright) en memoria.
        - ``cookies_file``:  Ruta a un archivo JSON externo con las cookies.

        Si no se proporcionan cookies, la cuenta se crea con estado ``UNKNOWN``
        y sin cookies; el operador deberá importarlas después con
        ``import_cookies()``.

        Args:
            platform:     ``"facebook"`` o ``"instagram"``.
            username:     Nombre de usuario de la cuenta.
            cookies:      Lista de dicts de cookies (formato Playwright).
            cookies_file: Ruta alternativa a un archivo JSON de cookies.
            name:         Nombre público del perfil (ej. "Kanozo Gonzalez").
            avatar:       Avatar del perfil en base64 (binario), o None.
            description:  Breve descripción del perfil.
            biography:    Biografía completa del perfil.
            followers_count: Personas que siguen el perfil.
            following_count: Perfiles que sigue este usuario.
            friends_count:   Número de amigos del perfil.
            password:      Contraseña de la cuenta (texto plano), o None.
            notes:        Notas libres del operador.

        Returns:
            ``AccountProfile`` creado y persistido.

        Raises:
            ValueError: Si la plataforma no es soportada o ambas fuentes
                        de cookies se proporcionan simultáneamente.
            StorageError: Si no se puede persistir el perfil.
        """
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Plataforma '{platform}' no soportada. "
                f"Opciones: {sorted(SUPPORTED_PLATFORMS)}"
            )
        if cookies is not None and cookies_file is not None:
            raise ValueError(
                "Proporciona 'cookies' o 'cookies_file', no ambos simultáneamente."
            )

        profile = AccountProfile(
            platform=platform,
            username=username,
            name=name,
            avatar=avatar,
            description=description,
            biography=biography,
            followers_count=followers_count,
            following_count=following_count,
            friends_count=friends_count,
            password=password,
            notes=notes,
        )

        # ── Persistir perfil base ─────────────────────────────────────────────
        await self._storage.save(profile)
        logger.info(
            "Cuenta añadida | account_id=%s | platform=%s | username=%s",
            profile.account_id,
            platform,
            username,
        )

        # ── Importar cookies si se proporcionaron ─────────────────────────────
        if cookies is not None:
            await self.import_cookies(profile.account_id, cookies)
        elif cookies_file is not None:
            await self.import_cookies_from_file(profile.account_id, cookies_file)

        # Recargar desde storage para obtener el perfil con cookies_path actualizado.
        updated = await self._storage.load(profile.account_id)
        return updated or profile

    async def remove_account(self, account_id: str) -> bool:
        """Elimina una cuenta y todos sus archivos asociados (perfil + cookies).

        Args:
            account_id: UUID4 de la cuenta a eliminar.

        Returns:
            ``True`` si existía y fue eliminada, ``False`` si no existía.
        """
        deleted = await self._storage.delete(account_id)
        if deleted:
            logger.info("Cuenta eliminada | account_id=%s", account_id)
        else:
            logger.warning(
                "Intento de eliminar cuenta inexistente | account_id=%s", account_id
            )
        return deleted

    async def get_account(self, account_id: str) -> AccountProfile | None:
        """Carga el perfil completo de una cuenta por su ID.

        Args:
            account_id: UUID4 de la cuenta.

        Returns:
            ``AccountProfile`` si existe, ``None`` si no se encuentra.
        """
        return await self._storage.load(account_id)

    async def list_accounts(
        self,
        platform: str | None = None,
    ) -> list[AccountProfile]:
        """Lista todas las cuentas registradas, con actividad y estado actuales.

        Args:
            platform: Filtrar por ``"facebook"`` o ``"instagram"``.
                      ``None`` retorna todas las plataformas.

        Returns:
            Lista de ``AccountProfile``. Puede estar vacía.
        """
        return await self._storage.list_all(platform=platform)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        notes: str | None = None,
    ) -> bool:
        """Actualiza el estado operativo de una cuenta.

        Args:
            account_id: UUID4 de la cuenta.
            status:     Nuevo estado. Ver ``AccountStatus``.
            notes:      Notas opcionales a añadir al campo ``notes`` del perfil.

        Returns:
            ``True`` si se actualizó, ``False`` si la cuenta no existe.
        """
        profile = await self._storage.load(account_id)
        if not profile:
            logger.warning(
                "update_status: cuenta no encontrada | account_id=%s", account_id
            )
            return False

        old_status = profile.status
        profile.status = status
        profile.touch_updated()

        if notes:
            prefix = f"[{profile.updated_at}] " if profile.updated_at else ""
            profile.notes = f"{prefix}{notes}\n{profile.notes}".strip()

        await self._storage.save(profile)
        logger.info(
            "Estado actualizado | account_id=%s | %s → %s",
            account_id,
            old_status.value,
            status.value,
        )
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Sección 2: Gestión de cookies
    # ─────────────────────────────────────────────────────────────────────────

    async def import_cookies(
        self,
        account_id: str,
        cookies: list[dict[str, Any]],
    ) -> bool:
        """Importa cookies en formato Playwright para una cuenta existente.

        Guarda las cookies en disco, resetea el contador de refresco
        y marca la cuenta como ``ACTIVE``.

        Formato de cookies (Playwright)::

            [
                {
                    "name": "c_user",
                    "value": "12345678",
                    "domain": ".facebook.com",
                    "path": "/",
                    "expires": 1735689600.0,
                    "httpOnly": false,
                    "secure": true,
                    "sameSite": "None"
                },
                ...
            ]

        Args:
            account_id: UUID4 de la cuenta destino.
            cookies:    Lista de dicts de cookies en formato Playwright.

        Returns:
            ``True`` si se importaron correctamente, ``False`` si la cuenta
            no existe.

        Raises:
            ValueError: Si ``cookies`` está vacío o no es una lista.
            StorageError: Si no se puede escribir el archivo.
        """
        if not isinstance(cookies, list) or len(cookies) == 0:
            raise ValueError("'cookies' debe ser una lista no vacía de dicts.")

        profile = await self._storage.load(account_id)
        if not profile:
            logger.error(
                "import_cookies: cuenta no encontrada | account_id=%s", account_id
            )
            return False

        # Guardar cookies en el backend activo (disco o BD).
        cookies_locator = await self._storage.save_cookies(
            profile.platform, account_id, cookies
        )

        # Actualizar perfil: cookies_path, estado y contador de refresco.
        profile.cookies_path = cookies_locator
        profile.status = AccountStatus.ACTIVE
        profile.activity.reset_cookie_refresh_counter()
        profile.touch_updated()

        await self._storage.save(profile)
        logger.info(
            "Cookies importadas | account_id=%s | username=%s | cookies=%d",
            account_id,
            profile.username,
            len(cookies),
        )
        return True

    async def import_cookies_from_file(
        self,
        account_id: str,
        cookies_file: str | Path,
    ) -> bool:
        """Importa cookies desde un archivo JSON externo.

        Útil cuando el operador exporta cookies desde el navegador
        (extensión EditThisCookie, Cookie-Editor, etc.) y las guarda
        en un archivo antes de pasarlas a Reaper.

        Args:
            account_id:   UUID4 de la cuenta destino.
            cookies_file: Ruta al archivo JSON de cookies externo.

        Returns:
            ``True`` si se importaron correctamente.

        Raises:
            FileNotFoundError: Si el archivo no existe.
            ValueError:        Si el archivo no contiene una lista válida.
            StorageError:      Si no se puede escribir internamente.
        """
        path = Path(cookies_file)
        if not path.exists():
            raise FileNotFoundError(
                f"Archivo de cookies no encontrado: '{path}'"
            )

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"El archivo de cookies no es JSON válido: {exc}") from exc

        if not isinstance(raw, list):
            raise ValueError(
                f"El archivo de cookies debe contener una lista JSON, "
                f"se encontró: {type(raw).__name__}"
            )

        return await self.import_cookies(account_id, raw)

    async def export_cookies(
        self,
        account_id: str,
    ) -> list[dict[str, Any]] | None:
        """Exporta las cookies de una cuenta en formato Playwright.

        Args:
            account_id: UUID4 de la cuenta.

        Returns:
            Lista de dicts de cookies, o ``None`` si la cuenta no tiene cookies.
        """
        profile = await self._storage.load(account_id)
        if not profile:
            return None
        return await self._storage.load_cookies(profile.platform, account_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Sección 2b: Acceso directo a cookies (para scrapers)
    # ─────────────────────────────────────────────────────────────────────────

    async def save_cookies(
        self,
        platform: str,
        account_id: str,
        cookies: list[dict[str, Any]],
    ) -> str:
        """Persiste cookies de una cuenta en el backend activo.

        Args:
            platform:   ``"facebook"`` o ``"instagram"``.
            account_id: UUID4 de la cuenta.
            cookies:    Lista de dicts de cookies en formato Playwright.

        Returns:
            Locator (string opaco) del lugar donde quedaron guardadas.

        Raises:
            StorageError: Si el backend no puede persistirlas.
        """
        return await self._storage.save_cookies(platform, account_id, cookies)

    async def load_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> list[dict[str, Any]] | None:
        """Lee las cookies de una cuenta desde el backend activo.

        Args:
            platform:   ``"facebook"`` o ``"instagram"``.
            account_id: UUID4 de la cuenta.

        Returns:
            Lista de dicts de cookies, o ``None`` si la cuenta no tiene.

        Raises:
            StorageError: Si el backend no puede leerlas.
        """
        return await self._storage.load_cookies(platform, account_id)

    async def delete_cookies(
        self,
        platform: str,
        account_id: str,
    ) -> bool:
        """Elimina las cookies de una cuenta del backend activo.

        Args:
            platform:   ``"facebook"`` o ``"instagram"``.
            account_id: UUID4 de la cuenta.

        Returns:
            ``True`` si existían y se eliminaron, ``False`` si no existían.
        """
        return await self._storage.delete_cookies(platform, account_id)

    async def close(self) -> None:
        """Cierra los recursos del backend (pools de conexión, clientes)."""
        await self._storage.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Sección 3: Rotación y registro de actividad
    # ─────────────────────────────────────────────────────────────────────────

    async def resolve_account(
        self,
        identifier: str,
        platform: str | None = None,
    ) -> AccountProfile | None:
        """Localiza una cuenta por su identificador.

        Acepta, en orden de precedencia:
        1. ``account_id`` (UUID4 interno de la cuenta).
        2. ``username`` (nombre de usuario o email registrado).
        3. ID de usuario de la plataforma (``c_user`` en Facebook,
           ``ds_user_id`` en Instagram), buscado en las cookies.

        Args:
            identifier: El valor a buscar (account_id, username o ID de usuario).
            platform:   Restringe la búsqueda a ``"facebook"`` o
                        ``"instagram"``. ``None`` busca en ambas plataformas.

        Returns:
            ``AccountProfile`` si se encontró exactamente una cuenta,
            ``None`` si no hay coincidencia.

        Raises:
            StorageError: Si el backend falla al leer.
        """
        # 1. account_id exacto (formato UUID4).
        if _is_uuid(identifier):
            profile = await self._storage.load(identifier)
            if profile is not None and (platform is None or profile.platform == platform):
                return profile

        # 2. username exacto (dentro de la plataforma si se indica).
        accounts = await self._storage.list_all(platform=platform)
        for acc in accounts:
            if acc.username == identifier:
                return acc

        # 3. ID de usuario de la plataforma (solo para valores numéricos,
        #    evitando leer cookies de todas las cuentas para cada búsqueda).
        if identifier.isdigit() and len(identifier) >= 5:
            for acc in accounts:
                if await self._account_matches_platform_user_id(acc, identifier):
                    return acc

        logger.debug("Cuenta no encontrada | identifier=%s | platform=%s", identifier, platform)
        return None

    async def _account_matches_platform_user_id(
        self,
        account: AccountProfile,
        user_id: str,
    ) -> bool:
        """Comprueba si una cuenta tiene el ID de usuario de plataforma dado."""
        cookie_name = PLATFORM_USER_ID_COOKIE.get(account.platform)
        if not cookie_name:
            return False
        try:
            cookies = await self._storage.load_cookies(account.platform, account.account_id)
        except StorageError:
            return False
        if not cookies:
            return False
        return any(
            c.get("name") == cookie_name and str(c.get("value", "")) == user_id
            for c in cookies
        )

    async def get_account_for_request(
        self,
        platform: str,
        *,
        preferred: str | None = None,
    ) -> AccountProfile | None:
        """Selecciona la cuenta a usar en una petición.

        Si se indica ``preferred`` (account_id, username o ID de usuario de
        plataforma), se usa esa cuenta concreta, ignorando el rotador. Es la
        forma de forzar manualmente una cuenta para una petición determinada.

        Si no hay ``preferred``, carga todas las cuentas de la plataforma y
        delega la selección al ``AccountRotator`` (comportamiento original).

        Retorna ``None`` (sin excepción) cuando no hay cuentas disponibles,
        permitiendo que el scraper caiga en modo anónimo.

        Args:
            platform: ``"facebook"`` o ``"instagram"``.
            preferred: Identificador opcional de la cuenta a forzar
                (``account_id``, ``username`` o ID de usuario de la plataforma).

        Returns:
            ``AccountProfile`` seleccionada, o ``None`` si no hay candidatas.
        """
        if preferred is not None:
            account = await self.resolve_account(preferred, platform=platform)
            if account is None:
                logger.warning(
                    "Cuenta preferida no encontrada | preferred=%s | platform=%s",
                    preferred,
                    platform,
                )
            elif not account.is_selectable:
                logger.warning(
                    "Cuenta preferida no seleccionable | preferred=%s | "
                    "username=%s | status=%s",
                    preferred,
                    account.username,
                    account.status.value,
                )
            return account

        accounts = await self._storage.list_all(platform=platform)
        return self._rotator.select(accounts, platform=platform)

    async def record_success(self, account_id: str) -> None:
        """Registra una petición exitosa y verifica el umbral de refresco.

        Si se alcanza el umbral ``cookie_refresh_threshold``, la cuenta se
        marca como ``NEEDS_REFRESH`` y se emite un aviso. La cuenta sigue
        siendo seleccionable pero con score reducido.

        Args:
            account_id: UUID4 de la cuenta que realizó la petición.
        """
        profile = await self._storage.load(account_id)
        if not profile:
            logger.debug("record_success: cuenta no encontrada | account_id=%s", account_id)
            return

        profile.activity.record_success()

        # ── Verificar umbral de refresco de cookies ───────────────────────────
        if (
            profile.activity.requests_since_cookie_refresh
            >= self.cookie_refresh_threshold
            and profile.status == AccountStatus.ACTIVE
        ):
            profile.status = AccountStatus.NEEDS_REFRESH
            profile.touch_updated()
            logger.warning(
                "Umbral de refresco alcanzado | account_id=%s | username=%s | "
                "peticiones_desde_refresco=%d — Importa cookies frescas con "
                "account_manager.import_cookies('%s', new_cookies)",
                account_id,
                profile.username,
                profile.activity.requests_since_cookie_refresh,
                account_id,
            )

        await self._storage.update_activity(account_id, profile.activity)

        # Si el estado cambió, persistir el perfil completo.
        if profile.status == AccountStatus.NEEDS_REFRESH:
            await self._storage.save(profile)

    async def record_failure(
        self,
        account_id: str,
        error_message: str,
    ) -> None:
        """Registra una petición fallida y evalúa si aplicar rate-limit.

        Si los últimos ``CONSECUTIVE_FAILURES_FOR_RATE_LIMIT`` fallos son
        consecutivos, marca la cuenta como ``RATE_LIMITED`` con penalización
        en el rotador.

        Args:
            account_id:    UUID4 de la cuenta que realizó la petición.
            error_message: Descripción del error ocurrido.
        """
        profile = await self._storage.load(account_id)
        if not profile:
            logger.debug("record_failure: cuenta no encontrada | account_id=%s", account_id)
            return

        profile.activity.record_failure(error_message)

        # ── Detectar fallos consecutivos para aplicar rate-limit ──────────────
        consecutive_failures = self._count_consecutive_failures(profile)
        if (
            consecutive_failures >= CONSECUTIVE_FAILURES_FOR_RATE_LIMIT
            and profile.status == AccountStatus.ACTIVE
        ):
            profile.status = AccountStatus.RATE_LIMITED
            profile.touch_updated()
            logger.warning(
                "Cuenta marcada como RATE_LIMITED | account_id=%s | username=%s | "
                "fallos_consecutivos=%d",
                account_id,
                profile.username,
                consecutive_failures,
            )

        await self._storage.update_activity(account_id, profile.activity)

        if profile.status != AccountStatus.ACTIVE:
            await self._storage.save(profile)

    # ─────────────────────────────────────────────────────────────────────────
    # Sección 4: Utilidades y diagnóstico
    # ─────────────────────────────────────────────────────────────────────────

    async def pool_summary(
        self,
        platform: str | None = None,
    ) -> dict[str, Any]:
        """Retorna un resumen del estado del pool de cuentas.

        Útil para monitoreo y diagnóstico del operador.

        Args:
            platform: Filtrar por plataforma, o ``None`` para todas.

        Returns:
            Dict con métricas agregadas del pool.

        Ejemplo de retorno::

            {
                "total": 5,
                "active": 3,
                "needs_refresh": 1,
                "rate_limited": 1,
                "suspended": 0,
                "cookie_expired": 0,
                "unknown": 0,
                "by_platform": {
                    "facebook": {"total": 3, "active": 2, ...},
                    "instagram": {"total": 2, "active": 1, ...},
                }
            }
        """
        accounts = await self._storage.list_all(platform=platform)

        status_counts: dict[str, int] = {s.value: 0 for s in AccountStatus}
        platform_counts: dict[str, dict[str, int]] = {}

        for acc in accounts:
            status_counts[acc.status.value] += 1

            if acc.platform not in platform_counts:
                platform_counts[acc.platform] = {
                    "total": 0,
                    **{s.value: 0 for s in AccountStatus},
                }
            platform_counts[acc.platform]["total"] += 1
            platform_counts[acc.platform][acc.status.value] += 1

        return {
            "total": len(accounts),
            **status_counts,
            "by_platform": platform_counts,
        }

    async def get_ranked_accounts(
        self,
        platform: str,
    ) -> list[dict[str, Any]]:
        """Retorna las cuentas rankeadas por score del rotador (para diagnóstico).

        Args:
            platform: ``"facebook"`` o ``"instagram"``.

        Returns:
            Lista de dicts con ``username``, ``status``, ``score`` y
            métricas clave de actividad.
        """
        accounts = await self._storage.list_all(platform=platform)
        ranked = self._rotator.rank_all(accounts, platform=platform)

        return [
            {
                "rank": idx + 1,
                "account_id": sa.account.account_id,
                "username": sa.account.username,
                "status": sa.account.status.value,
                "score": round(sa.score, 4),
                "success_rate": round(sa.account.activity.success_rate, 4),
                "total_requests": sa.account.activity.total_requests,
                "successful_requests": sa.account.activity.successful_requests,
                "failed_requests": sa.account.activity.failed_requests,
                "requests_since_cookie_refresh": sa.account.activity.requests_since_cookie_refresh,
                "last_used_at": sa.account.activity.last_used_at,
                "has_cookies": sa.account.has_cookies,
                "recent_errors_count": len(sa.account.activity.recent_errors),
            }
            for idx, sa in enumerate(ranked)
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers privados
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _count_consecutive_failures(profile: AccountProfile) -> int:
        """Cuenta los fallos más recientes en la cola de errores.

        Analiza ``recent_errors`` para detectar ráfagas de fallos consecutivos
        sin éxitos intercalados. Usa ``successful_requests`` como referencia
        aproximada comparando con ``total_requests``.

        Implementación simple: compara ``failed_requests`` recientes
        mirando cuántos de los últimos N registros son errores.

        Returns:
            Número de fallos consecutivos recientes (estimación).
        """
        # Estimación rápida: si el éxito más reciente fue hace
        # más peticiones que el umbral, contar errores recientes.
        activity = profile.activity
        if activity.total_requests == 0:
            return 0

        # Fallos en las últimas CONSECUTIVE_FAILURES_FOR_RATE_LIMIT peticiones.
        recent_window = CONSECUTIVE_FAILURES_FOR_RATE_LIMIT
        if activity.total_requests < recent_window:
            return activity.failed_requests

        # Aproximación: ratio de fallos en ventana reciente.
        # Para precisión total se necesitaría un log ordenado de resultados.
        recent_fail_ratio = (
            activity.failed_requests / activity.total_requests
        )

        # Si más del 80% de las últimas peticiones fallaron, contar el umbral.
        if recent_fail_ratio > 0.8:
            return recent_window

        return int(recent_fail_ratio * recent_window)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados del módulo
# ─────────────────────────────────────────────────────────────────────────────


def _is_uuid(value: str) -> bool:
    """True si ``value`` tiene formato UUID4 (cuenta_id interno).

    Args:
        value: Cadena a comprobar.

    Returns:
        ``True`` si es un UUID válido, ``False`` en caso contrario.
    """
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
