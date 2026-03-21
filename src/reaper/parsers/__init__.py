"""
reaper/parsers/__init__.py
==========================
Re-exportaciones del paquete de parsers.

Permite importar cualquier parser con una sola línea::

    from reaper.parsers import PostParser, IgReelParser

Jerarquía de herencia::

    BaseParser  (abstracto — utilidades DFS, safe_get, JSON blocks)
    └── FacebookContentParser  (métodos compartidos para Facebook)
        ├── PostParser          → posts regulares (facebook.com/posts/<id>)
        ├── ReelParser          → reels (facebook.com/reel/<id>)
        ├── VideoParser         → vídeos nativos (facebook.com/videos/<id>)
        ├── GroupParser         → grupos (facebook.com/groups/<id>)
        ├── ProfileParser       → perfiles (facebook.com/<username>)
        └── PhotoParser         → fotos individuales (facebook.com/photo/)
    BaseParser
    ├── IgPostParser            → posts de Instagram (/p/<code>)
    └── IgReelParser            → reels de Instagram (/reel/<code>)
"""

from reaper.parsers.base_parser import BaseParser
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.parsers.facebook.post_parser import PostParser
from reaper.parsers.facebook.reel_parser import ReelParser
from reaper.parsers.facebook.video_parser import VideoParser
from reaper.parsers.facebook.group_parser import GroupParser
from reaper.parsers.facebook.profile_parser import ProfileParser
from reaper.parsers.facebook.photo_parser import PhotoParser
from reaper.parsers.instagram.ig_post_parser import IgPostParser
from reaper.parsers.instagram.ig_reel_parser import IgReelParser

__all__ = [
    "BaseParser",
    "FacebookContentParser",
    "PostParser",
    "ReelParser",
    "VideoParser",
    "GroupParser",
    "ProfileParser",
    "PhotoParser",
    "IgPostParser",
    "IgReelParser",
]