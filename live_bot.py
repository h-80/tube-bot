import os
import re

from bot import (
    KIDS_LIVE_SOURCE_URLS,
    extract_youtube_video_id,
    firebase_delete,
    firebase_get,
    firebase_put,
    publish_video,
    youtube,
)

FIREBASE_URL = os.getenv("FIREBASE_URL", "https://alfaham-tube-web-default-rtdb.firebaseio.com").rstrip("/")
LIVE_CHANNELS_STATE_PATH = "botState/liveSourceChannels"


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def get_video_details_batched(video_ids):
    details = {}
    unique_ids = list(dict.fromkeys(video_id for video_id in video_ids if video_id))
    for start in range(0, len(unique_ids), 50):
        batch = unique_ids[start:start + 50]
        response = youtube(
            "videos",
            {"part": "snippet,contentDetails", "id": ",".join(batch)},
        )
        details.update({item["id"]: item for item in response.get("items", [])})
    return details


def load_live_categories():
    raw_categories = firebase_get("categories") or {}

    def category_path(category_id):
        names = []
        current_id = category_id
        visited = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            data = raw_categories.get(current_id) or {}
            names.append(str(data.get("name") or data.get("title") or "").strip())
            current_id = data.get("parentId")
        return names

    categories = {}
    for category_id, data in raw_categories.items():
        name = str((data or {}).get("name") or (data or {}).get("title") or "").strip()
        path = category_path(category_id)
        path_text = normalize(" ".join(path))
        if name and ("بث" in path_text or "مباشر" in path_text):
            categories[category_id] = {
                "id": category_id,
                "name": name,
                "search_name": " ".join(reversed([part for part in path if part])),
                "is_live": True,
            }
    return categories


def load_articles():
    return firebase_get("articles") or {}


def article_video_id(article):
    if article.get("videoId"):
        return str(article["videoId"])
    for url in [article.get("videoUrl", ""), *(article.get("videoUrls", []) or [])]:
        video_id = extract_youtube_video_id(url)
        if video_id:
            return video_id
    return ""


def is_live_article(article, live_categories):
    return article.get("isLive") is True or article.get("categoryId") in live_categories


def load_source_channels(categories, articles):
    saved = firebase_get(LIVE_CHANNELS_STATE_PATH) or {}
    sources = {
        str(channel_id): {
            "categoryId": str(data.get("categoryId")),
            "title": str(data.get("title") or ""),
        }
        for channel_id, data in saved.items()
        if channel_id and isinstance(data, dict) and data.get("categoryId") in categories
    }

    article_ids = []
    for article in articles.values():
        article = article or {}
        if not is_live_article(article, categories):
            continue
        video_id = article_video_id(article)
        if video_id:
            article_ids.append(video_id)

    source_ids = [extract_youtube_video_id(url) for url in KIDS_LIVE_SOURCE_URLS]
    details = get_video_details_batched(article_ids + source_ids)

    for video_id in article_ids:
        detail = details.get(video_id) or {}
        channel_id = ((detail.get("snippet") or {}).get("channelId"))
        article = next(
            (item or {} for item in articles.values() if article_video_id(item or {}) == video_id),
            {},
        )
        category_id = str(article.get("categoryId") or "")
        if channel_id and category_id in categories:
            sources[channel_id] = {
                "categoryId": category_id,
                "title": str(article.get("title") or ""),
            }

    kids_category = next(
        (
            category for category in categories.values()
            if any(word in normalize(category["search_name"]) for word in ("أطفال", "اطفال", "طفل", "كرتون"))
        ),
        None,
    )
    if kids_category:
        for video_id in source_ids:
            detail = details.get(video_id) or {}
            channel_id = ((detail.get("snippet") or {}).get("channelId"))
            if channel_id:
                sources.setdefault(channel_id, {"categoryId": kids_category["id"], "title": ""})

    firebase_put(LIVE_CHANNELS_STATE_PATH, sources)
    return sources


def load_upload_playlists(channel_ids):
    if not channel_ids:
        return {}
    response = youtube(
        "channels",
        {"part": "contentDetails", "id": ",".join(channel_ids), "maxResults": 50},
    )
    return {
        item["id"]: (((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
        for item in response.get("items", [])
        if ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    }


def find_live_videos(sources):
    playlists = load_upload_playlists(list(sources))
    playlist_items = {}
    all_video_ids = []
    for channel_id, playlist_id in playlists.items():
        response = youtube(
            "playlistItems",
            {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": 10},
        )
        playlist_items[channel_id] = response.get("items", [])
        all_video_ids.extend(
            (item.get("contentDetails") or {}).get("videoId")
            for item in playlist_items[channel_id]
            if (item.get("contentDetails") or {}).get("videoId")
        )

    details = get_video_details_batched(all_video_ids)
    live_videos = {}
    for channel_id, items in playlist_items.items():
        for item in items:
            video_id = (item.get("contentDetails") or {}).get("videoId")
            detail = details.get(video_id) or {}
            snippet = detail.get("snippet") or item.get("snippet") or {}
            if video_id and snippet.get("liveBroadcastContent") == "live":
                live_videos[channel_id] = {
                    "id": video_id,
                    "title": str(snippet.get("title") or "بث مباشر").strip(),
                    "description": str(snippet.get("description") or "").strip(),
                    "thumbnail": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url", ""),
                    "duration": 0,
                }
                break
    return live_videos


def search_external_live_videos(sources, existing_live_videos):
    live_videos = {}
    for channel_id in sources:
        if channel_id in existing_live_videos:
            continue
        response = youtube(
            "search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "eventType": "live",
                "type": "video",
                "order": "date",
                "maxResults": 5,
            },
        )
        for item in response.get("items", []):
            video_id = (item.get("id") or {}).get("videoId")
            snippet = item.get("snippet") or {}
            if not video_id or snippet.get("liveBroadcastContent") != "live":
                continue
            live_videos[channel_id] = {
                "id": video_id,
                "title": str(snippet.get("title") or "بث مباشر").strip(),
                "description": str(snippet.get("description") or "").strip(),
                "thumbnail": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url", ""),
                "duration": 0,
            }
            break
    return live_videos


def remove_ended_live_articles(articles, live_categories):
    return


def run_once():
    if not os.getenv("YOUTUBE_API_KEY", "").strip():
        raise RuntimeError("YOUTUBE_API_KEY is missing")

    categories = load_live_categories()
    if not categories:
        print("No live categories found", flush=True)
        return

    articles = load_articles()
    sources = load_source_channels(categories, articles)
    remove_ended_live_articles(articles, categories)
    articles = load_articles()
    used_ids = {article_video_id(article or {}) for article in articles.values() if article_video_id(article or {})}
    live_videos = find_live_videos(sources)
    external_live_videos = search_external_live_videos(sources, live_videos)
    live_videos.update(external_live_videos)

    for channel_id, video in live_videos.items():
        if video["id"] in used_ids:
            continue
        category = categories.get(sources[channel_id]["categoryId"])
        if not category:
            continue
        publish_video(category, video)
        used_ids.add(video["id"])


if __name__ == "__main__":
    run_once()
