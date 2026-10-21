# Telegram Anime Auto-Uploader & Controller Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a robust, modular Telegram Master Controller Bot that runs on VPS, manages Animein downloads/uploads on Railway with real-time animated progress bars, supports automatic and manual modes, dynamically manages Railway tokens with auto-provisioning, and strictly enforces zero duplicate uploads.

**Architecture:** A lightweight Master Bot runs on the VPS to handle administrative Telegram commands, queue management, and the persistent SQLite anti-duplicate database. Heavy video downloading and MTProto uploading are handled by the Railway worker, which reports live progress directly to the admin's Telegram chat via throttled `editMessageText` calls. The bot manages the Railway worker lifecycle and accounts dynamically through the Railway GraphQL API.

**Tech Stack:** Python 3, `urllib` / `aiohttp`, `telethon` (MTProto), `sqlite3`, Railway GraphQL API, Telegram Bot API.

**Spec:** [`docs/superpowers/specs/2026-10-21-telegram-anime-bot-design.md`](file:///root/docs/superpowers/specs/2026-10-21-telegram-anime-bot-design.md)

## Global Constraints

- Master Bot on VPS must run under 40 MB RAM footprint (strict VPS 1 GB RAM container limit).
- All video file transfers and buffering must happen on Railway worker, never on VPS disk.
- Zero duplicate uploads allowed under any restart or redeploy scenario.
- Rate-limit Telegram message updates to at least 2.0s intervals to prevent Telegram 429 FloodWait.
- Auto-provisioning on Railway must automatically detect existing projects or create a new project with required variables without manual user intervention.

## Review Focus

1. **Unstable Network / Stream Interruption:** If a download or upload stream drops mid-chunk, the worker must retry cleanly without leaving corrupt files or marking the episode as completed in SQLite.
2. **Invalid or Expired Railway Token:** If a user inputs an invalid Railway token via `/token`, the bot must validate it before saving and provide a clear error message instead of crashing.
3. **Telegram FloodWait (429) Handling:** If message editing encounters a rate limit, the progress updater must back off gracefully and continue transferring files without failing the upload.
4. **Episode Airing Gap:** In Auto Mode, if an anime in the catalog is ongoing with only partial episodes aired, the bot must upload all available episodes and cleanly advance to the next anime without infinite retries.
5. **Database Concurrency:** If multiple download events check the duplicate status simultaneously, SQLite transactions must prevent race conditions from allowing duplicate entries.

---

### Task 1: Anti-Duplicate Database Module

**Files:**
- Create: `src/database.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: None (Standard library `sqlite3`)
- Produces:
  - `init_db(db_path: str = "anime_db.sqlite") -> None`
  - `has_uploaded(movie_id: int | str, ep_index: int | str, quality: str, db_path: str = "anime_db.sqlite") -> bool`
  - `record_upload(movie_id: int | str, ep_index: int | str, quality: str, msg_id: int, anime_title: str = "", db_path: str = "anime_db.sqlite") -> None`
  - `get_uploaded_episodes(movie_id: int | str, db_path: str = "anime_db.sqlite") -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
import os
import pytest
from src.database import init_db, has_uploaded, record_upload, get_uploaded_episodes

TEST_DB = "tests/test_anime.sqlite"

@pytest.fixture(autouse=True)
def cleanup():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_database_operations():
    init_db(TEST_DB)
    assert not has_uploaded("6635", 1, "720p", db_path=TEST_DB)
    record_upload("6635", 1, "720p", msg_id=3100, anime_title="Yami Shibai 15", db_path=TEST_DB)
    assert has_uploaded("6635", 1, "720p", db_path=TEST_DB)
    assert not has_uploaded("6635", 1, "1080p", db_path=TEST_DB)
    records = get_uploaded_episodes("6635", db_path=TEST_DB)
    assert len(records) == 1
    assert records[0]["quality"] == "720p"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py -v`  
Expected: FAIL with ModuleNotFoundError or ImportError

- [ ] **Step 3: Implement `src/database.py`**

Implement SQLite table schema:
`CREATE TABLE IF NOT EXISTS records (movie_id TEXT, ep_index INTEGER, quality TEXT, msg_id INTEGER, anime_title TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(movie_id, ep_index, quality))`
Implement `init_db`, `has_uploaded`, `record_upload`, and `get_uploaded_episodes`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_database.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/database.py tests/test_database.py
git commit -m "feat: implement persistent anti-duplicate sqlite database module"
```

---

### Task 2: Animein API Client Module

**Files:**
- Create: `src/animein_client.py`
- Test: `tests/test_animein_client.py`

**Interfaces:**
- Consumes: None (Standard library `urllib.request`, `json`)
- Produces:
  - `get_latest_catalog() -> list[dict]`
  - `get_movie_details(movie_id: int | str) -> dict | None`
  - `get_movie_episodes(movie_id: int | str) -> list[dict]`
  - `get_episode_streams(ep_id: int | str) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import patch
import json
from src.animein_client import get_latest_catalog, get_movie_details, get_movie_episodes, get_episode_streams

def test_get_movie_episodes_parses_list():
    mock_response = {
        "status": 200,
        "data": {
            "episode": [
                {"id": "321211", "index": "1", "title": "Episode 1"},
                {"id": "321212", "index": "2", "title": "Episode 2"}
            ]
        }
    }
    with patch("src.animein_client.api_get", return_value=mock_response):
        eps = get_movie_episodes("6635")
        assert len(eps) == 2
        assert eps[0]["id"] == "321211"
        assert eps[0]["index"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_animein_client.py -v`  
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement `src/animein_client.py`**

Implement `api_get(endpoint)` targeting `https://xyz-api.animein.net/` with verified headers `User-Agent: okhttp/4.12.0`, `apk_ver: 5.2.2`. Implement catalog parsing, movie details, episode extraction, and stream server links extraction.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_animein_client.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/animein_client.py tests/test_animein_client.py
git commit -m "feat: implement animein api client"
```

---

### Task 3: Railway GraphQL Client & Auto-Provisioning

**Files:**
- Create: `src/railway_client.py`
- Test: `tests/test_railway_client.py`

**Interfaces:**
- Consumes: None (Standard library `urllib.request`, `json`)
- Produces:
  - `verify_railway_token(token: str) -> dict | None`
  - `ensure_railway_project(token: str, project_name: str = "file-streamer") -> tuple[str, str, str]` (returns `project_id, environment_id, service_id`)
  - `set_worker_start_command(token: str, env_id: str, service_id: str, command: str) -> bool`
  - `redeploy_worker(token: str, env_id: str, service_id: str) -> bool`
  - `stop_worker(token: str, deployment_id: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import patch
from src.railway_client import verify_railway_token

def test_verify_railway_token_success():
    mock_resp = {"data": {"me": {"email": "test@example.com"}}}
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        res = verify_railway_token("valid_token")
        assert res is not None
        assert res["email"] == "test@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_railway_client.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `src/railway_client.py`**

Implement GraphQL execution targeting `https://backboard.railway.com/graphql/v2`. Implement token verification (`query { me { email } }`), auto-detection of `file-streamer` project, environment and service lookup, and deployment commands (`serviceInstanceUpdate`, `serviceInstanceRedeploy`, `deploymentRemove`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_railway_client.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/railway_client.py tests/test_railway_client.py
git commit -m "feat: implement railway graphql client with auto-provisioning"
```

---

### Task 4: Railway Worker Script with Live Animated Progress Updates

**Files:**
- Create: `src/worker.py`
- Create: `src/worker_template.py` (Helper to package worker script for Railway deployment)
- Test: `tests/test_worker_progress.py`

**Interfaces:**
- Consumes: `src.animein_client`, `src.database`
- Produces:
  - `render_progress_bar(current: int, total: int, width: int = 12) -> str`
  - `format_progress_message(title: str, ep: int, quality: str, status: str, current: int, total: int, speed_bps: float) -> str`
  - Standalone worker execution script for Railway container.

- [ ] **Step 1: Write the failing test**

```python
from src.worker import render_progress_bar, format_progress_message

def test_render_progress_bar():
    bar = render_progress_bar(50, 100, width=10)
    assert bar == "[█████░░░░░] 50.0%"

def test_format_progress_message():
    msg = format_progress_message(
        title="Yami Shibai 15",
        ep=10,
        quality="720p",
        status="Uploading",
        current=50 * 1024 * 1024,
        total=100 * 1024 * 1024,
        speed_bps=1.5 * 1024 * 1024
    )
    assert "Yami Shibai 15" in msg
    assert "Episode 10" in msg
    assert "720p" in msg
    assert "Uploading" in msg
    assert "50.0%" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_progress.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `src/worker.py` and `render_progress_bar`**

Implement animated progress rendering. Add rate-limited progress callback (minimum 2.0s interval) that calls `api.telegram.org/bot<TOKEN>/editMessageText`. Implement direct download from `storages.animein.net` and chunked upload to Telegram channel with inline streamer buttons.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker_progress.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/worker.py src/worker_template.py tests/test_worker_progress.py
git commit -m "feat: implement worker engine with live animated progress bar"
```

---

### Task 5: Master Controller Telegram Bot

**Files:**
- Create: `src/master_bot.py`
- Create: `src/config.py`
- Test: `tests/test_master_bot.py`

**Interfaces:**
- Consumes: `src.database`, `src.animein_client`, `src.railway_client`, `src.worker_template`
- Produces:
  - Long-polling async Master Bot service handling `/start`, `/menu`, `/mode`, `/start_auto`, `/stop`, `/download`, `/token`, `/status`.
  - State machine for Auto vs Manual modes.

- [ ] **Step 1: Write the failing test**

```python
from src.config import Config
from src.master_bot import parse_command

def test_parse_commands():
    cmd, arg = parse_command("/token abcdef12345")
    assert cmd == "token"
    assert arg == "abcdef12345"
    cmd, arg = parse_command("/download 6635")
    assert cmd == "download"
    assert arg == "6635"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_master_bot.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `src/config.py` and `src/master_bot.py`**

Implement secure local config persistence in `config.json`. Implement Telegram bot message listener. Connect inline keyboard buttons for interactive controls. Connect `/token` command to auto-detect and configure Railway account. Connect `/start_auto` and `/stop` to Railway worker lifecycle and live progress message creation.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_master_bot.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/master_bot.py tests/test_master_bot.py
git commit -m "feat: implement master controller telegram bot"
```

---

### Task 6: System Integration & Verification

**Files:**
- Create: `start_bot.sh`
- Test: Live end-to-end command testing and Railway auto-provisioning test.

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`  
Expected: 100% tests PASS

- [ ] **Step 2: Initialize Master Bot on VPS**

Run: `./start_bot.sh` and verify memory footprint with `ps aux | grep master_bot`.  
Expected: Memory usage < 35 MB RAM.

- [ ] **Step 3: Verify Telegram `/start` menu and `/status` response**

Send `/start` to bot in Telegram and confirm interactive buttons render properly.

- [ ] **Step 4: Commit and finalize**

```bash
git add start_bot.sh
git commit -m "chore: setup master bot launcher and verify complete system"
```
