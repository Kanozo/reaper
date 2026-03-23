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
