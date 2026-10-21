from unittest.mock import patch
import urllib.error
import pytest
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
        assert eps[1]["id"] == "321212"
        assert eps[1]["index"] == 2

def test_get_latest_catalog():
    mock_response = {
        "status": 200,
        "data": {
            "movie": [
                {"id": "6635", "title": "Yami Shibai 15"},
                {"id": "6636", "title": "Another Anime"}
            ]
        }
    }
    with patch("src.animein_client.api_get", return_value=mock_response):
        catalog = get_latest_catalog()
        assert len(catalog) == 2
        assert catalog[0]["id"] == "6635"
        assert catalog[0]["title"] == "Yami Shibai 15"

def test_get_movie_details():
    mock_response = {
        "status": 200,
        "data": {
            "movie": {"id": "6635", "title": "Yami Shibai 15", "status": "Ongoing"}
        }
    }
    with patch("src.animein_client.api_get", return_value=mock_response):
        details = get_movie_details("6635")
        assert details is not None
        assert details["id"] == "6635"
        assert details["title"] == "Yami Shibai 15"

def test_get_episode_streams():
    mock_response = {
        "status": 200,
        "data": {
            "stream": [
                {"server": "Default", "quality": "720p", "url": "https://storages.animein.net/stream1.mp4"},
                {"server": "Default", "quality": "1080p", "url": "https://storages.animein.net/stream2.mp4"}
            ]
        }
    }
    with patch("src.animein_client.api_get", return_value=mock_response):
        streams = get_episode_streams("321211")
        assert len(streams) == 2
        assert streams[0]["quality"] == "720p"
        assert streams[0]["url"] == "https://storages.animein.net/stream1.mp4"

def test_api_network_error_returns_safe_fallbacks():
    with patch("src.animein_client.api_get", side_effect=urllib.error.URLError("Network unreachable")):
        assert get_latest_catalog() == []
        assert get_movie_details("123") is None
        assert get_movie_episodes("123") == []
        assert get_episode_streams("123") == []
