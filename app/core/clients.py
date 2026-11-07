"""가상 고객사 목록 (sample_data/clients.yaml)."""
import yaml

from app.core import config


def load_clients() -> list[dict]:
    path = config.SAMPLE_DIR / "clients.yaml"
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("clients", [])


def get_client(client_id: str) -> dict | None:
    return next((c for c in load_clients() if c["id"] == client_id), None)
