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

from playwright.async_api import Page, Request, Response

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
    body: dict[str, Any] | None = None
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
            if resp.body:
                data = resp.body.get("data", {})

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


# ============================================================================
# SECCIÓN 2: INTERCEPTOR DE RED
# ============================================================================


class NetworkInterceptor:
    """
    Intercepta y categoriza el tráfico de red de una página Playwright.

    Registra listeners para eventos ``request`` y ``response`` en la página
    y almacena el tráfico de interés en listas tipadas por categoría.

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
        debug:              Si True, emite logs de cada request/response.
        response_body_timeout: Segundos máximos para leer el body de una respuesta.
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

    def attach(self, page: Page) -> None:
        """
        Registra los listeners ``request`` y ``response`` en la página.

        Debe llamarse **ANTES de ``page.goto()``** para no perder las peticiones
        que se disparan durante la carga inicial.

        Args:
            page: Instancia de página de Playwright activa (no navegada aún).

        Notes:
            - El handler de ``request`` es síncrono (Playwright lo requiere así).
            - El handler de ``response`` es async (puede usar ``await``).
        """
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        if self.debug:
            logger.debug("NetworkInterceptor adjunto a la página.")

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
        logger.info("─" * 65)
        logger.info("Resumen de tráfico de red capturado")
        logger.info("─" * 65)
        logger.info(
            "  GraphQL — %3d peticiones / %3d respuestas",
            len(self._graphql_requests),
            len(self._graphql_responses),
        )
        for req in self._graphql_requests:
            if req.graphql_meta and req.graphql_meta.friendly_name:
                doc = f"  [doc_id: {req.graphql_meta.doc_id}]" if req.graphql_meta.doc_id else ""
                logger.info("    • %s%s", req.graphql_meta.friendly_name, doc)
        logger.info(
            "  API     — %3d peticiones / %3d respuestas",
            len(self._api_requests),
            len(self._api_responses),
        )
        logger.info("─" * 65)

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

        post_data_raw: str | None = None
        try:
            raw_buffer = request.post_data_buffer
            if raw_buffer:
                post_data_raw = raw_buffer.decode("utf-8", errors="replace")
        except Exception:
            post_data_raw = None

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

    async def _on_response(self, response: Response) -> None:
        """
        Handler asíncrono para cada respuesta de red.

        Lee el body con timeout configurable para no bloquear en respuestas
        lentas o de streaming.

        Args:
            response: Objeto Response de Playwright.
        """
        url = response.url
        category = self._categorize(url)
        if category is None:
            return

        body: dict[str, Any] | None = None
        body_raw: str | None = None

        try:
            content_type = response.headers.get("content-type", "")
            should_read = (
                "json" in content_type
                or "javascript" in content_type
                or category in ("graphql", "api")
            )

            if should_read:
                # asyncio.timeout() es preferible a asyncio.wait_for() en Python 3.11+.
                try:
                    async with asyncio.timeout(self.response_body_timeout):
                        raw_bytes = await response.body()
                except asyncio.TimeoutError:
                    logger.debug("Timeout leyendo body de respuesta: %s", url[:60])
                    raw_bytes = None

                if raw_bytes:
                    body_raw = raw_bytes.decode("utf-8", errors="replace")
                    try:
                        body = json.loads(body_raw)
                    except json.JSONDecodeError:
                        body = None

        except Exception as exc:
            logger.debug("Error en response handler (%s): %s", url[:60], exc)

        captured = CapturedResponse(
            url=url,
            status=response.status,
            category=category,
            timestamp=datetime.now(),
            body=body,
            body_raw=body_raw if self.debug else None,
            headers=dict(response.headers),
        )

        self._store_response(captured, category)
        self._activity_count += 1

        if self.debug:
            body_label = f" [body: {len(body_raw)}chars]" if body_raw else " [sin body]"
            logger.debug(
                "↓ [%s] %d %s%s",
                category.upper(),
                response.status,
                url[:80],
                body_label,
            )

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