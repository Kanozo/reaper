"""
reaper/scrapers/base.py
=======================
Clase base abstracta para todos los scrapers de plataforma.

Extiende el comportamiento original con ``_fetch_with_account()``: un wrapper
que selecciona la mejor cuenta disponible del ``AccountManager``, inyecta sus
cookies en el ``ContentFetcher`` y registra el resultado en la actividad de la
cuenta.

Compatibilidad hacia atrás garantizada:
    Si ``config.account_manager is None`` (comportamiento por defecto),
    ``_fetch_with_account()`` delega directamente en ``_fetch()`` sin ningún
    cambio en el flujo. Los scrapers existentes no requieren modificaciones.

Jerarquía::

    BaseScraper  (abstracto)
    ├── FacebookScraper
    └── InstagramScraper
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from reaper.config import ScraperConfig
from reaper.utils.logger import get_logger


class BaseScraper(ABC):
    """Clase base abstracta para scrapers de plataforma.

    Las subclases deben implementar ``run()`` y ``_fetch()``.
    El método ``_fetch_with_account()`` está implementado aquí y es el punto
    de entrada principal para fetches que requieren rotación de cuentas.

    Args:
        config: Configuración validada de la sesión de scraping.
    """

    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self.logger = get_logger(self.__class__.__module__)

        # ID de la cuenta que se está usando en la sesión actual.
        # None si se usa modo anónimo. Se asigna en _fetch_with_account().
        # Las subclases pueden leerlo para contexto de logging.
        self._active_account_id: str | None = None

    @abstractmethod
    async def run(self) -> dict[str, Any]:
        """Ejecuta la sesión completa de scraping.

        Returns:
            ``dict[str, Any]`` con los datos extraídos.
            Debe incluir siempre: ``url``, ``platform``, ``status``.

        Raises:
            ScrapingError: Si el proceso falla irrecuperablemente.
        """
        ...

    @abstractmethod
    async def _fetch(
        self,
        override_url: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ):
        """Fetch base: crea ContentFetcher y ejecuta la navegación.

        Cada scraper implementa este método con sus parámetros específicos
        (p.ej. InstagramScraper desactiva infinity_scroll siempre).

        Args:
            override_url: URL alternativa a la de ``config.url``.
                          Se usa en fetches secundarios (merge reel↔video).
            cookies:      Lista de cookies en formato Playwright a inyectar
                          en el contexto del navegador antes de navegar.
                          ``None`` = sin cookies (modo anónimo).

        Returns:
            ``FetchResult`` con HTML renderizado, tráfico y metadatos.
        """
        ...

    def _base_result(self, platform: str) -> dict[str, Any]:
        """Genera el esqueleto mínimo garantizado para cualquier resultado.

        Args:
            platform: Nombre de la plataforma (``"facebook"`` o ``"instagram"``).

        Returns:
            Dict con ``url``, ``platform`` y ``status="ok"``.
        """
        return {
            "url": self.config.url,
            "platform": platform,
            "status": "ok",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Lógica de rotación de cuentas
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch_with_account(
        self,
        platform: str,
        override_url: str | None = None,
    ):
        """Fetch con selección automática de cuenta autenticada (si disponible).

        Este método es el punto de entrada principal para el primer fetch de
        cada sesión de scraping. Coordina:

        1. Consulta al ``AccountManager`` por la mejor cuenta disponible.
        2. Lectura de cookies de disco para esa cuenta.
        3. Fetch con cookies inyectadas.
        4. Registro del resultado (éxito/fallo) en la actividad de la cuenta.

        Si ``config.account_manager`` es ``None``, o si no hay cuentas
        disponibles para la plataforma, delega en ``_fetch()`` directamente
        en modo anónimo. Este fallback garantiza compatibilidad total hacia atrás.

        Args:
            platform:     ``"facebook"`` o ``"instagram"``.
            override_url: URL alternativa (raramente necesaria en el fetch primario).

        Returns:
            ``FetchResult`` igual que ``_fetch()``.
        """
        manager = self.config.account_manager

        # ── Modo anónimo: sin AccountManager → flujo original ────────────────
        if manager is None:
            return await self._fetch(override_url=override_url)

        # ── Selección de cuenta ───────────────────────────────────────────────
        account = await manager.get_account_for_request(platform)

        if account is None:
            # Pool vacío o todas las cuentas no seleccionables → anónimo.
            self.logger.warning(
                "AccountManager activo pero sin cuentas disponibles para "
                "platform=%s — continuando en modo anónimo",
                platform,
            )
            return await self._fetch(override_url=override_url)

        # ── Cargar cookies de disco ───────────────────────────────────────────
        cookies = manager._local_storage.load_cookies(platform, account.account_id)

        if not cookies:
            # Cuenta registrada pero sin archivo de cookies → anónimo.
            self.logger.warning(
                "Cuenta seleccionada sin cookies en disco | "
                "account_id=%s | username=%s — continuando en modo anónimo",
                account.account_id,
                account.username,
            )
            return await self._fetch(override_url=override_url)

        # ── Fetch autenticado ─────────────────────────────────────────────────
        self._active_account_id = account.account_id

        self.logger.info(
            "Fetch autenticado | platform=%s | username=%s | "
            "cookies=%d | peticiones_cuenta=%d | score_aprox=%.0f%%",
            platform,
            account.username,
            len(cookies),
            account.activity.total_requests,
            account.activity.success_rate * 100,
        )

        fetch_result = await self._fetch(
            override_url=override_url,
            cookies=cookies,
        )

        # ── Registrar resultado en la actividad de la cuenta ─────────────────
        if fetch_result.success:
            await manager.record_success(account.account_id)
        else:
            error_msg = fetch_result.error or "fetch_failed_unknown"
            await manager.record_failure(account.account_id, error_msg)
            self.logger.debug(
                "Fallo registrado en cuenta | account_id=%s | error=%s",
                account.account_id,
                error_msg,
            )

        return fetch_result
