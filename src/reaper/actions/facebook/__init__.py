"""
reaper/actions/facebook/__init__.py
===================================
Flujos de escritura de Facebook sobre sesiones autenticadas.

Re-exporta las funciones de interacción de ``reaper.actions.facebook.flows``
y los selectores centralizados. ``ActionManager`` es el orquestador al que
el consumidor debería recurrir normalmente (ver ``reaper.actions.manager``),
pero los flujos se exponen aquí para uso avanzado o directo con una
``Page`` de Playwright:

    from reaper.actions.facebook import like_post

    async with actor.session(cookies=cookies, url=post_url) as handle:
        outcome = await like_post(handle.page)
"""

from reaper.actions.facebook.flows import (
    comment_on_post,
    like_post,
    post_text,
    post_text_group,
    share_post_to_group,
)
from reaper.actions.facebook.selectors import (
    COMMENT_BOX,
    COMMENT_CONTAINER,
    COMMENT_ID_PATTERNS,
    COMMENT_SUBMIT,
    COMPOSER_INPUT,
    GROUP_COMPOSER_INPUT,
    GROUP_SEARCH_INPUT,
    LIKE_ACTIVE_MARKERS,
    LIKE_BUTTON,
    PHOTO_INPUT,
    POST_LINK_PATTERNS,
    POST_SUBMIT,
    SHARE_BUTTON,
    SHARE_SUBMIT,
    SHARE_TO_GROUP_ENTRY,
    group_option,
)

__all__ = [
    "post_text",
    "post_text_group",
    "share_post_to_group",
    "comment_on_post",
    "like_post",
    "COMPOSER_INPUT",
    "PHOTO_INPUT",
    "POST_SUBMIT",
    "GROUP_COMPOSER_INPUT",
    "SHARE_BUTTON",
    "SHARE_TO_GROUP_ENTRY",
    "GROUP_SEARCH_INPUT",
    "SHARE_SUBMIT",
    "group_option",
    "COMMENT_BOX",
    "COMMENT_SUBMIT",
    "COMMENT_CONTAINER",
    "LIKE_BUTTON",
    "LIKE_ACTIVE_MARKERS",
    "POST_LINK_PATTERNS",
    "COMMENT_ID_PATTERNS",
]
