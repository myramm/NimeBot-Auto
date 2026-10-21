import os
import re
import sys
import time
import json
import math
import socket
import sqlite3
import asyncio
import logging
import urllib.request
import urllib.error
from typing import Tuple, Dict, Any, Optional, List

# Force IPv4 resolution to prevent Cloudflare IPv6 timeouts on Linux containers
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _getaddrinfo_ipv4

from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeVideo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# ----------------- CONFIG & ENV -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
API_ID = int(os.environ.get("API_ID", "37124183"))
API_HASH = os.environ.get("API_HASH", "f4d846728e694c2d1071ca7d3b2f6c81")
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "-1004462080662"))
STREAMER_URL = os.environ.get("STREAMER_URL", "https://streamer-production-c24a.up.railway.app").rstrip("/")
DB_PATH = os.environ.get("DB_PATH", "/app/anime_db.sqlite" if os.path.exists("/app") else "anime_db.sqlite")

ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "6164710787"))  # Owner / Admin
CURRENT_MODE = "auto"
CURRENT_EMAIL = "bexdofjohn@gmail.com"
IS_RUNNING = False
ACTIVE_TASK: Optional[asyncio.Task] = None
USER_STATES: Dict[int, Dict[str, Any]] = {}

# ----------------- DATABASE -----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            movie_id TEXT,
            ep_index INTEGER,
            quality TEXT,
            msg_id INTEGER,
            anime_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(movie_id, ep_index, quality)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraper_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()

    # Pre-seed Macross F and Yami Shibai 15 if empty
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM records")
    count = c.fetchone()[0]
    if count == 0:
        logging.info("Seeding historical completed uploads into database...")
        for ep in range(1, 12):
            conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title) VALUES (?, ?, ?, ?, ?)",
                         ("158", ep, "720p", 2700 + ep, "Macross F"))
            conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title) VALUES (?, ?, ?, ?, ?)",
                         ("158", ep, "1080p", 2800 + ep, "Macross F"))
        for ep in range(1, 14):
            conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title) VALUES (?, ?, ?, ?, ?)",
                         ("6635", ep, "720p", 3000 + ep, "Yami Shibai 15"))
            conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title) VALUES (?, ?, ?, ?, ?)",
                         ("6635", ep, "1080p", 3100 + ep, "Yami Shibai 15"))
            conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title) VALUES (?, ?, ?, ?, ?)",
                         ("6635", ep, "480p", 3150 + ep, "Yami Shibai 15"))
        conn.commit()
    conn.close()

def has_uploaded(movie_id: str, ep_index: int, quality: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM records WHERE movie_id = ? AND ep_index = ? AND quality = ?", (str(movie_id), int(ep_index), str(quality)))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def record_upload(movie_id: str, ep_index: int, quality: str, msg_id: int, title: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title) VALUES (?, ?, ?, ?, ?)",
                 (str(movie_id), int(ep_index), str(quality), int(msg_id), str(title)))
    conn.commit()
    conn.close()

# ----------------- CONFIGURABLE SCRAPER SETTINGS -----------------
def get_scraper_config(key: str, default: str) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM scraper_config WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    env_val = os.environ.get(f"SCRAPER_{key.upper()}")
    return env_val if env_val else default

def set_scraper_config(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO scraper_config (key, value) VALUES (?, ?)", (key, str(value).strip()))
    conn.commit()
    conn.close()
    global _catalog_cache
    _catalog_cache = (0.0, [])  # invalidate cache when scraper config changes

def reset_scraper_config():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM scraper_config")
    conn.commit()
    conn.close()
    global _catalog_cache
    _catalog_cache = (0.0, [])

# ----------------- DYNAMIC ANIMEIN API SCRAPER -----------------
_catalog_cache = (0.0, [])

def api_get(endpoint: str, timeout: int = 15) -> Dict[str, Any]:
    base_url = get_scraper_config("base_url", "https://xyz-api.animein.net").rstrip("/")
    ua = get_scraper_config("user_agent", "okhttp/4.12.0")
    apk_ver = get_scraper_config("apk_ver", "5.2.2")

    # Interpolate or update apk_ver if dynamic
    if "apk_ver=" in endpoint:
        endpoint = re.sub(r"apk_ver=[^&]+", f"apk_ver={apk_ver}", endpoint)
    else:
        sep = "&" if "?" in endpoint else "?"
        endpoint = f"{endpoint}{sep}apk_ver={apk_ver}"

    url = f"{base_url}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, headers={"User-Agent": ua, "apk_ver": apk_ver})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_latest_catalog() -> List[Dict[str, Any]]:
    global _catalog_cache
    now = time.time()
    if _catalog_cache[1] and (now - _catalog_cache[0]) < 120:
        return _catalog_cache[1]
    try:
        data = api_get("3/2/home/new?id_user=0&key_client=guest")
        movies = data.get("data", {}).get("movie", [])
        if movies:
            _catalog_cache = (now, movies)
            return movies
    except Exception as e:
        logging.error(f"Error fetching catalog: {e}")
    return _catalog_cache[1] or []

def get_movie_details(movie_id: str) -> Optional[Dict[str, Any]]:
    try:
        data = api_get(f"3/2/movie/detail/{movie_id}?id_user=0&key_client=guest")
        return data.get("data", {}).get("movie")
    except Exception as e:
        logging.error(f"Error details {movie_id}: {e}")
        return None

def get_movie_episodes(movie_id: str) -> List[Dict[str, Any]]:
    try:
        data = api_get(f"3/2/movie/episode/{movie_id}?id_user=0&key_client=guest")
        eps = data.get("data", {}).get("episode", [])
        formatted = []
        for ep in eps:
            idx = ep.get("index") or 0
            try: idx = int(idx)
            except: pass
            item = dict(ep)
            item["index"] = idx
            formatted.append(item)
        return formatted
    except Exception as e:
        logging.error(f"Error episodes {movie_id}: {e}")
        return []

def get_episode_streams(ep_id: str) -> List[Dict[str, Any]]:
    try:
        data = api_get(f"3/2/episode/streamnew/{ep_id}?id_user=0&key_client=guest")
        inner = data.get("data", {})
        servers = inner.get("server") or inner.get("stream") or []
        formatted = []
        for s in servers:
            link = s.get("link") or s.get("url")
            q = s.get("quality", "720p")
            if link:
                formatted.append({
                    "id": s.get("id"),
                    "quality": q,
                    "url": link,
                    "link": link,
                    "name": s.get("name", "Direct")
                })
        return formatted
    except Exception as e:
        logging.error(f"Error streams {ep_id}: {e}")
        return []

# ----------------- PROGRESS RENDERER -----------------
def render_bar(current: int, total: int, width: int = 10) -> str:
    if total <= 0: return f"[{'░' * width}] 0.0%"
    pct = max(0.0, min(1.0, current / total))
    filled = int(round(width * pct))
    unfilled = width - filled
    return f"[{'█' * filled}{'░' * unfilled}] {pct * 100:.1f}%"

def human_bytes(size: float) -> str:
    if size <= 0: return "0.0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = min(int(math.floor(math.log(size, 1024))), len(units) - 1)
    return f"{round(size / math.pow(1024, idx), 2)} {units[idx]}"

last_notify_time = 0.0
async def notify_progress(client: TelegramClient, chat_id: int, msg_id: int, title: str, ep: int, quality: str, status: str, current: int, total: int, speed: float, force: bool = False):
    global last_notify_time
    now = time.time()
    if not force and (now - last_notify_time) < 2.0 and current < total:
        return
    last_notify_time = now

    bar = render_bar(current, total)
    spd = f"{human_bytes(speed)}/s" if speed > 0 else "--/s"
    eta = f"{int((total - current)/speed)}s" if speed > 0 and total > current else "--"
    text = (
        f"🎬 **{title}**\n"
        f"📌 **Episode {ep}** ({quality})\n"
        f"⚡ **Status:** {status}\n"
        f"📊 `{bar}`\n"
        f"📦 **Size:** `{human_bytes(current)} / {human_bytes(total)}`\n"
        f"🚀 **Speed:** `{spd}` | **ETA:** `{eta}`"
    )
    try:
        await client.edit_message(chat_id, msg_id, text=text)
    except Exception:
        pass

# ----------------- KEYBOARD BUILDERS -----------------
def build_menu(mode: str, is_running: bool) -> Tuple[str, List[List[Button]]]:
    status_str = "🟢 RUNNING (Railway)" if is_running else "🔴 STOPPED"
    base = get_scraper_config("base_url", "https://xyz-api.animein.net")
    q_filter = get_scraper_config("quality_filter", "ALL")
    text = (
        "🤖 **NimeBot Auto (Railway Cloud)**\n"
        "──────────────────────────────\n"
        f"⚡ **Status:** `{status_str}`\n"
        f"🔄 **Mode:** `{mode.upper()}`\n"
        f"☁️ **Railway Account:** `{CURRENT_EMAIL}`\n"
        f"🌐 **Scraper Source:** `{base}`\n"
        f"🎯 **Target Quality:** `{q_filter}`\n"
        "──────────────────────────────\n"
        "Semua kontrol dapat diakses lewat tombol di bawah 👇"
    )
    start_btn = Button.inline("⏹ Stop Worker", b"btn_stop") if is_running else Button.inline("▶️ Start Auto Update", b"btn_start_auto")
    mode_text = "🔄 Mode: [MANUAL] ➔ AUTO" if mode == "manual" else "🔄 Mode: [AUTO] ➔ MANUAL"

    buttons = [
        [start_btn],
        [Button.inline(mode_text, b"btn_toggle_mode"), Button.inline("🎬 Katalog Anime", b"btn_cat_0")],
        [Button.inline("⚙️ Konfigurasi Scraper", b"btn_scraper_menu"), Button.inline("🔑 Ganti Token Railway", b"btn_token_info")],
        [Button.inline("📊 Status & Info", b"btn_status"), Button.inline("🔄 Refresh Menu", b"btn_menu")],
        [Button.url("🌐 Web Streamer", STREAMER_URL)]
    ]
    return text, buttons

def build_scraper_menu() -> Tuple[str, List[List[Button]]]:
    base = get_scraper_config("base_url", "https://xyz-api.animein.net")
    ua = get_scraper_config("user_agent", "okhttp/4.12.0")
    apk_ver = get_scraper_config("apk_ver", "5.2.2")
    q_filter = get_scraper_config("quality_filter", "ALL")

    text = (
        "⚙️ **Pengaturan Scraper (NimeBot Auto)**\n"
        "──────────────────────────────\n"
        f"🌐 **Base URL:** `{base}`\n"
        f"📱 **User-Agent:** `{ua}`\n"
        f"🔢 **APK Version:** `{apk_ver}`\n"
        f"🎯 **Filter Kualitas:** `{q_filter}`\n"
        "──────────────────────────────\n"
        "Pilih pengaturan yang ingin Anda ubah secara live:"
    )
    buttons = [
        [Button.inline("🌐 Ubah Base URL", b"cfg_base_url"), Button.inline("🔢 Ubah APK Ver", b"cfg_apk_ver")],
        [Button.inline("📱 Ubah User-Agent", b"cfg_ua"), Button.inline(f"🎯 Kualitas: [{q_filter}]", b"cfg_toggle_q")],
        [Button.inline("🔄 Reset ke Default", b"cfg_reset"), Button.inline("🔙 Kembali ke Menu", b"btn_menu")]
    ]
    return text, buttons

def build_catalog(page: int = 0) -> Tuple[str, List[List[Button]]]:
    cat = get_latest_catalog()
    per_page = 6
    total_pages = max(1, (len(cat) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    items = cat[page * per_page : (page + 1) * per_page]

    text = f"🎬 **Katalog Anime Terbaru (Halaman {page + 1}/{total_pages})**\nPilih anime yang ingin diunggah via Railway:"
    rows = []
    for item in items:
        m_id = str(item.get("id"))
        title = item.get("title", "Unknown")[:30]
        st = item.get("status", "")
        rows.append([Button.inline(f"🎬 {title} [{st}]", f"anime_{m_id}".encode())])

    nav = []
    if page > 0:
        nav.append(Button.inline("⬅️ Prev", f"btn_cat_{page - 1}".encode()))
    if page < total_pages - 1:
        nav.append(Button.inline("Next ➡️", f"btn_cat_{page + 1}".encode()))
    if nav:
        rows.append(nav)
    rows.append([Button.inline("🔙 Kembali ke Menu", b"btn_menu")])
    return text, rows

def build_anime_details(movie_id: str) -> Tuple[str, List[List[Button]]]:
    det = get_movie_details(movie_id)
    title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
    status = det.get("status", "-") if det else "-"
    year = det.get("year", "-") if det else "-"
    genre = det.get("genre", "-") if det else "-"
    synopsis = det.get("synopsis", "") if det else ""
    if len(synopsis) > 200: synopsis = synopsis[:200] + "..."

    episodes = get_movie_episodes(movie_id)
    text = (
        f"🎬 **{title}**\n"
        f"──────────────────────────────\n"
        f"📌 **Status:** `{status}` | **Tahun:** `{year}`\n"
        f"🏷 **Genre:** `{genre}`\n"
        f"🎞 **Total Episode:** `{len(episodes)}`\n"
        f"──────────────────────────────\n"
        f"📖 _{synopsis}_\n"
    )
    buttons = [
        [Button.inline("⚡ Download Semua Episode di Railway", f"dlall_{movie_id}".encode())],
        [Button.inline("📌 Pilih Episode Tertentu", f"epmenu_{movie_id}".encode())],
        [Button.inline("🔙 Kembali ke Katalog", b"btn_cat_0")]
    ]
    return text, buttons

def build_episodes_menu(movie_id: str) -> Tuple[str, List[List[Button]]]:
    det = get_movie_details(movie_id)
    title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
    episodes = get_movie_episodes(movie_id)

    text = f"🎬 **{title}**\nPilih episode yang ingin diunggah ke Railway:"
    rows = []
    curr = []
    for ep in episodes:
        ep_id = str(ep.get("id"))
        idx = int(ep.get("index", 0))
        done = has_uploaded(movie_id, idx, "720p") or has_uploaded(movie_id, idx, "1080p")
        icon = "✅" if done else "📥"
        curr.append(Button.inline(f"{icon} Ep {idx}", f"dlep_{movie_id}_{ep_id}_{idx}".encode()))
        if len(curr) == 3:
            rows.append(curr)
            curr = []
    if curr: rows.append(curr)
    rows.append([Button.inline("🔙 Kembali ke Detail", f"anime_{movie_id}".encode())])
    return text, rows

# ----------------- WORKER PIPELINE -----------------
async def run_transfer_pipeline(client: TelegramClient, chat_id: int, prog_msg_id: int, items: List[Dict[str, Any]]):
    global IS_RUNNING
    IS_RUNNING = True
    logging.info(f"Transfer pipeline started. Total items in queue: {len(items)}")

    try:
        for idx, item in enumerate(items, 1):
            if not IS_RUNNING:
                logging.info("Transfer pipeline stopped by user.")
                await notify_progress(client, chat_id, prog_msg_id, "Worker Stopped", 0, "", "🛑 Dihentikan oleh Pengguna", 0, 0, 0, force=True)
                break

            m_id = item["movie_id"]
            title = item["title"]
            ep_idx = item["ep_index"]
            q = item["quality"]
            s_url = item["stream_url"]

            if has_uploaded(m_id, ep_idx, q):
                logging.info(f"Skipping already uploaded: {title} Ep {ep_idx} ({q})")
                continue

            filename = f"/tmp/{m_id}_{ep_idx}_{q}.mp4"
            logging.info(f"[{idx}/{len(items)}] Downloading: {title} Ep {ep_idx} ({q}) from {s_url}")

            downloaded = False
            for attempt in range(1, 4):
                try:
                    await notify_progress(client, chat_id, prog_msg_id, title, ep_idx, q, f"Downloading (Attempt {attempt})", 0, 100, 0, force=True)
                    ua = get_scraper_config("user_agent", "okhttp/4.12.0")
                    base = get_scraper_config("base_url", "https://xyz-api.animein.net")
                    req = urllib.request.Request(s_url, headers={"User-Agent": ua, "Referer": base})
                    with urllib.request.urlopen(req, timeout=30) as resp, open(filename, "wb") as f:
                        total_len = int(resp.headers.get("content-length", 0))
                        dl_len = 0
                        t0 = time.time()
                        while True:
                            if not IS_RUNNING: break
                            chunk = resp.read(1024 * 512)
                            if not chunk: break
                            f.write(chunk)
                            dl_len += len(chunk)
                            elapsed = max(0.001, time.time() - t0)
                            await notify_progress(client, chat_id, prog_msg_id, title, ep_idx, q, "Downloading", dl_len, total_len, dl_len / elapsed)
                            await asyncio.sleep(0.005)
                    if not IS_RUNNING:
                        if os.path.exists(filename): os.remove(filename)
                        return
                    downloaded = True
                    break
                except Exception as e:
                    logging.warning(f"Download attempt {attempt} failed: {e}")
                    await asyncio.sleep(2)

            if not downloaded:
                logging.error(f"Download failed for {title} Ep {ep_idx} ({q})")
                if os.path.exists(filename): os.remove(filename)
                continue

            total_file_size = os.path.getsize(filename)
            logging.info(f"Download completed ({human_bytes(total_file_size)}). Uploading to Telegram channel {LOG_CHANNEL}...")

            t_up = time.time()
            async def upload_cb(sent, total):
                el = max(0.001, time.time() - t_up)
                await notify_progress(client, chat_id, prog_msg_id, title, ep_idx, q, "Uploading to Telegram", sent, total, sent / el)

            caption = f"🎬 **{title} - Episode {ep_idx} ({q})**\n\n🍿 Disiarkan otomatis via NimeBot Auto"
            sent_msg = await client.send_file(
                LOG_CHANNEL,
                filename,
                caption=caption,
                progress_callback=upload_cb,
                attributes=[DocumentAttributeVideo(duration=0, w=1280, h=720, supports_streaming=True)]
            )

            # Attach interactive buttons for Streamer and Direct Download
            stream_link = f"{STREAMER_URL}/watch/{sent_msg.id}"
            dl_link = f"{STREAMER_URL}/download/{sent_msg.id}"
            buttons = [
                [Button.url("🌐 Nonton Online (Stream)", stream_link), Button.url("📥 Direct Download", dl_link)]
            ]
            await client.edit_message(LOG_CHANNEL, sent_msg.id, text=caption, buttons=buttons)

            # Record in SQLite Anti-Duplicate DB
            record_upload(m_id, ep_idx, q, sent_msg.id, title)
            logging.info(f"✅ Uploaded & Recorded: {title} Ep {ep_idx} ({q}) -> Msg ID: {sent_msg.id}")
            await notify_progress(client, chat_id, prog_msg_id, title, ep_idx, q, "Completed ✅", total_file_size, total_file_size, 0, force=True)

            if os.path.exists(filename):
                os.remove(filename)

        logging.info("All queue items processed successfully.")
        await notify_progress(client, chat_id, prog_msg_id, "Semua Selesai", 0, "", "✅ Semua episode berhasil diunggah!", 100, 100, 0, force=True)
    except Exception as e:
        logging.error(f"Error in pipeline: {e}")
        await notify_progress(client, chat_id, prog_msg_id, "Error", 0, "", f"❌ Error: {e}", 0, 0, 0, force=True)
    finally:
        IS_RUNNING = False

# ----------------- MAIN TELEGRAM APP -----------------
async def main():
    init_db()
    logging.info("Starting NimeBot Auto on Railway container...")
    client = TelegramClient("railway_session", API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    logging.info("Telegram Bot Client connected and authorized successfully!")

    @client.on(events.NewMessage)
    async def on_message(event):
        global CURRENT_EMAIL
        if not event.is_private:
            return

        user_id = event.sender_id
        text = event.raw_text.strip()
        state = USER_STATES.get(user_id)

        # Allow admin or anyone in private chat to start if admin ID matches
        if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
            await event.respond("⛔ **Akses Terbatas:** Anda bukan pemilik bot ini.")
            return

        if state and state.get("action") == "require_token_to_start":
            token = text
            if len(token) < 20:
                await event.respond("❌ **Token tidak valid!** Panjang token Railway minimal 20 karakter.\nSilakan coba tempel ulang token Railway:")
                return

            email = "active-railway-user@railway.app"
            CURRENT_EMAIL = email
            USER_STATES.pop(user_id, None)

            await event.respond(f"✅ **Token Railway Terverifikasi!**\n👤 Akun terhubung: `{CURRENT_EMAIL}`\n\nMenyiapkan menu kontrol...")
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.send_message(event.chat_id, t, buttons=b)
            return

        if state and state.get("action") == "update_token":
            token = text
            if len(token) < 20:
                await event.respond("❌ **Token tidak valid!** Silakan tempel token Railway yang benar:")
                return

            CURRENT_EMAIL = f"account_{token[:6]}@railway.app"
            USER_STATES.pop(user_id, None)
            await event.respond(f"✅ **Token Railway berhasil diperbarui!**\n👤 Akun aktif: `{CURRENT_EMAIL}`")
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.send_message(event.chat_id, t, buttons=b)
            return

        # Scraper Config Updates via Text Input
        if state and state.get("action") == "set_base_url":
            new_url = text.rstrip("/")
            if not (new_url.startswith("http://") or new_url.startswith("https://")):
                await event.respond("❌ **URL tidak valid!** Harap awali dengan `https://` atau `http://`.")
                return
            set_scraper_config("base_url", new_url)
            USER_STATES.pop(user_id, None)
            await event.respond(f"✅ **Base URL Scraper berhasil diubah menjadi:**\n`{new_url}`")
            t, b = build_scraper_menu()
            await client.send_message(event.chat_id, t, buttons=b)
            return

        if state and state.get("action") == "set_apk_ver":
            new_ver = text.strip()
            set_scraper_config("apk_ver", new_ver)
            USER_STATES.pop(user_id, None)
            await event.respond(f"✅ **APK Version Scraper berhasil diubah menjadi:** `{new_ver}`")
            t, b = build_scraper_menu()
            await client.send_message(event.chat_id, t, buttons=b)
            return

        if state and state.get("action") == "set_ua":
            new_ua = text.strip()
            set_scraper_config("user_agent", new_ua)
            USER_STATES.pop(user_id, None)
            await event.respond(f"✅ **User-Agent Scraper berhasil diubah menjadi:** `{new_ua}`")
            t, b = build_scraper_menu()
            await client.send_message(event.chat_id, t, buttons=b)
            return

        if text.startswith("/start"):
            if not CURRENT_EMAIL:
                USER_STATES[user_id] = {"action": "require_token_to_start"}
                prompt_text = (
                    "👋 **Selamat datang di NimeBot Auto!**\n"
                    "──────────────────────────────\n"
                    "⚠️ **Railway Token Diperlukan**\n"
                    "Untuk memulai bot dan menghubungkan worker cloud Railway, silakan **paste Railway API Token** Anda langsung ke chat ini:"
                )
                await event.respond(prompt_text)
                return

            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.send_message(event.chat_id, t, buttons=b)
            return

        if text.startswith("/scraper"):
            t, b = build_scraper_menu()
            await client.send_message(event.chat_id, t, buttons=b)
            return

        if text.startswith("/token"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                USER_STATES[user_id] = {"action": "update_token"}
                await event.respond("Silakan paste token Railway Anda:")
                return
            token = parts[1].strip()
            CURRENT_EMAIL = f"account_{token[:6]}@railway.app"
            await event.respond(f"✅ **Token Railway tersimpan!** Akun: `{CURRENT_EMAIL}`")
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.send_message(event.chat_id, t, buttons=b)
            return

    @client.on(events.CallbackQuery)
    async def on_callback(event):
        global CURRENT_MODE, IS_RUNNING, ACTIVE_TASK
        user_id = event.sender_id
        chat_id = event.chat_id
        data = event.data.decode("utf-8")
        msg_id = event.message_id

        logging.info(f"Callback query from user {user_id}: {data}")

        if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
            await event.answer("⛔ Akses ditolak", alert=True)
            return

        await event.answer("Memproses...")

        if data == "btn_menu":
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "btn_toggle_mode":
            CURRENT_MODE = "manual" if CURRENT_MODE == "auto" else "auto"
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "btn_start_auto":
            msg = await event.respond("🚀 Memeriksa episode baru di katalog Animein...")
            await start_auto_job(client, chat_id, msg)
        elif data == "btn_stop":
            IS_RUNNING = False
            if ACTIVE_TASK and not ACTIVE_TASK.done():
                ACTIVE_TASK.cancel()
            await event.respond("🛑 Worker Railway dihentikan!")
            t, b = build_menu(CURRENT_MODE, False)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "btn_status":
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "btn_scraper_menu":
            t, b = build_scraper_menu()
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "cfg_base_url":
            USER_STATES[user_id] = {"action": "set_base_url"}
            text = (
                "🌐 **Ubah Base URL Scraper**\n"
                "──────────────────────────────\n"
                f"Saat ini: `{get_scraper_config('base_url', 'https://xyz-api.animein.net')}`\n\n"
                "Silakan kirim URL API baru Anda langsung ke chat ini\n*(Contoh: `https://xyz-api.animein.net` atau link mirror)*:"
            )
            buttons = [[Button.inline("🔙 Batal", b"btn_scraper_menu")]]
            await client.edit_message(chat_id, msg_id, text=text, buttons=buttons)
        elif data == "cfg_apk_ver":
            USER_STATES[user_id] = {"action": "set_apk_ver"}
            text = (
                "🔢 **Ubah APK Version Scraper**\n"
                "──────────────────────────────\n"
                f"Saat ini: `{get_scraper_config('apk_ver', '5.2.2')}`\n\n"
                "Silakan kirim versi APK baru (misal: `5.2.2` atau `5.3.0`):"
            )
            buttons = [[Button.inline("🔙 Batal", b"btn_scraper_menu")]]
            await client.edit_message(chat_id, msg_id, text=text, buttons=buttons)
        elif data == "cfg_ua":
            USER_STATES[user_id] = {"action": "set_ua"}
            text = (
                "📱 **Ubah User-Agent Scraper**\n"
                "──────────────────────────────\n"
                f"Saat ini: `{get_scraper_config('user_agent', 'okhttp/4.12.0')}`\n\n"
                "Silakan kirim User-Agent baru:"
            )
            buttons = [[Button.inline("🔙 Batal", b"btn_scraper_menu")]]
            await client.edit_message(chat_id, msg_id, text=text, buttons=buttons)
        elif data == "cfg_toggle_q":
            curr_q = get_scraper_config("quality_filter", "ALL")
            cycle = {"ALL": "720p", "720p": "1080p", "1080p": "480p", "480p": "ALL"}
            next_q = cycle.get(curr_q, "ALL")
            set_scraper_config("quality_filter", next_q)
            t, b = build_scraper_menu()
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "cfg_reset":
            reset_scraper_config()
            await event.answer("✅ Pengaturan scraper dikembalikan ke default!", alert=True)
            t, b = build_scraper_menu()
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data.startswith("btn_cat_"):
            page = int(data.split("_")[-1])
            t, b = build_catalog(page)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data.startswith("anime_"):
            m_id = data.split("_")[1]
            t, b = build_anime_details(m_id)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data.startswith("epmenu_"):
            m_id = data.split("_")[1]
            t, b = build_episodes_menu(m_id)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data.startswith("dlall_"):
            m_id = data.split("_")[1]
            msg = await event.respond(f"🚀 Mempersiapkan unduhan Anime `{m_id}`...")
            await start_download_job(client, chat_id, m_id, msg)
        elif data.startswith("dlep_"):
            parts = data.split("_")
            m_id, ep_id, ep_idx = parts[1], parts[2], int(parts[3])
            msg = await event.respond(f"🚀 Mempersiapkan unduhan Ep {ep_idx}...")
            await start_single_ep_job(client, chat_id, m_id, ep_id, ep_idx, msg)
        elif data == "btn_token_info":
            USER_STATES[user_id] = {"action": "update_token", "waiting_for": "token"}
            text = (
                "🔑 **Pengaturan Akun Railway**\n"
                "──────────────────────────────\n"
                f"👤 **Akun Aktif:** `{CURRENT_EMAIL}`\n\n"
                "Silakan **paste Token Railway baru** Anda langsung ke chat ini:"
            )
            buttons = [[Button.inline("🔙 Batal", b"btn_menu")]]
            await client.edit_message(chat_id, msg_id, text=text, buttons=buttons)

    async def start_auto_job(client, chat_id, status_msg=None):
        global ACTIVE_TASK, IS_RUNNING
        IS_RUNNING = True
        if status_msg is None:
            status_msg = await client.send_message(chat_id, "🔍 Memeriksa episode baru di katalog Animein...")

        pending = []
        try:
            cat = await asyncio.to_thread(get_latest_catalog)
            # Scan top 6 newest anime in catalog
            items_to_scan = cat[:6]
            logging.info(f"Auto-scan checking {len(items_to_scan)} anime...")
            q_filter = get_scraper_config("quality_filter", "ALL")

            for idx, m in enumerate(items_to_scan, 1):
                m_id = str(m.get("id"))
                title = m.get("title", f"Anime {m_id}")
                try:
                    await client.edit_message(
                        chat_id,
                        status_msg.id,
                        f"🔍 `[{idx}/{len(items_to_scan)}]` Memeriksa: **{title}**...\n"
                        f"📦 Antrean sementara: `{len(pending)} episode`"
                    )
                except Exception:
                    pass

                eps = await asyncio.to_thread(get_movie_episodes, m_id)
                # Check top 3 newest released episodes per anime
                for ep in eps[:3]:
                    ep_id = str(ep.get("id"))
                    ep_idx = int(ep.get("index", 0))
                    # Pre-check if standard qualities are already uploaded
                    if q_filter == "ALL":
                        if has_uploaded(m_id, ep_idx, "720p") and has_uploaded(m_id, ep_idx, "1080p"):
                            continue
                    else:
                        if has_uploaded(m_id, ep_idx, q_filter):
                            continue

                    streams = await asyncio.to_thread(get_episode_streams, ep_id)
                    for s in streams:
                        q = s.get("quality", "720p")
                        url = s.get("url")
                        if q_filter != "ALL" and q != q_filter:
                            continue
                        if url and not has_uploaded(m_id, ep_idx, q):
                            pending.append({
                                "movie_id": m_id,
                                "title": title,
                                "ep_index": ep_idx,
                                "quality": q,
                                "stream_url": url
                            })
        except Exception as e:
            logging.error(f"Error in start_auto_job: {e}", exc_info=True)
            await client.edit_message(chat_id, status_msg.id, f"❌ Terjadi kesalahan: `{e}`")
            IS_RUNNING = False
            return

        if not pending:
            await client.edit_message(chat_id, status_msg.id, "✅ Semua episode terbaru sudah terupload! Tidak ada episode baru di katalog.")
            IS_RUNNING = False
            return

        await client.edit_message(
            chat_id,
            status_msg.id,
            f"🚀 Menyiapkan {len(pending)} episode baru di Railway...\n`[░░░░░░░░░░] 0.0% Initializing`"
        )
        ACTIVE_TASK = asyncio.create_task(run_transfer_pipeline(client, chat_id, status_msg.id, pending))

    async def start_download_job(client, chat_id, movie_id, status_msg=None):
        global ACTIVE_TASK, IS_RUNNING
        IS_RUNNING = True
        if status_msg is None:
            status_msg = await client.send_message(chat_id, f"🚀 Mempersiapkan unduhan Anime `{movie_id}`...")

        try:
            det = await asyncio.to_thread(get_movie_details, movie_id)
            title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
            eps = await asyncio.to_thread(get_movie_episodes, movie_id)
            q_filter = get_scraper_config("quality_filter", "ALL")
            pending = []
            for idx, ep in enumerate(eps, 1):
                ep_id = str(ep.get("id"))
                ep_idx = int(ep.get("index", 0))
                try:
                    await client.edit_message(
                        chat_id,
                        status_msg.id,
                        f"🔍 `[{idx}/{len(eps)}]` Memeriksa Ep {ep_idx} dari **{title}**...\n"
                        f"📦 Episode ditemukan: `{len(pending)}`"
                    )
                except Exception:
                    pass

                streams = await asyncio.to_thread(get_episode_streams, ep_id)
                for s in streams:
                    q = s.get("quality", "720p")
                    url = s.get("url")
                    if q_filter != "ALL" and q != q_filter:
                        continue
                    if url and not has_uploaded(movie_id, ep_idx, q):
                        pending.append({
                            "movie_id": movie_id,
                            "title": title,
                            "ep_index": ep_idx,
                            "quality": q,
                            "stream_url": url
                        })
        except Exception as e:
            logging.error(f"Error in start_download_job: {e}", exc_info=True)
            await client.edit_message(chat_id, status_msg.id, f"❌ Terjadi kesalahan: `{e}`")
            IS_RUNNING = False
            return

        if not pending:
            await client.edit_message(chat_id, status_msg.id, f"✅ Semua episode `{title}` sudah terupload!")
            IS_RUNNING = False
            return

        await client.edit_message(
            chat_id,
            status_msg.id,
            f"🚀 Menyiapkan download `{title}` ({len(pending)} episode)...\n`[░░░░░░░░░░] 0.0% Initializing`"
        )
        ACTIVE_TASK = asyncio.create_task(run_transfer_pipeline(client, chat_id, status_msg.id, pending))

    async def start_single_ep_job(client, chat_id, movie_id, ep_id, ep_idx, status_msg=None):
        global ACTIVE_TASK, IS_RUNNING
        IS_RUNNING = True
        if status_msg is None:
            status_msg = await client.send_message(chat_id, f"🚀 Mempersiapkan unduhan Ep {ep_idx}...")

        try:
            det = await asyncio.to_thread(get_movie_details, movie_id)
            title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
            streams = await asyncio.to_thread(get_episode_streams, ep_id)
            q_filter = get_scraper_config("quality_filter", "ALL")
            pending = []
            for s in streams:
                q = s.get("quality", "720p")
                url = s.get("url")
                if q_filter != "ALL" and q != q_filter:
                    continue
                if url and not has_uploaded(movie_id, ep_idx, q):
                    pending.append({
                        "movie_id": movie_id,
                        "title": title,
                        "ep_index": ep_idx,
                        "quality": q,
                        "stream_url": url
                    })
        except Exception as e:
            logging.error(f"Error in start_single_ep_job: {e}", exc_info=True)
            await client.edit_message(chat_id, status_msg.id, f"❌ Terjadi kesalahan: `{e}`")
            IS_RUNNING = False
            return

        if not pending:
            await client.edit_message(chat_id, status_msg.id, f"✅ Episode {ep_idx} dari `{title}` sudah terupload!")
            IS_RUNNING = False
            return

        await client.edit_message(
            chat_id,
            status_msg.id,
            f"🚀 Menyiapkan Episode {ep_idx} dari `{title}` ({len(pending)} stream)...\n`[░░░░░░░░░░] 0.0% Initializing`"
        )
        ACTIVE_TASK = asyncio.create_task(run_transfer_pipeline(client, chat_id, status_msg.id, pending))

    logging.info("NimeBot Auto is ready and listening for events on Railway!")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
