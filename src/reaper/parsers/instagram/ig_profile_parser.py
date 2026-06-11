"""
parsers/ig_profile_parser.py

Parser para páginas de perfil de Instagram (``instagram.com/<username>/``).

Arquitectura de datos (2025-06):
    Instagram renderiza la página de perfil como shell SSR parcial.
    Los datos NO están en bloques JSON Relay pre-hidratados (a diferencia
    de los posts y reels). La información se distribuye en tres fuentes:

    1. **Meta tags** (og:title, og:description, description):
       - full_name, username, follower_count, following_count,
         posts_count (como fallback).
       - biography (en el meta ``description``).

    2. **HTML renderizado** (``<section>`` con el header del perfil):
       - full_name, follower_count, following_count, posts_count
         (más actualizado que meta tags).
       - biography, category (ej. "Digital creator").
       - profile_pic_url (``<img alt="<username>'s profile picture">``).
       - is_verified (presencia de SVG con aria-label "Verified").

    3. **JSON module** (``ScheduledServerJS`` → ``PolarisProfileRoot``):
       - user_id numérico extraído del ``props.id`` del componente.

    4. **HTML del ``<article>``** (grid de posts):
       - Lista de posts/reels: code, tipo, thumbnail_url, caption/texto,
         posted_at (decodificado desde el shortcode vía epoch offset IG).

Notas:
    - ``following_count``: el HTML renderizado tiene el valor más reciente;
      los meta tags pueden estar desactualizados (cache CDN). Se prefiere
      el HTML.
    - ``posted_at`` en feed: calculado desde el shortcode (no desde taken_at
      que no está disponible en el HTML del perfil).
      Fórmula: ``pk = decode_shortcode(code)``,
      ``ts_ms = (pk >> 23) + 1_314_220_021_721``.
    - ``pk`` de cada post: derivado del shortcode con la misma fórmula.
    - ``like_count`` / ``comment_count``: NO disponibles en la página de
      perfil (requieren sesión autenticada o llamada individual a cada post).

Python: 3.11+
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from reaper.parsers.base_parser import BaseParser
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# Epoch offset de Instagram para Snowflake-like IDs
_IG_EPOCH_MS: int = 1_314_220_021_721
# Alfabeto de Instagram para decodificar shortcodes
_IG_ALPHABET: str = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class IgProfileParser(BaseParser):
    """
    Parser para páginas de perfil de Instagram.

    Extrae el perfil completo del usuario y todos los posts/reels
    visibles en el grid de la página (normalmente los 12 más recientes).

    Attributes:
        result: Diccionario acumulador con todos los datos extraídos.
    """

    def __init__(
        self,
        html_content: str,
        final_url: str,
        original_url: str,
        debug: bool = False,
    ) -> None:
        """
        Inicializa el parser de perfiles de Instagram.

        Args:
            html_content: HTML completo renderizado de la página de perfil.
            final_url:    URL final de la página (tras posibles redirecciones).
            original_url: URL original solicitada por el usuario.
            debug:        Si True, activa logs de diagnóstico detallados.
        """
        super().__init__(
            html_content,
            final_url,
            original_url=original_url,
            debug=debug,
            platform="instagram",
        )
        self.result.update({
            # ── Datos del usuario ────────────────────────────────────────
            "__typename": "instagram_profile",
            "user_id":        None,   # pk numérico del usuario
            "username":       None,
            "full_name":      None,
            "biography":      None,
            "category":       None,   # "Digital creator", "Public figure", etc.
            "profile_pic_url": None,
            "is_verified":    False,
            "is_private":     False,
            "follower_count": 0,
            "following_count": 0,
            "posts_count":    0,
            "website":        None,
            # ── Grid de posts ────────────────────────────────────────────
            "feed": [],
        })

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def parse(self) -> dict[str, Any]:
        """
        Ejecuta el pipeline completo de extracción para un perfil de Instagram.

        Returns:
            dict[str, Any] con la siguiente estructura::

                {
                    "platform":       "instagram",
                    "status":         "ok" | "error",
                    "error":          str | None,
                    "raw_data_available": bool,
                    "scraped_at":     datetime,
                    "final_url":      str,
                    "post_url":       str,

                    # Datos del usuario
                    "user_id":        str | None,
                    "username":       str | None,
                    "full_name":      str | None,
                    "biography":      str | None,
                    "category":       str | None,
                    "profile_pic_url": str | None,
                    "is_verified":    bool,
                    "is_private":     bool,
                    "follower_count": int,
                    "following_count": int,
                    "posts_count":    int,
                    "website":        str | None,

                    # Posts del grid (los ~12 más recientes)
                    "feed": [
                        {
                            "post_user_id": str,    # "{user_id}_{pk}"
                            "id":           str,    # pk numérico (decodificado del code)
                            "code":         str,    # shortcode IG (ej. "DZcvnWbDNG5")
                            "permalink_url": str,
                            "posted_at":    datetime | None,
                            "media_type":   "Photo" | "Reel" | "unknown",
                            "thumbnail":    str | None,
                            "text":         str | None,  # alt del thumbnail (caption completo)
                            "like_count":   0,    # no disponible en la página de perfil
                            "comment_count": 0,   # ídem
                        },
                        ...
                    ]
                }
        """
        logger.info(
            "Iniciando extracción de PERFIL de Instagram | url=%s", self.final_url
        )

        soup = BeautifulSoup(self.html_content, "html.parser")

        # ── Paso 1: Datos del usuario ────────────────────────────────────
        self._extract_user_from_meta(soup)
        self._extract_user_from_html(soup)
        self._extract_user_id_from_json(soup)

        # Verificar que al menos tenemos el username
        if not self.result.get("username"):
            self.result["error"] = (
                "No se pudo extraer el username del perfil. "
                "El HTML puede no ser una página de perfil de Instagram válida."
            )
            logger.warning(
                "IgProfileParser: %s | url=%s", self.result["error"], self.final_url
            )
            return self.result

        self.result["raw_data_available"] = True

        # ── Paso 2: Grid de posts ────────────────────────────────────────
        self._extract_feed_from_html(soup)

        logger.debug(
            "Perfil de Instagram parseado | user=%s, feed_items=%d | url=%s",
            self.result["username"],
            len(self.result["feed"]),
            self.final_url,
        )
        return self.result

    # ------------------------------------------------------------------
    # Extracción de datos del usuario
    # ------------------------------------------------------------------

    def _extract_user_from_meta(self, soup: BeautifulSoup) -> None:
        """
        Extrae datos del usuario desde los meta tags de la página.

        Parsea ``og:title``, ``og:description`` y ``description``.
        Sirve como fuente inicial, después enriquecida por el HTML renderizado.

        Args:
            soup: BeautifulSoup del HTML completo.
        """
        # og:title → "Full Name (@username) • Instagram photos and videos"
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title:
            title_content = og_title.get("content", "")
            title_match = re.match(r"^(.+?)\s*\(@([^)]+)\)", title_content)
            if title_match:
                self.result["full_name"] = title_match.group(1).strip()
                self.result["username"] = title_match.group(2).strip()

        # og:url como fallback para username: "https://www.instagram.com/<username>"
        if not self.result.get("username"):
            og_url = soup.find("meta", attrs={"property": "og:url"})
            if og_url:
                url_match = re.search(
                    r"instagram\.com/([A-Za-z0-9_.]+)/?$",
                    og_url.get("content", ""),
                )
                if url_match:
                    self.result["username"] = url_match.group(1)

        # og:image → profile_pic_url (fallback; el HTML tiene la URL completa)
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image:
            self.result["profile_pic_url"] = og_image.get("content", "") or None

        # description → "N Followers, M Following, P Posts - Name (@user) on Instagram: "bio""
        desc_meta = soup.find("meta", attrs={"name": "description"})
        if desc_meta:
            desc = desc_meta.get("content", "")

            # Contadores
            counts_match = re.match(
                r"([\d,]+)\s+Followers?,\s*([\d,]+)\s+Following,\s*([\d,]+)\s+Posts?",
                desc,
            )
            if counts_match:
                self.result["follower_count"] = int(counts_match.group(1).replace(",", ""))
                self.result["following_count"] = int(counts_match.group(2).replace(",", ""))
                self.result["posts_count"] = int(counts_match.group(3).replace(",", ""))

            # Biografía: texto entre comillas al final
            bio_match = re.search(r'on Instagram:\s*["""](.+?)["""]?\s*$', desc, re.DOTALL)
            if bio_match:
                self.result["biography"] = bio_match.group(1).strip()

        logger.debug(
            "Meta: username=%s, full_name=%s, followers=%d",
            self.result.get("username"),
            self.result.get("full_name"),
            self.result.get("follower_count", 0),
        )

    def _extract_user_from_html(self, soup: BeautifulSoup) -> None:
        """
        Enriquece los datos del usuario desde el HTML renderizado del perfil.

        El HTML renderizado tiene los contadores más actualizados (sin cache
        de CDN), la bio completa, la foto de perfil en alta calidad,
        la categoría y el badge de verificación.

        Busca el ``<section>`` que contiene el header del perfil identificándolo
        por la presencia del username y la palabra "followers".

        Args:
            soup: BeautifulSoup del HTML completo.
        """
        username = self.result.get("username", "")
        profile_section = self._find_profile_section(soup, username)
        if not profile_section:
            logger.debug("_extract_user_from_html: profile section not found")
            return

        # ── Foto de perfil ───────────────────────────────────────────────
        profile_img = profile_section.find(
            "img",
            alt=re.compile(rf"^{re.escape(username)}'s profile picture$", re.I),
        )
        if profile_img and profile_img.get("src"):
            self.result["profile_pic_url"] = profile_img["src"]

        # ── Contadores (más recientes que meta tags) ─────────────────────
        for stat_text, field in [
            ("posts", "posts_count"),
            ("followers", "follower_count"),
            ("following", "following_count"),
        ]:
            # Buscar span que contenga "N posts" / "N followers" / "N following"
            stat_span = profile_section.find(
                "span", string=re.compile(rf"[\d,]+\s*{stat_text}", re.I)
            )
            if not stat_span:
                # Fallback: span con title numérico cerca de texto con stat_text
                for span in profile_section.find_all("span", title=True):
                    title_val = span.get("title", "").replace(",", "")
                    if title_val.isdigit():
                        parent_text = span.parent.get_text(strip=True) if span.parent else ""
                        if stat_text in parent_text.lower():
                            self.result[field] = int(title_val)
                            break
                continue
            # Extraer número del texto
            num_match = re.search(r"([\d,]+)", stat_span.get_text())
            if num_match:
                self.result[field] = int(num_match.group(1).replace(",", ""))

        # ── Nombre completo ──────────────────────────────────────────────
        # Buscar span con el nombre completo (suele estar en un span sin clases
        # con el nombre visible cerca del username)
        for span in profile_section.find_all("span"):
            txt = span.get_text(strip=True)
            full_name_candidate = self.result.get("full_name", "")
            if (
                txt
                and txt == full_name_candidate
                and not txt.startswith("x1")  # excluir clases CSS obfuscadas
                and len(txt) > 2
            ):
                self.result["full_name"] = txt
                break

        # ── Biografía ────────────────────────────────────────────────────
        bio_candidate = self._extract_biography(profile_section, username)
        if bio_candidate:
            self.result["biography"] = bio_candidate

        # ── Categoría ────────────────────────────────────────────────────
        category_keywords = [
            "Digital creator", "Public figure", "Journalist", "Media",
            "News", "Photographer", "Artist", "Musician", "Athlete",
            "Actor", "Blogger", "Business", "Brand", "Organization",
            "Government", "Community", "Education", "Entertainment",
            "Food", "Health", "Beauty", "Fashion", "Sports", "Travel",
        ]
        section_text = profile_section.get_text()
        for kw in category_keywords:
            if kw.lower() in section_text.lower():
                self.result["category"] = kw
                break

        # ── is_verified ──────────────────────────────────────────────────
        verified_svg = profile_section.find(
            "svg",
            attrs={"aria-label": re.compile(r"verif", re.I)},
        )
        if not verified_svg:
            verified_svg = profile_section.find(
                "span",
                attrs={"aria-label": re.compile(r"verif", re.I)},
            )
        self.result["is_verified"] = verified_svg is not None

        # ── Website ──────────────────────────────────────────────────────
        excluded_domains = {
            "instagram.com", "meta.com", "meta.ai", "threads.com",
            "facebook.com", "about.meta.com",
        }
        for a in profile_section.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("http") and not any(d in href for d in excluded_domains):
                self.result["website"] = href
                break

        logger.debug(
            "HTML: followers=%d, following=%d, posts=%d, verified=%s",
            self.result.get("follower_count", 0),
            self.result.get("following_count", 0),
            self.result.get("posts_count", 0),
            self.result.get("is_verified"),
        )

    def _extract_user_id_from_json(self, soup: BeautifulSoup) -> None:
        """
        Extrae el ``user_id`` numérico desde los módulos JS embebidos.

        Instagram incluye el ``id`` del perfil como prop del componente
        ``PolarisProfileRoot`` dentro de los bloques ``ScheduledServerJS``.

        Patrón buscado::

            "props": {"id": "10709343875", ...}

        Args:
            soup: BeautifulSoup del HTML completo.
        """
        # Buscar "user_id" directamente en el raw HTML (más rápido)
        raw = self.html_content
        user_id_match = re.search(r'"user_id"\s*:\s*"(\d{8,})"', raw)
        if user_id_match:
            self.result["user_id"] = user_id_match.group(1)
            return

        # Fallback: buscar props.id en bloques JSON
        for tag in soup.find_all("script", {"type": "application/json"}):
            if not tag.string:
                continue
            # Buscar "id":"<N>" donde N es un número largo de usuario
            id_match = re.search(
                r'"props"\s*:\s*\{[^}]*"id"\s*:\s*"(\d{8,})"',
                tag.string,
            )
            if id_match:
                self.result["user_id"] = id_match.group(1)
                return

        logger.debug("_extract_user_id_from_json: user_id not found")

    # ------------------------------------------------------------------
    # Extracción del feed (grid de posts)
    # ------------------------------------------------------------------

    def _extract_feed_from_html(self, soup: BeautifulSoup) -> None:
        """
        Extrae los posts del grid del perfil desde el ``<article>`` del HTML.

        Cada post se identifica por su ``<a href="/username/p/<code>/">``
        o ``<a href="/username/reel/<code>/">``. El thumbnail viene del
        ``<img>`` dentro del link y el texto/caption del atributo ``alt``.

        Args:
            soup: BeautifulSoup del HTML completo.
        """
        username = self.result.get("username", "")
        user_id = self.result.get("user_id")

        profile_section = self._find_profile_section(soup, username)
        if not profile_section:
            logger.debug("_extract_feed_from_html: profile section not found")
            return

        article = profile_section.find("article")
        if not article:
            logger.debug("_extract_feed_from_html: <article> not found in section")
            return

        post_pattern = re.compile(
            rf"^/{re.escape(username)}/(p|reel)/([A-Za-z0-9_-]{{9,12}})/?$"
        )
        seen_codes: set[str] = set()

        for a in article.find_all("a", href=True):
            href = a.get("href", "")
            match = post_pattern.match(href)
            if not match:
                continue

            post_type = match.group(1)   # "p" o "reel"
            code = match.group(2)

            if code in seen_codes:
                continue
            seen_codes.add(code)

            img = a.find("img")
            thumbnail = img.get("src") if img else None
            # El alt del thumbnail contiene la caption completa del post
            caption_text = img.get("alt") if img else None

            pk = self._decode_shortcode(code)
            posted_at = self._pk_to_datetime(pk)
            media_type = "Reel" if post_type == "reel" else "Photo"
            permalink = f"https://www.instagram.com/{username}/{post_type}/{code}/"

            feed_entry: dict[str, Any] = {
                "post_user_id": f"{user_id}_{pk}" if user_id and pk else str(pk or code),
                "id":           str(pk) if pk else None,
                "code":         code,
                "permalink_url": permalink,
                "posted_at":    posted_at,
                "media_type":   media_type,
                "thumbnail":    thumbnail,
                "text":         caption_text,
                "like_count":   0,   # no disponible sin autenticación
                "comment_count": 0,  # ídem
            }
            self.result["feed"].append(feed_entry)

        logger.debug(
            "_extract_feed_from_html: %d posts extraídos", len(self.result["feed"])
        )

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _find_profile_section(
        self, soup: BeautifulSoup, username: str
    ) -> BeautifulSoup | None:
        """
        Localiza el ``<section>`` del header del perfil.

        Identifica el section correcto buscando el que contiene tanto el
        username como la palabra "followers" en su texto.

        Args:
            soup:     BeautifulSoup del HTML completo.
            username: Username del perfil (para validar que es el correcto).

        Returns:
            El ``<section>`` del perfil, o None si no se encuentra.
        """
        for section in soup.find_all("section"):
            text = section.get_text()
            if username and username in text and "followers" in text.lower():
                return section
        return None

    def _extract_biography(
        self, profile_section: BeautifulSoup, username: str
    ) -> str | None:
        """
        Extrae la biografía del usuario desde el HTML del header del perfil.

        La bio es el texto de span que aparece después de los contadores de
        seguidores y que no es el username, nombre completo ni categoría.

        Args:
            profile_section: El ``<section>`` del header.
            username:        Username para excluirlo de candidatos.

        Returns:
            Texto de la biografía, o None si no se encuentra.
        """
        excluded_patterns = re.compile(
            r"^(\d[\d,]*\s*(posts?|followers?|following)|"
            + re.escape(username)
            + r"|Posts|Reels|Tagged|Options|Log In|Sign Up)$",
            re.I,
        )
        full_name = self.result.get("full_name", "")

        # Recopilar spans de texto candidatos a bio
        candidates: list[str] = []
        for span in profile_section.find_all("span"):
            txt = span.get_text(strip=True)
            if (
                txt
                and 10 < len(txt) < 500
                and not excluded_patterns.match(txt)
                and txt != full_name
                and not txt.startswith("x1")   # clases CSS obfuscadas
            ):
                candidates.append(txt)

        # La bio suele aparecer duplicada (versión mobile + desktop)
        # Devolver la primera aparición única
        seen: set[str] = set()
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                return candidate

        return None

    # ------------------------------------------------------------------
    # Utilidades estáticas
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_shortcode(code: str) -> int | None:
        """
        Decodifica un shortcode de Instagram al pk numérico del media.

        Instagram codifica el ``pk`` en base64 con el alfabeto propio:
        ``ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_``

        Args:
            code: Shortcode del post (ej. ``"DZcvnWbDNG5"``).

        Returns:
            El pk numérico, o None si el código contiene caracteres inválidos.
        """
        try:
            result = 0
            for char in code:
                result = result * 64 + _IG_ALPHABET.index(char)
            return result
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _pk_to_datetime(pk: int | None) -> datetime | None:
        """
        Convierte un pk de Instagram a ``datetime`` usando el epoch offset.

        Fórmula verificada contra ``taken_at`` real:
        ``timestamp_ms = (pk >> 23) + 1_314_220_021_721``

        Args:
            pk: pk numérico del media.

        Returns:
            ``datetime`` local, o None si pk es None o inválido.
        """
        if pk is None:
            return None
        try:
            ts_ms = (pk >> 23) + _IG_EPOCH_MS
            return datetime.fromtimestamp(ts_ms / 1000)
        except (OSError, OverflowError, ValueError):
            return None