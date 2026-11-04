"""Legacy tests use isolated, populated SQLite/index files rather than checkout data."""

import pytest

from src.config import config
from src.persistence.database import DatabaseManager
from src.persistence.store import IngestionService


@pytest.fixture(scope="session", autouse=True)
def legacy_test_database(tmp_path_factory):
    directory = tmp_path_factory.mktemp("legacy")
    original_db, original_index = config.DB_PATH, config.INDEX_DIR
    config.DB_PATH = str(directory / "legacy.db")
    config.INDEX_DIR = str(directory / "index")
    IngestionService(DatabaseManager()).run_full_ingestion()
    yield
    config.DB_PATH, config.INDEX_DIR = original_db, original_index
