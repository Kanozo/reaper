from reaper.utils.logger import get_logger
import re
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

logger = get_logger(__name__)

# OG description pattern: "1,413 likes · 7 talking about this. <bio>"
_OG_LIKES_PATTERN = re.compile(r"([\d,\.]+)\s+likes", re.IGNORECASE)
_OG_TALKING_PATTERN = re.compile(r"([\d,\.]+)\s+talking about this", re.IGNORECASE)

# Social context: "1.4K followers", "31 following", "1.2K friends"
_SOCIAL_COUNT_PATTERN = re.compile(
    r"([\d,\.]+[KkMmBb]?)\s*(followers?|following|friends?)",
    re.IGNORECASE,
)

# GraphQL operations relevantes para perfiles
_PROFILE_GRAPHQL_OPERATIONS = [
    "ProfileCometTimelineQuery",
    "ProfileCometAboutAppSectionQuery",
    "ProfileCometPhotosTabQuery",
    "CometUFICommentsProviderQuery",
    "ProfileCometTimelineFeedRefetchQuery"
]

# Patrones para detectar la red social de una URL o handle
_SOCIAL_PATTERNS: list[tuple[str, str]] = [
        ("instagram",  "instagram.com"),
        ("x",          "x.com"),
        ("twitter",    "twitter.com"),
        ("youtube",    "youtube.com"),
        ("tiktok",     "tiktok.com"),
        ("linkedin",   "linkedin.com"),
        ("telegram",   "t.me"),
        ("whatsapp",   "whatsapp"),
        ("snapchat",   "snapchat.com"),
        ("pinterest",  "pinterest.com"),
        ("threads",    "threads.net"),
    ]

class ProfileParser(FacebookContentParser):
    """Parser para perfiles de usuario de Facebook.

    Extrae toda la información visible del perfil: identidad, bio,
    métricas sociales, fotos, álbumes y el feed del timeline.

    Estructura de datos extraídos:
        - ``identity``: id, nombre, URL, username, género.
        - ``pictures``: avatar en tres tamaños, foto de perfil, vídeo.
        - ``cover_photo``: imagen de portada con dimensiones y foco.
        - ``bio``: descripción, categoría, alternate_name.
        - ``metrics``: followers, following, likes, talking_about.
        - ``delegate_page``: página de negocio/creador asociada.
        - ``feed``: posts del timeline visible.

    Attributes:
        _node_header: Nodo del header con cover_photo y __isRenderedProfile.
        _node_delegate: Nodo de la página delegada con category/best_description.
        _node_timeline: Nodo con timeline_list_feed_units (posts del feed).
        _node_social: Nodo con profile_social_context (followers/following).
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de perfiles de usuario.

        Args:
            html_content: HTML completo renderizado de la página del perfil.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self._node_header: dict | None = None
        self._node_delegate: dict | None = None
        self._node_timeline: dict | None = None
        self._node_social: dict | None = None

        self.result.update({
            "__typename": "facebook_user_profile",
            "profile_url": self.original_url,
            "feed": [],
            "education":    {"text": "", "name": "", "url": "", "id": ""},
            "current_city": {"text": "", "name": "", "url": "", "id": ""},
            "hometown":     {"text": "", "name": "", "url": "", "id": ""},
            "contact_info": {
                "email":           "",
                "phone":           "",
                "websites":        [],
                "social_accounts": [],
            },
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el proceso completo de extracción del perfil.

        Flujo:
            1. Extraer bloques JSON del HTML.
            2. Localizar nodos principales.
            3. Extraer todos los campos del perfil en orden lógico.
            4. Extraer álbumes y feed del timeline.
            5. Enriquecer con tráfico GraphQL.

        Returns:
            Diccionario con todos los campos del perfil. Si falla la
            localización del nodo principal, ``result["error"]`` indica el motivo.
        """
        logger.info("Iniciando extracción de PERFIL DE USUARIO...")

        self._blocks = self._extract_json_blocks()

        if not self._locate_profile_nodes():
            self.result["error"] = "No se encontró el nodo de perfil de usuario."
            logger.warning("ProfileParser: no se encontraron nodos en %s", self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_identity()
            self._extract_profile_pictures()
            self._extract_cover_photo()
            self._extract_bio_and_category()
            self._extract_social_metrics()
            self._extract_delegate_page()
            self._extract_intro_card_info()
            self._extract_contact_info() 
            self._extract_timeline_feed()
            self._parse_traffic()
            logger.debug(
                f"Perfil parseado. Feed: {len(self.result["feed"])} posts",
            )
        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando perfil: %s", exc)
            if self.debug:
                import traceback
                traceback.print_exc()

        return self.result

    def _locate_profile_nodes(self) -> bool:
        """Localiza y asigna los cuatro nodos principales del perfil.

        Estrategia por nodo:

        - **header**: Nodo ``User`` con ``__isRenderedProfile`` y
          ``cover_photo`` — contiene toda la estructura del header de perfil.
          Incluye foto de portada, foto de perfil en múltiples tamaños,
          tabs, social context y flags.

        - **delegate**: Nodo con ``category_name`` y ``best_description``
          — la página delegada asociada al perfil personal (tipo "Digital creator").
          Contiene bio, categoría y foto de perfil alternativa.

        - **timeline**: Nodo con ``timeline_list_feed_units``
          — la lista de Stories del feed del timeline del usuario.

        - **social**: Nodo con ``profile_social_context``
          — followers, following. Puede coincidir con ``_node_header``.

        Returns:
            True si se encontró al menos el nodo header o el delegate.
        """
        for block in self._blocks:
            if not self._node_header:
                self._node_header = self._recursive_search(
                    block,
                    condition=lambda n: (
                        "__isRenderedProfile" in n
                        and "cover_photo" in n
                        and (isinstance(n.get("cover_photo"), dict) or n.get("cover_photo") is None)
                        and "profilePicMedium" in n
                    ),
                )
                if self._node_header:
                    logger.debug("Nodo header localizado (id=%s).", self._node_header.get("id"))

            if not self._node_delegate:
                self._node_delegate = self._recursive_search(
                    block,
                    condition=lambda n: (
                        "category_name" in n
                        and "best_description" in n
                        and isinstance(n.get("best_description"), dict)
                    ),
                )
                if self._node_delegate:
                    logger.debug("Nodo delegate page localizado.")

            if not self._node_timeline:
                self._node_timeline = self._recursive_search(
                    block,
                    condition=lambda n: (
                        "timeline_list_feed_units" in n
                        and isinstance(n.get("timeline_list_feed_units"), dict)
                    ),
                )
                if self._node_timeline:
                    logger.debug("Nodo timeline feed localizado.")

            if not self._node_social:
                self._node_social = self._recursive_search(
                    block,
                    condition=lambda n: (
                        "profile_social_context" in n
                        and isinstance(n.get("profile_social_context"), dict)
                        and "content" in (n.get("profile_social_context") or {})
                    ),
                )
                if self._node_social:
                    logger.debug("Nodo social context localizado.")

        found = self._node_header is not None or self._node_delegate is not None
        if not found:
            logger.debug("No se encontraron nodos de perfil en %d bloques.", len(self._blocks))
        return found

    def _extract_identity(self) -> None:
        """Extrae los campos de identidad básica del perfil.

        Combina datos de ``_node_header`` (fuente primaria) y OG meta
        (fallback) para obtener id, nombre, URL, username y género.

        Actualiza ``self.result`` con:
            - ``id``: ID numérico del usuario de Facebook.
            - ``name``: Nombre completo.
            - ``url``: URL pública del perfil (con username o /profile.php?id=).
            - ``username``: Vanity URL (alias), puede ser None.
            - ``gender``: "MALE", "FEMALE" u otro valor de FB.
            - ``alternate_name``: Nombre alternativo / apodo público.
        """
        header = self._node_header or {}

        self.result["id"] = header.get("id") or self._extract_id_from_url(self.final_url)
        self.result["name"] = header.get("name") or self._extract_og_field("og:title")
        self.result["url"] = header.get("url") or self.final_url
        self.result["username"] = self._extract_username()
        self.result["gender"] = header.get("gender", "")
        self.result["alternate_name"] = header.get("alternate_name", "") or ""

    def _extract_username(self) -> str | None:
        """Extrae el username/vanity URL del perfil.

        Intenta tres fuentes: la URL canónica (ej. ``/zurdobo7``),
        el campo ``url`` del nodo header, y el campo OG url.

        Returns:
            Username como string si se puede extraer, None si no.
        """
        url = (
            self._safe_get(self._node_header, "url")
            or self._extract_og_field("og:url")
            or self.final_url
        )
        if not url:
            return None

        # Patrón /people/<name>/<id>/ → no es vanity
        if "/people/" in url:
            return None

        # Último segmento de la URL como username
        match = re.search(r"facebook\.com/([^/?#]+)/?$", url)
        if match:
            segment = match.group(1)
            # Si es numérico, es un ID, no un username
            if not segment.isdigit():
                return segment
        return None

    def _extract_id_from_url(self, url: str) -> str:
        """Extrae el ID numérico desde una URL de perfil de Facebook.

        Args:
            url: URL del perfil (ej. ``/profile.php?id=100080341951526``).

        Returns:
            ID numérico como string, o cadena vacía.
        """
        match = re.search(r"[?&]id=(\d+)", url)
        return match.group(1) if match else ""

    def _extract_profile_pictures(self) -> None:
        """Extrae las fotos de perfil en múltiples resoluciones.

        Facebook proporciona tres tamaños de la foto de perfil actual
        (small ~100px, medium ~148px, large ~200px) más una referencia
        al álbum de fotos de perfil.
        """
        header = self._node_header or {}
        self.result["avatar"] = self._safe_get(header, "profilePicMedium", "uri", default="")
        self.result["profile_video"] = header.get("profile_video")

    def _extract_cover_photo(self) -> None:
        """Extrae la foto de portada del perfil con todas sus variantes.

        La portada tiene tres variantes de imagen:
        - ``image``: Resolución alta (hasta 960px).
        - ``blurred_image``: Versión desenfocada para el fondo.
        - ``viewer_image``: Dimensiones reales de visualización del viewer.

        También incluye el foco de recorte (x, y) para centrado.

        Actualiza ``self.result["cover_photo"]`` in-place.
        """
        header = self._node_header or {}
        cover = header.get("cover_photo") or {}
        photo = cover.get("photo") or {}
        image = photo.get("image") or {}

        self.result["banner"] = {
            "image_uri": image.get("uri", ""),
            "image_width": image.get("width"),
            "image_height": image.get("height")
        }

    def _extract_bio_and_category(self) -> None:
        """Extrae la bio, categoría y nombre alternativo del perfil.

        La bio (descripción corta) puede venir de la página delegada
        (``best_description``) o del OG meta tag como fallback.
        La categoría indica el tipo de perfil (ej. "Digital creator").

        Actualiza ``self.result`` con ``bio``, ``category``.
        """
        # Bio: delegate → OG fallback
        bio_from_delegate = self._safe_get(
            self._node_delegate, "best_description", "text", default=""
        )
        self.result["bio"] = bio_from_delegate or self._extract_bio_from_og()

        # Categoría del creador/perfil
        self.result["category"] = self._safe_get(
            self._node_delegate, "category_name", default=""
        )

    def _extract_bio_from_og(self) -> str:
        """Extrae la bio desde el OG meta description como fallback.

        El OG description tiene el patrón:
        ``"<Name>. N likes · M talking about this. <bio>"``

        Returns:
            Texto de bio, o cadena vacía si no se puede parsear.
        """
        og_desc = self._extract_og_field("og:description") or ""
        if not og_desc:
            return ""

        # Eliminar el prefijo "Name. N likes · M talking about this. "
        # Buscar el patrón y tomar todo lo que venga después
        patterns = [
            r"talking about this\.\s*(.+)$",
            r"\d+\s+likes\s*[·•]\s*\d+\s+talking about this\.\s*(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, og_desc, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        return ""

    def _extract_social_metrics(self) -> None:
        """Extrae las métricas sociales del perfil.

        Combina tres fuentes de datos:
        1. ``profile_social_context``: followers y following del nodo header.
        2. OG meta description: likes y talking_about (métricas de la página).
        3. Texto parseado de ``profile_social_context.content[].text.text``.

        Nota para analistas:
            - ``followers_count``: personas que siguen el perfil.
            - ``following_count``: perfiles que sigue este usuario.
            - ``likes_count``: "Me gusta" de la página delegada (si existe).
            - ``talking_about_count``: interacciones recientes (últimos 7 días).

        Actualiza ``self.result["metrics"]`` in-place.
        """
        followers, following, friends = self._parse_social_context_counts()
        likes, talking = self._parse_og_metrics()
        
        self.result["followers_count"] = followers
        self.result["following_count"] = following
        self.result["friends_count"] = friends
        self.result["likes_count"] = likes           # Likes de la página delegada
        self.result["talking_about_count"] = talking  # Personas que hablan del perfil (7d)

    def _parse_social_context_counts(self) -> tuple[int, int, int]:
        """Parsea followers, following y friends desde profile_social_context.

        Itera el array ``content`` del social context y extrae los valores
        usando el patrón de texto localizado.

        Returns:
            Tuple ``(followers, following, friends)`` como enteros.
            Retorna (0, 0, 0) si no hay datos disponibles.
        """
        node = self._node_social or self._node_header or {}
        psc = self._safe_get(node, "profile_social_context", "content", default=[])

        followers = following = friends = 0

        for item in (psc or []):
            text = self._safe_get(item, "text", "text", default="")
            uri = item.get("uri", "")

            match = _SOCIAL_COUNT_PATTERN.search(text)
            if not match:
                continue

            count = self._parse_human_number(match.group(1))
            kind = match.group(2).lower()

            if "follower" in kind:
                followers = count
            elif "following" in kind:
                following = count
            elif "friend" in kind:
                friends = count

        return followers, following, friends

    def _parse_og_metrics(self) -> tuple[int, int]:
        """Parsea likes y talking_about desde el OG meta description.

        Returns:
            Tuple ``(likes, talking_about)`` como enteros.
        """
        og_desc = self._extract_og_field("og:description") or ""
        likes = talking = 0

        likes_match = _OG_LIKES_PATTERN.search(og_desc)
        if likes_match:
            likes = self._parse_human_number(
                likes_match.group(1).replace(",", "")
            )

        talking_match = _OG_TALKING_PATTERN.search(og_desc)
        if talking_match:
            talking = self._parse_human_number(
                talking_match.group(1).replace(",", "")
            )

        return likes, talking

    def _extract_delegate_page(self) -> None:
        """Extrae información de la página de negocio/creador delegada.

        Algunos perfiles personales tienen asociada una página de Facebook
        (ej. "Digital creator", "Business"). Esta página delegada contiene
        métricas adicionales que complementan el perfil personal.

        Actualiza ``self.result["delegate_page"]`` in-place.
        """
        header = self._node_header or {}
        delegate_ref = header.get("delegate_page") or {}

        self.result["is_business_page_active"] = delegate_ref.get("is_business_page_active", False)

    def _extract_timeline_feed(self) -> None:
        """Extrae los posts del timeline del usuario.

        Procesa el array ``timeline_list_feed_units.edges`` para extraer
        todos los posts visibles. Incluye el post fijado (pinned post)
        si está presente.

        Cada post se procesa con ``_build_feed_post_dict`` que extrae
        texto, adjuntos, hashtags, menciones, reacciones y privacidad.

        Actualiza ``self.result["feed"]`` in-place.
        """
        if not self._node_timeline:
            logger.debug("No hay nodo timeline disponible. Feed vacío.")
            return

        # Post fijado (aparece primero en el timeline)
        pinned_post_ref = self._node_timeline.get("profile_pinned_post")
        if pinned_post_ref:
            pinned = self._build_pinned_post_dict(pinned_post_ref)
            if pinned:
                pinned["is_pinned"] = True
                self.result["feed"].append(pinned)
                logger.debug("Post fijado añadido al feed.")

        # Posts del feed paginado
        feed_edges = self._safe_get(
            self._node_timeline, "timeline_list_feed_units", "edges", default=[]
        )
        seen_ids: set[str] = set()

        for edge in (feed_edges or []):
            story = edge.get("node") or {}
            if story.get("__typename") != "Story":
                continue

            post_id = story.get("post_id", story.get("id", ""))
            if post_id in seen_ids:
                continue
            seen_ids.add(post_id)

            post_dict = self._build_feed_post_dict(story)
            post_dict.setdefault("is_pinned", False)
            self.result["feed"].append(post_dict)

        logger.debug("Posts en el timeline: %d.", len(self.result["feed"]))

    def _build_pinned_post_dict(self, post_ref: dict) -> dict[str, Any] | None:
        """Construye el dict del post fijado si está disponible.

        El post fijado puede estar como referencia (solo id) o como
        nodo completo en el nodo timeline.

        Args:
            post_ref: Referencia al post fijado (puede ser solo {id: ...}).

        Returns:
            Dict del post fijado, o None si no hay datos suficientes.
        """
        if not post_ref:
            return None

        # Si es un nodo Story completo
        if post_ref.get("__typename") == "Story":
            return self._build_feed_post_dict(post_ref)

        # Si es solo una referencia (id)
        post_id = post_ref.get("post_id") or post_ref.get("id", "")
        if post_id:
            return {
                "id": post_id,
                "post_id": post_id,
                "is_pinned": True,
                "_note": "Pinned post - datos completos no disponibles en HTML",
            }
        return None

    def _build_feed_post_dict(self, story: dict) -> dict[str, Any]:
        """Construye el diccionario normalizado de un post del timeline.

        Extrae todos los campos disponibles del nodo Story incluyendo
        texto, adjuntos, engagement, privacidad y metadatos temporales.
        Reutiliza los métodos compartidos de FacebookContentParser.

        Args:
            story: Nodo Story del timeline (con ``__isFeedUnit == "Story"``).

        Returns:
            Dict normalizado con todos los campos del post. Los campos
            con datos no disponibles se incluyen con valor None o 0.
        """
        post_id = story.get("post_id", "")

        # --- Texto del mensaje ---
        text = (
            self._safe_get(
                story, "comet_sections", "content", "story",
                "comet_sections", "message", "story", "message", "text",
                default="",
            )
            or self._safe_get(
                story, "comet_sections", "content", "story",
                "comet_sections", "message_container", "story", "message", "text",
                default="",
            )
            or ""
        )

        # --- Hashtags y menciones ---
        msg_ranges = (
            self._safe_get(
                story, "comet_sections", "content", "story",
                "comet_sections", "message", "story", "message", "ranges",
                default=[],
            )
            or self._safe_get(
                story, "comet_sections", "content", "story",
                "comet_sections", "message_container", "story", "message", "ranges",
                default=[],
            )
            or []
        )
        hashtags, mentions = self._parse_ranges(msg_ranges)

        # --- Timestamp y URL del post ---
        ts_story = self._safe_get(
            story, "comet_sections", "timestamp", "story"
        ) or {}
        posted_at = self._parse_timestamp(ts_story.get("creation_time"))
        post_url = ts_story.get("url", "")

        # --- Autor ---
        author = self._extract_author_common(story)

        # --- Adjuntos ---
        attachments = self._extract_attachments_common(story)

        # --- Engagement (reacciones, comentarios) ---
        engagement = self._extract_post_engagement(story)

        # --- Grupo (si el post es de/en un grupo) ---
        group = self._extract_group_common(story)

        # --- Post original (si es un share) ---
        original_post = None
        if story.get("attached_story"):
            original_post = self._extract_original_post_common(story)

        return {
            "__typename": "feed_facebook_post",
            "id": post_id,
            "post_url": post_url,
            "posted_at": posted_at,
            "text": text,
            "hashtags": hashtags,
            "mentions": mentions,
            "author": author,
            "attachments": attachments,
            "group": group,
            "original_post": original_post,
            "is_sponsored": story.get("sponsored_data") is not None,
            **engagement,
        }

    def _extract_post_engagement(self, story: dict) -> dict[str, Any]:
        """Extrae métricas de engagement de un post del timeline.

        Navega la estructura ``story_ufi_container`` para obtener
        reacciones, comentarios y shares.

        Args:
            story: Nodo Story del timeline.

        Returns:
            Dict con ``reaction_count``, ``reactions``, ``comments_count``,
            ``share_count``.
        """
        feedback = self._safe_get(
            story,
            "comet_sections", "feedback", "story",
            "story_ufi_container", "story",
            "feedback_context", "feedback_target_with_context",
            "comet_ufi_summary_and_actions_renderer", "feedback",
        )

        # Fallback desde el story_ufi_container directamente
        if not feedback:
            feedback = self._recursive_search(
                story,
                condition=lambda n: (
                    "reaction_count" in n
                    and "top_reactions" in n
                    and isinstance(n.get("top_reactions"), dict)
                ),
            )

        if not feedback:
            # Mínimo feedback desde comet_ufi_summary_and_actions_renderer
            feedback = self._recursive_search(
                story,
                condition=lambda n: (
                    "top_reactions" in n
                ),
            )

        if not feedback:
            return {
                "reaction_count": 0,
                "reactions": [],
                "comments_count": 0,
                "share_count": 0
            }

        reaction_count_node = feedback.get("reaction_count") or {}
        reaction_count = (
            reaction_count_node.get("count", 0)
            if isinstance(reaction_count_node, dict)
            else int(reaction_count_node or 0)
        )

        reactions = [
            {
                "id": self._safe_get(edge, "node", "id"),
                "type": self._safe_get(edge, "node", "localized_name", default="Unknown"),
                "count": edge.get("reaction_count", 0)
            }
            for edge in self._safe_get(
                feedback, "top_reactions", "edges", default=[]
            )
            if self._safe_get(edge, "node")
        ]

        # Comments count
        comments_count = (
            self._safe_get(feedback, "total_comment_count", default=0)
            or self._safe_get(
                story,
                "comet_sections", "feedback", "story",
                "story_ufi_container", "story",
                "feedback_context", "feedback_target_with_context",
                "comment_list_renderer", "feedback",
                "comment_rendering_instance_for_feed_location", "comments",
                "total_count",
                default=0,
            )
            or 0
        )

        return {
            "reaction_count": reaction_count,
            "reactions": reactions,
            "comments_count": comments_count,
            "share_count": self._safe_get(
                feedback, "share_count", "count", default=0
            )
        }

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result`` con metadatos del tráfico GraphQL del perfil."""
        self._parse_facebook_traffic(operation_patterns=_PROFILE_GRAPHQL_OPERATIONS)

    def _extract_intro_card_info(self) -> None:
        """Extrae educación, ciudad actual y lugar de origen del perfil."""
        self.result["education"]    = self._get_intro_card("INTRO_CARD_EDUCATION")
        self.result["current_city"] = self._get_intro_card("INTRO_CARD_CURRENT_CITY")
        self.result["hometown"]     = self._get_intro_card("INTRO_CARD_HOMETOWN")

    def _get_intro_card(self, card_type: str) -> dict[str, str]:
        """Busca un nodo INTRO_CARD por tipo y devuelve text, name, url, id."""
        empty = {"text": "", "name": "", "url": "", "id": ""}

        for block in self._blocks:
            nodes = self._find_all_nodes(
                block,
                condition=lambda n, t=card_type: (
                    n.get("timeline_context_list_item_type") == t
                ),
            )
            if not nodes:
                continue

            title = self._safe_get(
                nodes[0], "renderer", "context_item", "title", default={}
            ) or {}
            text   = title.get("text", "")
            ranges = title.get("ranges", [])
            entity = ranges[0].get("entity", {}) if ranges else {}

            return {
                "text": text,
                "name": entity.get("short_name") or entity.get("name") or "",
                "url":  (entity.get("url")
                         or entity.get("comet_url")
                         or entity.get("profile_url")
                         or ""),
                "id":   entity.get("id", ""),
            }

        return empty
    
    def _extract_contact_info(self) -> None:
        """Extrae email, teléfono, sitios web y cuentas de otras redes sociales.

        Todos los datos provienen de nodos ``INTRO_CARD_*`` embebidos en el HTML:

        - ``INTRO_CARD_PROFILE_EMAIL`` → ``contact_info.email``
        - ``INTRO_CARD_PHONE``         → ``contact_info.phone``
        - ``INTRO_CARD_WEBSITE``       → ``contact_info.websites[]``
        - ``INTRO_CARD_OTHER_ACCOUNT`` → ``contact_info.social_accounts[]``

        Para las URL de sitios web y redes sociales, el enlace bruto es un
        redirect de Facebook (``l.facebook.com/l.php?u=...``). Se extrae
        automáticamente la URL destino real.

        Los campos no presentes en el HTML quedan como cadena vacía o lista
        vacía según corresponda.

        Actualiza ``self.result["contact_info"]`` in-place.
        """
        _CARD_MAP = {
            "INTRO_CARD_PROFILE_EMAIL": "email",
            "INTRO_CARD_PHONE":         "phone",
            "INTRO_CARD_WEBSITE":       "website",
            "INTRO_CARD_OTHER_ACCOUNT": "social",
        }

        contact = self.result["contact_info"]

        for block in self._blocks:
            for card_type, field in _CARD_MAP.items():
                nodes = self._find_all_nodes(
                    block,
                    condition=lambda n, t=card_type: (
                        n.get("timeline_context_list_item_type") == t
                    ),
                )
                for node in nodes:
                    ci     = self._safe_get(node, "renderer", "context_item") or {}
                    title  = ci.get("title") or {}
                    plain  = ci.get("plaintext_title") or {}
                    ranges = title.get("ranges", [])
                    entity = ranges[0].get("entity", {}) if ranges else {}

                    # Texto visible en el perfil
                    display = plain.get("text") or title.get("text", "")
                    # URL del enlace (puede ser redirect de FB)
                    raw_url = (
                        entity.get("url")
                        or entity.get("external_url")
                        or ""
                    )
                    clean_url = self._clean_fb_redirect_url(raw_url)

                    if field == "email" and not contact["email"]:
                        contact["email"] = display

                    elif field == "phone" and not contact["phone"]:
                        contact["phone"] = display

                    elif field == "website":
                        final_url = clean_url or display
                        if final_url and not any(
                            w["url"] == final_url for w in contact["websites"]
                        ):
                            contact["websites"].append({
                                "display": display,
                                "url": final_url,
                            })

                    elif field == "social":
                        platform = self._detect_social_platform(clean_url, display)
                        entry = {
                            "platform": platform,
                            "handle":   display,
                            "url":      clean_url,
                        }
                        if entry not in contact["social_accounts"]:
                            contact["social_accounts"].append(entry)

    @staticmethod
    def _clean_fb_redirect_url(url: str) -> str:
        """Extrae la URL destino real desde un redirect de Facebook.

        Los enlaces externos en el perfil pasan por
        ``https://l.facebook.com/l.php?u=<url_encoded>...``.
        Este método extrae y decodifica la URL destino.

        Args:
            url: URL cruda (puede ser redirect de FB o URL directa).

        Returns:
            URL destino limpia, o la URL original si no es un redirect de FB.

        Example:
            >>> ProfileParser._clean_fb_redirect_url(
            ...     "https://l.facebook.com/l.php?u=https%3A%2F%2Finstagram.com%2Fuser"
            ... )
            'https://instagram.com/user'
        """
        if not url or "l.facebook.com/l.php" not in url:
            return url
        match = re.search(r"[?&]u=(https?[^&]+)", url)
        if match:
            from urllib.parse import unquote
            return unquote(match.group(1))
        return url

    def _detect_social_platform(self, url: str, text: str) -> str:
        """Detecta la red social a partir de la URL o el texto del handle.

        Args:
            url: URL limpia del enlace externo.
            text: Texto visible (handle o nombre de usuario).

        Returns:
            Nombre de la plataforma en minúsculas (ej. ``"instagram"``),
            o ``"other"`` si no coincide con ningún patrón conocido.
        """
        combined = (url + " " + text).lower()
        for platform, pattern in _SOCIAL_PATTERNS:
            if pattern in combined:
                return platform
        return "other"

    # ==================================================================
    # UTILIDADES
    # ==================================================================

    def _extract_og_field(self, property_name: str) -> str:
        """Extrae el contenido de un meta tag OG o estándar.

        Args:
            property_name: Nombre del property del meta tag
                (ej. ``"og:title"``, ``"og:image"``).

        Returns:
            Contenido del meta tag, o cadena vacía si no existe.
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.html_content, "html.parser")
        tag = soup.find("meta", {"property": property_name})
        if not tag:
            tag = soup.find("meta", {"name": property_name})
        return tag.get("content", "") if tag else ""

    @staticmethod
    def _parse_human_number(text: str) -> int:
        """Convierte un número en formato legible (K, M, B) a int.

        Args:
            text: Número en formato humano (ej. ``"1.4K"``, ``"1,413"``).

        Returns:
            Valor entero, o 0 si no se puede parsear.
        """
        if not text:
            return 0

        clean = text.strip().upper().replace(",", "").replace(".", "")
        # Con sufijo y punto decimal "1.4K" → ya limpiamos el punto
        # Manejar "1K", "14K", "1M" etc.
        multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

        # Re-parsear con decimal para "1.4K"
        clean_original = text.strip().upper().replace(",", "")
        for suffix, mult in multipliers.items():
            if clean_original.endswith(suffix):
                try:
                    return int(float(clean_original[:-1]) * mult)
                except ValueError:
                    return 0

        try:
            return int(float(clean))
        except ValueError:
            return 0