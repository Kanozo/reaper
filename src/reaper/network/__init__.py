"""reaper/network — Capa de red: interceptor y fetcher de contenido.

Re-exporta la API pública de la capa de red para que los consumidores
puedan importar desde ``reaper.network`` sin bajar a los submódulos::

    from reaper.network import (
        ContentFetcher,
        FetchResult,
        CapturedTraffic,
        load_debug_session,
        list_debug_sessions,
        serialize_traffic,
        deserialize_traffic,
    )
"""

from reaper.network.content_fetcher import (
    ContentFetcher,
    deserialize_traffic,
    list_debug_sessions,
    load_debug_session,
    serialize_traffic,
)
from reaper.network.interceptor import (
    CapturedRequest,
    CapturedResponse,
    CapturedTraffic,
    FetchResult,
    GraphQLMeta,
    NetworkInterceptor,
)

__all__ = [
    # Interceptor y modelos de tráfico
    "NetworkInterceptor",
    "GraphQLMeta",
    "CapturedRequest",
    "CapturedResponse",
    "CapturedTraffic",
    "FetchResult",
    # Fetcher
    "ContentFetcher",
    # Persistencia y recarga de sesiones de debug
    "serialize_traffic",
    "deserialize_traffic",
    "load_debug_session",
    "list_debug_sessions",
]
