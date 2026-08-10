"""
utils/fb_auth_detector.py
=========================
Detecta si una página de Facebook o Instagram requiere autenticación,
si el contenido está bloqueado por privacidad, o si fue eliminado.

Ninguna plataforma devuelve HTTP 401. En su lugar sirven un HTML aparentemente
normal que internamente contiene señales específicas según el tipo de bloqueo:

    TIPO 0 — Contenido no disponible  ← tiene MAYOR prioridad en la detección
    ─────────────────────────────────────────────────────────────────────────
    El propietario eliminó el contenido, cambió su privacidad a un grupo muy
    reducido, o la cuenta fue desactivada.

    Facebook:
      Señal JSON: ``"title":"This content isn't available right now"``

    Instagram:
      Texto visible en el HTML: ``Post isn't available`` /
      ``The link may be broken, or the profile may have been removed``

    Resultado en todos los casos:
      ``AuthResult(requires_auth=False, auth_type="content_unavailable")``

    TIPO A — Error de ruta raíz (CometErrorRoute)  — solo Facebook
    ───────────────────────────────────────────────────────────────
    Señales: ``"tracePolicy":"comet.error"``,
             ``"canonicalRouteName":"comet.fbweb.CometErrorRoute"``

    TIPO B — Muro de login (redirección HTTP)
    ─────────────────────────────────────────
    URL final con ``/login`` o ``/checkpoint`` (FB) o ``/accounts/login`` (IG).

    TIPO C — Popup de login superpuesto  ← NO es bloqueo real
    ──────────────────────────────────────────────────────────
    Página con contenido accesible + dialog de onboarding superpuesto.
    ⚠ NO se usa ``div[role="dialog"]`` como señal para evitar falsos positivos.

Función pública::

    requires_auth(html_content, final_url="") -> AuthResult

Python: 3.11+
"""
from reaper.utils.logger import get_logger
import re
from dataclasses import dataclass

logger = get_logger(__name__)


# ===========================================================================
# Resultado
# ===========================================================================

@dataclass
class AuthResult:
    """Resultado del análisis de bloqueo de acceso."""
    requires_auth: bool
    reason: str | None = None   # descripción de la señal detectada
    auth_type: str | None = None  # "content_unavailable" | "error_route" | "login_redirect" | "privacy_wall"


# ===========================================================================
# Señales (ordenadas de más a menos específica)
# ===========================================================================

# ── Tipo A: Señales JSON fuertes en bloques ScheduledServerJS (Facebook) ─────
_JSON_STRONG: list[tuple[str, str]] = [
    (r'"tracePolicy"\s*:\s*"comet\.error"',
     "tracePolicy=comet.error en rootView"),
    (r'"canonicalRouteName"\s*:\s*"comet\.fbweb\.CometErrorRoute"',
     "canonicalRouteName=CometErrorRoute"),
]

_JSON_COMBINED: list[tuple[str, str]] = [
    (r'"__dr"\s*:\s*"CometErrorRoot\.react"',
     "CometErrorRoot.react como recurso de ruta"),
    (r'"privacy"\s*:\s*true',
     "privacy=true en props de rootView"),
]

# ── Tipo B: URL final redirigida a login ──────────────────────────────────────
_URL_SIGNALS: list[tuple[str, str]] = [
    (r"facebook\.com/login",                     "redirigido a /login"),
    (r"facebook\.com/checkpoint",                "redirigido a /checkpoint"),
    (r"[?&]next=.*(?:permalink|story_fbid|photo)", "login redirect con next="),
    (r"instagram\.com/accounts/login",            "ig: redirigido a /accounts/login"),
]

# ── Contenido explícitamente no disponible ────────────────────────────────────
#
# ORDEN CRÍTICO: evaluada ANTES que _CONTENT_PRESENT.
# Un post no disponible puede incluir "comet.post" en el WebLoomConfig de
# métricas (no como tracePolicy real), lo que dispararía un falso negativo
# en _CONTENT_PRESENT si se evaluase primero.
#
# Una sola señal basta para clasificar como content_unavailable.
_CONTENT_UNAVAILABLE: list[tuple[str, str]] = [

    # ── Facebook ─────────────────────────────────────────────────────────────
    (
        r'"title"\s*:\s*"This content isn\'t available(?: right now)?"',
        "fb_content_unavailable: título rootView — contenido eliminado o restringido",
    ),
    (
        r'"body"\s*:\s*"When this happens[^"]*(?:deleted|small group)',
        "fb_content_unavailable: body rootView — contenido eliminado o restringido",
    ),

    # ── Instagram ─────────────────────────────────────────────────────────────
    # Texto visible que Instagram incluye en el HTML cuando el post no existe,
    # fue eliminado, o su privacidad impide el acceso.
    # Presente tanto en el <title> como en el cuerpo del HTML.
    (
        r"Post isn't available",
        "ig_content_unavailable: Post isn't available",
    ),
    (
        r"The link may be broken,?\s+or the profile may have been removed",
        "ig_content_unavailable: link broken or profile removed",
    ),
]

# ── Señales de contenido PRESENTE (página normal) ────────────────────────────
# Si alguna de estas señales aparece, la página tiene contenido real aunque
# tenga un popup de login superpuesto → NO clasificar como bloqueado.
_CONTENT_PRESENT: list[str] = [
    # ── Facebook ──────────────────────────────────────────────────────────────
    r'"tracePolicy"\s*:\s*"comet\.post',
    r'"tracePolicy"\s*:\s*"comet\.reels',
    r'"tracePolicy"\s*:\s*"comet\.profile',
    r'"tracePolicy"\s*:\s*"comet\.group(?!\.permalink)',
    r'"tracePolicy"\s*:\s*"comet\.mediaviewer',
    r'"tracePolicy"\s*:\s*"comet\.watch',
    r'"viewer"\s*:\s*\{[^}]*"id"\s*:\s*"\d+',

    # ── Instagram — señales JSON ───────────────────────────────────────────────
    # "polaris.httpErrorPage" queda explícitamente FUERA para que los errores
    # de IG no reciban un falso «contenido presente».
    r'"tracePolicy"\s*:\s*"polaris\.post',
    r'"tracePolicy"\s*:\s*"polaris\.reel',
    r'"tracePolicy"\s*:\s*"polaris\.profile',
    r'"tracePolicy"\s*:\s*"polaris\.explore',
    r'"tracePolicy"\s*:\s*"polaris\.tag',
]


# ===========================================================================
# Función pública
# ===========================================================================

def requires_auth(html_content: str, final_url: str = "") -> AuthResult:
    """Detecta si el HTML de Facebook o Instagram indica que se requiere autenticación.

    Analiza el HTML en capas ordenadas de más específica a más general.
    El orden es crítico: capas más específicas deben ejecutarse primero
    para evitar que señales generales produzcan falsos negativos.

        1. Contenido explícitamente no disponible — dos niveles de señales:
             · JSON (ScheduledServerJS, HTML SSR): canonicalRouteName, tracePolicy
             · DOM (post-hidratación): <title>, <span> con texto de error
        2. Presencia de señales de contenido real (early exit: NO bloqueado)
        3. URL final redirigida a /login, /checkpoint (FB) o /accounts/login (IG)
        4. Señales JSON fuertes (tracePolicy, canonicalRouteName) — solo Facebook
        5. Señales JSON combinadas (CometErrorRoot + privacy) — solo Facebook

    Args:
        html_content: HTML renderizado por Playwright (SSR o post-hidratación).
        final_url:    URL final tras redirecciones HTTP.

    Returns:
        :class:`AuthResult`:
            - ``requires_auth=False`` + ``auth_type="content_unavailable"`` si
              el contenido fue eliminado o restringido permanentemente (FB e IG).
            - ``requires_auth=False`` sin ``auth_type`` si la página tiene
              contenido real.
            - ``requires_auth=True`` con ``auth_type`` correspondiente si hay
              muro de auth.
    """
    # ── Capa 0: Página vacía ──────────────────────────────────────────────────
    if not html_content:
        return AuthResult(requires_auth=False)

    # ── Capa 1: ¿Contenido explícitamente no disponible? ─────────────────────
    # Cubre señales JSON (HTML SSR) y señales DOM (HTML post-hidratación).
    # Se evalúa ANTES que _CONTENT_PRESENT — ver nota en _CONTENT_UNAVAILABLE.
    for pattern, reason in _CONTENT_UNAVAILABLE:
        if re.search(pattern, html_content, re.I | re.S):
            logger.debug("Contenido no disponible detectado | reason=%s", reason)
            return AuthResult(
                requires_auth=False,
                reason=reason,
                auth_type="content_unavailable",
            )

    # ── Capa 2: ¿Hay contenido real? → salir rápido, NO está bloqueado ────────
    for pattern in _CONTENT_PRESENT:
        if re.search(pattern, html_content, re.I):
            logger.debug("Contenido presente detectado — no se considera bloqueado")
            return AuthResult(requires_auth=False)

    # ── Capa 3: URL final ─────────────────────────────────────────────────────
    if final_url:
        for pattern, reason in _URL_SIGNALS:
            if re.search(pattern, final_url, re.I):
                logger.debug("Auth por URL | reason=%s | url=%s", reason, final_url)
                return AuthResult(
                    requires_auth=True,
                    reason=reason,
                    auth_type="login_redirect",
                )

    # ── Capa 4: Señales JSON fuertes (una sola basta) ─────────────────────────
    for pattern, reason in _JSON_STRONG:
        if re.search(pattern, html_content, re.I):
            logger.debug("Auth por JSON fuerte | reason=%s", reason)
            return AuthResult(
                requires_auth=True,
                reason=reason,
                auth_type="error_route",
            )

    # ── Capa 5: Señales JSON combinadas (necesitan ambas) ─────────────────────
    combined_hits: list[str] = []
    for pattern, reason in _JSON_COMBINED:
        if re.search(pattern, html_content, re.I):
            combined_hits.append(reason)

    if len(combined_hits) >= 2:
        reason_str = " + ".join(combined_hits)
        logger.debug("Auth por JSON combinado | reasons=%s", reason_str)
        return AuthResult(
            requires_auth=True,
            reason=reason_str,
            auth_type="privacy_wall",
        )

    return AuthResult(requires_auth=False)