"""
utils/platforms.py
Detección de plataforma de redes sociales a partir de una URL.

Proporciona un catálogo centralizado de dominios por plataforma y una
función de clasificación precisa que compara el netloc de la URL (no la
URL completa) para evitar falsos positivos por substring.

Constantes públicas::

    PLATFORM_DOMAINS   → dict {plataforma: (dominios,)}
    FACEBOOK_DOMAINS   → alias de PLATFORM_DOMAINS["facebook"]
    INSTAGRAM_DOMAINS  → alias de PLATFORM_DOMAINS["instagram"]

Función pública::

    detect_platform_from_url(url) → nombre de plataforma o "unknown"

Integración::

    from utils import detect_platform_from_url, PLATFORM_DOMAINS
    from utils.platforms import PLATFORM_DOMAINS

Python: 3.11+
"""
from urllib.parse import urlparse


# ===========================================================================
# CATÁLOGO DE DOMINIOS POR PLATAFORMA
# ===========================================================================

# La clave es el identificador interno de la plataforma (minúsculas).
# El valor es una tupla de dominios canónicos. La comparación usa
# ``netloc == dominio OR netloc.endswith('.' + dominio)``, por lo que:
#   - "facebook.com" cubre www.facebook.com, m.facebook.com, etc.
#   - "fb.com"       se lista por separado porque es un dominio distinto.
#   - "t.co"         solo coincide con t.co (nunca con snapchat.com).
#
# Orden: de mayor a menor volumen de uso esperado en el proyecto.
# Las plataformas más frecuentes se evalúan primero.

PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    # ── Meta ──────────────────────────────────────────────────────────────
    "facebook": (
        "facebook.com",
        "fb.com",
        "fb.watch",
    ),
    "instagram": (
        "instagram.com",
        "instagr.am",
    ),
    "threads": (
        "threads.net",
        "threads.com",
    ),
    # ── Google / Alphabet ─────────────────────────────────────────────────
    "youtube": (
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
    ),
    # ── X (antes Twitter) ─────────────────────────────────────────────────
    "x": (
        "x.com",
        "twitter.com",
        "t.co",           # shortlinks de X/Twitter — netloc exacto
    ),
    # ── TikTok / ByteDance ────────────────────────────────────────────────
    "tiktok": (
        "tiktok.com",
    ),
    # ── LinkedIn (Microsoft) ──────────────────────────────────────────────
    "linkedin": (
        "linkedin.com",
        "lnkd.in",
    ),
    # ── Telegram ──────────────────────────────────────────────────────────
    "telegram": (
        "t.me",           # shortlinks — netloc exacto
        "telegram.me",
        "telegram.org",
    ),
    # ── WhatsApp (Meta) ───────────────────────────────────────────────────
    "whatsapp": (
        "whatsapp.com",
        "wa.me",          # shortlinks — netloc exacto
    ),
    # ── Snap ──────────────────────────────────────────────────────────────
    "snapchat": (
        "snapchat.com",
        "snap.com",
    ),
    # ── Pinterest ─────────────────────────────────────────────────────────
    "pinterest": (
        "pinterest.com",
        "pin.it",         # shortlinks — netloc exacto
    ),
    # ── Reddit ────────────────────────────────────────────────────────────
    "reddit": (
        "reddit.com",
        "redd.it",        # shortlinks — netloc exacto
        "old.reddit.com",
    ),
    # ── Tumblr (Automattic) ───────────────────────────────────────────────
    "tumblr": (
        "tumblr.com",
    ),
    # ── Twitch ────────────────────────────────────────────────────────────
    "twitch": (
        "twitch.tv",
    ),
    # ── Vimeo ─────────────────────────────────────────────────────────────
    "vimeo": (
        "vimeo.com",
        "player.vimeo.com",
    ),
    # ── Mastodon (fediverso) ──────────────────────────────────────────────
    "mastodon": (
        "mastodon.social",
        "mastodon.online",
        "fosstodon.org",
        "hachyderm.io",
    ),
    # ── Bluesky ───────────────────────────────────────────────────────────
    "bluesky": (
        "bsky.app",
        "bsky.social",
    ),
    # ── Clubhouse ─────────────────────────────────────────────────────────
    "clubhouse": (
        "clubhouse.com",
        "joinclubhouse.com",
    ),
    # ── Discord ───────────────────────────────────────────────────────────
    "discord": (
        "discord.com",
        "discord.gg",
        "discordapp.com",
    ),
    # ── Substack ──────────────────────────────────────────────────────────
    "substack": (
        "substack.com",
    ),
    # ── Medium ────────────────────────────────────────────────────────────
    "medium": (
        "medium.com",
    ),
    # ── Quora ─────────────────────────────────────────────────────────────
    "quora": (
        "quora.com",
    ),
    # ── Dailymotion ───────────────────────────────────────────────────────
    "dailymotion": (
        "dailymotion.com",
        "dai.ly",
    ),
    # ── Odysee / LBRY ─────────────────────────────────────────────────────
    "odysee": (
        "odysee.com",
        "lbry.tv",
    ),
    # ── VK (VKontakte) ────────────────────────────────────────────────────
    "vk": (
        "vk.com",
        "vkontakte.ru",
    ),
    # ── Weibo ─────────────────────────────────────────────────────────────
    "weibo": (
        "weibo.com",
        "weibo.cn",
    ),
    # ── Kwai ──────────────────────────────────────────────────────────────
    "kwai": (
        "kwai.com",
        "kwai-video.com",
    ),
}

# Aliases de conveniencia para compatibilidad con código existente
FACEBOOK_DOMAINS: tuple[str, ...] = PLATFORM_DOMAINS["facebook"]
INSTAGRAM_DOMAINS: tuple[str, ...] = PLATFORM_DOMAINS["instagram"]


# ===========================================================================
# UTILIDAD INTERNA
# ===========================================================================

def _netloc_matches(netloc: str, domain: str) -> bool:
    """Comprueba si un netloc pertenece al dominio dado.

    Acepta el dominio exacto y cualquier subdominio de él.
    Ejemplo: ``_netloc_matches("www.facebook.com", "facebook.com")`` → True
             ``_netloc_matches("www.snapchat.com", "t.co")``         → False

    Args:
        netloc: Netloc de la URL en minúsculas (ej. ``"www.facebook.com"``).
        domain: Dominio canónico a comprobar (ej. ``"facebook.com"``).

    Returns:
        ``True`` si ``netloc`` es exactamente ``domain`` o termina con
        ``"." + domain``.
    """
    return netloc == domain or netloc.endswith("." + domain)


# ===========================================================================
# API PÚBLICA
# ===========================================================================

def detect_platform_from_url(url: str) -> str:
    """Detecta la plataforma de redes sociales a partir de la URL.

    Extrae el netloc de la URL y lo compara contra el catálogo de dominios
    ``PLATFORM_DOMAINS``. La comparación usa ``netloc == dominio`` o
    ``netloc.endswith('.' + dominio)``, lo que evita falsos positivos por
    substring (p. ej. ``"t.co"`` no coincide con ``"snapchat.com"``).

    Args:
        url: URL completa o parcial con dominio. Se acepta con o sin
             protocolo.

    Returns:
        Identificador de la plataforma en minúsculas (ej. ``"facebook"``,
        ``"youtube"``, ``"x"``), o ``"unknown"`` si el dominio no coincide
        con ninguna entrada del catálogo.

    Examples:
        >>> detect_platform_from_url("https://www.facebook.com/reel/12345")
        'facebook'
        >>> detect_platform_from_url("https://youtu.be/dQw4w9WgXcQ")
        'youtube'
        >>> detect_platform_from_url("https://twitter.com/user/status/123")
        'x'
        >>> detect_platform_from_url("https://t.co/abc123")
        'x'
        >>> detect_platform_from_url("https://vm.tiktok.com/abc123/")
        'tiktok'
        >>> detect_platform_from_url("https://t.me/channel/123")
        'telegram'
        >>> detect_platform_from_url("https://wa.me/+34612345678")
        'whatsapp'
        >>> detect_platform_from_url("https://www.snapchat.com/add/user")
        'snapchat'
        >>> detect_platform_from_url("https://bsky.app/profile/user")
        'bluesky'
        >>> detect_platform_from_url("https://discord.gg/abc123")
        'discord'
        >>> detect_platform_from_url("https://www.google.com")
        'unknown'
        >>> detect_platform_from_url("")
        'unknown'
    """
    if not url or not isinstance(url, str):
        return "unknown"

    try:
        netloc = urlparse(url.strip()).netloc.lower()
    except Exception:
        return "unknown"

    if not netloc:
        return "unknown"

    for platform, domains in PLATFORM_DOMAINS.items():
        if any(_netloc_matches(netloc, domain) for domain in domains):
            return platform

    return "unknown"