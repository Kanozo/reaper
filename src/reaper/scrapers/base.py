"""
reaper/scrapers/base.py
=======================
Clase base abstracta para todos los scrapers de plataforma.

Extiende el comportamiento original con ``_fetch_with_account()``: un wrapper
que selecciona la mejor cuenta disponible del ``AccountManager``, inyecta sus
cookies en el ``ContentFetcher``, registra el resultado en la actividad de la
cuenta y aplica el refresco automático de cookies si el servidor devolvió
versiones actualizadas.

Compatibilidad hacia atrás garantizada:
    Si ``config.account_manager is None`` (comportamiento por defecto),
    ``_fetch_with_account()`` delega directamente en ``_fetch()`` sin ningún
    cambio en el flujo.

Refresco automático de cookies:
    Después de cada petición autenticada exitosa, ``_fetch_with_account()``
    compara las cookies originales con las devueltas por el servidor
    (``fetch_result.updated_cookies``). Si difieren — porque el servidor
    extendió TTLs o rotó tokens de seguridad — las persiste en disco
    automáticamente vía ``manager.import_cookies()``. Esto extiende la vida
    de la sesión de forma indefinida mientras la plataforma no la invalide
    remotamente, sin necesidad de login manual periódico.

    El ``cookie_refresh_threshold`` del ``AccountManager`` actúa como
    safety net: si por algún motivo el servidor no devuelve cookies
    actualizadas durante N peticiones, el sistema emite un WARNING y
    solicita intervención manual.

Jerarquía::

    BaseScraper  (abstracto)
    ├── FacebookScraper
    └── InstagramScraper
"""

from abc import ABC, abstractmethod
from typing import Any

from reaper.config import ScraperConfig
from reaper.utils.logger import get_logger


class BaseScraper(ABC):
    """Clase base abstracta para scrapers de plataforma.

    Las subclases deben implementar ``run()`` y ``_fetch()``.
    El método ``_fetch_with_account()`` está implementado aquí y es el punto
    de entrada principal para fetches que requieren rotación de cuentas y
    refresco automático de cookies.

    Args:
        config: Configuración validada de la sesión de scraping.
    """

    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self.logger = get_logger(self.__class__.__module__)

        # ID de la cuenta que se está usando en la sesión actual.
        # None si se usa modo anónimo. Se asigna en _fetch_with_account().
        self._active_account_id: str | None = None

    @abstractmethod
    async def run(self) -> dict[str, Any]:
        """Ejecuta la sesión completa de scraping.

        Returns:
            ``dict[str, Any]`` con los datos extraídos.
            Debe incluir siempre: ``url``, ``platform``, ``status``.

        Raises:
            ScrapingError: Si el proceso falla irrecuperablemente.
        """
        ...

    @abstractmethod
    async def _fetch(
        self,
        override_url: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Fetch base: crea ContentFetcher y ejecuta la navegación.

        Cada scraper implementa este método con sus parámetros específicos
        (p.ej. InstagramScraper desactiva infinity_scroll siempre).

        Args:
            override_url: URL alternativa a la de ``config.url``.
                          Se usa en fetches secundarios (merge reel↔video).
            cookies:      Lista de cookies en formato Playwright a inyectar
                          en el contexto del navegador antes de navegar.
                          ``None`` = sin cookies (modo anónimo).

        Returns:
            ``FetchResult`` con HTML renderizado, tráfico, metadatos y
            ``updated_cookies`` con las cookies post-navegación si la
            sesión era autenticada.
        """
        ...

    def _base_result(self, platform: str) -> dict[str, Any]:
        """Genera el esqueleto mínimo garantizado para cualquier resultado.

        Args:
            platform: Nombre de la plataforma (``"facebook"`` o ``"instagram"``).

        Returns:
            Dict con ``url``, ``platform`` y ``status="ok"``.
        """
        return {
            "url": self.config.url,
            "platform": platform,
            "status": "ok",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Lógica de rotación de cuentas y refresco automático de cookies
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch_with_account(
        self,
        platform: str,
        override_url: str | None = None,
    ) -> Any:
        """Fetch con selección de cuenta, registro de actividad y auto-refresh.

        Coordina el flujo completo de una petición autenticada:

        1. Consulta al ``AccountManager`` por la mejor cuenta disponible.
        2. Lee las cookies de disco para esa cuenta.
        3. Ejecuta el fetch con las cookies inyectadas en el contexto Playwright.
        4. Registra éxito o fallo en la actividad de la cuenta.
        5. Si el fetch fue exitoso, compara las cookies originales con las
           devueltas por el servidor post-navegación. Si difieren, las persiste
           automáticamente — el servidor habrá extendido TTLs o rotado tokens
           de seguridad, alargando la vida de la sesión.

        Si ``config.account_manager`` es ``None``, o si no hay cuentas
        disponibles, delega en ``_fetch()`` directamente en modo anónimo.
        Este fallback garantiza compatibilidad total hacia atrás.

        Args:
            platform:     ``"facebook"`` o ``"instagram"``.
            override_url: URL alternativa (raramente necesaria en el fetch primario).

        Returns:
            ``FetchResult`` igual que ``_fetch()``.
        """
        manager = self.config.account_manager

        # ── Modo anónimo: sin AccountManager → flujo original sin cambios ────
        if manager is None:
            return await self._fetch(override_url=override_url)

        # ── Selección de cuenta ───────────────────────────────────────────────
        # Si config.preferred_account está definido, se fuerza esa cuenta
        # (por account_id, username o ID de usuario de la plataforma);
        # si no, el AccountManager aplica la rotación normal.
        account = await manager.get_account_for_request(
            platform,
            preferred=self.config.preferred_account,
        )

        if account is None:
            self.logger.warning(
                "AccountManager activo pero sin cuentas disponibles para "
                "platform=%s — continuando en modo anónimo",
                platform,
            )
            return await self._fetch(override_url=override_url)

        # ── Cargar cookies de disco ───────────────────────────────────────────
        original_cookies = await manager.load_cookies(platform, account.account_id)

        if not original_cookies:
            self.logger.warning(
                "Cuenta seleccionada sin cookies en disco | "
                "account_id=%s | username=%s — continuando en modo anónimo",
                account.account_id,
                account.username,
            )
            return await self._fetch(override_url=override_url)

        # ── Fetch autenticado ─────────────────────────────────────────────────
        self._active_account_id = account.account_id

        self.logger.info(
            "Fetch autenticado | platform=%s | username=%s | "
            "cookies=%d | peticiones_cuenta=%d | score_aprox=%.0f%%",
            platform,
            account.username,
            len(original_cookies),
            account.activity.total_requests,
            account.activity.success_rate * 100,
        )

        fetch_result = await self._fetch(
            override_url=override_url,
            cookies=original_cookies,
        )

        # ── Registrar resultado en la actividad de la cuenta ─────────────────
        if fetch_result.success:
            await manager.record_success(account.account_id)

            # ── Auto-refresh de cookies ───────────────────────────────────────
            # Después de una navegación exitosa, el servidor puede haber
            # devuelto cabeceras Set-Cookie con tokens actualizados o TTLs
            # extendidos. Si las cookies cambiaron, las persistimos de
            # inmediato para alargar la vida de la sesión sin login manual.
            # import_cookies() resetea el contador requests_since_refresh a 0,
            # manteniendo el threshold como safety net en lugar de señal habitual.
            updated_cookies = fetch_result.updated_cookies
            if updated_cookies and _cookies_differ(original_cookies, updated_cookies):
                try:
                    await manager.import_cookies(account.account_id, updated_cookies)
                    self.logger.debug(
                        "Cookies auto-refrescadas | account_id=%s | username=%s | "
                        "cookies_previas=%d | cookies_nuevas=%d",
                        account.account_id,
                        account.username,
                        len(original_cookies),
                        len(updated_cookies),
                    )
                except Exception as exc:
                    # El auto-refresh es best-effort: un fallo aquí no debe
                    # interrumpir el flujo de scraping. El threshold actuará
                    # como safety net si el problema persiste.
                    self.logger.warning(
                        "Error al auto-refrescar cookies | account_id=%s | error=%s",
                        account.account_id,
                        exc,
                    )
        else:
            error_msg = fetch_result.error or "fetch_failed_unknown"
            await manager.record_failure(account.account_id, error_msg)
            self.logger.debug(
                "Fallo registrado en cuenta | account_id=%s | error=%s",
                account.account_id,
                error_msg,
            )

        return fetch_result


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados del módulo
# ─────────────────────────────────────────────────────────────────────────────


def _cookies_differ(
    original: list[dict],
    updated: list[dict],
) -> bool:
    """Compara dos listas de cookies para detectar cambios relevantes.

    Compara únicamente ``value`` y ``expires`` de cada cookie por nombre,
    ignorando campos de metadatos irrelevantes (``httpOnly``, ``sameSite``,
    etc.) que no afectan la validez de la sesión.

    Considera que hay cambio si:
    - Algún ``value`` difiere (token rotado por el servidor).
    - Algún ``expires`` aumentó (TTL extendido).
    - El conjunto de nombres de cookies cambió (cookie nueva o eliminada).

    Args:
        original: Cookies cargadas de disco antes del fetch.
        updated:  Cookies leídas del contexto Playwright después del fetch.

    Returns:
        ``True`` si hay alguna diferencia relevante, ``False`` si son idénticas.
    """
    # Indexar por nombre para comparación O(n) en lugar de O(n²).
    original_map: dict[str, tuple] = {
        c["name"]: (c.get("value", ""), c.get("expires", 0))
        for c in original
    }
    updated_map: dict[str, tuple] = {
        c["name"]: (c.get("value", ""), c.get("expires", 0))
        for c in updated
    }
    return original_map != updated_map
