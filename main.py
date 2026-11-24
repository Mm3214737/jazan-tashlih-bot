import os
import json
import uuid
import requests
from fastapi import FastAPI, Request
from supabase import create_client, Client
from google import genai
from google.genai import types

app = FastAPI()

# ==========================================
# 1. إعداد المفاتيح (تُقرأ من متغيرات البيئة)
# ==========================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://kdcmeoqgllldycncmxpc.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

GREEN_ID = os.getenv("GREEN_ID", "710722812642")
GREEN_TOKEN = os.getenv("GREEN_TOKEN", "")
GREEN_API_URL = f"https://7107.api.greenapi.com/waInstance{GREEN_ID}"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 2. دوال الإرسال عبر Green-API
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
# 3. تحليل النصوص بالذكاء الاصطناعي (Gemini)
# ==========================================
def analyze_message_intent(text: str) -> dict:
    prompt = f"""
    أنت مساعد ذكي لسوق تشاليح وقطع غيار السيارات في جيزان.
    حلل الرسالة التالية وأرجع النتيجة بصيغة JSON فقط بدون أي نص إضافي:
    الرسالة: "{text}"

    المطلوب تحديده:
    1. "intent": اختر واحداً فقط من:
       - "register_supplier": إذا كان يريد تسجيل تشليح أو ورشة (مثل: تسجيل تشليح الأمل صناعية جيزان).
       - "add_part": إذا كان يعرض قطعة للبيع مع سعرها.
       - "search_part": إذا كان يبحث عن قطعة غيار يريد شراءها.
       - "help": تحية أو استفسار عام.
    2. "shop_name": اسم التشليح/الورشة (إن وجد).
    3. "location": الموقع في جيزان (إن وجد).
    4. "part_name": اسم القطعة (مثل: صدام أمامي، مكينة، شمعة).
    5. "car_model": نوع السيارة وموديلها (مثل: كامري 2020، هايلكس 2018).
    6. "condition": حالة القطعة (مستعمل/تشليح/جديد/وكالة) إن وجدت.
    7. "price": السعر كرقم فقط (إن وجد، وإلا null).
    """
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text)

# ==========================================
# 4. البحث في حراج (جيزان) عبر Serper
# ==========================================
def search_haraj_jazan(query: str) -> str:
    if not SERPER_API_KEY:
        return ""
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": f"site:haraj.com.sa {query} جيزان OR جازان", "num": 4}
    res = requests.post(url, headers=headers, json=payload).json()
    
    results = res.get("organic", [])
    if not results:
        return ""
    
    msg = "\n🌐 *نتائج إضافية من حراج (جيزان):*\n"
    for item in results[:3]:
        msg += f"• {item.get('title')}\n🔗 {item.get('link')}\n\n"
    return msg

# ==========================================
# 5. استقبال الرسائل (Webhook)
# ==========================================
@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    data = await request.json()
    
    # تجاهل الإشعارات غير المتعلقة بالرسائل الواردة
    if data.get("typeWebhook") != "incomingMessageReceived":
        return {"status": "ignored"}

    sender_data = data.get("senderData", {})
    chat_id = sender_data.get("chatId", "")
    # تجاهل القروبات والرد فقط على الخاص
    if not chat_id.endswith("@c.us"):
        return {"status": "group_ignored"}

    phone = chat_id.replace("@c.us", "")
    msg_data = data.get("messageData", {})
    msg_type = msg_data.get("typeMessage")

    text = ""
    image_download_url = None

    if msg_type == "textMessage":
        text = msg_data.get("textMessageData", {}).get("textMessage", "")
    elif msg_type == "extendedTextMessage":
        text = msg_data.get("extendedTextMessageData", {}).get("text", "")
    elif msg_type == "imageMessage":
        file_data = msg_data.get("fileMessageData", {})
        text = file_data.get("caption", "")
        image_download_url = file_data.get("downloadUrl")

    if not text and not image_download_url:
        return {"status": "empty"}

    # تحليل الرسالة
    analysis = analyze_message_intent(text or "إضافة قطعة بالصورة")
    intent = analysis.get("intent")

    # ------------------------------------------
    # الحالة أ: تسجيل مورد جديد (تشليح / ورشة)
    # ------------------------------------------
    if intent == "register_supplier":
        shop_name = analysis.get("shop_name") or "تشليح جيزان"
        location = analysis.get("location") or "صناعية جيزان"
        
        supabase.table("suppliers").upsert({
            "phone": phone,
            "shop_name": shop_name,
            "location": location
        }, on_conflict="phone").execute()

        reply = (
            f"✅ تم تسجيل منشأتكم بنجاح في منصة تشاليح جيزان!\n\n"
            f"🏪 *الاسم:* {shop_name}\n"
            f"📍 *الموقع:* {location}\n"
            f"📱 *الرقم المعتمد:* {phone}\n\n"
            f"📦 *لإضافة قطعة:* أرسل صورة القطعة واكتب معها (اسم القطعة + السيارة والموديل + السعر)."
        )
        send_whatsapp_text(chat_id, reply)

    # ------------------------------------------
    # الحالة ب: إضافة قطعة (مع صورة أو نص)
    # ------------------------------------------
    elif intent == "add_part" or image_download_url:
        # التأكد من أن المرسل مسجل كمورد
        supplier_check = supabase.table("suppliers").select("*").eq("phone", phone).execute()
        if not supplier_check.data:
            send_whatsapp_text(
                chat_id,
                "⚠️ عذراً، يجب تسجيل تشليحك أو ورشتك أولاً قبل إضافة القطع.\n"
                "أرسل مثال: *تسجيل تشليح النور صناعية جيزان*"
            )
            return {"status": "unregistered_supplier"}

        public_image_url = None
        if image_download_url:
            img_bytes = requests.get(image_download_url).content
            file_name = f"{phone}_{uuid.uuid4().hex[:8]}.jpg"
            supabase.storage.from_("parts-images").upload(
                file_name, img_bytes, {"content-type": "image/jpeg"}
            )
            public_image_url = supabase.storage.from_("parts-images").get_public_url(file_name)

        part_name = analysis.get("part_name") or text or "قطعة غيار"
        car_model = analysis.get("car_model") or "غير محدد"
        condition = analysis.get("condition") or "تشليح"
        price = analysis.get("price")

        supabase.table("parts").insert({
            "supplier_phone": phone,
            "part_name": part_name,
            "car_model": car_model,
            "condition": condition,
            "price": price,
            "image_url": public_image_url,
            "status": "available"
        }).execute()

        price_str = f"{price} ريال" if price else "على السوم"
        reply = (
            f"✅ تم نشر القطعة في سوق تشاليح جيزان:\n"
            f"🔧 *القطعة:* {part_name}\n"
            f"🚗 *السيارة:* {car_model}\n"
            f"💰 *السعر:* {price_str}"
        )
        send_whatsapp_text(chat_id, reply)

    # ------------------------------------------
    # الحالة ج: البحث عن قطعة للمشتري
    # ------------------------------------------
    elif intent == "search_part":
        part_q = analysis.get("part_name") or text
        car_q = analysis.get("car_model") or ""
        
        # البحث في قاعدة بيانات تشاليح جيزان
        db_query = supabase.table("parts").select("*, suppliers(shop_name, location)").eq("status", "available")
        if part_q:
            db_query = db_query.ilike("part_name", f"%{part_q}%")
        if car_q:
            db_query = db_query.ilike("car_model", f"%{car_q}%")
        
        found_parts = db_query.limit(5).execute().data

        if found_parts:
            send_whatsapp_text(chat_id, f"🔍 وجدنا *{len(found_parts)}* خيار(ات) متوفرة في تشاليح جيزان:")
            for p in found_parts:
                sup = p.get("suppliers") or {}
                price_txt = f"{p['price']} ريال" if p.get("price") else "على السوم"
                caption = (
                    f"🔧 *{p['part_name']} - {p['car_model']}*\n"
                    f"💰 السعر: *{price_txt}* ({p.get('condition', 'تشليح')})\n"
                    f"🏪 التشليح: {sup.get('shop_name', 'مورد معتمد')} - {sup.get('location', 'جيزان')}\n"
                    f"📞 للتواصل المباشر: https://wa.me/{p['supplier_phone']}"
                )
                if p.get("image_url"):
                    send_whatsapp_image(chat_id, p["image_url"], caption)
                else:
                    send_whatsapp_text(chat_id, caption)
        else:
            haraj_results = search_haraj_jazan(f"{part_q} {car_q}".strip())
            reply = f"❌ لم تتوفر القطعة حالياً لدى التشاليح المسجلة في جيزان.\n{haraj_results}"
            send_whatsapp_text(chat_id, reply)

    # ------------------------------------------
    # الحالة د: رسالة ترحيب وتعليمات
    # ------------------------------------------
    else:
        welcome = (
            "👋 حياك الله في *منصة تشاليح وورش جيزان الذكية*!\n\n"
            "🔍 *للبحث عن قطعة:* اكتب اسم القطعة ونوع السيارة وموديلها.\n"
            "_(مثال: صدام أمامي كامري 2020)_\n\n"
            "🏪 *لأصحاب التشاليح والورش:*\n"
            "1️⃣ للتسجيل أرسل: *تسجيل [اسم التشليح] [الموقع]*\n"
            "2️⃣ لإضافة قطعة: أرسل *صورة القطعة* واكتب معها الاسم والسيارة والسعر."
        )
        send_whatsapp_text(chat_id, welcome)

    return {"status": "ok"}
