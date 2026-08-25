# -*- coding: utf-8 -*-
"""
形态检测模块 - RIB 引擎的核心识别逻辑

每个 Detector 类负责一个阶段的形态识别：
  1. DowntrendDetector      - 长期下跌识别
  2. ImpulseDetector        - 第一波反转识别
  3. ImpulsePeakDetector    - 第一波高点识别
  4. PostImpulseBaseDetector - POST_IMPULSE_BASE 平台识别
  5. PreBreakoutDetector    - 平台转强识别
  6. SecondLegBreakoutDetector - 第二波突破识别
  7. FirstPullbackDetector  - 第一次健康回踩识别
  8. ReAccelerationDetector - 二次启动识别
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import RIB_CONFIG
from .indicators import (
    compute_impulse_atr, compute_impulse_return,
    compute_pullback_depth, compute_retain_ratio,
    find_local_extremes, ma, ma_slope,
)


def _safe_float(val, default=0.0) -> float:
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class DowntrendResult:
    """长期下跌识别结果。"""
    is_downtrend: bool = False
    start_index: int = 0
    end_index: int = 0
    lowest_index: int = 0
    lowest_price: float = 0.0
    decline_60d: float = 0.0  # 60日跌幅
    decline_120d: float = 0.0  # 120日跌幅
    ma20_slope: float = 0.0
    ma60_slope: float = 0.0
    ma20_below_ma60_ratio: float = 0.0
    price_below_ma60_ratio: float = 0.0
    higher_highs: int = 0  # 逐步降低的高点数
    lower_lows: int = 0  # 逐步下移的低点数
    duration_days: int = 0
    oversold_degree: float = 0.0  # 超跌程度
    volume_anomaly: bool = False
    score: float = 0.0


@dataclass
class ImpulseResult:
    """第一波反转识别结果。"""
    is_impulse: bool = False
    impulse_start_idx: int = 0
    impulse_low_idx: int = 0
    impulse_low: float = 0.0
    impulse_high_idx: int = 0
    impulse_high: float = 0.0
    impulse_days: int = 0
    impulse_return: float = 0.0
    impulse_atr: float = 0.0
    volume_ratio: float = 0.0
    is_extreme_acceleration: bool = False
    is_gradual: bool = False
    broke_ma20: bool = False
    broke_ma60: bool = False
    broke_trend_line: bool = False
    broke_previous_high: bool = False
    is_reversal_confirmed: bool = False
    score: float = 0.0


@dataclass
class ImpulsePeakResult:
    """第一波高点识别结果。"""
    peak_idx: int = 0
    peak_price: float = 0.0
    peak_date: str = ""
    is_peak_valid: bool = False
    exhaustion_signals: List[str] = field(default_factory=list)
    volume_peak: float = 0.0


@dataclass
class PostImpulseBaseResult:
    """POST_IMPULSE_BASE 平台识别结果。"""
    is_base: bool = False
    platform_start_idx: int = 0
    platform_end_idx: int = 0
    platform_days: int = 0
    base_high: float = 0.0
    base_low: float = 0.0
    base_range: float = 0.0
    pullback_depth: float = 0.0
    retain_ratio: float = 0.0
    volume_shrink_ratio: float = 0.0
    ma20_slope: float = 0.0
    high_structure: str = ""  # 高点结构
    low_structure: str = ""  # 低点结构
    base_type: str = ""  # 平台类型
    is_volume_plunge: bool = False  # 是否放量暴跌
    is_back_to_origin: bool = False  # 是否跌回启动区
    score: float = 0.0


@dataclass
class BreakoutResult:
    """第二波突破识别结果。"""
    is_breakout: bool = False
    breakout_idx: int = 0
    breakout_date: str = ""
    breakout_price: float = 0.0
    impulse_high: float = 0.0
    breakout_distance_atr: float = 0.0
    volume_ratio: float = 0.0
    close_location: float = 0.0
    upper_shadow: float = 0.0
    ma5_above_ma10: bool = False
    ma20_slope_ok: bool = False
    is_fake_breakout: bool = False
    score: float = 0.0


@dataclass
class PullbackResult:
    """第一次回踩识别结果。"""
    is_pullback: bool = False
    pullback_start_idx: int = 0
    pullback_low_idx: int = 0
    pullback_low: float = 0.0
    pullback_days: int = 0
    pullback_depth: float = 0.0
    pullback_volume_ratio: float = 0.0
    broke_impulse_high: bool = False
    is_test_and_reclaim: bool = False
    fell_back_to_base: bool = False
    support_found: bool = False
    score: float = 0.0


@dataclass
class ReAccelerationResult:
    """二次启动识别结果。"""
    is_reacceleration: bool = False
    reacc_idx: int = 0
    reacc_date: str = ""
    reacc_price: float = 0.0
    volume_ratio: float = 0.0
    close_location: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    vwap: float = 0.0
    break_pullback_high: bool = False
    ma5_slope_up: bool = False
    distance_atr: float = 0.0
    score: float = 0.0


# ═══════════════════════════════════════════════════════
# 1. 长期下跌识别
# ═══════════════════════════════════════════════════════

class DowntrendDetector:
    """识别长期下降趋势。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("downtrend", {}))
        if config:
            self.cfg.update(config)

    def detect(self, df: pd.DataFrame, end_idx: int) -> DowntrendResult:
        """在 end_idx 之前识别长期下跌。"""
        result = DowntrendResult()
        if end_idx < 60:
            return result

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        vols = df["vol"].values.astype(float)
        n = len(df)

        # 搜索窗口：从 end_idx 往前 120 日
        search_start = max(0, end_idx - self.cfg.get("max_days", 180))
        search_end = max(search_start + 60, end_idx)

        # 找局部极值
        order = 5
        seg_highs = highs[search_start:search_end]
        seg_lows = lows[search_start:search_end]

        high_idx, low_idx = find_local_extremes(seg_highs, order)
        if len(high_idx) < 2 or len(low_idx) < 2:
            return result

        # 调整索引到全局
        high_idx = high_idx + search_start
        low_idx = low_idx + search_start

        # 检查逐步降低的高点
        decreasing_highs = []
        for i in range(1, len(high_idx)):
            prev_h = highs[high_idx[i - 1]]
            curr_h = highs[high_idx[i]]
            if prev_h > curr_h * (1 + self.cfg.get("higher_high_drop", 0.05)):
                decreasing_highs.append((high_idx[i - 1], high_idx[i]))

        # 检查逐步下移的低点
        decreasing_lows = []
        for i in range(1, len(low_idx)):
            prev_l = lows[low_idx[i - 1]]
            curr_l = lows[low_idx[i]]
            if prev_l > curr_l * (1 + self.cfg.get("lower_low_drop", 0.03)):
                decreasing_lows.append((low_idx[i - 1], low_idx[i]))

        result.higher_highs = len(decreasing_highs)
        result.lower_lows = len(decreasing_lows)

        # 60日跌幅
        if end_idx >= 60:
            price_60d_ago = closes[end_idx - 60]
            if price_60d_ago > 0:
                result.decline_60d = (closes[end_idx] - price_60d_ago) / price_60d_ago

        # 120日跌幅
        if end_idx >= 120:
            price_120d_ago = closes[end_idx - 120]
            if price_120d_ago > 0:
                result.decline_120d = (closes[end_idx] - price_120d_ago) / price_120d_ago

        # MA20/MA60 斜率
        if "ma20" in df.columns:
            result.ma20_slope = _safe_float(df["ma20_slope"].values[end_idx], 0)
        if "ma60" in df.columns:
            result.ma60_slope = _safe_float(df["ma60_slope"].values[end_idx], 0)

        # MA20 在 MA60 下方比例
        if "ma20" in df.columns and "ma60" in df.columns:
            ma20_vals = df["ma20"].values[search_start:search_end]
            ma60_vals = df["ma60"].values[search_start:search_end]
            below_count = sum(
                1 for m20, m60 in zip(ma20_vals, ma60_vals)
                if not np.isnan(m20) and not np.isnan(m60) and m20 < m60
            )
            valid_count = sum(
                1 for m20, m60 in zip(ma20_vals, ma60_vals)
                if not np.isnan(m20) and not np.isnan(m60)
            )
            result.ma20_below_ma60_ratio = below_count / valid_count if valid_count > 0 else 0

        # 股价在 MA60 下方比例
        if "ma60" in df.columns:
            ma60_vals = df["ma60"].values[search_start:search_end]
            close_vals = closes[search_start:search_end]
            below_count = sum(
                1 for c, m in zip(close_vals, ma60_vals)
                if not np.isnan(m) and c < m
            )
            valid_count = sum(1 for m in ma60_vals if not np.isnan(m))
            result.price_below_ma60_ratio = below_count / valid_count if valid_count > 0 else 0

        # 超跌程度
        if "atr20" in df.columns:
            atr_val = _safe_float(df["atr20"].values[end_idx], 0)
            if atr_val > 0 and len(high_idx) > 0:
                # 从最近高点回撤的 ATR 数
                recent_high = highs[high_idx[-1]]
                result.oversold_degree = (recent_high - closes[end_idx]) / atr_val

        # 成交量异常
        if end_idx >= 20:
            recent_vol = vols[end_idx]
            vol_ma = np.mean(vols[end_idx - 20:end_idx])
            if vol_ma > 0:
                result.volume_anomaly = recent_vol > vol_ma * self.cfg.get("volume_anomaly_factor", 1.5)

        # 持续时间
        if len(decreasing_lows) > 0:
            result.start_index = decreasing_lows[0][0]
            result.end_index = end_idx
            result.duration_days = end_idx - result.start_index

        # 最低点
        if len(low_idx) > 0:
            lowest_idx = low_idx[np.argmin(lows[low_idx])]
            result.lowest_index = lowest_idx
            result.lowest_price = lows[lowest_idx]

        # 综合判断
        min_highs = self.cfg.get("max_highs_count", 3)
        result.is_downtrend = (
            result.higher_highs >= min_highs and
            result.ma20_below_ma60_ratio >= self.cfg.get("ma20_below_ma60_ratio", 0.6) and
            result.ma60_slope < 0
        )

        # 计算分数
        result.score = self._compute_score(result)
        return result

    def _compute_score(self, r: DowntrendResult) -> float:
        """计算 DOWNTREND_SCORE。"""
        s = 0.0
        w = self.cfg

        # 60日趋势 (20分)
        if r.decline_60d <= -0.20:
            s += w.get("weight_60d_trend", 20)
        elif r.decline_60d <= -0.10:
            s += w.get("weight_60d_trend", 20) * 0.7
        elif r.decline_60d <= -0.05:
            s += w.get("weight_60d_trend", 20) * 0.4

        # 120日趋势 (15分)
        if r.decline_120d <= -0.30:
            s += w.get("weight_120d_trend", 15)
        elif r.decline_120d <= -0.15:
            s += w.get("weight_120d_trend", 15) * 0.7

        # MA20 (15分)
        if r.ma20_below_ma60_ratio >= 0.8:
            s += w.get("weight_ma20", 15)
        elif r.ma20_below_ma60_ratio >= 0.5:
            s += w.get("weight_ma20", 15) * 0.6

        # MA60 (15分)
        if r.ma60_slope < -0.10:
            s += w.get("weight_ma60", 15)
        elif r.ma60_slope < 0:
            s += w.get("weight_ma60", 15) * 0.7

        # 高低点下降结构 (20分)
        if r.higher_highs >= 3:
            s += w.get("weight_higher_highs", 20)
        elif r.higher_highs >= 2:
            s += w.get("weight_higher_highs", 20) * 0.6

        # 持续时间 (10分)
        if r.duration_days >= 90:
            s += w.get("weight_duration", 10)
        elif r.duration_days >= 60:
            s += w.get("weight_duration", 10) * 0.7

        # 超跌程度 (5分)
        if r.oversold_degree >= 5:
            s += w.get("weight_oversold", 5)
        elif r.oversold_degree >= 3:
            s += w.get("weight_oversold", 5) * 0.6

        return round(min(100.0, max(0.0, s)), 1)


# ═══════════════════════════════════════════════════════
# 2. 第一波反转识别
# ═══════════════════════════════════════════════════════

class ImpulseDetector:
    """识别第一波强势上涨。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("impulse", {}))
        if config:
            self.cfg.update(config)

    def detect(self, df: pd.DataFrame, end_idx: int,
               downtrend: DowntrendResult) -> ImpulseResult:
        """在长期下跌后寻找第一波反转。"""
        result = ImpulseResult()
        if not downtrend.is_downtrend:
            return result

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        opens = df["open"].values.astype(float)
        vols = df["vol"].values.astype(float)

        # 从下跌最低点往后搜索第一波
        search_start = downtrend.lowest_index
        search_end = end_idx

        if search_end - search_start < self.cfg.get("min_days", 3):
            return result

        # 计算从低点开始的上涨
        impulse_low_idx = search_start
        impulse_low = lows[search_start]

        # 找这段区间的最高点
        segment_highs = highs[search_start:search_end + 1]
        impulse_high_idx = search_start + int(np.argmax(segment_highs))
        impulse_high = segment_highs.max()

        # 如果最高点不在合理位置（比如在区间起点），调整
        if impulse_high_idx <= impulse_low_idx:
            return result

        # 第一波参数
        result.impulse_start_idx = impulse_low_idx
        result.impulse_low_idx = impulse_low_idx
        result.impulse_low = impulse_low
        result.impulse_high_idx = impulse_high_idx
        result.impulse_high = impulse_high
        result.impulse_days = impulse_high_idx - impulse_low_idx

        # 涨幅
        result.impulse_return = compute_impulse_return(impulse_high, impulse_low)

        # ATR 计算
        if "atr20" in df.columns:
            atr_val = _safe_float(df["atr20"].values[impulse_low_idx], 0)
            result.impulse_atr = compute_impulse_atr(impulse_high, impulse_low, atr_val)

        # 时间判断
        min_days = self.cfg.get("min_days", 3)
        max_days = self.cfg.get("max_days", 15)
        if result.impulse_days < min_days:
            return result

        # EXTREME_ACCELERATION 标记
        extreme_days = self.cfg.get("extreme_acceleration_days", 2)
        if result.impulse_days <= extreme_days and result.impulse_return > 0.20:
            result.is_extreme_acceleration = True

        # 慢慢上涨标记
        gradual_days = self.cfg.get("gradual_days", 20)
        if result.impulse_days >= gradual_days:
            result.is_gradual = True

        # 成交量分析
        impulse_vols = vols[impulse_low_idx:impulse_high_idx + 1]
        avg_impulse_vol = np.mean(impulse_vols)
        if "vol_ma20" in df.columns:
            ma20_vol = _safe_float(df["vol_ma20"].values[impulse_low_idx], 0)
            if ma20_vol > 0:
                result.volume_ratio = avg_impulse_vol / ma20_vol

        # 突破检测
        # 突破 MA20
        if "ma20" in df.columns:
            ma20_at_high = _safe_float(df["ma20"].values[impulse_high_idx], 0)
            result.broke_ma20 = impulse_high > ma20_at_high

        # 突破 MA60
        if "ma60" in df.columns:
            ma60_at_high = _safe_float(df["ma60"].values[impulse_high_idx], 0)
            result.broke_ma60 = impulse_high > ma60_at_high

        # 突破下降趋势线（简化：使用 MA60 斜率判断）
        result.broke_trend_line = result.broke_ma60 and result.ma20_slope_up if hasattr(result, 'ma20_slope_up') else result.broke_ma60

        # 突破阶段前高
        if impulse_low_idx > 20:
            prev_high = np.max(highs[impulse_low_idx - 20:impulse_low_idx])
            result.broke_previous_high = impulse_high > prev_high

        # 综合突破确认
        break_count = sum([
            result.broke_ma20,
            result.broke_ma60,
            result.broke_trend_line,
            result.broke_previous_high,
        ])
        result.is_reversal_confirmed = break_count >= 2

        # 是否有效第一波
        min_return = self.cfg.get("min_return", 0.15)
        min_vol = self.cfg.get("min_volume_ratio", 1.2)
        result.is_impulse = (
            result.impulse_return >= min_return and
            result.volume_ratio >= min_vol and
            result.is_reversal_confirmed
        )

        # 计算分数
        result.score = self._compute_score(result)
        return result

    def _compute_score(self, r: ImpulseResult) -> float:
        """计算 IMPULSE_SCORE。"""
        if not r.is_impulse:
            return 0.0

        s = 0.0
        cfg = self.cfg

        # 涨幅
        if r.impulse_return >= 0.20:
            s += 25
        elif r.impulse_return >= 0.15:
            s += 18

        # 时间
        opt_low = cfg.get("optimal_days_low", 5)
        opt_high = cfg.get("optimal_days_high", 10)
        if opt_low <= r.impulse_days <= opt_high:
            s += 20
        elif cfg.get("min_days", 3) <= r.impulse_days < opt_low:
            s += 12
        else:
            s += 8

        # 成交量
        opt_lo = cfg.get("optimal_volume_low", 1.5)
        opt_hi = cfg.get("optimal_volume_high", 2.5)
        if opt_lo <= r.volume_ratio <= opt_hi:
            s += 25
        elif r.volume_ratio >= cfg.get("min_volume_ratio", 1.2):
            s += 18
        else:
            s += 5

        # 突破确认
        break_count = sum([r.broke_ma20, r.broke_ma60, r.broke_trend_line, r.broke_previous_high])
        s += min(20, break_count * 5)

        # 极端减速惩罚
        if r.is_extreme_acceleration:
            s -= 10
        if r.is_gradual:
            s -= 5

        return round(min(100.0, max(0.0, s)), 1)


# ═══════════════════════════════════════════════════════
# 3. 第一波高点识别
# ═══════════════════════════════════════════════════════

class ImpulsePeakDetector:
    """识别第一波高点（动能衰竭点）。"""

    def detect(self, df: pd.DataFrame, impulse: ImpulseResult,
              end_idx: int) -> ImpulsePeakResult:
        """识别第一波高点。"""
        result = ImpulsePeakResult()
        if not impulse.is_impulse:
            return result

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        vols = df["vol"].values.astype(float)

        peak_idx = impulse.impulse_high_idx
        result.peak_idx = peak_idx
        result.peak_price = highs[peak_idx]
        result.peak_date = str(df["trade_date"].iloc[peak_idx]) if "trade_date" in df.columns else ""

        # 检测动能衰竭特征
        signals = []
        if "upper_shadow" in df.columns:
            us = _safe_float(df["upper_shadow"].values[peak_idx], 0)
            if us > 0.3:
                signals.append("明显上影线")

        # 检查后续K线是否开始横盘或回落
        remaining = end_idx - peak_idx
        if remaining >= 2:
            post_highs = highs[peak_idx:peak_idx + min(remaining, 10)]
            post_lows = lows[peak_idx:peak_idx + min(remaining, 10)]
            if len(post_highs) >= 2:
                price_drop = (post_highs[0] - min(post_lows[1:])) / post_highs[0]
                if price_drop > 0.03:
                    signals.append("后续回落超过3%")
                if max(post_highs[1:]) < post_highs[0]:
                    signals.append("后续未创新高")

        # 成交量峰值
        if peak_idx > 0:
            vol_peak = vols[peak_idx]
            avg_vol = np.mean(vols[max(0, peak_idx - 10):peak_idx])
            if avg_vol > 0 and vol_peak > avg_vol * 1.5:
                signals.append("成交量峰值")

        result.exhaustion_signals = signals
        result.is_peak_valid = len(signals) >= 1
        result.volume_peak = _safe_float(vols[peak_idx], 0)
        return result


# ═══════════════════════════════════════════════════════
# 4. POST_IMPULSE_BASE 平台识别
# ═══════════════════════════════════════════════════════

class PostImpulseBaseDetector:
    """识别第一波后的高位强势整理平台。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("post_impulse_base", {}))
        if config:
            self.cfg.update(config)

    def detect(self, df: pd.DataFrame, impulse: ImpulseResult,
               peak: ImpulsePeakResult, end_idx: int) -> PostImpulseBaseResult:
        """识别 POST_IMPULSE_BASE 平台。"""
        result = PostImpulseBaseResult()
        if not impulse.is_impulse or not peak.is_peak_valid:
            return result

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        vols = df["vol"].values.astype(float)

        # 从峰值后开始寻找平台
        peak_idx = peak.peak_idx
        search_start = peak_idx + 1
        if search_start >= end_idx:
            return result

        impulse_high = impulse.impulse_high
        impulse_low = impulse.impulse_low

        # ── 规范§12：平台起点 = 峰值后第一次明显动能衰减点 ──
        # 第一波顶部的自然波动（峰值后2-5日的小回调）不属于平台，
        # 平台应从峰值后的"回撤低点"开始（动量明确衰减后的位置）
        # 策略：找到峰值后第一个从高点回撤>=3%的低点作为平台起点
        base_start = search_start
        for i in range(search_start, min(end_idx, search_start + 8)):
            drop = (peak.peak_price - lows[i]) / peak.peak_price if peak.peak_price > 0 else 0
            if drop >= 0.03:
                base_start = i
                break

        # 扫描平台窗口
        best_base = None
        min_days = self.cfg.get("min_days", 5)
        max_days = self.cfg.get("max_days", 30)

        # ── 关键：平台必须止于"第二波突破起点" ──
        # 当出现放量且收盘突破 impulse_high 的K线时，平台结束（那是突破不是整理）
        breakout_start = end_idx + 1
        for i in range(base_start, end_idx + 1):
            close_i = closes[i]
            vol_i = vols[i]
            vol_ma = _safe_float(df["vol_ma20"].values[i], 0) if "vol_ma20" in df.columns else 0
            vol_r = vol_i / vol_ma if vol_ma > 0 else 0
            if close_i > impulse_high and vol_r >= 1.3:
                breakout_start = i
                break

        platform_hard_end = min(end_idx, breakout_start - 1)

        for w in range(min_days, min(max_days + 1, platform_hard_end - base_start + 2)):
            s = base_start
            e = min(s + w, platform_hard_end)
            if e - s < min_days:
                continue

            seg_highs = highs[s:e + 1]
            seg_lows = lows[s:e + 1]
            seg_closes = closes[s:e + 1]
            seg_vols = vols[s:e + 1]

            base_high = float(np.max(seg_highs))
            base_low = float(np.min(seg_lows))
            base_range = (base_high - base_low) / impulse_high if impulse_high > 0 else 0

            # 回撤幅度
            pullback_depth = compute_pullback_depth(impulse_high, base_low, impulse_low)

            # 涨幅保留率
            retain = compute_retain_ratio(base_low, impulse_low, impulse_high)

            # 成交量收缩
            avg_base_vol = np.mean(seg_vols)
            avg_impulse_vol = np.mean(vols[impulse.impulse_low_idx:impulse.impulse_high_idx + 1])
            vol_shrink = avg_base_vol / avg_impulse_vol if avg_impulse_vol > 0 else 1.0

            # 平台质量评分
            quality = self._score_base_quality(
                e - s, base_range, pullback_depth, retain, vol_shrink,
                seg_highs, seg_lows, seg_closes, df, e, impulse_high
            )

            cand = {
                "start_idx": s,
                "end_idx": e,
                "days": e - s,
                "base_high": base_high,
                "base_low": base_low,
                "base_range": base_range,
                "pullback_depth": pullback_depth,
                "retain_ratio": retain,
                "vol_shrink": vol_shrink,
                "quality": quality,
            }

            if best_base is None or quality > best_base["quality"]:
                best_base = cand

        if best_base is None:
            return result

        # ── 规范：平台质量门槛 ──
        # 质量分过低的"平台"实为弱势回调（深回撤/低保留/放量），
        # 不是高位强势整理，不构成 POST_IMPULSE_BASE
        quality_min = self.cfg.get("quality_threshold", 40)
        if best_base["quality"] < quality_min:
            return result

        # ── 规范§7：涨幅保留率 <40%（严重）→ 直接 INVALID ──
        if best_base["retain_ratio"] < self.cfg.get("retain_bad", 0.40):
            return result

        # 填入结果
        result.is_base = True
        result.platform_start_idx = best_base["start_idx"]
        result.platform_end_idx = best_base["end_idx"]
        result.platform_days = best_base["days"]
        result.base_high = best_base["base_high"]
        result.base_low = best_base["base_low"]
        result.base_range = best_base["base_range"]
        result.pullback_depth = best_base["pullback_depth"]
        result.retain_ratio = best_base["retain_ratio"]
        result.volume_shrink_ratio = best_base["vol_shrink"]

        # MA20 斜率
        if "ma20" in df.columns:
            result.ma20_slope = _safe_float(
                df["ma20"].values[best_base["end_idx"]] / df["ma20"].values[best_base["end_idx"] - 5] - 1
                if best_base["end_idx"] >= 5 else 0, 0
            )

        # 高低点结构
        seg_highs = highs[best_base["start_idx"]:best_base["end_idx"] + 1]
        seg_lows = lows[best_base["start_idx"]:best_base["end_idx"] + 1]
        result.high_structure = self._classify_high_structure(seg_highs)
        result.low_structure = self._classify_low_structure(seg_lows)
        result.base_type = self._classify_base_type(seg_highs, seg_lows, best_base["vol_shrink"])

        # 危险信号
        result.is_volume_plunge = best_base["vol_shrink"] > 1.0
        result.is_back_to_origin = best_base["retain_ratio"] < 0.3

        result.score = best_base["quality"]
        return result

    def _score_base_quality(self, days, base_range, pullback_depth,
                            retain, vol_shrink, seg_highs, seg_lows,
                            seg_closes, df, end_idx, impulse_high) -> float:
        """评分平台质量（V2.0 规范§9 动态评分）。

        组成：时间15% + 回撤20% + 保留率20% + 缩量15% + 高低点结构15%
              + MA20 10% + 平台内波动率5%。
        奖励：价格越靠近 ImpulseHigh 且缩量（卖压弱）→ 加分。
        惩罚：放量下跌 -15、吞噬涨幅(保留<40%) -20。
        """
        s = 0.0
        cfg = self.cfg

        # 平台时间 (15分)
        opt_lo = cfg.get("optimal_days_low", 7)
        opt_hi = cfg.get("optimal_days_high", 15)
        if opt_lo <= days <= opt_hi:
            s += 15
        elif days >= cfg.get("min_days", 5):
            s += 9
        else:
            s += 3

        # 回撤深度 (20分)
        d_opt_lo = cfg.get("pullback_optimal_low", 0.20)
        d_opt_hi = cfg.get("pullback_optimal_high", 0.40)
        if d_opt_lo <= pullback_depth <= d_opt_hi:
            s += 20
        elif pullback_depth <= cfg.get("pullback_good_high", 0.50):
            s += 14
        elif pullback_depth <= cfg.get("pullback_danger", 0.60):
            s += 8

        # 涨幅保留率 (20分)
        if retain >= cfg.get("retain_excellent", 0.70):
            s += 20
        elif retain >= cfg.get("retain_good", 0.60):
            s += 14
        elif retain >= cfg.get("retain_pass", 0.50):
            s += 8

        # 缩量 (15分)
        if vol_shrink <= cfg.get("volume_shrink_ratio", 0.70):
            s += 15
        elif vol_shrink <= 0.85:
            s += 10
        elif vol_shrink <= 1.0:
            s += 5

        # 高低点结构 (15分)
        if len(seg_lows) >= 3:
            low_trend = np.polyfit(range(len(seg_lows)), seg_lows, 1)
            if low_trend[0] >= 0:  # 低点抬高/持平
                s += 8
        if len(seg_highs) >= 3:
            high_trend = np.polyfit(range(len(seg_highs)), seg_highs, 1)
            if high_trend[0] >= -0.01 * np.mean(seg_highs):  # 高点不显著下降
                s += 7

        # MA20 (10分)
        if "ma20" in df.columns and end_idx >= 5:
            ma20_now = _safe_float(df["ma20"].values[end_idx], 0)
            ma20_prev = _safe_float(df["ma20"].values[end_idx - 5], 0)
            if ma20_prev > 0:
                slope = ma20_now / ma20_prev - 1
                if slope >= 0:
                    s += 10
                elif slope > -0.02:
                    s += 5

        # 平台内波动率 (5分) — 振幅小为佳
        if base_range > 0:
            if base_range <= 0.08:
                s += 5
            elif base_range <= 0.12:
                s += 3
            elif base_range <= 0.18:
                s += 1

        # 奖励：价格靠近 ImpulseHigh 且缩量（卖压弱）(规范§9)
        if impulse_high > 0 and len(seg_highs) > 0:
            proximity = float(np.max(seg_highs)) / impulse_high
            if proximity >= 0.95 and vol_shrink <= 0.75:
                s += 5
            elif proximity >= 0.90 and vol_shrink <= 0.70:
                s += 3

        # 惩罚
        if vol_shrink > 1.0:
            s -= 15  # 放量下跌
        if retain < 0.40:
            s -= 20  # 吞噬涨幅

        return round(min(100.0, max(0.0, s)), 1)

    def _classify_high_structure(self, highs: np.ndarray) -> str:
        """分类高点结构。"""
        if len(highs) < 3:
            return "不足"
        trend = np.polyfit(range(len(highs)), highs, 1)
        slope = trend[0] / np.mean(highs) if np.mean(highs) > 0 else 0
        if slope > 0.01:
            return "高点抬高"
        elif slope < -0.01:
            return "高点降低"
        else:
            return "高点持平"

    def _classify_low_structure(self, lows: np.ndarray) -> str:
        """分类低点结构。"""
        if len(lows) < 3:
            return "不足"
        trend = np.polyfit(range(len(lows)), lows, 1)
        slope = trend[0] / np.mean(lows) if np.mean(lows) > 0 else 0
        if slope > 0.01:
            return "低点抬高"
        elif slope < -0.01:
            return "低点降低"
        else:
            return "低点持平"

    def _classify_base_type(self, highs: np.ndarray, lows: np.ndarray,
                            vol_shrink: float) -> str:
        """分类平台类型。"""
        high_struct = self._classify_high_structure(highs)
        low_struct = self._classify_low_structure(lows)

        if high_struct == "高点持平" and low_struct == "低点持平":
            return "高位横盘"
        elif high_struct == "高点降低" and low_struct == "低点降低" and vol_shrink < 1.0:
            return "旗形整理"
        elif high_struct == "高点降低" and low_struct == "低点抬高":
            return "小三角收敛"
        elif high_struct == "高点抬高" and low_struct == "低点抬高":
            return "阶梯整理"
        elif low_struct == "低点抬高":
            return "小型杯柄"
        else:
            return "普通整理"


# ═══════════════════════════════════════════════════════
# 5. 预突破检测（V2.1 §9~§13：平台成熟度）
# ═══════════════════════════════════════════════════════

@dataclass
class PreBreakoutResult:
    """V2.1 PRE_BREAKOUT 成熟度检测结果。

    PRE_BREAKOUT ≠ 平台内部突破，而是"平台已经成熟，
    未来1~3个交易日存在突破可能"（§9/§10）。
    """
    is_pre_breakout: bool = False       # 是否达到成熟平台标准
    score: float = 0.0                  # PRE_BREAKOUT_SCORE (§13)
    grade: str = ""                     # A+ / A / B
    distance_atr: float = 0.0           # DistanceToTrigger = (Trigger-Close)/ATR (§11)
    distance_band: str = ""             # IMMINENT/VERY_NEAR/NEAR/NORMAL/FAR
    trigger_price: float = 0.0          # BreakoutTrigger = ImpulseHigh + 0.3ATR
    vol_contraction: bool = False       # 最近3~5日波动率收缩
    upper_half: bool = False            # 最近价格位于平台上半区
    aged: bool = False                  # PRE_BREAKOUT_AGED（>5日未突破）
    expired: bool = False               # PRE_BREAKOUT_EXPIRED（>10日）
    days_at_level: int = 0              # 成熟条件首次满足至今的交易日数
    reasons: List[str] = field(default_factory=list)  # 满足/未满足的10条件


class PreBreakoutDetector:
    """V2.1 §9~§13：识别"成熟平台"（区别于早期平台 POST_IMPULSE_BASE）。

    升级标准（满足大部分条件，§10）：
      1. 平台>=7日  2. BASE_QUALITY>=75  3. Close距ImpulseHigh<=1.0ATR
      4. BaseHigh距ImpulseHigh<=0.8ATR  5. 平台缩量  6. MA20走平或向上
      7. 低点无明显破坏  8. 最近3~5日波动率收缩  9. 价格位于平台上半区
      10. 无明显放量抛压

    PRE_BREAKOUT_SCORE（§13）：
      距离25 + 平台质量20 + 缩量15 + 高低点10 + MA20 10
      + 波动率收缩10 + 收盘位置5 + 主题5
      >=85 A+ / 80~84 A / 75~79 B / <75 继续 POST_IMPULSE_BASE

    反直觉规则（§12）：距离突破位非常近 + 放量 + 长上影 = 抛压，
    必须扣分（"接近"不等于"即将突破"）。
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("v2", {}).get("pre_breakout", {}))
        if config:
            self.cfg.update(config)
        self.v2 = dict(RIB_CONFIG.get("v2", {}))

    def detect(self, df: pd.DataFrame, base: PostImpulseBaseResult,
               impulse: ImpulseResult, end_idx: int) -> PreBreakoutResult:
        """检测平台是否成熟到 PRE_BREAKOUT。"""
        result = PreBreakoutResult()
        if not base.is_base or not impulse.is_impulse:
            return result

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        vols = df["vol"].values.astype(float)

        close = float(closes[end_idx])
        atr = _safe_float(df["atr20"].values[end_idx], 0) if "atr20" in df.columns else 0
        if atr <= 0:
            return result

        impulse_high = impulse.impulse_high
        atr_buffer = RIB_CONFIG.get("breakout", {}).get("atr_buffer", 0.3)
        trigger = impulse_high + atr_buffer * atr

        result.trigger_price = trigger
        result.distance_atr = round((trigger - close) / atr, 2)
        result.distance_band = self._band(result.distance_atr)

        # ── 10 条件逐项评估（§10）──
        reasons = []
        cfg = self.cfg

        c1 = base.platform_days >= cfg.get("min_days", 7)
        c2 = base.score >= cfg.get("min_quality", 75)
        c3 = result.distance_atr <= cfg.get("close_to_impulse_atr", 1.0) + atr_buffer
        basehigh_gap = (impulse_high - base.base_high) / atr
        c4 = basehigh_gap <= cfg.get("basehigh_to_impulse_atr", 0.8)
        c5 = base.volume_shrink_ratio <= cfg.get("min_vol_shrink", 0.9)
        m20s = _safe_float(df["ma20_slope"].values[end_idx], 0) if "ma20_slope" in df.columns else 0
        c6 = m20s >= -0.005
        c7 = close >= base.base_low
        c8, recent_vol = self._vol_contraction(df, end_idx)
        seg_mid = (base.base_high + base.base_low) / 2
        c9 = close >= seg_mid
        vr = _safe_float(df["vol_ratio"].values[end_idx], 0) if "vol_ratio" in df.columns else 0
        c10 = vr <= 1.5
        us = _safe_float(df["upper_shadow"].values[end_idx], 0) if "upper_shadow" in df.columns else 0

        if c1: reasons.append(f"平台{base.platform_days}日>=7")
        else: reasons.append(f"平台仅{base.platform_days}日(<7)")
        if c2: reasons.append(f"平台质量{base.score:.0f}>=75")
        else: reasons.append(f"平台质量{base.score:.0f}<75")
        if c3: reasons.append(f"收盘距触发位{result.distance_atr:.1f}ATR<=1.3ATR")
        else: reasons.append(f"收盘距触发位{result.distance_atr:.1f}ATR过远")
        if c4: reasons.append(f"平台高点距第一波高点{basehigh_gap:.1f}ATR<=0.8")
        else: reasons.append(f"平台高点距第一波高点{basehigh_gap:.1f}ATR过远")
        if c5: reasons.append(f"平台缩量({base.volume_shrink_ratio:.2f})")
        else: reasons.append(f"平台未有效缩量({base.volume_shrink_ratio:.2f})")
        if c6: reasons.append("MA20走平或向上")
        else: reasons.append("MA20向下")
        if c7: reasons.append("低点未破坏")
        else: reasons.append("低点破坏")
        if c8: reasons.append(f"近{len(recent_vol)}日波动率收缩")
        else: reasons.append("波动率未收缩")
        if c9: reasons.append("价格位于平台上半区")
        else: reasons.append("价格位于平台下半区")
        if c10: reasons.append("无放量抛压")
        else: reasons.append(f"放量抛压(量比{vr:.2f})")
        result.reasons = reasons

        # ── PRE_BREAKOUT_SCORE（§13）──
        w = RIB_CONFIG.get("v2", {}).get("pre_breakout_weights", {})
        s = 0.0

        # 距离突破位 (25)：越近越高，但§12反例需扣分
        d = result.distance_atr
        if d <= 0.3: s += 25
        elif d <= 0.7: s += 21
        elif d <= 1.0: s += 17
        elif d <= 1.5: s += 10
        else: s += 4

        # 平台质量 (20)：线性映射 40~100 -> 0~20
        s += 20.0 * max(0.0, min(1.0, (base.score - 40) / 60.0))

        # 缩量 (15)
        vs = base.volume_shrink_ratio
        if vs <= 0.6: s += 15
        elif vs <= 0.7: s += 12
        elif vs <= 0.85: s += 8
        elif vs <= 0.9: s += 5

        # 高低点结构 (10)
        if base.low_structure == "低点抬高": s += 10
        elif base.low_structure == "低点持平": s += 7
        if base.high_structure == "高点抬高": s += 3

        # MA20 (10)
        if m20s >= 0: s += 10
        elif m20s >= -0.005: s += 6

        # 波动率收缩 (10)
        if c8: s += 10

        # 收盘位置 (5)
        cl = _safe_float(df["close_loc"].values[end_idx], 0.5) if "close_loc" in df.columns else 0.5
        s += 5.0 * max(0.0, min(1.0, cl))

        # 主题 (5)：无主题数据时给中性 2.5
        s += 2.5

        # ── §12 反直觉扣分：贴近阻力 + 放量 + 长上影 = 抛压 ──
        if d <= 0.7 and (vr >= 1.5 or us >= 0.3):
            pen = 12 if (vr >= 1.5 and us >= 0.3) else 7
            s -= pen
            result.reasons.append(f"贴近阻力但放量/长上影，扣{pen}分(§12)")

        result.score = round(min(100.0, max(0.0, s)), 1)

        # ── 分级（§13）──
        if result.score >= 85: result.grade = "A+"
        elif result.score >= 80: result.grade = "A"
        elif result.score >= 75: result.grade = "B"

        # ── 升级判定：满足大部分条件（>=8/10）且分数达标 ──
        conditions = [c1, c2, c3, c4, c5, c6, c7, c8, c9, c10]
        n_pass = sum(1 for c in conditions if c)
        min_score = cfg.get("min_score", 75)
        result.is_pre_breakout = (n_pass >= 8) and (result.score >= min_score)

        # ── 老化检测（§33）：以平台结束日为起点计算 ──
        days_since_base_end = end_idx - base.platform_end_idx
        result.days_at_level = days_since_base_end
        if result.is_pre_breakout:
            if days_since_base_end > self.v2.get("pre_expired_days", 10):
                result.expired = True
            elif days_since_base_end > self.v2.get("pre_aged_days", 5):
                result.aged = True

        return result

    def _band(self, d: float) -> str:
        """触发距离五档分带（§11）。"""
        v2 = RIB_CONFIG.get("v2", {})
        if d <= v2.get("distance_imminent", 0.3):
            return "IMMINENT"
        if d <= v2.get("distance_very_near", 0.7):
            return "VERY_NEAR"
        if d <= v2.get("distance_near", 1.0):
            return "NEAR"
        if d <= v2.get("distance_normal", 1.5):
            return "NORMAL"
        return "FAR"

    def _vol_contraction(self, df: pd.DataFrame,
                         end_idx: int) -> tuple:
        """最近3~5日波动率收缩：近段日均振幅 < 前段日均振幅的0.8倍。"""
        try:
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            recent_n = 3
            prev_n = 5
            if end_idx < recent_n + prev_n:
                return False, []
            recent_amp = highs[end_idx - recent_n + 1:end_idx + 1] - \
                lows[end_idx - recent_n + 1:end_idx + 1]
            prev_amp = highs[end_idx - recent_n - prev_n + 1:end_idx - recent_n + 1] - \
                lows[end_idx - recent_n - prev_n + 1:end_idx - recent_n + 1]
            r = float(np.mean(recent_amp))
            p = float(np.mean(prev_amp))
            return (p > 0 and r / p <= 0.8), recent_amp.tolist()
        except Exception:
            return False, []


# ═══════════════════════════════════════════════════════
# 6. 第二波突破检测
# ═══════════════════════════════════════════════════════

class SecondLegBreakoutDetector:
    """检测第二波突破（Close > ImpulseHigh + 0.3ATR）。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("breakout", {}))
        if config:
            self.cfg.update(config)

    def detect(self, df: pd.DataFrame, impulse: ImpulseResult,
               base: PostImpulseBaseResult, end_idx: int) -> BreakoutResult:
        """检测第二波突破。"""
        result = BreakoutResult()
        if not impulse.is_impulse or not base.is_base:
            return result

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        opens = df["open"].values.astype(float)
        vols = df["vol"].values.astype(float)

        impulse_high = impulse.impulse_high
        atr_buffer = self.cfg.get("atr_buffer", 0.3)

        search_start = base.platform_end_idx + 1
        if search_start > end_idx:
            return result

        # ── 簇确认模式 ──
        # 规范§20: 突破日量比>=1.3 且收盘位置>=0.75。
        # 但真实K线单日常有噪声，采用"首日站上 + 1~2日内补足确认"的簇判定：
        #   基准日 = 收盘首次站上 ImpulseHigh + 0.3ATR 的那根K线
        #   确认日 = 基准日后 confirm_window 日内，量比/收盘位置/MA结构达标的那根
        confirm_window = int(self.cfg.get("confirm_window", 2))
        vol_min = self.cfg.get("volume_ratio_min", 1.3)
        loc_min = self.cfg.get("close_location_min", 0.75)

        best = None
        i = search_start
        while i <= end_idx:
            close = closes[i]
            if close <= impulse_high:
                i += 1
                continue
            if "atr20" not in df.columns:
                break
            atr_val = _safe_float(df["atr20"].values[i], 0)
            if atr_val <= 0:
                i += 1
                continue

            # 基准条件：Close > ImpulseHigh + 0.3*ATR
            if close <= impulse_high + atr_buffer * atr_val:
                i += 1
                continue

            # 基准日找到，向后找确认日（含基准日自身）
            confirmed = None
            for j in range(i, min(i + confirm_window + 1, end_idx + 1)):
                close_j = closes[j]
                atr_j = _safe_float(df["atr20"].values[j], 0)
                if atr_j <= 0:
                    continue
                # 确认日必须仍然站在突破位之上（不能缩回平台）
                if close_j <= impulse_high + atr_buffer * atr_j:
                    continue

                distance_atr = (close_j - impulse_high) / atr_j

                vol_ma = _safe_float(df["vol_ma20"].values[j], 0) if "vol_ma20" in df.columns else 0
                vol_ratio = vols[j] / vol_ma if vol_ma > 0 else 0

                day_range = highs[j] - df["low"].values.astype(float)[j]
                close_loc = (close_j - df["low"].values.astype(float)[j]) / day_range if day_range > 0 else 0.5

                upper_shadow = 0.0
                if "upper_shadow" in df.columns:
                    upper_shadow = _safe_float(df["upper_shadow"].values[j], 0)

                ma5_ok = True
                ma10_ok = True
                if "ma5" in df.columns and "ma10" in df.columns:
                    ma5_val = _safe_float(df["ma5"].values[j], 0)
                    ma10_val = _safe_float(df["ma10"].values[j], 0)
                    ma5_ok = ma5_val > ma10_val

                ma20_slope_ok = True
                if "ma20_slope" in df.columns:
                    ma20_slope_ok = _safe_float(df["ma20_slope"].values[j], 0) >= 0

                # 允许用基准日量能或确认日量能的最大值（放量可能分散在两根K线）
                vol_ma_i = _safe_float(df["vol_ma20"].values[i], 0) if "vol_ma20" in df.columns else 0
                vol_ratio_i = vols[i] / vol_ma_i if vol_ma_i > 0 else 0
                eff_vol_ratio = max(vol_ratio, vol_ratio_i)

                if (eff_vol_ratio >= vol_min and
                        close_loc >= loc_min and
                        upper_shadow < self.cfg.get("max_upper_shadow", 0.3) and
                        ma5_ok and ma20_slope_ok):
                    confirmed = {
                        "idx": j,
                        "base_idx": i,
                        "close": close_j,
                        "distance_atr": distance_atr,
                        "vol_ratio": vol_ratio if vol_ratio >= vol_ratio_i else vol_ratio_i,
                        "close_loc": close_loc,
                        "upper_shadow": upper_shadow,
                        "ma5_above_ma10": ma5_ok,
                        "ma20_slope_ok": ma20_slope_ok,
                    }
                    break

            if confirmed is not None:
                # 距离太远惩罚
                if confirmed["distance_atr"] > self.cfg.get("breakout_distance_penalty", 1.5):
                    confirmed["penalized"] = True

                # 优先选择非惩罚候选；同级别取最早确认日
                if best is None or (best.get("penalized") and not confirmed.get("penalized")):
                    best = confirmed
                break  # 只认第一次有效突破簇

            i += 1

        if best is None:
            return result

        result.is_breakout = True
        result.breakout_idx = best["idx"]
        result.breakout_date = str(df["trade_date"].iloc[best["idx"]]) if "trade_date" in df.columns else ""
        result.breakout_price = best["close"]
        result.impulse_high = impulse_high
        result.breakout_distance_atr = best["distance_atr"]
        result.volume_ratio = best["vol_ratio"]
        result.close_location = best["close_loc"]
        result.upper_shadow = best["upper_shadow"]
        result.ma5_above_ma10 = best["ma5_above_ma10"]
        result.ma20_slope_ok = best["ma20_slope_ok"]

        # 假突破检测
        if best["distance_atr"] > self.cfg.get("max_breakout_distance_atr", 2.0):
            result.is_fake_breakout = True

        result.score = self._compute_score(result)
        return result

    def _compute_score(self, r: BreakoutResult) -> float:
        s = 0.0
        if r.volume_ratio >= 2.0:
            s += 25
        elif r.volume_ratio >= 1.5:
            s += 20
        elif r.volume_ratio >= 1.3:
            s += 12

        if r.close_location >= 0.85:
            s += 20
        elif r.close_location >= 0.75:
            s += 14

        if r.upper_shadow <= 0.15:
            s += 20
        elif r.upper_shadow <= 0.30:
            s += 12

        s += 15 if r.ma5_above_ma10 else 5
        s += 10 if r.ma20_slope_ok else 3

        if r.breakout_distance_atr > 1.5:
            s -= 15
        if r.is_fake_breakout:
            s -= 30

        return round(min(100.0, max(0.0, s)), 1)


# ═══════════════════════════════════════════════════════
# 7. 第一次回踩检测
# ═══════════════════════════════════════════════════════

class FirstPullbackDetector:
    """检测突破后的第一次健康回踩。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("pullback", {}))
        if config:
            self.cfg.update(config)

    def detect(self, df: pd.DataFrame, breakout: BreakoutResult,
               base: PostImpulseBaseResult, impulse: ImpulseResult,
               end_idx: int) -> PullbackResult:
        """检测第一次回踩。"""
        result = PullbackResult()
        if not breakout.is_breakout:
            return result

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        opens = df["open"].values.astype(float)
        vols = df["vol"].values.astype(float)

        b_idx = breakout.breakout_idx
        impulse_high = impulse.impulse_high
        breakout_price = breakout.breakout_price
        base_low = base.base_low

        # 突破后首次回落
        search_end = min(end_idx, b_idx + self.cfg.get("max_days", 5))
        if search_end <= b_idx:
            return result

        # 找突破后的最高点
        post_highs = highs[b_idx:search_end + 1]
        peak_idx = b_idx + int(np.argmax(post_highs))
        peak_high = highs[peak_idx]

        # 从峰值往下找回踩低点
        if peak_idx >= search_end:
            return result

        post_lows = lows[peak_idx:search_end + 1]
        pullback_low_idx = peak_idx + int(np.argmin(post_lows))
        pullback_low = lows[pullback_low_idx]

        # 回踩参数
        result.is_pullback = True
        result.pullback_start_idx = peak_idx
        result.pullback_low_idx = pullback_low_idx
        result.pullback_low = pullback_low
        result.pullback_days = pullback_low_idx - peak_idx

        # 回踩深度（规范§23：相对突破幅度）
        # 突破幅度 = 突破后峰值 - ImpulseHigh（第一波高点被突破的空间）
        breakout_range = peak_high - impulse_high
        if breakout_range > 0:
            result.pullback_depth = (peak_high - pullback_low) / breakout_range
        else:
            result.pullback_depth = 0.5

        # 回踩量（规范§23：回踩量 <= 突破量的80%）
        # 突破量取基准日/确认日中的最大量能（放量可能分布在两根K线）
        breakout_vol = max(vols[b_idx], vols[b_idx + 1]) if b_idx + 1 <= end_idx else vols[b_idx]
        if peak_idx < pullback_low_idx:
            pullback_vols = vols[peak_idx + 1:pullback_low_idx + 1]
            avg_pullback_vol = np.mean(pullback_vols) if len(pullback_vols) > 0 else vols[peak_idx]
        else:
            avg_pullback_vol = vols[peak_idx]
        result.pullback_volume_ratio = avg_pullback_vol / breakout_vol if breakout_vol > 0 else 0

        # 是否跌破 ImpulseHigh
        result.broke_impulse_high = pullback_low < impulse_high

        # 是否跌回平台
        result.fell_back_to_base = pullback_low < base_low

        # TEST_AND_RECLAIM 检测
        if result.broke_impulse_high:
            # 跌破 ImpulseHigh 但收盘站回
            for i in range(peak_idx, search_end + 1):
                if lows[i] < impulse_high and closes[i] >= impulse_high:
                    result.is_test_and_reclaim = True
                    break

        # 关键位承接检测
        result.support_found = self._check_support(
            pullback_low, impulse_high, base_low,
            df, peak_idx, pullback_low_idx
        )

        result.score = self._compute_score(result, breakout_price, peak_high)
        return result

    def _check_support(self, pullback_low, impulse_high, base_low,
                       df, start_idx, low_idx) -> bool:
        """检查关键位承接。"""
        # 是否守住 ImpulseHigh
        if pullback_low >= impulse_high:
            return True
        # 是否守住 BaseHigh 区域
        if "ma5" in df.columns:
            ma5_low = _safe_float(df["ma5"].values[low_idx], 0)
            if pullback_low >= ma5_low * 0.98:  # 接近 MA5
                return True
        return False

    def _compute_score(self, r: PullbackResult, breakout_price: float,
                       peak_high: float) -> float:
        s = 0.0
        cfg = self.cfg

        # 缩量 (40分)
        if r.pullback_volume_ratio <= cfg.get("volume_ratio_optimal", 0.65):
            s += 40
        elif r.pullback_volume_ratio <= cfg.get("volume_ratio_max", 0.80):
            s += 28
        elif r.pullback_volume_ratio <= 1.0:
            s += 10

        # 关键位承接 (30分)
        if r.support_found:
            s += 30
        elif not r.broke_impulse_high:
            s += 30
        else:
            s += 5

        # 回踩深度 (30分)
        d_opt_lo = cfg.get("depth_optimal_low", 0.20)
        d_opt_hi = cfg.get("depth_optimal_high", 0.60)
        if d_opt_lo <= r.pullback_depth <= d_opt_hi:
            s += 30
        elif r.pullback_depth <= cfg.get("depth_max", 0.80):
            s += 15

        # TEST_AND_RECLAIM 加分
        if r.is_test_and_reclaim:
            s += cfg.get("test_and_reclaim_bonus", 6)

        return round(min(100.0, max(0.0, s)), 1)


# ═══════════════════════════════════════════════════════
# 8. 二次启动检测
# ═══════════════════════════════════════════════════════

class ReAccelerationDetector:
    """检测二次启动（回踩后的重新转强）。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("re_acceleration", {}))
        if config:
            self.cfg.update(config)

    def detect(self, df: pd.DataFrame, pullback: PullbackResult,
               breakout: BreakoutResult, impulse: ImpulseResult,
               end_idx: int) -> ReAccelerationResult:
        """检测二次启动。"""
        result = ReAccelerationResult()
        if not pullback.is_pullback:
            return result

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        opens = df["open"].values.astype(float)
        vols = df["vol"].values.astype(float)

        # 在回踩后搜索启动K线
        # 规范§25: 二次启动需突破回踩期间的高点（重新转强）
        search_start = pullback.pullback_low_idx + 1
        if search_start > end_idx:
            return result

        impulse_high = impulse.impulse_high

        # 回踩期间（峰值->低点）的最高价为再启动需突破的阻力
        if pullback.pullback_low_idx > pullback.pullback_start_idx:
            pullback_zone_high = float(np.max(
                highs[pullback.pullback_start_idx:pullback.pullback_low_idx + 1]
            ))
        else:
            pullback_zone_high = highs[pullback.pullback_start_idx]

        # 规范§21/§25：BreakoutDistance = 距突破位的距离（防追高）
        # 基准用突破确认价而非 impulse_high，因为回踩后再启动
        # 价格必然在第一波高点之上数ATR，用第一波高点作基准会误杀
        breakout_ref = breakout.breakout_price if breakout.breakout_price > 0 else impulse_high

        for i in range(search_start, end_idx + 1):
            close = closes[i]
            ma5 = _safe_float(df["ma5"].values[i], 0) if "ma5" in df.columns else 0
            ma10 = _safe_float(df["ma10"].values[i], 0) if "ma10" in df.columns else 0
            prev_high = highs[i - 1] if i > 0 else close

            vol_ma = _safe_float(df["vol_ma20"].values[i], 0) if "vol_ma20" in df.columns else 0
            vol_ratio = vols[i] / vol_ma if vol_ma > 0 else 0

            close_loc = 0.5
            day_range = highs[i] - lows[i]
            if day_range > 0:
                close_loc = (close - lows[i]) / day_range

            ma5_slope_up = True
            if "ma5" in df.columns and i >= 1:
                ma5_prev = _safe_float(df["ma5"].values[i - 1], 0)
                ma5_slope_up = ma5 > ma5_prev

            # 综合判断
            if (close > ma5 and
                    close > prev_high and
                    ma5 > ma10 and
                    ma5_slope_up and
                    vol_ratio >= self.cfg.get("volume_ratio_min", 1.1) and
                    close_loc >= self.cfg.get("close_location_min", 0.75)):

                atr_val = _safe_float(df["atr20"].values[i], 0) if "atr20" in df.columns else 0
                dist_atr = abs(close - breakout_ref) / atr_val if atr_val > 0 else 999

                # 规范§21: >2ATR 禁止（假突破/追高），1.5~2ATR 允许但降分
                if dist_atr <= self.cfg.get("max_distance_atr_hard", 2.0):
                    result.is_reacceleration = True
                    result.reacc_idx = i
                    result.reacc_date = str(df["trade_date"].iloc[i]) if "trade_date" in df.columns else ""
                    result.reacc_price = close
                    result.volume_ratio = vol_ratio
                    result.close_location = close_loc
                    result.ma5 = ma5
                    result.ma10 = ma10
                    result.break_pullback_high = close > pullback_zone_high
                    result.ma5_slope_up = ma5_slope_up
                    result.distance_atr = dist_atr

                    if "vwap" in df.columns:
                        result.vwap = _safe_float(df["vwap"].values[i], 0)

                    result.score = self._compute_score(result)
                    return result

        return result

    def _compute_score(self, r: ReAccelerationResult) -> float:
        s = 0.0
        s += 25 if r.volume_ratio >= 1.5 else 15
        s += 25 if r.close_location >= 0.85 else 15
        s += 25 if r.ma5_slope_up else 5
        s += 25 if r.break_pullback_high else 10
        return round(min(100.0, max(0.0, s)), 1)
