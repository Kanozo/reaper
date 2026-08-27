"""
reaper/actions/facebook/selectors.py
====================================
Selectores centralizados de la UI web de Facebook (``www.facebook.com``).

Reaper opera sobre la versión **web** (no ``m.facebook.com``) para que los
flujos y los tests coincidan con el DOM de escritorio. Facebook cambia su DOM
con frecuencia, por eso cada elemento se define como una tupla de *candidatos*
ordenados por prioridad. ``reaper.actions.facebook.dom`` prueba los candidatos
en orden hasta que encuentra uno visible. Si un flujo deja de funcionar tras un
cambio de Facebook, ajusta únicamente estas constantes sin tocar la lógica.

Todos los selectores son CSS salvo los que empiezan por ``//``, que son XPath
(deben codificar el texto a buscar, p.ej. el nombre del grupo).
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Composer de posts (muro propio y grupos) — UI web
# ─────────────────────────────────────────────────────────────────────────────

# Botón que abre el composer del muro. El elemento clicable es el propio
# ``div[@role='button']`` (no su ``span`` hijo) cuyo texto es el placeholder.
# Dos ubicaciones según la URL:
#   - Muro (``https://www.facebook.com``): el botón vive dentro de un bloque
#     ``div[@aria-label='Crear una publicación'][@role='region']``.
#   - Perfil (``profile.php?id=...``): un ``div[@role='button']`` directo.
# Al hacer clic aquí Facebook abre el diálogo "Crear publicación".
COMPOSER_TRIGGER: tuple[str, ...] = (
    "//div[@role='button'][contains(normalize-space(.), 'Qué estás pensando')]",
    "//div[@role='region' and @aria-label='Crear una publicación']"
    "//div[contains(@role,'button')]",
    "//div[@role='button'][contains(normalize-space(.), 'Comparte una idea')]",
    "//div[@role='button'][contains(normalize-space(.), 'Què estàs pensant')]",
    "//div[@role='button'][contains(normalize-space(.), \"What's on your mind\")]",
    "//div[@role='button'][contains(normalize-space(.), 'What are you thinking')]",
    "//div[@role='button'][contains(normalize-space(.), 'Share an idea')]",
    "//div[@role='button'][contains(normalize-space(.), 'Create a post')]",
)

# El diálogo en el que se redacta y publica el post.
COMPOSER_DIALOG: tuple[str, ...] = (
    "div[role='dialog'][aria-label='Crear publicación']",
    "div[role='dialog'][aria-label*='Create public']",
    "div[role='dialog'][aria-label*='Create post']",
)

# Campos de texto del composer. El diálogo web usa un contenteditable con
# ``data-lexical-editor`` y ``aria-placeholder`` (no ``aria-label``). Se
# prueban en orden hasta encontrar uno editable.
COMPOSER_INPUT: tuple[str, ...] = (
    "div[role='dialog'] div[contenteditable='true'][role='textbox'][data-lexical-editor]",
    "div[role='dialog'] div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true'][role='textbox'][data-lexical-editor]",
    "div[contenteditable='true'][role='textbox'][aria-placeholder]",
    "div[contenteditable='true'][role='textbox']",
    "[name='xhpc_message']",
)

# Input de archivo para adjuntar imágenes.
PHOTO_INPUT: tuple[str, ...] = (
    "div[role='dialog'] input[type='file'][accept*='image']",
    "input[type='file'][accept*='image']",
    "input[root='file']",
    "input[data-testid*='photo-input']",
    "input[type='file'][name='file']",
    "input[accept='image/*,image/heif,image/heic,*']",
)

# Botón que confirma la publicación (inglés y español). Facebook lo deja
# ``aria-disabled="true"`` hasta que hay texto: usa ``dom.click_enabled_best``.
POST_SUBMIT: tuple[str, ...] = (
    "div[aria-label='Publicar'][role='button']",
    "div[aria-label='Post'][role='button']",
    "[data-testid='react-composer-post-button']",
    "button[type='submit']",
    "form input[type='submit']",
)

# ─────────────────────────────────────────────────────────────────────────────
# Grupo → publicar directamente en el grupo
# ─────────────────────────────────────────────────────────────────────────────

# Botón que abre el composer del grupo ("Escribe algo a este grupo...").
# Reutiliza el mismo diálogo "Crear publicación" de la UI web.
GROUP_COMPOSER_TRIGGER: tuple[str, ...] = (
    "//div[@role='button']//span[contains(normalize-space(.), 'Escribe algo')]",
    "//div[@role='button']//span[contains(normalize-space(.), 'a este grupo')]",
    "//div[@role='button']//span[contains(normalize-space(.), 'Write something')]",
    "//div[@role='button']//span[contains(normalize-space(.), 'this group')]",
    "//div[@role='button']//span[contains(normalize-space(.), 'Start a discussion')]",
)

# Campo de "Escribe algo a este grupo...". Idéntico al del muro (mismo diálogo).
GROUP_COMPOSER_INPUT: tuple[str, ...] = COMPOSER_INPUT

# Confirmación del post dentro del diálogo del grupo.
GROUP_POST_SUBMIT: tuple[str, ...] = POST_SUBMIT

# ─────────────────────────────────────────────────────────────────────────────
# Compartir una publicación en un grupo (diálogo de share)
# ─────────────────────────────────────────────────────────────────────────────

# Botón "Compartir" de un post.
SHARE_BUTTON: tuple[str, ...] = (
    "[data-testid='share_btn']",
    "div[aria-label='Share'][role='button']",
    "span[role='button'][aria-label='Share']",
)

# Entrada "Compartir en un grupo..." dentro del diálogo de share.
SHARE_TO_GROUP_ENTRY: tuple[str, ...] = (
    "div[aria-label*='group'][role='button']",
    "[data-testid='share_to_group']",
    "li[role='option'] div[role='button']",
)

# Buscador de grupos dentro del diálogo.
GROUP_SEARCH_INPUT: tuple[str, ...] = (
    "input[role='combobox'][aria-label*='group']",
    "input[data-testid='search_terms']",
    "input[type='search']",
)

# Opción de grupo en el desplegable de resultados. Requiere XPath con texto.
def group_option(group_name: str) -> tuple[str, ...]:
    """Candidatos para seleccionar un grupo en el diálogo de share.

    Args:
        group_name: Nombre (o texto visible) del grupo a seleccionar.

    Returns:
        Tupla de selectores priorizados (XPath + CSS).
    """
    return (
        f"//li[contains(@role,'option') and contains(normalize-space(.), '{group_name}')]",
        f"//div[contains(normalize-space(.), '{group_name}') and "
        f"(contains(@role,'option') or ancestor::li[contains(@role,'option')])]",
    )

# Confirmación final dentro del diálogo de share.
SHARE_SUBMIT: tuple[str, ...] = (
    "div[aria-label='Post'][role='button']",
    "[data-testid='share_submit']",
    "span[role='button'][aria-label='Post']",
)

# ─────────────────────────────────────────────────────────────────────────────
# Comentario
# ─────────────────────────────────────────────────────────────────────────────

# Caja de escribir un comentario bajo una publicación.
# Primero labels españoles (UI es-*), luego ingleses, y un genérico final.
COMMENT_BOX: tuple[str, ...] = (
    "div[contenteditable='true'][role='textbox'][aria-label*='comentar' i]",
    "textarea[placeholder*='comentar' i]",
    "textarea[placeholder*='escribe un comentario' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='comment' i]",
    "textarea[placeholder*='comment' i]",
    "div[contenteditable='true'][role='textbox']",
)

# Envío del comentario (algunas versiones usan Enter, otras un botón).
COMMENT_SUBMIT: tuple[str, ...] = (
    "[data-testid='comment-composer-submit']",
    "div[aria-label='Post'][role='button']",
    "div[aria-label='Publicar'][role='button']",
    "span[aria-label='Publicar']",
    "div[aria-label='Enviar'][role='button']",
    "span[aria-label='Enviar']",
    "span[role='button'][aria-label='Add comment']",
    "span[aria-label='Post']",
)

# Contenedor de comentarios publicados (para confirmar que el texto salió).
COMMENT_CONTAINER: tuple[str, ...] = (
    "ul[data-testid='comment_list']",
    "div[data-testid='comment']",
    "[class*='Comment']",
)

# ─────────────────────────────────────────────────────────────────────────────
# Like (reacción "Me gusta")
# ─────────────────────────────────────────────────────────────────────────────

# El botón "Me gusta" se identifica por el marcador interno
# ``data-ad-rendering-role="like_button"`` (estable e independiente del idioma
# y del estado: presente tanto sin like como ya reaccionado). El elemento
# clicable es el ancestro con ``role="button"`` (el div con el marcador es un
# placeholder sin tamaño). Se dejan los selectores por aria-label como
# respaldo por si el marcador interno cambia.
#
# Primero se busca **dentro del diálogo del post** (``role="dialog"``): en la
# página de permalink, Facebook renderiza el post objetivo en un diálogo
# ``CometSinglePostDialog`` superpuesto a la página de fondo (que suele ser el
# feed, con más botones "Me gusta" de otras publicaciones). Sin el scope del
# diálogo, el XPath coincide con todos los botones de la página y se clicaría
# el primero en orden DOM (el de una publicación del feed de fondo), no el del
# post objetivo.
LIKE_BUTTON: tuple[str, ...] = (
    "//div[@role='dialog']//div[@data-ad-rendering-role='like_button']"
    "/ancestor::div[@role='button'][1]",
    "//div[@data-ad-rendering-role='like_button']"
    "/ancestor::div[@role='button'][1]",
    "div[data-ad-rendering-role='like_button']",
    "div[aria-label='Like'][role='button']",
    "div[aria-label='Me gusta'][role='button']",
    "span[aria-label='Like']",
    "span[aria-label='Me gusta']",
)

# Marcadores de estado activo tras dar like (para confirmar el éxito). Se
# evita ``selected`` a secas: aparece en casi cualquier página de Facebook y
# produce falsos positivos.
LIKE_ACTIVE_MARKERS: tuple[str, ...] = (
    "aria-pressed='true'",
    "aria-pressed=\"true\"",
    ">Liked<",
    "aria-label='Eliminar Me gusta'",
    "aria-label=\"Eliminar Me gusta\"",
)

# ─────────────────────────────────────────────────────────────────────────────
# Detección de publicaciones nuevas (post_ids)
# ─────────────────────────────────────────────────────────────────────────────

# Patrones de URL de post de Facebook para extraer el post_id.
# La UI web (www.facebook.com) publica los posts bajo ``permalink.php?
# story_fbid=pfbid...`` o ``story.php?story_fbid=...``. Los IDs ``pfbid`` son
# alfanuméricos, por eso se capturan con ``[A-Za-z0-9]+`` (no solo dígitos).
POST_LINK_PATTERNS: tuple[str, ...] = (
    r"permalink\.php\?story_fbid=([A-Za-z0-9]+)",
    r"story\.php\?story_fbid=([A-Za-z0-9]+)",
    r"/posts/(pfbid[A-Za-z0-9]+)",
    r"/posts/(\d{15,20})",
    r"/permalink/(pfbid[A-Za-z0-9]+)",
    r"/permalink/(\d{15,20})",
    r"/pending_posts/(\d{15,20})",
)

# Patrón de URL de comentario para extraer el comment_id.
COMMENT_ID_PATTERNS: tuple[str, ...] = (
    r"comment_id=(\d+)",
    r"delete_comment_id=(\d+)",
)


# ─────────────────────────────────────────────────────────────────────────────
# Feed en vivo (comentario sin salir del feed)
# ─────────────────────────────────────────────────────────────────────────────

# Artículo (post) del feed de un grupo o timeline. Se usa SCOPED a la página
# para enumerar posts y como contenedor para los selectores de comentario.
FEED_ARTICLE: tuple[str, ...] = (
    "div[role='article']",
)

# Enlace/botón "Comentar" dentro de un artículo: abre el composer inline.
# Se prueba SCOPED al artículo (article.locator(selector)).
# En UI española de grupos el trigger es un div[role=button] con TEXTO
# "Responder"/"Comentar" SIN aria-label → se selecciona por texto exacto.
COMMENT_LINK: tuple[str, ...] = (
    "xpath=.//div[@role='button'][normalize-space(.)='Responder']",
    "xpath=.//div[@role='button'][normalize-space(.)='Comentar']",
    "xpath=.//span[@role='button'][normalize-space(.)='Responder']",
    "xpath=.//span[@role='button'][normalize-space(.)='Comentar']",
    "xpath=.//div[@role='button'][normalize-space(.)='Comment']",
    "xpath=.//span[@role='button'][normalize-space(.)='Comment']",
    "xpath=.//div[@role='button'][normalize-space(.)='Reply']",
    "span[role='button'][aria-label*='comentar' i]",
    "div[role='button'][aria-label*='comentar' i]",
    "span[role='button'][aria-label*='comment' i]",
    "div[role='button'][aria-label*='comment' i]",
    "[data-testid='UFI2ComposerInput/comment-toggle']",
)
