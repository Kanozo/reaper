# Changelog

## 0.2.3

### Nuevas funcionalidades

- **Detección de contenido no disponible** en `FacebookScraper` y `fb_auth_detector`:

  Hasta esta versión, las páginas de Facebook donde el contenido ha sido eliminado
  o restringido permanentemente eran detectadas como muros de autenticación
  (`auth_type="error_route"`), provocando reintentos inútiles con cuentas del pool.
  Autenticarse no resuelve este caso porque el contenido no existe para nadie.

  La detección ahora distingue ambos casos antes de evaluar si hay muro de login:

  - **`content_unavailable`** — nuevo `auth_type`. Facebook muestra este estado
    cuando el contenido fue eliminado, era privado para un grupo reducido, o el
    propietario cambió la privacidad. Señales detectadas en el bloque JSON de
    bootstrap del HTML renderizado:
    - Título `"This content isn't available right now"` en los props del `rootView`.
    - Texto del cuerpo del error con referencias a "deleted" o "small group".

  - **Sin reintento**: cuando se detecta `content_unavailable`, `FacebookScraper`
    retorna inmediatamente con `status="content_unavailable"` sin consultar al
    `AccountManager` ni consumir cuentas del pool.

- **Nuevo `status` en el resultado**: `"content_unavailable"` permite que el caller
  distinga este caso de un error técnico (`"error"`) o de un muro de autenticación
  no recuperable, y tome decisiones informadas (e.g. marcar el contenido como
  eliminado en su sistema).

- **Nuevo método `FacebookScraper._content_unavailable_result()`**: helper análogo
  a `_error_result()` que emite el dict con `status="content_unavailable"` y
  `error="Content not available — deleted or restricted"`.

### Archivos modificados

- `src/reaper/utils/fb_auth_detector.py` — constante `_CONTENT_UNAVAILABLE` + capa 1b en `requires_auth()`
- `src/reaper/scrapers/facebook.py` — guardia `content_unavailable` en `run()` + método `_content_unavailable_result()`

---

## 0.2.2

### Nuevas funcionalidades

- **Reintento automático al detectar muro de autenticación** en `FacebookScraper` e `InstagramScraper`:

  Cuando `requires_auth()` detecta un muro de login en el HTML, el scraper
  ya no devuelve error inmediatamente. En su lugar aplica esta lógica:

  - **Fetch anónimo + muro**: si hay `AccountManager` con cuentas disponibles,
    reintenta automáticamente con una cuenta autenticada.
  - **Fetch autenticado + muro**: las cookies de la cuenta activa han sido
    invalidadas por la plataforma. La cuenta se marca como `COOKIE_EXPIRED`
    y se reintenta con una cuenta diferente del pool.
  - **Sin posibilidad de retry** (sin manager, sin cuentas, o segundo muro):
    retorna error. Máximo 1 reintento por petición.

- `InstagramScraper` añade detección de muro de autenticación via `requires_auth()`
  (no existía en versiones anteriores).

### Archivos modificados

- `src/reaper/scrapers/facebook.py` — métodos `_handle_auth_wall()`, `_retry_with_account()`
- `src/reaper/scrapers/instagram.py` — mismos métodos + import de `requires_auth`

---

## 0.2.1

### Nuevas funcionalidades

- **Refresco automático de cookies** sin intervención manual:
  - `FetchResult` añade el campo `updated_cookies`: cookies leídas del
    contexto Playwright tras la navegación. El servidor emite `Set-Cookie`
    en cada respuesta que actualiza tokens de seguridad y extiende TTLs.
  - `ContentFetcher.fetch()` lee `context.cookies()` post-navegación cuando
    la sesión es autenticada y lo expone en `FetchResult.updated_cookies`.
  - `BaseScraper._fetch_with_account()` compara las cookies originales con
    las actualizadas vía `_cookies_differ()` y llama a `manager.import_cookies()`
    si difieren, persistiendo la sesión renovada en disco automáticamente.
  - `_cookies_differ()` — helper privado que compara `value` y `expires`
    por nombre de cookie, ignorando metadatos irrelevantes.

### Comportamiento del `cookie_refresh_threshold`

El threshold pasa de señal principal a **safety net**. Con el auto-refresh
activo, `requests_since_cookie_refresh` se resetea a 0 después de cada
petición donde el servidor devuelva cookies actualizadas. El WARNING
`NEEDS_REFRESH` solo aparece si el servidor no emite `Set-Cookie` durante
N peticiones consecutivas — caso inusual que requiere intervención manual.

### Archivos modificados

- `src/reaper/network/interceptor.py` — campo `updated_cookies` en `FetchResult`
- `src/reaper/network/content_fetcher.py` — captura de cookies post-navegación
- `src/reaper/scrapers/base.py` — lógica de auto-refresh y `_cookies_differ()`
- `docs/technical/auth.md` — documentación del mecanismo de auto-refresh
- `docs/user/pool.md` — ciclo de vida actualizado con auto-refresh

---

## 0.2.0

### Nuevas funcionalidades

- **Sistema de cuentas autenticadas** (`reaper.auth`):
  - `AccountManager` — gestor central de cuentas con CRUD completo.
  - `AccountRotator` — selección inteligente por score (tasa de éxito + LRU + antigüedad de cookies).
  - `AccountProfile`, `AccountActivity`, `AccountStatus` — modelos de datos serializables.
  - `LocalFileStorage` — persistencia en JSON estructurado por plataforma y account_id.
  - `BaseAccountStorage` — ABC para migración a cualquier backend de BD.
  - `run_login_new_account()` / `run_login_refresh()` — flujo interactivo de login con Playwright.

- **Rotación automática de cuentas** en `BaseScraper._fetch_with_account()`.
  - Selección, inyección de cookies, registro de actividad post-petición.
  - Modo anónimo automático si no hay cuentas disponibles o `account_manager=None`.

- **Refresco automático de cookies**:
  - Contador `requests_since_cookie_refresh` por cuenta.
  - Estado `NEEDS_REFRESH` con warning en logs al alcanzar el umbral.
  - `import_cookies()` resetea el contador y restaura estado `ACTIVE`.

- **CLI de login** (`python -m reaper.auth.login`):
  - Flags: `--platform`, `--username`, `--refresh`, `--account-id`, `--accounts-dir`, `--threshold`.

- **Inyección de cookies en Playwright** (`ContentFetcher.fetch(cookies=...)`):
  - Inyección en el contexto del navegador antes de navegar.

### API modificada

- `scrape()` acepta el parámetro opcional `account_manager`.
- `Reaper.__init__` acepta el parámetro opcional `account_manager`.
- `ScraperConfig` añade campo `account_manager` y propiedad `has_account_manager`.
- `BaseScraper._fetch()` ahora es abstracto y acepta parámetro `cookies`.
- `ContentFetcher.fetch()` acepta parámetro `cookies: list[dict] | None`.

### Compatibilidad

Todos los cambios son **compatibles hacia atrás**. El código existente que no
usa `account_manager` funciona sin modificaciones.

---

## 0.1.0

Versión inicial.

- Scraping de Facebook: posts, reels, vídeos, fotos, grupos, perfiles.
- Scraping de Instagram: posts y reels.
- Soporte de proxies (HTTP, SOCKS5, con autenticación).
- Modo debug con artefactos en disco (HTML, tráfico GraphQL).
- CLI (`reaper <url>`) con todas las opciones.
- API programática: `scrape()`, `Reaper`, `ScraperConfig`.