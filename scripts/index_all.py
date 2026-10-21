import asyncio, re, sqlite3, time, sys
from telethon import TelegramClient

API_ID = 37124183
API_HASH = "f4d846728e694c2d1071ca7d3b2f6c81"
BOT_TOKEN = "8942930830:AAG1RDxOjRKmZm-7TRKqgdIWCq87PiypY6c"
LOG_CHANNEL = -1004462080662
DB_PATH = "/root/file-streamer/anime_db.sqlite"

def clean_title(title: str) -> str:
    title = re.sub(r"^[┌│├└─\s🎬]+", "", title).strip()
    title = re.sub(r"[─\s━│]+$", "", title).strip()
    return title.strip()

def init_db(conn):
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

async def scan():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    client = TelegramClient("index_session", API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    
    total_cards = 0
    total_vids = 0
    start_time = time.time()
    
    # Scan from 1 to 3250 in batches of 100
    for chunk_start in range(1, 3251, 100):
        ids = list(range(chunk_start, min(chunk_start + 100, 3251)))
        try:
            msgs = await client.get_messages(LOG_CHANNEL, ids=ids)
        except Exception as e:
            print(f"Error fetching chunk {chunk_start}: {e}")
            await asyncio.sleep(2)
            continue
            
        for m in msgs:
            if not m:
                continue
            text = m.raw_text or ""
            is_image = bool(m.photo or (m.media and hasattr(m.media, "document") and getattr(m.media.document, "mime_type", "").startswith("image/")))
            if is_image and ("INFORMASI ANIME" in text or "SINOPSIS" in text or "Tipe:" in text or "Status:" in text):
                info = parse_info_card(text, m.id)
                if info:
                    total_cards += 1
                    conn.execute("""
                        INSERT OR REPLACE INTO anime_series (title, slug, cover_msg_id, anime_type, studio, year, genres, status, total_episodes, synopsis)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (info["title"], info["slug"], info["cover_msg_id"], info["type"], info["studio"], info["year"], info["genres"], info["status"], info["total_eps"], info["synopsis"]))
            elif m.media and hasattr(m.media, "document") and getattr(m.media.document, "mime_type", "").startswith("video/"):
                v = parse_video(m)
                if v:
                    total_vids += 1
                    conn.execute("""
                        INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title, file_size)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, ("", v["ep"], v["quality"], v["msg_id"], v["title"], v["file_size"]))
        conn.commit()
        print(f"Progress: [{chunk_start + len(ids) - 1}/3250] Indexed {total_cards} Anime Series, {total_vids} Episodes...")
    
    # Record max synced id in scraper_config
    conn.execute("INSERT OR REPLACE INTO scraper_config (key, value) VALUES ('last_synced_id', '3250')")
    conn.commit()
    
    elapsed = time.time() - start_time
    print(f"Done! Scanned in {elapsed:.2f}s. Total Anime Series: {total_cards}, Total Episodes: {total_vids}")
    await client.disconnect()
    conn.close()

if __name__ == "__main__":
    asyncio.run(scan())
