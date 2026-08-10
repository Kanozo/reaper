"""
reaper/utils/logger.py
======================
Configuración centralizada de logging para el proyecto Reaper.

Responsabilidades:
    - Configurar handlers, formato y nivel en un único punto de entrada
      (``setup_logging``), llamado desde ``cli.py`` o desde la API pública.
    - Proveer formato enriquecido con colores ANSI (solo TTY) y timestamps.
    - Ofrecer ``get_logger`` como alias documentado de ``getLogger(__name__)``
      para que los módulos del proyecto lo importen de un solo lugar.
    - Exponer ``DebugContext``: context manager que activa temporalmente
      el nivel DEBUG en el logger ``"reaper"`` (o un sub-logger concreto),
      útil para tests o sesiones de depuración puntuales.

Uso típico
----------
Desde la CLI (una sola vez al arrancar)::

    from reaper.utils.logger import setup_logging
    setup_logging(level="DEBUG", use_color=True)

Desde cualquier módulo del proyecto::

    from reaper.utils.logger import get_logger
    logger = get_logger(__name__)
    # Equivalente exacto a logging.getLogger(__name__),
    # pero importado desde un solo lugar.

Para tests o depuración puntual::

    from reaper.utils.logger import DebugContext
    with DebugContext("reaper.parsers"):
        result = parser.parse()

Jerarquía de loggers del proyecto::

    reaper                          ← logger raíz del proyecto
    ├── reaper.scrapers.facebook
    ├── reaper.scrapers.instagram
    ├── reaper.network.content_fetcher
    ├── reaper.network.interceptor
    └── reaper.parsers
        ├── reaper.parsers.base_parser
        ├── reaper.parsers.facebook.post_parser
        ├── reaper.parsers.facebook.reel_parser
        └── ...

Python: 3.11+
"""

import logging
import sys
from typing import Any


# ============================================================================
# CONSTANTES DE FORMATO
# ============================================================================

#: Formato para terminales con color (TTY).
_FMT_COLOR = "%(asctime)s %(levelname_colored)s %(name_short)s  %(message)s"

#: Formato plano para ficheros, pipes y entornos sin TTY.
_FMT_PLAIN = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"

#: Formato de fecha — sin milisegundos para logs de producción,
#: con milisegundos para DEBUG (añadidos manualmente en formatTime).
_DATEFMT_NORMAL = "%H:%M:%S"
_DATEFMT_DEBUG  = "%H:%M:%S"  # los ms se añaden en _ReaperFormatter.formatTime

#: Prefijo del nombre del paquete que se recorta en el formato corto.
_PKG_PREFIX = "reaper."

# Códigos ANSI de color por nivel
_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG:    "\033[36m",   # cyan
    logging.INFO:     "\033[32m",   # verde
    logging.WARNING:  "\033[33m",   # amarillo
    logging.ERROR:    "\033[31m",   # rojo
    logging.CRITICAL: "\033[1;31m", # rojo negrita
}
_RESET = "\033[0m"


# ============================================================================
# FORMATTER ENRIQUECIDO
# ============================================================================

class _ReaperFormatter(logging.Formatter):
    """Formatter con soporte opcional de color ANSI y nombre corto del módulo.

    Attributes:
        use_color: Si True, aplica códigos ANSI al nivel y al nombre.
        debug_mode: Si True, añade milisegundos al timestamp.
    """

    def __init__(self, use_color: bool = False, debug_mode: bool = False) -> None:
        datefmt = _DATEFMT_DEBUG if debug_mode else _DATEFMT_NORMAL
        fmt     = _FMT_COLOR if use_color else _FMT_PLAIN
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.use_color  = use_color
        self.debug_mode = debug_mode

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        """Extiende el timestamp con milisegundos en modo debug.

        ``logging`` usa ``time.strftime`` internamente, que no soporta ``%f``
        (microsegundos de ``datetime``). Los milisegundos se añaden manualmente
        usando ``record.msecs``, que el módulo logging siempre calcula.
        """
        base = super().formatTime(record, datefmt)
        if self.debug_mode:
            return f"{base}.{int(record.msecs):03d}"
        return base

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # ── Nombre corto: reaper.parsers.facebook.post_parser → parsers.fb.post
        short = record.name.removeprefix(_PKG_PREFIX) if record.name.startswith(_PKG_PREFIX) else record.name
        record.name_short = short  # type: ignore[attr-defined]

        if self.use_color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            record.levelname_colored = (  # type: ignore[attr-defined]
                f"{color}{record.levelname:<8}{_RESET}"
            )

        return super().format(record)


# ============================================================================
# SETUP PRINCIPAL
# ============================================================================

def setup_logging(
    level: str | int = "INFO",
    use_color: bool | None = None,
    log_file: str | None = None,
    propagate: bool = False,
) -> None:
    """Configura el logging del proyecto completo.

    Debe llamarse **una sola vez** al inicio del programa, antes de
    cualquier llamada a ``get_logger`` o uso de loggers del proyecto.
    Configura el logger raíz ``"reaper"`` con los handlers apropiados.

    Args:
        level: Nivel de log como string (``"DEBUG"``, ``"INFO"``, etc.)
            o como constante ``logging.*``. Default: ``"INFO"``.
        use_color: Si True, activa colores ANSI en el handler de consola.
            Si None (default), autodetecta según ``sys.stderr.isatty()``.
        log_file: Ruta opcional a un fichero de log adicional. El fichero
            usa siempre el formato plano (sin color) y nivel ``DEBUG``.
        propagate: Si True, los mensajes se propagan también al logger
            raíz de Python (útil para frameworks que configuran el root).
            Default: ``False`` — Reaper gestiona sus propios handlers.

    Example::

        # CLI normal
        setup_logging(level="INFO")

        # Sesión de depuración
        setup_logging(level="DEBUG", use_color=True)

        # Producción con fichero de log
        setup_logging(level="WARNING", log_file="/var/log/reaper.log")
    """
    numeric_level = (
        level if isinstance(level, int)
        else getattr(logging, level.upper(), logging.INFO)
    )

    # Autodetectar color si no se especifica
    _use_color = (
        use_color if use_color is not None
        else (hasattr(sys.stderr, "isatty") and sys.stderr.isatty())
    )

    reaper_logger = logging.getLogger("reaper")
    reaper_logger.setLevel(numeric_level)
    reaper_logger.propagate = propagate

    # Limpiar handlers anteriores para evitar duplicados si se llama varias veces
    reaper_logger.handlers.clear()

    # ── Handler de consola (stderr) ──────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(
        _ReaperFormatter(use_color=_use_color, debug_mode=numeric_level <= logging.DEBUG)
    )
    reaper_logger.addHandler(console_handler)

    # ── Handler de fichero (opcional) ────────────────────────────────────────
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Fichero siempre en DEBUG completo
        file_handler.setFormatter(_ReaperFormatter(use_color=False, debug_mode=True))
        reaper_logger.addHandler(file_handler)

    reaper_logger.debug(
        "Logging configurado | level=%s | color=%s | file=%s",
        logging.getLevelName(numeric_level),
        _use_color,
        log_file or "—",
    )


# ============================================================================
# HELPER PARA MÓDULOS DEL PROYECTO
# ============================================================================

def get_logger(name: str) -> logging.Logger:
    """Devuelve el logger para el módulo indicado.

    Wrapper mínimo sobre ``logging.getLogger(name)`` que actúa como
    punto de importación único para todos los módulos del proyecto.
    No añade configuración propia — el logger hereda la configuración
    establecida por ``setup_logging`` a través de la jerarquía ``"reaper.*"``.

    Args:
        name: Nombre del módulo. Usar siempre ``__name__`` para preservar
            la jerarquía automática de loggers.

    Returns:
        Logger configurado para el módulo.

    Example::

        # En cualquier módulo del proyecto:
        from reaper.utils.logger import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


# ============================================================================
# CONTEXT MANAGER PARA DEBUG PUNTUAL
# ============================================================================

class DebugContext:
    """Context manager que activa temporalmente el nivel DEBUG en un logger.

    Útil para activar logs detallados solo durante una sección de código
    específica, sin modificar la configuración global. Especialmente útil
    en tests y sesiones de depuración de parsers.

    Args:
        logger_name: Nombre del logger a afectar. Por defecto ``"reaper"``
            (afecta a todo el proyecto). Puede ser un sub-logger específico
            como ``"reaper.parsers.facebook"``.
        level: Nivel temporal a aplicar dentro del contexto. Default: DEBUG.

    Example::

        # Activar DEBUG solo para el parser de posts
        with DebugContext("reaper.parsers.facebook.post_parser"):
            result = PostParser(...).parse()

        # Activar DEBUG para todo Reaper durante un test
        with DebugContext():
            result = await scrape(url)

        # Ver solo tráfico de red
        with DebugContext("reaper.network", level=logging.DEBUG):
            fetch_result = await fetcher.fetch()
    """

    def __init__(
        self,
        logger_name: str = "reaper",
        level: int = logging.DEBUG,
    ) -> None:
        self._logger_name = logger_name
        self._target_level = level
        self._previous_level: int = logging.NOTSET

    def __enter__(self) -> "DebugContext":
        target = logging.getLogger(self._logger_name)
        self._previous_level = target.level
        target.setLevel(self._target_level)
        return self

    def __exit__(self, *_: Any) -> None:
        logging.getLogger(self._logger_name).setLevel(self._previous_level)
