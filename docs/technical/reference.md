# Reaper — Referencia Técnica para Desarrolladores

> **Versión**: 0.1.0 | **Python**: 3.11+ | **Licencia**: MIT

---

## Tabla de Contenidos

1. [Visión General de la Arquitectura](#1-visión-general-de-la-arquitectura)
2. [Entorno y Requisitos](#2-entorno-y-requisitos)
3. [Instalación y Configuración del Entorno de Desarrollo](#3-instalación-y-configuración-del-entorno-de-desarrollo)
4. [Estructura del Proyecto](#4-estructura-del-proyecto)
5. [Flujo de Trabajo Completo](#5-flujo-de-trabajo-completo)
6. [Módulo `config` — ScraperConfig](#6-módulo-config--scraperconfig)
7. [Módulo `core` — Reaper y Excepciones](#7-módulo-core--reaper-y-excepciones)
8. [Módulo `network` — Fetch e Interceptor](#8-módulo-network--fetch-e-interceptor)
9. [Módulo `scrapers` — Lógica de Orquestación](#9-módulo-scrapers--lógica-de-orquestación)
10. [Módulo `parsers` — Extracción de Datos](#10-módulo-parsers--extracción-de-datos)
11. [Módulo `utils` — Utilidades Compartidas](#11-módulo-utils--utilidades-compartidas)
12. [Estructuras de Datos — Resultados por Tipo de Contenido](#12-estructuras-de-datos--resultados-por-tipo-de-contenido)
13. [Sistema de Logging](#13-sistema-de-logging)
14. [Modo Debug y Artefactos](#14-modo-debug-y-artefactos)
15. [Añadir un Nuevo Parser](#15-añadir-un-nuevo-parser)
16. [Añadir una Nueva Plataforma](#16-añadir-una-nueva-plataforma)
17. [Herramientas de Desarrollo](#17-herramientas-de-desarrollo)
18. [Decisiones de Diseño](#18-decisiones-de-diseño)

---

## 1. Visión General de la Arquitectura

Reaper es una librería de scraping asíncrona estructurada en capas. Cada capa tiene una responsabilidad única y estricta:

```
┌─────────────────────────────────────────────────────────┐
│  API pública  reaper/__init__.py                        │
│  scrape()  |  Reaper  |  ScrapingError                  │
├─────────────────────────────────────────────────────────┤
│  Orquestación  core.py                                  │
│  Reaper.scrape()  →  _dispatch()  →  Scraper.run()      │
├─────────────────────────────────────────────────────────┤
│  Scrapers  scrapers/                                    │
│  FacebookScraper  |  InstagramScraper                   │
│  (fetch + selección de parser + fallback + merge)       │
├─────────────────────────────────────────────────────────┤
│  Red  network/                                          │
│  ContentFetcher  |  NetworkInterceptor                  │
│  (Playwright → HTML + tráfico GraphQL/API)              │
├─────────────────────────────────────────────────────────┤
│  Parsers  parsers/                                      │
│  PostParser | ReelParser | VideoParser | GroupParser    │
│  ProfileParser | PhotoParser | IgPostParser | IgReelParser│
│  (HTML + JSON blocks → dict estructurado)               │
├─────────────────────────────────────────────────────────┤
│  Utils  utils/                                          │
│  fb_url_classifier | fb_auth_detector | logger          │
│  metrics | encoding | http | media | platforms          │
└─────────────────────────────────────────────────────────┘
```

### Principio de flujo de datos

```
URL → ScraperConfig → Reaper → FacebookScraper/InstagramScraper
    → ContentFetcher → FetchResult{html, traffic}
    → Parser.parse() → dict[str, Any]
```

Los datos nunca fluyen hacia arriba. Cada capa solo conoce la que tiene inmediatamente por debajo.

---

## 2. Entorno y Requisitos

### Requisitos de sistema

| Componente | Versión mínima | Propósito |
|---|---|---|
| Python | 3.11 | Sintaxis `match`, `X \| Y`, `str.removeprefix` |
| pip | 23+ | Instalación con `pyproject.toml` |
| Firefox (via Playwright) | 1.40+ | Renderizado JavaScript |
| Sistema operativo | Linux / macOS / Windows | — |

### Dependencias de producción

```toml
# pyproject.toml
dependencies = [
    "playwright>=1.40.0",    # Automatización del navegador
    "beautifulsoup4>=4.12.0",# Parseo de HTML
    "httpx>=0.27.0",         # Cliente HTTP asíncrono (subtítulos, etc.)
    "requests>=2.31.0",      # Cliente HTTP síncrono (utilidades)
]
```

### Dependencias de desarrollo

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
    "mypy>=1.10",
]
```

---

## 3. Instalación y Configuración del Entorno de Desarrollo

### Instalación completa

```bash
# 1. Clonar el repositorio
git clone <repo-url>
cd reaper

# 2. Crear entorno virtual (recomendado)
python3.11 -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 3. Instalar en modo editable con dependencias de desarrollo
pip install -e ".[dev]"

# 4. Instalar el navegador Firefox (solo la primera vez)
playwright install firefox

# 5. Verificar instalación
python -c "from reaper import scrape, Reaper, ScraperConfig; print('OK')"
reaper --version
```

### Estructura de directorios de trabajo

```
reaper/                         ← raíz del proyecto
├── src/reaper/                 ← código fuente del paquete
├── tests/                      ← tests unitarios e integración
├── data/
│   ├── debug_artifacts/        ← artefactos de debug (auto-creado)
│   └── results/                ← resultados JSON (auto-creado)
├── pyproject.toml              ← configuración del proyecto
└── README.md
```

---

## 4. Estructura del Proyecto

```
src/reaper/
├── __init__.py             # API pública: scrape(), Reaper, excepciones
├── cli.py                  # Punto de entrada CLI (comando `reaper`)
├── config.py               # ScraperConfig — único objeto de configuración
├── core.py                 # Reaper, UnsupportedPlatformError, ScrapingError
│
├── network/
│   ├── __init__.py
│   ├── interceptor.py      # NetworkInterceptor, CapturedTraffic, FetchResult
│   └── content_fetcher.py  # ContentFetcher — gestión Playwright + debug
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py             # BaseScraper (abstracto)
│   ├── facebook.py         # FacebookScraper — orquesta parsers FB
│   └── instagram.py        # InstagramScraper — orquesta parsers IG
│
├── parsers/
│   ├── __init__.py         # Re-exportaciones de todos los parsers
│   ├── base_parser.py      # BaseParser — DFS, safe_get, JSON blocks
│   ├── facebook/
│   │   ├── facebook_parser.py  # FacebookContentParser — lógica común FB
│   │   ├── post_parser.py      # Posts regulares (/posts/, /permalink/)
│   │   ├── reel_parser.py      # Reels (/reel/)
│   │   ├── video_parser.py     # Vídeos nativos (/videos/, /watch/)
│   │   ├── group_parser.py     # Grupos (/groups/)
│   │   ├── profile_parser.py   # Perfiles de usuario y páginas
│   │   └── photo_parser.py     # Fotos individuales (/photo.php)
│   └── instagram/
│       ├── ig_post_parser.py   # Posts de Instagram (/p/)
│       └── ig_reel_parser.py   # Reels de Instagram (/reel/)
│
└── utils/
    ├── __init__.py             # Re-exportaciones de utilidades
    ├── logger.py               # setup_logging, get_logger, DebugContext
    ├── fb_url_classifier.py    # get_parser_from_fb_url()
    ├── fb_auth_detector.py     # requires_auth()
    ├── fb_helpers.py           # Helpers internos de Facebook
    ├── platforms.py            # detect_platform_from_url()
    ├── metrics.py              # SocialMediaParser — parseo de métricas
    ├── encoding.py             # datetime_encoder, decode_alphanumeric_id
    ├── http.py                 # get_text_from_url()
    └── media.py                # srt_to_dict()
```

---

## 5. Flujo de Trabajo Completo

### Traza de ejecución de `scrape("https://facebook.com/reel/123")`

```
scrape(url)                         [__init__.py]
  └─→ Reaper().scrape(url)          [core.py]
        ├─ _logging_initialized?    inicializa setup_logging si es primera vez
        ├─ ScraperConfig(url=url)   valida y crea configuración
        ├─ detect_platform_from_url → "facebook"
        └─→ _dispatch(config, "facebook")
              └─→ FacebookScraper(config).run()   [scrapers/facebook.py]
                    ├─→ ContentFetcher.fetch()    [network/content_fetcher.py]
                    │     ├─ Playwright: browser → context → page
                    │     ├─ NetworkInterceptor.attach(page)
                    │     ├─ page.goto(url)
                    │     ├─ auto_scroll (si config.auto_scroll)
                    │     ├─ page.content() → html_content
                    │     └─→ FetchResult{html, traffic, success}
                    │
                    ├─ requires_auth(html, url)  detecta login wall
                    ├─ get_parser_from_fb_url(url) → "ReelParser"
                    │
                    ├─→ ReelParser(html, ...).parse()   [parsers/facebook/reel_parser.py]
                    │     ├─ _extract_json_blocks()     busca <script type=application/json>
                    │     ├─ _locate_all_stories()      DFS buscando nodos Story
                    │     ├─ _build_reaction_cache()    cachea métricas por post_id
                    │     ├─ _build_reel_dict(story)    construye dict del reel
                    │     ├─ _parse_traffic()           enriquece con datos GraphQL
                    │     └─→ dict{platform, status, author, text, ...}
                    │
                    ├─ [si __typename=facebook_video] → fetch + ReelParser (merge)
                    ├─ [si __typename=facebook_reel]  → fetch + VideoParser (merge)
                    │
                    ├─ _enrich_with_traffic(result, fetch_result)
                    └─→ dict[str, Any]   ← resultado final
```

### Diagrama de selección de parser (Facebook)

```
URL Facebook
    │
    ▼
get_parser_from_fb_url(url)
    │
    ├─ /hashtag/        → HashtagParser (→ PostParser)
    ├─ /reel/           → ReelParser
    ├─ /videos/ | ?v=   → VideoParser
    ├─ /photo.php | /photo/ → PhotoParser
    ├─ /groups/*/posts  → PostParser
    ├─ /groups/         → GroupParser
    ├─ /*/posts/        → PostParser
    ├─ profile.php      → ProfileParser
    ├─ permalink.php    → PostParser
    ├─ /share/r/        → ReelParser
    ├─ /share/p/        → PostParser
    ├─ /people/*/id/    → ProfileParser
    └─ /<vanity>        → ProfileParser
```

---

## 6. Módulo `config` — ScraperConfig

`ScraperConfig` es un dataclass inmutable que fluye por toda la librería como único objeto de configuración.

### Definición completa

```python
@dataclass
class ScraperConfig:
    # Obligatorio
    url: str

    # Navegador
    headless: bool = True
    debug: bool = False
    screenshot: bool = False

    # Scroll
    auto_scroll: bool = False
    infinity_scroll: bool = False

    # Proxy
    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None

    # Rutas
    artifacts_dir: Path = field(default_factory=lambda: Path("data/debug_artifacts"))
    results_dir:   Path = field(default_factory=lambda: Path("data/results"))
```

### Validaciones en `__post_init__`

| Condición | Excepción |
|---|---|
| `url` vacía | `ValueError` |
| `url` sin `http://` o `https://` | `ValueError` |
| `proxy_username/password` sin `proxy_server` | `ValueError` |
| `infinity_scroll=True` con `auto_scroll=False` | `ValueError` |

### Propiedades derivadas

```python
config.has_proxy    # bool — True si proxy_server is not None
config.proxy_config # dict{"server", "username"?, "password"?} | None
```

---

## 7. Módulo `core` — Reaper y Excepciones

### Clase `Reaper`

```python
class Reaper:
    async def scrape(
        self,
        url: str,
        *,
        headless: bool = True,
        debug: bool = False,
        screenshot: bool = False,
        auto_scroll: bool = True,
        infinity_scroll: bool = False,
        proxy_server: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
    ) -> dict[str, Any]: ...
```

Internamente:
1. Llama a `setup_logging()` en la primera ejecución (modo programático).
2. Crea `ScraperConfig` con los parámetros.
3. Detecta la plataforma con `detect_platform_from_url()`.
4. Despacha al scraper apropiado via `_dispatch()`.
5. Captura excepciones y las convierte en `ScrapingError`.

### Excepciones

```python
class UnsupportedPlatformError(Exception):
    """URL no corresponde a ninguna plataforma soportada.
    Lanzada por Reaper._dispatch() cuando platform == "unknown".
    """

class ScrapingError(Exception):
    """Error en runtime durante el proceso de scraping.
    Envuelve cualquier excepción no esperada de los scrapers.
    """
```

### Inicialización perezosa del logging

```python
_logging_initialized: bool = False  # módulo-global

# En Reaper.scrape():
if not _logging_initialized:
    setup_logging(level="DEBUG" if debug else "INFO")
    _logging_initialized = True
```

La CLI llama a `setup_logging()` explícitamente antes de instanciar `Reaper`, por lo que la rama perezosa solo se ejecuta en uso programático directo.

---

## 8. Módulo `network` — Fetch e Interceptor

### `FetchResult`

Objeto retornado por `ContentFetcher.fetch()`:

```python
@dataclass
class FetchResult:
    original_url: str
    final_url: str | None = None
    html_content: str | None = None       # HTML con JS ejecutado
    traffic: CapturedTraffic | None = None
    fetched_at: datetime = field(default_factory=datetime.now)
    success: bool = False
    error: str | None = None
    authenticated: bool = False
    account_id: str | None = None
    debug_session_dir: Path | None = None
```

### `CapturedTraffic`

Organiza el tráfico de red capturado durante el fetch:

```python
@dataclass
class CapturedTraffic:
    graphql_requests:  list[CapturedRequest]   # peticiones a /api/graphql/
    graphql_responses: list[CapturedResponse]  # respuestas GraphQL
    api_requests:      list[CapturedRequest]   # peticiones a /api/v1/ (Instagram)
    api_responses:     list[CapturedResponse]  # respuestas API REST

    def graphql_by_operation(self, operation_name: str) -> list[CapturedResponse]:
        """Filtra por nombre de operación GraphQL (búsqueda parcial)."""

    def summary(self) -> str:
        """Línea de resumen legible del tráfico."""
```

### `CapturedRequest` y `CapturedResponse`

```python
@dataclass
class CapturedRequest:
    url: str
    method: str
    post_data_raw: str | None = None
    post_data: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    graphql_meta: GraphQLMeta | None = None

@dataclass
class CapturedResponse:
    url: str
    status: int
    body: dict[str, Any] | None = None    # body JSON parseado
    body_raw: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

@dataclass
class GraphQLMeta:
    friendly_name: str | None = None      # nombre de la operación GraphQL
    doc_id: str | None = None
    variables: dict[str, Any] | None = None
    caller_class: str | None = None
```

### `ContentFetcher` — Proceso de fetch

```python
class ContentFetcher:
    def __init__(self, url: str, headless: bool = True, debug: bool = False): ...

    async def fetch(
        self,
        screenshot: bool = False,
        auto_scroll: bool = True,
        infinity_scroll: bool = False,
        proxy_server: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
    ) -> FetchResult: ...
```

**Proceso interno de `fetch()`:**

```
1. async_playwright() → browser (Firefox)
2. browser.new_context(viewport, proxy_config)
3. context.new_page()
4. NetworkInterceptor.attach(page)   ← ANTES de goto()
5. page.goto(url, wait_until="networkidle")
6. [auto_scroll] → _auto_scroll_by_height()
7. [infinity_scroll] → _infinity_scroll_by_network()
8. [screenshot] → page.locator(...).screenshot()
9. page.content() → html_content
10. interceptor.get_traffic() → CapturedTraffic
11. [debug] → _save_debug_artifacts()
12. return FetchResult(success=True, ...)
```

### `NetworkInterceptor`

Se adjunta a la página Playwright antes de `goto()` y escucha los eventos `request` y `response`:

```python
class NetworkInterceptor:
    GRAPHQL_PATTERNS: frozenset[str]        # URLs de GraphQL de Facebook
    INSTAGRAM_API_PATTERNS: frozenset[str]  # URLs de API de Instagram

    def attach(self, page: Page) -> None:
        """Registra los handlers en la página."""

    def get_traffic(self) -> CapturedTraffic:
        """Retorna el tráfico capturado hasta el momento."""

    def snapshot_activity(self) -> int:
        """Retorna el total de responses como baseline para infinity_scroll."""
```

---

## 9. Módulo `scrapers` — Lógica de Orquestación

### `BaseScraper`

```python
class BaseScraper(ABC):
    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self.logger = get_logger(self.__class__.__module__)

    @abstractmethod
    async def run(self) -> dict[str, Any]: ...

    def _base_result(self, platform: str) -> dict[str, Any]:
        return {"url": self.config.url, "platform": platform, "status": "ok"}
```

### `FacebookScraper`

Responsabilidades:

1. **Fetch** de la URL mediante `_fetch()`.
2. **Detección de autenticación** con `requires_auth()`.
3. **Selección de parser** con `get_parser_from_fb_url()`.
4. **Lógica de merge** reel↔video cuando el contenido es ambiguo.
5. **Fallback** a parser alternativo si el primario no encuentra datos.
6. **Enriquecimiento** con `graphql_responses_count`.

#### Mapas de parsers

```python
_PRIMARY_PARSER_MAP = {
    "PostParser":    PostParser,
    "ReelParser":    ReelParser,
    "VideoParser":   VideoParser,
    "GroupParser":   GroupParser,
    "ProfileParser": ProfileParser,
    "HashtagParser": PostParser,
    "PhotoParser":   PhotoParser,
}

_FALLBACK_PARSER_MAP = {
    "ReelParser":    VideoParser,   # si ReelParser falla, intenta VideoParser
    "ProfileParser": GroupParser,   # si ProfileParser falla, intenta GroupParser
}
```

#### Lógica de merge reel↔video

Facebook sirve el mismo contenido bajo dos URLs diferentes:
- `/reel/<id>` → sirve datos de Reel (engagement, texto, hashtags)
- `/watch/?v=<id>` → sirve datos de Vídeo (URLs de descarga, métricas técnicas)

El scraper detecta el `__typename` y realiza un segundo fetch para combinar ambos:

```
__typename == "facebook_video" → fetch /reel/<permalink_url>
                                → ReelParser → merge(reel, video)

__typename == "facebook_reel"  → fetch /watch/?v=<attachment_id>
                                → VideoParser → merge(reel, video)
```

#### Lógica de foto de álbum

```
__typename == "facebook_photo" AND is_album == True
    → fetch parent_post_url
    → PostParser → resultado del post completo con todas las fotos
```

### `InstagramScraper`

Estrategia de dos intentos:

```
1. IgPostParser.parse()
   └─ Si result["error"] → 2. IgReelParser.parse()
```

---

## 10. Módulo `parsers` — Extracción de Datos

### Jerarquía de herencia

```
BaseParser  (base_parser.py)
├── FacebookContentParser  (facebook/facebook_parser.py)
│   ├── PostParser          (facebook/post_parser.py)
│   ├── ReelParser          (facebook/reel_parser.py)
│   ├── VideoParser         (facebook/video_parser.py)
│   ├── GroupParser         (facebook/group_parser.py)
│   ├── ProfileParser       (facebook/profile_parser.py)
│   └── PhotoParser         (facebook/photo_parser.py)
├── IgPostParser            (instagram/ig_post_parser.py)
└── IgReelParser            (instagram/ig_reel_parser.py)
```

### `BaseParser` — Utilidades compartidas

#### Estado garantizado en `self.result` (inicializado por `BaseParser.__init__`)

```python
self.result: dict[str, Any] = {
    "platform":           platform,     # "facebook" | "instagram"
    "status":             "ok",
    "error":              None,
    "raw_data_available": False,
    "scraped_at":         datetime.now(),
    "final_url":          self.final_url,
    "post_url":           self.original_url,
}
```

Las subclases añaden sus campos específicos con `self.result.update({...})`.

#### Métodos de utilidad de `BaseParser`

```python
# Búsqueda DFS — primer resultado
def _recursive_search(
    self,
    data: Any,
    condition: Callable[[dict], bool],
    extract: Callable[[dict], Any] | None = None,
) -> Any | None: ...

# Búsqueda DFS — todos los resultados
def _find_all_nodes(
    self,
    data: Any,
    condition: Callable[[dict], bool],
    depth: int = 0,
    max_depth: int = 50,
) -> list[dict]: ...

# Acceso seguro a rutas anidadas (nunca lanza KeyError)
def _safe_get(self, data: Any, *keys: str, default: Any = None) -> Any: ...

# Extrae bloques <script type="application/json"> del HTML
def _extract_json_blocks(self) -> list[dict]: ...

# Convierte timestamp Unix a datetime
@staticmethod
def _parse_timestamp(ts: Any) -> datetime | None: ...

# Obtiene respuestas GraphQL por nombre de operación
def _get_graphql_response_by_operation(self, operation_name: str) -> list[dict]: ...
def _get_first_graphql_response(self, operation_name: str) -> dict | None: ...
```

### `FacebookContentParser` — Métodos comunes de Facebook

```python
# Extrae actor/author desde nodos Story (3 rutas de fallback)
def _extract_author_common(self, story: dict, fallback_path: list[str] | None = None) -> dict: ...

# Extrae datos del grupo si el post pertenece a uno
def _extract_group_common(self, story: dict, ...) -> dict | None: ...

# Extrae reaction_count, share_count, comments_count, reactions
def _extract_feedback_common(self, story: dict, ...) -> dict: ...

# Extrae adjuntos (fotos, vídeos, álbumes)
def _extract_attachments_common(self, story: dict, ...) -> list[dict]: ...

# Parsea hashtags y menciones desde message.ranges
def _parse_ranges(self, ranges: list[dict]) -> tuple[list[dict], list[dict]]: ...

# Extrae el post original cuando es un share/repost
def _extract_original_post_common(self, story: dict) -> dict | None: ...

# Enriquece self.result con metadatos del tráfico GraphQL
def _parse_facebook_traffic(self, operation_patterns: list[str]) -> None: ...
```

### Ciclo de vida de un parser

```python
parser = PostParser(
    html_content=fetch_result.html_content,
    final_url=final_url,
    original_url=fetch_result.original_url,
    traffic=fetch_result.traffic,
    debug=self.config.debug,
)
result = parser.parse()
```

Internamente `parse()` sigue siempre el mismo flujo:
```
_extract_json_blocks()      # parsea todos los <script type="application/json">
_locate_*()                 # localiza los nodos clave con DFS
if not found: return error  # early return si no hay datos
_extract_basic_info()       # id, timestamp, permalink
_extract_author()           # actor / actors[0]
_extract_content()          # texto, hashtags, menciones
_extract_attachments()      # fotos, vídeos
_extract_feedback()         # reacciones, shares, comentarios
_parse_traffic()            # tráfico GraphQL adicional
return self.result
```

### Estrategias de localización por parser

| Parser | Nodo buscado | Condición |
|---|---|---|
| `PostParser` | Story | `__isFeedUnit == "Story"` |
| `ReelParser` | creation_story | `short_form_video_context` o `post_id` |
| `VideoParser` | Nodo vídeo | `__typename == "Video"` con id numérico |
| `GroupParser` | Nodo grupo | `profile_header_renderer` o `__bbox.result.data.group` |
| `ProfileParser`| Nodo perfil | `__typename` en `User/UserProfile/Page` |
| `PhotoParser` | currMedia | `__typename == "Photo"` + `image` dict + `creation_story` |
| `IgPostParser` | Post IG | `xdt_api__v1__media__shortcode__web_info` |
| `IgReelParser` | Reel IG | `xdt_api__v1__clips__clips_on_logged_out_connection_v2` |

---

## 11. Módulo `utils` — Utilidades Compartidas

### `fb_url_classifier.py`

```python
def get_parser_from_fb_url(url: str) -> str:
    """Devuelve el nombre del parser sin hacer ninguna petición HTTP.
    Posibles valores: PostParser | ReelParser | VideoParser | GroupParser
                     | ProfileParser | PhotoParser | HashtagParser | UnknownParser
    """
```

Internamente normaliza la URL (elimina parámetros de tracking, convierte `m.facebook.com` → `www.facebook.com`) y aplica 15 reglas regex en orden de prioridad.

### `fb_auth_detector.py`

```python
@dataclass
class AuthResult:
    requires_auth: bool
    reason: str

def requires_auth(html_content: str, url: str) -> AuthResult:
    """Detecta si la página requiere autenticación analizando el HTML."""
```

### `logger.py`

```python
def setup_logging(
    level: str | int = "INFO",
    use_color: bool | None = None,  # None = autodetectar TTY
    log_file: str | None = None,
    propagate: bool = False,
) -> None: ...

def get_logger(name: str) -> logging.Logger:
    """Equivalente a logging.getLogger(name). Punto único de importación."""

class DebugContext:
    """Context manager que activa temporalmente DEBUG en un logger."""
    def __init__(self, logger_name: str = "reaper", level: int = logging.DEBUG): ...
```

### `platforms.py`

```python
def detect_platform_from_url(url: str) -> str:
    """Retorna "facebook" | "instagram" | "unknown"."""

FACEBOOK_DOMAINS: frozenset[str]
INSTAGRAM_DOMAINS: frozenset[str]
PLATFORM_DOMAINS: dict[str, frozenset[str]]
```

### `metrics.py`

```python
class SocialMediaParser:
    """Parsea valores de métricas de texto a enteros.
    Maneja formatos como: "1.2K", "45,3K", "1M", "23 456".
    """
    result: dict[str, int]  # acumulador

    def _parse_metric(self, value: str | int | float | None, field_name: str) -> int: ...
    def parse_engagement_metrics(
        self,
        reaction_count: str | int | float | None,
        share_count: str | int | float | None,
        comment_count: str | int | float | None = None,
    ) -> dict[str, int]: ...
```

### `encoding.py`

```python
def datetime_encoder(obj: Any) -> str:
    """Serializa datetime a ISO 8601 para json.dumps(default=...)."""

def decode_alphanumeric_id(encoded_id: str) -> str:
    """Decodifica IDs alfanuméricos de Facebook (base64 → numeric string)."""
```

---

## 12. Estructuras de Datos — Resultados por Tipo de Contenido

Todos los resultados comparten el esqueleto base:

```python
{
    "platform":           str,       # "facebook" | "instagram"
    "status":             str,       # "ok" | "error"
    "error":              str | None,
    "raw_data_available": bool,
    "scraped_at":         datetime,
    "final_url":          str,
    "post_url":           str,
}
```

### Facebook — Post regular (`__typename: "regular_post"`)

```python
{
    # ── Base ───────────────────────────────
    "platform":           "facebook",
    "status":             "ok",
    "error":              None,
    "raw_data_available": True,
    "scraped_at":         datetime,
    "final_url":          str,
    "post_url":           str,
    "__typename":         "regular_post",
    # ── Identificación ─────────────────────
    "id":                 str,          # post_id numérico
    "posted_at":          datetime | None,
    "permalink_url":      str,
    "is_sponsored":       bool,
    # ── Autor ──────────────────────────────
    "author": {
        "id":          str,
        "name":        str,
        "url":         str,
        "avatar":      str,             # URL de la foto de perfil
        "is_verified": bool,
        "work_info":   str | None,
    },
    # ── Contenido ──────────────────────────
    "text":        str,
    "hashtags": [{"tag": str, "id": str, "url": str, "mobile_url": str}],
    "mentions": [{"id": str, "url": str}],
    # ── Adjuntos ───────────────────────────
    "attachments": [
        {
            "type":    str,             # "photo" | "video" | "link" | ...
            "id":      str,
            "url":     str,
            "caption": str | list,
        }
    ],
    # ── Engagement ─────────────────────────
    "reaction_count": int,
    "share_count":    int,
    "comments_count": int,
    "reactions": [{"id": str, "type": str, "count": int}],
    "comments": [
        {
            "id":           str,
            "depth":        int,        # 0 = comentario raíz, 1+ = reply
            "text":         str | None,
            "created_at":   datetime | None,
            "author": {
                "id": str, "name": str, "profile_url": str,
                "gender": str, "avatar": str | None,
            },
            "replies_count":  int,
            "reactions":      list,
            "reaction_count": int,
        }
    ],
    # ── Contexto ───────────────────────────
    "group": {
        "id": str, "name": str, "url": str, "avatar": str,
    } | None,
    "original_post": dict | None,       # si es un share/repost
    # ── Tráfico GraphQL ────────────────────
    "graphql_responses_count":    int,
    "traffic_parsed":             bool,
    "graphql_responses_available":int,
    "api_responses_available":    int,
    "graphql_operations_found":   dict[str, int],
}
```

### Facebook — Reel (`__typename: "facebook_reel"`)

```python
{
    # (campos base + author + text + hashtags + mentions comunes)
    "__typename":     "facebook_reel",
    "id":             str,
    "posted_at":      datetime | None,
    "permalink_url":  str,
    "reaction_count": int,
    "comments_count": int,
    "share_count":    int,
    "privacy":        str,              # "Public" | "Friends" | ...
    "is_ad":          bool,
    "attachments": [
        {
            "type":          "reel",
            "id":            str,
            "url":           str | None,         # URL SD del vídeo
            "caption":       list,               # SRT/subtítulos
            "thumbnail_url": str | None,
            "duration_ms":   int | None,
            "width":         int | None,
            "height":        int | None,
            "play_count":    int | None,
        }
    ],
    "group":         dict | None,
    "original_post": dict | None,
    "collaborators": list,
    "reactions":     list[dict],
    "feed": [                           # reels relacionados en la misma página
        {"__typename": "feed_facebook_reel", ...}
    ],
    "feed_video": [dict],              # vídeos del merge reel+video
}
```

### Facebook — Vídeo nativo (`__typename: "facebook_video"`)

```python
{
    "__typename":         "facebook_video",
    "id":                 str,
    "posted_at":          datetime | None,
    "permalink_url":      str,
    "title":              str,
    "title_story":        str,
    "text":               str,
    "hashtags":           list,
    "mentions":           list,
    "author":             dict,
    "privacy":            dict | None,
    "collaborators":      list,
    "thumbnail_url":      str | None,
    "video": {
        "url_sd":      str | None,
        "captions":    list,            # subtítulos SRT parseados
        "duration_ms": int | None,
        "width":       int | None,
        "height":      int | None,
        "is_looping":  bool,
        "is_spherical": bool,
    },
    "is_gaming_video":    bool,
    "is_podcast_video":   bool,
    "is_looping":         bool,
    "is_spherical":       bool,
    "is_video_broadcast": bool,
    "is_live_streaming":  bool,
    "broadcast_id":       str | None,
    "reaction_count":     int,
    "reactions":          list,
    "comments_count":     int,
    "share_count":        int,
    "play_count":         int | None,
    "video_view_count":   int | None,
    "video_post_view_count": int | None,
    "feed": [dict],                     # vídeos relacionados
}
```

### Facebook — Grupo (`__typename: "about_private_group"`)

```python
{
    "__typename":    "about_private_group",
    "group_url":     str,
    "id":            str,
    "name":          str,
    "url":           str,
    "privacy": {
        "level":       str,             # "CLOSED" | "OPEN" | "SECRET"
        "description": str,
        "is_private":  bool,
    },
    "cover_photo": {
        "photo_id": str,
        "cdn_uri":  str,
    } | None,
    "description":       str,
    "created_at":        datetime | None,
    "total_members":     int,
    "total_members_text":str,
    "posts_last_day":    int | None,
    "admins": [
        {"id": str, "name": str, "url": str, "avatar": str}
    ],
    "admins_count":      int,
    "viewer": {
        "is_member":       bool,
        "membership_state": str | None,
    },
    "content_gated":     bool,         # True si el contenido requiere ser miembro
    "requested_post_id": str | None,   # post_id si la URL apuntaba a un permalink
}
```

### Facebook — Perfil (`__typename: "facebook_user_profile"`)

```python
{
    "__typename":    "facebook_user_profile",
    "profile_url":   str,
    "id":            str,
    "name":          str,
    "url":           str,
    "username":      str | None,
    "gender":        str,
    "alternate_name":str,
    "avatar":        str,              # URL foto de perfil (120x120)
    "profile_video": dict | None,
    "banner": {
        "photo_id": str, "cdn_uri": str,
    } | None,
    "bio":           str,
    "category":      str | None,
    # Métricas
    "followers_count":     int,
    "following_count":     int,
    "friends_count":       int,
    "likes_count":         int,        # para páginas
    "talking_about_count": int,
    # Página delegada (si es perfil de página)
    "delegate_page":          dict | None,
    "is_business_page_active":bool,
    # Datos biográficos
    "education":    {"text": str, "name": str, "url": str, "id": str},
    "current_city": {"text": str, "name": str, "url": str, "id": str},
    "hometown":     {"text": str, "name": str, "url": str, "id": str},
    "contact_info": {
        "email":           str,
        "phone":           str,
        "websites":        list[str],
        "social_accounts": list[dict],
    },
    # Timeline
    "feed": [dict],                    # posts del timeline del perfil
}
```

### Facebook — Foto (`__typename: "facebook_photo"`)

```python
{
    "__typename":           "facebook_photo",
    "id":                   str,       # photo_id
    "posted_at":            datetime | None,
    "permalink_url":        str,       # URL del post padre
    "is_sponsored":         bool,
    "is_album":             bool,      # True si pertenece a un álbum MULTI_IMAGE
    "parent_post_url":      str | None,# URL del post padre (para re-fetch si is_album)
    "author":               dict,
    "attachments": [
        {
            "type":                  "photo",
            "id":                    str,
            "url":                   str,   # URL CDN de la imagen
            "width":                 int | None,
            "height":                int | None,
            "accessibility_caption": str,
        }
    ],
    "text":        str,                # caption (vacío en fotos de álbum sin caption)
    "hashtags":    list,
    "mentions":    list,
    "reaction_count": int,
    "share_count":    int,
    "comments_count": int,
    "reactions":      list,
    "comments":       list,
    "group":          dict | None,
}
```

### Instagram — Post (`platform: "instagram"`)

```python
{
    "platform":           "instagram",
    "status":             "ok",
    "code":               str,         # shortcode del post (ej. "DVQ7dz...")
    "id":                 str,
    "pk":                 str,
    "permalink_url":      str,
    "posted_at":          datetime | None,
    "user": {
        "id":              str,
        "username":        str,
        "full_name":       str,
        "profile_pic_url": str,
        "is_verified":     bool,
    },
    "group":              Any | None,
    "location":           Any | None,
    "media_type":         str,         # "Photo" | "Reel" | "carousel"
    "carousel_media_count": int | None,
    "thumbnail":          str | None,  # display_uri
    "caption":            str | None,  # accessibility_caption
    "text":               str | None,  # caption.text
    "like_count":         int,
    "comment_count":      int,
    "link":               str | None,
    "image_versions":     list | None, # candidates de image_versions2
    "video_versions":     list | None,
    "tagged_users":       list[dict],
    "comments":           list[dict],  # preview_comments
    "feed":               list[dict],  # otros posts del perfil del autor
}
```

### Instagram — Reel

Misma estructura que el post de Instagram pero con campo adicional:

```python
{
    # (todos los campos de Instagram Post)
    "media_repost_count": int,
    "feed": list[dict],  # reels relacionados + posts del perfil
}
```

---

## 13. Sistema de Logging

### Jerarquía de loggers

```
reaper                              ← logger raíz (configurado por setup_logging)
├── reaper.cli
├── reaper.core
├── reaper.scrapers.facebook
├── reaper.scrapers.instagram
├── reaper.network.content_fetcher
├── reaper.network.interceptor
└── reaper.parsers
    ├── reaper.parsers.base_parser
    ├── reaper.parsers.facebook.post_parser
    ├── reaper.parsers.facebook.reel_parser
    └── ...
```

### Configuración

```python
from reaper.utils.logger import setup_logging

# Normal (INFO)
setup_logging(level="INFO")

# Debug con colores (TTY)
setup_logging(level="DEBUG", use_color=True)

# Producción con fichero
setup_logging(level="WARNING", log_file="/var/log/reaper.log")

# En cada módulo
from reaper.utils.logger import get_logger
logger = get_logger(__name__)
```

### Formato de logs

**Modo INFO** (sin milisegundos):
```
14:22:31 INFO     scrapers.facebook  Iniciando extracción Facebook | url=...
14:22:32 INFO     network.content_fetcher  Navegando a: https://...
14:22:35 INFO     scrapers.facebook  Extracción completada | parser=ReelParser
```

**Modo DEBUG** (con milisegundos):
```
14:22:31.045 DEBUG    parsers.facebook.reel_parser  Nodo Story localizado.
14:22:31.047 DEBUG    network.interceptor  ↑ [GRAPHQL] ReelQuery POST ...
14:22:32.123 DEBUG    parsers.facebook.reel_parser  GraphQL op 'Reel': 3 responses
```

### `DebugContext` — Debug puntual

```python
from reaper.utils.logger import DebugContext

# Solo para el parser de fotos
with DebugContext("reaper.parsers.facebook.photo_parser"):
    result = PhotoParser(...).parse()

# Para toda la capa de parsers
with DebugContext("reaper.parsers"):
    result = await scrape(url)

# Restaura el nivel automáticamente al salir del contexto
```

---

## 14. Modo Debug y Artefactos

Con `debug=True`, por cada sesión se crea un directorio en `data/debug_artifacts/`:

```
data/debug_artifacts/
└── www.facebook.com_reel_123_20250601_143022/
    ├── meta.json                   ← metadatos de la sesión
    ├── page.html                   ← HTML completo renderizado
    ├── screenshot_full.png         ← captura de pantalla (si screenshot=True)
    ├── traffic.json                ← índice del tráfico de red
    ├── graphql_requests/
    │   └── 000_ReelQuery.json
    ├── graphql_responses/
    │   ├── 000_ReelQuery.json
    │   └── 001_CommentsQuery.json
    ├── api_requests/
    └── api_responses/
```

### Análisis offline

```python
from reaper.network.content_fetcher import load_debug_session, list_debug_sessions
from reaper.parsers import ReelParser

# Listar sesiones disponibles
sessions = list_debug_sessions("data/debug_artifacts")
for s in sessions:
    print(s["fetched_at"], s["original_url"][:60])

# Cargar y parsear sin navegador
fetch_result = load_debug_session(sessions[0]["path"])
result = ReelParser(
    html_content=fetch_result.html_content,
    final_url=fetch_result.final_url,
    original_url=fetch_result.original_url,
    traffic=fetch_result.traffic,
    debug=True,
).parse()
```

---

## 15. Añadir un Nuevo Parser

### Pasos completos

**1. Crear el archivo del parser**

```python
# src/reaper/parsers/facebook/event_parser.py
from reaper.parsers.facebook.facebook_parser import FacebookContentParser
from reaper.utils.logger import get_logger
from typing import Any
import traceback

logger = get_logger(__name__)

class EventParser(FacebookContentParser):
    """Parser para eventos de Facebook (/events/<id>)."""

    def __init__(self, html_content, final_url, original_url, traffic=None, debug=False):
        super().__init__(html_content, final_url, original_url, traffic, debug)
        self.result.update({
            "__typename": "facebook_event",
        })

    def parse(self) -> dict[str, Any]:
        logger.info("Iniciando extracción de EVENTO | url=%s", self.final_url)
        self._blocks = self._extract_json_blocks()

        if not self._locate_event_node():
            self.result["error"] = "No se encontró el nodo de evento."
            return self.result

        self.result["raw_data_available"] = True

        try:
            self._extract_event_info()
            self._parse_traffic()
        except Exception as exc:
            self.result["error"] = str(exc)
            logger.error("Error parseando evento: %s", exc)
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
        self.result["id"]       = node.get("id", "unknown")
        self.result["name"]     = node.get("name", "")
        self.result["start_at"] = self._parse_timestamp(node.get("start_timestamp"))
        # ... más campos

    def _parse_traffic(self) -> None:
        self._parse_facebook_traffic(operation_patterns=["EventQuery"])
```

**2. Registrar en `parsers/__init__.py`**

```python
from reaper.parsers.facebook.event_parser import EventParser

__all__ = [..., "EventParser"]
```

**3. Registrar en `scrapers/facebook.py`**

```python
from reaper.parsers import ..., EventParser

_PRIMARY_PARSER_MAP = {
    ...,
    "EventParser": EventParser,
}
```

**4. Actualizar el clasificador de URLs**

```python
# utils/fb_url_classifier.py
# Añadir en get_parser_from_fb_url():
if re.match(r"^/events/\d+", path, re.I):
    return "EventParser"
```

**5. Añadir a `_RESERVED` si aplica**

```python
_RESERVED: frozenset[str] = frozenset({
    ...,
    "events",  # ya está, pero verificar
})
```

---

## 16. Añadir una Nueva Plataforma

**1. Actualizar `utils/platforms.py`**

```python
TIKTOK_DOMAINS: frozenset[str] = frozenset({"tiktok.com", "www.tiktok.com"})

PLATFORM_DOMAINS = {
    "facebook":  FACEBOOK_DOMAINS,
    "instagram": INSTAGRAM_DOMAINS,
    "tiktok":    TIKTOK_DOMAINS,
}
```

**2. Crear el scraper**

```python
# src/reaper/scrapers/tiktok.py
from reaper.scrapers.base import BaseScraper
from reaper.utils.logger import get_logger
logger = get_logger(__name__)

class TiktokScraper(BaseScraper):
    async def run(self) -> dict[str, Any]:
        fetch_result = await self._fetch()
        # ... lógica de scraping
```

**3. Registrar en `core.py`**

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

## 17. Herramientas de Desarrollo

### Tests

```bash
pytest                              # todos los tests
pytest -v                           # verbose
pytest tests/test_reaper.py -v      # un fichero
pytest -k "proxy" -v                # filtrar por nombre
pytest --tb=short                   # traceback corto
```

### Linting y tipos

```bash
ruff check src/                     # detectar problemas
ruff check src/ --fix               # corregir automáticamente
ruff format src/                    # formatear código

mypy src/                           # verificación de tipos estática
```

### Configuración de herramientas (`pyproject.toml`)

```toml
[tool.ruff]
line-length    = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
python_version         = "3.11"
strict                 = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths    = ["tests"]
```

---

## 18. Decisiones de Diseño

### Por qué `self.result` se inicializa en `BaseParser`

Los campos garantizados (`platform`, `status`, `error`, `raw_data_available`, `scraped_at`, `final_url`, `post_url`) deben existir en **todos** los resultados, incluso en los de error. Inicializarlos en `BaseParser.__init__` garantiza esto independientemente de qué subclase se use o si `parse()` falla.

Cada subclase añade solo sus campos específicos con `self.result.update({...})`.

### Por qué los parsers de Facebook buscan en el HTML y no solo en el tráfico

Facebook sirve los datos iniciales embebidos en el HTML como bloques `<script type="application/json">`. El tráfico GraphQL complementa estos datos con paginación y comentarios adicionales. Los parsers usan el HTML como fuente primaria y el tráfico como enriquecimiento.

### Por qué `get_parser_from_fb_url` no hace fetch

El clasificador es determinista y funciona offline. Hacer un fetch para determinar el tipo de contenido añadiría latencia innecesaria y una dependencia de red. En los pocos casos donde el parser primario no encuentra datos, el `_FALLBACK_PARSER_MAP` del scraper maneja la situación.

### Por qué el merge reel↔video

Facebook sirve Reels y Vídeos como dos tipos de contenido distintos con HTML diferentes. Un Reel tiene datos de engagement y texto pero carece de URLs de descarga. Un Vídeo tiene URLs de descarga pero puede carecer de algunos metadatos de Reel. El merge combina lo mejor de ambos.

### Por qué `logging.getLogger(__name__)` se envuelve en `get_logger()`

`get_logger()` es funcionalmente idéntico a `logging.getLogger()`. El valor es semántico: todos los módulos importan desde un único punto (`reaper.utils.logger`), lo que facilita añadir funcionalidad futura (logging estructurado, contexto extra) sin modificar 20 archivos.
