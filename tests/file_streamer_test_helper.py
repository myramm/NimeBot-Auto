import sqlite3
import os

def init_db_helper(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraper_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_scraper_config_helper(db_path: str, key: str, default: str) -> str:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT value FROM scraper_config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return default

def set_scraper_config_helper(db_path: str, key: str, value: str):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR REPLACE INTO scraper_config (key, value) VALUES (?, ?)", (key, str(value).strip()))
    conn.commit()
    conn.close()

def reset_scraper_config_helper(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM scraper_config")
    conn.commit()
    conn.close()
