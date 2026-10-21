import os
import json
from typing import Optional

DEFAULT_CONFIG_PATH = "config.json"

class Config:
    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path
        self.bot_token = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
        self.api_id = os.environ.get("API_ID", "37124183")
        self.api_hash = os.environ.get("API_HASH", "f4d846728e694c2d1071ca7d3b2f6c81")
        self.log_channel = os.environ.get("LOG_CHANNEL", "-1004462080662")
        self.streamer_url = os.environ.get("STREAMER_URL", "https://streamer-production-c24a.up.railway.app")
        
        self.railway_token = os.environ.get("RAILWAY_TOKEN", "")
        self.railway_email = ""
        self.railway_project_id = os.environ.get("RAILWAY_PROJECT_ID", "9cb30572-6794-4cf6-9df2-778e6933d48d")
        self.railway_env_id = os.environ.get("RAILWAY_ENV_ID", "035a4bdc-a53c-431a-b077-992b0224ea67")
        self.railway_service_id = os.environ.get("RAILWAY_SERVICE_ID", "4b50b07a-8ee6-4ce7-b7fa-90e29428c6d8")
        
        self.mode = "auto"  # 'auto' or 'manual'
        self.is_running = False
        self.active_deployment_id = ""
        self.active_progress_msg_id = 0
        self.admin_chat_id = 0
        
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.bot_token = data.get("bot_token", self.bot_token)
                    self.api_id = str(data.get("api_id", self.api_id))
                    self.api_hash = data.get("api_hash", self.api_hash)
                    self.log_channel = str(data.get("log_channel", self.log_channel))
                    self.streamer_url = data.get("streamer_url", self.streamer_url)
                    self.railway_token = data.get("railway_token", self.railway_token)
                    self.railway_email = data.get("railway_email", self.railway_email)
                    self.railway_project_id = data.get("railway_project_id", self.railway_project_id)
                    self.railway_env_id = data.get("railway_env_id", self.railway_env_id)
                    self.railway_service_id = data.get("railway_service_id", self.railway_service_id)
                    self.mode = data.get("mode", self.mode)
                    self.is_running = data.get("is_running", self.is_running)
                    self.active_deployment_id = data.get("active_deployment_id", self.active_deployment_id)
                    self.active_progress_msg_id = data.get("active_progress_msg_id", self.active_progress_msg_id)
                    self.admin_chat_id = data.get("admin_chat_id", self.admin_chat_id)
            except Exception as e:
                print(f"Error loading config {self.path}: {e}")

    def save(self) -> None:
        data = {
            "bot_token": self.bot_token,
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "log_channel": self.log_channel,
            "streamer_url": self.streamer_url,
            "railway_token": self.railway_token,
            "railway_email": self.railway_email,
            "railway_project_id": self.railway_project_id,
            "railway_env_id": self.railway_env_id,
            "railway_service_id": self.railway_service_id,
            "mode": self.mode,
            "is_running": self.is_running,
            "active_deployment_id": self.active_deployment_id,
            "active_progress_msg_id": self.active_progress_msg_id,
            "admin_chat_id": self.admin_chat_id
        }
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def set_token(self, token: str, email: str = "") -> None:
        self.railway_token = token
        if email:
            self.railway_email = email
        self.save()

    def set_mode(self, mode: str) -> None:
        if mode in ("auto", "manual"):
            self.mode = mode
            self.save()

    def set_status(self, is_running: bool, deployment_id: str = "", progress_msg_id: int = 0) -> None:
        self.is_running = is_running
        if deployment_id:
            self.active_deployment_id = deployment_id
        if progress_msg_id:
            self.active_progress_msg_id = progress_msg_id
        self.save()
