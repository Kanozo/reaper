"""
reaper/auth/models.py
=====================
Modelos de datos para el sistema de gestión de cuentas autenticadas.

Define las estructuras que representan una cuenta (``AccountProfile``),
su estado operativo (``AccountStatus``) y el historial de actividad
(``AccountActivity``). Todos los modelos son serializables a/desde JSON
para soportar múltiples backends de almacenamiento.

Jerarquía de modelos::

    AccountStatus  (Enum)        → estado operativo de la cuenta
    AccountActivity (dataclass)  → contadores, timestamps, errores recientes
    AccountProfile  (dataclass)  → identidad + cookies + actividad

Diseño de la actividad:
    - ``recent_errors`` mantiene solo los últimos N errores (``MAX_RECENT_ERRORS``)
      para no crecer indefinidamente y ser útil en diagnóstico rápido.
    - ``requests_since_cookie_refresh`` se resetea a cero cada vez que se
      importan cookies frescas, permitiendo que el ``AccountManager`` detecte
      cuándo es hora de solicitar un nuevo ciclo de refresco.

Ejemplo de uso::

    from reaper.auth.models import AccountProfile, AccountStatus

    perfil = AccountProfile(
        account_id="uuid-1234",
        platform="facebook",
        username="mi_usuario",
        email="user@example.com",
    )
    perfil.activity.record_success()
    print(perfil.status)          # AccountStatus.UNKNOWN
    print(perfil.to_dict())       # dict serializable para JSON
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados del módulo — deben declararse antes de los dataclasses
# que los usan como default_factory
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Retorna el timestamp UTC actual en formato ISO-8601 con microsegundos."""
    return datetime.now(tz=timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Constantes del módulo
# ─────────────────────────────────────────────────────────────────────────────

# Número máximo de errores recientes a conservar por cuenta.
# Más allá de este límite los errores más antiguos se descartan (FIFO).
MAX_RECENT_ERRORS: int = 20

# Plataformas soportadas como valor de AccountProfile.platform.
SUPPORTED_PLATFORMS: frozenset[str] = frozenset({"facebook", "instagram"})

# Nombre de la cookie que contiene el ID de usuario de cada plataforma.
# Se usa para localizar una cuenta por su ID de plataforma (c_user / ds_user_id).
PLATFORM_USER_ID_COOKIE: dict[str, str] = {
    "facebook": "c_user",
    "instagram": "ds_user_id",
}


# ─────────────────────────────────────────────────────────────────────────────
# Sección 1: Estado de la cuenta
# ─────────────────────────────────────────────────────────────────────────────


class AccountStatus(str, Enum):
    """Estado operativo de una cuenta autenticada.

    Se usa ``str`` como mixin para que el valor sea directamente
    serializable a JSON sin conversión extra (``json.dumps`` acepta str).

    Valores:
        ACTIVE:          La cuenta está operativa y sus cookies son válidas.
        SUSPENDED:       La cuenta fue suspendida por la plataforma.
                         No se seleccionará para rotación.
        RATE_LIMITED:    La plataforma devolvió señales de rate-limit.
                         El rotador la evitará temporalmente.
        COOKIE_EXPIRED:  Las cookies han expirado o son inválidas.
                         Requiere refresco manual antes de volver a usarla.
        NEEDS_REFRESH:   Las cookies están próximas a necesitar refresco
                         (umbral de peticiones alcanzado). Se puede seguir
                         usando pero con prioridad reducida.
        UNKNOWN:         Estado inicial; no se ha verificado todavía.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    RATE_LIMITED = "rate_limited"
    COOKIE_EXPIRED = "cookie_expired"
    NEEDS_REFRESH = "needs_refresh"
    UNKNOWN = "unknown"


# Conjunto de estados que bloquean la selección de la cuenta en rotación.
NON_SELECTABLE_STATUSES: frozenset[AccountStatus] = frozenset({
    AccountStatus.SUSPENDED,
    AccountStatus.COOKIE_EXPIRED,
})


# ─────────────────────────────────────────────────────────────────────────────
# Sección 2: Actividad de la cuenta
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AccountActivity:
    """Historial de actividad y métricas operativas de una cuenta.

    Este objeto es el núcleo del sistema de rotación inteligente:
    el ``AccountRotator`` lo lee para calcular un score de salud
    y decidir qué cuenta usar en la siguiente petición.

    Todos los campos son serializables (int, float, str, list[str], None).

    Attributes:
        total_requests:                  Total acumulado de peticiones realizadas.
        successful_requests:             Peticiones que retornaron datos válidos.
        failed_requests:                 Peticiones con error o sin datos.
        last_used_at:                    ISO-8601 del último uso (o None).
        last_success_at:                 ISO-8601 del último éxito (o None).
        last_error_at:                   ISO-8601 del último error (o None).
        recent_errors:                   Lista FIFO con los últimos N mensajes de error.
                                         Máximo ``MAX_RECENT_ERRORS`` entradas.
        requests_since_cookie_refresh:   Contador que se resetea al importar cookies nuevas.
                                         El ``AccountManager`` usa este valor para saber
                                         cuándo solicitar un refresco de cookies.
    """

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Timestamps en formato ISO-8601 (str | None) para serialización directa a JSON.
    last_used_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None

    # Errores recientes (FIFO, máximo MAX_RECENT_ERRORS).
    recent_errors: list[str] = field(default_factory=list)

    # Contador de peticiones desde el último refresco de cookies.
    requests_since_cookie_refresh: int = 0

    # ── Métodos de mutación ───────────────────────────────────────────────────

    def record_success(self) -> None:
        """Registra una petición exitosa actualizando todos los contadores."""
        now_iso = _now_iso()
        self.total_requests += 1
        self.successful_requests += 1
        self.requests_since_cookie_refresh += 1
        self.last_used_at = now_iso
        self.last_success_at = now_iso

    def record_failure(self, error_message: str) -> None:
        """Registra una petición fallida y añade el error a la cola FIFO.

        Args:
            error_message: Descripción del error ocurrido.
                           Se almacena con timestamp para facilitar el diagnóstico.
        """
        now_iso = _now_iso()
        self.total_requests += 1
        self.failed_requests += 1
        self.requests_since_cookie_refresh += 1
        self.last_used_at = now_iso
        self.last_error_at = now_iso

        # Añadir con timestamp al frente y truncar al límite máximo.
        error_entry = f"[{now_iso}] {error_message}"
        self.recent_errors.insert(0, error_entry)
        if len(self.recent_errors) > MAX_RECENT_ERRORS:
            self.recent_errors = self.recent_errors[:MAX_RECENT_ERRORS]

    def reset_cookie_refresh_counter(self) -> None:
        """Resetea el contador de peticiones desde el último refresco de cookies.

        Llamado por ``AccountManager`` cuando se importan nuevas cookies
        para la cuenta.
        """
        self.requests_since_cookie_refresh = 0

    # ── Propiedades derivadas ─────────────────────────────────────────────────

    @property
    def success_rate(self) -> float:
        """Tasa de éxito entre 0.0 y 1.0. Retorna 1.0 si no hay peticiones."""
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests

    @property
    def failure_rate(self) -> float:
        """Tasa de fallos entre 0.0 y 1.0."""
        return 1.0 - self.success_rate

    # ── Serialización ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serializa la actividad a un dict JSON-compatible."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "last_used_at": self.last_used_at,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "recent_errors": self.recent_errors,
            "requests_since_cookie_refresh": self.requests_since_cookie_refresh,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountActivity":
        """Deserializa desde un dict (tipicamente leído de JSON).

        Campos ausentes usan el valor por defecto del dataclass,
        lo que hace la carga tolerante a versiones anteriores del schema.
        """
        return cls(
            total_requests=data.get("total_requests", 0),
            successful_requests=data.get("successful_requests", 0),
            failed_requests=data.get("failed_requests", 0),
            last_used_at=data.get("last_used_at"),
            last_success_at=data.get("last_success_at"),
            last_error_at=data.get("last_error_at"),
            recent_errors=data.get("recent_errors", []),
            requests_since_cookie_refresh=data.get("requests_since_cookie_refresh", 0),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sección 3: Perfil de cuenta
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AccountProfile:
    """Perfil completo de una cuenta autenticada gestionada por Reaper.

    Cada ``AccountProfile`` representa una cuenta de Facebook o Instagram
    con sus credenciales de sesión (cookies), su estado operativo y el
    historial de actividad que guía la rotación inteligente.

    La carpeta de cookies se gestiona automáticamente por ``AccountManager``
    bajo la ruta ``{accounts_dir}/{platform}/{account_id}/cookies.json``.

    Args:
        account_id: Identificador único (UUID4). Generado automáticamente
                    si no se especifica.
        platform:   ``"facebook"`` o ``"instagram"``.
        username:   Nombre de usuario o identificador legible de la cuenta.
        email:      Email asociado a la cuenta (opcional, no se usa para auth).
        status:     Estado operativo. Ver ``AccountStatus``.
        cookies_path: Ruta absoluta al archivo de cookies JSON en formato
                      Playwright. ``None`` si todavía no se han importado cookies.
        activity:   Historial de actividad. Se crea vacío por defecto.
        created_at: ISO-8601 de creación del perfil.
        updated_at: ISO-8601 de la última modificación.
        notes:      Campo libre para anotaciones del operador.

    Raises:
        ValueError: Si ``platform`` no está en ``SUPPORTED_PLATFORMS``.
    """

    # ── Identidad ─────────────────────────────────────────────────────────────
    account_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    platform: str = "facebook"
    username: str = ""
    email: str | None = None

    # ── Estado ───────────────────────────────────────────────────────────────
    status: AccountStatus = AccountStatus.UNKNOWN

    # ── Cookies ───────────────────────────────────────────────────────────────
    # Ruta al archivo cookies.json (formato Playwright) en disco.
    # None = todavía no se han importado cookies para esta cuenta.
    cookies_path: str | None = None

    # ── Actividad ─────────────────────────────────────────────────────────────
    activity: AccountActivity = field(default_factory=AccountActivity)

    # ── Metadatos ─────────────────────────────────────────────────────────────
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    notes: str = ""

    def __post_init__(self) -> None:
        """Valida invariantes al construir el objeto."""
        if self.platform not in SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Plataforma '{self.platform}' no soportada. "
                f"Valores válidos: {sorted(SUPPORTED_PLATFORMS)}"
            )
        # Garantizar que account_id nunca sea vacío.
        if not self.account_id:
            self.account_id = str(uuid.uuid4())

    # ── Propiedades de conveniencia ───────────────────────────────────────────

    @property
    def has_cookies(self) -> bool:
        """True si la cuenta tiene cookies asociadas.

        Se basa en ``cookies_path`` (locator opaco). Cada backend es
        responsable de dejar este campo a ``None`` cuando no hay cookies:
        ``LocalFileStorage`` lo reconstruye según exista ``cookies.json``,
        y los backends de BD lo guardan como identificador simbólico.
        """
        return self.cookies_path is not None

    @property
    def is_selectable(self) -> bool:
        """True si el rotador puede seleccionar esta cuenta.

        Una cuenta es seleccionable cuando:
        - Tiene cookies disponibles en disco.
        - Su estado no está en ``NON_SELECTABLE_STATUSES``.
        """
        return self.has_cookies and self.status not in NON_SELECTABLE_STATUSES

    def touch_updated(self) -> None:
        """Actualiza ``updated_at`` al momento presente."""
        self.updated_at = _now_iso()

    # ── Serialización ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serializa el perfil completo a un dict JSON-compatible.

        El dict resultante es apto para ``json.dumps()`` sin ``default=str``
        ya que todos los valores son tipos primitivos de Python.
        """
        return {
            "account_id": self.account_id,
            "platform": self.platform,
            "username": self.username,
            "email": self.email,
            "status": self.status.value,  # str, no el Enum
            "cookies_path": self.cookies_path,
            "activity": self.activity.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountProfile":
        """Deserializa desde un dict (tipicamente leído de JSON).

        Tolerante a versiones: campos ausentes usan valores por defecto.

        Args:
            data: Dict con los campos del perfil.

        Returns:
            Instancia de ``AccountProfile`` reconstituida.
        """
        raw_status = data.get("status", AccountStatus.UNKNOWN.value)
        # Conversión segura: si el valor almacenado es desconocido, usar UNKNOWN.
        try:
            status = AccountStatus(raw_status)
        except ValueError:
            status = AccountStatus.UNKNOWN

        activity_data = data.get("activity", {})

        return cls(
            account_id=data.get("account_id", str(uuid.uuid4())),
            platform=data.get("platform", "facebook"),
            username=data.get("username", ""),
            email=data.get("email"),
            status=status,
            cookies_path=data.get("cookies_path"),
            activity=AccountActivity.from_dict(activity_data),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            notes=data.get("notes", ""),
        )



