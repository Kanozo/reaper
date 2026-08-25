"""
reaper/auth/login.py
====================
Flujo interactivo de autenticación: abre Firefox, espera login manual del usuario
y persiste las cookies resultantes en el ``AccountManager``.

¿Por qué login manual y no automatizado?
-----------------------------------------
Facebook e Instagram disponen de sistemas de detección de automatización muy
sofisticados. Rellenar el formulario programáticamente mediante Playwright activa
esas defensas y resulta en bloqueos de cuenta o CAPTCHAs irresolubles. El enfoque
correcto es que **un humano inicie sesión de forma normal**; el script solo se
encarga de capturar y persistir las cookies resultantes.

Dos modos de operación
-----------------------
1. **Cuenta nueva** (``mode="new"``):
   Crea el ``AccountProfile`` en el ``AccountManager`` y le asigna las cookies
   capturadas. El operador proporciona plataforma y nombre de usuario.

2. **Refresco de cookies** (``mode="refresh"``):
   Para una cuenta ya registrada (identificada por ``account_id``), captura una
   sesión fresca y reemplaza sus cookies. Resetea el contador de refresco y
   restaura el estado a ``ACTIVE``. Este modo responde al aviso
   ``NEEDS_REFRESH`` que emite el ``AccountManager``.

Flujo completo:
    1. Se abre Firefox visible (``headless=False``).
    2. El usuario completa el login manualmente en la ventana.
    3. El script detecta la URL de éxito post-login supervisando ``page.url``.
    4. Las cookies se extraen con ``context.cookies()``.
    5. Se persisten en el ``AccountManager`` vía ``add_account`` o ``import_cookies``.
    6. El navegador se cierra automáticamente.

Uso desde CLI::

    # Modo interactivo (hace preguntas)
    python -m reaper.auth.login

    # Cuenta nueva en Facebook
    python -m reaper.auth.login --platform facebook --username mi_usuario@gmail.com

    # Refresco de cookies para cuenta existente
    python -m reaper.auth.login --refresh --account-id 3f2e1a...

    # Especificar directorio de almacenamiento
    python -m reaper.auth.login --platform instagram --username ig_user --accounts-dir /data/accounts

Uso programático::

    from reaper.auth.login import run_login_new_account, run_login_refresh
    from reaper.auth import AccountManager

    manager = AccountManager()

    # Registrar cuenta nueva
    profile = await run_login_new_account(manager, "facebook", "usuario@gmail.com")

    # Refrescar cookies de cuenta existente
    ok = await run_login_refresh(manager, "3f2e1a-uuid...")
"""

from __future__ import annotations

import asyncio
import base64
import random
import re
import sys
from dataclasses import dataclass
from typing import Any, cast

from camoufox.async_api import AsyncCamoufox
from playwright.async_api import BrowserContext, Page

from reaper.auth.account_manager import AccountManager
from reaper.auth.models import AccountProfile, AccountStatus
from reaper.parsers.facebook.profile_parser import ProfileParser
from reaper.utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de configuración
# ─────────────────────────────────────────────────────────────────────────────

# URLs de inicio de login por plataforma.
LOGIN_URLS: dict[str, str] = {
    "facebook":  "https://www.facebook.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
}

# Fragmentos de URL que indican que el login fue completado con éxito.
# Se comprueban con ``in current_url`` (búsqueda de subcadena).
SUCCESS_URL_PATTERNS: dict[str, list[str]] = {
    "facebook": [
        "facebook.com/profile.php?id=",              # Feed principal post-login
        "facebook.com/home",
        "facebook.com/me",
        "facebook.com/feed",
        "facebook.com/?sk=h_chr",      # Feed clásico alternativo
    ],
    "instagram": [
        "instagram.com/?",             # Feed post-login
        "instagram.com/accounts/onetap",   # Pantalla "Guardar datos de acceso"
        "instagram.com/accounts/login/two_factor",  # 2FA — aún no terminó pero seguimos
    ],
}

# Tiempo máximo de espera para que el usuario complete el login (segundos).
# 5 minutos es suficiente para 2FA, verificaciones de dispositivo, etc.
LOGIN_TIMEOUT_SECONDS: int = 300

# Intervalo entre comprobaciones de URL (segundos).
URL_POLL_INTERVAL: float = 1.0

# Intervalo entre mensajes de progreso al usuario (segundos).
PROGRESS_REPORT_INTERVAL: int = 30

# Viewport del navegador visible.
BROWSER_VIEWPORT: dict[str, int] = {"width": 1280, "height": 800}


@dataclass
class LoginSessionData:
    """Datos capturados durante una sesión de login interactivo.

    Attributes:
        cookies:      Lista de dicts de cookies (formato Playwright).
        profile:      Resultado del ``ProfileParser`` sobre la página de
                      perfil post-login, o ``None`` si no se pudo capturar.
        avatar_base64: Avatar del usuario en base64 (binario), o ``None``
                      si no se pudo descargar.
        error:        Mensaje de error si el login no se completó.
    """

    cookies: list[dict[str, Any]]
    profile: dict[str, Any] | None = None
    avatar_base64: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.cookies)


# ─────────────────────────────────────────────────────────────────────────────
# Sección 1: API programática pública
# ─────────────────────────────────────────────────────────────────────────────


async def run_login_new_account(
    manager: AccountManager,
    platform: str,
    username: str,
    notes: str = "",
    password: str | None = None,
) -> AccountProfile | None:
    """Flujo completo de login para registrar una cuenta nueva.

    Combina la apertura del navegador, la captura de cookies y el registro
    en el ``AccountManager`` en una sola llamada. Es la forma recomendada
    de añadir una cuenta cuando el operador también va a hacer el login
    en el mismo momento.

    Tras completar el login, se navega a la página de perfil del usuario
    para capturar con ``ProfileParser`` los datos sociales (nombre, bio,
    seguidores, seguidos, amigos) y descargar el avatar en base64.

    Si prefieres registrar la cuenta primero y hacer el login más tarde,
    usa ``manager.add_account()`` y después ``run_login_refresh()``.

    Args:
        manager:  Instancia de ``AccountManager`` donde se registrará la cuenta.
        platform: ``"facebook"`` o ``"instagram"``.
        username: Nombre de usuario o email de la cuenta (solo informativo).
        notes:    Notas libres para identificar la cuenta.
        password: Contraseña de la cuenta (texto plano), o ``None``. Se
                  persiste junto al perfil para reutilizarla en flujos
                  que requieran autenticación directa.

    Returns:
        ``AccountProfile`` registrado y con cookies importadas,
        o ``None`` si el login falló o fue cancelado.

    Raises:
        ValueError: Si la plataforma no está soportada.
    """
    _validate_platform(platform)

    _print_login_header(platform, username, mode="new")

    # Capturar cookies y datos de perfil mediante el flujo interactivo.
    session = await _run_browser_login_flow(platform)

    if not session.ok:
        _print_error(session.error or "No se obtuvieron cookies. El login no fue completado.")
        return None

    # Extraer datos del perfil capturado (si está disponible).
    profile_data = session.profile or {}
    name = profile_data.get("name") or username
    description = profile_data.get("bio") or ""
    biography = profile_data.get("bio") or ""

    # Crear la cuenta en el AccountManager con las cookies recién capturadas.
    try:
        profile = await manager.add_account(
            platform=platform,
            username=username,
            name=name,
            avatar=session.avatar_base64,
            description=description,
            biography=biography,
            followers_count=profile_data.get("followers_count", 0),
            following_count=profile_data.get("following_count", 0),
            friends_count=profile_data.get("friends_count", 0),
            password=password,
            notes=notes,
            cookies=session.cookies,
        )
        _print_success(
            f"Cuenta registrada y sesión guardada.\n"
            f"   account_id : {profile.account_id}\n"
            f"   platform   : {profile.platform}\n"
            f"   username   : {profile.username}\n"
            f"   name       : {profile.name}\n"
            f"   cookies    : {len(session.cookies)}\n"
            f"   estado     : {profile.status.value}"
        )
        logger.info(
            "Cuenta nueva registrada vía login | account_id=%s | platform=%s | "
            "username=%s | name=%s | cookies=%d | followers=%d | following=%d | "
            "friends=%d | avatar=%s",
            profile.account_id, platform, username, profile.name,
            len(session.cookies), profile.followers_count,
            profile.following_count, profile.friends_count,
            "si" if profile.avatar else "no",
        )
        return profile

    except Exception as exc:
        _print_error(f"Error al guardar la cuenta en el AccountManager: {exc}")
        logger.exception("Error al registrar cuenta nueva post-login")
        return None


async def run_login_refresh(
    manager: AccountManager,
    account_id: str,
) -> bool:
    """Flujo de login para refrescar las cookies de una cuenta existente.

    Ideal para responder al aviso ``NEEDS_REFRESH`` que emite el
    ``AccountManager`` cuando se alcanza el umbral de peticiones.
    También útil para ``COOKIE_EXPIRED`` o ``RATE_LIMITED``.

    El flujo:
    1. Carga el perfil del ``AccountManager`` para confirmar que existe.
    2. Abre Firefox con la página de login de la plataforma correspondiente.
    3. El usuario completa el login manualmente.
    4. Las cookies nuevas reemplazan las antiguas vía ``manager.import_cookies()``.
    5. El estado vuelve a ``ACTIVE`` y el contador de refresco se resetea a 0.

    Args:
        manager:    Instancia de ``AccountManager`` donde está la cuenta.
        account_id: UUID4 de la cuenta a refrescar.

    Returns:
        ``True`` si el refresco fue exitoso, ``False`` si falló o fue cancelado.
    """
    # Cargar el perfil para validar que existe y obtener sus datos.
    profile = await manager.get_account(account_id)
    if profile is None:
        _print_error(
            f"No se encontró ninguna cuenta con account_id='{account_id}'.\n"
            f"   Usa 'manager.list_accounts()' para ver las cuentas disponibles."
        )
        return False

    _print_login_header(
        profile.platform,
        profile.username,
        mode="refresh",
        account_id=account_id,
        current_status=profile.status,
    )

    # Capturar cookies y datos de perfil mediante el flujo interactivo.
    session = await _run_browser_login_flow(profile.platform)

    if not session.ok:
        _print_error(session.error or "No se obtuvieron cookies. El refresco no fue completado.")
        return False

    # Reemplazar cookies en el AccountManager.
    try:
        ok = await manager.import_cookies(account_id, session.cookies)
        if ok:
            _print_success(
                f"Cookies refrescadas correctamente.\n"
                f"   account_id : {account_id}\n"
                f"   username   : {profile.username}\n"
                f"   cookies    : {len(session.cookies)}\n"
                f"   nuevo estado: active"
            )
            logger.info(
                "Cookies refrescadas vía login | account_id=%s | username=%s | "
                "cookies=%d",
                account_id, profile.username, len(session.cookies),
            )
        return ok

    except Exception as exc:
        _print_error(f"Error al importar cookies en el AccountManager: {exc}")
        logger.exception(
            "Error al importar cookies post-login | account_id=%s", account_id
        )
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Sección 2: Núcleo del flujo de navegación
# ─────────────────────────────────────────────────────────────────────────────


async def _run_browser_login_flow(
    platform: str,
) -> LoginSessionData:
    """Abre Firefox, espera el login manual y captura cookies + perfil.

    Esta función encapsula toda la interacción con Playwright. Es independiente
    del ``AccountManager`` — solo devuelve cookies crudas y datos de perfil.
    El llamador decide qué hacer con ellos.

    Tras detectar el login exitoso, navega a la página de perfil del usuario
    y la parsea con ``ProfileParser`` para obtener nombre, bio, seguidores,
    seguidos y amigos; también descarga el avatar como base64.

    Args:
        platform: ``"facebook"`` o ``"instagram"``.

    Returns:
        ``LoginSessionData`` con cookies y (si fue posible) el perfil capturado.
        ``error`` no es ``None`` si el flujo falló.
    """
    login_url = LOGIN_URLS[platform]

    # Se almacena el resultado en una variable en lugar de hacer ``return``
    # dentro del ``async with``: si la conexión con el driver muere (bug de
    # Playwright con pageError), el ``browser.close()`` del ``__aexit__``
    # puede lanzar y descartaría un login ya completado.
    result: LoginSessionData | None = None

    try:
        # ── Lanzar Camoufox visible ─────────────────────────────────────────
        # Camoufox tiene su propio launcher (NO usar async_playwright):
        # usa un Firefox modificado anti-detección con su propio build.
        # headless=False es obligatorio: el usuario debe poder interactuar
        # con el navegador para completar el login manualmente.
        async with AsyncCamoufox(
            headless=False,
            humanize=True,  # Simula comportamiento humano
            os=random.choice(["windows", "macos", "linux"]),  # OS aleatorio
        ) as browser:
            context: BrowserContext = await browser.new_context(
                viewport=BROWSER_VIEWPORT,
                locale="en-US",
                timezone_id="America/New_York",
            )

            page: Page = await context.new_page()

            # ── Navegar a la página de login ──────────────────────────────────
            print(f"\n   Navegando a {login_url}...")
            await page.goto(login_url, wait_until="domcontentloaded")

            # ── Esperar login manual ──────────────────────────────────────────
            print("   Esperando que completes el login en el navegador...\n")
            login_completed = await _wait_for_login_success(page, platform)

            if not login_completed:
                result = LoginSessionData(
                    cookies=[],
                    error=(
                        f"Tiempo de espera agotado "
                        f"({LOGIN_TIMEOUT_SECONDS // 60} min) sin detectar login exitoso."
                    ),
                )
            else:
                # ── Extraer cookies del contexto ──────────────────────────────
                # Se obtienen del contexto (no de la página) para incluir todas
                # las cookies de todos los dominios de la sesión.
                cookies: list[dict[str, Any]] = await context.cookies()

                if not cookies:
                    result = LoginSessionData(
                        cookies=[],
                        error="No se encontraron cookies en la sesión.",
                    )
                else:
                    print(f"\n   {len(cookies)} cookies capturadas.")

                    # ── Capturar perfil del usuario + avatar ─────────────────
                    profile_data, avatar_base64 = await _capture_profile(
                        context, page, platform
                    )
                    if profile_data:
                        print(
                            f"   Perfil capturado: {profile_data.get('name', '')} | "
                            f"seguidores={profile_data.get('followers_count', 0)} | "
                            f"seguidos={profile_data.get('following_count', 0)} | "
                            f"amigos={profile_data.get('friends_count', 0)}"
                        )
                    if avatar_base64:
                        print(f"   Avatar descargado ({len(avatar_base64) // 1024} KB).")

                    # Breve pausa para que el usuario vea el mensaje de éxito
                    # en pantalla antes de que el navegador se cierre solo.
                    await asyncio.sleep(2)

                    result = LoginSessionData(
                        cookies=cookies,
                        profile=profile_data,
                        avatar_base64=avatar_base64,
                    )

    except KeyboardInterrupt:
        # El usuario canceló con Ctrl+C mientras el navegador estaba abierto.
        print("\n\n   Login cancelado por el usuario.")
        logger.info("Login cancelado por el usuario (KeyboardInterrupt)")
        return LoginSessionData(cookies=[], error="Login cancelado por el usuario.")

    except Exception as exc:
        # Si ya había un resultado (login completado), un error al cerrar el
        # navegador no debe descartar la sesión capturada.
        if result is not None:
            logger.warning(
                "Login completado pero hubo un error al cerrar el navegador: %s",
                exc,
            )
        else:
            _print_error(f"Error inesperado durante el flujo de login: {exc}")
            logger.exception("Error inesperado en _run_browser_login_flow | platform=%s", platform)
            return LoginSessionData(cookies=[], error=str(exc))

    if result is None:
        return LoginSessionData(cookies=[], error="El flujo de login no produjo un resultado.")
    return result


async def _capture_profile(
    context: BrowserContext,
    page: Page,
    platform: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Captura el perfil del usuario recién autenticado y su avatar.

    Navega a la página de perfil del usuario logueado, extrae su HTML
    renderizado y lo parsea con ``ProfileParser`` para obtener los datos
    sociales. Después descarga el avatar (URL del parser) y lo codifica
    en base64 para persistirlo junto a la cuenta.

    Facebook: navega a ``facebook.com/me`` (redirige al perfil del usuario).
    Instagram: usa ``instagram.com/{username}`` cuando se conoce el username.

    Args:
        context: Contexto del navegador con la sesión autenticada.
        page:    Página activa de Playwright.
        platform: ``"facebook"`` o ``"instagram"``.

    Returns:
        Tupla ``(profile_data, avatar_base64)``. ``profile_data`` es el
        dict del ``ProfileParser`` (o ``None`` si falló); ``avatar_base64``
        es el avatar en base64 (o ``None`` si no se pudo descargar).
    """
    try:
        if platform == "facebook":
            # Si ya estamos en una página de perfil (p.ej. tras el login la
            # URL es profile.php?id=...), capturar directamente SIN navegar.
            # Navegar a /me dispara un bug del driver de Playwright/Camoufox
            # (pageError con location undefined) que mata la conexión.
            if not _is_fb_profile_url(page.url):
                await page.goto(
                    "https://www.facebook.com/me", wait_until="domcontentloaded"
                )
                await page.wait_for_timeout(2500)
        else:
            # Instagram: intentar con /accounts/edit o capturar desde la página actual.
            try:
                username = await _extract_username_from_url(page.url)
                if username:
                    await page.goto(
                        f"https://www.instagram.com/{username}/",
                        wait_until="domcontentloaded",
                    )
                    await page.wait_for_timeout(2500)
            except Exception:
                return None, None

        html_content = await page.content()
        final_url = page.url

        parser = ProfileParser(
            html_content=html_content,
            final_url=final_url,
            original_url=final_url,
        )
        profile_data = parser.parse()

        # Descargar avatar desde la URL capturada por el parser.
        # El fallo de la descarga NO debe descartar el perfil completo:
        # se intenta con un timeout corto y fallbacks, y en el peor caso
        # se devuelve el perfil con avatar=None.
        avatar_url = profile_data.get("avatar") or ""
        avatar_base64 = await _download_avatar(page, context, avatar_url)

        return profile_data, avatar_base64

    except Exception as exc:
        logger.warning("No se pudo capturar el perfil post-login: %s", exc)
        return None, None


async def _download_avatar(
    page: Page,
    context: BrowserContext,
    avatar_url: str,
) -> str | None:
    """Descarga el avatar y lo devuelve en base64 (o ``None`` si falla).

    Intentos en orden:
    1. ``context.request.get`` (10s de timeout) — rápido, pero puede fallar
       si el CDN de Facebook exige cookies/referer de la sesión.
    2. ``fetch`` dentro de la página (15s) — hereda cookies, headers y
       referer del navegador, por lo que es el método más fiable.
    3. Página temporal con ``await page.goto(avatar_url)`` — último recurso.

    Args:
        page:       Página activa (con la sesión autenticada).
        context:    Contexto del navegador.
        avatar_url: URL del avatar a descargar.

    Returns:
        Avatar codificado en base64, o ``None`` si no se pudo descargar.
    """
    if not avatar_url:
        return None

    # 1) APIRequestContext del contexto
    try:
        response = await context.request.get(avatar_url, timeout=10_000)
        if response.ok:
            data = await response.body()
            return base64.b64encode(data).decode("ascii")
    except Exception as exc:
        logger.debug("Avatar vía context.request falló: %s", exc)

    # 2) fetch dentro de la página (hereda cookies/referer de la sesión)
    try:
        b64 = cast(
            str | None,
            await page.evaluate(
                """async (url) => {
                    const res = await fetch(url, { credentials: 'include' });
                    if (!res.ok) return null;
                    const buf = await res.arrayBuffer();
                    let bin = '';
                    const bytes = new Uint8Array(buf);
                    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                    return btoa(bin);
                }""",
                avatar_url,
            ),
        )
        if b64:
            return b64
    except Exception as exc:
        logger.debug("Avatar vía fetch in-page falló: %s", exc)

    # 3) Navegar a la URL de la imagen (hereda cookies/referer) y usar
    #    la response del goto directamente.
    try:
        new_page = await context.new_page()
        try:
            img_response = await new_page.goto(avatar_url, timeout=15_000)
            if img_response and img_response.ok:
                body = await img_response.body()
                if body:
                    return base64.b64encode(body).decode("ascii")
        finally:
            await new_page.close()
    except Exception as exc:
        logger.warning("Avatar vía página temporal falló: %s", exc)

    return None


def _is_fb_profile_url(url: str) -> bool:
    """Indica si la URL ya es la página de perfil de un usuario de Facebook.

    Evita navegar a ``/me`` cuando tras el login ya estamos en el perfil
    (p. ej. ``profile.php?id=...``). Navegar de nuevo dispara un bug del
    driver de Playwright/Camoufox (``pageError`` con ``location`` undefined)
    que corta la conexión con el navegador.

    Args:
        url: URL actual del navegador.

    Returns:
        ``True`` si la URL es un perfil personal de Facebook.
    """
    if "profile.php?id=" in url:
        return True
    if "facebook.com/me" in url:
        return True
    # Vanity URL (p. ej. https://www.facebook.com/zurdobo7 o
    # https://www.facebook.com/EstefaniaQuesada91/) — último segmento
    # de la ruta (con barra final opcional) que no sea una sección
    # reservada de la plataforma.
    match = re.search(r"facebook\.com/([^/?#]+)/?(?:[?#]|$)", url)
    if not match:
        return False
    slug = match.group(1).lower()
    reserved = {
        "login", "home", "feed", "watch", "messages", "notifications",
        "friends", "groups", "marketplace", "pages", "events", "reels",
        "settings", "help", "search", "profile.php", "stories", "saved",
    }
    return slug not in reserved


async def _extract_username_from_url(url: str) -> str | None:
    """Extrae el username de una URL de Instagram (o None)."""
    match = re.search(r"instagram\.com/([^/?#]+)", url)
    return match.group(1) if match else None


async def _wait_for_login_success(
    page: Page,
    platform: str,
    timeout_seconds: int = LOGIN_TIMEOUT_SECONDS,
) -> bool:
    """Sondea la URL del navegador hasta detectar un patrón de login exitoso.

    Comprueba cada ``URL_POLL_INTERVAL`` segundos. Emite mensajes de progreso
    cada ``PROGRESS_REPORT_INTERVAL`` segundos para que el usuario sepa
    que el script sigue en ejecución.

    Args:
        page:            Página de Playwright activa.
        platform:        Plataforma — determina qué patrones buscar.
        timeout_seconds: Tiempo máximo de espera.

    Returns:
        ``True`` si se detectó una URL de éxito antes del timeout.
        ``False`` si se agotó el tiempo sin detectar login.
    """
    patterns = SUCCESS_URL_PATTERNS[platform]
    elapsed: float = 0.0

    while elapsed < timeout_seconds:
        current_url = page.url

        # Comprobar si la URL actual contiene algún patrón de éxito.
        matched = any(pattern in current_url for pattern in patterns)
        # Facebook: además de los patrones fijos, detectar perfiles con
        # vanity URL (p. ej. /EstefaniaQuesada91) o /profile.php?id=...,
        # que indican que el login redirigió al perfil del usuario.
        if not matched and platform == "facebook":
            matched = _is_fb_profile_url(current_url)

        if matched:
            print(f"   Login completado. URL detectada: {current_url[:80]}")
            return True

        await asyncio.sleep(URL_POLL_INTERVAL)
        elapsed += URL_POLL_INTERVAL

        # Mensaje de progreso periódico.
        if int(elapsed) % PROGRESS_REPORT_INTERVAL == 0:
            remaining = int(timeout_seconds - elapsed)
            print(
                f"   Esperando login... {int(elapsed)}s transcurridos, "
                f"{remaining}s restantes. "
                f"URL actual: {current_url[:60]}"
            )

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Sección 3: Interfaz de línea de comandos
# ─────────────────────────────────────────────────────────────────────────────


async def _cli_main() -> int:
    """Punto de entrada del CLI con argumentos posicionales y modo interactivo.

    Flags soportados::

        --platform       facebook | instagram
        --username       nombre de usuario o email
        --refresh        modo refresco (requiere --account-id)
        --account-id     UUID4 de la cuenta a refrescar
        --accounts-dir   directorio raíz del AccountManager
        --threshold      umbral de refresco de cookies (defecto: 50)

    Retorna el código de salida (0 = éxito, 1 = error).
    """
    args = sys.argv[1:]

    # ── Parseo de argumentos (sin argparse para mantener 0 dependencias extra)
    def _get_flag(flag: str) -> str | None:
        """Retorna el valor del flag --flag valor, o None si no está."""
        try:
            idx = args.index(flag)
            return args[idx + 1] if idx + 1 < len(args) else None
        except ValueError:
            return None

    def _has_flag(flag: str) -> bool:
        return flag in args

    refresh_mode  = _has_flag("--refresh")
    platform      = _get_flag("--platform")
    username      = _get_flag("--username")
    account_id    = _get_flag("--account-id")
    accounts_dir  = _get_flag("--accounts-dir") or "data/accounts"
    threshold_raw = _get_flag("--threshold")
    threshold     = int(threshold_raw) if threshold_raw else 50

    # ── Crear AccountManager ──────────────────────────────────────────────────
    manager = AccountManager(
        accounts_dir=accounts_dir,
        cookie_refresh_threshold=threshold,
    )

    # ── Modo refresco ─────────────────────────────────────────────────────────
    if refresh_mode:
        if not account_id:
            # Si no se pasó --account-id, mostrar cuentas disponibles y preguntar.
            print("\nCuentas registradas:")
            all_accounts = await manager.list_accounts()
            if not all_accounts:
                _print_error(
                    "No hay cuentas registradas en el AccountManager.\n"
                    "Usa el modo normal (sin --refresh) para añadir la primera cuenta."
                )
                return 1

            for acc in all_accounts:
                print(
                    f"   [{acc.platform:10s}] {acc.account_id[:8]}...  "
                    f"{acc.username:30s}  estado={acc.status.value}"
                )

            account_id = input(
                "\nIntroduce el account_id completo de la cuenta a refrescar: "
            ).strip()

        if not account_id:
            _print_error("account_id no puede estar vacío.")
            return 1

        success = await run_login_refresh(manager, account_id)
        return 0 if success else 1

    # ── Modo cuenta nueva ─────────────────────────────────────────────────────
    if not platform:
        print(f"\nPlataformas disponibles: {', '.join(LOGIN_URLS.keys())}")
        platform = input("Introduce la plataforma: ").lower().strip()

    if platform not in LOGIN_URLS:
        _print_error(
            f"Plataforma '{platform}' no reconocida. "
            f"Opciones: {', '.join(LOGIN_URLS.keys())}"
        )
        return 1

    if not username:
        label = "email o nombre de usuario"
        username = input(f"Introduce el {label} de la cuenta: ").strip()

    if not username:
        _print_error("El nombre de usuario no puede estar vacío.")
        return 1

    profile = await run_login_new_account(manager, platform, username)
    return 0 if profile is not None else 1


# ─────────────────────────────────────────────────────────────────────────────
# Sección 4: Helpers de presentación
# ─────────────────────────────────────────────────────────────────────────────


def _validate_platform(platform: str) -> None:
    if platform not in LOGIN_URLS:
        raise ValueError(
            f"Plataforma '{platform}' no soportada. "
            f"Opciones válidas: {', '.join(LOGIN_URLS.keys())}"
        )


def _print_login_header(
    platform: str,
    username: str,
    mode: str,
    account_id: str | None = None,
    current_status: AccountStatus | None = None,
) -> None:
    """Imprime la cabecera del flujo de login con instrucciones claras."""
    mode_label = "CUENTA NUEVA" if mode == "new" else "REFRESCO DE COOKIES"
    sep = "=" * 62

    print(f"\n{sep}")
    print(f"  REAPER — LOGIN INTERACTIVO — {mode_label}")
    print(sep)
    print(f"  Plataforma : {platform.upper()}")
    print(f"  Usuario    : {username}")
    if account_id:
        print(f"  account_id : {account_id}")
    if current_status:
        print(f"  Estado actual: {current_status.value}")
    print(sep)
    print()
    print("  INSTRUCCIONES:")
    print("  1. Se abrirá Firefox en unos segundos.")
    print("  2. Inicia sesión manualmente con tus credenciales.")
    print("  3. Completa cualquier verificación (2FA, captcha, SMS, etc.).")
    print("  4. Una vez en tu feed principal, el script detectará")
    print("     el login y cerrará el navegador automáticamente.")
    print(f"  5. Tiempo máximo de espera: {LOGIN_TIMEOUT_SECONDS // 60} minutos.")
    print()
    print("  IMPORTANTE: No cierres el navegador manualmente.")
    print(f"{sep}\n")


def _print_success(message: str) -> None:
    sep = "-" * 62
    print(f"\n{sep}")
    print(f"  OK  {message}")
    print(f"{sep}\n")


def _print_error(message: str) -> None:
    sep = "-" * 62
    print(f"\n{sep}")
    print(f"  ERROR  {message}")
    print(f"{sep}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(_cli_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nOperación cancelada por el usuario.")
        sys.exit(0)
