import pytest
from unittest.mock import patch, MagicMock
from src.worker import render_progress_bar, format_progress_message, TelegramProgressNotifier
from src.worker_template import generate_worker_payload

def test_render_progress_bar():
    bar = render_progress_bar(50, 100, width=10)
    assert bar == "[█████░░░░░] 50.0%"

def test_render_progress_bar_edge_cases():
    assert render_progress_bar(0, 100, width=10) == "[░░░░░░░░░░] 0.0%"
    assert render_progress_bar(100, 100, width=10) == "[██████████] 100.0%"
    assert render_progress_bar(0, 0, width=10) == "[░░░░░░░░░░] 0.0%"

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
    assert "MB" in msg

def test_generate_worker_payload():
    cmd = generate_worker_payload(
        bot_token="test_bot_token",
        chat_id=123456,
        progress_msg_id=789,
        target_mode="manual",
        movie_id="6635",
        streamer_url="https://streamer.up.railway.app"
    )
    assert "base64" in cmd or "python3 -c" in cmd
    assert "test_bot_token" not in cmd  # sensitive token should be b64 encoded inside command

def test_telegram_progress_notifier_throttling():
    with patch("urllib.request.urlopen") as mock_url:
        notifier = TelegramProgressNotifier("fake_token", 12345, 6789, min_interval=2.0)
        
        # First call (force=True to bypass 0 timestamp delay)
        notifier.update("Test Anime", 1, "720p", "Downloading", 100, 1000, 100, force=True)
        assert mock_url.call_count == 1
        
        # Second call immediately after should be throttled
        notifier.update("Test Anime", 1, "720p", "Downloading", 200, 1000, 100, force=False)
        assert mock_url.call_count == 1
