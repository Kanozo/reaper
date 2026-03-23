# Preguntas frecuentes

## Sobre el scraping en general

**¿Reaper guarda mis credenciales?**

No. Reaper nunca almacena contraseñas. Solo guarda las cookies de sesión que
el navegador genera cuando tú inicias sesión manualmente. Las contraseñas
nunca pasan por el código de Reaper.

**¿Cuánto tarda cada extracción?**

Entre 5 y 30 segundos dependiendo del tipo de contenido y la velocidad de la red.
Posts simples con `auto_scroll=False` tardan ~5-10s. Grupos con infinity scroll
pueden tardar más de un minuto.

**¿Por qué `raw_data_available` es `False`?**

Significa que el parser no encontró datos estructurados. Causas comunes:

- La página requiere login para mostrar el contenido.
- Facebook/Instagram cambió la estructura de sus datos internos.
- La URL apunta a contenido eliminado o no disponible.

Usa `debug=True` para guardar el HTML y diagnosticar qué ocurrió.

**¿Puedo extraer vídeos y descargarlos?**

Reaper extrae la URL del vídeo en el campo `url` de los adjuntos. La descarga
del archivo de vídeo es responsabilidad del consumidor:

```python
import httpx

result = await scrape(url)
video_url = result["attachments"][0]["url"]
video_bytes = httpx.get(video_url).content
```

## Sobre cuentas autenticadas

**¿Cuántas cuentas puedo registrar?**

Sin límite técnico. Puedes registrar todas las cuentas que necesites de
Facebook y/o Instagram.

**¿Cómo decide Reaper qué cuenta usar?**

Evalúa cada cuenta con un score basado en tasa de éxito (peso 60%), tiempo
desde el último uso (peso 30%) y proximidad al umbral de refresco (peso 10%).
La de mayor score se selecciona.

**¿Qué pasa si no tengo cuentas para una plataforma?**

Reaper usa modo anónimo automáticamente para esa plataforma. Tener cuentas de
Facebook no impide hacer scraping anónimo de Instagram y viceversa.

**¿El umbral de refresco desconecta la cuenta?**

No. Cuando se alcanza el umbral la cuenta pasa a `needs_refresh` pero sigue
siendo usable con prioridad reducida. Solo deja de funcionar si las cookies
realmente expiran (`cookie_expired`), algo que controla la plataforma.

**¿Puedo usar el login en un servidor sin pantalla?**

El login interactivo requiere pantalla porque abre Firefox visible. En servidores
headless exporta las cookies desde tu máquina local e impórtalas:

```bash
# En tu máquina local con pantalla
python -m reaper.auth.login --platform facebook --username usuario

# Copiar el archivo generado al servidor
scp data/accounts/facebook/UUID/cookies.json servidor:/ruta/

# En el servidor
manager.import_cookies_from_file(account_id, "/ruta/cookies.json")
```

## Resolución de errores comunes

**`ModuleNotFoundError: No module named 'reaper'`**

```bash
pip install -e .
```

**`playwright._impl._errors.Error: Executable doesn't exist`**

```bash
playwright install firefox
# En servidores Linux:
playwright install-deps firefox
```

**`ScrapingError: Page.goto: Timeout`**

La página tardó demasiado. Posibles causas: conexión lenta, proxy lento,
rate limiting de la plataforma. Soluciones: usar proxy diferente, esperar
unos minutos, desactivar scroll (`auto_scroll=False`).

**`get_account_for_request()` devuelve `None` teniendo cuentas**

Comprueba el estado:

```python
for c in await manager.list_accounts("facebook"):
    print(c.username, c.status.value, c.has_cookies)
```

Los estados `suspended` y `cookie_expired` bloquean la selección.
También es necesario que el archivo `cookies.json` exista (`has_cookies=True`).
