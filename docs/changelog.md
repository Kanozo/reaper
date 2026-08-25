# Changelog

## 0.4.0

### Acciones de escritura — fiabilidad del flujo de posts

- **Navegación al perfil propio para publicar en el muro**: `create_post()`
  sin `group` ahora deriva la URL del perfil a partir del cookie `c_user` de
  la cuenta (`https://www.facebook.com/profile.php?id={c_user}`) y navega
  ahí, en lugar de al home. `resolve_profile_url()` en `reaper.actions.utils`.
- **Apertura del composer**: la UI de escritorio muestra un trigger
  ("¿Qué estás pensando?") que abre el diálogo con el campo de texto. Se añade
  `COMPOSER_TRIGGER` / `GROUP_COMPOSER_TRIGGER` y el helper `_open_composer()`
  en `reaper.actions.facebook.flows`: si el campo no está visible, se hace
  clic en el trigger antes de escribir.
- **Botones de envío en español**: `POST_SUBMIT` y el nuevo `GROUP_POST_SUBMIT`
  incluyen variantes `aria-label='Publicar'` además de `Post`.
- **Selectores ampliados**: `COMPOSER_INPUT` añade `div[data-contents='true']`
  y `PHOTO_INPUT` el input de archivos del composer de escritorio.
- **Confirmación de post en el muro sin URL nueva**: en muchos casos Facebook
  inserta el post en el feed sin navegar a una URL `story.php`/`permalink.php`.
  `_confirm_new_post()` con `text=` confirma por el texto presente en el DOM y
  el diálogo del composer **cerrado**; `post_id` puede quedar en `None` y
  `post_url` en la URL de la página actual.
- **Timeouts generosos para redes lentas**: `COMPOSER_OPEN_TIMEOUT` (120 s) para
  que el diálogo renderice tras abrirlo y `SUBMIT_TIMEOUT` (60 s) para esperar
  que el botón **Publicar** se habilite (quita `aria-disabled`). No dependen de
  `confirm_timeout`.
- **Composer falso del perfil**: el perfil de escritorio tiene un
  `contenteditable` colapsado **siempre visible** que no es el diálogo real.
  `_open_composer()` solo lo considera abierto cuando coexisten el campo
  editable y el botón Publicar.
- **`post_url` canónico y `text` persistido**: el resultado guardado ahora
  registra el `text` del post (antes quedaba `null`). Y el `post_url` ya no es
  el primer enlace "nuevo" del DOM (que podía ser un post de grupo o una
  notificación ajena): con `text=` se busca el permalink **junto al texto** en
  el feed, se descartan los enlaces de grupos/otras personas (filtrando por el
  `id=` del `c_user` en curso) y se limpian los parámetros de tracking
  (`notif_*`, `ref=`). Si no se encuentra un permalink del propio usuario, se
  guarda la URL de la página actual con `post_id=None`.

### Correcciones

- **`find_config_file()` capturaba el cwd en el import**: `DEFAULT_CONFIG_PATHS`
  se computaba al cargar el módulo, así que
  `AccountManager()` / `build_storage()` podían ignorar un `reaper.toml`
  creado o cambiado después del import, y los tests que aislaban el cwd fallaban.
  Ahora las rutas se evalúan en cada búsqueda.

### Compatibilidad

- Cambios aditivos. La navegación al perfil deriva `c_user`; si la cuenta no
  tiene ese cookie (o el perfil no se puede resolver), cae al home como antes.

---

## 0.3.0

### Nuevas funcionalidades

- **Acciones de escritura autenticadas** (`reaper.actions`):
  - `ActionManager` — orquestador que ejecuta acciones contra una sesión de
    Facebook autenticada y persiste cada resultado (`ActionResult`).
  - `create_post()` — publicar texto y/o imágenes en el muro de la cuenta o
    directamente dentro de un grupo (`group` por ID o URL).
  - `share_post()` — compartir una publicación existente hacia un grupo
    (búsqueda por `group_name` o `group`).
  - `comment()` — responder con texto a una publicación.
  - `like()` — reaccionar con "Me gusta" a una publicación.
  - Confirmación por DOM (`confirm_timeout`) y estado `status="ok"` / `"error"`.
  - Selección explícita de `account` por `account_id`, `username` o `c_user`;
    `None` = rotación automática del `AccountManager`.
  - `ActionResult` se persiste en el mismo backend que las cuentas
    (local / PostgreSQL / MongoDB) pero en un recurso separado:
    `actions_dir` / `actions_table` / `actions_collection` (defaults
    `data/actions`, `actions`, `actions`).

- **`SessionActor` / `SessionHandle`** (`reaper.actions.actor`):
  - Lanzamiento de sesiones autenticadas con Camoufox, inyección de cookies y
    exportación de cookies actualizadas post-ejecución.

- **Storage de acciones** (`reaper.actions.storage`):
  - `BaseActionStorage` (ABC), `LocalActionStorage`, `PostgresActionStorage`,
    `MongoActionStorage` y `build_action_storage()`.
  - Interfaz: `add`, `get`, `list_all`, `delete`, `count`.

- **CLI de acciones** (`reaper action <verb>`):
  - Subcomandos `post`, `share`, `comment` y `like`, con flags comunes
    `--account`, `--no-headless`, `--proxy`, `--proxy-user`, `--proxy-pass`,
    `--confirm-timeout`, `--output`, `--indent` y `--debug`.
  - Códigos de salida: `0` confirmada, `1` ejecutada sin confirmar
    (`status="error"`), `5` error de entrada/sesión (`ActionError`).

### API modificada

- `reaper` y `reaper.actions` exportan: `ActionManager`, `ActionType`,
  `ActionResult`, `ActionError`, `SessionActor`, `SessionHandle`,
  `BaseActionStorage`, los tres backends de acciones y `build_action_storage`.

### Compatibilidad

- Los cambios son **aditivos** y no rompen la API de scraping/autenticación
  existente. El storage de acciones requiere que el `AccountManager` tenga al
  menos una cuenta de Facebook autenticada con cookies válidas.

---

## 0.2.4

### Nuevas funcionalidades

- **Almacenamiento configurable** (`reaper.auth.storage`):
  - Dos backends de base de datos además del local en JSON:
    `PostgresStorage` (asyncpg) y `MongoStorage` (motor).
  - Fichero de configuración `reaper.toml` con precedencia:
    ruta explícita → `REAPER_CONFIG` → `./reaper.toml` →
    `~/.config/reaper/reaper.toml` → `/etc/reaper/reaper.toml`.
  - `load_storage_config()`, `find_config_file()` y `build_storage()` para
    construir el backend indicado sin instanciarlo manualmente.
  - La elección de backend es **todo o nada**: perfiles y cookies viven en el
    mismo backend; no hay mezcla local/BD.
  - `AccountManager()` respeta automáticamente la configuración del fichero y
    expone `save_cookies()`, `load_cookies()` y `delete_cookies()` con
    implementación por backend.

- **Selección explícita de cuenta**:
  - `AccountManager.resolve_account()` localiza una cuenta por `account_id`
    (UUID4), `username` o ID de usuario de la plataforma (`c_user` / `ds_user_id`).
  - `get_account_for_request(preferred=...)` permite fijar la cuenta para una
    petición sin desactivar el rotador.
  - `scrape(account=...)`, `Reaper.scrape(account=...)` y la CLI
    `reaper <url> --account <username>` propagan la preferencia.
  - Resolución de ID de usuario solo para valores numéricos de 5+ dígitos,
    evitando colisiones con usernames cortos.

### API modificada

- `AccountManager` añade `resolve_account()`, `save_cookies()`,
  `load_cookies()`, `delete_cookies()`.
- `scrape()` y `Reaper.scrape()` añaden el parámetro opcional `account`.
- `ScraperConfig` añade `preferred_account`.
- CLI: nuevo flag `--account`.

### Compatibilidad

Todos los cambios son **compatibles hacia atrás**. Sin `reaper.toml` el
comportamiento es idéntico a 0.2.3 (backend `local` por defecto).

---

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