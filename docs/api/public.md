# API pública — `reaper`

Todos los símbolos documentados en esta página se importan directamente
desde el paquete `reaper` sin necesidad de acceder a submódulos internos.

```python
from reaper import (
    scrape,
    Reaper,
    ScraperConfig,
    ScrapingError,
    UnsupportedPlatformError,
    AccountManager,
    AccountProfile,
    AccountActivity,
    AccountStatus,
    BaseAccountStorage,
    LocalFileStorage,
    StorageError,
    ActionManager,
    ActionType,
    ActionResult,
    ActionError,
    build_action_storage,
)
```

---

## `scrape()`

::: reaper.scrape

---

## `Reaper`

::: reaper.core.Reaper

---

## Excepciones

::: reaper.core.ScrapingError

::: reaper.core.UnsupportedPlatformError

---

## Acciones de escritura

Símbolos de `reaper.actions` re-exportados desde el paquete raíz `reaper`.
Ver [API Reference — reaper.actions](actions.md) para la referencia completa.

::: reaper.actions.ActionManager

::: reaper.actions.ActionResult

::: reaper.actions.ActionType

::: reaper.actions.ActionError

::: reaper.actions.build_action_storage
