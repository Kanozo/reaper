import re
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.base_parser import BaseParser
from reaper.utils import get_text_from_url, srt_to_dict
from reaper.utils.logger import get_logger

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
            author_name = (
                self._safe_get(actor, "name")
                or self._safe_get(
                    story, 'comet_sections', 'context_layout', 'story',
                    'comet_sections', 'actor_photo', 'story', 'actors',
                )[0]['name']
                or "unknow"
            )
            return {
                "id": author_id,
                "name": author_name,
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
            feedback = self._safe_get(
                        story, "feedback",
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

        # 1. Intentamos la ruta profunda
        comments_count = self._safe_get(
            story,
            "comet_sections", "feedback", "story", "story_ufi_container", "story",
            "feedback_context", "feedback_target_with_context", "comment_list_renderer",
            "feedback", "comment_rendering_instance_for_feed_location", "comments", "total_count"
        )

        # 2. Si no se encontró (es None), intentamos la ruta corta
        if comments_count is None:
            comments_count = self._safe_get(feedback, "total_comment_count")

        # 3. Fallback para feeds de grupo: los contadores viven en
        #    ``feedback.adaptive_ufi_action_renderers`` (renderers por
        #    tipo de acción). Cada renderer expone su contador en
        #    ``feedback.reaction_count / share_count / comment_rendering_instance``.
        adaptive_renderers = (
            feedback.get("adaptive_ufi_action_renderers") or []
        )
        for renderer in adaptive_renderers:
            if not isinstance(renderer, dict):
                continue
            renderer_feedback = renderer.get("feedback") or {}
            renderer_type = renderer.get("__typename") or ""

            if renderer_type == "UFIStoryReactActionRenderer":
                rc = self._safe_get(
                    renderer_feedback, "reaction_count", "count"
                )
                if rc is not None:
                    reaction_count = rc

            elif renderer_type == "UFICommentActionRenderer":
                cc = self._safe_get(
                    renderer_feedback,
                    "comment_rendering_instance", "comments", "total_count",
                )
                if cc is not None:
                    comments_count = cc

            elif renderer_type == "XFBUFIAdaptiveShareActionRenderer":
                sc = self._safe_get(renderer_feedback, "share_count", "count")
                if sc is not None:
                    share_count = sc

        # 4. Si sigue sin encontrarse, lo dejamos en 0
        if comments_count is None:
            comments_count = 0

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

            # Formato de highlight/group: el renderer y el media viven
            # anidados en ``style_type_renderer`` en lugar de ``styles``/``media``.
            if not style and "style_type_renderer" in att:
                str_renderer = att.get("style_type_renderer") or {}
                style = dict(str_renderer)
                style["attachment"] = str_renderer.get("attachment", {})
                renderer_type = str_renderer.get("__typename", "")
                media = self._safe_get(str_renderer, "attachment", "media") or {}
                att["media"] = media
                att["styles"] = style

            if renderer_type == "StoryAttachmentAlbumStyleRenderer":
                attachments.extend(self._extract_album_attachment(style))

            elif renderer_type in (
                "StoryAttachmentVideoStyleRenderer",
                # Renderer unificado actual (search/feed ago 2026): el media
                # completo vive en styles.attachment.media con progressive_urls
                # y captions; el att.media de primer nivel es solo referencia
                # ({__isNode, __typename, id}).
                "StoryAttachmentUnifiedLightweightVideoStyleRenderer",
            ):
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

        captions = self._process_captions_locales(
            media.get("video_available_captions_locales")
        )

        return [
            {
                "type": media.get("__typename"),
                "id": media.get("id", ""),
                "url": self._video_playback_url(media),
                "permalink_url": self._video_permalink(media),
                "caption": captions,
                "thumbnail_url": self._safe_get(
                    media, "thumbnailImage", "uri", default=""
                ),
                "playable_duration_in_ms": media.get("playable_duration_in_ms"),
                "width": media.get("width"),
                "height": media.get("height"),
            }
        ]

    def _video_playback_url(self, media: dict[str, Any] | None) -> str | None:
        """Resuelve la URL de reproducción de un vídeo (uso interno).

        Prioridad verificada contra tráfico real (ago 2026):

        1. ``videoDeliveryLegacyFields.browser_native_sd_url`` — formato
           legacy (aún presente en algunas superficies).
        2. ``videoDeliveryResponseFragment.videoDeliveryResponseResult
           .progressive_urls[].progressive_url`` — formato actual; la
           primera entrada es la de mayor calidad.

        Args:
            media: Nodo media del vídeo (el completo, no la referencia).

        Returns:
            URL directa de reproducción (mp4), o None si no hay ninguna.
        """
        if not isinstance(media, dict):
            return None

        legacy = self._safe_get(
            media, "videoDeliveryLegacyFields", "browser_native_sd_url"
        )
        if legacy:
            return str(legacy)

        progressive = self._safe_get(
            media,
            "videoDeliveryResponseFragment",
            "videoDeliveryResponseResult",
            "progressive_urls",
            default=[],
        )
        for entry in progressive or []:
            if isinstance(entry, dict) and entry.get("progressive_url"):
                return str(entry["progressive_url"])
        return None

    def _video_permalink(self, media: dict[str, Any] | None) -> str | None:
        """Resuelve el permalink estable de un vídeo (uso interno).

        Args:
            media: Nodo media del vídeo.

        Returns:
            URL pública permanente (p.ej. ``/reel/<id>``), o None.
        """
        if not isinstance(media, dict):
            return None
        permalink = media.get("url") or media.get("permalink_url")
        return str(permalink) if permalink else None

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
            "url": (
                self._safe_get(photo_media, "photo_image", "uri", default="")
                or self._safe_get(photo_media, "image", "uri", default="")
            ),
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
            media: Nodo media ya extraído. En las respuestas actuales puede
                ser solo la referencia ``{__isNode, __typename, id}``; en
                ese caso se resuelve el nodo completo desde
                ``att.styles.attachment.media``.
            include_video_metadata: Si True, enriquece con metadatos de vídeo.

        Returns:
            Dict con los datos del vídeo o reel.
        """
        # El media de primer nivel suele ser una referencia sin datos
        # (verificado contra tráfico real ago 2026): resolver el completo.
        full_media = media if (media.get("url") or media.get("width")) else (
            self._safe_get(att, "styles", "attachment", "media") or media
        )

        sfc = story.get("short_form_video_context") or {}
        is_reel_context = (
            media.get("playback_duration_in_ms")
            or media.get("is_short_video")
            or sfc
        )
        permalink = self._video_permalink(full_media)
        # Un vídeo sin contexto reel pero con permalink /reel/ es un reel.
        if not is_reel_context and permalink and "/reel/" in permalink:
            is_reel_context = True
        content_type = "reel" if is_reel_context else "video"

        # URL de reproducción: contexto reel (legacy) → helpers sobre el
        # media completo (progressive_urls / legacy fields / permalink).
        url = (
            self._safe_get(
                is_reel_context if isinstance(is_reel_context, dict) else sfc,
                "playback_video", "videoDeliveryLegacyFields", "browser_native_sd_url",
            )
            or self._video_playback_url(full_media)
        )

        # Captions (con descarga y conversión SRT→dict): contexto reel o,
        # en su defecto, el propio media del adjunto.
        captions = self._process_captions_locales(
            self._safe_get(
                story, "short_form_video_context", "playback_video",
                "video_available_captions_locales", default=[],
            )
            or full_media.get("video_available_captions_locales")
        )

        result: dict[str, Any] = {
            "type": content_type,
            "id": media.get("id", ""),
            "url": url,
            "permalink_url": permalink,
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
                "duration_ms": video_node.get("playable_duration_in_ms")
                or full_media.get("playable_duration_in_ms"),
                "width": self._safe_get(reel_ctx, "playback_video", "width")
                or full_media.get("width"),
                "height": self._safe_get(reel_ctx, "playback_video", "height")
                or full_media.get("height"),
                "thumbnail_url": self._safe_get(
                    reel_ctx, "playback_video", "thumbnailImage", "uri", default=""
                )
                or self._safe_get(full_media, "thumbnailImage", "uri", default=""),
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

                captions = self._process_captions_locales(
                    self._safe_get(
                        video, "video_available_captions_locales", default=[]
                    )
                )

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
                        "is_verified": (
                            self._safe_get(owner, "is_verified", default=False)
                            if owner else False
                        ),
                    },
                    "attachments": attachment,
                }
        except (IndexError, Exception) as exc:
            logger.debug("No se encontró reel compartido: %s", exc)

        return None

    def _process_captions_locales(
        self, captions_locales: list | None
    ) -> list[dict]:
        """Procesa los subtítulos de un vídeo con el patrón canónico.

        Lee ``video_available_captions_locales``, filtra por los idiomas de
        interés (en_US/es_ES o English/Español) y convierte cada URL externa
        a un dict con las frases del SRT. Mismo comportamiento que
        ``_extract_technical_metadata`` del parser de vídeo.

        Args:
            captions_locales: Lista de nodos de locales de subtítulos, o None.

        Returns:
            Lista de dicts de subtítulos procesados.
        """
        captions: list[dict] = []
        for caption_item in captions_locales or []:
            if not isinstance(caption_item, dict):
                continue
            # Aceptar variantes regionales completas (es_ES, es_CL, en_US,
            # en_GB...) vía prefijo, y ambos nombres del campo de lenguaje
            # ("localized_language" legacy y "localized_unambiguous_language"
            # actual — verificado contra tráfico real ago 2026).
            locale = str(caption_item.get("locale") or "")
            language = (
                caption_item.get("localized_language")
                or caption_item.get("localized_unambiguous_language")
                or ""
            )
            if (
                locale.startswith(("en_", "es_"))
                or language in {"English", "Español"}
            ):
                caption_item["captions_url"] = srt_to_dict(
                    get_text_from_url(caption_item.get("captions_url"))
                )
                captions.append(caption_item)
        return captions

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
                            caption = self._process_captions_locales(
                                media.get("video_available_captions_locales", [])
                            )
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
    # CONSTRUCCIÓN DE POSTS DESDE EDGES/STORIES (COMPARTIDO)
    #
    # Estos métodos son reutilizados por PostParser y GroupParser para
    # normalizar cualquier nodo Story/edge a un dict de post canónico.
    # Operan sobre el nodo recibido (nunca sobre self.result) y no
    # redisparan el parseo de tráfico, evitando recursión.
    # ==================================================================

    def _extract_basic_info(
        self, edge: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Extrae id, timestamp de publicación y URL permanente del post.

        Args:
            edge: Nodo/post del que extraer. Si es ``None`` se usa
                ``self._story`` (solo PostParser).

        Returns:
            Dict con ``id``, ``posted_at`` y ``permalink_url``.
        """
        target_story = edge if edge is not None else getattr(self, "_story", {})
        final_url = self.final_url if edge is None else None

        if not isinstance(target_story, dict):
            return {
                "id": "unknown",
                "posted_at": None,
                "permalink_url": final_url,
            }

        creation_time = self._safe_get(
            target_story, "comet_sections", "timestamp", "story", "creation_time"
        )

        return {
            "id": self._safe_get(target_story, "post_id", default="unknown"),
            "posted_at": self._parse_timestamp(creation_time),
            "permalink_url": target_story.get("permalink_url", final_url),
        }

    def _extract_content(
        self, story: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Extrae el texto principal del story.

        Args:
            story: Nodo del que extraer. Si es ``None`` se usa ``self._story``.

        Returns:
            Dict con la clave ``text``.
        """
        target_story = story if story is not None else getattr(self, "_story", {})

        if not isinstance(target_story, dict):
            return {"text": ""}

        message = self._safe_get(
            target_story, "comet_sections", "content", "story", "message"
        )
        extracted_text = (
            self._safe_get(message, "text", default="")
            if isinstance(message, dict)
            else ""
        )
        return {"text": extracted_text}

    def _extract_comments(
        self, story: dict | None = None
    ) -> list[dict[str, Any]]:
        """Extrae los comentarios visibles de un story.

        Args:
            story: Nodo del que extraer. Si es ``None`` se usa ``self._story``.

        Returns:
            Lista de dicts de comentarios, o lista vacía.
        """
        target_story = story if story is not None else getattr(self, "_story", {})

        comments_node = self._safe_get(
            target_story,
            "comet_sections", "feedback", "story",
            "story_ufi_container", "story",
            "feedback_context", "feedback_target_with_context",
            "comment_list_renderer", "feedback",
            "comment_rendering_instance_for_feed_location", "comments",
        )

        if not isinstance(comments_node, dict):
            return []

        edges = self._safe_get(comments_node, "edges", default=[])
        if not isinstance(edges, list):
            return []

        return [
            self._build_comment_dict(node)
            for edge in edges
            if isinstance(edge, dict)
            and isinstance((node := edge.get("node")), dict)
            and node
        ]

    def _extract_attachments_fallback(
        self, search_root: dict
    ) -> list[dict[str, Any]]:
        """Extrae adjuntos buscando nodos ``StoryAttachment`` via DFS.

        Se invoca cuando ``_extract_attachments_common`` devuelve vacío.
        Usa nodos con ``target`` y ``styles`` (versión canónica) para
        normalizar el media en ``styles.attachment.media``.

        Args:
            search_root: Subtree del JSON donde buscar.

        Returns:
            Lista de dicts de adjuntos normalizados.
        """
        raw_nodes: list[dict] = self._find_all_nodes(
            search_root,
            condition=lambda n: (
                n.get("__typename") == "StoryAttachment"
                and "target" in n
                and "styles" in n
            ),
        )

        if not raw_nodes:
            logger.debug(
                "Fallback attachments: no se encontraron nodos StoryAttachment con 'target'."
            )
            return []

        attachments: list[dict[str, Any]] = []
        seen_dedup_keys: set[str] = set()

        for node in raw_nodes:
            dedup_key = node.get("deduplication_key")
            if dedup_key:
                if dedup_key in seen_dedup_keys:
                    continue
                seen_dedup_keys.add(dedup_key)

            style_list: list[str] = node.get("style_list") or []
            primary_style: str = style_list[0] if style_list else "unknown"
            styles: dict = node.get("styles") or {}
            attachment_data: dict = styles.get("attachment") or {}
            media: dict = attachment_data.get("media") or {}

            built = self._build_attachment_from_media(
                media=media,
                style_typename=styles.get("__typename") or "",
                primary_style=primary_style,
                attachment_data=attachment_data,
            )

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

        return attachments

    def _build_attachment_from_media(
        self,
        media: dict,
        style_typename: str,
        primary_style: str,
        attachment_data: dict,
    ) -> dict[str, Any]:
        """Normaliza un nodo ``media`` en un dict de adjunto canónico."""
        media_typename: str = media.get("__typename") or ""
        is_photo = (
            "Photo" in style_typename or "Photo" in media_typename or primary_style == "photo"
        )
        is_video = (
            "Video" in style_typename or "Video" in media_typename or primary_style == "video"
        )
        is_link = "Link" in style_typename or primary_style == "link"

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
                "caption": self._process_captions_locales(
                    media.get("video_available_captions_locales")
                ),
            }

        if is_link:
            return {
                "type": "link",
                "url": self._safe_get(attachment_data, "url"),
                "title": self._safe_get(attachment_data, "title", "text"),
                "description": self._safe_get(attachment_data, "description", "text"),
                "caption": self._safe_get(media, "accessibility_caption", default=""),
            }

        return {
            "type": primary_style or "unknown",
            "id": media.get("id"),
            "url": media.get("url"),
            "caption": "",
        }

    def _extract_original_post_fallback(
        self, story: dict
    ) -> dict[str, Any] | None:
        """Extrae el post original combinando las dos ramas de attached_story."""

        top_attached: dict = story.get("attached_story") or {}
        content_attached: dict = (
            self._safe_get(
                story, "comet_sections", "content", "story", "attached_story"
            )
            or {}
        )

        if not top_attached and not content_attached:
            return None

        # El post original real puede estar un nivel más profundo:
        # attached_story.comet_sections.content.story (highlight units).
        # Ahí viven post_id, url, actors, attachments y message reales.
        deep_attached: dict = (
            self._safe_get(
                content_attached,
                "comet_sections", "content", "story",
            )
            or {}
        )
        source: dict = deep_attached or content_attached

        text: str = (
            self._safe_get(
                deep_attached, "comet_sections", "message",
                "story", "message", "text", default="",
            )
            or self._safe_get(
                deep_attached, "comet_sections", "message_container",
                "story", "message", "text", default="",
            )
            or self._safe_get(
                deep_attached, "message", "text", default="",
            )
            or self._safe_get(
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
        actor_from_content: dict = (
            source.get("actors") or content_attached.get("actors") or [{}]
        )[0]
        # En highlights el actor del post original vive en
        # context_layout.actor_photo (con name) o en content.story.actors
        # (solo id+avatar). Priorizamos el que tenga name.
        actor_from_ctx: dict = (
            self._safe_get(
                content_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "actor_photo", "story",
                "actors", default=[{}],
            )[0]
            if isinstance(
                self._safe_get(
                    content_attached,
                    "comet_sections", "context_layout", "story",
                    "comet_sections", "actor_photo", "story",
                    "actors", default=None,
                ),
                list,
            )
            else {}
        )
        actor: dict = actor_from_top or actor_from_content
        if not actor.get("name"):
            actor = actor_from_ctx or actor_from_content

        author: dict[str, Any] = {
            "id": actor.get("id"),
            "name": actor.get("name") or actor.get("__typename"),
            "url": actor.get("url") or actor.get("profile_url"),
            "avatar": self._safe_get(actor, "profile_picture", "uri", default=""),
            "is_verified": actor.get("is_verified", False),
            "work_info": actor.get("work_info", ""),
        }

        metadata_list: list = (
            self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "metadata",
                default=[],
            )
            or []
        )
        creation_time = None
        for meta_item in metadata_list:
            creation_time = self._safe_get(meta_item, "story", "creation_time")
            if creation_time:
                break

        post_id: str = (
            deep_attached.get("post_id")
            or content_attached.get("post_id")
            or top_attached.get("id")
            or ""
        )
        permalink_url: str = (
            deep_attached.get("url")
            or top_attached.get("permalink_url")
            or (top_attached.get("url") if isinstance(top_attached.get("url"), str) else "")
            or self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "metadata", default=[{}],
            )[0]
            and self._safe_get(
                top_attached,
                "comet_sections", "context_layout", "story",
                "comet_sections", "metadata", default=[{}],
            )[0].get("story", {}).get("url", "")
            or ""
        )

        original_attachments: list[dict] = (
            self._extract_attachments_common(deep_attached)
            or self._extract_attachments_common(content_attached)
            or (self._extract_attachments_fallback(top_attached) if top_attached else [])
        )

        return {
            "id": post_id,
            "text": text,
            "permalink_url": permalink_url,
            "posted_at": self._parse_timestamp(creation_time),
            "author": author,
            "attachments": original_attachments,
        }

    def _build_comment_dict(self, node: dict) -> dict[str, Any]:
        """Construye el dict normalizado de un comentario."""
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

    def parse_edge(self, edge: dict) -> dict[str, Any]:
        """Construye un dict de post desde un edge/nodo Story del GraphQL.

        Método puro: opera sobre ``edge`` y no muta ``self.result`` ni
        redispara el parseo de tráfico. Usado por PostParser y GroupParser.

        Args:
            edge: Nodo Story con los datos del post.

        Returns:
            Dict con los campos del post.
        """
        post: dict[str, Any] = {}

        try:
            post |= self._extract_basic_info(edge)
            post["author"] = self._extract_author_common(edge)
            post |= self._extract_content(edge)
            post["attachments"] = self._extract_attachments_common(edge)
            post["comments"] = self._extract_comments(edge)
            post.update(self._extract_feedback_common(edge))

            msg_ranges = (
                self._safe_get(
                    edge, "comet_sections", "content", "story",
                    "comet_sections", "message", "story", "message", "ranges",
                    default=[],
                )
                or self._safe_get(
                    edge, "comet_sections", "content", "story",
                    "comet_sections", "message_container", "story", "message", "ranges",
                    default=[],
                )
                or []
            )
            hashtags, mentions = self._parse_ranges(msg_ranges)
            post["hashtags"] = hashtags
            post["mentions"] = mentions

            group = self._extract_group_common(edge)
            if group is not None:
                post["group"] = group

            has_attached_story = bool(
                edge.get("attached_story")
                or self._safe_get(
                    edge, "comet_sections", "content", "story", "attached_story"
                )
            )

            if has_attached_story:
                original_post = self._extract_original_post_common(edge)
                if not original_post:
                    original_post = self._extract_original_post_fallback(edge)
                elif not original_post.get("attachments"):
                    top_attached: dict = edge.get("attached_story") or {}
                    if top_attached:
                        retried_atts = self._extract_attachments_fallback(top_attached)
                        if retried_atts:
                            original_post["attachments"] = retried_atts

                if original_post:
                    post["original_post"] = original_post

            post["is_sponsored"] = edge.get("sponsored_data") is not None

        except Exception as exc:
            post["error"] = str(exc)
            logger.error("Error parseando edge: %s", exc)
            if getattr(self, "debug", False):
                import traceback
                traceback.print_exc()

        return post

    def _add_story_to_feed(self, story: dict, seen_ids: set[str]) -> None:
        """Construye un dict de post desde un nodo Story y lo añade al feed.

        Args:
            story:    Nodo Story del JSON.
            seen_ids: Set mutable de post_ids ya procesados.
        """
        post_id = story.get("post_id") or self._find_post_id_in_story(story)
        if not post_id or post_id in seen_ids:
            return
        seen_ids.add(str(post_id))

        try:
            post = self.parse_edge(story)
            self.result["feed"].append(post)
        except Exception as exc:
            logger.debug("_add_story_to_feed error post_id=%s: %s", post_id, exc)

    def _process_traffic_fragment(
        self, fragment: dict[str, Any], seen_ids: set[str]
    ) -> int:
        """Procesa un fragmento JSON del tráfico GraphQL.

        Busca Stories en ``data.node`` (relay paginado), en
        ``data.group.group_feed.edges`` y en ``data.node`` tipo ``Group``
        con ``group_feed.edges`` (feed de grupo paginado).

        Args:
            fragment: Fragmento JSON normalizado.
            seen_ids: Set mutable de post_ids ya procesados.

        Returns:
            Número de posts añadidos.
        """
        added = 0
        data = fragment.get("data") or {}
        node = data.get("node") or {}

        # Patrón 1: relay paginado → data.node es la Story
        if node.get("__typename") == "Story" and node.get("post_id"):
            self._add_story_to_feed(node, seen_ids)
            return 1

        # Patrón 2: data.node es Group con group_feed.edges (feed de grupo)
        if node.get("__typename") == "Group":
            for edge in (node.get("group_feed") or {}).get("edges") or []:
                story = edge.get("node") or {}
                if story.get("__typename") == "Story" and story.get("post_id"):
                    prev = len(seen_ids)
                    self._add_story_to_feed(story, seen_ids)
                    if len(seen_ids) > prev:
                        added += 1

        # Patrón 3: data.group.group_feed.edges (feed completo)
        group = data.get("group") or {}
        for edge in (group.get("group_feed") or {}).get("edges") or []:
            story = edge.get("node") or {}
            if story.get("__typename") == "Story" and story.get("post_id"):
                prev = len(seen_ids)
                self._add_story_to_feed(story, seen_ids)
                if len(seen_ids) > prev:
                    added += 1

        # Patrón 4: búsqueda profunda como fallback
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

    def _find_post_id_in_story(self, story: dict) -> str | None:
        """Busca el ``post_id`` de un Story desde sus secciones anidadas.

        Args:
            story: Nodo Story.

        Returns:
            post_id como string, o None si no se encuentra.
        """
        if story.get("post_id"):
            return str(story["post_id"])
        cs = story.get("comet_sections") or {}
        ts_url = self._safe_get(cs, "timestamp", "story", "url") or ""
        m = re.search(r"/posts/(\d+)", ts_url)
        if m:
            return m.group(1)
        return self._safe_get(cs, "content", "story", "post_id")

    def _normalize_highlight_story(self, story: dict) -> dict:
        """Promueve los campos de un nodo highlight a la forma estándar de Story.

        Los nodos de highlight (``highlight_units.edges[].node.story``) tienen
        una estructura distinta a las Stories del feed: el id/texto viven en
        ``comet_sections.content.story`` y el timestamp en ``context_layout``
        metadata. Este método normaliza esas rutas para que ``parse_edge``
        pueda procesarlos.

        Args:
            story: Nodo story de un highlight unit.

        Returns:
            Dict normalizado con la forma estándar de Story.
        """
        normalized = dict(story)
        cs = story.get("comet_sections") or {}
        content_story = cs.get("content", {}).get("story") or {}

        post_id = content_story.get("post_id") or story.get("post_id")
        if post_id:
            normalized["post_id"] = post_id

        ts_url = (
            self._safe_get(cs, "timestamp", "story", "url")
            or content_story.get("url")
            or story.get("url")
        )
        normalized["permalink_url"] = (
            story.get("permalink_url") or content_story.get("url") or ""
        )

        # Mensaje real: content.story.comet_sections.message/message_container
        inner = content_story.get("comet_sections") or {}
        real_msg = (
            self._safe_get(inner, "message", "story", "message")
            or self._safe_get(inner, "message_container", "story", "message")
            or content_story.get("message")
            or {}
        )
        # Fallback profundo: highlights compartidos (reshare) anidan el texto
        # en attached_story.comet_sections.content.story.comet_sections.
        if not (real_msg or {}).get("text"):
            deep_attached = (
                self._safe_get(
                    content_story,
                    "attached_story", "comet_sections", "content", "story",
                    "comet_sections",
                )
                or {}
            )
            real_msg = (
                self._safe_get(deep_attached, "message", "story", "message")
                or self._safe_get(
                    deep_attached, "message_container", "story", "message"
                )
                or {}
            )
        content_story["message"] = real_msg

        if content_story.get("attachments"):
            normalized["attachments"] = content_story["attachments"]

        # Timestamp desde context_layout metadata
        creation_time = None
        layouts = cs.get("context_layout", {}).get("story") or {}
        ts_nodes = self._find_all_nodes(
            layouts,
            condition=lambda n: n.get("__typename")
            == "CometFeedStoryMinimizedTimestampStrategy",
        )
        if ts_nodes:
            creation_time = self._safe_get(ts_nodes[0], "story", "creation_time")

        ts_section = normalized.setdefault("comet_sections", {}) \
            .setdefault("timestamp", {}) \
            .setdefault("story", {})
        ts_section["creation_time"] = creation_time
        ts_section["url"] = ts_url

        return normalized

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
