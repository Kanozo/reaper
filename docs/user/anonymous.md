# Scraping sin cuentas

El modo anónimo es el comportamiento original de Reaper. No requiere ninguna
configuración adicional.

## Función `scrape()` — uso mínimo

```python
import asyncio
from reaper import scrape

result = asyncio.run(scrape("https://www.facebook.com/reel/816043001524221"))

if result["status"] == "ok" and result["raw_data_available"]:
    print(result["author"]["name"])
    print(result["text"])
    print(f"Reacciones: {result['reaction_count']}")
```

## Clase `Reaper` — múltiples scrapes

```python
import asyncio
from reaper import Reaper

async def main():
    reaper = Reaper()

    post  = await reaper.scrape("https://www.instagram.com/p/abc123/")
    reel  = await reaper.scrape("https://www.facebook.com/reel/456789")
    grupo = await reaper.scrape("https://www.facebook.com/groups/123456")

asyncio.run(main())
```

## Opciones disponibles

```python
result = asyncio.run(scrape(
    url="https://www.facebook.com/groups/123456/posts/789012",
    headless=True,           # False = ver Firefox mientras trabaja
    debug=False,             # True = logs detallados + artefactos en disco
    screenshot=False,        # True = captura PNG del contenedor principal
    auto_scroll=True,        # False = más rápido, menos comentarios cargados
    infinity_scroll=False,   # True = scroll continuo para feeds muy largos
    proxy_server=None,       # "http://ip:puerto" o "socks5://ip:puerto"
    proxy_username=None,
    proxy_password=None,
))
```

## Desde la línea de comandos

```bash
# Extraer un reel de Facebook
reaper https://www.facebook.com/reel/816043001524221

# Extraer un post de Instagram
reaper https://www.instagram.com/p/abc123XYZ/

# Guardar resultado como JSON
reaper https://www.facebook.com/reel/123 --output resultado.json --indent 4

# Ver navegador mientras trabaja (diagnóstico)
reaper https://www.facebook.com/reel/123 --no-headless

# Activar modo debug completo
reaper https://www.facebook.com/reel/123 --debug
```

## Verificar si el scraping fue exitoso

```python
result = asyncio.run(scrape(url))

if result["status"] == "error":
    print(f"Error: {result['error']}")
elif result["raw_data_available"]:
    print("Extracción exitosa")
else:
    print("Sin datos estructurados — usa --debug para diagnosticar")
```

## Contenido soportado

### Facebook

| Tipo | URL de ejemplo |
|---|---|
| Post regular | `facebook.com/*/posts/<id>` |
| Post permalink | `facebook.com/permalink.php?story_fbid=...` |
| Reel | `facebook.com/reel/<id>` |
| Vídeo nativo | `facebook.com/watch?v=<id>` |
| Foto | `facebook.com/photo.php?fbid=<id>` |
| Grupo | `facebook.com/groups/<id>` |
| Post en grupo | `facebook.com/groups/<id>/posts/<id>` |
| Perfil | `facebook.com/<username>` |

### Instagram

| Tipo | URL de ejemplo |
|---|---|
| Post (foto/vídeo/carrusel) | `instagram.com/p/<code>/` |
| Reel | `instagram.com/reel/<code>/` |
