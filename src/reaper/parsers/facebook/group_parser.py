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
import re
from typing import Any

from bs4 import BeautifulSoup

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.utils.logger import get_logger

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

    El ``feed`` y los ``highlight_posts`` se construyen con la misma
    estructura (vía ``parse_edge`` del parser base), de modo que todos los
    posts comparten el mismo esquema canónico.

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
            "highlight_post_ids": [],  # solo post_ids de highlights
            "highlight_posts":  [],    # posts destacados completos
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
                "Grupo parseado | name=%s feed=%d photos=%d highlights=%d",
                self.result.get("name"),
                len(self.result["feed"]),
                len(self.result["photos"]),
                len(self.result["highlight_posts"]),
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

        Actualiza ``self.result["is_private"]`` in-place.
        """
        pi = (self._node_header or {}).get("privacy_info") or {}
        if pi:
            level = self._safe_get(pi, "title", "text", default="")
            self.result["is_private"] = level.lower() in ("private", "privado", "private group")
            return

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
        Actualiza ``self.result["baner"]`` in-place.
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
        los items de ``_node_about``.

        Actualiza ``self.result["description"]`` in-place.
        """
        desc = self._safe_get(
            self._node_main, "description_with_entities", "text", default=""
        )
        if desc:
            self.result["description"] = desc
            return

        desc = self._safe_get(
            self._node_about,
            "if_viewer_can_view_description",
            "description_with_entities", "text",
            default="",
        )
        if desc:
            self.result["description"] = desc
            return

        desc = self._safe_get(
            self._node_about, "description_with_entities", "text", default=""
        )
        self.result["description"] = desc or ""

    def _extract_member_count(self) -> None:
        """Extrae el recuento de miembros del grupo.

        Fuentes:
        1. ``_node_header.group_member_profiles.formatted_count_text`` (home).
        2. ``about_info_items`` con actividad (about).

        Actualiza ``total_members`` in-place.
        """
        fmt = self._safe_get(
            self._node_header, "group_member_profiles", "formatted_count_text"
        )
        if fmt:
            self.result["total_members"] = self._parse_member_count(fmt)
            return

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

    # ==================================================================
    # HIGHLIGHT POSTS (DESTACADOS)
    # ==================================================================

    def _extract_highlight_posts(self) -> None:
        """Extrae los posts destacados (highlight_units).

        Los posts destacados están en:
        ``_node_main.if_viewer_can_see_highlight_units.highlight_units.edges``

        Cada edge contiene un ``node.story``. Se normaliza cada story a la
        forma estándar con ``_normalize_highlight_story`` y luego se
        construye con ``parse_edge`` (misma estructura que el feed).

        Actualiza ``self.result["highlight_posts"]`` y
        ``self.result["highlight_post_ids"]`` in-place.
        """
        hu = self._safe_get(
            self._node_main,
            "if_viewer_can_see_highlight_units",
            "highlight_units",
        ) or {}
        edges = hu.get("edges") or []
        highlight_posts: list[dict[str, Any]] = []
        highlight_ids: list[str] = []

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            story = self._safe_get(edge, "node", "story") or {}
            if not story:
                continue

            normalized = self._normalize_highlight_story(story)
            post = self.parse_edge(normalized)

            # Los nodos highlight solo exponen ``associated_group.id`` en el
            # feedback, sin name/url/avatar. Se rellenan desde la identidad
            # del grupo ya extraída (self.result), si el id coincide.
            grp = post.get("group") or {}
            grp_id = grp.get("id")
            if grp_id and not grp.get("name"):
                grp["name"] = self.result.get("name")
                grp["url"] = self.result.get("url")
                post["group"] = grp

            post["__typename"] = "highlight_post"
            post_id = normalized.get("post_id")
            if post_id:
                if str(post_id) in highlight_ids:
                    continue
                highlight_ids.append(str(post_id))

            highlight_posts.append(post)

        self.result["highlight_posts"] = highlight_posts
        self.result["highlight_post_ids"] = highlight_ids
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

        Los posts se construyen con ``parse_edge`` (estructura canónica).

        Actualiza ``self.result["feed"]`` in-place.
        """
        seen_ids: set[str] = {
            str(p.get("id") or "") for p in self.result["feed"] if p.get("id")
        }

        feed_node = (self._node_feed or {})
        group_feed = feed_node.get("group_feed") or {}
        for edge in group_feed.get("edges") or []:
            story = (edge.get("node") or {})
            if story.get("__typename") == "Story":
                self._add_story_to_feed(story, seen_ids)

        relay_node = feed_node.get("node") or {}
        if relay_node.get("__typename") == "Story":
            self._add_story_to_feed(relay_node, seen_ids)

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

    # ==================================================================
    # FOTOS RECIENTES (HTML renderizado)
    # ==================================================================

    def _extract_photos_from_html(self) -> None:
        """Extrae las fotos recientes del grupo desde el HTML renderizado.

        Se usan patrones semánticos del href (``/photo/?fbid=...``) en lugar
        de clases CSS. Actualiza ``self.result["photos"]`` in-place.
        """
        try:
            soup = BeautifulSoup(self.html_content, "html.parser")
        except Exception as exc:
            logger.debug("_extract_photos_from_html: BS4 error: %s", exc)
            return

        group_id = str(self.result.get("id") or "")
        href_pat = re.compile(
            r"/photo/\?fbid=(\d+)&(?:amp;)?set=([a-z]+)\.(\d+)"
        )
        seen_fbids: set[str] = set()
        photos: list[dict[str, Any]] = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = href_pat.search(href)
            if not m:
                continue

            fbid       = m.group(1)
            set_prefix = m.group(2)   # "g", "a", "pcb", etc.
            set_id     = m.group(3)

            if set_prefix == "a":
                continue
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
        ``normalize_body``. Reutiliza ``_process_traffic_fragment`` de la
        base (incluye el patrón ``data.node`` tipo ``Group`` con
        ``group_feed.edges``).

        Actualiza ``self.result["feed"]`` in-place.
        """
        if not self._has_graphql_traffic():
            logger.debug("_parse_traffic: sin tráfico GraphQL.")
            return

        seen_ids: set[str] = {
            str(p.get("id") or "")
            for p in self.result["feed"]
            if p.get("id")
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
            return int(n)
        except ValueError:
            return 0
