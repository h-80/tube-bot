import os
import requests
from googleapiclient.discovery import build
import google.generativeai as genai

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FIREBASE_DB_URL = "https://alfaham-tube-web-default-rtdb.firebaseio.com/"

genai.configure(api_key=GEMINI_API_KEY)
generation_model = genai.GenerativeModel('gemini-1.5-flash')

def fetch_latest_youtube_videos():
    print("جاري البحث عن أحدث الفيديوهات من يوتيوب...")
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    
    request = youtube.search().list(
        part="snippet",
        maxResults=3,
        q="technology programming",
        type="video",
        order="date"
    )
    response = request.execute()
    return response.get("items", [])

def enhance_with_gemini(title, description):
    print(f"تحسين المحتوى عبر الذكاء الاصطناعي للفيديو: {title}")
    prompt = f"قم بإعادة صياغة هذا العنوان والوصف ليكون أكثر جاذبية واحترافية باللغة العربية:\nالعنوان: {title}\nالوصف: {description}\nأعطني النتيجة بصيغة عنوان ووصف فقط."
    try:
        response = generation_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"خطأ في الاتصال مع جيميني: {e}")
        return title

def save_to_firebase(video_data):
    print("جاري حفظ الفيديو في قاعدة بيانات Firebase...")
    response = requests.post(f"{FIREBASE_DB_URL}/videos.json", json=video_data)
    if response.status_code == 200:
        print("تم حفظ الفيديو بنجاح في الموقع!")
    else:
        print("فشل الحفظ في قاعدة البيانات.")

if __name__ == "__main__":
    videos = fetch_latest_youtube_videos()
    for video in videos:
        video_id = video['id']['videoId']
        original_title = video['snippet']['title']
        original_desc = video['snippet']['description']
        thumbnail = video['snippet']['thumbnails']['high']['url']
        
        enhanced_info = enhance_with_gemini(original_title, original_desc)
        
        video_payload = {
            "videoId": video_id,
            "title": enhanced_info,
            "description": original_desc,
            "thumbnail": thumbnail,
            "url": f"https://www.youtube.com/watch?v={video_id}"
        }
        
        save_to_firebase(video_payload)