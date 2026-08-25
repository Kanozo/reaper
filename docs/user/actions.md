# Acciones de escritura (posts, comentarios, likes)

Desde la versión **0.3.0**, Reaper no solo **lee** contenido: también puede
**escribir** sobre una sesión de Facebook autenticada. Con `ActionManager`
puedes:

- Publicar texto y/o imágenes en el **muro de la cuenta**.
- Publicar directamente **dentro de un grupo** (texto e imágenes).
- **Compartir** una publicación existente en un grupo.
- **Responder con texto** (comentar) a una publicación.
- **Reaccionar con "Me gusta"** a una publicación.

Cada operación genera un [`ActionResult`](../api/actions.md) que se guarda en
el mismo backend de almacenamiento que eliges para las cuentas (JSON local,
PostgreSQL o MongoDB), pero en una tabla/colección/directorio separado
(`actions` por defecto).

!!! warning "Acciones de escritura requieren una cuenta"
    A diferencia del scraping (que también funciona en modo anónimo), las
    acciones de escritura **siempre** requieren un `AccountManager` con una
    cuenta de Facebook autenticada y con cookies válidas.

---

## Preparación

1. Registra una cuenta con cookies (ver [Cuentas autenticadas](accounts.md) y
   [Login interactivo](login.md)):

   ```python
   import asyncio
   from reaper import AccountManager
   from reaper.auth.login import run_login_new_account

   async def main():
       manager = AccountManager()
       await run_login_new_account(manager, "facebook", "mi_cuenta@gmail.com")
       await manager.close()

   asyncio.run(main())
   ```

2. El almacenamiento de resultados se configura en `reaper.toml` (igual que
   el de cuentas). Añade un directorio/tabla/colección para las acciones:

   ```toml
   [storage]
   backend = "local"
   accounts_dir = "data/accounts"
   actions_dir  = "data/actions"      # opcional; default: data/actions
   ```

   Para PostgreSQL / MongoDB el mecanismo es el mismo que en
   [Almacenamiento](storage.md), con `actions_table` / `actions_collection`:

   ```toml
   [storage.postgres]
   dsn = "postgresql://user:pass@localhost:5432/reaper"
   actions_table = "actions"

   [storage.mongodb]
   uri = "mongodb://localhost:27017"
   actions_collection = "actions"
   ```

---

## Publicar un post en el muro

Al publicar en el muro (sin `group`), Reaper navega a tu perfil propio. La URL
del perfil (**no hace falta indicarla**) se deriva automáticamente del cookie
`c_user` de la cuenta (`profile.php?id={c_user}`); si no se puede resolver,
cae al home de la cuenta. En la UI de escritorio Reaper abre el campo "¿Qué
estás pensando?" (el botón que despliega el composer) antes de escribir.

### Solo texto

```python
import asyncio
from reaper import ActionManager, AccountManager

async def main():
    manager = ActionManager(account_manager=AccountManager())

    result = await manager.create_post(text="Hola desde Reaper 🖐️")

    print(f"status:        {result.status}")
    print(f"post_id:       {result.post_id}")
    print(f"post_url:      {result.post_url}")
    print(f"guardado en:   {manager.storage!r}")

    await manager.close()
    return result

asyncio.run(main())
```

!!! tip "Qué significa `post_url` y `post_id` al publicar en el muro"
    En el muro, Facebook **ya no siempre navega a una URL de post**: muchas
    veces el post se inserta en el feed sin cambiar de página. Por eso el
    flujo confirma por `status == "ok"` cuando el texto aparece en el DOM y el
    diálogo del composer se cerró. Si Facebook expone el permalink del post
    junto al texto (habitual con imágenes), ese se guarda en `post_url` con su
    `post_id`; si no, `post_url` es la URL de la página actual (tu perfil) y
    `post_id` puede ser `None`. Los enlaces de notificación del feed se
    descartan.

### Con imágenes locales

Se aceptan rutas locales (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`).
Se puede pasar una o varias:

```python
import asyncio
from reaper import ActionManager, AccountManager

async def main():
    manager = ActionManager(account_manager=AccountManager())

    result = await manager.create_post(
        text="Mis fotos del viaje 📸",
        images=["fotos/amanecer.jpg", "fotos/playa.png"],
    )
    print(result.status, result.post_url)

    await manager.close()

asyncio.run(main())
```

!!! tip "Imagen sin texto"
    Si no pasas `text`, el post solo sube las imágenes. Al menos uno de los
    dos (`text` o `images`) es obligatorio; si faltan ambos se lanza
    `ActionError`.

!!! note "Input de archivo oculto"
    El `input[type=file]` del composer de Facebook está oculto en el DOM
    (`display: none`). Reaper lo localiza con `PHOTO_INPUT` (scope
    `div[role='dialog']`) y adjunta las rutas sin necesidad de que sea
    visible. Con imágenes, Facebook suele devolver una URL de post real
    (`posts/...`) y por tanto `post_id` poblado; si no, se aplica la misma
    confirmación por texto que en el muro.

### Forzando una cuenta concreta

Usa `account=` para elegir una cuenta del pool por `account_id`, `username`
o ID de usuario de Facebook (`c_user`). Sin el parámetro se aplica la
rotación automática del `AccountManager`:

```python
result = await manager.create_post(
    text="Publicando con una cuenta fija",
    account="mi_cuenta@gmail.com",   # o account_id, o c_user numérico
)
```

### Ajustando el timeout de confirmación

Tras publicar, Reaper espera a que el DOM confirme el post (`confirm_timeout`
en milisegundos, default 15000):

```python
result = await manager.create_post(
    text="A ver si sale",
    confirm_timeout=30_000,
)
```

!!! warning "Timeout de confirmación en redes lentas"
    La apertura del diálogo y la habilitación del botón **Publicar** usan
    timeouts internos generosos (`COMPOSER_OPEN_TIMEOUT` de 120 s y
    `SUBMIT_TIMEOUT` de 60 s) que **no** controla `confirm_timeout`: este solo
    cubre la espera de la confirmación DOM final. Si la conexión es lenta y
    ves que el diálogo abre pero la acción tarda, aumenta `confirm_timeout`;
    si el diálogo no llega a abrir, el problema es de la red y esos timeouts
    internos ya dan margen amplio.

---

## Publicar directamente en un grupo

Para publicar **dentro** de un grupo, pasa `group` como **ID numérico** o
como **URL de grupo**. Reaper navega a la portada del grupo y publica ahí.

```python
import asyncio
from reaper import ActionManager, AccountManager

async def main():
    manager = ActionManager(account_manager=AccountManager())

    # Por ID numérico, solo texto
    result = await manager.create_post(
        text="Aviso importante para el grupo 📢",
        group="123456789",
    )
    print(result.action, result.status)   # "group_post", "ok"

    # Por URL, con imágenes
    result = await manager.create_post(
        text="Fotos del evento del sábado",
        images=["evento/1.jpg", "evento/2.jpg"],
        group="https://www.facebook.com/groups/987654321/",
    )
    await manager.close()

asyncio.run(main())
```

---

## Compartir una publicación en un grupo

Comparte cualquier post (por URL) hacia un grupo. El diálogo de compartir de
Facebook localiza el grupo por su **nombre visible**, así que lo recomendable
es pasar `group_name`.

```python
import asyncio
from reaper import ActionManager, AccountManager

POST_URL = "https://www.facebook.com/story.php?story_fbid=123456&id=789"

async def main():
    manager = ActionManager(account_manager=AccountManager())

    result = await manager.share_post(
        post_url=POST_URL,
        group="123456789",
        group_name="Mi Grupo de Test",   # texto de búsqueda en el diálogo
    )
    print(result.status, result.post_url)

    await manager.close()

asyncio.run(main())
```

Si solo tienes el ID/URL del grupo y no el nombre, se usa ese texto como
búsqueda (puede no encontrar el grupo si el buscador no lo resuelve por ID):

```python
result = await manager.share_post(
    post_url=POST_URL,
    group="123456789",            # se usa como texto de búsqueda
)
```

---

## Responder con texto (comentar)

Comenta cualquier publicación por URL:

```python
import asyncio
from reaper import ActionManager, AccountManager

async def main():
    manager = ActionManager(account_manager=AccountManager())

    result = await manager.comment(
        post_url="https://www.facebook.com/story.php?story_fbid=123456&id=789",
        text="Excelente contenido 👍",
    )
    print(result.status, result.comment_id)

    await manager.close()

asyncio.run(main())
```

---

## Reaccionar con "Me gusta"

```python
import asyncio
from reaper import ActionManager, AccountManager

async def main():
    manager = ActionManager(account_manager=AccountManager())

    result = await manager.like(
        post_url="https://www.facebook.com/story.php?story_fbid=123456&id=789",
    )
    print(result.status)   # "ok" si el DOM confirmó la reacción activa

    await manager.close()

asyncio.run(main())
```

---

## Configuración de la sesión

`ActionManager` acepta los mismos parámetros de navegador que el scraping:

```python
from reaper import ActionManager, AccountManager

manager = ActionManager(
    account_manager=AccountManager(),
    headless=False,                      # ver la ventana del navegador
    browser_type="firefox",              # o "chromium"
    proxy_server="http://mi.proxy:8080",
    proxy_username="usuario",
    proxy_password="clave",
)
```

---

## Persistencia de resultados

Cada acción se guarda automáticamente. Con el backend `local`, la estructura
es un archivo por cada operación:

```
data/actions/
├── {action_id}.json   ← un ActionResult por acción
└── ...
```

Un registro guardado (`ActionResult.to_dict()`) tiene este aspecto (en el muro,
`post_url` suele ser la URL de la página actual y `post_id` puede quedar en
`null` si Facebook no expone una URL de post en el DOM):

```json
{
  "action_id": "9f4a3b2c…",
  "action": "post",
  "platform": "facebook",
  "account_id": "2e8b4a…",
  "account_username": "mi_cuenta@gmail.com",
  "status": "ok",
  "post_url": "https://www.facebook.com/profile.php?id=61585052593000",
  "post_id": null,
  "comment_id": null,
  "group": null,
  "text": "Mis fotos del viaje 📸",
  "error": null,
  "executed_at": "2026-08-12T17:25:35.377660+00:00"
}
```

!!! note "Qué se guarda en `post_url`"
    `post_url` es el permalink canónico del post creado **solo si Facebook lo
    expone junto al texto y pertenece al propio usuario** (`id={c_user}`);
    típico al publicar con imágenes (`permalink.php`/`posts/...`). Se
    descartan los enlaces de notificación (`notif_*`, `ref=`) y los posts de
    grupos/otras personas del feed. Si no aparece un permalink propio, se
    guarda la URL de la página actual (p. ej. tu perfil) y `post_id` queda en
    `null`.

Puedes consultar el historial a través del `storage`:

```python
from reaper.actions.storage import build_action_storage

async def consultar():
    storage = build_action_storage()          # respeta reaper.toml

    historial = await storage.list_all(action="post", limit=10)
    for registro in historial:
        print(registro.executed_at, registro.status, registro.post_id)

    conteo = await storage.count(action="like")
    print("total likes:", conteo)

    await storage.close()

# asyncio.run(consultar())
```

---

## Manejo de errores

Hay dos niveles de fallo:

1. **`ActionError` (excepción)** — problemas antes de ejecutar nada:
   faltan `AccountManager`/cuentas/cookies, el post está vacío, la URL es
   inválida o las imágenes no existen.

   ```python
   from reaper.actions import ActionError

   try:
       await manager.create_post(text="", images=[])   # ambos vacíos
   except ActionError as exc:
       print("No se pudo: ", exc)
   ```

2. **`ActionResult` con `status="error"`** — la acción se ejecutó pero el DOM
   no confirmó el resultado (selector no encontrado, diálogo no cerrado, etc.).
   El registro **sí se persiste**, con el motivo en `error`:

   ```python
   result = await manager.like(post_url=POST_URL)
   if result.status == "error":
       print("La plataforma no confirmó el like:", result.error)
   ```

---

## Línea de comandos (CLI)

Todos los verbos disponibles:

```bash
# Post de texto en el muro
reaper action post --text "Hola desde Reaper"

# Post con una o varias imágenes
reaper action post --text "Mis fotos" --images foto1.jpg foto2.png

# Post directo en un grupo (ID o URL)
reaper action post --text "Aviso" --group 123456789

# Compartir un post en un grupo
reaper action share --post-url "https://www.facebook.com/story.php…" \
    --group 123456789 --group-name "Mi Grupo"

# Comentar una publicación
reaper action comment --post-url "https://www.facebook.com/story.php…" \
    --text "Excelente contenido"

# Dar like
reaper action like --post-url "https://www.facebook.com/story.php…"

# Forzar cuenta y usar proxy
reaper action post --text "Con cuenta fija" --account mi_cuenta@mail.com \
    --proxy http://proxy:8080 --proxy-user u --proxy-pass p

# Guardar el resultado en un fichero
reaper action like --post-url "…" --output resultado.json

# Ver el navegador en pantalla (depuración)
reaper action post --text "…" --no-headless --debug
```

Códigos de salida de la CLI:

| Código | Significado |
|--------|-------------|
| `0` | Acción ejecutada y confirmada por la plataforma. |
| `1` | Acción ejecutada pero **no confirmada** (`status="error"`). |
| `5` | Error de entrada/sesión (`ActionError`). |

---

## Notas importantes

!!! warning "Selectores en constante cambio"
    Facebook modifica su DOM con frecuencia. Los selectores usados por los
    flujos están **centralizados** en `reaper.actions.facebook.selectors`.
    Si una acción deja de confirmarse, ajusta ahí los candidatos (no en la
    lógica). Puedes depurar con `--debug` para inspeccionar el HTML real.

!!! warning "Riesgo de casting / bloqueos"
    Las acciones de escritura son mucho más sensibles a la anti-detección que
    el scraping: interacciones al muro, grupos y comentarios se monitorizan
    activamente. Usa las mismas precauciones de [Anti-Detección](../anti_scraping.md)
    y respeta los tiempos humanos (`human_type`) que ya aplica la librería.

!!! tip "Prueba primero en un grupo privado"
    Antes de publicar en un muro público o un grupo grande, valida el flujo en
    un grupo propio/privado y con `headless=False` para observar el resultado.

!!! note "Cómo se hace el clic en el composer"
    El botón que abre el composer es el `div[@role='button']` cuyo texto es
    "¿Qué estás pensando?" (en el muro vive dentro del bloque
    `aria-label='Crear una publicación'`). Reaper ignora el `contenteditable`
    colapsado del perfil (un campo siempre presente que **no** es el diálogo
    real) y solo considera el composer abierto cuando están visibles el campo
    editable **y** el botón **Publicar** a la vez. Si una versión de Facebook
    cambia estos elementos, ajústalos en `reaper.actions.facebook.selectors`.

---

## Referencia

- [API Reference — reaper.actions](../api/actions.md)
- [Almacenamiento de cuentas](storage.md)
- [Cuentas autenticadas](accounts.md)