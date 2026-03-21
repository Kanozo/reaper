"""
test_manual.py
==============
Test manual para verificar que la librería Reaper funciona correctamente.

NO es un test de pytest. Es un script que puedes ejecutar directamente
para comprobar el funcionamiento real con URLs reales.

Uso:
    python test_manual.py

Requisitos:
    - pip install -e ".[dev]"
    - playwright install firefox
    - Conexión a internet
"""

import asyncio
import json
from reaper import scrape, Reaper, ScraperConfig
from reaper import UnsupportedPlatformError, ScrapingError


# ==============================================================================
# CONFIGURA AQUÍ TUS URLs DE PRUEBA
# ==============================================================================

URL_FACEBOOK_REEL    = "https://www.facebook.com/reel/816043001524221"
URL_FACEBOOK_POST    = "https://www.facebook.com/permalink/10160286451851071"
URL_INSTAGRAM_POST   = "https://www.instagram.com/p/C9xQ2fYsJ3k/"
URL_INSTAGRAM_REEL   = "https://www.instagram.com/reel/C9xQ2fYsJ3k/"
URL_NO_SOPORTADA     = "https://twitter.com/user/status/123"
URL_INVALIDA         = "no-es-una-url"


# ==============================================================================
# HELPERS
# ==============================================================================

def ok(msg: str) -> None:
    print(f"  ✅ {msg}")

def fail(msg: str) -> None:
    print(f"  ❌ {msg}")

def info(msg: str) -> None:
    print(f"     {msg}")

def separador(titulo: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {titulo}")
    print(f"{'─' * 60}")


# ==============================================================================
# TESTS
# ==============================================================================

def test_imports():
    """Verifica que todos los símbolos públicos se pueden importar."""
    separador("TEST 1 — Imports públicos")
    try:
        from reaper import scrape, Reaper, ScraperConfig
        from reaper import UnsupportedPlatformError, ScrapingError
        from reaper.parsers import PostParser, ReelParser, IgPostParser
        from reaper.network.interceptor import CapturedTraffic, FetchResult
        ok("Todos los imports correctos")
        return True
    except ImportError as exc:
        fail(f"Import fallido: {exc}")
        return False


def test_config_valida():
    """Verifica que ScraperConfig valida correctamente los parámetros."""
    separador("TEST 2 — Validación de ScraperConfig")
    errores = 0

    # URL vacía debe fallar
    try:
        ScraperConfig(url="")
        fail("Debería haber lanzado ValueError por URL vacía")
        errores += 1
    except ValueError:
        ok("URL vacía → ValueError correcto")

    # URL sin esquema debe fallar
    try:
        ScraperConfig(url="www.facebook.com/reel/123")
        fail("Debería haber lanzado ValueError por esquema inválido")
        errores += 1
    except ValueError:
        ok("URL sin http:// → ValueError correcto")

    # Proxy sin servidor debe fallar
    try:
        ScraperConfig(url="https://fb.com", proxy_username="user")
        fail("Debería haber lanzado ValueError por proxy sin servidor")
        errores += 1
    except ValueError:
        ok("Proxy sin servidor → ValueError correcto")

    # Configuración válida mínima
    try:
        config = ScraperConfig(url="https://www.facebook.com/reel/123")
        ok(f"Config mínima válida | headless={config.headless} | debug={config.debug}")
    except Exception as exc:
        fail(f"Config válida falló: {exc}")
        errores += 1

    # Proxy completo válido
    try:
        config = ScraperConfig(
            url="https://fb.com",
            proxy_server="http://proxy:8080",
            proxy_username="user",
            proxy_password="pass",
        )
        ok(f"Config con proxy válida | proxy_config={config.proxy_config}")
    except Exception as exc:
        fail(f"Config con proxy falló: {exc}")
        errores += 1

    return errores == 0


def test_plataforma_no_soportada():
    """Verifica que URLs no soportadas lanzan UnsupportedPlatformError."""
    separador("TEST 3 — Plataforma no soportada")
    try:
        asyncio.run(scrape(URL_NO_SOPORTADA))
        fail("Debería haber lanzado UnsupportedPlatformError")
        return False
    except UnsupportedPlatformError as exc:
        ok(f"UnsupportedPlatformError correcto: {exc}")
        return True
    except Exception as exc:
        fail(f"Excepción inesperada: {type(exc).__name__}: {exc}")
        return False


def test_url_invalida():
    """Verifica que una URL malformada lanza ValueError."""
    separador("TEST 4 — URL inválida")
    try:
        asyncio.run(scrape(URL_INVALIDA))
        fail("Debería haber lanzado ValueError")
        return False
    except ValueError as exc:
        ok(f"ValueError correcto: {exc}")
        return True
    except Exception as exc:
        fail(f"Excepción inesperada: {type(exc).__name__}: {exc}")
        return False


async def test_scrape_facebook():
    """Test real: scrape de un reel de Facebook."""
    separador("TEST 5 — Scrape real Facebook Reel")
    info(f"URL: {URL_FACEBOOK_REEL}")

    try:
        result = await scrape(
            URL_FACEBOOK_REEL,
            headless=True,
            debug=False,
            auto_scroll=False,   # más rápido para el test
        )

        # Verificar campos mínimos garantizados
        assert result.get("platform") == "facebook", "Campo 'platform' incorrecto"
        ok(f"platform = {result['platform']}")

        assert "status" in result, "Falta campo 'status'"
        ok(f"status = {result['status']}")

        if result.get("raw_data_available"):
            ok("raw_data_available = True")
            # Campos específicos del reel
            if result.get("author"):
                ok(f"author.name = {result['author'].get('name', 'N/A')}")
            if result.get("reaction_count") is not None:
                ok(f"reaction_count = {result['reaction_count']}")
            if result.get("id"):
                ok(f"id = {result['id']}")
        else:
            fail(f"raw_data_available = False | error = {result.get('error')}")
            return False

        return True

    except ScrapingError as exc:
        fail(f"ScrapingError: {exc}")
        return False
    except Exception as exc:
        fail(f"Error inesperado: {type(exc).__name__}: {exc}")
        return False


async def test_scrape_instagram():
    """Test real: scrape de un post de Instagram."""
    separador("TEST 6 — Scrape real Instagram Post")
    info(f"URL: {URL_INSTAGRAM_POST}")

    try:
        result = await scrape(
            URL_INSTAGRAM_POST,
            headless=True,
            debug=False,
            auto_scroll=False,
        )

        assert result.get("platform") == "instagram", "Campo 'platform' incorrecto"
        ok(f"platform = {result['platform']}")

        if result.get("raw_data_available"):
            ok("raw_data_available = True")
            user = result.get("user") or {}
            if user.get("username"):
                ok(f"user.username = {user['username']}")
            if result.get("like_count") is not None:
                ok(f"like_count = {result['like_count']}")
        else:
            fail(f"raw_data_available = False | error = {result.get('error')}")
            return False

        return True

    except ScrapingError as exc:
        fail(f"ScrapingError: {exc}")
        return False
    except Exception as exc:
        fail(f"Error inesperado: {type(exc).__name__}: {exc}")
        return False


async def test_scrape_con_debug():
    """Test que debug=True guarda artefactos en disco."""
    separador("TEST 7 — Modo debug (guarda artefactos)")
    info(f"URL: {URL_FACEBOOK_REEL}")
    info("Se guardarán artefactos en data/debug_artifacts/")

    try:
        result = await scrape(
            URL_FACEBOOK_REEL,
            headless=True,
            debug=True,          # activa guardado de artefactos
            auto_scroll=False,
        )

        from pathlib import Path
        artifacts_dir = Path("data/debug_artifacts")

        if artifacts_dir.exists():
            sesiones = list(artifacts_dir.iterdir())
            if sesiones:
                ok(f"Artefactos guardados en: {artifacts_dir}")
                ok(f"Sesiones encontradas: {len(sesiones)}")
                # Verificar que la sesión más reciente tiene los ficheros esperados
                ultima = sorted(sesiones)[-1]
                for fichero in ["meta.json", "page.html"]:
                    ruta = ultima / fichero
                    if ruta.exists():
                        ok(f"  {fichero} ({ruta.stat().st_size // 1024} KB)")
                    else:
                        fail(f"  Falta {fichero}")
            else:
                fail("El directorio de artefactos está vacío")
                return False
        else:
            fail("No se creó el directorio data/debug_artifacts/")
            return False

        return True

    except Exception as exc:
        fail(f"Error: {type(exc).__name__}: {exc}")
        return False


def imprimir_resultado(result: dict) -> None:
    """Imprime el resultado completo formateado."""
    separador("RESULTADO COMPLETO (JSON)")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


# ==============================================================================
# RUNNER PRINCIPAL
# ==============================================================================

async def main():
    print("\n" + "=" * 60)
    print("  REAPER — TEST MANUAL DE INTEGRACIÓN")
    print("=" * 60)

    resultados = {}

    # Tests síncronos (no necesitan Playwright)
    resultados["imports"]             = test_imports()
    resultados["config_valida"]       = test_config_valida()
    resultados["plataforma_no_sop"]   = test_plataforma_no_soportada()
    resultados["url_invalida"]        = test_url_invalida()

    # Tests reales (necesitan Playwright + internet)
    # Comenta los que no quieras ejecutar
    resultados["scrape_facebook"]     = await test_scrape_facebook()
    resultados["scrape_instagram"]    = await test_scrape_instagram()
    # resultados["scrape_debug"]      = await test_scrape_con_debug()

    # Resumen final
    separador("RESUMEN")
    total   = len(resultados)
    pasados = sum(1 for v in resultados.values() if v)
    fallidos = total - pasados

    for nombre, resultado in resultados.items():
        estado = "✅ PASS" if resultado else "❌ FAIL"
        print(f"  {estado}  {nombre}")

    print(f"\n  Total: {pasados}/{total} pasados", end="")
    if fallidos:
        print(f" | {fallidos} fallidos ⚠️")
    else:
        print(" | Todos correctos 🎉")
    print()


if __name__ == "__main__":
    asyncio.run(main())