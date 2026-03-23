"""
reaper/scrapers/instagram.py
============================
Scraper de orquestación para posts y Reels de Instagram.

Cambios respecto a la versión anónima:
    - ``run()`` llama a ``_fetch_with_account("instagram")`` en lugar
      de ``_fetch()`` directamente, activando rotación de cuentas si
      ``config.account_manager`` está configurado.
    - ``_fetch()`` acepta el parámetro ``cookies`` y lo pasa al
      ``ContentFetcher``.

Sin cambios: estrategia de dos intentos (IgPostParser → IgReelParser).
"""

from datetime import datetime
from typing import Any

from reaper.network.content_fetcher import ContentFetcher
from reaper.parsers import IgPostParser, IgReelParser
from reaper.scrapers.base import BaseScraper
from reaper.utils.logger import get_logger

logger = get_logger(__name__)


class InstagramScraper(BaseScraper):
    """Scraper para posts y Reels de Instagram.

    Con AccountManager: selecciona automáticamente la mejor cuenta disponible.
    Sin AccountManager: opera en modo anónimo (comportamiento original).
    """

    async def run(self) -> dict[str, Any]:
        logger.info("Iniciando extracción Instagram | url=%s", self.config.url)

        # ── Fetch primario (con rotación de cuentas si está configurada) ──────
        fetch_result = await self._fetch_with_account("instagram")

        if not fetch_result.success:
            error_msg = fetch_result.error or "Error desconocido en el fetch."
            logger.error("Fetch fallido | url=%s | error=%s", self.config.url, error_msg)
            return self._error_result(error_msg)

        final_url = fetch_result.final_url or self.config.url
        is_reel   = "/reel/" in final_url.lower()
        logger.info(
            "Tipo detectado: %s | url=%s", "REEL" if is_reel else "POST", final_url
        )

        # ── Intento 1: IgPostParser ───────────────────────────────────────────
        result: dict[str, Any] = IgPostParser(
            html_content=fetch_result.html_content,
            final_url=final_url,
            original_url=self.config.url,
            debug=self.config.debug,
        ).parse()

        # ── Intento 2: IgReelParser — solo si IgPostParser no encontró datos ──
        if result.get("error"):
            logger.info(
                "IgPostParser sin datos, reintentando con IgReelParser | url=%s",
                final_url,
            )
            result = IgReelParser(
                html_content=fetch_result.html_content,
                final_url=final_url,
                original_url=self.config.url,
                debug=self.config.debug,
            ).parse()

        logger.info(
            "Extracción completada | error=%s | url=%s",
            result.get("error"),
            self.config.url,
        )
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Fetch
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch(
        self,
        override_url: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ):
        """Crea un ContentFetcher y ejecuta la navegación.

        Instagram no necesita infinity_scroll: siempre desactivado.

        Args:
            override_url: URL alternativa (no usada en Instagram actualmente).
            cookies:      Cookies de sesión a inyectar antes de navegar.
        """
        fetcher = ContentFetcher(
            url=override_url or self.config.url,
            headless=self.config.headless,
            debug=self.config.debug,
        )
        return await fetcher.fetch(
            screenshot=self.config.screenshot,
            auto_scroll=self.config.auto_scroll,
            infinity_scroll=False,   # Instagram no necesita infinity scroll
            proxy_server=self.config.proxy_server,
            proxy_username=self.config.proxy_username,
            proxy_password=self.config.proxy_password,
            cookies=cookies,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helper
    # ──────────────────────────────────────────────────────────────────────────

    def _error_result(self, error_msg: str) -> dict[str, Any]:
        return {
            **self._base_result("instagram"),
            "platform":          "instagram",
            "post_url":          self.config.url,
            "scraped_at":        datetime.now(),
            "error":             error_msg,
            "raw_data_available": False,
            "feed":              [],
            "status":            "error",
        }
