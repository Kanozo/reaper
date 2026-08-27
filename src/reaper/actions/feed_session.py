"""
reaper/actions/feed_session.py
==============================
Sesión de feed en vivo: scroll y comentario SIN cerrar el navegador.

Modelo opuesto al de las acciones clásicas (una sesión por acción): aquí el
navegador se abre UNA vez, navega al grupo, hace scroll humano, y comenta
cada artículo en cuanto aparece. La sesión la posee
``ActionManager.feed_session``; esta clase solo orquesta sobre la página.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from reaper.actions.facebook import dom, flows, selectors
from reaper.actions.models import ActionError
from reaper.anti_detection.human_behavior import human_scroll
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

#: Artículos con menos texto se consideran shells del DOM (no posts).
MIN_ARTICLE_CHARS: int = 40

#: Firma de dedupe: primeros caracteres normalizados del texto del artículo.
SIGNATURE_CHARS: int = 160


@dataclass(slots=True)
class FeedPostUnit:
    """Artículo vivo del feed listo para interactuar.

    Attributes:
        element:   Locator de Playwright del ``div[role='article']``.
        signature: Huella textual completa (dedupe y re-localización).
    """

    element: Any
    signature: str


ResultCallback = Callable[[dict[str, Any], str], Awaitable[None]]


_PROBE_COMMENTABLE_JS: str = """
(element, triggerTexts) => {
  if (!element || !element.querySelectorAll) return false;
  // 1) Caja de comentario presente (visible o colapsada).
  if (element.querySelector("div[contenteditable='true'][role='textbox'], textarea")) {
    return true;
  }
  // 2) Trigger de acción con texto exacto (es/en), presencia instantánea.
  const buttons = element.querySelectorAll("div[role='button'], span[role='button']");
  for (const btn of buttons) {
    const text = (btn.textContent || "").trim();
    if (triggerTexts.includes(text)) return true;
  }
  return false;
}
"""

#: Textos exactos del trigger de comentario en las UI es-*/en-*.
_TRIGGER_TEXTS: list[str] = ["Responder", "Comentar", "Comment", "Reply"]


async def _is_commentable(article: Any, page: Any | None = None) -> bool:
    """Artículo comentable: caja presente o trigger con texto conocido.

    Sonda INSTANTÁNEA vía JavaScript dentro del elemento: sin esperas de
    Playwright, los primeros posts no se pierden por renders perezosos.
    ``page`` es opcional y solo se usa como respaldo si el evaluate directo
    falla en builds antiguos.
    """
    try:
        probe = article.evaluate(_PROBE_COMMENTABLE_JS, _TRIGGER_TEXTS)
        if probe is True or probe is False:
            return bool(probe)
    except Exception:
        pass
    # Respaldo legacy: sondeo Playwright con presupuesto corto.
    _, locator = await dom._first_match(
        article,
        selectors.COMMENT_BOX + selectors.COMMENT_LINK,
        900,
        require_visible=False,
    )
    return locator is not None


class GroupFeedSession:
    """Orquestador de navegación/scroll/comentario sobre una página viva.

    No posee el navegador: lo inyecta ``ActionManager.feed_session``, que
    además le pasa ``on_result`` para persistir cada comentario.

    Args:
        page:        Página de Playwright autenticada.
        interceptor: ``NetworkInterceptor`` de la sesión (confirmación de
            comentarios por tráfico GraphQL). Opcional pero recomendado.
        on_result:   Callback opcional ``(outcome_dict, text)`` tras cada
            intento de comentario (persistencia/actividad).
    """

    def __init__(
        self,
        page: Any,
        *,
        interceptor: Any | None = None,
        on_result: ResultCallback | None = None,
    ) -> None:
        self._page = page
        self._interceptor = interceptor
        self._on_result = on_result
        logger.debug(
            "FeedSession | interceptor=%s",
            type(interceptor).__name__ if interceptor else "None",
        )

    @property
    def page(self) -> Any:
        """Página de Playwright de la sesión (para trazas y URLs)."""
        return self._page

    async def goto_group(self, group_url: str) -> None:
        """Navega a un grupo dentro de la MISMA sesión de navegador.

        Raises:
            ActionError: Si la navegación cae en formulario de login
                (cookies inválidas/caducadas para la cuenta resuelta).
        """
        logger.debug("FeedSession | navegando a %s", group_url)
        await self._page.goto(group_url, wait_until="domcontentloaded")
        await dom.wait_for_page_ready(self._page)
        # Asentamiento: los action-bars de los primeros posts terminan de
        # montar tras el network-idle; sin esta pausa se saltan artículos.
        await asyncio.sleep(2.0)
        await self._assert_authenticated()

    async def _assert_authenticated(self) -> None:
        """Falla rápido si la página actual es una pantalla de login.

        Solo cuentan señales VISIBLES: el botón azul "Log in" de Facebook
        o un input de email visible. Inputs ocultos sueltos no son señal.
        """
        try:
            outcome = await self._page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    };
                    const royal = document.querySelector(
                        "[data-testid='royal_login_button']"
                    );
                    const emailInput = document.querySelector(
                        "input[name='email'][type!='hidden']"
                    );
                    return {
                        loginVisible: visible(royal) || visible(emailInput),
                        url: location.href,
                    };
                }"""
            )
        except Exception:
            return  # sondeo best-effort; los pasos siguientes fallarán solos
        if outcome.get("loginVisible") or "/login" in str(outcome.get("url", "")):
            raise ActionError(f"cookies inválidas: Facebook muestra login ({outcome.get('url')})")

    async def scroll_posts(
        self,
        max_posts: int,
        *,
        max_rounds: int = 12,
        settle_s_range: tuple[float, float] = (1.5, 3.5),
    ) -> AsyncIterator[FeedPostUnit]:
        """Scroll progresivo; entrega artículos nuevos a medida que aparecen.

        Deduplica por firma textual y salta shells del DOM sin contenido
        suficiente. Detiene el scroll en cuanto entrega ``max_posts``.

        Args:
            max_posts:      Artículos a entregar como máximo.
            max_rounds:     Rondas de scroll antes de rendirse.
            settle_s_range: Pausa humana entre rondas de scroll (s).

        Yields:
            ``FeedPostUnit`` con el locator vivo del artículo.
        """
        seen: set[str] = set()
        delivered = 0
        skipped_no_composer = 0
        for _ in range(max_rounds):
            if delivered >= max_posts:
                return
            articles = self._page.locator(selectors.FEED_ARTICLE[0])
            count = await articles.count()
            for index in range(count):
                if delivered >= max_posts:
                    return
                article = articles.nth(index)
                text = await flows._article_text(article)
                if len(text) < MIN_ARTICLE_CHARS:
                    continue
                signature = text[:SIGNATURE_CHARS]
                if signature in seen:
                    continue
                # Solo entregamos artículos con composer inline propio:
                # comentar es la acción objetivo y evita elegidos "ciegos".
                if not await _is_commentable(article):
                    skipped_no_composer += 1
                    continue
                seen.add(signature)
                delivered += 1
                logger.debug(
                    "FeedSession | post detectado #%d | %s…",
                    delivered,
                    signature[:60],
                )
                yield FeedPostUnit(element=article, signature=signature)
            if delivered < max_posts:
                await human_scroll(self._page, "down")
                await asyncio.sleep(random.uniform(*settle_s_range))
        logger.debug(
            "FeedSession | scroll agotado | entregados=%d | sin composer=%d",
            delivered,
            skipped_no_composer,
        )

    async def report_error(self, error_text: str, *, group: str | None = None) -> None:
        """Registra un fallo de navegación/scroll en la salud de la cuenta.

        Los errores que no pasan por ``comment_article`` (goto, scroll,
        re-localización) también deben descontar salud del rotador.
        """
        if self._on_result is None:
            return
        try:
            await self._on_result(
                {
                    "status": "error",
                    "post_url": self._page.url,
                    "comment_id": None,
                    "group": group,
                    "note": "feed",
                    "error": error_text,
                },
                "",
            )
        except Exception:
            logger.exception("FeedSession | on_result falló al registrar error")

    async def comment_article(
        self,
        unit: FeedPostUnit,
        *,
        text: str,
        confirm_timeout: int = 15_000,
    ) -> dict[str, Any]:
        """Comenta el artículo indicado y notifica el desenlace.

        Si el locator envejeció (el feed reordenó/reelementó), intenta
        re-localizar el artículo por su firma textual antes de fallar.

        Args:
            unit:            Artículo devuelto por :meth:`scroll_posts`.
            text:            Texto del comentario.
            confirm_timeout: Timeout de confirmación DOM (ms).

        Returns:
            Dict de desenlace del flujo ``comment_in_feed``.
        """
        article = await self._fresh_element(unit)
        if article is None:
            outcome = {
                "status": "error",
                "post_url": self._page.url,
                "comment_id": None,
                "note": "feed",
                "error": "artículo no re-localizable en el feed actual",
            }
        else:
            try:
                outcome = await flows.comment_in_feed(
                    self._page,
                    article,
                    text=text,
                    confirm_timeout=confirm_timeout,
                    traffic=self._interceptor,
                )
            except Exception as exc:
                # Convertir a desenlace para que on_result lo registre en la
                # SALUD DE LA CUENTA (si se propaga, el rotador nunca se
                # entera y reelegiría una cuenta rota indefinidamente).
                outcome = {
                    "status": "error",
                    "post_url": self._page.url,
                    "comment_id": None,
                    "note": "feed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        if self._on_result is not None:
            await self._on_result(outcome, text)
        if outcome.get("status") == "ok":
            logger.info("FeedSession | comentario OK | %s…", unit.signature[:60])
        else:
            logger.warning(
                "FeedSession | comentario falló | %s… | %s",
                unit.signature[:60],
                outcome.get("error"),
            )
        return outcome

    async def _fresh_element(self, unit: FeedPostUnit) -> Any | None:
        """Devuelve un locator vivo del artículo, re-localizándolo si hace falta."""
        try:
            current_text = await flows._article_text(unit.element)
        except Exception:
            current_text = ""
        if (
            len(current_text) >= MIN_ARTICLE_CHARS
            and current_text[:SIGNATURE_CHARS] == unit.signature
            and await _is_commentable(unit.element)
        ):
            return unit.element

        logger.debug("FeedSession | locator envejecido; re-localizando por firma")
        articles = self._page.locator(selectors.FEED_ARTICLE[0])
        count = await articles.count()
        for index in range(count):
            candidate = articles.nth(index)
            text = await flows._article_text(candidate)
            if text[:SIGNATURE_CHARS] == unit.signature and await _is_commentable(candidate):
                return candidate
        return None
