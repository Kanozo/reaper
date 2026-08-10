# API Reference — `reaper.auth`

---

## `AccountManager`

::: reaper.auth.account_manager.AccountManager
    options:
      members:
        - __init__
        - add_account
        - remove_account
        - get_account
        - list_accounts
        - update_status
        - import_cookies
        - import_cookies_from_file
        - export_cookies
        - save_cookies
        - load_cookies
        - delete_cookies
        - close
        - get_account_for_request
        - resolve_account
        - record_success
        - record_failure
        - pool_summary
        - get_ranked_accounts

---

## `AccountProfile`

::: reaper.auth.models.AccountProfile
    options:
      members:
        - __init__
        - has_cookies
        - is_selectable
        - touch_updated
        - to_dict
        - from_dict

---

## `AccountActivity`

::: reaper.auth.models.AccountActivity
    options:
      members:
        - record_success
        - record_failure
        - reset_cookie_refresh_counter
        - success_rate
        - failure_rate
        - to_dict
        - from_dict

---

## `AccountStatus`

::: reaper.auth.models.AccountStatus

---

## `AccountRotator`

::: reaper.auth.rotator.AccountRotator
    options:
      members:
        - __init__
        - select
        - rank_all

---

## Login interactivo

::: reaper.auth.login.run_login_new_account

::: reaper.auth.login.run_login_refresh

---

## Storage

::: reaper.auth.storage.base.BaseAccountStorage
    options:
      members:
        - save
        - load
        - delete
        - list_all
        - update_activity
        - save_cookies
        - load_cookies
        - delete_cookies
        - connect
        - close
        - exists

::: reaper.auth.storage.base.StorageError

::: reaper.auth.storage.local.LocalFileStorage
    options:
      members:
        - __init__
        - save
        - load
        - delete
        - list_all
        - update_activity
        - cookies_path_for
        - save_cookies
        - load_cookies
        - delete_cookies

::: reaper.auth.storage.postgres.PostgresStorage
    options:
      members:
        - __init__
        - connect
        - close
        - save
        - load
        - delete
        - list_all
        - update_activity
        - save_cookies
        - load_cookies
        - delete_cookies

::: reaper.auth.storage.mongodb.MongoStorage
    options:
      members:
        - __init__
        - connect
        - close
        - save
        - load
        - delete
        - list_all
        - update_activity
        - save_cookies
        - load_cookies
        - delete_cookies

## Configuración de storage

::: reaper.auth.storage.config.StorageConfig

::: reaper.auth.storage.config.PostgresConfig

::: reaper.auth.storage.config.MongoConfig

::: reaper.auth.storage.config.load_storage_config

::: reaper.auth.storage.config.find_config_file

::: reaper.auth.storage.config.build_storage
