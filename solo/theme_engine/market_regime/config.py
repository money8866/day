"""Market Regime 配置加载器."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "market_regime.json"
_config_cache: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    """加载 market_regime.json 配置."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _config_cache = json.load(f)
        logger.info("Market Regime 配置已加载: %s", _CONFIG_PATH)
        return _config_cache
    except Exception as e:
        logger.error("加载 Market Regime 配置失败: %s", e)
        return {}


def reset_config() -> None:
    """重置配置缓存."""
    global _config_cache
    _config_cache = None


def get_layer_weights() -> Dict[str, float]:
    """获取一级因子权重."""
    cfg = load_config()
    return {k: float(v) for k, v in cfg.get("layer_weights", {}).items()}


def get_regime_threshold(name: str, default: float = 50) -> float:
    """获取 Regime 阈值."""
    cfg = load_config()
    return float(cfg.get("regime_thresholds", {}).get(name, default))


def get_multiplier(regime: str) -> float:
    """获取市场乘数."""
    cfg = load_config()
    return float(cfg.get("multipliers", {}).get(regime, 1.0))


def get_exposure(regime: str) -> float:
    """获取推荐仓位."""
    cfg = load_config()
    return float(cfg.get("recommended_exposure", {}).get(regime, 0.5))


def get_factor_weights(factor_name: str) -> Dict[str, float]:
    """获取子因子权重."""
    cfg = load_config()
    return {k: float(v) for k, v in cfg.get(factor_name, {}).items() if k.endswith("_weight")}


def get_norm_range(factor_name: str, sub_key: str) -> List[float]:
    """获取归一化范围."""
    cfg = load_config()
    factor_cfg = cfg.get(factor_name, {})
    for k, v in factor_cfg.items():
        if k.startswith(sub_key) and isinstance(v, list):
            return [float(x) for x in v]
    return [0.0, 100.0]
