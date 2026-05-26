"""
reaper/scrapers/instagram.py
============================
Scraper de orquestación para posts y Reels de Instagram.

Flujo de reintento por muro de autenticación
---------------------------------------------
Mismo mecanismo que ``FacebookScraper``: si se detecta un muro de
autenticación en el HTML, el scraper distingue si la petición previa era
anónima o autenticada y actúa en consecuencia.

- **Fetch anónimo + muro**: reintenta con cuenta autenticada si hay
  ``AccountManager`` con cuentas disponibles.
- **Fetch autenticado + muro**: las cookies de la cuenta activa han
  caducado. Se marca como ``COOKIE_EXPIRED`` y se reintenta con otra.
- **Sin posibilidad de retry**: retorna error sin seguir intentando.
"""

from datetime import datetime
from typing import Any

from reaper.auth.models import AccountStatus
from reaper.network.content_fetcher import ContentFetcher
from reaper.parsers import IgPostParser, IgReelParser
from reaper.scrapers.base import BaseScraper
from reaper.utils.fb_auth_detector import requires_auth
from reaper.utils.logger import get_logger

logger = get_logger(__name__)


class InstagramScraper(BaseScraper):
    """Scraper para posts y Reels de Instagram.

    Con AccountManager:
        - Selecciona automáticamente la mejor cuenta disponible.
        - Si el HTML devuelve muro de auth, reintenta con cuenta autenticada
          (o con una cuenta diferente si la actual tiene cookies caducadas).

    Sin AccountManager: opera en modo anónimo (comportamiento original).
    """

    async def run(self) -> dict[str, Any]:
        logger.info("Iniciando extracción Instagram | url=%s", self.config.url)

        # ── Fetch primario ────────────────────────────────────────────────────
        fetch_result = await self._fetch_with_account("instagram")

        if not fetch_result.success:
            error_msg = fetch_result.error or "Error desconocido en el fetch."
            logger.error("Fetch fallido | url=%s | error=%s", self.config.url, error_msg)
            return self._error_result(error_msg)

        final_url = fetch_result.final_url or self.config.url

        # ── Detección de muro de autenticación ───────────────────────────────
        auth = requires_auth(fetch_result.html_content, final_url)

        if auth.auth_type == "content_unavailable":
            logger.info(
                "Contenido no disponible | url=%s | reason=%s",
                final_url, auth.reason,
            )
            return self._content_unavailable_result(final_url)
    
        if auth.requires_auth:
            retry = await self._handle_auth_wall(auth.reason, final_url)
            if retry is None:
                return self._error_result(
                    f"Authentication required — {auth.reason}",
                )
            fetch_result = retry
            final_url = fetch_result.final_url or final_url

        is_reel = "/reel/" in final_url.lower()
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
    # Manejo del muro de autenticación
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_auth_wall(
        self,
        reason: str | None,
        final_url: str,
    ):
        """Gestiona un muro de autenticación detectado en el HTML.

        Idéntico en lógica a ``FacebookScraper._handle_auth_wall()``:
        distingue fetch anónimo de autenticado y actúa en consecuencia.

        Args:
            reason:    Motivo del muro según ``requires_auth()``.
            final_url: URL final del fetch original (para logging).

        Returns:
            Nuevo ``FetchResult`` sin muro de auth, o ``None`` si no fue
            posible recuperarse.
        """
        manager = self.config.account_manager

        # ── Escenario A: fetch anónimo ────────────────────────────────────────
        if self._active_account_id is None:
            if manager is None:
                logger.warning(
                    "Muro de auth en fetch anónimo | reason=%s | "
                    "sin AccountManager — no es posible reintentar | url=%s",
                    reason, final_url,
                )
                return None

            logger.info(
                "Muro de auth en fetch anónimo | reason=%s | "
                "reintentando con cuenta autenticada | url=%s",
                reason, final_url,
            )
            return await self._retry_with_account(reason, final_url)

        # ── Escenario B: fetch autenticado → cookies caducadas ────────────────
        expired_account_id = self._active_account_id
        logger.warning(
            "Muro de auth con cuenta autenticada — cookies expiradas | "
            "account_id=%s | reason=%s | url=%s",
            expired_account_id, reason, final_url,
        )

        await manager.update_status(
            account_id=expired_account_id,
            status=AccountStatus.COOKIE_EXPIRED,
            notes=(
                f"Cookies invalidadas por Instagram durante scraping. "
                f"Muro detectado: {reason}. "
                f"URL: {final_url}"
            ),
        )

        self._active_account_id = None

        logger.info(
            "Cuenta marcada COOKIE_EXPIRED | account_id=%s | "
            "reintentando con cuenta diferente | url=%s",
            expired_account_id, final_url,
        )
        return await self._retry_with_account(reason, final_url)

    async def _retry_with_account(
        self,
        original_reason: str | None,
        final_url: str,
    ):
        """Reintenta el fetch con una cuenta autenticada y verifica el resultado.

        Args:
            original_reason: Motivo del muro original (para logging).
            final_url:       URL final del intento original.

        Returns:
            ``FetchResult`` sin muro de auth, o ``None`` si falló.
        """
        retry_result = await self._fetch_with_account("instagram")

        if not retry_result.success:
            logger.warning(
                "Reintento autenticado fallido | error=%s | url=%s",
                retry_result.error, final_url,
            )
            return None

        retry_final_url = retry_result.final_url or self.config.url
        retry_auth = requires_auth(retry_result.html_content, retry_final_url)

        if retry_auth.requires_auth:
            logger.warning(
                "Reintento autenticado también bloqueado | "
                "reason_original=%s | reason_retry=%s | url=%s",
                original_reason, retry_auth.reason, final_url,
            )
            return None

        logger.info(
            "Reintento autenticado exitoso | url=%s", retry_final_url
        )
        return retry_result

    # ──────────────────────────────────────────────────────────────────────────
    # Fetch
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch(
        self,
        override_url: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Crea un ContentFetcher y ejecuta la navegación.

        Instagram no necesita infinity_scroll: siempre desactivado.
        """
        fetcher = ContentFetcher(
            url=override_url or self.config.url,
            headless=self.config.headless,
            debug=self.config.debug,
        )
        return await fetcher.fetch(
            screenshot=self.config.screenshot,
            auto_scroll=self.config.auto_scroll,
            infinity_scroll=False,
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
            "platform":           "instagram",
            "post_url":           self.config.url,
            "scraped_at":         datetime.now(),
            "error":              error_msg,
            "raw_data_available": False,
            "feed":               [],
            "status":             "error",
        }

    def _content_unavailable_result(self, final_url: str = "") -> dict[str, Any]:
        """Resultado para contenido eliminado o restringido permanentemente.

        Se diferencia de ``_error_result`` en el ``status``: ``content_unavailable``
        indica al caller que el contenido no existe o no es accesible para nadie,
        no que el scraping haya fallado por un error técnico o de autenticación.

        Args:
            final_url: URL final tras redirecciones HTTP.

        Returns:
            Dict con ``status="content_unavailable"`` y ``raw_data_available=False``.
        """
        return {
            **self._base_result("instagram"),
            "final_url":               final_url or self.config.url,
            "scraped_at":              datetime.now(),
            "error":                   "Content not available — deleted or restricted",
            "raw_data_available":      False,
            "graphql_responses_count": 0,
            "status":                  "content_unavailable",
        }
