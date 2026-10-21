import os
import json
import pytest
from unittest.mock import MagicMock, patch
from src.config import Config
from src.master_bot import MasterBot
from src.worker_template import generate_worker_payload
from src.database import init_db, record_upload, has_uploaded

TEST_CFG = "tests/test_review_cfg.json"
TEST_DB = "tests/test_review_db.sqlite"

@pytest.fixture(autouse=True)
def cleanup():
    for f in (TEST_CFG, TEST_DB):
        if os.path.exists(f):
            os.remove(f)
    yield
    for f in (TEST_CFG, TEST_DB):
        if os.path.exists(f):
            os.remove(f)

def test_admin_authorization_guard():
    """Finding Critical 2: Unauthorized users must be blocked."""
    cfg = Config(path=TEST_CFG)
    cfg.admin_user_id = 999999
    cfg.save()

    bot = MasterBot(cfg)
    bot.api.send_message = MagicMock()

    # User 12345 is not admin
    unauth_msg = {
        "chat": {"id": 12345},
        "from": {"id": 12345},
        "text": "/token new_token"
    }
    bot.dispatch_message(unauth_msg)
    # Token should not have been updated
    assert cfg.railway_token != "new_token"
    # User should receive unauthorized message
    sent_text = bot.api.send_message.call_args[0][1]
    assert "Unauthorized" in sent_text or "Akses ditolak" in sent_text

def test_secrets_not_hardcoded_in_config():
    """Finding Critical 3: Config must not hardcode production secrets as fallbacks."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = Config(path=TEST_CFG)
        # Should not have hardcoded production bot token or api hash by default
        assert not cfg.bot_token.startswith("8942930830")
        assert cfg.api_hash != "f4d846728e694c2d1071ca7d3b2f6c81"

def test_worker_payload_includes_streamer_buttons_and_retries():
    """Finding Important 1 & 4: Worker payload must include streamer buttons and retry logic."""
    payload = generate_worker_payload(
        bot_token="test_token",
        chat_id=123,
        progress_msg_id=456,
        target_mode="manual",
        movie_id="6635",
        streamer_url="https://streamer.up.railway.app"
    )
    # Decode base64 to inspect inner python script
    import base64, re
    match = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", payload)
    assert match is not None
    decoded = base64.b64decode(match.group(1)).decode("utf-8")
    
    # Must include streamer buttons
    assert "Button.url" in decoded or "streamer_url" in decoded
    # Must include retry logic
    assert "retry" in decoded.lower() or "attempts" in decoded.lower()

def test_anti_duplicate_filter_in_master_bot_queue():
    """Finding Critical 1: Master Bot must filter out already uploaded episodes from download queue."""
    init_db(TEST_DB)
    # Mark ep 1 720p as already uploaded
    record_upload("6635", 1, "720p", 3100, "Yami Shibai 15", db_path=TEST_DB)
    assert has_uploaded("6635", 1, "720p", db_path=TEST_DB)

    cfg = Config(path=TEST_CFG)
    bot = MasterBot(cfg)
    bot.db_path = TEST_DB

    # Test helper method that resolves pending episodes
    mock_episodes = [
        {"id": "ep1", "index": 1, "title": "Ep 1"},
        {"id": "ep2", "index": 2, "title": "Ep 2"}
    ]
    mock_streams = [
        {"quality": "720p", "url": "https://cdn/ep.mp4"}
    ]

    with patch("src.master_bot.get_movie_episodes", return_value=mock_episodes), \
         patch("src.master_bot.get_episode_streams", return_value=mock_streams):
        pending = bot.get_pending_downloads(movie_id="6635")
        # Ep 1 720p should be filtered out; Ep 2 720p should remain
        assert len(pending) == 1
        assert pending[0]["ep_index"] == 2
        assert pending[0]["quality"] == "720p"
