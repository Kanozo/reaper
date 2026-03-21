# Reaper — Manual de Usuario

> Librería Python para extraer datos de publicaciones, perfiles, grupos y más en **Facebook** e **Instagram**.
> Versión 0.1.0 — Python 3.11+

---

## Tabla de Contenidos

1. [¿Qué es Reaper?](#1-qué-es-reaper)
2. [Instalación](#2-instalación)
3. [Uso desde la línea de comandos (CLI)](#3-uso-desde-la-línea-de-comandos-cli)
4. [Uso desde Python](#4-uso-desde-python)
5. [Opciones de configuración](#5-opciones-de-configuración)
6. [Tipos de contenido soportados](#6-tipos-de-contenido-soportados)
7. [Entendiendo el resultado](#7-entendiendo-el-resultado)
8. [Manejo de errores](#8-manejo-de-errores)
9. [Uso con proxy](#9-uso-con-proxy)
10. [Modo debug](#10-modo-debug)
11. [Ejemplos completos por caso de uso](#11-ejemplos-completos-por-caso-de-uso)
12. [Preguntas frecuentes](#12-preguntas-frecuentes)
13. [Problemas comunes y soluciones](#13-problemas-comunes-y-soluciones)

---

## 1. ¿Qué es Reaper?

Reaper extrae datos estructurados de contenido público de Facebook e Instagram: texto, autor, reacciones, comentarios, imágenes, vídeos y más. El resultado se devuelve como un diccionario Python (o JSON desde la CLI) listo para procesar.

**Lo que puede hacer:**
- Extraer datos de posts, reels, vídeos, fotos, grupos y perfiles de Facebook.
- Extraer datos de posts y reels de Instagram.
- Devolver el resultado como JSON en la terminal o como dict en Python.
- Trabajar con proxies para anonimidad o rotación de IPs.
- Guardar artefactos de debug (HTML, tráfico de red) para diagnóstico.

**Lo que NO hace:**
- No accede a contenido privado ni requiere credenciales.
- No evita bloqueos de reCAPTCHA ni CAPTCHAs visuales.
- No garantiza funcionamiento si Facebook/Instagram cambia su estructura.

---

## 2. Instalación

### Requisitos previos

- Python 3.11 o superior
- pip 23+

### Instalar Reaper

```bash
# Desde el directorio del proyecto (donde está pyproject.toml)
cd reaper/

# Instalación estándar
pip install .

# O en modo desarrollo (cambios en el código tienen efecto inmediato)
pip install -e .
```

### Instalar el navegador Firefox

Reaper usa Firefox para renderizar las páginas con JavaScript. Solo necesitas hacer esto una vez:

```bash
playwright install firefox
```

### Verificar la instalación

```bash
# Debe imprimir la versión
reaper --version

# Debe imprimir "OK"
python -c "from reaper import scrape; print('OK')"
```

### Usar un entorno virtual (recomendado)

```bash
python3.11 -m venv .venv
source .venv/bin/activate      # Linux / macOS
# .venv\Scripts\activate       # Windows

pip install .
playwright install firefox
```

---

## 3. Uso desde la línea de comandos (CLI)

El comando `reaper` recibe una URL y muestra el resultado en formato JSON.

### Sintaxis

```
reaper URL [opciones]
```

### Ejemplos básicos

```bash
# Extraer un reel de Facebook
reaper https://www.facebook.com/reel/816043001524221

# Extraer un post de Instagram
reaper https://www.instagram.com/p/abc123XYZ/

# Extraer un grupo de Facebook
reaper https://www.facebook.com/groups/123456789

# Extraer un perfil de Facebook
reaper https://www.facebook.com/zuck
```

### Guardar el resultado en un archivo

```bash
# Guardar como JSON con indentación de 4 espacios
reaper https://www.facebook.com/reel/123456 --output resultado.json --indent 4

# Guardar en una subcarpeta (se crea automáticamente)
reaper https://www.instagram.com/p/abc123 --output datos/instagram/post.json
```

### Opciones del navegador

```bash
# Ver el navegador mientras trabaja (útil para diagnóstico)
reaper https://www.facebook.com/reel/123 --no-headless

# Capturar una imagen de la página durante el scraping
reaper https://www.facebook.com/reel/123 --screenshot
```

### Opciones de scroll

```bash
# Desactivar el scroll automático (más rápido, menos datos)
reaper https://www.facebook.com/groups/123 --no-scroll

# Scroll continuo para feeds muy largos (grupos, búsquedas)
reaper https://www.facebook.com/groups/123 --infinity-scroll
```

### Activar modo debug

```bash
# Ver todos los logs y guardar artefactos en data/debug_artifacts/
reaper https://www.facebook.com/reel/123 --debug
```

### Todas las opciones disponibles

```
Argumentos:
  URL                       URL de Facebook o Instagram a scrapear

Opciones del navegador:
  --no-headless             Mostrar la ventana del navegador
  --screenshot              Capturar screenshot durante el scraping

Opciones de scroll:
  --no-scroll               Desactivar el scroll automático
  --infinity-scroll         Scroll continuo por tráfico de red

Opciones de proxy:
  --proxy URL               Servidor proxy (ej. http://ip:8080)
  --proxy-user USUARIO      Usuario del proxy
  --proxy-pass CONTRASEÑA   Contraseña del proxy

Opciones de salida:
  --output FICHERO, -o      Guardar resultado en fichero JSON
  --indent N                Espacios de indentación (defecto: 2)

Opciones generales:
  --debug                   Logging detallado + artefactos
  --version                 Mostrar versión y salir
  --help                    Mostrar ayuda y salir
```

### Códigos de salida

| Código | Significado |
|---|---|
| `0` | Éxito |
| `2` | URL inválida |
| `3` | Plataforma no soportada |
| `4` | Error durante el scraping |

### Redirigir la salida

```bash
# Procesar con jq (herramienta de línea de comandos para JSON)
reaper https://www.facebook.com/reel/123 | jq '.author.name'
reaper https://www.facebook.com/reel/123 | jq '.reaction_count'

# Guardar y procesar
reaper https://www.facebook.com/reel/123 --output data.json
cat data.json | jq '.text'
```

---

## 4. Uso desde Python

### Función `scrape()` — El modo más sencillo

```python
import asyncio
from reaper import scrape

async def main():
    result = await scrape("https://www.facebook.com/reel/816043001524221")
    print(result["author"]["name"])
    print(result["text"])
    print(f"Reacciones: {result['reaction_count']}")

asyncio.run(main())
```

### Desde código síncrono

```python
import asyncio
from reaper import scrape

# Si no tienes un loop de eventos propio
result = asyncio.run(scrape("https://www.instagram.com/p/abc123/"))
print(result)
```

### Clase `Reaper` — Para múltiples scrapes

```python
import asyncio
from reaper import Reaper

async def main():
    reaper = Reaper()

    # Puedes reusar la misma instancia
    post  = await reaper.scrape("https://www.instagram.com/p/abc123/")
    reel  = await reaper.scrape("https://www.facebook.com/reel/456789")
    grupo = await reaper.scrape("https://www.facebook.com/groups/123456")

    return post, reel, grupo

asyncio.run(main())
```

### Con todas las opciones

```python
import asyncio
from reaper import scrape

result = asyncio.run(scrape(
    url="https://www.facebook.com/groups/123456/posts/789012",
    headless=True,          # True = sin ventana (recomendado)
    debug=False,            # True = logs detallados + artefactos
    screenshot=False,       # True = captura de pantalla
    auto_scroll=True,       # True = scroll para cargar más contenido
    infinity_scroll=False,  # True = scroll continuo (feeds largos)
    proxy_server=None,      # "http://ip:puerto" o "socks5://ip:puerto"
    proxy_username=None,
    proxy_password=None,
))
```

### Guardar el resultado como JSON

```python
import asyncio
import json
from reaper import scrape

result = asyncio.run(scrape("https://www.facebook.com/reel/123456"))

# Guardar en archivo
with open("resultado.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)

# Imprimir formateado
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
```

> **Nota**: Usa siempre `default=str` al serializar el resultado porque contiene objetos `datetime`.

---

## 5. Opciones de configuración

### `headless` (por defecto: `True`)

Controla si el navegador Firefox es visible o no.

- `True` — Sin ventana. Recomendado para producción y scripts automatizados.
- `False` — Muestra la ventana del navegador. Útil para ver qué está pasando cuando algo falla.

```bash
reaper https://... --no-headless   # CLI
```
```python
await scrape(url, headless=False)  # Python
```

### `auto_scroll` (por defecto: `True`)

Hace scroll hacia abajo automáticamente para cargar contenido paginado (comentarios, posts del feed).

- `True` — Activa el scroll. Tarda un poco más pero carga más datos.
- `False` — Sin scroll. Más rápido, pero puede perder comentarios o posts.

```bash
reaper https://... --no-scroll     # CLI (desactiva)
```

### `infinity_scroll` (por defecto: `False`)

Scroll continuo monitorizando la actividad de red. Para feeds muy largos.

⚠️ Requiere `auto_scroll=True` (se valida automáticamente).

```bash
reaper https://www.facebook.com/groups/123 --infinity-scroll
```

### `debug` (por defecto: `False`)

Activa logging detallado y guarda artefactos de la sesión.

```bash
reaper https://... --debug
```

### `screenshot` (por defecto: `False`)

Guarda una imagen PNG del contenedor principal de la página. Solo útil con `debug=True` para diagnóstico visual.

---

## 6. Tipos de contenido soportados

### Facebook

| Tipo | URL de ejemplo | Parser usado |
|---|---|---|
| Post regular | `facebook.com/*/posts/<id>` | PostParser |
| Post por permalink | `facebook.com/permalink.php?story_fbid=...` | PostParser |
| Reel | `facebook.com/reel/<id>` | ReelParser |
| Vídeo nativo | `facebook.com/watch?v=<id>` | VideoParser |
| Foto individual | `facebook.com/photo.php?fbid=<id>` | PhotoParser |
| Foto de álbum | `facebook.com/photo/?fbid=<id>` | PhotoParser |
| Grupo | `facebook.com/groups/<id>` | GroupParser |
| Post en grupo | `facebook.com/groups/<id>/posts/<id>` | PostParser |
| Perfil | `facebook.com/<username>` | ProfileParser |
| Enlace corto | `facebook.com/share/r/<id>` | ReelParser |

### Instagram

| Tipo | URL de ejemplo |
|---|---|
| Post (foto/vídeo/carrusel) | `instagram.com/p/<code>/` |
| Reel | `instagram.com/reel/<code>/` |

---

## 7. Entendiendo el resultado

Todos los resultados son diccionarios Python con los siguientes campos garantizados:

```python
{
    "platform":           str,    # "facebook" o "instagram"
    "status":             str,    # "ok" o "error"
    "error":              str | None,  # mensaje de error si status="error"
    "raw_data_available": bool,   # True si se extrajo contenido real
    "scraped_at":         str,    # fecha y hora del scraping
    "final_url":          str,    # URL final (tras redirecciones)
    "post_url":           str,    # URL original solicitada
}
```

### Campos adicionales según el tipo de contenido

#### Post de Facebook

```python
{
    "id":             str,    # identificador del post
    "posted_at":      str,    # fecha de publicación
    "permalink_url":  str,    # URL permanente del post
    "text":           str,    # texto del post
    "author": {
        "id":          str,
        "name":        str,   # nombre del autor
        "url":         str,   # URL del perfil
        "avatar":      str,   # URL de la foto de perfil
        "is_verified": bool,
    },
    "attachments":    list,   # fotos, vídeos adjuntos
    "hashtags":       list,   # [{"tag": "...", "url": "..."}]
    "mentions":       list,   # usuarios mencionados
    "reaction_count": int,    # total de reacciones
    "share_count":    int,    # veces compartido
    "comments_count": int,    # número de comentarios
    "reactions":      list,   # desglose por tipo (Like, Love, etc.)
    "comments":       list,   # comentarios visibles
    "group":          dict | None,  # grupo si aplica
    "is_sponsored":   bool,
}
```

#### Reel de Facebook

```python
{
    "id":            str,
    "text":          str,
    "author":        dict,
    "reaction_count": int,
    "comments_count": int,
    "share_count":    int,
    "attachments": [{
        "type":          "reel",
        "url":           str | None,   # URL del vídeo
        "thumbnail_url": str | None,
        "duration_ms":   int | None,
        "play_count":    int | None,
    }],
    "feed":          list,   # reels relacionados en la misma página
}
```

#### Grupo de Facebook

```python
{
    "id":            str,
    "name":          str,
    "url":           str,
    "description":   str,
    "privacy": {
        "level":      str,    # "CLOSED" | "OPEN" | "SECRET"
        "is_private": bool,
    },
    "total_members": int,
    "posts_last_day": int | None,
    "admins":        list,   # lista de administradores
    "content_gated": bool,   # True si necesitas ser miembro para ver contenido
}
```

#### Post de Instagram

```python
{
    "code":           str,   # shortcode del post
    "id":             str,
    "permalink_url":  str,
    "posted_at":      str,
    "user": {
        "id":              str,
        "username":        str,
        "full_name":       str,
        "profile_pic_url": str,
        "is_verified":     bool,
    },
    "media_type":     str,   # "Photo" | "Reel" | "carousel"
    "text":           str | None,  # caption del post
    "like_count":     int,
    "comment_count":  int,
    "thumbnail":      str | None,
    "video_versions": list | None,
    "comments":       list,
    "feed":           list,
}
```

### Verificar si la extracción fue exitosa

```python
result = asyncio.run(scrape(url))

if result.get("status") == "error":
    print(f"Error: {result['error']}")
elif result.get("raw_data_available"):
    print("Extracción exitosa")
    print(result["text"])
else:
    print("No se encontraron datos estructurados")
```

---

## 8. Manejo de errores

### Excepciones de Python

```python
from reaper import scrape, UnsupportedPlatformError, ScrapingError
import asyncio

async def extraer_con_manejo(url: str):
    try:
        result = await scrape(url)

        if result.get("status") == "error":
            print(f"Sin datos: {result['error']}")
            return None

        return result

    except ValueError as exc:
        # URL mal formada o parámetros inválidos
        print(f"URL inválida: {exc}")
        return None

    except UnsupportedPlatformError as exc:
        # URL de Twitter, TikTok, YouTube, etc.
        print(f"Plataforma no soportada: {exc}")
        return None

    except ScrapingError as exc:
        # Error durante el scraping (red, Playwright, parseo)
        print(f"Error de scraping: {exc}")
        return None
```

### Tabla de excepciones

| Excepción | Cuándo ocurre | Qué hacer |
|---|---|---|
| `ValueError` | URL vacía, sin `http://`, configuración inválida | Revisar y corregir la URL |
| `UnsupportedPlatformError` | URL de Twitter, TikTok, YouTube, etc. | Solo Facebook e Instagram son soportados |
| `ScrapingError` | Error de red, Playwright falla, timeout | Reintentar, verificar conexión, usar `debug=True` |

### Resultado con `status: "error"` (sin excepción)

Algunos fallos no lanzan excepciones sino que devuelven un resultado con `status: "error"`:

```python
result = asyncio.run(scrape(url))

# Casos comunes:
# - Contenido que requiere login
# - Parser no encontró datos (HTML cambió)
# - URL correcta pero contenido no disponible

if result["status"] == "error":
    print(result["error"])
    # Ejemplos de mensajes:
    # "Authentication required — login_wall_detected"
    # "No se encontró ningún nodo Story válido."
```

### Patrón de reintento

```python
import asyncio
from reaper import scrape, ScrapingError

async def scrape_con_reintentos(url: str, max_intentos: int = 3) -> dict | None:
    for intento in range(1, max_intentos + 1):
        try:
            result = await scrape(url, headless=True)
            if result.get("raw_data_available"):
                return result
            print(f"Sin datos en intento {intento}")
        except ScrapingError as exc:
            print(f"Error en intento {intento}/{max_intentos}: {exc}")
            if intento < max_intentos:
                await asyncio.sleep(5 * intento)  # espera progresiva

    return None
```

---

## 9. Uso con proxy

Un proxy te permite redirigir el tráfico del navegador a través de un servidor intermediario.

### Desde la CLI

```bash
# Proxy HTTP básico
reaper https://www.facebook.com/reel/123 \
    --proxy http://192.168.1.100:8080

# Proxy con autenticación
reaper https://www.facebook.com/reel/123 \
    --proxy http://proxy.ejemplo.com:8080 \
    --proxy-user mi_usuario \
    --proxy-pass mi_contraseña

# Proxy SOCKS5
reaper https://www.facebook.com/reel/123 \
    --proxy socks5://127.0.0.1:1080
```

### Desde Python

```python
import asyncio
from reaper import scrape

result = asyncio.run(scrape(
    url="https://www.facebook.com/reel/123456",
    proxy_server="http://proxy.ejemplo.com:8080",
    proxy_username="usuario",
    proxy_password="contraseña",
))
```

### Formatos de proxy soportados

```
http://ip:puerto
https://ip:puerto
socks5://ip:puerto
http://usuario:contraseña@ip:puerto
```

### Verificar que el proxy funciona

```bash
curl --proxy http://tu-proxy:puerto https://www.facebook.com
```

---

## 10. Modo debug

El modo debug activa dos cosas:
1. **Logs detallados**: muestra cada paso del proceso en la terminal.
2. **Artefactos**: guarda el HTML, tráfico de red y screenshots en disco.

### Activar debug

```bash
# CLI
reaper https://www.facebook.com/reel/123 --debug

# Python
result = asyncio.run(scrape(url, debug=True))
```

### Qué se guarda en debug

```
data/debug_artifacts/
└── www.facebook.com_reel_123_20250601_143022/
    ├── meta.json            ← información de la sesión
    ├── page.html            ← HTML completo de la página
    ├── traffic.json         ← resumen del tráfico de red
    ├── graphql_requests/    ← peticiones GraphQL de Facebook
    └── graphql_responses/   ← respuestas con datos estructurados
```

### Usar artefactos para depurar sin internet

Una vez que tienes los artefactos guardados, puedes re-parsear sin abrir el navegador:

```python
from reaper.network.content_fetcher import load_debug_session, list_debug_sessions
from reaper.parsers import ReelParser

# Ver sesiones disponibles
sesiones = list_debug_sessions("data/debug_artifacts")
for s in sesiones:
    print(s["fetched_at"], s["original_url"])

# Cargar una sesión y parsear
sesion = load_debug_session(sesiones[0]["path"])
result = ReelParser(
    html_content=sesion.html_content,
    final_url=sesion.final_url,
    original_url=sesion.original_url,
    traffic=sesion.traffic,
).parse()
```

### Entender los logs en modo debug

```
14:22:31.045 INFO     scrapers.facebook  Iniciando extracción Facebook | url=...
14:22:31.123 INFO     network.content_fetcher  Navegando a: https://...
14:22:35.891 INFO     network.content_fetcher  Contenido obtenido. Tráfico: GraphQL: 5req/4res
14:22:35.892 INFO     scrapers.facebook  Parser seleccionado | parser=ReelParser
14:22:35.901 DEBUG    parsers.facebook.reel_parser  Nodo Story localizado.
14:22:35.910 INFO     scrapers.facebook  Extracción completada | parser=ReelParser
```

---

## 11. Ejemplos completos por caso de uso

### Extraer un reel de Facebook y mostrar estadísticas

```python
import asyncio
import json
from reaper import scrape

async def analizar_reel(url: str):
    result = await scrape(url, auto_scroll=False)

    if result.get("status") == "error":
        print(f"Error: {result['error']}")
        return

    print(f"Autor:      {result['author']['name']}")
    print(f"Texto:      {result.get('text', '')[:100]}")
    print(f"Reacciones: {result.get('reaction_count', 0)}")
    print(f"Shares:     {result.get('share_count', 0)}")
    print(f"Comentarios:{result.get('comments_count', 0)}")
    print(f"URL:        {result.get('permalink_url')}")

    # Desglose de reacciones
    for r in result.get("reactions", []):
        print(f"  {r['type']}: {r['count']}")

asyncio.run(analizar_reel("https://www.facebook.com/reel/816043001524221"))
```

### Extraer múltiples URLs en paralelo

```python
import asyncio
from reaper import scrape

URLS = [
    "https://www.facebook.com/reel/111111",
    "https://www.instagram.com/p/abc123/",
    "https://www.facebook.com/reel/222222",
]

async def extraer_todas(urls: list[str]) -> list[dict]:
    tareas = [scrape(url, auto_scroll=False) for url in urls]
    resultados = await asyncio.gather(*tareas, return_exceptions=True)

    datos = []
    for url, resultado in zip(urls, resultados):
        if isinstance(resultado, Exception):
            print(f"Error en {url}: {resultado}")
        elif resultado.get("status") == "error":
            print(f"Sin datos en {url}: {resultado['error']}")
        else:
            datos.append(resultado)
            print(f"OK: {url}")

    return datos

resultados = asyncio.run(extraer_todas(URLS))
print(f"Extraídos: {len(resultados)}/{len(URLS)}")
```

### Extraer comentarios de un post

```python
import asyncio
from reaper import scrape

async def obtener_comentarios(url: str):
    # auto_scroll=True para cargar más comentarios
    result = await scrape(url, auto_scroll=True)

    comentarios = result.get("comments", [])
    print(f"Comentarios encontrados: {len(comentarios)}")

    for c in comentarios:
        nivel = "  " * c.get("depth", 0)  # indentación por nivel de reply
        autor = c.get("author", {}).get("name", "Desconocido")
        texto = c.get("text", "")
        print(f"{nivel}[{autor}]: {texto}")

asyncio.run(obtener_comentarios("https://www.facebook.com/permalink.php?story_fbid=..."))
```

### Monitorear un grupo de Facebook

```python
import asyncio
import json
from datetime import datetime
from pathlib import Path
from reaper import scrape

async def snapshot_grupo(url_grupo: str, directorio_salida: str = "snapshots"):
    result = await scrape(url_grupo, auto_scroll=True, infinity_scroll=True)

    if result.get("status") == "error":
        print(f"Error: {result['error']}")
        return

    # Crear nombre de archivo con timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    Path(directorio_salida).mkdir(parents=True, exist_ok=True)
    fichero = f"{directorio_salida}/grupo_{timestamp}.json"

    with open(fichero, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"Guardado en: {fichero}")
    print(f"Grupo: {result.get('name')}")
    print(f"Miembros: {result.get('total_members')}")
    print(f"Posts hoy: {result.get('posts_last_day')}")

asyncio.run(snapshot_grupo("https://www.facebook.com/groups/123456789"))
```

### Pipeline: extraer y procesar datos de Instagram

```python
import asyncio
import csv
from reaper import scrape, ScrapingError

URLS_INSTAGRAM = [
    "https://www.instagram.com/p/abc123/",
    "https://www.instagram.com/p/def456/",
    "https://www.instagram.com/reel/ghi789/",
]

async def extraer_a_csv(urls: list[str], fichero_csv: str):
    filas = []

    for url in urls:
        try:
            result = await scrape(url, auto_scroll=False)

            if result.get("raw_data_available"):
                user = result.get("user", {})
                filas.append({
                    "url":           url,
                    "username":      user.get("username", ""),
                    "full_name":     user.get("full_name", ""),
                    "media_type":    result.get("media_type", ""),
                    "text":          (result.get("text") or "")[:200],
                    "like_count":    result.get("like_count", 0),
                    "comment_count": result.get("comment_count", 0),
                    "posted_at":     str(result.get("posted_at", "")),
                })
                print(f"OK: {url}")
            else:
                print(f"Sin datos: {url} — {result.get('error')}")

        except ScrapingError as exc:
            print(f"Error en {url}: {exc}")

    if filas:
        with open(fichero_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)
        print(f"\nGuardado en {fichero_csv} ({len(filas)} filas)")

asyncio.run(extraer_a_csv(URLS_INSTAGRAM, "instagram_posts.csv"))
```

### Extraer con proxy rotativo

```python
import asyncio
from reaper import scrape

PROXIES = [
    "http://proxy1.ejemplo.com:8080",
    "http://proxy2.ejemplo.com:8080",
    "socks5://proxy3.ejemplo.com:1080",
]

async def extraer_con_proxy_rotativo(url: str) -> dict | None:
    for proxy in PROXIES:
        try:
            result = await scrape(
                url,
                proxy_server=proxy,
                proxy_username="usuario",
                proxy_password="contraseña",
            )
            if result.get("raw_data_available"):
                print(f"Éxito con proxy: {proxy}")
                return result
        except Exception as exc:
            print(f"Proxy {proxy} falló: {exc}")
            continue

    print("Todos los proxies fallaron")
    return None

asyncio.run(extraer_con_proxy_rotativo("https://www.facebook.com/reel/123456"))
```

### Integrar con una aplicación FastAPI

```python
from fastapi import FastAPI, HTTPException
from reaper import scrape, ScrapingError, UnsupportedPlatformError
import json

app = FastAPI()

@app.get("/scrape")
async def endpoint_scrape(url: str, scroll: bool = False):
    try:
        result = await scrape(url, auto_scroll=scroll)
        # Serializar datetime a string
        return json.loads(json.dumps(result, default=str))

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UnsupportedPlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ScrapingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

# Ejecutar con: uvicorn main:app --reload
```

---

## 12. Preguntas frecuentes

**¿Necesito una cuenta de Facebook o Instagram?**
No. Reaper extrae solo contenido público, sin necesitar credenciales.

**¿Cuánto tarda cada extracción?**
Entre 5 y 30 segundos dependiendo del tipo de contenido, la velocidad de la red y si el scroll está activado. Posts simples sin scroll tardan ~5-10s. Grupos con infinity scroll pueden tardar más de un minuto.

**¿Por qué `raw_data_available` es `False` a veces?**
Significa que el parser no encontró datos estructurados en el HTML. Puede ocurrir cuando:
- La página requiere login para mostrar el contenido.
- Facebook/Instagram cambió la estructura de sus datos.
- La URL apunta a contenido eliminado o no disponible.

Usa `--debug` para ver el HTML y diagnosticar qué ocurre.

**¿Puedo usarlo en un servidor sin pantalla?**
Sí. El modo `headless=True` (por defecto) no necesita pantalla. En servidores Linux puede necesitar instalar dependencias de Playwright:
```bash
playwright install-deps firefox
```

**¿`auto_scroll` o `infinity_scroll`? ¿Cuándo usar cada uno?**
- `auto_scroll=True` — para la mayoría de los casos. Hace scroll midiendo el DOM.
- `infinity_scroll=True` — para feeds que cargan contenido monitorizando la red (grupos, búsquedas). Requiere también `auto_scroll=True`.
- `auto_scroll=False` — cuando solo necesitas los datos del primer vistazo (más rápido).

**¿Puedo extraer vídeos y descargarlos?**
Reaper extrae la URL del vídeo (`url` en los adjuntos de VideoParser). Descargar el archivo de vídeo es responsabilidad del consumidor (por ejemplo, con `httpx` o `requests`).

**¿Funciona con URLs acortadas (`fb.me/...`, `bit.ly/...`)?**
Las URLs de `fb.com/share/...` están soportadas. URLs de acortadores externos pueden necesitar resolverse primero:
```python
import httpx
url_larga = httpx.get("https://bit.ly/...", follow_redirects=True).url
```

---

## 13. Problemas comunes y soluciones

### `ModuleNotFoundError: No module named 'reaper'`

```bash
# Solución: instalar el paquete
pip install -e .
```

### `playwright._impl._errors.Error: Executable doesn't exist`

```bash
# Solución: instalar Firefox para Playwright
playwright install firefox

# En servidores Linux, también:
playwright install-deps firefox
```

### `raw_data_available: False` sin excepción

El parser no encontró datos. Causas y soluciones:

```bash
# 1. Activar debug para ver el HTML
reaper https://... --debug

# 2. Ver si el error indica autenticación requerida
#    El campo "error" dirá: "Authentication required — ..."

# 3. Probar sin headless para ver qué muestra el navegador
reaper https://... --no-headless
```

### `ValueError: infinity_scroll requiere auto_scroll=True`

```python
# ❌ Incorrecto
await scrape(url, auto_scroll=False, infinity_scroll=True)

# ✅ Correcto
await scrape(url, auto_scroll=True, infinity_scroll=True)
```

### El scraping es muy lento

```bash
# Desactivar scroll si no necesitas comentarios paginados
reaper https://... --no-scroll

# Modo headless (ya es el default)
# NO usar --no-headless salvo para diagnóstico
```

### Error de proxy (`ProxyError`, `ConnectionRefusedError`)

```bash
# Verificar que el proxy responde
curl --proxy http://ip:puerto https://www.facebook.com

# Verificar credenciales
curl --proxy http://usuario:contraseña@ip:puerto https://www.facebook.com
```

### `ScrapingError: Error al scrapear '...': Page.goto: Timeout`

La página tardó demasiado en cargar. Posibles causas:
- Conexión lenta.
- El proxy es lento.
- Facebook/Instagram está limitando las peticiones.

Soluciones:
- Usar un proxy diferente.
- Esperar unos minutos y reintentar.
- Usar `--no-scroll` para reducir el tiempo de carga.

### La URL es válida pero dice "Plataforma no soportada"

Solo están soportados dominios de Facebook e Instagram:

```
✅ facebook.com, www.facebook.com, m.facebook.com, fb.com
✅ instagram.com, www.instagram.com
❌ twitter.com, tiktok.com, youtube.com, linkedin.com
```

### Los comentarios están vacíos aunque existen en la página

Los comentarios se cargan con scroll. Activa `auto_scroll`:

```bash
reaper https://... # ya tiene auto_scroll por defecto en CLI
```

```python
await scrape(url, auto_scroll=True)  # por defecto True en Python también
```
