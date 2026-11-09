"""配置装载与校验。

所有业务阈值只允许出现在 linkage_config.json 中；本模块负责类型校验、
路径解析与配置指纹（用于快照溯源，保证每条边能回溯到当时的参数）。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).with_name("linkage_config.json")

_REQUIRED = (
    "paths", "data", "universe", "features", "residual",
    "linkage", "cluster", "scoring", "attribution", "observation",
    "report", "runtime",
)


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in _REQUIRED if k not in cfg]
    if missing:
        raise ValueError(f"配置缺少必需段: {missing}")

    root = Path(__file__).resolve().parent
    for key, val in cfg["paths"].items():
        if key.endswith("_dir") or key.endswith("_db") or key.endswith("_csv"):
            cfg["paths"][key] = str(Path(val))
    cfg["paths"].setdefault("module_root", str(root))

    w = cfg["linkage"]["edge_weights"]
    if abs(sum(w.values()) - 1.0) > 1e-6:
        raise ValueError(f"linkage.edge_weights 必须合计为 1，当前 {sum(w.values()):.6f}")
    sw = cfg["scoring"]["weights"]
    if abs(sum(sw.values()) - 100.0) > 1e-6:
        raise ValueError(f"scoring.weights 必须合计为 100，当前 {sum(sw.values()):.2f}")

    cfg["_config_path"] = str(p)
    cfg["_config_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for key in ("output_dir", "log_dir"):
        Path(cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
    Path(cfg["paths"]["linkage_db"]).parent.mkdir(parents=True, exist_ok=True)


def cget(cfg: dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
