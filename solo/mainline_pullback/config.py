"""
配置类 Config — 集中管理所有选股参数

使用规则：
  - 所有魔法数字必须定义在此文件，业务代码不得出现硬编码阈值
  - 支持通过环境变量覆盖（方便 CI/定时任务调参）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ──────────────────────────────────────────────
# 数据路径配置
# ──────────────────────────────────────────────
@dataclass
class PathConfig:
    """文件路径配置"""
    cache_daily: str = os.getenv("CACHE_DAILY_DIR", r"d:\mystock\cache_daily")
    solo_dir: str = os.getenv("SOLO_DIR", r"d:\mystock\solo")
    tushare_token: str = os.getenv("TUSHARE_TOKEN", "1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34")


# ──────────────────────────────────────────────
# 基础过滤（A层）参数
# ──────────────────────────────────────────────
@dataclass
class HardFilterConfig:
    """基础硬过滤参数"""
    min_listing_days: int = 60          # 最少上市天数
    min_amount: float = 30_000          # 最低成交额（千元），即3000万元
    st_keywords: tuple = ("ST", "*ST", "退市")


# ──────────────────────────────────────────────
# 主升浪动量（B层）参数
# ──────────────────────────────────────────────
@dataclass
class MomentumConfig:
    """第一波主升浪动量参数"""
    lookback_days: int = 20                     # 回看天数
    min_wave_gain: float = 1.30                 # 最大涨幅 / 最低价 ≥ 1.30（30%）
    min_limit_up_count: int = 2                 # 最少出现 2 次涨停/大阳
    limit_up_threshold: float = 9.8             # 涨停阈值（%）
    big_positive_threshold: float = 7.0         # 大阳线阈值（%）


# ──────────────────────────────────────────────
# 首次回踩（C层）参数
# ──────────────────────────────────────────────
@dataclass
class PullbackConfig:
    """首次回踩缩量止跌参数"""
    # 支撑位
    ma_periods: tuple = (10, 20)                # 均线周期 MA10 / MA20
    support_tolerance: float = 0.02             # 距均线 ±2% 以内视为回踩
    # 缩量因子
    vol_peak_window: int = 5                    # 第一波主升浪中取最高量的窗口
    max_vol_ratio: float = 0.6                  # 今日量 / 峰值量 < 0.6（缩量 40%+）
    # 回撤位置
    min_pullback: float = 0.10                  # 距高点最少回撤 10%
    max_pullback: float = 0.25                  # 距高点最多回撤 25%


# ──────────────────────────────────────────────
# 综合评分参数
# ──────────────────────────────────────────────
@dataclass
class ScoringConfig:
    """综合评分权重"""
    wave_momentum_weight: float = 0.25      # 第一波动量强度
    pullback_quality_weight: float = 0.25   # 回踩质量（缩量程度 + 支撑吻合度）
    volume_shrink_weight: float = 0.20      # 缩量因子
    support_alignment_weight: float = 0.15  # 支撑位吻合度
    volume_ratio_weight: float = 0.15       # 量比合理性


# ──────────────────────────────────────────────
# 统一配置容器
# ──────────────────────────────────────────────
@dataclass
class Config:
    """系统统一配置"""
    path: PathConfig = field(default_factory=PathConfig)
    hard_filter: HardFilterConfig = field(default_factory=HardFilterConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    pullback: PullbackConfig = field(default_factory=PullbackConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    # 运行参数
    output_dir: str = field(default_factory=lambda: os.getenv(
        "OUTPUT_DIR", r"d:\mystock\solo\mainline_pullback\output"
    ))
    max_workers: int = int(os.getenv("MAX_WORKERS", "8"))
    target_date: str = ""  # 留空则自动识别最新交易日


# 全局单例
_CONFIG: Config | None = None


def get_config() -> Config:
    """获取全局配置单例"""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config()
    return _CONFIG


def reload_config() -> Config:
    """重新加载配置"""
    global _CONFIG
    _CONFIG = Config()
    return _CONFIG
