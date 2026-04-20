import os
import cv2
import time
import json
import logging
import requests
import threading
from datetime import datetime
from telebot import TeleBot, types
from telebot.util import extract_arguments
from flask import Flask

# --- CONFIGURATION ---
BOT_TOKEN    = "8740463465:AAHD0PR7Sk6hMrmR1TvqIVYld-QY-OZvqns"
BOT_USERNAME = "hbbbbbbvvvbot"
OWNER_ID     = 8656150458
SE_USER      = "431285661"
SE_SECRET    = "KDso7QnWFP6ACYCumxkx5EWvXeiRPWAe"

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN)

# --- DATABASE MANAGEMENT ---
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

# --- SIGHTENGINE CORE ---
def analyze_media(file_path):
    """Resim analizi yapan temel fonksiyon."""
    params = {
        'models': 'nudity-2.0,wad,offensive',
        'api_user': SE_USER,
        'api_secret': SE_SECRET
    }
    try:
        with open(file_path, 'rb') as f:
            files = {'media': f}
            r = requests.post('https://api.sightengine.com/1.0/check.json', files=files, data=params)
            output = r.json()
        
        if output.get("status") == "success":
            # NSFW Kontrolü
            nudity = output.get('nudity', {})
            if nudity.get('sexually_explicit', 0) > 0.5 or nudity.get('suggestive', 0) > 0.8:
                return True, "NSFW / Müstehcen İçerik"
            
            # Uyuşturucu/Silah Kontrolü
            wad = output.get('wad', {})
            if wad.get('drugs', 0) > 0.2: # Ücretsiz hak için hassasiyeti optimize ettik
                return True, "Yasaklı Madde / Uyuşturucu"
                
        return False, None
    except Exception as e:
        logger.error(f"API Hatası: {e}")
        return False, None

# --- MEDIA PROCESSING ---
def process_video_logic(file_path, chat_id):
    """Videoyu parçalara böler ve analiz eder."""
    cap = cv2.VideoCapture(file_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0
    
    # 2 Dakika sınırı ve 5 saniyede bir kare
    check_limit = min(duration, 120)
    
    for sec in range(0, int(check_limit), 5):
        cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
        ret, frame = cap.read()
        if ret:
            frame_name = f"check_{chat_id}_{sec}.jpg"
            cv2.imwrite(frame_name, frame)
            is_bad, reason = analyze_media(frame_name)
            os.remove(frame_name)
            
            if is_bad:
                cap.release()
                return True, reason
    cap.release()
    return False, None

def process_sticker_logic(file_path, chat_id):
    """Sticker'ı baş, orta, son olarak 3 karede kontrol eder."""
    cap = cv2.VideoCapture(file_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    check_points = [0, total_frames // 2, total_frames - 1]
    for frame_idx in check_points:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret:
            temp_name = f"stk_{chat_id}_{frame_idx}.jpg"
            cv2.imwrite(temp_name, frame)
            is_bad, reason = analyze_media(temp_name)
            os.remove(temp_name)
            if is_bad:
                cap.release()
                return True, reason
    cap.release()
    return False, None

# --- UI COMPONENTS ---
def main_menu_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Beni Grubuna Ekle", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"),
        types.InlineKeyboardButton("📜 Hakkımızda", callback_data="about_info"),
        types.InlineKeyboardButton("👑 Sistem Sahibi", url=f"tg://user?id={OWNER_ID}")
    )
    return markup

def settings_markup(group_id):
    gid = str(group_id)
    if gid not in db["groups"]:
        db["groups"][gid] = {"nsfw": True, "drugs": True, "warn_limit": 3}
    
    s = db["groups"][gid]
    n_icon = "✅" if s["nsfw"] else "❌"
    d_icon = "✅" if s["drugs"] else "❌"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"🔞 NSFW Koruması: {n_icon}", callback_data=f"toggle_nsfw_{group_id}"),
        types.InlineKeyboardButton(f"💊 Uyuşturucu Filtresi: {d_icon}", callback_data=f"toggle_drugs_{group_id}"),
        types.InlineKeyboardButton(f"⚠️ Limit: {s['warn_limit']} Uyarı", callback_data=f"setlimit_{group_id}"),
        types.InlineKeyboardButton("🗑️ Menüyü Kapat", callback_data="close_menu")
    )
    return markup

# --- HANDLERS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = str(message.from_user.id)
    if uid not in db["users"]:
        db["users"][uid] = {"date": datetime.now().strftime("%d/%m/%Y %H:%M")}
        save_db(db)

    args = extract_arguments(message.text)
    if args and args.startswith("set_"):
        gid = args.replace("set_", "")
        bot.send_message(message.chat.id, f"🛠️ *Grup Yönetim Paneli*\nID: `{gid}`\n\nLütfen yapmak istediğiniz işlemi seçin:", 
                         parse_mode="Markdown", reply_markup=settings_markup(gid))
        return

    if message.chat.type == 'private':
        text = (
            "🛡️ *Father NSFW Delete Bot* \n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Merhaba değerli kullanıcı,\n\n"
            "Ben, grubunuzu yapay zeka desteğiyle 7/24 koruyan gelişmiş bir güvenlik botuyum. "
            "Müstehcen içerikleri, yasaklı maddeleri ve zararlı medyaları anında tespit ederek imha ederim.\n\n"
            "📍 *Özelliklerim:*\n"
            "├ Videoları 2 dakikaya kadar kare kare tarama\n"
            "├ Hareketli sticker analizi\n"
            "├ Fotoğraf ve belge (medya) denetimi\n"
            "└ Profesyonel yönetim paneli\n\n"
            "✨ _Hizmet kalitemizi artırmak için beni grubunuza ekleyin!_"
        )
        bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=main_menu_markup())
    else:
        # Gruba eklenince karşılama
        bot.send_message(message.chat.id, "✅ *Father NSFW Aktif!*\nBeni eklediğiniz için teşekkürler. Ayarlar için /settings yazabilir veya aşağıdaki butona basabilirsiniz.", 
                         parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⚙️ Ayarlar", callback_data=f"manage_{message.chat.id}")))

@bot.message_handler(commands=['settings'])
def settings_cmd(message):
    if message.chat.type == 'private': return
    member = bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ['creator', 'administrator']:
        bot.reply_to(message, "❌ *Yetki Reddedildi!* Bu komut sadece yöneticiler içindir.")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📩 Özel Mesajda Aç", url=f"https://t.me/{BOT_USERNAME}?start=set_{message.chat.id}"))
    markup.add(types.InlineKeyboardButton("📍 Burada Aç", callback_data=f"here_{message.chat.id}"))
    bot.send_message(message.chat.id, "⚙️ *Panel Erişim Seçeneği:*", parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    uid = call.from_user.id
    data = call.data

    if data.startswith("here_"):
        gid = data.split("_")[1]
        bot.edit_message_text("🛠️ *Grup Ayarları*", call.message.chat.id, call.message.message_id, reply_markup=settings_markup(gid))
    
    elif data.startswith("toggle_"):
        _, target, gid = data.split("_")
        if str(gid) in db["groups"]:
            db["groups"][str(gid)][target] = not db["groups"][str(gid)].get(target, True)
            save_db(db)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=settings_markup(gid))
    
    elif data == "about_info":
        bot.answer_callback_query(call.id)
        bot.edit_message_text("🔐 *Hizmet Politikası ve Güvenlik*\n\nSistemimiz SightEngine AI altyapısını kullanarak verileri işler. Hiçbir medya sunucularımızda depolanmaz, analiz sonrası anında imha edilir.", 
                             call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=main_menu_markup())
    
    elif data == "close_menu":
        bot.delete_message(call.message.chat.id, call.message.message_id)

# --- UNIFIED MEDIA HANDLER ---
@bot.message_handler(content_types=['photo', 'video', 'video_note', 'sticker', 'document'])
def handle_all_media(message):
    if message.chat.type == 'private': return
    
    gid = str(message.chat.id)
    # Eğer grup kaydı yoksa varsayılan ayarları oluştur
    if gid not in db["groups"]:
        db["groups"][gid] = {"nsfw": True, "drugs": True, "warn_limit": 3}
        save_db(db)

    file_id = None
    m_type = None # 'img' or 'vid'

    # Medya Tipini Belirle
    if message.photo:
        file_id = message.photo[-1].file_id
        m_type = 'img'
    elif message.video:
        file_id = message.video.file_id
        m_type = 'vid'
    elif message.video_note:
        file_id = message.video_note.file_id
        m_type = 'vid'
    elif message.sticker and message.sticker.is_video:
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
    
    if not file_id: return

    def run_analysis():
        try:
            f_info = bot.get_file(file_id)
            d_file = bot.download_file(f_info.file_path)
            ext = f_info.file_path.split('.')[-1]
            temp_path = f"analiz_{message.message_id}.{ext}"
            
            with open(temp_path, 'wb') as f:
                f.write(d_file)

            bad = False
            reason = ""

            if m_type == 'img':
                bad, reason = analyze_media(temp_path)
            elif m_type == 'vid':
                # Önce Thumbnail (İlk kare) kontrolü - HIZLI KONTROL
                bad, reason = analyze_media(temp_path) 
                if not bad: # Eğer ilk kare temizse videonun içine gir
                    bad, reason = process_video_logic(temp_path, message.chat.id)
            elif m_type == 'stk':
                bad, reason = process_sticker_logic(temp_path, message.chat.id)

            if bad:
                bot.delete_message(message.chat.id, message.message_id)
                db["stats"]["total_deleted"] += 1
                save_db(db)
                
                # Profesyonel Uyarı Metni
                alert = (
                    "⚠️ *Zararlı İçerik Tespit Edildi!*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 *Gönderen:* {message.from_user.first_name}\n"
                    f"🚫 *İhlal:* `{reason}`\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "🛡️ _Father NSFW Güvenlik Sistemi Tarafından İmha Edildi._"
                )
                bot.send_message(message.chat.id, alert, parse_mode="Markdown")

            if os.path.exists(temp_path): os.remove(temp_path)
        except Exception as e:
            logger.error(f"Genel Analiz Hatası: {e}")

    threading.Thread(target=run_analysis).start()

# --- ADMIN PANEL ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != OWNER_ID: return
    t_u = len(db["users"])
    t_g = len(db["groups"])
    t_d = db["stats"]["total_deleted"]
    
    text = (
        "👑 *Father NSFW Admin Panel*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Kullanıcı: `{t_u}`\n"
        f"🌐 Gruplar: `{t_g}`\n"
        f"🗑️ İmha Edilen: `{t_d}`\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Global Duyuru", callback_data="admin_bc"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

# --- FLASK FOR RENDER ---
app = Flask('')
@app.route('/')
def home(): return "<h1>Father NSFW Bot Active</h1>"

def run_server():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    logger.info("Bot Start...")
    threading.Thread(target=run_server).start()
    bot.infinity_polling()