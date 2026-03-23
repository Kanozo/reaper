import base64
import json
import re
import urllib.parse
from typing import Literal
from urllib.parse import urlparse

# Dominios de Facebook usados internamente por is_facebook_video_url.
# El catálogo completo de plataformas está en utils/platforms.py.
_FACEBOOK_DOMAINS: tuple[str, ...] = ("facebook.com", "fb.com", "fb.watch")


# ===========================================================================
# FUNCIONES PÚBLICAS
# ===========================================================================

def extraer_id_video_facebook(url: str) -> str | None:
    """Extrae el ID numérico de un vídeo de Facebook desde cualquier formato de URL.

    Los IDs de vídeo de Facebook son números de 15–20 dígitos. Los patrones
    se evalúan en orden de especificidad: los más explícitos (con contexto
    de ruta) tienen prioridad sobre el genérico de último recurso.

    Args:
        url: URL de Facebook en cualquier formato soportado.

    Returns:
        ID del vídeo como string, o ``None`` si no se encuentra.

    Examples:
        >>> extraer_id_video_facebook("https://www.facebook.com/user/videos/slug/123456789012345/")
        '123456789012345'
        >>> extraer_id_video_facebook("https://www.facebook.com/reel/816043001524221")
        '816043001524221'
        >>> extraer_id_video_facebook("https://www.facebook.com/watch?v=987654321098765")
        '987654321098765'
        >>> extraer_id_video_facebook("https://www.google.com")
        None
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    patrones = [
        r"/videos/[^/]+/(\d{15,20})(?:[/?&#]|$)",
        r"/reel/(\d{15,20})(?:[/?&#]|$)",
        r"/watch[^?]*[?&]v=(\d{15,20})",
        r"[?&](?:v|video_id)=(\d{15,20})",
        r"(?<!\d)(\d{15,20})(?:[/?&#]|$)",
    ]

    for patron in patrones:
        match = re.search(patron, url)
        if match:
            return match.group(1)

    return None


def normalizar_url_facebook(
    url: str,
    base_url: str = "https://www.facebook.com/reel",
) -> str:
    """Convierte una URL de vídeo de Facebook a formato canónico.

    Extrae el ID numérico del vídeo y construye una URL limpia en el
    formato ``{base_url}/{id}``.

    Args:
        url: URL original de Facebook en cualquier formato soportado.
        base_url: Prefijo de la URL resultante. Por defecto apunta al
                  endpoint de reels (``/reel``).

    Returns:
        URL canónica en formato ``https://www.facebook.com/reel/{id}``.

    Raises:
        TypeError: Si ``url`` no es un string.
        ValueError: Si la URL no contiene un ID de vídeo válido.

    Examples:
        >>> normalizar_url_facebook("https://www.facebook.com/watch?v=816043001524221")
        'https://www.facebook.com/reel/816043001524221'
        >>> normalizar_url_facebook(
        ...     "https://www.facebook.com/user/videos/slug/816043001524221/",
        ...     base_url="https://www.facebook.com/videos",
        ... )
        'https://www.facebook.com/videos/816043001524221'
    """
    if not isinstance(url, str):
        raise TypeError(f"Se esperaba un string, se recibió {type(url).__name__}")

    video_id = extraer_id_video_facebook(url)

    if not video_id:
        raise ValueError(
            f"No se pudo extraer un ID de vídeo válido.\n"
            f"URL recibida: {url}\n"
            f"Formatos soportados:\n"
            f"  • /videos/{{usuario}}/{{slug}}/{{ID}}/\n"
            f"  • /reel/{{ID}}\n"
            f"  • /watch?v={{ID}}\n"
            f"  • ?v={{ID}} o ?video_id={{ID}}"
        )

    return f"{base_url}/{video_id}"


def is_facebook_video_url(url: str) -> bool:
    """Comprueba si una URL corresponde a vídeo nativo de Facebook.

    Verifica que la URL pertenezca a un dominio de Facebook y que su
    estructura coincida con alguno de los patrones de vídeo conocidos.

    Args:
        url: URL a validar.

    Returns:
        ``True`` si es URL de vídeo nativo de Facebook, ``False`` en caso
        contrario. Devuelve ``False`` para entradas vacías o no-string.

    Examples:
        >>> is_facebook_video_url("https://www.facebook.com/watch?v=12345678901234")
        True
        >>> is_facebook_video_url("https://www.facebook.com/user/videos/title/123/")
        True
        >>> is_facebook_video_url("https://www.facebook.com/reel/12345678901234")
        False
        >>> is_facebook_video_url("https://www.google.com/watch?v=abc")
        False
    """
    if not url or not isinstance(url, str):
        return False

    url = url.strip()

    try:
        dominio = urlparse(url).netloc
        if not any(d in dominio for d in _FACEBOOK_DOMAINS):
            return False
    except Exception:
        return False

    patrones_video = [
        r"facebook\.com/[^/]+/videos/",
        r"facebook\.com/watch",
        r"facebook\.com/[^/]+/posts/.*\bv=\d",
        r"fb\.watch/",
        r"[?&](?:v|video_id)=\d{15,20}",
        r"/videos/[^/]+/\d{15,20}",
    ]

    return any(re.search(patron, url, re.IGNORECASE) for patron in patrones_video)


def generate_fb_recent_search_url(
    query: str,
    time_range: Literal["hour", "today", "week", "month", "year"] = "hour",
) -> str:
    """Genera una URL de búsqueda de Facebook con filtro temporal.

    Construye la URL codificada para buscar publicaciones recientes en
    Facebook con una ventana de tiempo aplicada. El filtro se codifica en
    base64 según el formato interno de la interfaz web de Facebook.

    Args:
        query: Palabra clave o hashtag. El símbolo ``#`` inicial se elimina
               automáticamente.
        time_range: Ventana temporal: ``"hour"``, ``"today"``, ``"week"``,
                    ``"month"`` o ``"year"``.

    Returns:
        URL completa lista para usar en scraping o navegación.

    Examples:
        >>> url = generate_fb_recent_search_url("#Cuba", time_range="today")
        >>> url.startswith("https://www.facebook.com/search/posts?q=Cuba")
        True
    """
    clean_query = query.lstrip("#")

    filters_obj = {
        "recent_posts:0": json.dumps({"name": "recent_posts", "args": ""}),
        "creation_time:0": json.dumps({
            "name": "creation_time",
            "args": json.dumps({"value": time_range}),
        }),
    }

    filters_serialized = json.dumps({
        k: json.dumps(v) if isinstance(v, dict) else v
        for k, v in filters_obj.items()
    })

    b64_str = (
        base64.b64encode(filters_serialized.encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )
    filters_encoded = urllib.parse.quote(b64_str, safe="")
    query_encoded = urllib.parse.quote(clean_query, safe="")

    return (
        f"https://www.facebook.com/search/posts"
        f"?q={query_encoded}&filters={filters_encoded}&epa=FILTERS"
    )