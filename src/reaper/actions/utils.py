"""
reaper/actions/utils.py
=======================
Utilidades compartidas por ``ActionManager`` y los flujos de acciones.

Contiene:
- Normalización de identificadores de grupo (id numérico → URL del grupo).
- Validación de rutas de imágenes locales (existencia y extensión).
- Snapshot de enlaces previos para detectar posts nuevos.
- Extracción de post_id / comment_id desde URLs y HTML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from reaper.actions.models import ActionError
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# Extensiones de imagen aceptadas para adjuntar a publicaciones.
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
)

# Grupos de Facebook pueden referirse por ID numérico o por URL completa.
_GROUP_ID_PATTERN = re.compile(r"^\d{6,20}$")
_GROUP_URL_PATTERN = re.compile(r"/groups/(\d{6,20})")

# Patrón de URL de post para extraer el post_id (mismo set que
# ``reaper.actions.facebook.selectors.POST_LINK_PATTERNS``).
# Cada rama captura el post_id en su propio grupo; ``post_id_from_url`` toma
# el primer grupo no vacío.
_POST_URL_PATTERN = re.compile(
    r"permalink\.php\?story_fbid=([A-Za-z0-9]+)"
    r"|story\.php\?story_fbid=([A-Za-z0-9]+)"
    r"|/posts/(pfbid[A-Za-z0-9]+)"
    r"|/posts/(\d{15,20})"
    r"|/permalink/(pfbid[A-Za-z0-9]+)"
    r"|/permalink/(\d{15,20})"
)


def resolve_group(
    group: str,
    base_url: str = "https://www.facebook.com",
) -> str:
    """Normaliza un identificador de grupo a su URL canónica.

    Acepta:
    - ID numérico puro (``"123456789"``).
    - URL de grupo (``"https://www.facebook.com/groups/123459/"``).

    Args:
        group:   Identificador o URL del grupo.
        base_url: Raíz de las URLs de navegación (versión web por defecto).

    Returns:
        URL canónica del grupo: ``{base_url}/groups/{id}/``.

    Raises:
        ValueError: Si el identificador no es un ID numérico ni contiene
            un ID de grupo en su URL.
    """
    group_id = extract_group_id(group)
    if group_id is None:
        raise ValueError(
            f"Identificador de grupo inválido: '{group}'. "
            "Usa un ID numérico o una URL de grupo de Facebook."
        )
    base = base_url.rstrip("/")
    return f"{base}/groups/{group_id}/"


def extract_group_id(group: str) -> str | None:
    """Extrae el ID numérico de un grupo desde id puro o URL.

    Args:
        group: ``"123456789"`` o ``"https://www.facebook.com/groups/123456789/"``.

    Returns:
        ID del grupo como string, o ``None`` si no se puede determinar.
    """
    if _GROUP_ID_PATTERN.match(group):
        return group
    match = _GROUP_URL_PATTERN.search(group)
    return match.group(1) if match else None


def validate_image_paths(image_paths: list[str] | None) -> list[Path]:
    """Valida que las rutas de imagen existan y sean de una extensión soportada.

    Args:
        image_paths: Rutas locales de imágenes. ``None`` o ``[]`` = sin imágenes.

    Returns:
        Lista de ``Path`` absolutos validados (posiblemente vacía).

    Raises:
        ActionError: Si una ruta no existe, es un directorio, o su extensión
            no es soportada.
    """
    if not image_paths:
        return []

    validated: list[Path] = []
    for raw_path in image_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ActionError(f"Imagen no encontrada: '{path}'")
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ActionError(
                f"Extensión de imagen no soportada: '{path.name}'. "
                f"Soportadas: {sorted(SUPPORTED_IMAGE_EXTENSIONS)}"
            )
        validated.append(path)
    return validated


def post_id_from_url(url: str | None) -> str | None:
    """Extrae el post_id numérico de una URL de post de Facebook.

    Args:
        url: URL de un post (story.php, /posts/... o /permalink/...).

    Returns:
        ID del post como string, o ``None`` si la URL no contiene ninguno.
    """
    if not url:
        return None
    match = _POST_URL_PATTERN.search(url)
    if not match:
        return None
    return next((g for g in match.groups() if g), None)


def resolve_profile_url(
    cookies: list[dict[str, Any]] | None,
    base_url: str = "https://www.facebook.com",
) -> str | None:
    """Deriva la URL del perfil propio desde el cookie ``c_user``.

    Facebook identifica al usuario autenticado con el cookie numérico
    ``c_user``; su perfil es ``https://www.facebook.com/profile.php?id={id}``.
    Ese es el destino de navegación usado para publicar en el muro propio.

    Args:
        cookies: Cookie de sesión en formato Playwright.
        base_url: Raíz de las URLs (www.facebook.com por defecto).

    Returns:
        URL del perfil, o ``None`` si no hay ``c_user`` (sesión anónima).
    """
    if not cookies:
        return None
    for cookie in cookies:
        if cookie.get("name") == "c_user" and isinstance(
            cookie.get("value"), str
        ):
            value = cookie["value"].strip()
            if value.isdigit():
                return f"{base_url.rstrip('/')}/profile.php?id={value}"
    return None


def snapshot_story_links(html_content: str) -> set[str]:
    """Recolecta enlaces de posts (story.php / posts / permalink) de un HTML.

    Args:
        html_content: HTML de la página.

    Returns:
        Set de URLs absolutas de posts.
    """
    links: set[str] = set()
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html_content):
        href = match.group(1)
        if _POST_URL_PATTERN.search(href):
            links.add(_abs_url(href))
    return links


def _abs_url(href: str) -> str:
    """Convierte un href relativo a URL absoluta de facebook.com."""
    if href.startswith("/"):
        return f"https://www.facebook.com{href}"
    return href


def cookies_differ(
    original: list[dict[str, Any]],
    updated: list[dict[str, Any]],
) -> bool:
    """Compara cookies por ``(name → value, expires)`` para refrescar sesión.

    Args:
        original: Cookies antes de la sesión.
        updated:  Cookies leídas tras la sesión.

    Returns:
        ``True`` si algún valor/expiración cambió o el conjunto de nombres
        difiere (el servidor rotó tokens o extendió TTLs).
    """

    def index(cookies: list[dict[str, Any]]) -> dict[str, tuple[str, Any]]:
        return {
            cookie.get("name", ""): (
                cookie.get("value", ""),
                cookie.get("expires", 0),
            )
            for cookie in cookies
        }

    return index(original) != index(updated)
