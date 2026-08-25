"""
reaper/actions/facebook/dom.py
==============================
Helpers de interacción con el DOM de Facebook.

Aíslan el patrón de "probar varios selectores candidatos en orden" que usan
todos los flujos de ``reaper.actions.facebook``. Cada helper es ``async`` y
operan sobre una ``Page`` de Playwright. Los candidatos se extraen de
``reaper.actions.facebook.selectors``, pero cualquier tupla de CSS/XPath sirve.

Reglas de los helpers:
- ``wait_any_visible`` devuelve el primer locator con un elemento visible.
- ``click_best``, ``type_best`` y ``set_files_best`` lanzan ``ActionError``
  si ningún candidato coincide, con una traza de candidatos para depurar.
- Los textos se escriben con ``human_type`` de ``reaper.anti_detection``
  para simular escritura humana (velocidad variable y errores tipográficos).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from reaper.actions.models import ActionError
from reaper.anti_detection.human_behavior import human_type
from reaper.utils.logger import get_logger

logger = get_logger(__name__)


async def wait_for_page_ready(
    page: Any,
    timeout: int = 60_000,
    *,
    settle_s: float = 3.0,
) -> None:
    """Espera a que la página termine de cargar antes de interactuar.

    Facebook renderiza en fases: ``domcontentloaded`` ocurre MUCHO antes de
    que el perfil esté operable y mientras tanto muestra spinners/overlays que
    interceptan los clics (el cursor se posiciona pero el clic no cae). Esta
    espera combina:

    1. ``load`` (recursos clave) con grace a un timeout corto.
    2. ``networkidle`` como señal de que el HTML ya no está cambiando.
    3. Un pequeño reposo ``settle_s`` para que React estabilice el layout.

    Args:
        page:      Página de Playwright activa.
        timeout:   Presupuesto total de espera (ms).
        settle_s:  Segundos de reposo extra tras las señales de carga.
    """
    # Facebook (muro/perfil) a veces NUNCA dispara "load" ni "networkidle"
    # (las peticiones son continuas). Ambas esperas son intentos opcionales;
    # lo importante es no interactuar demasiado pronto. El reposo final da
    # tiempo a que React estabilice el layout.
    for state, budget in (("load", 20_000), ("networkidle", 20_000)):
        try:
            await page.wait_for_load_state(state, timeout=budget)
            logger.debug("Load state %r alcanzado.", state)
        except Exception as exc:
            logger.debug("Load state %r no alcanzado (%s); se continúa.", state, exc)
    await asyncio.sleep(settle_s)
    logger.debug("Página lista para interactuar | url=%s", page.url)


async def wait_any_visible(
    page: Any,
    candidates: tuple[str, ...],
    timeout: int = 10_000,
) -> Any | None:
    """Devuelve el primer locator con un elemento visible entre los candidatos.

    Args:
        page:        Página de Playwright activa.
        candidates:  Selectores CSS o XPath probados en orden.
        timeout:     Timeout de visibilidad por candidato (ms).

    Returns:
        Locator del primer elemento visible, o ``None`` si ninguno aparece.
    """
    _, locator = await _first_match(page, candidates, timeout)
    return locator


async def wait_any_visible_many(
    page: Any,
    groups: tuple[tuple[str, ...], ...],
    timeout: int = 10_000,
) -> Any | None:
    """Devuelve el primer locator visible de VARIOS grupos de candidatos.

    A diferencia de ``wait_any_visible``, aquí basta con que un elemento de
    **cualquiera** de los grupos sea visible. Útil cuando una misma condición
    (p.ej. "el composer se abrió") puede manifestarse de varias formas.

    Args:
        page:    Página de Playwright activa.
        groups:  Tupla de tuplas de selectores; cada grupo se prueba aparte.
        timeout: Presupuesto total de sondeo (ms).

    Returns:
        Locator del primer elemento visible de cualquier grupo, o ``None``.
    """
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for candidates in groups:
            selector, locator = await _first_match(page, candidates, 400)
            if locator is not None:
                logger.debug(
                    "Elemento visible en grupo | selector=%s", selector
                )
                return locator
        await asyncio.sleep(0.2)
    return None


async def click_best(
    page: Any,
    candidates: tuple[str, ...],
    timeout: int = 10_000,
) -> None:
    """Hace clic en el primer elemento visible entre los candidatos.

    Args:
        page:       Página de Playwright activa.
        candidates: Selectores CSS o XPath probados en orden.
        timeout:    Presupuesto total de tiempo (ms) para probar candidatos.

    Raises:
        ActionError: Si ningún candidato es visible o el clic falla.
    """
    selector, locator = await _first_match(
        page, candidates, timeout, require_clickable=True
    )
    if locator is None:
        raise ActionError(
            "Elemento no encontrado al hacer clic. Candidatos: "
            f"{_describe_candidates(candidates)}"
        )
    await _click_with_fallback(locator, timeout)
    logger.debug("Clic ejecutado | selector=%s", selector)


async def click_enabled_best(
    page: Any,
    candidates: tuple[str, ...],
    timeout: int = 10_000,
) -> None:
    """Hace clic en el primer candidato visible y *habilitado*.

    Facebook deja el botón de publicación con ``aria-disabled="true"`` hasta
    que hay texto; ``click`` de Playwright no espera por ``aria-disabled``
    (solo por el atributo ``disabled``). Este helper sondea el atributo y
    reintenta hasta que el botón esté habilitado o se agote el timeout.

    Args:
        page:       Página de Playwright activa.
        candidates: Selectores CSS o XPath probados en orden.
        timeout:    Presupuesto total de tiempo (ms).

    Raises:
        ActionError: Si ningún candidato se habilita dentro del timeout.
    """
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        remaining = max(100, int((deadline - time.monotonic()) * 1000))
        _, locator = await _first_match(
            page, candidates, remaining, require_enabled=True
        )
        if locator is not None:
            await _click_with_fallback(locator, remaining)
            logger.debug("Clic en elemento habilitado | selector=%s", candidates[0])
            return
        await asyncio.sleep(0.3)
    raise ActionError(
        "El botón de confirmación no se habilitó. Candidatos: "
        f"{_describe_candidates(candidates)}"
    )


async def type_best(
    page: Any,
    candidates: tuple[str, ...],
    text: str,
    timeout: int = 10_000,
) -> None:
    """Escribe ``text`` con escritura humana en el primer campo visible.

    Args:
        page:       Página de Playwright activa.
        candidates: Selectores CSS o XPath del campo de texto.
        text:       Texto a escribir.
        timeout:    Timeout de visibilidad por candidato (ms).

    Raises:
        ActionError: Si ningún candidato es visible o la escritura falla.
    """
    selector, locator = await _first_match(page, candidates, timeout)
    if locator is None or selector is None:
        raise ActionError(
            "Campo de texto no encontrado. Candidatos: "
            f"{_describe_candidates(candidates)}"
        )
    await human_type(page, selector, text)

    # Espacio mental del usuario antes de pasar a la siguiente acción.
    await asyncio.sleep(0.4)


async def set_files_best(
    page: Any,
    candidates: tuple[str, ...],
    paths: list[str],
    timeout: int = 10_000,
) -> None:
    """Adjunta archivos (imágenes) al primer input file visible.

    El ``input[type=file]`` de Facebook suele estar **oculto** (``display:
    none``): basta con que exista en el DOM para adjuntar con
    ``set_input_files``. El diálogo del composer nuevo expone incluso un input
    con scope ``div[role='dialog'] input[type='file']``.

    Args:
        page:       Página de Playwright activa.
        candidates: Selectores CSS o XPath del ``input[type=file]``.
        paths:      Rutas absolutas de los archivos a adjuntar.
        timeout:    Timeout de presencia por candidato (ms).

    Raises:
        ActionError: Si ningún input de archivo está presente en el DOM.
    """
    selector, locator = await _first_match(
        page, candidates, timeout, require_visible=False
    )
    if locator is None:
        raise ActionError(
            "Input de archivo no encontrado. Candidatos: "
            f"{_describe_candidates(candidates)}"
        )
    await locator.set_input_files(paths)
    logger.debug(
        "Archivos adjuntados | selector=%s | count=%d", selector, len(paths)
    )


async def wait_any_hidden(
    page: Any,
    candidates: tuple[str, ...],
    timeout: int = 10_000,
) -> bool:
    """Espera a que el primer elemento de los candidatos deje de ser visible.

    Útil para confirmar el cierre de diálogos (p.ej. tras compartir un post).

    Args:
        page:        Página de Playwright activa.
        candidates:  Selectores CSS o XPath a vigilar.
        timeout:     Timeout total (ms).

    Returns:
        ``True`` si algún candidato desapareció, ``False`` si pasó el timeout.
    """
    deadline = timeout / 1000
    elapsed = 0.0
    while elapsed < deadline:
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="hidden", timeout=600)
                logger.debug("Elemento oculto | selector=%s", selector)
                return True
            except Exception:
                continue
        await asyncio.sleep(0.25)
        elapsed += 0.25
    return False


async def press_enter(page: Any) -> None:
    """Pulsa Enter (envío de comentarios en Facebook)."""
    await page.keyboard.press("Enter")
    await asyncio.sleep(0.5)


async def wait_for_url_change(
    page: Any,
    previous_url: str,
    timeout: int = 15_000,
) -> bool:
    """Espera a que la URL de la página cambie respecto a ``previous_url``.

    Args:
        page:         Página de Playwright activa.
        previous_url: URL de referencia (antes de la interacción).
        timeout:      Timeout total (ms).

    Returns:
        ``True`` si la URL cambió dentro del timeout, ``False`` si no.
    """
    deadline_seconds = timeout / 1000
    for _ in range(int(deadline_seconds * 4)):
        current_url = page.url
        if current_url and current_url != previous_url:
            logger.debug("URL cambiada | %s → %s", previous_url, current_url)
            return True
        await asyncio.sleep(0.25)
    return False


async def page_content(page: Any) -> str:
    """Devuelve el HTML actual de la página (helpers async)."""
    return str(await page.content())


def extract_first_match(
    content: str,
    patterns: tuple[str, ...],
) -> str | None:
    """Extrae el primer grupo capturado de los patrones sobre ``content``.

    Args:
        content:  HTML (o texto) en el que buscar.
        patterns: Regex con al menos un grupo de captura.

    Returns:
        La primera coincidencia (grupo 1), o ``None`` si no hay ninguna.
    """
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(1)
    return None


def contains_any(content: str, markers: tuple[str, ...]) -> bool:
    """True si ``content`` contiene alguno de los marcadores (case-insensitive)."""
    lowered = content.lower()
    return any(marker.lower() in lowered for marker in markers)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados
# ─────────────────────────────────────────────────────────────────────────────


async def _first_match(
    page: Any,
    candidates: tuple[str, ...],
    timeout: int,
    *,
    require_visible: bool = True,
    require_enabled: bool = False,
    require_clickable: bool = False,
) -> tuple[str | None, Any | None]:
    """Prueba candidatos en orden y devuelve ``(selector, locator)``.

    ``timeout`` es un **presupuesto total** repartido entre todos los
    candidatos (no un timeout por candidato): un elemento ausente no debe
    acumular ``len(candidatos) * timeout`` de espera.

    Args:
        page:              Página de Playwright activa.
        candidates:        Selectores CSS o XPath probados en orden.
        timeout:           Presupuesto total de tiempo (ms).
        require_visible:   Si ``False``, basta con que el elemento esté
            **presente en el DOM** (no visible). Útil para ``input[type=file]``
            que Facebook mantiene oculto y se adjunta con ``set_input_files``.
        require_enabled:   Si ``True``, se ignora cualquier candidato con
            ``aria-disabled="true"`` (Facebook deja el botón Publicar
            deshabilitado hasta que hay texto).
        require_clickable: Si ``True``, además de visible exige que el
            elemento sea **clicable** (pasa la comprobación de acción de
            Playwright). Útil para selectores que coinciden con copias
            ocultas/duplicadas del DOM de Facebook.

    Returns:
        Tupla con el selector coincidente y su locator, o ``(None, None)``
        si ningún candidato cumple los requisitos dentro del presupuesto.
    """
    deadline = time.monotonic() + timeout / 1000
    # Se sondean TODOS los candidatos en rondas, no secuencialmente: así un
    # candidato ausente (p.ej. uno con scope ``div[role='dialog']`` cuando el
    # diálogo aún no renderizó) no consume todo el presupuesto y deja con
    # apenas milisegundos a los genéricos de más abajo. Además, dentro de un
    # mismo candidato se recorren TODAS las coincidencias (no solo
    # ``.first``): Facebook duplica elementos (p.ej. el trigger del composer)
    # y la primera copia suele ser una oculta.
    while time.monotonic() < deadline:
        for selector in candidates:
            loc_list = page.locator(selector)
            count = await loc_list.count()
            if count == 0:
                continue
            for i in range(count):
                locator = loc_list.nth(i)
                try:
                    state = "visible" if require_visible else "attached"
                    await locator.wait_for(state=state, timeout=250)
                    if require_enabled:
                        disabled = await locator.get_attribute("aria-disabled")
                        if disabled in ("true", "True"):
                            continue
                    if require_clickable:
                        box = await locator.bounding_box()
                        if (
                            box is None
                            or box.get("width", 0) <= 1
                            or box.get("height", 0) <= 1
                        ):
                            continue
                    return selector, locator
                except Exception as exc:
                    logger.debug(
                        "Candidato no visible | selector=%s | %s", selector, exc
                    )
        await asyncio.sleep(0.2)
    return None, None


async def _click_with_fallback(locator: Any, timeout: int) -> None:
    """Hace clic en un locator, con reintento robusto si el primero falla.

    Facebook superpone spinners/overlays que interceptan los eventos de
    puntero: Playwright posiciona el cursor sobre el elemento (se ve el mouse
    "parado" encima) pero el clic no se registra y espera hasta el timeout.
    Si el clic normal falla, se reintenta con ``dispatch_event("click")``:
    dispara el handler del elemento directamente, sin depender de que el
    overlay libere el punto. ``force=True`` clicaría el overlay por encima,
    por eso NO se usa aquí.

    Args:
        locator: Locator de Playwright sobre el elemento objetivo.
        timeout: Presupuesto de tiempo para el clic normal (ms).
    """
    click_timeout = max(1_000, min(timeout, 5_000))
    try:
        await locator.click(timeout=click_timeout)
        return
    except Exception as exc:
        logger.debug(
            "Clic normal bloqueado (%s); reintento por handler | selector=%s",
            type(exc).__name__,
            getattr(locator, "_selector", "?"),
        )
    try:
        await locator.dispatch_event("click")
    except Exception as exc:
        logger.debug("dispatch_event falló (%s); clic forzado", exc)
        await locator.click(force=True, timeout=click_timeout)


def _describe_candidates(candidates: tuple[str, ...]) -> str:
    """Formatea la lista de candidatos para mensajes de error legibles."""
    return "; ".join(candidates) if candidates else "(sin candidatos)"
