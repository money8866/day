# -*- coding: utf-8 -*-
"""
RIB V2.1 下一状态预测模块（V2.1 §21/§22/§11）

核心：
  1. NEXT_STATE        - 预测下一状态
  2. NEXT_STATE_SCORE  - 距离下一状态还有多远（0~100，越高越接近）
     V2.1 权重（§22）：距离触发位30 + 结构成熟度20 + 平台质量15
     + 量价状态10 + MA状态10 + 波动率收缩5 + 市场环境5 + 主题同步5
  3. NEXT_SCORE_LEVEL  - VERY_NEAR(>=85)/NEAR(80~84)/POTENTIAL(70~79)/NOT_READY(<70)
  4. NEXT_STATE_GAP    - 触发价格距离 / ATR 距离 / 文字描述
  5. DISTANCE_TO_BREAKOUT - 距突破触发位的五档分带
     (IMMINENT/VERY_NEAR/NEAR/NORMAL/FAR)

原则：任何状态都必须给出"距离下一状态还有多远"，防止把高分但远未
到买点的股票提前进入交易池。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

import numpy as np
import pandas as pd

from .config import RIB_CONFIG

if TYPE_CHECKING:
    from .engine import RIBResult


def _safe_float(val, default=0.0) -> float:
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


@dataclass
class NextStateInfo:
    """下一状态预测结果。"""
    next_state: str = ""          # 预测的下一状态
    score: float = 0.0            # NEXT_STATE_SCORE 0~100
    level: str = "NOT_READY"      # V2.1 §22 分级
    gap_price: float = 0.0        # 到触发位价格距离
    gap_atr: float = 0.0          # 到触发位 ATR 距离
    trigger_price: float = 0.0    # 触发位价格
    gap_desc: str = ""            # "需要突破15.00，当前14.72，距离0.28"
    trigger_cond: str = ""        # 预计触发条件描述
    components: dict = field(default_factory=dict)  # 分项评分
    penalties: List[str] = field(default_factory=list)  # 老化等惩罚


def next_score_level(score: float) -> str:
    """V2.1 §22：NEXT_STATE_SCORE 分级。"""
    if score >= 85:
        return "VERY_NEAR"
    if score >= 80:
        return "NEAR"
    if score >= 70:
        return "POTENTIAL"
    return "NOT_READY"


def classify_distance_to_breakout(close: float, impulse_high: float,
                                  atr: float) -> tuple:
    """V2.1 §11：计算距突破触发位（ImpulseHigh+0.3ATR）的距离并五档分类。

    DistanceToTrigger = (BreakoutTrigger - Close) / ATR
      <=0.3ATR  : IMMINENT
      0.3~0.7ATR: VERY_NEAR
      0.7~1.0ATR: NEAR
      1.0~1.5ATR: NORMAL
      >1.5ATR   : FAR
    """
    v2 = RIB_CONFIG.get("v2", {})
    if atr <= 0:
        return "FAR", 99.0
    buffer = RIB_CONFIG.get("breakout", {}).get("atr_buffer", 0.3)
    trigger = impulse_high + buffer * atr
    dist = (trigger - close) / atr
    if dist <= v2.get("distance_imminent", 0.3):
        return "IMMINENT", dist
    if dist <= v2.get("distance_very_near", 0.7):
        return "VERY_NEAR", dist
    if dist <= v2.get("distance_near", 1.0):
        return "NEAR", dist
    if dist <= v2.get("distance_normal", 1.5):
        return "NORMAL", dist
    return "FAR", dist


def _regime_score(regime: str, cfg: dict) -> float:
    """市场环境得分（0~100）。"""
    return float(cfg.get("regime_scores", {}).get(regime, 60))


class NextStateAnalyzer:
    """下一状态预测分析器。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG)
        if config:
            self.cfg.update(config)
        self.v2 = self.cfg.get("v2", {})

    # ─────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────
    def analyze(self, df: pd.DataFrame, result: RIBResult) -> NextStateInfo:
        """根据当前状态预测下一状态。"""
        info = NextStateInfo()
        state = result.state
        ms = _regime_score(result.market_regime, self.v2)

        # 终态（失败/无效）→ 无下一状态
        if state in ("FAILED_REVERSAL", "FAILED_BREAKOUT",
                     "FAILED_PULLBACK", "INVALIDATED"):
            info.next_state = "重新寻找新结构"
            info.score = 0.0
            info.trigger_cond = "本轮结构已失效，必须重新寻找完整的下跌→反转→平台→突破→回踩结构。"
            return info

        # PRIMARY_BUY → HOLD
        if state == "PRIMARY_BUY":
            info.next_state = "HOLD"
            info.score = 100.0
            info.trigger_cond = "已进入买点，按计划持有 3~5 个交易日。"
            return info

        # 下一状态映射（禁止跳级）
        targets = {
            "DOWNTREND": "REVERSAL_SETUP",
            "REVERSAL_SETUP": "IMPULSE_ACTIVE",
            "IMPULSE_ACTIVE": "IMPULSE_PEAK",
            "IMPULSE_PEAK": "POST_IMPULSE_BASE",
            "POST_IMPULSE_BASE": "SECOND_LEG_BREAKOUT",
            "PRE_BREAKOUT": "SECOND_LEG_BREAKOUT",
            "SECOND_LEG_BREAKOUT": "FIRST_PULLBACK",
            "FIRST_PULLBACK": "PULLBACK_SUPPORT",
            "PULLBACK_SUPPORT": "RE_ACCELERATION",
            "RE_ACCELERATION": "PRIMARY_BUY",
        }
        info.next_state = targets.get(state, "")

        # 分状态计算
        try:
            if state in ("POST_IMPULSE_BASE", "PRE_BREAKOUT"):
                self._calc_near_breakout(df, result, info, ms)
            elif state == "PULLBACK_SUPPORT":
                self._calc_to_reacc(df, result, info, ms)
            elif state == "FIRST_PULLBACK":
                self._calc_to_support(df, result, info, ms)
            elif state == "SECOND_LEG_BREAKOUT":
                self._calc_to_pullback(df, result, info, ms)
            elif state == "IMPULSE_PEAK":
                self._calc_to_base(df, result, info, ms)
            elif state == "IMPULSE_ACTIVE":
                self._calc_to_peak(df, result, info, ms)
            elif state == "DOWNTREND":
                self._calc_to_setup(df, result, info, ms)
            elif state == "RE_ACCELERATION":
                self._calc_to_primary(df, result, info, ms)
            else:
                info.score = 0.0
        except Exception:
            info.score = 0.0

        info.score = round(min(100.0, max(0.0, info.score)), 1)
        info.level = next_score_level(info.score)
        info.gap_desc = self._build_gap_desc(info, result)
        return info

    # ─────────────────────────────────────────────
    # 各状态计算
    # ─────────────────────────────────────────────

    def _calc_near_breakout(self, df, result, info, ms) -> None:
        """POST_IMPULSE_BASE / PRE_BREAKOUT -> SECOND_LEG_BREAKOUT。
        V2.1 §22：距离30+结构成熟度20+平台质量15+量价10+MA10+波动收缩5+市场5+主题5。
        """
        imp = result.impulse
        base = result.base
        if not imp:
            return
        atr = _safe_float(df["atr20"].values[-1], 1.0) or 1.0
        close = result.close
        impulse_high = imp.impulse_high
        atr_buffer = self.cfg.get("breakout", {}).get("atr_buffer", 0.3)

        trigger = impulse_high + atr_buffer * atr
        info.trigger_price = trigger
        info.gap_price = max(0.0, trigger - close)
        info.gap_atr = info.gap_price / atr if atr > 0 else 99.0
        info.trigger_cond = f"放量突破{trigger:.2f}（第一波高点{impulse_high:.2f}+0.3ATR）"

        s = 0.0
        # ── 距离触发位 (30，§22) ──
        if info.gap_atr <= 0.3:
            s += 30
        elif info.gap_atr <= 0.7:
            s += 26
        elif info.gap_atr <= 1.0:
            s += 21
        elif info.gap_atr <= 1.5:
            s += 12
        else:
            s += 5

        # ── 结构成熟度 (20)：平台时间结构 ──
        days = base.platform_days if base and base.is_base else 0
        if 7 <= days <= 15:
            s += 20
        elif days >= 5:
            s += 13
        elif days >= 3:
            s += 7

        # ── 平台质量 (15) ──
        bq = (base.score / 100.0) if base and base.score > 0 else 0
        s += 15 * bq

        # ── 量价状态 (10)：缩量蓄势最佳 ──
        vr = _safe_float(df["vol_ratio"].values[-1], 0)
        us = _safe_float(df["upper_shadow"].values[-1], 0)
        if vr <= 0.8:
            s += 10
        elif vr <= 1.2:
            s += 7
        elif vr <= 1.5:
            s += 4
        else:
            s += 1
        # §12 反例：贴近阻力 + 放量/长上影 = 抛压
        if info.gap_atr <= 0.7 and (vr >= 1.5 or us >= 0.3):
            s -= 8
            info.penalties.append("贴近阻力但放量/长上影（§12抛压）")

        # ── MA 状态 (10) ──
        ma5 = _safe_float(df["ma5"].values[-1], 0)
        ma10 = _safe_float(df["ma10"].values[-1], 0)
        if ma5 > ma10:
            s += 6
        elif ma5 > ma10 * 0.99:
            s += 3
        if _safe_float(df["ma20_slope"].values[-1], 0) >= 0:
            s += 4

        # ── 波动率收缩 (5) ──
        try:
            highs_v = df["high"].values.astype(float)
            lows_v = df["low"].values.astype(float)
            e = len(df) - 1
            r_amp = float(np.mean(highs_v[e-2:e+1] - lows_v[e-2:e+1]))
            p_amp = float(np.mean(highs_v[e-7:e-2] - lows_v[e-7:e-2]))
            if p_amp > 0 and r_amp / p_amp <= 0.8:
                s += 5
        except Exception:
            pass

        # ── 市场环境 (5) + 主题同步 (5，中性2.5) ──
        s += 5 * (ms / 100.0)
        s += 2.5

        # ── 老化惩罚（§33）──
        if result.state == "PRE_BREAKOUT" and base and base.is_base:
            days_at = (len(df) - 1) - base.platform_end_idx
            if days_at > self.v2.get("pre_expired_days", 10):
                s -= 15
                info.penalties.append(f"PRE_BREAKOUT_EXPIRED（{days_at}日未突破）")
            elif days_at > self.v2.get("pre_aged_days", 5):
                pen = float(self.v2.get("pre_aged_penalty", 10))
                s -= pen
                info.penalties.append(f"PRE_BREAKOUT_AGED（{days_at}日未突破，-{pen}分）")

        info.components = {"价格距离": round(info.gap_atr, 2),
                           "平台质量": round(bq * 100, 0),
                           "平台天数": days,
                           "量能": round(vr, 2),
                           "市场": result.market_regime}
        info.score = s

    def _calc_to_reacc(self, df, result, info, ms) -> None:
        """PULLBACK_SUPPORT → RE_ACCELERATION。"""
        pb = result.pullback
        if not pb:
            return
        atr = _safe_float(df["atr20"].values[-1], 1.0) or 1.0
        close = result.close
        highs = df["high"].values.astype(float)

        # 回踩区高点 = 再启动需突破的阻力
        if pb.pullback_low_idx > pb.pullback_start_idx:
            pullback_high = float(np.max(
                highs[pb.pullback_start_idx:pb.pullback_low_idx + 1]
            ))
        else:
            pullback_high = float(highs[pb.pullback_start_idx])

        info.trigger_price = pullback_high
        info.gap_price = max(0.0, pullback_high - close)
        info.gap_atr = info.gap_price / atr if atr > 0 else 99.0
        info.trigger_cond = f"放量突破回踩高点{pullback_high:.2f}"

        s = 0.0
        # 价格距离 (35)
        if info.gap_atr <= 0.3:
            s += 35
        elif info.gap_atr <= 0.7:
            s += 25
        elif info.gap_atr <= 1.5:
            s += 15
        else:
            s += 8

        # 成交量 (20)
        vr = _safe_float(df["vol_ratio"].values[-1], 0)
        if vr >= 1.1:
            s += 20
        elif vr >= 0.9:
            s += 12
        else:
            s += 6

        # MA (20)
        ma5 = _safe_float(df["ma5"].values[-1], 0)
        ma5_prev = _safe_float(df["ma5"].values[-2], 0) if len(df) > 1 else ma5
        ma10 = _safe_float(df["ma10"].values[-1], 0)
        if ma5 > ma5_prev:
            s += 12
        if ma5 > ma10:
            s += 8

        # K线位置 (10)
        s += 10 * max(0.0, min(1.0, _safe_float(df["close_loc"].values[-1], 0.5)))

        # 支撑质量 (5)
        if pb.support_found or not pb.broke_impulse_high:
            s += 5

        # 市场 (5)
        s += 5 * (ms / 100.0)

        # 时间 (5)
        if 1 <= pb.pullback_days <= 5:
            s += 5
        else:
            s += 2

        info.components = {"回踩区高点": round(pullback_high, 2),
                           "距离ATR": round(info.gap_atr, 2),
                           "量能": round(vr, 2)}
        info.score = s

    def _calc_to_support(self, df, result, info, ms) -> None:
        """FIRST_PULLBACK → PULLBACK_SUPPORT。"""
        pb = result.pullback
        bo = result.breakout
        imp = result.impulse
        base = result.base
        if not pb:
            return
        close = result.close

        # 支撑级别优先级：ImpulseHigh → BaseHigh → BreakoutPrice → MA5 → MA10
        levels = []
        if imp:
            levels.append(("ImpulseHigh", imp.impulse_high))
        if base:
            levels.append(("BaseHigh", base.base_high))
        if bo:
            levels.append(("BreakoutPrice", bo.breakout_price))
        ma5 = _safe_float(df["ma5"].values[-1], 0)
        ma10 = _safe_float(df["ma10"].values[-1], 0)
        levels.append(("MA5", ma5))
        levels.append(("MA10", ma10))

        support = levels[0][1]
        support_name = levels[0][0]
        for name, lv in levels:
            if lv > 0 and close >= lv * 0.99:
                support = lv
                support_name = name
                break

        info.trigger_price = support
        info.gap_price = max(0.0, support - close)
        info.trigger_cond = f"回踩在{support_name}（{support:.2f}）获得承接并站回"

        s = 0.0
        # 支撑测试 (40)
        if close >= support:
            s += 40
        elif close >= support * 0.98:
            s += 25
        else:
            s += 8

        # 缩量 (25)
        pvr = pb.pullback_volume_ratio
        if pvr <= 0.65:
            s += 25
        elif pvr <= 0.80:
            s += 18
        elif pvr <= 1.0:
            s += 8

        # 站回关键位 (15)
        if pb.is_test_and_reclaim or (imp and close >= imp.impulse_high):
            s += 15
        else:
            s += 5

        # MA (10)
        ma5v = _safe_float(df["ma5"].values[-1], 0)
        ma10v = _safe_float(df["ma10"].values[-1], 0)
        if ma5v > ma10v:
            s += 6
        if close > ma5v:
            s += 4

        # 市场 (5) + 时间 (5)
        s += 5 * (ms / 100.0)
        if 1 <= pb.pullback_days <= 5:
            s += 5

        info.components = {"关键支撑": support_name,
                           "支撑价": round(support, 2),
                           "回踩量比": round(pvr, 2)}
        info.score = s

    def _calc_to_pullback(self, df, result, info, ms) -> None:
        """SECOND_LEG_BREAKOUT → FIRST_PULLBACK。"""
        bo = result.breakout
        imp = result.impulse
        if not bo:
            return
        atr = _safe_float(df["atr20"].values[-1], 1.0) or 1.0
        close = result.close
        highs = df["high"].values.astype(float)
        end_idx = len(df) - 1

        days_since = end_idx - bo.breakout_idx
        peak_after = float(np.max(highs[bo.breakout_idx:end_idx + 1]))
        info.trigger_cond = "突破后出现第一次缩量回踩（1~5日）"
        info.gap_price = 0.0

        s = 0.0
        # 回踩是否已开始 (30)
        if close < peak_after * 0.99:
            s += 30
        elif close < peak_after:
            s += 15
        else:
            s += 5

        # 量能衰减 (20)：回踩需缩量
        vr = _safe_float(df["vol_ratio"].values[-1], 0)
        if bo.volume_ratio > 0 and vr <= bo.volume_ratio * 0.8:
            s += 20
        elif vr <= 1.0:
            s += 10
        else:
            s += 4

        # 延伸空间 (15)：距突破位 0.5~2.5ATR 最健康
        if imp:
            dist = (close - imp.impulse_high) / atr
            if 0.5 <= dist <= 2.5:
                s += 15
            elif dist > 3.0:
                s += 5   # 追高
            else:
                s += 10

        # MA (10)
        ma5v = _safe_float(df["ma5"].values[-1], 0)
        ma10v = _safe_float(df["ma10"].values[-1], 0)
        if ma5v > ma10v:
            s += 6
        if _safe_float(df["ma20_slope"].values[-1], 0) >= 0:
            s += 4

        # 时间窗口 (20)：接近 max_days 仍未回踩 → 信号老化
        max_wait = self.cfg.get("pullback", {}).get("max_days", 5)
        if days_since <= 2:
            s += 20
        elif days_since <= max_wait:
            s += 12
        else:
            s += 5

        # 市场 (5)
        s += 5 * (ms / 100.0)

        info.components = {"突破后": f"{days_since}日",
                           "突破后高点": round(peak_after, 2),
                           "量能": round(vr, 2)}
        info.score = s

    def _calc_to_base(self, df, result, info, ms) -> None:
        """IMPULSE_PEAK → POST_IMPULSE_BASE。"""
        imp = result.impulse
        peak = result.peak
        if not imp:
            return
        close = result.close
        end_idx = len(df) - 1
        peak_idx = peak.peak_idx if peak else imp.impulse_high_idx
        days_since = end_idx - peak_idx

        impulse_range = imp.impulse_high - imp.impulse_low
        retain = (close - imp.impulse_low) / impulse_range if impulse_range > 0 else 0
        info.trigger_cond = f"形成 5~20 日缩量强势平台，保留≥60%涨幅"
        info.gap_price = 0.0

        s = 0.0
        # 缩量 (30)
        vr = _safe_float(df["vol_ratio"].values[-1], 0)
        if vr <= 0.7:
            s += 30
        elif vr <= 1.0:
            s += 18
        else:
            s += 5

        # 保留率 (25)
        if retain >= 0.60:
            s += 25
        elif retain >= 0.50:
            s += 15
        else:
            s += 5

        # 时间 (20)
        if 3 <= days_since <= 15:
            s += 20
        elif days_since < 3:
            s += 8
        else:
            s += 12

        # MA (10)
        ma5v = _safe_float(df["ma5"].values[-1], 0)
        ma10v = _safe_float(df["ma10"].values[-1], 0)
        if ma5v > ma10v:
            s += 6
        if _safe_float(df["ma20_slope"].values[-1], 0) >= 0:
            s += 4

        # 稳定性 (10)
        recent = df["close"].values[-5:].astype(float)
        spread = (recent.max() - recent.min()) / close if close > 0 else 0
        if spread <= 0.05:
            s += 10
        elif spread <= 0.10:
            s += 5

        # 市场 (5)
        s += 5 * (ms / 100.0)

        info.components = {"峰值后": f"{days_since}日",
                           "保留率": round(retain, 2)}
        info.score = s

    def _calc_to_peak(self, df, result, info, ms) -> None:
        """IMPULSE_ACTIVE → IMPULSE_PEAK。"""
        imp = result.impulse
        if not imp:
            return
        close = result.close
        info.trigger_cond = "第一波动能衰竭（量能萎缩+冲高回落）"

        s = 0.0
        # 涨幅接近目标 (30)
        if imp.impulse_return >= 0.15:
            s += 30
        elif imp.impulse_return >= 0.10:
            s += 15

        # 时间 (20)
        if imp.impulse_days >= 3:
            s += 20
        else:
            s += 10

        # 量能 (25)
        if imp.volume_ratio >= 1.5:
            s += 25
        elif imp.volume_ratio >= 1.2:
            s += 18

        # MA (15)
        ma20 = _safe_float(df["ma20"].values[-1], 0)
        ma60 = _safe_float(df["ma60"].values[-1], 0)
        if close > ma20:
            s += 8
        if close > ma60:
            s += 7

        # 市场 (10)
        s += 10 * (ms / 100.0)

        info.components = {"涨幅": round(imp.impulse_return, 2),
                           "天数": imp.impulse_days}
        info.score = s

    def _calc_to_setup(self, df, result, info, ms) -> None:
        """DOWNTREND → REVERSAL_SETUP。"""
        dt = result.downtrend
        close = result.close
        info.trigger_cond = "长期下跌末端企稳，出现第一波放量反转"

        s = 0.0
        # 下跌结构成熟度 (20)
        if dt and dt.score >= 80:
            s += 20
        elif dt and dt.score >= 65:
            s += 12

        # 近期止跌 (25)：5日线拐头
        ma5 = _safe_float(df["ma5"].values[-1], 0)
        ma5_prev = _safe_float(df["ma5"].values[-2], 0) if len(df) > 1 else ma5
        if close > ma5 and ma5 > ma5_prev:
            s += 25
        elif close > ma5:
            s += 12

        # 量能回暖 (15)
        vr = _safe_float(df["vol_ratio"].values[-1], 0)
        if vr >= 1.2:
            s += 15
        elif vr >= 1.0:
            s += 8

        # MA20 拐头 (15)
        m20s = _safe_float(df["ma20_slope"].values[-1], 0)
        if m20s >= 0:
            s += 15
        elif m20s > -0.02:
            s += 7

        # 超跌 (10)
        if dt and dt.oversold_degree >= 3:
            s += 10
        elif dt and dt.oversold_degree >= 1.5:
            s += 5

        # 市场 (15)
        s += 15 * (ms / 100.0)

        info.components = {"下跌分": dt.score if dt else 0,
                           "止跌": "是" if (close > ma5 and ma5 > ma5_prev) else "否"}
        info.score = s

    def _calc_to_primary(self, df, result, info, ms) -> None:
        """RE_ACCELERATION → PRIMARY_BUY。"""
        ra = result.reacc
        rr = result.risk_reward
        info.trigger_cond = "满足全部 PRIMARY_BUY 条件（盈亏比≥2 + 市场非BEAR + 结构完整）"

        s = 0.0
        # 盈亏比 (35)
        if rr >= 3.0:
            s += 35
        elif rr >= 2.0:
            s += 30
        else:
            s += 15 * (rr / 2.0)

        # 突破回踩高点 (15)
        if ra and ra.break_pullback_high:
            s += 15
        elif ra:
            s += 8

        # 量能 (15)
        if ra:
            if ra.volume_ratio >= 1.5:
                s += 15
            elif ra.volume_ratio >= 1.1:
                s += 10

        # 收盘位置 (10)
        s += 10 * max(0.0, min(1.0, ra.close_location if ra else 0.5))

        # 市场 (15)
        if result.market_regime in ("bull", "normal"):
            s += 15
        elif result.market_regime == "recovery":
            s += 10
        elif result.market_regime == "weak":
            s += 5

        # 结构风险 (10)
        if result.structure_risk < 50:
            s += 10

        info.components = {"RR": round(rr, 2), "市场": result.market_regime}
        info.score = s

    def _build_gap_desc(self, info: NextStateInfo, result: RIBResult) -> str:
        """生成 NEXT_STATE_GAP 文字描述。"""
        if info.next_state in ("HOLD", "重新寻找新结构"):
            return info.trigger_cond
        if info.trigger_price <= 0:
            return info.trigger_cond
        if info.gap_price <= 0.001:
            return f"已到达触发位{info.trigger_price:.2f}，等待{info.trigger_cond}的K线确认"
        return (f"需要{info.trigger_cond}，当前{result.close:.2f}，"
                f"距离{info.gap_price:.2f}（{info.gap_atr:.1f}ATR）")
