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
import re 
from urllib.parse import urlparse

from reaper.auth.models import AccountStatus
from reaper.network.content_fetcher import ContentFetcher
from reaper.parsers import IgPostParser, IgReelParser, IgProfileParser
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

    def detect_instagram_content_type(self, url: str) -> str:
        """
        Retorna 'post', 'reel', 'profile' o 'unknown' según el path de la URL.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')  # elimina trailing slash para comparar

        # Patrón para post: /p/<shortcode>[/...]
        if re.search(r'^/p/[A-Za-z0-9_-]+', path):
            return 'post'
        # Patrón para reel: /reel/<shortcode>[/...]
        if re.search(r'^/reel/[A-Za-z0-9_-]+', path):
            return 'reel'
        # Patrón para perfil: solo un segmento (username) y nada más
        if re.match(r'^/[A-Za-z0-9_.]+$', path) and not path.count('/') > 1:
            return 'profile'
        return 'unknown'

    async def run(self) -> dict[str, Any]:
        logger.info("Iniciando extracción Instagram | url=%s", self.config.url)

        fetch_result = await self._fetch_with_account("instagram")
        if not fetch_result.success:
            return self._error_result(fetch_result.error or "Error desconocido")

        final_url = fetch_result.final_url or self.config.url

        # Manejo de auth (sin cambios)
        auth = requires_auth(fetch_result.html_content, final_url)
        if auth.auth_type == "content_unavailable":
            return self._content_unavailable_result(final_url)
        if auth.requires_auth:
            retry = await self._handle_auth_wall(auth.reason, final_url)
            if retry is None:
                return self._error_result(f"Authentication required — {auth.reason}")
            fetch_result = retry
            final_url = fetch_result.final_url or final_url

        # Nueva detección
        content_type = self.detect_instagram_content_type(final_url)
        logger.info("Tipo detectado: %s | url=%s", content_type.upper(), final_url)

        # Selección del parser
        if content_type == 'post':
            parser = IgPostParser(
                html_content=fetch_result.html_content,
                final_url=final_url,
                original_url=self.config.url,
                debug=self.config.debug,
            )
        elif content_type == 'reel':
            parser = IgReelParser(
                html_content=fetch_result.html_content,
                final_url=final_url,
                original_url=self.config.url,
                debug=self.config.debug,
            )
        elif content_type == 'profile':
            parser = IgProfileParser(
                html_content=fetch_result.html_content,
                final_url=final_url,
                original_url=self.config.url,
                debug=self.config.debug,
            )
        else:
            # Fallback: intentar post, luego reel (como antes)
            logger.warning("Tipo desconocido, intentando IgPostParser como fallback")
            result = IgPostParser(...).parse()
            if result.get("error"):
                result = IgReelParser(...).parse()
            return result

        result = parser.parse()
        logger.info("Extracción completada | error=%s | url=%s", result.get("error"), self.config.url)
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
