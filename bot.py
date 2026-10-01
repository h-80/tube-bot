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


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def is_excluded_category(name):
    return "مسلسل" in normalize(name)


def load_categories():
    raw_categories = firebase_get("categories") or {}
    categories = []
    for category_id, data in raw_categories.items():
        name = str((data or {}).get("name") or (data or {}).get("title") or "").strip()
        if name and not is_excluded_category(name):
            categories.append({"id": category_id, "name": name})
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


def is_live_category(name):
    return "بث مباشر" in normalize(name) or "مباشر" in normalize(name)


def search_video(category_name, used_ids):
    params = {
        "part": "snippet",
        "maxResults": 10,
        "order": "date",
        "type": "video",
        "q": category_name,
        "key": YOUTUBE_API_KEY,
    }
    if is_live_category(category_name):
        params["eventType"] = "live"

    response = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    for item in response.json().get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id or video_id in used_ids:
            continue
        snippet = item.get("snippet") or {}
        return {
            "id": video_id,
            "title": snippet.get("title", "فيديو جديد").strip(),
            "description": snippet.get("description", "").strip(),
            "thumbnail": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url", ""),
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
    if is_live_category(category["name"]):
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
        "isLive": is_live_category(category["name"]),
    }
    firebase_post("articles", article)
    record_daily_publish()
    log(f"Published: {video['title']} | category: {category['name']}")


def run_once():
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is missing")
    if daily_limit_reached():
        log(f"Daily limit reached: {MAX_DAILY_POSTS} videos")
        return

    categories = load_categories()
    if not categories:
        raise RuntimeError("No eligible categories found")

    category = choose_category(categories)
    used_ids = used_video_ids(load_articles())
    log(f"Searching one video for: {category['name']}")
    video = search_video(category["name"], used_ids)
    if not video:
        log("No new video found for this category; nothing was published")
        return
    publish_video(category, video)


if __name__ == "__main__":
    run_once()
