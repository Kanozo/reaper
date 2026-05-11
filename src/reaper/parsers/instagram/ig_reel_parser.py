from reaper.utils.logger import get_logger
from typing import Any

from reaper.parsers.base_parser import BaseParser

logger = get_logger(__name__)

class IgReelParser(BaseParser):
    """
    Parser para Reels de Instagram.

    Diferencias respecto a IgPostParser:
    - El nodo raíz es ``xdt_api__v1__clips__clips_on_logged_out_connection_v2``.
    - Los datos se organizan en un array de ``edges`` (estructura paginada).
    - El primer edge es el reel solicitado; los siguientes van al campo ``feed``.
    - Los Reels incluyen versiones de vídeo y un campo ``media_repost_count``.

    Attributes:
        original_url: URL original solicitada por el usuario.
        result:       Diccionario acumulador con todos los datos extraídos.
    """

    # Clave JSON que identifica el nodo de Reels en los scripts embebidos
    REEL_NODE_KEY = "xdt_api__v1__clips__clips_on_logged_out_connection_v2"
    # Clave JSON del feed lateral del perfil del autor
    FEED_NODE_KEY = "xdt_api__v1__profile_timeline"

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
        # self.original_url y self.result ya inicializados por BaseParser.
        # Campos específicos de IgReelParser:
        self.result.update({
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
                    "post_user_id":str,
                    "id":          str,
                    "permalink_url": str,
                    "posted_at":   Optional[datetime],
                    "user": {
                        "id": str, "username": str, "full_name": str,
                        "profile_pic_url": str, "is_verified": bool
                    },
                    "location":    Any | None,
                    "thumbnail":   str | None,
                    "caption":     str | None,
                    "like_count":  int,
                    "comment_count": int,
                    "media_repost_count": int,
                    "link":        str | None,
                    "text":        str | None,
                    "media_type":  str,   # "Reel" | "Photo"
                    "image_versions": list | None,
                    "video_versions": list | None,
                    "tagged_users": list[dict],
                    "comments":   list[dict],
                    "feed":       list[dict]   # reels relacionados y posts del perfil
                }
        """
        logger.info("Iniciando extracción de REEL de Instagram | url=%s", self.final_url)

        blocks = self._extract_json_blocks()

        # --- Paso 1: Reel principal + reels relacionados ---
        if not self._extract_reel_nodes(blocks):
            self.result["error"] = (
                f"No se encontró el nodo '{self.REEL_NODE_KEY}'."
            )
            logger.warning("IgReelParser: %s | url=%s", self.result["error"], self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        # --- Paso 2: Feed del perfil del autor (opcional) ---
        self._extract_profile_feed(blocks)

        logger.debug("Reel de Instagram parseado correctamente | url=%s", self.final_url)
        return self.result

    # ------------------------------------------------------------------
    # Extracción del reel principal y reels relacionados
    # ------------------------------------------------------------------

    def _extract_reel_nodes(self, blocks: list[dict]) -> bool:
        """
        Localiza el nodo ``xdt_api__v1__clips__clips_on_logged_out_connection_v2``
        y procesa todos sus edges.

        El primer edge se convierte en el post principal (campos raíz del result).
        Los edges restantes se añaden al campo ``feed`` como reels relacionados.

        Args:
            blocks: Lista de dicts con todos los bloques JSON del HTML.

        Returns:
            True si se encontró y procesó al menos un edge, False en caso contrario.
        """
        for block in blocks:
            node = self._recursive_search(
                block,
                condition=lambda n: bool(n.get(self.REEL_NODE_KEY)),
            )
            if not node:
                continue

            edges = self._safe_get(node, self.REEL_NODE_KEY, "edges", default=[])
            if not edges:
                continue

            for idx, edge in enumerate(edges):
                media = self._safe_get(edge, "node", "media")
                if not media:
                    continue

                reel_data = self._build_reel_dict(media)

                if idx == 0:
                    # El primer edge es el reel solicitado → campos raíz
                    self.result.update(reel_data)
                else:
                    # Reels relacionados → campo feed
                    self.result["feed"].append(reel_data)

            # Retornar True si al menos se procesó el primer edge
            return bool(self.result.get("id"))

        return False

    def _build_reel_dict(self, data: dict) -> dict[str, Any]:
        """
        Construye el diccionario de datos de un reel a partir del subnodo
        ``node.media`` de un edge.

        Args:
            data: dict del campo ``media`` dentro de ``edges[n].node``.

        Returns:
            dict completo con todos los campos del reel.
        """
        code = data.get("code")
        media_type_raw = data.get("media_type", 2)  # Reels son tipo 2 por defecto

        # URL permanente: Instagram usa tanto /p/ como /reel/ según el tipo
        if media_type_raw == 2:
            permalink = (
                f"https://www.instagram.com/reel/{code}" if code else self.final_url
            )
        else:
            permalink = (
                f"https://www.instagram.com/p/{code}" if code else self.final_url
            )

        user = data.get("user")

        reel: dict[str, Any] = {
            "code": code,
            "post_user_id": data.get("id"),
            "id": data.get("pk"),
            "permalink_url": permalink,
            "posted_at": self._parse_timestamp(data.get("taken_at")),
            "user": self._build_user_dict(user) if user else None,
            "location": data.get("location"),
            "thumbnail": data.get("display_uri"),
            "caption": data.get("accessibility_caption"),
            "like_count": data.get("like_count", 0),
            "comment_count": data.get("comment_count", 0),
            "media_repost_count": self._safe_get(data, "media_repost_count", default=0),
            "link": data.get("link"),
            "text": self._safe_get(data, "caption", "text"),
            "media_type": self._map_media_type(media_type_raw),
            "image_versions": self._safe_get(data, "image_versions2", "candidates"),
            "video_versions": data.get("video_versions"),
            "tagged_users": self._extract_tagged_users(data),
            "comments": self._extract_preview_comments(data),
            "raw_data_available": True,
        }

        return reel

    # ------------------------------------------------------------------
    # Extracción del feed del perfil del autor
    # ------------------------------------------------------------------

    def _extract_profile_feed(self, blocks: list[dict]) -> None:
        """
        Localiza el nodo ``xdt_api__v1__profile_timeline`` y añade los posts
        del perfil del autor al campo ``feed``, evitando duplicar el reel
        principal.

        Args:
            blocks: Lista de dicts con todos los bloques JSON del HTML.
        """
        main_id = self.result.get("id")

        for block in blocks:
            node = self._recursive_search(
                block,
                condition=lambda n: bool(n.get(self.FEED_NODE_KEY)),
            )
            if not node:
                continue

            items = self._safe_get(node, self.FEED_NODE_KEY, "items", default=[])
            for item in items:
                item_id = self._safe_get(item, "id")
                # Saltar si es el reel principal o si no tiene ID
                if not item_id or item_id == main_id:
                    continue
                feed_entry = self._build_feed_entry(item)
                if feed_entry:
                    self.result["feed"].append(feed_entry)

            break  # Solo procesar el primer bloque con feed

    def _build_feed_entry(self, data: dict) -> dict | None:
        """
        Construye un dict resumido de un ítem del feed del perfil.

        Args:
            data: dict del ítem de ``xdt_api__v1__profile_timeline.items``.

        Returns:
            dict con los campos básicos del ítem, o None si no tiene ID.
        """
        post_id = self._safe_get(data, "id")
        if not post_id:
            return None

        code = self._safe_get(data, "code")
        media_type_raw = self._safe_get(data, "media_type", default=0)

        entry: dict[str, Any] = {
            "post_user_id": post_id,
            "id": self._safe_get(data, "pk"),
            "code": code,
            "permalink_url": (
                f"https://www.instagram.com/p/{code}" if code else self.final_url
            ),
            "media_type": self._map_media_type(media_type_raw),
            "text": self._safe_get(data, "caption", "text"),
            "caption": self._safe_get(data, "accessibility_caption"),
            "display_uri": self._safe_get(data, "display_uri"),
            "like_count": self._safe_get(data, "like_count", default=0),
            "comment_count": self._safe_get(data, "comment_count", default=0),
            "image_versions": self._safe_get(data, "image_versions2", "candidates"),
            "video_versions": data.get("video_versions"),
            "user": (
                self._build_user_dict(data.get("user"))
                if data.get("user")
                else None
            ),
        }

        if media_type_raw == 8:
            entry["carousel_media_count"] = self._safe_get(data, "carousel_media_count")

        return entry

    # ------------------------------------------------------------------
    # Constructores de subestructuras compartidos
    # ------------------------------------------------------------------

    def _build_user_dict(self, user: dict) -> dict[str, Any]:
        """
        Construye el diccionario normalizado de un usuario de Instagram.

        Args:
            user: dict con los datos del usuario extraídos del JSON.

        Returns:
            dict con los campos normalizados del usuario.
        """
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "profile_pic_url": user.get("profile_pic_url"),
            "is_verified": user.get("is_verified", False),
        }

    def _extract_tagged_users(self, data: dict) -> list[dict]:
        """
        Extrae los usuarios etiquetados en el reel desde ``usertags.in``.

        Args:
            data: dict del ítem del reel.

        Returns:
            Lista de dicts de usuarios etiquetados.
        """
        tagged: list[dict] = []
        for tag in self._safe_get(data, "usertags", "in", default=[]):
            user = tag.get("user")
            if user:
                tagged.append(self._build_user_dict(user))
        return tagged

    def _extract_preview_comments(self, data: dict) -> list[dict]:
        """
        Extrae los comentarios de previsualización (``preview_comments``).

        Args:
            data: dict del ítem del reel.

        Returns:
            Lista de dicts con texto y datos básicos del autor.
        """
        comments: list[dict] = []
        for comment in data.get("preview_comments", []):
            user = comment.get("user") or {}
            comments.append({
                "text": comment.get("text"),
                "user": {
                    "id": user.get("id"),
                    "username": user.get("username"),
                    "is_verified": user.get("is_verified", False),
                },
            })
        return comments

    @staticmethod
    def _map_media_type(media_type_raw: int) -> str:
        """
        Convierte el código numérico de tipo de media al string descriptivo.

        Args:
            media_type_raw: Código numérico del tipo de media de Instagram.

        Returns:
            String: "Photo" | "Reel" | "carousel" | "unknown(N)".
        """
        mapping = {1: "Photo", 2: "Reel", 8: "carousel"}
        return mapping.get(media_type_raw, f"unknown({media_type_raw})")
