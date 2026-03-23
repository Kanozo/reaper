"""
utils/fb_auth_detector.py
=========================
Detecta si una página de Facebook requiere autenticación o
si el contenido está bloqueado por privacidad.

Facebook nunca devuelve HTTP 401. En su lugar sirve un HTML aparentemente
normal que internamente contiene señales específicas según el tipo de bloqueo:

    TIPO A — Error de ruta raíz (CometErrorRoute)
    ───────────────────────────────────────────────
    El servidor establece la *ruta raíz* como ``comet.error`` en los bloques
    JSON de bootstrap. Esto ocurre cuando:
      - El post es privado o solo visible para un grupo reducido.
      - El contenido ha sido eliminado.
      - La cuenta está desactivada.

    Señales en el HTML (bloque ScheduledServerJS / JSON embebido):
      · ``"tracePolicy":"comet.error"``
      · ``"canonicalRouteName":"comet.fbweb.CometErrorRoute"``
      · ``"CometErrorRoot.react"`` como recurso del rootView
      · ``"privacy":true`` en las props del rootView

    TIPO B — Muro de login (redirección HTTP)
    ─────────────────────────────────────────
    La URL final contiene ``/login`` o ``/checkpoint``.
    Esto es menos frecuente porque Playwright sigue las redirecciones.

    TIPO C — Popup de login superpuesto  ← NO es bloqueo real
    ──────────────────────────────────────────────────────────
    Página con contenido accesible + dialog de onboarding o
    popup de «inicia sesión para ver más». El contenido sí está
    cargado en el DOM; solo hay un diálogo superpuesto.
    Este caso NO debe tratarse como autenticación requerida.

    ⚠ Por eso NO se usa ``div[role="dialog"]`` como señal: esa etiqueta
      aparece tanto en páginas bloqueadas como en páginas normales.

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
    auth_type: str | None = None  # "error_route" | "login_redirect" | "privacy_wall"


# ===========================================================================
# Señales (ordenadas de más a menos específica)
# ===========================================================================

# ── Tipo A: Señales de ruta de error en bloques JSON ─────────────────────────
# Estas señales están en los <script type="application/json"> que Facebook
# incrusta en el HTML para configurar el cliente React/Relay.
# Son MUY PRECISAS porque pertenecen a la estructura de datos interna.

_JSON_STRONG: list[tuple[str, str]] = [
    # Señal más fiable: tracePolicy es siempre "comet.error" cuando la ruta
    # raíz no puede renderizar el contenido solicitado.
    (r'"tracePolicy"\s*:\s*"comet\.error"',
     "tracePolicy=comet.error en rootView"),

    # Nombre canónico de la ruta de error — solo aparece en páginas de error.
    (r'"canonicalRouteName"\s*:\s*"comet\.fbweb\.CometErrorRoute"',
     "canonicalRouteName=CometErrorRoute"),
]

_JSON_COMBINED: list[tuple[str, str]] = [
    # CometErrorRoot.react como recurso del rootView (puede aparecer en
    # algunas páginas normales como fallback, pero combinado con "privacy":true
    # es inequívoco).
    (r'"__dr"\s*:\s*"CometErrorRoot\.react"',
     "CometErrorRoot.react como recurso de ruta"),

    # privacy:true en los props del rootView indica que Facebook sabe que el
    # contenido existe pero está bloqueado por configuración de privacidad.
    (r'"privacy"\s*:\s*true',
     "privacy=true en props de rootView"),
]

# ── Tipo B: URL final redirigida a login ──────────────────────────────────────
_URL_SIGNALS: list[tuple[str, str]] = [
    (r"facebook\.com/login",                "redirigido a /login"),
    (r"facebook\.com/checkpoint",           "redirigido a /checkpoint"),
    (r"[?&]next=.*(?:permalink|story_fbid|photo)", "login redirect con next="),
]

# ── Señales de contenido PRESENTE (página normal) ────────────────────────────
# Si estas señales aparecen, la página tiene contenido real aunque tenga
# un popup de login encima → NO bloquear.
_CONTENT_PRESENT: list[str] = [
    # La ruta tiene datos de historia/post real
    r'"tracePolicy"\s*:\s*"comet\.post',
    r'"tracePolicy"\s*:\s*"comet\.reels',
    r'"tracePolicy"\s*:\s*"comet\.profile',
    r'"tracePolicy"\s*:\s*"comet\.group(?!\.permalink)',
    r'"tracePolicy"\s*:\s*"comet\.mediaviewer',
    r'"tracePolicy"\s*:\s*"comet\.watch',
    # El viewer tiene un ID real (usuario logueado o contenido público disponible)
    r'"viewer"\s*:\s*\{[^}]*"id"\s*:\s*"\d+',
]


# ===========================================================================
# Función pública
# ===========================================================================

def requires_auth(html_content: str, final_url: str = "") -> AuthResult:
    """Detecta si el HTML de Facebook indica que se requiere autenticación.

    Analiza el HTML en tres capas en orden de prioridad:

        1. Presencia de señales de contenido real (early exit: NO bloqueado)
        2. URL final redirigida a /login o /checkpoint
        3. Señales JSON fuertes (tracePolicy, canonicalRouteName)
        4. Señales JSON combinadas (CometErrorRoot + privacy)

    La función NO usa elementos del DOM (``div[role="dialog"]`` etc.) para
    evitar falsos positivos con popups de onboarding o banners de cookies
    que aparecen en páginas con contenido accesible.

    Args:
        html_content: HTML renderizado por Playwright.
        final_url:    URL final tras redirecciones HTTP.

    Returns:
        :class:`AuthResult` con ``requires_auth=True`` y la ``reason``
        de la primera señal detectada.
    """
    # ── Capa 0: Página vacía ──────────────────────────────────────────────────
    if not html_content:
        return AuthResult(requires_auth=False)

    # ── Capa 1: ¿Hay contenido real? → salir rápido, NO está bloqueado ────────
    # Si la ruta es de post/reel/perfil real, la página tiene contenido
    # aunque tenga un popup de login superpuesto.
    for pattern in _CONTENT_PRESENT:
        if re.search(pattern, html_content, re.I):
            logger.debug("Contenido presente detectado — no se considera bloqueado")
            return AuthResult(requires_auth=False)

    # ── Capa 2: URL final ─────────────────────────────────────────────────────
    if final_url:
        for pattern, reason in _URL_SIGNALS:
            if re.search(pattern, final_url, re.I):
                logger.debug("Auth por URL | reason=%s | url=%s", reason, final_url)
                return AuthResult(
                    requires_auth=True,
                    reason=reason,
                    auth_type="login_redirect",
                )

    # ── Capa 3: Señales JSON fuertes (una sola basta) ─────────────────────────
    for pattern, reason in _JSON_STRONG:
        if re.search(pattern, html_content, re.I):
            logger.debug("Auth por JSON fuerte | reason=%s", reason)
            return AuthResult(
                requires_auth=True,
                reason=reason,
                auth_type="error_route",
            )

    # ── Capa 4: Señales JSON combinadas (necesitan ambas) ─────────────────────
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