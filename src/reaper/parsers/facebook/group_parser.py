from reaper.utils.logger import get_logger
import re
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

logger = get_logger(__name__)

# Patrones de operaciones GraphQL relevantes para grupos
_GROUP_GRAPHQL_OPERATIONS = ["GroupHome", "GroupAbout", "GroupMembers", "GroupsCometFeedRegularStoriesPaginationQuery"]

# Regex para parsear el conteo de miembros desde texto (ej. "1.200 miembros")
_MEMBER_COUNT_PATTERN = re.compile(
    r"^([\d\s,\.]+)\s*(mil\b|k\b)?", re.IGNORECASE
)

# Regex para extraer el post_id de una URL de permalink de grupo
_PERMALINK_POST_ID_PATTERN = re.compile(r"/permalink/(\d+)/")


class GroupParser(FacebookContentParser):
    """Parser para páginas de grupos de Facebook.

    Extrae toda la información disponible de un grupo: identidad,
    privacidad, portada, descripción, historial, métricas de actividad,
    administradores y estado del viewer respecto al grupo.

    Attributes:
        _group_about: Nodo con ``about_info_items`` del grupo, o None.
        _group_header: Nodo con ``profile_header_renderer`` del grupo, o None.
        result: Diccionario acumulador con los datos extraídos del grupo.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de grupos.

        Args:
            html_content: HTML completo renderizado de la página del grupo.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self._group_about: dict | None = None
        self._group_header: dict | None = None
        self.result.update({
            "__typename": "facebook_group",
            "group_url": self.original_url,
            "requested_post_id": self._extract_post_id_from_url(self.original_url),
            "content_gated": False,
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta el proceso completo de extracción del grupo.

        Flujo de extracción:
            1. Extraer bloques JSON del HTML.
            2. Localizar los nodos ``_group_about`` y ``_group_header``.
            3. Extraer todos los campos del grupo en orden.
            4. Determinar si el contenido está bloqueado (gating).
            5. Enriquecer con tráfico GraphQL.

        Returns:
            Diccionario con todos los campos del grupo. Si falla la
            localización de bloques, ``result["error"]`` indicará el motivo.
        """
        logger.debug("Iniciando extracción de GRUPO...")

        self._blocks = self._extract_json_blocks()

        if not self._locate_group_blocks():
            self.result["error"] = "No se encontraron bloques de grupo."
            logger.warning("GroupParser: no se encontraron bloques en %s", self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_group_identity()
            self._extract_privacy()
            self._extract_cover_photo()
            self._extract_description()
            self._extract_history()
            self._extract_activity_metrics()
            self._extract_people()
            self._extract_viewer()
            self._determine_content_gating()
            self._parse_traffic()
            logger.info("Grupo parseado correctamente.")

        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando grupo: %s", exc)

        return self.result

    # ==================================================================
    # LOCALIZACIÓN DE BLOQUES DE GRUPO
    # ==================================================================

    def _locate_group_blocks(self) -> bool:
        """Localiza y asigna los nodos ``_group_about`` y ``_group_header``.

        Recorre todos los bloques JSON buscando nodos ``__bbox`` que
        contengan datos de grupo. Distingue el nodo ``about`` (con
        ``about_info_items``) del nodo ``header`` (con
        ``profile_header_renderer``).

        Returns:
            True si se encontró al menos uno de los dos nodos.
        """
        for block in self._blocks:
            bbox_wrapper = self._find_bbox_with_group_data(block)
            if not bbox_wrapper:
                continue

            group_data = (
                self._safe_get(bbox_wrapper, "__bbox", "result", "data", "group") or {}
            )

            if "about_info_items" in group_data and not self._group_about:
                self._group_about = group_data
                logger.debug("Nodo _group_about localizado.")

            if "profile_header_renderer" in group_data and not self._group_header:
                self._group_header = group_data
                logger.debug("Nodo _group_header localizado.")

        found = self._group_about is not None or self._group_header is not None
        if not found:
            logger.debug("No se encontraron bloques de grupo en ningún bloque JSON.")
        return found

    def _find_bbox_with_group_data(self, data: Any) -> dict | None:
        """Busca el primer nodo ``__bbox`` que contenga datos de grupo (uso interno).

        Args:
            data: Estructura de datos a buscar (raíz de un bloque JSON).

        Returns:
            El nodo dict que contiene el ``__bbox`` con datos de grupo,
            o None si no se encuentra.
        """
        return self._recursive_search(
            data,
            condition=lambda node: (
                isinstance(
                    self._safe_get(node, "__bbox", "result", "data", "group"), dict
                )
                and bool(self._safe_get(node, "__bbox", "result", "data", "group"))
            ),
        )

    # ==================================================================
    # EXTRACCIÓN DE CAMPOS
    # ==================================================================

    def _extract_group_identity(self) -> None:
        """Extrae id, nombre y URL del grupo.

        Combina datos de ``_group_header`` y ``_group_about`` para
        obtener la identidad del grupo, prefiriendo el header.

        Actualiza ``self.result`` con ``id``, ``name``, ``url``.
        """
        header_group = (
            self._safe_get(self._group_header, "profile_header_renderer", "group") or {}
        )
        about_group = self._group_about or {}

        group_id = header_group.get("id") or about_group.get("id") or "unknown"
        self.result["id"] = group_id
        self.result["name"] = (
            header_group.get("name")
            or self._safe_get(header_group, "featurable_title", "text")
            or "Unknown Group"
        )
        self.result["url"] = (
            header_group.get("url")
            or self._safe_get(header_group, "profile_url")
            or self.final_url
        )

    def _extract_privacy(self) -> None:
        """Extrae el nivel de privacidad del grupo.

        Busca el item de tipo ``*Privacy*`` dentro de ``about_info_items``
        para obtener el nivel, descripción e ícono de privacidad.

        Actualiza ``self.result["privacy"]`` in-place.
        """
        self.result["privacy"] = {
            "level": "",
            "description": "",
            "icon": "",
            "is_private": False,
        }

        for item in self._safe_get(self._group_about, "about_info_items") or []:
            if "Privacy" not in item.get("__typename", ""):
                continue

            privacy_info = (
                self._safe_get(item, "group", "privacy_info") or {}
            )
            level = self._safe_get(privacy_info, "label", "text", default="")
            self.result["privacy"] = {
                "level": level,
                "description": self._safe_get(
                    privacy_info, "description", "text", default=""
                ),
                "icon": privacy_info.get("icon_name", ""),
                "is_private": level.lower() in ("privado", "private"),
            }
            break  # Solo hay un item de privacidad

    def _extract_cover_photo(self) -> None:
        """Extrae la foto de portada del grupo.

        Actualiza ``self.result["cover_photo"]`` in-place con
        ``photo_id``, ``cdn_uri``, ``width``, ``height``.
        Asigna dict vacío si no hay foto.
        """
        self.result["cover_photo"] = {}

        header_group = (
            self._safe_get(self._group_header, "profile_header_renderer", "group") or {}
        )
        cover_photo_content = (
            self._safe_get(
                header_group, "cover_renderer", "cover_photo_content"
            ) or {}
        )
        photo = cover_photo_content.get("photo", {}) or {}

        if photo:
            image = photo.get("image", {}) or {}
            self.result["cover_photo"] = {
                "photo_id": photo.get("id", ""),
                "cdn_uri": image.get("uri", ""),
                "width": image.get("width"),
                "height": image.get("height"),
            }

    def _extract_description(self) -> None:
        """Extrae la descripción del grupo.

        Intenta dos rutas: la ruta gateada por viewer
        (``if_viewer_can_view_description``) y la ruta directa
        (``description_with_entities``).

        Actualiza ``self.result["description"]`` in-place.
        """
        description = (
            self._safe_get(
                self._group_about,
                "if_viewer_can_view_description",
                "description_with_entities", "text",
                default="",
            )
            or self._safe_get(
                self._group_about,
                "description_with_entities", "text",
                default="",
            )
            or ""
        )
        self.result["description"] = description

    def _extract_history(self) -> None:
        """Extrae la fecha de creación del grupo.

        Busca el item de tipo ``*History*`` dentro de ``about_info_items``
        y convierte el ``create_time`` Unix a datetime.

        Actualiza ``self.result["created_at"]`` in-place.
        """
        self.result["created_at"] = None

        for item in self._safe_get(self._group_about, "about_info_items") or []:
            if "History" not in item.get("__typename", ""):
                continue

            create_time = self._safe_get(
                item, "group", "group_history", "create_time"
            )
            if create_time:
                self.result["created_at"] = self._parse_timestamp(create_time)
            break  # Solo hay un item de historial

    def _extract_activity_metrics(self) -> None:
        """Extrae métricas de actividad del grupo.

        Obtiene posts del último día y total de miembros desde
        ``if_viewer_can_see_activity_section``.

        Actualiza ``self.result`` con ``posts_last_day``,
        ``total_members_text`` y ``total_members``.
        """
        activity_section = (
            self._safe_get(self._group_about, "if_viewer_can_see_activity_section") or {}
        )
        self.result["posts_last_day"] = activity_section.get(
            "number_of_posts_in_last_day", 0
        ) or 0

        members_text = activity_section.get("group_total_members_info_text", "") or ""
        self.result["total_members_text"] = members_text
        self.result["total_members"] = self._parse_member_count(members_text)

    def _extract_people(self) -> None:
        """Extrae la lista de administradores del grupo.

        Procesa ``facepile_admin_profiles`` para obtener los admins
        visibles junto con el total de administradores.

        Actualiza ``self.result["admins"]`` y ``self.result["admins_count"]``.
        """
        self.result["admins"] = []
        self.result["admins_count"] = 0

        if not self._group_about:
            return

        admin_profiles = self._group_about.get("facepile_admin_profiles", {}) or {}
        self.result["admins_count"] = admin_profiles.get("count", 0) or 0

        for edge in admin_profiles.get("edges", []) or []:
            node = edge.get("node", {}) or {}
            self.result["admins"].append({
                "id": node.get("id", ""),
                "name": node.get("name", ""),
                "url": node.get("url", ""),
            })

    def _extract_viewer(self) -> None:
        """Determina si el scraper es miembro del grupo.

        La presencia de ``if_viewer_can_see_content`` indica que el
        viewer tiene acceso de miembro al grupo.

        Actualiza ``self.result["viewer"]`` in-place.
        """
        about_group = self._group_about or {}
        can_see_content = about_group.get("if_viewer_can_see_content")

        self.result["viewer"] = {
            "id": "",
            "name": "",
            "is_member": can_see_content is not None,
        }

    def _determine_content_gating(self) -> None:
        """Determina si el contenido solicitado está bloqueado.

        El contenido está "gateado" cuando:
        - Se solicitó un post específico (``requested_post_id`` presente).
        - El grupo es privado.
        - El scraper NO es miembro del grupo.

        Actualiza ``self.result["content_gated"]`` in-place.
        """
        post_id = self.result.get("requested_post_id")
        is_private = self.result.get("privacy", {}).get("is_private", False)
        is_member = self.result.get("viewer", {}).get("is_member", False)

        self.result["content_gated"] = bool(post_id and is_private and not is_member)

    # ==================================================================
    # PARSEO DE TRÁFICO
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result`` con metadatos del tráfico GraphQL del grupo."""
        self._parse_facebook_traffic(operation_patterns=_GROUP_GRAPHQL_OPERATIONS)

    # ==================================================================
    # UTILIDADES ESTÁTICAS
    # ==================================================================

    @staticmethod
    def _extract_post_id_from_url(url: str) -> str | None:
        """Extrae el post_id de una URL de permalink de grupo.

        Args:
            url: URL del grupo (puede incluir /permalink/<id>/).

        Returns:
            El post_id como string si se encuentra en la URL, None si no.

        Example:
            >>> GroupParser._extract_post_id_from_url(
            ...     "https://facebook.com/groups/123/permalink/456/"
            ... )
            '456'
        """
        if not url or "permalink" not in url:
            return None
        match = _PERMALINK_POST_ID_PATTERN.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_member_count(text: str) -> int:
        """Parsea el conteo de miembros desde un texto localizado.

        Maneja formatos con separadores de miles y sufijos ``mil``/``k``.

        Args:
            text: Texto con el conteo de miembros
                (ej. ``"1.200 miembros"``, ``"3,5 mil miembros"``).

        Returns:
            Número entero de miembros, o 0 si no se pudo parsear.

        Example:
            >>> GroupParser._parse_member_count("1.200 miembros")
            1200
            >>> GroupParser._parse_member_count("3,5 mil")
            3500
            >>> GroupParser._parse_member_count("")
            0
        """
        if not text:
            return 0

        # Normalizar espacio no separable (U+00A0)
        clean_text = text.replace("\u00a0", " ").strip()
        match = _MEMBER_COUNT_PATTERN.match(clean_text)
        if not match:
            return 0

        raw_number = match.group(1).strip().replace(" ", "").replace(",", "")
        suffix = (match.group(2) or "").lower()

        try:
            number = float(raw_number)
            if suffix in ("mil", "k"):
                return int(number * 1_000)
            return int(number)
        except ValueError:
            return 0