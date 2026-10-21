import os
import json
import pytest
from unittest.mock import patch, MagicMock
from src.config import Config
from src.master_bot import (
    parse_command,
    format_menu_text,
    build_menu_keyboard,
    build_catalog_keyboard,
    build_anime_detail_keyboard,
    build_episodes_keyboard,
    MasterBot
)

TEST_CONFIG_PATH = "tests/test_config.json"

@pytest.fixture(autouse=True)
def cleanup():
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)
    yield
    if os.path.exists(TEST_CONFIG_PATH):
        os.remove(TEST_CONFIG_PATH)

def test_config_operations():
    cfg = Config(path=TEST_CONFIG_PATH)
    assert cfg.mode == "auto"
    assert cfg.railway_token == ""
    cfg.set_token("test_railway_token")
    assert cfg.railway_token == "test_railway_token"
    cfg.set_mode("manual")
    assert cfg.mode == "manual"

    # Reload from disk
    cfg2 = Config(path=TEST_CONFIG_PATH)
    assert cfg2.railway_token == "test_railway_token"
    assert cfg2.mode == "manual"

def test_parse_commands():
    cmd, arg = parse_command("/token abcdef12345")
    assert cmd == "token"
    assert arg == "abcdef12345"
    cmd, arg = parse_command("/download 6635")
    assert cmd == "download"
    assert arg == "6635"
    cmd, arg = parse_command("/start")
    assert cmd == "start"
    assert arg == ""
    cmd, arg = parse_command("not_a_command")
    assert cmd == ""
    assert arg == "not_a_command"

def test_format_menu_text():
    text = format_menu_text(mode="auto", is_running=True, railway_email="test@example.com")
    assert "AUTO" in text
    assert "RUNNING" in text
    assert "test@example.com" in text

def test_build_menu_keyboard():
    kb_running = build_menu_keyboard(mode="auto", is_running=True)
    assert "Stop" in kb_running["inline_keyboard"][0][0]["text"]

    kb_stopped = build_menu_keyboard(mode="auto", is_running=False)
    assert "Start" in kb_stopped["inline_keyboard"][0][0]["text"]

def test_master_bot_dispatch():
    cfg = Config(path=TEST_CONFIG_PATH)
    bot = MasterBot(cfg)
    bot.api.send_message = MagicMock()

    # /start
    bot.dispatch_message({"chat": {"id": 12345}, "text": "/start"})
    assert bot.api.send_message.called

    # /mode
    bot.dispatch_message({"chat": {"id": 12345}, "text": "/mode manual"})
    assert cfg.mode == "manual"

def test_full_button_keyboards():
    mock_catalog = [
        {"id": "1", "title": "Anime One", "status": "Finished"},
        {"id": "2", "title": "Anime Two", "status": "Ongoing"}
    ]
    with patch("src.master_bot.get_latest_catalog", return_value=mock_catalog):
        text, kb = build_catalog_keyboard(page=0)
        assert "Anime One" in kb["inline_keyboard"][0][0]["text"]
        assert kb["inline_keyboard"][0][0]["callback_data"] == "anime_1"

    mock_detail = {"id": "1", "title": "Anime One", "status": "Finished", "year": "2026", "genre": "Action"}
    mock_episodes = [{"id": "ep1", "index": 1, "title": "Ep 1"}]
    with patch("src.master_bot.get_movie_details", return_value=mock_detail), \
         patch("src.master_bot.get_movie_episodes", return_value=mock_episodes):
        text, kb = build_anime_detail_keyboard("1", db_path="tests/test_tmp.sqlite")
        assert "Anime One" in text
        assert kb["inline_keyboard"][0][0]["callback_data"] == "dlall_1"
        assert kb["inline_keyboard"][1][0]["callback_data"] == "epmenu_1"

        text_ep, kb_ep = build_episodes_keyboard("1", db_path="tests/test_tmp.sqlite")
        assert "Ep 1" in kb_ep["inline_keyboard"][0][0]["text"]
        assert kb_ep["inline_keyboard"][0][0]["callback_data"] == "dlep_1_ep1_1"
