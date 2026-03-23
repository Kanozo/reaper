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
        - get_account_for_request
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
