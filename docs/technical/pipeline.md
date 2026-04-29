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

---

## Detección de bloqueo de acceso

`requires_auth()` evalúa el HTML en cuatro capas ordenadas de mayor a menor
prioridad. El resultado determina si el scraper reintenta, retorna error o
retorna `content_unavailable`.

### Capas de detección

```
requires_auth(html, final_url)
    │
    ├─ Capa 1   ¿tracePolicy de contenido real en el HTML?
    │               → AuthResult(requires_auth=False)   [página normal con popup]
    │
    ├─ Capa 1b  ¿Título/cuerpo de error de contenido no disponible?
    │               → AuthResult(requires_auth=False, auth_type="content_unavailable")
    │               [contenido eliminado — autenticarse no resuelve]
    │
    ├─ Capa 2   ¿URL final redirigida a /login o /checkpoint?
    │               → AuthResult(requires_auth=True, auth_type="login_redirect")
    │
    ├─ Capa 3   ¿tracePolicy="comet.error" o canonicalRouteName=CometErrorRoute?
    │               → AuthResult(requires_auth=True, auth_type="error_route")
    │
    └─ Capa 4   ¿CometErrorRoot.react + privacy=true? (ambas necesarias)
                    → AuthResult(requires_auth=True, auth_type="privacy_wall")
```

### Flujo completo en `FacebookScraper.run()`

```
fetch_result
    │
    ▼
requires_auth(html, url)
    │
    ├─ auth_type == "content_unavailable"
    │       └─ _content_unavailable_result()   → status="content_unavailable"
    │          [sin reintento, sin tocar el pool]
    │
    ├─ requires_auth == True
    │       └─ _handle_auth_wall(reason, url)
    │               │
    │               ├─ _active_account_id is None?  (fetch era anónimo)
    │               │       ├─ account_manager is None → error
    │               │       └─ → _retry_with_account()
    │               │
    │               └─ _active_account_id is not None?  (fetch autenticado)
    │                       ├─ update_status(COOKIE_EXPIRED)
    │                       ├─ _active_account_id = None
    │                       └─ → _retry_with_account()
    │
    │                   _retry_with_account()
    │                       ├─ fetch_with_account()
    │                       └─ requires_auth(html_retry)?
    │                               ├─ SÍ → None → error definitivo
    │                               └─ NO → fetch_result → continuar parseo
    │
    └─ requires_auth == False
            └─ continuar flujo normal (selección de parser)
```

### Valores de `status` en el resultado

| `status` | Causa | Reintento |
|---|---|---|
| `"ok"` | Extracción exitosa | — |
| `"content_unavailable"` | Contenido eliminado o inaccesible para todos | No |
| `"error"` | Fallo técnico, muro no recuperable, o doble muro | No aplica |