import pytest

from app.agents import REGISTRY
from app.core import config, db, state


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """테스트마다 임시 DB·출력 폴더를 쓰고, 지연은 0으로."""
    db.init_db(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "DEMO_SECONDS", 0)
    monkeypatch.setattr(config, "CALC_DELAY", 0)
    monkeypatch.setattr(config, "MANAGER_POLL", 0.01)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "DONE_HOLD_SECONDS", 0.05)
    state.reset()
    for agent_id in REGISTRY:
        state.register(agent_id)
    yield
