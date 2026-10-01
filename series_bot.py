import html
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import requests

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
FIREBASE_URL = os.getenv("FIREBASE_URL", "https://alfaham-tube-web-default-rtdb.firebaseio.com").rstrip("/")
SOURCES_FILE = os.getenv("SERIES_SOURCES_FILE", "مسلسلات.txt")
REQUEST_TIMEOUT = 30
SERIES_STRONG_WORDS = (
    "مسلسل", "المسلسل", "مسلسلات", "المسلسلات", "سلسلة", "السلسلة",
    "مسلسلات عربية", "جميع الحلقات", "كل الحلقات", "الحلقات", "حلقات",
    "حلقات كاملة", "الحلقات كاملة", "جميع حلقات", "مسلسل كامل",
    "الموسم", "موسم", "المواسم", "مواسم", "الحلقة كاملة", "جميع المواسم",
    "الجزء", "الفصل", "المدبلج", "المترجم", "المدبلجة", "المترجمة",
    "series", "drama",
)
SERIES_WEAK_WORDS = (
    "حلقة", "الحلقة", "حلقات", "موسم", "الموسم", "مواسم", "المواسم",
    "episode", "episodes", "season", "seasons", "ep",
)
SERIES_NUMERIC_PATTERNS = (
    r"(?:حلقة|الحلقة|episode|episodes|ep)\s*[:#-]?\s*\d{1,3}",
    r"(?:موسم|الموسم|season|seasons)\s*[:#-]?\s*\d{1,3}",
    r"\d{1,3}\s*(?:حلقة|الحلقة|episode|episodes|ep)",
    r"\d{1,3}\s*(?:موسم|الموسم|season|seasons)",
    r"(?:الجزء|الفصل)\s*\d{1,3}",
)
REJECTED_WORDS = ("برنامج", "برامج", "لقاء", "مقابلة", "إعلان", "اعلان", "تريلر", "ملخص", "مقطع", "أغنية", "اغنية", "promo", "trailer", "recap", "summary", "رياضة", "كرة", "مباراة")


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def log(message):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {message}", flush=True)


def firebase(method, path, payload=None):
    response = requests.request(method, f"{FIREBASE_URL}/{path}.json", json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def youtube(path, params):
    params = {**params, "key": YOUTUBE_API_KEY}
    response = requests.get(f"https://www.googleapis.com/youtube/v3/{path}", params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def load_sources():
    sources = []
    current_section = ""
    with open(SOURCES_FILE, encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith("http"):
                current_section = line
                continue
            sources.append({"section": current_section, "url": line})
    return sources


def handle_from_url(url):
    match = re.search(r"/@([^/]+)", unquote(url))
    return match.group(1) if match else ""


def has_series_marker(value):
    return any(word in value for word in SERIES_STRONG_WORDS) or any(word in value for word in SERIES_WEAK_WORDS)


def has_numeric_series_pattern(value):
    return any(re.search(pattern, value) for pattern in SERIES_NUMERIC_PATTERNS)


def is_strong_series_context(value, playlist_value=""):
    if any(word in value for word in SERIES_STRONG_WORDS):
        return True
    if not any(word in value for word in SERIES_WEAK_WORDS):
        return False
    return bool(
        any(word in playlist_value for word in SERIES_STRONG_WORDS)
        and has_numeric_series_pattern(value)
    )


def series_video_title(title, playlist_title=""):
    value = normalize(title)
    playlist_value = normalize(playlist_title)
    if any(word in value for word in REJECTED_WORDS):
        return False
    if is_strong_series_context(value, playlist_value):
        return True
    if any(word in playlist_value for word in SERIES_STRONG_WORDS) and has_numeric_series_pattern(value):
        return True
    return False


def playlist_is_series(title):
    value = normalize(title)
    if any(word in value for word in REJECTED_WORDS):
        return False
    if any(word in value for word in SERIES_STRONG_WORDS):
        return True
    if not any(word in value for word in SERIES_WEAK_WORDS):
        return False
    return has_numeric_series_pattern(value)


def get_category_id(section, categories):
    wanted = normalize(section).replace("قسم ", "")
    exact = [item for item in categories if normalize(item["name"]) == wanted]
    if exact:
        return exact[0]["id"]
    matches = [item for item in categories if wanted and (wanted in normalize(item["name"]) or normalize(item["name"]) in wanted)]
    return matches[0]["id"] if matches else None


def get_playlists(channel_handle):
    channels = youtube("channels", {"part": "id", "forHandle": f"@{channel_handle}"}).get("items", [])
    if not channels:
        return []
    channel_id = channels[0]["id"]
    playlists = youtube("playlists", {"part": "snippet", "channelId": channel_id, "maxResults": 50}).get("items", [])
    return [
        item for item in playlists
        if playlist_is_series((item.get("snippet") or {}).get("title", ""))
    ]


def get_playlist_episodes(playlist_id, playlist_title):
    items = youtube("playlistItems", {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": 50}).get("items", [])
    episodes = []
    for item in items:
        snippet = item.get("snippet") or {}
        title = str(snippet.get("title", "")).strip()
        video_id = (item.get("contentDetails") or {}).get("videoId")
        if not video_id or not series_video_title(title, playlist_title):
            continue
        episodes.append({
            "id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "imageUrl": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url", ""),
        })
    return list(reversed(episodes))


def publish_or_update_series(playlist, category_id, section):
    playlist_id = playlist["id"]
    playlist_title = (playlist.get("snippet") or {}).get("title", "مسلسل")
    episodes = get_playlist_episodes(playlist_id, playlist_title)
    if not episodes:
        return
    articles = firebase("GET", "articles") or {}
    existing_id = None
    existing = None
    for article_id, article in articles.items():
        if (article or {}).get("seriesKey") == playlist_id:
            existing_id = article_id
            existing = article or {}
            break
    known_urls = set((existing or {}).get("videoUrls", []) or [])
    new_episodes = [episode for episode in episodes if episode["url"] not in known_urls]
    if not new_episodes and existing_id:
        return
    urls = list(known_urls) + [episode["url"] for episode in new_episodes]
    first_image = (existing or {}).get("imageUrl") or episodes[0]["imageUrl"]
    article = {
        "title": playlist_title,
        "categoryId": category_id,
        "imageUrl": first_image,
        "url": "",
        "videoId": episodes[0]["id"],
        "videoUrl": urls[0],
        "videoUrls": urls,
        "content": f"<p>حلقات {html.escape(playlist_title)} - القسم: {html.escape(section)}</p>",
        "seriesKey": playlist_id,
        "source": "YouTube series playlist",
        "isSeries": True,
        "createdAt": (existing or {}).get("createdAt") or {".sv": "timestamp"},
        "updatedAt": {".sv": "timestamp"},
    }
    if existing_id:
        firebase("PUT", f"articles/{existing_id}", article)
        log(f"Updated {playlist_title}: +{len(new_episodes)} episodes")
    else:
        firebase("POST", "articles", article)
        log(f"Published {playlist_title}: {len(urls)} episodes")


def choose_source_by_section(sources):
    sections = {}
    for source in sources:
        sections.setdefault(source["section"], []).append(source)
    section_names = list(sections)
    section_index = int(firebase("GET", "botState/seriesSectionIndex") or 0)
    section = section_names[section_index % len(section_names)]
    channel_indexes = firebase("GET", "botState/seriesSectionChannelIndexes") or {}
    channel_index = int(channel_indexes.get(section) or 0)
    source = sections[section][channel_index % len(sections[section])]
    channel_indexes[section] = (channel_index + 1) % len(sections[section])
    firebase("PUT", "botState/seriesSectionIndex", (section_index + 1) % len(section_names))
    firebase("PUT", "botState/seriesSectionChannelIndexes", channel_indexes)
    return source


def run_once():
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is missing")
    sources = load_sources()
    categories = [{"id": key, "name": (value or {}).get("name", "")} for key, value in (firebase("GET", "categories") or {}).items()]
    if not sources:
        log("No series sources found")
        return
    source = choose_source_by_section(sources)
    category_id = get_category_id(source["section"], categories)
    if not category_id:
        log(f"Category not found: {source['section']}")
        return
    handle = handle_from_url(source["url"])
    if not handle:
        log(f"Invalid channel URL: {source['url']}")
        return
    for playlist in get_playlists(handle):
        publish_or_update_series(playlist, category_id, source["section"])


if __name__ == "__main__":
    run_once()
