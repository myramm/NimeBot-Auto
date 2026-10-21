import os
import tempfile
import sqlite3
import pytest

from file_streamer_test_helper import (
    init_db_helper,
    get_scraper_config_helper,
    set_scraper_config_helper,
    reset_scraper_config_helper
)

def test_scraper_config_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_anime.sqlite")
        init_db_helper(db_path)

        # Default fallback
        val = get_scraper_config_helper(db_path, "base_url", "https://xyz-api.animein.net")
        assert val == "https://xyz-api.animein.net"

        # Update base_url
        set_scraper_config_helper(db_path, "base_url", "https://new-mirror.animein.net")
        val2 = get_scraper_config_helper(db_path, "base_url", "https://xyz-api.animein.net")
        assert val2 == "https://new-mirror.animein.net"

        # Update apk_ver
        set_scraper_config_helper(db_path, "apk_ver", "5.3.0")
        ver = get_scraper_config_helper(db_path, "apk_ver", "5.2.2")
        assert ver == "5.3.0"

        # Reset
        reset_scraper_config_helper(db_path)
        val3 = get_scraper_config_helper(db_path, "base_url", "https://xyz-api.animein.net")
        assert val3 == "https://xyz-api.animein.net"
