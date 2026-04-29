# Arquitectura

## Capas del sistema

Reaper está organizado en capas con responsabilidad única. Los datos solo
fluyen hacia abajo: ninguna capa conoce los detalles de implementación de
la que está por encima.

```
┌─────────────────────────────────────────────────────────────────┐
│  API pública  reaper/__init__.py                                │
│  scrape()  |  Reaper  |  AccountManager  |  ScraperConfig      │
├─────────────────────────────────────────────────────────────────┤
│  Orquestación  core.py                                          │
│  Reaper.scrape() → _dispatch() → Scraper.run()                 │
├──────────────────────────────┬──────────────────────────────────┤
│  Scrapers  scrapers/         │  Auth  auth/                    │
│  FacebookScraper             │  AccountManager                 │
│  InstagramScraper            │  AccountRotator                 │
│  BaseScraper                 │  LocalFileStorage               │
├──────────────────────────────┴──────────────────────────────────┤
│  Red  network/                                                  │
│  ContentFetcher  |  NetworkInterceptor                          │
│  (Playwright → HTML + tráfico GraphQL/API)                      │
├─────────────────────────────────────────────────────────────────┤
│  Parsers  parsers/                                              │
│  PostParser | ReelParser | VideoParser | GroupParser            │
│  ProfileParser | PhotoParser | IgPostParser | IgReelParser      │
├─────────────────────────────────────────────────────────────────┤
│  Utils  utils/                                                  │
│  fb_url_classifier | fb_auth_detector | logger                  │
│  metrics | encoding | http | media | platforms                  │
└─────────────────────────────────────────────────────────────────┘
```

## Flujo de datos completo

```
scrape(url, account_manager=manager)
    │
    ▼
Reaper(account_manager)
    │  crea ScraperConfig con account_manager propagado
    ▼
_dispatch(config, platform)
    │  selecciona FacebookScraper o InstagramScraper
    ▼
FacebookScraper.run()
    │
    ├─► BaseScraper._fetch_with_account("facebook")
    │       │  si hay AccountManager:
    │       ├─► manager.get_account_for_request()  ← rotador elige cuenta
    │       ├─► local_storage.load_cookies()        ← lee cookies de disco
    │       ├─► _fetch(cookies=cookies)             ← Playwright con sesión
    │       └─► manager.record_success/failure()   ← actualiza actividad
    │
    ├─► requires_auth(html, url)           ← detecta muros de login
    ├─► get_parser_from_fb_url(url)        ← clasifica el tipo de contenido
    │
    ▼
ReelParser(html, traffic).parse()
    ├─► _extract_json_blocks()             ← <script type="application/json">
    ├─► _locate_all_stories()              ← DFS buscando nodos Story
    ├─► _build_reel_dict(story)            ← extrae campos estructurados
    └─► _parse_traffic()                   ← enriquece con datos GraphQL
    │
    ▼
dict[str, Any]  ← resultado final
```

## Principios de diseño

**Compatibilidad hacia atrás total.** El campo `account_manager` en
`ScraperConfig` es `None` por defecto. Cuando es `None`, el flujo es
idéntico al de la versión 0.1.x sin ninguna penalización de rendimiento.

**Parsers sin efectos secundarios.** Los parsers reciben HTML + tráfico
y devuelven un dict. No hacen peticiones de red, no escriben en disco,
no modifican estado global. Esto permite usarlos offline con artefactos
de debug guardados.

**Extensibilidad explícita.** `BaseAccountStorage` es un ABC diseñado
para ser implementado. Migrar de JSON en disco a PostgreSQL solo requiere
implementar cinco métodos abstractos.

**Fetch de segundo nivel sin cambio de cuenta.** Los fetches secundarios
(merge reel↔video, re-fetch de álbum) llaman a `_fetch()` directamente,
sin pasar por `_fetch_with_account()`. Son parte de la misma sesión y no
deben contar como peticiones independientes ni cambiar de cuenta.
