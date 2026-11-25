import os
import re
import json
import time
import base64
import requests
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from supabase import create_client, Client

app = FastAPI()

# --- إعدادات البيئة ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# إعدادات Green-API (الحالية)
GREEN_API_URL = os.environ.get("GREEN_API_URL", "https://api.green-api.com")
GREEN_ID_INSTANCE = os.environ.get("GREEN_ID_INSTANCE", "")
GREEN_API_TOKEN = os.environ.get("GREEN_API_TOKEN", "")

# إعدادات Evolution API (للانتقال بعد انتهاء مهلة الواتساب)
EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "jazan2026")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "jazan")
USE_EVOLUTION = os.environ.get("USE_EVOLUTION", "false").lower() == "true"

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- أدوات مساعدة ---
def normalize_arabic_numbers(text: str) -> str:
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    return text.translate(str.maketrans(arabic_digits, english_digits))


def send_whatsapp_text(chat_id: str, message: str):
    if USE_EVOLUTION and EVOLUTION_URL:
        number = chat_id.split("@")[0]
        url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
        payload = {"number": number, "text": message}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=15)
            print("Evolution Send Status:", r.status_code, r.text[:200])
        except Exception as e:
            print("Evolution Send Error:", e)
        return

    if GREEN_ID_INSTANCE and GREEN_API_TOKEN:
        url = f"{GREEN_API_URL}/waInstance{GREEN_ID_INSTANCE}/sendMessage/{GREEN_API_TOKEN}"
        try:
            r = requests.post(url, json={"chatId": chat_id, "message": message}, timeout=15)
            print("GreenAPI Send Status:", r.status_code, r.text[:200])
        except Exception as e:
            print("GreenAPI Send Error:", e)


def send_whatsapp_image(chat_id: str, image_url: str, caption: str = ""):
    if USE_EVOLUTION and EVOLUTION_URL:
        number = chat_id.split("@")[0]
        url = f"{EVOLUTION_URL}/message/sendMedia/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
        payload = {
            "number": number,
            "mediatype": "image",
            "media": image_url,
            "caption": caption,
        }
        try:
            requests.post(url, json=payload, headers=headers, timeout=15)
        except Exception as e:
            print("Evolution Image Error:", e)
        return

    if GREEN_ID_INSTANCE and GREEN_API_TOKEN:
        url = f"{GREEN_API_URL}/waInstance{GREEN_ID_INSTANCE}/sendFileByUrl/{GREEN_API_TOKEN}"
        try:
            requests.post(
                url,
                json={"chatId": chat_id, "urlFile": image_url, "fileName": "part.jpg", "caption": caption},
                timeout=15,
            )
        except Exception as e:
            print("GreenAPI Image Error:", e)


# --- الذكاء الاصطناعي مع التبديل التلقائي وخطة الطوارئ ---
def call_gemini_safe(contents) -> dict:
    models_to_try = ["gemini-3.8-flash", "gemini-2.5-flash-lite"]
    for model_name in models_to_try:
        try:
            res = ai_client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
            return json.loads(res.text)
        except Exception as e:
            print(f"Gemini Error ({model_name}):", e)
            time.sleep(0.5)

    # خطة بديلة فورية إذا كانت خوادم Gemini مشغولة
    raw_text = contents if isinstance(contents, str) else ""
    if raw_text:
        clean_query = raw_text.splitlines()[-1].replace("رسالة العميل:", "").strip()
        clean_query = normalize_arabic_numbers(clean_query)
        for prefix in ["ابغى", "أبغى", "ابي", "أبي", "مطلوب", "عندكم", "في"]:
            if clean_query.startswith(prefix + " "):
                clean_query = clean_query[len(prefix) + 1 :].strip()
        if len(clean_query) >= 3:
            return {
                "intent": "search",
                "part_name": clean_query,
                "car_model": "",
                "year": "",
            }
    return {"intent": "busy"}


def analyze_user_message(text: str) -> dict:
    clean = normalize_arabic_numbers(text.strip())
    greetings = ["مرحبا", "السلام", "هلا", "اهلا", "أهلا", "مساء", "صباح", "سلام", "كيف الحال", "hi", "hello"]
    if len(clean) <= 18 and any(w in clean.lower() for w in greetings):
        return {"intent": "greeting"}

    prompt = f"""
أنت مساعد ذكي لسوق تشاليح جيزان لقطع غيار السيارات.
حلل رسالة العميل وأرجع JSON فقط بالصيغة التالية:
{{
  "intent": "search" أو "register_supplier" أو "greeting" أو "other",
  "part_name": "اسم القطعة بدون كلمات زائدة",
  "car_model": "نوع وموديل السيارة مثل كامري او هايلكس",
  "year": "سنة الصنع بالأرقام الإنجليزية إن وجدت مثل 2019",
  "supplier_name": "اسم التشليح إذا كانت الرسالة تسجيل تشليح",
  "supplier_location": "موقع التشليح إذا كانت الرسالة تسجيل تشليح"
}}
رسالة العميل: {clean}
"""
    return call_gemini_safe(prompt)


# --- البحث في قاعدة بيانات تشاليح جيزان ---
def search_local_supabase(part_name: str, car_model: str = "", year: str = "") -> list:
    try:
        query = supabase.table("parts").select("*, suppliers(name, phone, location)")
        keywords = [w for w in f"{part_name} {car_model} {year}".split() if len(w) > 1]
        if not keywords:
            return []

        res = query.ilike("part_name", f"%{keywords[0]}%").limit(10).execute()
        items = res.data or []
        if not items and car_model:
            res = supabase.table("parts").select("*, suppliers(name, phone, location)").ilike("car_model", f"%{car_model}%").limit(10).execute()
            items = res.data or []
        return items[:5]
    except Exception as e:
        print("Supabase Search Error:", e)
        return []


# --- البحث المباشر في حراج ---
def search_haraj(query_text: str) -> list:
    clean_q = normalize_arabic_numbers(query_text).strip()
    url = "https://graphql.haraj.com.sa/"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Origin": "https://haraj.com.sa",
        "Referer": "https://haraj.com.sa/",
    }
    graphql_payload = {
        "query": """
        query fetchAds($search: String, $page: Int) {
            posts(search: $search, page: $page) {
                items {
                    id
                    title
                    price { formattedPrice }
                    city
                    authorUsername
                    URL
                }
            }
        }
        """,
        "variables": {"search": clean_q, "page": 1},
    }
    try:
        r = requests.post(url, json=graphql_payload, headers=headers, timeout=12)
        if r.status_code == 200:
            data = r.json()
            items = data.get("data", {}).get("posts", {}).get("items", [])
            # إعطاء أولوية لإعلانات جيزان ثم بقية المناطق
            jazan_items = [i for i in items if "جازان" in (i.get("city") or "") or "جيزان" in (i.get("city") or "")]
            other_items = [i for i in items if i not in jazan_items]
            return (jazan_items + other_items)[:5]
    except Exception as e:
        print("Haraj Search Error:", e)
    return []


# --- نقاط الاتصال (Endpoints) ---
@app.get("/")
def home():
    return {"status": "Jazan Tashlih Bot is running", "engine": "evolution" if USE_EVOLUTION else "green-api"}


@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    chat_id = ""
    text = ""
    image_url = ""

    # 1. تحليل رسائل Evolution API
    if "event" in body and body.get("event") in ["messages.upsert", "MESSAGES_UPSERT"]:
        data = body.get("data", {})
        key = data.get("key", {})
        if key.get("fromMe"):
            return {"ok": True}
        chat_id = key.get("remoteJid", "")
        if "@g.us" in chat_id:
            return {"ok": True}
        msg = data.get("message", {})
        text = (
            msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("imageMessage", {}).get("caption")
            or ""
        )

    # 2. تحليل رسائل Green-API
    elif body.get("typeWebhook") == "incomingMessageReceived":
        sender_data = body.get("senderData", {})
        chat_id = sender_data.get("chatId", "")
        if "@g.us" in chat_id:
            return {"ok": True}
        msg_data = body.get("messageData", {})
        msg_type = msg_data.get("typeMessage", "")
        if msg_type == "textMessage":
            text = msg_data.get("textMessageData", {}).get("textMessage", "")
        elif msg_type == "extendedTextMessage":
            text = msg_data.get("extendedTextMessageData", {}).get("text", "")
        elif msg_type == "imageMessage":
            file_data = msg_data.get("fileMessageData", {})
            text = file_data.get("caption", "")
            image_url = file_data.get("downloadUrl", "")

    if not chat_id or (not text and not image_url):
        return {"ok": True}

    clean_text = normalize_arabic_numbers(text.strip())

    # --- معالجة إضافة قطعة بالصورة من أصحاب التشاليح ---
    if image_url and clean_text:
        try:
            img_bytes = requests.get(image_url, timeout=15).content
            prompt_img = f"""
استخرج بيانات قطعة الغيار من الصورة والوصف وأرجع JSON فقط:
{{"part_name": "", "car_model": "", "year": "", "price": ""}}
الوصف: {clean_text}
"""
            parsed_img = call_gemini_safe([
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                prompt_img,
            ])
            phone = chat_id.split("@")[0]
            sup = supabase.table("suppliers").select("id, name").eq("phone", phone).execute()
            supplier_id = sup.data[0]["id"] if sup.data else None

            supabase.table("parts").insert({
                "supplier_id": supplier_id,
                "part_name": parsed_img.get("part_name") or clean_text,
                "car_model": parsed_img.get("car_model", ""),
                "year": parsed_img.get("year", ""),
                "price": parsed_img.get("price", "على السوم"),
                "image_url": image_url,
            }).execute()

            send_whatsapp_text(
                chat_id,
                f"✅ تم إضافة القطعة بنجاح في سوق تشاليح جيزان:\n"
                f"🔧 القطعة: {parsed_img.get('part_name') or clean_text}\n"
                f"🚗 السيارة: {parsed_img.get('car_model', '-')} {parsed_img.get('year', '')}\n"
                f"💰 السعر: {parsed_img.get('price', 'على السوم')}",
            )
            return {"ok": True}
        except Exception as e:
            print("Image Insert Error:", e)

    # --- تسجيل تشليح جديد ---
    if clean_text.startswith("تسجيل تشليح"):
        parts_txt = clean_text.replace("تسجيل تشليح", "").strip().split()
        sup_name = parts_txt[0] if len(parts_txt) >= 1 else "تشليح جيزان"
        sup_loc = " ".join(parts_txt[1:]) if len(parts_txt) >= 2 else "صناعية جيزان"
        phone = chat_id.split("@")[0]
        try:
            supabase.table("suppliers").upsert({
                "phone": phone,
                "name": sup_name,
                "location": sup_loc,
            }).execute()
            send_whatsapp_text(
                chat_id,
                f"✅ تم تسجيل تشليحكم بنجاح!\n"
                f"🏪 الاسم: {sup_name}\n"
                f"📍 الموقع: {sup_loc}\n\n"
                f"الآن يمكنك إرسال صورة أي قطعة مع كتابة اسمها والسيارة والسعر في الوصف لإضافتها فوراً.",
            )
        except Exception as e:
            print("Supplier Register Error:", e)
            send_whatsapp_text(chat_id, "⚠️ حدث خطأ أثناء التسجيل، تأكد من الصيغة: تسجيل تشليح [الاسم] [الموقع]")
        return {"ok": True}

    # --- تحليل رسالة العميل ---
    analysis = analyze_user_message(clean_text)
    intent = analysis.get("intent", "search")

    if intent == "greeting":
        welcome = (
            "أهلاً بك في *سوق تشاليح جيزان الذكي* 🚗🔧\n\n"
            "🔹 *للباحثين عن قطع غيار:*\n"
            "أرسل اسم القطعة والسيارة والموديل مباشرة (مثال: *ابغى صدام أمامي كامري 2020*).\n\n"
            "🔹 *لأصحاب التشاليح والموردين:*\n"
            "1️⃣ لتسجيل تشليحك أرسل:\n"
            "تسجيل تشليح [اسم التشليح] [الموقع]\n"
            "2️⃣ لإضافة قطعة: أرسل *صورة القطعة* واكتب معها اسمها والسيارة والسعر."
        )
        send_whatsapp_text(chat_id, welcome)
        return {"ok": True}

    if intent == "busy":
        send_whatsapp_text(chat_id, "⏳ النظام عليه ضغط بسيط حالياً، أعد إرسال طلبك بعد لحظات.")
        return {"ok": True}

    # --- تنفيذ البحث المزدوج (تشاليح جيزان + حراج) ---
    part_name = analysis.get("part_name") or clean_text
    car_model = analysis.get("car_model", "")
    year = analysis.get("year", "")
    search_phrase = " ".join([part_name, car_model, year]).strip()

    local_results = search_local_supabase(part_name, car_model, year)
    haraj_results = search_haraj(search_phrase)

    if not local_results and not haraj_results:
        haraj_link = f"https://haraj.com.sa/search/{requests.utils.quote(search_phrase)}"
        send_whatsapp_text(
            chat_id,
            f"🔍 بحثت عن: *{search_phrase}*\n\n"
            f"لم أجد نتائج مطابقة حالياً في القائمة المسجلة، يمكنك تصفح أحدث العروض مباشرة عبر الرابط:\n{haraj_link}",
        )
        return {"ok": True}

    reply_lines = [f"🔎 *نتائج البحث عن:* {search_phrase}\n"]

    if local_results:
        reply_lines.append("🏪 *المتوفر في تشاليح جيزان المسجلة:*")
        for idx, item in enumerate(local_results, 1):
            sup = item.get("suppliers") or {}
            sup_name = sup.get("name", "تشليح جيزان")
            sup_phone = sup.get("phone", "")
            sup_loc = sup.get("location", "جيزان")
            price = item.get("price", "على السوم")
            reply_lines.append(
                f"{idx}. *{item.get('part_name', '')}* ({item.get('car_model', '')} {item.get('year', '')})\n"
                f"   💰 السعر: {price} | 📍 {sup_name} ({sup_loc})\n"
                f"   📞 للتواصل: +{sup_phone}"
            )
        reply_lines.append("")

    if haraj_results:
        reply_lines.append("🌐 *أحدث العروض في حراج:*")
        for idx, ad in enumerate(haraj_results, 1):
            title = ad.get("title", "")
            city = ad.get("city", "السعودية")
            price = (ad.get("price") or {}).get("formattedPrice") or "على السوم"
            ad_url = ad.get("URL") or f"https://haraj.com.sa/11{ad.get('id', '')}"
            reply_lines.append(
                f"{idx}. *{title}*\n"
                f"   📍 المدينة: {city} | 💰 {price}\n"
                f"   🔗 {ad_url}"
            )

    send_whatsapp_text(chat_id, "\n".join(reply_lines))
    return {"ok": True}
