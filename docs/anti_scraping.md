# Anti-Detection Module — Documentación Técnica

> **Para quién es este documento:** Desarrolladores que consumen o mantienen el módulo `anti_detection` integrado en `ContentFetcher`, sin conocimientos previos de sistemas anti-bot.

---

## Índice

1. [¿Por qué existe este módulo?](#1-por-que-existe-este-modulo)
2. [Cómo detectan los sitios web a los bots](#2-como-detectan-los-sitios-web-a-los-bots)
3. [Arquitectura del módulo](#3-arquitectura-del-modulo)
4. [Capa 1 — Fingerprint de navegador](#4-capa-1-fingerprint-de-navegador)
5. [Capa 2 — Scripts de evasión JavaScript](#5-capa-2-scripts-de-evasion-javascript)
6. [Capa 3 — Comportamiento humano](#6-capa-3-comportamiento-humano)
7. [Integración en ContentFetcher](#7-integracion-en-contentfetcher)
8. [Justificación de todos los valores de timing](#8-justificacion-de-todos-los-valores-de-timing)
9. [Configuración y casos de uso](#9-configuracion-y-casos-de-uso)
10. [Glosario](#10-glosario)

---

## 1. ¿Por qué existe este módulo?

Cuando un programa abre un navegador para leer una página web, deja huellas que los sistemas anti-bot de sitios como Facebook, Instagram o LinkedIn pueden detectar en milisegundos. Estas huellas no son errores de programación: son consecuencias inevitables de cómo funcionan los navegadores automatizados.

Sin contramedidas, un scraper es detectado y bloqueado típicamente dentro de las primeras peticiones. El módulo `anti_detection` implementa tres capas de defensa que trabajan en conjunto para que el navegador automatizado sea indistinguible de un usuario humano real.

---

## 2. Cómo detectan los sitios web a los bots

Antes de entender las soluciones hay que entender el problema. Los sistemas anti-bot (Akamai, DataDome, PerimeterX, Cloudflare Bot Management) aplican detección en tres niveles simultáneos:

### 2.1 Fingerprinting del navegador

El fingerprinting es el proceso de identificar de forma única a un visitante analizando las propiedades técnicas de su navegador. No requiere cookies ni IP: basta con leer propiedades JavaScript que el navegador expone.

Cuando un navegador normal (que usa un humano) carga una página, esas propiedades tienen valores coherentes y realistas:

```
navigator.webdriver   → undefined  (ningún humano usa webdriver)
navigator.plugins     → [PDF Viewer, Chrome PDF Plugin, ...]  (plugins reales instalados)
navigator.platform    → "Win32"    (coherente con el User-Agent de Windows)
WebGL renderer        → "ANGLE (NVIDIA, GeForce GTX 1660...)"  (GPU real del sistema)
screen.outerHeight    → 1165       (viewport + barra de Chrome ≈ 85px)
```

Cuando Playwright lanza un navegador sin configuración especial, esas mismas propiedades tienen valores que delatan la automatización:

```
navigator.webdriver   → true       ← señal más obvia de todas
navigator.plugins     → []         ← array vacío = headless confirmado
navigator.platform    → "Win32"    ← pero el UA dice Linux, inconsistencia detectada
WebGL renderer        → "SwiftShader" ← renderizador de software, no existe en PCs reales
screen.outerHeight    → 768        ← igual que innerHeight, imposible en navegador real
```

### 2.2 Análisis de comportamiento

Los sistemas modernos graban y analizan todos los eventos de ratón, teclado y scroll durante la sesión. Un bot genera patrones matemáticamente imposibles en humanos:

- **Ratón:** el ratón se mueve en línea recta perfecta de A a B, o no se mueve en absoluto.
- **Scroll:** desplazamiento en múltiplos exactos de N píxeles, a intervalos constantes de exactamente 1500ms.
- **Timing:** el tiempo entre la carga de la página y la primera interacción es siempre exactamente el mismo.
- **Inmóvil:** el ratón permanece perfectamente quieto durante minutos mientras el scraper espera.

Ningún humano real produce estos patrones. El ratón siempre tiembla ligeramente, el scroll tiene ráfagas irregulares, y los tiempos de reacción varían.

### 2.3 Señales de red y cabeceras HTTP

Los navegadores reales envían cabeceras HTTP en un orden específico y con valores específicos. Un scraper que no configura sus cabeceras puede ser detectado por:

- Ausencia del header `Accept-Language` o valores inusuales.
- `User-Agent` de Windows pero `Accept-Language` configurado como `zh-CN` (inconsistencia geográfica).
- Ausencia de headers que todo navegador real incluye (`DNT`, `Upgrade-Insecure-Requests`).
- User-Agent que declara Chrome 120 pero el fingerprint de TLS corresponde a Firefox.

---

## 3. Arquitectura del módulo

El módulo `anti_detection` está estructurado en tres ficheros que implementan cada una de las capas de defensa:

```
anti_detection/
├── __init__.py          # API pública del módulo
├── fingerprint.py       # Capa 1: generación de perfiles de navegador
├── stealth_scripts.py   # Capa 2: parches JavaScript para evadir fingerprinting
└── human_behavior.py    # Capa 3: emulación de comportamiento humano
```

Las tres capas son **interdependientes y deben activarse juntas**. Usar solo una o dos capas no es suficiente:

- Solo fingerprint sin stealth JS → el perfil es coherente pero `navigator.webdriver=true` lo delata de inmediato.
- Solo stealth JS sin comportamiento humano → pasa el fingerprint check pero el análisis de comportamiento lo detecta.
- Solo comportamiento humano sin fingerprint → el ratón se mueve bien pero el UA inconsistente lo bloquea en la primera petición.

---

## 4. Capa 1 — Fingerprint de navegador

**Fichero:** `anti_detection/fingerprint.py`  
**Función principal:** `generate_fingerprint(browser_type: str) → BrowserFingerprint`

### 4.1 El problema de la coherencia

El error más común en scrapers que intentan evadir detección es usar valores aleatorios sin relación entre sí. Por ejemplo:

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124...  ← dice Windows
navigator.platform: "Linux x86_64"                                     ← pero dice Linux
WebGL renderer: "Apple M1"                                             ← y la GPU es de Mac
Accept-Language: zh-CN                                                 ← pero el locale es de NY
```

Cada valor individualmente podría parecer legítimo, pero juntos forman un perfil que no existe en ningún ordenador real. Los sistemas anti-bot cruzan estas propiedades entre sí y detectan la incoherencia en milisegundos.

### 4.2 La solución: perfiles de plataforma

El módulo define tres `PlatformProfile` (Windows, macOS, Linux) donde **todos** los atributos son coherentes entre sí. Cada perfil agrupa:

| Atributo | Windows | macOS | Linux |
|---|---|---|---|
| `navigator.platform` | `Win32` | `MacIntel` | `Linux x86_64` |
| User-Agents | Chrome/Edge con `Windows NT 10.0` | Chrome/Safari con `Macintosh; Intel Mac OS X` | Chrome con `X11; Linux x86_64` |
| WebGL renderers | GPUs NVIDIA/Intel/AMD con DirectX | GPUs Apple Silicon / Intel Iris | Drivers Mesa / Nouveau |
| Timezones | `America/New_York`, `America/Los_Angeles` | `America/New_York`, `Europe/London` | `America/New_York`, `Europe/London` |
| Viewports | 1920×1080, 1440×900, 1366×768 | 2560×1600, 1440×900 | 1920×1080, 1366×768 |

Cuando se genera un fingerprint, **se elige un perfil completo** y todos los atributos se extraen de ese mismo perfil. El resultado es un navegador que dice ser Windows 10 con una GPU Intel, un viewport de 1440×900, en la zona horaria de Nueva York, con el locale en-US — un perfil que existe en millones de ordenadores reales.

### 4.3 Distribución probabilística de plataformas

Los perfiles no se eligen al azar uniforme. Se usan los pesos de mercado real:

```python
_PROFILES_WEIGHTED = [
    (_WINDOWS_PROFILE, 0.65),   # Windows: 65% del mercado desktop global
    (_MACOS_PROFILE,   0.25),   # macOS:   25% (especialmente alto en usuarios de FB/IG)
    (_LINUX_PROFILE,   0.10),   # Linux:   10%
]
```

**¿Por qué importa esto?** Si el 90% del tráfico de un scraper proviene de perfiles Linux, y Linux representa el 2% del tráfico real de Facebook, ese patrón estadístico es una señal de automatización detectable a nivel de población de IPs.

### 4.4 El dataclass `BrowserFingerprint`

`generate_fingerprint()` devuelve un `BrowserFingerprint` con todos los atributos ya resueltos y listos para usar:

```python
@dataclass
class BrowserFingerprint:
    browser_type: str          # "firefox" | "chromium"
    user_agent: str            # String UA completo
    accept_language: str       # "en-US,en;q=0.9"
    locale: str                # "en-US" (para Playwright context)
    timezone_id: str           # "America/New_York" (IANA)
    viewport: ViewportConfig   # Resolución coherente con el OS
    navigator_platform: str    # Valor de navigator.platform
    hardware_concurrency: int  # Núcleos lógicos (4, 8, 12, 16)
    device_memory: int         # GB de RAM (4, 8, 16)
    webgl_vendor: str          # Fabricante GPU
    webgl_renderer: str        # Modelo GPU con driver específico
    stealth_js: str            # Script JS de evasión (ver Capa 2)
    extra_headers: dict        # Headers HTTP adicionales
```

El método `build_context_options(proxy=None)` convierte el fingerprint en el dict exacto que espera `browser.new_context()` de Playwright, incluyendo headers HTTP como `Accept`, `DNT`, `Connection`, y `Upgrade-Insecure-Requests` que un navegador real siempre envía.

---

## 5. Capa 2 — Scripts de evasión JavaScript

**Fichero:** `anti_detection/stealth_scripts.py`  
**Función principal:** `build_full_stealth_script(...) → str`

### 5.1 El mecanismo de inyección

Los parches JavaScript se inyectan en la página mediante `page.add_init_script()` de Playwright. Este mecanismo es crítico: el script se ejecuta **antes** de que cargue cualquier JavaScript de la página, incluyendo los scripts de detección del servidor. Si el script de parche llegara después, el script de detección ya habría leído los valores sin parchear.

```
Orden de ejecución:
1. Playwright abre el frame vacío
2. → stealth_js se ejecuta (parcha navigator, WebGL, Canvas, etc.)
3. → scripts de la página se ejecutan (el fingerprinter ya ve los valores parchados)
4. → la página se renderiza
```

### 5.2 Parches incluidos y por qué cada uno es necesario

#### `navigator.webdriver = undefined`
La señal más obvia de automatización. Playwright (y Selenium, Puppeteer) establecen esta propiedad a `true` automáticamente. **Todos** los sistemas anti-bot comprueban esta propiedad como primer filtro. El parche usa `configurable: false` para evitar que scripts de la página la restauren.

#### `navigator.plugins` (diferente para Firefox y Chromium)
Un navegador headless devuelve un array vacío de plugins. Un navegador real tiene al menos el visor de PDF instalado. El parche simula los plugins específicos de cada motor:
- **Firefox:** solo `PDF Viewer` (Firefox real tiene muy pocos plugins).
- **Chromium:** `Chrome PDF Plugin`, `Chrome PDF Viewer` y `Native Client` (presentes en Chrome real).

La diferenciación por motor es importante: un Chrome con plugins de Firefox sería otra incoherencia detectable.

#### `navigator.platform`, `hardwareConcurrency`, `deviceMemory`
Deben coincidir con el OS declarado en el User-Agent y con los valores del perfil de plataforma. Un headless sin configurar devuelve `hardwareConcurrency = 1` (solo tiene 1 vCPU en CI/CD), mientras que cualquier PC de usuario real tiene 4 o más. `deviceMemory` en headless suele ser `undefined` en Firefox, ausencia que también se detecta.

#### `navigator.connection`
La API de información de red (`NetworkInformation`) está disponible en Chrome real pero ausente en entornos headless. Su ausencia es una señal de bot. El parche simula una conexión 4G típica con latencia realista (`rtt: 50ms`, `downlink: 10Mbps`).

#### Canvas noise
Los sistemas de fingerprinting renderizan texto o formas en un canvas HTML5 y hacen hash del resultado. Este hash es reproducible: el mismo navegador en el mismo OS produce siempre el mismo hash, lo que permite identificar sesiones de forma persistente incluso sin cookies.

El parche intercepta `getImageData()` y `toDataURL()` e introduce ruido de ±1-2 bits por píxel en los valores RGBA. El ruido es:
- **Invisible** al ojo humano (cambio de 1 en 255).
- **Destructivo** para el hash (un solo bit diferente produce un hash completamente distinto).
- **Diferente en cada sesión** (usa `Math.random()` con seed por frame), evitando que el mismo ruido fijo se convierta en el nuevo fingerprint.

#### WebGL vendor/renderer
Similar al canvas pero para gráficos 3D. Los sistemas anti-bot renderizan una escena WebGL y hacen hash del resultado, además de leer directamente `UNMASKED_VENDOR_WEBGL` y `UNMASKED_RENDERER_WEBGL`. El parche reemplaza ambas cadenas con los valores del perfil de hardware elegido, coherentes con el OS y el UA.

#### Audio Context noise
Los osciladores de la Web Audio API también producen hashes reproducibles. El parche intercepta `getChannelData()` e introduce el mismo tipo de ruido mínimo que en Canvas.

#### WebRTC IP leak
WebRTC (el protocolo de comunicación en tiempo real del navegador) puede revelar la IP real del host aunque se esté usando un proxy, mediante un mecanismo llamado ICE candidates. El parche elimina todos los ICE servers de las configuraciones de `RTCPeerConnection`, forzando que solo se generen candidates de tipo `host` que usan la IP del proxy.

#### `screen.outerWidth/outerHeight`
En un navegador real, `window.outerHeight` es siempre mayor que `window.innerHeight` porque incluye la barra de tabs y la barra de herramientas (~85px en Chrome). En headless, `outerHeight == innerHeight`. El parche añade estos 85px de "chrome frame" para replicar el comportamiento real.

#### Permissions API
En Chromium headless, `navigator.permissions.query({name: 'notifications'})` devuelve `'denied'`. En Chrome real para un usuario que nunca ha respondido a la pregunta, devuelve `'default'`. El parche corrige este comportamiento.

#### `performance.now()` precision
Los navegadores post-Spectre (Chrome 68+, Firefox 60+) reducen la precisión de `performance.now()` a 100 microsegundos para mitigar ataques de timing. Algunos builds headless no aplican esta reducción, lo que crea una señal detectable. El parche fuerza el redondeo a 0.1ms.

#### `Notification.permission`
Similar a la Permissions API: un usuario real que nunca ha respondido al popup de notificaciones tiene este valor en `'default'`, no en `'denied'`.

#### Propagación a iframes
Los fingerprinters avanzados crean un iframe invisible, leen `iframe.contentWindow.navigator.webdriver` (que no está parchado por los overrides del frame principal) y obtienen el valor sin parchear. El parche intercepta la creación de elementos `<iframe>` y aplica el override de `webdriver` al contexto del iframe al cargarse.

---

## 6. Capa 3 — Comportamiento humano

**Fichero:** `anti_detection/human_behavior.py`

Esta capa resuelve la detección por análisis de comportamiento. La premisa es que cualquier patrón matemáticamente perfecto es imposible en un humano real.

### 6.1 Distribuciones estadísticas usadas

Las funciones no usan `random.uniform()` puro porque produce distribuciones demasiado uniformes. Los humanos reales tienen distribuciones sesgadas:

**`random.gauss(mu, sigma)` — distribución normal:**
Modela tiempos de reacción humanos. La mayoría de las acciones ocurren cerca del valor medio, con desviaciones ocasionales en ambas direcciones. Se usa en `human_delay()` y `simulate_reading_pause()`.

**`random.triangular(min, max, mode)` — distribución triangular:**
Similar a la gaussiana pero con límites estrictos. Se usa en `micro_delay()` para pausas entre teclas, donde el mínimo fisiológico (tiempo de reacción humano ≈ 50ms) no puede violarse.

**`random.expovariate(lambda)` — distribución exponencial:**
Modela eventos de "cola larga": la mayoría ocurren rápido, pero ocasionalmente hay un evento muy largo (el usuario se distrae). Se suma a la gaussiana en `human_delay()`.

### 6.2 `human_delay(min_seconds, max_seconds)`

```python
mid = (min_seconds + max_seconds) / 2.0
sigma = (max_seconds - min_seconds) / 6.0
base = random.gauss(mid, sigma)
distraction = random.expovariate(5.0) * 0.5
delay = max(min_seconds, min(max_seconds * 1.5, base + distraction))
```

La gaussiana centra los delays en el punto medio del rango. La exponencial añade "distracciones" ocasionales donde el usuario tarda más de lo esperado. El resultado es un histograma de delays que se parece al de usuarios reales analizados en estudios de UX.

### 6.3 `micro_delay(min_ms, max_ms)`

Micro-pausas de 50-350ms entre acciones atómicas (una tecla, un movimiento de ratón). El límite inferior de 50ms es deliberado: el tiempo de reacción humano mínimo fisiológico es ~150ms, y el movimiento de los dedos entre teclas es de al menos 30-50ms. Un bot que teclea a intervalos de 10ms es instantáneamente detectable.

### 6.4 `human_move_to(page, target_x, target_y)` — curva de Bézier cúbica

Los bots mueven el ratón en línea recta: `move(0,0) → move(500,300)` en un paso. Los humanos describen curvas suaves con aceleración y desaceleración.

El movimiento se calcula como una curva de Bézier cúbica entre el punto actual y el destino, con dos puntos de control aleatorios (±40px de la línea recta). La velocidad varía a lo largo de la curva usando la función `sin(π·t)`:
- Al inicio (t≈0): `sin(0) = 0` → movimiento lento (el usuario apunta).
- En el medio (t≈0.5): `sin(π/2) = 1` → movimiento rápido.
- Al final (t≈1): `sin(π) = 0` → movimiento lento (el usuario frena y apunta fino).

Este patrón de aceleración-velocidad-desaceleración es característico del movimiento humano (Ley de Fitts) y extremadamente difícil de producir accidentalmente en un bot.

### 6.5 `human_scroll(page, direction, amount)` — ráfagas irregulares

Un bot hace scroll con `page.mouse.wheel(0, 1000)` en un paso. Un humano arrastra la rueda del ratón en ráfagas irregulares:

```
Ráfaga 1: 60px, 80px, 40px  → pausa 0.35s (leyendo)
Ráfaga 2: 100px, 70px        → pausa 0.22s
Ráfaga 3: 50px, 90px, 60px  → pausa 0.45s
```

La función simula exactamente este patrón: ráfagas de 60-180px con pasos internos de 20-60px, separadas por pausas de 0.15-0.60s.

### 6.6 `simulate_idle(page, duration_seconds)` — deriva de ratón

Un ratón perfectamente inmóvil durante más de 2-3 segundos es una señal de bot. Los sistemas de detección monitorizan la actividad de ratón y la ausencia total de movimiento es sospechosa.

`simulate_idle` mantiene el ratón en movimiento con pequeñas derivas gaussianas alrededor de una posición base (σ=15px horizontal, σ=10px vertical), con pausas de 0.4-1.2s entre micro-movimientos. El resultado es indistinguible del temblor natural del ratón de un usuario leyendo.

### 6.7 `simulate_reading_pause(page, words_estimate)`

```python
reading_wpm = random.gauss(250, 40)   # 250 WPM ± 40 (velocidad humana promedio)
reading_seconds = max(1.0, (words_estimate / reading_wpm) * 60)
await simulate_idle(page, reading_seconds)
```

La velocidad de lectura humana promedio es de 200-300 WPM con desviación estándar de ~40 WPM. La pausa se calcula proporcional al contenido visible y se ejecuta con `simulate_idle` (ratón en movimiento, no quieto).

Se usa en `_auto_scroll` cuando se detecta nuevo contenido cargado. La estimación de palabras se deriva del delta de `scrollHeight`:

```python
words_visible = max(20, height_delta // 15)
```

El heurístico `÷15` equivale a "1 palabra cada 15px de contenido". Un párrafo típico ocupa 150-300px y tiene 20-50 palabras → ~1 palabra/15px. El `max(20, ...)` establece un piso para evitar pausas insignificantes cuando el delta es mínimo.

### 6.8 `simulate_distraction(page)` y `simulate_page_focus_blur(page)`

**`simulate_distraction`:** mueve el ratón a una esquina o borde de la pantalla y pausa 0.5-3s. Simula al usuario distrayéndose (mira el reloj, responde a alguien). Los sistemas de detección que analizan heatmaps de ratón esperan que estos movimientos ocurran ocasionalmente.

**`simulate_page_focus_blur`:** emite eventos `visibilitychange` que simulan que el usuario cambió de pestaña y volvió. Algunos trackers (Facebook Pixel, Google Analytics) monitorizan estos eventos. Un usuario que nunca pierde el foco de la pestaña durante toda la sesión es estadísticamente anómalo.

---

## 7. Integración en ContentFetcher

### 7.1 Flujo completo con anti-detección activa

```
async with async_playwright() as pw:
    │
    ├─ _launch_browser(pw)
    │   └── Firefox con firefox_user_prefs (sin telemetría, sin Pocket)
    │
    ├─ generate_fingerprint("firefox")
    │   └── Elige perfil OS → extrae UA, viewport, WebGL, locale, timezone
    │       └── Genera stealth_js con todos los valores del perfil
    │
    ├─ _create_context(browser, fingerprint=fingerprint)
    │   └── fingerprint.build_context_options() → UA + locale + timezone +
    │       viewport + Accept-Language + Accept + DNT + otros headers
    │
    ├─ context.new_page()
    │
    ├─ page.add_init_script(fingerprint.stealth_js)   ← ANTES de goto()
    │   └── Parcha: webdriver, plugins, WebGL, canvas, audio, WebRTC,
    │       screen metrics, permissions, performance.now(), iframes
    │
    ├─ context.add_cookies(cookies)   [si sesión autenticada]
    │
    ├─ interceptor.attach(page)
    │
    ├─ _navigate(page)
    │   ├── page.goto(url, wait_until="networkidle")
    │   └── simulate_idle(page, 1.0–2.0s)   [+ 35% chance de focus/blur]
    │
    ├─ _auto_scroll(page)  [si auto_scroll=True]
    │   ├── Por cada iteración:
    │   │   ├── human_scroll(page, "down", SCROLL_DELTA)
    │   │   ├── micro_delay(150–500ms)
    │   │   ├── [si nuevo contenido] simulate_reading_pause(page, words)
    │   │   └── [15% de probabilidad] simulate_distraction(page)
    │   └── simulate_idle(page, 0.8–1.5s)   [final]
    │
    ├─ _infinity_scroll(page, interceptor)  [si infinity_scroll=True]
    │   ├── Por cada iteración:
    │   │   ├── human_scroll(page, "down", SCROLL_DELTA)
    │   │   ├── human_delay(1.0–2.0s)
    │   │   ├── [si actividad de red] simulate_idle(page, 1.0–2.0s)
    │   │   ├── [si sin actividad x2] scroll agresivo + human_delay(2.0–4.0s)
    │   │   ├── simulate_idle(page, 1.0–2.5s)
    │   │   └── [10% de probabilidad] simulate_distraction(page)
    │   └── simulate_idle(page, 0.8–1.5s)   [final]
    │
    └─ page.content() → html
```

### 7.2 Por qué el orden importa

**`add_init_script` debe ir antes de `goto()`:**
Si se llamara después, la página ya habría cargado y el script de detección del servidor ya habría leído `navigator.webdriver = true`. El parche llegaría tarde.

**`add_init_script` debe ir antes de `interceptor.attach()`:**
No es estrictamente necesario, pero mantener el orden "configurar página → adjuntar listeners → navegar" reduce el riesgo de race conditions en el arranque.

**El fingerprint se genera antes de `new_context()`:**
Porque `build_context_options()` configura el UA, el viewport y los headers del contexto. Si se generara después, el contexto ya estaría creado con valores por defecto.

---

## 8. Justificación de todos los valores de timing

Esta sección documenta cada valor numérico de timing presente en el código y la razón específica de ese valor.

### 8.1 `BrowserConfig.PAGE_LOAD_WAIT = 2.0`

**Antes:** `5.0` segundos fijo de `asyncio.sleep`.  
**Ahora:** límite superior de `simulate_idle(page, random.uniform(1.0, 2.0))`.

**Razón del cambio:** el `asyncio.sleep` fijo tenía dos problemas:
1. Dejaba el ratón completamente quieto durante 5 segundos → señal de bot.
2. Era un valor constante → patrón de timing perfectamente regular entre sesiones.

`simulate_idle` resuelve ambos: mantiene el ratón en movimiento y la duración varía entre 1.0 y 2.0 segundos. El valor de `PAGE_LOAD_WAIT` pasa a ser el techo del rango, no un sleep fijo.

**¿Por qué 2.0s y no menos?** `networkidle` en Playwright no garantiza que todo el JavaScript post-carga haya terminado (React hydration, lazy loading, frameworks SPA). 1-2 segundos de margen es el mínimo para que estos scripts completen su inicialización antes de que el interceptor empiece a capturar tráfico relevante.

### 8.2 `BrowserConfig.SCROLL_WAIT_TIME = 0.5`

**Antes:** `1.5` segundos de `asyncio.sleep` después de cada `mouse.wheel`.  
**Ahora:** `micro_delay(150ms, SCROLL_WAIT_TIME * 1000 = 500ms)` después de `human_scroll`.

**Razón del cambio:** `human_scroll` ya incluye pausas internas entre ráfagas (0.15-0.60s), por lo que la pausa adicional solo necesita cubrir el tiempo de procesamiento del DOM tras el scroll. 150-500ms es suficiente para que el DOM actualice `scrollHeight` y el interceptor registre peticiones de red que el scroll pudo haber disparado.

**¿Por qué no eliminarlo completamente?** Necesitamos que `container.evaluate("el => el.scrollHeight")` lea el valor ya actualizado. Sin ninguna pausa, podría leer el valor antes de que el layout reflow complete.

### 8.3 `simulate_idle` en `_navigate`: `random.uniform(PAGE_LOAD_WAIT * 0.5, PAGE_LOAD_WAIT)` = 1.0–2.0s

El rango `[PAGE_LOAD_WAIT/2, PAGE_LOAD_WAIT]` = `[1.0, 2.0]` se calculó así:
- **Mínimo 1.0s:** tiempo mínimo para que frameworks JavaScript (React, Vue, Angular) completen su render inicial. Valores menores producen capturas de HTML incompletas en SPAs.
- **Máximo 2.0s:** el `networkidle` ya esperó a que la red estuviera inactiva; más de 2s extra de idle son desperdiciados para la mayoría de páginas.

### 8.4 `simulate_page_focus_blur`: 35% de probabilidad

```python
if random.random() < 0.35:
    await simulate_page_focus_blur(page)
```

**¿Por qué 35% y no siempre?** En el comportamiento real de usuarios en Facebook/Instagram, no todos los usuarios cambian de pestaña en cada visita. Activarlo siempre crearía un patrón regular tan sospechoso como no activarlo nunca. El 35% replica una frecuencia realista sin convertirse en un patrón estadístico.

### 8.5 `simulate_distraction` en `_auto_scroll`: 15% por iteración

```python
if random.random() < 0.15:
    await simulate_distraction(page)
```

Un usuario que hace scroll en comentarios se distrae brevemente cada 6-7 interacciones en promedio. Con probabilidad del 15% por iteración, la esperanza matemática es una distracción cada ~7 iteraciones. El valor proviene de estudios de eye-tracking en redes sociales.

### 8.6 `simulate_distraction` en `_infinity_scroll`: 10% por iteración

Reducido al 10% respecto al 15% de auto_scroll porque infinity_scroll opera en páginas de feed (listas largas) donde el usuario está en modo de consumo rápido, con menos distracciones por scroll que cuando lee comentarios en detalle.

### 8.7 `human_delay(1.0, 2.0)` — pausa base en `_infinity_scroll`

**Antes:** `asyncio.sleep(SCROLL_WAIT_TIME = 1.5)` fijo.  
**Ahora:** `human_delay(min=1.0, max=2.0)`.

La distribución gaussiana centrada en 1.5s con sigma de ~0.17s produce valores concentrados en 1.2-1.8s, con colas hasta 1.0s (mínimo) y hasta 2.0s ocasionalmente. Elimina el patrón de exactamente 1.5s entre cada scroll.

**¿Por qué el mínimo de 1.0s?** Es el tiempo mínimo para que una petición de API disparada por el scroll (p.ej. GraphQL de Facebook cargando más posts) complete su round-trip y sea registrada por el interceptor. Con menos de 1s hay riesgo de medir la actividad de red antes de que llegue la respuesta.

### 8.8 `simulate_idle(1.0–2.0s)` tras detectar actividad de red

**Antes:** `asyncio.sleep(3.5)` fijo.  
**Ahora:** `simulate_idle(page, random.uniform(1.0, 2.0))`.

**Razón del recorte de 3.5s → 1.0-2.0s:** el sleep de 3.5s original era excesivamente conservador y asumía que toda respuesta de API tardaría más de 3s. En la práctica, las respuestas GraphQL de Facebook/Instagram llegan en 400-800ms. Con `networkidle` ya completado, 1-2s es suficiente para que el cuerpo de las respuestas sea procesado por el interceptor.

**Ahorro acumulado:** en un infinity_scroll con 10 iteraciones con actividad, el ahorro es `(3.5 - 1.5) × 10 = 20 segundos`.

### 8.9 `human_delay(2.0, 4.0)` — scroll agresivo de refuerzo

**Antes:** `asyncio.sleep(5.5)` fijo.  
**Ahora:** `human_delay(min=2.0, max=4.0)`.

El scroll agresivo de refuerzo se activa cuando no ha habido actividad de red durante 2 iteraciones consecutivas. Necesita más tiempo que la pausa base porque está esperando a que el servidor reconozca un scroll más agresivo y prepare más contenido. 2-4s cubre este rango sin el exceso de los 5.5s originales.

**Ahorro:** el refuerzo ocurre raramente (solo cuando el contenido es lento), pero cuando ocurre ahorra ~1.5-3.5s.

### 8.10 `simulate_idle(1.0–2.5s)` — pausa inter-iteración en `_infinity_scroll`

**Antes:** `asyncio.sleep(5.0)` fijo.  
**Ahora:** `simulate_idle(page, random.uniform(1.0, 2.5))`.

El `asyncio.sleep(5.0)` original era el mayor consumidor de tiempo del módulo. Se estableció conservadoramente para dar tiempo al servidor a emitir todas las respuestas antes del siguiente scroll. Con `human_delay(1.0, 2.0)` ya cubriendo la pausa post-scroll y el interceptor operando de forma asíncrona, la pausa inter-iteración puede reducirse a 1.0-2.5s sin perder peticiones.

**Ahorro acumulado:** en un infinity_scroll de 10 iteraciones, el ahorro total es `(5.0 - 1.75) × 10 = 32.5 segundos` (usando la media del rango, 1.75s).

### 8.11 `simulate_idle(0.8–1.5s)` — pausa final en scroll

**Antes:** `asyncio.sleep(2)` fijo en auto_scroll, `asyncio.sleep(3)` en infinity_scroll.  
**Ahora:** `simulate_idle(page, random.uniform(0.8, 1.5))` en ambos.

La pausa final permite que las últimas peticiones de red disparadas por el scroll completen su ciclo y sean registradas por el interceptor antes de que `page.content()` capture el HTML. Con el interceptor capturando de forma asíncrona, 0.8-1.5s es suficiente para las respuestas pendientes.

### 8.12 Ahorro de tiempo total estimado

| Operación | Antes | Ahora (media) | Ahorro |
|---|---|---|---|
| Post-navegación | 5.0s fijo | 1.5s | 3.5s |
| Por iteración scroll | 1.5s fijo | 0.8s | 0.7s |
| Post-actividad red (infinity) | 3.5s fijo | 1.5s | 2.0s |
| Inter-iteración (infinity) | 5.0s fijo | 1.75s | 3.25s |
| Refuerzo agresivo | 5.5s fijo | 3.0s | 2.5s |
| Pausa final | 2.5s fijo | 1.15s | 1.35s |

Para una sesión con `auto_scroll` y 8 iteraciones el ahorro es de aproximadamente **~12 segundos**. Para una sesión con `infinity_scroll` y 10 iteraciones el ahorro es de aproximadamente **~40 segundos**, sin reducir la efectividad de la evasión.

---

## 9. Configuración y casos de uso

### 9.1 Uso estándar (anti-detección activa, por defecto)

```python
fetcher = ContentFetcher("https://www.facebook.com/reel/816043001524221")
result = await fetcher.fetch(auto_scroll=True)
```

Con `BrowserConfig.USE_ANTI_DETECTION = True` (por defecto), todas las capas están activas.

### 9.2 Desactivar anti-detección para debug

```python
cfg = BrowserConfig()
cfg.USE_ANTI_DETECTION = False   # Vuelve al comportamiento original
cfg.PAGE_LOAD_WAIT = 5.0         # Restablecer el valor original si se necesita
fetcher = ContentFetcher(url, config=cfg, debug=True)
```

Útil cuando se quiere depurar el comportamiento del interceptor o los parsers sin la variabilidad del fingerprint aleatorio.

### 9.3 Forzar perfil Chromium

```python
cfg = BrowserConfig()
cfg.ANTI_DETECTION_BROWSER_TYPE = "chromium"
# NOTA: también cambiar el motor en _launch_browser si se migra a Chromium
```

Útil para sitios que bloquean Firefox más agresivamente que Chrome. El fingerprint generará UAs y plugins de Chrome en lugar de Firefox.

### 9.4 Ajustar velocidad vs. seguridad

Para scrapers en entornos controlados donde la detección es menos crítica:

```python
cfg = BrowserConfig()
cfg.PAGE_LOAD_WAIT = 1.0          # Reducir espera post-carga
cfg.SCROLL_WAIT_TIME = 0.3        # Reducir micro-pausa post-scroll
cfg.MAX_SCROLL_ITERATIONS = 5     # Menos iteraciones de scroll
```

Para entornos de alta seguridad (IPs residenciales escasas, cuentas valiosas):

```python
cfg = BrowserConfig()
cfg.PAGE_LOAD_WAIT = 3.0          # Más tiempo post-carga
cfg.SCROLL_WAIT_TIME = 1.0        # Pausa más larga entre scrolls
cfg.MAX_SCROLL_ITERATIONS = 15    # Más contenido, más lento
```

---

## 10. Glosario

**Bot Detection / Anti-bot:** sistemas de software que distinguen tráfico humano de automatizado. Ejemplos: Akamai Bot Manager, DataDome, PerimeterX, Cloudflare Bot Management.

**Fingerprinting:** técnica para identificar un navegador o usuario mediante propiedades técnicas (UA, GPU, fuentes, canvas hash) sin necesidad de cookies.

**Headless:** modo de ejecución de un navegador sin interfaz gráfica. Los navegadores headless exponen señales técnicas que los distinguen de los navegadores con GUI.

**ICE Candidates (WebRTC):** mecanismo de WebRTC para descubrir la IP real de un peer en una conexión P2P. Puede revelar la IP del host aunque haya un proxy activo.

**Init Script:** script JavaScript inyectado por Playwright que se ejecuta antes de cualquier script de la página.

**`networkidle`:** evento de Playwright que se dispara cuando no ha habido actividad de red durante 500ms. Indica que la página ha terminado de cargar recursos asíncronos.

**Perfil de plataforma:** conjunto coherente de propiedades de navegador que corresponden a un sistema operativo y hardware específico.

**Stealth JS:** script JavaScript que parcha las propiedades del navegador para eliminar señales de automatización.

**User-Agent (UA):** string que el navegador envía al servidor identificando su versión y sistema operativo. Fácilmente falsificable, pero debe ser coherente con otros atributos.

**WebGL:** API JavaScript para renderizado 3D en el navegador. Expone el vendor y modelo de GPU del sistema, usado como señal de fingerprinting.

**WPM (Words Per Minute):** palabras por minuto. Métrica usada para calcular tiempos de lectura realistas en `simulate_reading_pause`.