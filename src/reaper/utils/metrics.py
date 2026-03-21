"""
utils/metrics.py
Utilidades de métricas de redes sociales: parseo de cantidades con sufijos
K/M/B y cálculo de métricas de engagement.

Clases públicas::

    SocialMediaParser  → parsea métricas individuales o en bloque

Integración::

    from utils import SocialMediaParser
    from utils.metrics import SocialMediaParser

Ejemplo rápido::

    parser = SocialMediaParser()
    metrics = parser.parse_engagement_metrics(
        reaction_count="5.2K",
        share_count="1.1M",
        comment_count=342,
    )
    # → {"reaction_count": 5200, "share_count": 1100000, "comment_count": 342}

Python: 3.11+
"""
from reaper.utils.logger import get_logger
import re

logger = get_logger(__name__)


class SocialMediaParser:
    """Parser de métricas de redes sociales con soporte de sufijos K, M y B.

    Convierte representaciones textuales de cantidades (como ``"5.2K"``,
    ``"1.5M"``, ``"2,300"``) a valores enteros. Diseñado para parsear los
    valores de engagement que aparecen en el HTML o tráfico JSON de plataformas
    como Facebook o Instagram.

    Los resultados se acumulan en ``self.result`` y también se devuelven como
    valor de retorno de cada método, lo que permite encadenar llamadas o
    usar la instancia como acumulador.

    Attributes:
        result: Diccionario acumulativo con todos los campos parseados.
                Se rellena en cada llamada a ``_parse_metric`` o
                ``parse_engagement_metrics``.

    Examples:
        >>> parser = SocialMediaParser()
        >>> parser._parse_metric("5.2K", "likes")
        5200
        >>> parser._parse_metric("1.5M", "shares")
        1500000
        >>> parser.result
        {'likes': 5200, 'shares': 1500000}
    """

    # Multiplicadores para cada sufijo reconocido (case-insensitive)
    SUFFIX_MULTIPLIERS: dict[str, int] = {
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
    }

    # Patrón para extraer la parte numérica y el sufijo opcional.
    # Acepta enteros y decimales con coma o punto, seguidos de K/M/B opcionales.
    METRIC_PATTERN = re.compile(r"^([\d,.]+)\s*([kmb])?$", re.IGNORECASE)

    def __init__(self) -> None:
        """Inicializa el parser con un acumulador de resultados vacío."""
        self.result: dict[str, int] = {}

    def _parse_metric(
        self,
        value: str | int | float | None,
        field_name: str,
    ) -> int:
        """Parsea un valor de métrica y lo almacena en ``self.result``.

        Convierte strings con sufijos K/M/B, números con separadores de miles
        (coma o punto) y valores numéricos directos a un entero. Los valores
        ``None`` o vacíos se almacenan como ``0``.

        Args:
            value: Valor a parsear. Puede ser:
                   - ``str``: ``"5.2K"``, ``"1.5M"``, ``"2,300"``, ``"42"``
                   - ``int`` o ``float``: se convierte directamente a int
                   - ``None``: se almacena como 0
            field_name: Nombre del campo en ``self.result`` donde se almacena
                        el valor parseado.

        Returns:
            Valor entero parseado. Devuelve ``0`` si el parseo falla.

        Examples:
            >>> parser = SocialMediaParser()
            >>> parser._parse_metric("5.2K", "reactions")
            5200
            >>> parser._parse_metric(None, "comments")
            0
            >>> parser._parse_metric("1.5M", "shares")
            1500000
            >>> parser._parse_metric("2,300", "views")
            2300
        """
        # None o vacío → 0
        if value is None:
            self.result[field_name] = 0
            return 0

        # Numérico directo → conversión simple
        if isinstance(value, (int, float)):
            parsed = int(value)
            self.result[field_name] = parsed
            return parsed

        value_str = str(value).strip().lower()

        if not value_str:
            self.result[field_name] = 0
            return 0

        # Intentar match con el patrón de número + sufijo
        match = self.METRIC_PATTERN.match(value_str)
        if not match:
            # Fallback: intentar conversión directa (ej. "1234")
            try:
                parsed = int(float(value_str.replace(",", ".")))
                self.result[field_name] = parsed
                return parsed
            except (ValueError, TypeError):
                logger.debug(
                    "No se pudo parsear la métrica '%s'='%s'. Se usará 0.",
                    field_name, value,
                )
                self.result[field_name] = 0
                return 0

        number_str, suffix = match.groups()

        try:
            # Normalizar separador decimal: la coma puede ser decimal o de miles
            number = float(number_str.replace(",", "."))
            multiplier = self.SUFFIX_MULTIPLIERS.get(suffix or "", 1)
            parsed = int(number * multiplier)
            self.result[field_name] = parsed
            return parsed
        except (ValueError, TypeError) as exc:
            logger.debug(
                "Error convirtiendo métrica '%s'='%s': %s. Se usará 0.",
                field_name, value, exc,
            )
            self.result[field_name] = 0
            return 0

    def parse_engagement_metrics(
        self,
        reaction_count: str | int | float | None,
        share_count: str | int | float | None,
        comment_count: str | int | float | None = None,
    ) -> dict[str, int]:
        """Parsea las métricas principales de engagement en una sola llamada.

        Parsea reacciones, compartidos y opcionalmente comentarios, y los
        almacena en ``self.result``. Es el método de conveniencia habitual
        para extraer métricas del tráfico de redes sociales.

        Args:
            reaction_count: Total de reacciones (likes, loves, etc.).
            share_count: Total de veces que se compartió el contenido.
            comment_count: Total de comentarios. Si es ``None``, no se añade
                           ``comment_count`` al resultado.

        Returns:
            Diccionario con las métricas parseadas. Siempre contiene
            ``reaction_count`` y ``share_count``. Contiene ``comment_count``
            solo si se proporcionó un valor distinto de ``None``.

        Examples:
            >>> parser = SocialMediaParser()
            >>> parser.parse_engagement_metrics("5.2K", "342", 89)
            {'reaction_count': 5200, 'share_count': 342, 'comment_count': 89}

            >>> parser2 = SocialMediaParser()
            >>> parser2.parse_engagement_metrics("1.1M", None)
            {'reaction_count': 1100000, 'share_count': 0}
        """
        self._parse_metric(reaction_count, "reaction_count")
        self._parse_metric(share_count, "share_count")

        if comment_count is not None:
            self._parse_metric(comment_count, "comment_count")

        return self.result