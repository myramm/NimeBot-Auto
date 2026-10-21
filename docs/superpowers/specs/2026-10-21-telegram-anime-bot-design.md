# Design Document: Telegram Anime Auto-Uploader & Controller Bot

**Date:** 2026-10-21  
**Status:** Draft / Reviewing  
**Author:** Antigravity AI  

---

## 1. Intent & Purpose

The user requires a completely redesigned, reliable, and user-friendly Telegram Bot system for streaming and archiving anime directly from Animein into Telegram storage channels.

The previous bot suffered from:
- Lack of interactive controls (cannot cleanly start or stop without manual terminal interventions).
- Hardcoded starting queues causing repeated uploads of earlier anime (e.g., Macross F duplicate spam).
- No real-time visual progress indicator (blind uploads for 10+ minutes per episode).
- Fixed Railway account without easy token switching.

The new system will provide an interactive Master Bot that controls the Railway worker via dynamic API tokens, supports both Auto and Manual downloading modes, displays real-time moving progress bars, strictly enforces anti-duplicate filters, and links media to the Railway web streamer.

---

## 2. Goals, Non-Goals, & Anti-Goals

### Goals
- **Start / Stop Controls:** Instant control via Telegram chat commands or inline keyboard buttons to start or pause auto-updating.
- **Two Operation Modes:**
  - *Mode Otomatis:* Automatically polls Animein catalog for new releases and downloads/uploads in sequence.
  - *Mode Manual:* Allows the admin to search/input specific anime ID or title, choose specific episodes or full season.
- **Real-Time Moving Progress Bar:** A single message in the admin chat that updates every 2–3 seconds showing:
  - Anime title & episode number
  - Quality being processed (360p, 480p, 720p, 1080p)
  - State (`Downloading...` vs `Uploading to Telegram...`)
  - Progress bar (`[████████░░] 80%`), transferred size / total size, speed (MB/s), and ETA.
- **Dynamic Railway Token Switching:** An admin command (`/token <railway_token>`) to update or authenticate with different Railway accounts dynamically without editing code.
- **Strict Anti-Duplicate Filter:** Permanent tracking of `(movie_id, ep_index, quality)` in a persistent state file plus Telegram channel verification to ensure no anime or episode is ever uploaded twice.
- **Integrated Streaming URL:** Uploaded video cards provide clickable inline buttons for direct download and web streaming via the Railway `streamer` service.

### Non-Goals
- Building a custom video transcoding/encoding pipeline (we use direct pre-encoded MP4 files from Animein storage).
- Providing public user search bots (this is an administrator uploader and backend channel feeder).

### Anti-Goals
- No silent duplicate uploads under any circumstances.
- No heavy download/upload processing running on the local 1 GB RAM VPS that could trigger OOM-Kills; heavy transfer workloads remain on Railway worker instances.

---

## 3. Architecture & Components

```
┌────────────────────────────────────────────────────────┐
│                   TELEGRAM ADMIN CHAT                  │
│   (Commands: /start, /mode, /token, /stop, buttons)    │
└───────────────────────────▲────────────────────────────┘
                            │
                            │ Live Edit Progress Messages
                            ▼
┌────────────────────────────────────────────────────────┐
│                   MASTER TELEGRAM BOT                  │
│              (Controller & State Manager)              │
│  - Mode Handler (Auto / Manual)                        │
│  - Anti-Duplicate Database (anime_db.sqlite)           │
│  - Animein API Client (Catalog & Metadata)             │
│  - Railway GraphQL Client (Token Manager & Lifecycle)  │
└─────────────┬──────────────────────────────────────────┘
              │ 1. Dispatches Job / Starts Deployment
              ▼
┌────────────────────────────────────────────────────────┐
│                   RAILWAY SERVICES                     │
│                                                        │
│ 1. Uploader Worker (Python + Telethon)                 │
│    - Directly downloads from storages.animein.net      │
│    - Directly updates Admin progress message via       │
│      Telegram Bot API (editMessageText, throttled 2s)  │
│    - Uploads video chunks to Telegram Storage Channel  │
│    - Attaches Streamer URL buttons                     │
│                                                        │
│ 2. Streamer Service (TG-FileStreamBot)                 │
│    - Keeps public HTTP stream URLs alive               │
└─────────────────────────────┬──────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────┐
│               TELEGRAM STORAGE CHANNEL                 │
│                   "STORAGE ANIME"                      │
│ - Poster & Info Cards                                  │
│ - Multi-Quality Video Files (360p, 480p, 720p, 1080p)  │
│ - Inline Stream Buttons                                │
└────────────────────────────────────────────────────────┘
```

### Technical Decisions & Contracts:

1. **Inter-Process Progress Reporting (Zero-Inbound Networking):**
   - *Design Call:* The Railway worker does NOT need to make network requests back to the VPS IP. Instead, when a task starts, the Master Bot creates an active status message in the Admin chat and passes `ADMIN_CHAT_ID` and `PROGRESS_MSG_ID` to the worker (via job parameters or Railway env/command).
   - The worker directly edits that status message via Telegram Bot API (`editMessageText`) with a 2-second rate-limiting throttle. This eliminates any inbound firewall, port forwarding, or VPS domain dependencies.

2. **Animein API Contract (Verified):**
   - Base URL: `https://xyz-api.animein.net/`
   - Headers: `User-Agent: okhttp/4.12.0`, `apk_ver: 5.2.2`
   - Key Endpoints:
     - `3/2/home/new`: Latest releases catalog.
     - `3/2/movie/detail/{movie_id}`: Synopsis, poster URL, studio, year, genre.
     - `3/2/movie/episode/{movie_id}`: Complete episode list.
     - `3/2/episode/streamnew/{ep_id}`: Direct stream/download MP4 URLs per quality.
   - Storage CDN: `https://storages.animein.net/` with `Referer: https://xyz-api.animein.net`.

3. **Railway Token & Lifecycle Management (Full Auto Provisioning):**
   - Uses Railway's Public GraphQL API (`https://backboard.railway.com/graphql/v2`).
   - Token Authentication: `Authorization: Bearer <TOKEN>`.
   - When a token is supplied via `/token <token>`:
     - The bot automatically verifies and queries the account workspaces and projects.
     - **Auto-Detection:** If a project with `file-streamer` or `uploader` exists, it connects automatically.
     - **Auto-Provisioning:** If no project exists on the new account, the bot automatically creates the project and configures the required environment variables (`BOT_TOKEN`, `API_ID`, `API_HASH`, `LOG_CHANNEL`, `STREAMER_URL`) without requiring any manual setup by the user.
   - Lifecycle controls:
     - Start: `serviceInstanceRedeploy(environmentId, serviceId)`.
     - Stop: `deploymentRemove(id)` (cancels active deployment immediately, scaling resource use to 0).

4. **Storage Channel & Streamer URL Integration:**
   - Streamer URL pattern: `https://<PUBLIC_STREAMER_DOMAIN>/watch/{message_id}` or `{STREAMER_URL}` inline buttons.
   - Each upload sends message with HTML caption and inline buttons: `[📥 Download]` and `[🌐 Stream]`.

5. **Anti-Duplicate Filter System:**
   - Local SQLite database on VPS (`anime_db.sqlite`) tracks:
     `records(movie_id, ep_index, quality, telegram_msg_id, created_at)`.
   - Before queueing or executing an upload, the filter queries SQLite. If `(movie_id, ep_index, quality)` exists, it is skipped instantly.


---

## 4. Security & Configuration

- **Admin Whitelist:** Only the owner user ID is allowed to send bot commands or update Railway tokens.
- **Railway Token Storage:** Stored securely in a local config or `.env` on VPS, never exposed in public channels.
- **Memory Safety:** The Master Bot on VPS runs as an ultra-lightweight async listener (~30 MB RAM) to fit comfortably within the 1 GB container limit. Heavy video files are buffered in Railway's memory or ephemeral disk, never on VPS.

---

## 5. What is Left to the Builder

- Choice between SQLite or JSON for local history state.
- Exact Telegram button layout (inline keyboard layout for `/start` menu).
- Progress update throttle interval (2.5 to 3.0 seconds to prevent Telegram rate limits on `editMessageText`).
