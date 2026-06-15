"""
parsers/ig_post_parser.py

Parser para publicaciones regulares de Instagram (fotos, carruseles y vídeos
publicados bajo la ruta ``/p/``).

Cambio de estructura (2025-06):
    Instagram migró de bloques JSON con clave raíz ``xdt_api__v1__...``
    a un modelo Relay con la siguiente ruta:

        require[N][3][i].__bbox.require[M][3][j].__bbox.result.data
          .xig_polaris_media
            .if_not_gated_logged_out   ← datos del post
            .comments_connection.edges ← comentarios

    El feed del perfil del autor se extrae de:
        xig_polaris_media.if_not_gated_logged_out
          .user.polaris_ordered_timeline_connection.edges

Notas de implementación:
    - ``post_user_id``: combinación ``{user_pk}_{media_pk}`` (formato IG estándar).
    - ``like_count`` / ``comment_count``: Instagram no los expone a usuarios no
      logueados en el JSON Relay. Se parsean desde el meta tag ``description``
      como fallback ("N likes, M comments - ...").
    - ``taken_at`` en nodos del feed: ausente en el HTML. Se decodifica desde el
      pk usando el epoch offset de Instagram (Snowflake-like ID).

Python: 3.11+
"""
from reaper.utils.logger import get_logger
from typing import Any
import re

from bs4 import BeautifulSoup

from reaper.parsers.base_parser import BaseParser

logger = get_logger(__name__)

_MEDIA_ROOT_KEY = "xig_polaris_media"
# Offset de epoch de Instagram para decodificar timestamps desde pk
_IG_EPOCH_MS = 1_314_220_021_721


class IgPostParser(BaseParser):
    """
    Parser para publicaciones regulares de Instagram (fotos, carruseles y vídeos).

    Attributes:
        original_url: URL original solicitada por el usuario.
        result:       Diccionario acumulador con todos los datos extraídos.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        debug: bool = False,
    ) -> None:
        """
        Inicializa el parser de posts regulares de Instagram.

        Args:
            html_content: HTML completo renderizado de la página.
            final_url:    URL final de la página (tras posibles redirecciones).
            original_url: URL original solicitada por el usuario.
            debug:        Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(
            html_content,
            final_url,
            original_url=original_url,
            debug=debug,
            platform="instagram",
        )
        self.result.update({
            "__typename": "regular_post",
            "feed": [],  # posts adicionales del perfil del autor
        })

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def parse(self) -> dict[str, Any]:
        """
        Ejecuta el pipeline completo de extracción para un post de Instagram.

        Returns:
            dict[str, Any] con la siguiente estructura::

                {
                    "post_url":    str,
                    "scraped_at":  datetime,
                    "error":       str | None,
                    "raw_data_available": bool,
                    "code":        str,
                    "id":          str,           # pk numérico del media
                    "post_user_id": str,          # "{user_pk}_{media_pk}"
                    "permalink_url": str,
                    "posted_at":   Optional[datetime],
                    "user": {
                        "id": str, "username": str, "full_name": str,
                        "profile_pic_url": str, "is_verified": bool,
                        "is_private": bool
                    },
                    "location":    Any | None,
                    "media_type":  str,
                    "carousel_media_count": int | None,
                    "thumbnail":   str | None,
                    "caption":     str | None,
                    "like_count":  int,   # 0 si Instagram no lo expone (no logueado)
                    "comment_count": int, # ídem
                    "link":        str | None,
                    "text":        str | None,
                    "image_versions": list | None,
                    "video_versions": list | None,
                    "tagged_users": list[dict],
                    "comments":    list[dict],
                    "feed":        list[dict]
                }
        """
        logger.info("Iniciando extracción de POST de Instagram | url=%s", self.final_url)

        blocks = self._extract_json_blocks()

        media_root = self._find_relay_media_root(blocks)
        if media_root is None:
            self.result["error"] = (
                f"No se encontró el nodo Relay '{_MEDIA_ROOT_KEY}' "
                "en ningún bloque JSON del HTML."
            )
            logger.warning("IgPostParser: %s | url=%s", self.result["error"], self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        post_data = media_root.get("if_not_gated_logged_out") or {}
        self._populate_post_fields(post_data)

        # Fallback de contadores desde meta description (usuarios no logueados)
        self._enrich_counts_from_meta()

        self._extract_comments_from_connection(media_root)
        self._extract_profile_feed(post_data)

        logger.debug("Post de Instagram parseado correctamente | url=%s", self.final_url)
        return self.result

    # ------------------------------------------------------------------
    # Localización del nodo Relay
    # ------------------------------------------------------------------

    def _find_relay_media_root(self, blocks: list[dict]) -> dict | None:
        """
        Busca el nodo ``xig_polaris_media`` dentro de la estructura Relay.

        Usa ``_recursive_search`` del BaseParser con la condición
        ``__bbox.result.data`` que contiene ``xig_polaris_media``, lo que
        lo hace robusto ante cambios de anidamiento.

        Args:
            blocks: Lista de bloques JSON extraídos del HTML.

        Returns:
            El dict ``xig_polaris_media`` si se encuentra, None en caso contrario.
        """
        def _is_relay_result(node: dict) -> bool:
            result = node.get("result")
            if not isinstance(result, dict):
                return False
            data = result.get("data")
            return isinstance(data, dict) and _MEDIA_ROOT_KEY in data

        for block in blocks:
            relay_node = self._recursive_search(block, condition=_is_relay_result)
            if relay_node:
                return relay_node["result"]["data"][_MEDIA_ROOT_KEY]

        return None

    # ------------------------------------------------------------------
    # Extracción del post principal
    # ------------------------------------------------------------------

    def _populate_post_fields(self, data: dict) -> None:
        """
        Rellena ``self.result`` con los campos del post.

        Args:
            data: Nodo ``if_not_gated_logged_out`` del ``xig_polaris_media``.
        """
        code = data.get("code")
        media_pk = data.get("pk")
        user_pk = self._safe_get(data, "user", "pk")

        self.result["code"] = code
        self.result["id"] = media_pk
        # Formato estándar de Instagram: "{user_pk}_{media_pk}"
        self.result["post_user_id"] = (
            f"{user_pk}_{media_pk}" if user_pk and media_pk else media_pk
        )
        self.result["permalink_url"] = (
            f"https://www.instagram.com/p/{code}" if code else self.final_url
        )
        self.result["posted_at"] = self._parse_timestamp(data.get("taken_at"))

        user = data.get("user")
        self.result["user"] = self._build_user_dict(user) if user else None
        self.result["location"] = data.get("location")

        media_type_raw = data.get("media_type", 0)
        self.result["media_type"] = self._map_media_type(media_type_raw)
        if media_type_raw == 8:
            self.result["carousel_media_count"] = data.get("carousel_media_count")

        self.result["thumbnail"] = data.get("display_uri")
        self.result["image_versions"] = self._safe_get(data, "image_versions2", "candidates")
        self.result["video_versions"] = data.get("video_versions")
        self.result["caption"] = data.get("accessibility_caption")
        # like_count y comment_count son 0 para usuarios no logueados en el JSON Relay.
        # Se enriquecen desde el meta description en _enrich_counts_from_meta().
        self.result["like_count"] = data.get("like_count", 0)
        self.result["comment_count"] = data.get("comment_count", 0)
        self.result["link"] = data.get("link")
        self.result["text"] = self._safe_get(data, "caption", "text")
        self.result["tagged_users"] = self._extract_tagged_users(data)

    # ------------------------------------------------------------------
    # Enriquecimiento de contadores desde meta description
    # ------------------------------------------------------------------

    def _enrich_counts_from_meta(self) -> None:
        """
        Parsea el meta tag ``description`` para extraer like_count y
        comment_count cuando el JSON Relay los devuelve a 0.

        Instagram incluye en el meta description el patrón:
        ``"N likes, M comments - username on date: ..."``
        incluso para usuarios no logueados, siendo la única fuente
        fiable de estos contadores en el HTML sin autenticación.

        Solo sobreescribe si el JSON Relay devolvió 0 en ambos campos,
        para no pisar datos válidos cuando sí estén presentes.
        """
        if self.result.get("like_count", 0) != 0 or self.result.get("comment_count", 0) != 0:
            return

        try:
            soup = BeautifulSoup(self.html_content, "html.parser")
            meta = soup.find("meta", attrs={"name": "description"})
            if not meta:
                meta = soup.find("meta", attrs={"property": "og:description"})
            if not meta:
                return

            content = meta.get("content", "")
            # Patrón: "1,234 likes, 56 comments - ..."
            match = re.match(
                r'([\d,]+)\s+likes?,\s*([\d,]+)\s+comments?',
                content,
            )
            if match:
                self.result["like_count"] = int(match.group(1).replace(",", ""))
                self.result["comment_count"] = int(match.group(2).replace(",", ""))
                logger.debug(
                    "Counts from meta: likes=%d, comments=%d",
                    self.result["like_count"],
                    self.result["comment_count"],
                )
        except Exception as exc:
            logger.debug("_enrich_counts_from_meta failed: %s", exc)

    # ------------------------------------------------------------------
    # Extracción de comentarios desde comments_connection
    # ------------------------------------------------------------------

    def _extract_comments_from_connection(self, media_root: dict) -> None:
        """
        Extrae comentarios desde ``xig_polaris_media.comments_connection.edges``.

        Args:
            media_root: El nodo raíz ``xig_polaris_media``.
        """
        edges = self._safe_get(media_root, "comments_connection", "edges", default=[])
        comments: list[dict] = []
        for edge in edges:
            node = edge.get("node") or {}
            user = node.get("user") or {}
            comments.append({
                "text": node.get("text"),
                "user": {
                    "id": user.get("id"),
                    "username": user.get("username"),
                    "is_verified": user.get("is_verified", False),
                },
            })
        self.result["comments"] = comments

    # ------------------------------------------------------------------
    # Extracción del feed del perfil del autor
    # ------------------------------------------------------------------

    def _extract_profile_feed(self, post_data: dict) -> None:
        """
        Extrae posts del perfil desde
        ``user.polaris_ordered_timeline_connection.edges``.

        Args:
            post_data: Nodo ``if_not_gated_logged_out`` del post principal.
        """
        edges = self._safe_get(
            post_data,
            "user", "polaris_ordered_timeline_connection", "edges",
            default=[],
        )
        main_pk = self.result.get("id")

        for edge in edges:
            node = edge.get("node") or {}
            entry = self._build_feed_entry(node)
            if entry and entry.get("id") != main_pk:
                self.result["feed"].append(entry)

    def _build_feed_entry(self, data: dict) -> dict | None:
        """
        Construye un dict resumido de un ítem del feed del perfil.

        ``taken_at`` no está presente en los nodos del feed lateral de IG.
        Se decodifica desde el ``pk`` usando el epoch offset de Instagram
        (Snowflake-like: ``timestamp_ms = (pk >> 23) + IG_EPOCH_MS``).

        Args:
            data: Nodo de un edge de ``polaris_ordered_timeline_connection``.

        Returns:
            dict con los campos básicos del ítem, o None si no tiene pk.
        """
        media_pk = data.get("pk")
        if not media_pk:
            return None

        code = data.get("code")
        media_type_raw = data.get("media_type", 0)
        user = data.get("user")
        user_pk = (user or {}).get("pk") if user else None

        entry: dict[str, Any] = {
            "post_user_id": f"{user_pk}_{media_pk}" if user_pk else str(media_pk),
            "id": media_pk,
            "code": code,
            "permalink_url": (
                f"https://www.instagram.com/p/{code}" if code else self.final_url
            ),
            "posted_at": self._decode_pk_timestamp(media_pk),
            "media_type": self._map_media_type(media_type_raw),
            "text": self._safe_get(data, "caption", "text"),
            "caption": data.get("accessibility_caption"),
            "display_uri": data.get("display_uri"),
            "like_count": data.get("like_count", 0),
            "comment_count": data.get("comment_count", 0),
            "image_versions": self._safe_get(data, "image_versions2", "candidates"),
            "video_versions": data.get("video_versions"),
            "user": self._build_user_dict(user) if user else None,
        }

        if media_type_raw == 8:
            entry["carousel_media_count"] = data.get("carousel_media_count")

        return entry

    # ------------------------------------------------------------------
    # Constructores de subestructuras reutilizables
    # ------------------------------------------------------------------

    def _build_user_dict(self, user: dict) -> dict[str, Any]:
        """
        Construye el diccionario normalizado de un usuario de Instagram.

        Args:
            user: dict con los datos del usuario extraídos del nodo Relay.

        Returns:
            dict con los campos normalizados del usuario.
        """
        return {
            "id": user.get("pk") or user.get("id"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "profile_pic_url": user.get("profile_pic_url") or user.get("profile_image_uri"),
            "is_verified": user.get("is_verified", False),
            "is_private": user.get("is_private", False),
        }

    def _extract_tagged_users(self, data: dict) -> list[dict]:
        """
        Extrae los usuarios etiquetados desde ``usertags.in``.

        Args:
            data: Nodo de datos del post.

        Returns:
            Lista de dicts de usuarios etiquetados (puede estar vacía).
        """
        tagged: list[dict] = []
        for tag in self._safe_get(data, "usertags", "in", default=[]):
            user = tag.get("user")
            if user:
                tagged.append(self._build_user_dict(user))
        return tagged

    # ------------------------------------------------------------------
    # Utilidades estáticas
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_pk_timestamp(pk: str | int | None) -> Any:
        """
        Decodifica el timestamp de publicación a partir del pk de Instagram.

        Instagram usa un ID Snowflake-like donde:
        ``timestamp_ms = (pk >> 23) + 1_314_220_021_721``

        Verificado contra ``taken_at`` real: coincidencia exacta al segundo.

        Args:
            pk: El pk numérico del media (str o int).

        Returns:
            ``datetime`` si la decodificación es exitosa, None en caso contrario.
        """     
        if pk is None:
            return None
        try:
            ts_ms = (int(pk) >> 23) + _IG_EPOCH_MS
            from datetime import datetime
            dt = datetime.fromtimestamp(ts_ms / 1000)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError, OSError):
            return None

    @staticmethod
    def _map_media_type(media_type_raw: int) -> str:
        """
        Convierte el código numérico de tipo de media de Instagram a string legible.

        Códigos conocidos:
            - 1 → Photo
            - 2 → Reel
            - 8 → carousel

        Args:
            media_type_raw: Código numérico del tipo de media.

        Returns:
            String descriptivo del tipo de media.
        """
        mapping = {1: "Photo", 2: "Reel", 8: "carousel"}
        return mapping.get(media_type_raw, f"unknown({media_type_raw})")