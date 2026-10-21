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
    assert has_uploaded(6635, "1", "720p", db_path=TEST_DB)  # test type flexibility
    assert not has_uploaded("6635", 1, "1080p", db_path=TEST_DB)
    
    # Add another episode
    record_upload("6635", 2, "720p", msg_id=3101, anime_title="Yami Shibai 15", db_path=TEST_DB)
    records = get_uploaded_episodes("6635", db_path=TEST_DB)
    assert len(records) == 2
    assert records[0]["ep_index"] == 1
    assert records[0]["quality"] == "720p"
    assert records[0]["msg_id"] == 3100
    assert records[0]["anime_title"] == "Yami Shibai 15"
    assert records[1]["ep_index"] == 2
    assert records[1]["msg_id"] == 3101

def test_has_uploaded_empty():
    assert not has_uploaded("non_existent", 1, "720p", db_path=TEST_DB)
    assert get_uploaded_episodes("non_existent", db_path=TEST_DB) == []
