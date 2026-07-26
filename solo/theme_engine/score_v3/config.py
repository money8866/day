"""V3评分配置加载器 — 所有权重和阈值可配置."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring_v3.json"
_config_cache: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    """加载 scoring_v3.json 配置."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _config_cache = json.load(f)
        logger.info("V3评分配置已加载: %s", _CONFIG_PATH)
        return _config_cache
    except Exception as e:
        logger.error("加载V3配置失败: %s", e)
        return {}


def reset_config() -> None:
    """重置配置缓存（测试用）. """
    global _config_cache
    _config_cache = None


def get_layer_weights() -> Dict[str, float]:
    """获取一级因子权重."""
    cfg = load_config()
    return {k: float(v) for k, v in cfg.get("layer_weights", {}).items()}


def get_threshold(name: str, default: float = 50) -> float:
    """获取阈值."""
    cfg = load_config()
    return float(cfg.get("thresholds", {}).get(name, default))


def get_lifecycle_bonus(stage: str) -> int:
    """获取生命周期阶段加分."""
    cfg = load_config()
    return int(cfg.get("lifecycle_bonus", {}).get(stage, 0))


def get_factor_weights(factor_name: str) -> Dict[str, float]:
    """获取子因子权重."""
    cfg = load_config()
    return {k: float(v) for k, v in cfg.get(factor_name, {}).items() if k.endswith("_weight")}


def get_norm_range(factor_name: str, sub_key: str) -> List[float]:
    """获取归一化范围 [min, max]."""
    cfg = load_config()
    factor_cfg = cfg.get(factor_name, {})
    for k, v in factor_cfg.items():
        if k.startswith(sub_key) and isinstance(v, list):
            return [float(x) for x in v]
    return [0.0, 100.0]
