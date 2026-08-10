import traceback
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

_POSTS_GRAPHQL_OPERATIONS = [
    "GroupsCometLoggedOutPermalinkFeedPaginationQuery",
    "GroupsCometLoggedOutPermalinkFeedPaginat"
]


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
            "feed": []
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
            self.result |= self._extract_basic_info()
            self.result["author"] = self._extract_author_common(self._story)
            self.result |= self._extract_content()
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

            # --- Original post ---
            has_group = self._extract_group_info()
            has_attached_story = bool(
                self._story.get("attached_story")
                or self._safe_get(
                    self._story, "comet_sections", "content", "story", "attached_story"
                )
            )

            if has_group or has_attached_story:
                original_post = self._extract_original_post_common(self._story)

                if not original_post:
                    logger.debug(
                        "_extract_original_post_common vacío; intentando fallback."
                    )
                    original_post = self._extract_original_post_fallback(self._story)

                elif not original_post.get("attachments"):
                    # original_post resuelto pero con attachments vacío.
                    # Ocurre cuando _extract_shared_post recibió un álbum y
                    # story_att no era alcanzable (la ruta profunda falló),
                    # por lo que el Cambio 1 no tuvo oportunidad de ejecutarse.
                    # Safety-net: DFS sobre attached_story top-level con
                    # _extract_attachments_fallback, que localiza nodos
                    # StoryAttachment con all_subattachments independiente
                    # de la profundidad exacta del árbol.
                    top_attached: dict = self._story.get("attached_story") or {}
                    if top_attached:
                        logger.debug(
                            "original_post sin attachments; reintentando extracción "
                            "de adjuntos via DFS sobre attached_story top-level."
                        )
                        retried_atts = self._extract_attachments_fallback(top_attached)
                        if retried_atts:
                            original_post["attachments"] = retried_atts
                            logger.debug(
                                "Retry DFS exitoso: %d adjunto(s) recuperado(s).",
                                len(retried_atts),
                            )

                self.result["original_post"] = original_post

            self.result["is_sponsored"] = self._story.get("sponsored_data") is not None
            self._parse_traffic()

            logger.debug("Post regular parseado correctamente.")

        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando post: %s", exc)
            if self.debug:
                traceback.print_exc()

        return self.result
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
    def _extract_group_info(self) -> bool:
        """Extrae información del grupo si el post pertenece a uno.

        Delega en ``_extract_group_common`` y actualiza
        ``self.result["group"]`` in-place.

        Returns:
            True si el post pertenece a un grupo, False si no.
        """
        self.result["group"] = self._extract_group_common(self._story)
        return self.result["group"] is not None
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
                response[0],
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


        if not self._has_graphql_traffic():
            logger.debug("_parse_traffic: sin tráfico GraphQL.")
            return

        seen_ids: set[str] = {
            str(p.get("post_id") or "")
            for p in self.result["feed"]
            if p.get("post_id")
        }

        added = 0
        for operation in _POSTS_GRAPHQL_OPERATIONS:
            bodies = self._get_graphql_response_by_operation(operation)
            if not bodies:
                continue
            logger.debug("_parse_traffic: %d bodies para '%s'", len(bodies), operation)

            for body in bodies:
                for fragment in self.traffic.normalize_body(body):
                    added += self._process_traffic_fragment(fragment, seen_ids)

        logger.debug("_parse_traffic: %d posts añadidos desde tráfico.", added)

