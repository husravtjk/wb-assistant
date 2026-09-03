import os
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.yaml")
DB_PATH = os.path.join(ROOT, "data", "wb.sqlite3")


@dataclass
class Store:
    key: str
    name: str
    token: str
    enabled: bool = True


@dataclass
class Config:
    telegram: dict
    server: dict
    schedule: dict
    thresholds: dict
    hosts: dict
    rate_limits: dict
    stores: list = field(default_factory=list)

    @property
    def active_stores(self) -> list:
        return [s for s in self.stores if s.enabled]

    def store(self, key: str):
        for s in self.stores:
            if s.key == key:
                return s
        return None


def load(path: str = CONFIG_PATH) -> Config:
    if not os.path.exists(path):
        sys.exit(
            f"Нет файла {path}. Скопируй config.example.yaml в config.yaml и заполни токены."
        )
    with open(path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    stores = [Store(**s) for s in raw.get("stores", [])]
    if not stores:
        sys.exit("В config.yaml не указан ни один магазин.")

    cfg = Config(
        telegram=raw.get("telegram", {}),
        server=raw.get("server", {"host": "127.0.0.1", "port": 8765}),
        schedule=raw.get("schedule", {}),
        thresholds=raw.get("thresholds", {}),
        hosts=raw.get("hosts", {}),
        rate_limits=raw.get("rate_limits", {}),
        stores=stores,
    )
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return cfg
