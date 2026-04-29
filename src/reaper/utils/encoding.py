"""
utils/encoding.py
Utilidades de codificación: decodificación base64 de IDs de Facebook y
serialización JSON de objetos ``datetime``.

Funciones públicas::

    decode_alphanumeric_id(b64_id)  → ID numérico de post desde base64
    datetime_encoder(obj)           → encoder JSON para datetime

Integración::

    from utils import decode_alphanumeric_id, datetime_encoder
    from utils.encoding import datetime_encoder

Python: 3.11+
"""
import base64
from reaper.utils.logger import get_logger
from datetime import datetime
from typing import Any

logger = get_logger(__name__)


def decode_alphanumeric_id(b64_id: str) -> str | None:
    """Decodifica un ``feedback_id`` de Facebook en base64 para obtener el ID numérico.

    Facebook codifica algunos IDs de feedback y comentarios en base64 con el
    formato ``"feedback:<post_id>"`` o ``"comment:<comment_id>"``. Este método
    extrae la parte numérica tras los dos puntos.

    Args:
        b64_id: ID alfanumérico en base64 tal como aparece en el HTML o tráfico
                GraphQL (ej. ``"ZmVlZGJhY2s6MTIzNDU2Nzg5MDEyMzQ1"``)

    Returns:
        El ID numérico como string si la decodificación es exitosa, o ``None``
        si el formato es inválido, la entrada está vacía o la decodificación falla.

    Examples:
        >>> decode_alphanumeric_id("ZmVlZGJhY2s6MTIzNDU2")
        '123456'
        >>> decode_alphanumeric_id("")
        None
        >>> decode_alphanumeric_id("no_es_base64!!!")
        None
    """
    if not b64_id:
        return None
    try:
        decoded = base64.b64decode(b64_id).decode("utf-8")
        if ":" in decoded:
            return decoded.split(":", 1)[1]
    except Exception as exc:
        logger.debug("No se pudo decodificar alphanumeric_id '%s': %s", b64_id, exc)
    return None


def datetime_encoder(obj: Any) -> str:
    """Encoder personalizado para serialización JSON de objetos ``datetime``.

    Usar como argumento ``default`` en ``json.dump`` / ``json.dumps`` cuando
    el diccionario a serializar contiene campos de tipo ``datetime``.

    Args:
        obj: Objeto a serializar. Si es ``datetime``, se convierte a ISO 8601.
             Para cualquier otro tipo no serializable natively por JSON, se
             lanza ``TypeError``.

    Returns:
        Representación ISO 8601 del datetime (ej. ``"2024-01-15T14:30:00"``).

    Raises:
        TypeError: Si el objeto no es de tipo ``datetime``.

    Examples:
        >>> import json
        >>> from datetime import datetime
        >>> from utils.encoding import datetime_encoder
        >>> data = {"ts": datetime(2024, 1, 15, 14, 30)}
        >>> json.dumps(data, default=datetime_encoder)
        '{"ts": "2024-01-15T14:30:00"}'
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Objeto de tipo {type(obj).__name__} no es serializable a JSON.")