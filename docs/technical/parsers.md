# Parsers

## Jerarquía de herencia

```
BaseParser
└── FacebookContentParser
    ├── PostParser
    ├── ReelParser
    ├── VideoParser
    ├── GroupParser
    ├── ProfileParser
    └── PhotoParser
IgPostParser   (hereda de BaseParser)
IgReelParser   (hereda de BaseParser)
```

## `BaseParser`

::: reaper.parsers.base_parser.BaseParser

---

## Ciclo de vida de un parser

Todos los parsers concretos siguen el mismo flujo interno en `parse()`:

```python
def parse(self) -> dict[str, Any]:
    self._blocks = self._extract_json_blocks()  # <script type="application/json">

    if not self._locate_node():                 # DFS buscando el nodo raíz
        self.result["error"] = "Nodo no encontrado"
        return self.result

    self.result["raw_data_available"] = True

    self._extract_basic_info()    # id, timestamp, permalink
    self._extract_author()        # actor / actors[0]
    self._extract_content()       # texto, hashtags, menciones
    self._extract_attachments()   # fotos, vídeos adjuntos
    self._extract_feedback()      # reacciones, shares, comentarios
    self._parse_traffic()         # enriquecimiento desde GraphQL

    return self.result
```

## Estrategias de localización por parser

| Parser | Nodo buscado | Condición DFS |
|---|---|---|
| `PostParser` | Story | `__isFeedUnit == "Story"` |
| `ReelParser` | creation_story | `short_form_video_context` o `post_id` |
| `VideoParser` | Nodo vídeo | `__typename == "Video"` con id numérico |
| `GroupParser` | Nodo grupo | `profile_header_renderer` o `__bbox.result.data.group` |
| `ProfileParser` | Nodo perfil | `__typename` en `User/UserProfile/Page` |
| `PhotoParser` | currMedia | `__typename == "Photo"` + `image` dict + `creation_story` |
| `IgPostParser` | Post IG | `xdt_api__v1__media__shortcode__web_info` |
| `IgReelParser` | Reel IG | `xdt_api__v1__clips__clips_on_logged_out_connection_v2` |

## Usar parsers offline con artefactos de debug

```python
from reaper.network.content_fetcher import load_debug_session
from reaper.parsers import ReelParser

sesion = load_debug_session("data/debug_artifacts/sesion_guardada/")

result = ReelParser(
    html_content=sesion.html_content,
    final_url=sesion.final_url,
    original_url=sesion.original_url,
    traffic=sesion.traffic,
    debug=True,
).parse()
```

## Añadir un parser nuevo

1. Crear `src/reaper/parsers/facebook/event_parser.py` heredando de
   `FacebookContentParser`.
2. Registrar en `src/reaper/parsers/__init__.py`.
3. Añadir a `_PRIMARY_PARSER_MAP` en `src/reaper/scrapers/facebook.py`.
4. Añadir la regla de URL en `src/reaper/utils/fb_url_classifier.py`.

```python
# event_parser.py — plantilla mínima
from reaper.parsers.facebook.facebook_parser import FacebookContentParser

class EventParser(FacebookContentParser):
    def __init__(self, html_content, final_url, original_url,
                 traffic=None, debug=False):
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self.result.update({"__typename": "facebook_event"})

    def parse(self) -> dict:
        self._blocks = self._extract_json_blocks()
        if not self._locate_event_node():
            self.result["error"] = "Nodo de evento no encontrado"
            return self.result
        self.result["raw_data_available"] = True
        self._extract_event_info()
        self._parse_facebook_traffic(["EventQuery"])
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
        self.result["id"]       = node.get("id", "")
        self.result["name"]     = node.get("name", "")
        self.result["start_at"] = self._parse_timestamp(node.get("start_timestamp"))
```
