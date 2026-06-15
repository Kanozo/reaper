"""
parsers/facebook/group_parser.py

Parser para páginas de grupos de Facebook (home + about).

Fuentes de datos según la página capturada:

    **Página home** (``/groups/<vanity>/``):
        - Block con ``profile_header_renderer``: id, name, url, vanity,
          privacy_info, cover_photo, member_count (formatted_count_text),
          group_content_views (tabs disponibles), viewer_join_state.
        - Block con ``group_feed`` / relay node+cursor: Stories del feed
          (post_id, actor, message, creation_time, reactions).
        - Block con ``if_viewer_can_see_highlight_units``: posts destacados
          con sus post_ids.
        - Block con ``description_with_entities``: descripción del grupo.
        - **HTML renderizado**: fotos recientes (recent media) con fbid y
          thumbnail CDN — única fuente de fotos en la página home.

    **Página about** (``/groups/<vanity>/about/``):
        - Block con ``about_info_items``: privacidad detallada, historial
          (create_time), actividad (posts_last_day, total_members).
        - Block con ``facepile_admin_profiles``: admins.

    **Tráfico GraphQL** (``GroupsCometFeedRegularStoriesPaginationQuery``):
        - Stories adicionales paginadas para el ``feed``.

Python: 3.11+
"""
from reaper.utils.logger import get_logger
import re
from typing import Any

from bs4 import BeautifulSoup

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

logger = get_logger(__name__)

_GROUP_GRAPHQL_OPERATIONS = [
    "GroupsCometFeedRegularStoriesPaginationQuery",
    "GroupsCometFeed",
    "GroupHome",
]

_MEMBER_COUNT_PATTERN = re.compile(
    r"^([\d\s,\.]+)\s*(mil\b|k\b)?", re.IGNORECASE
)
_PERMALINK_POST_ID_PATTERN = re.compile(r"/permalink/(\d+)/")
_PHOTO_FBID_PATTERN = re.compile(r"fbid=(\d+)")


class GroupParser(FacebookContentParser):
    """Parser para páginas de grupos de Facebook.

    Soporta tanto la página home (``/groups/<vanity>/``) como la página
    about (``/groups/<vanity>/about/``), extrayendo los datos disponibles
    en cada caso. Los nodos internos se asignan en ``_locate_group_blocks``.

    Attributes:
        _node_header:  Nodo ``profile_header_renderer.group`` (page home).
        _node_about:   Nodo con ``about_info_items`` (page about).
        _node_feed:    Nodo con ``group_feed`` o relay feed (page home).
        _node_main:    Nodo con ``description_with_entities`` y highlights
                       (page home, block 47-style).
        result:        Diccionario acumulador con los datos extraídos.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self._node_header: dict | None = None
        self._node_about:  dict | None = None
        self._node_feed:   dict | None = None
        self._node_main:   dict | None = None

        self.result.update({
            "__typename":       "facebook_group",
            "group_url":        self.original_url,
            "requested_post_id": self._extract_post_id_from_url(self.original_url),
            "id":               None,
            "name":             None,
            "url":              None,
            "vanity":           None,
            "description":      None,
            "is_private":       False,
            "baner":      {},
            "total_members":    0,
            "posts_last_day":   0,
            "created_at":       None,
            "admins":           [],
            "admins_count":     0,
            "highlight_post_ids": [],  # posts destacados (featured)
            "photos":           [],    # fotos recientes (recent media)
            "feed":             [],    # posts del feed
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el pipeline completo de extracción del grupo.

        Flujo:
            1. Extraer bloques JSON del HTML.
            2. Localizar los nodos internos.
            3. Extraer campos disponibles (graceful — cada método falla
               silenciosamente si su nodo no está disponible).
            4. Extraer fotos y feed del HTML renderizado.
            5. Enriquecer con tráfico GraphQL.

        Returns:
            Diccionario con todos los campos del grupo.
        """
        logger.debug("Iniciando extracción de GRUPO | url=%s", self.final_url)

        self._blocks = self._extract_json_blocks()

        if not self._locate_group_blocks():
            self.result["error"] = "No se encontraron bloques de grupo en el HTML."
            logger.warning("GroupParser: sin bloques en %s", self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_identity()
            self._extract_privacy()
            self._extract_cover_photo()
            self._extract_description()
            self._extract_member_count()

            self._extract_history()
            self._extract_activity_metrics()
            self._extract_admins()

            self._extract_highlight_posts()
            self._extract_feed_from_blocks()
            self._extract_photos_from_html()
            self._parse_traffic()
            logger.info(
                "Grupo parseado | name=%s feed=%d photos=%d",
                self.result.get("name"),
                len(self.result["feed"]),
                len(self.result["photos"]),
            )
        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando grupo: %s", exc, exc_info=True)

        return self.result

    # ==================================================================
    # LOCALIZACIÓN DE NODOS
    # ==================================================================

    def _locate_group_blocks(self) -> bool:
        """Localiza y asigna los nodos internos desde los bloques JSON.

        Busca recursivamente en todos los bloques JSON cuatro tipos de nodos:

        - ``profile_header_renderer`` → ``_node_header``
        - ``about_info_items``        → ``_node_about``
        - ``group_feed`` / ``highlight_units`` → ``_node_main``
        - ``description_with_entities`` (en comet_discussion_tab_cards) → ``_node_about``
        - relay ``data.node`` (Story) → ``_node_feed``

        Returns:
            True si se encontró al menos un nodo de grupo.
        """
        for block in self._blocks:
            # ── Patrón 1: __bbox.result.data.group ───────────────────
            wrapper = self._recursive_search(
                block,
                condition=lambda n: isinstance(
                    self._safe_get(n, "__bbox", "result", "data", "group"), dict
                ) and bool(
                    self._safe_get(n, "__bbox", "result", "data", "group")
                ),
            )
            if wrapper:
                g = self._safe_get(wrapper, "__bbox", "result", "data", "group") or {}

                if "profile_header_renderer" in g and self._node_header is None:
                    inner = self._safe_get(g, "profile_header_renderer", "group")
                    if isinstance(inner, dict):
                        self._node_header = inner
                        logger.debug("_node_header localizado (id=%s)", inner.get("id"))

                if "about_info_items" in g and self._node_about is None:
                    self._node_about = g
                    logger.debug("_node_about localizado.")

                if (
                    "group_feed" in g
                    or "if_viewer_can_see_highlight_units" in g
                ) and self._node_main is None:
                    self._node_main = g
                    logger.debug("_node_main localizado (keys=%s)",
                                 list(g.keys())[:6])

                if "group_feed" in g and self._node_feed is None:
                    self._node_feed = g
                    logger.debug("_node_feed localizado.")
                continue

            # ── Patrón 2: nodo con description_with_entities ─────────
            # Está en comet_discussion_tab_cards (about card inline en home)
            desc_node = self._recursive_search(
                block,
                condition=lambda n: (
                    isinstance(n, dict)
                    and "description_with_entities" in n
                    and isinstance(n.get("description_with_entities"), dict)
                    and bool((n["description_with_entities"] or {}).get("text"))
                ),
            )
            if desc_node and self._node_about is None:
                self._node_about = desc_node
                logger.debug(
                    "_node_about (description) localizado (keys=%s)",
                    list(desc_node.keys())[:6],
                )
                continue

            # ── Patrón 3: relay node — Story directa ─────────────────
            relay = self._recursive_search(
                block,
                condition=lambda n: isinstance(
                    self._safe_get(n, "__bbox", "result", "data", "node"), dict
                ) and self._safe_get(
                    n, "__bbox", "result", "data", "node", "__typename"
                ) == "Story",
            )
            if relay and self._node_feed is None:
                self._node_feed = self._safe_get(relay, "__bbox", "result", "data")
                logger.debug("_node_feed (relay Story) localizado.")

        found = any([
            self._node_header, self._node_about,
            self._node_main,   self._node_feed,
        ])
        if not found:
            logger.debug("Ningún bloque de grupo localizado.")
        return found

    # ==================================================================
    # EXTRACCIÓN DE CAMPOS
    # ==================================================================

    def _extract_identity(self) -> None:
        """Extrae id, name, url y vanity del grupo.

        Usa ``_node_header`` (home) o ``_node_about`` (about) como fuente.
        Actualiza ``self.result`` con los campos de identidad.
        """
        header = self._node_header or {}
        about  = self._node_about  or {}
        main   = self._node_main   or {}

        self.result["id"] = (
            header.get("id") or about.get("id") or main.get("id")
        )
        self.result["name"] = (
            header.get("name")
            or self._safe_get(header, "featurable_title", "text")
            or about.get("name")
            or main.get("name")
        )
        self.result["url"] = (
            header.get("url") or about.get("url") or self.final_url
        )
        self.result["vanity"] = (
            header.get("group_address")
            or header.get("vanity")
            or main.get("vanity")
            or main.get("group_address")
        )

    def _extract_privacy(self) -> None:
        """Extrae el nivel de privacidad del grupo.

        Fuentes (en orden de preferencia):
        1. ``_node_header.privacy_info`` (home) — tiene icon_name y title.
        2. ``about_info_items`` con ``*Privacy*`` typename (about).

        Actualiza ``self.result["privacy"]`` in-place.
        """
        # Fuente 1: header (home page)
        pi = (self._node_header or {}).get("privacy_info") or {}
        if pi:
            level = self._safe_get(pi, "title", "text", default="")
            self.result["is_private"] = level.lower() in ("private", "privado", "private group")
            return

        # Fuente 2: about_info_items (about page)
        for item in self._safe_get(self._node_about, "about_info_items") or []:
            if "Privacy" not in (item.get("__typename") or ""):
                continue
            privacy_info = self._safe_get(item, "group", "privacy_info") or {}
            level = self._safe_get(privacy_info, "label", "text", default="")
            self.result["is_private"] = level.lower() in ("private", "privado")
            break

    def _extract_cover_photo(self) -> None:
        """Extrae la foto de portada del grupo.

        Navega ``_node_header.cover_renderer.cover_photo_content.photo``.
        Actualiza ``self.result["cover_photo"]`` in-place.
        """
        cover_content = self._safe_get(
            self._node_header, "cover_renderer", "cover_photo_content"
        ) or {}
        photo = cover_content.get("photo") or {}
        if not photo:
            return

        image = photo.get("image") or {}
        self.result["baner"] = {
            "photo_id": photo.get("id"),
            "cdn_uri":  image.get("uri"),
            "width":    image.get("width"),
            "height":   image.get("height"),
        }

    def _extract_description(self) -> None:
        """Extrae la descripción del grupo.

        Busca ``description_with_entities.text`` en ``_node_main`` y en
        los items de ``_node_about`` (ruta gateada y directa).

        Actualiza ``self.result["description"]`` in-place.
        """
        # Fuente 1: _node_main (home page)
        desc = self._safe_get(
            self._node_main, "description_with_entities", "text", default=""
        )
        if desc:
            self.result["description"] = desc
            return

        # Fuente 2: about page — ruta gateada
        desc = self._safe_get(
            self._node_about,
            "if_viewer_can_view_description",
            "description_with_entities", "text",
            default="",
        )
        if desc:
            self.result["description"] = desc
            return

        # Fuente 3: about page — directa
        desc = self._safe_get(
            self._node_about, "description_with_entities", "text", default=""
        )
        self.result["description"] = desc or ""

    def _extract_member_count(self) -> None:
        """Extrae el recuento de miembros del grupo.

        Fuentes:
        1. ``_node_header.group_member_profiles.formatted_count_text`` (home).
        2. ``about_info_items`` con actividad (about).

        Actualiza ``total_members_text`` y ``total_members`` in-place.
        """
        # Fuente 1: home
        fmt = self._safe_get(
            self._node_header, "group_member_profiles", "formatted_count_text"
        )
        if fmt:
            self.result["total_members"] = self._parse_member_count(fmt)
            return

        # Fuente 2: about activity section
        activity = self._safe_get(
            self._node_about, "if_viewer_can_see_activity_section"
        ) or {}
        text = activity.get("group_total_members_info_text") or ""
        self.result["total_members"] = self._parse_member_count(text)

    def _extract_history(self) -> None:
        """Extrae la fecha de creación del grupo (solo desde about page).

        Actualiza ``self.result["created_at"]`` in-place.
        """
        for item in self._safe_get(self._node_about, "about_info_items") or []:
            if "History" not in (item.get("__typename") or ""):
                continue
            ts = self._safe_get(item, "group", "group_history", "create_time")
            if ts:
                self.result["created_at"] = self._parse_timestamp(ts)
            break

    def _extract_activity_metrics(self) -> None:
        """Extrae posts_last_day (solo desde about page).

        Actualiza ``self.result["posts_last_day"]`` in-place.
        """
        activity = self._safe_get(
            self._node_about, "if_viewer_can_see_activity_section"
        ) or {}
        self.result["posts_last_day"] = (
            activity.get("number_of_posts_in_last_day") or 0
        )

    def _extract_admins(self) -> None:
        """Extrae la lista de administradores del grupo (solo desde about page).

        Actualiza ``self.result["admins"]`` y ``self.result["admins_count"]``.
        """
        admin_profiles = (self._node_about or {}).get(
            "facepile_admin_profiles", {}
        ) or {}
        self.result["admins_count"] = admin_profiles.get("count") or 0
        admins = []
        for edge in admin_profiles.get("edges") or []:
            node = edge.get("node") or {}
            admins.append({
                "id":   node.get("id"),
                "name": node.get("name"),
                "url":  node.get("url"),
            })
        self.result["admins"] = admins

    def _extract_highlight_posts(self) -> None:
        """Extrae los post_ids de los posts destacados (highlight_units).

        Los posts destacados están en:
        ``_node_main.if_viewer_can_see_highlight_units.highlight_units.edges``

        Cada edge contiene un ``node.story`` con el ``post_id``.
        Actualiza ``self.result["highlight_post_ids"]`` in-place.
        """
        hu = self._safe_get(
            self._node_main,
            "if_viewer_can_see_highlight_units",
            "highlight_units",
        ) or {}
        edges = hu.get("edges") or []
        highlight_posts = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            story = self._safe_get(edge, "node", "story") or {}
            post = self._build_highlight_post_dict(story)
            highlight_posts.append(post)
            
        self.result["highlight_posts"] = highlight_posts
        logger.debug("Highlight posts: %d", len(highlight_posts))

    # ==================================================================
    # FEED (posts del grupo)
    # ==================================================================

    def _extract_feed_from_blocks(self) -> None:
        """Extrae los posts del feed desde los bloques JSON.

        Procesa dos patrones de datos:

        1. ``_node_feed.group_feed.edges``: edges del feed en la página home,
           donde el primer edge suele ser un header unit (sin post_id).
        2. ``_node_feed.node`` (relay paginado): Story directa con todos
           los campos (post_id, creation_time, actor, message, reactions).

        Actualiza ``self.result["feed"]`` in-place.
        """
        seen_ids: set[str] = set()

        # Patrón 1: group_feed.edges
        feed_node = (self._node_feed or {})
        group_feed = feed_node.get("group_feed") or {}
        for edge in group_feed.get("edges") or []:
            story = (edge.get("node") or {})
            if story.get("__typename") == "Story":
                self._add_story_to_feed(story, seen_ids)

        # Patrón 2: relay node (Story directa del bloque secondary)
        relay_node = feed_node.get("node") or {}
        if relay_node.get("__typename") == "Story":
            self._add_story_to_feed(relay_node, seen_ids)

        # Patrón 3: buscar Stories con post_id en todos los bloques
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
                self._add_story_to_feed(story, seen_ids)

        logger.debug("Feed desde bloques: %d posts", len(self.result["feed"]))

    def _add_story_to_feed(self, story: dict, seen_ids: set[str]) -> None:
        """Construye un dict de post desde un nodo Story y lo añade al feed.

        Args:
            story:    Nodo Story del JSON.
            seen_ids: Set mutable de post_ids ya procesados (deduplicación).
        """
        post_id = story.get("post_id") or self._find_post_id_in_story(story)
        if not post_id or post_id in seen_ids:
            return
        seen_ids.add(str(post_id))

        try:
            post = self._build_post_dict(story)
            self.result["feed"].append(post)
        except Exception as exc:
            logger.debug("_add_story_to_feed error post_id=%s: %s", post_id, exc)

    def _build_post_dict(self, story: dict) -> dict[str, Any]:
        """Construye el diccionario normalizado de un post del grupo.

        Extrae datos de las secciones ``comet_sections.timestamp``,
        ``comet_sections.content.story`` y ``comet_sections.feedback``.
        Si el post es un share, el mensaje real está en ``attached_story``.

        Args:
            story: Nodo Story completo del JSON.

        Returns:
            dict con los campos del post.
        """
        cs = story.get("comet_sections") or {}

        # ── Timestamp ────────────────────────────────────────────────
        ts_story = self._safe_get(cs, "timestamp", "story") or {}
        creation_time = ts_story.get("creation_time")
        post_url = ts_story.get("url") or story.get("permalink_url") or ""

        # ── Content story ────────────────────────────────────────────
        content_story = self._safe_get(cs, "content", "story") or {}
        post_id = story.get("post_id") or content_story.get("post_id")

        # ── Actor ────────────────────────────────────────────────────
        actors = content_story.get("actors") or story.get("actors") or []
        actor: dict[str, Any] = {}
        if actors:
            a = actors[0]
            actor = {
                "id":       a.get("id"),
                "name":     a.get("name"),
                "__typename": a.get("__typename"),
            }

        # ── Message ──────────────────────────────────────────────────
        # Para posts propios: content_story.message
        # Para shares: content_story.attached_story.message
        msg_text = self._safe_get(content_story, "message", "text") or ""
        if not msg_text:
            attached = content_story.get("attached_story") or {}
            msg_text = self._safe_get(attached, "message", "text") or ""

        # ── Attachments / fotos del post ─────────────────────────────
        attachments = content_story.get("attachments") or []
        # También en attached_story
        if not attachments:
            attached = content_story.get("attached_story") or {}
            attachments = attached.get("attachments") or []

        media_items = self._extract_media_from_attachments(attachments)

        # ── Reactions / engagement ───────────────────────────────────
        reaction_count, comment_count, share_count = self._extract_counts(story)

        return {
            "post_id":        str(post_id) if post_id else None,
            "post_url":       post_url,
            "posted_at":      self._parse_timestamp(creation_time),
            "actor":          actor,
            "message":        msg_text,
            "media":          media_items,
            "reaction_count": reaction_count,
            "comment_count":  comment_count,
            "share_count":    share_count,
        }

    def _build_highlight_post_dict(self, story: dict) -> dict[str, Any]:
        """Construye el diccionario normalizado de un post del grupo.

        Extrae datos de las secciones ``comet_sections.timestamp``,
        ``comet_sections.content.story`` y ``comet_sections.feedback``.
        Si el post es un share, el mensaje real está en ``attached_story``.

        Args:
            story: Nodo Story completo del JSON.

        Returns:
            dict con los campos del post.
        """
        cs = story.get("comet_sections") or {}

        # ── Timestamp ────────────────────────────────────────────────
        metadata = self._safe_get(story, 'comet_sections',
                                       'context_layout', 'story', 'comet_sections',
                                       'metadata')
        node_time = self._recursive_search(data=metadata, 
                                               condition=lambda n: n.get("__typename") == "CometFeedStoryMinimizedTimestampStrategy",
                                           )
        creation_time = None
        if node_time:
            creation_time = self._safe_get(node_time, 'story', 'creation_time')
            
        # ── Content story ────────────────────────────────────────────
        content_story = self._safe_get(cs, "content", "story") or {}
        post_id = story.get("post_id") or content_story.get("post_id")

        # ── Acutor ────────────────────────────────────────────────────
        author = self._extract_author_common(story)

        # ── Message ──────────────────────────────────────────────────
        # Para posts propios: content_story.message
        # Para shares: content_story.attached_story.message
        msg_text = self._safe_get(content_story, "message", "text") or ""
        if not msg_text:
            attached = content_story.get("attached_story") or {}
            msg_text = self._safe_get(attached, "message", "text") or ""

        # ── Attachments / fotos del post ─────────────────────────────
        attachments = self._extract_attachments_common(story)
        # También en attached_story
        if not attachments:
            attached = content_story.get("attached_story") or {}
            attachments = attached.get("attachments") or []

        #media_items = self._extract_media_from_attachments(attachments)
        post_url = f"{self.result.get('group_url')}/posts/{post_id}/"
        # ── Reactions / engagement ───────────────────────────────────
        reaction_count, comment_count, share_count = self._extract_counts(story)
        Feedback = self._extract_feedback_common(story)
        return {
            "__typename":     "highlight_post",
            "id":             str(post_id) if post_id else None,
            "post_url":       post_url,
            "permalink_url":  self._safe_get(story, "url", default=""),
            "posted_at":      self._parse_timestamp(creation_time),

            "author":         author,
            "text":           msg_text,

            "attachments":    attachments,
            "reaction_count": reaction_count,
            "comments_count": comment_count,
            "share_count":    share_count,
        }
    
        # "author": {
        #         "id": "100059022512747",
        #         "name": "Ikan Elenu Oni Chango",
        #         "profile_url": "https://www.facebook.com/iyawo.ikanlenu",
        #         "gender": "FEMALE",
        #         "avatar": "https://scontent.fptp4-1.fna.fbcdn.net/v/t39.30808-1/685603340_1327784542532284_673774723795755494_n.jpg?stp=c0.69.1080.1080a_cp0_dst-jpg_tt6&cstp=mx1080x1080&ctp=s40x40&_nc_cat=102&ccb=1-7&_nc_sid=1d2534&_nc_ohc=ePYWBrbP8_wQ7kNvwHZ_MMO&_nc_oc=AdqGmacybaAtEQ2xEYhJKs1UA6NSaVFB0nmtMImwARUXQBoscPFOLXSQMpoFRXepC3A&_nc_zt=24&_nc_ht=scontent.fptp4-1.fna&_nc_gid=OFf0VqdEnrQEfscafCsoTw&_nc_ss=70289&oh=00_Af8Z9bctYHrJBSbOPUz--U0N_d20Fa6m8rTWrURTi-lJlg&oe=6A33CD0E"
        #     }

        # "reactions": [
        #     {
        #         "id": "115940658764963",
        #         "type": "Haha",
        #         "count": 1537
        #     }
        # ],
        # "hashtags": [],
        # "mentions": [],

    def _extract_media_from_attachments(
        self, attachments: list
    ) -> list[dict[str, Any]]:
        """Extrae items de media (fotos/vídeos) de una lista de attachments.

        Navega la ruta ``styles.attachment.all_subattachments.nodes``
        (para albums) o ``styles.attachment.media`` (para media único).

        Args:
            attachments: Lista de attachment dicts del Story.

        Returns:
            Lista de dicts con ``photo_id``, ``uri``, ``width``, ``height``,
            ``__typename``.
        """
        media_items: list[dict[str, Any]] = []

        for att in attachments:
            if not isinstance(att, dict):
                continue
            attachment = self._safe_get(att, "styles", "attachment") or {}

            # Album (múltiples fotos)
            nodes = self._safe_get(
                attachment, "all_subattachments", "nodes", default=[]
            ) or []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                media = node.get("media") or {}
                item = self._build_media_item(media)
                if item:
                    media_items.append(item)

            # Media único
            if not nodes:
                media = attachment.get("media") or {}
                item = self._build_media_item(media)
                if item:
                    media_items.append(item)

        return media_items

    def _build_media_item(self, media: dict) -> dict[str, Any] | None:
        """Construye un dict de media desde un nodo media de attachment.

        Args:
            media: Nodo media (Photo o Video) del attachment.

        Returns:
            dict con los campos del media, o None si no tiene datos útiles.
        """
        if not media or not isinstance(media, dict):
            return None

        typename = media.get("__typename")
        media_id = media.get("id")

        # Buscar imagen: photo_image > large_preview > image
        img = (
            media.get("photo_image")
            or media.get("large_preview")
            or media.get("image")
            or {}
        )
        uri = img.get("uri") or ""

        if not media_id and not uri:
            return None

        return {
            "__typename": typename,
            "photo_id":   media_id,
            "uri":        uri,
            "uri_base":   uri.split("?")[0] if uri else None,
            "width":      img.get("width"),
            "height":     img.get("height"),
        }

    def _extract_counts(
        self, story: dict
    ) -> tuple[int, int, int]:
        """Extrae reaction_count, comment_count y share_count de un Story.

        Busca recursivamente el primer nodo que tenga ``reaction_count``
        con campo ``count`` numérico.

        Args:
            story: Nodo Story completo.

        Returns:
            Tupla (reaction_count, comment_count, share_count).
        """
        def find_first_feedback(obj: Any, depth: int = 0) -> dict:
            if depth > 10:
                return {}
            if isinstance(obj, dict):
                rc = obj.get("reaction_count")
                if isinstance(rc, dict) and rc.get("count") is not None:
                    return obj
                for v in obj.values():
                    if v:
                        r = find_first_feedback(v, depth + 1)
                        if r:
                            return r
            elif isinstance(obj, list):
                for item in obj:
                    if item:
                        r = find_first_feedback(item, depth + 1)
                        if r:
                            return r
            return {}

        fb = find_first_feedback(story)
        reaction_count = (fb.get("reaction_count") or {}).get("count") or 0
        comment_count  = (fb.get("comment_count")  or {}).get("count") or 0
        share_count    = (fb.get("share_count")    or {}).get("count") or 0
        return reaction_count, comment_count, share_count

    # ==================================================================
    # FOTOS RECIENTES (HTML renderizado)
    # ==================================================================

    def _extract_photos_from_html(self) -> None:
        """Extrae las fotos recientes del grupo desde el HTML renderizado.

        Las fotos del grupo en la página home se renderizan como links
        ``<a href="/photo/?fbid=<id>&set=g.<group_id>">`` con una ``<img>``
        thumbnail dentro. La CDN URI sin parámetros da acceso a la imagen
        en mayor calidad.

        No se usan clases CSS (que Facebook rota y ofusca) sino el patrón
        semántico del href y la presencia de img dentro del link.

        Actualiza ``self.result["photos"]`` in-place.
        """
        try:
            soup = BeautifulSoup(self.html_content, "html.parser")
        except Exception as exc:
            logger.debug("_extract_photos_from_html: BS4 error: %s", exc)
            return

        group_id = str(self.result.get("id") or "")
        # set=g.<group_id>  → foto reciente del grupo
        # set=pcb.<id>      → foto de post multi-imagen del grupo
        # set=a.<id>        → álbum de portada/perfil → EXCLUIR
        _HREF_PAT = re.compile(
            r"/photo/\?fbid=(\d+)&(?:amp;)?set=([a-z]+)\.(\d+)"
        )
        seen_fbids: set[str] = set()
        photos: list[dict[str, Any]] = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = _HREF_PAT.search(href)
            if not m:
                continue

            fbid       = m.group(1)
            set_prefix = m.group(2)   # "g", "a", "pcb", etc.
            set_id     = m.group(3)

            # Solo fotos del grupo (set=g.) o de posts del grupo (set=pcb.)
            # Excluir álbumes de portada (set=a.) y otros
            if set_prefix == "a":
                continue
            # Si tenemos group_id, verificar que las fotos g. sean de este grupo
            if set_prefix == "g" and group_id and set_id != group_id:
                continue

            if fbid in seen_fbids:
                continue
            seen_fbids.add(fbid)

            img = a.find("img")
            if not img:
                parent = a.parent
                if parent:
                    img = parent.find("img")

            thumbnail_url  = img.get("src") if img else None
            thumbnail_base = thumbnail_url.split("?")[0] if thumbnail_url else None
            alt_text       = img.get("alt") if img else None

            photos.append({
                "photo_id":       fbid,
                "photo_url":      (
                    f"https://www.facebook.com/photo/?fbid={fbid}"
                    f"&set=g.{group_id}" if group_id else
                    f"https://www.facebook.com/photo/?fbid={fbid}"
                ),
                "thumbnail_url":  thumbnail_url,
                "thumbnail_base": thumbnail_base,
                "alt":            alt_text,
            })

        self.result["photos"] = photos
        logger.debug("Fotos recientes extraídas: %d", len(photos))

    # ==================================================================
    # TRÁFICO GRAPHQL
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result["feed"]`` con Stories del tráfico GraphQL.

        Procesa las operaciones ``GroupsCometFeedRegularStoriesPaginationQuery``
        capturadas durante el scroll, normalizando el body con
        ``normalize_body`` para manejar dict y list[dict] (Incremental Delivery).

        Actualiza ``self.result["feed"]`` in-place.
        """
        if not self._has_graphql_traffic():
            logger.debug("_parse_traffic: sin tráfico GraphQL.")
            return

        seen_ids: set[str] = {
            str(p.get("post_id") or "")
            for p in self.result["feed"]
            if p.get("post_id")
        }

        added = 0
        for operation in _GROUP_GRAPHQL_OPERATIONS:
            bodies = self._get_graphql_response_by_operation(operation)
            if not bodies:
                continue
            logger.debug("_parse_traffic: %d bodies para '%s'", len(bodies), operation)

            for body in bodies:
                for fragment in self.traffic.normalize_body(body):
                    added += self._process_traffic_fragment(fragment, seen_ids)

        logger.debug("_parse_traffic: %d posts añadidos desde tráfico.", added)

    def _process_traffic_fragment(
        self, fragment: dict[str, Any], seen_ids: set[str]
    ) -> int:
        """Procesa un fragmento JSON del tráfico GraphQL.

        Busca Stories en ``data.node`` (relay paginado) y en
        ``data.group.group_feed.edges`` (feed completo).

        Args:
            fragment: Fragmento JSON normalizado.
            seen_ids: Set mutable de post_ids ya procesados.

        Returns:
            Número de posts añadidos.
        """
        added = 0
        data = fragment.get("data") or {}

        # Patrón 1: relay paginado → data.node es la Story
        node = data.get("node") or {}
        if node.get("__typename") == "Story" and node.get("post_id"):
            self._add_story_to_feed(node, seen_ids)
            added += 1
            return added

        # Patrón 2: feed completo → data.group.group_feed.edges
        group = data.get("group") or {}
        for edge in (group.get("group_feed") or {}).get("edges") or []:
            story = edge.get("node") or {}
            if story.get("__typename") == "Story" and story.get("post_id"):
                prev = len(seen_ids)
                self._add_story_to_feed(story, seen_ids)
                if len(seen_ids) > prev:
                    added += 1

        # Patrón 3: búsqueda profunda como fallback
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
    # HELPERS PRIVADOS
    # ==================================================================

    def _find_post_id_in_story(self, story: dict) -> str | None:
        """Busca el post_id en un Story desde sus secciones anidadas.

        Navega ``comet_sections.timestamp.story`` y
        ``comet_sections.content.story`` para encontrar el post_id.

        Args:
            story: Nodo Story.

        Returns:
            post_id como string si se encuentra, None en caso contrario.
        """
        if story.get("post_id"):
            return str(story["post_id"])
        cs = story.get("comet_sections") or {}
        # En el timestamp section está el URL que contiene el post_id
        ts_url = self._safe_get(cs, "timestamp", "story", "url") or ""
        m = re.search(r"/posts/(\d+)", ts_url)
        if m:
            return m.group(1)
        return self._safe_get(cs, "content", "story", "post_id")

    # ==================================================================
    # UTILIDADES ESTÁTICAS
    # ==================================================================

    @staticmethod
    def _extract_post_id_from_url(url: str) -> str | None:
        """Extrae el post_id de una URL de permalink de grupo.

        Args:
            url: URL del grupo (puede incluir /permalink/<id>/).

        Returns:
            El post_id como string, o None si no está en la URL.
        """
        if not url or "permalink" not in url:
            return None
        match = _PERMALINK_POST_ID_PATTERN.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_member_count(text: str) -> int:
        """Parsea el recuento de miembros desde un texto formateado.

        Maneja separadores de miles y sufijos ``mil``/``k``.

        Args:
            text: Texto (ej. ``"23.3K members"``, ``"1.200 miembros"``).

        Returns:
            Entero con el número de miembros, 0 si no se pudo parsear.
        """
        if not text:
            return 0
        clean = text.replace("\u00a0", " ").strip()
        # Extraer número antes de "members/miembros"
        m = re.search(r"([\d][,\d\.]*)\s*(K|k|mil|M|m)?", clean)
        if not m:
            return 0
        raw = m.group(1).replace(",", "").replace(".", "")
        suffix = (m.group(2) or "").lower()
        try:
            n = float(raw)
            if suffix in ("k", "mil"):
                return int(n * 1_000)
            if suffix == "m":
                return int(n * 1_000_000)
            # Si raw tenía decimales (ej "23.3K" → raw="233" ya sin punto)
            # pero "23.3" con punto decimal → tratar como miles si tiene sufijo
            return int(n)
        except ValueError:
            return 0