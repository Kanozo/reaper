import traceback
from typing import Any

from reaper.network.interceptor import CapturedTraffic
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

class PhotoParser(FacebookContentParser):
    """Parser para páginas de foto individual de Facebook.

    Extrae el contenido de una foto publicada: autor, texto del pie de
    foto, imagen, reacciones, shares y comentarios.

    El resultado devuelto es estructuralmente equivalente al de
    ``PostParser`` con ``__typename = "facebook_photo"``.

    Attributes:
        _node_photo: Nodo ``currMedia`` con los datos de la foto.
        _node_story: Nodo con ``creation_story`` y datos del autor.
        _node_ufi: Nodo con el resumen UFI de engagement.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        traffic: CapturedTraffic | None = None,
        debug: bool = False,
    ) -> None:
        """Inicializa el parser de fotos individuales.

        Args:
            html_content: HTML completo renderizado de la página.
            final_url: URL final de la página (tras redirecciones).
            original_url: URL original solicitada por el usuario.
            traffic: Tráfico de red capturado con GraphQL responses.
            debug: Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(html_content, final_url, original_url, traffic, debug)

        self._node_photo: dict | None = None
        self._node_story: dict | None = None
        self._node_ufi: dict | None = None

        self.result.update({
            "__typename": "facebook_photo",
        })

    # ==================================================================
    # PUNTO DE ENTRADA
    # ==================================================================

    def parse(self) -> dict[str, Any]:
        """Ejecuta la extracción completa de la foto.

        Flujo:
            1. Extraer bloques JSON del HTML.
            2. Localizar los tres nodos (photo, story, UFI).
            3. Extraer campos en orden: básicos → autor → imagen
               → texto/hashtags → engagement → comentarios → grupo.
            4. Enriquecer con tráfico GraphQL.

        Returns:
            Diccionario con los datos de la foto. Si falla la
            localización del nodo foto (dato mínimo indispensable),
            ``result["error"]`` describe el motivo.
        """
        logger.info("Iniciando extracción de FOTO | url=%s", self.final_url)

        self._blocks = self._extract_json_blocks()

        if not self._locate_nodes():
            self.result["error"] = (
                "No se encontró el nodo de foto (currMedia __typename=Photo)."
            )
            logger.warning("PhotoParser: nodo foto no encontrado en %s", self.final_url)
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_basic_info()
            self._extract_author()
            self._extract_image()
            self._extract_text()
            self._extract_engagement()
            self._extract_comments_from_html()
            self._extract_group()
            self._parse_traffic()

            logger.debug("Foto parseada correctamente | url=%s", self.final_url)

        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando foto: %s", exc)
            if self.debug:
                traceback.print_exc()

        return self.result

    # ==================================================================
    # LOCALIZACIÓN DE NODOS
    # ==================================================================

    def _locate_nodes(self) -> bool:
        """Localiza los tres nodos de datos del visor de fotos.

        Estrategias de detección:

        - **Nodo foto** (``_node_photo``):
          Primer dict con ``__typename == "Photo"``, subclave ``image``
          (contiene uri/dimensiones) y subclave ``creation_story``.

        - **Nodo story** (``_node_story``):
          Primer dict con ``creation_story.actors`` lista no vacía.

          .. note::
              Intencionadamente **no** se exige que ``message`` sea un
              dict. En fotos de álbum (``MULTI_IMAGE``) el campo
              ``message`` es ``None`` porque el texto pertenece al post
              padre. Usar ``message`` como discriminador rompía la
              detección del autor en esas páginas.

        - **Nodo UFI** (``_node_ufi``):
          Primer dict con ``comet_ufi_summary_and_actions_renderer``
          que a su vez contiene ``feedback``.

        Returns:
            True si se encontró al menos el nodo foto.
            Los otros dos nodos son opcionales (degradan gracefully).
        """
        for block in self._blocks:
            # ── Nodo foto ───────────────────────────────────────────
            if self._node_photo is None:
                self._node_photo = self._recursive_search(
                    block,
                    condition=lambda n: (
                        n.get("__typename") == "Photo"
                        and isinstance(n.get("image"), dict)
                        and isinstance(n.get("creation_story"), dict)
                    ),
                )
                if self._node_photo:
                    logger.debug("Nodo foto localizado.")

            # ── Nodo story (actor) ───────────────────────────────────
            # NOTA: no se filtra por `message` — las fotos de álbum
            # tienen message=None pero sí tienen creation_story.actors.
            if self._node_story is None:
                self._node_story = self._recursive_search(
                    block,
                    condition=lambda n: (
                        isinstance(n.get("creation_story"), dict)
                        and isinstance(n.get("creation_story", {}).get("actors"), list)
                        and len(n.get("creation_story", {}).get("actors", [])) > 0
                    ),
                )
                if self._node_story:
                    logger.debug("Nodo story (actor) localizado.")

            # ── Nodo UFI ────────────────────────────────────────────
            if self._node_ufi is None:
                candidate = self._recursive_search(
                    block,
                    condition=lambda n: (
                        isinstance(n.get("comet_ufi_summary_and_actions_renderer"), dict)
                        and isinstance(
                            n["comet_ufi_summary_and_actions_renderer"].get("feedback"),
                            dict,
                        )
                    ),
                )
                if candidate:
                    self._node_ufi = candidate[
                        "comet_ufi_summary_and_actions_renderer"
                    ]
                    logger.debug("Nodo UFI localizado.")

            if self._node_photo and self._node_story and self._node_ufi:
                break  # Todos encontrados — no seguir iterando

        if self._node_photo is None:
            return False

        if self._node_story is None:
            logger.warning(
                "Nodo story no encontrado. Autor y texto pueden estar vacíos."
            )
        if self._node_ufi is None:
            logger.warning(
                "Nodo UFI no encontrado. Engagement estará en 0."
            )

        return True

    # ==================================================================
    # EXTRACCIÓN DE CAMPOS
    # ==================================================================

    def _extract_basic_info(self) -> None:
        """Extrae id de foto, post_id, timestamp, permalink y post padre.

        Fuentes (en orden de prioridad):
        - ``photo_id`` / ``id``: ``_node_photo.id``
        - ``post_id``: ``_node_photo.creation_story.post_id``
        - ``permalink_url``: ``_node_photo.creation_story.url``
        - ``parent_post_url``: URL del post padre del álbum (solo cuando
          ``business_content_type == "MULTI_IMAGE"``). Se construye con
          ``container_story.post_id`` + ``creation_story.tracking``
          → ``content_owner_id_new``.
          Para fotos de imagen única este campo replica ``permalink_url``.
        - ``posted_at``: ``_node_story.created_time`` o
          ``metadata[0].story.creation_time``, con fallback a
          ``_node_photo.created_time``
        - ``is_sponsored``: ``_node_photo.creation_story.sponsored_data``

        Actualiza ``self.result`` in-place.
        """
        import json as _json

        photo    = self._node_photo
        cs_photo = photo.get("creation_story", {})

        self.result["id"]       = str(cs_photo.get("post_id") or "")
        self.result["permalink_url"] = cs_photo.get("url") or self.final_url
        self.result["is_sponsored"]  = cs_photo.get("sponsored_data") is not None

        # ── URL del post padre ────────────────────────────────────────
        # Para fotos de imagen única el post_id de la foto ES el post:
        #   parent_post_url == permalink_url.
        # Para fotos de álbum (MULTI_IMAGE) el post padre es distinto:
        #   container_story.post_id  → id del álbum
        #   tracking.content_owner_id_new → id numérico del autor
        container = photo.get("container_story") or {}
        album_post_id = container.get("post_id")
        is_album = container.get("business_content_type") == "MULTI_IMAGE"
        self.result["is_album"] = is_album
        if is_album and album_post_id:
            # El owner_id está en creation_story.tracking (JSON en string)
            tracking_raw = cs_photo.get("tracking") or "{}"
            try:
                tracking = _json.loads(tracking_raw) if isinstance(tracking_raw, str) else tracking_raw
            except (ValueError, TypeError):
                tracking = {}
            owner_id = (
                tracking.get("content_owner_id_new")
                or tracking.get("profile_id")
                or ""
            )
            if owner_id:
                self.result["parent_post_url"] = (
                    f"https://www.facebook.com/permalink.php"
                    f"?story_fbid={album_post_id}&id={owner_id}"
                )
            else:
                self.result["parent_post_url"] = self.result["permalink_url"]
        else:
            # Imagen única: el post padre es el propio post
            self.result["parent_post_url"] = self.result["permalink_url"]

        # ── Timestamp ─────────────────────────────────────────────────
        if self._node_story:
            creation_time = self._node_story.get("created_time")
            if creation_time is None:
                cs_story = self._node_story.get("creation_story", {})
                creation_time = self._safe_get(
                    cs_story,
                    "comet_sections", "metadata", 0, "story", "creation_time",
                )
        else:
            creation_time = photo.get("created_time")

        self.result["posted_at"] = self._parse_timestamp(creation_time)

    def _extract_author(self) -> None:
        """Extrae los datos del autor de la foto.

        Ruta principal: ``_node_story.creation_story.actors[0]``
        con foto de perfil en
        ``creation_story.comet_sections.actor_photo.story.actors[0].profile_picture``.

        Fallback: ``_node_story.owner`` o ``_node_photo.owner``.

        Actualiza ``self.result["author"]`` in-place.
        """
        default_author: dict[str, Any] = {
            "id": "unknown",
            "name": "Unknown",
            "url": "",
            "avatar": "",
            "is_verified": False,
            "work_info": "",
        }

        if not self._node_story:
            self.result["author"] = default_author
            return

        cs = self._node_story.get("creation_story", {})
        actors: list[dict] = cs.get("actors", [])

        if not actors:
            # Fallback al owner del story_data o del currMedia
            owner = (
                self._node_story.get("owner")
                or self._node_photo.get("owner")
                or {}
            )
            if owner.get("name"):
                self.result["author"] = {
                    "id":          owner.get("id", "unknown"),
                    "name":        owner.get("name", "Unknown"),
                    "url":         owner.get("url", ""),
                    "avatar":      self._safe_get(owner, "profile_picture", "uri", default=""),
                    "is_verified": owner.get("is_verified", False),
                    "work_info":   "",
                }
            else:
                self.result["author"] = default_author
            return

        actor = actors[0]

        # Avatar: actor_photo tiene la versión 40x40; owner tiene la 120x120
        avatar = self._safe_get(
            cs,
            "comet_sections", "actor_photo", "story",
            "actors", 0, "profile_picture", "uri",
            default="",
        )
        if not avatar:
            # Fallback al owner del story_data (120x120)
            avatar = self._safe_get(
                self._node_story, "owner", "profile_picture", "uri", default=""
            )

        self.result["author"] = {
            "id":          actor.get("id", "unknown"),
            "name":        actor.get("name", "Unknown"),
            "url":         actor.get("url", ""),
            "avatar":      avatar,
            "is_verified": actor.get("is_verified", False),
            "work_info":   actor.get("work_info") or "",
        }

    def _extract_image(self) -> None:
        """Extrae la URL e información de la imagen.

        Campos:
            - ``image``: dict con ``uri``, ``width``, ``height``.
            - ``accessibility_caption``: descripción automática de FB.
            - ``attachments``: lista compatible con el formato de PostParser.

        Actualiza ``self.result`` in-place.
        """
        image = self._node_photo.get("image") or {}

        self.result["attachments"] = [{
            "type":                  "photo",
            "id":                    self._node_photo.get("id", ""),
            "url":                   image.get("uri", ""),
            "width":                 image.get("width"),
            "height":                image.get("height"),
            "accessibility_caption": self._node_photo.get("accessibility_caption") or "",
        }]

    def _extract_text(self) -> None:
        """Extrae el texto del pie de foto y clasifica hashtags/menciones.

        Fuente: ``_node_story.message_preferred_body`` (preferida) o
        ``_node_story.message``.

        Para fotos de álbum (``MULTI_IMAGE``) ambos campos son ``None``
        y ``result["text"]`` quedará como cadena vacía, lo que es
        correcto — el caption pertenece al post padre del álbum.

        Actualiza ``self.result["text"]``, ``["hashtags"]`` y
        ``["mentions"]`` in-place.
        """
        if not self._node_story:
            self.result["text"]     = ""
            self.result["hashtags"] = []
            self.result["mentions"] = []
            return

        msg = (
            self._node_story.get("message_preferred_body")
            or self._node_story.get("message")
            or {}
        )

        self.result["text"] = msg.get("text", "") if isinstance(msg, dict) else ""

        hashtags, mentions = self._parse_ranges(
            msg.get("ranges", []) if isinstance(msg, dict) else []
        )
        self.result["hashtags"] = hashtags
        self.result["mentions"] = mentions

    def _extract_engagement(self) -> None:
        """Extrae reacciones, shares y comentarios del nodo UFI.

        Rutas dentro de ``_node_ufi.feedback``:
            - ``reaction_count.count``
            - ``share_count.count``
            - ``comments_count_summary_renderer.feedback
              .comment_rendering_instance.comments.total_count``
            - ``top_reactions.edges[].{node.id, node.localized_name, reaction_count}``

        Si el nodo UFI no está disponible, todos los contadores quedan en 0.

        Actualiza ``self.result`` in-place.
        """
        if not self._node_ufi:
            self.result["reaction_count"] = 0
            self.result["share_count"]    = 0
            self.result["comments_count"] = 0
            self.result["reactions"]      = []
            return

        fb = self._node_ufi.get("feedback", {})

        self.result["reaction_count"] = self._safe_get(
            fb, "reaction_count", "count", default=0
        )
        self.result["share_count"] = self._safe_get(
            fb, "share_count", "count", default=0
        )
        self.result["comments_count"] = self._safe_get(
            fb,
            "comments_count_summary_renderer", "feedback",
            "comment_rendering_instance", "comments", "total_count",
            default=0,
        )

        self.result["reactions"] = [
            {
                "id":    self._safe_get(edge, "node", "id", default=""),
                "type":  self._safe_get(edge, "node", "localized_name", default="Unknown"),
                "count": edge.get("reaction_count", 0),
            }
            for edge in self._safe_get(fb, "top_reactions", "edges", default=[])
            if self._safe_get(edge, "node")
        ]

    def _extract_comments_from_html(self) -> None:
        """Extrae los comentarios visibles en el HTML de la página.

        En la vista de foto, los comentarios se encuentran en el nodo UFI
        bajo ``comment_list_renderer.feedback
        .comment_rendering_instance_for_feed_location.comments.edges``.

        Actualiza ``self.result["comments"]`` in-place.
        """
        self.result["comments"] = []

        if not self._node_ufi:
            return

        edges = self._safe_get(
            self._node_ufi,
            "comment_list_renderer", "feedback",
            "comment_rendering_instance_for_feed_location",
            "comments", "edges",
            default=[],
        )

        if not edges:
            return

        for edge in edges:
            node = edge.get("node", {})
            if not node:
                continue
            author    = node.get("author") or {}
            author_id = author.get("id", "")
            self.result["comments"].append({
                "id":         node.get("legacy_fbid"),
                "depth":      node.get("depth", 0),
                "text":       self._safe_get(node, "body", "text"),
                "created_at": self._parse_timestamp(node.get("created_time")),
                "author": {
                    "id":          author_id,
                    "name":        author.get("name", ""),
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
                "reactions":      [],
                "reaction_count": self._safe_get(
                    node, "feedback", "reactors", "count_reduced", default=0
                ),
            })

    def _extract_group(self) -> None:
        """Extrae el grupo si la foto pertenece a uno.

        Busca ``creation_story.target_group`` en el nodo story o en el
        nodo foto como fallback.

        Actualiza ``self.result["group"]`` in-place.
        """
        story_for_group = self._node_story or self._node_photo
        cs = story_for_group.get("creation_story", {}) if story_for_group else {}
        target_group = cs.get("target_group")

        if isinstance(target_group, dict) and target_group.get("id"):
            self.result["group"] = {
                "id":     target_group.get("id", ""),
                "name":   target_group.get("name"),
                "url":    target_group.get("url"),
                "avatar": "",
            }
        else:
            self.result["group"] = None

    # ==================================================================
    # PARSEO DE TRÁFICO
    # ==================================================================

    def _parse_traffic(self) -> None:
        """Enriquece ``self.result`` con metadatos del tráfico GraphQL."""
        self._parse_facebook_traffic(
            operation_patterns=["CometPhotoRootContent", "CometPhoto"],
        )