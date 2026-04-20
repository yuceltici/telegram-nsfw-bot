# ╔══════════════════════════════════════════════════════════╗
#   Father NSFW Delete Bot — bot.py
#   Render.com / Linux uyumlu
#   pip install pyTelegramBotAPI requests opencv-python Pillow
# ╚══════════════════════════════════════════════════════════╝

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AYARLAR — sadece burası doldurulur
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN    = "8740463465:AAHD0PR7Sk6hMrmR1TvqIVYld-QY-OZvqns"
BOT_USERNAME = "hbbbbbbvvvbot"   # @ işaretsiz
OWNER_ID     = 8656150458                        # Kendi Telegram ID'n

SE_USER   = "431285661"
SE_SECRET = "KDso7QnWFP6ACYCumxkx5EWvXeiRPWAe"
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os, io, json, time, logging, tempfile, threading, sqlite3
import requests, cv2
import telebot
from PIL import Image
from telebot.types import (Message, CallbackQuery,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           ChatPermissions)

# ══════════════════════════════════════
#  SABİTLER
# ══════════════════════════════════════
BOT_NAME  = "Father NSFW Delete Bot"
BOT_VER   = "v5.0"
DB_PATH   = "father.db"
THRESHOLD = 0.60
SEP       = "━━━━━━━━━━━━━━━━━━━━"
PERM_TICK = 60   # saniye

SE_IMG    = "https://api.sightengine.com/1.0/check.json"
SE_MODELS = "nudity-2.1,wad,gore-2.0,hate-symbols"

# Video tarama parametreleri
VID_MAX_SEC   = 120  # en fazla kaç saniye taransın
VID_INTERVAL  = 5    # kaç saniyede bir kare

DEF_CATS = {
    "nudity": True, "drugs": True, "weapon": True,
    "violence": True, "gore": False, "hate": True,
}
DEF_WA = {"1": "warn", "3": "mute_1h", "5": "kick"}
ACT_LBL = {
    "warn": "⚠️ Uyar", "mute_1h": "🔇 1s Sus",
    "mute_24h": "🔇 24s Sus", "kick": "👢 At",
    "ban": "🚫 Yasakla", "none": "— Yok",
}
NOTIF_DEL_OPTS = [0, 30, 60, 120, 300]

# ══════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("father.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
L = logging.getLogger("father")

# ══════════════════════════════════════
#  VERİTABANI
# ══════════════════════════════════════
_dbl = threading.Lock()

def _db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    with _dbl:
        c = _db()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            uid INTEGER PRIMARY KEY, username TEXT,
            fullname TEXT, joined INTEGER, banned INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS grps(
            cid INTEGER PRIMARY KEY, title TEXT, added INTEGER,
            active INTEGER DEFAULT 1,
            warn_limit INTEGER DEFAULT 3,
            del_timeout INTEGER DEFAULT 0,
            notify INTEGER DEFAULT 1,
            notif_del INTEGER DEFAULT 60,
            cats TEXT, warn_actions TEXT,
            has_perms INTEGER DEFAULT 0,
            perm_ts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS warns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cid INTEGER, uid INTEGER,
            reason TEXT, ts INTEGER, mid INTEGER);
        CREATE TABLE IF NOT EXISTS stats(
            cid INTEGER PRIMARY KEY,
            deleted INTEGER DEFAULT 0,
            warned INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            last_ts INTEGER DEFAULT 0);
        """)
        for col, default in [("notif_del", "60")]:
            try:
                c.execute(f"ALTER TABLE grps ADD COLUMN {col} INTEGER DEFAULT {default}")
            except Exception:
                pass
        c.commit(); c.close()
    L.info("DB hazır.")

# ── kullanıcı ──────────────────────────
def u_save(uid, uname, fname):
    with _dbl:
        c = _db()
        c.execute("INSERT OR IGNORE INTO users(uid,username,fullname,joined) VALUES(?,?,?,?)",
                  (uid, uname or "", fname or "", int(time.time())))
        c.execute("UPDATE users SET username=?,fullname=? WHERE uid=?",
                  (uname or "", fname or "", uid))
        c.commit(); c.close()

def u_all(page=1, per=8):
    with _dbl:
        c = _db(); off = (page - 1) * per
        rows = c.execute(
            "SELECT * FROM users ORDER BY joined DESC LIMIT ? OFFSET ?",
            (per, off)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        c.close()
    return [dict(r) for r in rows], total

# ── grup ───────────────────────────────
def g_save(cid, title):
    with _dbl:
        c = _db()
        c.execute("""INSERT OR IGNORE INTO grps
            (cid,title,added,cats,warn_actions) VALUES(?,?,?,?,?)""",
            (cid, title, int(time.time()),
             json.dumps(DEF_CATS), json.dumps(DEF_WA)))
        c.execute("UPDATE grps SET title=? WHERE cid=?", (title, cid))
        c.commit(); c.close()

def g_get(cid):
    with _dbl:
        c = _db()
        r = c.execute("SELECT * FROM grps WHERE cid=?", (cid,)).fetchone()
        c.close()
    if not r:
        return None
    g = dict(r)
    try: g["cats"] = json.loads(g.get("cats") or "{}")
    except: g["cats"] = dict(DEF_CATS)
    try: g["warn_actions"] = json.loads(g.get("warn_actions") or "{}")
    except: g["warn_actions"] = dict(DEF_WA)
    return g

def g_update(cid, **kw):
    with _dbl:
        c = _db()
        for k, v in kw.items():
            if isinstance(v, (dict, list)): v = json.dumps(v)
            c.execute(f"UPDATE grps SET {k}=? WHERE cid=?", (v, cid))
        c.commit(); c.close()

def g_all(page=1, per=8):
    with _dbl:
        c = _db(); off = (page - 1) * per
        rows = c.execute(
            "SELECT * FROM grps ORDER BY added DESC LIMIT ? OFFSET ?",
            (per, off)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM grps").fetchone()[0]
        c.close()
    return [dict(r) for r in rows], total

def g_active():
    with _dbl:
        c = _db()
        rows = c.execute("SELECT * FROM grps WHERE active=1").fetchall()
        c.close()
    return [dict(r) for r in rows]

# ── uyarı ──────────────────────────────
def w_add(cid, uid, reason, mid):
    with _dbl:
        c = _db()
        c.execute("INSERT INTO warns(cid,uid,reason,ts,mid) VALUES(?,?,?,?,?)",
                  (cid, uid, reason, int(time.time()), mid))
        c.commit()
        cnt = c.execute(
            "SELECT COUNT(*) FROM warns WHERE cid=? AND uid=?",
            (cid, uid)).fetchone()[0]
        c.close()
    return cnt

# ── istatistik ─────────────────────────
def s_inc(cid, field):
    with _dbl:
        c = _db()
        c.execute("INSERT OR IGNORE INTO stats(cid) VALUES(?)", (cid,))
        c.execute(
            f"UPDATE stats SET {field}={field}+1, last_ts=? WHERE cid=?",
            (int(time.time()), cid))
        c.commit(); c.close()

def s_get(cid):
    with _dbl:
        c = _db()
        c.execute("INSERT OR IGNORE INTO stats(cid) VALUES(?)", (cid,))
        c.commit()
        r = c.execute("SELECT * FROM stats WHERE cid=?", (cid,)).fetchone()
        c.close()
    return dict(r) if r else {}

def s_global():
    with _dbl:
        c = _db()
        r = c.execute(
            "SELECT SUM(deleted) d,SUM(warned) w,SUM(banned) b FROM stats"
        ).fetchone()
        u = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        g = c.execute("SELECT COUNT(*) FROM grps WHERE active=1").fetchone()[0]
        c.close()
    return {
        "deleted": r["d"] or 0, "warned": r["w"] or 0,
        "banned": r["b"] or 0, "users": u, "groups": g,
    }

# ══════════════════════════════════════
#  SightEngine — resim API
# ══════════════════════════════════════
def _se_post(jpeg_bytes, fname="media.jpg"):
    """Ham JPEG byte'larını SightEngine resim API'sine gönder."""
    try:
        r = requests.post(
            SE_IMG,
            files={"media": (fname, jpeg_bytes, "image/jpeg")},
            data={"models": SE_MODELS,
                  "api_user": SE_USER,
                  "api_secret": SE_SECRET},
            timeout=20,
        )
        res = r.json()
        L.debug("[SE] status=%s nudity=%.2f drug=%.2f",
                res.get("status"),
                res.get("nudity", {}).get("sexual_activity", 0),
                res.get("recreational_drug", {}).get("prob", 0))
        return res
    except Exception as e:
        L.error("[SE] istek hatası: %s", e)
        return {}

def _se_post_file(path):
    """Dosyayı oku, SightEngine'e gönder (statik resimler için)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        ext  = os.path.splitext(path)[1].lower()
        mime = "image/webp" if ext == ".webp" else "image/jpeg"
        r = requests.post(
            SE_IMG,
            files={"media": (os.path.basename(path), raw, mime)},
            data={"models": SE_MODELS,
                  "api_user": SE_USER,
                  "api_secret": SE_SECRET},
            timeout=20,
        )
        res = r.json()
        L.debug("[SE-FILE] status=%s", res.get("status"))
        return res
    except Exception as e:
        L.error("[SE-FILE] %s", e)
        return {}

# ══════════════════════════════════════
#  Sonuç ayrıştırma
# ══════════════════════════════════════
def parse(res, cats):
    """SightEngine yanıtını ayrıştır, tespit edilen kategorileri döndür."""
    found = []
    if not res or res.get("status") != "success":
        L.warning("[PARSE] başarısız: %s", res)
        return False, []

    n  = res.get("nudity", {})
    ns = max(n.get("sexual_activity", 0), n.get("sexual_display", 0),
             n.get("erotica", 0), n.get("very_suggestive", 0))
    L.debug("[PARSE] nudity=%.3f", ns)
    if cats.get("nudity") and ns >= THRESHOLD:
        found.append("🔞 Çıplaklık/Cinsellik")

    wc = res.get("weapon", {}).get("classes", {})
    ws = max(wc.get("firearm", 0), wc.get("knife", 0))
    L.debug("[PARSE] weapon=%.3f", ws)
    if cats.get("weapon") and ws >= THRESHOLD:
        found.append("🔫 Silah")

    ds = res.get("recreational_drug", {}).get("prob", 0)
    L.debug("[PARSE] drug=%.3f", ds)
    if cats.get("drugs") and ds >= THRESHOLD:
        found.append("💊 Uyuşturucu")

    vs = res.get("violence", {}).get("prob", 0)
    L.debug("[PARSE] violence=%.3f", vs)
    if cats.get("violence") and vs >= THRESHOLD:
        found.append("⚔️ Şiddet")

    gs = res.get("gore", {}).get("prob", 0)
    L.debug("[PARSE] gore=%.3f", gs)
    if cats.get("gore") and gs >= THRESHOLD:
        found.append("🩸 Kan/Gore")

    hc = res.get("hate_symbols", {}).get("classes", {})
    hs = max(hc.get("nazi", 0), hc.get("supremacist", 0))
    L.debug("[PARSE] hate=%.3f", hs)
    if cats.get("hate") and hs >= THRESHOLD:
        found.append("☣️ Nefret Sembolü")

    L.info("[PARSE] %s", found if found else "TEMİZ")
    return bool(found), found

# ══════════════════════════════════════
#  OpenCV kare çekme
# ══════════════════════════════════════
def _frame_to_jpeg(frame_bgr):
    """OpenCV BGR frame → Pillow → JPEG bytes."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()

def check_video_frames(path, cats):
    """
    OpenCV ile video/hareketli sticker karelere bölünür.
    Her kare Pillow ile JPEG'e çevrilip SightEngine resim
    API'sine normal fotoğraf olarak gönderilir.

    - İlk 2 dakika (120 sn) taranır
    - Her 5 saniyede 1 kare
    - İlk tespitte ANINDA durulur (API hakkı koruması)

    Döndürür: (detected: bool, found: list)
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        L.error("[CV2] Açılamadı: %s", path)
        return False, []

    fps        = cap.get(cv2.CAP_PROP_FPS) or 25
    total_fr   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration   = total_fr / fps if fps > 0 else 0
    scan_until = min(duration, VID_MAX_SEC)

    L.info("[CV2] %s | %.1fs | %.0ffps | taranacak: %.0fs",
           os.path.basename(path), duration, fps, scan_until)

    detected, found = False, []
    t = 0.0
    while t <= scan_until:
        frame_idx = int(t * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            L.debug("[CV2] %.1fs kare okunamadı", t)
            t += VID_INTERVAL
            continue

        try:
            jpeg = _frame_to_jpeg(frame)
        except Exception as e:
            L.debug("[CV2] %.1fs encode hatası: %s", t, e)
            t += VID_INTERVAL
            continue

        L.debug("[CV2] %.1fs → SE (%d byte)", t, len(jpeg))
        res = _se_post(jpeg, f"frame_{int(t)}s.jpg")
        ok, cats_found = parse(res, cats)
        if ok:
            L.info("[CV2] ✅ TESPIT %.1fs: %s", t, cats_found)
            detected, found = True, cats_found
            break

        t += VID_INTERVAL

    cap.release()
    if not detected:
        L.info("[CV2] Temiz — %d nokta kontrol edildi", int(scan_until / VID_INTERVAL) + 1)
    return detected, found

# ══════════════════════════════════════
#  İndirme
# ══════════════════════════════════════
def dl(bot, file_id, suffix):
    """Telegram'dan streaming indirme, max 50MB."""
    info = bot.get_file(file_id)
    url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}"
    tmp  = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    total = 0; MAX = 50 * 1024 * 1024
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            for chunk in r.iter_content(524288):
                if not chunk: continue
                tmp.write(chunk); total += len(chunk)
                if total >= MAX:
                    L.warning("[DL] 50MB limit")
                    break
    finally:
        tmp.close()
    L.info("[DL] %s (%.1f MB)", os.path.basename(tmp.name), total / 1024 / 1024)
    return tmp.name

def rm(path):
    try:
        if path and os.path.exists(path): os.remove(path)
    except: pass

# ══════════════════════════════════════
#  Moderasyon
# ══════════════════════════════════════
def best_action(wa, count):
    act, lvl = "warn", 0
    for ls, a in wa.items():
        try:
            l = int(ls)
            if l <= count and l > lvl: lvl = l; act = a
        except: pass
    return act

def do_action(bot, cid, uid, action):
    L.info("[MOD] %s uid=%d", action, uid)
    try:
        if action == "mute_1h":
            bot.restrict_chat_member(
                cid, uid, ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + 3600)
        elif action == "mute_24h":
            bot.restrict_chat_member(
                cid, uid, ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + 86400)
        elif action == "kick":
            bot.kick_chat_member(cid, uid)
            time.sleep(1)
            bot.unban_chat_member(cid, uid)
        elif action == "ban":
            bot.kick_chat_member(cid, uid)
            s_inc(cid, "banned")
    except Exception as e:
        L.warning("[MOD] başarısız: %s", e)

def del_later(bot, cid, mid, secs):
    if secs <= 0: return
    def _d():
        time.sleep(secs)
        try: bot.delete_message(cid, mid)
        except: pass
    threading.Thread(target=_d, daemon=True).start()

# ══════════════════════════════════════
#  Klavye yardımcısı
# ══════════════════════════════════════
def mk(*rows):
    m = InlineKeyboardMarkup()
    for row in rows:
        btns = []
        for text, data in row:
            if data.startswith("url:"):
                btns.append(InlineKeyboardButton(text, url=data[4:]))
            else:
                btns.append(InlineKeyboardButton(text, callback_data=data))
        m.row(*btns)
    return m

# ── Klavyeler ──────────────────────────
def kb_dm(owner=False):
    rows = [[("➕ Beni Grubuna Ekle",
              f"url:https://t.me/{BOT_USERNAME}?startgroup=true")]]
    if owner:
        rows += [
            [("👥 Kullanıcılar", "op_u:1"), ("🏠 Gruplar", "op_g:1")],
            [("📢 Duyuru", "op_bc"), ("📊 İstatistikler", "op_s")],
        ]
    rows.append([("ℹ️ Hakkında", "about"), ("👤 Sahip", "contact")])
    return mk(*rows)

def kb_gs(cid):
    return mk([("💬 Burada Aç", f"sh:{cid}"), ("💌 DM'de Aç", f"sd:{cid}")])

def kb_gw(cid):
    return mk([("⚙️ Ayarları Aç", f"sh:{cid}")])

def kb_drl(cid):
    return mk([("▶️ Başlat & Ayarları Aç",
                f"url:https://t.me/{BOT_USERNAME}?start=s_{cid}")])

def kb_set(cid):
    return mk(
        [("🔍 Kategoriler", f"mc:{cid}"), ("⚖️ Uyarı Eylemleri", f"mw:{cid}")],
        [("⏱ Silme Gecikmesi", f"mt:{cid}"), ("🔔 Bildirimler", f"mn:{cid}")],
        [("🗑 Bildirim Silme", f"mnd:{cid}"), ("⚠️ Uyarı Limiti", f"ml:{cid}")],
        [("📊 İstatistikler", f"ms:{cid}"), ("❌ Kapat", f"cx:{cid}")],
    )

def kb_cats(cid, cats):
    def ic(k): return "✅" if cats.get(k) else "❌"
    return mk(
        [(f"{ic('nudity')} Cinsellik", f"tc:{cid}:nudity"),
         (f"{ic('drugs')} Uyuşturucu", f"tc:{cid}:drugs")],
        [(f"{ic('weapon')} Silah", f"tc:{cid}:weapon"),
         (f"{ic('violence')} Şiddet", f"tc:{cid}:violence")],
        [(f"{ic('gore')} Gore/Kan", f"tc:{cid}:gore"),
         (f"{ic('hate')} Nefret", f"tc:{cid}:hate")],
        [("✅ Tümünü Aç", f"ca:{cid}"), ("❌ Tümünü Kapat", f"co:{cid}")],
        [("◀️ Geri", f"bs:{cid}")],
    )

def kb_warns(cid, wa):
    rows = [[(f"{i}. Uyarı → {ACT_LBL.get(wa.get(str(i),'none'),'—')}",
               f"pa:{cid}:{i}")] for i in range(1, 6)]
    rows.append([("◀️ Geri", f"bs:{cid}")])
    return mk(*rows)

def kb_ap(cid, lvl):
    rows = [[(lbl, f"sa:{cid}:{lvl}:{a}")] for a, lbl in ACT_LBL.items()]
    rows.append([("◀️ Geri", f"mw:{cid}")])
    return mk(*rows)

def kb_timeout(cid, cur):
    rows = [[((("🔘" if cur == t else "⚪️") + " " +
               ("Anında" if t == 0 else f"{t}sn")),
              f"st:{cid}:{t}")] for t in [0, 5, 10, 30, 60]]
    rows.append([("◀️ Geri", f"bs:{cid}")])
    return mk(*rows)

def kb_notify(cid, on):
    return mk(
        [(("🔘" if on else "⚪️") + " ✅ Açık", f"sn:{cid}:1"),
         (("🔘" if not on else "⚪️") + " ❌ Kapalı", f"sn:{cid}:0")],
        [("◀️ Geri", f"bs:{cid}")],
    )

def kb_notif_del(cid, cur):
    rows = []
    for t in NOTIF_DEL_OPTS:
        mark  = "🔘" if cur == t else "⚪️"
        label = f"{mark} Silinmesin" if t == 0 else f"{mark} {t}sn sonra"
        rows.append([(label, f"snd:{cid}:{t}")])
    rows.append([("◀️ Geri", f"bs:{cid}")])
    return mk(*rows)

def kb_wl(cid, cur):
    rows = [[(("🔘" if cur == w else "⚪️") + f" {w} Uyarı",
               f"sl:{cid}:{w}")] for w in [1, 2, 3, 5, 7, 10]]
    rows.append([("◀️ Geri", f"bs:{cid}")])
    return mk(*rows)

def kb_panel():
    return mk(
        [("👥 Kullanıcılar", "op_u:1"), ("🏠 Gruplar", "op_g:1")],
        [("📢 Duyuru", "op_bc"), ("📊 İstatistikler", "op_s")],
        [("❌ Kapat", "cx")],
    )

def kb_unav(page, total, per=8):
    tp = max(1, (total + per - 1) // per)
    nav = []
    if page > 1: nav.append(("◀️", f"op_u:{page-1}"))
    nav.append((f"📄 {page}/{tp}", "noop"))
    if page < tp: nav.append(("▶️", f"op_u:{page+1}"))
    return mk(nav, [("◀️ Panel", "op_p")])

def kb_gnav(page, total, per=8):
    tp = max(1, (total + per - 1) // per)
    nav = []
    if page > 1: nav.append(("◀️", f"op_g:{page-1}"))
    nav.append((f"📄 {page}/{tp}", "noop"))
    if page < tp: nav.append(("▶️", f"op_g:{page+1}"))
    return mk(nav, [("◀️ Panel", "op_p")])

def kb_bc():
    return mk([("✅ Gönder", "op_bcs"), ("❌ İptal", "op_bcc")])

def kb_geri(d):
    return mk([("◀️ Geri", d)])

def kb_kapat():
    return mk([("❌ Kapat", "cx")])

# ══════════════════════════════════════
#  Metinler
# ══════════════════════════════════════
import datetime as _dt

def t_dm(name):
    return (f"👋 Merhaba, <b>{name}</b>!\n{SEP}\n"
            f"🛡️ <b>{BOT_NAME}</b>'e hoş geldin!\n\n"
            f"<b>🔍 Kontrol Ettiğim İçerikler:</b>\n"
            f"├ 🔞 Cinsellik & Müstehcenlik\n"
            f"├ 💊 Uyuşturucu & Madde\n"
            f"├ 🔫 Silah & Tehlikeli Nesneler\n"
            f"├ ⚔️ Şiddet\n"
            f"└ ☣️ Nefret Sembolleri\n\n"
            f"<b>📦 Desteklenen Medya:</b>\n"
            f"├ 🖼 Fotoğraf\n"
            f"├ 🎬 Video (kare kare analiz)\n"
            f"└ 🎭 Sticker (hareketli dahil)\n"
            f"{SEP}\n⚙️ <i>Başlamak için beni grubuna ekle!</i>")

def t_owner(name, st):
    return (f"👑 Merhaba, <b>{name}</b>!\n{SEP}\n"
            f"🤖 <b>{BOT_NAME}</b> {BOT_VER}\n\n"
            f"<b>📊 Anlık Durum:</b>\n"
            f"├ 👥 Kullanıcı: <b>{st['users']}</b>\n"
            f"├ 🏠 Aktif Grup: <b>{st['groups']}</b>\n"
            f"├ 🗑 Silinen: <b>{st['deleted']}</b>\n"
            f"├ ⚠️ Uyarı: <b>{st['warned']}</b>\n"
            f"└ 🚫 Ban: <b>{st['banned']}</b>\n"
            f"{SEP}\n<i>Aşağıdan paneli yönet:</i>")

def t_added(title):
    return (f"👋 Merhaba <b>{title}</b>!\n{SEP}\n"
            f"🛡️ <b>{BOT_NAME}</b> grubunuza eklendi!\n\n"
            f"<b>⚡ Aktif Kontroller:</b>\n"
            f"├ 🔞 Cinsellik  ├ 💊 Uyuşturucu\n"
            f"├ 🔫 Silah      ├ ⚔️ Şiddet\n"
            f"└ ☣️ Nefret Sembolleri\n\n"
            f"<b>📋 Başlamak İçin:</b>\n"
            f"1️⃣ Bana <b>Mesaj Sil</b> yetkisi ver\n"
            f"2️⃣ Yöneticiler /settings ile ayarlayabilir\n"
            f"{SEP}")

def t_no_perm():
    return (f"⚠️ <b>Yetki Eksikliği!</b>\n{SEP}\n"
            f"Mesaj silme yetkim <b>yok!</b>\n\n"
            f"Lütfen bana şu yetkiyi verin:\n"
            f"└ 🗑 <b>Mesajları Sil</b>\n\n"
            f"<i>Yetki verilmeden içerik denetimi yapamam.</i>")

def t_deleted(name, uid, cats, wc, wl):
    cl = "\n".join(
        (f"├ {c}" if i < len(cats) - 1 else f"└ {c}")
        for i, c in enumerate(cats)
    ) if cats else "└ Bilinmeyen"
    return (f"🚨 <b>Uygunsuz İçerik Silindi</b>\n{SEP}\n"
            f"👤 <a href='tg://user?id={uid}'>{name}</a>\n\n"
            f"<b>📋 Tespit Edilen:</b>\n{cl}\n\n"
            f"⚠️ Uyarı: <b>{wc}/{wl}</b>\n{SEP}")

def t_action(name, uid, action, wc):
    al = {"warn": "⚠️ Uyarıldı", "mute_1h": "🔇 1s Susturuldu",
          "mute_24h": "🔇 24s Susturuldu", "kick": "👢 Atıldı",
          "ban": "🚫 Yasaklandı"}
    return (f"⚡ <b>İşlem Uygulandı</b>\n{SEP}\n"
            f"👤 <a href='tg://user?id={uid}'>{name}</a>\n"
            f"📊 Uyarı: <b>{wc}</b>  ⚖️ <b>{al.get(action, action)}</b>\n{SEP}")

def t_settings(g):
    cats = g.get("cats", {}); wa = g.get("warn_actions", {})
    def ic(k): return "✅" if cats.get(k) else "❌"
    tl  = "Anında" if g.get("del_timeout", 0) == 0 else f"{g.get('del_timeout')}sn"
    nd  = g.get("notif_del", 60)
    ndl = "Silinmez" if nd == 0 else f"{nd}sn sonra silinir"
    wa_t = "".join(
        f"├ {i}. → {ACT_LBL.get(wa.get(str(i), 'none'), '—')}\n"
        for i in range(1, 6)
    )
    return (f"⚙️ <b>Grup Ayarları</b>\n{SEP}\n"
            f"🏠 <b>{g.get('title', 'Grup')}</b>\n\n"
            f"<b>🔍 Kategoriler:</b>\n"
            f"├ 🔞 Cinsellik: {ic('nudity')}  💊 Uyuşturucu: {ic('drugs')}\n"
            f"├ 🔫 Silah: {ic('weapon')}  ⚔️ Şiddet: {ic('violence')}\n"
            f"├ 🩸 Gore: {ic('gore')}  ☣️ Nefret: {ic('hate')}\n\n"
            f"<b>⚖️ Uyarı Eylemleri:</b>\n{wa_t}"
            f"<b>📋 Diğer:</b>\n"
            f"├ ⏱ Silme Gecikmesi: <b>{tl}</b>\n"
            f"├ 🔔 Bildirim: <b>{'✅ Açık' if g.get('notify', 1) else '❌ Kapalı'}</b>\n"
            f"├ 🗑 Bildirim Mesajı: <b>{ndl}</b>\n"
            f"└ ⚠️ Uyarı Limiti: <b>{g.get('warn_limit', 3)}</b>\n"
            f"{SEP}\n<i>Aşağıdan ayarlayabilirsiniz:</i>")

def t_about():
    return (f"ℹ️ <b>Hakkında</b>\n{SEP}\n"
            f"🤖 <b>{BOT_NAME}</b>  {BOT_VER}\n\n"
            f"SightEngine AI ile gruplardaki uygunsuz içerikleri\n"
            f"otomatik tespit edip siler.\n\n"
            f"<b>📦 Kontrol:</b>\n"
            f"├ 🖼 Fotoğraf\n"
            f"├ 🎬 Video (OpenCV kare analizi)\n"
            f"└ 🎭 Sticker (hareketli dahil)\n{SEP}")

def t_panel(st):
    return (f"👑 <b>Kurucu Paneli</b>\n{SEP}\n"
            f"🤖 {BOT_NAME} {BOT_VER}\n\n"
            f"<b>📊 İstatistikler:</b>\n"
            f"├ 👥 Kullanıcı: <b>{st['users']}</b>\n"
            f"├ 🏠 Aktif Grup: <b>{st['groups']}</b>\n"
            f"├ 🗑 Silinen: <b>{st['deleted']}</b>\n"
            f"├ ⚠️ Uyarı: <b>{st['warned']}</b>\n"
            f"└ 🚫 Ban: <b>{st['banned']}</b>\n"
            f"{SEP}\n<i>Yönetmek istediğin bölümü seç:</i>")

def t_users(rows, page, total, per=8):
    tp = max(1, (total + per - 1) // per)
    lines = [f"👥 <b>Kullanıcılar</b>", SEP,
             f"📄 <b>{page}/{tp}</b> · Toplam: <b>{total}</b>\n"]
    for u in rows:
        d   = _dt.datetime.fromtimestamp(u.get("joined", 0)).strftime("%d.%m.%Y")
        ban = " 🚫" if u.get("banned") else ""
        un  = f"@{u['username']}" if u.get("username") else "—"
        uid_s = str(u["uid"]); fn = u.get("fullname", "?") or "?"
        lines.append(f"├ <a href='tg://user?id={uid_s}'>{fn}</a>{ban}\n"
                     f"│  {un} · <code>{uid_s}</code> · {d}")
    return "\n".join(lines) + f"\n{SEP}"

def t_groups(rows, page, total, per=8):
    tp = max(1, (total + per - 1) // per)
    lines = [f"🏠 <b>Gruplar</b>", SEP,
             f"📄 <b>{page}/{tp}</b> · Toplam: <b>{total}</b>\n"]
    for g in rows:
        d  = _dt.datetime.fromtimestamp(g.get("added", 0)).strftime("%d.%m.%Y")
        st = "✅" if g.get("active") else "❌"
        pk = "🔑" if g.get("has_perms") else "⚠️"
        lines.append(f"├ {st}{pk} <b>{g.get('title', '?')}</b>\n"
                     f"│  <code>{g['cid']}</code> · {d}")
    return "\n".join(lines) + f"\n{SEP}"

def t_stat(s, title):
    last = (_dt.datetime.fromtimestamp(s.get("last_ts", 0)).strftime("%d.%m.%Y %H:%M")
            if s.get("last_ts") else "—")
    return (f"📊 <b>İstatistikler</b>\n{SEP}\n🏠 <b>{title}</b>\n\n"
            f"├ 🗑 Silinen: <b>{s.get('deleted', 0)}</b>\n"
            f"├ ⚠️ Uyarı: <b>{s.get('warned', 0)}</b>\n"
            f"├ 🚫 Ban: <b>{s.get('banned', 0)}</b>\n"
            f"└ 🕒 Son: <b>{last}</b>\n{SEP}")

# ══════════════════════════════════════
#  BOT
# ══════════════════════════════════════
bot     = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
_bot_id = None
_bc_w   = {}

def bot_id():
    global _bot_id
    if _bot_id is None: _bot_id = bot.get_me().id
    return _bot_id

def is_adm(cid, uid):
    try:
        m = bot.get_chat_member(cid, uid)
        return m.status in ("administrator", "creator")
    except: return False

def is_own(uid): return uid == OWNER_ID

def refresh_perm(cid):
    try:
        me  = bot.get_chat_member(cid, bot_id())
        has = (me.status in ("administrator", "creator") and
               getattr(me, "can_delete_messages", False))
        g_update(cid, has_perms=int(has), perm_ts=int(time.time()))
        L.info("[PERM] cid=%d → %s", cid, has)
        return has
    except Exception as e:
        L.warning("[PERM] cid=%d err=%s", cid, e)
        g_update(cid, has_perms=0, perm_ts=int(time.time()))
        return False

def perm_loop():
    while True:
        time.sleep(PERM_TICK)
        for g in g_active():
            try:
                me  = bot.get_chat_member(g["cid"], bot_id())
                has = (me.status in ("administrator", "creator") and
                       getattr(me, "can_delete_messages", False))
                g_update(g["cid"], has_perms=int(has), perm_ts=int(time.time()))
            except:
                g_update(g["cid"], has_perms=0)

def handle_viol(message, user, cats_found, grp):
    cid   = message.chat.id; uid = user.id
    name  = user.full_name or user.first_name or "Kullanıcı"
    wl    = grp.get("warn_limit", 3)
    wa    = grp.get("warn_actions", DEF_WA)
    nd    = grp.get("notif_del", 60)
    ntfy  = bool(grp.get("notify", 1))

    try:
        bot.delete_message(cid, message.message_id)
        L.info("[MOD] Silindi msg=%d cid=%d", message.message_id, cid)
    except Exception as e:
        L.error("[MOD] Silinemedi: %s", e); return

    s_inc(cid, "deleted")
    wc     = w_add(cid, uid, ", ".join(cats_found), message.message_id)
    s_inc(cid, "warned")
    action = best_action(wa, wc)
    do_action(bot, cid, uid, action)

    if ntfy:
        try:
            m = bot.send_message(cid, t_deleted(name, uid, cats_found, wc, wl))
            del_later(bot, cid, m.message_id, nd)
        except Exception as e:
            L.warning("[MOD] bildirim: %s", e)
        if action not in ("warn", "none"):
            try:
                m2 = bot.send_message(cid, t_action(name, uid, action, wc))
                del_later(bot, cid, m2.message_id, nd)
            except: pass

# ── Medya analiz ───────────────────────
def analyze(message, file_id, suffix, is_video):
    grp = g_get(message.chat.id)
    if not grp or not grp.get("has_perms"): return
    cats = grp.get("cats", DEF_CATS)

    def _run():
        path = None
        try:
            path = dl(bot, file_id, suffix)
            if is_video:
                ok, found = check_video_frames(path, cats)
            else:
                ok, found = parse(_se_post_file(path), cats)
            if ok:
                handle_viol(message, message.from_user, found, grp)
        except Exception as e:
            L.error("[ANALYZE] %s: %s", suffix, e)
        finally:
            rm(path)

    threading.Thread(target=_run, daemon=True).start()

# ── Komutlar ───────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg: Message):
    u = msg.from_user
    u_save(u.id, u.username, u.full_name)
    parts = msg.text.split(maxsplit=1)
    param = parts[1] if len(parts) > 1 else ""

    if param.startswith("s_"):
        try:
            tcid = int(param[2:])
            grp  = g_get(tcid)
            if grp and is_adm(tcid, u.id):
                bot.send_message(msg.chat.id, t_settings(grp),
                                 reply_markup=kb_set(tcid))
                return
        except Exception as e:
            L.warning("[START] deeplink: %s", e)

    if msg.chat.type != "private":
        grp = g_get(msg.chat.id)
        if not grp:
            g_save(msg.chat.id, msg.chat.title or "Grup")
            grp = g_get(msg.chat.id)
        has = refresh_perm(msg.chat.id)
        pl  = "✅ Mesaj silme yetkim var." if has else "⚠️ Mesaj silme yetkim yok!"
        txt = (f"🛡️ <b>{BOT_NAME}</b>\n{SEP}\n"
               f"Gruplardaki uygunsuz içerikleri AI ile tespit edip silerim.\n\n"
               f"<b>📦 Kontrol:</b> 🖼 Fotoğraf · 🎬 Video · 🎭 Sticker\n\n{pl}")
        try: bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
        kb  = kb_gs(msg.chat.id) if is_adm(msg.chat.id, u.id) else None
        sent = bot.send_message(msg.chat.id, txt, reply_markup=kb)
        del_later(bot, msg.chat.id, sent.message_id, 60)
        return

    if is_own(u.id):
        bot.send_message(msg.chat.id,
                         t_owner(u.full_name or u.first_name, s_global()),
                         reply_markup=kb_dm(True))
    else:
        bot.send_message(msg.chat.id,
                         t_dm(u.full_name or u.first_name),
                         reply_markup=kb_dm(False))

@bot.message_handler(commands=["settings"])
def cmd_settings(msg: Message):
    if msg.chat.type == "private":
        bot.reply_to(msg, "⚠️ Bu komutu bir <b>grupta</b> kullanın.")
        return
    if not is_adm(msg.chat.id, msg.from_user.id):
        try: bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
        return
    grp = g_get(msg.chat.id)
    if not grp: g_save(msg.chat.id, msg.chat.title or "Grup")
    refresh_perm(msg.chat.id)
    try: bot.delete_message(msg.chat.id, msg.message_id)
    except: pass
    bot.send_message(msg.chat.id,
                     "📍 <b>Ayarları nerede açmak istersiniz?</b>",
                     reply_markup=kb_gs(msg.chat.id))

@bot.message_handler(commands=["help"])
def cmd_help(msg: Message):
    bot.reply_to(msg,
        f"📖 <b>Komutlar</b>\n{SEP}\n"
        f"├ /start — Bot hakkında & başlat\n"
        f"├ /settings — Grup ayarları (yönetici)\n"
        f"└ /help — Bu menü")

@bot.message_handler(content_types=["new_chat_members"])
def on_join(msg: Message):
    for m in msg.new_chat_members:
        if m.id != bot_id(): continue
        cid = msg.chat.id
        g_save(cid, msg.chat.title or "Grup")
        has = refresh_perm(cid)
        bot.send_message(cid, t_added(msg.chat.title or "Grup"),
                         reply_markup=kb_gw(cid))
        if not has:
            bot.send_message(cid, t_no_perm())

@bot.message_handler(content_types=["photo"])
def on_photo(msg: Message):
    if msg.chat.type == "private": return
    analyze(msg, msg.photo[-1].file_id, ".jpg", False)

@bot.message_handler(content_types=["video"])
def on_video(msg: Message):
    if msg.chat.type == "private": return
    analyze(msg, msg.video.file_id, ".mp4", True)

@bot.message_handler(content_types=["sticker"])
def on_sticker(msg: Message):
    if msg.chat.type == "private": return
    s    = msg.sticker
    is_v = s.is_animated or s.is_video
    analyze(msg, s.file_id, ".webm" if is_v else ".webp", is_v)

@bot.message_handler(func=lambda m: (
    m.from_user.id in _bc_w and
    _bc_w[m.from_user.id].get("w") and
    m.chat.type == "private"))
def recv_bc(msg: Message):
    uid = msg.from_user.id
    if msg.text == "/iptal":
        _bc_w.pop(uid, None)
        bot.reply_to(msg, "❌ İptal edildi.")
        return
    _bc_w[uid] = {"w": False, "text": msg.text}
    bot.send_message(msg.chat.id,
        f"📢 <b>Önizleme</b>\n{SEP}\n{msg.text}\n{SEP}\n"
        f"<i>Tüm kullanıcılara gönderilsin mi?</i>",
        reply_markup=kb_bc())

# ── Callback ───────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def on_cb(call: CallbackQuery):
    d   = call.data; uid = call.from_user.id
    cid = call.message.chat.id; mid = call.message.message_id

    def ans(t="", alert=False):
        bot.answer_callback_query(call.id, t, show_alert=alert)
    def edit(text, kb=None):
        bot.edit_message_text(text, cid, mid, parse_mode="HTML", reply_markup=kb)
    def ekb(kb):
        bot.edit_message_reply_markup(cid, mid, reply_markup=kb)
    def adm(tc):
        if not is_adm(tc, uid): ans("🚫 Yalnızca yöneticiler!", True); return False
        return True
    def own():
        if not is_own(uid): ans("🚫 Yalnızca kurucu!", True); return False
        return True

    try:
        if d == "noop": ans(); return

        if d == "cx" or d.startswith("cx:"):
            ans()
            try: bot.delete_message(cid, mid)
            except: pass
            return

        if d == "about": ans(); edit(t_about(), kb_kapat()); return

        if d == "contact":
            ans()
            bot.send_message(cid, f"👤 <b>Sahip</b>\n{SEP}\n"
                             f"Bot sahibiyle iletişim için profiline git.")
            return

        # ── Ayarlar açma ─────────────────
        if d.startswith("sh:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            refresh_perm(tc); grp = g_get(tc)
            if not grp.get("has_perms"):
                ans(); edit(t_no_perm() + "\n\nYetki verdikten sonra tekrar deneyin.")
                return
            ans(); edit(t_settings(grp), kb_set(tc)); return

        if d.startswith("sd:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            ans(); grp = g_get(tc)
            try:
                bot.send_message(uid, t_settings(grp), reply_markup=kb_set(tc))
                try: bot.delete_message(cid, mid)
                except: pass
                bot.send_message(cid, "💌 Ayarlar DM'nize gönderildi!",
                                 reply_markup=kb_kapat())
            except telebot.apihelper.ApiTelegramException:
                edit("💌 DM'de açmak için önce bota <b>start</b> verin:", kb_drl(tc))
            return

        if d.startswith("bs:"):
            tc = int(d.split(":")[1]); ans()
            edit(t_settings(g_get(tc)), kb_set(tc)); return

        # ── Kategoriler ──────────────────
        if d.startswith("mc:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            grp = g_get(tc); ans()
            edit(f"🔍 <b>Kategori Ayarları</b>\n{SEP}\n"
                 f"Açmak/kapatmak istediğin kategoriye bas:",
                 kb_cats(tc, grp.get("cats", DEF_CATS))); return

        if d.startswith("tc:"):
            p = d.split(":"); tc = int(p[1]); ck = p[2]
            if not adm(tc): return
            grp = g_get(tc); cats = dict(grp.get("cats", DEF_CATS))
            cats[ck] = not cats.get(ck, True)
            g_update(tc, cats=cats); ans("✅ Güncellendi!")
            ekb(kb_cats(tc, cats)); return

        if d.startswith("ca:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            cats = {k: True for k in DEF_CATS}
            g_update(tc, cats=cats); ans("✅ Tümü açıldı!")
            ekb(kb_cats(tc, cats)); return

        if d.startswith("co:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            cats = {k: False for k in DEF_CATS}
            g_update(tc, cats=cats); ans("✅ Tümü kapatıldı!")
            ekb(kb_cats(tc, cats)); return

        # ── Uyarı Eylemleri ──────────────
        if d.startswith("mw:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            grp = g_get(tc); ans()
            edit(f"⚖️ <b>Uyarı Eylemleri</b>\n{SEP}\n"
                 f"Her uyarı için uygulanacak işlemi belirle:",
                 kb_warns(tc, grp.get("warn_actions", {}))); return

        if d.startswith("pa:"):
            p = d.split(":"); tc = int(p[1]); lvl = p[2]
            if not adm(tc): return
            ans(); edit(f"⚖️ <b>{lvl}. Uyarı için eylem seç:</b>",
                        kb_ap(tc, lvl)); return

        if d.startswith("sa:"):
            p = d.split(":"); tc = int(p[1]); lvl = p[2]; act = p[3]
            if not adm(tc): return
            grp = g_get(tc); wa = dict(grp.get("warn_actions", {}))
            wa[str(lvl)] = act; g_update(tc, warn_actions=wa); ans("✅ Güncellendi!")
            edit(f"⚖️ <b>Uyarı Eylemleri</b>\n{SEP}\n"
                 f"Her uyarı için uygulanacak işlemi belirle:",
                 kb_warns(tc, wa)); return

        # ── Silme Gecikmesi ──────────────
        if d.startswith("mt:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            grp = g_get(tc); ans()
            edit(f"⏱ <b>Silme Gecikmesi</b>\n{SEP}\n"
                 f"Tespit edilince kaç saniye sonra silinsin?\n"
                 f"<i>(0 = anında sil)</i>",
                 kb_timeout(tc, grp.get("del_timeout", 0))); return

        if d.startswith("st:"):
            p = d.split(":"); tc = int(p[1]); t = int(p[2])
            if not adm(tc): return
            g_update(tc, del_timeout=t); ans("✅ Güncellendi!")
            ekb(kb_timeout(tc, t)); return

        # ── Bildirimler ──────────────────
        if d.startswith("mn:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            grp = g_get(tc); ans()
            edit(f"🔔 <b>Bildirimler</b>\n{SEP}\n"
                 f"İçerik silinince gruba bildirim gönderilsin mi?",
                 kb_notify(tc, bool(grp.get("notify", 1)))); return

        if d.startswith("sn:"):
            p = d.split(":"); tc = int(p[1]); v = int(p[2])
            if not adm(tc): return
            g_update(tc, notify=v); ans("✅ Güncellendi!")
            ekb(kb_notify(tc, bool(v))); return

        # ── Bildirim Silme Süresi ────────
        if d.startswith("mnd:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            grp = g_get(tc); ans()
            edit(f"🗑 <b>Bildirim Mesajı Silme</b>\n{SEP}\n"
                 f"Bildirim mesajı kaç saniye sonra silinsin?",
                 kb_notif_del(tc, grp.get("notif_del", 60))); return

        if d.startswith("snd:"):
            p = d.split(":"); tc = int(p[1]); nd = int(p[2])
            if not adm(tc): return
            g_update(tc, notif_del=nd); ans("✅ Güncellendi!")
            ekb(kb_notif_del(tc, nd)); return

        # ── Uyarı Limiti ─────────────────
        if d.startswith("ml:"):
            tc = int(d.split(":")[1])
            if not adm(tc): return
            grp = g_get(tc); ans()
            edit(f"⚠️ <b>Uyarı Limiti</b>\n{SEP}\n"
                 f"Kaç uyarıda son eylem uygulanacak?",
                 kb_wl(tc, grp.get("warn_limit", 3))); return

        if d.startswith("sl:"):
            p = d.split(":"); tc = int(p[1]); wl = int(p[2])
            if not adm(tc): return
            g_update(tc, warn_limit=wl); ans("✅ Güncellendi!")
            ekb(kb_wl(tc, wl)); return

        # ── İstatistikler ────────────────
        if d.startswith("ms:"):
            tc = int(d.split(":")[1])
            grp = g_get(tc); s = s_get(tc); ans()
            edit(t_stat(s, grp.get("title", "Grup")),
                 kb_geri(f"bs:{tc}")); return

        # ══ Kurucu Paneli ════════════════
        if d == "op_p":
            if not own(): return
            ans(); edit(t_panel(s_global()), kb_panel()); return

        if d == "op_s":
            if not own(): return
            ans(); edit(t_panel(s_global()), kb_panel()); return

        if d.startswith("op_u:"):
            if not own(): return
            page = int(d.split(":")[1])
            rows, total = u_all(page); ans()
            edit(t_users(rows, page, total), kb_unav(page, total)); return

        if d.startswith("op_g:"):
            if not own(): return
            page = int(d.split(":")[1])
            rows, total = g_all(page); ans()
            edit(t_groups(rows, page, total), kb_gnav(page, total)); return

        if d == "op_bc":
            if not own(): return
            ans(); _bc_w[uid] = {"w": True}
            bot.send_message(cid,
                f"📢 <b>Duyuru Metni Yaz</b>\n{SEP}\n"
                f"Göndermek istediğin metni yaz.\n"
                f"<i>İptal: /iptal</i>"); return

        if d == "op_bcs":
            if not own(): return
            text = _bc_w.get(uid, {}).get("text", "")
            if not text: ans("⚠️ Metin yok!", True); return
            ans("📤 Gönderiliyor...")
            rows, total = u_all(page=1, per=99999)
            ok = 0
            for u_row in rows:
                try:
                    bot.send_message(u_row["uid"],
                        f"📢 <b>{BOT_NAME} Duyurusu</b>\n{SEP}\n{text}")
                    ok += 1
                except: pass
            _bc_w.pop(uid, None)
            edit(f"✅ <b>Gönderildi</b>\n{SEP}\n└ {ok}/{total} kullanıcı",
                 kb_geri("op_p")); return

        if d == "op_bcc":
            _bc_w.pop(uid, None); ans("❌ İptal.")
            edit("❌ İptal edildi.", kb_geri("op_p")); return

    except telebot.apihelper.ApiTelegramException as e:
        L.error("[CB] API: %s", e)
        try: ans("⚠️ Bir hata oluştu.", True)
        except: pass
    except Exception as e:
        L.error("[CB] Hata: %s", e)

# ══════════════════════════════════════
#  BAŞLAT
# ══════════════════════════════════════
if __name__ == "__main__":
    db_init()
    L.info("=" * 52)
    L.info("  %s %s", BOT_NAME, BOT_VER)
    L.info("  OWNER=%d  THRESHOLD=%.2f", OWNER_ID, THRESHOLD)
    L.info("  VID_INTERVAL=%ds  VID_MAX=%ds", VID_INTERVAL, VID_MAX_SEC)
    L.info("=" * 52)
    threading.Thread(target=perm_loop, daemon=True).start()
    L.info("✅ Bot başlatıldı.")
    bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
