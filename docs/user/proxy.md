# Uso con proxy

Reaper puede enrutar el tráfico del navegador a través de un servidor proxy.

## Desde Python

```python
import asyncio
from reaper import scrape

result = asyncio.run(scrape(
    url="https://www.facebook.com/reel/123456",
    proxy_server="http://proxy.ejemplo.com:8080",
    proxy_username="usuario",     # opcional
    proxy_password="contraseña",  # opcional
))
```

## Desde la terminal

```bash
# Proxy HTTP básico
reaper https://www.facebook.com/reel/123 \
    --proxy http://192.168.1.100:8080

# Proxy con autenticación
reaper https://www.facebook.com/reel/123 \
    --proxy http://proxy.ejemplo.com:8080 \
    --proxy-user usuario \
    --proxy-pass contraseña

# Proxy SOCKS5
reaper https://www.facebook.com/reel/123 \
    --proxy socks5://127.0.0.1:1080
```

## Combinar proxy y cuentas autenticadas

```python
result = await scrape(
    url="https://www.facebook.com/reel/123",
    account_manager=manager,
    proxy_server="http://proxy.ejemplo.com:8080",
)
```

## Formatos soportados

```
http://ip:puerto
https://ip:puerto
socks5://ip:puerto
http://usuario:contraseña@ip:puerto
```

## Verificar que el proxy funciona

```bash
curl --proxy http://tu-proxy:puerto https://www.facebook.com
```
