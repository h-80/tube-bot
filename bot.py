import os
import requests
import google.generativeai as genai

# جلب المفاتيح من بيئة GitHub Actions
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FIREBASE_URL = "https://alfaham-tube-web-default-rtdb.firebaseio.com/videos.json"

print("جاري تشغيل بوت Alfaham Tube بنجاح...")

if not YOUTUBE_API_KEY or not GEMINI_API_KEY:
    print("خطأ: المفاتيح غير متوفرة في البيئة.")
    exit(1)

# إعداد جيميناي
genai.configure(api_key=GEMINI_API_KEY)

# جلب أحدث الفيديوهات من يوتيوب (مثال على القناة أو الكلمة المفتاحية)
# يمكنك تعديل المعرفات والمنطق حسب رغبتك هنا
url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&maxResults=1&q=technology&key={YOUTUBE_API_KEY}"
response = requests.get(url)

if response.status_code == 200:
    data = response.json()
    items = data.get('items', [])
    if items:
        video = items[0]
        title = video['snippet']['title']
        video_id = video['id']['videoId']
        print(آخر فيديو تم العثور عليه: {title})
        
        # استخدام Gemini لتحسين الوصف أو توليد محتوى
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"اكتب وصفاً جذاباً واحترافياً باللغة العربية لهذا الفيديو: {title}"
        ai_response = model.generate_content(prompt)
        enhanced_description = ai_response.text
        
        # رفع البيانات إلى فايربيز
        firebase_data = {
            "title": title,
            "videoId": video_id,
            "description": enhanced_description,
            "url": f"https://www.youtube.com/watch?v={video_id}"
        }
        
        fb_response = requests.post(FIREBASE_URL, json=firebase_data)
        if fb_response.status_code == 200:
            print("تم إرسال البيانات إلى Firebase بنجاح!")
        else:
            print(f"فشل إرسال البيانات إلى Firebase: {fb_response.text}")
    else:
        print("لم يتم العثور على فيديوهات.")
else:
    print(f"خطأ في الاتصال بيوتيوب: {response.text}")
