import base64
from reaper.utils.logger import get_logger
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.utils import SocialMediaParser
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

logger = get_logger(__name__)

# Patrones de operaciones GraphQL relevantes para Reels
_REEL_GRAPHQL_OPERATIONS = ["Reel", "ReelMetadata", "ShortsVideo"]


class ReelParser(FacebookContentParser):
    """Parser para Reels de Facebook.

    Extrae el reel principal de la URL y el feed de reels relacionados
    visibles en la misma página. Las métricas de engagement se obtienen
    de los nodos ``fb_reel_react_button`` y se cachean por post_id.

    Attributes:
        _all_stories: Lista de nodos Story/creation_story extraídos.
            El primero es el reel principal; el resto forman el feed.
        _reaction_cache: Mapa ``post_id → reaction_count``.
        _comments_count_cache: Mapa ``post_id → comments_count``.
        _share_count_cache: Mapa ``post_id → share_count``.
        result: Diccionario acumulador con los datos extraídos.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de Reels.

        Args:
            html_content: HTML completo renderizado de la página.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self._all_stories: list[dict] = []
        self._reaction_cache: dict[str, int] = {}
        self._comments_count_cache: dict[str, int] = {}
        self._share_count_cache: dict[str, int] = {}
        self.result.update({
            "__typename": "facebook_reel",
            "feed": [],
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el proceso completo de extracción del reel.

        Flujo de extracción:
            1. Extraer bloques JSON del HTML.
            2. Localizar todos los nodos Story/creation_story.
            3. Construir caché de métricas de engagement.
            4. Extraer el reel principal (``_all_stories[0]``).
            5. Extraer el feed de reels relacionados.
            6. Enriquecer con tráfico GraphQL.

        Returns:
            Diccionario con el reel principal y el feed de reels
            relacionados. Si no se encuentra ningún Story, retorna
            con ``result["error"]`` informando el fallo.
        """
        logger.info("Iniciando extracción de REEL...")

        blocks = self._extract_json_blocks()

        if not self._locate_all_stories(blocks):
            self.result["error"] = "No se encontró ningún nodo Story con vídeo."
            logger.warning("ReelParser: no se encontraron Stories en %s", self.final_url)
            return self.result

        self.result["raw_data_available"] = True
        self._build_reaction_cache(blocks)

        try:
            # Reel principal
            main_data = self._build_reel_dict(self._all_stories[0])
            self.result.update(main_data)

            # Feed de reels relacionados
            for story in self._all_stories[1:]:
                feed_entry = self._build_reel_dict(story)
                feed_entry["__typename"] = "feed_facebook_reel"
                self.result["feed"].append(feed_entry)

            self._parse_traffic()
            logger.debug(
                "Reel parseado correctamente. Feed: %d entradas.",
                len(self.result["feed"]),
            )

        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando reel: %s", exc)

        return self.result

    # ==================================================================
    # LOCALIZACIÓN DE STORIES
    # ==================================================================

    def _locate_all_stories(self, blocks: list[dict]) -> bool:
        """Localiza y acumula todos los nodos Story/creation_story.

        Usa dos estrategias complementarias:
        1. Busca nodos ``creation_story`` directos (reel principal).
        2. Busca nodos Story con ``feedback`` y contexto de vídeo
           (``short_form_video_context`` o ``post_id``).

        Desuplica por ``post_id`` para evitar procesar el mismo reel dos veces.

        Args:
            blocks: Lista de bloques JSON extraídos del HTML.

        Returns:
            True si se encontró al menos un Story/creation_story.
        """
        seen_ids: set[str] = set()

        for block in blocks:
            # Estrategia 1: nodo creation_story (reel principal)
            creation_story = self._recursive_search(
                block,
                condition=lambda n: (
                    "creation_story" in n
                    and isinstance(n.get("creation_story"), dict)
                ),
                extract=lambda n: n["creation_story"],
            )
            if creation_story:
                self._all_stories.append(creation_story)
                logger.debug("Nodo creation_story localizado.")

            # Estrategia 2: nodos Story del feed
            feed_stories = self._find_all_nodes(
                block,
                condition=lambda n: (
                    n.get("__typename") == "Story"
                    and "feedback" in n
                    and (
                        "short_form_video_context" in n
                        or n.get("post_id")
                    )
                ),
            )
            for story in feed_stories:
                post_id = str(story.get("post_id", ""))
                if post_id and post_id not in seen_ids:
                    seen_ids.add(post_id)
                    self._all_stories.append(story)

        found = bool(self._all_stories)
        if not found:
            logger.debug("No se encontraron nodos Story con vídeo.")
        return found

    # ==================================================================
    # CACHÉ DE MÉTRICAS DE ENGAGEMENT
    # ==================================================================

    def _build_reaction_cache(self, blocks: list[dict]) -> None:
        """Construye cachés de métricas de engagement por post_id.

        Busca nodos ``fb_reel_react_button`` en los bloques JSON y extrae
        reacciones, shares y comentarios, usando ``SocialMediaParser``
        para normalizar los valores.

        Rellena ``_reaction_cache``, ``_share_count_cache`` y
        ``_comments_count_cache``.

        Args:
            blocks: Lista de bloques JSON extraídos del HTML.
        """
        metrics_parser = SocialMediaParser()

        for block in blocks:
            react_nodes = self._find_all_nodes(
                block,
                condition=lambda n: (
                    "fb_reel_react_button" in n
                    and isinstance(n.get("fb_reel_react_button"), dict)
                ),
            )
            for node in react_nodes:
                btn = node.get("fb_reel_react_button", {}) or {}
                feedback = self._safe_get(btn, "story", "feedback") or {}
                feedback_id_b64 = feedback.get("id", "")

                post_id = (
                    node.get("post_id")
                    if node.get("id")
                    else self._decode_feedback_id(feedback_id_b64)
                )
                if not post_id:
                    continue

                metrics = metrics_parser.parse_engagement_metrics(
                    reaction_count=self._safe_get(
                        feedback, "likers", "count", default=0
                    ),
                    share_count=self._safe_get(
                        node, "feedback", "share_count_reduced"
                    ),
                    comment_count=self._safe_get(
                        node, "feedback", "total_comment_count"
                    ),
                )

                # Solo cachear si la clave no existe (primer valor encontrado)
                self._reaction_cache.setdefault(
                    post_id, metrics.get("reaction_count", 0)
                )
                self._share_count_cache.setdefault(
                    post_id, metrics.get("share_count", 0)
                )
                self._comments_count_cache.setdefault(
                    post_id, metrics.get("comment_count", 0)
                )

    # ==================================================================
    # CONSTRUCCIÓN DE DICT DE REEL
    # ==================================================================

    def _build_reel_dict(self, story: dict) -> dict[str, Any]:
        """Construye el diccionario normalizado de un reel.

        Combina datos del nodo Story con métricas de la caché y
        métodos compartidos de ``FacebookContentParser``.

        Args:
            story: Nodo Story o creation_story del reel.

        Returns:
            Dict normalizado con todos los campos del reel:
            ``id``, ``posted_at``, ``permalink_url``, ``author``,
            ``text``, ``hashtags``, ``mentions``, ``attachments``,
            ``reaction_count``, ``comments_count``, ``share_count``,
            ``privacy``, ``is_ad``, ``group``, ``original_post``.
        """
        sfc = story.get("short_form_video_context") or {}
        msg = story.get("message") or {}
        post_id = str(story.get("post_id", ""))

        # Métodos compartidos de FacebookContentParser
        author = self._extract_author_common(story)
        group = self._extract_group_common(story)
        original_post = self._extract_original_post_common(story)
        attachments = self._extract_attachments_common(
            story, include_video_metadata=True
        )

        hashtags, mentions = self._parse_ranges(msg.get("ranges", []))

        # Permalink: intentar varias rutas
        permalink_url = (
            sfc.get("shareable_url")
            or self._safe_get(sfc, "if_should_change_url_for_reels", "shareable_url")
            or self.final_url
        )

        return {
            "id": post_id,
            "posted_at": self._parse_timestamp(story.get("creation_time")),
            "permalink_url": permalink_url,
            "author": author,
            "text": msg.get("text", ""),
            "hashtags": hashtags,
            "mentions": mentions,
            "attachments": attachments,
            "reaction_count": self._reaction_cache.get(post_id, 0),
            "comments_count": self._comments_count_cache.get(post_id, 0),
            "share_count": self._share_count_cache.get(post_id, 0),
            "privacy": self._safe_get(
                story, "privacy_scope", "label", default="Unknown"
            ),
            "is_ad": self._safe_get(
                story, "transparency_ad_info", "should_display_ad_info",
                default=False,
            ),
            "group": group,
            "original_post": original_post,
        }

    def _build_video_meta(
        self,
        video_node: dict,
        playback: dict,
        story: dict,
    ) -> dict[str, Any]:
        """Construye metadatos técnicos del vídeo del reel.

        Método auxiliar disponible para uso futuro o depuración.
        Actualmente no se llama en el flujo principal (ver ``_build_reel_dict``).

        Args:
            video_node: Nodo de vídeo desde ``short_form_video_context.video``.
            playback: Nodo de reproducción desde
                ``short_form_video_context.playback_video``.
            story: Nodo Story completo (para detectar Meta AI).

        Returns:
            Dict con metadatos técnicos del vídeo.
        """
        is_meta_ai = any(
            att.get("media", {}).get("is_made_with_meta_ai_app") is not None
            and bool(att.get("media", {}).get("is_made_with_meta_ai_app"))
            for att in story.get("attachments", [])
        )

        return {
            "duration_s": playback.get("length_in_second"),
            "width": playback.get("width"),
            "height": playback.get("height"),
            "thumbnail_url": self._safe_get(
                playback, "thumbnailImage", "uri", default=""
            ),
            "audio_availability": video_node.get("audio_availability"),
            "embeddable": video_node.get("embeddable"),
            "is_looping": playback.get("is_looping", False),
            "is_made_with_meta_ai": is_meta_ai,
        }

    # ==================================================================
    # PARSEO DE TRÁFICO
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result`` con metadatos del tráfico GraphQL de Reels."""
        self._parse_facebook_traffic(operation_patterns=_REEL_GRAPHQL_OPERATIONS)

    # ==================================================================
    # UTILIDADES ESTÁTICAS
    # ==================================================================

    @staticmethod
    def _decode_feedback_id(b64_id: str) -> str | None:
        """Decodifica un feedback_id en base64 para obtener el post_id.

        El feedback_id de Facebook tiene el formato base64 de
        ``"feedback:<post_id>"``.

        Args:
            b64_id: ID de feedback en base64.

        Returns:
            El post_id como string si la decodificación es exitosa,
            None si el formato es inválido.

        Example:
            >>> ReelParser._decode_feedback_id("ZmVlZGJhY2s6MTIzNDU2")
            '123456'
        """
        if not b64_id:
            return None
        try:
            decoded = base64.b64decode(b64_id).decode("utf-8")
            if ":" in decoded:
                return decoded.split(":", 1)[1]
        except Exception as exc:
            logger.debug("No se pudo decodificar feedback_id '%s': %s", b64_id, exc)
        return None