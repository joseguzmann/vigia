"""Loads the YAML config. `config/vigia.yaml` if present, else the committed example."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load() -> dict:
    explicit = os.environ.get("VIGIA_CONFIG")
    candidates = [Path(explicit)] if explicit else [
        ROOT / "config" / "vigia.yaml",
        ROOT / "config" / "vigia.example.yaml",
        Path("/app/config/vigia.yaml"),
    ]
    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg["_path"] = str(p)
            return cfg
    raise FileNotFoundError("No config found. Copy config/vigia.example.yaml to config/vigia.yaml.")


def profile() -> dict:
    return load().get("profile", {})


def source(name: str) -> dict:
    return load().get("sources", {}).get(name, {})


def reload() -> None:
    load.cache_clear()
