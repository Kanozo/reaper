"""
parsers/base_parser.py
Clase base abstracta con utilidades compartidas por todos los parsers.

Responsabilidades:
    - Búsqueda recursiva genérica en estructuras JSON anidadas (DFS).
    - Acceso seguro a claves anidadas sin excepciones.
    - Extracción de bloques JSON embebidos en HTML.
    - Soporte para tráfico de red capturado (GraphQL/API).
    - Métodos base para funciones comunes entre parsers.

Arquitectura de herencia::

    BaseParser (abstracto)
    └── FacebookContentParser (lógica compartida de Facebook)
        ├── PostParser
        ├── SearchParser
        ├── GroupParser
        └── ReelParser

Python: 3.11+
"""
import json
from reaper.utils.logger import get_logger
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable

from bs4 import BeautifulSoup

from reaper.network.interceptor import CapturedTraffic

logger = get_logger(__name__)

class BaseParser(ABC):
    """Clase base abstracta para todos los parsers de contenido.

    Define la interfaz común y utilidades plataforma-agnósticas.
    No instanciar directamente; usar siempre una subclase concreta.

    Attributes:
        html_content: HTML crudo de la página a parsear.
        final_url: URL final (tras redirecciones) de la página capturada.
        original_url: URL original solicitada por el usuario.
        traffic: Tráfico de red capturado con GraphQL responses.
        debug: Si True, emite logs de diagnóstico adicionales vía ``logging``.
        result: Esqueleto de resultado garantizado. Siempre contiene:
            ``platform``, ``status``, ``error``, ``raw_data_available``,
            ``scraped_at``, ``final_url``, ``post_url``. Las subclases
            extienden este dict con sus campos específicos vía
            ``self.result.update({...})`` en su propio ``__init__``.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str = "",
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
        platform: str = "",
    ) -> None:
        """Inicializa el parser base y construye el esqueleto de resultado.

        Args:
            html_content: HTML completo renderizado de la página.
            final_url: URL final de la página (puede diferir de la original).
            original_url: URL original solicitada por el usuario. Si está
                vacía se usa ``final_url`` como fallback.
            traffic: Tráfico de red capturado durante el fetch.
                **CRUCIAL**: Los parsers deben usar esto para obtener
                datos estructurados de las APIs GraphQL.
            debug: Si True, activa logs de diagnóstico en nivel DEBUG.
            platform: Identificador de plataforma (``"facebook"`` o
                ``"instagram"``). Poblado por las subclases concretas.

        Raises:
            ValueError: Si ``html_content`` está vacío.
        """
        if not html_content:
            raise ValueError("html_content no puede estar vacío.")

        self.html_content = html_content
        self.final_url = final_url
        self.original_url = original_url or final_url
        self.traffic = traffic
        self.debug = debug
        self._blocks: list[dict] = []

        # ── Esqueleto de resultado garantizado ──────────────────────────
        # Todos los campos que SIEMPRE deben existir en cualquier resultado,
        # independientemente de si la extracción tuvo éxito o no.
        # Las subclases concretas extienden este dict con sus campos
        # específicos usando self.result.update({...}) en su __init__.
        self.result: dict[str, Any] = {
            "platform":           platform,
            "status":             "ok",
            "error":              None,
            "raw_data_available": False,
            "scraped_at":         datetime.now(),
            "final_url":          self.final_url,
            "post_url":           self.original_url,
        }

    # ==================================================================
    # INTERFAZ PÚBLICA (contrato abstracto)
    # ==================================================================

    @abstractmethod
    def parse(self) -> dict[str, Any]:
        """Ejecuta el proceso completo de extracción.

        Returns:
            Diccionario con todos los datos extraídos. La estructura
            exacta depende de cada subclase concreta.
        """
        ...

    # ==================================================================
    # BÚSQUEDA RECURSIVA DFS
    # ==================================================================

    def _recursive_search(
        self,
        data: Any,
        condition: Callable[[dict], bool],
        extract: Callable[[dict], Any] | None = None,
    ) -> Any | None:
        """Búsqueda Depth-First en una estructura de datos anidada.

        Recorre dicts y listas recursivamente hasta encontrar el primer
        nodo que cumpla ``condition``.

        Args:
            data: Raíz de la estructura a explorar.
            condition: Callable que recibe un dict y retorna True si
                es el nodo buscado.
            extract: Callable opcional para transformar el nodo
                encontrado antes de retornarlo.

        Returns:
            El primer nodo encontrado (o su transformación si se
            proporcionó ``extract``), o None si no se encontró.

        Example:
            >>> node = self._recursive_search(
            ...     block,
            ...     condition=lambda n: n.get("__isFeedUnit") == "Story",
            ... )
        """
        if isinstance(data, dict):
            if condition(data):
                return extract(data) if extract else data
            for value in data.values():
                result = self._recursive_search(value, condition, extract)
                if result is not None:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._recursive_search(item, condition, extract)
                if result is not None:
                    return result
        return None

    def _find_all_nodes(
        self,
        data: Any,
        condition: Callable[[dict], bool],
        depth: int = 0,
        max_depth: int = 50,
    ) -> list[dict]:
        """Búsqueda DFS que recopila TODOS los nodos que cumplen la condición.

        A diferencia de ``_recursive_search``, no se detiene en el primer
        resultado sino que acumula todos los nodos coincidentes.

        Args:
            data: Estructura de datos a recorrer.
            condition: Función que retorna True si el nodo cumple el criterio.
            depth: Profundidad actual (uso interno para la recursión).
            max_depth: Profundidad máxima de recursión para evitar stack overflow.

        Returns:
            Lista con todos los nodos que cumplen la condición.

        Example:
            >>> stories = self._find_all_nodes(
            ...     block,
            ...     condition=lambda n: n.get("__typename") == "Story",
            ... )
        """
        results: list[dict] = []
        if depth > max_depth:
            return results

        if isinstance(data, dict):
            if condition(data):
                results.append(data)
            for value in data.values():
                results.extend(
                    self._find_all_nodes(value, condition, depth + 1, max_depth)
                )
        elif isinstance(data, list):
            for item in data:
                results.extend(
                    self._find_all_nodes(item, condition, depth + 1, max_depth)
                )
        return results

    # ==================================================================
    # ACCESO SEGURO A CLAVES ANIDADAS
    # ==================================================================

    def _safe_get(
        self,
        data: Any,
        *keys: str,
        default: Any = None,
    ) -> Any:
        """Navega una ruta de claves anidadas en un dict de forma segura.

        Evita KeyError y TypeError al acceder a estructuras JSON
        profundamente anidadas cuya existencia no está garantizada.

        Args:
            data: Diccionario raíz desde el que iniciar la navegación.
            *keys: Secuencia de claves que forman la ruta de navegación.
            default: Valor a retornar si la ruta no existe o es None.

        Returns:
            El valor al final de la ruta, o ``default`` si algún paso
            falla o retorna None.

        Example:
            >>> url = self._safe_get(actor, "profile_picture", "uri", default="")
            >>> count = self._safe_get(feedback, "reaction_count", "count", default=0)
        """
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
            if current is None:
                return default
        return current if current is not None else default

    # ==================================================================
    # EXTRACCIÓN DE BLOQUES JSON DEL HTML
    # ==================================================================

    def _extract_json_blocks(self) -> list[dict]:
        """Extrae y parsea todos los bloques JSON embebidos en el HTML.

        Busca etiquetas ``<script type="application/json">`` y parsea
        su contenido. Los bloques inválidos se descartan silenciosamente
        (o con log DEBUG si ``self.debug`` está activo).

        Returns:
            Lista de dicts, uno por cada bloque JSON válido encontrado.
            Retorna lista vacía si no hay bloques o si el HTML es inválido.
        """
        blocks: list[dict] = []

        try:
            soup = BeautifulSoup(self.html_content, "html.parser")
            script_tags = soup.find_all("script", {"type": "application/json"})

            if not script_tags:
                logger.debug("No se encontraron bloques <script type='application/json'>")
                return blocks

            logger.debug("Procesando %d bloques JSON...", len(script_tags))

            for idx, tag in enumerate(script_tags):
                if not tag.string:
                    continue
                try:
                    parsed = json.loads(tag.string)
                    blocks.append(parsed)
                except json.JSONDecodeError as exc:
                    logger.debug("Bloque #%d JSON inválido: %s", idx, exc)
                    continue

            self._blocks = blocks
            logger.debug("%d bloques JSON válidos extraídos.", len(blocks))

        except Exception as exc:
            logger.error("Error extrayendo bloques JSON: %s", exc)

        return blocks

    # ==================================================================
    # PARSEO DE TIMESTAMPS
    # ==================================================================

    @staticmethod
    def _parse_timestamp(ts: Any) -> datetime | None:
        """Convierte un timestamp Unix a un objeto ``datetime``.

        Args:
            ts: Timestamp en segundos desde epoch (int, float o string).
                Acepta None de forma segura.

        Returns:
            ``datetime`` local si la conversión es exitosa, ``None``
            si el valor es inválido, None, o fuera de rango.

        Example:
            >>> dt = BaseParser._parse_timestamp(1700000000)
            >>> dt = BaseParser._parse_timestamp("1700000000")
            >>> dt = BaseParser._parse_timestamp(None)  # → None
        """
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(int(ts))
        except (ValueError, TypeError, OSError):
            return None

    # ==================================================================
    # UTILIDADES PARA TRÁFICO GRAPHQL
    # ==================================================================

    def _get_graphql_response_by_operation(
        self,
        operation_name: str,
    ) -> list[dict[str, Any]]:
        """Filtra las respuestas GraphQL por nombre de operación.

        Args:
            operation_name: Nombre exacto de la operación GraphQL a filtrar
                (ej. ``"CommentsListComponentsPaginationQuery"``).

        Returns:
            Lista de bodies parseados (dict) de las respuestas que coinciden.
            Retorna lista vacía si no hay tráfico disponible.
        """
        if not self.traffic:
            return []

        responses = self.traffic.graphql_by_operation(operation_name)
        return [resp.body for resp in responses if resp.body]

    def _get_first_graphql_response(
        self,
        operation_name: str,
    ) -> dict[str, Any] | None:
        """Obtiene la primera respuesta GraphQL que coincide con la operación.

        Wrapper conveniente sobre ``_get_graphql_response_by_operation``
        cuando solo se necesita un resultado.

        Args:
            operation_name: Nombre de la operación GraphQL a filtrar.

        Returns:
            Body parseado (dict) de la primera respuesta que coincide,
            o None si no hay coincidencias.
        """
        responses = self._get_graphql_response_by_operation(operation_name)
        return responses[0] if responses else None

    def _has_graphql_traffic(self) -> bool:
        """Verifica si hay tráfico GraphQL disponible.

        Returns:
            True si hay al menos una respuesta GraphQL capturada.
        """
        if not self.traffic:
            return False
        return len(self.traffic.graphql_responses) > 0

    def _get_traffic_summary(self) -> str:
        """Retorna un resumen legible del tráfico capturado.

        Returns:
            String con el resumen del tráfico, o cadena vacía si no hay tráfico.
        """
        if not self.traffic:
            return ""
        return self.traffic.summary()

    # ==================================================================
    # MÉTODO BASE DE PARSEO DE TRÁFICO (placeholder para subclases)
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Parsea el tráfico de red para enriquecer el resultado.

        **MÉTODO BASE** — Las subclases deben hacer override para
        implementar lógica específica de su plataforma o tipo de contenido.

        La implementación base es un no-op intencional. Las subclases
        operan sobre ``self.result`` directamente, eliminando la ambigüedad
        de pasar un dict externo que siempre era ``self.result``.
        """