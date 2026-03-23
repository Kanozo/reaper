from reaper.utils.logger import get_logger
import traceback
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

logger = get_logger(__name__)


class PostParser(FacebookContentParser):
    """Parser para posts regulares de Facebook.

    Extrae el contenido principal de una publicación: autor, texto,
    adjuntos, comentarios, reacciones y metadata del grupo si aplica.

    Attributes:
        _story: Nodo Story principal extraído del HTML, o None hasta
            que ``_locate_story()`` lo encuentre.
        result: Diccionario acumulador con los datos extraídos del post.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de posts regulares.

        Args:
            html_content: HTML completo renderizado de la página.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self._story: dict | None = None
        # Campos específicos de PostParser; el esqueleto común
        # (platform, status, error, raw_data_available, scraped_at,
        # final_url, post_url) ya fue inicializado por BaseParser.
        self.result.update({
            "__typename": "regular_post",
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el proceso completo de extracción del post.

        Flujo de extracción:
            1. Extraer bloques JSON del HTML.
            2. Localizar el nodo Story principal.
            3. Extraer todos los campos del post.
            4. Enriquecer con comentarios adicionales del tráfico GraphQL.

        Returns:
            Diccionario con todos los campos del post. Si falla algún
            paso crítico, ``result["error"]`` contendrá el mensaje de error.
        """
        logger.info("Iniciando extracción de POST REGULAR...")

        self._blocks = self._extract_json_blocks()

        if not self._locate_story():
            self.result["error"] = "No se encontró ningún nodo Story válido."
            logger.warning("PostParser: no se encontró Story en %s", self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_basic_info()
            self.result["author"] = self._extract_author_common(self._story)
            self._extract_content()
            self.result["attachments"] = self._extract_attachments_common(self._story)
            self._extract_comments()
            self.result.update(self._extract_feedback_common(self._story))

            # --- Hashtags y menciones ---
            msg_ranges = (
                self._safe_get(
                    self._story, "comet_sections", "content", "story",
                    "comet_sections", "message", "story", "message", "ranges",
                    default=[],
                )
                or self._safe_get(
                    self._story, "comet_sections", "content", "story",
                    "comet_sections", "message_container", "story", "message", "ranges",
                    default=[],
                )
                or []
            )
            hashtags, mentions = self._parse_ranges(msg_ranges)

            self.result["hashtags"] = hashtags
            self.result["mentions"] = mentions

            has_group = self._extract_group_info()
            if has_group:
                self.result["original_post"] = self._extract_original_post_common(
                    self._story
                )
            self.result["is_sponsored"] = self._story.get("sponsored_data") is not None
            self._parse_traffic()

            logger.info("Post regular parseado correctamente.")

        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando post: %s", exc)
            if self.debug:
                traceback.print_exc()

        return self.result

    # ==================================================================
    # LOCALIZACIÓN DEL NODO STORY
    # ==================================================================

    def _locate_story(self) -> bool:
        """Localiza el nodo Story principal en los bloques JSON.

        Busca el primer dict que tenga ``__isFeedUnit == "Story"``
        mediante búsqueda DFS en todos los bloques extraídos del HTML.

        Returns:
            True si se encontró y asignó ``self._story``, False si no.
        """
        for block in self._blocks:
            node = self._recursive_search(
                block,
                condition=lambda n: n.get("__isFeedUnit") == "Story",
            )
            if node:
                self._story = node
                logger.debug("Nodo Story localizado.")
                return True

        logger.debug("No se encontró nodo Story con __isFeedUnit='Story'.")
        return False

    # ==================================================================
    # EXTRACCIÓN DE CAMPOS
    # ==================================================================

    def _extract_basic_info(self) -> None:
        """Extrae id, timestamp de publicación y URL permanente del post.

        Actualiza ``self.result`` in-place con:
            - ``id``: ID del post.
            - ``posted_at``: datetime de publicación.
            - ``permalink_url``: URL permanente del post.
        """
        story = self._story
        self.result["id"] = self._safe_get(story, "post_id", default="unknown")

        creation_time = self._safe_get(
            story, "comet_sections", "timestamp", "story", "creation_time"
        )
        self.result["posted_at"] = self._parse_timestamp(creation_time)
        self.result["permalink_url"] = story.get("permalink_url", self.final_url)

    def _extract_content(self) -> None:
        """Extrae el texto principal del post.

        Navega ``comet_sections.content.story.message`` para obtener el
        texto del mensaje. Asigna cadena vacía si no hay texto.

        Actualiza ``self.result["text"]`` in-place.
        """
        message = self._safe_get(
            self._story, "comet_sections", "content", "story", "message"
        )
        if message and isinstance(message, dict):
            self.result["text"] = self._safe_get(message, "text", default="")
        else:
            self.result["text"] = ""

    def _extract_comments(self) -> None:
        """Extrae los comentarios visibles en el HTML del post.

        Los comentarios adicionales cargados dinámicamente se añaden
        después en ``_parse_traffic`` desde el tráfico GraphQL.

        Actualiza ``self.result["comments"]`` in-place.
        """
        self.result["comments"] = []

        comments_node = self._safe_get(
            self._story,
            "comet_sections", "feedback", "story",
            "story_ufi_container", "story",
            "feedback_context", "feedback_target_with_context",
            "comment_list_renderer", "feedback",
            "comment_rendering_instance_for_feed_location", "comments",
        )

        if not comments_node:
            return

        for edge in self._safe_get(comments_node, "edges", default=[]):
            node = edge.get("node", {})
            if node:
                self.result["comments"].append(self._build_comment_dict(node))

    def _extract_group_info(self) -> bool:
        """Extrae información del grupo si el post pertenece a uno.

        Delega en ``_extract_group_common`` y actualiza
        ``self.result["group"]`` in-place.

        Returns:
            True si el post pertenece a un grupo, False si no.
        """
        self.result["group"] = self._extract_group_common(self._story)
        return self.result["group"] is not None

    # ==================================================================
    # CONSTRUCCIÓN DE COMENTARIOS
    # ==================================================================

    def _build_comment_dict(self, node: dict) -> dict[str, Any]:
        """Construye el diccionario normalizado de un comentario.

        Args:
            node: Nodo de comentario extraído del grafo de Facebook.

        Returns:
            Dict normalizado con los campos del comentario:
                ``id``, ``depth``, ``text``, ``created_at``, ``author``,
                ``replies_count``, ``reactions``, ``reaction_count``.
        """
        author = node.get("author", {})
        author_id = author.get("id", "")

        reactions = [
            {
                "id": self._safe_get(edge, "node", "id"),
                "count": edge.get("reaction_count", 0),
            }
            for edge in self._safe_get(
                node, "feedback", "top_reactions", "edges", default=[]
            )
        ]

        return {
            "id": node.get("legacy_fbid"),
            "depth": node.get("depth", 0),
            "text": self._safe_get(node, "body", "text"),
            "created_at": self._parse_timestamp(node.get("created_time")),
            "author": {
                "id": author_id,
                "name": author.get("name", ""),
                "profile_url": (
                    author.get("url")
                    or f"https://www.facebook.com/profile.php?id={author_id}"
                ),
                "gender": author.get("gender", ""),
                "avatar": self._safe_get(
                    author, "profile_picture_depth_0_increased", "uri"
                ),
            },
            "replies_count": self._safe_get(
                node, "feedback", "replies_fields", "total_count", default=0
            ),
            "reactions": reactions,
            "reaction_count": self._safe_get(
                node, "feedback", "reactors", "count_reduced", default=0
            ),
        }

    # ==================================================================
    # PARSEO DE TRÁFICO (COMENTARIOS DINÁMICOS)
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece los comentarios con datos del tráfico GraphQL.

        Busca la operación ``CommentsListComponentsPaginationQuery`` en el
        tráfico capturado y añade los comentarios paginados a ``self.result``.
        """
        comments_responses = self._get_graphql_response_by_operation(
            "CommentsListComponentsPaginationQuery"
        )

        for response in comments_responses:
            edges = self._safe_get(
                response,
                "data", "node",
                "comment_rendering_instance_for_feed_location",
                "comments", "edges",
            )
            if not edges:
                continue
            for edge in edges:
                node = edge.get("node", {})
                if node:
                    self.result["comments"].append(self._build_comment_dict(node))

        logger.debug(
            "Tráfico GraphQL procesado: %d responses de paginación de comentarios.",
            len(comments_responses),
        )