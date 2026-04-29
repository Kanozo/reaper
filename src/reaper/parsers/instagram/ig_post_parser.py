from reaper.utils.logger import get_logger
from typing import Any

from reaper.parsers.base_parser import BaseParser

logger = get_logger(__name__)

class IgPostParser(BaseParser):
    """
    Parser para publicaciones regulares de Instagram (fotos, carruseles y vídeos
    publicados bajo la ruta ``/p/``).

    Flujo de ``parse()``:
    1. Extraer todos los bloques JSON del HTML.
    2. Localizar ``xdt_api__v1__media__shortcode__web_info`` → datos del post principal.
    3. Extraer metadatos, usuario, tipo de media, adjuntos, comentarios y etiquetas.
    4. (Opcional) Localizar ``xdt_api__v1__profile_timeline`` → feed de posts del autor.
    5. Retornar diccionario estructurado completo.

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
        # self.original_url y self.result ya inicializados por BaseParser.
        # Campos específicos de IgPostParser:
        self.result.update({
            "feed": [],  # posts adicionales del perfil del autor
        })

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def parse(self) -> dict[str, Any]:
        """
        Ejecuta el pipeline completo de extracción para un post regular de Instagram.

        Returns:
            dict[str, Any] con la siguiente estructura::

                {
                    "post_url":    str,
                    "scraped_at":  datetime,
                    "error":       str | None,
                    "raw_data_available": bool,
                    "code":        str,          # shortcode del post (ej. "DVQ7dz...")
                    "id":          str,
                    "pk":          str,
                    "permalink_url": str,
                    "posted_at":   Optional[datetime],
                    "user": {
                        "id": str, "username": str, "full_name": str,
                        "profile_pic_url": str, "is_verified": bool
                    },
                    "group":       Any | None,
                    "location":    Any | None,
                    "media_type":  str,          # "Photo" | "Reel" | "carousel"
                    "carousel_media_count": int | None,
                    "thumbnail":   str | None,
                    "caption":     str | None,
                    "like_count":  int,
                    "comment_count": int,
                    "link":        str | None,
                    "text":        str | None,
                    "image_versions": list | None,
                    "video_versions": list | None,
                    "tagged_users": list[dict],
                    "comments":    list[dict],
                    "feed":        list[dict]    # posts del feed del autor
                }
        """
        logger.info("Iniciando extracción de POST de Instagram | url=%s", self.final_url)

        # Extraer todos los bloques JSON del HTML una sola vez
        blocks = self._extract_json_blocks()

        # --- Paso 1: Post principal ---
        if not self._extract_main_post(blocks):
            self.result["error"] = (
                "No se encontró el nodo 'xdt_api__v1__media__shortcode__web_info'."
            )
            logger.warning("IgPostParser: %s | url=%s", self.result["error"], self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        # --- Paso 2: Feed lateral del perfil (datos adicionales opcionales) ---
        self._extract_feed(blocks)

        logger.info("Post de Instagram parseado correctamente | url=%s", self.final_url)
        return self.result

    # ------------------------------------------------------------------
    # Extracción del post principal
    # ------------------------------------------------------------------

    def _extract_main_post(self, blocks: list[dict]) -> bool:
        """
        Localiza el nodo ``xdt_api__v1__media__shortcode__web_info`` en los
        bloques JSON y extrae los datos del post principal.

        Args:
            blocks: Lista de dicts con todos los bloques JSON del HTML.

        Returns:
            True si se encontró y procesó el post principal, False en caso contrario.
        """
        for block in blocks:
            # Buscar el nodo raíz que contenga la clave de detalle del post
            node = self._recursive_search(
                block,
                condition=lambda n: bool(n.get("xdt_api__v1__media__shortcode__web_info")),
            )
            if not node:
                continue

            # Navegar hasta el primer ítem (el post principal)
            items = self._safe_get(
                node, "xdt_api__v1__media__shortcode__web_info", "items"
            )
            if not items or not isinstance(items, list):
                continue

            data = items[0]
            self._populate_post_fields(data)
            return True

        return False

    def _populate_post_fields(self, data: dict) -> None:
        """
        Rellena ``self.result`` con todos los campos del post a partir del
        nodo de datos del ítem extraído.

        Extrae:
        - Identificadores (code, id, pk) y URL permanente.
        - Timestamp de publicación.
        - Datos del usuario/autor.
        - Tipo de media y recuento de carrusel (si aplica).
        - Adjuntos: imágenes y versiones de vídeo.
        - Texto, caption de accesibilidad, contadores.
        - Usuarios etiquetados en la publicación.
        - Comentarios de previsualización.

        Args:
            data: dict del ítem principal extraído de ``items[0]``.
        """
        code = data.get("code")
        self.result["code"] = code
        self.result["id"] = data.get("id")
        self.result["pk"] = data.get("pk")

        # URL permanente: preferir la canónica con el shortcode
        self.result["permalink_url"] = (
            f"https://www.instagram.com/p/{code}" if code else self.final_url
        )

        # Timestamp Unix → datetime
        self.result["posted_at"] = self._parse_timestamp(data.get("taken_at"))

        # Datos del autor
        user = data.get("user")
        self.result["user"] = self._build_user_dict(user) if user else None

        # Metadatos del post
        self.result["group"] = data.get("group")
        self.result["location"] = data.get("location")

        # Tipo de media
        media_type_raw = data.get("media_type", 0)
        self.result["media_type"] = self._map_media_type(media_type_raw)
        if media_type_raw == 8:  # carrusel
            self.result["carousel_media_count"] = data.get("carousel_media_count")

        # Adjuntos multimedia
        self.result["thumbnail"] = data.get("display_uri")
        self.result["image_versions"] = self._safe_get(
            data, "image_versions2", "candidates"
        )
        self.result["video_versions"] = data.get("video_versions")

        # Texto y métricas
        self.result["caption"] = data.get("accessibility_caption")
        self.result["like_count"] = data.get("like_count", 0)
        self.result["comment_count"] = data.get("comment_count", 0)
        self.result["link"] = data.get("link")
        self.result["text"] = self._safe_get(data, "caption", "text")

        # Usuarios etiquetados en la publicación
        self.result["tagged_users"] = self._extract_tagged_users(data)

        # Comentarios de previsualización (preview_comments)
        self.result["comments"] = self._extract_preview_comments(data)

    # ------------------------------------------------------------------
    # Extracción del feed lateral del perfil
    # ------------------------------------------------------------------

    def _extract_feed(self, blocks: list[dict]) -> None:
        """
        Localiza el nodo ``xdt_api__v1__profile_timeline`` y extrae la lista
        de posts adicionales del perfil del autor que Instagram sirve junto
        al post principal.

        Los datos se añaden a ``self.result["feed"]``. Cada ítem del feed
        contiene un subconjunto de los campos del post principal.

        Args:
            blocks: Lista de dicts con todos los bloques JSON del HTML.
        """
        for block in blocks:
            node = self._recursive_search(
                block,
                condition=lambda n: bool(n.get("xdt_api__v1__profile_timeline")),
            )
            if not node:
                continue

            items = self._safe_get(node, "xdt_api__v1__profile_timeline", "items")
            if not items or not isinstance(items, list):
                continue

            for item in items:
                feed_entry = self._build_feed_entry(item)
                # Evitar duplicar el post principal en el feed
                if feed_entry and feed_entry.get("id") != self.result.get("id"):
                    self.result["feed"].append(feed_entry)

            break  # Solo procesar el primer bloque que contenga el feed

    def _build_feed_entry(self, data: dict) -> dict | None:
        """
        Construye un dict resumido de un ítem del feed lateral del perfil.

        Args:
            data: dict del ítem del feed extraído de ``xdt_api__v1__profile_timeline.items``.

        Returns:
            dict con los campos básicos del ítem, o None si no tiene ID.
        """
        post_id = self._safe_get(data, "id")
        if not post_id:
            return None

        # Verificar si ya fue procesado como post principal
        if post_id == self.result.get("id"):
            return None

        code = self._safe_get(data, "code")
        media_type_raw = self._safe_get(data, "media_type", default=0)

        entry: dict[str, Any] = {
            "id": post_id,
            "pk": self._safe_get(data, "pk"),
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
            "user": self._build_user_dict(data.get("user")) if data.get("user") else None,
        }

        if media_type_raw == 8:
            entry["carousel_media_count"] = self._safe_get(data, "carousel_media_count")

        return entry

    # ------------------------------------------------------------------
    # Constructores de subestructuras reutilizables
    # ------------------------------------------------------------------

    def _build_user_dict(self, user: dict) -> dict[str, Any]:
        """
        Construye el diccionario de información de un usuario de Instagram.

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
        Extrae la lista de usuarios etiquetados en la publicación.

        Navega hasta ``usertags.in`` y construye un dict por cada usuario.

        Args:
            data: dict del ítem del post.

        Returns:
            Lista de dicts de usuarios etiquetados (puede estar vacía).
        """
        tagged: list[dict] = []
        tags = self._safe_get(data, "usertags", "in", default=[])
        for tag in tags:
            user = tag.get("user")
            if user:
                tagged.append(self._build_user_dict(user))
        return tagged

    def _extract_preview_comments(self, data: dict) -> list[dict]:
        """
        Extrae los comentarios de previsualización (``preview_comments``).

        Instagram incluye una muestra de comentarios recientes directamente
        en el JSON del post, sin necesidad de petición adicional.

        Args:
            data: dict del ítem del post.

        Returns:
            Lista de dicts con texto y datos básicos del autor de cada comentario.
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
