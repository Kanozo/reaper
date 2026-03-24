"""
reaper/scrapers/facebook.py
===========================
Scraper de orquestación para todo el contenido de Facebook.

Flujo de reintento por muro de autenticación
---------------------------------------------
Cuando se detecta un muro de autenticación en el HTML recibido, el scraper
distingue dos escenarios:

1. **Fetch era anónimo** (sin cuenta activa):
   Si hay un ``AccountManager`` con cuentas disponibles, reintenta
   automáticamente usando ``_fetch_with_account()``. El retry es transparente
   para el caller: si tiene éxito se continúa el parseo normalmente.

2. **Fetch ya estaba autenticado** (``_active_account_id`` asignado):
   Las cookies de esa cuenta han caducado o fueron invalidadas por Facebook.
   Se marca la cuenta como ``COOKIE_EXPIRED`` para excluirla del rotador
   y se reintenta con una cuenta diferente. Si no hay más cuentas disponibles,
   se retorna error.

En ambos casos, si el reintento también devuelve muro de autenticación,
se retorna el error sin seguir intentando (máximo 1 retry por petición).
"""

from datetime import datetime
from typing import Any

from reaper.auth.models import AccountStatus
from reaper.network.content_fetcher import ContentFetcher
from reaper.parsers import (
    GroupParser,
    PhotoParser,
    PostParser,
    ProfileParser,
    ReelParser,
    VideoParser,
)
from reaper.scrapers.base import BaseScraper
from reaper.utils import get_parser_from_fb_url, requires_auth
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de merge reel↔video
# ─────────────────────────────────────────────────────────────────────────────

_POST_FILL_FROM_VIDEO: tuple[str, ...] = (
    "collaborators",
    "reactions",
    "title",
)

_ATTACHMENT_INJECT_FROM_VIDEO_ROOT: tuple[str, ...] = (
    "play_count", "video_view_count", "video_post_view_count",
    "is_gaming_video", "is_podcast_video", "is_looping", "is_spherical",
    "is_video_broadcast", "is_live_streaming", "broadcast_id",
)

_ATTACHMENT_FILL_FROM_VIDEO_SUBDICT: dict[str, str] = {
    "url":         "url_sd",
    "caption":     "captions",
    "duration_ms": "duration_ms",
    "width":       "width",
    "height":      "height",
}

_FEED_ITEM_FILL_FROM_VIDEO_ITEM: tuple[str, ...] = (
    "reaction_count", "comments_count", "posted_at", "permalink_url", "reactions",
)

_FEED_ATTACHMENT_INJECT_FROM_VIDEO_ITEM: tuple[str, ...] = (
    "title", "play_count", "post_play_count",
    "video_post_view_count", "is_live_streaming", "is_video_broadcast",
)

# ─────────────────────────────────────────────────────────────────────────────
# Mapas de parsers
# ─────────────────────────────────────────────────────────────────────────────

_PRIMARY_PARSER_MAP: dict[str, type] = {
    "PostParser":    PostParser,
    "ReelParser":    ReelParser,
    "VideoParser":   VideoParser,
    "GroupParser":   GroupParser,
    "ProfileParser": ProfileParser,
    "HashtagParser": PostParser,
    "PhotoParser":   PhotoParser,
}

_FALLBACK_PARSER_MAP: dict[str, type] = {
    "ReelParser":    VideoParser,
    "ProfileParser": GroupParser,
}


# ─────────────────────────────────────────────────────────────────────────────
# Scraper principal
# ─────────────────────────────────────────────────────────────────────────────


class FacebookScraper(BaseScraper):
    """Scraper para todo tipo de contenido de Facebook.

    Soporta: posts, reels, vídeos, fotos, grupos y perfiles.

    Con AccountManager:
        - Selecciona automáticamente la mejor cuenta disponible.
        - Si la respuesta anónima choca con un muro de auth, reintenta
          automáticamente con una cuenta autenticada.
        - Si la respuesta autenticada choca con un muro de auth, marca
          las cookies de esa cuenta como expiradas y reintenta con otra.

    Sin AccountManager: opera en modo anónimo (comportamiento original).
    """

    async def run(self) -> dict[str, Any]:
        logger.info("Iniciando extracción Facebook | url=%s", self.config.url)

        # ── Fetch primario ────────────────────────────────────────────────────
        # _fetch_with_account() usa una cuenta si hay AccountManager,
        # o hace una petición anónima si no.
        fetch_result = await self._fetch_with_account("facebook")

        if not fetch_result.success:
            error_msg = fetch_result.error or "Error desconocido en el fetch."
            logger.error("Fetch fallido | url=%s | error=%s", self.config.url, error_msg)
            return self._error_result(error_msg)

        final_url = fetch_result.final_url or self.config.url

        # ── Detección de muro de autenticación ───────────────────────────────
        auth = requires_auth(fetch_result.html_content, final_url)
        if auth.requires_auth:
            # Intentar recuperarse con o sin cuenta según el contexto.
            fetch_result = await self._handle_auth_wall(auth.reason, final_url)
            if fetch_result is None:
                # Sin posibilidad de reintento: retornar error.
                return self._error_result(
                    f"Authentication required — {auth.reason}",
                    final_url=final_url,
                )
            # Reintento exitoso: actualizar final_url y continuar el flujo.
            final_url = fetch_result.final_url or final_url

        # ── Selección de parser ───────────────────────────────────────────────
        parser_name   = get_parser_from_fb_url(final_url)
        primary_class = _PRIMARY_PARSER_MAP.get(parser_name)

        logger.info(
            "Parser seleccionado | parser=%s | url_final=%s", parser_name, final_url
        )

        if not primary_class:
            logger.warning(
                "Sin parser disponible | parser=%s | url=%s", parser_name, final_url
            )
            return self._error_result(
                "Authentication required or no parser associated with the request"
            )

        result: dict[str, Any] = primary_class(
            html_content=fetch_result.html_content,
            final_url=final_url,
            original_url=fetch_result.original_url,
            traffic=fetch_result.traffic,
            debug=self.config.debug,
        ).parse()

        # ── Lógica de merge / re-fetch por tipo ───────────────────────────────
        if result.get("__typename") == "facebook_photo":
            parent_post_url = result.get("parent_post_url")
            if result.get("is_album") and result.get("permalink_url") != parent_post_url:
                new_fetch = await self._fetch(override_url=parent_post_url)
                if new_fetch.success:
                    result = PostParser(
                        html_content=new_fetch.html_content,
                        final_url=new_fetch.final_url,
                        original_url=result.get("permalink_url"),
                        traffic=new_fetch.traffic,
                        debug=self.config.debug,
                    ).parse()

        elif result.get("__typename") == "facebook_video":
            reel_url  = result.get("permalink_url", self.config.url)
            if "reel" not in reel_url:
                reel_url = f"https://www.facebook.com/reel/{result.get('id')}"
            new_fetch = await self._fetch(override_url=reel_url)
            if new_fetch.success:
                new_result = ReelParser(
                    html_content=new_fetch.html_content,
                    final_url=new_fetch.final_url,
                    original_url=reel_url,
                    traffic=new_fetch.traffic,
                    debug=self.config.debug,
                ).parse()
                result = self._merge_video_into_reel(
                    reel_result=new_result,
                    video_result=result,
                )

        elif result.get("__typename") == "facebook_reel":
            reel_attachments = result.get("attachments") or []
            reel_video_id = reel_attachments[0].get("id") if reel_attachments else ""
            if reel_video_id:
                video_url = f"https://www.facebook.com/watch/?v={reel_video_id}"
                new_fetch = await self._fetch(override_url=video_url)
                if new_fetch.success:
                    new_result = VideoParser(
                        html_content=new_fetch.html_content,
                        final_url=new_fetch.final_url,
                        original_url=video_url,
                        traffic=new_fetch.traffic,
                        debug=self.config.debug,
                    ).parse()
                    result = self._merge_video_into_reel(
                        reel_result=result,
                        video_result=new_result,
                    )

        # ── Resultado con datos ───────────────────────────────────────────────
        if result.get("raw_data_available"):
            logger.info(
                "Extracción completada | parser=%s | url=%s", parser_name, final_url
            )
            return self._enrich_with_traffic(result, fetch_result)

        # ── Fallback a parser alternativo ─────────────────────────────────────
        fallback_class = _FALLBACK_PARSER_MAP.get(parser_name)

        if fallback_class:
            logger.info(
                "Fallback | %s → %s | url=%s",
                parser_name, fallback_class.__name__, final_url,
            )
            result = fallback_class(
                html_content=fetch_result.html_content,
                final_url=final_url,
                original_url=fetch_result.original_url,
                traffic=fetch_result.traffic,
                debug=self.config.debug,
            ).parse()
            if result.get("raw_data_available"):
                logger.info(
                    "Extracción completada con fallback | parser=%s | url=%s",
                    fallback_class.__name__, final_url,
                )
                return self._enrich_with_traffic(result, fetch_result)

        logger.warning(
            "Extracción sin datos | parser=%s | fallback=%s | url=%s",
            parser_name,
            fallback_class.__name__ if fallback_class else "ninguno",
            final_url,
        )
        return self._enrich_with_traffic(result, fetch_result)

    # ──────────────────────────────────────────────────────────────────────────
    # Manejo del muro de autenticación
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_auth_wall(
        self,
        reason: str | None,
        final_url: str,
    ):
        """Gestiona un muro de autenticación detectado en el HTML.

        Diferencia dos escenarios y actúa en consecuencia:

        **Escenario A — fetch previo era anónimo** (``_active_account_id`` es None):
            Si hay un ``AccountManager`` con cuentas disponibles para Facebook,
            reintenta la petición con una cuenta autenticada. Si no hay
            ``AccountManager`` o no hay cuentas, retorna ``None``.

        **Escenario B — fetch previo ya estaba autenticado**:
            Las cookies de la cuenta activa han caducado o fueron invalidadas
            por Facebook. Se marca la cuenta como ``COOKIE_EXPIRED`` para
            excluirla del rotador permanentemente hasta que se refresquen sus
            cookies. Luego se reintenta con una cuenta diferente.

        En ambos escenarios, si el reintento también devuelve muro de
        autenticación, se retorna ``None`` para que el caller emita el error.
        No se realizan más de un reintento para no entrar en bucles.

        Args:
            reason:    Motivo del muro detectado por ``requires_auth()``.
            final_url: URL final de la petición original (para logging).

        Returns:
            Nuevo ``FetchResult`` exitoso y sin muro de auth, o ``None`` si
            no fue posible recuperarse.
        """
        manager = self.config.account_manager

        # ── Escenario A: fetch anónimo ────────────────────────────────────────
        if self._active_account_id is None:
            if manager is None:
                logger.warning(
                    "Muro de auth en fetch anónimo | reason=%s | "
                    "sin AccountManager — no es posible reintentar | url=%s",
                    reason, final_url,
                )
                return None

            logger.info(
                "Muro de auth en fetch anónimo | reason=%s | "
                "reintentando con cuenta autenticada | url=%s",
                reason, final_url,
            )
            return await self._retry_with_account(reason, final_url)

        # ── Escenario B: fetch ya autenticado → cookies expiradas ─────────────
        expired_account_id = self._active_account_id
        logger.warning(
            "Muro de auth con cuenta autenticada — cookies expiradas | "
            "account_id=%s | reason=%s | url=%s",
            expired_account_id, reason, final_url,
        )

        # Marcar la cuenta como COOKIE_EXPIRED para excluirla del rotador.
        await manager.update_status(
            account_id=expired_account_id,
            status=AccountStatus.COOKIE_EXPIRED,
            notes=(
                f"Cookies invalidadas por Facebook durante scraping. "
                f"Muro detectado: {reason}. "
                f"URL: {final_url}"
            ),
        )

        # Resetear la cuenta activa para que el retry pueda elegir otra.
        self._active_account_id = None

        logger.info(
            "Cuenta marcada COOKIE_EXPIRED | account_id=%s | "
            "reintentando con cuenta diferente | url=%s",
            expired_account_id, final_url,
        )
        return await self._retry_with_account(reason, final_url)

    async def _retry_with_account(
        self,
        original_reason: str | None,
        final_url: str,
    ):
        """Reintenta el fetch con una cuenta autenticada y verifica el resultado.

        Args:
            original_reason: Motivo del muro original (para logging).
            final_url:       URL final para el log en caso de segundo muro.

        Returns:
            ``FetchResult`` si el reintento tuvo éxito y sin muro de auth.
            ``None`` si el reintento falló, el HTML estaba vacío, o el
            reintento también devolvió muro de autenticación.
        """
        retry_result = await self._fetch_with_account("facebook")

        if not retry_result.success:
            logger.warning(
                "Reintento autenticado fallido | error=%s | url=%s",
                retry_result.error, final_url,
            )
            return None

        # Comprobar si el reintento también devuelve muro de auth.
        retry_final_url = retry_result.final_url or self.config.url
        retry_auth = requires_auth(retry_result.html_content, retry_final_url)

        if retry_auth.requires_auth:
            logger.warning(
                "Reintento autenticado también bloqueado | "
                "reason_original=%s | reason_retry=%s | url=%s",
                original_reason, retry_auth.reason, final_url,
            )
            return None

        logger.info(
            "Reintento autenticado exitoso | url=%s", retry_final_url
        )
        return retry_result

    # ──────────────────────────────────────────────────────────────────────────
    # Fetch
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch(
        self,
        override_url: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Crea un ContentFetcher y ejecuta la navegación.

        Args:
            override_url: URL alternativa (fetches secundarios de merge).
            cookies:      Cookies de sesión a inyectar antes de navegar.
        """
        url = override_url or self.config.url
        fetcher = ContentFetcher(
            url=url,
            headless=self.config.headless,
            debug=self.config.debug,
        )
        return await fetcher.fetch(
            screenshot=self.config.screenshot,
            auto_scroll=self.config.auto_scroll,
            infinity_scroll=self.config.infinity_scroll,
            proxy_server=self.config.proxy_server,
            proxy_username=self.config.proxy_username,
            proxy_password=self.config.proxy_password,
            cookies=cookies,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _enrich_with_traffic(
        self, result: dict[str, Any], fetch_result: Any
    ) -> dict[str, Any]:
        result["graphql_responses_count"] = (
            len(fetch_result.traffic.graphql_responses) if fetch_result.traffic else 0
        )
        return result

    def _error_result(self, error_msg: str, final_url: str = "") -> dict[str, Any]:
        return {
            **self._base_result("facebook"),
            "final_url":               final_url or self.config.url,
            "scraped_at":              datetime.now(),
            "error":                   error_msg,
            "raw_data_available":      False,
            "graphql_responses_count": 0,
            "status":                  "error",
        }

    def _merge_video_into_reel(
        self,
        reel_result: dict[str, Any],
        video_result: dict[str, Any],
    ) -> dict[str, Any]:
        for field in _POST_FILL_FROM_VIDEO:
            if field not in reel_result:
                video_value = video_result.get(field)
                if video_value is not None:
                    reel_result[field] = video_value

        reel_result["attachments"] = self._enrich_reel_attachments(
            reel_attachments=reel_result.get("attachments") or [],
            video_result=video_result,
        )
        merged_feed, feed_exclusive = self._merge_feeds(
            reel_feed=reel_result.get("feed") or [],
            video_feed=video_result.get("feed") or [],
        )
        reel_result["feed"]       = merged_feed
        reel_result["feed_video"] = feed_exclusive
        logger.info(
            "Merge video→reel | attachments=%d | feed_merged=%d | feed_video=%d",
            len(reel_result["attachments"]), len(merged_feed), len(feed_exclusive),
        )
        return reel_result

    def _enrich_reel_attachments(
        self,
        reel_attachments: list[dict[str, Any]],
        video_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        video_id: str = video_result.get("id", "")
        video_subdict: dict[str, Any] = video_result.get("video") or {}
        enriched: list[dict[str, Any]] = []

        for attachment in reel_attachments:
            if attachment.get("id") != video_id:
                enriched.append(attachment)
                continue
            for att_field, video_field in _ATTACHMENT_FILL_FROM_VIDEO_SUBDICT.items():
                if att_field not in attachment:
                    source_value = video_subdict.get(video_field)
                    if source_value is not None:
                        attachment[att_field] = source_value
            if "thumbnail_url" not in attachment:
                root_thumb = video_result.get("thumbnail_url")
                if root_thumb is not None:
                    attachment["thumbnail_url"] = root_thumb
            for field in _ATTACHMENT_INJECT_FROM_VIDEO_ROOT:
                if field not in attachment:
                    source_value = (
                        video_subdict.get(field)
                        if field in ("is_looping", "is_spherical")
                        else video_result.get(field)
                    )
                    if source_value is not None:
                        attachment[field] = source_value
            enriched.append(attachment)
        return enriched

    def _merge_feeds(
        self,
        reel_feed: list[dict[str, Any]],
        video_feed: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        video_items_by_id: dict[str, dict[str, Any]] = {
            item["id"]: item for item in video_feed if item.get("id")
        }
        matched_video_ids: set[str] = set()
        feed_merged: list[dict[str, Any]] = []

        for reel_item in reel_feed:
            reel_attachments: list[dict[str, Any]] = reel_item.get("attachments") or []
            content_id: str = reel_attachments[0].get("id", "") if reel_attachments else ""
            video_item = video_items_by_id.get(content_id)

            if video_item is None:
                feed_merged.append(reel_item)
                continue

            matched_video_ids.add(content_id)
            merged_item: dict[str, Any] = dict(reel_item)

            for field in _FEED_ITEM_FILL_FROM_VIDEO_ITEM:
                if field not in merged_item:
                    video_value = video_item.get(field)
                    if video_value is not None:
                        merged_item[field] = video_value

            if reel_attachments:
                att: dict[str, Any] = dict(reel_attachments[0])
                if "thumbnail_url" not in att and video_item.get("thumbnail_url"):
                    att["thumbnail_url"] = video_item["thumbnail_url"]
                if "duration_ms" not in att and video_item.get("duration_ms"):
                    att["duration_ms"] = video_item["duration_ms"]
                for field in _FEED_ATTACHMENT_INJECT_FROM_VIDEO_ITEM:
                    if field not in att:
                        source_field = (
                            "post_play_count"
                            if field == "video_post_view_count"
                            else field
                        )
                        video_value = video_item.get(source_field)
                        if video_value is not None:
                            att[field] = video_value
                merged_item["attachments"] = [att, *reel_attachments[1:]]

            feed_merged.append(merged_item)

        video_only = [
            item for item in video_feed if item.get("id") not in matched_video_ids
        ]
        return feed_merged, video_only
