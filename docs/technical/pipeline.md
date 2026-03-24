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

## Reintento por muro de autenticación

Cuando `requires_auth()` detecta un muro de login en el HTML devuelto,
`_handle_auth_wall()` evalúa el contexto y decide si reintentar:

```
requires_auth(html) → True
    │
    ▼
_handle_auth_wall(reason, final_url)
    │
    ├─ _active_account_id is None?  (fetch era anónimo)
    │       │
    │       ├─ account_manager is None → return None (error)
    │       └─ account_manager tiene cuentas → _retry_with_account()
    │
    └─ _active_account_id is not None?  (fetch ya estaba autenticado)
            │
            ├─ update_status(COOKIE_EXPIRED)  ← excluir cuenta del pool
            ├─ _active_account_id = None      ← resetear para el retry
            └─ _retry_with_account()

_retry_with_account()
    │
    ├─ _fetch_with_account()  ← elige la siguiente mejor cuenta
    │       │
    │       └─ requires_auth(html_retry)?
    │               ├─ SÍ → return None (doble muro, error definitivo)
    │               └─ NO → return fetch_result  (continuar parseo)
```

### Garantías del mecanismo

- **Máximo 1 reintento** por petición. No hay bucles ni cascadas.
- **Detección de doble muro**: si el reintento también devuelve muro de
  autenticación, se retorna error sin seguir intentando.
- **Sin side effects en modo anónimo**: si `account_manager is None`,
  el comportamiento es idéntico al original — error inmediato, sin ramas nuevas.
- **Gestión de estado correcta**: `_active_account_id` se resetea a `None`
  antes del retry para que `_fetch_with_account()` seleccione una cuenta
  diferente en lugar de volver a la misma.
