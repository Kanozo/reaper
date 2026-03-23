"""
utils/media.py
Utilidades de parseo de medios: subtítulos SRT y otros formatos de medios.

Funciones públicas::

    srt_to_dict(contenido_srt)  → list[dict[str, str]]

Integración::

    from utils import srt_to_dict
    from utils.media import srt_to_dict

Python: 3.11+
"""
import re


def srt_to_dict(contenido_srt: str) -> list[dict[str, str]]:
    """Convierte el contenido de un archivo SRT en una lista de bloques indexados por tiempo.

    Parsea el texto completo de un archivo de subtítulos SRT y produce una
    lista de diccionarios donde cada elemento contiene el rango de tiempo
    y el texto asociado. Los índices de secuencia numérica se ignoran.

    Formato de entrada esperado::

        1
        00:00:01,000 --> 00:00:04,000
        Texto del primer subtítulo.

        2
        00:00:05,200 --> 00:00:07,800
        Texto del segundo subtítulo
        en múltiples líneas.

    Args:
        contenido_srt: Contenido completo del archivo SRT como string.
                       Se acepta tanto con como sin índices de secuencia.
                       Las líneas vacías actúan como separadores de bloque.

    Returns:
        Lista de diccionarios con el formato::

            [
                {"time": "00:00:01,000 --> 00:00:04,000", "text": "Texto del primer subtítulo."},
                {"time": "00:00:05,200 --> 00:00:07,800", "text": "Texto del segundo subtítulo\nen múltiples líneas."},
            ]

        Los bloques con timestamp inválido o sin texto se omiten silenciosamente.
        Devuelve una lista vacía si la entrada está vacía o no contiene
        bloques válidos.

    Examples:
        >>> srt = '''
        ... 1
        ... 00:00:01,000 --> 00:00:03,000
        ... Hola mundo.
        ...
        ... 2
        ... 00:00:04,000 --> 00:00:06,000
        ... Segunda línea.
        ... '''
        >>> srt_to_dict(srt)
        [{'time': '00:00:01,000 --> 00:00:03,000', 'text': 'Hola mundo.'}, {'time': '00:00:04,000 --> 00:00:06,000', 'text': 'Segunda línea.'}]
    """
    resultado: list[dict[str, str]] = []

    if not contenido_srt or not contenido_srt.strip():
        return resultado

    # Separar bloques por líneas vacías (uno o más saltos de línea)
    bloques = re.split(r"\n\s*\n", contenido_srt.strip())

    # Patrón estricto para validar líneas de tiempo SRT
    patron_tiempo = re.compile(
        r"^(\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3})$"
    )

    for bloque in bloques:
        lineas = bloque.strip().split("\n")
        if len(lineas) < 2:
            # Un bloque válido necesita al menos timestamp + una línea de texto
            continue

        # Determinar si la primera línea es el índice de secuencia numérico
        indice_timestamp = 1 if lineas[0].strip().isdigit() else 0

        if indice_timestamp >= len(lineas):
            continue

        linea_tiempo = lineas[indice_timestamp].strip()

        # Validar que sea un timestamp SRT válido
        match = patron_tiempo.match(linea_tiempo)
        if not match:
            continue

        # El timestamp canónico
        timestamp = match.group(1)

        # El resto de líneas forman el texto del subtítulo
        texto_lineas = lineas[indice_timestamp + 1:]
        texto = "\n".join(linea.strip() for linea in texto_lineas if linea.strip())

        if texto:
            resultado.append({"time": timestamp, "text": texto})

    return resultado