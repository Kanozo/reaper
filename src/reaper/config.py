"""
reaper/config.py
================
Dataclass central de configuración para todas las sesiones de scraping.

``ScraperConfig`` es el único objeto que fluye entre todos los componentes
de la librería (``Reaper`` → ``FacebookScraper/InstagramScraper`` →
``ContentFetcher``). Centraliza la validación de parámetros en un único
punto para que los scrapers individuales no necesiten repetir lógica de
validación.

Diseño:
    - Inmutable en la práctica (no hay setters externos).
    - ``__post_init__`` valida las invariantes antes de que cualquier
      componente pueda usar el objeto.
    - Las propiedades ``has_proxy`` y ``proxy_config`` generan datos
      derivados sin necesidad de lógica extra en los scrapers.

Ejemplo::

    from reaper.config import ScraperConfig

    # Configuración mínima
    config = ScraperConfig(url="https://www.facebook.com/reel/123456")

    # Configuración completa
    config = ScraperConfig(
        url="https://www.instagram.com/p/abc123",
        headless=False,
        debug=True,
        screenshot=True,
        auto_scroll=True,
        infinity_scroll=False,
        proxy_server="http://myproxy:8080",
        proxy_username="user",
        proxy_password="secret",
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Import diferido solo para type-checking, evita circular import en runtime.
# AccountManager depende de modelos que no dependen de config, así que
# técnicamente no hay ciclo, pero el import diferido es más explícito
# y más rápido de cargar cuando no se usan cuentas.
if TYPE_CHECKING:
    from reaper.auth.account_manager import AccountManager


@dataclass
class ScraperConfig:
    """Configuración completa para una sesión de scraping.

    Es el único objeto de configuración que existe en la librería.
    Se crea en ``Reaper.scrape()`` y se pasa a todos los componentes
    sin modificación.

    Args:
        url: URL objetivo a scrapear. Único parámetro obligatorio.
            Debe empezar con ``http://`` o ``https://``.
        headless: Ejecutar Firefox sin interfaz gráfica.
            Recomendado ``True`` en producción. Defecto: True.
        debug: Activar logging DEBUG y guardar artefactos (HTML,
            screenshots, tráfico GraphQL) en ``artifacts_dir``.
            Defecto: False.
        screenshot: Capturar PNG del contenedor principal al cargar
            la página. Útil para diagnóstico visual. Defecto: False.
        auto_scroll: Scroll automático midiendo cambios de ``scrollHeight``
            del DOM. Recomendado para cargar comentarios paginados.
            Defecto: True.
        infinity_scroll: Scroll continuo monitorizando actividad de red.
            Para feeds con carga infinita (grupos, búsquedas).
            Requiere ``auto_scroll=True``. Defecto: False.
        proxy_server: URL del proxy, p.ej. ``"http://ip:8080"`` o
            ``"socks5://ip:1080"``. Defecto: None.
        proxy_username: Usuario del proxy. Ignorado si no hay ``proxy_server``.
            Defecto: None.
        proxy_password: Contraseña del proxy. Igual que ``proxy_username``.
            Defecto: None.
        artifacts_dir: Directorio para artefactos de debug (screenshots,
            HTML, tráfico). Solo se usa si ``debug=True``.
            Defecto: ``data/debug_artifacts``.
        results_dir: Directorio para guardar resultados JSON.
            Defecto: ``data/results``.

    Raises:
        ValueError: Si alguna invariante de configuración se viola:
            - URL vacía.
            - Esquema de URL inválido (no http/https).
            - Credenciales de proxy sin servidor de proxy.
            - ``infinity_scroll=True`` con ``auto_scroll=False``.
    """

    # ── Parámetro obligatorio ──────────────────────────────────────────────────
    url: str

    # ── Opciones del navegador ─────────────────────────────────────────────────
    headless: bool = True
    debug: bool = False
    screenshot: bool = False

    # ── Opciones de scroll ─────────────────────────────────────────────────────
    auto_scroll: bool = False
    infinity_scroll: bool = False

    # ── Opciones de proxy ──────────────────────────────────────────────────────
    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None

    # ── Rutas de almacenamiento ────────────────────────────────────────────────
    # Se usan `field(default_factory=...)` para evitar instancias compartidas
    artifacts_dir: Path = field(default_factory=lambda: Path("data/debug_artifacts"))
    results_dir: Path   = field(default_factory=lambda: Path("data/results"))

    # ── Gestión de cuentas autenticadas (opcional) ─────────────────────────────
    # None = modo anónimo (comportamiento original, 100% compatible hacia atrás).
    account_manager: Any = field(default=None, repr=False)

    # Identificador de la cuenta a forzar en cada petición de esta sesión.
    # Acepta account_id (UUID4), username o ID de usuario de la plataforma
    # (c_user / ds_user_id). None = rotación automática del AccountManager.
    preferred_account: str | None = None

    # ──────────────────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Valida las invariantes de configuración al construir el objeto.

        Se ejecuta automáticamente por ``@dataclass`` después de ``__init__``.
        Convierte ``artifacts_dir`` y ``results_dir`` a ``Path`` si se
        pasaron como strings.

        Raises:
            ValueError: Si alguna validación falla.
        """
        # Validar URL no vacía
        if not self.url or not self.url.strip():
            raise ValueError("'url' no puede estar vacía.")

        # Validar esquema HTTP/HTTPS
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(
                f"Esquema de URL inválido: '{self.url}'. "
                "Debe empezar con 'http://' o 'https://'."
            )

        # Validar que las credenciales de proxy requieren un servidor
        if (self.proxy_username or self.proxy_password) and not self.proxy_server:
            raise ValueError(
                "'proxy_username' y 'proxy_password' requieren que "
                "'proxy_server' esté configurado."
            )

        # Validar dependencia entre scroll options
        if self.infinity_scroll and not self.auto_scroll:
            raise ValueError(
                "'infinity_scroll=True' requiere 'auto_scroll=True'."
            )

        # Normalizar a Path (permite que el usuario pase strings)
        self.artifacts_dir = Path(self.artifacts_dir)
        self.results_dir   = Path(self.results_dir)

    # ──────────────────────────────────────────────────────────────────────────
    # Propiedades derivadas (datos calculados, sin estado extra)
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def has_proxy(self) -> bool:
        """True si hay un servidor proxy configurado."""
        return self.proxy_server is not None

    @property
    def has_account_manager(self) -> bool:
        """True si hay un AccountManager configurado para rotación de cuentas."""
        return self.account_manager is not None

    @property
    def proxy_config(self) -> dict[str, str] | None:
        """Devuelve el dict de proxy listo para Playwright, o None.

        Playwright espera la estructura::

            {"server": "...", "username": "...", "password": "..."}

        Returns:
            Dict con ``server`` (y opcionalmente ``username``/``password``),
            o None si no hay proxy configurado.
        """
        if not self.has_proxy:
            return None

        # proxy_server no es None aquí (garantizado por has_proxy)
        proxy: dict[str, str] = {"server": self.proxy_server}  # type: ignore[assignment]

        if self.proxy_username:
            proxy["username"] = self.proxy_username
        if self.proxy_password:
            proxy["password"] = self.proxy_password

        return proxy