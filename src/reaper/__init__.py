"""
reaper/__init__.py
==================
API pública de la librería Reaper.

Expone la función ``scrape()`` (wrapper conveniente) y la clase ``Reaper``
(para uso avanzado con múltiples scrapes). Todos los tipos de excepción
relevantes se re-exportan desde aquí para que el consumidor no necesite
navegar la jerarquía interna de módulos.

Uso rápido::

    import asyncio
    from reaper import scrape

    result = asyncio.run(scrape("https://www.facebook.com/reel/123456"))
    print(result["platform"])  # 'facebook'

Uso con la clase directamente (múltiples scrapes reutilizando configuración)::

    import asyncio
    from reaper import Reaper

    async def main():
        r = Reaper()
        post  = await r.scrape("https://www.instagram.com/p/abc123")
        reel  = await r.scrape("https://www.facebook.com/reel/456", debug=True)
        return post, reel

    asyncio.run(main())

Manejo de errores::

    from reaper import scrape, UnsupportedPlatformError, ScrapingError

    try:
        result = asyncio.run(scrape("https://twitter.com/user"))
    except UnsupportedPlatformError:
        print("Plataforma no soportada")
    except ScrapingError as exc:
        print(f"Fallo el scraping: {exc}")
"""

from typing import Any

# Re-exportaciones del API público
# El consumidor solo necesita importar desde 'reaper', nunca desde submódulos
from reaper.config import ScraperConfig
from reaper.core import Reaper, ScrapingError, UnsupportedPlatformError

__version__ = "0.1.0"
__all__ = [
    "scrape",
    "Reaper",
    "ScraperConfig",
    "ScrapingError",
    "UnsupportedPlatformError",
]


async def scrape(
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
    """Scrape una URL y devuelve los datos extraídos como diccionario.

    Función de conveniencia a nivel de módulo. Internamente crea una
    instancia de ``Reaper`` y delega en ``Reaper.scrape()``.

    Args:
        url: URL de Facebook o Instagram a scrapear. Obligatorio.
        headless: Ejecutar el navegador sin interfaz gráfica. Defecto: True.
        debug: Logging detallado + artefactos en data/debug_artifacts/. Defecto: False.
        screenshot: Capturar PNG del contenedor principal. Defecto: False.
        auto_scroll: Scroll automático por altura DOM. Defecto: True.
        infinity_scroll: Scroll continuo por tráfico de red. Defecto: False.
        proxy_server: URL del proxy, p.ej. "http://ip:8080". Defecto: None.
        proxy_username: Usuario del proxy. Defecto: None.
        proxy_password: Contraseña del proxy. Defecto: None.

    Returns:
        ``dict[str, Any]`` con los datos scrapeados.
        Siempre incluye: ``url``, ``platform``, ``status``.

    Raises:
        ValueError: URL vacía o esquema inválido.
        UnsupportedPlatformError: URL no es Facebook ni Instagram.
        ScrapingError: Error en runtime durante fetch o parseo.

    Examples::

        # Mínimo
        result = asyncio.run(scrape("https://www.instagram.com/p/abc123"))

        # Con opciones
        result = asyncio.run(scrape(
            "https://www.facebook.com/groups/123/posts/456",
            headless=False,
            debug=True,
            proxy_server="http://proxy:8080",
            proxy_username="user",
            proxy_password="secret",
        ))
    """
    return await Reaper().scrape(
        url,
        headless=headless,
        debug=debug,
        screenshot=screenshot,
        auto_scroll=auto_scroll,
        infinity_scroll=infinity_scroll,
        proxy_server=proxy_server,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
    )