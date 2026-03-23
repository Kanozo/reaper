# Instalación

## Requisitos

| Componente | Versión mínima |
|---|---|
| Python | 3.11 |
| pip | 23+ |
| Firefox (via Playwright) | instalado automáticamente |

## Instalar desde el repositorio

```bash
# Clonar el repositorio
git clone <repo-url>
cd reaper

# Crear entorno virtual (recomendado)
python3.11 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# Instalar Reaper
pip install .

# Instalar Firefox para Playwright (solo la primera vez)
playwright install firefox
```

## Instalar en modo desarrollo

```bash
pip install -e ".[dev]"
playwright install firefox
```

El modo editable (`-e`) hace que los cambios en el código fuente tengan efecto
inmediato sin reinstalar.

## Verificar la instalación

```bash
# Debe imprimir la versión
reaper --version

# Debe imprimir "OK"
python -c "from reaper import scrape, AccountManager; print('OK')"
```

## Servidores Linux sin pantalla (headless)

En servidores sin interfaz gráfica, instala también las dependencias del sistema
que Firefox requiere:

```bash
playwright install-deps firefox
```

!!! note "Entornos sin pantalla y login"
    El flujo de login interactivo (`run_login_new_account`, `run_login_refresh`)
    **requiere pantalla** porque abre Firefox visible para que el usuario
    complete el login manualmente. En un servidor headless deberás exportar
    las cookies desde tu máquina local e importarlas con
    `manager.import_cookies_from_file()`.
