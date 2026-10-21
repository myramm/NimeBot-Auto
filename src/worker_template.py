import base64
import json

def generate_worker_payload(
    bot_token: str,
    chat_id: int | str,
    progress_msg_id: int,
    target_mode: str = "auto",
    movie_id: str = "",
    streamer_url: str = "https://streamer-production-c24a.up.railway.app",
    log_channel: str = "-1004462080662",
    api_id: str = "37124183",
    api_hash: str = "f4d846728e694c2d1071ca7d3b2f6c81"
) -> str:
    """
    Generates a standalone base64-encoded Python command string
    that runs on Railway to execute the worker with real-time progress updates.
    """
    config = {
        "bot_token": bot_token,
        "chat_id": str(chat_id),
        "progress_msg_id": progress_msg_id,
        "target_mode": target_mode,
        "movie_id": str(movie_id),
        "streamer_url": streamer_url,
        "log_channel": log_channel,
        "api_id": api_id,
        "api_hash": api_hash
    }
    config_json = json.dumps(config)

    worker_code = f"""
import os, sys, time, json, math, urllib.request, urllib.error, asyncio
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo

CFG = {config_json}

def render_bar(current, total, width=10):
    if total <= 0: return "[░░░░░░░░░░] 0.0%"
    pct = max(0.0, min(1.0, current / total))
    filled = int(round(width * pct))
    unfilled = width - filled
    return f"[{{'█'*filled}}{{'░'*unfilled}}] {{pct * 100:.1f}}%"

def human_bytes(size):
    if size <= 0: return "0.0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = min(int(math.floor(math.log(size, 1024))), len(units)-1)
    return f"{{round(size / math.pow(1024, idx), 2)}} {{units[idx]}}"

last_edit = 0.0
def notify_progress(title, ep, quality, status, current, total, speed):
    global last_edit
    now = time.time()
    if now - last_edit < 2.0 and current < total:
        return
    last_edit = now
    bar = render_bar(current, total)
    spd = f"{{human_bytes(speed)}}/s" if speed > 0 else "--/s"
    eta = f"{{int((total - current)/speed)}}s" if speed > 0 and total > current else "--"
    text = (
        f"🎬 **{{title}}**\\n"
        f"📌 **Episode {{ep}}** ({{quality}})\\n"
        f"⚡ **Status:** {{status}}\\n"
        f"📊 `{{bar}}`\\n"
        f"📦 **Size:** `{{human_bytes(current)}} / {{human_bytes(total)}}`\\n"
        f"🚀 **Speed:** `{{spd}}` | **ETA:** `{{eta}}`"
    )
    url = f"https://api.telegram.org/bot{{CFG['bot_token']}}/editMessageText"
    payload = json.dumps({{
        "chat_id": CFG["chat_id"],
        "message_id": CFG["progress_msg_id"],
        "text": text,
        "parse_mode": "Markdown"
    }}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={{"Content-Type": "application/json"}})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def api_get(endpoint):
    url = f"https://xyz-api.animein.net/{{endpoint.lstrip('/')}}"
    req = urllib.request.Request(url, headers={{"User-Agent": "okhttp/4.12.0", "apk_ver": "5.2.2"}})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

async def main():
    client = TelegramClient('railway_worker', int(CFG['api_id']), CFG['api_hash'])
    await client.start(bot_token=CFG['bot_token'])
    channel = int(CFG['log_channel'])

    movies_to_process = []
    if CFG['target_mode'] == 'manual' and CFG['movie_id']:
        det = api_get(f"3/2/movie/detail/{{CFG['movie_id']}}?id_user=0&key_client=guest&apk_ver=5.2.2")
        title = det.get('data', {{}}).get('movie', {{}}).get('title', f"Anime {{CFG['movie_id']}}")
        movies_to_process.append({{"id": CFG['movie_id'], "title": title}})
    else:
        cat = api_get("3/2/home/new?id_user=0&key_client=guest&apk_ver=5.2.2")
        movies = cat.get('data', {{}}).get('movie', [])
        movies_to_process.extend(movies)

    for m in movies_to_process:
        m_id = str(m.get('id', ''))
        m_title = m.get('title', 'Unknown')
        eps_data = api_get(f"3/2/movie/episode/{{m_id}}?id_user=0&key_client=guest&apk_ver=5.2.2")
        episodes = eps_data.get('data', {{}}).get('episode', [])
        
        for ep in episodes:
            ep_id = ep.get('id')
            ep_idx = int(ep.get('index', 0))
            streams_data = api_get(f"3/2/episode/streamnew/{{ep_id}}?id_user=0&key_client=guest&apk_ver=5.2.2")
            streams = streams_data.get('data', {{}}).get('stream', [])

            for s in streams:
                q = s.get('quality', '720p')
                s_url = s.get('url')
                if not s_url: continue

                filename = f"/tmp/{{m_id}}_{{ep_idx}}_{{q}}.mp4"
                try:
                    # Download phase
                    req = urllib.request.Request(s_url, headers={{"User-Agent": "okhttp/4.12.0", "Referer": "https://xyz-api.animein.net"}})
                    with urllib.request.urlopen(req, timeout=30) as resp, open(filename, 'wb') as f:
                        total_size = int(resp.headers.get('content-length', 0))
                        dl_size = 0
                        t0 = time.time()
                        while True:
                            chunk = resp.read(1024 * 512)
                            if not chunk: break
                            f.write(chunk)
                            dl_size += len(chunk)
                            elapsed = max(0.001, time.time() - t0)
                            notify_progress(m_title, ep_idx, q, "Downloading", dl_size, total_size, dl_size/elapsed)

                    # Upload phase
                    total_file_size = os.path.getsize(filename)
                    t0 = time.time()
                    def upload_cb(sent, total):
                        el = max(0.001, time.time() - t0)
                        notify_progress(m_title, ep_idx, q, "Uploading to Telegram", sent, total, sent/el)

                    caption = f"🎬 **{{m_title}} - Ep {{ep_idx}} ({{q}})**\\n\\n🍿 Powered by ReNime AutoBot"
                    sent_msg = await client.send_file(
                        channel,
                        filename,
                        caption=caption,
                        progress_callback=upload_cb,
                        attributes=[DocumentAttributeVideo(duration=0, w=1280, h=720, supports_streaming=True)]
                    )

                    # Post completed notification
                    notify_progress(m_title, ep_idx, q, "Completed ✅", total_file_size, total_file_size, 0)
                except Exception as e:
                    notify_progress(m_title, ep_idx, q, f"Error: {{str(e)}}", 0, 0, 0)
                finally:
                    if os.path.exists(filename):
                        os.remove(filename)

if __name__ == '__main__':
    asyncio.run(main())
"""

    encoded_code = base64.b64encode(worker_code.strip().encode("utf-8")).decode("utf-8")
    command = f'python3 -c "import base64; exec(base64.b64decode(\'{encoded_code}\').decode(\'utf-8\'))"'
    return command
