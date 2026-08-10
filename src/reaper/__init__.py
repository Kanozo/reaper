"""
reaper/__init__.py
==================
API pública de la librería Reaper.

Exporta la función ``scrape()`` (wrapper conveniente), la clase ``Reaper``
(para múltiples scrapes con el mismo AccountManager) y todos los tipos
necesarios para el sistema de cuentas autenticadas.

Uso rápido sin cuentas (compatible con versiones anteriores)::

    import asyncio
    from reaper import scrape

    result = asyncio.run(scrape("https://www.facebook.com/reel/123456"))

Uso con cuentas autenticadas::

    import asyncio
    from reaper import scrape, AccountManager

    async def main():
        manager = AccountManager()
        await manager.add_account(
            platform="facebook",
            username="mi_usuario",
            cookies_file="exports/fb_cookies.json",
        )
        result = await scrape(
            "https://www.facebook.com/reel/123456",
            account_manager=manager,
        )
        return result

    asyncio.run(main())

Uso con la clase Reaper (reutiliza el AccountManager entre peticiones)::

    import asyncio
    from reaper import Reaper, AccountManager

    async def main():
        manager = AccountManager(cookie_refresh_threshold=50)
        reaper  = Reaper(account_manager=manager)

        post  = await reaper.scrape("https://www.instagram.com/p/abc123")
        reel  = await reaper.scrape("https://www.facebook.com/reel/456", debug=True)
        return post, reel

    asyncio.run(main())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# ── API de scraping ────────────────────────────────────────────────────────
from reaper.config import ScraperConfig
from reaper.core import Reaper, ScrapingError, UnsupportedPlatformError

# ── API de gestión de cuentas ──────────────────────────────────────────────
from reaper.auth import AccountManager
from reaper.auth.models import AccountActivity, AccountProfile, AccountStatus
from reaper.auth.storage.base import BaseAccountStorage, StorageError
from reaper.auth.storage.config import (
    StorageConfig,
    MongoConfig,
    PostgresConfig,
    build_storage,
    load_storage_config,
)
from reaper.auth.storage.local import LocalFileStorage
from reaper.auth.storage.mongodb import MongoStorage
from reaper.auth.storage.postgres import PostgresStorage

if TYPE_CHECKING:
    pass

__version__ = "0.2.3"

__all__ = [
    # Función de conveniencia y clase principal
    "scrape",
    "Reaper",
    "ScraperConfig",
    # Excepciones de scraping
    "ScrapingError",
    "UnsupportedPlatformError",
    # Gestión de cuentas
    "AccountManager",
    "AccountProfile",
    "AccountActivity",
    "AccountStatus",
    # Storage (para extensión con BD propia)
    "BaseAccountStorage",
    "LocalFileStorage",
    "PostgresStorage",
    "MongoStorage",
    "StorageError",
    # Configuración de storage
    "StorageConfig",
    "PostgresConfig",
    "MongoConfig",
    "build_storage",
    "load_storage_config",
]


async def scrape(
    url: str,
    *,
    account_manager: "AccountManager | None" = None,
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
    """Scrape una URL y devuelve los datos extraídos como diccionario.

    Función de conveniencia a nivel de módulo. Internamente crea una
    instancia de ``Reaper`` y delega en ``Reaper.scrape()``.

    Para múltiples scrapes con rotación de cuentas, es más eficiente
    usar la clase ``Reaper`` directamente, ya que reutiliza el
    ``AccountManager`` entre peticiones sin recrearlo.

    Args:
        url:             URL de Facebook o Instagram a scrapear. Obligatorio.
        account_manager: Gestor de cuentas para rotación autenticada.
                         ``None`` = modo anónimo (defecto).
        headless:        Ejecutar Firefox sin interfaz gráfica. Defecto: True.
        debug:           Logging detallado + artefactos. Defecto: False.
        screenshot:      Capturar PNG del contenedor principal. Defecto: False.
        auto_scroll:     Scroll automático por altura DOM. Defecto: True.
        infinity_scroll: Scroll continuo por tráfico de red. Defecto: False.
        proxy_server:    URL del proxy, p.ej. ``"http://ip:8080"``.
        proxy_username:  Usuario del proxy.
        proxy_password:  Contraseña del proxy.
        account:         Cuenta a forzar en esta sesión (account_id, username
                         o ID de usuario de la plataforma). Requiere
                         ``account_manager``. ``None`` = rotación automática.

    Returns:
        ``dict[str, Any]`` con los datos scrapeados.
        Siempre incluye: ``url``, ``platform``, ``status``.

    Raises:
        ValueError:               URL vacía o esquema inválido.
        UnsupportedPlatformError: URL no es Facebook ni Instagram.
        ScrapingError:            Error en runtime durante fetch o parseo.

    Examples::

        # Modo anónimo (comportamiento original)
        result = asyncio.run(scrape("https://www.instagram.com/p/abc123"))

        # Con cuenta autenticada
        manager = AccountManager()
        await manager.add_account("facebook", "usuario", cookies=[...])
        result = await scrape(
            "https://www.facebook.com/reel/123",
            account_manager=manager,
        )

        # Forzar una cuenta concreta por username
        result = await scrape(
            "https://www.facebook.com/reel/123",
            account_manager=manager,
            account="mi_usuario@gmail.com",
        )
    """
    return await Reaper(account_manager=account_manager).scrape(
        url,
        headless=headless,
        debug=debug,
        screenshot=screenshot,
        auto_scroll=auto_scroll,
        infinity_scroll=infinity_scroll,
        proxy_server=proxy_server,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
        account=account,
    )
