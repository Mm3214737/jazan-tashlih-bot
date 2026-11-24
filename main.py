import os
import json
import uuid
import time
import requests
from fastapi import FastAPI, Request
from supabase import create_client, Client
from google import genai
from google.genai import types

app = FastAPI()

# ==========================================
# 1. إعداد المفاتيح (تُقرأ من متغيرات البيئة)
# ==========================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
GREEN_ID = "710722812642"
GREEN_TOKEN = os.getenv("GREEN_TOKEN", "")
GREEN_API_URL = f"https://7107.api.greenapi.com/waInstance{GREEN_ID}"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 2. دوال إرسال رسائل وصور واتساب عبر Green-API
# ==========================================
def send_whatsapp_text(chat_id: str, text: str):
    url = f"{GREEN_API_URL}/sendMessage/{GREEN_TOKEN}"
    payload = {"chatId": chat_id, "message": text}
    requests.post(url, json=payload)

def send_whatsapp_image(chat_id: str, image_url: str, caption: str):
    url = f"{GREEN_API_URL}/sendFileByUrl/{GREEN_TOKEN}"
    payload = {
        "chatId": chat_id,
        "urlFile": image_url,
        "fileName": "part.jpg",
        "caption": caption
    }
    requests.post(url, json=payload)

# ==========================================
# 3. استدعاء Gemini مع إعادة المحاولة عند الضغط
# ==========================================
def call_gemini_safe(contents) -> dict:
    for attempt in range(3):
        try:
            response = ai_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            return json.loads(response.text)
        except Exception:
            if attempt < 2:
                time.sleep(2)
                continue
            return {"intent": "busy"}

def analyze_user_message(text: str) -> dict:
    clean = text.strip()
    if any(w in clean for w in ["مرحبا", "مرحباً", "السلام", "هلا", "اهلا", "أهلا"]):
        return {"intent": "greeting"}

    if clean.startswith("تسجيل"):
        parts = clean.replace("تسجيل تشليح", "").replace("تسجيل", "").strip()
        return {"intent": "register_yard", "yard_name": parts or "تشليح جيزان", "location": "صناعية جيزان"}

    prompt = f"""
    أنت مساعد ذكي لسوق التشاليح وقطع الغيار في جيزان.
    حلل رسالة المستخدم التالية وأخرج النتيجة بصيغة JSON فقط بدون أي نص إضافي:
    الرسالة: "{text}"

    حدد النية (intent) من بين:
    - "greeting": إذا كانت تحية.
    - "register_yard": إذا أراد تسجيل تشليح.
    - "search_part": إذا كان يبحث عن قطعة غيار.
    - "other": أي شيء آخر.

    أخرج JSON بالهيكل التالي:
    {{
        "intent": "...",
        "yard_name": "اسم التشليح إن وجد",
        "location": "الموقع إن وجد",
        "part_name": "اسم القطعة إن وجد",
        "car_make": "نوع السيارة إن وجد",
        "car_model": "موديل السيارة أو السنة إن وجد"
    }}
    """
    return call_gemini_safe(prompt)

def analyze_part_image(image_bytes: bytes, caption: str) -> dict:
    prompt = f"""
    أنت خبير قطع غيار سيارات في تشاليح جيزان.
    انظر إلى صورة القطعة واقرأ وصف البائع: "{caption}"
    استخرج بيانات القطعة بصيغة JSON فقط:
    {{
        "part_name": "اسم القطعة بدقة",
        "car_make": "الشركة المصنعة (مثلاً تويوتا، هيونداي، فورد)",
        "car_model": "اسم السيارة وسنة الصنع",
        "condition": "حالة القطعة (ممتازة / جيدة / تحتاج رش)",
        "price": السعر كرقم صحيح فقط (اكتب 0 إذا لم يُذكر السعر)
    }}
    """
    res = call_gemini_safe([
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
        prompt
    ])
    if res.get("intent") == "busy":
        return {"part_name": caption or "قطعة غيار", "car_make": "", "car_model": "", "condition": "مستعملة", "price": 0}
    return res

# ==========================================
# 4. البحث في الإنترنت عبر Serper
# ==========================================
def search_external_web(query: str) -> str:
    if not SERPER_API_KEY or SERPER_API_KEY == "none":
        return ""
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": f"{query} تشليح السعودية حراج", "gl": "sa", "hl": "ar", "num": 3}
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code == 200:
        results = res.json().get("organic", [])
        links = [f"- {r.get('title')}: {r.get('link')}" for r in results]
        return "\n".join(links)
    return ""

# ==========================================
# 5. استقبال رسائل واتساب (Webhook)
# ==========================================
@app.get("/")
def home():
    return {"status": "Jazan Tashlih Bot is Live!"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    if data.get("typeWebhook") != "incomingMessageReceived":
        return {"status": "ignored"}

    sender_data = data.get("senderData", {})
    chat_id = sender_data.get("chatId", "")
    phone = chat_id.replace("@c.us", "")
    message_data = data.get("messageData", {})
    msg_type = message_data.get("typeMessage", "")

    if msg_type in ["textMessage", "extendedTextMessage"]:
        text = (
            message_data.get("textMessageData", {}).get("textMessage")
            or message_data.get("extendedTextMessageData", {}).get("text")
            or ""
        ).strip()

        analysis = analyze_user_message(text)
        intent = analysis.get("intent")

        if intent == "busy":
            send_whatsapp_text(chat_id, "⏳ النظام عليه ضغط بسيط حالياً، أعد إرسال طلبك بعد لحظات.")
            return {"status": "busy"}

        if intent == "greeting":
            welcome = (
                "أهلاً بك في *سوق تشاليح جيزان الذكي* 🚗🔧\n\n"
                "🔹 *للباحثين عن قطع غيار:*\n"
                "أرسل اسم القطعة والسيارة والموديل مباشرة (مثال: _ابغى صدام أمامي كامري 2020_).\n\n"
                "🔹 *لأصحاب التشاليح والموردين:*\n"
                "1️⃣ لتسجيل تشليحك أرسل: _تسجيل تشليح [اسم التشليح] [الموقع]_\n"
                "2️⃣ لإضافة قطعة: أرسل *صورة القطعة* واكتب معها اسمها والسيارة والسعر."
            )
            send_whatsapp_text(chat_id, welcome)

        elif intent == "register_yard":
            yard_name = analysis.get("yard_name") or text.replace("تسجيل", "").strip()
            location = analysis.get("location") or "صناعية جيزان"
            supabase.table("yards").upsert({
                "phone": phone,
                "name": yard_name,
                "location": location
            }, on_conflict="phone").execute()
            send_whatsapp_text(
                chat_id,
                f"✅ تم تسجيل *{yard_name}* ({location}) بنجاح!\n"
                "يمكنك الآن إرسال صور القطع مع الوصف والسعر لعرضها للمشترين فوراً."
            )

        elif intent == "search_part":
            part_name = analysis.get("part_name", "")
            car_make = analysis.get("car_make", "")
            car_model = analysis.get("car_model", "")

            query = supabase.table("parts").select("*, yards(name, phone, location)")
            if part_name:
                query = query.ilike("part_name", f"%{part_name}%")
            if car_make:
                query = query.ilike("car_make", f"%{car_make}%")

            results = query.limit(5).execute().data

            if results:
                send_whatsapp_text(chat_id, f"🔍 وجدنا *{len(results)}* قطعة مطابقة في تشاليح جيزان:")
                for item in results:
                    yard = item.get("yards") or {}
                    y_name = yard.get("name", "تشليح جيزان")
                    y_phone = yard.get("phone", "")
                    y_loc = yard.get("location", "صناعية جيزان")
                    price_txt = f"{item['price']} ريال" if item.get("price") else "على السوم"
                    caption = (
                        f"🔧 *{item.get('part_name')} - {item.get('car_make')} {item.get('car_model')}*\n"
                        f"💰 السعر: *{price_txt}*\n"
                        f"📋 الحالة: {item.get('condition', 'جيدة')}\n"
                        f"🏪 التشليح: {y_name} ({y_loc})\n"
                        f"📞 للتواصل واتساب: https://wa.me/{y_phone}"
                    )
                    if item.get("image_url"):
                        send_whatsapp_image(chat_id, item["image_url"], caption)
                    else:
                        send_whatsapp_text(chat_id, caption)
            else:
                web_links = search_external_web(f"{part_name} {car_make} {car_model}")
                reply = f"⚠️ عذراً، لم تتوفر *{part_name} {car_make} {car_model}* حالياً في قاعدة بيانات تشاليح جيزان المسجلة."
                if web_links:
                    reply += f"\n\n🌐 نتائج مقترحة من الإنترنت:\n{web_links}"
                send_whatsapp_text(chat_id, reply)
        else:
            send_whatsapp_text(
                chat_id,
                "اكتب *مرحبا* لعرض القائمة، أو اكتب اسم القطعة وسيارتك للبحث عنها مباشرة 🚗"
            )

    elif msg_type == "imageMessage":
        file_data = message_data.get("fileMessageData", {})
        download_url = file_data.get("downloadUrl", "")
        caption = file_data.get("caption", "")

        yard_res = supabase.table("yards").select("*").eq("phone", phone).execute().data
        if not yard_res:
            send_whatsapp_text(
                chat_id,
                "⚠️ رقمك غير مسجل كتشليح بعد.\n"
                "أرسل أولاً: *تسجيل تشليح [اسم تشليحك] صناعية جيزان*"
            )
            return {"status": "
