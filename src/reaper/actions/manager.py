"""
reaper/actions/manager.py
=========================
``ActionManager`` — orquestador de acciones de escritura sobre Facebook.

Coordina el ciclo completo de una acción autenticada:

    1. Resuelve la cuenta (rotación o cuenta forzada) vía ``AccountManager``.
    2. Carga sus cookies y abre una sesión de navegador con ``SessionActor``.
    3. Ejecuta el flujo de UI correspondiente (post, grupo, share, comment,
       like) contra ``www.facebook.com``.
    4. Confirma el resultado por DOM y construye un ``ActionResult``.
    5. Persiste el registro en el backend de acciones configurado
       (JSON local por defecto, o PostgreSQL/MongoDB vía ``reaper.toml``).
    6. Actualiza la actividad de la cuenta y refresca cookies si el servidor
       las rotó.

Ejemplo::

    import asyncio
    from reaper.actions import ActionManager
    from reaper.auth import AccountManager

    async def main():
        accounts = AccountManager()
        manager = ActionManager(account_manager=accounts)

        result = await manager.create_post(
            text="Hola desde Reaper",
            images=["foto.jpg"],
        )
        print(result.to_dict())

        result = await manager.like(post_url="https://www.facebook.com/...")
        await manager.close()

    asyncio.run(main())

Diseño para testabilidad: acepta un ``browser_factory`` (mismo mecanismo que
``SessionActor``) para simular el navegador en tests sin abrir Camoufox.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from reaper.actions.actor import SessionActor
from reaper.actions.facebook import flows
from reaper.actions.models import ActionError, ActionResult, ActionType
from reaper.actions.storage.base import BaseActionStorage
from reaper.actions.storage.config import build_action_storage
from reaper.actions.utils import (
    cookies_differ,
    resolve_group,
    resolve_profile_url,
    validate_image_paths,
)
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

Flow = Callable[..., Awaitable[dict[str, Any]]]

DEFAULT_BASE_URL: str = "https://www.facebook.com/"


class ActionManager:
    """Orquesta acciones de escritura autenticadas y persiste sus resultados.

    Args:
        account_manager: Gestor de cuentas que provee cookies y rotación.
            Obligatorio para todas las acciones (la escritura requiere sesión).
        storage: Backend de persistencia de acciones. Si es ``None``, se
            construye desde la configuración de ``reaper.toml``
            (defecto: ``LocalActionStorage`` en ``data/actions``).
        headless: Ejecutar el navegador sin interfaz gráfica. Defecto: True.
        browser_type: Perfil de fingerprint (``"firefox"`` o ``"chromium"``).
        proxy_server / proxy_username / proxy_password: Configuración del
            proxy de salida para las sesiones.
        base_url: Raíz de navegación de Facebook (versión web). Defecto:
            ``https://www.facebook.com/``.
        browser_factory: Factory de navegador alternativo inyectable para
            tests (equivalente a ``SessionActor.use_browser_factory``).
    """

    def __init__(
        self,
        account_manager: Any | None = None,
        storage: BaseActionStorage | None = None,
        *,
        headless: bool = True,
        browser_type: str = "firefox",
        proxy_server: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        browser_factory: Any | None = None,
        debug: bool = False,
    ) -> None:
        self._account_manager = account_manager
        self._storage = storage or build_action_storage()
        self._base_url = base_url.rstrip("/")
        self._debug = debug

        self._actor = SessionActor(
            headless=headless,
            proxy_server=proxy_server,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            browser_type=browser_type,
            debug=debug,
        )
        if browser_factory is not None:
            self._actor.use_browser_factory(browser_factory)

        logger.debug(
            "ActionManager inicializado | storage=%s | headless=%s | base_url=%s",
            type(self._storage).__name__,
            headless,
            self._base_url,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # API pública de acciones
    # ─────────────────────────────────────────────────────────────────────────

    async def create_post(
        self,
        *,
        text: str | None = None,
        images: list[str | Path] | None = None,
        group: str | None = None,
        account: str | None = None,
        confirm_timeout: int = 15_000,
    ) -> ActionResult:
        """Publica un post de texto y/o imágenes en el muro (o en un grupo).

        Args:
            text:     Texto/caption de la publicación.
            images:   Rutas locales de imágenes a adjuntar (jpg, png, gif, ...).
            group:    Si se indica (ID o URL de grupo), la publicación se hace
                      directamente dentro de ese grupo en lugar del muro.
            account:  Cuenta a forzar (account_id, username o c_user).
                      ``None`` = rotación automática del AccountManager.
            confirm_timeout: Timeout (ms) de la confirmación DOM del post.

        Returns:
            ``ActionResult`` persistido con el resultado de la operación.

        Raises:
            ActionError: Si no hay cuenta autenticada, el post está vacío o
                las imágenes no son válidas.
        """
        text_clean = (text or "").strip()
        images_ok = [str(path) for path in validate_image_paths(
            [str(p) for p in images] if images else None
        )]
        if not text_clean and not images_ok:
            raise ActionError(
                "La publicación necesita al menos texto o una imagen."
            )

        if group is not None:
            group_url = resolve_group(group, self._base_url)
            account_profile, cookies = await self._resolve_account(account)
            return await self._execute(
                action_type=ActionType.GROUP_POST,
                account=account_profile,
                cookies=cookies,
                url=group_url,
                flow=flows.post_text_group,
                flow_kwargs={
                    "text": text_clean,
                    "image_paths": images_ok,
                    "confirm_timeout": confirm_timeout,
                },
                group=group,
            )

        account_profile, cookies = await self._resolve_account(account)
        profile_url = resolve_profile_url(cookies, self._base_url)
        return await self._execute(
            action_type=ActionType.POST,
            account=account_profile,
            cookies=cookies,
            url=profile_url or self._base_url,
            flow=flows.post_text,
            flow_kwargs={
                "text": text_clean,
                "image_paths": images_ok,
                "confirm_timeout": confirm_timeout,
            },
        )

    async def share_post(
        self,
        *,
        post_url: str,
        group: str,
        group_name: str | None = None,
        account: str | None = None,
        confirm_timeout: int = 15_000,
    ) -> ActionResult:
        """Comparte una publicación existente dentro de un grupo.

        Args:
            post_url:    URL de la publicación a compartir.
            group:       Grupo de destino (ID o URL). Se usa como texto de
                         búsqueda en el diálogo si no se da ``group_name``.
            group_name:  Nombre visible del grupo de destino (recomendado);
                         es el texto que se busca en el diálogo de compartir.
            account:     Cuenta a forzar. ``None`` = rotación automática.
            confirm_timeout: Timeout (ms) de la confirmación DOM.

        Returns:
            ``ActionResult`` persistido.

        Raises:
            ActionError: Si la URL del post o la cuenta son inválidas.
        """
        _validate_post_url(post_url)
        search_text = group_name or group
        account_profile, cookies = await self._resolve_account(account)
        return await self._execute(
            action_type=ActionType.SHARE_TO_GROUP,
            account=account_profile,
            cookies=cookies,
            url=post_url,
            flow=flows.share_post_to_group,
            flow_kwargs={
                "group_name": search_text,
                "confirm_timeout": confirm_timeout,
            },
            group=group,
        )

    async def comment(
        self,
        *,
        post_url: str,
        text: str,
        account: str | None = None,
        confirm_timeout: int = 15_000,
    ) -> ActionResult:
        """Responde con texto (comenta) una publicación existente.

        Args:
            post_url: URL de la publicación a comentar.
            text:     Texto del comentario.
            account:  Cuenta a forzar. ``None`` = rotación automática.
            confirm_timeout: Timeout (ms) de la confirmación DOM.

        Returns:
            ``ActionResult`` persistido.

        Raises:
            ActionError: Si el texto está vacío o la cuenta es inválida.
        """
        _validate_post_url(post_url)
        if not (text or "").strip():
            raise ActionError("El comentario no puede estar vacío.")

        account_profile, cookies = await self._resolve_account(account)
        return await self._execute(
            action_type=ActionType.COMMENT,
            account=account_profile,
            cookies=cookies,
            url=post_url,
            flow=flows.comment_on_post,
            flow_kwargs={
                "text": text.strip(),
                "confirm_timeout": confirm_timeout,
            },
        )

    async def like(
        self,
        *,
        post_url: str,
        account: str | None = None,
        confirm_timeout: int = 15_000,
    ) -> ActionResult:
        """Reacciona con "Me gusta" a una publicación existente.

        Args:
            post_url: URL de la publicación a reaccionar.
            account:  Cuenta a forzar. ``None`` = rotación automática.
            confirm_timeout: Timeout (ms) de la confirmación DOM.

        Returns:
            ``ActionResult`` persistido.

        Raises:
            ActionError: Si la URL del post o la cuenta son inválidas.
        """
        _validate_post_url(post_url)
        account_profile, cookies = await self._resolve_account(account)
        return await self._execute(
            action_type=ActionType.LIKE,
            account=account_profile,
            cookies=cookies,
            url=post_url,
            flow=flows.like_post,
            flow_kwargs={"confirm_timeout": confirm_timeout},
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Ciclo de vida
    # ─────────────────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Cierra los recursos del backend de almacenamiento de acciones."""
        await self._storage.close()

    @property
    def storage(self) -> BaseActionStorage:
        """Backend de persistencia en uso (para consultas del operador)."""
        return self._storage

    # ─────────────────────────────────────────────────────────────────────────
    # Internos
    # ─────────────────────────────────────────────────────────────────────────

    async def _resolve_account(self, account: str | None) -> tuple[Any, list[dict[str, Any]]]:
        """Resuelve una cuenta autenticada y carga sus cookies.

        Args:
            account: Identificador de cuenta a forzar, o ``None`` para rotación.

        Returns:
            Tupla ``(AccountProfile, cookies)``.

        Raises:
            ActionError: Si no hay AccountManager, no hay cuentas disponibles,
                la cuenta no es seleccionable, o no tiene cookies.
        """
        manager = self._account_manager
        if manager is None:
            raise ActionError(
                "Las acciones de escritura requieren un AccountManager "
                "con cuentas autenticadas."
            )

        profile = await manager.get_account_for_request("facebook", preferred=account)
        if profile is None:
            raise ActionError(
                "No hay cuentas de Facebook disponibles para ejecutar la acción."
            )
        if not profile.is_selectable:
            raise ActionError(
                f"La cuenta '{profile.username}' no es seleccionable "
                f"(estado: {profile.status.value})."
            )

        cookies = await manager.load_cookies("facebook", profile.account_id)
        if not cookies:
            raise ActionError(
                f"La cuenta '{profile.username}' no tiene cookies almacenadas."
            )

        logger.debug(
            "Cuenta resuelta | account_id=%s | username=%s",
            profile.account_id,
            profile.username,
        )
        return profile, cookies

    async def _execute(
        self,
        *,
        action_type: ActionType,
        account: Any,
        cookies: list[dict[str, Any]],
        url: str,
        flow: Flow,
        flow_kwargs: dict[str, Any],
        group: str | None = None,
    ) -> ActionResult:
        """Ejecuta un flujo en una sesión autenticada y persiste el resultado.

        Args:
            action_type: Tipo de acción (``ActionType``).
            account:     ``AccountProfile`` de la cuenta que actúa.
            cookies:     Cookies de sesión a inyectar.
            url:         URL de arranque de la sesión.
            flow:        Función de flujo (recibe ``page``).
            flow_kwargs: Argumentos del flujo.
            group:       Identificador de grupo (solo acciones de grupo).

        Returns:
            ``ActionResult`` persistido (regex ``status`` ok/error).
        """
        result = ActionResult(
            action=action_type.value,
            account_id=account.account_id,
            account_username=account.username,
            group=group,
            text=flow_kwargs.get("text"),
        )

        handle: Any | None = None
        try:
            async with self._actor.session(cookies=cookies, url=url) as handle:
                # El interceptor de red expone las respuestas GraphQL de
                # Facebook: los flujos lo usan para confirmar el post con la
                # respuesta real del servidor en vez del DOM.
                flow_kwargs = dict(flow_kwargs)
                interceptor = getattr(handle, "interceptor", None)
                if interceptor is not None:
                    flow_kwargs["traffic"] = interceptor
                outcome = await flow(handle.page, **flow_kwargs)
            # Tras salir del contexto, el actor ya rellenó updated_cookies.
            updated_cookies = handle.updated_cookies
            logger.debug(
                "Flujo completado | action=%s | status=%s | id=%s",
                action_type.value,
                outcome.get("status"),
                outcome.get("post_id"),
            )
        except ActionError as exc:
            updated_cookies = handle.updated_cookies if handle else None
            result.status = "error"
            result.error = str(exc)
            logger.warning(
                "Acción fallida en la UI | action=%s | account_id=%s | error=%s",
                action_type.value,
                account.account_id,
                exc,
            )
            outcome = {"status": "error"}
        except Exception as exc:
            updated_cookies = handle.updated_cookies if handle else None
            result.status = "error"
            result.error = f"Error inesperado al ejecutar '{action_type.value}': {exc}"
            logger.exception(
                "Fallo inesperado | action=%s | account_id=%s",
                action_type.value,
                account.account_id,
            )
            outcome = {"status": "error"}

        result.status = outcome.get("status", result.status)
        result.post_url = outcome.get("post_url")
        result.post_id = outcome.get("post_id")
        result.comment_id = outcome.get("comment_id")
        result.note = outcome.get("note")
        result.error = outcome.get("error", result.error)

        await self._persist(account, cookies, updated_cookies, result)
        return result

    async def _persist(
        self,
        account: Any,
        original_cookies: list[dict[str, Any]],
        updated_cookies: list[dict[str, Any]] | None,
        result: ActionResult,
    ) -> None:
        """Persiste el resultado, registra actividad y refresca cookies."""
        await self._storage.add(result)

        manager = self._account_manager
        if manager is None:
            return

        if result.status == "ok":
            await manager.record_success(account.account_id)
        else:
            await manager.record_failure(
                account.account_id, result.error or "unknown_action_error"
            )

        if (
            updated_cookies
            and cookies_differ(original_cookies, updated_cookies)
        ):
            try:
                await manager.import_cookies(account.account_id, updated_cookies)
                logger.debug(
                    "Cookies refrescadas | account_id=%s | cookies=%d",
                    account.account_id,
                    len(updated_cookies),
                )
            except Exception as exc:
                logger.warning(
                    "Error al refrescar cookies | account_id=%s | %s",
                    account.account_id,
                    exc,
                )


def _validate_post_url(post_url: str) -> None:
    """Valida que una URL de post sea una URL http(s) de Facebook."""
    if not isinstance(post_url, str) or not post_url.startswith(("http://", "https://")):
        raise ActionError(f"URL de post inválida: '{post_url}'.")
