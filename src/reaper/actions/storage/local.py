"""
reaper/actions/storage/local.py
================================
Implementación de almacenamiento de acciones basada en archivos JSON locales.

Cada acción ocupa un archivo propio dentro de ``actions_dir``:

    {actions_dir}/
    ├── {action_id}.json        ← registro completo de una acción
    └── index.json              ← índice ligero (opcional, generado bajo demanda)

Estructura de un archivo de registro (serialización de ``ActionResult``)::

    {
      "action_id":   "9f4a3b...",
      "action":      "post",
      "platform":    "facebook",
      "account_id":  "uuid-1234",
      "status":      "ok",
      "post_url":    "https://www.facebook.com/username/posts/123",
      "note":        "El post se creó pero quedó pendiente de aprobación...",
      "executed_at": "2026-01-01T12:00:00+00:00"
    }

Ventajas frente a un único JSON gigante:
- Escrituras independientes (cada registro se lee/escribe por separado).
- Inspección manual sencilla (un archivo por acción).
- Sin riesgo de reescribir todo el historial al guardar una acción.

Las operaciones de listado aplican filtros en memoria y devuelven los
registros más recientes primero (ordenados por ``executed_at``).

Ejemplo::

    from reaper.actions.storage.local import LocalActionStorage

    storage = LocalActionStorage("data/actions")
    await storage.add(result)                      # persiste el registro
    historial = await storage.list_all(action="post")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reaper.actions.models import ActionResult
from reaper.actions.storage.base import BaseActionStorage, StorageError
from reaper.utils.logger import get_logger

logger = get_logger(__name__)


class LocalActionStorage(BaseActionStorage):
    """Almacenamiento de acciones en archivos JSON locales.

    Args:
        actions_dir: Directorio raíz para almacenar los registros de acciones.
                     Se crea automáticamente si no existe.
                     Ejemplo: ``Path("data/actions")``.
    """

    def __init__(self, actions_dir: str | Path = "data/actions") -> None:
        self.actions_dir = Path(actions_dir)
        self.actions_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("LocalActionStorage inicializado | dir=%s", self.actions_dir)

    # ── API pública ───────────────────────────────────────────────────────────

    async def add(self, action: ActionResult) -> None:
        """Persiste un registro de acción en ``{action_id}.json``."""
        action_file = self.actions_dir / f"{action.action_id}.json"
        try:
            _write_json(action_file, action.to_dict())
            logger.debug(
                "Acción guardada | action_id=%s | action=%s | status=%s",
                action.action_id,
                action.action,
                action.status,
            )
        except OSError as exc:
            raise StorageError(
                f"No se pudo guardar la acción '{action.action_id}': {exc}"
            ) from exc

    async def get(self, action_id: str) -> ActionResult | None:
        """Carga el registro de una acción por su ``action_id``."""
        action_file = self.actions_dir / f"{action_id}.json"
        if not action_file.exists():
            return None

        try:
            data = _read_json(action_file)
            return ActionResult.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StorageError(
                f"No se pudo leer la acción '{action_id}': {exc}"
            ) from exc

    async def list_all(
        self,
        account_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[ActionResult]:
        """Lista registros, opcionalmente filtrados, más recientes primero."""
        records: list[ActionResult] = []

        for action_file in self.actions_dir.glob("*.json"):
            if action_file.name == "index.json":
                continue
            try:
                data = _read_json(action_file)
                result = ActionResult.from_dict(data)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "Registro de acción corrupto ignorado | file=%s | %s",
                    action_file,
                    exc,
                )
                continue

            if account_id and result.account_id != account_id:
                continue
            if action and result.action != action:
                continue
            records.append(result)

        records.sort(key=lambda r: r.executed_at, reverse=True)

        if limit > 0:
            records = records[:limit]

        logger.debug(
            "Acciones listadas | account_id=%s | action=%s | total=%d",
            account_id or "todas",
            action or "todas",
            len(records),
        )
        return records

    async def delete(self, action_id: str) -> bool:
        """Elimina el archivo del registro de una acción.

        Returns:
            ``True`` si existía y fue eliminado, ``False`` si no existía.
        """
        action_file = self.actions_dir / f"{action_id}.json"
        if not action_file.exists():
            return False

        try:
            action_file.unlink()
            logger.debug("Acción eliminada | action_id=%s", action_id)
            return True
        except OSError as exc:
            raise StorageError(
                f"No se pudo eliminar la acción '{action_id}': {exc}"
            ) from exc

    async def count(
        self,
        account_id: str | None = None,
        action: str | None = None,
    ) -> int:
        """Cuenta los registros coincidentes sin materializar la lista completa."""
        count = 0
        for action_file in self.actions_dir.glob("*.json"):
            if action_file.name == "index.json":
                continue
            try:
                data = _read_json(action_file)
                if account_id and data.get("account_id") != account_id:
                    continue
                if action and data.get("action") != action:
                    continue
                count += 1
            except (OSError, json.JSONDecodeError):
                continue
        return count


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de I/O de archivos JSON
# ─────────────────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: Any) -> None:
    """Escribe ``data`` como JSON UTF-8 indentado en ``path``."""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    """Lee y parsea JSON desde ``path``.

    Raises:
        OSError: Si el archivo no se puede leer.
        json.JSONDecodeError: Si el contenido no es JSON válido.
    """
    return json.loads(path.read_text(encoding="utf-8"))
