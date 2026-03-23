from reaper.utils.media import srt_to_dict
from reaper.utils.fb_url_classifier import get_parser_from_fb_url
from reaper.utils.fb_auth_detector import requires_auth
from reaper.utils.metrics import SocialMediaParser
from reaper.utils.encoding import datetime_encoder, decode_alphanumeric_id
from reaper.utils.http import get_text_from_url
from reaper.utils.logger import get_logger, setup_logging, DebugContext

from reaper.utils.platforms import (
    FACEBOOK_DOMAINS,
    INSTAGRAM_DOMAINS,
    PLATFORM_DOMAINS,
    detect_platform_from_url
)
__all__ = [
    # Clasificador de URLs de Facebook
    "get_parser_from_fb_url",
    # Helpers de Facebook
    "extraer_id_video_facebook",
    "generate_fb_recent_search_url",
    "is_facebook_video_url",
    "requires_auth",
    "normalizar_url_facebook",
    # Detección de plataforma (multi-red)
    "PLATFORM_DOMAINS",
    "FACEBOOK_DOMAINS",
    "INSTAGRAM_DOMAINS",
    "detect_platform_from_url",
    # Encoding
    "datetime_encoder",
    "decode_alphanumeric_id",
    # HTTP
    "get_text_from_url",
    # Media
    "srt_to_dict",
    # Métricas
    "SocialMediaParser",
    # Logging
    "get_logger",
    "setup_logging",
    "DebugContext",
]