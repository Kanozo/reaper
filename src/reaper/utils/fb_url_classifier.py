"""
utils/fb_url_classifier.py
Clasificador de URLs de Facebook: determina el parser a utilizar.

Expone una única función pública, ``get_parser_from_fb_url``, que recibe
una URL de Facebook y devuelve el nombre del parser que debe instanciarse
para procesarla. La decisión se toma analizando la estructura de la URL
sin realizar ninguna petición HTTP.

Reglas de clasificación (en orden de evaluación)::

    /hashtag/<tag>                              → HashtagParser
    /reel/<id>                                  → ReelParser
    /<x>/videos/<slug>/<id>  o  ?v=<id>        → VideoParser
    photo.php?fbid=<id>  o  /photo/?fbid=<id>  → PhotoParser
    /groups/<x>/posts/<id>                      → PostParser
    /groups/<x>/permalink/<id>                  → PostParser
    /groups/<x>  (sin posts/permalink)          → GroupParser
    /<x>/posts/<id>  (fuera de groups)          → PostParser
    /<x>/posts/<slug>/<id>                      → PostParser  ← NUEVO
    profile.php?id=<id>                         → ProfileParser
    story.php?story_fbid=  o  permalink.php?story_fbid=  → PostParser
    permalink.php?id=<id>  (sin story_fbid)    → PostParser
    share/r/<id>                                → ReelParser
    share/p/<id>                                → PostParser
    share/v/<id>                                → ReelParser
    share/<id>  (sin sufijo)                    → PostParser
    /people/<nombre>/<id_numerico>/             → ProfileParser
    /<vanity>  (slug sin prefijos conocidos)    → ProfileParser
    Cualquier otro caso                         → UnknownParser

Python: 3.13+
"""
import re
from urllib.parse import parse_qs, urlencode, urlparse

# ---------------------------------------------------------------------------
# Parámetros de tracking eliminados durante la normalización.
# ---------------------------------------------------------------------------
_TRACKING: frozenset[str] = frozenset({
    "__cft__", "__tn__", "mibextid", "app", "rdid", "ref", "__eep__", "sk",
})

# ---------------------------------------------------------------------------
# Segmentos de ruta reservados por Facebook.
# Una URL cuyo primer segmento sea uno de estos valores NO es una vanity URL
# de usuario o página.
#
# CORRECCIÓN: añadidos photo.php y video.php para que Rule 14 (vanity) no
# los capture y los clasifique incorrectamente como ProfileParser.
# Sin esta corrección, photo.php → ProfileParser → fallback GroupParser,
# y el usuario recibía resultados de grupo vacíos en lugar de post.
# ---------------------------------------------------------------------------
_RESERVED: frozenset[str] = frozenset({
    "groups", "pages", "events", "marketplace", "watch", "gaming",
    "photos", "videos", "reels", "stories", "live", "notifications",
    "messages", "friends", "bookmarks", "saved", "search", "explore",
    "help", "settings", "privacy", "about", "ads", "business",
    "profile.php", "permalink.php", "login", "logout", "share", "hashtag",
    # ── AÑADIDOS ─────────────────────────────────────────────────────────
    "photo.php",    # foto individual → PhotoParser (no ProfileParser)
    "photo",        # /photo/?fbid=... → PhotoParser
    "video.php",    # por si acaso — similar a photo.php
})


# ===========================================================================
# API PÚBLICA
# ===========================================================================

def get_parser_from_fb_url(url: str) -> str:
    """Determina el parser a utilizar para una URL de Facebook.

    Args:
        url: URL de Facebook a clasificar.

    Returns:
        Nombre de la clase de parser:
        ``"PostParser"``, ``"ReelParser"``, ``"VideoParser"``,
        ``"GroupParser"``, ``"ProfileParser"``, ``"PhotoParser"``,
        ``"HashtagParser"``, ``"UnknownParser"``.

    Examples:
        >>> get_parser_from_fb_url("https://www.facebook.com/reel/816043001524221")
        'ReelParser'
        >>> get_parser_from_fb_url("https://www.facebook.com/photo.php?fbid=861685686929188")
        'PhotoParser'
        >>> get_parser_from_fb_url("https://www.facebook.com/permalink.php?story_fbid=pfbid035&id=123")
        'PostParser'
        >>> get_parser_from_fb_url("https://www.facebook.com/permalink.php?id=61576849942202")
        'PostParser'
        >>> get_parser_from_fb_url("https://www.facebook.com/groups/12345")
        'GroupParser'
        >>> get_parser_from_fb_url("https://www.facebook.com/zurdobo7")
        'ProfileParser'
        >>> get_parser_from_fb_url(
        ...     "https://www.facebook.com/NoticiasTelemundo/posts/"
        ...     "-liveblog-l-la-casa-blanca/1532119218279349/"
        ... )
        'PostParser'
    """
    path, query = _normalize_url(url)

    # ── Regla 1: Hashtag ──────────────────────────────────────────────────────
    if re.match(r"^/hashtag/", path, re.I):
        return "HashtagParser"

    # ── Regla 2: Reel directo (/reel/<id>) ───────────────────────────────────
    if re.match(r"^/reel/\d+", path, re.I):
        return "ReelParser"

    # ── Regla 3: Vídeo nativo ────────────────────────────────────────────────
    # Cubre:
    #   /<user>/videos/<id>/
    #   /<user>/videos/<slug>/<id>/     ← slug opcional entre user e id
    #   ?v=<id>
    if (
        re.search(r"/videos/(?:[^/]+/)*\d+", path, re.I)
        or re.search(r"(?:^|&)v=\d+", query)
    ):
        return "VideoParser"

    # ── Regla 4: Foto individual (photo.php o /photo/) ───────────────────────
    # photo.php?fbid=<id>                → visor clásico
    # /photo/?fbid=<id>                  → variante moderna
    # /<user>/photos/<id>/               → foto en álbum (sin slug)
    # /<user>/photos/<slug>/<id>/        → foto en álbum (con slug) ← FIX
    if re.search(r"/photo(?:s|\.php)?/?", path, re.I) and (
        re.search(r"(?:^|&)fbid=\d+", query)
        or re.search(r"/photos?/(?:[^/]+/)?\d+", path, re.I)  # ← Regex actualizado
    ):
        return "PhotoParser"

    # ── Regla 5: Post en grupo ────────────────────────────────────────────────
    if re.match(r"^/groups/[^/]+/(?:posts|permalink)/", path, re.I):
        return "PostParser"

    # ── Regla 6: Grupo raíz ───────────────────────────────────────────────────
    if re.match(r"^/groups/", path, re.I):
        return "GroupParser"

    # ── Regla 7: Post directo ────────────────────────────────────────────────
    # Cubre tres variantes:
    #   /<page>/posts/<id_numérico>/
    #   /<page>/posts/pfbid<alfanumérico>/
    #   /<page>/posts/<slug_textual>/<id_numérico>/   ← AÑADIDO
    #
    # CORRECCIÓN: la tercera alternativa ([^/]+/\d+) resuelve el caso donde
    # Facebook incluye un slug descriptivo entre /posts/ y el ID numérico,
    # p.ej. /NoticiasTelemundo/posts/-liveblog-.../1532119218279349/
    if re.search(r"/posts/(?:\d+|pfbid[A-Za-z0-9]+|[^/]+/\d+)", path, re.I):
        return "PostParser"

    # ── Regla 8: Perfil numérico (profile.php?id=<id>) ───────────────────────
    if re.search(r"/profile\.php", path, re.I) and re.search(r"(?:^|&)id=\d+", query):
        return "ProfileParser"

    # ── Regla 9: permalink.php o story.php ───────────────────────────────────
    # A) ?story_fbid=<id>  → post identificado por story_fbid
    # B) ?id=<id> sin story_fbid → permalink genérico
    if re.search(r"/(?:story|permalink)\.php", path, re.I):
        if re.search(r"story_fbid=", query):
            return "PostParser"
        if re.search(r"(?:^|&)id=\d+", query):
            return "PostParser"

    # ── Reglas 10–13: Shortlinks share/* ─────────────────────────────────────
    if re.match(r"^/share/r/", path, re.I):
        return "ReelParser"
    if re.match(r"^/share/p/", path, re.I):
        return "PostParser"
    if re.match(r"^/share/v/", path, re.I):
        return "ReelParser"
    if re.match(r"^/share/", path, re.I):
        return "PostParser"

    # ── Regla 14: Perfil /people/<nombre>/<id_numérico>/ ─────────────────────
    if re.match(r"^/people/[^/]+/\d+/?$", path, re.I):
        return "ProfileParser"

    # ── Regla 15: Vanity URL (/<slug>) ───────────────────────────────────────
    # Captura cualquier /<slug> que no haya sido reconocido antes.
    # IMPORTANTE: photo.php y video.php están en _RESERVED para que
    # no lleguen aquí (ver corrección en la definición de _RESERVED).
    m = re.match(r"^/([A-Za-z0-9._-]+)/?$", path)
    if m and m.group(1).lower() not in _RESERVED and not m.group(1).isdigit():
        return "ProfileParser"

    return "UnknownParser"


# ===========================================================================
# UTILIDADES INTERNAS
# ===========================================================================

def _normalize_url(url: str) -> tuple[str, str]:
    """Normaliza una URL de Facebook y devuelve ``(path, query_string)``."""
    if not url:
        return "/", ""

    url = url.strip().strip('"').strip("\u201c\u201d'").rstrip(")*")
    url = re.sub(r"^http://",             "https://",                   url, flags=re.I)
    url = re.sub(r"^https://m\.facebook", "https://www.facebook",       url, flags=re.I)
    url = re.sub(r"^https://fb\.com/",    "https://www.facebook.com/",  url, flags=re.I)

    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        clean = {
            k: v for k, v in qs.items()
            if not any(k.startswith(t.rstrip("_")) for t in _TRACKING)
        }
        return parsed.path or "/", urlencode(clean, doseq=True)
    except Exception:
        return "/", ""
    
#print (get_parser_from_fb_url("https://www.facebook.com/profile.php?id=100079458018484"))