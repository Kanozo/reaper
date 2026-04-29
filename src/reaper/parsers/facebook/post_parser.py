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
    # FALLBACK: ATTACHMENTS VÍA DFS
    # ==================================================================

    def _extract_attachments_fallback(
        self,
        search_root: dict,
    ) -> list[dict[str, Any]]:
        """Extrae adjuntos buscando nodos ``StoryAttachment`` via DFS.

        Se invoca únicamente cuando ``_extract_attachments_common`` devuelve
        una lista vacía. Busca nodos con ``__typename == "StoryAttachment"``
        que tengan campo ``target`` (versión más completa del nodo) para
        evitar duplicados y luego normaliza el media encontrado en
        ``styles.attachment.media``.

        Soporta los estilos: ``photo``, ``video``, ``link`` y ``album``
        (galería con ``all_subattachments``).

        La separación entre adjuntos del post principal y del post original
        (reshare) se resuelve **estructuralmente**: el caller pasa el subtree
        correcto como ``search_root``, en lugar de filtrar por metadatos como
        ``creation_story.id`` (que no es un discriminador fiable de propiedad).

        - Main post  → pasar ``comet_sections.content.story``.
        - Original post → pasar ``story["attached_story"]``.

        Args:
            search_root: Subtree del JSON en el que se realizará la búsqueda
                DFS. Debe ser el nodo raíz del contexto cuyos adjuntos se
                quieren extraer.

        Returns:
            Lista de dicts normalizados, uno por adjunto encontrado.
            Lista vacía si no se encuentra ningún nodo válido.
        """
        # Usar solo nodos con 'target': son la versión canónica (incluyen
        # all_subattachments y media top-level). Los nodos sin 'target' son
        # duplicados parciales del mismo StoryAttachment.
        raw_nodes: list[dict] = self._find_all_nodes(
            search_root,
            condition=lambda n: (
                n.get("__typename") == "StoryAttachment"
                and "target" in n
                and "styles" in n
            ),
        )

        if not raw_nodes:
            logger.debug("Fallback attachments: no se encontraron nodos StoryAttachment con 'target'.")
            return []

        attachments: list[dict[str, Any]] = []
        seen_dedup_keys: set[str] = set()

        for node in raw_nodes:
            dedup_key: str | None = node.get("deduplication_key")
            if dedup_key:
                if dedup_key in seen_dedup_keys:
                    continue
                seen_dedup_keys.add(dedup_key)

            style_list: list[str] = node.get("style_list") or []
            primary_style: str = style_list[0] if style_list else "unknown"

            styles: dict = node.get("styles") or {}
            style_typename: str = styles.get("__typename") or ""
            attachment_data: dict = styles.get("attachment") or {}
            media: dict = attachment_data.get("media") or {}

            built = self._build_attachment_from_media(
                media=media,
                style_typename=style_typename,
                primary_style=primary_style,
                attachment_data=attachment_data,
            )

            # Galería: si hay subattachments, los anexamos como items del álbum.
            sub_nodes: list[dict] = self._safe_get(
                node, "all_subattachments", "nodes", default=[]
            )
            if sub_nodes:
                built["type"] = "album"
                built["items"] = [
                    self._build_attachment_from_media(
                        media=sub.get("media") or {},
                        style_typename="",
                        primary_style="photo",
                        attachment_data={},
                    )
                    for sub in sub_nodes
                    if sub.get("media")
                ]

            attachments.append(built)

        logger.debug(
            "Fallback attachments: %d adjuntos extraídos via DFS.", len(attachments)
        )
        return attachments

    def _build_attachment_from_media(
        self,
        media: dict,
        style_typename: str,
        primary_style: str,
        attachment_data: dict,
    ) -> dict[str, Any]:
        """Normaliza un nodo ``media`` en un dict de adjunto canónico.

        Args:
            media: Nodo ``media`` extraído de ``styles.attachment.media``.
            style_typename: ``__typename`` del renderer de estilo
                (``StoryAttachmentPhotoStyleRenderer``, etc.).
            primary_style: Primer elemento de ``style_list`` del nodo padre.
            attachment_data: Dict ``styles.attachment`` completo (para links).

        Returns:
            Dict normalizado con al menos los campos ``type`` e ``id``.
        """
        media_typename: str = media.get("__typename") or ""
        is_photo = "Photo" in style_typename or "Photo" in media_typename or primary_style == "photo"
        is_video = "Video" in style_typename or "Video" in media_typename or primary_style == "video"
        is_link  = "Link"  in style_typename or primary_style == "link"

        if is_photo:
            photo_image: dict = media.get("photo_image") or {}
            return {
                "type": media.get("__typename") or "Photo",
                "id": media.get("id"),
                "url": photo_image.get("uri"),
                "caption": media.get("accessibility_caption", ""),
            }

        if is_video:
            thumbnail: dict = media.get("thumbnailImage") or {}
            return {
                "type": media.get("__typename") or "Video",
                "id": media.get("id"),
                "url": self._safe_get(
                    media, "videoDeliveryLegacyFields", "browser_native_sd_url", default=""
                ),
                "thumbnail_url": thumbnail.get("uri", ""),
                "caption": media.get("video_available_captions_locales"),
            }

        if is_link:
            return {
                "type": "link",
                "url": self._safe_get(attachment_data, "url"),
                "title": self._safe_get(attachment_data, "title", "text"),
                "description": self._safe_get(attachment_data, "description", "text"),
                "caption": self._safe_get(media, "accessibility_caption", default=""),
            }

        # Tipo desconocido: devolver lo mínimo disponible.
        return {
            "type": primary_style or "unknown",
            "id": media.get("id"),
            "url": media.get("url"),
            "caption": "",
        }

    # ==================================================================
    # FALLBACK: ORIGINAL POST VÍA ATTACHED_STORY COMPUESTO
    # ==================================================================

    def _extract_original_post_fallback(
        self, story: dict
    ) -> dict[str, Any] | None:
        """Extrae el post original combinando las dos ramas de attached_story.

        Facebook dispersa los datos del post reshareado en dos ubicaciones:

        - ``story["attached_story"]`` (top-level): contiene ``permalink_url``,
          ``feedback`` y, dentro de ``comet_sections.context_layout.story``,
          los actores completos y el ``creation_time``.
        - ``story.comet_sections.content.story.attached_story``: contiene
          el texto del mensaje y el ``post_id``.

        Este método los combina para construir un dict canónico equivalente
        al que devolvería ``_extract_original_post_common`` en condiciones
        normales.

        Args:
            story: Nodo Story principal del post.

        Returns:
            Dict con los datos del post original, o None si ninguna de
            las dos ramas contiene datos útiles.
        """
        top_attached: dict = story.get("attached_story") or {}
        content_attached: dict = (
            self._safe_get(
                story, "comet_sections", "content", "story", "attached_story"
            )
            or {}
        )

        if not top_attached and not content_attached:
            logger.debug("Fallback original_post: no se encontró ningún attached_story.")
            return None

        # ── Texto ────────────────────────────────────────────────────────
        text: str = (
            self._safe_get(
                content_attached, "comet_sections", "message",
                "story", "message", "text", default="",
            )
            or self._safe_get(
                content_attached, "comet_sections", "message_container",
                "story", "message", "text", default="",
            )
            or self._safe_get(content_attached, "message", "text", default="")
            or ""
        )

        # ── Autor ────────────────────────────────────────────────────────
        # La rama top_attached tiene actores con info completa (nombre, avatar).
        actor_from_top: dict = (
            self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "actor_photo", "story",
                "actors", default=[{}],
            )[0]
            if isinstance(
                self._safe_get(
                    top_attached,
                    "comet_sections", "context_layout", "story",
                    "comet_sections", "actor_photo", "story",
                    "actors", default=None,
                ),
                list,
            )
            else {}
        )
        # Fallback: actores directos en content_attached
        actor_from_content: dict = (
            content_attached.get("actors") or [{}]
        )[0]

        actor: dict = actor_from_top or actor_from_content

        author: dict[str, Any] = {
            "id": actor.get("id"),
            "name": actor.get("name") or actor.get("__typename"),
            "url": actor.get("url") or actor.get("profile_url"),
            "avatar": self._safe_get(actor, "profile_picture", "uri", default=""),
            "is_verified": actor.get("is_verified", False),
            "work_info": actor.get("work_info", ""),
        }

        # ── Timestamp ────────────────────────────────────────────────────
        # El creation_time vive en metadata[0].story.creation_time dentro
        # del context_layout del top_attached.
        metadata_list: list = (
            self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "metadata",
                default=[],
            )
            or []
        )
        creation_time: Any = None
        for meta_item in metadata_list:
            creation_time = self._safe_get(meta_item, "story", "creation_time")
            if creation_time:
                break

        # ── ID y permalink ───────────────────────────────────────────────
        post_id: str = (
            content_attached.get("post_id")
            or top_attached.get("id")
            or ""
        )
        permalink_url: str = (
            top_attached.get("permalink_url")
            or self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "metadata", default=[{}],
            )[0] and self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "metadata", default=[{}],
            )[0].get("story", {}).get("url", "")
            or ""
        )

        # ── Attachments del post original ────────────────────────────────
        # Buscamos únicamente dentro del subtree top_attached (story["attached_story"]).
        # Esto garantiza separación estructural: solo se encontrarán adjuntos
        # que pertenecen al post original, nunca los del post principal.
        original_attachments: list[dict] = (
            self._extract_attachments_fallback(top_attached)
            if top_attached else []
        )

        result: dict[str, Any] = {
            "id": post_id,
            "text": text,
            "permalink_url": permalink_url,
            "posted_at": self._parse_timestamp(creation_time),
            "author": author,
            "attachments": original_attachments,
        }

        logger.debug(
            "Fallback original_post: extraído — author=%s, text_len=%d, attachments=%d",
            author.get("name"),
            len(text),
            len(original_attachments),
        )
        return result

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