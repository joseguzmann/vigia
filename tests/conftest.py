import pytest

from vigia import config, db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIA_CONFIG", str(config.ROOT / "config" / "vigia.example.yaml"))
    config.reload()
    db.use(tmp_path / "test.db")
    yield
    db.use(tmp_path / "unused.db")


@pytest.fixture
def profile():
    return config.profile()
