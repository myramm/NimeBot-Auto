import json
import time
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional, Union, Tuple

ANIMEIN_BASE_URL = "https://xyz-api.animein.net"
DEFAULT_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "apk_ver": "5.2.2"
}

_catalog_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
_details_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

def api_get(endpoint: str, timeout: int = 15) -> Dict[str, Any]:
    url = f"{ANIMEIN_BASE_URL}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = response.read().decode("utf-8")
        return json.loads(content)

def clear_cache() -> None:
    global _catalog_cache, _details_cache
    _catalog_cache = (0.0, [])
    _details_cache = {}

def get_latest_catalog(force_refresh: bool = False) -> List[Dict[str, Any]]:
    global _catalog_cache
    now = time.time()
    if not force_refresh and _catalog_cache[1] and (now - _catalog_cache[0]) < 120:
        return _catalog_cache[1]

    endpoint = "3/2/home/new?id_user=0&key_client=guest&apk_ver=5.2.2"
    try:
        data = api_get(endpoint)
        if isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, dict):
                movies = inner.get("movie") or inner.get("latest") or []
                if isinstance(movies, list):
                    _catalog_cache = (now, movies)
                    return movies
            elif isinstance(inner, list):
                _catalog_cache = (now, inner)
                return inner
        return _catalog_cache[1] or []
    except Exception as e:
        print(f"Error fetching catalog: {e}")
        return _catalog_cache[1] or []

def get_movie_details(movie_id: Union[int, str]) -> Optional[Dict[str, Any]]:
    global _details_cache
    m_id = str(movie_id)
    now = time.time()
    if m_id in _details_cache and (now - _details_cache[m_id][0]) < 300:
        return _details_cache[m_id][1]

    endpoint = f"3/2/movie/detail/{m_id}?id_user=0&key_client=guest&apk_ver=5.2.2"
    try:
        data = api_get(endpoint)
        if isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, dict):
                res = inner.get("movie") or inner
                _details_cache[m_id] = (now, res)
                return res
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
