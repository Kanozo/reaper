"""
reaper/actions/facebook/flows.py
================================
Flujos de UI de Facebook sobre una sesión autenticada.

Cada función recibe una ``Page`` de Playwright ya navegada al destino
(el muro del usuario, el grupo, o la publicación concreta) y ejecuta una
acción de escritura. Devuelven un dict con el desenlace de la acción:

    {
        "status":      "ok" | "error",
        "post_url":    str | None,   # URL de la publicación creada o objetivo
        "post_id":     str | None,   # ID numérico de la publicación
        "comment_id":  str | None,   # ID del comentario (solo comment)
        "note":        str | None,   # Contexto (p.ej. "pendiente de aprobación")
        "error":       str | None,   # Descripción si status="error"
    }

Excepciones:
    - ``ActionError`` si una interacción con la interfaz falla de forma
      irrecuperable (elemento no encontrado). ``ActionManager`` la convierte
      en ``status="error"``.

Confirmación de éxito (detección DOM):
    - ``post_text*``: prioriza una URL de post nueva (``story.php``/``permalink``
      o ``post_id`` en la URL), y si Facebook no la expone (lo habitual en el
      muro) confirma por el texto en el DOM con el diálogo del composer
      cerrado. Requiere pasar ``text=`` a la confirmación.
    - ``comment_on_post``: busca el texto del comentario en el DOM y extrae
      el ``comment_id``.
    - ``like_post``: verifica el marcador activo de la reacción.
    - ``share_post_to_group``: verifica que el diálogo de share se cerró tras
      confirmar.
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

from reaper.actions.facebook import dom, selectors
from reaper.actions.models import ActionError
from reaper.anti_detection.human_behavior import (
    human_type_focused,
    human_type_locator,
)
from reaper.network.interceptor import CapturedTraffic
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# Timeout por defecto para las confirmaciones DOM tras una acción.
DEFAULT_CONFIRM_TIMEOUT: int = 15_000

# Timeout para que el diálogo del composer renderice tras abrirlo. Facebook
# tarda más en redes lentas; el trigger puede clicarse correctamente y el
# diálogo tardar decenas de segundos en aparecer.
COMPOSER_OPEN_TIMEOUT: int = 120_000

# Timeout para escribir y esperar que el botón Publicar se habilite. Tras
# escribir, Facebook tarda en quitar el ``aria-disabled`` con redes lentas.
SUBMIT_TIMEOUT: int = 60_000

# Espera de gracia tras hacer clic en Publicar, ANTES de cerrar la sesión.
# Facebook procesa la publicación de forma asíncrona: la imagen se sube en
# segundo plano y la mutación GraphQL de publish llega después. Si el
# navegador se cierra justo tras el clic, la subida se aborta y el post
# nunca se publica (o la confirmación da un falso positivo con posts de
# otros miembros del feed). Mantener la sesión abierta este tiempo da a la
# plataforma margen para completar la solicitud.
POST_PUBLISH_GRACE_SECONDS: float = 15.0

# Nota contextual cuando un post de grupo se creó pero quedó pendiente de
# aprobación por el administrador (la URL es ``.../pending_posts/{id}/``).
PENDING_APPROVAL_NOTE: str = (
    "El post se creó pero quedó pendiente de aprobación por el "
    "administrador del grupo y aún no es visible para los miembros."
)


# ─────────────────────────────────────────────────────────────────────────────
# Publicar texto (+ imágenes) en el muro propio
# ─────────────────────────────────────────────────────────────────────────────


async def post_text(
    page: Any,
    *,
    text: str,
    image_paths: list[str] | None = None,
    confirm_timeout: int = DEFAULT_CONFIRM_TIMEOUT,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Publica ``text`` (e imágenes opcionales) en el muro de la cuenta.

    El ``page`` debe estar navegado al muro / perfil de la cuenta.

    Args:
        page:           Página de Playwright en el muro.
        text:           Texto de la publicación.
        image_paths:    Rutas locales de imágenes a adjuntar. Opcional.
        confirm_timeout: Timeout de la confirmación DOM.
        traffic:        ``NetworkInterceptor`` opcional; su tráfico GraphQL
            permite confirmar el post con la respuesta real del servidor.

    Returns:
        Dict de desenlace (ver docstring del módulo).
    """
    previous_links = await _story_links(page)

    await dom.wait_for_page_ready(page, timeout=confirm_timeout)
    await _open_composer(page, selectors.COMPOSER_TRIGGER, confirm_timeout)
    await dom.type_best(page, selectors.COMPOSER_INPUT, text, timeout=SUBMIT_TIMEOUT)

    if image_paths:
        await dom.set_files_best(page, selectors.PHOTO_INPUT, image_paths, timeout=confirm_timeout)

    await dom.click_enabled_best(page, selectors.POST_SUBMIT, timeout=SUBMIT_TIMEOUT)

    # Gracia post-publicación: mantener la sesión abierta para que Facebook
    # termine de subir la imagen y procesar la mutación de publish antes de
    # que el manager cierre el navegador.
    logger.debug(
        "Post publicado; esperando %.1fs de gracia antes de cerrar la sesión.",
        POST_PUBLISH_GRACE_SECONDS,
    )
    await asyncio.sleep(POST_PUBLISH_GRACE_SECONDS)

    return await _confirm_new_post(
        page, previous_links, confirm_timeout, text=text, traffic=traffic
    )


# ─────────────────────────────────────────────────────────────────────────────
# Publicar directamente dentro de un grupo
# ─────────────────────────────────────────────────────────────────────────────


async def post_text_group(
    page: Any,
    *,
    text: str,
    image_paths: list[str] | None = None,
    confirm_timeout: int = DEFAULT_CONFIRM_TIMEOUT,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Publica ``text`` (e imágenes opcionales) en un grupo.

    El ``page`` debe estar navegado a la portada del grupo.

    Args:
        page:            Página de Playwright en el grupo.
        text:            Texto de la publicación.
        image_paths:     Rutas locales de las imágenes. Opcional.
        confirm_timeout: Timeout de la confirmación DOM.
        traffic:         ``NetworkInterceptor`` opcional para confirmar con la
            respuesta real del servidor.

    Returns:
        Dict de desenlace (ver docstring del módulo).
    """
    previous_links = await _story_links(page)

    await dom.wait_for_page_ready(page, timeout=confirm_timeout)
    await _open_composer(page, selectors.GROUP_COMPOSER_TRIGGER, confirm_timeout)
    await dom.type_best(page, selectors.GROUP_COMPOSER_INPUT, text, timeout=SUBMIT_TIMEOUT)

    if image_paths:
        await dom.set_files_best(page, selectors.PHOTO_INPUT, image_paths, timeout=confirm_timeout)

    await dom.click_enabled_best(page, selectors.GROUP_POST_SUBMIT, timeout=SUBMIT_TIMEOUT)

    # Gracia post-publicación (idéntica al flujo de muro): Facebook procesa
    # la publicación de forma asíncrona; si se cierra el navegador al momento,
    # la subida de la imagen se aborta y el post no llega al grupo.
    logger.debug(
        "Post de grupo publicado; esperando %.1fs de gracia antes de cerrar.",
        POST_PUBLISH_GRACE_SECONDS,
    )
    await asyncio.sleep(POST_PUBLISH_GRACE_SECONDS)

    return await _confirm_new_post(
        page, previous_links, confirm_timeout, text=text, traffic=traffic
    )


# ─────────────────────────────────────────────────────────────────────────────
# Compartir una publicación en un grupo
# ─────────────────────────────────────────────────────────────────────────────


async def share_post_to_group(
    page: Any,
    *,
    group_name: str,
    confirm_timeout: int = DEFAULT_CONFIRM_TIMEOUT,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Comparte la publicación abierta en ``page`` dentro de un grupo.

    Drives el diálogo "Compartir": busca el grupo por ``group_name``
    y confirma la publicación.

    Args:
        page:            Página del post a compartir.
        group_name:      Nombre (texto visible) del grupo de destino.
        confirm_timeout: Timeout de la confirmación DOM.
        traffic:         ``NetworkInterceptor`` opcional (no se usa en este
            flujo; se acepta para mantener la interfaz común con los flujos
            que sí confirman por tráfico).

    Returns:
        Dict de desenlace con ``post_url`` = URL del post compartido.
    """
    post_url = page.url

    await dom.click_best(page, selectors.SHARE_BUTTON, timeout=confirm_timeout)
    await dom.click_best(page, selectors.SHARE_TO_GROUP_ENTRY, timeout=confirm_timeout)

    await dom.type_best(page, selectors.GROUP_SEARCH_INPUT, group_name, timeout=confirm_timeout)

    # Pequeña pausa para que el resultado del buscador aparezca.
    await asyncio.sleep(1.0)
    await dom.click_best(page, selectors.group_option(group_name), timeout=confirm_timeout)

    await dom.click_best(page, selectors.SHARE_SUBMIT, timeout=confirm_timeout)

    # Confirmación: el diálogo de share debe cerrarse.
    dialog_closed = await dom.wait_any_hidden(
        page, selectors.SHARE_TO_GROUP_ENTRY, timeout=confirm_timeout
    )
    if not dialog_closed:
        return _failure_result(
            "No se confirmó la compartición: el diálogo de share sigue abierto.",
            post_url=post_url,
        )

    logger.info("Post compartido | post_url=%s | group=%s", post_url, group_name)
    return {
        "status": "ok",
        "post_url": post_url,
        "post_id": _post_id_from_url(post_url),
        "comment_id": None,
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Responder con texto a una publicación (comentario)
# ─────────────────────────────────────────────────────────────────────────────


async def comment_on_post(
    page: Any,
    *,
    text: str,
    confirm_timeout: int = DEFAULT_CONFIRM_TIMEOUT,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Comenta ``text`` bajo la publicación abierta en ``page``.

    Args:
        page:            Página de la publicación objetivo.
        text:            Texto del comentario.
        confirm_timeout: Timeout de la confirmación DOM.
        traffic:         ``NetworkInterceptor`` opcional (no se usa en este
            flujo; se acepta para mantener la interfaz común con los flujos
            que sí confirman por tráfico).

    Returns:
        Dict de desenlace con ``comment_id`` si se detectó.
    """
    post_url = page.url
    previous_content = await dom.page_content(page)

    await dom.type_best(page, selectors.COMMENT_BOX, text, timeout=confirm_timeout)

    # Envío: algunos build usan Enter, otros un botón de confirmación.
    try:
        await dom.click_best(page, selectors.COMMENT_SUBMIT, timeout=4_000)
        await asyncio.sleep(0.8)
    except ActionError:
        await dom.press_enter(page)

    return await _confirm_comment(page, post_url, text, previous_content, confirm_timeout)


async def comment_in_feed(
    page: Any,
    article: Any,
    *,
    text: str,
    confirm_timeout: int = DEFAULT_CONFIRM_TIMEOUT,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Comenta ``text`` sobre un artículo del feed SIN navegar al permalink.

    Estrategia inmune a feeds virtualizados (el DOM se reordena al hacer
    scroll o al clic): se captura la posición Y del artículo, se pulsa su
    trigger "Responder"/"Comentar" y la caja de texto se localiza por
    GEOMETRÍA (elemento visible más cercano debajo del ancla), no por
    identidad del nodo. Confirmación por conteo del texto en la página.

    Args:
        page:            Página activa con el feed cargado.
        article:         Locator del artículo (``div[role='article']``).
        text:            Texto del comentario.
        confirm_timeout: Timeout de la confirmación DOM (ms).

    Returns:
        Dict de desenlace con ``comment_id`` si se detectó y
        ``note="feed"`` para trazabilidad.

    Raises:
        ActionError: Si el composer inline no aparece o el envío falla.
    """
    try:
        anchor_box = await article.bounding_box()
    except Exception:
        anchor_box = None
    anchor_top = float(anchor_box["y"]) if anchor_box else None
    normalized = " ".join(text.split())

    baseline_html = await page.content()
    # Subcadena distintiva: los últimos caracteres pueden transformarse en
    # el DOM (links/mentions), el inicio basta y sobra para confirmar.
    needle = normalized[:24]
    baseline_count = _count_occurrences(baseline_html, needle)
    # Variantes para payloads GraphQL: JSON escapa los no-ASCII (\u00ed),
    # así que comparamos contra la forma escapada además del literal.
    needle_variants = [needle]
    try:
        import json as _json

        escaped = _json.dumps(needle, ensure_ascii=True)[1:-1]
        if escaped and escaped != needle:
            needle_variants.append(escaped)
    except Exception:  # pragma: no cover
        pass

    # 1) Abrir el composer inline (algunos builds lo dejan ya enfocable).
    try:
        await dom.click_best_in(article, selectors.COMMENT_LINK, timeout=5_000)
    except ActionError:
        logger.debug("comment_in_feed: sin trigger; se intenta caja directa")

    # 2) Caja de texto: geometría vía Playwright; si no hay layout, foco
    #    quirúrgico vía JS (getBoundingClientRect + clic en el centro).
    composer = await dom.closest_visible_below(
        page, selectors.COMMENT_BOX, anchor_top, timeout=min(confirm_timeout, 10_000)
    )
    if composer is not None:
        await human_type_locator(page, composer, text)
    else:
        focused = await dom.focus_composer_near(
            page, selectors.COMMENT_BOX, anchor_top, timeout=8_000
        )
        if not focused:
            raise ActionError(f"Composer de comentario no enfocable (ancla Y={anchor_top}).")
        await human_type_focused(page, text)

    # 3) Enviar: botón junto a la caja (por geometría, no por el artículo
    #    que ya envejeció) y Enter como refuerzo; segundo Enter a mitad de
    #    ventana si aún no hay confirmación (builds multilínea ignoran el
    #    primero cuando inserta salto de línea).
    submit_baseline = None
    if traffic is not None:
        try:
            get_traffic = getattr(traffic, "get_traffic", None)
            container = get_traffic() if callable(get_traffic) else traffic
            submit_baseline = len(list(container.all_responses()))
        except Exception:
            submit_baseline = None
    try:
        submit_btn = await dom.closest_visible_below(
            page, selectors.COMMENT_SUBMIT, anchor_top, timeout=4_000
        )
        if submit_btn is not None:
            await dom._click_with_fallback(submit_btn, 3_000)
            logger.debug("comment_in_feed | envío vía botón geométrico")
    except ActionError:
        pass
    await dom.press_enter(page)
    await asyncio.sleep(0.8)

    retried_enter = False
    half_deadline = asyncio.get_running_loop().time() + confirm_timeout / 2000

    # 4) Confirmar: prioridad al tráfico GraphQL — SOLO respuestas NUEVAS
    #    posteriores al envío (los listados traen comment_ids de comentarios
    #    viejos que falsearían el hit). DOM como respaldo.
    deadline = asyncio.get_running_loop().time() + confirm_timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        if traffic is not None:
            comment_id = _traffic_comment_hit(traffic, needle_variants, baseline=submit_baseline)
            if comment_id is not None:
                return {
                    "status": "ok",
                    "post_url": page.url,
                    "post_id": None,
                    "comment_id": comment_id,
                    "note": "feed",
                    "error": None,
                }
        current_html = await page.content()
        if _count_occurrences(current_html, needle) > baseline_count:
            comment_id = None
            try:
                comment_id = dom.extract_first_match(current_html, selectors.COMMENT_ID_PATTERNS)
            except Exception:  # pragma: no cover - best effort
                pass
            return {
                "status": "ok",
                "post_url": page.url,
                "post_id": None,
                "comment_id": comment_id,
                "note": "feed",
                "error": None,
            }
        if not retried_enter and asyncio.get_running_loop().time() >= half_deadline:
            retried_enter = True
            logger.debug("comment_in_feed | refuerzo: segundo Enter")
            await dom.press_enter(page)
        await asyncio.sleep(0.5)

    return {
        "status": "error",
        "post_url": page.url,
        "post_id": None,
        "comment_id": None,
        "note": "feed",
        "error": "El comentario no se confirmó por tráfico ni DOM dentro del timeout",
    }


def _traffic_comment_hit(
    traffic: Any,
    needle_variants: list[str],
    *,
    baseline: int | None = None,
) -> str | None:
    """Busca la mutación de comentario confirmada en el tráfico GraphQL.

    Solo examina respuestas NUEVAS posteriores a ``baseline`` (instantánea
    tomada al enviar): los listados del feed traen comment_ids de
    comentarios viejos que falsearían la confirmación.

    Criterio, en orden:
        1. Respuesta con ``comment_id`` numérico.
        2. Respuesta cuyo payload contenga el texto enviado (cualquiera de
           sus variantes: literal o escapada JSON).
        3. Respuesta de operación ``*CreateComment*`` aunque no exponga el
           id en un campo plano.

    Returns:
        El ``comment_id`` detectado (o ``"sin-id"``), o ``None``.
    """
    get_traffic = getattr(traffic, "get_traffic", None)
    container = get_traffic() if callable(get_traffic) else traffic

    if baseline is None:
        baseline = _response_baseline(traffic) or 0
    try:
        all_responses = list(container.all_responses())
    except Exception as exc:
        logger.debug("traffic_comment | contenedor no accesible | %s", exc)
        return None
    fresh = all_responses[baseline:]
    logger.debug(
        "traffic_comment | total=%d | nuevas desde el envío=%d",
        len(all_responses),
        len(fresh),
    )

    for response in reversed(fresh):  # la más reciente primero
        payload = _payload_of(response)
        if not payload:
            continue
        match = re.search(r'"comment_id"\s*:\s*"?(\d{8,40})', payload)
        if match:
            return match.group(1)
        for variant in needle_variants:
            if variant and variant in payload:
                return "sin-id"

    # Refuerzo: alguna respuesta NUEVA de una op *CreateComment*, aunque
    # su payload no exponga campos reconocibles.
    try:
        fresh_ids = {id(response) for response in fresh}
        create_ops = container.graphql_by_operation("CreateComment")
        for response in reversed(create_ops):
            if id(response) not in fresh_ids:
                continue
            if _payload_of(response):
                return "sin-id"
    except Exception:  # pragma: no cover
        pass
    return None


def _response_baseline(traffic: Any) -> int | None:
    """Número de respuestas capturadas hasta ahora (o ``None``)."""
    for getter in ("total_responses", "snapshot_activity"):
        value = getattr(traffic, getter, None)
        if callable(value):
            try:
                return int(value())
            except Exception:
                continue
    return None


def _payload_of(response: Any) -> str:
    """Cuerpo crudo o serializado de una respuesta capturada."""
    raw = getattr(response, "body_raw", None)
    if raw:
        return str(raw)
    try:
        from reaper.network.interceptor import CapturedTraffic

        return str(CapturedTraffic.normalize_body(getattr(response, "body", None)))
    except Exception:
        return ""


def _count_occurrences(html: str, needle: str) -> int:
    """Veces que aparece ``needle`` normalizado en un HTML."""
    if not needle:
        return 0
    haystack = re.sub(r"\s+", " ", html)
    return haystack.count(needle)


async def _article_text(article: Any) -> str:
    """Texto plano normalizado de un artículo del feed."""
    try:
        return re.sub(r"\s+", " ", (await article.inner_text()).strip())
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Reaccionar con "Me gusta"
# ─────────────────────────────────────────────────────────────────────────────


async def like_post(
    page: Any,
    *,
    confirm_timeout: int = DEFAULT_CONFIRM_TIMEOUT,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Da "Me gusta" a la publicación abierta en ``page``.

    La confirmación prioriza el tráfico GraphQL: la respuesta de
    ``CometUFIFeedbackReactMutation`` confirma que el servidor registró la
    reacción (independiente del idioma y del estado previo del botón). Si no
    hay interceptor, se cae a los marcadores DOM de reacción activa.

    Args:
        page:            Página de la publicación objetivo.
        confirm_timeout: Timeout de la confirmación.
        traffic:         ``NetworkInterceptor`` opcional con el tráfico
            GraphQL/API de la sesión.

    Returns:
        Dict de desenlace.
    """
    post_url = page.url

    await dom.click_best(page, selectors.LIKE_BUTTON, timeout=confirm_timeout)

    # Confirmación: primero por tráfico (respuesta de la mutación de
    # reacción), con caída a los marcadores DOM de reacción activa.
    deadline = confirm_timeout / 1000
    elapsed = 0.0
    while elapsed < deadline:
        if traffic is not None:
            try:
                if _like_confirmed_by_traffic(traffic):
                    break
            except Exception as exc:
                logger.debug("Lectura de tráfico falló: %s", exc)
        content = await dom.page_content(page)
        if dom.contains_any(content, selectors.LIKE_ACTIVE_MARKERS):
            break
        await asyncio.sleep(0.3)
        elapsed += 0.3
    else:
        return _failure_result("No se detectó la reacción activa tras dar like.", post_url=post_url)

    logger.info("Like ejecutado | post_url=%s", post_url)
    return {
        "status": "ok",
        "post_url": post_url,
        "post_id": _post_id_from_url(post_url),
        "comment_id": None,
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de confirmación
# ─────────────────────────────────────────────────────────────────────────────


async def _open_composer(
    page: Any,
    trigger_candidates: tuple[str, ...],
    timeout: int,
) -> None:
    """Abre el diálogo del composer de Facebook si aún no está abierto.

    En la UI de escritorio el campo de escritura no está presente hasta que
    se hace clic en el trigger ("¿Qué estás pensando?"). Este helper intenta
    primero localizar un campo editable (composer ya abierto) y, si no lo
    encuentra, hace clic en el trigger para abrir el diálogo.

    Nota: en el nuevo diseño de perfil el campo "colapsado" del composer
    inline (un ``contenteditable`` siempre presente en el DOM) coincide con
    los selectores de ``COMPOSER_INPUT`` aunque el composer **no esté
    expandido**. Para no dar el composer por abierto en falso, se exige que
    además del campo editable exista el botón de publicación visible.

    Args:
        page:               Página de Playwright activa.
        trigger_candidates: Selectores del trigger que abre el composer.
        timeout:            Timeout de espera (ms).

    Raises:
        ActionError: Si no se puede localizar ni abrir el composer.
    """
    # Composer YA abierto = campo editable visible Y botón Publicar también.
    # En el perfil nuevo hay un ``contenteditable`` "colapsado" siempre visible
    # que NO es el composer real: si solo está EDITE, hay que clicar el trigger.
    open_input = await dom.wait_any_visible(page, selectors.COMPOSER_INPUT, timeout=4_000)
    submit_visible = await dom.wait_any_visible(page, selectors.POST_SUBMIT, timeout=4_000)
    if open_input is not None and submit_visible is not None:
        return

    # Varios intentos: la página puede seguir cargando y el primer clic caer
    # sobre un spinner/overlay que aún no se ha ido.
    last_error: str = ""
    for attempt in range(1, 4):
        try:
            await dom.click_best(page, trigger_candidates, timeout=timeout)
            # El diálogo del composer tarda en renderizarse, sobre todo con
            # redes lentas: hay que esperar más que el timeout de confirmación.
            await asyncio.sleep(0.8)
            if (
                await dom.wait_any_visible(
                    page, selectors.COMPOSER_INPUT, timeout=COMPOSER_OPEN_TIMEOUT
                )
                is not None
                and await dom.wait_any_visible(
                    page, selectors.POST_SUBMIT, timeout=COMPOSER_OPEN_TIMEOUT
                )
                is not None
            ):
                return
            last_error = "El clic no abrió el diálogo"
        except ActionError as exc:
            last_error = str(exc)
        logger.debug("Reintento de apertura del composer %d/3 | error=%s", attempt, last_error)
        await asyncio.sleep(2.0)

    raise ActionError(
        "No se pudo abrir el composer de publicación. Candidatos del trigger: "
        f"{dom._describe_candidates(trigger_candidates)} | último error: {last_error}"
    )


async def _confirm_new_post(
    page: Any,
    previous_links: set[str],
    confirm_timeout: int,
    *,
    text: str | None = None,
    traffic: Any | None = None,
) -> dict[str, Any]:
    """Confirma un post nuevo y devuelve su ``post_url`` real.

    Prioridad de confirmación:
    1. El tráfico GraphQL capturado: la respuesta del publish contiene el
       permalink y el id reales del post (fuente más fiable, no depende del
       DOM).
    2. La URL de la página contiene un post id (Facebook navegó al post).
    3. Si se pasó ``text``: el texto aparece en el DOM y el composer se
       cerró (publicó). Se busca además el enlace canónico del post **junto
       al texto** en el HTML; si no aparece, ``post_url`` queda en la URL
       de la página actual.
    4. Sin texto (solo imágenes): apareció un enlace de post nuevo respecto
       al snapshot previo.

    Args:
        page:            Página tras confirmar el post.
        previous_links:  Enlaces de posts capturados antes de publicar.
        confirm_timeout: Timeout de espera.
        text:            Texto del post; permite confirmar sin URL nueva.
        traffic:         ``NetworkInterceptor`` opcional con el tráfico
            GraphQL/API de la sesión.

    Returns:
        Dict de desenlace.
    """
    deadline = confirm_timeout / 1000
    elapsed = 0.0
    owner_id = _owner_id_from_url(page.url)
    while elapsed < deadline:
        # 1) Tráfico GraphQL: la respuesta del publish (ComposerStoryCreate/
        #    Publish) llega tras el clic; se consulta en cada iteración.
        if traffic is not None:
            try:
                found = _new_post_from_traffic(traffic, owner_id=owner_id)
            except Exception as exc:
                logger.debug("Lectura de tráfico falló: %s", exc)
                found = None
            if found:
                new_post_url, new_post_id = found
                logger.debug(
                    "Post confirmado vía tráfico | url=%s | post_id=%s",
                    new_post_url,
                    new_post_id,
                )
                return _ok_result(
                    new_post_url,
                    new_post_id or _post_id_from_url(new_post_url),
                    error=None,
                    note=_pending_approval_note(new_post_url),
                )

        url_post_id = _post_id_from_url(page.url)
        if url_post_id:
            return _ok_result(
                page.url,
                url_post_id,
                error=None,
                note=_pending_approval_note(page.url),
            )

        # Confirmación fiable por texto + diálogo cerrado. Prioriza el
        # permalink junto al texto sobre el primer enlace "nuevo" del DOM.
        if text and await _post_text_published(page, text):
            near_url = None
            try:
                content = await dom.page_content(page)
                near_url = _post_url_near_text(content, text, owner_id=_owner_id_from_url(page.url))
            except Exception as exc:
                logger.debug("Búsqueda de permalink cercano falló: %s", exc)
            if near_url:
                logger.debug("Permalink junto al texto | url=%s", near_url)
                return _ok_result(
                    near_url,
                    _post_id_from_url(near_url),
                    error=None,
                    note=_pending_approval_note(near_url),
                )
            # El texto ya se publicó pero el permalink aún no aparece en el
            # DOM (el muro no se recargó). Si hay interceptor de red, la
            # respuesta de ComposerStoryCreateMutation es la fuente fiable con
            # la URL e id reales: darle tiempo a que llegue en lugar de guardar
            # la URL del perfil con post_id nulo.
            if traffic is not None:
                await asyncio.sleep(0.3)
                elapsed += 0.3
                continue
            return _ok_result(
                page.url,
                _post_id_from_url(page.url),
                error=None,
                note=_pending_approval_note(page.url),
            )

        # Solo imágenes (sin texto): detectar enlace nuevo como último recurso.
        # Solo se acepta si aparece EXACTAMENTE un link nuevo. La paginación
        # del feed (p.ej. en grupos) puede cargar posts de otros miembros
        # durante la sesión; si el diff trae varios enlaces no hay forma de
        # saber cuál es el post publicado sin caer en falsos positivos.
        new_links = await _story_links(page) - previous_links
        if len(new_links) == 1:
            new_post_url = _clean_post_url(min(new_links))
            return _ok_result(
                new_post_url,
                _post_id_from_url(new_post_url),
                error=None,
                note=_pending_approval_note(new_post_url),
            )
        if len(new_links) > 1:
            logger.debug(
                "Varios enlaces nuevos tras publicar (%d); se descarta el diff por ambigüedad.",
                len(new_links),
            )

        await asyncio.sleep(0.3)
        elapsed += 0.3

    return _failure_result("No se detectó la URL del post creado.")


async def _post_text_published(page: Any, text: str) -> bool:
    """True si ``text`` está en el DOM y el composer se cerró (se publicó).

    Con redes lentas o el muro sin recargar, Facebook no navega a una URL de
    post: el contenido editable desaparece (el diálogo se cierra) y el texto
    queda únicamente en la publicación del muro. Esta señal es suficiente
    para dar el post por publicado.
    """
    needle = (text or "").strip()
    if not needle:
        return False
    try:
        content = await dom.page_content(page)
    except Exception as exc:
        logger.debug("page_content falló en confirmación: %s", exc)
        return False
    if needle not in content:
        return False
    # El DIÁLOGO del composer debe haberse cerrado (el post se publicó). No se
    # vigila el contenteditable: el perfil tiene uno "colapsado" siempre
    # visible que haría creer que el composer sigue abierto.
    dialog_open = await dom.wait_any_visible(page, selectors.COMPOSER_DIALOG, timeout=600)
    return dialog_open is None


async def _confirm_comment(
    page: Any,
    post_url: str,
    text: str,
    previous_content: str,
    confirm_timeout: int,
) -> dict[str, Any]:
    """Confirma que el comentario apareció y extrae su ``comment_id``."""
    deadline = confirm_timeout / 1000
    elapsed = 0.0
    while elapsed < deadline:
        content = await dom.page_content(page)
        comment_id = dom.extract_first_match(content, selectors.COMMENT_ID_PATTERNS)
        if comment_id:
            return {
                "status": "ok",
                "post_url": post_url,
                "post_id": _post_id_from_url(post_url),
                "comment_id": comment_id,
                "error": None,
            }
        if text and text in content and text not in previous_content:
            logger.info("Comentario publicado | post_url=%s", post_url)
            return {
                "status": "ok",
                "post_url": post_url,
                "post_id": _post_id_from_url(post_url),
                "comment_id": None,
                "error": None,
            }
        elapsed += 0.4
        await asyncio.sleep(0.4)

    return _failure_result("No se confirmó el comentario en el DOM.", post_url=post_url)


def _failure_result(
    error_message: str,
    *,
    post_url: str | None = None,
    post_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Construye un desenlace de error estándar."""
    return {
        "status": "error",
        "post_url": post_url,
        "post_id": post_id,
        "comment_id": None,
        "note": note,
        "error": error_message,
    }


def _ok_result(
    post_url: str,
    post_id: str | None,
    *,
    error: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    """Construye un desenlace de éxito estándar."""
    return {
        "status": "ok",
        "post_url": post_url,
        "post_id": post_id,
        "comment_id": None,
        "note": note,
        "error": error,
    }


def _pending_approval_note(post_url: str | None) -> str | None:
    """Devuelve la nota de pendiente de aprobación si la URL lo indica.

    Facebook expone los posts de grupo pendientes de aprobación bajo la ruta
    ``.../pending_posts/{id}/``. Cuando la URL confirmada es de esa forma, el
    post se creó pero el administrador aún no lo ha aprobado.

    Args:
        post_url: URL confirmada del post (o ``None``).

    Returns:
        ``PENDING_APPROVAL_NOTE`` si la URL es de un post pendiente, si no
        ``None``.
    """
    if post_url and "/pending_posts/" in post_url:
        return PENDING_APPROVAL_NOTE
    return None


def _post_url_near_text(
    content: str,
    text: str,
    window: int = 4_000,
    *,
    owner_id: str | None = None,
) -> str | None:
    """Localiza el enlace canónico del post más cercano a ``text``.

    Facebook embed el permalink de cada noticia en el JSON del feed, junto al
    texto del post (con ``\\/`` escapado). Este helper desescapa el contenido,
    encuentra la posición del needle y devuelve la primera URL de post dentro
    de una ventana alrededor; ``None`` si no hay ninguna.

    Si se pasa ``owner_id`` (el ``c_user`` del perfil en curso), se exige que
    el permalink pertenezca a ese usuario (``id={owner_id}``). Así se descartan
    posts **de grupos/otras personas** que aparecen en el feed del muro y que
    antes se confundían con el post recién creado.

    Args:
        content:  Texto plano (o HTML) de la página.
        text:     Texto del post buscado.
        window:   Caracteres a mirar a cada lado del needle.
        owner_id: ID numérico del usuario en curso (``c_user``), opcional.

    Returns:
        URL canónica del post más cercano perteneciente al dueño, o ``None``.
    """
    needle = (text or "").strip()
    if not needle:
        return None
    unescaped = content.replace("\\/", "/")
    pos = unescaped.find(needle)
    if pos < 0:
        return None
    start = max(0, pos - window)
    end = min(len(unescaped), pos + len(needle) + window)
    chunk = unescaped[start:end]
    for match in re.finditer(r"https?://[^\"'\s<>\\]+", chunk):
        url = _clean_post_url(match.group(0))
        if not _post_id_from_url(url):
            continue
        if owner_id and f"id={owner_id}" not in url:
            continue
        return url
    return None


def _owner_id_from_url(url: str | None) -> str | None:
    """Extrae el ``id`` de un perfil (``profile.php?id=...``) o c_user del URL."""
    if not url:
        return None
    match = re.search(r"[?&]id=(\d+)", url)
    return match.group(1) if match else None


# Fragmentos que identifican las operaciones GraphQL de "publicar" un post
# (ComposerStoryCreateMutation, ComposerComposerPublish, ...). La respuesta
# contiene el permalink y el id reales del post creado.
_PUBLISH_OPERATION_MARKERS: tuple[str, ...] = (
    "StoryCreate",
    "ComposerComposerPublish",
    "ComposerPublish",
    "Publish",
)


def _post_urls_from_traffic(traffic: Any) -> set[str]:
    """Extrae URLs de post de las respuestas GraphQL del interceptor.

    Busca las respuestas de las operaciones de "publish" (la mutación que
    crea el post) y devuelve los permalinks canónicos del post creado.

    Es la fuente más fiable del ``post_url``: viene de la respuesta del
    servidor, no del DOM. Se recorren todas las cadenas del body y se filtran
    las URLs de post que no pertenecen a grupos (el post al muro propio jamás
    usa la ruta ``/groups/``).

    Args:
        traffic: ``NetworkInterceptor`` con las respuestas capturadas.

    Returns:
        Set de URLs de post canónicas (posiblemente vacío).
    """
    urls: set[str] = set()
    captured = traffic.get_traffic()
    for resp in captured.graphql_responses:
        if not _is_publish_response(resp, captured):
            continue
        for fragment in CapturedTraffic.normalize_body(resp.body):
            urls.update(_collect_post_urls(fragment))

    non_group = {url for url in urls if "/groups/" not in url}
    if non_group:
        return non_group
    return urls


# Patrón del storyID base64 de un post de muro. Decodificado toma la forma
# ``S:_I{actor_id}:{post_id}`` (respuesta directa de ComposerStoryCreateMutation),
# ``S:_I{actor_id}:{post_id}:{post_id}`` (storys dentro del feed refrescado) o
# ``S:_I{actor_id}:VK:{post_id}`` (posts pendientes de aprobación en grupos,
# donde el segmento ``VK:`` sustituye al post_id duplicado). El post_id
# numérico es el mismo en todos los formatos.
_STORY_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^S:_I\d+:(?:VK:)?(?P<post_id>\d{15,20})(:(?P=post_id))?$"
)


def _post_id_from_story_id(raw: Any) -> str | None:
    """Extrae el ``post_id`` numérico de un storyID base64 de Facebook.

    Los storys codifican su id en base64; al decodificar, el patrón es
    ``S:_I{actor_id}:{post_id}:{post_id}`` (publicación normal), o
    ``S:_I{actor_id}:VK:{post_id}`` cuando el post quedó **pendiente de
    aprobación** en un grupo. El id numérico es el ``post_id`` real del post,
    independiente del ``pfbid`` de la URL.

    Args:
        raw: Valor del campo ``id`` de un story (o ``None``).

    Returns:
        El ``post_id`` numérico, o ``None`` si no es un storyID válido.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return None
    match = _STORY_ID_PATTERN.match(decoded)
    return match.group("post_id") if match else None


def _story_create_from_fragment(fragment: dict[str, Any]) -> dict[str, Any] | None:
    """Extrae el story recién creado de un fragmento de publish.

    La respuesta de ``ComposerStoryCreateMutation`` expone el post creado en
    ``data.story_create.story`` con su ``url`` y ``legacy_story_hideable_id``
    reales. Esta es la fuente más fiable del post nuevo: viene directa del
    servidor, sin depender del DOM ni del feed refrescado.

    Cubre también los posts pendientes de aprobación en grupos: en ese caso
    ``publishing_flow == "FALLBACK"``, ``story_id``/``post_id`` llegan ``null``
    y la ``url`` es ``.../pending_posts/{id}/``.

    Args:
        fragment: Fragmento JSON (dict) de la respuesta GraphQL.

    Returns:
        El dict del story creado, o ``None`` si el fragmento no es de
        ``story_create``.
    """
    data = fragment.get("data")
    if not isinstance(data, dict):
        return None
    story_create = data.get("story_create")
    if not isinstance(story_create, dict):
        return None
    story = story_create.get("story")
    return story if isinstance(story, dict) else None


def _new_post_from_traffic(traffic: Any, owner_id: str | None = None) -> tuple[str, str] | None:
    """Extrae el post recién creado de las respuestas de publish.

    A diferencia de ``_post_urls_from_traffic`` (que devuelve todas las URLs),
    esta función identifica el post **nuevo** de forma inequívoca:

    1. Primero busca el ``story_create`` directo de la mutación de publish
       (``data.story_create.story``): fuente más fiable, y la única que cubre
       posts pendientes de aprobación en grupos (URL ``pending_posts/``).
    2. Si no hay story directo, recorre el JSON buscando storys con ``id``
       base64 decodificable que tengan permalink, quedándose con el de mayor
       ``post_id`` numérico (el feed refrescado por el publish incluye posts
       previos).

    Args:
        traffic: ``NetworkInterceptor`` con el tráfico capturado.
        owner_id: ID del perfil (``c_user``); si se pasa, exige que el story
            pertenezca a ese actor.

    Returns:
        Tupla ``(post_url, post_id_numérico)`` o ``None`` si no hay candidato.
    """
    captured = traffic.get_traffic()

    # 1) Story directo de ComposerStoryCreateMutation (más fiable).
    for resp in captured.graphql_responses:
        if not _is_publish_response(resp, captured):
            continue
        for fragment in CapturedTraffic.normalize_body(resp.body):
            story = _story_create_from_fragment(fragment)
            if story is None:
                continue
            post_id = story.get("legacy_story_hideable_id") or _post_id_from_story_id(
                story.get("id")
            )
            url = story.get("url")
            if not post_id or not url:
                continue
            post_url = _clean_post_url(url)
            if owner_id and f"id={owner_id}" not in post_url:
                continue
            logger.debug(
                "Post confirmado vía story_create directo | url=%s | post_id=%s",
                post_url,
                post_id,
            )
            return post_url, str(post_id)

    # 2) Fallback: storys del feed refrescado por la respuesta del publish.
    candidates: list[tuple[str, str]] = []
    for resp in captured.graphql_responses:
        if not _is_publish_response(resp, captured):
            continue
        for fragment in CapturedTraffic.normalize_body(resp.body):
            candidates.extend(_collect_story_candidates(fragment, owner_id))

    if not candidates:
        return None

    owned = [cand for cand in candidates if owner_id is None or f"id={owner_id}" in cand[1]]
    if not owned:
        owned = candidates

    best = max(owned, key=lambda item: int(item[0]))
    return _clean_post_url(best[1]), best[0]


def _collect_story_candidates(node: Any, owner_id: str | None) -> list[tuple[str, str]]:
    """Recorre el JSON buscando storys con post_id numérico + permalink.

    Args:
        node: Nodo (dict, lista o escalar) del body GraphQL.
        owner_id: ID del perfil en curso (filtro opcional por actor).

    Returns:
        Lista de tuplas ``(post_id_numérico, post_url)``.
    """
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        post_id = _post_id_from_story_id(node.get("id"))
        if post_id:
            url = _story_permalink(node, owner_id)
            if url:
                found.append((post_id, url))
        for value in node.values():
            found.extend(_collect_story_candidates(value, owner_id))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_story_candidates(item, owner_id))
    return found


def _story_permalink(node: dict[str, Any], owner_id: str | None) -> str | None:
    """Localiza el permalink de un story dentro de su propio dict/subárbol.

    Facebook deja la URL canónica del post en campos como ``wwwURL``, ``url``
    o ``permalink_url`` del story. Se prefiere una URL que pertenezca al perfil
    en curso (``id={owner_id}``) y que no sea de grupo.

    Args:
        node: Dict del story con su ``id`` base64.
        owner_id: ID del perfil (``c_user``), opcional.

    Returns:
        URL canónica del post, o ``None``.
    """
    candidates: list[str] = []
    for value in _iter_strings(node):
        url = _clean_post_url(value)
        if _post_id_from_any_url(url):
            candidates.append(url)

    owned = [url for url in candidates if f"id={owner_id}" in url] if owner_id else []
    if owned:
        return min(owned)
    non_group = [url for url in candidates if "/groups/" not in url]
    if non_group:
        return min(non_group)
    return min(candidates) if candidates else None


def _iter_strings(node: Any) -> list[str]:
    """Reúne todas las cadenas de un subárbol JSON (para buscar URLs)."""
    strings: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            strings.extend(_iter_strings(value))
    elif isinstance(node, list):
        for item in node:
            strings.extend(_iter_strings(item))
    elif isinstance(node, str):
        strings.append(node)
    return strings


def _is_react_response(resp: Any) -> bool:
    """True si ``resp`` corresponde a la mutación de reacción (like).

    La respuesta de ``CometUFIFeedbackReactMutation`` confirma que el servidor
    registró la reacción; es la fuente más fiable de éxito, mejor que los
    marcadores DOM (que varían con el idioma y el estado).

    Args:
        resp: ``CapturedResponse`` del interceptor.

    Returns:
        True si la operación es una mutación de reacción.
    """
    op = getattr(resp, "operation", None) or ""
    return "ReactMutation" in op and "Feedback" in op


def _like_confirmed_by_traffic(traffic: Any) -> bool:
    """True si el tráfico capturado contiene la mutación de reacción.

    Args:
        traffic: ``NetworkInterceptor`` con las respuestas capturadas.

    Returns:
        True si se confirmó el like por respuesta del servidor.
    """
    captured = traffic.get_traffic()
    for resp in captured.graphql_responses:
        if _is_react_response(resp):
            return True
    return False


def _is_publish_response(
    resp: Any,
    captured: Any,
) -> bool:
    """True si ``resp`` corresponde a una operación de publicación de post.

    Prioriza el ``operation`` capturado en la propia respuesta (el
    ``friendly_name`` del request que la originó, más fiable porque todas las
    peticiones GraphQL comparten el endpoint ``/api/graphql/``). Solo si la
    respuesta no trae ``operation`` se cae al emparejamiento por URL contra
    los requests capturados.
    """
    if getattr(resp, "operation", None):
        return any(marker in resp.operation for marker in _PUBLISH_OPERATION_MARKERS)
    for req in captured.graphql_requests:
        if req.url != resp.url:
            continue
        name = ""
        if req.graphql_meta and req.graphql_meta.friendly_name:
            name = req.graphql_meta.friendly_name
        if any(marker in name for marker in _PUBLISH_OPERATION_MARKERS):
            return True
    return False


def _collect_post_urls(node: Any) -> set[str]:
    """Recorre recursivamente el JSON de la respuesta buscando URLs de post.

    Args:
        node: Dict, lista o escalar del body GraphQL.

    Returns:
        Set de URLs de post limpiadas.
    """
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found.update(_collect_post_urls(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_collect_post_urls(item))
    elif isinstance(node, str):
        for match in re.finditer(r"https?://[^\"'\s<>\\]+", node):
            url = _clean_post_url(match.group(0))
            if _post_id_from_any_url(url):
                found.add(url)
    return found


def _post_id_from_any_url(url: str) -> str | None:
    """Extrae el post_id de una URL, aceptando ``story_fbid`` en cualquier query.

    Más amplio que ``_post_id_from_url`` (que exige ``story.php?story_fbid=``
    justo al inicio): cubre URLs tipo ``profile.php?id=...&story_fbid=pfbid``
    y ``groups/.../posts/{id}``.

    Args:
        url: URL de post de Facebook.

    Returns:
        ID del post (pfbid o numérico), o ``None``.
    """
    cleaned = _clean_post_url(url)
    for pattern in selectors.POST_LINK_PATTERNS:
        match = re.search(pattern, cleaned)
        if match:
            return match.group(1)
    match = re.search(r"[?&]story_fbid=([A-Za-z0-9]+)", cleaned)
    if match:
        return match.group(1)
    match = re.search(r"/posts/(\d{15,20})", cleaned)
    if match:
        return match.group(1)
    # Posts pendientes de aprobación en grupos: ``.../pending_posts/{id}/``.
    match = re.search(r"/pending_posts/(\d{15,20})", cleaned)
    if match:
        return match.group(1)
    return None


def _clean_post_url(url: str) -> str:
    """Limpia una URL de post de basura de tracking (``notif_*``, ``ref=``).

    Facebook añade ``?notif_id=...&notif_t=...&ref=notif`` a los enlaces de
    notificación; esa versión no es el permalink canónico del post.

    Args:
        url: URL cruda (posiblemente con parámetros de tracking).

    Returns:
        URL sin parámetros de notificación y con el dominio www.facebook.com.
    """
    if url.startswith("/"):
        url = f"https://www.facebook.com{url}"
    match = re.match(r"(https?://[^?#]+)(?:\?([^#]*))?", url)
    if not match:
        return url
    base, query = match.group(1), match.group(2) or ""
    keep = [
        param
        for param in query.split("&")
        if param
        and not param.startswith("notif_")
        and not param.startswith("ref=")
        and not param.startswith("_rdc=")
        and not param.startswith("_rdr=")
    ]
    return f"{base}?{'&'.join(keep)}" if keep else base


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de extracción
# ─────────────────────────────────────────────────────────────────────────────


async def _story_links(page: Any) -> set[str]:
    """Recolecta los enlaces de posts visibles en el HTML de la página.

    La UI web de Facebook no expone cada post como un ``href`` simple: el
    enlace canónico viaja incrustado en JSON dentro del HTML
    (``"url":"https:\\/\\/www.facebook.com\\/permalink.php?story_fbid=..."``).
    Por eso se escanea el texto plano del documento (desescapando ``\\/``)
    además de los ``href``.

    Args:
        page: Página de Playwright.

    Returns:
        Set de URLs canónicas de posts (story.php / posts / permalink).
    """
    content = await dom.page_content(page)
    return links_from_html(content)


def links_from_html(html: str) -> set[str]:
    """Extrae las URLs canónicas de posts presentes en un HTML.

    Las URLs salen limpiadas (sin ``notif_*`` ni ``ref=``), de modo que el
    snapshot ``previous_links`` y la comparación posterior usen siempre el
    permalink canónico.

    Args:
        html: HTML (o texto plano) de la página.

    Returns:
        Set de URLs absolutas de posts.
    """
    links: set[str] = set()
    unescaped = html.replace("\\/", "/")
    for pattern in selectors.POST_LINK_PATTERNS:
        links.update(_match_urls(unescaped, pattern))
    return {_clean_post_url(link) for link in links}


def _match_urls(content: str, url_pattern: str) -> list[str]:
    """Reconstruye URLs completas de post a partir de patrones de enlace.

    Busca en el texto plano del documento las URLs que contienen el patrón
    de post (tanto en ``href`` como en payloads JSON) y las devuelve
    canonizadas (con dominio facebook.com y sin escapes).
    """
    results: list[str] = []
    for match in re.finditer(r"https?://[^\"'\s<>]+", content):
        url = match.group(0)
        if re.search(url_pattern, url):
            results.append(_canonical_post_url(url))
    return results


def _canonical_post_url(href: str) -> str:
    """Normaliza un href a la forma ``https://www.facebook.com/story.php?...``."""
    if href.startswith("/"):
        return f"https://www.facebook.com{href}"
    return href


def _post_id_from_url(url: str | None) -> str | None:
    """Extrae el post_id numérico de una URL de post de Facebook."""
    if not url:
        return None
    return dom.extract_first_match(url, selectors.POST_LINK_PATTERNS)
