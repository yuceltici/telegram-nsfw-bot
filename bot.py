import os
import cv2
import json
import logging
import requests
import threading
from datetime import datetime
from telebot import TeleBot, types
from telebot.util import extract_arguments
from flask import Flask

# --- YAPILANDIRMA ---
BOT_TOKEN    = "8740463465:AAHD0PR7Sk6hMrmR1TvqIVYld-QY-OZvqns"
BOT_USERNAME = "hbbbbbbvvvbot"
OWNER_ID     = 8656150458
SE_USER      = "431285661"
SE_SECRET    = "KDso7QnWFP6ACYCumxkx5EWvXeiRPWAe"

# --- LOGLAMA ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN)

# --- VERİTABANI ---
DB_FILE = "database.json"

def load_db():
    if not os.path.exists(DB_FILE):
        return {"users": {}, "groups": {}, "stats": {"total_deleted": 0}}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {"users": {}, "groups": {}, "stats": {"total_deleted": 0}}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

db = load_db()

# --- SIGHTENGINE GELİŞMİŞ ANALİZ ---
def analyze_media_detailed(file_path, media_info="Resim"):
    """Tüm kategorileri (Nudity, WAD, Offensive) kontrol eder ve detaylı rapor döner."""
    params = {
        'models': 'nudity-2.0,wad,offensive',
        'api_user': SE_USER,
        'api_secret': SE_SECRET
    }
    try:
        with open(file_path, 'rb') as f:
            files = {'media': f}
            r = requests.post('https://api.sightengine.com/1.0/check.json', files=files, data=params)
            res = r.json()
        
        if res.get("status") != "success":
            return False, None, res

        # Kategorik Kontroller
        nudity = res.get('nudity', {})
        wad = res.get('wad', {})
        offensive = res.get('offensive', {})

        findings = []
        is_bad = False
        
        # 1. Cinsellik (Nudity)
        if nudity.get('sexually_explicit', 0) > 0.5:
            findings.append(f"🔞 Cinsellik ({nudity['sexually_explicit']:.2f})")
            is_bad = True
        elif nudity.get('suggestive', 0) > 0.8:
            findings.append(f"🫦 Müstehcen ({nudity['suggestive']:.2f})")
            is_bad = True

        # 2. Silah, Alkol, Uyuşturucu (WAD)
        if wad.get('drugs', 0) > 0.1:
            findings.append(f"💊 Uyuşturucu ({wad['drugs']:.2f})")
            is_bad = True
        if wad.get('weapons', 0) > 0.2:
            findings.append(f"🔫 Silah ({wad['weapons']:.2f})")
            is_bad = True

        # 3. Şiddet & Rahatsız Edici (Offensive)
        # Not: Bazı modellerde 'offensive' içinde 'violence' veya benzeri alt dallar olabilir
        # SightEngine güncel modeline göre 'offensive' objesini kontrol ediyoruz.
        if offensive.get('prob', 0) > 0.7:
            findings.append(f"👊 Şiddet/Ofansif ({offensive['prob']:.2f})")
            is_bad = True

        reason = ", ".join(findings) if findings else None
        return is_bad, reason, res

    except Exception as e:
        logger.error(f"API Hatası: {e}")
        return False, None, {"error": str(e)}

# --- SAHİBE RAPOR GÖNDER ---
def send_owner_report(user_info, group_info, media_type, status, reason, full_res):
    """Her kontrolden sonra bot sahibine detaylı teknik log atar."""
    report = (
        "🔍 *TEKNİK ANALİZ RAPORU*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Kullanıcı:* `{user_info}`\n"
        f"🏘️ *Grup:* `{group_info}`\n"
        f"📁 *Tür:* `{media_type}`\n"
        f"📊 *Durum:* {'❌ İHLAL' if status else '✅ TEMİZ'}\n"
        f"📝 *Bulgu:* `{reason if reason else '-'}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 *API Response:* \n`{json.dumps(full_res, indent=2)[:800]}...`"
    )
    try:
        bot.send_message(OWNER_ID, report, parse_mode="Markdown")
    except:
        pass

# --- MENÜLER ---
def main_menu():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("➕ Gruba Ekle", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"),
        types.InlineKeyboardButton("ℹ️ Hakkında", callback_data="about"),
        types.InlineKeyboardButton("👑 Sahip", url=f"tg://user?id={OWNER_ID}")
    )
    return m

def settings_categories(gid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("🔞 Cinsellik & NSFW", callback_data=f"cat_nsfw_{gid}"),
        types.InlineKeyboardButton("💊 Madde & Silah", callback_data=f"cat_wad_{gid}"),
        types.InlineKeyboardButton("👊 Şiddet & Ofansif", callback_data=f"cat_off_{gid}"),
        types.InlineKeyboardButton("⚙️ Genel Ayarlar", callback_data=f"cat_gen_{gid}"),
        types.InlineKeyboardButton("❌ Kapat", callback_data="close_menu")
    )
    return m

# --- HANDLERS ---
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    if uid not in db["users"]:
        db["users"][uid] = {"joined": datetime.now().strftime("%Y-%m-%d %H:%M")}
        save_db(db)

    args = extract_arguments(message.text)
    if args and args.startswith("set_"):
        gid = args.replace("set_", "")
        bot.send_message(message.chat.id, f"🛠️ *Yönetim Paneli*\nGrup ID: `{gid}`\n\nKategorileri düzenleyerek koruma seviyesini belirleyin:", 
                         parse_mode="Markdown", reply_markup=settings_categories(gid))
        return

    if message.chat.type == 'private':
        welcome = (
            "🚀 *Father NSFW Delete Bot'a Hoş Geldiniz!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Yapay zeka tabanlı görüntü işleme teknolojisi ile gruplarınızı "
            "en üst düzeyde koruyoruz.\n\n"
            "✅ *Aktif Taramalar:*\n"
            "└ Videolar, Fotoğraflar, Belgeler, Stickerlar\n"
            "└ Cinsellik, Silah, Uyuşturucu ve Şiddet Tespiti\n\n"
            "👇 Aşağıdaki butonları kullanarak sistemi başlatın."
        )
        bot.send_message(message.chat.id, welcome, parse_mode="Markdown", reply_markup=main_menu())

@bot.message_handler(commands=['settings'])
def settings(message):
    if message.chat.type == 'private': return
    member = bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ['creator', 'administrator'] and message.from_user.id != OWNER_ID:
        return
    
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("📩 Özel Mesajda Yönet", url=f"https://t.me/{BOT_USERNAME}?start=set_{message.chat.id}"))
    m.add(types.InlineKeyboardButton("📍 Burada Yönet", callback_data=f"here_{message.chat.id}"))
    bot.send_message(message.chat.id, "🛠️ *Panel Erişim Seçeneği:*", parse_mode="Markdown", reply_markup=m)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    if data.startswith("here_"):
        gid = data.split("_")[1]
        bot.edit_message_text("🛠️ *Kategori Bazlı Ayarlar*", call.message.chat.id, call.message.message_id, reply_markup=settings_categories(gid))
    
    elif data.startswith("cat_"):
        # Kategori detaylarını burada yönetebilirsiniz, şimdilik bilgilendirme veriyoruz
        bot.answer_callback_query(call.id, "Bu kategori için koruma şu an en yüksek seviyede.", show_alert=True)

    elif data == "close_menu":
        bot.delete_message(call.message.chat.id, call.message.message_id)

# --- ANALİZ MOTORU ---
def process_media(message, file_id, m_type):
    user_label = f"{message.from_user.first_name} ({message.from_user.id})"
    group_label = f"{message.chat.title} ({message.chat.id})"
    
    try:
        f_info = bot.get_file(file_id)
        d_file = bot.download_file(f_info.file_path)
        ext = f_info.file_path.split('.')[-1]
        temp_name = f"check_{message.message_id}.{ext}"
        
        with open(temp_name, 'wb') as f:
            f.write(d_file)

        is_bad = False
        reason = ""
        full_res = {}

        if m_type == 'img':
            is_bad, reason, full_res = analyze_media_detailed(temp_name, "Resim")
        
        elif m_type == 'stk':
            # Sticker 3 kare kontrolü
            cap = cv2.VideoCapture(temp_name)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            for frame_idx in [0, total//2, total-1]:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if ret:
                    cv2.imwrite("stk_frame.jpg", frame)
                    is_bad, reason, full_res = analyze_media_detailed("stk_frame.jpg", "Sticker Kare")
                    os.remove("stk_frame.jpg")
                    if is_bad: break
            cap.release()

        elif m_type == 'vid':
            # Video: İlk kare + her 5 saniyede bir
            cap = cv2.VideoCapture(temp_name)
            fps = cap.get(cv2.CAP_PROP_FPS)
            duration = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps) if fps > 0 else 0
            check_limit = min(duration, 120)

            for sec in range(0, check_limit, 5):
                cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
                ret, frame = cap.read()
                if ret:
                    cv2.imwrite("vid_frame.jpg", frame)
                    is_bad, reason, full_res = analyze_media_detailed("vid_frame.jpg", f"Video {sec}.sn")
                    os.remove("vid_frame.jpg")
                    if is_bad: break
            cap.release()

        # Sahibe raporla
        send_owner_report(user_label, group_label, m_type, is_bad, reason, full_res)

        if is_bad:
            bot.delete_message(message.chat.id, message.message_id)
            db["stats"]["total_deleted"] += 1
            save_db(db)
            
            warning = (
                "⚠️ *İmha Edildi: Güvenlik İhlali*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *Kullanıcı:* {message.from_user.first_name}\n"
                f"🚫 *Tespit:* `{reason}`\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🛡️ _Topluluk kuralları gereği içerik silindi._"
            )
            bot.send_message(message.chat.id, warning, parse_mode="Markdown")

        if os.path.exists(temp_name): os.remove(temp_name)

    except Exception as e:
        logger.error(f"İşlem Hatası: {e}")

@bot.message_handler(content_types=['photo', 'video', 'video_note', 'sticker', 'document'])
def filter_media(message):
    if message.chat.type == 'private': return
    
    file_id = None
    m_type = None

    if message.photo:
        file_id = message.photo[-1].file_id
        m_type = 'img'
    elif message.video or message.video_note:
        file_id = message.video.file_id if message.video else message.video_note.file_id
        m_type = 'vid'
    elif message.sticker and (message.sticker.is_video or message.sticker.is_animated):
        file_id = message.sticker.file_id
        m_type = 'stk'
    elif message.document:
        mime = message.document.mime_type or ""
        if "image" in mime:
            file_id = message.document.file_id
            m_type = 'img'
        elif "video" in mime:
            file_id = message.document.file_id
            m_type = 'vid'

    if file_id:
        threading.Thread(target=process_media, args=(message, file_id, m_type)).start()

# --- ADMIN PANEL ---
@bot.message_handler(commands=['admin'])
def admin(message):
    if message.from_user.id != OWNER_ID: return
    text = (
        "👑 *Sistem Yöneticisi Paneli*\n"
        f"📊 Kullanıcı: `{len(db['users'])}` | Grup: `{len(db['groups'])}`\n"
        f"🗑️ Toplam İmha: `{db['stats']['total_deleted']}`\n\n"
        "Loglar anlık olarak özel mesajınıza iletilmektedir."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# --- RENDER WEB SERVER ---
app = Flask('')
@app.route('/')
def home(): return "<h1>Father NSFW Bot Active</h1>"

def run():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    threading.Thread(target=run).start()
    bot.infinity_polling()