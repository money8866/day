# -*- coding: utf-8 -*-
"""
RIB V2.0 结构破坏评分模块（规范§20）

STRUCTURE_RISK 0~100：
  >=70 → 状态 INVALIDATED
  >=50 → 禁止升级状态（且 BUY_READINESS 上限40）

风险项：
  放量下跌 +25 / 跌破ImpulseLow +40 / 跌破BaseLow +35 / MA20重新向下 +20
  平台振幅扩大 +10 / 突破失败 +30 / 回踩放量 +20 / 跌破关键支撑 +25
  高位长上影 +10 / 市场退潮 +15
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

import pandas as pd

from .config import RIB_CONFIG
from .v2_next_state import _safe_float

if TYPE_CHECKING:
    from .engine import RIBResult


@dataclass
class StructureRiskInfo:
    """结构破坏评分结果。"""
    score: float = 0.0
    items: List[str] = field(default_factory=list)


class StructureRiskEngine:
    """结构破坏评分引擎。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG)
        if config:
            self.cfg.update(config)
        self.v2 = self.cfg.get("v2", {})

    def compute(self, df: pd.DataFrame, result: "RIBResult") -> StructureRiskInfo:
        """计算 STRUCTURE_RISK。"""
        info = StructureRiskInfo()
        if len(df) < 5:
            return info

        s = 0.0
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        vols = df["vol"].values.astype(float)

        vr = _safe_float(df["vol_ratio"].values[-1], 0)
        us = _safe_float(df["upper_shadow"].values[-1], 0)
        close = result.close
        imp = result.impulse
        base = result.base
        bo = result.breakout
        pb = result.pullback

        # ① 放量下跌 (+25)
        if vr > 1.5 and close < closes[-2]:
            s += 25
            info.items.append("放量下跌")

        # ② 跌破ImpulseLow (+40)
        if imp and imp.impulse_low > 0 and close < imp.impulse_low:
            s += 40
            info.items.append(f"跌破第一波启动点{imp.impulse_low:.2f}")

        # ③ 跌破BaseLow (+35)
        if base and base.is_base and base.base_low > 0 and close < base.base_low:
            s += 35
            info.items.append(f"跌破平台低点{base.base_low:.2f}")

        # ④ MA20 重新向下 (+20)
        m20s = _safe_float(df["ma20_slope"].values[-1], 0)
        if m20s < -0.01:
            s += 20
            info.items.append("MA20重新向下")

        # ⑤ 高位长上影 (+10)
        if us > 0.3:
            s += 10
            info.items.append("高位长上影")

        # ⑥ 突破失败 (+30)
        if bo and bo.is_fake_breakout:
            s += 30
            info.items.append("突破失败(假突破)")

        # ⑦ 回踩放量 (+20)
        if pb and pb.is_pullback and pb.pullback_volume_ratio > 1.0:
            s += 20
            info.items.append(f"回踩放量(量比{pb.pullback_volume_ratio:.2f})")

        # ⑧ 跌破关键支撑 (+25) — 突破后跌回第一波高点下方
        if bo and bo.is_breakout and imp:
            if close < imp.impulse_high:
                s += 25
                info.items.append(f"跌破第一波高点{imp.impulse_high:.2f}")

        # ⑨ 市场退潮 (+15)
        if result.market_regime == "bear":
            s += 15
            info.items.append("市场退潮(BEAR)")

        info.score = round(min(100.0, s), 1)
        return info
