# API Reference — `reaper.network`

> Toda la API de esta página es importable directamente desde `reaper.network`
> (y también desde los submódulos que se indican).

## `ContentFetcher`

::: reaper.network.content_fetcher.ContentFetcher
    options:
      members:
        - __init__
        - fetch

---

## `BrowserConfig`

::: reaper.network.content_fetcher.BrowserConfig

---

## `FetchResult`

::: reaper.network.interceptor.FetchResult

---

## `CapturedTraffic`

::: reaper.network.interceptor.CapturedTraffic
    options:
      members:
        - graphql_by_operation
        - all_requests
        - all_responses
        - total_requests
        - total_responses
        - summary

---

## Persistencia y análisis offline de sesiones

Guardado, recarga y consulta de sesiones de debug sin abrir el navegador.

::: reaper.network.content_fetcher.serialize_traffic

::: reaper.network.content_fetcher.deserialize_traffic

::: reaper.network.content_fetcher.load_debug_session

::: reaper.network.content_fetcher.list_debug_sessions
