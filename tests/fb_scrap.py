import os, json
from datetime import datetime
from reaper import scrape, ScrapingError
from reaper.utils import datetime_encoder

URL_FACEBOOK_REEL="https://www.facebook.com/photo?fbid=910276441667945&set=pcb.910347274994195"

def ok(msg: str) -> None:
    print(f"  ✅ {msg}")

def fail(msg: str) -> None:
    print(f"  ❌ {msg}")

def separador(titulo: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {titulo}")
    print(f"{'─' * 60}")

def imprimir_resultado(result: dict) -> None:
    """Imprime el resultado completo formateado."""
    separador("RESULTADO COMPLETO (JSON)")
    output_path = os.path.join("data/result", f"{result.get("platform")}-{result.get("__typename")}-{result.get("post_id")}-{datetime.now().timestamp():.0f}.json")

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(
            result, fp,
            indent=4,
            ensure_ascii=False,
            default=datetime_encoder,
        )
    #print(json.dumps(result, ensure_ascii=False, indent=4, default=str))

async def test_scrape_facebook():
    try:
        result = await scrape(
            URL_FACEBOOK_REEL,
            headless=True,
            debug=True,
            auto_scroll=False
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
            return result

        return result

    except ScrapingError as exc:
        fail(f"ScrapingError: {exc}")
        return False
    except Exception as exc:
        fail(f"Error inesperado: {type(exc).__name__}: {exc}")
        return False

async def main():
    result = await test_scrape_facebook()
    imprimir_resultado(result)

import asyncio

if __name__ == "__main__":
    asyncio.run(main())