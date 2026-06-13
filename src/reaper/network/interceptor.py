"""
network_interceptor.py
======================
Dataclasses de tráfico de red e interceptor Playwright.

Este módulo es independiente de ContentFetcher y puede ser importado
directamente por los parsers para acceder a los tipos de tráfico sin
arrastrar la dependencia de Playwright + BrowserConfig.

Exports principales:
    - GraphQLMeta
    - CapturedRequest
    - CapturedResponse
    - CapturedTraffic
    - FetchResult
    - NetworkInterceptor
"""

import asyncio
import json
from reaper.utils.logger import get_logger
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Request, Route

logger = get_logger(__name__)

# ============================================================================
# SECCIÓN 1: DATACLASSES DE RESULTADO DE RED
# ============================================================================


@dataclass
class GraphQLMeta:
    """
    Metadatos extraídos del body de una petición GraphQL de Facebook.

    Facebook envía sus peticiones GraphQL como application/x-www-form-urlencoded
    con campos clave que identifican la operación y sus parámetros.

    Attributes:
        friendly_name: Nombre legible de la operación.
                       Corresponde a ``fb_api_req_friendly_name`` del body.
        doc_id:        ID del query pre-compilado en servidores de FB.
        variables:     Variables de la query como dict Python.
        caller_class:  Clase JS que originó la petición (para trazabilidad).
    """

    friendly_name: str | None = None
    doc_id: str | None = None
    variables: dict[str, Any] | None = None
    caller_class: str | None = None


@dataclass
class CapturedRequest:
    """
    Petición de red capturada, categorizada e introspectada.

    Attributes:
        url:           URL completa de la petición.
        method:        Método HTTP (GET, POST, etc.).
        category:      ``"graphql"`` | ``"api"``.
        timestamp:     Momento de intercepción.
        post_data_raw: Body crudo (string). Solo en modo debug.
        post_data:     Body parseado como dict. None si no hay body o no es parseable.
        headers:       Headers HTTP de la petición.
        graphql_meta:  Metadatos GraphQL (solo cuando ``category == "graphql"``).
    """

    url: str
    method: str
    category: str
    timestamp: datetime
    post_data_raw: str | None = None
    post_data: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    graphql_meta: GraphQLMeta | None = None


@dataclass
class CapturedResponse:
    """
    Respuesta de red capturada, categorizada e introspectada.

    Attributes:
        url:      URL de la respuesta.
        status:   Código de estado HTTP.
        category: ``"graphql"`` | ``"api"``.
        timestamp: Momento de captura.
        body:     Body parseado como dict. None si no es JSON.
        body_raw: Body crudo como string. Solo en modo debug.
        headers:  Headers HTTP de la respuesta.
    """

    url: str
    status: int
    category: str
    timestamp: datetime
    # Facebook puede responder con un único objeto JSON (dict) o con múltiples
    # fragmentos NDJSON / Incremental Delivery (list[dict]). Usar siempre
    # CapturedTraffic.normalize_body(resp.body) para iterar de forma uniforme.
    body: list[dict[str, Any]] | dict[str, Any] | None = None
    body_raw: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class CapturedTraffic:
    """
    Tráfico de red completo capturado durante una sesión de fetch.

    Organiza requests y responses por categoría para acceso directo
    desde los parsers. Exclusivo para Facebook (graphql) e Instagram (api).

    Uso desde parsers::

        for resp in result.traffic.graphql_responses:
            for fragment in CapturedTraffic.normalize_body(resp.body):
                data = fragment.get("data", {})

        search_responses = result.traffic.graphql_by_operation("SearchResultsPage")

    Attributes:
        graphql_requests:  Peticiones a ``/api/graphql/``.
        graphql_responses: Respuestas de las operaciones GraphQL.
        api_requests:      Peticiones a ``/api/v1/`` (Instagram REST API).
        api_responses:     Respuestas de la API REST de Instagram.
    """

    graphql_requests: list[CapturedRequest] = field(default_factory=list)
    graphql_responses: list[CapturedResponse] = field(default_factory=list)
    api_requests: list[CapturedRequest] = field(default_factory=list)
    api_responses: list[CapturedResponse] = field(default_factory=list)

    def all_requests(self) -> list[CapturedRequest]:
        """Retorna todas las peticiones de todas las categorías."""
        return self.graphql_requests + self.api_requests

    def all_responses(self) -> list[CapturedResponse]:
        """Retorna todas las respuestas de todas las categorías."""
        return self.graphql_responses + self.api_responses

    def graphql_by_operation(self, operation_name: str) -> list[CapturedResponse]:
        """
        Filtra respuestas GraphQL por nombre de operación (búsqueda parcial).

        Args:
            operation_name: Nombre o fragmento del ``friendly_name`` a buscar.

        Returns:
            Lista de CapturedResponse cuya request tiene el friendly_name indicado.
        """
        matching_urls: set[str] = {
            req.url
            for req in self.graphql_requests
            if req.graphql_meta
            and req.graphql_meta.friendly_name
            and operation_name in req.graphql_meta.friendly_name
        }
        return [resp for resp in self.graphql_responses if resp.url in matching_urls]

    @staticmethod
    def normalize_body(
        body: "list[dict[str, Any]] | dict[str, Any] | None",
    ) -> "list[dict[str, Any]]":
        """
        Normaliza el campo ``body`` de un ``CapturedResponse`` a lista de dicts.

        Facebook puede responder con un único objeto JSON (``dict``) o con
        múltiples fragmentos NDJSON / Incremental Delivery (``list[dict]``).
        Este helper abstrae esa diferencia::

            for fragment in CapturedTraffic.normalize_body(resp.body):
                data = fragment.get("data", {})

        Args:
            body: El campo ``body`` de un ``CapturedResponse``.

        Returns:
            Lista de dicts (vacía si body es None o tipo inesperado).
        """
        if body is None:
            return []
        if isinstance(body, dict):
            return [body]
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        return []

    @property
    def total_requests(self) -> int:
        """Número total de peticiones capturadas."""
        return len(self.graphql_requests) + len(self.api_requests)

    @property
    def total_responses(self) -> int:
        """Número total de respuestas capturadas."""
        return len(self.graphql_responses) + len(self.api_responses)

    def summary(self) -> str:
        """Línea de resumen del tráfico capturado."""
        return (
            f"GraphQL: {len(self.graphql_requests)}req/{len(self.graphql_responses)}res | "
            f"API: {len(self.api_requests)}req/{len(self.api_responses)}res"
        )


@dataclass
class FetchResult:
    """
    Resultado completo de una operación de obtención de contenido.

    Encapsula el HTML renderizado, el tráfico de red y los metadatos de sesión.
    En caso de error, ``success=False`` y ``error`` describe el fallo.

    Attributes:
        original_url:      URL solicitada originalmente.
        final_url:         URL final tras redirecciones.
        html_content:      HTML renderizado (DOM final con JS ejecutado), o None.
        traffic:           Tráfico de red capturado (GraphQL + API).
        fetched_at:        Timestamp de la captura.
        success:           True si el HTML fue obtenido sin errores críticos.
        error:             Mensaje descriptivo si ``success=False``.
        authenticated:     True si la sesión usó cookies de autenticación.
        account_id:        ID de la cuenta usada para la sesión autenticada.
        debug_session_dir: Path al directorio de debug. None si debug=False.
        updated_cookies:   Cookies leídas del contexto Playwright después de
                           la navegación. El servidor puede haber emitido
                           cabeceras ``Set-Cookie`` que extienden el TTL o
                           rotan tokens de seguridad de corta vida.
                           ``BaseScraper._fetch_with_account()`` compara este
                           campo con las cookies originales y persiste las
                           actualizadas si difieren, alargando automáticamente
                           la vida de la sesión sin necesidad de login manual.
                           ``None`` en sesiones anónimas o si la lectura falló.

    Notes:
        Los parsers deben usar ``traffic.graphql_responses`` para datos
        estructurados en lugar de depender únicamente del HTML.
    """

    original_url: str
    final_url: str | None = None
    html_content: str | None = None
    traffic: CapturedTraffic | None = None
    fetched_at: datetime = field(default_factory=datetime.now)
    success: bool = False
    error: str | None = None
    authenticated: bool = False
    account_id: str | None = None
    debug_session_dir: Path | None = None
    # Cookies post-navegación leídas del contexto de Playwright.
    # El servidor emite Set-Cookie en cada respuesta exitosa, actualizando
    # tokens de corta vida y extendiendo el TTL de las cookies de sesión.
    # None en sesiones anónimas (cookies=None al llamar a fetch()).
    updated_cookies: list[dict] | None = None


# ============================================================================
# SECCIÓN 2: INTERCEPTOR DE RED
# ============================================================================


class NetworkInterceptor:
    """
    Intercepta y categoriza el tráfico de red de una página Playwright.

    Usa dos mecanismos complementarios:

    1. **Listener ``request``** (síncrono): captura metadatos de todas las
       peticiones (URL, método, headers, body de la request, GraphQL meta).
       No lee el body de la respuesta.

    2. **``page.route()``** (async): para las URLs de interés (GraphQL /
       Instagram API), intercepta la petición, hace ``route.fetch()`` para
       obtener la respuesta con el body **completamente bufferizado** antes de
       procesarla, y luego la devuelve al browser sin modificaciones.

    Este diseño evita el ``NS_ERROR_FAILURE`` / ``Target closed`` de Firefox
    que ocurría al llamar a ``response.body()`` desde el evento ``response``
    (que se dispara cuando llegan los headers, no cuando termina el body).

    La intercepción debe activarse ANTES de ``page.goto()`` para capturar
    también las peticiones de la carga inicial de la página.

    Categorías de tráfico capturado:
        - **graphql**: POST a ``/api/graphql/`` (Facebook) o ``graphql/query`` (Instagram).
        - **api**:     Peticiones a ``/api/v1/`` o ``i.instagram.com/api`` (Instagram REST).

    Uso típico::

        interceptor = NetworkInterceptor(debug=True)
        interceptor.attach(page)          # ANTES de page.goto()
        await page.goto(url)
        # ... scroll opcional ...
        traffic = interceptor.get_traffic()
        for req in traffic.graphql_requests:
            print(req.graphql_meta.friendly_name)

    Monitorización para infinity-scroll::

        baseline = interceptor.snapshot_activity()
        await page.mouse.wheel(0, 1000)
        await asyncio.sleep(1.5)
        new_events = interceptor.new_activity_since(baseline)
        # Si new_events == 0 repetidamente → no hay más contenido

    Attributes:
        debug:                 Si True, emite logs de cada request/response.
        response_body_timeout: Segundos máximos para ``route.fetch()``.
    """

    #: Patrones que identifican endpoints GraphQL.
    GRAPHQL_PATTERNS: frozenset[str] = frozenset({
        "/api/graphql",   # Facebook: endpoint GraphQL principal
        "graphql/query",  # Instagram: queries GraphQL
    })

    #: Patrones que identifican la REST API de Instagram.
    INSTAGRAM_API_PATTERNS: frozenset[str] = frozenset({
        "/api/v1/",            # Instagram: API REST principal
        "i.instagram.com/api", # Instagram: variante CDN
    })

    def __init__(
        self,
        debug: bool = False,
        response_body_timeout: float = 25.0,
    ) -> None:
        """
        Args:
            debug:                  Emite logs de cada request/response interceptado.
            response_body_timeout:  Timeout en segundos para leer el body de respuesta.
                                    Evita bloquear en respuestas lentas o de streaming.
        """
        self.debug = debug
        self.response_body_timeout = response_body_timeout

        self._graphql_requests: list[CapturedRequest] = []
        self._graphql_responses: list[CapturedResponse] = []
        self._api_requests: list[CapturedRequest] = []
        self._api_responses: list[CapturedResponse] = []

        # Contador global de eventos para monitorización de actividad (infinity-scroll).
        self._activity_count: int = 0

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    async def attach(self, page: Page) -> None:
        """
        Registra los listeners ``request`` y las routes en la página.

        ``page.route()`` en la API async de Playwright es una coroutine y
        debe ser awaited. Por eso ``attach`` es async.

        Estrategia de captura de bodies:
            Para las URLs de interés (GraphQL / Instagram API) se registra un
            **``page.route()``** que intercepta cada petición, hace
            ``await route.fetch()`` para obtener la respuesta con el body
            completamente bufferizado, y la devuelve al browser sin cambios.
            Esto elimina la race condition de ``response.body()`` en Firefox
            (NS_ERROR_FAILURE / Target closed).

        Debe llamarse **ANTES de ``page.goto()``** para no perder las peticiones
        que se disparan durante la carga inicial.

        Args:
            page: Instancia de página de Playwright activa (no navegada aún).
        """
        # Listener síncrono de request: captura metadatos de TODAS las peticiones.
        # page.on() sigue siendo síncrono.
        page.on("request", self._on_request)

        # page.route() es async en la API async de Playwright → await obligatorio.
        for pattern in self._route_patterns():
            await page.route(pattern, self._on_route)

        if self.debug:
            logger.debug("NetworkInterceptor adjunto a la página.")

    def _route_patterns(self) -> list[str]:
        """
        Genera los patrones glob de Playwright para registrar routes.

        Playwright route() acepta strings con ``*`` y ``**``:
            - ``**/api/graphql/**`` captura Facebook GraphQL.
            - ``**/api/graphql``    captura sin trailing slash.

        Returns:
            Lista de patrones glob para cubrir todos los endpoints de interés.
        """
        return [
            "**/api/graphql",
            "**/api/graphql/**",
            "**/graphql/query",
            "**/graphql/query/**",
            "**/api/v1/**",
            "**/i.instagram.com/api/**",
        ]

    async def _on_route(self, route: Route) -> None:
        """
        Route handler que captura requests + responses con body garantizado.

        Llama a ``route.fetch()`` para obtener la respuesta del servidor con
        el body completamente bufferizado antes de procesarla. Esto evita el
        problema de NS_ERROR_FAILURE / Target closed de Firefox que ocurre
        cuando se intenta leer ``response.body()`` desde el evento ``response``
        (que se dispara cuando llegan los headers, no cuando termina el body).

        Flujo:
            1. ``route.fetch()`` → realiza la petición y espera el body completo.
            2. Leer y parsear el body de ``api_response``.
            3. ``route.fulfill()`` → devolver la respuesta al browser sin
               modificaciones, para que la página funcione con normalidad.

        El ``CapturedRequest`` ya fue creado por ``_on_request`` (listener
        síncrono que se dispara antes). Este método solo crea el
        ``CapturedResponse`` correspondiente.

        Args:
            route: Objeto Route de Playwright.
        """
        request = route.request
        url = request.url
        category = self._categorize(url)

        if category is None:
            # URL no relevante que llegó al route handler (no debería ocurrir
            # con los patrones actuales, pero manejarlo por seguridad)
            await route.continue_()
            return

        body: list[dict[str, Any]] | dict[str, Any] | None = None
        body_raw: str | None = None
        status = 200
        response_headers: dict[str, str] = {}

        try:
            async with asyncio.timeout(self.response_body_timeout):
                api_response = await route.fetch()

            status = api_response.status
            response_headers = dict(api_response.headers)

            # Sólo leer body si el status puede tenerlo
            if status not in self._NO_BODY_STATUS_CODES:
                raw_bytes = await api_response.body()
                if raw_bytes:
                    body_raw = raw_bytes.decode("utf-8", errors="replace")
                    body = self._parse_response_body(body_raw)

            # Devolver la respuesta al browser sin modificar
            await route.fulfill(response=api_response)

        except asyncio.TimeoutError:
            logger.debug("Timeout en route.fetch() para: %s", url[:60])
            await route.continue_()
            return
        except Exception as exc:
            err_str = str(exc)
            # "Target closed" ocurre si el browser se cierra mientras
            # esperamos el fetch — es esperado al cerrar la sesión
            if "Target closed" in err_str or "closed" in err_str.lower():
                logger.debug("Route abortado (target cerrado): %s", url[:60])
            else:
                logger.debug("Error en route handler (%s): %s", url[:60], exc)
            # Intentar continuar normalmente para no romper la navegación
            try:
                await route.continue_()
            except Exception:
                pass
            return

        captured = CapturedResponse(
            url=url,
            status=status,
            category=category,
            timestamp=datetime.now(),
            body=body,
            body_raw=body_raw if self.debug else None,
            headers=response_headers,
        )

        self._store_response(captured, category)
        self._activity_count += 1

        if self.debug:
            body_label = f" [body: {len(body_raw)}chars]" if body_raw else " [sin body]"
            logger.debug(
                "↓ [%s] %d %s%s",
                category.upper(),
                status,
                url[:80],
                body_label,
            )

    # Códigos HTTP que nunca llevan body según RFC 7230.
    _NO_BODY_STATUS_CODES: frozenset[int] = frozenset({
        100, 101, 102, 103,
        204,
        304,
    })

    def get_traffic(self) -> CapturedTraffic:
        """
        Retorna todo el tráfico capturado hasta el momento.

        Retorna copias de las listas internas para evitar mutaciones externas.
        Puede llamarse en cualquier momento durante o tras la navegación.

        Returns:
            CapturedTraffic con todos los requests y responses categorizados.
        """
        return CapturedTraffic(
            graphql_requests=list(self._graphql_requests),
            graphql_responses=list(self._graphql_responses),
            api_requests=list(self._api_requests),
            api_responses=list(self._api_responses),
        )

    def snapshot_activity(self) -> int:
        """
        Captura el nivel actual de actividad para comparación posterior.

        Usar con ``new_activity_since()`` para detectar inactividad durante
        el infinity-scroll.

        Returns:
            Total de eventos (requests + responses) capturados hasta ahora.
        """
        return self._activity_count

    def new_activity_since(self, baseline: int) -> int:
        """
        Calcula la actividad nueva desde un snapshot anterior.

        Args:
            baseline: Valor retornado previamente por ``snapshot_activity()``.

        Returns:
            Número de eventos nuevos desde el snapshot.
        """
        return self._activity_count - baseline

    @property
    def total_activity(self) -> int:
        """Total acumulado de eventos de red capturados."""
        return self._activity_count

    def print_summary(self) -> None:
        """Imprime un resumen del tráfico capturado (operaciones GraphQL incluidas)."""
        logger.debug("─" * 65)
        logger.debug("Resumen de tráfico de red capturado")
        logger.debug("─" * 65)
        logger.debug(
            "  GraphQL — %3d peticiones / %3d respuestas",
            len(self._graphql_requests),
            len(self._graphql_responses),
        )
        for req in self._graphql_requests:
            if req.graphql_meta and req.graphql_meta.friendly_name:
                doc = f"  [doc_id: {req.graphql_meta.doc_id}]" if req.graphql_meta.doc_id else ""
                logger.debug("    • %s%s", req.graphql_meta.friendly_name, doc)
        logger.debug(
            "  API     — %3d peticiones / %3d respuestas",
            len(self._api_requests),
            len(self._api_responses),
        )
        logger.debug("─" * 65)

    # ------------------------------------------------------------------
    # Handlers de eventos (privados)
    # ------------------------------------------------------------------

    def _on_request(self, request: Request) -> None:
        """
        Handler síncrono para cada petición de red.

        Playwright requiere que el listener de ``request`` sea síncrono.
        Las peticiones no relevantes se descartan con un return temprano.

        Args:
            request: Objeto Request de Playwright.
        """
        url = request.url
        category = self._categorize(url)
        if category is None:
            return

        # ── Leer body de la petición de forma segura ──
        # Usamos post_data_buffer (bytes crudos) para evitar UnicodeDecodeError
        # que lanza post_data cuando el body contiene bytes non-UTF-8
        post_data_raw: str | None = None
        try:
            raw_buffer = request.post_data_buffer
            if raw_buffer:
                post_data_raw = raw_buffer.decode("utf-8", errors="replace")
        except Exception:
            post_data_raw = None

        # ── Parsear body ──
        post_data: dict[str, Any] | None = None
        try:
            pd_json = request.post_data_json
            if isinstance(pd_json, dict):
                post_data = pd_json
            else:
                post_data = self._safe_parse_json(post_data_raw)
        except Exception:
            post_data = self._safe_parse_json(post_data_raw)

        graphql_meta: GraphQLMeta | None = None
        if category == "graphql" and post_data:
            graphql_meta = self._extract_graphql_meta(post_data)

        captured = CapturedRequest(
            url=url,
            method=request.method,
            category=category,
            timestamp=datetime.now(),
            post_data_raw=post_data_raw if self.debug else None,
            post_data=post_data,
            headers=dict(request.headers),
            graphql_meta=graphql_meta,
        )

        self._store_request(captured, category)
        self._activity_count += 1

        if self.debug:
            meta_label = (
                f" [{graphql_meta.friendly_name}]"
                if graphql_meta and graphql_meta.friendly_name
                else ""
            )
            logger.debug("↑ [%s]%s %s %s", category.upper(), meta_label, request.method, url[:80])

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _categorize(self, url: str) -> str | None:
        """
        Clasifica una URL en su categoría de tráfico.

        GraphQL tiene prioridad sobre API para evitar falsas clasificaciones.

        Args:
            url: URL completa de la petición o respuesta.

        Returns:
            ``"graphql"``, ``"api"``, o ``None`` si no es de interés.
        """
        if any(pattern in url for pattern in self.GRAPHQL_PATTERNS):
            return "graphql"
        if any(pattern in url for pattern in self.INSTAGRAM_API_PATTERNS):
            return "api"
        return None

    def _extract_graphql_meta(self, post_data: dict[str, Any]) -> GraphQLMeta:
        """
        Extrae metadatos de una petición GraphQL de Facebook.

        Args:
            post_data: dict del body ya parseado por Playwright.

        Returns:
            GraphQLMeta con todos los campos extraídos (None para los ausentes).
        """
        variables_raw = post_data.get("variables")
        variables: dict[str, Any] | None = None

        if isinstance(variables_raw, str):
            variables = self._safe_parse_json(variables_raw)
        elif isinstance(variables_raw, dict):
            variables = variables_raw

        return GraphQLMeta(
            friendly_name=post_data.get("fb_api_req_friendly_name"),
            doc_id=post_data.get("doc_id"),
            variables=variables,
            caller_class=post_data.get("fb_api_caller_class"),
        )

    @staticmethod
    def _parse_response_body(raw: str) -> list[dict[str, Any]] | None:
        """
        Parsea el body de una respuesta GraphQL de Facebook.

        Facebook puede responder en dos formatos:

        **Formato 1 — JSON simple** (respuestas normales):
            Un único objeto JSON en el body::

                {"data": {...}, "extensions": {...}}

            Resultado: ``[{"data": {...}, "extensions": {...}}]``

        **Formato 2 — NDJSON / Incremental Delivery** (respuestas con @defer/@stream):
            Múltiples objetos JSON separados por newlines, emitidos de forma
            incremental. El primer objeto contiene los datos no-diferidos; los
            siguientes son fragmentos @defer con su ``label`` y ``path``::

                {"data": {...}, "extensions": {"is_final": false}}
                {"label": "FooDeferred", "path": [...], "data": {...}, ...}
                ...
                {"label": "BarDeferred", "path": [...], "data": {...}, "extensions": {"is_final": true}}

            Resultado: lista con todos los fragmentos en orden de emisión.

        En ambos casos elimina el prefijo anti-XSS ``for(;;);`` si está presente.
        Siempre retorna ``List[Dict]`` (nunca un dict suelto) o ``None``.

        Args:
            raw: Body crudo como string (decodificado de UTF-8).

        Returns:
            Lista de dicts con los fragmentos parseados.
            None si el body está vacío o no contiene JSON válido.
        """
        if not raw:
            return None

        clean = raw.lstrip()

        # Eliminar el guard anti-XSS/CSRF que Facebook antepone en muchas respuestas
        if clean.startswith("for(;;);"):
            clean = clean[8:]

        if not clean:
            return None

        # ── Intento 1: JSON monolítico ──
        # Cubre el caso más común: un único objeto JSON bien formado.
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                return [parsed]
            if isinstance(parsed, list):
                # Lista JSON (formato menos habitual, pero posible)
                result = [item for item in parsed if isinstance(item, dict)]
                return result or None
            # Scalar (número, bool…) — ignorar
            return None
        except json.JSONDecodeError:
            pass

        # ── Intento 2: NDJSON (Incremental Delivery / multipart) ──
        # Cada línea no vacía debe ser un objeto JSON independiente.
        # Facebook usa este formato para respuestas con @defer o @stream.
        fragments: list[dict[str, Any]] = []
        for line in clean.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    fragments.append(obj)
                # Líneas que no sean objetos (arrays, scalars) se descartan
            except json.JSONDecodeError:
                logger.debug(f"Línea NDJSON no parseable (primeros 80 chars): {line[:80]}")
                continue

        return fragments if fragments else None
    
    @staticmethod
    def _safe_parse_json(text: str | None) -> dict[str, Any] | None:
        """
        Parsea un string JSON de forma segura, retornando None ante cualquier error.

        Args:
            text: String a parsear.

        Returns:
            dict si el parseo fue exitoso, None en cualquier otro caso.
        """
        if not text:
            return None
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    def _store_request(self, req: CapturedRequest, category: str) -> None:
        """Almacena un CapturedRequest en la lista de su categoría."""
        category_map: dict[str, list[CapturedRequest]] = {
            "graphql": self._graphql_requests,
            "api": self._api_requests,
        }
        target = category_map.get(category)
        if target is not None:
            target.append(req)

    def _store_response(self, resp: CapturedResponse, category: str) -> None:
        """Almacena un CapturedResponse en la lista de su categoría."""
        category_map: dict[str, list[CapturedResponse]] = {
            "graphql": self._graphql_responses,
            "api": self._api_responses,
        }
        target = category_map.get(category)
        if target is not None:
            target.append(resp)