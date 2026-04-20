import os
import cv2
import json
import logging
import requests
import threading
import time
from datetime import datetime
from telebot import TeleBot, types, apihelper
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

# --- DATABASE ---
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

# --- SIGHTENGINE ANALİZ MOTORU ---
def analyze_media(file_path, detail_label="Medya"):
    """
    SightEngine API'ye dosyayı gönderir ve detaylı rapor döner.
    Kategoriler: Cinsellik, Silah, Uyuşturucu, Şiddet
    """
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
            return False, f"API Hatası: {res.get('error', {}).get('message', 'Bilinmiyor')}", res

        is_bad = False
        findings = []
        
        # 1. Cinsellik (Nudity)
        nud = res.get('nudity', {})
        if nud.get('sexually_explicit', 0) > 0.5:
            findings.append(f"🔞 Cinsellik ({nud['sexually_explicit']:.2f})")
            is_bad = True
        elif nud.get('suggestive', 0) > 0.7:
            findings.append(f"🔞 Müstehcen ({nud['suggestive']:.2f})")
            is_bad = True

        # 2. Silah & Uyuşturucu (WAD)
        wad = res.get('wad', {})
        if wad.get('drugs', 0) > 0.1:
            findings.append(f"💊 Uyuşturucu ({wad['drugs']:.2f})")
            is_bad = True
        if wad.get('weapons', 0) > 0.1:
            findings.append(f"🔫 Silah ({wad['weapons']:.2f})")
            is_bad = True

        # 3. Şiddet (Offensive)
        off = res.get('offensive', {})
        if off.get('prob', 0) > 0.6:
            findings.append(f"👊 Şiddet/Ofansif ({off['prob']:.2f})")
            is_bad = True

        reason = " | ".join(findings) if findings else "Temiz"
        return is_bad, reason, res

    except Exception as e:
        return False, f"Hata: {str(e)}", {"error": str(e)}

# --- SAHİBE TEKNİK LOG GÖNDERME ---
def log_to_owner(chat_id, user_id, media_type, status, reason, api_res, frame_info=""):
    """Bot sahibine her kontrolde tam teknik detay iletir."""
    status_emoji = "❌ İHLAL" if status else "✅ TEMİZ"
    log_text = (
        f"🛠️ *TEKNİK ANALİZ LOGU*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 *Grup:* `{chat_id}`\n"
        f"👤 *User:* `{user_id}`\n"
        f"📁 *Tür:* `{media_type}` {frame_info}\n"
        f"📊 *Durum:* {status_emoji}\n"
        f"🔎 *Bulgu:* `{reason}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Ham Yanıt:* \n`{json.dumps(api_res, indent=2)[:600]}...`"
    )
    try:
        bot.send_message(OWNER_ID, log_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Sahibe log iletilemedi: {e}")

# --- MENÜLER ---
def main_markup():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("➕ Gruba Ekle", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"),
        types.InlineKeyboardButton("👑 Sahip", url=f"tg://user?id={OWNER_ID}")
    )
    return m

def settings_panel(gid):
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(
        types.InlineKeyboardButton("🔞 Cinsellik Kontrolü", callback_data=f"opt_nsfw_{gid}"),
        types.InlineKeyboardButton("💊 Uyuşturucu & Silah", callback_data=f"opt_wad_{gid}"),
        types.InlineKeyboardButton("👊 Şiddet & Ofansif", callback_data=f"opt_off_{gid}"),
        types.InlineKeyboardButton("🗑️ Paneli Kapat", callback_data="close_menu")
    )
    return m

# --- HANDLERS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    uid = str(message.from_user.id)
    if uid not in db["users"]:
        db["users"][uid] = {"join": datetime.now().strftime("%Y-%m-%d %H:%M")}
        save_db(db)
    
    args = extract_arguments(message.text)
    if args and args.startswith("set_"):
        gid = args.replace("set_", "")
        bot.send_message(message.chat.id, f"⚙️ *Grup Yönetim Paneli*\nID: `{gid}`\n\nKategori bazlı filtreleri özelleştirin:", 
                         parse_mode="Markdown", reply_markup=settings_panel(gid))
        return

    bot.send_message(message.chat.id, "🛡️ *Father NSFW Delete Bot*\n\nProfesyonel grup koruma sistemine hoş geldiniz. "
                                      "Beni grubunuza ekleyerek tüm medyaları yapay zeka ile denetleyebilirsiniz.", 
                     parse_mode="Markdown", reply_markup=main_markup())

@bot.message_handler(commands=['settings'])
def settings_handler(message):
    if message.chat.type == 'private': return
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("📩 Özel Mesajda Aç", url=f"https://t.me/{BOT_USERNAME}?start=set_{message.chat.id}"))
    bot.send_message(message.chat.id, "🛠️ Güvenlik ayarları için butona tıklayın:", reply_markup=m)

# --- MEDYA İŞLEME ---
@bot.message_handler(content_types=['photo', 'video', 'sticker', 'document', 'video_note'])
def handle_media(message):
    if message.chat.type == 'private': return
    
    # Dosya Belirleme
    file_id = None
    m_type = ""
    if message.photo:
        file_id = message.photo[-1].file_id
        m_type = "RESİM"
    elif message.video or message.video_note:
        file_id = (message.video.file_id if message.video else message.video_note.file_id)
        m_type = "VİDEO"
    elif message.sticker and (message.sticker.is_video or message.sticker.is_animated):
        file_id = message.sticker.file_id
        m_type = "STICKER"
    elif message.document:
        mime = message.document.mime_type or ""
        if "image" in mime: m_type, file_id = "RESİM (DOC)", message.document.file_id
        elif "video" in mime: m_type, file_id = "VİDEO (DOC)", message.document.file_id

    if not file_id: return

    def analysis_thread():
        try:
            f_info = bot.get_file(file_id)
            d_file = bot.download_file(f_info.file_path)
            temp_name = f"tmp_{message.message_id}_{file_id[:5]}.mp4" # MP4 genelde her şeyi kapsar
            with open(temp_name, 'wb') as f:
                f.write(d_file)

            bad = False
            final_reason = "Temiz"
            final_res = {}

            if "RESİM" in m_type:
                bad, final_reason, final_res = analyze_media(temp_name, m_type)
                log_to_owner(message.chat.id, message.from_user.id, m_type, bad, final_reason, final_res)
            
            elif m_type == "STICKER":
                cap = cv2.VideoCapture(temp_name)
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                for i, frame_pos in enumerate([0, total//2, total-1]):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
                    ret, frame = cap.read()
                    if ret:
                        cv2.imwrite("stk_frame.jpg", frame)
                        bad, final_reason, final_res = analyze_media("stk_frame.jpg", "Sticker Kare")
                        log_to_owner(message.chat.id, message.from_user.id, m_type, bad, final_reason, final_res, f"| Kare: {i+1}")
                        if bad: break
                cap.release()
            
            elif "VİDEO" in m_type:
                cap = cv2.VideoCapture(temp_name)
                fps = cap.get(cv2.CAP_PROP_FPS)
                duration = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps) if fps > 0 else 0
                check_limit = min(duration, 120)
                
                # Önce ilk kare (hızlı kontrol)
                for sec in range(0, check_limit, 5):
                    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
                    ret, frame = cap.read()
                    if ret:
                        cv2.imwrite("vid_frame.jpg", frame)
                        bad, final_reason, final_res = analyze_media("vid_frame.jpg", f"Video {sec}s")
                        log_to_owner(message.chat.id, message.from_user.id, m_type, bad, final_reason, final_res, f"| Sn: {sec}")
                        if bad: break
                cap.release()

            if bad:
                bot.delete_message(message.chat.id, message.message_id)
                db["stats"]["total_deleted"] += 1
                save_db(db)
                bot.send_message(message.chat.id, f"⚠️ *İçerik İmha Edildi!*\n\nKullanıcı: {message.from_user.first_name}\nSebep: `{final_reason}`", parse_mode="Markdown")

            if os.path.exists(temp_name): os.remove(temp_name)
            if os.path.exists("stk_frame.jpg"): os.remove("stk_frame.jpg")
            if os.path.exists("vid_frame.jpg"): os.remove("vid_frame.jpg")

        except Exception as e:
            logger.error(f"Analiz Hatası: {e}")

    threading.Thread(target=analysis_thread).start()

# --- SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot Active"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    # 409 Conflict hatasını önlemek için Webhook'u temizle ve polling başlat
    bot.remove_webhook()
    time.sleep(1) # Eski bağlantının kopması için kısa bekleme
    threading.Thread(target=run_flask).start()
    logger.info("Father NSFW Bot Başlatıldı...")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)