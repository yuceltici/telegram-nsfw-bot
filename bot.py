import os
import asyncio
import aiohttp
import cv2
import tempfile
import json
import uvicorn
import logging
from datetime import datetime
from math import ceil
from contextlib import suppress
from quart import Quart

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions, Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

# --- KONFİGÜRASYON ---
BOT_TOKEN = "8740463465:AAHD0PR7Sk6hMrmR1TvqIVYld-QY-OZvqns"
OWNER_ID = 8656150458
API_USER = "431285661"
API_SECRET = "KDso7QnWFP6ACYCumxkx5EWvXeiRPWAe"
PORT = 8080

DB_NAME = "father_core.sqlite"
GROUP_CACHE = {}
ADMIN_CACHE = {}

# --- RENDER UYUMU (QUART SERVER) ---
webapp = Quart(__name__)

@webapp.route('/')
async def health_check():
    return {"status": "online", "engine": "Father NSFW Protocol", "timestamp": datetime.now().isoformat()}

# --- BOT ÇEKİRDEĞİ ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class PanelStates(StatesGroup):
    waiting_for_search = State()

class SysLogger:
    """Tüm bot hareketlerini sahibine ileten gelişmiş raporlama birimi"""
    @staticmethod
    async def log(text: str):
        with suppress(Exception):
            # Log mesajları çok uzun olursa kırpılmalı
            if len(text) > 4000: text = text[:3900] + "...(devamı var)"
            await bot.send_message(OWNER_ID, f"🛡 <b>SİSTEM GÜNLÜĞÜ</b>\n\n{text}")

class Database:
    @staticmethod
    async def initialize():
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, join_date TEXT)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS groups (group_id INTEGER PRIMARY KEY, title TEXT, join_date TEXT)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS group_settings 
                              (group_id INTEGER PRIMARY KEY, title TEXT, nudity INTEGER, drugs INTEGER, 
                               weapons INTEGER, gore INTEGER, hate INTEGER, 
                               warn_limit INTEGER, action TEXT)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS warnings (user_id INTEGER, group_id INTEGER, count INTEGER, PRIMARY KEY(user_id, group_id))''')
            await db.execute('''CREATE TABLE IF NOT EXISTS stats (id INTEGER PRIMARY KEY, scanned INTEGER, deleted INTEGER)''')
            await db.execute("INSERT OR IGNORE INTO stats VALUES (1, 0, 0)")
            await db.commit()

    @staticmethod
    async def get_settings(gid: int):
        if gid in GROUP_CACHE: return GROUP_CACHE[gid]
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT * FROM group_settings WHERE group_id = ?", (gid,)) as cur:
                data = await cur.fetchone()
                if data: GROUP_CACHE[gid] = data
                return data

    @staticmethod
    async def update_stats(scanned=0, deleted=0):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE stats SET scanned = scanned + ?, deleted = deleted + ? WHERE id = 1", (scanned, deleted))
            await db.commit()

class VisionEngine:
    @staticmethod
    async def analyze(image_bytes: bytes, log_info: str):
        url = "https://api.sightengine.com/1.0/check.json"
        params = {
            'models': 'nudity-2.0,wad,offensive,gore',
            'api_user': API_USER,
            'api_secret': API_SECRET
        }
        data = aiohttp.FormData()
        data.add_field('media', image_bytes, filename='scan.jpg', content_type='image/jpeg')
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, params=params, data=data) as resp:
                    res = await resp.json()
                    await Database.update_stats(scanned=1)
                    # Detaylı API Raporu
                    await SysLogger.log(f"🔍 <b>Analiz Detayı:</b> {log_info}\n🛰 <b>API Yanıtı:</b>\n<pre>{json.dumps(res, indent=2)}</pre>")
                    return res
            except Exception as e:
                await SysLogger.log(f"⚠️ <b>API Hatası:</b> {str(e)}")
                return None

    @staticmethod
    def slice_media(path: str, mode: str):
        """Video ve Stickerları karelere bölerek analiz eder"""
        cap = cv2.VideoCapture(path)
        frames = []
        if mode == "sticker":
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            targets = [0, total // 2, total - 1] if total > 2 else [0]
            for t in targets:
                cap.set(cv2.CAP_PROP_POS_FRAMES, t)
                ret, f = cap.read()
                if ret: frames.append(cv2.imencode('.jpg', f)[1].tobytes())
        else:
            fps = cap.get(cv2.CAP_PROP_FPS) or 24
            duration = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps)
            # Her 3 saniyede bir kare al (max 20 kare)
            for s in range(0, min(duration, 60), 3):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps * s))
                ret, f = cap.read()
                if not ret: break
                frames.append(cv2.imencode('.jpg', f)[1].tobytes())
        cap.release()
        return frames

    @staticmethod
    def check_violation(res, conf):
        if not res or res.get('status') != 'success': return None
        # conf: (id, title, nudity, drugs, weapons, gore, hate, limit, action)
        if conf[2] and res.get('nudity', {}).get('safe', 1) < 0.45: return "🔞 Müstehcenlik"
        if conf[5] and res.get('gore', {}).get('prob', 0) > 0.7: return "🩸 Şiddet/Gore"
        if conf[3] and res.get('wad', {}).get('drugs', 0) > 0.8: return "💊 Uyuşturucu"
        if conf[4] and res.get('wad', {}).get('weapon', 0) > 0.8: return "🔫 Silah"
        if conf[6] and res.get('offensive', {}).get('prob', 0) > 0.85: return "☣️ Nefret Sembolü"
        return None

# --- YARDIMCI FONKSİYONLAR ---

async def is_admin(chat_id: int, user_id: int):
    if user_id == OWNER_ID: return True
    key = f"{chat_id}:{user_id}"
    if key in ADMIN_CACHE: return ADMIN_CACHE[key]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        res = member.status in ['creator', 'administrator']
        ADMIN_CACHE[key] = res
        return res
    except: return False

def get_main_kb(uid: int):
    buttons = [
        [InlineKeyboardButton(text="➕ Beni Gruba Ekle", url=f"https://t.me/{(bot.username or 'bot')}?startgroup=true")],
        [InlineKeyboardButton(text="⚙️ Grup Ayarlarım", callback_data="pnl_user_groups")],
        [InlineKeyboardButton(text="👨‍💻 Geliştirici", url="https://t.me/GELISTIRICI")]
    ]
    if uid == OWNER_ID:
        buttons.insert(2, [InlineKeyboardButton(text="🔐 Kurucu Paneli", callback_data="pnl_admin_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- MESAJ YÖNETİMİ ---

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    if msg.chat.type == ChatType.PRIVATE:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO users VALUES (?,?)", (msg.from_user.id, datetime.now().isoformat()))
            await db.commit()
        
        welcome = (
            "🛡 <b>FATHER GÜVENLİK SİSTEMİNE HOŞ GELDİNİZ</b>\n\n"
            "Bu bot, topluluklarınızı dijital kirlilikten ve zararlı içeriklerden korumak için "
            "en gelişmiş yapay zeka altyapısını kullanır. Hareketli stickerlardan videolara kadar "
            "her türlü medyayı otonom olarak denetler.\n\n"
            "<b>🚀 Başlamak için;</b>\n"
            "1. Beni grubunuza ekleyin.\n"
            "2. Yönetici yetkisi (Mesaj Silme) verin.\n"
            "3. Koruma protokolü otomatik olarak başlayacaktır."
        )
        await msg.answer(welcome, reply_markup=get_main_kb(msg.from_user.id))

@dp.message(F.new_chat_members)
async def bot_added(msg: Message):
    for m in msg.new_chat_members:
        if m.id == bot.id:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("INSERT OR REPLACE INTO groups VALUES (?,?,?)", (msg.chat.id, msg.chat.title, datetime.now().isoformat()))
                await db.execute("INSERT OR IGNORE INTO group_settings VALUES (?,?,1,1,1,1,1,3,'mute')", (msg.chat.id, msg.chat.title))
                await db.commit()
            
            await SysLogger.log(f"🆕 <b>Yeni Grup Katılımı:</b>\n🏢 Ad: {msg.chat.title}\n🆔 ID: <code>{msg.chat.id}</code>\n👤 Ekleyen: {msg.from_user.full_name}")
            await msg.answer("✅ <b>Father Güvenlik Protokolü Aktif!</b>\nYönetici yetkilerim verildiği sürece bu grup tam koruma altındadır.")

# --- AYARLAR VE CALLBACK ---

@dp.callback_query(F.data == "pnl_user_groups")
async def user_groups(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT group_id, title FROM groups") as cur:
            all_groups = await cur.fetchall()
            
    valid = []
    for gid, title in all_groups:
        if await is_admin(gid, call.from_user.id):
            valid.append((gid, title))
            
    if not valid: return await call.answer("Yönetici olduğunuz grup bulunamadı.", show_alert=True)
    
    kb = [[InlineKeyboardButton(text=f"🏢 {t}", callback_data=f"set_{i}")] for i, t in valid]
    kb.append([InlineKeyboardButton(text="🔙 Geri", callback_data="pnl_back_home")])
    await call.message.edit_text("⚙️ <b>Lütfen ayar yapmak istediğiniz grubu seçin:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("set_"))
async def group_settings_menu(call: CallbackQuery):
    gid = int(call.data.split("_")[1])
    if not await is_admin(gid, call.from_user.id): return await call.answer("Yetkisiz erişim.")
    
    conf = await Database.get_settings(gid)
    # conf: (id, title, nudity, drugs, weapons, gore, hate, limit, action)
    def b(txt, v): return f"{txt} {'✅' if v else '❌'}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b("🔞 Cinsellik", conf[2]), callback_data=f"up_{gid}_nudity"),
         InlineKeyboardButton(text=b("💊 Madde", conf[3]), callback_data=f"up_{gid}_drugs")],
        [InlineKeyboardButton(text=b("🔫 Silahlar", conf[4]), callback_data=f"up_{gid}_weapons"),
         InlineKeyboardButton(text=b("🩸 Şiddet", conf[5]), callback_data=f"up_{gid}_gore")],
        [InlineKeyboardButton(text=b("☣️ Nefret S.", conf[6]), callback_data=f"up_{gid}_hate")],
        [InlineKeyboardButton(text=f"⚠️ Uyarı Limiti: {conf[7]}", callback_data=f"up_{gid}_limit")],
        [InlineKeyboardButton(text=f"🔨 Ceza: {conf[8].upper()}", callback_data=f"up_{gid}_action")],
        [InlineKeyboardButton(text="🔙 Geri", callback_data="pnl_user_groups")]
    ])
    await call.message.edit_text(f"🛠 <b>{conf[1]}</b> için konfigürasyon:", reply_markup=kb)

@dp.callback_query(F.data.startswith("up_"))
async def update_settings(call: CallbackQuery):
    _, gid, field = call.data.split("_")
    gid = int(gid)
    conf = await Database.get_settings(gid)
    
    async with aiosqlite.connect(DB_NAME) as db:
        if field in ["nudity", "drugs", "weapons", "gore", "hate"]:
            idx = {"nudity":2, "drugs":3, "weapons":4, "gore":5, "hate":6}[field]
            await db.execute(f"UPDATE group_settings SET {field} = ? WHERE group_id = ?", (0 if conf[idx] else 1, gid))
        elif field == "limit":
            new_lim = conf[7] + 1 if conf[7] < 5 else 1
            await db.execute("UPDATE group_settings SET warn_limit = ? WHERE group_id = ?", (new_lim, gid))
        elif field == "action":
            nxt = {"mute":"kick", "kick":"ban", "ban":"none", "none":"mute"}[conf[8]]
            await db.execute("UPDATE group_settings SET action = ? WHERE group_id = ?", (nxt, gid))
        await db.commit()
    
    GROUP_CACHE.pop(gid, None)
    await SysLogger.log(f"🔧 <b>Ayar Güncellendi:</b>\n🏢 Grup: {conf[1]}\n🛠 Alan: {field}\n👤 Yapan: {call.from_user.full_name}")
    call.data = f"set_{gid}"
    await group_settings_menu(call)

# --- MEDYA ANALİZ KATMANI ---

@dp.message(F.photo | F.video | F.sticker | F.animation)
async def on_media(msg: Message):
    if msg.chat.type == ChatType.PRIVATE: return
    conf = await Database.get_settings(msg.chat.id)
    if not conf: return
    
    # Yönetici muafiyeti
    if await is_admin(msg.chat.id, msg.from_user.id): return

    file_id = None
    mode = "image"
    
    if msg.photo: file_id = msg.photo[-1].file_id
    elif msg.video: file_id = msg.video.file_id; mode = "video"
    elif msg.animation: file_id = msg.animation.file_id; mode = "video"
    elif msg.sticker:
        file_id = msg.sticker.file_id
        if msg.sticker.is_animated or msg.sticker.is_video: mode = "sticker"
        else: mode = "image"
        
    if not file_id: return
    
    f_info = await bot.get_file(file_id)
    violation = None
    log_tag = f"👤 {msg.from_user.id} | 🏢 {msg.chat.id} ({msg.chat.title})"

    if mode == "image":
        raw = await bot.download_file(f_info.file_path)
        res = await VisionEngine.analyze(raw.read(), f"🖼 Fotoğraf - {log_tag}")
        violation = VisionEngine.check_violation(res, conf)
    else:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            await bot.download_file(f_info.file_path, tmp.name)
            frames = await asyncio.to_thread(VisionEngine.slice_media, tmp.name, mode)
            os.remove(tmp.name)
            
        for i, frame in enumerate(frames):
            res = await VisionEngine.analyze(frame, f"📽 {mode.upper()} Kare {i+1} - {log_tag}")
            violation = VisionEngine.check_violation(res, conf)
            if violation: break

    if violation:
        await Database.update_stats(deleted=1)
        with suppress(Exception): await msg.delete()
        
        await SysLogger.log(f"🗑 <b>TEHDİT İMHA EDİLDİ</b>\n👤 Kullanıcı: {msg.from_user.id}\n🚫 Sebep: {violation}\n🏢 Grup: {msg.chat.title}")
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT count FROM warnings WHERE user_id=? AND group_id=?", (msg.from_user.id, msg.chat.id)) as cur:
                row = await cur.fetchone()
                w_cnt = (row[0] + 1) if row else 1
            await db.execute("INSERT OR REPLACE INTO warnings VALUES (?,?,?)", (msg.from_user.id, msg.chat.id, w_cnt))
            await db.commit()
            
        penalty_text = ""
        if w_cnt >= conf[7] and conf[8] != "none":
            action = conf[8]
            with suppress(Exception):
                if action == "mute": await bot.restrict_chat_member(msg.chat.id, msg.from_user.id, permissions=ChatPermissions(can_send_messages=False))
                elif action == "kick": await bot.ban_chat_member(msg.chat.id, msg.from_user.id); await bot.unban_chat_member(msg.chat.id, msg.from_user.id)
                elif action == "ban": await bot.ban_chat_member(msg.chat.id, msg.from_user.id)
                penalty_text = f"\n🚨 <b>Limit aşıldı, kullanıcı cezalandırıldı ({action.upper()}).</b>"
                await SysLogger.log(f"🔨 <b>OTONOM CEZA:</b> {action.upper()}\n👤 Kullanıcı: {msg.from_user.id}")

        info = await msg.answer(f"🚫 <a href='tg://user?id={msg.from_user.id}'>{msg.from_user.first_name}</a>, gönderdiğin içerik sistem tarafından zararlı bulundu.\n⚠️ <b>Uyarı:</b> {w_cnt}/{conf[7]}{penalty_text}")
        await asyncio.sleep(7)
        with suppress(Exception): await info.delete()

# --- KURUCU KOMUTA PANELİ ---

@dp.callback_query(F.data == "pnl_admin_main")
async def admin_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with aiosqlite.connect(DB_NAME) as db:
        st = await (await db.execute("SELECT * FROM stats WHERE id=1")).fetchone()
        u = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        g = await (await db.execute("SELECT COUNT(*) FROM groups")).fetchone()
        
    text = (
        "👑 <b>KURUCU KOMUTA PANELİ</b>\n\n"
        f"👁‍🗨 <b>Toplam Tarama:</b> <code>{st[1]}</code>\n"
        f"🗑 <b>Silinen Tehdit:</b> <code>{st[2]}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Kayıtlı Kullanıcı:</b> <code>{u[0]}</code>\n"
        f"🏢 <b>Aktif Grup:</b> <code>{g[0]}</code>\n\n"
        "Anlık raporlar DM kutunuza gönderilmeye devam ediyor."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Kullanıcı Listesi", callback_data="pnl_list_usr_1"),
         InlineKeyboardButton(text="🏢 Grup Listesi", callback_data="pnl_list_grp_1")],
        [InlineKeyboardButton(text="🔍 Veritabanında Ara", callback_data="pnl_search_start")],
        [InlineKeyboardButton(text="🔙 Ana Menü", callback_data="pnl_back_home")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("pnl_list_"))
async def admin_list(call: CallbackQuery):
    parts = call.data.split("_")
    mode, page = parts[2], int(parts[3])
    table = "users" if mode == "usr" else "groups"
    offset = (page - 1) * 10
    
    async with aiosqlite.connect(DB_NAME) as db:
        items = await (await db.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 10 OFFSET {offset}")).fetchall()
        total = (await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[0]
        
    pages = ceil(total / 10) or 1
    text = f"📋 <b>{mode.upper()} Kayıtları (Sayfa {page}/{pages}):</b>\n\n"
    for i in items:
        if mode == "usr": text += f"• <code>{i[0]}</code> | {i[1][:10]}\n"
        else: text += f"• {i[1][:15]} | <code>{i[0]}</code>\n"
        
    nav = []
    if page > 1: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"pnl_list_{mode}_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{pages}", callback_data="null"))
    if page < pages: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"pnl_list_{mode}_{page+1}"))
    
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav, [InlineKeyboardButton(text="🔙 Geri", callback_data="pnl_admin_main")]]))

@dp.callback_query(F.data == "pnl_search_start")
async def admin_search_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PanelStates.waiting_for_search)
    await call.message.edit_text("🔎 <b>Sorgu Başlatıldı</b>\n\nAramak istediğiniz Kullanıcı ID, Grup ID veya Grup Adını yazın:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="İptal", callback_data="pnl_admin_main")]]))

@dp.message(PanelStates.waiting_for_search)
async def admin_search_proc(msg: Message, state: FSMContext):
    q = f"%{msg.text}%"
    async with aiosqlite.connect(DB_NAME) as db:
        u = await (await db.execute("SELECT * FROM users WHERE user_id LIKE ?", (q,))).fetchall()
        g = await (await db.execute("SELECT * FROM groups WHERE group_id LIKE ? OR title LIKE ?", (q, q))).fetchall()
        
    res = "🔎 <b>Arama Sonuçları:</b>\n\n"
    if u:
        res += "<b>Kullanıcılar:</b>\n"
        for i in u: res += f"• <code>{i[0]}</code> (Kayıt: {i[1][:10]})\n"
    if g:
        res += "\n<b>Gruplar:</b>\n"
        for i in g: res += f"• {i[1]} | <code>{i[0]}</code>\n"
    
    if not u and not g: res = "❌ Eşleşen kayıt bulunamadı."
    
    await msg.answer(res, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Panele Dön", callback_data="pnl_admin_main")]]))
    await state.clear()

@dp.callback_query(F.data == "pnl_back_home")
async def back_home(call: CallbackQuery):
    await call.message.edit_text("🛡 <b>FATHER GÜVENLİK SİSTEMİ</b>\n\nProtokol devrede.", reply_markup=get_main_kb(call.from_user.id))

# --- ÇALIŞTIRMA ---

async def main():
    await Database.initialize()
    
    # Quart config
    config = uvicorn.Config(webapp, host="0.0.0.0", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    
    await SysLogger.log(f"🚀 <b>SİSTEM ÇEVRİMİÇİ</b>\n\nSunucu Portu: {PORT}\nZaman: {datetime.now().strftime('%H:%M:%S')}")
    
    # Render'da her iki süreci de asenkron çalıştır
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"])
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass