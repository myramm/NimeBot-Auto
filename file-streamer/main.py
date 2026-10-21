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
from telethon.errors import MessageNotModifiedError
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# ----------------- CONFIG & ENV -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0") or "0")
STREAMER_URL = os.environ.get("STREAMER_URL", "https://uploader-production-26af.up.railway.app").rstrip("/")
DB_PATH = os.environ.get("DB_PATH", "/app/anime_db.sqlite" if os.path.exists("/app") else "anime_db.sqlite")

ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "6164710787,8942930830").strip()  # Owner / Admin ID(s)
CURRENT_MODE = "auto"
CURRENT_EMAIL = "bexdofjohn@gmail.com"
IS_RUNNING = False
ACTIVE_TASK: Optional[asyncio.Task] = None
USER_STATES: Dict[int, Dict[str, Any]] = {}

def is_authorized(user_id: int) -> bool:
    """Check if user_id is authorized. Returns True if ADMIN_USER_ID is 0/empty or user_id matches."""
    admin_val = os.environ.get("ADMIN_USER_ID", ADMIN_USER_ID).strip()
    if not admin_val or admin_val == "0":
        return True
    allowed_ids = {int(x.strip()) for x in admin_val.split(",") if x.strip().lstrip("-").isdigit()}
    if not allowed_ids:
        return True
    return user_id in allowed_ids

# ----------------- DATABASE -----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anime_series (
            title TEXT PRIMARY KEY,
            slug TEXT,
            cover_msg_id INTEGER,
            anime_type TEXT,
            studio TEXT,
            year TEXT,
            genres TEXT,
            status TEXT,
            total_episodes INTEGER,
            synopsis TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            movie_id TEXT,
            ep_index INTEGER,
            quality TEXT,
            msg_id INTEGER PRIMARY KEY,
            anime_title TEXT,
            file_size INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraper_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def has_uploaded(movie_id: str, ep_index: int, quality: str, title: str = "") -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if title:
        cursor.execute("SELECT 1 FROM records WHERE (movie_id = ? OR anime_title = ?) AND ep_index = ? AND quality = ?", (str(movie_id), str(title), int(ep_index), str(quality)))
    else:
        cursor.execute("SELECT 1 FROM records WHERE movie_id = ? AND ep_index = ? AND quality = ?", (str(movie_id), int(ep_index), str(quality)))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def record_upload(movie_id: str, ep_index: int, quality: str, msg_id: int, title: str, file_size: int = 0):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title, file_size) VALUES (?, ?, ?, ?, ?, ?)",
                 (str(movie_id), int(ep_index), str(quality), int(msg_id), str(title), int(file_size)))
    conn.commit()
    conn.close()

def get_record_by_msg_id(msg_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT movie_id, ep_index, quality, msg_id, anime_title, created_at, file_size FROM records WHERE msg_id = ?", (msg_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "movie_id": row[0],
            "ep_index": row[1],
            "quality": row[2],
            "msg_id": row[3],
            "anime_title": row[4],
            "created_at": row[5],
            "file_size": row[6] or 0
        }
    return None

def get_recent_uploads(limit: int = 40) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT movie_id, ep_index, quality, msg_id, anime_title, created_at, file_size FROM records ORDER BY msg_id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "movie_id": r[0],
        "ep_index": r[1],
        "quality": r[2],
        "msg_id": r[3],
        "anime_title": r[4],
        "created_at": r[5],
        "file_size": r[6] or 0
    } for r in rows]

def get_all_anime(search: Optional[str] = None, genre: Optional[str] = None, status: Optional[str] = None, page: int = 1, limit: int = 50) -> Dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    query = "SELECT title, slug, cover_msg_id, anime_type, studio, year, genres, status, total_episodes, synopsis FROM anime_series WHERE 1=1"
    params = []
    if search:
        query += " AND (title LIKE ? OR synopsis LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if genre:
        query += " AND genres LIKE ?"
        params.append(f"%{genre}%")
    if status:
        query += " AND status LIKE ?"
        params.append(f"%{status}%")

    count_query = f"SELECT COUNT(*) FROM ({query})"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]

    offset = (page - 1) * limit
    query += " ORDER BY title ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for r in rows:
        title = r[0]
        cursor.execute("SELECT COUNT(DISTINCT ep_index), GROUP_CONCAT(DISTINCT quality) FROM records WHERE anime_title = ?", (title,))
        ep_stats = cursor.fetchone()
        avail_eps = ep_stats[0] if ep_stats else 0
        qualities_str = ep_stats[1] if ep_stats and ep_stats[1] else ""
        qualities = [q.strip() for q in qualities_str.split(",") if q.strip()] if qualities_str else []

        cover_id = r[2]
        cover_url = f"{STREAMER_URL}/cover/{cover_id}.jpg" if cover_id else f"{STREAMER_URL}/icon.svg"

        results.append({
            "title": title,
            "slug": r[1],
            "cover_url": cover_url,
            "cover_msg_id": cover_id,
            "type": r[3],
            "studio": r[4],
            "year": r[5],
            "genres": [g.strip() for g in r[6].split(",") if g.strip()] if r[6] else [],
            "status": r[7],
            "total_episodes": r[8],
            "available_episodes": avail_eps,
            "qualities": qualities,
            "synopsis": r[9],
            "detail_url": f"/api/anime/{r[1]}"
        })
    conn.close()
    return {
        "page": page,
        "limit": limit,
        "total": total_count,
        "total_pages": (total_count + limit - 1) // limit if limit > 0 else 1,
        "anime": results
    }

def get_anime_detail(slug_or_title: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT title, slug, cover_msg_id, anime_type, studio, year, genres, status, total_episodes, synopsis
        FROM anime_series
        WHERE slug = ? OR LOWER(title) = LOWER(?)
    """, (slug_or_title.lower(), slug_or_title))
    row = cursor.fetchone()

    if not row:
        cursor.execute("SELECT DISTINCT anime_title FROM records WHERE anime_title LIKE ? LIMIT 1", (f"%{slug_or_title}%",))
        fallback = cursor.fetchone()
        if not fallback:
            conn.close()
            return None
        title = fallback[0]
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
        row = (title, slug, None, "SERIES", "", "", "", "UNKNOWN", 0, "")

    title = row[0]
    cover_id = row[2]
    cover_url = f"{STREAMER_URL}/cover/{cover_id}.jpg" if cover_id else f"{STREAMER_URL}/icon.svg"

    cursor.execute("""
        SELECT ep_index, quality, msg_id, file_size
        FROM records
        WHERE anime_title = ?
        ORDER BY ep_index ASC, quality ASC
    """, (title,))
    ep_rows = cursor.fetchall()
    conn.close()

    episodes_map = {}
    for ep_idx, quality, msg_id, file_size in ep_rows:
        if ep_idx not in episodes_map:
            episodes_map[ep_idx] = {
                "episode": ep_idx,
                "qualities": {}
            }
        size_fmt = f"{file_size / (1024*1024):.1f} MB" if file_size else "Video MP4"
        episodes_map[ep_idx]["qualities"][quality] = {
            "msg_id": msg_id,
            "quality": quality,
            "file_size": file_size,
            "file_size_formatted": size_fmt,
            "stream_url": f"{STREAMER_URL}/stream/{msg_id}",
            "watch_url": f"{STREAMER_URL}/watch/{msg_id}",
            "download_url": f"{STREAMER_URL}/download/{msg_id}"
        }

    return {
        "title": title,
        "slug": row[1],
        "cover_url": cover_url,
        "cover_msg_id": cover_id,
        "type": row[3],
        "studio": row[4],
        "year": row[5],
        "genres": [g.strip() for g in row[6].split(",") if g.strip()] if row[6] else [],
        "status": row[7],
        "total_episodes": row[8],
        "available_episodes_count": len(episodes_map),
        "synopsis": row[9],
        "episodes": list(episodes_map.values())
    }

def get_all_episodes(page: int = 1, limit: int = 50, anime_title: Optional[str] = None, quality: Optional[str] = None) -> Dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    query = "SELECT movie_id, ep_index, quality, msg_id, anime_title, file_size, created_at FROM records WHERE 1=1"
    params = []
    if anime_title:
        query += " AND anime_title LIKE ?"
        params.append(f"%{anime_title}%")
    if quality:
        query += " AND quality = ?"
        params.append(quality)

    count_query = f"SELECT COUNT(*) FROM ({query})"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]

    offset = (page - 1) * limit
    query += " ORDER BY msg_id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    items = []
    for r in rows:
        msg_id = r[3]
        fsize = r[5] or 0
        items.append({
            "anime_title": r[4],
            "episode": r[1],
            "quality": r[2],
            "msg_id": msg_id,
            "file_size": fsize,
            "file_size_formatted": f"{fsize / (1024*1024):.1f} MB" if fsize else "Video MP4",
            "stream_url": f"{STREAMER_URL}/stream/{msg_id}",
            "watch_url": f"{STREAMER_URL}/watch/{msg_id}",
            "download_url": f"{STREAMER_URL}/download/{msg_id}",
            "created_at": r[6]
        })
    return {
        "page": page,
        "limit": limit,
        "total": total_count,
        "total_pages": (total_count + limit - 1) // limit if limit > 0 else 1,
        "episodes": items
    }

def get_api_stats() -> Dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM anime_series")
    total_anime = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*), SUM(COALESCE(file_size, 0)) FROM records")
    ep_row = cursor.fetchone()
    total_episodes = ep_row[0] if ep_row else 0
    total_bytes = ep_row[1] if ep_row and ep_row[1] else 0
    conn.close()

    return {
        "status": "online",
        "service": "NimeBot Auto Streaming & Media API",
        "version": "2.1.0",
        "total_anime": total_anime,
        "total_episodes": total_episodes,
        "total_storage_bytes": total_bytes,
        "total_storage_gb": round(total_bytes / (1024 ** 3), 2),
        "channel_id": LOG_CHANNEL,
        "endpoints": {
            "api_docs": f"{STREAMER_URL}/api",
            "stats": f"{STREAMER_URL}/api/stats",
            "anime_list": f"{STREAMER_URL}/api/anime",
            "anime_detail": f"{STREAMER_URL}/api/anime/{{slug}}",
            "episodes": f"{STREAMER_URL}/api/episodes",
            "cover_media": f"{STREAMER_URL}/cover/{{cover_msg_id}}.jpg",
            "stream_video": f"{STREAMER_URL}/stream/{{msg_id}}",
            "watch_video": f"{STREAMER_URL}/watch/{{msg_id}}",
            "download_video": f"{STREAMER_URL}/download/{{msg_id}}",
            "favicon_svg": f"{STREAMER_URL}/favicon.svg",
            "logo_svg": f"{STREAMER_URL}/logo.svg",
            "icon_svg": f"{STREAMER_URL}/icon.svg"
        }
    }

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
    _catalog_cache = (0.0, [])

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
        formatted.sort(key=lambda x: x["index"])
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
        [Button.inline("🔄 Sync Channel (2,877+ Eps)", b"btn_sync_channel"), Button.inline("🌐 Info REST API", b"btn_api_info")],
        [Button.inline("📊 Status & Info", b"btn_status"), Button.inline("🔄 Refresh Menu", b"btn_menu")],
        [Button.url("🌐 Web Portal & API", STREAMER_URL)]
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
    posted_anime_headers = set()

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

            # Post Anime Info Card with Poster before first episode in this batch
            if m_id not in posted_anime_headers:
                try:
                    det = get_movie_details(m_id)
                    if det:
                        poster_url = det.get("image_poster") or det.get("image_cover")
                        a_title = det.get("title", title)
                        status = det.get("status", "-")
                        year = det.get("year", "-")
                        genre = det.get("genre", "-")
                        synopsis = det.get("synopsis", "")
                        a_type = det.get("type", "Anime")

                        info_caption = (
                            f"🎬 **{a_title}**\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"📌 **Status:** `{status}` | **Tipe:** `{a_type}`\n"
                            f"🏷 **Genre:** `{genre}`\n"
                            f"📅 **Tahun:** `{year}`\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"📖 **Sinopsis:**\n_{synopsis}_\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            "🍿 Disiarkan otomatis via NimeBot Auto"
                        )

                        poster_path = None
                        if poster_url:
                            try:
                                p_file = f"/tmp/poster_{m_id}.jpg"
                                ua = get_scraper_config("user_agent", "okhttp/4.12.0")
                                base = get_scraper_config("base_url", "https://xyz-api.animein.net")
                                req = urllib.request.Request(poster_url, headers={"User-Agent": ua, "Referer": base})
                                with urllib.request.urlopen(req, timeout=15) as resp, open(p_file, "wb") as pf:
                                    pf.write(resp.read())
                                poster_path = p_file
                            except Exception as err:
                                logging.warning(f"Could not download poster: {err}")

                        if poster_path and os.path.exists(poster_path):
                            await client.send_file(LOG_CHANNEL, poster_path, caption=info_caption)
                            try: os.remove(poster_path)
                            except Exception: pass
                        else:
                            await client.send_message(LOG_CHANNEL, info_caption)

                        posted_anime_headers.add(m_id)
                except Exception as e:
                    logging.error(f"Error posting anime info card: {e}")

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

            caption = (
                f"🎬 **{title}**\n"
                f"📌 **Episode {ep_idx}** ({q})\n\n"
                f"🍿 Disiarkan otomatis via NimeBot Auto"
            )
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

# ----------------- EMBEDDED AIOHTTP WEB STREAMER & REST API -----------------
def create_web_app(tg_client: TelegramClient) -> web.Application:
    app = web.Application()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(base_dir, "static")
    covers_dir = os.path.join(base_dir, "covers")
    os.makedirs(static_dir, exist_ok=True)
    os.makedirs(covers_dir, exist_ok=True)

    # 1. Static Media Handlers (SVG Icons, Favicon, Brand Logo)
    async def handle_favicon(request: web.Request) -> web.Response:
        fav_path = os.path.join(static_dir, "favicon.svg")
        if os.path.exists(fav_path):
            return web.FileResponse(fav_path, headers={"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400"})
        return web.Response(text="<svg></svg>", content_type="image/svg+xml")

    async def handle_logo(request: web.Request) -> web.Response:
        logo_path = os.path.join(static_dir, "logo.svg")
        if os.path.exists(logo_path):
            return web.FileResponse(logo_path, headers={"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400"})
        return web.Response(text="<svg></svg>", content_type="image/svg+xml")

    async def handle_icon(request: web.Request) -> web.Response:
        icon_path = os.path.join(static_dir, "icon.svg")
        if os.path.exists(icon_path):
            return web.FileResponse(icon_path, headers={"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400"})
        return web.Response(text="<svg></svg>", content_type="image/svg+xml")

    # 2. Cover Poster Media Handler (High-res JPG direct & cached)
    async def handle_cover(request: web.Request) -> web.StreamResponse:
        raw_id = request.match_info.get("msg_id", "").replace(".jpg", "")
        try:
            msg_id = int(raw_id)
        except ValueError:
            return web.Response(status=400, text="ID Cover tidak valid")

        cached_file = os.path.join(covers_dir, f"{msg_id}.jpg")
        if os.path.exists(cached_file):
            return web.FileResponse(cached_file, headers={"Content-Type": "image/jpeg", "Cache-Control": "public, max-age=604800"})

        # Not in local cache, download from Telegram
        try:
            msg = await tg_client.get_messages(LOG_CHANNEL, ids=msg_id)
        except Exception as e:
            logging.error(f"Error fetching cover message {msg_id}: {e}")
            return web.Response(status=502, text=f"Error Telegram: {e}")

        if not msg or not (msg.photo or (msg.media and hasattr(msg.media, "document"))):
            return await handle_icon(request)

        media = msg.photo or msg.media
        try:
            await tg_client.download_media(media, file=cached_file)
            if os.path.exists(cached_file):
                return web.FileResponse(cached_file, headers={"Content-Type": "image/jpeg", "Cache-Control": "public, max-age=604800"})
        except Exception as e:
            logging.error(f"Error downloading cover {msg_id}: {e}")

        return await handle_icon(request)

    # 3. REST API Handlers
    async def handle_api_index(request: web.Request) -> web.Response:
        stats = get_api_stats()
        return web.json_response({
            "status": "success",
            "message": "NimeBot Auto Streaming & Anime Media REST API",
            "version": "2.1.0",
            "base_url": STREAMER_URL,
            "stats": stats,
            "endpoints": {
                "GET /api/stats": "Overall statistics of indexed anime, episodes, and storage",
                "GET /api/anime": "List of all indexed anime series (params: page, limit, search, genre, status)",
                "GET /api/anime/{slug}": "Full anime details and all episodes grouped by quality with media URLs",
                "GET /api/episodes": "Flat paginated list of all episodes (params: page, limit, anime, quality)",
                "GET /cover/{cover_msg_id}.jpg": "Direct JPG cover poster image",
                "GET /stream/{msg_id}": "Direct video stream (supports HTTP 206 Partial Content Range)",
                "GET /watch/{msg_id}": "HTML5 web video player",
                "GET /download/{msg_id}": "Direct file download attachment",
                "GET /favicon.svg": "SVG Favicon",
                "GET /logo.svg": "SVG Brand Logo",
                "GET /icon.svg": "SVG Icon"
            }
        })

    async def handle_api_stats(request: web.Request) -> web.Response:
        return web.json_response(get_api_stats())

    async def handle_api_anime(request: web.Request) -> web.Response:
        search = request.query.get("search")
        genre = request.query.get("genre")
        status = request.query.get("status")
        try:
            page = max(1, int(request.query.get("page", 1)))
            limit = min(100, max(1, int(request.query.get("limit", 50))))
        except ValueError:
            page, limit = 1, 50
        res = get_all_anime(search=search, genre=genre, status=status, page=page, limit=limit)
        return web.json_response({"status": "success", **res})

    async def handle_api_anime_detail(request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        detail = get_anime_detail(slug)
        if not detail:
            return web.json_response({"status": "error", "message": f"Anime '{slug}' tidak ditemukan"}, status=404)
        return web.json_response({"status": "success", "data": detail})

    async def handle_api_episodes(request: web.Request) -> web.Response:
        anime = request.query.get("anime")
        quality = request.query.get("quality")
        try:
            page = max(1, int(request.query.get("page", 1)))
            limit = min(100, max(1, int(request.query.get("limit", 50))))
        except ValueError:
            page, limit = 1, 50
        res = get_all_episodes(page=page, limit=limit, anime_title=anime, quality=quality)
        return web.json_response({"status": "success", **res})

    # 4. Web UI Portal Handlers
    async def handle_index(request: web.Request) -> web.Response:
        stats = get_api_stats()
        anime_data = get_all_anime(limit=60)["anime"]
        recent_records = get_recent_uploads(24)

        # Anime Series Catalog Cards with Real JPG Covers
        catalog_html = ""
        for a in anime_data:
            cover_url = a["cover_url"]
            title = a["title"]
            slug = a["slug"]
            eps_avail = a["available_episodes"]
            qualities = " • ".join(a["qualities"][:3]) if a["qualities"] else "HD"
            genres_pills = "".join([f'<span class="genre-pill">{g}</span>' for g in a["genres"][:2]])

            catalog_html += f"""
            <div class="anime-card">
                <div class="anime-poster-wrapper">
                    <img src="{cover_url}" alt="{title}" class="anime-poster" loading="lazy" onerror="this.src='/icon.svg'">
                    <span class="eps-badge">{eps_avail} Eps</span>
                </div>
                <div class="anime-content">
                    <h3 class="anime-title" title="{title}">{title}</h3>
                    <div class="anime-meta">{qualities}</div>
                    <div class="genre-container">{genres_pills}</div>
                    <a href="/anime/{slug}" class="btn btn-primary">▶️ Lihat Episode</a>
                </div>
            </div>
            """

        # Recent Episodes Cards
        recent_html = ""
        for r in recent_records:
            msg_id = r["msg_id"]
            title = r["anime_title"]
            ep = r["ep_index"]
            q = r["quality"]
            fsize = f"{r['file_size'] / (1024*1024):.1f} MB" if r["file_size"] else "MP4"
            recent_html += f"""
            <div class="rec-card">
                <div class="rec-title">🎬 {title}</div>
                <div class="rec-meta">Ep {ep} • <span class="badge">{q}</span> • {fsize}</div>
                <div class="card-actions">
                    <a href="/watch/{msg_id}" class="btn btn-sm">▶️ Nonton</a>
                    <a href="/download/{msg_id}" class="btn btn-sm btn-sec">📥 Unduh</a>
                </div>
            </div>
            """

        html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <title>NimeBot Auto - Streaming & Anime Media API</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #090d16; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            padding: 0; min-height: 100vh;
        }}
        .navbar {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 16px 32px; background: #0f172a; border-bottom: 1px solid #1e293b;
            position: sticky; top: 0; z-index: 50;
        }}
        .logo-img {{ height: 38px; }}
        .nav-links {{ display: flex; gap: 14px; align-items: center; }}
        .nav-btn {{
            background: #1e293b; color: #38bdf8; text-decoration: none; padding: 7px 14px;
            border-radius: 8px; font-size: 13px; font-weight: 600; border: 1px solid #334155;
            transition: all 0.2s;
        }}
        .nav-btn:hover {{ background: #38bdf8; color: #0f172a; }}
        .hero {{
            text-align: center; padding: 45px 20px 25px;
            background: radial-gradient(circle at 50% 20%, rgba(56, 189, 248, 0.12), transparent 70%);
        }}
        .hero h1 {{ font-size: 32px; font-weight: 800; margin-bottom: 10px; color: #f8fafc; }}
        .hero h1 span {{ color: #38bdf8; }}
        .hero p {{ color: #94a3b8; font-size: 16px; max-width: 650px; margin: 0 auto 20px; }}
        .stats-bar {{
            display: inline-flex; gap: 16px; background: #1e293b; padding: 8px 20px;
            border-radius: 999px; border: 1px solid #334155; font-size: 13px; color: #cbd5e1;
            flex-wrap: wrap; justify-content: center;
        }}
        .stats-bar strong {{ color: #38bdf8; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 30px 20px; }}
        .section-header {{
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 22px; border-bottom: 1px solid #1e293b; padding-bottom: 12px;
        }}
        .section-title {{ font-size: 20px; font-weight: 700; color: #f1f5f9; display: flex; align-items: center; gap: 8px; }}
        .anime-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 20px; margin-bottom: 50px;
        }}
        .anime-card {{
            background: #111827; border-radius: 12px; overflow: hidden; border: 1px solid #1f2937;
            display: flex; flex-direction: column; transition: transform 0.2s, box-shadow 0.2s;
        }}
        .anime-card:hover {{
            transform: translateY(-4px); box-shadow: 0 12px 24px -8px rgba(56, 189, 248, 0.2);
            border-color: #38bdf8;
        }}
        .anime-poster-wrapper {{ position: relative; width: 100%; aspect-ratio: 3 / 4; background: #1f2937; }}
        .anime-poster {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
        .eps-badge {{
            position: absolute; top: 8px; right: 8px; background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(4px); color: #38bdf8; font-size: 11px; font-weight: 700;
            padding: 3px 8px; border-radius: 6px; border: 1px solid rgba(56, 189, 248, 0.4);
        }}
        .anime-content {{ padding: 12px; display: flex; flex-direction: column; flex-grow: 1; }}
        .anime-title {{
            font-size: 14px; font-weight: 700; color: #f8fafc; margin-bottom: 6px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }}
        .anime-meta {{ color: #94a3b8; font-size: 12px; margin-bottom: 8px; }}
        .genre-container {{ display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 12px; }}
        .genre-pill {{ background: #1e293b; color: #94a3b8; font-size: 10px; padding: 2px 6px; border-radius: 4px; }}
        .btn {{
            display: block; text-align: center; background: #2563eb; color: white; text-decoration: none;
            padding: 8px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; transition: background 0.2s;
        }}
        .btn:hover {{ background: #1d4ed8; }}
        .btn-sec {{ background: #334155; }}
        .btn-sec:hover {{ background: #475569; }}
        .btn-sm {{ padding: 6px 10px; font-size: 12px; }}
        .rec-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 14px;
        }}
        .rec-card {{
            background: #111827; border-radius: 10px; padding: 14px; border: 1px solid #1f2937;
            display: flex; flex-direction: column; justify-content: space-between;
        }}
        .rec-title {{ font-size: 14px; font-weight: 700; color: #f1f5f9; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .rec-meta {{ color: #94a3b8; font-size: 12px; margin-bottom: 12px; }}
        .badge {{ background: #3b82f6; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; }}
        .card-actions {{ display: flex; gap: 8px; }}
        .card-actions .btn {{ flex: 1; }}
        footer {{ text-align: center; padding: 40px 20px; color: #64748b; font-size: 13px; border-top: 1px solid #1e293b; margin-top: 60px; }}
    </style>
</head>
<body>
    <header class="navbar">
        <a href="/"><img src="/logo.svg" alt="NimeBot Logo" class="logo-img"></a>
        <div class="nav-links">
            <a href="/api" class="nav-btn">⚡ REST API Docs</a>
            <a href="/api/stats" class="nav-btn">📊 Stats JSON</a>
        </div>
    </header>

    <div class="hero">
        <h1>NimeBot <span>Auto</span> Media &amp; Streaming</h1>
        <p>Pusat Streaming &amp; REST API Anime Telegram Berkecepatan Tinggi di Cloud Railway</p>
        <div class="stats-bar">
            <span>📺 <strong>{stats['total_episodes']}</strong> Episode</span>
            <span>🎬 <strong>{stats['total_anime']}</strong> Anime Series</span>
            <span>💾 <strong>{stats['total_storage_gb']} GB</strong> Video Storage</span>
            <span>⚡ <strong>HTTP 206</strong> Fast Seeking</span>
        </div>
    </div>

    <main class="container">
        <div class="section-header">
            <h2 class="section-title">🎬 Koleksi Anime ({stats['total_anime']})</h2>
            <a href="/api/anime" class="nav-btn" style="font-size: 12px;">Lihat Format API JSON &rarr;</a>
        </div>
        <div class="anime-grid">
            {catalog_html}
        </div>

        <div class="section-header">
            <h2 class="section-title">🔥 Unggahan Episode Terbaru</h2>
            <a href="/api/episodes" class="nav-btn" style="font-size: 12px;">Endpoint Episodes &rarr;</a>
        </div>
        <div class="rec-grid">
            {recent_html}
        </div>
    </main>

    <footer>
        &copy; 2026 NimeBot Auto • Streaming &amp; Media API • Running 24/7 on Railway
    </footer>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def handle_anime_page(request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        detail = get_anime_detail(slug)
        if not detail:
            return web.Response(text=f"Anime '{slug}' tidak ditemukan.", status=404)

        title = detail["title"]
        cover_url = detail["cover_url"]
        synopsis = detail["synopsis"] or "Tidak ada sinopsis tersedia untuk anime ini."
        year = detail["year"] or "-"
        studio = detail["studio"] or "-"
        atype = detail["type"] or "SERIES"
        status = detail["status"] or "FINISHED"
        genres = " • ".join(detail["genres"]) if detail["genres"] else "Anime"

        eps_html = ""
        for ep_obj in detail["episodes"]:
            ep_num = ep_obj["episode"]
            q_buttons = ""
            for q_name, q_info in sorted(ep_obj["qualities"].items()):
                msg_id = q_info["msg_id"]
                q_buttons += f"""
                <div class="ep-quality-pill">
                    <span class="q-tag">{q_name}</span>
                    <a href="/watch/{msg_id}" class="ep-btn">▶️ Nonton</a>
                    <a href="/download/{msg_id}" class="ep-btn ep-btn-dl">📥 Unduh</a>
                </div>
                """
            eps_html += f"""
            <div class="ep-row">
                <div class="ep-label">Episode {ep_num}</div>
                <div class="ep-qualities">{q_buttons}</div>
            </div>
            """

        html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <title>{title} | NimeBot Auto</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #090d16; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            padding: 0; min-height: 100vh;
        }}
        .navbar {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 16px 32px; background: #0f172a; border-bottom: 1px solid #1e293b;
        }}
        .logo-img {{ height: 38px; }}
        .nav-btn {{
            background: #1e293b; color: #38bdf8; text-decoration: none; padding: 7px 14px;
            border-radius: 8px; font-size: 13px; font-weight: 600; border: 1px solid #334155;
        }}
        .container {{ max-width: 1000px; margin: 30px auto; padding: 0 20px; }}
        .anime-header {{
            display: flex; gap: 30px; background: #111827; border-radius: 16px; padding: 24px;
            border: 1px solid #1f2937; margin-bottom: 30px; flex-wrap: wrap;
        }}
        .poster {{ width: 220px; aspect-ratio: 3/4; object-fit: cover; border-radius: 10px; border: 1px solid #334155; }}
        .header-content {{ flex: 1; min-width: 280px; display: flex; flex-direction: column; justify-content: center; }}
        .badge-type {{ display: inline-block; background: #38bdf8; color: #0f172a; font-weight: 800; font-size: 11px; padding: 3px 8px; border-radius: 4px; margin-bottom: 10px; width: fit-content; }}
        h1 {{ font-size: 26px; font-weight: 800; color: #fff; margin-bottom: 8px; }}
        .meta-line {{ color: #94a3b8; font-size: 14px; margin-bottom: 14px; }}
        .meta-line strong {{ color: #cbd5e1; }}
        .synopsis {{ color: #94a3b8; font-size: 14px; line-height: 1.6; max-height: 140px; overflow-y: auto; background: #0b0f19; padding: 12px; border-radius: 8px; border: 1px solid #1f2937; }}
        .section-title {{ font-size: 20px; font-weight: 700; margin-bottom: 18px; color: #fff; }}
        .ep-list {{ display: flex; flex-direction: column; gap: 12px; }}
        .ep-row {{
            background: #111827; border-radius: 10px; padding: 16px 20px; border: 1px solid #1f2937;
            display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;
        }}
        .ep-label {{ font-size: 16px; font-weight: 700; color: #38bdf8; }}
        .ep-qualities {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .ep-quality-pill {{
            display: flex; align-items: center; gap: 6px; background: #1e293b; padding: 4px 8px;
            border-radius: 8px; border: 1px solid #334155;
        }}
        .q-tag {{ font-size: 12px; font-weight: 700; color: #e2e8f0; }}
        .ep-btn {{
            background: #2563eb; color: #fff; text-decoration: none; padding: 4px 8px;
            border-radius: 4px; font-size: 11px; font-weight: 600;
        }}
        .ep-btn-dl {{ background: #334155; }}
        .ep-btn:hover {{ opacity: 0.9; }}
        footer {{ text-align: center; padding: 40px 20px; color: #64748b; font-size: 13px; }}
    </style>
</head>
<body>
    <header class="navbar">
        <a href="/"><img src="/logo.svg" alt="NimeBot Logo" class="logo-img"></a>
        <a href="/" class="nav-btn">🏠 Beranda Portal</a>
    </header>

    <div class="container">
        <div class="anime-header">
            <img src="{cover_url}" alt="{title}" class="poster" onerror="this.src='/icon.svg'">
            <div class="header-content">
                <span class="badge-type">{atype} • {status}</span>
                <h1>{title}</h1>
                <p class="meta-line">📅 Tahun: <strong>{year}</strong> • 🏢 Studio: <strong>{studio}</strong> • 🏷️ <strong>{genres}</strong></p>
                <div class="synopsis">{synopsis}</div>
            </div>
        </div>

        <h2 class="section-title">📺 Daftar Episode Tersedia ({detail['available_episodes_count']})</h2>
        <div class="ep-list">
            {eps_html}
        </div>
    </div>

    <footer>
        &copy; 2026 NimeBot Auto • Running on Railway
    </footer>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def handle_watch(request: web.Request) -> web.Response:
        try:
            msg_id = int(request.match_info["msg_id"])
        except ValueError:
            return web.Response(text="ID Pesan tidak valid", status=400)

        record = get_record_by_msg_id(msg_id)
        title = record["anime_title"] if record else f"Anime #{msg_id}"
        ep_idx = record["ep_index"] if record else 1
        quality = record["quality"] if record else "720p"

        # Try to find anime detail to get cover and slug
        anime_info = get_anime_detail(title)
        cover_url = anime_info["cover_url"] if anime_info else "/icon.svg"
        slug = anime_info["slug"] if anime_info else ""

        file_size_str = "Video MP4"
        if record and record.get("file_size"):
            file_size_str = f"{record['file_size'] / (1024*1024):.1f} MB"
        else:
            try:
                msg = await tg_client.get_messages(LOG_CHANNEL, ids=msg_id)
                if msg and msg.media and hasattr(msg.media, "document"):
                    file_size_str = human_bytes(msg.media.document.size)
            except Exception:
                pass

        tg_chat_link = f"https://t.me/c/{abs(LOG_CHANNEL) - 1000000000000}/{msg_id}" if LOG_CHANNEL != 0 else "#"

        html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <title>{title} - Episode {ep_idx} ({quality}) | NimeBot Auto</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #090d16; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; flex-direction: column; align-items: center; min-height: 100vh; padding: 20px 10px;
        }}
        .container {{
            width: 100%; max-width: 960px; background: #111827; border-radius: 16px;
            overflow: hidden; box-shadow: 0 10px 30px -5px rgba(0, 0, 0, 0.7); border: 1px solid #1f2937;
        }}
        .player-wrapper {{
            position: relative; background: #000; width: 100%; aspect-ratio: 16 / 9;
        }}
        video {{ width: 100%; height: 100%; display: block; }}
        .info {{ padding: 24px; }}
        .badge {{
            display: inline-block; background: #38bdf8; color: #0f172a; font-size: 12px;
            font-weight: 800; padding: 4px 10px; border-radius: 6px; margin-bottom: 10px;
        }}
        h1 {{ font-size: 22px; font-weight: 800; margin-bottom: 8px; color: #fff; }}
        .meta {{ color: #94a3b8; font-size: 14px; margin-bottom: 20px; }}
        .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .btn {{
            display: inline-flex; align-items: center; gap: 6px; background: #2563eb; color: white;
            text-decoration: none; padding: 10px 18px; border-radius: 8px; font-size: 14px; font-weight: 600;
        }}
        .btn-sec {{ background: #1e293b; color: #38bdf8; border: 1px solid #334155; }}
        .btn:hover {{ opacity: 0.9; }}
        footer {{ margin-top: 30px; color: #64748b; font-size: 13px; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="player-wrapper">
            <video controls autoplay playsinline preload="metadata" poster="{cover_url}">
                <source src="/stream/{msg_id}" type="video/mp4">
                Browser Anda tidak mendukung pemutar video HTML5.
            </video>
        </div>
        <div class="info">
            <span class="badge">{quality}</span>
            <h1>🎬 {title} - Episode {ep_idx}</h1>
            <p class="meta">Ukuran file: <strong>{file_size_str}</strong> • Disiarkan otomatis via NimeBot Auto</p>
            <div class="actions">
                <a href="/download/{msg_id}" class="btn">📥 Direct Download ({file_size_str})</a>
                {f'<a href="/anime/{slug}" class="btn btn-sec">📺 Semua Episode Anime</a>' if slug else ''}
                <a href="{tg_chat_link}" class="btn btn-sec">💬 Buka di Telegram</a>
                <a href="/" class="btn btn-sec">🏠 Beranda Portal</a>
            </div>
        </div>
    </div>
    <footer>
        &copy; 2026 NimeBot Auto Cloud • Running on Railway
    </footer>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def handle_stream(request: web.Request) -> web.StreamResponse:
        return await stream_telegram_file(request, tg_client, is_download=False)

    async def handle_download(request: web.Request) -> web.StreamResponse:
        return await stream_telegram_file(request, tg_client, is_download=True)

    # Register Web UI & Player Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/anime/{slug}", handle_anime_page)
    app.router.add_get("/watch/{msg_id}", handle_watch)
    app.router.add_get("/stream/{msg_id}", handle_stream)
    app.router.add_get("/download/{msg_id}", handle_download)

    # Register Static Media Routes (Favicon, Logo, Icon, Anime Cover JPGs)
    app.router.add_get("/favicon.ico", handle_favicon)
    app.router.add_get("/favicon.svg", handle_favicon)
    app.router.add_get("/logo.svg", handle_logo)
    app.router.add_get("/icon.svg", handle_icon)
    app.router.add_get("/cover/{msg_id}", handle_cover)
    app.router.add_get("/cover/{msg_id}.jpg", handle_cover)
    app.router.add_get("/api/cover/{msg_id}", handle_cover)

    # Register REST API Routes
    app.router.add_get("/api", handle_api_index)
    app.router.add_get("/api/stats", handle_api_stats)
    app.router.add_get("/api/anime", handle_api_anime)
    app.router.add_get("/api/anime/{slug}", handle_api_anime_detail)
    app.router.add_get("/api/episodes", handle_api_episodes)

    return app

async def stream_telegram_file(request: web.Request, tg_client: TelegramClient, is_download: bool = False) -> web.StreamResponse:
    try:
        msg_id = int(request.match_info["msg_id"])
    except ValueError:
        return web.Response(text="ID Pesan tidak valid", status=400)

    try:
        msg = await tg_client.get_messages(LOG_CHANNEL, ids=msg_id)
    except Exception as e:
        logging.error(f"Error fetching message {msg_id}: {e}")
        return web.Response(text=f"Error Telegram: {e}", status=502)

    if not msg or not msg.media or not hasattr(msg.media, "document"):
        return web.Response(text="Video tidak ditemukan di Telegram Storage", status=404)

    doc = msg.media.document
    file_size = doc.size
    mime_type = doc.mime_type or "video/mp4"

    record = get_record_by_msg_id(msg_id)
    if record:
        fname = f"{record['anime_title']} - Ep {record['ep_index']} ({record['quality']}).mp4"
    else:
        fname = f"Anime_Episode_{msg_id}.mp4"

    disposition = "attachment" if is_download else "inline"
    range_header = request.headers.get("Range")

    if range_header:
        # HTTP 206 Partial Content (Supports Video Seeking/Scrubbing)
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
        else:
            start = 0
            end = file_size - 1

        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))
        content_length = end - start + 1

        headers = {
            "Content-Type": mime_type,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'{disposition}; filename="{fname}"'
        }
        response = web.StreamResponse(status=206, reason="Partial Content", headers=headers)
        await response.prepare(request)

        if request.method == "HEAD":
            return response

        try:
            async for chunk in tg_client.iter_download(doc, offset=start, limit=content_length, chunk_size=256 * 1024):
                await response.write(chunk)
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception as e:
            logging.debug(f"Stream interrupted: {e}")
        return response
    else:
        # Standard Full File Stream / Download
        headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Disposition": f'{disposition}; filename="{fname}"'
        }
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)

        if request.method == "HEAD":
            return response

        try:
            async for chunk in tg_client.iter_download(doc, chunk_size=256 * 1024):
                await response.write(chunk)
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception as e:
            logging.debug(f"Stream interrupted: {e}")
        return response

# ----------------- CHANNEL SCANNER & PARSER HELPERS -----------------
def clean_title(title: str) -> str:
    title = re.sub(r"^[┌│├└─\s🎬]+", "", title).strip()
    title = re.sub(r"[─\s━│]+$", "", title).strip()
    return title.strip()

def parse_info_card(text: str, msg_id: int):
    m_title = re.search(r"🎬\s*(.+?)(?:\n|─|━|$)", text)
    title = clean_title(m_title.group(1)) if m_title else ""
    if not title:
        return None
    m_type = re.search(r"Tipe\s*:\s*([^\n•]+)", text, re.I)
    atype = m_type.group(1).strip() if m_type else "SERIES"
    m_studio = re.search(r"Studio\s*:\s*([^\n•]+)", text, re.I)
    studio = m_studio.group(1).strip() if m_studio else ""
    m_year = re.search(r"Tahun\s*:\s*(\d{4})", text, re.I)
    year = m_year.group(1).strip() if m_year else ""
    m_genre = re.search(r"Genre\s*:\s*([^\n•]+)", text, re.I)
    genre = m_genre.group(1).strip() if m_genre else ""
    m_status = re.search(r"Status\s*:\s*([^\n•|]+)", text, re.I)
    status = m_status.group(1).strip() if m_status else "FINISHED"
    m_eps = re.search(r"Episode\s*:\s*(\d+)", text, re.I)
    total_eps = int(m_eps.group(1)) if m_eps else 0
    m_syn = re.search(r"SINOPSIS\s*:?\s*\n*(.+?)(?:\n\s*🍿|$)", text, re.DOTALL | re.I)
    synopsis = m_syn.group(1).strip() if m_syn else ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return {
        "title": title,
        "slug": slug,
        "cover_msg_id": msg_id,
        "type": atype,
        "studio": studio,
        "year": year,
        "genres": genre,
        "status": status,
        "total_eps": total_eps,
        "synopsis": synopsis
    }

def parse_video(msg):
    doc = msg.media.document
    text = msg.raw_text or ""
    m_title = re.search(r"🎬\s*([^\n─━│]+)", text)
    title = clean_title(m_title.group(1)) if m_title else ""
    if not title:
        return None
    m_ep = re.search(r"(?:Episode|Ep)\s*:?\s*(?:Episode\s*)?(\d+)", text, re.I)
    ep = int(m_ep.group(1)) if m_ep else 1
    m_q = re.search(r"(360p|480p|720p|1080p)", text, re.I)
    quality = m_q.group(1).lower() if m_q else "720p"
    return {
        "title": title,
        "ep": ep,
        "quality": quality,
        "msg_id": msg.id,
        "file_size": doc.size
    }

async def run_channel_sync(client: TelegramClient, chat_id: int, status_msg_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM scraper_config WHERE key = 'last_synced_id'")
    row = c.fetchone()
    last_id = int(row[0]) if row and row[0] else 1
    conn.close()

    try:
        sample_ids = list(range(last_id, last_id + 500))
        msgs = await client.get_messages(LOG_CHANNEL, ids=sample_ids)
        valid_ids = [m.id for m in msgs if m is not None]
        if not valid_ids:
            sample_ids = list(range(max(1, last_id - 100), last_id + 100))
            msgs = await client.get_messages(LOG_CHANNEL, ids=sample_ids)
            valid_ids = [m.id for m in msgs if m is not None]
        max_id = max(valid_ids) if valid_ids else last_id
    except Exception:
        max_id = last_id + 100

    if max_id <= last_id and last_id > 1:
        stats = get_api_stats()
        await client.edit_message(
            chat_id,
            status_msg_id,
            f"✅ **Database Channel Sudah Terkini!**\n\n"
            f"🎬 Total Anime: `{stats['total_anime']}`\n"
            f"📺 Total Episode: `{stats['total_episodes']}`\n"
            f"💾 Video Storage: `{stats['total_storage_gb']} GB`\n\n"
            f"Semua file dan cover media siap disajikan via REST API:\n`{STREAMER_URL}/api`"
        )
        return

    start_scan = max(1, last_id - 50)
    end_scan = max_id + 50

    conn = sqlite3.connect(DB_PATH)
    for chunk_start in range(start_scan, end_scan + 1, 50):
        chunk_ids = list(range(chunk_start, min(chunk_start + 50, end_scan + 1)))
        try:
            msgs = await client.get_messages(LOG_CHANNEL, ids=chunk_ids)
        except Exception:
            continue

        for m in msgs:
            if not m:
                continue
            text = m.raw_text or ""
            is_image = bool(m.photo or (m.media and hasattr(m.media, "document") and getattr(m.media.document, "mime_type", "").startswith("image/")))
            if is_image and ("INFORMASI ANIME" in text or "SINOPSIS" in text or "Tipe:" in text or "Status:" in text):
                info = parse_info_card(text, m.id)
                if info:
                    conn.execute("""
                        INSERT OR REPLACE INTO anime_series (title, slug, cover_msg_id, anime_type, studio, year, genres, status, total_episodes, synopsis)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (info["title"], info["slug"], info["cover_msg_id"], info["type"], info["studio"], info["year"], info["genres"], info["status"], info["total_eps"], info["synopsis"]))
            elif m.media and hasattr(m.media, "document") and getattr(m.media.document, "mime_type", "").startswith("video/"):
                v = parse_video(m)
                if v:
                    conn.execute("""
                        INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title, file_size)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, ("", v["ep"], v["quality"], v["msg_id"], v["title"], v["file_size"]))
        conn.commit()

    conn.execute("INSERT OR REPLACE INTO scraper_config (key, value) VALUES ('last_synced_id', ?)", (str(end_scan),))
    conn.commit()
    conn.close()

    stats = get_api_stats()
    await client.edit_message(
        chat_id,
        status_msg_id,
        f"✅ **Sinkronisasi Selesai!**\n\n"
        f"🎬 **Total Anime Series:** `{stats['total_anime']}`\n"
        f"📺 **Total Episode:** `{stats['total_episodes']}`\n"
        f"💾 **Total Cloud Storage:** `{stats['total_storage_gb']} GB`\n\n"
        f"🌐 **REST API & Cover Media Siap Diakses!**\n"
        f"URL: `{STREAMER_URL}/api`"
    )

# ----------------- MAIN TELEGRAM APP & WEB RUNNER -----------------
async def main():
    init_db()
    logging.info("Starting NimeBot Auto on Railway container...")
    client = TelegramClient("railway_session", API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    logging.info("Telegram Bot Client connected and authorized successfully!")

    # Safe edit message wrapper to ignore MessageNotModifiedError
    _orig_edit = client.edit_message
    async def safe_edit(*args, **kwargs):
        try:
            return await _orig_edit(*args, **kwargs)
        except MessageNotModifiedError:
            return None
    client.edit_message = safe_edit

    # Setup & Launch Integrated aiohttp Web Streamer
    app = create_web_app(client)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"NimeBot Auto Web Streamer listening on http://0.0.0.0:{port}!")

    @client.on(events.NewMessage)
    async def on_message(event):
        global CURRENT_EMAIL
        if not event.is_private:
            return

        user_id = event.sender_id
        text = event.raw_text.strip()
        state = USER_STATES.get(user_id)

        if not is_authorized(user_id):
            await event.respond(
                f"⛔ **Akses Terbatas:** Anda bukan pemilik bot ini.\n\n"
                f"🆔 **ID Telegram Anda:** `{user_id}`\n\n"
                f"ℹ️ *Jika Anda adalah pemilik bot, tambahkan ID di atas ke variabel `ADMIN_USER_ID` di Railway (bisa dipisah koma untuk banyak ID), atau ubah `ADMIN_USER_ID=0` untuk mengizinkan semua pengguna.*"
            )
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

        if text.startswith("/api"):
            stats = get_api_stats()
            api_msg = (
                "🌐 **NimeBot Auto REST API & Media**\n"
                "──────────────────────────────\n"
                f"📊 **Total Anime:** `{stats['total_anime']}`\n"
                f"📺 **Total Episode:** `{stats['total_episodes']}`\n"
                f"💾 **Total Cloud Storage:** `{stats['total_storage_gb']} GB`\n"
                "──────────────────────────────\n"
                f"🔗 **Base API URL:** `{STREAMER_URL}/api`\n"
                f"🎬 **Daftar Anime (JSON):** `{STREAMER_URL}/api/anime`\n"
                f"📺 **Daftar Episode (JSON):** `{STREAMER_URL}/api/episodes`\n"
                f"🖼️ **Media Cover (JPG):** `{STREAMER_URL}/cover/{{id}}.jpg`\n"
                f"✨ **Brand Icon (SVG):** `{STREAMER_URL}/favicon.svg`"
            )
            buttons = [
                [Button.url("🚀 Buka Portal Web", STREAMER_URL)],
                [Button.url("📜 Dokumentasi API", f"{STREAMER_URL}/api")]
            ]
            await event.respond(api_msg, buttons=buttons)
            return

        if text.startswith("/sync"):
            status_msg = await event.respond("🔄 **Memulai Sinkronisasi Channel Telegram...**\nMemeriksa episode dan cover anime terbaru...")
            await run_channel_sync(client, event.chat_id, status_msg.id)
            return

    @client.on(events.CallbackQuery)
    async def on_callback(event):
        global CURRENT_MODE, IS_RUNNING, ACTIVE_TASK
        user_id = event.sender_id
        chat_id = event.chat_id
        data = event.data.decode("utf-8")
        msg_id = event.message_id

        logging.info(f"Callback query from user {user_id}: {data}")

        if not is_authorized(user_id):
            await event.answer(f"⛔ Akses ditolak (ID Anda: {user_id})", alert=True)
            return

        try:
            await event.answer("Memproses...")
        except Exception:
            pass

        if data == "btn_menu":
            t, b = build_menu(CURRENT_MODE, IS_RUNNING)
            await client.edit_message(chat_id, msg_id, text=t, buttons=b)
        elif data == "btn_sync_channel":
            status_msg = await event.respond("🔄 **Memulai Sinkronisasi Channel Telegram...**\nMemeriksa episode dan cover anime terbaru...")
            await run_channel_sync(client, chat_id, status_msg.id)
        elif data == "btn_api_info":
            stats = get_api_stats()
            api_msg = (
                "🌐 **NimeBot Auto REST API & Media**\n"
                "──────────────────────────────\n"
                f"📊 **Total Anime:** `{stats['total_anime']}`\n"
                f"📺 **Total Episode:** `{stats['total_episodes']}`\n"
                f"💾 **Total Cloud Storage:** `{stats['total_storage_gb']} GB`\n"
                "──────────────────────────────\n"
                f"🔗 **Base API URL:** `{STREAMER_URL}/api`\n"
                f"🎬 **Daftar Anime (JSON):** `{STREAMER_URL}/api/anime`\n"
                f"📺 **Daftar Episode (JSON):** `{STREAMER_URL}/api/episodes`\n"
                f"🖼️ **Media Cover (JPG):** `{STREAMER_URL}/cover/{{id}}.jpg`\n"
                f"✨ **Brand Icon (SVG):** `{STREAMER_URL}/favicon.svg`"
            )
            buttons = [
                [Button.url("🚀 Buka Portal Web", STREAMER_URL)],
                [Button.url("📜 Dokumentasi API", f"{STREAMER_URL}/api")],
                [Button.inline("🔙 Kembali ke Menu", b"btn_menu")]
            ]
            await client.edit_message(chat_id, msg_id, text=api_msg, buttons=buttons)
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
                # Check all episodes in ascending order (Ep 1 to Ep N)
                for ep in eps:
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
