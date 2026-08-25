"""
reaper/cli.py
=============
Punto de entrada de la interfaz de línea de comandos (CLI).

Registrado como comando del sistema en ``pyproject.toml``::

    [project.scripts]
    reaper = "reaper.cli:main"

Después de ``pip install -e .`` el comando ``reaper`` estará disponible
globalmente en el entorno Python activo.

Uso::

    # Mínimo
    reaper https://www.facebook.com/reel/123456

    # Con opciones
    reaper https://www.instagram.com/p/abc123 --no-headless --debug

    # Con proxy
    reaper https://fb.com/groups/1/posts/2 \\
        --proxy http://myproxy:8080 \\
        --proxy-user usuario \\
        --proxy-pass contraseña

    # Guardar resultado en JSON
    reaper https://fb.com/reel/123 --output resultado.json --indent 4

    # Ver todas las opciones
    reaper --help

Códigos de salida:
    0 → Éxito
    2 → URL inválida (ValueError)
    3 → Plataforma no soportada (UnsupportedPlatformError)
    4 → Error en runtime del scraping (ScrapingError)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from reaper import (
    ActionError,
    ActionManager,
    Reaper,
    ScrapingError,
    UnsupportedPlatformError,
    __version__,
)
from reaper.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Construye y devuelve el parser de argumentos de la CLI.

    Returns:
        ``argparse.ArgumentParser`` configurado con todos los argumentos
        organizados en grupos temáticos.
    """
    parser = argparse.ArgumentParser(
        prog="reaper",
        description="Scraper de Facebook e Instagram. Extrae datos de posts, reels, grupos y perfiles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ejemplos:
  reaper https://www.facebook.com/watch?v=123456
  reaper https://www.instagram.com/p/abc123 --no-headless --debug
  reaper https://fb.com/groups/123/posts/456 --proxy http://proxy:8080
  reaper https://fb.com/reel/123 --output resultado.json --indent 4
  reaper https://fb.com/reel/123 --no-scroll
        """,
    )

    # Argumento posicional: URL (único obligatorio)
    parser.add_argument(
        "url",
        help="URL de Facebook o Instagram a scrapear.",
    )

    # ── Opciones del navegador ────────────────────────────────────────────────
    browser_group = parser.add_argument_group("opciones del navegador")
    browser_group.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        default=True,
        help="Mostrar la ventana del navegador (útil para depuración). Por defecto: headless.",
    )
    browser_group.add_argument(
        "--screenshot",
        action="store_true",
        default=False,
        help="Capturar screenshot del contenedor principal durante el scraping.",
    )

    # ── Opciones de scroll ────────────────────────────────────────────────────
    scroll_group = parser.add_argument_group("opciones de scroll")
    scroll_group.add_argument(
        "--no-scroll",
        dest="auto_scroll",
        action="store_false",
        default=True,
        help="Desactivar el scroll automático. Por defecto: activado.",
    )
    scroll_group.add_argument(
        "--infinity-scroll",
        action="store_true",
        default=False,
        help="Scroll continuo monitorizando tráfico de red. Para feeds infinitos.",
    )

    # ── Opciones de proxy ─────────────────────────────────────────────────────
    proxy_group = parser.add_argument_group("opciones de proxy")
    proxy_group.add_argument(
        "--proxy",
        dest="proxy_server",
        metavar="URL",
        default=None,
        help="Servidor proxy, p.ej. 'http://ip:8080' o 'socks5://ip:1080'.",
    )
    proxy_group.add_argument(
        "--proxy-user",
        dest="proxy_username",
        metavar="USUARIO",
        default=None,
        help="Usuario para autenticación del proxy.",
    )
    proxy_group.add_argument(
        "--proxy-pass",
        dest="proxy_password",
        metavar="CONTRASEÑA",
        default=None,
        help="Contraseña del proxy.",
    )

    # ── Opciones de salida ────────────────────────────────────────────────────
    output_group = parser.add_argument_group("opciones de salida")
    output_group.add_argument(
        "--output", "-o",
        type=Path,
        metavar="FICHERO",
        default=None,
        help="Guardar resultado como JSON en FICHERO (por defecto imprime en stdout).",
    )
    output_group.add_argument(
        "--indent",
        type=int,
        default=2,
        metavar="N",
        help="Número de espacios de indentación del JSON. Defecto: 2.",
    )

    # ── Opciones de cuenta ────────────────────────────────────────────────────
    account_group = parser.add_argument_group("opciones de cuenta")
    account_group.add_argument(
        "--account",
        metavar="IDENTIFICADOR",
        default=None,
        help="Forzar una cuenta concreta: account_id (UUID), username o ID de "
             "usuario de la plataforma (c_user/ds_user_id). Requiere "
             "account_manager configurado; si no, se ignora y se usa modo anónimo.",
    )

    # ── Opciones generales ────────────────────────────────────────────────────
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Activar logging detallado y guardar artefactos en data/debug_artifacts/.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"reaper {__version__}",
    )

    return parser


def _write_output(
    result: dict[str, Any],
    output_path: Path | None,
    indent: int,
) -> None:
    """Serializa el resultado a JSON y lo escribe en fichero o stdout.

    Usa ``default=str`` para que tipos no serializables (como ``datetime``)
    se conviertan a string automáticamente.

    Args:
        result: Diccionario de resultado del scraper.
        output_path: Ruta del fichero de salida, o None para stdout.
        indent: Número de espacios de indentación del JSON.
    """
    serialized = json.dumps(result, ensure_ascii=False, indent=indent, default=str)

    if output_path is None:
        # Imprimir en stdout directamente
        print(serialized)
        return

    # Crear directorios intermedios si no existen
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    logger.info("Resultado guardado en: %s", output_path)


def _exit_error(message: str, code: int = 1) -> NoReturn:
    """Imprime un error en stderr y termina el proceso.

    Args:
        message: Mensaje de error descriptivo para el usuario.
        code: Código de salida del proceso (default: 1).
    """
    print(f"[reaper] error: {message}", file=sys.stderr)
    sys.exit(code)


# ─────────────────────────────────────────────────────────────────────────────
# CLI de acciones de escritura (reaper action ...)
# ─────────────────────────────────────────────────────────────────────────────


def build_action_parser() -> argparse.ArgumentParser:
    """Construye el parser de ``reaper action <verb>``.

    Returns:
        ``argparse.ArgumentParser`` con subcomandos post/share/comment/like.
    """
    parser = argparse.ArgumentParser(
        prog="reaper action",
        description="Acciones de escritura autenticadas sobre Facebook.",
        epilog="""
ejemplos:
  reaper action post --text "Hola desde Reaper" --images foto.jpg otro.png
  reaper action post --text "Nota en el grupo" --group 123456789 --account mi_cuenta
  reaper action share --post-url https://fb.com/... --group 123456789 --group-name "Mi Grupo"
  reaper action comment --post-url https://fb.com/... --text "Excelente post"
  reaper action like --post-url https://fb.com/...
        """,
    )
    subparsers = parser.add_subparsers(dest="verb", required=True)

    post_p = subparsers.add_parser("post", help="Publicar texto/imágenes en el muro o un grupo.")
    post_p.add_argument("--text", default=None, help="Texto/caption de la publicación.")
    post_p.add_argument("--images", nargs="+", metavar="ARCHIVO", default=None,
                        help="Imágenes locales a adjuntar (jpg, png, ...).")
    post_p.add_argument("--group", default=None,
                        help="ID o URL de un grupo para publicar directamente en él.")

    share_p = subparsers.add_parser("share", help="Compartir una publicación en un grupo.")
    share_p.add_argument("--post-url", required=True, help="URL del post a compartir.")
    share_p.add_argument("--group", required=True, help="Grupo de destino (ID o URL).")
    share_p.add_argument("--group-name", default=None,
                         help="Nombre visible del grupo (texto de búsqueda en el diálogo).")

    comment_p = subparsers.add_parser("comment", help="Responder con texto a una publicación.")
    comment_p.add_argument("--post-url", required=True, help="URL del post a comentar.")
    comment_p.add_argument("--text", required=True, help="Texto del comentario.")

    like_p = subparsers.add_parser("like", help="Reaccionar con Me gusta a una publicación.")
    like_p.add_argument("--post-url", required=True, help="URL del post a reaccionar.")

    _add_action_common_args(list(subparsers.choices.values()))
    return parser


def _add_action_common_args(parsers: list[argparse.ArgumentParser]) -> None:
    """Añade los argumentos comunes a todos los subcomandos de acciones."""
    for parser in parsers:
        parser.add_argument("--account", metavar="IDENTIFICADOR", default=None,
                            help="Cuenta a forzar (account_id, username o c_user).")
        parser.add_argument("--no-headless", dest="headless", action="store_false",
                            default=True, help="Mostrar la ventana del navegador.")
        parser.add_argument("--proxy", dest="proxy_server", metavar="URL", default=None,
                            help="Servidor proxy (p.ej. 'http://ip:8080').")
        parser.add_argument("--proxy-user", dest="proxy_username", metavar="USUARIO",
                            default=None, help="Usuario del proxy.")
        parser.add_argument("--proxy-pass", dest="proxy_password", metavar="CONTRASEÑA",
                            default=None, help="Contraseña del proxy.")
        parser.add_argument("--confirm-timeout", type=int, default=15_000, metavar="MS",
                            help="Timeout de confirmación DOM. Defecto: 15000.")
        parser.add_argument("--output", "-o", type=Path, metavar="FICHERO", default=None,
                            help="Guardar el resultado como JSON en FICHERO.")
        parser.add_argument("--indent", type=int, default=2, metavar="N",
                            help="Indentación del JSON de salida. Defecto: 2.")
        parser.add_argument("--debug", action="store_true", default=False,
                            help="Logging detallado.")


async def _run_action(verb: str, args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta una acción de escritura y devuelve el ``ActionResult`` como dict.

    Args:
        verb: Subcomando ({'post', 'share', 'comment', 'like'}).
        args: Argumentos parseados del subcomando.

    Returns:
        Dict serializable del ``ActionResult``.

    Raises:
        ActionError: Si la acción no puede ejecutarse.
    """
    from reaper.auth import AccountManager

    manager = ActionManager(
        account_manager=AccountManager(),
        headless=args.headless,
        proxy_server=args.proxy_server,
        proxy_username=args.proxy_username,
        proxy_password=args.proxy_password,
    )
    try:
        match verb:
            case "post":
                result = await manager.create_post(
                    text=args.text,
                    images=args.images,
                    group=args.group,
                    account=args.account,
                    confirm_timeout=args.confirm_timeout,
                )
            case "share":
                result = await manager.share_post(
                    post_url=args.post_url,
                    group=args.group,
                    group_name=args.group_name,
                    account=args.account,
                    confirm_timeout=args.confirm_timeout,
                )
            case "comment":
                result = await manager.comment(
                    post_url=args.post_url,
                    text=args.text,
                    account=args.account,
                    confirm_timeout=args.confirm_timeout,
                )
            case "like":
                result = await manager.like(
                    post_url=args.post_url,
                    account=args.account,
                    confirm_timeout=args.confirm_timeout,
                )
            case _:
                raise ActionError(f"Verbo de acción no soportado: '{verb}'.")
        return result.to_dict()
    finally:
        await manager.close()


def _run_action_cli(argv: list[str]) -> None:
    """Ejecuta el flujo CLI de acciones (``reaper action ...``)."""
    setup_logging(
        level="DEBUG" if "--debug" in argv else "INFO",
        use_color=None,
    )
    parser = build_action_parser()
    args = parser.parse_args(argv[1:])

    try:
        result = asyncio.run(_run_action(args.verb, args))
    except ActionError as exc:
        _exit_error(str(exc), code=5)

    _write_output(result, args.output, args.indent)

    # Código de salida no nulo si la plataforma no confirmó la acción.
    if result.get("status") == "error":
        sys.exit(1)


def main() -> None:
    """Punto de entrada principal de la CLI.

    Registrado como ``reaper = "reaper.cli:main"`` en ``pyproject.toml``.
    Parsea los argumentos, ejecuta el scrape de forma asíncrona con
    ``asyncio.run()`` y escribe el resultado. Si el primer argumento es
    ``action``, delega en ``_run_action_cli`` (acciones de escritura).
    """
    # Detección del subcomando de acciones antes de construir el parser de scrape.
    if len(sys.argv) > 1 and sys.argv[1] == "action":
        _run_action_cli(sys.argv[1:])
        return

    parser = build_parser()
    args = parser.parse_args()

    # Configurar logging antes de cualquier operación.
    # --debug activa nivel DEBUG y colores; el modo normal usa INFO.
    setup_logging(
        level="DEBUG" if args.debug else "INFO",
        use_color=None,   # autodetecta TTY
    )

    # Ejecutar el scrape asíncrono desde el contexto síncrono de la CLI.
    # Si se indica --account, se construye un AccountManager (respeta el
    # backend de reaper.toml) para poder resolver la cuenta solicitada.
    account_manager = None
    if args.account is not None:
        from reaper.auth import AccountManager

        account_manager = AccountManager()

    try:
        result = asyncio.run(
            Reaper(account_manager=account_manager).scrape(
                args.url,
                headless=args.headless,
                debug=args.debug,
                screenshot=args.screenshot,
                auto_scroll=args.auto_scroll,
                infinity_scroll=args.infinity_scroll,
                proxy_server=args.proxy_server,
                proxy_username=args.proxy_username,
                proxy_password=args.proxy_password,
                account=args.account,
            )
        )
    except ValueError as exc:
        _exit_error(str(exc), code=2)
    except UnsupportedPlatformError as exc:
        _exit_error(str(exc), code=3)
    except ScrapingError as exc:
        _exit_error(str(exc), code=4)

    _write_output(result, args.output, args.indent)


if __name__ == "__main__":
    main()
