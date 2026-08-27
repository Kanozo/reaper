"""
parsers/facebook/search_parser.py
==================================
Parser para páginas de resultados de búsqueda de Facebook
(``/search/posts?q=...`` con filtros temporales).

Fuentes de datos:

    **HTML inicial** (bloques JSON embebidos):
        - Los primeros resultados visibles llegan como nodos ``Story``
          dentro de los bloques JSON del HTML renderizado, igual que en
          grupos y perfiles. Se localizan mediante búsqueda recursiva
          (patrón ``__typename == "Story"`` + ``post_id`` +
          ``comet_sections``).

    **Tráfico GraphQL** (capturado durante el scroll):
        - La paginación del feed de búsqueda llega vía operaciones
          ``SearchCometResults*`` / ``SearchResults*``. Cada respuesta
          trae edges de Stories que se añaden al feed deduplicando por
          ``post_id``.

El resultado comparte el mismo esquema canónico de post que el resto
de parsers (vía ``parse_edge`` de ``FacebookContentParser``).

Python: 3.11+
"""
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# Operaciones GraphQL relevantes para búsquedas (verificadas contra
# tráfico real capturado con debug=True en agosto 2026):
#   - SearchCometResultsPaginatedResultsQuery: paginación del feed durante
#     el scroll (bodies de varios MB, edges relay de Stories).
#   - CometSearchBootstrapKeywordsDataSourceQuery: bootstrap inicial de la
#     búsqueda (sugerencias/keywords).
#   - SearchResultsPage / variantes "SearchComet*": compatibilidad con
#     otras superficies de búsqueda.
_SEARCH_GRAPHQL_OPERATIONS = [
    "SearchCometResultsPaginatedResultsQuery",
    "SearchCometResultsPaginationQuery",
    "SearchCometResultsFilteredTabQuery",
    "SearchCometResultsTabRefetchQuery",
    "CometSearchBootstrapKeywordsDataSourceQuery",
    "SearchResultsPage",
]

# Rango temporal → etiqueta legible.
_TIME_RANGE_LABELS = {
    "hour":  "última hora",
    "today": "últimas 24 horas",
    "week":  "última semana",
    "month": "último mes",
    "year":  "último año",
}


class SearchParser(FacebookContentParser):
    """Parser para resultados de búsqueda de Facebook (/search/posts).

    Extrae los posts (Stories) visibles en el HTML inicial y enriquece
    el feed con las respuestas GraphQL capturadas durante el scroll.
    Solo incluye posts: perfiles, grupos y páginas que aparezcan como
    resultados de tipo "ver más" se ignoran.

    Attributes:
        result: Diccionario acumulador. Claves específicas:
            ``query``, ``time_range``, ``search_url``, ``feed``.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de búsquedas.

        Args:
            html_content: HTML completo renderizado de la página de resultados.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada (generada por
                ``generate_fb_recent_search_url`` o escrita a mano).
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)

        query, time_range = self._parse_search_params(self.final_url or original_url)

        self.result.update({
            "__typename":   "facebook_search_results",
            "search_url":   original_url,
            "query":        query,
            "time_range":   time_range,
            "feed":         [],
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el pipeline completo de extracción de la búsqueda.

        Flujo:
            1. Extraer bloques JSON del HTML.
            2. Extraer los primeros posts visibles desde los bloques.
            3. Enriquecer con tráfico GraphQL (posts paginados del scroll).
            4. Registrar metadatos del tráfico.

        Returns:
            Diccionario con ``query``, ``time_range``, ``search_url`` y
            ``feed`` con el esquema canónico de post.
        """
        logger.debug(
            "Iniciando extracción de BÚSQUEDA | url=%s | q=%s",
            self.final_url,
            self.result.get("query"),
        )

        self._blocks = self._extract_json_blocks()

        if self._blocks:
            self.result["raw_data_available"] = True

        try:
            self._extract_initial_posts()
            initial_count = len(self.result["feed"])
            self._parse_traffic()
            logger.info(
                "Búsqueda parseada | q=%s | feed=%d (html=%d)",
                self.result.get("query"),
                len(self.result["feed"]),
                initial_count,
            )
        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando búsqueda: %s", exc, exc_info=True)

        return self.result

    # ==================================================================
    # FEED DESDE HTML INICIAL
    # ==================================================================

    def _extract_initial_posts(self) -> None:
        """Extrae los primeros resultados desde los bloques JSON del HTML.

        Los resultados de búsqueda llegan como nodos ``Story`` estándar
        anidados en los bloques JSON (misma forma que el feed de grupo).
        Se recorren todos los bloques con búsqueda profunda, se deduplican
        por ``post_id`` y se construyen con ``parse_edge``.

        Actualiza ``self.result["feed"]`` in-place.
        """
        seen_ids: set[str] = {
            str(p.get("id") or p.get("post_id") or "")
            for p in self.result["feed"]
            if p.get("id") or p.get("post_id")
        }

        found = 0
        for block in self._blocks:
            stories = self._find_all_nodes(
                block,
                condition=lambda n: (
                    n.get("__typename") == "Story"
                    and bool(n.get("post_id"))
                    and "comet_sections" in n
                ),
            )
            for story in stories:
                prev = len(seen_ids)
                self._add_story_to_feed(story, seen_ids)
                if len(seen_ids) > prev:
                    found += 1

        logger.debug("Posts iniciales desde HTML: %d", found)

    # ==================================================================
    # TRÁFICO GRAPHQL
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result["feed"]`` con Stories del tráfico GraphQL.

        Itera directamente sobre las respuestas capturadas y procesa cada
        body exactamente una vez, sea cual sea su operación: la paginación
        conocida (``_SEARCH_GRAPHQL_OPERATIONS``) y cualquier variante
        futura de Facebook aportan Stories por igual. La deduplicación por
        ``post_id`` hace el proceso idempotente.

        Nota: NO se usa ``graphql_by_operation`` porque todas las peticiones
        GraphQL comparten la URL ``/api/graphql/`` y ese método filtra por
        URL, devolviendo TODAS las respuestas si alguna petición coincide.

        Cada body se normaliza con ``CapturedTraffic.normalize_body``
        (soporta Incremental Delivery) y se delega en
        ``_process_search_fragment``.

        Actualiza ``self.result["feed"]`` in-place.
        """
        if not self._has_graphql_traffic() or not self.traffic:
            logger.debug("_parse_traffic: sin tráfico GraphQL.")
            return

        seen_ids: set[str] = {
            str(p.get("id") or p.get("post_id") or "")
            for p in self.result["feed"]
            if p.get("id") or p.get("post_id")
        }

        added = 0
        operation_counts: dict[str, int] = {}

        for resp in self.traffic.graphql_responses:
            if not resp.body:
                continue

            key = getattr(resp, "operation", None) or "(sin operación)"
            operation_counts[key] = operation_counts.get(key, 0) + 1

            for fragment in CapturedTraffic.normalize_body(resp.body):
                added += self._process_search_fragment(fragment, seen_ids)

        self.result["traffic_parsed"] = True
        self.result["graphql_responses_available"] = len(self.traffic.graphql_responses)
        self.result["graphql_operations_found"] = operation_counts
        logger.debug("_parse_traffic: %d posts añadidos desde tráfico.", added)

    def _process_search_fragment(
        self,
        fragment: dict[str, Any],
        seen_ids: set[str],
    ) -> int:
        """Extrae y añade posts de un fragmento GraphQL de búsqueda.

        Estructura real verificada contra tráfico (ago 2026)::

            data.serpResponse.results.edges[]
                .rendering_strategy.view_model.click_model.story

        Cada edge del SERP envuelve la Story varias capas más profundo
        que en feeds de grupo/perfil, por lo que además se cubren el
        patrón relay clásico (``edges[].node``), el nodo unitario
        (``data.node``) y un último recurso DFS profundo.

        Args:
            fragment: Fragmento JSON normalizado de una respuesta GraphQL.
            seen_ids: Set de post_ids ya procesados (se modifica in-place).

        Returns:
            Número de posts añadidos desde este fragmento.
        """
        added = 0

        # Patrón 1: SERP de búsqueda (estructura canónica actual).
        serp = self._safe_get(fragment, "data", "serpResponse", "results", default={})
        if isinstance(serp, dict):
            for edge in serp.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                story = self._safe_get(
                    edge,
                    "rendering_strategy", "view_model",
                    "click_model", "story",
                )
                if (
                    isinstance(story, dict)
                    and story.get("__typename") == "Story"
                ):
                    prev = len(seen_ids)
                    self._add_story_to_feed(story, seen_ids)
                    if len(seen_ids) > prev:
                        added += 1

        # Patrón 2: cualquier nodo con edges relay de Stories.
        feed_nodes = self._find_all_nodes(
            fragment,
            condition=lambda n: isinstance(n.get("edges"), list)
            and any(
                isinstance(e, dict)
                and isinstance(e.get("node"), dict)
                and e["node"].get("__typename") == "Story"
                for e in n["edges"]
            ),
        )
        for node in feed_nodes:
            for edge in node.get("edges") or []:
                story = edge.get("node") or {}
                if story.get("__typename") != "Story":
                    continue
                prev = len(seen_ids)
                self._add_story_to_feed(story, seen_ids)
                if len(seen_ids) > prev:
                    added += 1

        # Patrón 3: Story directa en data.node (relay unitario).
        data = fragment.get("data") or {}
        node = data.get("node") or {}
        if (
            node.get("__typename") == "Story"
            and node.get("post_id")
            and "comet_sections" in node
        ):
            prev = len(seen_ids)
            self._add_story_to_feed(node, seen_ids)
            if len(seen_ids) > prev:
                added += 1

        # Patrón 4: búsqueda profunda como último recurso.
        if not added:
            stories = self._find_all_nodes(
                data,
                condition=lambda n: (
                    n.get("__typename") == "Story"
                    and bool(n.get("post_id"))
                    and "comet_sections" in n
                ),
            )
            for story in stories:
                prev = len(seen_ids)
                self._add_story_to_feed(story, seen_ids)
                if len(seen_ids) > prev:
                    added += 1

        return added

    # ==================================================================
    # UTILIDADES ESTÁTICAS
    # ==================================================================

    @staticmethod
    def _parse_search_params(url: str) -> tuple[str | None, str | None]:
        """Extrae la consulta y el rango temporal de una URL de búsqueda.

        El filtro temporal llega codificado en base64 dentro del parámetro
        ``filters`` (generado por ``generate_fb_recent_search_url``)::

            filters=<b64({"creation_time:0": '{"name":"creation_time",
            "args":"{\\"value\\":\\"week\\"}"}'})>

        Args:
            url: URL de búsqueda (``/search/posts?q=...&filters=...``).

        Returns:
            Tupla ``(query, time_range)``. Ambos pueden ser None si la
            URL no contiene los parámetros.
        """
        import base64
        import json as _json

        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
        except Exception:
            return None, None

        query_values = qs.get("q") or []
        query = unquote(query_values[0]) if query_values else None

        time_range: str | None = None
        filter_values = qs.get("filters") or []
        if filter_values:
            try:
                padded = filter_values[0] + "=" * (-len(filter_values[0]) % 4)
                decoded = base64.b64decode(padded).decode("utf-8")
                filters_obj = _json.loads(decoded)
                raw_ct = filters_obj.get("creation_time:0")
                if isinstance(raw_ct, str):
                    ct = _json.loads(raw_ct)
                    args = ct.get("args")
                    if isinstance(args, str):
                        args = _json.loads(args)
                    if isinstance(args, dict):
                        time_range = args.get("value")
            except Exception:
                time_range = None

        return query, time_range


__all__ = ["SearchParser"]
