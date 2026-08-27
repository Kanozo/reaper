import asyncio
import json
from reaper.utils.logger import get_logger
import random
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from camoufox.sync_api import Camoufox
from camoufox.async_api import AsyncCamoufox

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from reaper.anti_detection import (
    BrowserFingerprint,
    human_delay,
    human_scroll,
    micro_delay,
    simulate_distraction,
    simulate_idle,
    simulate_reading_pause,
    simulate_page_focus_blur
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

    # Pausa máxima tras la carga inicial (segundos). Con anti-detección activa,
    # se usa como límite superior de simulate_idle (que ya aporta el realismo).
    # Reducido de 5.0 → 2.0 porque simulate_idle cubre la espera con movimiento.
    PAGE_LOAD_WAIT: float = 2.0

    # Pausa entre iteraciones de scroll (segundos).
    # Con anti-detección, human_scroll() aporta sus propios delays internos;
    # este valor se usa solo como micro-pausa adicional de seguridad.
    SCROLL_WAIT_TIME: float = 0.5

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

    # User-Agent desktop moderno. Usado solo cuando USE_ANTI_DETECTION=False.
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Timeout de navegación en milisegundos.
    NAVIGATION_TIMEOUT_MS: int = 100_000

    # Viewport Full HD (se instancia en __init__ para evitar mutabilidad compartida).
    # Usado solo cuando USE_ANTI_DETECTION=False; si está activo, el fingerprint
    # genera su propio viewport coherente con el UA y el OS.
    VIEWPORT: dict[str, int] = None  # type: ignore[assignment]

    LOCALE: str = "en-US"
    TIMEZONE_ID: str = "America/New_York"

    # ---- Anti-detección ----

    # Si True, usa generate_fingerprint() para UA/viewport/locale/timezone aleatorio
    # y coherente, inyecta stealth JS y aplica comportamiento humano en scroll/esperas.
    USE_ANTI_DETECTION: bool = True

    # Tipo de navegador para el fingerprint. "firefox" = mejor cobertura en Meta
    # (Facebook/Instagram). "chromium" para otros targets.
    ANTI_DETECTION_BROWSER_TYPE: str = "chromium"

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
        "operation": resp.operation,
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
            "operation": resp.operation,
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

        fetcher = ContentFetcher("https://www.facebook.com/reel/816043001524221")
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
        self._fingerprint: BrowserFingerprint | None = None

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
        cookies: list[dict] | None = None,
    ) -> FetchResult:
        """
        Ejecuta la navegación completa usando Camoufox.

        Camoufox es un Firefox modificado que:
        - Evade detección de Facebook (no muestra logo congelado)
        - Captura tráfico sin errores 0x80004005
        - No requiere stealth JS adicional

        Args:
            screenshot:      Captura PNG del contenedor.
            auto_scroll:     Scroll midiendo cambios de altura DOM.
            infinity_scroll: Scroll monitorizando tráfico de red.
            proxy_server:    URL del proxy (ej: "http://ip:port").
            proxy_username:  Usuario para autenticación del proxy.
            proxy_password:  Contraseña para autenticación del proxy.
            cookies:         Lista de cookies en formato Playwright.

        Returns:
            FetchResult con HTML renderizado, tráfico capturado y metadatos.
        """
        interceptor = NetworkInterceptor(
            debug=self.debug,
            response_body_timeout=self.cfg.RESPONSE_BODY_TIMEOUT,
        )

        result = FetchResult(original_url=self.url)

        # Configurar proxy para Camoufox
        proxy_config: dict[str, str] | None = None
        if proxy_server:
            proxy_config = {"server": proxy_server}
            if proxy_username:
                proxy_config["username"] = proxy_username
            if proxy_password:
                proxy_config["password"] = proxy_password

        try:
            # ── Lanzar Camoufox ──
            # Camoufox tiene su propio async context manager
            async with AsyncCamoufox(
                headless=self.headless,
                proxy=proxy_config,
                # Anti-detección nativa (no necesita stealth JS)
                humanize=True,  # Simula comportamiento humano
                os=random.choice(["windows", "macos", "linux"]),  # OS aleatorio
            ) as browser:
                context = await browser.new_context(
                    viewport=self.cfg.VIEWPORT,
                    locale=self.cfg.LOCALE,
                    timezone_id=self.cfg.TIMEZONE_ID,
                )
                page = await context.new_page()

                # ── Inyección de cookies ──
                if cookies:
                    await context.add_cookies(cookies)
                    if self.debug:
                        logger.debug(
                            "Cookies inyectadas | cantidad=%d | dominios=%s",
                            len(cookies),
                            list({c.get("domain", "?") for c in cookies})[:5],
                        )

                # ── Adjuntar interceptor ANTES de navegar ──
                await interceptor.attach(page)

                if not await self._navigate(page):
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

                # ── Capturar cookies actualizadas ──
                if cookies is not None:
                    try:
                        self._updated_cookies = await context.cookies()
                    except Exception as _exc:
                        logger.debug("No se pudieron leer cookies post-nav: %s", _exc)
                        self._updated_cookies = None
                else:
                    self._updated_cookies = None

                if self.debug:
                    session_dir = await self._save_debug_artifacts(page, interceptor, result)
                    result.debug_session_dir = session_dir
                    interceptor.print_summary()

            result.html_content = self._html_content
            result.final_url = self._final_url
            result.traffic = interceptor.get_traffic()
            result.updated_cookies = getattr(self, "_updated_cookies", None)
            result.success = True
            logger.debug("Contenido obtenido. Tráfico: %s", result.traffic.summary())

        except Exception as exc:
            result.error = str(exc)
            result.traffic = interceptor.get_traffic()
            logger.error("Error al obtener contenido: %s", exc)
            if self.debug:
                traceback.print_exc()

        return result

    # ------------------------------------------------------------------
    # Configuración del navegador
    # ------------------------------------------------------------------

    async def _launch_browser(self, pw) -> Browser:
        """
        Lanza Camoufox (Firefox modificado anti-detección).

        Camoufox resuelve:
        1. Detección de automatización por Facebook (patches binarios)
        2. Error 0x80004005 al leer bodies (patches de red)

        Nota: Camoufox tiene su propio launcher, no usa pw.firefox.launch().
        El parámetro 'pw' se ignora pero se mantiene por compatibilidad de interfaz.

        Returns:
            Browser: Instancia de Camoufox (compatible con API de Playwright).
        """
        # Camoufox se lanza con su propio contexto, no con async_playwright
        # Retornamos None aquí y manejamos el lanzamiento en fetch()
        return None

    async def _create_context(
        self,
        browser: Browser,
        proxy_config: dict[str, str] | None = None,
        fingerprint: BrowserFingerprint | None = None,
    ) -> BrowserContext:
        """
        Camoufox maneja su propio contexto con anti-detección nativa.
        Este método no se usa con Camoufox, pero se mantiene por compatibilidad.
        """
        raise NotImplementedError("Camoufox maneja su propio contexto")

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    async def _navigate(self, page: Page) -> bool:
        """
        Navega a ``self.url`` y espera a que el DOM esté listo.

        CRÍTICO: No usar ``wait_until="networkidle"`` con Facebook porque
        tiene heartbeats periódicos (/nw/, webstorage) que mantienen la red
        "activa" indefinidamente, causando que el proceso nunca termine.

        Args:
            page: Página de Playwright activa.

        Returns:
            True si la navegación fue exitosa, False si ocurrió un error.
        """
        try:
            logger.info("Navegando a: %s", self.url)
            
            # ── CRÍTICO: Usar domcontentloaded en lugar de networkidle ──
            await page.goto(
                self.url,
                wait_until="domcontentloaded",  # ← Cambiar de "networkidle"
                timeout=self.cfg.NAVIGATION_TIMEOUT_MS,
            )
            logger.info("URL final: %s", page.url)

            # Espera adicional para que el JS renderice el contenido
            if self.cfg.USE_ANTI_DETECTION:
                idle_duration = random.uniform(
                    self.cfg.PAGE_LOAD_WAIT * 0.5,
                    self.cfg.PAGE_LOAD_WAIT,
                )
                logger.debug("Post-nav idle: %.2fs", idle_duration)
                await simulate_idle(page, idle_duration)

                if random.random() < 0.35:
                    await simulate_page_focus_blur(page)
            else:
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
        Scroll automático con detección y cierre garantizado de auth dialogs.

        Si detecta un login popup, lo cierra inmediatamente y hace scroll
        sobre la página completa. Timeout global de 60 segundos.

        Args:
            page: Página de Playwright activa (con interceptor ya adjunto).
        """
        _AUTH_SELECTORS: tuple[str, ...] = (
            'input[type="password"]',
            'form[action*="login"]',
            'input[name="email"]',
            'input[name="pass"]',
        )
        _CLOSE_ARIA_LABELS: frozenset[str] = frozenset({
            "close", "cerrar", "fermer", "schließen", "chiudi",
            "fechar", "закрыть", "닫기", "关闭", "閉じる",
        })

        try:
            # ── Detectar dialog con timeout de 10s ──
            container = page.locator('div[role="dialog"]')
            
            try:
                await container.wait_for(state="visible", timeout=10000)
                bbox = await container.bounding_box()
                logger.debug("Dialog detectado y visible")
            except Exception:
                bbox = None
                logger.debug("No hay dialog visible tras 10s. Scroll sobre la página.")

            scroll_on_page: bool = bbox is None

            if bbox is not None:
                # ── Detectar si es auth dialog ──
                is_auth_dialog = False
                for selector in _AUTH_SELECTORS:
                    try:
                        if await container.locator(selector).count() > 0:
                            is_auth_dialog = True
                            logger.debug("Auth dialog detectado via selector '%s'.", selector)
                            break
                    except Exception:
                        continue

                if is_auth_dialog:
                    # ── Cerrar auth dialog ──
                    close_btn = None

                    # Intento 1: aria-label="Close"
                    for btn in await container.locator("[role='button'][aria-label]").all():
                        try:
                            label = (await btn.get_attribute("aria-label") or "").lower().strip()
                            if label in _CLOSE_ARIA_LABELS:
                                close_btn = btn
                                logger.debug("Botón close encontrado: aria-label=%r", label)
                                break
                        except Exception:
                            continue

                    # Intento 2: primer botón visible sin aria-label de acción
                    if close_btn is None:
                        for btn in await container.locator("[role='button']").all():
                            try:
                                if not await btn.is_visible():
                                    continue
                                label = (await btn.get_attribute("aria-label") or "").lower().strip()
                                # Si no tiene label o no es una acción de contenido, es el close
                                if not label or label not in {"like", "comment", "share", "log in", "create new account"}:
                                    close_btn = btn
                                    logger.debug("Botón close (fallback) encontrado")
                                    break
                            except Exception:
                                continue

                    # Intento 3: tecla Escape (método universal)
                    if close_btn is not None:
                        try:
                            if self.cfg.USE_ANTI_DETECTION:
                                btn_bbox = await close_btn.bounding_box()
                                if btn_bbox:
                                    cx = btn_bbox["x"] + btn_bbox["width"] / 2
                                    cy = btn_bbox["y"] + btn_bbox["height"] / 2
                                    await page.mouse.move(cx, cy)
                                    await micro_delay(80, 150)
                            await close_btn.click()
                            logger.debug("Auth dialog cerrado via click")
                        except Exception as exc:
                            logger.warning("Click en close falló: %s. Usando Escape.", exc)
                            await page.keyboard.press("Escape")
                            logger.debug("Auth dialog cerrado via Escape")
                    else:
                        logger.warning("No se encontró botón close. Usando Escape.")
                        await page.keyboard.press("Escape")
                        logger.debug("Auth dialog cerrado via Escape")

                    # Esperar a que el dialog desaparezca
                    await asyncio.sleep(1.0)
                    scroll_on_page = True

            # ── Posicionar cursor ──
            if scroll_on_page:
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                center_x = viewport["width"] / 2
                start_y = viewport["height"] / 2
                await page.mouse.move(center_x, start_y)
                get_scroll_height = "document.documentElement.scrollHeight"
                logger.debug("Scroll sobre PÁGINA | cursor X=%.0f, Y=%.0f", center_x, start_y)
            else:
                center_x = bbox["x"] + bbox["width"] / 2
                start_y = bbox["y"] + 100
                await page.mouse.move(center_x, start_y)
                get_scroll_height = "el => el.scrollHeight"
                logger.debug("Scroll sobre DIALOG | cursor X=%.0f, Y=%.0f", center_x, start_y)

            # ── Bucle de scroll con timeout global ──
            last_height = 0
            no_change_count = 0
            iteration = 0
            start_time = asyncio.get_event_loop().time()
            MAX_SCROLL_TIME = 60  # Timeout global

            logger.debug("Iniciando auto-scroll (timeout: %ds)...", MAX_SCROLL_TIME)

            while iteration < self.cfg.MAX_SCROLL_ITERATIONS:
                # Verificar timeout global
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > MAX_SCROLL_TIME:
                    logger.debug("Auto-scroll finalizado: timeout %ds alcanzado", MAX_SCROLL_TIME)
                    break

                if self.cfg.USE_ANTI_DETECTION:
                    await human_scroll(page, direction="down", amount=self.cfg.SCROLL_DELTA)
                    await micro_delay(150, int(self.cfg.SCROLL_WAIT_TIME * 1000))
                else:
                    await page.mouse.wheel(0, self.cfg.SCROLL_DELTA)
                    await asyncio.sleep(self.cfg.SCROLL_WAIT_TIME)

                # Medir altura
                if scroll_on_page:
                    current_height = await page.evaluate(get_scroll_height)
                else:
                    current_height = await container.evaluate(get_scroll_height)

                if current_height == last_height:
                    no_change_count += 1
                    logger.debug("Sin cambio (%d/%d)", no_change_count, self.cfg.NO_CHANGE_THRESHOLD)
                    if no_change_count >= self.cfg.NO_CHANGE_THRESHOLD:
                        logger.debug("Auto-scroll finalizado: sin más contenido.")
                        break
                else:
                    no_change_count = 0
                    height_delta = current_height - last_height
                    last_height = current_height
                    logger.debug("scrollHeight: %dpx (+%dpx)", current_height, height_delta)

                iteration += 1

            if self.cfg.USE_ANTI_DETECTION:
                await simulate_idle(page, random.uniform(0.8, 1.5))
            else:
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

        Con anti-detección activa sustituye los ``asyncio.sleep`` fijos por
        ``simulate_idle`` y ``human_delay``, reduciendo el tiempo total de espera
        sin sacrificar el realismo: el ratón sigue en movimiento y los intervalos
        siguen distribuciones gaussianas en lugar de valores constantes.

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

            logger.debug("Iniciando infinity-scroll (modo tráfico de red)...")

            no_activity_count = 0
            iteration = 0
            baseline = interceptor.snapshot_activity()

            while (
                iteration < self.cfg.MAX_SCROLL_ITERATIONS
                and no_activity_count < self.cfg.INFINITY_NO_REQUESTS_THRESHOLD
            ):
                iteration += 1

                if self.cfg.USE_ANTI_DETECTION:
                    await human_scroll(page, direction="down", amount=self.cfg.SCROLL_DELTA)
                else:
                    await page.mouse.wheel(0, self.cfg.SCROLL_DELTA)

                # Pausa base entre scroll y medición de actividad.
                # human_delay usa distribución gaussiana → evita el patrón fijo.
                if self.cfg.USE_ANTI_DETECTION:
                    await human_delay(min_seconds=1.0, max_seconds=2.0)
                else:
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
                    # Espera mientras la red procesa las respuestas.
                    # simulate_idle mantiene el ratón activo durante la carga.
                    if self.cfg.USE_ANTI_DETECTION:
                        await simulate_idle(page, random.uniform(1.0, 2.0))
                    else:
                        await asyncio.sleep(3.5)
                else:
                    no_activity_count += 1

                    if no_activity_count == 2:
                        logger.debug("Scroll agresivo de refuerzo...")
                        await page.mouse.wheel(0, 1_500)
                        # Espera más generosa para que el servidor responda al
                        # scroll agresivo; human_delay varía para no ser predecible.
                        if self.cfg.USE_ANTI_DETECTION:
                            await human_delay(min_seconds=2.0, max_seconds=4.0)
                        else:
                            await asyncio.sleep(5.5)
                        if interceptor.new_activity_since(baseline) > 0:
                            no_activity_count = 0

                # Pausa inter-iteración: el usuario está leyendo el contenido cargado.
                # Con anti-detección: rango reducido (1.5-3s vs 5s fijo) + idle activo.
                if self.cfg.USE_ANTI_DETECTION:
                    await simulate_idle(page, random.uniform(1.0, 2.5))
                else:
                    await asyncio.sleep(5.0)

                baseline = interceptor.snapshot_activity()

                # Distracción ocasional (10%): añade variabilidad a nivel de sesión.
                if self.cfg.USE_ANTI_DETECTION and random.random() < 0.10:
                    await simulate_distraction(page)
                    await page.mouse.move(cx, cy)

            # Pausa final tras terminar el scroll.
            if self.cfg.USE_ANTI_DETECTION:
                await simulate_idle(page, random.uniform(0.8, 1.5))
            else:
                await asyncio.sleep(3)

            logger.debug(
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

            logger.debug("Sesión debug guardada en: %s", session_dir)
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
            operation=d.get("operation"),
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

        from reaper.network import load_debug_session

        result = load_debug_session("data/debug_artifacts/www.facebook.com_reel_20250601_143022")
        print(result.final_url, result.fetched_at)

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
            logger.debug("Tráfico cargado: %s", traffic.summary())
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

    logger.debug("Sesión offline cargada desde: %s", session_path)
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