"""
reaper/scrapers/base.py
=======================
Clase base abstracta para todos los scrapers de plataforma.

Define el contrato: un método asíncrono ``run()`` que devuelve un dict,
y un método utilitario ``_base_result()`` con el esqueleto mínimo garantizado.

Jerarquía::

    BaseScraper  (abstracto)
    ├── FacebookScraper
    └── InstagramScraper
"""
from abc import ABC, abstractmethod
from typing import Any

from reaper.config import ScraperConfig
from reaper.utils.logger import get_logger


class BaseScraper(ABC):
    """Clase base abstracta para scrapers de plataforma.

    Las subclases deben implementar ``run()`` como método asíncrono.
    El resultado siempre debe incluir las claves ``url``, ``platform``
    y ``status``.

    Args:
        config: Configuración validada de la sesión de scraping.
    """

    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        # Usamos el nombre del módulo concreto (ej. reaper.scrapers.facebook)
        # para que los logs sean filtrables por scraper específico.
        self.logger = get_logger(self.__class__.__module__)

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

    def _base_result(self, platform: str) -> dict[str, Any]:
        """Genera el esqueleto mínimo garantizado para cualquier resultado.

        Usar como base al construir el resultado de ``run()``::

            result = {
                **self._base_result("facebook"),
                "id": "...",
                "text": "...",
            }

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