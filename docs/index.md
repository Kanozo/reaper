# Reaper

> Librería Python para extraer datos estructurados de **Facebook** e **Instagram**.
> Versión **0.2.0** — Python 3.11+

---

Reaper convierte URLs de redes sociales en diccionarios Python listos para procesar.
Soporta posts, reels, vídeos, fotos, grupos y perfiles de Facebook, y posts y reels
de Instagram. Desde la versión 0.2.0 también gestiona **sesiones autenticadas**
con rotación inteligente de cuentas.

## Instalación

```bash
pip install .
playwright install firefox
```

## Uso mínimo

```python
import asyncio
from reaper import scrape

result = asyncio.run(scrape("https://www.facebook.com/reel/816043001524221"))

print(result["author"]["name"])
print(result["text"])
print(f"Reacciones: {result['reaction_count']}")
```

## Uso con cuentas autenticadas

```python
import asyncio
from reaper import scrape, AccountManager
from reaper.auth.login import run_login_new_account

async def main():
    manager = AccountManager()

    # Primera vez: registrar una cuenta (abre Firefox para login manual)
    await run_login_new_account(manager, "facebook", "mi_usuario@gmail.com")

    # Scraping usando la sesión autenticada con rotación automática
    result = await scrape(
        "https://www.facebook.com/reel/816043001524221",
        account_manager=manager,
    )
    print(result["author"]["name"])

asyncio.run(main())
```

## Lo que puede hacer

- Extraer posts, reels, vídeos, fotos, grupos y perfiles de Facebook.
- Extraer posts y reels de Instagram.
- Gestionar múltiples cuentas autenticadas con rotación inteligente.
- Trabajar con proxies para anonimidad o distribución geográfica.
- Guardar artefactos de debug (HTML, tráfico GraphQL) para diagnóstico offline.
- Serializar el resultado como JSON con un solo parámetro (`default=str`).

## Lo que no hace

- No accede a mensajes privados ni contenido de cuentas de terceros.
- No automatiza el proceso de login (siempre es manual por diseño).
- No garantiza funcionamiento si Facebook/Instagram cambia su estructura interna.
- No gestiona CAPTCHAs visuales ni verificaciones de seguridad de plataforma.

## Estructura del proyecto

```
src/reaper/
├── __init__.py          API pública: scrape(), Reaper, AccountManager
├── cli.py               Comando reaper en la terminal
├── config.py            ScraperConfig — objeto de configuración central
├── core.py              Reaper — orquestador principal
├── auth/                Sistema de cuentas autenticadas (v0.2.0)
│   ├── models.py        AccountProfile, AccountActivity, AccountStatus
│   ├── account_manager.py  Gestor central de cuentas
│   ├── rotator.py       Algoritmo de rotación por score
│   ├── login.py         Flujo interactivo de login
│   └── storage/         Backends de persistencia
├── network/             Playwright + captura de tráfico
├── scrapers/            Orquestadores por plataforma
├── parsers/             Extracción de datos del HTML/GraphQL
└── utils/               Utilidades compartidas
```
