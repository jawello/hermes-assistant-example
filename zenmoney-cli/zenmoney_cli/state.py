"""Local cache of the Zen-Money diff state (serverTimestamp + entities)."""
import json
from pathlib import Path

ENTITY_TYPES = ("transaction", "tag", "account", "instrument", "user")


class State:
    def __init__(self, dir_path: Path, server_timestamp: int = 0,
                 entities: dict | None = None):
        self.dir_path = Path(dir_path)
        self.server_timestamp = server_timestamp
        self.entities = entities or {t: {} for t in ENTITY_TYPES}

    @property
    def _file(self) -> Path:
        return self.dir_path / "state.json"

    @classmethod
    def load(cls, dir_path: Path) -> "State":
        file = Path(dir_path) / "state.json"
        if not file.exists():
            return cls(dir_path)
        raw = json.loads(file.read_text())
        entities = {t: raw.get("entities", {}).get(t, {}) for t in ENTITY_TYPES}
        return cls(dir_path, raw.get("serverTimestamp", 0), entities)

    def apply(self, diff_response: dict) -> None:
        self.server_timestamp = diff_response.get(
            "serverTimestamp", self.server_timestamp)
        for etype in ENTITY_TYPES:
            for obj in diff_response.get(etype, []) or []:
                self.entities[etype][obj["id"]] = obj
        for gone in diff_response.get("deletion", []) or []:
            self.entities.get(gone.get("object"), {}).pop(gone.get("id"), None)

    def save(self) -> None:
        self.dir_path.mkdir(parents=True, exist_ok=True)
        payload = {"serverTimestamp": self.server_timestamp,
                   "entities": self.entities}
        self._file.write_text(json.dumps(payload, ensure_ascii=False))
        self._file.chmod(0o600)
