"""
parsers/ig_reel_parser.py

Parser para Reels de Instagram publicados bajo la ruta ``/reel/``.

Cambio de estructura (2025-06):
    Instagram migró de bloques JSON con clave raíz
    ``xdt_api__v1__clips__clips_on_logged_out_connection_v2``
    al mismo modelo Relay que usa el parser de posts:

        require[N][3][i].__bbox.require[M][3][j].__bbox.result.data
          .xig_polaris_media
            .if_not_gated_logged_out   ← datos del reel principal
            .comments_connection.edges ← comentarios

    El feed del perfil del autor se extrae de:
        xig_polaris_media
          .if_not_gated_logged_out
            .user.polaris_ordered_timeline_connection.edges

    Nota: Instagram sirve los Reels bajo ``/reel/<code>`` pero el nodo
    ``xig_polaris_media`` es idéntico al de los posts regulares.
    La diferencia observable es ``media_type=2`` (Reel) vs ``media_type=1``
    (Photo) y la presencia de ``video_versions`` / ``media_repost_count``.

Python: 3.11+
"""
from reaper.utils.logger import get_logger
from typing import Any

from reaper.parsers.base_parser import BaseParser

logger = get_logger(__name__)

# Clave raíz del nodo de media en la respuesta Relay de Instagram
_MEDIA_ROOT_KEY = "xig_polaris_media"


class IgReelParser(BaseParser):
    """
    Parser para Reels de Instagram.

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
        Inicializa el parser de Reels de Instagram.

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
            "__typename": "regular_reel",
            "feed": [],  # reels relacionados + posts del perfil del autor
        })

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def parse(self) -> dict[str, Any]:
        """
        Ejecuta el pipeline completo de extracción para un Reel de Instagram.

        Returns:
            dict[str, Any] con la siguiente estructura::

                {
                    "post_url":    str,
                    "scraped_at":  datetime,
                    "error":       str | None,
                    "raw_data_available": bool,
                    "code":        str,
                    "id":          str,
                    "post_user_id": str,
                    "permalink_url": str,
                    "posted_at":   Optional[datetime],
                    "user": {
                        "id": str, "username": str, "full_name": str,
                        "profile_pic_url": str, "is_verified": bool,
                        "is_private": bool
                    },
                    "location":    Any | None,
                    "thumbnail":   str | None,
                    "caption":     str | None,
                    "like_count":  int,
                    "comment_count": int,
                    "media_repost_count": int,
                    "link":        str | None,
                    "text":        str | None,
                    "media_type":  str,
                    "image_versions": list | None,
                    "video_versions": list | None,
                    "tagged_users": list[dict],
                    "comments":    list[dict],
                    "feed":        list[dict]
                }
        """
        logger.info("Iniciando extracción de REEL de Instagram | url=%s", self.final_url)

        blocks = self._extract_json_blocks()

        media_root = self._find_relay_media_root(blocks)
        if media_root is None:
            self.result["error"] = (
                f"No se encontró el nodo Relay '{_MEDIA_ROOT_KEY}' "
                "en ningún bloque JSON del HTML."
            )
            logger.warning("IgReelParser: %s | url=%s", self.result["error"], self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        # Datos del reel principal
        reel_data = media_root.get("if_not_gated_logged_out") or {}
        self._populate_reel_fields(reel_data)

        # Comentarios desde comments_connection
        self._extract_comments_from_connection(media_root)

        # Feed del perfil del autor
        self._extract_profile_feed(reel_data)

        logger.debug("Reel de Instagram parseado correctamente | url=%s", self.final_url)
        return self.result

    # ------------------------------------------------------------------
    # Localización del nodo Relay
    # ------------------------------------------------------------------

    def _find_relay_media_root(self, blocks: list[dict]) -> dict | None:
        """
        Busca el nodo ``xig_polaris_media`` dentro de la estructura Relay.

        Instagram embebe los datos en:
            require[N][3][i].__bbox.require[M][3][j].__bbox.result.data

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
    # Extracción del reel principal
    # ------------------------------------------------------------------

    def _populate_reel_fields(self, data: dict) -> None:
        """
        Rellena ``self.result`` con los campos del reel.

        Args:
            data: Nodo ``if_not_gated_logged_out`` del ``xig_polaris_media``.
        """
        code = data.get("code")
        media_type_raw = data.get("media_type", 2)

        # Reels → /reel/, otros tipos → /p/
        if media_type_raw == 2:
            permalink = (
                f"https://www.instagram.com/reel/{code}" if code else self.final_url
            )
        else:
            permalink = (
                f"https://www.instagram.com/p/{code}" if code else self.final_url
            )

        self.result["code"] = code
        self.result["post_user_id"] = data.get("id")
        self.result["id"] = data.get("pk")
        self.result["permalink_url"] = permalink
        self.result["posted_at"] = self._parse_timestamp(data.get("taken_at"))

        user = data.get("user")
        self.result["user"] = self._build_user_dict(user) if user else None

        self.result["location"] = data.get("location")
        self.result["thumbnail"] = data.get("display_uri")
        self.result["caption"] = data.get("accessibility_caption")
        self.result["like_count"] = data.get("like_count", 0)
        self.result["comment_count"] = data.get("comment_count", 0)
        self.result["media_repost_count"] = data.get("media_repost_count", 0)
        self.result["link"] = data.get("link")
        self.result["text"] = self._safe_get(data, "caption", "text")
        self.result["media_type"] = self._map_media_type(media_type_raw)
        self.result["image_versions"] = self._safe_get(data, "image_versions2", "candidates")
        self.result["video_versions"] = data.get("video_versions")
        self.result["tagged_users"] = self._extract_tagged_users(data)

    # ------------------------------------------------------------------
    # Extracción de comentarios desde comments_connection
    # ------------------------------------------------------------------

    def _extract_comments_from_connection(self, media_root: dict) -> None:
        """
        Extrae los comentarios desde ``xig_polaris_media.comments_connection.edges``.

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

    def _extract_profile_feed(self, reel_data: dict) -> None:
        """
        Extrae los posts del perfil del autor desde
        ``user.polaris_ordered_timeline_connection.edges``.

        Args:
            reel_data: Nodo ``if_not_gated_logged_out`` del reel principal.
        """
        edges = self._safe_get(
            reel_data,
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

        Args:
            data: Nodo de un edge de ``polaris_ordered_timeline_connection``.

        Returns:
            dict con los campos básicos del ítem, o None si no tiene PK.
        """
        post_pk = data.get("pk")
        if not post_pk:
            return None

        code = data.get("code")
        media_type_raw = data.get("media_type", 0)
        user = data.get("user")

        entry: dict[str, Any] = {
            "post_user_id": data.get("id"),
            "id": post_pk,
            "code": code,
            "permalink_url": (
                f"https://www.instagram.com/p/{code}" if code else self.final_url
            ),
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
            "id": user.get("id") or user.get("pk"),
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
            data: Nodo de datos del reel.

        Returns:
            Lista de dicts de usuarios etiquetados (puede estar vacía).
        """
        tagged: list[dict] = []
        for tag in self._safe_get(data, "usertags", "in", default=[]):
            user = tag.get("user")
            if user:
                tagged.append(self._build_user_dict(user))
        return tagged

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