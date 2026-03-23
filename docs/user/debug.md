# Modo debug

El modo debug activa dos cosas simultáneamente: logging detallado en la
terminal y guardado de artefactos en disco para análisis offline.

## Activar debug

=== "Python"

    ```python
    result = asyncio.run(scrape(url, debug=True))
    ```

=== "Terminal"

    ```bash
    reaper https://www.facebook.com/reel/123 --debug
    ```

## Qué se guarda en disco

```
data/debug_artifacts/
└── www.facebook.com_reel_123_20260101_143022/
    ├── meta.json              ← metadatos de la sesión
    ├── page.html              ← HTML completo renderizado por Firefox
    ├── traffic.json           ← índice del tráfico de red capturado
    ├── graphql_requests/      ← peticiones GraphQL salientes
    │   └── 000_ReelQuery.json
    └── graphql_responses/     ← respuestas GraphQL con datos estructurados
        ├── 000_ReelQuery.json
        └── 001_CommentsQuery.json
```

## Parsear artefactos sin abrir el navegador

Una vez guardados, puedes re-parsear sin conexión:

```python
from reaper.network.content_fetcher import load_debug_session, list_debug_sessions
from reaper.parsers import ReelParser

# Ver sesiones disponibles
sesiones = list_debug_sessions("data/debug_artifacts")
for s in sesiones:
    print(s["fetched_at"], s["original_url"])

# Cargar y parsear
sesion = load_debug_session(sesiones[0]["path"])
result = ReelParser(
    html_content=sesion.html_content,
    final_url=sesion.final_url,
    original_url=sesion.original_url,
    traffic=sesion.traffic,
    debug=True,
).parse()
```

## Activar solo logging verbose (sin artefactos)

```python
from reaper.utils.logger import setup_logging

setup_logging(level="DEBUG")
result = await scrape(url)
```

## Entender los logs en modo debug

```
14:22:31.045 INFO   scrapers.facebook   Iniciando extracción Facebook | url=...
14:22:31.123 INFO   network.content_fetcher  Navegando a: https://...
14:22:35.891 INFO   network.content_fetcher  Contenido obtenido. Tráfico: GraphQL: 5req/4res
14:22:35.892 INFO   scrapers.facebook   Parser seleccionado | parser=ReelParser
14:22:35.910 INFO   scrapers.facebook   Extracción completada | parser=ReelParser
```

Con cuentas autenticadas verás adicionalmente:

```
14:22:31.040 INFO   scrapers.base  Fetch autenticado | platform=facebook |
                    username=mi_usuario | cookies=28 | peticiones_cuenta=12 | score_aprox=91%
```
