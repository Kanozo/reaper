# Pipeline de scraping

## Selección de parser en Facebook

```
URL de Facebook
    │
    ▼
get_parser_from_fb_url(url)
    │
    ├─ /reel/              → ReelParser
    ├─ /videos/ | ?v=      → VideoParser
    ├─ /photo.php | /photo/→ PhotoParser
    ├─ /groups/*/posts/    → PostParser
    ├─ /groups/            → GroupParser
    ├─ /*/posts/           → PostParser
    ├─ profile.php         → ProfileParser
    ├─ permalink.php       → PostParser
    ├─ /share/r/           → ReelParser
    ├─ /hashtag/           → PostParser (HashtagParser)
    └─ /<vanity>           → ProfileParser
```

## Lógica de merge reel↔video

Facebook sirve el mismo contenido bajo dos URLs con datos distintos:

- `/reel/<id>` → datos de engagement, texto, hashtags.
- `/watch/?v=<id>` → URLs de descarga, métricas técnicas de vídeo.

El scraper detecta el `__typename` del resultado primario y realiza un
fetch secundario para combinar ambas fuentes:

```
resultado.__typename == "facebook_video"
    → fetch /reel/<permalink_url>
    → ReelParser → merge(reel, video)

resultado.__typename == "facebook_reel"
    → fetch /watch/?v=<attachment_id>
    → VideoParser → merge(reel, video)
```

## Mapas de parsers

```python
# Parser primario por tipo de URL
_PRIMARY_PARSER_MAP = {
    "PostParser":    PostParser,
    "ReelParser":    ReelParser,
    "VideoParser":   VideoParser,
    "GroupParser":   GroupParser,
    "ProfileParser": ProfileParser,
    "HashtagParser": PostParser,
    "PhotoParser":   PhotoParser,
}

# Fallback si el primario no encuentra datos
_FALLBACK_PARSER_MAP = {
    "ReelParser":    VideoParser,
    "ProfileParser": GroupParser,
}
```

## Estrategia de Instagram

Instagram usa dos parsers en secuencia:

```
fetch(url)
    │
    ▼
IgPostParser.parse()
    │  ¿error?
    ├─ NO  → resultado final
    └─ SÍ  ↓
           │
           ▼
    IgReelParser.parse()
           │
           ▼
        resultado final
```

## Inyección de cookies en el navegador

Cuando hay una cuenta autenticada, las cookies se inyectan en el contexto
de Playwright **antes de navegar**:

```python
# ContentFetcher.fetch()
if cookies:
    await context.add_cookies(cookies)
    # Scope: contexto (no página) → aplica a todos los dominios y
    # persiste entre navegaciones dentro de la sesma sesión.

interceptor.attach(page)
await page.goto(url)
```

La inyección en el contexto (en lugar de en la página) garantiza que las
cookies estén presentes desde la primera petición de red y evita cualquier
redirect al muro de login de la plataforma.
