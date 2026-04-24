import json
import os


class Storage:
    """Save/load match data as JSON in Kivy user_data_dir."""
    def __init__(self, base_dir: str):
        os.makedirs(base_dir, exist_ok=True)
        self.path = os.path.join(base_dir, "match_state.json")

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def save(self, data: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def load(self) -> dict:
        if not self.exists():
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def clear(self):
        if self.exists():
            os.remove(self.path)