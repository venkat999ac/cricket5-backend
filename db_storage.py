import json
import os


class JsonDB:
    def __init__(self, base_dir: str, filename: str = "data.json"):
        os.makedirs(base_dir, exist_ok=True)
        self.path = os.path.join(base_dir, filename)

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {"tournaments": {}}
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {"tournaments": {}}

    def save(self, data: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)