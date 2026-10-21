import time
import json
import urllib.request
import urllib.error
import asyncio
from typing import Tuple, Dict, Any, Optional, List

from src.config import Config
from src.database import init_db, get_uploaded_episodes, has_uploaded, record_upload
from src.animein_client import (
    get_latest_catalog,
    get_movie_details,
    get_movie_episodes,
    get_episode_streams
)
from src.railway_client import (
    verify_railway_token,
    ensure_railway_project,
    set_worker_start_command,
    redeploy_worker,
    stop_worker,
    get_latest_deployment
)
from src.worker_template import generate_worker_payload

def parse_command(text: str) -> Tuple[str, str]:
    if not text:
        return "", ""
    text = text.strip()
    if not text.startswith("/"):
        return "", text
    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg

def format_menu_text(mode: str, is_running: bool, railway_email: str = "") -> str:
    status_str = "🟢 RUNNING" if is_running else "🔴 STOPPED"
    mode_str = mode.upper()
    acc_str = railway_email if railway_email else "(Not configured)"
    
    lines = [
        "🤖 **ReNime Anime Auto-Uploader Master Bot**",
        "──────────────────────────────",
        f"⚡ **Status:** `{status_str}`",
        f"🔄 **Mode:** `{mode_str}`",
        f"☁️ **Railway Account:** `{acc_str}`",
        "──────────────────────────────",
        "Gunakan tombol di bawah atau command:",
        "• `/mode` - Ubah mode (Auto / Manual)",
        "• `/start_auto` - Mulai auto update anime",
        "• `/download <id>` - Download anime manual",
        "• `/stop` - Hentikan worker Railway",
        "• `/token <token>` - Pasang / Ganti Railway token",
        "• `/status` - Cek status detail"
    ]
    return "\n".join(lines)

def build_menu_keyboard(mode: str, is_running: bool) -> dict:
    start_stop_btn = (
        {"text": "⏹ Stop Worker", "callback_data": "btn_stop"}
        if is_running else
        {"text": "▶️ Start Auto Update", "callback_data": "btn_start_auto"}
    )
    toggle_mode_text = "🔄 Mode: [MANUAL] ➔ AUTO" if mode == "manual" else "🔄 Mode: [AUTO] ➔ MANUAL"
    
    return {
        "inline_keyboard": [
            [start_stop_btn],
            [{"text": toggle_mode_text, "callback_data": "btn_toggle_mode"}],
            [
                {"text": "📊 Status", "callback_data": "btn_status"},
                {"text": "🔄 Refresh", "callback_data": "btn_menu"}
            ]
        ]
    }

class TelegramBotAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, chat_id: int | str, text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
        url = f"{self.base_url}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._request(url, payload)

    def edit_message(self, chat_id: int | str, message_id: int, text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
        url = f"{self.base_url}/editMessageText"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._request(url, payload)

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        url = f"{self.base_url}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id, "text": text}
        self._request(url, payload)

    def get_updates(self, offset: int = 0, timeout: int = 20) -> list:
        url = f"{self.base_url}/getUpdates?offset={offset}&timeout={timeout}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("result", [])
        except Exception:
            return []

    def _request(self, url: str, payload: dict) -> Optional[dict]:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8")).get("result")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            print(f"Telegram API HTTP error {e.code}: {err}")
            return None
        except Exception as e:
            print(f"Telegram API error: {e}")
            return None

class MasterBot:
    def __init__(self, config: Config, db_path: str = "anime_db.sqlite"):
        self.config = config
        self.db_path = db_path
        self.api = TelegramBotAPI(config.bot_token)
        init_db(self.db_path)

    def is_admin(self, user_id: int) -> bool:
        if self.config.admin_user_id == 0:
            # First user to interact becomes registered admin
            self.config.admin_user_id = user_id
            self.config.save()
            return True
        return user_id == self.config.admin_user_id

    def get_pending_downloads(self, movie_id: str = "") -> List[Dict[str, Any]]:
        """
        Queries Animein API and filters out episodes already recorded in SQLite
        to strictly enforce zero duplicate uploads.
        """
        targets = []
        if movie_id:
            det = get_movie_details(movie_id)
            title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
            targets.append({"id": str(movie_id), "title": title})
        else:
            cat = get_latest_catalog()
            targets.extend(cat)

        pending = []
        for t in targets:
            m_id = str(t.get("id", ""))
            m_title = t.get("title", "Unknown")
            if not m_id:
                continue

            episodes = get_movie_episodes(m_id)
            for ep in episodes:
                ep_id = ep.get("id")
                ep_idx = int(ep.get("index", 0))
                streams = get_episode_streams(ep_id)

                for s in streams:
                    q = s.get("quality", "720p")
                    url = s.get("url")
                    if not url:
                        continue

                    if not has_uploaded(m_id, ep_idx, q, db_path=self.db_path):
                        pending.append({
                            "movie_id": m_id,
                            "title": m_title,
                            "ep_index": ep_idx,
                            "quality": q,
                            "stream_url": url
                        })
        return pending

    def handle_start(self, chat_id: int | str) -> None:
        text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
        kb = build_menu_keyboard(self.config.mode, self.config.is_running)
        self.api.send_message(chat_id, text, reply_markup=kb)

    def handle_token(self, chat_id: int | str, token: str) -> None:
        if not token:
            self.api.send_message(chat_id, "❌ Format salah. Ketik:\n`/token <railway_api_token>`")
            return
        
        self.api.send_message(chat_id, "🔍 Memverifikasi token Railway...")
        user_info = verify_railway_token(token)
        if not user_info:
            self.api.send_message(chat_id, "❌ Token Railway tidak valid atau ditolak oleh Railway API.")
            return

        email = user_info.get("email", "Unknown")
        self.api.send_message(chat_id, f"✅ Token valid! Akun: `{email}`\nSedang auto-provisioning project & services...")
        
        try:
            p_id, e_id, s_id = ensure_railway_project(token)
            self.config.set_token(token, email=email)
            self.config.railway_project_id = p_id
            self.config.railway_env_id = e_id
            self.config.railway_service_id = s_id
            self.config.save()
            self.api.send_message(chat_id, f"🎉 Railway siap!\nProject: `{p_id}`\nService: `{s_id}`\nAkun: `{email}`")
        except Exception as e:
            self.api.send_message(chat_id, f"⚠️ Token valid tapi auto-provisioning gagal: {e}")

    def handle_mode(self, chat_id: int | str, new_mode: str = "") -> None:
        if new_mode in ("auto", "manual"):
            self.config.set_mode(new_mode)
        else:
            self.config.set_mode("manual" if self.config.mode == "auto" else "auto")
        
        self.api.send_message(chat_id, f"🔄 Mode sistem diubah ke: **{self.config.mode.upper()}**")

    def handle_start_auto(self, chat_id: int | str) -> None:
        if not self.config.railway_token:
            self.api.send_message(chat_id, "❌ Token Railway belum diatur! Pasang dulu dengan `/token <token>`.")
            return

        self.api.send_message(chat_id, "🔍 Memeriksa anime baru dari katalog...")
        pending = self.get_pending_downloads()
        if not pending:
            self.api.send_message(chat_id, "✅ Semua episode terbaru sudah terupload! Tidak ada duplikat yang perlu diproses.")
            return

        prog_msg = self.api.send_message(chat_id, f"🚀 Menyiapkan {len(pending)} episode baru di Railway Worker...\n`[░░░░░░░░░░] 0.0% Initializing`")
        prog_msg_id = prog_msg.get("message_id", 0) if prog_msg else 0

        cmd = generate_worker_payload(
            bot_token=self.config.bot_token,
            chat_id=chat_id,
            progress_msg_id=prog_msg_id,
            target_mode="auto",
            streamer_url=self.config.streamer_url,
            log_channel=self.config.log_channel,
            api_id=self.config.api_id,
            api_hash=self.config.api_hash,
            items=pending
        )

        ok = set_worker_start_command(
            self.config.railway_token,
            self.config.railway_env_id,
            self.config.railway_service_id,
            cmd
        )
        if not ok:
            self.api.send_message(chat_id, "❌ Gagal memperbarui startCommand di Railway.")
            return

        redeploy_worker(
            self.config.railway_token,
            self.config.railway_env_id,
            self.config.railway_service_id
        )
        self.config.set_status(is_running=True, progress_msg_id=prog_msg_id)
        self.api.send_message(chat_id, f"✅ Auto update anime berhasil dijalankan di Railway ({len(pending)} episode dalam antrean)!")

    def handle_download(self, chat_id: int | str, movie_id: str) -> None:
        if not self.config.railway_token:
            self.api.send_message(chat_id, "❌ Token Railway belum diatur! Pasang dulu dengan `/token <token>`.")
            return
        if not movie_id:
            self.api.send_message(chat_id, "❌ Masukkan Anime ID. Contoh: `/download 6635`")
            return

        pending = self.get_pending_downloads(movie_id=movie_id)
        if not pending:
            self.api.send_message(chat_id, f"✅ Semua episode untuk Anime `{movie_id}` sudah terupload! Filter anti-duplicate aktif.")
            return

        prog_msg = self.api.send_message(chat_id, f"🚀 Menyiapkan download Anime `{movie_id}` ({len(pending)} episode pending)...\n`[░░░░░░░░░░] 0.0% Initializing`")
        prog_msg_id = prog_msg.get("message_id", 0) if prog_msg else 0

        cmd = generate_worker_payload(
            bot_token=self.config.bot_token,
            chat_id=chat_id,
            progress_msg_id=prog_msg_id,
            target_mode="manual",
            movie_id=movie_id,
            streamer_url=self.config.streamer_url,
            log_channel=self.config.log_channel,
            api_id=self.config.api_id,
            api_hash=self.config.api_hash,
            items=pending
        )

        set_worker_start_command(
            self.config.railway_token,
            self.config.railway_env_id,
            self.config.railway_service_id,
            cmd
        )
        redeploy_worker(
            self.config.railway_token,
            self.config.railway_env_id,
            self.config.railway_service_id
        )
        self.config.set_status(is_running=True, progress_msg_id=prog_msg_id)
        self.api.send_message(chat_id, f"✅ Manual download untuk Anime `{movie_id}` dimulai di Railway ({len(pending)} episode)!")

    def handle_stop(self, chat_id: int | str) -> None:
        if not self.config.railway_token:
            self.api.send_message(chat_id, "❌ Token Railway belum diatur.")
            return

        self.api.send_message(chat_id, "🛑 Menghentikan Railway Worker...")
        # 1. Terminate active deployment immediately
        dep = get_latest_deployment(self.config.railway_token, self.config.railway_service_id, self.config.railway_env_id)
        if dep and dep.get("id"):
            stop_worker(self.config.railway_token, dep["id"])

        # 2. Set idle command
        set_worker_start_command(
            self.config.railway_token,
            self.config.railway_env_id,
            self.config.railway_service_id,
            'python3 -c "import time; print(\'Worker idle\'); time.sleep(86400)"'
        )
        self.config.set_status(is_running=False)
        self.api.send_message(chat_id, "🛑 Railway Worker berhasil dihentikan!")

    def handle_status(self, chat_id: int | str) -> None:
        text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
        self.api.send_message(chat_id, text)

    def dispatch_message(self, msg: dict) -> None:
        chat_id = msg.get("chat", {}).get("id")
        user_id = (msg.get("from") or {}).get("id") or chat_id
        text = msg.get("text", "")
        if not chat_id or not text:
            return

        if not self.is_admin(user_id):
            self.api.send_message(chat_id, "⛔ Unauthorized. Akses ditolak.")
            return

        cmd, arg = parse_command(text)
        if cmd in ("start", "menu"):
            self.handle_start(chat_id)
        elif cmd == "token":
            self.handle_token(chat_id, arg)
        elif cmd == "mode":
            self.handle_mode(chat_id, arg)
        elif cmd == "start_auto":
            self.handle_start_auto(chat_id)
        elif cmd == "download":
            self.handle_download(chat_id, arg)
        elif cmd == "stop":
            self.handle_stop(chat_id)
        elif cmd == "status":
            self.handle_status(chat_id)

    def dispatch_callback(self, cq: dict) -> None:
        cq_id = cq.get("id")
        user_id = (cq.get("from") or {}).get("id")
        data = cq.get("data", "")
        msg = cq.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        if not chat_id:
            return

        if not self.is_admin(user_id or chat_id):
            self.api.answer_callback(cq_id, "⛔ Akses ditolak")
            return

        self.api.answer_callback(cq_id, "Memproses...")
        if data == "btn_start_auto":
            self.handle_start_auto(chat_id)
        elif data == "btn_stop":
            self.handle_stop(chat_id)
        elif data == "btn_toggle_mode":
            self.handle_mode(chat_id)
            text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
            kb = build_menu_keyboard(self.config.mode, self.config.is_running)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data == "btn_status":
            self.handle_status(chat_id)
        elif data == "btn_menu":
            text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
            kb = build_menu_keyboard(self.config.mode, self.config.is_running)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)

    def run_polling(self) -> None:
        offset = 0
        print("MasterBot polling loop started...")
        while True:
            try:
                updates = self.api.get_updates(offset=offset, timeout=10)
                for u in updates:
                    offset = max(offset, u["update_id"] + 1)
                    if "message" in u:
                        self.dispatch_message(u["message"])
                    elif "callback_query" in u:
                        self.dispatch_callback(u["callback_query"])
            except Exception as e:
                print(f"Polling loop error: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    cfg = Config()
    bot = MasterBot(cfg)
    bot.run_polling()
