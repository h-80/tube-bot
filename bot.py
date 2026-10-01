import html
import os
import re
from datetime import datetime, timezone

import requests


YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite"
FIREBASE_URL = os.getenv(
    "FIREBASE_URL",
    "https://alfaham-tube-web-default-rtdb.firebaseio.com",
).rstrip("/")
MAX_DAILY_POSTS = 10
REQUEST_TIMEOUT = 30
MIN_FILM_SECONDS = 60 * 60
REJECTED_TITLE_WORDS = (
    "مقطع", "مشهد", "تريلر", "إعلان", "اعلان", "برومو", "تشويقي",
    "trailer", "clip", "promo", "teaser", "episode", "حلقة", "مسلسل",
)


def log(message):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] {message}", flush=True)


def firebase_get(path):
    response = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def firebase_put(path, value):
    response = requests.put(f"{FIREBASE_URL}/{path}.json", json=value, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def firebase_post(path, value):
    response = requests.post(f"{FIREBASE_URL}/{path}.json", json=value, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def firebase_delete(path):
    response = requests.delete(f"{FIREBASE_URL}/{path}.json", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def load_categories():
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

    child_ids = {
        data.get("parentId")
        for data in raw_categories.values()
        if isinstance(data, dict) and data.get("parentId")
    }
    categories = []
    for category_id, data in raw_categories.items():
        name = str((data or {}).get("name") or (data or {}).get("title") or "").strip()
        path = category_path(category_id)
        path_text = " ".join(path)
        live = "بث" in normalize(path_text) or "مباشر" in normalize(path_text)
        excluded_series = "مسلسل" in normalize(name) and not live
        if name and not excluded_series and category_id not in child_ids:
            search_name = " ".join(reversed([part for part in path if part]))
            categories.append({"id": category_id, "name": name, "search_name": search_name, "is_live": live})
    if not categories:
        for category_id, data in raw_categories.items():
            name = str((data or {}).get("name") or (data or {}).get("title") or "").strip()
            if name and not is_excluded_category(name):
                path = category_path(category_id)
                categories.append({"id": category_id, "name": name, "search_name": " ".join(reversed(path)), "is_live": False})
    return categories


def load_articles():
    return firebase_get("articles") or {}


def extract_video_id(article):
    if article.get("videoId"):
        return str(article["videoId"])
    urls = [article.get("videoUrl", ""), article.get("url", "")]
    urls.extend(article.get("videoUrls", []) or [])
    for url in urls:
        match = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{6,})", str(url))
        if match:
            return match.group(1)
    return ""


def used_video_ids(articles):
    return {extract_video_id(article or {}) for article in articles.values() if extract_video_id(article or {})}


def today_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def daily_limit_reached():
    state = firebase_get("botState/youtubeDaily") or {}
    return state.get("date") == today_key() and int(state.get("count") or 0) >= MAX_DAILY_POSTS


def record_daily_publish():
    path = "botState/youtubeDaily"
    current = firebase_get(path) or {}
    count = int(current.get("count") or 0) if current.get("date") == today_key() else 0
    firebase_put(path, {"date": today_key(), "count": count + 1})


def choose_category(categories):
    state_path = "botState/youtubeNextCategoryIndex"
    current_index = int(firebase_get(state_path) or 0)
    category = categories[current_index % len(categories)]
    firebase_put(state_path, (current_index + 1) % len(categories))
    return category


def is_live_category(category):
    return bool(category.get("is_live")) or "بث" in normalize(category.get("name")) or "مباشر" in normalize(category.get("name"))


def parse_duration(duration):
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        duration or "",
    )
    if not match:
        return 0
    return (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def rejected_title(title):
    normalized_title = normalize(title)
    return any(word in normalized_title for word in REJECTED_TITLE_WORDS)


def get_video_details(video_ids):
    if not video_ids:
        return {}
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,contentDetails",
            "id": ",".join(video_ids),
            "key": YOUTUBE_API_KEY,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return {item["id"]: item for item in response.json().get("items", [])}


def cleanup_unavailable_videos():
    state_path = "botState/lastVideoCleanup"
    if firebase_get(state_path) == today_key():
        return

    articles = load_articles()
    references = {}
    for article_id, article in articles.items():
        article = article or {}
        video_id = extract_video_id(article)
        if video_id and (article.get("source") == "YouTube" or article.get("videoId")):
            references[video_id] = {
                "article_id": article_id,
                "is_live": article.get("isLive") is True,
            }

    video_ids = list(references)
    removed = 0
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start:start + 50]
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "status,snippet", "id": ",".join(batch), "key": YOUTUBE_API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        found = {item["id"]: item for item in response.json().get("items", [])}
        for video_id in batch:
            item = found.get(video_id)
            status = (item or {}).get("status") or {}
            snippet = (item or {}).get("snippet") or {}
            unavailable = not item or status.get("privacyStatus") == "private" or status.get("embeddable") is False
            ended_live = references[video_id]["is_live"] and snippet.get("liveBroadcastContent") == "none"
            if unavailable or ended_live:
                firebase_delete(f"articles/{references[video_id]['article_id']}")
                removed += 1

    firebase_put(state_path, today_key())
    log(f"Daily cleanup complete: removed {removed} unavailable videos")


def search_video(category, used_ids):
    category_name = category["name"]
    live = is_live_category(category)
    params = {
        "part": "snippet",
        "maxResults": 10,
        "order": "date",
        "type": "video",
        "q": category_name if live else f"فيلم كامل {category_name}",
        "key": YOUTUBE_API_KEY,
    }
    if live:
        params["eventType"] = "live"
    else:
        params["videoDuration"] = "long"

    response = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    items = response.json().get("items", [])
    candidate_ids = [
        (item.get("id") or {}).get("videoId")
        for item in items
        if (item.get("id") or {}).get("videoId")
    ]
    details = get_video_details(candidate_ids)

    for item in items:
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id or video_id in used_ids:
            continue
        snippet = item.get("snippet") or {}
        title = snippet.get("title", "").strip()
        detail = details.get(video_id) or {}
        duration = parse_duration((detail.get("contentDetails") or {}).get("duration"))
        if not live and (duration < MIN_FILM_SECONDS or rejected_title(title)):
            log(f"Skipped non-film: {title} ({duration // 60} minutes)")
            continue
        return {
            "id": video_id,
            "title": title or "فيديو جديد",
            "description": snippet.get("description", "").strip(),
            "thumbnail": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url", ""),
            "duration": duration,
        }
    return None


def generate_description(title, category_name):
    fallback = f"شاهد هذا الفيديو من قسم {category_name}. نرجو متابعة المصدر الأصلي لمعرفة التفاصيل الكاملة."
    if not GEMINI_API_KEY:
        return fallback

    prompt = (
        "اكتب وصفًا عربيًا قصيرًا وأصليًا من 60 إلى 100 كلمة لفيديو يوتيوب. "
        "لا تنسخ أي نص، ولا تدّعي معلومات غير موجودة. أعد النص فقط دون عنوان.\n"
        f"عنوان الفيديو: {title}\nالقسم: {category_name}"
    )
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.6, "maxOutputTokens": 300},
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as error:
        log(f"Gemini skipped; using fallback description: {error}")
        return fallback


def publish_video(category, video):
    video_url = f"https://www.youtube.com/watch?v={video['id']}"
    description = generate_description(video["title"], category["name"])
    content = f"<p>{html.escape(description)}</p>"
    if is_live_category(category):
        content = f"<p><strong>بث مباشر الآن</strong></p>{content}"

    article = {
        "title": video["title"],
        "categoryId": category["id"],
        "imageUrl": video["thumbnail"],
        "url": video_url,
        "videoId": video["id"],
        "videoUrl": video_url,
        "videoUrls": [video_url],
        "content": content,
        "createdAt": {".sv": "timestamp"},
        "source": "YouTube",
        "isLive": is_live_category(category),
    }
    firebase_post("articles", article)
    record_daily_publish()
    log(f"Published: {video['title']} | category: {category['name']}")


def run_once():
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is missing")
    cleanup_unavailable_videos()
    if daily_limit_reached():
        log(f"Daily limit reached: {MAX_DAILY_POSTS} videos")
        return

    categories = load_categories()
    if not categories:
        raise RuntimeError("No eligible categories found")

    category = choose_category(categories)
    used_ids = used_video_ids(load_articles())
    log(f"Searching one video for: {category['name']}")
    video = search_video(category, used_ids)
    if not video:
        log("No new video found for this category; nothing was published")
        return
    publish_video(category, video)


if __name__ == "__main__":
    run_once()
