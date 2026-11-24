import os, json, uuid, time, requests
from fastapi import FastAPI, Request, BackgroundTasks
from supabase import create_client, Client
from google import genai
from google.genai import types

app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
GREEN_ID = "710722812642"
GREEN_TOKEN = os.getenv("GREEN_TOKEN", "")
GREEN_API_URL = f"https://7107.api.greenapi.com/waInstance{GREEN_ID}"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def send_whatsapp_text(chat_id: str, text: str):
    requests.post(f"{GREEN_API_URL}/sendMessage/{GREEN_TOKEN}", json={"chatId": chat_id, "message": text}, timeout=10)

def send_whatsapp_image(chat_id: str, image_url: str, caption: str):
    requests.post(
        f"{GREEN_API_URL}/sendFileByUrl/{GREEN_TOKEN}",
        json={"chatId": chat_id, "urlFile": image_url, "fileName": "part.jpg", "caption": caption},
        timeout=15
    )

def call_gemini_safe(contents) -> dict:
    for attempt in range(2):
        try:
            res = ai_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            return json.loads(res.text)
        except Exception:
            if attempt == 0:
                time.sleep(1)
                continue
            return {"intent": "busy"}

def analyze_user_message(text: str) -> dict:
    clean = text.strip()
    if any(w in clean for w in ["مرحبا", "مرحباً", "السلام", "هلا", "اهلا", "أهلا"]):
        return {"intent": "greeting"}
    if clean.startswith("تسجيل"):
        parts = clean.replace("تسجيل تشليح", "").replace("تسجيل", "").strip()
        return {"intent": "register_yard", "yard_name": parts or "تشليح جيزان", "location": "صناعية جيزان"}

    prompt = f'حلل رسالة سوق تشاليح جيزان وأخرج JSON فقط: "{text}" -> {{"intent":"greeting|register_yard|search_part|other","yard_name":"","location":"","part_name":"","car_make":"","car_model":""}}'
    return call_gemini_safe(prompt)

def analyze_part_image(image_bytes: bytes, caption: str) -> dict:
    prompt = f'استخرج بيانات قطعة الغيار من الصورة والوصف "{caption}" كـ JSON فقط: {{"part_name":"","car_make":"","car_model":"","condition":"مستعملة","price":0}}'
    res = call_gemini_safe([types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt])
    if res.get("intent") == "busy":
        return {"part_name": caption or "قطعة غيار", "car_make": "", "car_model": "", "condition": "مستعملة", "price": 0}
    return res

def search_external_web(query: str) -> str:
    if not SERPER_API_KEY or SERPER_API_KEY == "none":
        return ""
    res = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": f"{query} تشليح السعودية حراج", "gl": "sa", "hl": "ar", "num": 3},
        timeout=8
    )
    if res.status_code == 200:
        return "\n".join([f"- {r.get('title')}: {r.get('link')}" for r in res.json().get("organic", [])])
    return ""

def process_incoming_message(data: dict):
    chat_id = data.get("senderData", {}).get("chatId", "")
    phone = chat_id.replace("@c.us", "")
    msg_data = data.get("messageData", {})
    msg_type = msg_data.get("typeMessage", "")

    if msg_type in ["textMessage", "extendedTextMessage"]:
        text = (msg_data.get("textMessageData", {}).get("textMessage") or msg_data.get("extendedTextMessageData", {}).get("text") or "").strip()
        analysis = analyze_user_message(text)
        intent = analysis.get("intent")

        if intent == "busy":
            send_whatsapp_text(chat_id, "⏳ النظام عليه ضغط بسيط حالياً، أعد إرسال طلبك بعد لحظات.")
            return

        if intent == "greeting":
            send_whatsapp_text(chat_id, "أهلاً بك في *سوق تشاليح جيزان الذكي* 🚗🔧\n\n🔹 *للباحثين عن قطع غيار:*\nأرسل اسم القطعة والسيارة والموديل مباشرة (مثال: _ابغى صدام أمامي كامري 2020_).\n\n🔹 *لأصحاب التشاليح والموردين:*\n1️⃣ لتسجيل تشليحك أرسل: _تسجيل تشليح [اسم التشليح] [الموقع]_\n2️⃣ لإضافة قطعة: أرسل *صورة القطعة* واكتب معها اسمها والسيارة والسعر.")

        elif intent == "register_yard":
            yard_name = analysis.get("yard_name") or text.replace("تسجيل", "").strip()
            location = analysis.get("location") or "صناعية جيزان"
            supabase.table("yards").upsert({"phone": phone, "name": yard_name, "location": location}, on_conflict="phone").execute()
            send_whatsapp_text(chat_id, f"✅ تم تسجيل *{yard_name}* ({location}) بنجاح!\nيمكنك الآن إرسال صور القطع مع الوصف والسعر لعرضها للمشترين فوراً.")

        elif intent == "search_part":
            part_name, car_make, car_model = analysis.get("part_name", ""), analysis.get("car_make", ""), analysis.get("car_model", "")
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
                    price_txt = f"{item['price']} ريال" if item.get("price") else "على السوم"
                    caption = f"🔧 *{item.get('part_name')} - {item.get('car_make')} {item.get('car_model')}*\n💰 السعر: *{price_txt}*\n📋 الحالة: {item.get('condition', 'جيدة')}\n🏪 التشليح: {yard.get('name', 'تشليح جيزان')} ({yard.get('location', 'صناعية جيزان')})\n📞 للتواصل واتساب: https://wa.me/{yard.get('phone', '')}"
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
            send_whatsapp_text(chat_id, "اكتب *مرحبا* لعرض القائمة، أو اكتب اسم القطعة وسيارتك للبحث عنها مباشرة 🚗")

    elif msg_type == "imageMessage":
        file_data = message_data.get("fileMessageData", {})
        yard_res = supabase.table("yards").select("*").eq("phone", phone).execute().data
        if not yard_res:
            send_whatsapp_text(chat_id, "⚠️ رقمك غير مسجل كتشليح بعد.\nأرسل أولاً: *تسجيل تشليح [اسم تشليحك] صناعية جيزان*")
            return

        img_bytes = requests.get(file_data.get("downloadUrl", ""), timeout=15).content
        file_name = f"{uuid.uuid4()}.jpg"
        supabase.storage.from_("parts-images").upload(file_name, img_bytes, {"content-type": "image/jpeg"})
        public_url = supabase.storage.from_("parts-images").get_public_url(file_name)

        caption = file_data.get("caption", "")
        part_info = analyze_part_image(img_bytes, caption)
        supabase.table("parts").insert({
            "yard_id": yard_res[0]["id"],
            "part_name": part_info.get("part_name", caption),
            "car_make": part_info.get("car_make", ""),
            "car_model": part_info.get("car_model", ""),
            "condition": part_info.get("condition", "مستعملة"),
            "price": part_info.get("price", 0),
            "image_url": public_url
        }).execute()

        send_whatsapp_text(chat_id, f"✅ تم نشر القطعة بنجاح!\n🔧 القطعة: *{part_info.get('part_name')}*\n🚗 السيارة: {part_info.get('car_make')} {part_info.get('car_model')}\n💰 السعر: {part_info.get('price')} ريال")

@app.get("/")
@app.head("/")
def home():
    return {"status": "Jazan Tashlih Bot is Live!"}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    if data.get("typeWebhook") == "incomingMessageReceived":
        background_tasks.add_task(process_incoming_message, data)
    return {"status": "received"}
