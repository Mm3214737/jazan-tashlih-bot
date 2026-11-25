import os
import json
import time
import requests
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from supabase import create_client, Client

app = FastAPI()

# --- اكتشاف مفاتيح البيئة تلقائياً مهما كان اسمها ---
def get_env_by_prefix(prefix: str, exclude: str = "") -> str:
    for k, v in os.environ.items():
        if k.startswith(prefix) and (not exclude or exclude not in k) and v:
            return v.strip()
    return ""

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or get_env_by_prefix("GEMI")
SUPABASE_URL = os.environ.get("SUPABASE_URL") or next((v for k, v in os.environ.items() if k.startswith("SUPA") and v.startswith("http")), "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or next((v for k, v in os.environ.items() if k.startswith("SUPA") and not v.startswith("http")), "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY") or get_env_by_prefix("SERP")

# إعدادات Green-API (تدعم الرابط الكامل أو الـ ID والتوكن المنفصلين)
GREEN_RAW = get_env_by_prefix("GREE")
GREEN_ID_INSTANCE = os.environ.get("GREEN_ID_INSTANCE", "")
GREEN_API_TOKEN = os.environ.get("GREEN_API_TOKEN", "")

# إعدادات Evolution API (للانتقال بعد ربط الباركود)
EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "jazan2026")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "jazan")
USE_EVOLUTION = os.environ.get("USE_EVOLUTION", "false").lower() == "true"

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def normalize_arabic_numbers(text: str) -> str:
    return text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def build_green_send_url(method: str = "sendMessage") -> str:
    # إذا كان المتغير رابطاً كاملاً مثل https://.../waInstanceXXXX/sendMessage/TOKEN
    if GREEN_RAW.startswith("http"):
        base = GREEN_RAW.rstrip("/")
        if "/sendMessage/" in base:
            return base.replace("/sendMessage/", f"/{method}/")
        if "/waInstance" in base and GREEN_API_TOKEN:
            return f"{base}/{method}/{GREEN_API_TOKEN}"
        return base
    # إذا كان المتغير هو ID_INSTANCE فقط
    if GREEN_ID_INSTANCE and GREEN_API_TOKEN:
        return f"https://api.green-api.com/waInstance{GREEN_ID_INSTANCE}/{method}/{GREEN_API_TOKEN}"
    return ""


def send_whatsapp_text(chat_id: str, message: str):
    if USE_EVOLUTION and EVOLUTION_URL:
        number = chat_id.split("@")[0]
        url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
        try:
            r = requests.post(url, json={"number": number, "text": message}, headers=headers, timeout=15)
            print("Evolution Send Status:", r.status_code)
        except Exception as e:
            print("Evolution Send Error:", e)
        return

    url = build_green_send_url("sendMessage")
    if url:
        try:
            r = requests.post(url, json={"chatId": chat_id, "message": message}, timeout=15)
            print("GreenAPI Send Status:", r.status_code, r.text[:150])
        except Exception as e:
            print("GreenAPI Send Error:", e)
    else:
        print("GreenAPI URL not configured! GREEN_RAW:", GREEN_RAW[:15])


def call_gemini_safe(contents) -> dict:
    models_to_try = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
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
            time.sleep(0.4)

    # خطة بديلة فورية عند ضغط خوادم Gemini
    raw_text = contents if isinstance(contents, str) else ""
    if raw_text:
        clean_query = raw_text.splitlines()[-1].replace("رسالة العميل:", "").strip()
        clean_query = normalize_arabic_numbers(clean_query)
        for prefix in ["ابغى", "أبغى", "ابي", "أبي", "مطلوب", "عندكم", "في", "اريد", "أريد"]:
            if clean_query.startswith(prefix + " "):
                clean_query = clean_query[len(prefix) + 1 :].strip()
        if len(clean_query) >= 3:
            return {"intent": "search", "part_name": clean_query, "car_model": "", "year": ""}
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
  "intent": "search" أو "greeting" أو "other",
  "part_name": "اسم القطعة بدون كلمات زائدة",
  "car_model": "نوع وموديل السيارة مثل كامري او هايلكس",
  "year": "سنة الصنع بالأرقام الإنجليزية إن وجدت مثل 2019"
}}
رسالة العميل: {clean}
"""
    return call_gemini_safe(prompt)


def search_local_supabase(part_name: str, car_model: str = "", year: str = "") -> list:
    try:
        keywords = [w for w in f"{part_name} {car_model} {year}".split() if len(w) > 1]
        if not keywords:
            return []
        res = supabase.table("parts").select("*, suppliers(*)").ilike("part_name", f"%{keywords[0]}%").limit(5).execute()
        items = res.data or []
        if not items and car_model:
            res = supabase.table("parts").select("*, suppliers(*)").ilike("car_model", f"%{car_model}%").limit(5).execute()
            items = res.data or []
        return items
    except Exception as e:
        print("Supabase Search Error:", e)
        return []


def search_haraj(query_text: str) -> list:
    clean_q = normalize_arabic_numbers(query_text).strip()
    results = []

    # 1. البحث عبر SerpApi في موقع حراج (الأكثر موثوقية)
    if SERPAPI_KEY:
        try:
            params = {
                "engine": "google",
                "q": f"site:haraj.com.sa {clean_q} تشليح",
                "api_key": SERPAPI_KEY,
                "hl": "ar",
                "gl": "sa",
                "num": 6,
            }
            r = requests.get("https://serpapi.com/search.json", params=params, timeout=12)
            if r.status_code == 200:
                organic = r.json().get("organic_results", [])
                for item in organic[:5]:
                    results.append({
                        "title": item.get("title", "").replace(" - حراج", ""),
                        "snippet": item.get("snippet", "")[:90],
                        "url": item.get("link", ""),
                    })
                if results:
                    return results
        except Exception as e:
            print("SerpApi Haraj Error:", e)

    # 2. البحث المباشر في GraphQL حراج كبديل
    try:
        url = "https://graphql.haraj.com.sa/"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            "Origin": "https://haraj.com.sa",
        }
        payload = {
            "query": "query fetchAds($search: String) { posts(search: $search, page: 1) { items { id title city price { formattedPrice } URL } } }",
            "variables": {"search": clean_q},
        }
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            items = r.json().get("data", {}).get("posts", {}).get("items", [])
            for ad in items[:5]:
                price = (ad.get("price") or {}).get("formattedPrice") or "على السوم"
                city = ad.get("city") or "السعودية"
                results.append({
                    "title": ad.get("title", ""),
                    "snippet": f"📍 {city} | 💰 {price}",
                    "url": ad.get("URL") or f"https://haraj.com.sa/11{ad.get('id', '')}",
                })
    except Exception as e:
        print("Haraj GraphQL Error:", e)

    return results


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

    # Evolution API
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

    # Green-API
    elif body.get("typeWebhook") == "incomingMessageReceived":
        chat_id = body.get("senderData", {}).get("chatId", "")
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

    # إضافة قطعة بالصورة من أصحاب التشاليح
    if image_url and clean_text:
        try:
            img_bytes = requests.get(image_url, timeout=15).content
            parsed_img = call_gemini_safe([
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                f'استخرج بيانات قطعة الغيار وأرجع JSON فقط: {{"part_name": "", "car_model": "", "year": "", "price": ""}} الوصف: {clean_text}',
            ])
            phone = chat_id.split("@")[0]
            sup = supabase.table("suppliers").select("*").eq("phone", phone).execute()
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

    # تسجيل تشليح جديد
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
                f"✅ تم تسجيل تشليحكم بنجاح!\n🏪 الاسم: {sup_name}\n📍 الموقع: {sup_loc}\n\n"
                f"الآن يمكنك إرسال صورة أي قطعة مع كتابة اسمها والسيارة والسعر في الوصف لإضافتها فوراً.",
            )
        except Exception as e:
            print("Supplier Register Error:", e)
            send_whatsapp_text(chat_id, "⚠️ حدث خطأ أثناء التسجيل، تأكد من الصيغة: تسجيل تشليح [الاسم] [الموقع]")
        return {"ok": True}

    analysis = analyze_user_message(clean_text)
    intent = analysis.get("intent", "search")

    if intent == "greeting":
        send_whatsapp_text(
            chat_id,
            "أهلاً بك في *سوق تشاليح جيزان الذكي* 🚗🔧\n\n"
            "🔹 *للباحثين عن قطع غيار:*\n"
            "أرسل اسم القطعة والسيارة والموديل مباشرة (مثال: *ابغى صدام أمامي كامري 2020*).\n\n"
            "🔹 *لأصحاب التشاليح والموردين:*\n"
            "1️⃣ لتسجيل تشليحك أرسل:\n"
            "تسجيل تشليح [اسم التشليح] [الموقع]\n"
            "2️⃣ لإضافة قطعة: أرسل *صورة القطعة* واكتب معها اسمها والسيارة والسعر.",
        )
        return {"ok": True}

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
            sup_name = sup.get("name") or sup.get("shop_name") or sup.get("supplier_name") or "تشليح جيزان"
            sup_phone = sup.get("phone", "")
            sup_loc = sup.get("location") or sup.get("city") or "جيزان"
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
            reply_lines.append(
                f"{idx}. *{ad.get('title', '')}*\n"
                f"   {ad.get('snippet', '')}\n"
                f"   🔗 {ad.get('url', '')}"
            )

    send_whatsapp_text(chat_id, "\n".join(reply_lines))
    return {"ok": True}
