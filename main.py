import os
import json
import requests
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from supabase import create_client, Client

app = FastAPI()

# قراءة المفاتيح بمرونة
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or next((v for k, v in os.environ.items() if k.startswith("GEMI") and v), "")
SUPABASE_URL = os.environ.get("SUPABASE_URL") or next((v for k, v in os.environ.items() if k.startswith("SUPA") and v.startswith("http")), "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or next((v for k, v in os.environ.items() if k.startswith("SUPA") and not v.startswith("http")), "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY") or next((v for k, v in os.environ.items() if k.startswith("SERP") and v), "")

# رابط الإرسال المباشر (Green-API أو Evolution API)
WHATSAPP_SEND_URL = os.environ.get("WHATSAPP_SEND_URL", "").strip()
EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "jazan2026")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "jazan")
USE_EVOLUTION = os.environ.get("USE_EVOLUTION", "false").lower() == "true"

ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def clean_arabic(text: str) -> str:
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).strip()
    for word in ["ابغى", "أبغى", "ابي", "أبي", "مطلوب", "عندكم", "في", "اريد", "أريد", "بكم", "كم سعر"]:
        if text.startswith(word + " "):
            text = text[len(word) + 1:].strip()
    return text


def send_whatsapp(chat_id: str, message: str):
    if USE_EVOLUTION and EVOLUTION_URL:
        number = chat_id.split("@")[0]
        url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
        requests.post(url, json={"number": number, "text": message}, headers={"apikey": EVOLUTION_API_KEY}, timeout=15)
        return

    if WHATSAPP_SEND_URL:
        r = requests.post(WHATSAPP_SEND_URL, json={"chatId": chat_id, "message": message}, timeout=15)
        print("GreenAPI Status:", r.status_code, r.text[:120])
    else:
        print("ERROR: WHATSAPP_SEND_URL is empty!")


def search_supabase(query_text: str) -> list:
    try:
        words = [w for w in query_text.split() if len(w) > 1]
        if not words:
            return []
        res = supabase.table("parts").select("*, suppliers(*)").ilike("part_name", f"%{words[0]}%").limit(5).execute()
        return res.data or []
    except Exception as e:
        print("Supabase Error:", e)
        return []


def search_haraj(query_text: str) -> list:
    results = []
    if SERPAPI_KEY:
        try:
            params = {
                "engine": "google",
                "q": f"site:haraj.com.sa {query_text}",
                "api_key": SERPAPI_KEY,
                "hl": "ar",
                "gl": "sa",
                "num": 5,
            }
            r = requests.get("https://serpapi.com/search.json", params=params, timeout=12)
            if r.status_code == 200:
                for item in r.json().get("organic_results", [])[:5]:
                    results.append({
                        "title": item.get("title", "").replace(" - حراج", ""),
                        "info": item.get("snippet", "")[:85],
                        "url": item.get("link", ""),
                    })
                if results:
                    return results
        except Exception as e:
            print("SerpApi Error:", e)

    # بديل مباشر من حراج إذا لم يتوفر SerpApi
    try:
        r = requests.post(
            "https://graphql.haraj.com.sa/",
            json={
                "query": "query($s:String){posts(search:$s,page:1){items{id title city price{formattedPrice} URL}}}",
                "variables": {"s": query_text},
            },
            headers={"Content-Type": "application/json", "Origin": "https://haraj.com.sa"},
            timeout=10,
        )
        if r.status_code == 200:
            for ad in r.json().get("data", {}).get("posts", {}).get("items", [])[:5]:
                price = (ad.get("price") or {}).get("formattedPrice") or "على السوم"
                results.append({
                    "title": ad.get("title", ""),
                    "info": f"📍 {ad.get('city', 'السعودية')} | 💰 {price}",
                    "url": ad.get("URL") or f"https://haraj.com.sa/11{ad.get('id', '')}",
                })
    except Exception as e:
        print("Haraj Direct Error:", e)
    return results


@app.get("/")
def home():
    return {"status": "Live"}


@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    chat_id, text, image_url = "", "", ""

    # Evolution API
    if body.get("event") in ["messages.upsert", "MESSAGES_UPSERT"]:
        data = body.get("data", {})
        key = data.get("key", {})
        if key.get("fromMe"):
            return {"ok": True}
        chat_id = key.get("remoteJid", "")
        msg = data.get("message", {})
        text = msg.get("conversation") or msg.get("extendedTextMessage", {}).get("text") or msg.get("imageMessage", {}).get("caption") or ""

    # Green-API
    elif body.get("typeWebhook") == "incomingMessageReceived":
        chat_id = body.get("senderData", {}).get("chatId", "")
        msg_data = body.get("messageData", {})
        t = msg_data.get("typeMessage", "")
        if t == "textMessage":
            text = msg_data.get("textMessageData", {}).get("textMessage", "")
        elif t == "extendedTextMessage":
            text = msg_data.get("extendedTextMessageData", {}).get("text", "")
        elif t == "imageMessage":
            text = msg_data.get("fileMessageData", {}).get("caption", "")
            image_url = msg_data.get("fileMessageData", {}).get("downloadUrl", "")

    if not chat_id or "@g.us" in chat_id or (not text and not image_url):
        return {"ok": True}

    query = clean_arabic(text)

    # الترحيب
    greetings = ["مرحبا", "السلام", "هلا", "اهلا", "أهلا", "مساء", "صباح", "سلام", "hi", "hello"]
    if len(query) <= 18 and any(w in query.lower() for w in greetings):
        send_whatsapp(
            chat_id,
            "أهلاً بك في *سوق تشاليح جيزان الذكي* 🚗🔧\n\n"
            "أرسل اسم القطعة والسيارة والموديل مباشرة للبحث الفوري في تشاليح جيزان وحراج (مثال: *صدام كامري 2019*).\n\n"
            "📌 *لأصحاب التشاليح:*\n"
            "• للتسجيل: أرسل *تسجيل تشليح [الاسم] [الموقع]*\n"
            "• لإضافة قطعة: أرسل *صورة القطعة* واكتب معها اسمها والسيارة والسعر.",
        )
        return {"ok": True}

    # تسجيل تشليح
    if query.startswith("تسجيل تشليح"):
        parts_txt = query.replace("تسجيل تشليح", "").strip().split()
        sup_name = parts_txt[0] if parts_txt else "تشليح جيزان"
        sup_loc = " ".join(parts_txt[1:]) if len(parts_txt) > 1 else "صناعية جيزان"
        phone = chat_id.split("@")[0]
        try:
            supabase.table("suppliers").upsert({"phone": phone, "name": sup_name, "location": sup_loc}).execute()
            send_whatsapp(chat_id, f"✅ تم تسجيل *{sup_name}* ({sup_loc}) بنجاح! أرسل صور القطع مع الوصف لإضافتها.")
        except Exception as e:
            print("Register Error:", e)
            send_whatsapp(chat_id, "⚠️ تعذر التسجيل حالياً.")
        return {"ok": True}

    # إضافة قطعة بالصورة
    if image_url and query:
        try:
            phone = chat_id.split("@")[0]
            sup = supabase.table("suppliers").select("id").eq("phone", phone).execute()
            sup_id = sup.data[0]["id"] if sup.data else None
            supabase.table("parts").insert({
                "supplier_id": sup_id,
                "part_name": query,
                "price": "على السوم",
                "image_url": image_url,
            }).execute()
            send_whatsapp(chat_id, f"✅ تم إضافة القطعة بنجاح: *{query}*")
        except Exception as e:
            print("Insert Part Error:", e)
        return {"ok": True}

    # البحث الفوري (بدون انتظار Gemini حتى لا يتعطل أبداً)
    local_items = search_supabase(query)
    haraj_items = search_haraj(query)

    if not local_items and not haraj_items:
        link = f"https://haraj.com.sa/search/{requests.utils.quote(query)}"
        send_whatsapp(chat_id, f"🔍 لم أجد نتائج مطابقة لـ *{query}* حالياً.\nتصفح حراج مباشرة:\n{link}")
        return {"ok": True}

    lines = [f"🔎 *نتائج البحث عن:* {query}\n"]

    if local_items:
        lines.append("🏪 *المتوفر في تشاليح جيزان:*")
        for i, item in enumerate(local_items, 1):
            sup = item.get("suppliers") or {}
            s_name = sup.get("name") or sup.get("shop_name") or "تشليح جيزان"
            s_phone = sup.get("phone", "")
            lines.append(f"{i}. *{item.get('part_name', '')}* — 💰 {item.get('price', 'على السوم')}\n   📍 {s_name} | 📞 +{s_phone}")
        lines.append("")

    if haraj_items:
        lines.append("🌐 *أحدث العروض في حراج:*")
        for i, ad in enumerate(haraj_items, 1):
            lines.append(f"{i}. *{ad['title']}*\n   {ad['info']}\n   🔗 {ad['url']}")

    send_whatsapp(chat_id, "\n".join(lines))
    return {"ok": True}
