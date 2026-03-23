# Resultados por tipo de contenido

Todos los resultados comparten estos campos garantizados:

```python
{
    "platform":           str,        # "facebook" | "instagram"
    "status":             str,        # "ok" | "error"
    "error":              str | None,
    "raw_data_available": bool,
    "scraped_at":         datetime,
    "final_url":          str,
    "post_url":           str,
}
```

## Facebook — Post regular

```python
{
    "__typename":         "regular_post",
    "id":                 str,
    "posted_at":          datetime | None,
    "permalink_url":      str,
    "is_sponsored":       bool,
    "author": {
        "id": str, "name": str, "url": str,
        "avatar": str, "is_verified": bool, "work_info": str | None,
    },
    "text":        str,
    "hashtags":    [{"tag": str, "id": str, "url": str, "mobile_url": str}],
    "mentions":    [{"id": str, "url": str}],
    "attachments": [{"type": str, "id": str, "url": str, "caption": str | list}],
    "reaction_count": int,
    "share_count":    int,
    "comments_count": int,
    "reactions":  [{"id": str, "type": str, "count": int}],
    "comments":   [
        {
            "id": str, "depth": int, "text": str | None,
            "created_at": datetime | None,
            "author": {"id": str, "name": str, "profile_url": str,
                       "gender": str, "avatar": str | None},
            "replies_count": int, "reactions": list, "reaction_count": int,
        }
    ],
    "group":         {"id": str, "name": str, "url": str, "avatar": str} | None,
    "original_post": dict | None,
}
```

## Facebook — Reel

```python
{
    "__typename":    "facebook_reel",
    "id":            str,
    "posted_at":     datetime | None,
    "permalink_url": str,
    "text":          str,
    "author":        dict,
    "reaction_count": int, "comments_count": int, "share_count": int,
    "privacy":       str,
    "is_ad":         bool,
    "attachments": [{
        "type":          "reel",
        "id":            str,
        "url":           str | None,
        "thumbnail_url": str | None,
        "duration_ms":   int | None,
        "width":         int | None,
        "height":        int | None,
        "play_count":    int | None,
        "caption":       list,
    }],
    "collaborators": list,
    "reactions":     list,
    "feed":          list,
    "feed_video":    list,
}
```

## Facebook — Vídeo nativo

```python
{
    "__typename":    "facebook_video",
    "id":            str,
    "title":         str,
    "text":          str,
    "author":        dict,
    "thumbnail_url": str | None,
    "video": {
        "url_sd":      str | None,
        "captions":    list,
        "duration_ms": int | None,
        "width":       int | None,
        "height":      int | None,
        "is_looping":  bool,
        "is_spherical": bool,
    },
    "reaction_count": int, "comments_count": int,
    "share_count":    int, "play_count":     int | None,
    "is_live_streaming": bool,
    "feed": list,
}
```

## Facebook — Grupo

```python
{
    "__typename": "about_private_group",
    "id":         str,
    "name":       str,
    "url":        str,
    "privacy": {
        "level": str,             # "CLOSED" | "OPEN" | "SECRET"
        "description": str,
        "is_private": bool,
    },
    "description":        str,
    "total_members":      int,
    "total_members_text": str,
    "posts_last_day":     int | None,
    "admins": [{"id": str, "name": str, "url": str, "avatar": str}],
    "content_gated": bool,
}
```

## Facebook — Perfil

```python
{
    "__typename": "facebook_user_profile",
    "id":         str,
    "name":       str,
    "username":   str | None,
    "avatar":     str,
    "bio":        str,
    "followers_count":  int,
    "following_count":  int,
    "friends_count":    int,
    "education":    {"text": str, "name": str, "url": str, "id": str},
    "current_city": {"text": str, "name": str, "url": str, "id": str},
    "contact_info": {
        "email": str, "phone": str,
        "websites": list[str], "social_accounts": list[dict],
    },
    "feed": list,
}
```

## Facebook — Foto

```python
{
    "__typename":     "facebook_photo",
    "id":             str,
    "is_album":       bool,
    "parent_post_url": str | None,
    "author":         dict,
    "attachments": [{
        "type":                  "photo",
        "id":                    str,
        "url":                   str,
        "width":                 int | None,
        "height":                int | None,
        "accessibility_caption": str,
    }],
    "text":           str,
    "reaction_count": int,
}
```

## Instagram — Post

```python
{
    "platform":      "instagram",
    "code":          str,
    "id":            str,
    "permalink_url": str,
    "posted_at":     datetime | None,
    "user": {
        "id": str, "username": str, "full_name": str,
        "profile_pic_url": str, "is_verified": bool,
    },
    "media_type":            str,   # "Photo" | "Reel" | "carousel"
    "carousel_media_count":  int | None,
    "thumbnail":             str | None,
    "text":                  str | None,
    "like_count":            int,
    "comment_count":         int,
    "image_versions":        list | None,
    "video_versions":        list | None,
    "tagged_users":          list[dict],
    "comments":              list[dict],
    "feed":                  list[dict],
}
```

## Instagram — Reel

Misma estructura que el post de Instagram con un campo adicional:

```python
{
    # ... todos los campos de Instagram Post
    "media_repost_count": int,
}
```

## Serializar el resultado a JSON

```python
import json
from reaper import scrape
import asyncio

result = asyncio.run(scrape(url))

# El resultado contiene objetos datetime — usar default=str
json_str = json.dumps(result, ensure_ascii=False, indent=2, default=str)

# Guardar en archivo
with open("resultado.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
```
