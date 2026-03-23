# Extender Reaper

## Añadir un nuevo parser de Facebook

### 1. Crear el archivo del parser

```python
# src/reaper/parsers/facebook/event_parser.py
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.utils.logger import get_logger
from typing import Any
import traceback

logger = get_logger(__name__)


class EventParser(FacebookContentParser):
    """Parser para eventos de Facebook (/events/<id>)."""

    def __init__(self, html_content, final_url, original_url,
                 traffic=None, debug=False):
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self.result.update({"__typename": "facebook_event"})

    def parse(self) -> dict[str, Any]:
        logger.info("Extrayendo EVENTO | url=%s", self.final_url)
        self._blocks = self._extract_json_blocks()

        if not self._locate_event_node():
            self.result["error"] = "Nodo de evento no encontrado"
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_event_info()
            self._parse_facebook_traffic(["EventQuery"])
        except Exception as exc:
            self.result["error"] = str(exc)
            if self.debug:
                traceback.print_exc()

        return self.result

    def _locate_event_node(self) -> bool:
        for block in self._blocks:
            node = self._recursive_search(
                block,
                condition=lambda n: n.get("__typename") == "Event" and "name" in n,
            )
            if node:
                self._event_node = node
                return True
        return False

    def _extract_event_info(self) -> None:
        node = self._event_node
        self.result["id"]        = node.get("id", "")
        self.result["name"]      = node.get("name", "")
        self.result["start_at"]  = self._parse_timestamp(node.get("start_timestamp"))
        self.result["end_at"]    = self._parse_timestamp(node.get("end_timestamp"))
        self.result["cover_url"] = self._safe_get(node, "cover_media", "photo",
                                                   "image", "uri")
```

### 2. Registrar en `parsers/__init__.py`

```python
from reaper.parsers.facebook.event_parser import EventParser
__all__ = [..., "EventParser"]
```

### 3. Añadir al mapa de parsers en `scrapers/facebook.py`

```python
_PRIMARY_PARSER_MAP: dict[str, type] = {
    ...,
    "EventParser": EventParser,
}
```

### 4. Añadir la regla de URL en `utils/fb_url_classifier.py`

```python
# Dentro de get_parser_from_fb_url(), antes de la regla del perfil vanity:
if re.match(r"^/events/\d+", path, re.I):
    return "EventParser"
```

---

## Añadir una nueva plataforma

### 1. Registrar el dominio en `utils/platforms.py`

```python
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "facebook":  ("facebook.com", "fb.com", "fb.watch"),
    "instagram": ("instagram.com", "instagr.am"),
    "tiktok":    ("tiktok.com", "www.tiktok.com"),   # nuevo
}
```

### 2. Crear el scraper en `scrapers/tiktok.py`

```python
from datetime import datetime
from typing import Any
from reaper.network.content_fetcher import ContentFetcher
from reaper.scrapers.base import BaseScraper
from reaper.utils.logger import get_logger

logger = get_logger(__name__)


class TiktokScraper(BaseScraper):

    async def run(self) -> dict[str, Any]:
        fetch_result = await self._fetch_with_account("tiktok")
        if not fetch_result.success:
            return self._error_result(fetch_result.error or "Fetch fallido")
        # ... lógica de parseo
        return self._base_result("tiktok")

    async def _fetch(self, override_url=None, cookies=None):
        fetcher = ContentFetcher(
            url=override_url or self.config.url,
            headless=self.config.headless,
            debug=self.config.debug,
        )
        return await fetcher.fetch(
            screenshot=self.config.screenshot,
            auto_scroll=self.config.auto_scroll,
            proxy_server=self.config.proxy_server,
            proxy_username=self.config.proxy_username,
            proxy_password=self.config.proxy_password,
            cookies=cookies,
        )

    def _error_result(self, error_msg: str) -> dict[str, Any]:
        return {
            **self._base_result("tiktok"),
            "final_url": self.config.url,
            "scraped_at": datetime.now(),
            "error": error_msg,
            "raw_data_available": False,
            "status": "error",
        }
```

### 3. Registrar en `core.py`

```python
match platform:
    case "facebook":  ...
    case "instagram": ...
    case "tiktok":
        from reaper.scrapers.tiktok import TiktokScraper
        scraper = TiktokScraper(config)
    case _:
        raise UnsupportedPlatformError(...)
```

---

## Implementar un backend de almacenamiento propio

Ver [Migración a base de datos](auth.md#sistema-de-almacenamiento)
en la documentación técnica del sistema de autenticación.

---

## Añadir soporte de cuentas para una nueva plataforma

Cuando se añade una nueva plataforma que también requiere autenticación,
basta con añadir su nombre a `SUPPORTED_PLATFORMS` en `auth/models.py`:

```python
SUPPORTED_PLATFORMS: frozenset[str] = frozenset({
    "facebook",
    "instagram",
    "tiktok",     # nuevo
})
```

Y llamar a `_fetch_with_account("tiktok")` en el nuevo scraper.
El `AccountManager` y el rotador funcionan con cualquier nombre de plataforma
que esté en ese conjunto.
