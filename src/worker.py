import time
import math
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

def render_progress_bar(current: int, total: int, width: int = 12) -> str:
    if total <= 0:
        pct = 0.0
    else:
        pct = max(0.0, min(1.0, current / total))
    
    filled = int(round(width * pct))
    unfilled = width - filled
    bar = "█" * filled + "░" * unfilled
    return f"[{bar}] {pct * 100:.1f}%"

def human_bytes(size: float) -> str:
    if size <= 0:
        return "0.0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = int(math.floor(math.log(size, 1024)))
    idx = min(idx, len(units) - 1)
    p = math.pow(1024, idx)
    s = round(size / p, 2)
    return f"{s} {units[idx]}"

def format_progress_message(
    title: str,
    ep: int,
    quality: str,
    status: str,
    current: int,
    total: int,
    speed_bps: float
) -> str:
    bar = render_progress_bar(current, total, width=10)
    current_str = human_bytes(current)
    total_str = human_bytes(total)
    speed_str = f"{human_bytes(speed_bps)}/s" if speed_bps > 0 else "--/s"
    
    eta_str = "--"
    if speed_bps > 0 and total > current:
        remaining = (total - current) / speed_bps
        eta_str = f"{int(remaining)}s"

    lines = [
        f"🎬 **{title}**",
        f"📌 **Episode {ep}** ({quality})",
        f"⚡ **Status:** {status}",
        f"📊 `{bar}`",
        f"📦 **Size:** `{current_str} / {total_str}`",
        f"🚀 **Speed:** `{speed_str}` | **ETA:** `{eta_str}`"
    ]
    return "\n".join(lines)

class TelegramProgressNotifier:
    def __init__(self, bot_token: str, chat_id: int | str, message_id: int, min_interval: float = 2.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.message_id = message_id
        self.min_interval = min_interval
        self.last_update_time = 0.0
        self.last_text = ""

    def update(
        self,
        title: str,
        ep: int,
        quality: str,
        status: str,
        current: int,
        total: int,
        speed_bps: float,
        force: bool = False
    ) -> None:
        now = time.time()
        if not force and (now - self.last_update_time) < self.min_interval:
            return

        text = format_progress_message(title, ep, quality, status, current, total, speed_bps)
        if text == self.last_text and not force:
            return

        self.last_update_time = now
        self.last_text = text
        self._send_edit(text)

    def _send_edit(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except urllib.error.HTTPError as e:
            # Handle rate limit 429 gracefully
            if e.code == 429:
                pass
        except Exception:
            pass
