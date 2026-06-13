from reaper.utils.logger import get_logger
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.utils import get_text_from_url, srt_to_dict
from reaper.parsers.base_parser import BaseParser

logger = get_logger(__name__)

class FacebookContentParser(BaseParser):
    """Clase base para todos los parsers de contenido de Facebook.

    Centraliza la lógica común para evitar duplicación entre PostParser,
    SearchParser, GroupParser y ReelParser.

    Attributes:
        original_url: URL original solicitada por el usuario (antes de
            redirecciones).
        result: Diccionario acumulador con datos extraídos. Las subclases
            lo inicializan con su estructura específica en ``__init__``.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de Facebook.

        Args:
            html_content: HTML completo renderizado de la página.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(
            html_content,
            final_url,
            original_url=original_url,
            traffic=traffic,
            debug=debug,
            platform="facebook",
        )
        # self.original_url y self.result ya inicializados por BaseParser.

    # ==================================================================
    # EXTRACCIÓN DE AUTOR
    # ==================================================================

    def _extract_author_common(
        self,
        story: dict,
        fallback_path: list[str] | None = None,
    ) -> dict[str, Any]:
        """Extrae información del autor desde un nodo Story.

        Intenta tres rutas en orden de prioridad:
        1. ``comet_sections.content.story.actors[0]`` (ruta principal).
        2. Ruta personalizada vía ``fallback_path`` (para SearchParser).
        3. ``short_form_video_context.video_owner`` (fallback para Reels).

        Usado por: PostParser, SearchParser, ReelParser.

        Args:
            story: Nodo Story o estructura similar con datos del autor.
            fallback_path: Ruta alternativa como lista de claves para
                ``_safe_get`` (ej. ``["actors", "0"]``).

        Returns:
            Dict con las claves: ``id``, ``name``, ``url``, ``avatar``,
            ``is_verified``, ``work_info``. Nunca retorna None; si no
            encuentra datos retorna el dict con valores por defecto.
        """
        # --- Ruta 1: comet_sections.content.story.actors ---
        actors = self._safe_get(story, "comet_sections", "content", "story", "actors")

        # --- Ruta 2: fallback personalizado (ej. SearchParser) ---
        if not actors and fallback_path:
            actors = self._safe_get(story, *fallback_path)

        if actors and isinstance(actors, list) and len(actors) > 0:
            actor = actors[0]
            author_id = self._safe_get(actor, "id", default="unknown")
            return {
                "id": author_id,
                "name": self._safe_get(actor, "name", default="Unknown"),
                "url": (
                    actor.get("url")
                    or f"https://www.facebook.com/profile.php?id={author_id}"
                ),
                "avatar": self._safe_get(actor, "profile_picture", "uri", default=""),
                "is_verified": actor.get("is_verified", False),
                "work_info": actor.get("work_info", ""),
            }

        # --- Ruta 3: video_owner (exclusivo de Reels) ---
        video_owner = self._safe_get(story, "short_form_video_context", "video_owner")
        if video_owner:
            owner_id = video_owner.get("id", "unknown")
            uri_token = self._safe_get(video_owner, "delegate_page", "uri_token")
            delegate_page_url = (
                f"https://www.facebook.com/{uri_token}" if uri_token else ""
            )
            return {
                "id": owner_id,
                "name": video_owner.get("name", "Unknown"),
                "url": (
                    video_owner.get("url")
                    or f"https://www.facebook.com/profile.php?id={owner_id}"
                ),
                "avatar": self._safe_get(
                    video_owner, "displayPicture", "uri", default=""
                ),
                "delegate_page": {
                    "id": self._safe_get(video_owner, "delegate_page", "id"),
                    "url": delegate_page_url,
                },
                "is_verified": video_owner.get("is_verified", False),
                "work_info": "",
            }

        # --- Default: no se encontró autor ---
        logger.debug("No se pudo extraer autor del story. Usando valores por defecto.")
        return {
            "id": "unknown",
            "name": "Unknown",
            "url": "",
            "avatar": "",
            "is_verified": False,
            "work_info": "",
        }

    # ==================================================================
    # EXTRACCIÓN DE GRUPO
    # ==================================================================

    def _extract_group_common(
        self,
        story: dict,
        inner_cs: dict | None = None,
        cont_story: dict | None = None,
    ) -> dict[str, Any] | None:
        """Extrae información del grupo si el post pertenece a uno.

        Intenta cuatro fuentes en orden:
        1. Campo ``to`` del story (tipo Group).
        2. ``inner_cs.title.story.to`` (SearchParser).
        3. ``feedback.associated_group``.
        4. ``media.creation_story.target_group`` (SearchParser).

        Usado por: PostParser, SearchParser, ReelParser.

        Args:
            story: Nodo Story principal.
            inner_cs: Contexto interno del story (opcional, SearchParser).
            cont_story: Contenido del story (opcional, SearchParser).

        Returns:
            Dict con ``id``, ``name``, ``url``, ``avatar`` del grupo,
            o None si el post no pertenece a un grupo.
        """
        # --- Fuente 1: campo `to` del story ---
        to = self._safe_get(story, "to")
        if to and self._safe_get(to, "__typename") == "Group":
            avatar = self._safe_get(
                story,
                "comet_sections", "context_layout", "story",
                "comet_sections", "actor_photo", "story",
                "to", "profile_picture", "uri",
                default="",
            )
            return {
                "id": self._safe_get(to, "id", default="unknown"),
                "name": self._safe_get(to, "name", default="Unknown Group"),
                "url": self._safe_get(to, "url", default=""),
                "avatar": avatar,
            }

        # --- Fuente 2: inner_cs.title.story.to (SearchParser) ---
        if inner_cs:
            title_to = self._safe_get(inner_cs, "title", "story", "to") or {}
            if title_to.get("__typename") == "Group":
                return {
                    "id": title_to.get("id", ""),
                    "name": title_to.get("name"),
                    "url": title_to.get("url"),
                    "avatar": "",
                }

        # --- Fuente 3: feedback.associated_group ---
        associated_group = self._safe_get(story, "feedback", "associated_group") or {}
        if associated_group.get("id"):
            return {
                "id": associated_group.get("id", ""),
                "name": associated_group.get("name"),
                "url": associated_group.get("url"),
                "avatar": "",
            }

        # --- Fuente 4: media.creation_story.target_group (SearchParser) ---
        if cont_story:
            for att in cont_story.get("attachments", []) or []:
                target_group = self._safe_get(
                    att, "styles", "attachment", "media", "creation_story", "target_group"
                ) or {}
                if target_group.get("id"):
                    return {
                        "id": target_group.get("id", ""),
                        "name": None,
                        "url": None,
                        "avatar": "",
                    }

        return None

    # ==================================================================
    # EXTRACCIÓN DE FEEDBACK (REACCIONES, COMENTARIOS, SHARES)
    # ==================================================================

    def _extract_feedback_common(
        self,
        story: dict,
        cont_story: dict | None = None,
    ) -> dict[str, Any]:
        """Extrae reacciones, comentarios y shares del post.

        Navega la estructura ``comet_sections.feedback`` para obtener
        los contadores de engagement y el detalle de top reactions.

        Usado por: PostParser, SearchParser, ReelParser.

        Args:
            story: Nodo Story principal.
            cont_story: Contenido del story (opcional, actualmente no usado
                en la implementación base pero disponible para subclases).

        Returns:
            Dict con las claves:
                - ``reaction_count`` (int): Total de reacciones.
                - ``share_count`` (int): Total de shares.
                - ``comments_count`` (int): Total de comentarios.
                - ``reactions`` (list): Top reactions con id, type y count.
            Si no se encuentra feedback, retorna todos los contadores en 0.
        """
        feedback = self._safe_get(
            story,
            "comet_sections", "feedback", "story",
            "story_ufi_container", "story",
            "feedback_context", "feedback_target_with_context",
            "comet_ufi_summary_and_actions_renderer", "feedback",
        )

        if not feedback:
            logger.debug("No se encontró feedback en el story.")
            return {
                "reaction_count": 0,
                "share_count": 0,
                "comments_count": 0,
                "reactions": [],
            }

        # Contadores principales
        reaction_count = self._safe_get(
            feedback, "reaction_count", "count", default=0
        )
        share_count = self._safe_get(feedback, "share_count", "count", default=0)

        # Comments count (ruta más profunda que el nodo feedback principal)
        comments_count = self._safe_get(
            story,
            "comet_sections", "feedback", "story",
            "story_ufi_container", "story",
            "feedback_context", "feedback_target_with_context",
            "comment_list_renderer", "feedback",
            "comment_rendering_instance_for_feed_location",
            "comments", "total_count",
            default=0,
        )

        # Top reactions (desglose por tipo)
        reactions = [
            {
                "id": self._safe_get(edge, "node", "id"),
                "type": self._safe_get(edge, "node", "localized_name", default="Unknown"),
                "count": edge.get("reaction_count", 0),
            }
            for edge in self._safe_get(feedback, "top_reactions", "edges", default=[])
            if self._safe_get(edge, "node")
        ]

        return {
            "reaction_count": reaction_count,
            "share_count": share_count,
            "comments_count": comments_count,
            "reactions": reactions,
        }

    # ==================================================================
    # EXTRACCIÓN DE ADJUNTOS (FOTOS, VÍDEOS, ÁLBUMES)
    # ==================================================================

    def _extract_attachments_common(
        self,
        story: dict,
        include_video_metadata: bool = False,
    ) -> list[dict[str, Any]]:
        """Extrae adjuntos (fotos, vídeos, álbumes) del post.

        Maneja cuatro tipos de renderers de adjuntos de Facebook:
        - ``StoryAttachmentAlbumStyleRenderer``: álbum de múltiples fotos.
        - ``StoryAttachmentVideoStyleRenderer``: vídeo estilo post normal.
        - ``StoryAttachmentFBReelsStyleRenderer``: adjunto de Reel genérico.
        - Adjunto simple: foto o vídeo individual.

        Usado por: PostParser, SearchParser, ReelParser.

        Args:
            story: Nodo Story principal.
            include_video_metadata: Si True, enriquece los adjuntos de
                vídeo/reel con metadatos adicionales (dimensiones, reshare).

        Returns:
            Lista de dicts con los adjuntos extraídos. Cada dict incluye
            al menos: ``type``, ``id``, ``url``, ``caption``.
        """
        attachments: list[dict[str, Any]] = []

        for att in story.get("attachments", []):
            style = att.get("styles", {})
            renderer_type = self._safe_get(style, "__typename", default="")
            media_type = self._safe_get(att, "media", "__typename")

            if renderer_type == "StoryAttachmentAlbumStyleRenderer":
                attachments.extend(self._extract_album_attachment(style))

            elif renderer_type == "StoryAttachmentVideoStyleRenderer":
                # Retorno directo: el vídeo principal reemplaza toda la lista
                return self._extract_video_attachment(style)

            elif renderer_type == "StoryAttachmentFBReelsStyleRenderer":
                if media_type == "GenericAttachmentMedia":
                    return []

            else:
                # Adjunto simple (foto o vídeo individual)
                simple = self._extract_simple_attachment(
                    att, story, include_video_metadata
                )
                if simple:
                    attachments.append(simple)

        return attachments

    def _extract_album_attachment(self, style: dict) -> list[dict[str, Any]]:
        """Extrae las imágenes de un álbum (uso interno).

        Args:
            style: Nodo ``styles`` del adjunto de tipo álbum.

        Returns:
            Lista de dicts con cada imagen del álbum.
        """
        nodes = self._safe_get(
            style, "attachment", "all_subattachments", "nodes", default=[]
        )
        return [
            {
                "type": node.get("media", {}).get("__typename", "Photo"),
                "id": node.get("media", {}).get("id", ""),
                "url": self._safe_get(node, "media", "image", "uri"),
                
                "caption": node.get("media", {}).get("accessibility_caption", ""),
            }
            for node in nodes
        ]

    def _extract_video_attachment(self, style: dict) -> list[dict[str, Any]]:
        """Extrae los datos de un vídeo con renderer dedicado (uso interno).

        Args:
            style: Nodo ``styles`` del adjunto de tipo vídeo.

        Returns:
            Lista con un único dict con los metadatos del vídeo.
        """
        media = self._safe_get(style, "attachment", "media") or {}

        captions_locales = media.get("video_available_captions_locales")
        captions = []
        for caption_item in captions_locales:
            if caption_item.get("locale") in {"en_US", "es_ES"} or caption_item.get("localized_language") in {"English", "Español"}:
                caption_item["captions_url"] = srt_to_dict(
                    get_text_from_url(caption_item.get("captions_url"))
                )
                captions.append(caption_item)
                
        return [
            {
                "type": media.get("__typename"),
                "id": media.get("id", ""),
                "url": self._safe_get(
                    media, "videoDeliveryLegacyFields", "browser_native_sd_url",
                    default="",
                ),
                "caption": captions,
                "thumbnail_url": self._safe_get(
                    media, "thumbnailImage", "uri", default=""
                ),
                "playable_duration_in_ms": media.get("playable_duration_in_ms"),
                "width": media.get("width"),
                "height": media.get("height"),
            }
        ]

    def _extract_simple_attachment(
        self,
        att: dict,
        story: dict,
        include_video_metadata: bool,
    ) -> dict[str, Any] | None:
        """Extrae un adjunto simple (foto o vídeo individual) (uso interno).

        Args:
            att: Nodo de adjunto individual.
            story: Nodo Story completo (para contexto de reel/captions).
            include_video_metadata: Si True, añade metadatos de vídeo.

        Returns:
            Dict con los datos del adjunto, o None si no hay media válida.
        """
        media = self._safe_get(att, "media")
        if not media:
            return None

        media_typename = media.get("__typename", "")
        is_video = media_typename == "Video" or media.get("is_playable")

        if is_video:
            return self._extract_video_or_reel_simple(
                att, story, media, include_video_metadata
            )

        # --- Foto ---
        photo_media = self._safe_get(att, "styles", "attachment", "media") or {}
        return {
            "type": media_typename or "Photo",
            "id": media.get("id", ""),
            "url": self._safe_get(photo_media, "photo_image", "uri", default=""),
            "caption": photo_media.get("accessibility_caption", ""),
        }

    def _extract_video_or_reel_simple(
        self,
        att: dict,
        story: dict,
        media: dict,
        include_video_metadata: bool,
    ) -> dict[str, Any]:
        """Extrae datos de un vídeo/reel en adjunto simple (uso interno).

        Args:
            att: Nodo de adjunto.
            story: Nodo Story completo.
            media: Nodo media ya extraído.
            include_video_metadata: Si True, enriquece con metadatos de vídeo.

        Returns:
            Dict con los datos del vídeo o reel.
        """
        sfc = story.get("short_form_video_context") or {}
        is_reel_context = (
            media.get("playback_duration_in_ms")
            or media.get("is_short_video")
            or sfc
        )
        content_type = "reel" if is_reel_context else "video"

        # URL de reproducción
        url = self._safe_get(
            is_reel_context if isinstance(is_reel_context, dict) else sfc,
            "playback_video", "videoDeliveryLegacyFields", "browser_native_sd_url",
        )

        # Captions (con descarga y conversión SRT→dict)
        raw_captions = self._safe_get(
            story, "short_form_video_context", "playback_video",
            "video_available_captions_locales", default=[],
        )
        captions = []
        for caption_item in raw_captions:
            if caption_item.get("locale") in {"en_US", "es_ES"} or caption_item.get("localized_language") in {"English", "Español"}:
                caption_item["captions_url"] = srt_to_dict(
                    get_text_from_url(caption_item.get("captions_url"))
                )
                captions.append(caption_item)

        result: dict[str, Any] = {
            "type": content_type,
            "id": media.get("id", ""),
            "url": url,
            "caption": captions,
        }

        if include_video_metadata:
            reel_ctx = is_reel_context if isinstance(is_reel_context, dict) else sfc
            video_node = self._safe_get(reel_ctx, "video") or {}
            reshare_ctx = self._safe_get(reel_ctx, "fb_shorts_reshare_context") or {}
            is_reshare = reshare_ctx.get("is_reshare", False)

            reshare_creator: dict = {}
            if is_reshare:
                raw_creator = self._safe_get(reshare_ctx, "reshare_creator") or {}
                reshare_creator = {
                    "id": raw_creator.get("id"),
                    "name": raw_creator.get("name"),
                    "is_verified": raw_creator.get("is_verified"),
                    "url": raw_creator.get("url"),
                }

            result.update({
                "duration_ms": video_node.get("playable_duration_in_ms"),
                "width": self._safe_get(reel_ctx, "playback_video", "width"),
                "height": self._safe_get(reel_ctx, "playback_video", "height"),
                "thumbnail_url": self._safe_get(
                    reel_ctx, "playback_video", "thumbnailImage", "uri", default=""
                ),
                "reshare_creator": reshare_creator,
            })

        return result

    # ==================================================================
    # PARSEO DE HASHTAGS Y MENCIONES
    # ==================================================================

    def _parse_ranges(
        self,
        ranges: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Parsea hashtags y menciones desde ``message.ranges``.

        Itera los rangos del campo ``message`` de un Story y clasifica
        las entidades como Hashtag o mención de perfil/página.

        Usado por: PostParser, ReelParser.

        Args:
            ranges: Lista de rangos del campo ``message.ranges``.

        Returns:
            Tuple ``(hashtags, mentions)`` donde cada elemento es una
            lista de dicts.

            Hashtag dict: ``tag``, ``id``, ``url``, ``mobile_url``.
            Mention dict: ``id``, ``url``.

        Example:
            >>> hashtags, mentions = self._parse_ranges(msg.get("ranges", []))
        """
        hashtags: list[dict] = []
        mentions: list[dict] = []

        for range_item in ranges:
            entity = range_item.get("entity") or {}
            typename = entity.get("__typename")

            if typename == "Hashtag":
                raw_url = entity.get("url", "")
                tag = (
                    raw_url.split("hashtag/")[-1].split("?")[0] if raw_url else ""
                )
                hashtags.append({
                    "tag": tag,
                    "id": entity.get("id", ""),
                    "url": raw_url,
                    "mobile_url": entity.get("mobileUrl", ""),
                })
            elif typename in ("Profile", "User", "Page"):
                mentions.append({
                    "id": entity.get("id", ""),
                    "url": entity.get("url", ""),
                })

        return hashtags, mentions

    # ==================================================================
    # EXTRACCIÓN DE POST ORIGINAL (SHARES / REPOSTS)
    # ==================================================================

    def _extract_original_post_common(
        self,
        story: dict,
    ) -> dict[str, Any] | None:
        """Extrae el post original cuando el contenido es un share/repost.

        Intenta dos estrategias en orden:
        1. Reel compartido via ``fb_shorts_story`` en ``style_infos``.
        2. Post regular compartido via ``attached_story``.

        Usado por: PostParser, ReelParser.

        Args:
            story: Nodo Story principal que puede contener un share.

        Returns:
            Dict con ``id``, ``permalink_url``, ``posted_at``, ``text``,
            ``author`` y ``attachment`` del post original,
            o None si el story no es un share.
        """
        # --- Intento 1: Reel compartido (fb_shorts_story) ---
        original = self._extract_shared_reel(story)
        if original is not None:
            return original

        # --- Intento 2: Post regular compartido (attached_story) ---
        return self._extract_shared_post(story)

    def _extract_shared_reel(self, story: dict) -> dict[str, Any] | None:
        """Extrae un Reel compartido via ``fb_shorts_story`` (uso interno).

        Args:
            story: Nodo Story principal.

        Returns:
            Dict con datos del reel original, o None si no aplica.
        """
        try:
            attachments = story.get("attachments") or []
            first_att = attachments[0] if attachments else {}
            style_infos = self._safe_get(first_att, "styles", "attachment", "style_infos")
            if not style_infos:
                return None

            for style_info in style_infos:
                original = style_info.get("fb_shorts_story")
                if not original:
                    continue

                owner = self._safe_get(
                    original, "short_form_video_context", "video_owner"
                )
                video = self._safe_get(
                    original, "short_form_video_context", "playback_video"
                )

                captions_raw = self._safe_get(
                    video, "video_available_captions_locales", default=[]
                )
                captions = []
                for caption_item in captions_raw:
                    caption_item["captions_url"] = srt_to_dict(
                        get_text_from_url(caption_item.get("captions_url"))
                    )
                    captions.append(caption_item)

                attachment: list[dict] = []
                if video:
                    attachment = [
                        {
                            "type": "video",
                            "id": self._safe_get(video, "id", default=""),
                            "url": self._safe_get(
                                video, "videoDeliveryLegacyFields",
                                "browser_native_sd_url",
                            ),
                            "thumbnail_url": self._safe_get(
                                video, "thumbnailImage", "uri"
                            ),
                            "caption": captions,
                            "duration_ms": int(video.get("length_in_second", 0) * 1000),
                            "width": video.get("width"),
                            "height": video.get("height"),
                        }
                    ]

                owner_id = self._safe_get(owner, "id", default="") if owner else ""
                return {
                    "id": self._safe_get(original, "post_id"),
                    "permalink_url": self._safe_get(
                        original, "short_form_video_context", "shareable_url"
                    ),
                    "posted_at": self._parse_timestamp(
                        self._safe_get(original, "creation_time")
                    ),
                    "text": self._safe_get(original, "message", "text"),
                    "author": {
                        "id": owner_id,
                        "name": self._safe_get(owner, "name", default="") if owner else "",
                        "url": (
                            (owner.get("url") if owner else None)
                            or f"https://www.facebook.com/profile.php?id={owner_id}"
                        ),
                        "avatar": self._safe_get(owner, "displayPicture", "uri") if owner else "",
                        "is_verified": self._safe_get(owner, "is_verified", default=False) if owner else False,
                    },
                    "attachments": attachment,
                }
        except (IndexError, Exception) as exc:
            logger.debug("No se encontró reel compartido: %s", exc)

        return None

    def _extract_shared_post(self, story: dict) -> dict[str, Any] | None:
        """Extrae un post regular compartido via ``attached_story`` (uso interno).

        Args:
            story: Nodo Story principal.

        Returns:
            Dict con datos del post original, o None si no aplica.
        """
        try:
            original = story.get("attached_story")
            if not original:
                return None

            owner = self._safe_get(original, "feedback", "owning_profile")
            actors = self._safe_get(
                original,
                "comet_sections", "context_layout", "story",
                "comet_sections", "actor_photo", "story", "actors",
            )
            avatar = (
                self._safe_get(actors[0], "profile_picture", "uri")
                if actors
                else ""
            )

            owner_id = self._safe_get(owner, "id", default="") if owner else ""
            author = (
                {
                    "id": owner_id,
                    "name": self._safe_get(owner, "name", default=""),
                    "url": (
                        owner.get("url")
                        or f"https://www.facebook.com/profile.php?id={owner_id}"
                    ),
                    "avatar": avatar,
                    "is_verified": False,
                }
                if owner
                else {}
            )

            story_att = self._safe_get(
                story,
                "comet_sections", "content", "story",
                "comet_sections", "attached_story", "story",
                "attached_story", "comet_sections",
                "attached_story_layout", "story",
            )

            text = ""
            attachments: list[dict] = []
            posted_at = None

            if story_att:
                text = self._safe_get(story_att, "message", "text", default="")

                if not story_att.get("is_text_only_story"):
                    for att in self._safe_get(story_att, "attachments", default=[]):
                        media = self._safe_get(att, "styles", "attachment", "media")
                        if not media:
                            continue

                        if media.get("__typename") == "Video":
                            url = self._safe_get(
                                media, "videoDeliveryLegacyFields", "browser_native_sd_url"
                            )
                            caption = str(media.get("video_available_captions_locales", []))
                        else:
                            url = self._safe_get(media, "photo_image", "uri")
                            caption = media.get("accessibility_caption", "")

                        attachments.append({
                            "type": media.get("__typename"),
                            "id": media.get("id", ""),
                            "thumbnail_url": self._safe_get(media, "thumbnailImage", "uri"),
                            "url": url,
                            "caption": caption,
                        })

                    # ── Fallback álbum ──────────────────────────────────────────
                    # El loop anterior solo resuelve Photo/Video directos via
                    # styles.attachment.media. Para StoryAttachmentAlbumStyleRenderer
                    # ese campo no existe: las imágenes viven en
                    # styles.attachment.all_subattachments.nodes.
                    # _extract_attachments_common ya maneja ese renderer
                    # correctamente, lo reutilizamos cuando el loop dejó
                    # attachments vacío.
                    if not attachments:
                        logger.debug(
                            "_extract_shared_post: loop principal sin adjuntos; "
                            "reintentando con _extract_attachments_common "
                            "(posible StoryAttachmentAlbumStyleRenderer)."
                        )
                        attachments = self._extract_attachments_common(story_att)

                for meta in self._safe_get(story_att, "comet_sections", "metadata", default=[]):
                    ts = self._safe_get(meta, "story", "creation_time")
                    if ts:
                        posted_at = self._parse_timestamp(ts)
                        break

            permalink_url = original.get("permalink_url", "")
            return {
                "id": permalink_url.split("/")[-1] if permalink_url else "",
                "permalink_url": permalink_url,
                "posted_at": posted_at,
                "text": text,
                "author": author,
                "attachments": attachments,
            }

        except Exception as exc:
            logger.debug("Error extrayendo post compartido: %s", exc)
            if self.debug:
                import traceback
                traceback.print_exc()

        return None

    # ==================================================================
    # PARSEO DE TRÁFICO GRAPHQL
    # ==================================================================

    def _parse_facebook_traffic(
        self,
        operation_patterns: list[str],
    ) -> None:
        """Parsea tráfico GraphQL específico de Facebook.

        Registra metadatos sobre las operaciones GraphQL capturadas,
        enriqueciendo ``self.result`` con contadores por operación.

        Usado por: todos los parsers de Facebook.

        Args:
            operation_patterns: Lista de nombres de operaciones GraphQL
                a contabilizar (ej. ``["GroupHome", "GroupAbout"]``).
        """
        if not self.traffic:
            self.result["traffic_parsed"] = False
            return

        logger.debug("Parseando tráfico: %s", self._get_traffic_summary())

        operation_counts = {
            pattern: len(self._get_graphql_response_by_operation(pattern))
            for pattern in operation_patterns
        }

        self.result["traffic_parsed"] = True
        self.result["graphql_responses_available"] = len(self.traffic.graphql_responses)
        self.result["api_responses_available"] = len(self.traffic.api_responses)
        self.result["graphql_operations_found"] = operation_counts

        if self.debug:
            for operation, count in operation_counts.items():
                logger.debug("GraphQL op '%s': %d responses", operation, count)

    def _parse_traffic(self) -> None:
        """Override del método base para parsear tráfico de Facebook.

        Llama a ``_parse_facebook_traffic`` con un patrón genérico.
        Las subclases concretas deben hacer override con sus patrones
        específicos para obtener conteos más precisos.
        """
        self._parse_facebook_traffic(operation_patterns=["Facebook"])