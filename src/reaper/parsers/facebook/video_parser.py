from datetime import datetime
from reaper.utils.logger import get_logger
import re
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

from reaper.utils import srt_to_dict, get_text_from_url, SocialMediaParser

logger = get_logger(__name__)

# Patrón para extraer view count y reaction count del OG title
# Ejemplo: "419K views · 12K reactions | Amelia Calzadilla on Reels"
_OG_TITLE_PATTERN = re.compile(
    r"([\d,\.]+[KkMmBb]?)\s*views?\s*[·•]\s*([\d,\.]+[KkMmBb]?)\s*reactions?",
    re.IGNORECASE,
)

# Patrones GraphQL relevantes para vídeos nativos de Facebook
_VIDEO_GRAPHQL_OPERATIONS = [
    "CometVideoHomeQuery",
    "VideoPlayerQuery",
    "UFICommentsQuery",
    "VideoHomeWatchFeedQuery",
]


class VideoParser(FacebookContentParser):
    """Parser para vídeos nativos de Facebook (/videos/<id>/).

    Extrae el vídeo principal con todos sus metadatos técnicos y de
    engagement, más el feed lateral de vídeos relacionados del mismo autor.

    Attributes:
        _video_id: ID numérico del vídeo extraído de la URL original.
        _node_technical: Nodo del reproductor con URLs y metadatos técnicos
            (videoDeliveryLegacyFields). Bloque 22 en el HTML analizado.
        _node_story: Nodo con creation_story, autor y privacidad.
            Bloque 42 en el HTML analizado.
        _node_feedback: Nodo de feedback con métricas de visualización y
            reacciones (video_view_count_renderer). Bloque 56.
        _node_durations: Nodo con broadcast_duration y playable_duration.
            Bloque 67.
        result: Diccionario acumulador con todos los datos extraídos.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de vídeos nativos.

        Args:
            html_content: HTML completo renderizado de la página del vídeo.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)

        self._video_id: str = self._extract_video_id_from_url(original_url)
        self._node_technical: dict | None = None
        self._node_story: dict | None = None
        self._node_feedback: dict | None = None
        self._node_durations: dict | None = None

        self.result.update({
            "__typename": "facebook_video",
            "feed": [],
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el proceso completo de extracción del vídeo.

        Flujo de extracción:
            1. Extraer bloques JSON del HTML.
            2. Localizar los cuatro nodos principales.
            3. Extraer campos en orden creciente de dependencia.
            4. Extraer el feed de vídeos relacionados.
            5. Enriquecer con tráfico GraphQL.

        Returns:
            Diccionario con todos los campos del vídeo. Si falla algún
            paso crítico, ``result["error"]`` contendrá el mensaje de error.
        """
        logger.info("Iniciando extracción de VÍDEO NATIVO (id=%s)...", self._video_id)

        self._blocks = self._extract_json_blocks()

        if not self._locate_video_nodes():
            self.result["error"] = "No se encontró ningún nodo de vídeo válido."
            logger.warning(
                "VideoParser: no se encontraron nodos en %s", self.final_url
            )
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_basic_info()
            self._extract_author()
            self._extract_video_content()
            self._extract_technical_metadata()
            self._extract_engagement_metrics()
            self._extract_related_feed()
            self._parse_traffic()
            logger.debug(
                "Vídeo parseado correctamente. Feed: %d vídeos relacionados.",
                len(self.result["feed"]),
            )

        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando vídeo: %s", exc)
            if self.debug:
                import traceback
                traceback.print_exc()

        return self.result

    # ==================================================================
    # LOCALIZACIÓN DE NODOS
    # ==================================================================

    def _locate_video_nodes(self) -> bool:
        """Localiza y asigna los cuatro nodos de datos del vídeo.

        Estrategia por nodo:

        - **technical**: Primer nodo ``Video`` que contiene
          ``videoDeliveryLegacyFields`` (reproductor con URLs de stream).
        - **story**: Primer nodo ``Video`` que contiene ``creation_story``
          y ``owner`` (metadatos de autor y story).
        - **feedback**: Primer nodo con ``video_view_count_renderer`` y
          ``top_reactions`` (métricas de engagement).
        - **durations**: Primer nodo ``Video`` con ``broadcast_duration``
          y ``playable_duration``.

        Returns:
            True si se encontró al menos el nodo técnico o el de story.
        """
        for block in self._blocks:
            # Nodo técnico del reproductor (URLs SD/HD/DASH)
            if not self._node_technical:
                self._node_technical = self._recursive_search(
                    block,
                    condition=lambda n: (
                        n.get("__typename") == "Video"
                        and "videoDeliveryLegacyFields" in n
                        and "playable_duration_in_ms" in n
                    ),
                )
                if self._node_technical:
                    logger.debug("Nodo técnico localizado (id=%s).", self._node_technical.get("id"))

            # Nodo de autor y creation_story
            if not self._node_story:
                self._node_story = self._recursive_search(
                    block,
                    condition=lambda n: (
                        n.get("__typename") == "Video"
                        and "creation_story" in n
                        and "owner" in n
                        and isinstance(n.get("creation_story"), dict)
                    ),
                )
                if self._node_story:
                    logger.debug("Nodo story localizado (id=%s).", self._node_story.get("id"))

            # Nodo de feedback con métricas de visualización
            if not self._node_feedback:
                self._node_feedback = self._recursive_search(
                    block,
                    condition=lambda n: (
                        "video_view_count_renderer" in n
                        and "top_reactions" in n
                        and isinstance(n.get("video_view_count_renderer"), dict)
                    ),
                )
                if self._node_feedback:
                    logger.debug("Nodo de feedback/métricas localizado.")

            # Nodo de duraciones (broadcast_duration en segundos)
            if not self._node_durations:
                self._node_durations = self._recursive_search(
                    block,
                    condition=lambda n: (
                        n.get("__typename") == "Video"
                        and "broadcast_duration" in n
                        and "playable_duration" in n
                        and n.get("id") == self._video_id
                    ),
                )
                if self._node_durations:
                    logger.debug("Nodo de duraciones localizado.")

        found = self._node_technical is not None or self._node_story is not None
        if not found:
            logger.debug("No se encontraron nodos de vídeo en los %d bloques JSON.", len(self._blocks))
        return found

    # ==================================================================
    # EXTRACCIÓN DE CAMPOS BÁSICOS
    # ==================================================================

    def _extract_basic_info(self) -> None:
        """Extrae id, URL permanente y timestamps del vídeo.

        Combina datos de ``_node_technical`` y ``_node_story`` para
        garantizar la mayor cobertura posible.

        Actualiza ``self.result`` con:
            - ``id``: ID numérico del vídeo.
            - ``permalink_url``: URL pública permanente.
            - ``posted_at``: datetime de publicación (Unix → datetime).
            - ``unpublished_content_type``: estado de publicación.
            - ``is_video_broadcast``: True si fue una transmisión en vivo.
        """
        # ID: prioridad nodo técnico → story → extraído de URL
        self.result["id"] = (
            self._safe_get(self._node_technical, "id")
            or self._safe_get(self._node_story, "id")
            or self._video_id
        )
        self.result["permalink_url"] = (
            self._safe_get(self._node_technical, "permalink_url")
            or self._safe_get(
                self._node_story, "creation_story", "comet_sections",
                "metadata", default=[]
            ) and self._extract_permalink_from_metadata()
            or self.final_url
        )
        creation_time = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections",
            "metadata", default=[]
        )
        self.result["posted_at"] = self._extract_creation_time_from_metadata(creation_time)
        self.result["is_video_broadcast"] = bool(
            self._safe_get(self._node_technical, "is_video_broadcast", default=False)
        )
        self.result["is_live_streaming"] = bool(
            self._safe_get(self._node_technical, "is_live_streaming", default=False)
        )
        self.result["broadcast_id"] = self._safe_get(self._node_technical, "broadcast_id")

    def _extract_permalink_from_metadata(self) -> str | None:
        """Extrae la URL permanente desde el array ``metadata`` de creation_story.

        Returns:
            URL como string, o None si no se encuentra.
        """
        for meta in self._safe_get(
            self._node_story, "creation_story", "comet_sections", "metadata", default=[]
        ):
            url = self._safe_get(meta, "story", "url")
            if url:
                return url
        return None

    def _extract_creation_time_from_metadata(self, metadata_list: list) -> datetime | None:
        """Extrae el timestamp de publicación desde el array de metadata.

        Args:
            metadata_list: Lista de secciones de metadata del story.

        Returns:
            datetime de publicación, o None.
        """
        if not isinstance(metadata_list, list):
            return None
        for meta in metadata_list:
            ts = self._safe_get(meta, "story", "creation_time")
            if ts:
                return self._parse_timestamp(ts)
        # Fallback: publish_time del nodo técnico
        return self._parse_timestamp(self._safe_get(self._node_technical, "publish_time"))

    # ==================================================================
    # EXTRACCIÓN DE AUTOR
    # ==================================================================

    def _extract_author(self) -> None:
        """Extrae información del autor del vídeo.

        Combina tres fuentes en orden de prioridad:
        1. ``creation_story.comet_sections.actor_photo.story.actors[0]``
           (datos completos del actor con avatar y delegate_page).
        2. ``creation_story.comet_sections.title.story.actors[0]``
           (datos del actor con is_verified).
        3. ``owner`` del nodo story (datos básicos del propietario).

        Actualiza ``self.result["author"]`` in-place.
        """
        # --- Fuente 1: actor_photo (avatar, delegate_page, is_verified) ---
        actor_photo_actors = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "actor_photo", "story", "actors",
        )
        if actor_photo_actors and isinstance(actor_photo_actors, list):
            actor = actor_photo_actors[0]
            self.result["author"] = self._build_author_from_actor(actor)
            return

        # --- Fuente 2: title.story.actors (tiene is_verified) ---
        title_actors = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "title", "story", "actors",
        )
        if title_actors and isinstance(title_actors, list):
            actor = title_actors[0]
            # Buscar is_verified en ranges del title
            is_verified = self._extract_is_verified_from_title_ranges()
            author = self._build_author_from_actor(actor)
            if is_verified is not None:
                author["is_verified"] = is_verified
            self.result["author"] = author
            return

        # --- Fuente 3: owner del nodo story ---
        owner = self._safe_get(self._node_story, "owner") or {}
        owner_id = owner.get("id", "unknown")
        self.result["author"] = {
            "id": owner_id,
            "name": owner.get("name", "Unknown"),
            "url": owner.get("url") or f"https://www.facebook.com/profile.php?id={owner_id}",
            "avatar": self._safe_get(owner, "profile_picture", "uri", default=""),
            "is_verified": False,
            "gender": owner.get("gender", ""),
            "delegate_page": None,
            "work_info": None,
        }

    def _build_author_from_actor(self, actor: dict) -> dict[str, Any]:
        """Construye el dict normalizado de autor desde un nodo actor.

        Args:
            actor: Nodo actor extraído de ``actors[]``.

        Returns:
            Dict con ``id``, ``name``, ``url``, ``avatar``, ``is_verified``,
            ``gender``, ``delegate_page``, ``work_info``.
        """
        actor_id = actor.get("id", "unknown")
        delegate_page = actor.get("delegate_page") or {}

        return {
            "id": actor_id,
            "name": actor.get("name", "Unknown"),
            "url": (
                actor.get("url")
                or actor.get("profile_url")
                or f"https://www.facebook.com/profile.php?id={actor_id}"
            ),
            "avatar": self._safe_get(actor, "profile_picture", "uri", default=""),
            "is_verified": actor.get("is_verified", False),
            "gender": actor.get("gender", ""),
            "delegate_page": {
                "id": delegate_page.get("id"),
                "is_business_page_active": delegate_page.get("is_business_page_active"),
            } if delegate_page else None,
            "work_info": actor.get("work_info"),
        }

    def _extract_is_verified_from_title_ranges(self) -> bool | None:
        """Extrae el flag ``is_verified`` desde los ranges del título.

        Returns:
            True/False si se encontró en los ranges, None si no hay datos.
        """
        ranges = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "title", "story", "title", "ranges",
            default=[],
        )
        for range_item in ranges:
            entity = range_item.get("entity") or {}
            if "is_verified" in entity:
                return bool(entity["is_verified"])
        return None

    # ==================================================================
    # EXTRACCIÓN DE CONTENIDO DEL VÍDEO
    # ==================================================================

    def _extract_video_content(self) -> None:
        """Extrae título, descripción, privacidad y metadatos de contenido.

        Actualiza ``self.result`` con:
            - ``title``: Título del vídeo (puede estar vacío).
            - ``title_story``: Texto del story (ej. "was live").
            - ``privacy``: Nivel de privacidad (ej. "Public").
            - ``hashtags``: Lista de hashtags del título/descripción.
            - ``mentions``: Lista de menciones del título/descripción.
            - ``collaborators``: Colaboradores en el vídeo.
            - ``is_gaming_video``: True si es contenido de gaming.
            - ``is_podcast_video``: True si es un podcast.
        """
        # Título del vídeo (campo top-level del nodo story)
        title_node = self._safe_get(self._node_story, "title") or {}
        self.result["title"] = title_node.get("text", "") if isinstance(title_node, dict) else ""

        # Texto narrativo del story (ej. "Amelia Calzadilla was live.")
        title_story_text = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "title", "story", "title", "text",
            default="",
        )
        self.result["title_story"] = title_story_text

        # Hashtags y menciones desde los ranges del título del story
        title_ranges = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "title", "story", "title", "ranges",
            default=[],
        )
        hashtags, mentions = self._parse_ranges(title_ranges)
        self.result["hashtags"] = hashtags
        self.result["mentions"] = mentions

        # Privacidad
        self.result["privacy"] = self._extract_privacy_from_story()

        # Colaboradores
        collaborators_raw = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "title", "story", "collaborators",
            default=[],
        )
        self.result["collaborators"] = [
            {
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "url": c.get("url", ""),
            }
            for c in (collaborators_raw or [])
        ]

        # Flags de tipo de contenido
        self.result["is_gaming_video"] = bool(
            self._safe_get(self._node_story, "is_gaming_video", default=False)
        )
        self.result["is_podcast_video"] = bool(
            self._safe_get(self._node_technical, "is_podcast_video", default=False)
        )
        self.result["is_looping"] = bool(
            self._safe_get(self._node_technical, "is_looping", default=False)
        )
        self.result["is_spherical"] = bool(
            self._safe_get(self._node_technical, "is_spherical", default=False)
        )

        # Título del vídeo (campo top-level del nodo story)
        title_node = self._safe_get(self._node_story, "title") or {}
        self.result["title"] = title_node.get("text", "") if isinstance(title_node, dict) else ""

        # Fallback 1: si title sigue vacío, buscarlo en los bloques por id del vídeo
        # El nodo tiene estructura {"id":"<video_id>"}, "title":{"text":"..."}
        # sin __typename, por eso _node_story no lo captura
        if not self.result["title"]:
            for block in self._blocks:
                title_text = self._find_title_by_video_id(block, self._video_id)
                if title_text:
                    self.result["title"] = title_text
                    logger.debug("title extraído por fallback de id (blocks): %s", title_text[:60])
                    break

        # title_story — en este tipo de vídeo es null; fallback a message.text
        title_story_text = self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "title", "story", "title", "text",
            default="",
        )
        # Fallback 2: usar el texto del mensaje de la creation_story
        if not title_story_text:
            title_story_text = self._safe_get(
                self._node_story,
                "creation_story", "comet_sections", "message", "story", "message", "text",
                default="",
            )
        self.result["title_story"] = title_story_text

    def _extract_privacy_from_story(self) -> str:
        """Extrae el nivel de privacidad desde el array de metadata del story.

        Busca el item de tipo ``CometFeedStoryAudienceStrategy`` que contiene
        la descripción legible de la privacidad.

        Returns:
            String con el nivel de privacidad (ej. ``"Public"``),
            o ``"Unknown"`` si no se encuentra.
        """
        for meta in self._safe_get(
            self._node_story,
            "creation_story", "comet_sections", "metadata",
            default=[],
        ):
            if "Audience" in meta.get("__typename", ""):
                return self._safe_get(
                    meta, "story", "privacy_scope", "description", default="Unknown"
                )
        return "Unknown"

    # ==================================================================
    # METADATOS TÉCNICOS DEL VÍDEO
    # ==================================================================

    def _extract_technical_metadata(self) -> None:
        """Extrae metadatos técnicos del reproductor de vídeo.

        Procesa el nodo ``_node_technical`` para obtener URLs de reproducción
        (SD, HD, DASH), dimensiones, duración y metadatos de captions.

        Actualiza ``self.result`` con:
            - ``video``: Dict con todas las propiedades técnicas del vídeo.
            - ``thumbnail``: URL de la imagen de preview preferida.
        """
        # Thumbnail preferida
        self.result["thumbnail_url"] = (
            self._safe_get(self._node_technical, "preferred_thumbnail", "image", "uri", default="")
            or self._extract_og_image()
        )

        # Duración (en ms desde el nodo técnico; en segundos desde el nodo durations)
        playable_duration_ms = self._safe_get(
            self._node_technical, "playable_duration_in_ms"
        )

        # URLs de reproducción
        vdlf = self._safe_get(self._node_technical, "videoDeliveryLegacyFields") or {}

        captions_locales = self._safe_get(
                self._node_technical, "video_available_captions_locales", default=[]
            )

        captions = []
        for caption_item in captions_locales:
            if caption_item.get("locale") in {"en_US", "es_ES"} or caption_item.get("localized_language") in {"English", "Español"}:
                caption_item["captions_url"] = srt_to_dict(
                    get_text_from_url(caption_item.get("captions_url"))
                )
                captions.append(caption_item)

        self.result["video"] = {
            "id": self._safe_get(self._node_technical, "id", default=self._video_id),
            # Dimensiones
            "width": self._safe_get(self._node_technical, "width"),
            "height": self._safe_get(self._node_technical, "height"),
            # Duración en múltiples unidades para conveniencia del analista
            "duration_ms": playable_duration_ms,
            # URLs de reproducción (en orden de calidad descendente)
            "url_sd": vdlf.get("browser_native_sd_url", ""),

            # Captions/subtítulos
            "captions": captions,
            
            # Flags técnicos
            "is_looping": self.result.get("is_looping", False),
            "is_spherical": self.result.get("is_spherical", False)
        }

    def _extract_og_image(self) -> str:
        """Extrae la URL de imagen del meta tag OG como fallback.

        Returns:
            URL de la imagen OG, o cadena vacía si no se encuentra.
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.html_content, "html.parser")
        og_image = soup.find("meta", {"property": "og:image"})
        return og_image.get("content", "") if og_image else ""

    # ==================================================================
    # MÉTRICAS DE ENGAGEMENT
    # ==================================================================

    def _extract_engagement_metrics(self) -> None:
        """Extrae todas las métricas de engagement del vídeo.

        Combina datos de ``_node_feedback`` y metadatos OG para obtener:
        - Métricas de visualización (play_count vs video_view_count).
        - Reacciones por tipo.
        - Conteo de comentarios.

        Actualiza ``self.result`` con los campos de engagement.

        Nota para analistas:
            - ``play_count``: Total de veces que se inició el vídeo
              (incluye repeticiones del mismo usuario).
            - ``video_view_count``: Visualizaciones únicas estimadas
              (métrica más cercana a "alcance real").
            - ``video_post_view_count``: Visualizaciones del post que
              contiene el vídeo (puede diferir de views directas).
        """
        if not self._node_feedback:
            logger.warning("No se encontró nodo de feedback. Usando fallback de OG tags.")
            self._extract_engagement_from_og_fallback()
            return

        # Métricas de visualización desde video_view_count_renderer
        vvc = self._safe_get(
            self._node_feedback, "video_view_count_renderer", "feedback"
        ) or {}
        self.result["play_count"] = vvc.get("play_count", 0) or 0
        self.result["video_view_count"] = vvc.get("video_view_count", 0) or 0
        self.result["video_post_view_count"] = vvc.get("video_post_view_count", 0) or 0

        # Reacciones
        reaction_count_node = self._safe_get(
            self._node_feedback, "reaction_count"
        ) or {}
        self.result["reaction_count"] = reaction_count_node.get("count", 0) or 0

        # Top reactions por tipo
        self.result["reactions"] = [
            {
                "id": self._safe_get(edge, "node", "id"),
                "type": self._safe_get(edge, "node", "localized_name", default="Unknown"),
                "count": edge.get("reaction_count", 0)
            }
            for edge in self._safe_get(
                self._node_feedback, "top_reactions", "edges", default=[]
            )
            if self._safe_get(edge, "node")
        ]

        # Comentarios
        self.result["comments_count"] = (
            self._safe_get(
                self._node_feedback,
                "comment_rendering_instance", "comments", "total_count",
                default=0,
            )
            or self._safe_get(self._node_feedback, "total_comment_count", default=0)
            or 0
        )

    def _extract_engagement_from_og_fallback(self) -> None:
        """Extrae métricas básicas desde OG meta tags como fallback.

        Parsea el patrón "419K views · 12K reactions" del ``og:title``.
        Asigna 0 a todas las métricas que no se puedan extraer.
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.html_content, "html.parser")
        og_title_tag = soup.find("meta", {"property": "og:title"})
        og_title = og_title_tag.get("content", "") if og_title_tag else ""

        play_count_raw = 0
        reaction_count_raw = 0

        if og_title:
            match = _OG_TITLE_PATTERN.search(og_title)
            if match:
                play_count_raw = self._parse_human_number(match.group(1))
                reaction_count_raw = self._parse_human_number(match.group(2))

        self.result["play_count"] = play_count_raw
        self.result["video_view_count"] = 0
        self.result["video_post_view_count"] = 0
        self.result["should_show_play_count"] = False
        self.result["reaction_count"] = reaction_count_raw
        self.result["reactions"] = []
        self.result["comments_count"] = 0
        logger.debug("OG fallback: play_count=%d, reaction_count=%d", play_count_raw, reaction_count_raw)


    # ==================================================================
    # FEED DE VÍDEOS RELACIONADOS
    # ==================================================================

    def _extract_related_feed(self) -> None:
        """Extrae el feed lateral de vídeos relacionados del mismo autor.

        Los vídeos relacionados están en el nodo ``Video`` con campos
        ``play_count`` y ``canonical_uri_with_fallback`` (Block 60).
        Son otros vídeos del mismo autor mostrados en el panel lateral.

        Actualiza ``self.result["feed"]`` in-place con una lista de
        dicts normalizados por ``_build_feed_video_dict``.
        """
        for block in self._blocks:
            related_videos = self._find_all_nodes(
                block,
                condition=lambda n: (
                    n.get("__typename") == "Video"
                    and "play_count" in n
                    and "canonical_uri_with_fallback" in n
                    and n.get("id") != self._video_id  # Excluir el vídeo principal
                ),
            )
            for video_node in related_videos:
                # Deduplicar por id
                video_id = video_node.get("id", "")
                if not any(v.get("id") == video_id for v in self.result["feed"]):
                    self.result["feed"].append(self._build_feed_video_dict(video_node))

        logger.debug("Feed de vídeos relacionados: %d entradas.", len(self.result["feed"]))

    def _build_feed_video_dict(self, video_node: dict) -> dict[str, Any]:
        """Construye el diccionario normalizado de un vídeo del feed relacionado.

        Args:
            video_node: Nodo ``Video`` con ``play_count`` y
                ``canonical_uri_with_fallback``.

        Returns:
            Dict normalizado con todos los campos del vídeo relacionado.
        """
        feedback = video_node.get("feedback", {}) or {}
        owner = video_node.get("owner", {}) or {}
        owner_id = owner.get("id", "")

        # Descripción/título del vídeo
        savable_desc = video_node.get("savable_description") or {}
        title = savable_desc.get("text", "") if isinstance(savable_desc, dict) else ""

        # Top reactions del feed
        reactions = [
            {
                "id": self._safe_get(edge, "node", "id"),
                "type": self._safe_get(edge, "node", "localized_name", default="Unknown"),
                "count": edge.get("reaction_count", 0),
            }
            for edge in self._safe_get(
                feedback, "top_reactions", "edges", default=[]
            )
            if self._safe_get(edge, "node")
        ]

        # Duración desde el renderer de thumbnail (si disponible)
        duration_ms = self._safe_get(
            video_node,
            "video_thumbnail_overlays_renderer", "video", "playable_duration_in_ms",
        )
        social_metrics = SocialMediaParser()
        comments_count = social_metrics._parse_metric(feedback.get("comment_count_reduced", 0), "comment_count_reduced")

        return {
            "__typename": "feed_facebook_video",
            "id": video_node.get("id", ""),
            "title": title,
            "permalink_url": video_node.get("canonical_uri_with_fallback", ""),
            "thumbnail_url": self._safe_get(video_node, "image", "uri", default=""),
            "posted_at": self._parse_timestamp(video_node.get("created_time")),
            "duration_ms": duration_ms,
            # Métricas de visualización
            "play_count": video_node.get("play_count", 0) or 0,
            "post_play_count": video_node.get("post_play_count", 0) or 0,
            # Engagement
            "reaction_count": self._safe_get(feedback, "reaction_count", "count", default=0),
            "reactions": reactions,
            "comments_count": comments_count,
            # Estado
            "is_live_streaming": video_node.get("is_live_streaming", False),
            "is_video_broadcast": video_node.get("is_video_broadcast", False),
            # Autor (mismo para todos los vídeos del feed, usualmente)
            "author": {
                "id": owner_id,
                "name": owner.get("name", ""),
                "url": f"https://www.facebook.com/profile.php?id={owner_id}",
                "avatar": self._safe_get(owner, "profile_picture", "uri", default=""),
                "gender": owner.get("gender", ""),
            },
        }

    # ==================================================================
    # PARSEO DE TRÁFICO GRAPHQL
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result`` con metadatos del tráfico GraphQL del vídeo."""
        self._parse_facebook_traffic(operation_patterns=_VIDEO_GRAPHQL_OPERATIONS)

    def _find_title_by_video_id(self, node: Any, video_id: str) -> str:
        """
        Busca recursivamente el campo title.text en un nodo cuyo id directo
        coincide con video_id, incluso si no tiene __typename="Video".

        Cubre el caso en que Facebook sirve el nodo del vídeo principal
        sin __typename en esta posición del JSON.
        """
        if not isinstance(node, dict):
            return ""

        # El patrón observado: {"id":"<video_id>"} anidado justo antes de "title"
        # El id numérico aparece como campo "id" directo del objeto
        if node.get("id") == video_id:
            title_node = node.get("title")
            if isinstance(title_node, dict):
                text = title_node.get("text", "")
                if text:
                    return text

        for value in node.values():
            if isinstance(value, dict):
                found_title = self._find_title_by_video_id(value, video_id)
                if found_title:
                    return found_title
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        found_title = self._find_title_by_video_id(item, video_id)
                        if found_title:
                            return found_title
        return ""

    # ==================================================================
    # UTILIDADES ESTÁTICAS
    # ==================================================================

    @staticmethod
    def _extract_video_id_from_url(url: str) -> str:
        """Extrae el ID numérico del vídeo desde la URL.

        Maneja múltiples formatos de URLs de vídeos de Facebook:
        - ``/videos/<id>/``
        - ``/<profile_id>/videos/<id>/``
        - ``/watch/?v=<id>``

        Args:
            url: URL del vídeo de Facebook.

        Returns:
            ID numérico como string, o cadena vacía si no se puede extraer.

        Example:
            >>> VideoParser._extract_video_id_from_url(
            ...     "https://www.facebook.com/61559641311874/videos/1824452301579645/"
            ... )
            '1824452301579645'
        """
        if not url:
            return ""

        # Patrón /videos/<id>/
        match = re.search(r"/videos/(\d+)/?", url)
        if match:
            return match.group(1)

        # Patrón ?v=<id>
        match = re.search(r"[?&]v=(\d+)", url)
        if match:
            return match.group(1)

        return ""

    @staticmethod
    def _parse_human_number(text: str) -> int:
        """Convierte un número en formato legible a int.

        Maneja sufijos K/M/B (miles, millones, billones) en inglés y
        español (K/k, M/m, B/b).

        Args:
            text: Número en formato humano (ej. ``"419K"``, ``"1.2M"``).

        Returns:
            Valor entero, o 0 si no se puede parsear.

        Example:
            >>> VideoParser._parse_human_number("419K")
            419000
            >>> VideoParser._parse_human_number("1.2M")
            1200000
        """
        if not text:
            return 0

        text = text.strip().upper().replace(",", "")
        multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

        for suffix, mult in multipliers.items():
            if text.endswith(suffix):
                try:
                    return int(float(text[:-1]) * mult)
                except ValueError:
                    return 0

        try:
            return int(float(text))
        except ValueError:
            return 0