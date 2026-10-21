import json
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional, Union

ANIMEIN_BASE_URL = "https://xyz-api.animein.net"
DEFAULT_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "apk_ver": "5.2.2"
}

def api_get(endpoint: str, timeout: int = 15) -> Dict[str, Any]:
    url = f"{ANIMEIN_BASE_URL}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = response.read().decode("utf-8")
        return json.loads(content)

def get_latest_catalog() -> List[Dict[str, Any]]:
    endpoint = "3/2/home/new?id_user=0&key_client=guest&apk_ver=5.2.2"
    try:
        data = api_get(endpoint)
        if isinstance(data, dict):
            # Might be under data.movie or data.latest or directly data
            inner = data.get("data", {})
            if isinstance(inner, dict):
                movies = inner.get("movie") or inner.get("latest") or []
                if isinstance(movies, list):
                    return movies
            elif isinstance(inner, list):
                return inner
        return []
    except Exception as e:
        print(f"Error fetching catalog: {e}")
        return []

def get_movie_details(movie_id: Union[int, str]) -> Optional[Dict[str, Any]]:
    endpoint = f"3/2/movie/detail/{movie_id}?id_user=0&key_client=guest&apk_ver=5.2.2"
    try:
        data = api_get(endpoint)
        if isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, dict):
                return inner.get("movie") or inner
        return None
    except Exception as e:
        print(f"Error fetching movie {movie_id} details: {e}")
        return None

def get_movie_episodes(movie_id: Union[int, str]) -> List[Dict[str, Any]]:
    endpoint = f"3/2/movie/episode/{movie_id}?id_user=0&key_client=guest&apk_ver=5.2.2"
    try:
        data = api_get(endpoint)
        if isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, dict):
                episodes = inner.get("episode", [])
                formatted = []
                for ep in episodes:
                    idx_val = ep.get("index") or ep.get("ep_index") or 0
                    try:
                        idx_val = int(idx_val)
                    except (ValueError, TypeError):
                        pass
                    item = dict(ep)
                    item["index"] = idx_val
                    formatted.append(item)
                return formatted
        return []
    except Exception as e:
        print(f"Error fetching movie {movie_id} episodes: {e}")
        return []

def get_episode_streams(ep_id: Union[int, str]) -> List[Dict[str, Any]]:
    endpoint = f"3/2/episode/streamnew/{ep_id}?id_user=0&key_client=guest&apk_ver=5.2.2"
    try:
        data = api_get(endpoint)
        if isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, dict):
                streams = inner.get("stream", [])
                if isinstance(streams, list):
                    return streams
        return []
    except Exception as e:
        print(f"Error fetching episode {ep_id} streams: {e}")
        return []
