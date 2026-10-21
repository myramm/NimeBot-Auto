import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    acc_str = railway_email if railway_email else "(Belum diatur)"
    
    lines = [
        "🤖 **ReNime Anime Auto-Uploader Master Bot**",
        "──────────────────────────────",
        f"⚡ **Status:** `{status_str}`",
        f"🔄 **Mode:** `{mode_str}`",
        f"☁️ **Railway Account:** `{acc_str}`",
        "──────────────────────────────",
        "Semua kontrol dapat diakses lewat tombol di bawah 👇"
    ]
    return "\n".join(lines)

def build_menu_keyboard(mode: str, is_running: bool, streamer_url: str = "") -> dict:
    start_stop_btn = (
        {"text": "⏹ Stop Worker", "callback_data": "btn_stop"}
        if is_running else
        {"text": "▶️ Start Auto Update", "callback_data": "btn_start_auto"}
    )
    toggle_mode_text = "🔄 Mode: [MANUAL] ➔ AUTO" if mode == "manual" else "🔄 Mode: [AUTO] ➔ MANUAL"
    
    rows = [
        [start_stop_btn],
        [
            {"text": toggle_mode_text, "callback_data": "btn_toggle_mode"},
            {"text": "🎬 Katalog Anime", "callback_data": "btn_catalog_0"}
        ],
        [
            {"text": "📊 Status & Kuota", "callback_data": "btn_status"},
            {"text": "🔑 Ganti Token Railway", "callback_data": "btn_token_info"}
        ],
        [
            {"text": "🔄 Refresh Menu", "callback_data": "btn_menu"}
        ]
    ]
    if streamer_url:
        rows[-1].append({"text": "🌐 Web Streamer", "url": streamer_url})
    return {"inline_keyboard": rows}

def build_catalog_keyboard(page: int = 0) -> Tuple[str, dict]:
    cat = get_latest_catalog()
    per_page = 6
    total_pages = max(1, (len(cat) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    
    items = cat[page * per_page : (page + 1) * per_page]
    text = f"🎬 **Katalog Anime Terbaru (Halaman {page + 1}/{total_pages})**\nSilakan pilih anime yang ingin diunduh via Railway:\n"
    
    rows = []
    for item in items:
        m_id = item.get("id")
        title = item.get("title", "Unknown")[:30]
        status = item.get("status", "")
        btn_text = f"🎬 {title} [{status}]"
        rows.append([{"text": btn_text, "callback_data": f"anime_{m_id}"}])
        
    nav_row = []
    if page > 0:
        nav_row.append({"text": "⬅️ Sebelumnya", "callback_data": f"btn_catalog_{page - 1}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Selanjutnya ➡️", "callback_data": f"btn_catalog_{page + 1}"})
    if nav_row:
        rows.append(nav_row)
        
    rows.append([{"text": "🔙 Kembali ke Menu Utama", "callback_data": "btn_menu"}])
    return text, {"inline_keyboard": rows}

def build_anime_detail_keyboard(movie_id: str, db_path: str = "anime_db.sqlite") -> Tuple[str, dict]:
    det = get_movie_details(movie_id)
    title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
    status = det.get("status", "Unknown") if det else "Unknown"
    year = det.get("year", "-") if det else "-"
    genre = det.get("genre", "-") if det else "-"
    synopsis = det.get("synopsis", "") if det else ""
    if len(synopsis) > 200:
        synopsis = synopsis[:200] + "..."

    episodes = get_movie_episodes(movie_id)
    uploaded = get_uploaded_episodes(movie_id, db_path=db_path)
    up_count = len(set(u["ep_index"] for u in uploaded))

    text = (
        f"🎬 **{title}**\n"
        f"──────────────────────────────\n"
        f"📌 **Status:** `{status}` | **Tahun:** `{year}`\n"
        f"🏷 **Genre:** `{genre}`\n"
        f"🎞 **Total Episode:** `{len(episodes)}` | **Terupload:** `{up_count}`\n"
        f"──────────────────────────────\n"
        f"📖 _{synopsis}_\n"
    )

    rows = [
        [{"text": "⚡ Download Semua Episode di Railway", "callback_data": f"dlall_{movie_id}"}],
        [{"text": "📌 Pilih Episode Tertentu", "callback_data": f"epmenu_{movie_id}"}],
        [{"text": "🔙 Kembali ke Katalog", "callback_data": "btn_catalog_0"}]
    ]
    return text, {"inline_keyboard": rows}

def build_episodes_keyboard(movie_id: str, db_path: str = "anime_db.sqlite") -> Tuple[str, dict]:
    det = get_movie_details(movie_id)
    title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
    episodes = get_movie_episodes(movie_id)

    text = f"🎬 **{title}**\nPilih episode yang ingin diunggah ke Railway:\n(Tanda ✅ = sudah diupload)"

    rows = []
    curr_row = []
    for ep in episodes:
        ep_id = ep.get("id")
        ep_idx = int(ep.get("index", 0))
        is_done = has_uploaded(movie_id, ep_idx, "720p", db_path=db_path) or has_uploaded(movie_id, ep_idx, "1080p", db_path=db_path)
        icon = "✅" if is_done else "📥"
        btn_text = f"{icon} Ep {ep_idx}"
        curr_row.append({"text": btn_text, "callback_data": f"dlep_{movie_id}_{ep_id}_{ep_idx}"})
        if len(curr_row) == 3:
            rows.append(curr_row)
            curr_row = []
    if curr_row:
        rows.append(curr_row)

    rows.append([{"text": "🔙 Kembali ke Detail", "callback_data": f"anime_{movie_id}"}])
    return text, {"inline_keyboard": rows}

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

    def get_updates(self, offset: int = 0, timeout: int = 15) -> list:
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
        self.user_state: Dict[int, Dict[str, Any]] = {}
        init_db(self.db_path)

    def is_admin(self, user_id: int) -> bool:
        if self.config.admin_user_id == 0:
            self.config.admin_user_id = user_id
            self.config.save()
            return True
        return user_id == self.config.admin_user_id

    def get_pending_downloads(self, movie_id: str = "") -> List[Dict[str, Any]]:
        targets = []
        if movie_id:
            det = get_movie_details(movie_id)
            title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
            targets.append({"id": str(movie_id), "title": title})
        else:
            cat = get_latest_catalog()
            targets.extend(cat[:6])

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
        kb = build_menu_keyboard(self.config.mode, self.config.is_running, self.config.streamer_url)
        self.api.send_message(chat_id, text, reply_markup=kb)

    def process_token_input(self, chat_id: int | str, user_id: int, token: str) -> None:
        token = token.strip()
        if not token:
            self.api.send_message(chat_id, "❌ Token kosong. Silakan paste Token Railway Anda:")
            return

        self.api.send_message(chat_id, "🔍 Memverifikasi token Railway...")
        user_info = verify_railway_token(token)
        if not user_info:
            kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": "btn_cancel_state"}]]}
            self.api.send_message(chat_id, "❌ Token Railway tidak valid atau ditolak oleh Railway API.\nSilakan kirim token yang benar:", reply_markup=kb)
            return

        email = user_info.get("email", "Unknown")
        try:
            p_id, e_id, s_id = ensure_railway_project(token)
            self.config.set_token(token, email=email)
            self.config.railway_project_id = p_id
            self.config.railway_env_id = e_id
            self.config.railway_service_id = s_id
            self.config.save()
        except Exception as e:
            self.api.send_message(chat_id, f"⚠️ Gagal auto-provisioning project di Railway: {e}")
            return

        state = self.user_state.pop(user_id, {})
        action = state.get("action")
        self.api.send_message(chat_id, f"✅ Token valid! Akun: `{email}`\n🚀 Menghubungkan worker Railway...")

        if action == "auto":
            self.handle_start_auto(chat_id, bypass_prompt=True)
        elif action == "download":
            self.handle_download(chat_id, state.get("movie_id", ""), bypass_prompt=True)
        elif action == "single_ep":
            self.handle_download_single_ep(chat_id, state.get("movie_id", ""), state.get("ep_id", ""), state.get("ep_idx", 0), bypass_prompt=True)
        else:
            self.api.send_message(chat_id, f"🎉 Akun Railway berhasil disimpan: `{email}`")
            self.handle_start(chat_id)

    def handle_token(self, chat_id: int | str, token: str) -> None:
        user_id = int(chat_id)
        if not token:
            self.user_state[user_id] = {"action": "update_token", "waiting_for": "token"}
            kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": "btn_cancel_state"}]]}
            self.api.send_message(chat_id, "🔑 **Masukkan Token Railway**\n\nSilakan langsung **paste Token Railway** Anda ke chat ini:", reply_markup=kb)
            return
        self.process_token_input(chat_id, user_id, token)

    def handle_mode(self, chat_id: int | str, new_mode: str = "") -> None:
        if new_mode in ("auto", "manual"):
            self.config.set_mode(new_mode)
        else:
            self.config.set_mode("manual" if self.config.mode == "auto" else "auto")
        self.api.send_message(chat_id, f"🔄 Mode sistem diubah ke: **{self.config.mode.upper()}**")

    def handle_start_auto(self, chat_id: int | str, bypass_prompt: bool = False) -> None:
        user_id = int(chat_id)
        if not bypass_prompt:
            if not self.config.railway_token:
                self.user_state[user_id] = {"action": "auto", "waiting_for": "token"}
                kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": "btn_cancel_state"}]]}
                self.api.send_message(
                    chat_id,
                    "🔑 **Masukkan Token Railway**\n\nUntuk memulai auto update di Railway, silakan **paste Token Railway** akun Anda langsung ke chat ini:",
                    reply_markup=kb
                )
                return
            else:
                kb = {
                    "inline_keyboard": [
                        [{"text": "🚀 Jalankan dengan Token Saat Ini", "callback_data": "btn_confirm_start_auto"}],
                        [{"text": "🔑 Input Token Baru", "callback_data": "btn_prompt_token_auto"}],
                        [{"text": "🔙 Batal", "callback_data": "btn_menu"}]
                    ]
                }
                text = (
                    "🔑 **Verifikasi Akun Railway**\n"
                    "──────────────────────────────\n"
                    f"Akun saat ini: `{self.config.railway_email or 'Connected'}`\n\n"
                    "Silakan pilih opsi untuk memulai:"
                )
                self.api.send_message(chat_id, text, reply_markup=kb)
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

    def handle_download(self, chat_id: int | str, movie_id: str, bypass_prompt: bool = False) -> None:
        user_id = int(chat_id)
        if not movie_id:
            self.api.send_message(chat_id, "❌ Masukkan Anime ID atau pilih lewat tombol `[🎬 Katalog Anime]`.")
            return

        if not bypass_prompt:
            if not self.config.railway_token:
                self.user_state[user_id] = {"action": "download", "movie_id": movie_id, "waiting_for": "token"}
                kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": f"anime_{movie_id}"}]]}
                self.api.send_message(
                    chat_id,
                    f"🔑 **Masukkan Token Railway**\n\nSilakan **paste Token Railway** akun Anda langsung ke chat ini untuk mengunduh Anime `{movie_id}`:",
                    reply_markup=kb
                )
                return
            else:
                kb = {
                    "inline_keyboard": [
                        [{"text": "🚀 Download dengan Akun Saat Ini", "callback_data": f"confirm_dlall_{movie_id}"}],
                        [{"text": "🔑 Input Token Akun Lain", "callback_data": f"prompt_token_dl_{movie_id}"}],
                        [{"text": "🔙 Kembali", "callback_data": f"anime_{movie_id}"}]
                    ]
                }
                text = (
                    f"🔑 **Konfirmasi Akun Railway**\n"
                    f"Anime ID: `{movie_id}`\n"
                    f"Akun saat ini: `{self.config.railway_email or 'Connected'}`\n\n"
                    f"Silakan pilih opsi:"
                )
                self.api.send_message(chat_id, text, reply_markup=kb)
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

    def handle_download_single_ep(self, chat_id: int | str, movie_id: str, ep_id: str, ep_idx: int, bypass_prompt: bool = False) -> None:
        user_id = int(chat_id)
        if not bypass_prompt and not self.config.railway_token:
            self.user_state[user_id] = {"action": "single_ep", "movie_id": movie_id, "ep_id": ep_id, "ep_idx": ep_idx, "waiting_for": "token"}
            kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": f"epmenu_{movie_id}"}]]}
            self.api.send_message(chat_id, "🔑 **Masukkan Token Railway**\n\nSilakan **paste Token Railway** akun Anda ke chat ini:", reply_markup=kb)
            return

        det = get_movie_details(movie_id)
        title = det.get("title", f"Anime {movie_id}") if det else f"Anime {movie_id}"
        streams = get_episode_streams(ep_id)
        
        pending = []
        for s in streams:
            q = s.get("quality", "720p")
            url = s.get("url")
            if url and not has_uploaded(movie_id, ep_idx, q, db_path=self.db_path):
                pending.append({
                    "movie_id": movie_id,
                    "title": title,
                    "ep_index": ep_idx,
                    "quality": q,
                    "stream_url": url
                })

        if not pending:
            self.api.send_message(chat_id, f"✅ Episode {ep_idx} dari `{title}` sudah terupload!")
            return

        prog_msg = self.api.send_message(chat_id, f"🚀 Menyiapkan Episode {ep_idx} dari `{title}` di Railway...\n`[░░░░░░░░░░] 0.0% Initializing`")
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
        self.api.send_message(chat_id, f"✅ Episode {ep_idx} sedang diunggah di Railway!")

    def handle_stop(self, chat_id: int | str) -> None:
        if not self.config.railway_token:
            self.api.send_message(chat_id, "❌ Token Railway belum diatur.")
            return

        self.api.send_message(chat_id, "🛑 Menghentikan Railway Worker...")
        dep = get_latest_deployment(self.config.railway_token, self.config.railway_service_id, self.config.railway_env_id)
        if dep and dep.get("id"):
            stop_worker(self.config.railway_token, dep["id"])

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
        kb = build_menu_keyboard(self.config.mode, self.config.is_running, self.config.streamer_url)
        self.api.send_message(chat_id, text, reply_markup=kb)

    def dispatch_message(self, msg: dict) -> None:
        chat_id = msg.get("chat", {}).get("id")
        user_id = (msg.get("from") or {}).get("id") or chat_id
        text = msg.get("text", "")
        if not chat_id or not text:
            return

        if not self.is_admin(user_id):
            self.api.send_message(chat_id, "⛔ Unauthorized. Akses ditolak.")
            return

        # Check if user is in an input state (e.g. waiting for token paste)
        if user_id in self.user_state and self.user_state[user_id].get("waiting_for") == "token":
            if text.startswith("/cancel") or text.lower() == "batal":
                self.user_state.pop(user_id, None)
                self.api.send_message(chat_id, "❌ Pengisian token dibatalkan.")
                self.handle_start(chat_id)
                return

            raw_token = text
            if text.startswith("/token"):
                _, raw_token = parse_command(text)
            self.process_token_input(chat_id, user_id, raw_token)
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
        elif data == "btn_confirm_start_auto":
            self.handle_start_auto(chat_id, bypass_prompt=True)
        elif data == "btn_prompt_token_auto":
            self.user_state[user_id] = {"action": "auto", "waiting_for": "token"}
            kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": "btn_cancel_state"}]]}
            self.api.edit_message(
                chat_id,
                msg_id,
                "🔑 **Silakan Kirim Token Railway Anda:**\n\nCukup **paste** token Railway Anda langsung ke chat ini (tanpa perlu ketik command).\nBot akan memverifikasi dan langsung memulai auto update!",
                reply_markup=kb
            )
        elif data == "btn_stop":
            self.handle_stop(chat_id)
        elif data == "btn_toggle_mode":
            self.handle_mode(chat_id)
            text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
            kb = build_menu_keyboard(self.config.mode, self.config.is_running, self.config.streamer_url)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data == "btn_status":
            self.handle_status(chat_id)
        elif data == "btn_menu":
            text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
            kb = build_menu_keyboard(self.config.mode, self.config.is_running, self.config.streamer_url)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data == "btn_cancel_state":
            self.user_state.pop(user_id, None)
            text = format_menu_text(self.config.mode, self.config.is_running, self.config.railway_email)
            kb = build_menu_keyboard(self.config.mode, self.config.is_running, self.config.streamer_url)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data.startswith("btn_catalog_"):
            page = int(data.split("_")[-1])
            text, kb = build_catalog_keyboard(page)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data.startswith("anime_"):
            m_id = data.split("_")[1]
            text, kb = build_anime_detail_keyboard(m_id, db_path=self.db_path)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data.startswith("epmenu_"):
            m_id = data.split("_")[1]
            text, kb = build_episodes_keyboard(m_id, db_path=self.db_path)
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data.startswith("dlall_"):
            m_id = data.split("_")[1]
            self.handle_download(chat_id, m_id)
        elif data.startswith("confirm_dlall_"):
            m_id = data.split("_")[2]
            self.handle_download(chat_id, m_id, bypass_prompt=True)
        elif data.startswith("prompt_token_dl_"):
            m_id = data.split("_")[3]
            self.user_state[user_id] = {"action": "download", "movie_id": m_id, "waiting_for": "token"}
            kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": f"anime_{m_id}"}]]}
            self.api.edit_message(
                chat_id,
                msg_id,
                f"🔑 **Silakan Kirim Token Railway Anda:**\n\nCukup **paste** token Railway Anda ke chat ini untuk mengunduh Anime `{m_id}`:",
                reply_markup=kb
            )
        elif data.startswith("dlep_"):
            parts = data.split("_")
            m_id, ep_id, ep_idx = parts[1], parts[2], int(parts[3])
            self.handle_download_single_ep(chat_id, m_id, ep_id, ep_idx)
        elif data == "btn_token_info":
            text = (
                "🔑 **Pengaturan Akun Railway**\n"
                "──────────────────────────────\n"
                f"👤 **Akun Aktif:** `{self.config.railway_email or 'Belum diatur'}`\n"
                f"📦 **Project:** `{self.config.railway_project_id or '-'}`\n"
                f"⚙️ **Service:** `{self.config.railway_service_id or '-'}`\n\n"
                "Klik tombol di bawah untuk memasukkan token baru:"
            )
            kb = {
                "inline_keyboard": [
                    [{"text": "🔑 Masukkan / Ganti Token Baru", "callback_data": "btn_prompt_token_update"}],
                    [{"text": "🔙 Kembali ke Menu", "callback_data": "btn_menu"}]
                ]
            }
            self.api.edit_message(chat_id, msg_id, text, reply_markup=kb)
        elif data == "btn_prompt_token_update":
            self.user_state[user_id] = {"action": "update_token", "waiting_for": "token"}
            kb = {"inline_keyboard": [[{"text": "🔙 Batal", "callback_data": "btn_menu"}]]}
            self.api.edit_message(
                chat_id,
                msg_id,
                "🔑 **Ganti Akun Railway**\n\nSilakan **paste Token Railway baru** Anda langsung ke chat ini:\n(Bot akan otomatis memverifikasi)",
                reply_markup=kb
            )

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
