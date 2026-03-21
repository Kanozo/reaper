from typing import Any

from reaper.config import ScraperConfig
from reaper.utils.logger import get_logger, setup_logging
from reaper.utils.platforms import detect_platform_from_url

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
    ) -> dict[str, Any]:
        global _logging_initialized
        if not _logging_initialized:
            # Primera llamada sin CLI: configurar con nivel apropiado.
            # La CLI habrá llamado setup_logging() antes, por lo que esta
            # rama solo se ejecuta en uso programático directo.
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
        )

        logger.info("Iniciando scrape | url=%s | headless=%s | debug=%s", url, headless, debug)

        platform = detect_platform_from_url(url)
        logger.debug("Plataforma detectada: %s", platform)

        return await self._dispatch(config, platform)

    async def _dispatch(
        self,
        config: ScraperConfig,
        platform: str,
    ) -> dict[str, Any]:

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
            raise ScrapingError(f"Error al scrapear '{config.url}': {exc}") from exc

        logger.info(
            "Scrape completado | url=%s | status=%s",
            config.url,
            result.get("status", "unknown"),
        )
        return result