from reaper.utils.logger import get_logger
from typing import Any

import requests

logger = get_logger(__name__)

# Headers por defecto que simulan un navegador moderno para evitar bloqueos básicos.
# Se pueden sobreescribir parcialmente pasando el argumento ``headers`` a la función.
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

def get_text_from_url(
    url: str,
    timeout: int = 30,
    encoding: str | None = None,
    headers: dict[str, str] | None = None,
) -> str | None:
    """Obtiene el contenido de texto de una URL mediante una petición GET.

    Usa headers realistas para reducir la probabilidad de ser bloqueado por
    protecciones básicas anti-bot. El encoding se detecta automáticamente
    salvo que se especifique explícitamente.

    Esta función es adecuada para recursos de texto estático (JSON, SRT,
    páginas HTML simples). Para páginas que requieren JavaScript, usar
    ``ContentFetcher`` de ``get_content``.

    Args:
        url: URL completa del recurso a obtener.
        timeout: Segundos máximos de espera para la respuesta. Por defecto 30.
        encoding: Encoding forzado (ej. ``"utf-8"``). Si es ``None``, se usa
                  la detección automática de ``requests`` (``apparent_encoding``).
        headers: Headers adicionales o de sustitución. Se fusionan sobre los
                 headers por defecto: los valores aquí especificados tienen
                 prioridad.

    Returns:
        Contenido de la respuesta como string, o ``None`` si la petición falla
        por cualquier motivo (timeout, error HTTP, error de red, error de encoding).

    Examples:
        >>> texto = get_text_from_url("https://example.com/data.json")
        >>> if texto:
        ...     import json
        ...     data = json.loads(texto)

        >>> # Con headers personalizados y timeout reducido
        >>> texto = get_text_from_url(
        ...     "https://api.example.com/resource",
        ...     timeout=10,
        ...     headers={"Authorization": "Bearer token123"},
        ... )
    """
    request_headers = dict(_DEFAULT_HEADERS)
    if headers:
        request_headers.update(headers)

    try:
        response = requests.get(url, timeout=timeout, headers=request_headers)
        response.raise_for_status()

        # Forzar encoding si se especifica; si no, usar detección automática
        response.encoding = encoding if encoding else response.apparent_encoding
        return response.text

    except requests.exceptions.Timeout:
        logger.warning("Timeout al obtener URL (timeout=%ds): %s", timeout, url)
    except requests.exceptions.HTTPError as exc:
        logger.warning("Error HTTP %s al obtener URL: %s", exc.response.status_code, url)
    except requests.exceptions.RequestException as exc:
        logger.warning("Error de conexión al obtener URL '%s': %s", url, exc)
    except UnicodeDecodeError as exc:
        logger.warning("Error de encoding al decodificar respuesta de '%s': %s", url, exc)

    return None