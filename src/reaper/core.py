"""
reaper/core.py
==============
Clase central ``Reaper`` y excepciones públicas de la librería.

Cambios respecto a la versión original:
    - ``Reaper.__init__`` acepta un ``AccountManager`` opcional que se propaga
      a ``ScraperConfig`` en cada llamada a ``scrape()``.
    - Sin ``AccountManager`` (defecto ``None``) el comportamiento es idéntico
      al original: 100% compatible hacia atrás.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from reaper.config import ScraperConfig
from reaper.utils.logger import get_logger, setup_logging
from reaper.utils.platforms import detect_platform_from_url

if TYPE_CHECKING:
    from reaper.auth.account_manager import AccountManager

logger = get_logger(__name__)


class UnsupportedPlatformError(Exception):
    """La URL no corresponde a ninguna plataforma soportada."""


class ScrapingError(Exception):
    """El proceso de scraping falló en tiempo de ejecución."""


# Centinela para la inicialización perezosa del logging.
# La CLI llama a setup_logging() explícitamente antes de instanciar Reaper.
# En uso programático (tests, scripts) lo configuramos la primera vez que
# se llama a scrape(), para que los logs sean visibles sin configuración manual.
_logging_initialized: bool = False


class Reaper:
    """Orquestador principal de Reaper.

    Puede reutilizarse para múltiples scrapes manteniendo el mismo
    ``AccountManager``, lo que permite que el rotador acumule historial
    de actividad entre peticiones.

    Args:
        account_manager: Gestor de cuentas para rotación autenticada.
                         ``None`` = modo anónimo (comportamiento original).

    Ejemplo con cuentas::

        from reaper.auth import AccountManager
        from reaper import Reaper

        manager = AccountManager()
        reaper  = Reaper(account_manager=manager)

        result = await reaper.scrape("https://www.facebook.com/reel/123")
        result = await reaper.scrape("https://www.instagram.com/p/abc/")

    Ejemplo sin cuentas (comportamiento original)::

        reaper = Reaper()
        result = await reaper.scrape("https://www.facebook.com/reel/123")
    """

    def __init__(
        self,
        account_manager: "AccountManager | None" = None,
    ) -> None:
        self._account_manager = account_manager

    async def scrape(
        self,
        url: str,
        *,
        headless: bool = True,
        debug: bool = False,
        screenshot: bool = False,
        auto_scroll: bool = True,
        infinity_scroll: bool = False,
        proxy_server: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Extrae datos de una URL de Facebook o Instagram.

        Args:
            url:            URL de Facebook o Instagram. Obligatorio.
            headless:       Ejecutar Firefox sin interfaz gráfica. Defecto: True.
            debug:          Logging detallado + artefactos. Defecto: False.
            screenshot:     Capturar PNG del contenedor principal. Defecto: False.
            auto_scroll:    Scroll automático por altura DOM. Defecto: True.
            infinity_scroll: Scroll continuo por tráfico de red. Defecto: False.
            proxy_server:   URL del proxy, p.ej. ``"http://ip:8080"``.
            proxy_username: Usuario del proxy.
            proxy_password: Contraseña del proxy.
            account:        Identificador de la cuenta a forzar en esta sesión:
                            ``account_id`` (UUID4), ``username`` o ID de usuario
                            de la plataforma (``c_user``/``ds_user_id``).
                            ``None`` = rotación automática. Requiere que el
                            ``Reaper`` se haya construido con ``account_manager``.

        Returns:
            ``dict[str, Any]`` con los datos scrapeados.
            Siempre incluye: ``url``, ``platform``, ``status``.

        Raises:
            ValueError:              URL vacía o esquema inválido.
            UnsupportedPlatformError: URL no es Facebook ni Instagram.
            ScrapingError:           Error en runtime durante fetch o parseo.
        """
        global _logging_initialized
        if not _logging_initialized:
            setup_logging(level="DEBUG" if debug else "INFO")
            _logging_initialized = True

        config = ScraperConfig(
            url=url,
            headless=headless,
            debug=debug,
            screenshot=screenshot,
            auto_scroll=auto_scroll,
            infinity_scroll=infinity_scroll,
            proxy_server=proxy_server,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            account_manager=self._account_manager,  # None en modo anónimo
            preferred_account=account,
        )

        auth_mode = (
            f"autenticado ({type(self._account_manager).__name__})"
            if self._account_manager
            else "anónimo"
        )
        logger.info(
            "Iniciando scrape | url=%s | headless=%s | debug=%s | modo=%s",
            url, headless, debug, auth_mode,
        )

        platform = detect_platform_from_url(url)
        logger.debug("Plataforma detectada: %s", platform)

        return await self._dispatch(config, platform)

    async def _dispatch(
        self,
        config: ScraperConfig,
        platform: str,
    ) -> dict[str, Any]:
        """Selecciona el scraper de plataforma y ejecuta la extracción."""

        match platform:
            case "facebook":
                from reaper.scrapers.facebook import FacebookScraper
                scraper = FacebookScraper(config)

            case "instagram":
                from reaper.scrapers.instagram import InstagramScraper
                scraper = InstagramScraper(config)

            case _:
                raise UnsupportedPlatformError(
                    f"No hay scraper registrado para la URL: '{config.url}'. "
                    "Plataformas soportadas: Facebook, Instagram."
                )

        try:
            result: dict[str, Any] = await scraper.run()
        except (UnsupportedPlatformError, ValueError):
            raise
        except Exception as exc:
            logger.exception("Scraping fallido para %s", config.url)
            raise ScrapingError(
                f"Error al scrapear '{config.url}': {exc}"
            ) from exc

        logger.info(
            "Scrape completado | url=%s | status=%s",
            config.url,
            result.get("status", "unknown"),
        )
        return result
