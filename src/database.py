import os
import sqlite3
from typing import Union, List, Dict, Any

DEFAULT_DB_PATH = "anime_db.sqlite"

def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                movie_id TEXT,
                ep_index INTEGER,
                quality TEXT,
                msg_id INTEGER,
                anime_title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(movie_id, ep_index, quality)
            )
        """)
        conn.commit()

def has_uploaded(movie_id: Union[int, str], ep_index: Union[int, str], quality: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM records WHERE movie_id = ? AND ep_index = ? AND quality = ?",
            (str(movie_id), int(ep_index), str(quality))
        )
        row = cursor.fetchone()
        return row is not None

def record_upload(
    movie_id: Union[int, str],
    ep_index: Union[int, str],
    quality: str,
    msg_id: int,
    anime_title: str = "",
    db_path: str = DEFAULT_DB_PATH
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO records (movie_id, ep_index, quality, msg_id, anime_title)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(movie_id), int(ep_index), str(quality), int(msg_id), str(anime_title))
        )
        conn.commit()

def get_uploaded_episodes(movie_id: Union[int, str], db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT movie_id, ep_index, quality, msg_id, anime_title, created_at FROM records WHERE movie_id = ? ORDER BY ep_index ASC, quality ASC",
            (str(movie_id),)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
