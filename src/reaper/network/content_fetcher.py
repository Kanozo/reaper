import asyncio
import json
from reaper.utils.logger import get_logger
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from reaper.network.interceptor import (
    CapturedRequest,
    CapturedResponse,
    CapturedTraffic,
    FetchResult,
    GraphQLMeta,
    NetworkInterceptor,
)

logger = get_logger(__name__)

# ============================================================================
# SECCIÓN 1: CONFIGURACIÓN DEL NAVEGADOR
# ============================================================================


class BrowserConfig:
    """
    Parámetros de configuración del navegador y comportamiento de scroll.

    Centraliza los ajustes en un único lugar sin tocar la lógica de navegación.
    Para personalizar, instancia y sobreescribe atributos de instancia::

        cfg = BrowserConfig()
        cfg.PAGE_LOAD_WAIT = 8.0
        cfg.MAX_SCROLL_ITERATIONS = 30
        fetcher = ContentFetcher(url, config=cfg)
    """

    # ---- Tiempos de espera ----

    # Pausa tras la carga inicial (segundos). Da tiempo al JS para ejecutarse.
    PAGE_LOAD_WAIT: float = 5.0

    # Pausa entre iteraciones de scroll (segundos).
    SCROLL_WAIT_TIME: float = 1.5

    # ---- Scroll ----

    # Píxeles desplazados por iteración (mouse.wheel).
    SCROLL_DELTA: int = 1_000

    # Iteraciones máximas (auto y infinity). Previene bucles infinitos.
    MAX_SCROLL_ITERATIONS: int = 10

    # Iteraciones consecutivas sin cambio de scrollHeight → detener auto_scroll.
    NO_CHANGE_THRESHOLD: int = 2

    # Iteraciones consecutivas sin actividad de red → detener infinity_scroll.
    INFINITY_NO_REQUESTS_THRESHOLD: int = 5

    # ---- Navegador ----

    # User-Agent desktop moderno. Reduce detección como bot.
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Timeout de navegación en milisegundos.
    NAVIGATION_TIMEOUT_MS: int = 100_000

    # Viewport Full HD (se instancia en __init__ para evitar mutabilidad compartida).
    VIEWPORT: dict[str, int] = None  # type: ignore[assignment]

    LOCALE: str = "en-US"
    TIMEZONE_ID: str = "America/New_York"

    # ---- Proxy ----
    PROXY_SERVER: str | None = None    # "http://proxy.example.com:8080" | "socks5://..."
    PROXY_USERNAME: str | None = None
    PROXY_PASSWORD: str | None = None

    # ---- Captura de body en responses ----

    # Timeout (segundos) para leer el body de una respuesta.
    # Evita bloquear en respuestas lentas o de streaming.
    RESPONSE_BODY_TIMEOUT: float = 25.0

    # ---- Artefactos de debug ----

    # Directorio raíz para screenshots, HTML y tráfico de red.
    DEBUG_OUTPUT_DIR: str = "data/debug_artifacts"

    # Si True, guarda el tráfico de red completo en debug_artifacts.
    # False → solo screenshots y HTML (sin volcado de red).
    DEBUG_SAVE_TRAFFIC: bool = True

    def __init__(self) -> None:
        # dict por instancia para evitar mutación compartida entre instancias.
        self.VIEWPORT: dict[str, int] = {"width": 1920, "height": 1080}


# ============================================================================
# SECCIÓN 2: HELPERS DE SERIALIZACIÓN DE TRÁFICO
# ============================================================================


def _serialize_graphql_meta(meta: GraphQLMeta | None) -> dict[str, Any] | None:
    """Convierte un GraphQLMeta a dict serializable. Retorna None si meta es None."""
    if meta is None:
        return None
    return {
        "friendly_name": meta.friendly_name,
        "doc_id": meta.doc_id,
        "variables": meta.variables,
        "caller_class": meta.caller_class,
    }


def _serialize_request(req: CapturedRequest) -> dict[str, Any]:
    """Convierte un CapturedRequest a dict JSON-serializable."""
    return {
        "url": req.url,
        "method": req.method,
        "category": req.category,
        "timestamp": req.timestamp.isoformat() if req.timestamp else None,
        "post_data_raw": req.post_data_raw,
        "post_data": req.post_data,
        "headers": req.headers,
        "graphql_meta": _serialize_graphql_meta(req.graphql_meta),
    }


def _serialize_response(resp: CapturedResponse) -> dict[str, Any]:
    """Convierte un CapturedResponse a dict JSON-serializable."""
    return {
        "url": resp.url,
        "status": resp.status,
        "category": resp.category,
        "timestamp": resp.timestamp.isoformat() if resp.timestamp else None,
        "body": resp.body,
        "body_raw": resp.body_raw,
        "headers": resp.headers,
    }


# ============================================================================
# SECCIÓN 3: ESCRITURA DE FICHEROS DE TRÁFICO
# ============================================================================


def _write_request_files(
    requests: list[CapturedRequest],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """
    Guarda cada request como un fichero JSON individual y retorna el índice.

    Siempre guarda todos los requests (sin umbral de tamaño). El payload
    completo va al fichero; el índice contiene solo metadatos + referencia.

    Args:
        requests:   Lista de CapturedRequest a persistir.
        output_dir: Directorio destino (se crea si no existe).

    Returns:
        Lista de dicts con metadatos y referencia al fichero (``__FILE__``).
    """
    if not requests:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    index_entries: list[dict[str, Any]] = []

    for idx, req in enumerate(requests):
        serialized = _serialize_request(req)

        friendly = (
            req.graphql_meta.friendly_name[:40]
            if req.graphql_meta and req.graphql_meta.friendly_name
            else ""
        )
        fname = f"{idx:03d}_{friendly}.json" if friendly else f"{idx:03d}.json"
        fpath = output_dir / fname

        fpath.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        index_entries.append({
            "file": fname,
            "url": req.url,
            "method": req.method,
            "timestamp": serialized["timestamp"],
            "graphql_meta": serialized["graphql_meta"],
        })

    return index_entries


def _write_response_files(
    responses: list[CapturedResponse],
    output_dir: Path,
    requests: list[CapturedRequest] | None = None,
) -> list[dict[str, Any]]:
    """
    Guarda cada response como un fichero JSON individual y retorna el índice.

    Siempre guarda todos los responses (sin umbral de tamaño). Si se pasan
    los requests paralelos, el nombre del fichero incluye el ``friendly_name``
    de la operación para facilitar la navegación manual.

    Args:
        responses:  Lista de CapturedResponse a persistir.
        output_dir: Directorio destino (se crea si no existe).
        requests:   Lista de requests paralela (para extraer operation names).

    Returns:
        Lista de dicts con metadatos y referencia ``__FILE__`` al fichero.
        Compatible con ``deserialize_traffic()`` para carga offline.
    """
    if not responses:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    index_entries: list[dict[str, Any]] = []

    for idx, resp in enumerate(responses):
        operation_name = ""
        if requests and idx < len(requests):
            req = requests[idx]
            if req.graphql_meta and req.graphql_meta.friendly_name:
                operation_name = req.graphql_meta.friendly_name[:40]

        fname = f"{idx:03d}_{operation_name}.json" if operation_name else f"{idx:03d}.json"
        fpath = output_dir / fname

        fpath.write_text(
            json.dumps(resp.body or {}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        index_entries.append({
            "file": fname,
            "url": resp.url,
            "status": resp.status,
            "category": resp.category,
            "timestamp": resp.timestamp.isoformat() if resp.timestamp else None,
            # Referencia __FILE__ → deserialize_traffic() carga el fichero al reconstruir
            "body": f"__FILE__{fname}",
            "body_raw": None,
            "headers": resp.headers,
        })

    return index_entries


# ============================================================================
# SECCIÓN 4: FETCHER DE CONTENIDO
# ============================================================================


class ContentFetcher:
    """
    Obtiene HTML completamente renderizado de una URL con Playwright (Firefox).

    Orquesta la navegación, la interceptación de red y las técnicas de scroll.
    Actúa como facade sobre Playwright: el código consumidor solo interactúa
    con ``fetch()`` y recibe un ``FetchResult`` completamente tipado.

    Responsabilidad única:
        Navegar a una URL, activar la captura de tráfico de red, esperar la
        carga completa del contenido dinámico y devolver HTML + tráfico.
        NO parsea, NO interpreta, NO extrae datos de la página.

    Modos de scroll:
        - ``auto_scroll=True``:     scroll midiendo cambios de scrollHeight DOM.
                                    Recomendado para posts individuales con comentarios.
        - ``infinity_scroll=True``: scroll monitorizando actividad de red.
                                    Recomendado para feeds y búsquedas largas.
        - Ambos False:              sin scroll (posts simples, Instagram).

    Uso básico::

        fetcher = ContentFetcher("https://www.facebook.com/reel/XXXXXX")
        result = await fetcher.fetch(auto_scroll=True)
        if result.success:
            html = result.html_content
            traffic = result.traffic
    """

    def __init__(
        self,
        url: str,
        headless: bool = True,
        debug: bool = False,
        config: BrowserConfig | None = None,
    ) -> None:
        """
        Args:
            url:      URL completa de la página a obtener.
            headless: Si True, lanza el navegador sin interfaz gráfica.
                      Recomendado True en producción y daemons.
            debug:    Si True, activa logs detallados, guarda screenshots,
                      HTML y tráfico de red en ``BrowserConfig.DEBUG_OUTPUT_DIR``.
            config:   Instancia de BrowserConfig con parámetros personalizados.
                      Si None, usa la configuración por defecto.

        Raises:
            ValueError: Si url está vacía o no es un string.
        """
        if not url or not isinstance(url, str):
            raise ValueError("La URL debe ser un string no vacío.")

        self.url = url.strip()
        self.headless = headless
        self.debug = debug
        self.cfg = config or BrowserConfig()

        self._html_content: str | None = None
        self._final_url: str | None = None

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    async def fetch(
        self,
        screenshot: bool = False,
        auto_scroll: bool = False,
        infinity_scroll: bool = False,
        proxy_server: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
    ) -> FetchResult:
        """
        Ejecuta la navegación completa y retorna HTML + tráfico de red.

        Flujo interno:
            1. Crear ``NetworkInterceptor`` y ``FetchResult`` base.
            2. Lanzar Firefox con configuración anti-detección.
            3. Crear contexto (viewport, user-agent, locale, proxy opcional).
            4. **Adjuntar el interceptor ANTES de navegar** (captura desde inicio).
            5. Navegar a la URL y esperar ``networkidle``.
            6. (Opcional) Capturar screenshot del contenedor principal.
            7. (Opcional) Ejecutar scroll (auto o infinity).
            8. Capturar el HTML final (``page.content()``).
            9. (Debug) Guardar artefactos en disco.
            10. Cerrar navegador y retornar FetchResult.

        Args:
            screenshot:      Captura PNG del contenedor ``div[role='dialog']``.
            auto_scroll:     Scroll midiendo cambios de altura DOM.
            infinity_scroll: Scroll monitorizando tráfico de red.
                             Tiene prioridad sobre ``auto_scroll`` si ambos True.
            proxy_server:    URL del proxy (ej: "http://ip:port").
            proxy_username:  Usuario para autenticación del proxy.
            proxy_password:  Contraseña para autenticación del proxy.

        Returns:
            FetchResult con HTML renderizado, tráfico capturado y metadatos.
            Si falla, ``success=False`` y ``error`` describe el problema.
        """
        interceptor = NetworkInterceptor(
            debug=self.debug,
            response_body_timeout=self.cfg.RESPONSE_BODY_TIMEOUT,
        )

        result = FetchResult(original_url=self.url)

        proxy_config: dict[str, str] | None = None
        if proxy_server:
            proxy_config = {"server": proxy_server}
            if proxy_username:
                proxy_config["username"] = proxy_username
            if proxy_password:
                proxy_config["password"] = proxy_password
            if self.debug:
                logger.debug("Configurando proxy: %s", proxy_server)

        try:
            async with async_playwright() as pw:
                browser = await self._launch_browser(pw)
                context = await self._create_context(browser, proxy_config=proxy_config)
                page = await context.new_page()

                # ADJUNTAR ANTES DE NAVEGAR: garantiza que no se pierden
                # las peticiones de la carga inicial de la página.
                interceptor.attach(page)

                if not await self._navigate(page):
                    await browser.close()
                    result.error = "Falló la navegación a la URL."
                    result.traffic = interceptor.get_traffic()
                    return result

                if screenshot:
                    await self._take_container_screenshot(page)

                if infinity_scroll:
                    await self._infinity_scroll(page, interceptor)
                elif auto_scroll:
                    await self._auto_scroll(page)

                self._html_content = await page.content()
                self._final_url = page.url

                if self.debug:
                    session_dir = await self._save_debug_artifacts(page, interceptor, result)
                    result.debug_session_dir = session_dir
                    interceptor.print_summary()

                await browser.close()

            result.html_content = self._html_content
            result.final_url = self._final_url
            result.traffic = interceptor.get_traffic()
            result.success = True
            logger.info("Contenido obtenido. Tráfico: %s", result.traffic.summary())

        except Exception as exc:
            result.error = str(exc)
            result.traffic = interceptor.get_traffic()
            logger.error("Error al obtener el contenido: %s", exc)
            if self.debug:
                traceback.print_exc()

        return result

    # ------------------------------------------------------------------
    # Configuración del navegador
    # ------------------------------------------------------------------

    async def _launch_browser(self, pw) -> Browser:
        """
        Lanza Firefox con flags para reducir la detección como bot.

        Firefox ofrece mejor compatibilidad con Facebook respecto a Chromium
        en términos de fingerprinting y anti-bot.

        Args:
            pw: Instancia activa de async_playwright.

        Returns:
            Browser: Instancia del navegador lanzado.
        """
        return await pw.firefox.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

    async def _create_context(
        self,
        browser: Browser,
        proxy_config: dict[str, str] | None = None,
    ) -> BrowserContext:
        """
        Crea un contexto de navegador que simula un usuario desktop real.

        El viewport 1920×1080, el locale en-US y la zona horaria aumentan
        la verosimilitud del perfil y reducen el riesgo de detección por
        fingerprinting.

        Args:
            browser:      Instancia del navegador ya lanzado.
            proxy_config: dict con ``server``, ``username``, ``password`` (opcionales).

        Returns:
            BrowserContext configurado y listo para abrir páginas.
        """
        context_args: dict[str, Any] = {
            "viewport": self.cfg.VIEWPORT,
            "user_agent": self.cfg.USER_AGENT,
            "locale": self.cfg.LOCALE,
            "timezone_id": self.cfg.TIMEZONE_ID,
        }

        if proxy_config:
            context_args["proxy"] = proxy_config

        return await browser.new_context(**context_args)

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    async def _navigate(self, page: Page) -> bool:
        """
        Navega a ``self.url`` y espera a que la red esté inactiva.

        Usa ``wait_until="networkidle"`` para asegurar que el contenido
        dinámico (JS, API calls) haya terminado de cargarse. Añade una
        pausa adicional configurable vía ``BrowserConfig.PAGE_LOAD_WAIT``.

        Args:
            page: Página de Playwright activa.

        Returns:
            True si la navegación fue exitosa, False si ocurrió un error.
        """
        try:
            logger.info("Navegando a: %s", self.url)
            await page.goto(
                self.url,
                wait_until="networkidle",
                timeout=self.cfg.NAVIGATION_TIMEOUT_MS,
            )
            logger.info("URL final: %s", page.url)
            logger.debug("Pausa de carga: %.1fs", self.cfg.PAGE_LOAD_WAIT)
            await asyncio.sleep(self.cfg.PAGE_LOAD_WAIT)
            return True

        except Exception as exc:
            logger.error("Error en navegación: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Screenshot del contenedor
    # ------------------------------------------------------------------

    async def _take_container_screenshot(self, page: Page) -> None:
        """
        Localiza ``div[role='dialog']`` y captura un screenshot en disco.

        Si el contenedor no es visible o no existe, registra una advertencia
        sin lanzar excepción (no es un error crítico).

        Args:
            page: Página de Playwright activa.
        """
        try:
            logger.debug("Capturando screenshot del contenedor...")
            container = page.locator('div[role="dialog"]')
            await container.wait_for(state="visible", timeout=10_000)
            await container.screenshot(path="post_container.png")
            logger.debug("Screenshot guardado: post_container.png")
        except Exception as exc:
            logger.warning("No se pudo capturar screenshot del contenedor: %s", exc)

    # ------------------------------------------------------------------
    # Scroll automático (basado en cambios de altura DOM)
    # ------------------------------------------------------------------

    async def _auto_scroll(self, page: Page) -> None:
        """
        Scroll automático midiendo cambios de ``scrollHeight`` del contenedor.

        Estrategia:
            1. Localiza ``div[role='dialog']`` y obtiene su bounding box.
            2. Posiciona el cursor en el centro superior del contenedor.
            3. En cada iteración hace ``mouse.wheel(0, SCROLL_DELTA)`` y compara
               ``scrollHeight`` con la iteración anterior.
            4. Si ``scrollHeight`` no cambia por ``NO_CHANGE_THRESHOLD``
               iteraciones consecutivas, detiene el scroll.

        Se usa ``mouse.wheel()`` en vez de ``page.evaluate()`` para simular
        un evento de hardware real, más difícil de detectar como bot.

        Args:
            page: Página de Playwright activa (con interceptor ya adjunto).
        """
        try:
            container = page.locator('div[role="dialog"]')
            bbox = await container.bounding_box()

            if not bbox:
                logger.warning("Contenedor no encontrado; auto-scroll omitido.")
                return

            center_x = bbox["x"] + bbox["width"] / 2
            start_y = bbox["y"] + 100
            await page.mouse.move(center_x, start_y)
            logger.debug("Cursor en X=%.0f, Y=%.0f", center_x, start_y)

            last_height = 0
            no_change_count = 0
            iteration = 0

            logger.info("Iniciando auto-scroll (modo altura DOM)...")

            while iteration < self.cfg.MAX_SCROLL_ITERATIONS:
                await page.mouse.wheel(0, self.cfg.SCROLL_DELTA)
                await asyncio.sleep(self.cfg.SCROLL_WAIT_TIME)

                current_height = await container.evaluate("el => el.scrollHeight")

                if current_height == last_height:
                    no_change_count += 1
                    logger.debug(
                        "Sin cambio (%d/%d)",
                        no_change_count,
                        self.cfg.NO_CHANGE_THRESHOLD,
                    )
                    if no_change_count >= self.cfg.NO_CHANGE_THRESHOLD:
                        logger.info("Auto-scroll finalizado: sin más contenido.")
                        break
                else:
                    no_change_count = 0
                    last_height = current_height
                    logger.debug("scrollHeight: %dpx", current_height)

                iteration += 1

            await asyncio.sleep(2)

        except Exception as exc:
            logger.warning("Error en auto-scroll: %s", exc)

    # ------------------------------------------------------------------
    # Scroll infinito (basado en tráfico de red vía interceptor)
    # ------------------------------------------------------------------

    async def _infinity_scroll(
        self,
        page: Page,
        interceptor: NetworkInterceptor,
    ) -> None:
        """
        Scroll continuo monitorizando la actividad de red para detectar carga.

        Usa el ``NetworkInterceptor`` compartido (ya adjunto) para detectar
        si el scroll está generando nuevas peticiones de red.
        **No configura sus propios listeners.**

        Estrategia:
            1. Captura un snapshot de actividad del interceptor (baseline).
            2. En cada iteración: scroll → espera → compara actividad con baseline.
            3. Sin actividad nueva durante ``INFINITY_NO_REQUESTS_THRESHOLD``
               iteraciones → scroll agresivo de refuerzo.
            4. Si el refuerzo tampoco genera actividad → detener.

        Args:
            page:        Página de Playwright activa.
            interceptor: NetworkInterceptor ya adjunto. Se usa para monitorizar
                         actividad sin configurar nuevos listeners.
        """
        try:
            viewport = page.viewport_size or {"width": 1920, "height": 1080}
            cx = viewport["width"] / 2
            cy = viewport["height"] / 2
            await page.mouse.move(cx, cy)
            logger.debug("Cursor en centro de página: X=%.0f, Y=%.0f", cx, cy)

            logger.info("Iniciando infinity-scroll (modo tráfico de red)...")

            no_activity_count = 0
            iteration = 0
            baseline = interceptor.snapshot_activity()

            while (
                iteration < self.cfg.MAX_SCROLL_ITERATIONS
                and no_activity_count < self.cfg.INFINITY_NO_REQUESTS_THRESHOLD
            ):
                iteration += 1
                await page.mouse.wheel(0, self.cfg.SCROLL_DELTA)
                await asyncio.sleep(self.cfg.SCROLL_WAIT_TIME)

                new_events = interceptor.new_activity_since(baseline)
                logger.debug(
                    "Iter %3d: +%d eventos de red (total=%d)",
                    iteration,
                    new_events,
                    interceptor.total_activity,
                )

                if new_events > 0:
                    no_activity_count = 0
                    await asyncio.sleep(3.5)
                else:
                    no_activity_count += 1

                    if no_activity_count == 2:
                        logger.debug("Scroll agresivo de refuerzo...")
                        await page.mouse.wheel(0, 1_500)
                        await asyncio.sleep(5.5)
                        if interceptor.new_activity_since(baseline) > 0:
                            no_activity_count = 0

                await asyncio.sleep(5.0)
                baseline = interceptor.snapshot_activity()

            await asyncio.sleep(3)
            logger.info(
                "Infinity-scroll finalizado. Tráfico total: %s",
                interceptor.get_traffic().summary(),
            )

        except Exception as exc:
            logger.warning("Error en infinity-scroll: %s", exc)

    # ------------------------------------------------------------------
    # Artefactos de debug
    # ------------------------------------------------------------------

    async def _save_debug_artifacts(
        self,
        page: Page,
        interceptor: NetworkInterceptor,
        result: FetchResult,
    ) -> Path | None:
        """
        Guarda en disco todos los artefactos de una sesión de debug.

        Estructura generada::

            {DEBUG_OUTPUT_DIR}/
              {slug}_{ts}/
                meta.json                  ← metadatos + índice de artefactos
                screenshot_full.png        ← captura de la página completa
                page.html                  ← HTML renderizado
                traffic.json               ← índice ligero (refs __FILE__)
                graphql_requests/          ← requests GraphQL individuales
                  000_OperationName.json
                graphql_responses/         ← responses GraphQL individuales
                  000_OperationName.json
                api_requests/              ← requests API individuales
                  000.json
                api_responses/             ← responses API individuales
                  000.json

        El directorio puede recargarse offline con :func:`load_debug_session`.

        Args:
            page:        Página Playwright activa.
            interceptor: NetworkInterceptor con el tráfico ya capturado.
            result:      FetchResult parcialmente construido (para metadatos).

        Returns:
            Path al directorio de sesión creado, o None si hubo un error.
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug = re.sub(r"[^\w]+", "_", self.url.split("//")[-1])[:50]
            session_dir = Path(self.cfg.DEBUG_OUTPUT_DIR) / f"{slug}_{ts}"
            session_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Sesión de debug: %s", session_dir)

            traffic = interceptor.get_traffic()

            # ── 1. Screenshot ─────────────────────────────────────────────
            screenshot_path = session_dir / "screenshot_full.png"
            try:
                await page.screenshot(path=str(screenshot_path), full_page=False)
                logger.debug("Screenshot: %s", screenshot_path.name)
            except Exception as exc:
                logger.warning("No se pudo capturar screenshot: %s", exc)

            # ── 2. HTML renderizado ───────────────────────────────────────
            if self._html_content:
                html_path = session_dir / "page.html"
                html_path.write_text(self._html_content, encoding="utf-8")
                logger.debug(
                    "HTML: %s (%d KB)",
                    html_path.name,
                    len(self._html_content) // 1024,
                )

            # ── 3. Tráfico de red ─────────────────────────────────────────
            gql_req_index: list[dict[str, Any]] = []
            gql_res_index: list[dict[str, Any]] = []
            api_req_index: list[dict[str, Any]] = []
            api_res_index: list[dict[str, Any]] = []

            if self.cfg.DEBUG_SAVE_TRAFFIC:
                # Requests: siempre como ficheros individuales (antes no existía este directorio)
                gql_req_index = _write_request_files(
                    requests=traffic.graphql_requests,
                    output_dir=session_dir / "graphql_requests",
                )
                api_req_index = _write_request_files(
                    requests=traffic.api_requests,
                    output_dir=session_dir / "api_requests",
                )

                # Responses: siempre como ficheros individuales (antes solo si > 200KB)
                gql_res_index = _write_response_files(
                    responses=traffic.graphql_responses,
                    output_dir=session_dir / "graphql_responses",
                    requests=traffic.graphql_requests,  # para operation name en nombres de fichero
                )
                api_res_index = _write_response_files(
                    responses=traffic.api_responses,
                    output_dir=session_dir / "api_responses",
                )

                # traffic.json: índice ligero con referencias __FILE__ (compatible con
                # deserialize_traffic para carga offline)
                traffic_index: dict[str, Any] = {
                    "version": "3.0",
                    "captured_at": datetime.now().isoformat(),
                    "summary": traffic.summary(),
                    "graphql_requests": gql_req_index,
                    "graphql_responses": gql_res_index,
                    "api_requests": api_req_index,
                    "api_responses": api_res_index,
                }
                traffic_path = session_dir / "traffic.json"
                traffic_path.write_text(
                    json.dumps(traffic_index, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                logger.debug(
                    "Tráfico indexado: GQL %d/%d req/res | API %d/%d req/res",
                    len(gql_req_index), len(gql_res_index),
                    len(api_req_index), len(api_res_index),
                )

            # ── 4. meta.json ──────────────────────────────────────────────
            # SIEMPRE se escribe, independientemente de DEBUG_SAVE_TRAFFIC.
            # Antes estaba dentro del bloque DEBUG_SAVE_TRAFFIC, lo que hacía
            # fallar load_debug_session() cuando DEBUG_SAVE_TRAFFIC=False.
            meta: dict[str, Any] = {
                "version": "3.0",
                "original_url": result.original_url,
                "final_url": self._final_url,
                "fetched_at": result.fetched_at.isoformat(),
                "success": result.success,
                "error": result.error,
                "authenticated": result.authenticated,
                "account_id": result.account_id,
                "traffic_summary": traffic.summary(),
                "artifacts": {
                    "screenshot": "screenshot_full.png",
                    "html": "page.html",
                    "traffic": "traffic.json" if self.cfg.DEBUG_SAVE_TRAFFIC else None,
                    "graphql_requests_dir": "graphql_requests/",
                    "graphql_responses_dir": "graphql_responses/",
                    "api_requests_dir": "api_requests/",
                    "api_responses_dir": "api_responses/",
                },
            }
            meta_path = session_dir / "meta.json"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logger.info("Sesión debug guardada en: %s", session_dir)
            return session_dir

        except Exception as exc:
            logger.error("Error guardando artefactos debug: %s", exc)
            if self.debug:
                traceback.print_exc()
            return None


# ============================================================================
# SECCIÓN 5: SERIALIZACIÓN Y ANÁLISIS OFFLINE
# ============================================================================


def serialize_traffic(traffic: CapturedTraffic) -> dict[str, Any]:
    """
    Convierte un ``CapturedTraffic`` a un dict JSON-serializable.

    Serializa todos los dataclasses anidados convirtiendo los ``datetime``
    a cadenas ISO-8601. Compatible con ``deserialize_traffic()``.

    Args:
        traffic: Objeto CapturedTraffic retornado por ``NetworkInterceptor.get_traffic()``.

    Returns:
        dict serializable con la siguiente estructura::

            {
                "version":           "3.0",
                "captured_at":       "<ISO-8601>",
                "summary":           "GraphQL: Xreq/Xres | ...",
                "graphql_requests":  [{CapturedRequest serializado} ...],
                "graphql_responses": [{CapturedResponse serializado} ...],
                "api_requests":      [...],
                "api_responses":     [...]
            }

    Example::

        traffic = interceptor.get_traffic()
        data = serialize_traffic(traffic)
        Path("traffic.json").write_text(json.dumps(data, indent=2))
    """
    return {
        "version": "3.0",
        "captured_at": datetime.now().isoformat(),
        "summary": traffic.summary(),
        "graphql_requests":  [_serialize_request(r) for r in traffic.graphql_requests],
        "graphql_responses": [_serialize_response(r) for r in traffic.graphql_responses],
        "api_requests":      [_serialize_request(r) for r in traffic.api_requests],
        "api_responses":     [_serialize_response(r) for r in traffic.api_responses],
    }


def deserialize_traffic(
    data: dict[str, Any],
    session_dir: str | Path | None = None,
) -> CapturedTraffic:
    """
    Reconstruye un ``CapturedTraffic`` desde un dict serializado con
    :func:`serialize_traffic`.

    Si ``session_dir`` se provee, resuelve los bodies de respuesta que fueron
    guardados como ficheros separados (marcados con el prefijo ``__FILE__``).

    Args:
        data:        dict cargado desde ``traffic.json``.
        session_dir: Directorio raíz de la sesión de debug (para resolver
                     referencias ``__FILE__``). Puede ser str o Path.

    Returns:
        CapturedTraffic completamente reconstruido con todos sus campos tipados.

    Example::

        data = json.loads(Path("debug_artifacts/session/traffic.json").read_text())
        traffic = deserialize_traffic(data, session_dir="debug_artifacts/session")

        for resp in traffic.graphql_responses:
            if resp.body:
                print(resp.body.get("data", {}).keys())
    """
    session_path = Path(session_dir) if session_dir else None

    def _resolve_body(body: Any, category: str) -> dict[str, Any] | None:
        """Resuelve referencias __FILE__ cargando el fichero correspondiente."""
        if isinstance(body, str) and body.startswith("__FILE__"):
            fname = body[len("__FILE__"):]
            if session_path:
                subdir_map = {
                    "graphql": "graphql_responses",
                    "api": "api_responses",
                }
                fpath = session_path / subdir_map.get(category, category) / fname
                if fpath.exists():
                    try:
                        return json.loads(fpath.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        pass
            return None
        return body

    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    def _build_graphql_meta(gm_data: dict | None) -> GraphQLMeta | None:
        if not gm_data:
            return None
        return GraphQLMeta(
            friendly_name=gm_data.get("friendly_name"),
            doc_id=gm_data.get("doc_id"),
            variables=gm_data.get("variables"),
            caller_class=gm_data.get("caller_class"),
        )

    def _build_req(d: dict[str, Any]) -> CapturedRequest:
        return CapturedRequest(
            url=d.get("url", ""),
            method=d.get("method", "GET"),
            category=d.get("category", ""),
            timestamp=_parse_dt(d.get("timestamp")) or datetime.now(),
            post_data_raw=d.get("post_data_raw"),
            post_data=d.get("post_data"),
            headers=d.get("headers", {}),
            graphql_meta=_build_graphql_meta(d.get("graphql_meta")),
        )

    def _build_resp(d: dict[str, Any]) -> CapturedResponse:
        category = d.get("category", "")
        return CapturedResponse(
            url=d.get("url", ""),
            status=d.get("status", 0),
            category=category,
            timestamp=_parse_dt(d.get("timestamp")) or datetime.now(),
            body=_resolve_body(d.get("body"), category),
            body_raw=d.get("body_raw"),
            headers=d.get("headers", {}),
        )

    return CapturedTraffic(
        graphql_requests=[_build_req(r) for r in data.get("graphql_requests", [])],
        graphql_responses=[_build_resp(r) for r in data.get("graphql_responses", [])],
        api_requests=[_build_req(r) for r in data.get("api_requests", [])],
        api_responses=[_build_resp(r) for r in data.get("api_responses", [])],
    )


def load_debug_session(session_dir: str | Path) -> FetchResult:
    """
    Carga una sesión de debug guardada en disco y reconstruye un ``FetchResult``
    listo para análisis offline.

    Funciona con sesiones guardadas por ``ContentFetcher`` cuando ``debug=True``.
    No requiere abrir un navegador ni hacer peticiones de red::

        from get_content import load_debug_session
        from parsers import FbPostParser

        result = load_debug_session("data/debug_artifacts/www.facebook.com_reel_20250601_143022")
        parser = FbPostParser(
            html_content=result.html_content,
            final_url=result.final_url,
            original_url=result.original_url,
            traffic=result.traffic,
        )
        data = parser.parse()

    Args:
        session_dir: Ruta al directorio de la sesión de debug.
                     Debe contener al menos ``meta.json``.

    Returns:
        FetchResult completamente reconstruido.

    Raises:
        FileNotFoundError: Si ``session_dir`` no existe o no contiene ``meta.json``.
        ValueError:        Si ``meta.json`` tiene formato inválido.
    """
    session_path = Path(session_dir)
    if not session_path.exists():
        raise FileNotFoundError(f"Directorio de sesión no encontrado: {session_path}")

    meta_path = session_path / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json no encontrado en: {session_path}")

    # ── Metadatos ─────────────────────────────────────────────────────────
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"meta.json inválido: {exc}") from exc

    # ── HTML ──────────────────────────────────────────────────────────────
    html_content: str | None = None
    html_path = session_path / "page.html"
    if html_path.exists():
        html_content = html_path.read_text(encoding="utf-8")
        logger.info("HTML cargado: %d KB", len(html_content) // 1024)

    # ── Tráfico de red ────────────────────────────────────────────────────
    traffic: CapturedTraffic | None = None
    traffic_path = session_path / "traffic.json"
    if traffic_path.exists():
        try:
            traffic_data = json.loads(traffic_path.read_text(encoding="utf-8"))
            traffic = deserialize_traffic(traffic_data, session_dir=session_path)
            logger.info("Tráfico cargado: %s", traffic.summary())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Error cargando traffic.json: %s", exc)

    # ── Reconstruir FetchResult ───────────────────────────────────────────
    fetched_at = datetime.now()
    try:
        fetched_at = datetime.fromisoformat(meta.get("fetched_at", ""))
    except (ValueError, TypeError):
        pass

    result = FetchResult(
        original_url=meta.get("original_url", ""),
        final_url=meta.get("final_url"),
        html_content=html_content,
        traffic=traffic,
        fetched_at=fetched_at,
        success=meta.get("success", False),
        error=meta.get("error"),
        authenticated=meta.get("authenticated", False),
        account_id=meta.get("account_id"),
        debug_session_dir=session_path,
    )

    logger.info("Sesión offline cargada desde: %s", session_path)
    return result


def list_debug_sessions(
    base_dir: str | Path = "data/debug_artifacts",
) -> list[dict[str, Any]]:
    """
    Lista todas las sesiones de debug guardadas, ordenadas de más reciente a más antigua.

    Útil para elegir qué sesión cargar con :func:`load_debug_session`
    o para análisis batch de múltiples sesiones.

    Args:
        base_dir: Directorio raíz de artefactos de debug.

    Returns:
        Lista de dicts con metadatos básicos de cada sesión::

            [
                {
                    "path":            Path("data/debug_artifacts/session_name"),
                    "original_url":    "https://...",
                    "fetched_at":      "2025-01-01T12:00:00",
                    "success":         True,
                    "authenticated":   False,
                    "traffic_summary": "GraphQL: 8req/8res | API: 2req/2res"
                },
                ...
            ]

        Las sesiones sin ``meta.json`` válido se omiten silenciosamente.

    Example::

        sessions = list_debug_sessions()
        for s in sessions:
            print(s["fetched_at"], s["original_url"][:60], s["traffic_summary"])

        result = load_debug_session(sessions[0]["path"])
    """
    base = Path(base_dir)
    if not base.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for session_path in sorted(base.iterdir(), reverse=True):
        if not session_path.is_dir():
            continue
        meta_path = session_path / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            sessions.append({
                "path": session_path,
                "original_url": meta.get("original_url", ""),
                "fetched_at": meta.get("fetched_at", ""),
                "success": meta.get("success", False),
                "authenticated": meta.get("authenticated", False),
                "traffic_summary": meta.get("traffic_summary", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return sessions