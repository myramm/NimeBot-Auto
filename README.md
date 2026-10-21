# 🤖 NimeBot Auto

Automated Anime Downloader & Uploader to Telegram with Real-Time Progress, Cloud Streaming, and Dynamic Scraper Configuration on Railway.

---

## ✨ Features

- ⚡ **Full Cloud Deployment (Railway):** Runs 24/7 directly on a lightweight Railway container.
- 📊 **Real-Time Moving Progress Bar:** Displays percentage `[████░░░░] 45.0%`, size, download/upload speed, and dynamic ETA in Telegram.
- 🛡️ **Anti-Duplicate Filter:** SQLite-backed tracking prevents duplicate downloads across restarts or redeployments.
- ⚙️ **Configurable Dynamic Scraper:**
  - Base URL, APK Version, User-Agent, and Quality Filters (`ALL`, `720p`, `1080p`, `480p`) can be modified live directly via Telegram interactive buttons (`⚙️ Konfigurasi Scraper`) or environment variables without redeploying.
- 🌐 **Web Streaming & Direct Download:** Injects clickable interactive buttons linking directly to the streaming player and direct download service.
- 🎛️ **Dual Operation Modes:**
  - **Auto Mode:** Automatically scans top newest catalog items and uploads new aired episodes.
  - **Manual Mode:** Interactive catalog pagination and episode picker.
- 🔑 **Interactive Multi-Account Railway Setup:** Switch Railway accounts on the fly by pasting your Railway token.

---

## 🛠️ Environment Variables

| Variable | Description | Default |
|---|---|---|
| `BOT_TOKEN` | Telegram Bot Token from @BotFather | *(Required)* |
| `API_ID` | Telegram App API ID from my.telegram.org | *(Required)* |
| `API_HASH` | Telegram App API Hash | *(Required)* |
| `LOG_CHANNEL` | Target Telegram Channel ID for anime uploads | `-1004462080662` |
| `STREAMER_URL` | Base URL of Web Streamer Service | `https://streamer-production-c24a.up.railway.app` |
| `ADMIN_USER_ID` | Owner Telegram User ID | `6164710787` |
| `SCRAPER_BASE_URL` | Base URL of Anime Scraper API | `https://xyz-api.animein.net` |
| `SCRAPER_APK_VER` | APK version header | `5.2.2` |
| `SCRAPER_USER_AGENT` | Custom User-Agent header | `okhttp/4.12.0` |

---

## 🚀 Deployment to Railway

### Using Railway CLI
```bash
cd file-streamer
railway up -s uploader -e production -d
```

### Viewing Live Logs
```bash
cd file-streamer
railway logs -s uploader
```

---

## 🧪 Running Tests

```bash
PYTHONPATH=. pytest tests/ -v
```

---

## 📄 License
MIT License
