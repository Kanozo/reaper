"""
reaper/actions/actor.py
=======================
``SessionActor`` — lanzamiento de una sesión de navegador autenticada.

Es el puente entre la capa de acciones (posts, comentarios, likes) y
Camoufox. Su única responsabilidad es abrir una sesión de navegador realista
con las cookies de una cuenta autenticada, de modo que los flujos de UI de
Facebook (``reaper.actions.facebook``) puedan operar contra ella como un
usuario humano.

Para ello reutiliza los mismos patrones que ``ContentFetcher``:

- ``AsyncCamoufox(headless, proxy, humanize=True, os=random)``.
- Contexto con fingerprint coherente (``generate_fingerprint`` de
  ``reaper.anti_detection``): UA, viewport, locale y timezone del mismo perfil.
- Inyección de cookies de la cuenta antes de navegar.
- Stealth JS como init script del contexto.

Uso básico::

    from reaper.actions.actor import SessionActor

    actor = SessionActor(headless=True, proxy_server="http://proxy:8080")
    async with actor.session(cookies=cookies, url="https://www.facebook.com/") as handle:
        # handle.page  → Page de Playwright lista para operar
        # handle.context → BrowserContext (para cookies, etc.)
        await handle.page.fill("input[name='q']", "hola")
    # Tras el with, handle.updated_cookies contiene las cookies actualizadas.

Diseño para testabilidad:
    ``SessionActor`` admite inyectar un ``browser_factory`` (objeto con un
    método ``__aenter__``/``__aexit__`` que simula el arranque de Camoufox)
    para que los tests no dependan de un navegador real.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from reaper.anti_detection.fingerprint import generate_fingerprint
from reaper.network.interceptor import NetworkInterceptor
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# Callable que lanza un "navegador" (AsyncCamoufox real o un fake en tests).
BrowserFactory = Callable[..., AsyncIterator[Any]]


@dataclass
class SessionHandle:
    """Contexto de una sesión de navegador abierta por ``SessionActor``.

    Attributes:
        page:       Página principal de Playwright ya navegada a ``url``.
        context:    BrowserContext de Playwright (para cookies, páginas, etc.).
        browser:    Navegador Playwright subyacente.
        interceptor: Interceptor de red adjunto a la página (GraphQL/API de
            Facebook). Se rellena antes de ``goto``; ``None`` si no pudo
            adjuntarse (p. ej. en tests con fakes).
        updated_cookies: Cookies actualizadas tras cerrar la sesión. Se
            rellena automáticamente en ``__aexit__`` del contexto.
    """

    page: Any
    context: Any
    browser: Any
    interceptor: NetworkInterceptor | None = field(default=None)
    updated_cookies: list[dict[str, Any]] | None = field(default=None)


class SessionActor:
    """Lanza sesiones de navegador autenticadas con Camoufox.

    Args:
        headless: Modo sin ventana visible. Defecto: ``True``.
        proxy_server: URL del proxy (``"http://ip:port"``, ``"socks5://..."``).
        proxy_username: Usuario para autenticación del proxy.
        proxy_password: Contraseña para autenticación del proxy.
        browser_type: Perfil de fingerprint para anti-detección.
            ``"firefox"`` (mejor cobertura en Meta) o ``"chromium"``.
            Defecto: ``"firefox"``.
        viewport: Viewport fijo (dict ``{"width": ..., "height": ...}``).
            Si es ``None``, se usa el viewport del fingerprint generado.
        locale: Locale BCP-47 (p.ej. ``"en-US"``). Si es ``None``, el del
            fingerprint.
        timezone_id: IANA timezone (p.ej. ``"America/New_York"``). Si es
            ``None``, la del fingerprint.
    """

    def __init__(
        self,
        headless: bool = True,
        proxy_server: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
        browser_type: str = "firefox",
        viewport: dict[str, int] | None = None,
        locale: str | None = None,
        timezone_id: str | None = None,
        debug: bool = False,
    ) -> None:
        self.headless = headless
        self.browser_type = browser_type
        self.proxy_server = proxy_server
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
        self.viewport = viewport
        self.locale = locale
        self.timezone_id = timezone_id
        self.debug = debug

        # Punto de inyección para tests: factory que devuelve un navegador.
        self._browser_factory: BrowserFactory | None = None
        self._fingerprint: Any | None = None

        logger.debug(
            "SessionActor inicializado | headless=%s | browser_type=%s | proxy=%s",
            headless,
            browser_type,
            proxy_server or "ninguno",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────────────────

    def use_browser_factory(self, factory: BrowserFactory) -> None:
        """Inyecta un factory de navegador alternativo (para tests/mocks)."""
        self._browser_factory = factory

    def force_fingerprint(self, fingerprint: Any) -> None:
        """Fuerza un fingerprint concreto (para tests deterministas)."""
        self._fingerprint = fingerprint

    @asynccontextmanager
    async def session(
        self,
        *,
        cookies: list[dict[str, Any]] | None = None,
        url: str | None = None,
        traffic_debug: bool = False,
    ) -> AsyncIterator[SessionHandle]:
        """Abre una sesión de navegador autenticada y la cierra al salir.

        Args:
            cookies: Cookies en formato Playwright de la cuenta autenticada.
            url: URL a navegar al abrir la sesión. Opcional.
            traffic_debug: Fuerza el almacenamiento del cuerpo crudo de las
                respuestas capturadas aunque ``debug`` global esté off
                (necesario para confirmar acciones por tráfico GraphQL).

        Lanza Camoufox (o el factory inyectado en tests), crea un contexto
        con fingerprint coherente, inyecta las cookies y navega a ``url`` si
        se indica. El cuerpo del ``with`` recibe un ``SessionHandle``.

        Al salir del contexto, ``handle.updated_cookies`` contiene las
        cookies actualizadas por el sitio (para persistirlas en la cuenta).

        Args:
            cookies: Cookies en formato Playwright de la cuenta autenticada.
            url: URL a navegar al abrir la sesión. Opcional.

        Yields:
            ``SessionHandle`` con la página y el contexto listos para operar.

        Raises:
            ImportError: Si ``camoufox`` no está instalado.
            Exception: Cualquier error de navegación/lanzamiento.
        """
        proxy_config: dict[str, str] | None = None
        if self.proxy_server:
            proxy_config = {"server": self.proxy_server}
            if self.proxy_username:
                proxy_config["username"] = self.proxy_username
            if self.proxy_password:
                proxy_config["password"] = self.proxy_password

        browser = await self._launch(proxy_config)
        try:
            context = await self._build_context(browser)
            page = await context.new_page()

            # Intercepta el tráfico GraphQL/API de Facebook ANTES de navegar:
            # la confirmación de acciones (p.ej. el post creado) puede leer la
            # respuesta real del servidor en lugar del DOM.
            interceptor: NetworkInterceptor | None = None
            try:
                candidate = NetworkInterceptor(debug=self.debug or traffic_debug)
                await candidate.attach(page)
                interceptor = candidate
            except Exception as _exc:  # pragma: no cover
                logger.debug("No se pudo adjuntar el interceptor: %s", _exc)
                interceptor = None

            if cookies:
                await context.add_cookies(cookies)

            if url:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )

            handle = SessionHandle(
                page=page,
                context=context,
                browser=browser,
                interceptor=interceptor,
            )
            try:
                yield handle
            finally:
                if self.debug:
                    await self._save_debug_artifacts(handle)
                try:
                    handle.updated_cookies = await context.cookies()
                except Exception as _exc:  # pragma: no cover
                    logger.debug("No se pudieron leer cookies post-sesión: %s", _exc)
                    handle.updated_cookies = None
        finally:
            await self._close(browser)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers de lanzamiento
    # ─────────────────────────────────────────────────────────────────────────

    async def _save_debug_artifacts(self, handle: SessionHandle) -> None:
        """Guarda DOM final y tráfico de la sesión en ``data/debug_artifacts``.

        Solo se invoca con ``debug=True``. Escribe:
        ``page.html`` (DOM tras la acción), ``traffic.json`` (índice ligero) y
        las respuestas GraphQL individuales para poder inspeccionar qué devolvió
        Facebook tras publicar (especialmente el permalink y el id del post).
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir = Path("data/debug_artifacts") / f"action_{ts}"
            session_dir.mkdir(parents=True, exist_ok=True)

            try:
                content = await handle.page.content()
                (session_dir / "page.html").write_text(content, encoding="utf-8")
            except Exception as exc:
                logger.debug("No se pudo capturar el DOM de debug: %s", exc)

            interceptor = handle.interceptor
            if interceptor is not None:
                try:
                    traffic = interceptor.get_traffic()
                    traffic_path = session_dir / "traffic.json"
                    index: dict[str, Any] = {
                        "version": "3.0",
                        "captured_at": datetime.now().isoformat(),
                        "summary": traffic.summary(),
                        "graphql_responses": [
                            {
                                "url": resp.url,
                                "status": resp.status,
                                "operation": resp.operation,
                                "body": resp.body,
                            }
                            for resp in traffic.graphql_responses
                        ],
                    }
                    traffic_path.write_text(
                        json.dumps(index, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8",
                    )
                except Exception as exc:
                    logger.debug("No se pudo volcar el tráfico de debug: %s", exc)

            logger.debug("Artefactos de debug guardados en: %s", session_dir)
        except Exception as exc:
            logger.debug("Error guardando artefactos de debug: %s", exc)

    async def _launch(self, proxy_config: dict[str, str] | None) -> Any:
        """Lanza el navegador (Camoufox real o el factory inyectado).

        Returns:
            Objeto browser con ``new_context()`` y ``close()``.
        """
        if self._browser_factory is not None:
            return await self._browser_factory(proxy_config)

        from camoufox.async_api import AsyncCamoufox

        # Anti-detección nativa de Camoufox + fingerprint de la librería.
        return await AsyncCamoufox(
            headless=self.headless,
            proxy=proxy_config,
            humanize=True,  # Simula comportamiento humano
            os=random.choice(["windows", "macos", "linux"]),  # OS aleatorio
        ).start()

    async def _build_context(self, browser: Any) -> Any:
        """Crea un BrowserContext con fingerprint coherente y stealth JS.

        Reutiliza ``generate_fingerprint`` de ``reaper.anti_detection`` para
        que UA, viewport, locale y timezone provengan del mismo perfil, y
        añade el stealth JS como init script (detección de bots).
        """
        fp = self._fingerprint or generate_fingerprint(self.browser_type)

        if self.viewport is None:
            self.viewport = {"width": fp.viewport.width, "height": fp.viewport.height}
        locale = self.locale or fp.locale
        timezone_id = self.timezone_id or fp.timezone_id

        context = await browser.new_context(
            viewport=self.viewport,
            locale=locale,
            timezone_id=timezone_id,
            user_agent=fp.user_agent,
        )

        # Stealth JS: parchea navigator, WebGL, plugins, WebRTC, etc.
        try:
            await context.add_init_script(fp.stealth_js)
        except Exception as _exc:  # pragma: no cover
            logger.debug("No se pudo inyectar stealth JS: %s", _exc)

        return context

    async def _close(self, browser: Any) -> None:
        """Cierra el navegador liberando recursos."""
        close = getattr(browser, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result
