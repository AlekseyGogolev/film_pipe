from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "detector": {
        "name": "microsoft_bopbtl_scratch",
        "threshold": 0.4,
        "dilation": 2,
        "mask_postprocess": "scene_lines",
        "input_size": "full_size",
        "tile_size": 1024,
        "tile_overlap": 128,
    },
    "restorers": {
        "telea": {
            "enabled": True,
            "radius": 3.0,
        },
        "lama": {
            "enabled": True,
            "python": None,
            "refine": False,
        },
    },
    "output": {
        "feather_radius": 0,
    },
}


def load_config(path: Path | None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path is None:
        return config
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must contain a JSON object: {path}")
    return deep_merge(config, loaded)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
