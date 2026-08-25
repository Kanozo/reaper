"""
reaper/actions/models.py
========================
Modelos de datos para las acciones de escritura sobre las plataformas.

Este módulo define el contrato de datos de las operaciones que Reaper
puede ejecutar sobre una sesión autenticada:

- ``ActionType``  — enum de las acciones soportadas (post, comentario, like).
- ``ActionResult`` — registro serializable de una acción ejecutada (o fallida).
- ``ActionError``  — excepción del dominio de acciones.

``ActionResult`` es el objeto que se persiste en el almacenamiento de
acciones (``BaseActionStorage``) y el que la API pública retorna al caller.
Todos los campos son opcionales salvo ``action``, para que un único esquema
cubra posts, comentarios, likes y fallos por igual.

Ejemplo::

    from reaper.actions.models import ActionResult, ActionType

    result = ActionResult(
        action=ActionType.LIKE.value,
        account_id="uuid-1234",
        post_url="https://www.facebook.com/reel/123456",
    )
    print(result.to_dict())   # dict serializable para JSON
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Retorna el timestamp UTC actual en formato ISO-8601 con microsegundos."""
    return datetime.now(tz=UTC).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Enums y excepciones
# ─────────────────────────────────────────────────────────────────────────────


class ActionType(str, Enum):
    """Tipo de acción de escritura soportada por Reaper.

    Valores:
        POST:           Crear una publicación nueva en el muro de la cuenta.
        GROUP_POST:     Publicar un post nuevo directamente dentro de un grupo.
        SHARE_TO_GROUP: Compartir una publicación existente dentro de un grupo.
        COMMENT:        Responder con texto a una publicación existente.
        LIKE:           Reaccionar con "Me gusta" a una publicación existente.
    """

    POST = "post"
    GROUP_POST = "group_post"
    SHARE_TO_GROUP = "share_to_group"
    COMMENT = "comment"
    LIKE = "like"


class ActionError(Exception):
    """Error en la ejecución de una acción de escritura.

    Se lanza cuando la acción no puede ejecutarse: falta de cuenta con
    cookies válidas, elemento de la interfaz no encontrado, o la plataforma
    rechaza la operación.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Resultado de una acción
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ActionResult:
    """Registro del resultado de una acción de escritura ejecutada.

    Es el contrato de datos entre ``ActionManager``, el ``SessionActor``
    y el almacenamiento de acciones. Se serializa con ``to_dict()`` /
    ``from_dict()`` para persistir en cualquier backend (JSON, PostgreSQL,
    MongoDB).

    Attributes:
        action_id:       UUID4 interno del registro. Se genera al ejecutar.
        action:          Valor de ``ActionType`` (``"post"``, ``"comment"``, ...).
        platform:        Plataforma sobre la que se ejecutó (``"facebook"``).
        account_id:      UUID4 de la cuenta autenticada usada.
        account_username: Username de la cuenta (para logs/auditoría).
        status:          ``"ok"`` si la plataforma confirmó la acción,
                         ``"error"`` si falló.
        post_url:        URL de la publicación creada o objetivo.
        post_id:         ID de la publicación creada o objetivo (si se detecta).
        comment_id:      ID del comentario publicado (solo ``comment``).
        group:           Identificador del grupo (id o URL) para acciones de grupo.
        text:            Texto enviado (contenido del post o del comentario).
        note:            Información contextual de la acción, p.ej. un post
                         quedó pendiente de aprobación por el administrador
                         del grupo (``"pending_approval"``) pero sí se creó.
        error:           Mensaje de error cuando ``status="error"``.
        executed_at:     Timestamp UTC ISO-8601 de la ejecución.
    """

    action: str
    platform: str = "facebook"
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    account_id: str | None = None
    account_username: str | None = None
    status: str = "ok"
    post_url: str | None = None
    post_id: str | None = None
    comment_id: str | None = None
    group: str | None = None
    text: str | None = None
    note: str | None = None
    error: str | None = None
    executed_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Convierte el resultado en un dict serializable a JSON.

        Returns:
            Dict con todos los campos del registro.
        """
        return {
            "action_id": self.action_id,
            "action": self.action,
            "platform": self.platform,
            "account_id": self.account_id,
            "account_username": self.account_username,
            "status": self.status,
            "post_url": self.post_url,
            "post_id": self.post_id,
            "comment_id": self.comment_id,
            "group": self.group,
            "text": self.text,
            "note": self.note,
            "error": self.error,
            "executed_at": self.executed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionResult:
        """Reconstruye un ``ActionResult`` desde un dict deserializado.

        Args:
            data: Dict producido por :meth:`to_dict`.

        Returns:
            ``ActionResult`` con los valores del dict.

        Raises:
            ValueError: Si falta el campo ``action``.
        """
        action = data.get("action")
        if not action:
            raise ValueError("ActionResult requiere el campo 'action'.")
        return cls(
            action=str(action),
            action_id=str(data.get("action_id") or uuid.uuid4().hex),
            platform=str(data.get("platform", "facebook")),
            account_id=(
                str(data["account_id"]) if data.get("account_id") else None
            ),
            account_username=(
                str(data["account_username"])
                if data.get("account_username")
                else None
            ),
            status=str(data.get("status", "ok")),
            post_url=str(data["post_url"]) if data.get("post_url") else None,
            post_id=str(data["post_id"]) if data.get("post_id") else None,
            comment_id=(
                str(data["comment_id"]) if data.get("comment_id") else None
            ),
            group=str(data["group"]) if data.get("group") else None,
            text=str(data["text"]) if data.get("text") else None,
            note=str(data["note"]) if data.get("note") else None,
            error=str(data["error"]) if data.get("error") else None,
            executed_at=str(data.get("executed_at") or _now_iso()),
        )
