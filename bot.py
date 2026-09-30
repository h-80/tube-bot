import os
import requests
import google.generativeai as genai

# جلب المفاتيح والإعدادات من بيئة العمل
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FIREBASE_URL = "https://alfaham-tube-web-default-rtdb.firebaseio.com"

print("Starting Alfaham Tube Bot...")

if not YOUTUBE_API_KEY or not GEMINI_API_KEY:
    print("Error: API keys are missing.")
    exit(1)

# إعداد جيميناي
genai.configure(api_key=GEMINI_API_KEY)

# 1. جلب الأقسام من Firebase لمعرفة الهيكلية
categories_response = requests.get(f"{FIREBASE_URL}/categories.json")
categories = {}
if categories_response.status_code == 200 and categories_response.json():
    categories = categories_response.json()
    print(f"Fetched categories count: {len(categories)}")
else:
    print("Error: No categories found in Firebase.")
    exit(1)

# 2. جلب أحدث الفيديوهات من يوتيوب
youtube_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&maxResults=1&order=date&type=video&key={YOUTUBE_API_KEY}"
response = requests.get(youtube_url)

if response.status_code == 200:
    data = response.json()
    items = data.get('items', [])
    if items:
        video = items[0]
        title = video['snippet']['title']
        video_id = video['id']['videoId']
        thumbnail_url = video['snippet']['thumbnails']['high']['url']
        print(f"Latest video found: {title}")
        
        # 3. إعداد الأقسام للذكاء الاصطناعي
        categories_list_str = ""
        for cat_id, cat_data in categories.items():
            cat_name = cat_data.get('name', '')
            categories_list_str += f"- ID: {cat_id} | Name: {cat_name}\n"

        # 4. استخدام Gemini لتصنيف الفيديو
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"""
Analyze this YouTube video title:
"{title}"

Choose the most appropriate Category ID from this list:
{categories_list_str}

Return ONLY the Category ID string and nothing else.
"""
        ai_response = model.generate_content(prompt)
        selected_category_id = ai_response.text.strip()
        
        if selected_category_id not in categories:
            print("AI returned invalid ID, using default category.")
            selected_category_id = list(categories.keys())[0]
        else:
            print(f"Selected Category ID: {selected_category_id}")

        # توليد وصف احترافي
        desc_prompt = f"Write an engaging Arabic description and short article summary for this video title: {title}"
        desc_response = model.generate_content(desc_prompt)
        enhanced_description = desc_response.text

        # 5. تجهيز بيانات الفيديو للنشر
        article_data = {
            "title": title,
            "categoryId": selected_category_id,
            "imageUrl": thumbnail_url,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "videoUrl": f"https://www.youtube.com/watch?v={video_id}",
            "videoUrls": [f"https://www.youtube.com/watch?v={video_id}"],
            "content": f"<p>{enhanced_description}</p>",
            "createdAt": {".sv": "timestamp"}
        }
        
        # 6. رفع البيانات إلى Firebase
        fb_response = requests.post(f"{FIREBASE_URL}/articles.json", json=article_data)
        if fb_response.status_code == 200:
            print("Video successfully published to Firebase!")
        else:
            print(f"Failed to push to Firebase: {fb_response.text}")
    else:
        print("No new videos found on YouTube.")
else:
    print(f"YouTube API Error: {response.text}")
