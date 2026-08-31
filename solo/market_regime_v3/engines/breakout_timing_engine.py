"""BreakoutTimingEngine V1.0 - V8右侧确认后二阶段突破择时引擎（T+1/T+3 BREAKOUT TIMING）

定位：
  V8 回答"有没有进入右侧交易区？"（启动->回调->右侧确认，输出 S/A/B 级）。
  本引擎回答"什么时候买，谁最可能马上启动？"--只做 BREAKOUT TIMING / QUALITY / FAILURE RISK。

禁止重复计算：
  Fundamental / Alpha / Washout / Quant Score 一律不碰，仅作为 V8 前置结果的输入。

输入：
  - V8 结果（right_confirm_buy_{date}.json 的 S/A/B 级信号，或 RightConfirmResult 列表）
  - DataLoader 日线（open/high/low/close/vol + ma_qfq_5/10/20/60 + atr_qfq）
  - 大盘环境（market_score / regime / env_tier）+ 主题强度（theme_scores / top_themes）

分层规则：
  V8_S -> 直接 PRIMARY_CANDIDATE（T1+T3 双评分）
  V8_A -> T1 + T3 双评分
  V8_B -> T3 评分为主；量能加速 + VWAP收复 + 距确认价<=2% 三条件同时成立才升级 T1 评分
  V8_C 及以下 -> 不处理

T1_SCORE(100) = TRIGGER_PROXIMITY 20 + VOLUME_READINESS 20 + BREAKOUT_HOLD 15
              + VWAP_SUPPORT 15 + SHORT_TREND 10 + RELATIVE_STRENGTH 10
              + THEME_SYNC 5 + RISK_CONTROL 5

T3_SCORE(100) = BASE_COMPLETION 25 + TREND_RECOVERY 20 + VOLUME_STRUCTURE 15
              + VWAP_RECLAIM 10 + RS_STABILITY 10 + THEME_MOMENTUM 10 + RISK_SAFETY 10

BREAKOUT_WINDOW: D0 / D1 / D2 / D3 / D5_PLUS
FALSE_BREAKOUT_RISK: 0~100（>55 禁 PRIMARY_BUY；>70 AVOID）
RETEST_QUALITY_SCORE: 0~100（>=80 定义 PRIMARY_RETEST_BUY）

状态机（唯一允许状态）：
  PRIMARY_BUY / PRIMARY_RETEST_BUY / NEAR_TRIGGER / T3_WATCH / WAIT_PULLBACK
  FALSE_BREAKOUT / OVERHEATED / FAILED_STRUCTURE / AVOID

排序（未来启动概率优先，V8 只是前置）：
  BREAKOUT_PRIORITY = T1×40% + T3×30% + RETEST×15% + V8_NORM×10% + THEME_SYNC×5% - FBR×0.20

仓位模型：
  PRIMARY_BUY: T1>=90 -> 20%~25% | T1 85~89 -> 15%~20%
  PRIMARY_RETEST_BUY: 15%~25%（按 Retest Quality 调整）
  NEAR_TRIGGER: 5%~10% 试仓 | T3_WATCH: 0%
  总仓位受 Market Regime 限制。
"""

import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'inst_pullback_v2'))
from data.loader import DataLoader


# ──────────────────────────────────────────────
# 结果结构
# ──────────────────────────────────────────────
@dataclass
class BreakoutTimingResult:
    """二阶段突破择时单股结果"""
    ts_code: str = ""
    name: str = ""
    v8_grade: str = ""                 # S / A / B
    v8_score: float = 0.0

    # 双评分
    t1_score: float = 0.0
    t3_score: float = 0.0
    t1_subs: Dict[str, float] = field(default_factory=dict)
    t3_subs: Dict[str, float] = field(default_factory=dict)
    t1_eligible: bool = False          # B级是否升级进入 T1 评分

    # 择时核心
    breakout_window: str = ""          # D0 / D1 / D2 / D3 / D5_PLUS
    breakout_priority: float = 0.0     # 最终排序分
    false_breakout_risk: float = 0.0   # 0~100
    fbr_level: str = ""                # LOW / MEDIUM / HIGH
    retest_quality: float = 0.0        # 0~100
    retest_zone: str = ""              # 理想回踩区描述

    # 状态机
    state: str = ""                    # PRIMARY_BUY / PRIMARY_RETEST_BUY / NEAR_TRIGGER / T3_WATCH / WAIT_PULLBACK / FALSE_BREAKOUT / OVERHEATED / FAILED_STRUCTURE / AVOID
    state_reason: str = ""

    # 交易计划
    current_price: float = 0.0
    confirm_price: float = 0.0
    best_buy_zone: str = ""            # 最佳买点描述
    trigger_price: float = 0.0         # 启动触发价
    invalid_price: float = 0.0         # 失效价
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    position_size: str = ""            # 建议仓位

    # 诊断
    distance_to_trigger: float = 0.0   # (close-confirm)/confirm 百分比
    vol_readiness: float = 0.0         # 量能准备度 0~20（VolumeReadiness）
    volume_ratio: float = 0.0          # 今日量/5日均量
    close_pos: float = 0.0             # 收盘位置
    upper_shadow: float = 0.0          # 上影线比例
    rs5: float = 0.0
    rs10: float = 0.0
    rs20: float = 0.0
    theme_sync: float = 0.0            # 0~100
    base_days: int = 0                 # 平台天数
    base_range: float = 0.0            # 平台振幅
    consecutive_up: int = 0            # 连续上涨天数
    bias_ma20: float = 0.0             # 乖离MA20(%)
    event_risk: bool = False
    risk_gate_pass: bool = True        # RiskGate
    missing_signals: List[str] = field(default_factory=list)  # 需补齐的信号
    core_reason: str = ""              # ≤3句话核心理由


# ──────────────────────────────────────────────
# 引擎
# ──────────────────────────────────────────────
class BreakoutTimingEngine:
    """V8 -> T+1/T+3 突破择时引擎 V1.0"""

    def __init__(self, config: dict = None):
        self.cfg = (config or {}).get('breakout_timing', {})
        self.loader = DataLoader()
        self._benchmark_cache: Optional[np.ndarray] = None

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────
    def detect(self, v8_signal: dict, trade_date: str,
               market_env: dict = None, stock_theme: str = "") -> Optional[BreakoutTimingResult]:
        """对单只 V8 S/A/B 信号做二阶段择时。

        Args:
            v8_signal: V8 信号 dict（right_confirm_buy JSON 单元素）
            trade_date: 交易日 YYYYMMDD
            market_env: {market_score, regime, env_tier, risk_score, top_themes, theme_scores}
            stock_theme: 个股主题名
        """
        ts_code = v8_signal.get('ts_code', '')
        if not ts_code:
            return None
        grade = str(v8_signal.get('signal_level', ''))
        if grade not in ('S', 'A', 'B'):
            return None
        _name = str(v8_signal.get('name', '') or '')
        if 'ST' in _name.upper() or '退' in _name:
            return None

        market_env = market_env or {}
        td = trade_date
        lookback = self.cfg.get('lookback', 120)
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback + 40)).strftime('%Y%m%d')
        df = self.loader.load_stk_factor(ts_code, start_date, td, silent=True)
        if df is None or df.empty or len(df) < 45:
            return None

        r = BreakoutTimingResult(
            ts_code=ts_code,
            name=v8_signal.get('name', ''),
            v8_grade=grade,
            v8_score=float(v8_signal.get('final_score', 0) or 0),
        )

        close = df['close_qfq'].values if 'close_qfq' in df.columns else df['close'].values
        open_vals = df['open_qfq'].values if 'open_qfq' in df.columns else df['open'].values
        high = df['high_qfq'].values if 'high_qfq' in df.columns else df['high'].values
        low = df['low_qfq'].values if 'low_qfq' in df.columns else df['low'].values
        vol = df['vol'].values if 'vol' in df.columns else None
        n = len(close)
        if n < 45 or vol is None:
            return None
        vol = np.asarray(vol, dtype=float)

        # ── 基础指标 ──
        ma5 = self._ma(df, close, 'ma_qfq_5', 5)
        ma10 = self._ma(df, close, 'ma_qfq_10', 10)
        ma20 = self._ma(df, close, 'ma_qfq_20', 20)
        atr = self._atr(df, close, high, low)

        last_close = float(close[-1])
        r.current_price = round(last_close, 2)
        r.confirm_price = float(v8_signal.get('confirm_price', 0) or 0)
        r.stop_loss = float(v8_signal.get('stop_loss', 0) or 0)
        r.tp1 = float(v8_signal.get('tp1', 0) or 0)
        r.tp2 = float(v8_signal.get('tp2', 0) or 0)
        if r.confirm_price <= 0:
            r.confirm_price = last_close

        # VWAP（日线典型价量加权，与 V8 同法）
        vwap_series = self._vwap(high, low, close, vol)
        vwap_now = float(vwap_series[-1]) if vwap_series is not None else last_close
        vwap_prev = float(vwap_series[-2]) if vwap_series is not None and len(vwap_series) >= 2 else vwap_now

        # 量能
        vol5 = float(np.mean(vol[-5:]))
        vol10 = float(np.mean(vol[-10:])) if n >= 10 else vol5
        vol20 = float(np.mean(vol[-20:])) if n >= 20 else vol10
        vol3 = float(np.mean(vol[-3:]))
        today_vol = float(vol[-1])
        r.volume_ratio = round(today_vol / vol5, 2) if vol5 > 0 else 1.0

        # 相对强度（对沪深300，单位：百分点超额）
        rs5, rs10, rs20 = self._rs_vs_benchmark(close, td)
        r.rs5, r.rs10, r.rs20 = round(rs5, 1), round(rs10, 1), round(rs20, 1)

        # K线形态
        today_high, today_low = float(high[-1]), float(low[-1])
        r.close_pos = round((last_close - today_low) / (today_high - today_low), 2) if today_high > today_low else 0.5
        r.upper_shadow = round((today_high - last_close) / (today_high - today_low), 2) if today_high > today_low else 0.0

        # 连续上涨 / 乖离
        r.consecutive_up = self._consecutive_up(close)
        r.bias_ma20 = round((last_close - ma20) / ma20 * 100, 1) if ma20 > 0 else 0.0

        # 主题
        r.theme_sync = self._theme_sync_score(stock_theme, market_env)

        # 平台识别（3~15日）
        base = self._detect_base(close, high, vol, n)
        r.base_days, r.base_range = base['days'], round(base['range'], 3)

        # ── 分层判定 ──
        t1_eligible, upgrade_reason = self._layer_rule(grade, r, vol, vol20, vol3, vol5,
                                                       vwap_now, last_close, n)
        r.t1_eligible = t1_eligible

        # ═══════════════════════════════════════════
        # 1. T1_SCORE
        # ═══════════════════════════════════════════
        if t1_eligible:
            t1_subs = {}
            t1_subs['trigger_proximity'] = self._t1_trigger_proximity(r, last_close)
            vr, vol_flags = self._t1_volume_readiness(vol, vol3, vol5, vol20, close, n)
            t1_subs['volume_readiness'] = vr
            r.vol_readiness = vr
            t1_subs['breakout_hold'] = self._t1_breakout_hold(r, last_close)
            t1_subs['vwap_support'] = self._t1_vwap_support(r, last_close, vwap_now, vwap_prev)
            t1_subs['short_trend'] = self._t1_short_trend(close, ma5, ma10, ma20, n)
            t1_subs['relative_strength'] = self._t1_rs(rs5, rs10, rs20)
            t1_subs['theme_sync'] = round(r.theme_sync * 5.0 / 100.0, 1)
            t1_subs['risk_control'] = self._t1_risk_control(r, atr, last_close)
            r.t1_score = round(sum(t1_subs.values()), 1)
            r.t1_subs = t1_subs
            r.missing_signals.extend(vol_flags)
        else:
            r.t1_score = 0.0
            r.vol_readiness = 0.0
            r.distance_to_trigger = round((last_close - r.confirm_price) / r.confirm_price * 100, 2) if r.confirm_price > 0 else 0.0

        # ═══════════════════════════════════════════
        # 2. T3_SCORE
        # ═══════════════════════════════════════════
        t3_subs = {}
        t3_subs['base_completion'] = self._t3_base_completion(base)
        t3_subs['trend_recovery'] = self._t3_trend_recovery(close, ma5, ma10, ma20, n)
        t3_subs['volume_structure'] = self._t3_volume_structure(vol, n)
        t3_subs['vwap_reclaim'] = self._t3_vwap_reclaim(last_close, vwap_now, vwap_prev)
        t3_subs['rs_stability'] = self._t3_rs_stability(rs5, rs10, rs20)
        t3_subs['theme_momentum'] = self._t3_theme_momentum(r.theme_sync, stock_theme)
        t3_subs['risk_safety'] = self._t3_risk_safety(r, atr, last_close)
        r.t3_score = round(sum(t3_subs.values()), 1)
        r.t3_subs = t3_subs

        # ═══════════════════════════════════════════
        # 3. FALSE_BREAKOUT_RISK
        # ═══════════════════════════════════════════
        fbr, fbr_factors = self._false_breakout_risk(r, last_close, today_high,
                                                     vwap_now, vol20, today_vol, market_env, atr)
        r.false_breakout_risk = round(fbr, 1)
        r.fbr_level = 'LOW' if fbr <= 30 else ('MEDIUM' if fbr <= 55 else 'HIGH')
        r.missing_signals.extend(fbr_factors)

        # ═══════════════════════════════════════════
        # 4. RETEST_QUALITY（T+1回踩优先模型）
        # ═══════════════════════════════════════════
        rq = self._retest_quality(r, last_close, vwap_now, ma5, vol3, vol5, vol20, today_vol, close, n)
        r.retest_quality = round(rq['score'], 1)
        r.retest_zone = rq['zone']

        # ═══════════════════════════════════════════
        # 5. BREAKOUT_WINDOW
        # ═══════════════════════════════════════════
        r.breakout_window = self._breakout_window(r, last_close)

        # ═══════════════════════════════════════════
        # 6. 状态机
        # ═══════════════════════════════════════════
        self._state_machine(r, last_close, market_env)

        # ═══════════════════════════════════════════
        # 7. BREAKOUT_PRIORITY 排序分
        # ═══════════════════════════════════════════
        v8_norm = min(100.0, max(0.0, r.v8_score))
        w = self.cfg.get('priority_weights', {})
        r.breakout_priority = round(
            r.t1_score * w.get('t1', 0.40) +
            r.t3_score * w.get('t3', 0.30) +
            r.retest_quality * w.get('retest', 0.15) +
            v8_norm * w.get('v8', 0.10) +
            r.theme_sync * w.get('theme', 0.05) -
            r.false_breakout_risk * self.cfg.get('fbr_penalty', 0.20), 1)

        # 交易计划细节
        self._fill_plan(r, last_close, ma20, atr)

        return r

    # ══════════════════════════════════════════════
    # 分层规则
    # ══════════════════════════════════════════════
    def _layer_rule(self, grade, r, vol, vol20, vol3, vol5, vwap_now, last_close, n):
        """V8_S/A -> T1+T3；V8_B -> T3 为主，三条件同时成立才升级 T1。"""
        if grade in ('S', 'A'):
            return True, ""
        # B 级升级判定：VOLUME_ACCELERATION + VWAP_RECLAIM + 距确认价<=2%
        vol_accel = False
        if vol20 > 0:
            vol_accel = (vol5 / vol20 >= 1.05) and (vol3 / vol5 >= 1.0)
        vwap_reclaim = vwap_now > 0 and last_close > vwap_now
        near_trigger = False
        if r.confirm_price > 0:
            dist = (last_close - r.confirm_price) / r.confirm_price
            near_trigger = dist <= 0.02
        if vol_accel and vwap_reclaim and near_trigger:
            return True, "B级升级：量能加速+VWAP收复+距确认价<=2%"
        return False, ""

    # ══════════════════════════════════════════════
    # T1 子评分
    # ══════════════════════════════════════════════
    def _t1_trigger_proximity(self, r, last_close):
        """距触发价距离 0~20。0%~1% 最优；>8% 归零。"""
        if r.confirm_price <= 0:
            return 8.0
        dist = (last_close - r.confirm_price) / r.confirm_price
        r.distance_to_trigger = round(dist * 100, 2)
        if 0 <= dist <= 0.01:
            return 20.0
        if 0.01 < dist <= 0.02:
            return 18.0
        if 0.02 < dist <= 0.03:
            return 15.0
        if 0.03 < dist <= 0.05:
            return 10.0
        if 0.05 < dist <= 0.08:
            return 5.0
        if dist > 0.08:
            return 0.0
        # 负值（价格低于确认价，未突破）：按接近度给分
        if dist > -0.01:
            return 16.0
        if dist > -0.02:
            return 12.0
        if dist > -0.03:
            return 9.0
        return 5.0

    def _t1_volume_readiness(self, vol, vol3, vol5, vol20, close, n):
        """量能准备度 0~20。
        最佳：缩量整理->温和放量（COMPRESSION_EXPANSION）16~20。
        放量滞涨（VOLUME_PRICE_DIVERGENCE）扣 5~15。
        """
        score = 10.0
        flags = []
        if vol20 <= 0:
            return score, flags

        today_20 = float(vol[-1]) / vol20
        r3_20 = vol3 / vol20
        # 前期缩量（整理）：前5-10日均值 vs vol20
        prev5 = float(np.mean(vol[-10:-5])) if n >= 10 else vol5
        compression = prev5 / vol20 < 0.85
        expansion = today_20 >= 1.2 or r3_20 >= 1.1

        if compression and expansion:
            score = 20.0
        elif compression and 0.9 <= today_20 < 1.2:
            score = 17.0
        elif compression and today_20 < 0.9:
            score = 12.0  # 缩量到位但今日未放量，接近就绪
        elif vol5 / vol20 >= 1.1 and today_20 >= 1.1:
            score = 15.0
        elif today_20 < 0.7:
            score = 8.0
            flags.append('量能萎缩')
        else:
            score = 11.0

        # 放量滞涨：巨量但价格不涨
        chg_today = float(close[-1] / close[-2] - 1) if n >= 2 and close[-2] > 0 else 0.0
        if today_20 >= 1.8 and chg_today <= 0.005:
            score = max(0.0, score - 12.0)
            flags.append('放量滞涨')
        elif today_20 >= 1.5 and chg_today < 0:
            score = max(0.0, score - 8.0)
            flags.append('放量不涨')
        return score, flags

    def _t1_breakout_hold(self, r, last_close):
        """突破站稳度 0~15。"""
        score = 5.0
        above = last_close > r.confirm_price if r.confirm_price > 0 else False
        if above:
            score += 4.0
        if r.close_pos >= 0.7:
            score += 3.0
        elif r.close_pos >= 0.5:
            score += 1.5
        if above and r.volume_ratio >= 0.8:
            score += 2.0
        # 长上影+收弱
        if r.upper_shadow >= 0.5 and r.close_pos < 0.4:
            score = max(0.0, score - 6.0)
        # 收盘跌回确认价以下
        if r.confirm_price > 0 and last_close < r.confirm_price * 0.99:
            score = max(0.0, score - 5.0)
        return min(15.0, score)

    def _t1_vwap_support(self, r, last_close, vwap_now, vwap_prev):
        """VWAP 支撑 0~15。BREAKOUT_RETEST_CONFIRM 13~15；跌破VWAP+VWAP向下 <=5。"""
        if vwap_now <= 0:
            return 7.5
        above_vwap = last_close >= vwap_now
        vwap_up = vwap_now >= vwap_prev
        score = 6.0
        if above_vwap and vwap_up:
            score = 12.0
            if r.retest_quality >= 80:
                score = 15.0
            elif r.volume_ratio >= 1.3:
                score = 13.5
        elif above_vwap and not vwap_up:
            score = 8.0
        elif not above_vwap and vwap_up:
            score = 5.0
        else:
            score = 3.0
        dist_vwap = (last_close - vwap_now) / vwap_now
        if dist_vwap > 0.05:
            score -= 3.0
        return max(0.0, min(15.0, score))

    def _t1_short_trend(self, close, ma5, ma10, ma20, n):
        """短趋势 0~10。MA5向上0-3 + MA10向上0-3 + 价>MA5 0-2 + MA20走平/向上0-2。"""
        score = 0.0
        if n >= 7:
            s5 = pd.Series(close).rolling(5).mean()
            ma5_prev = float(s5.iloc[-2]) if not np.isnan(s5.iloc[-2]) else None
            if ma5_prev and ma5 > ma5_prev:
                score += 3.0
        if n >= 12:
            s10 = pd.Series(close).rolling(10).mean()
            ma10_prev = float(s10.iloc[-2]) if not np.isnan(s10.iloc[-2]) else None
            if ma10_prev and ma10 > ma10_prev:
                score += 3.0
        if ma5 > 0 and close[-1] > ma5:
            score += 2.0
        if ma20 > 0 and n >= 22:
            s20 = pd.Series(close).rolling(20).mean()
            ma20_prev = float(s20.iloc[-6]) if not np.isnan(s20.iloc[-6]) else None
            if ma20_prev and ma20 >= ma20_prev * 0.998:
                score += 2.0
        return min(10.0, score)

    def _t1_rs(self, rs5, rs10, rs20):
        """相对强度 0~10。RS5>=0 且 RS10>=0 高分；RS5<-5 禁高等级（<=3）。"""
        score = 4.0
        if rs5 >= 0 and rs10 >= 0:
            score = 10.0
            if rs20 < -5:
                score = 7.0
        elif rs5 >= 0 or rs10 >= 0:
            score = 6.5
        if rs5 < -5:
            score = min(score, 3.0)
        return score

    def _t1_risk_control(self, r, atr, last_close):
        """风险控制 0~5。事件/过热/ATR异常/连续上涨。"""
        score = 5.0
        if r.event_risk:
            score = 0.0
        if r.consecutive_up >= 4:
            score = min(score, 2.0)
        if r.bias_ma20 > 15:
            score = min(score, 2.0)
        if atr > 0 and last_close > 0 and atr / last_close > 0.07:
            score = min(score, 1.0)
        return score

    # ══════════════════════════════════════════════
    # T3 子评分
    # ══════════════════════════════════════════════
    def _t3_base_completion(self, base):
        """平台完成度 0~25。5~12日 + 振幅4%~12% + 低点抬高 + 缩量 + 压力明确。"""
        days, rng = base['days'], base['range']
        score = 6.0
        if 5 <= days <= 12:
            score += 8.0
        elif 3 <= days < 5:
            score += 5.0
        elif 12 < days <= 15:
            score += 4.0
        if 0.04 <= rng <= 0.12:
            score += 6.0
        elif 0.12 < rng <= 0.18:
            score += 3.0
        if base['higher_low']:
            score += 3.0
        if base['vol_declining']:
            score += 3.0
        if base['resistance_clear']:
            score += 2.0
        return min(25.0, score)

    def _t3_trend_recovery(self, close, ma5, ma10, ma20, n):
        """趋势恢复 0~20。最优 PRICE>MA5>MA10>MA20。"""
        score = 0.0
        last = float(close[-1])
        if ma5 > 0 and ma10 > 0 and ma20 > 0:
            if last > ma5 > ma10 > ma20:
                score = 20.0
            elif last > ma5 and ma5 > ma10:
                score = 15.0
            elif last > ma20 and last > ma5:
                score = 12.0
            elif last > ma20:
                score = 9.0
            elif last > ma20 * 0.98:
                score = 5.0
        if n >= 7:
            s5 = pd.Series(close).rolling(5).mean()
            ma5_prev = float(s5.iloc[-2]) if not np.isnan(s5.iloc[-2]) else None
            if ma5_prev and ma5 > ma5_prev:
                score += 2.0
        return min(20.0, score)

    def _t3_volume_structure(self, vol, n):
        """量能结构 0~15。ENERGY_BUILDING：VOL20下降->VOL5收缩->近1-3日改善。"""
        if n < 40:
            return 7.0
        vol20_now = float(np.mean(vol[-20:]))
        vol20_prev = float(np.mean(vol[-40:-20]))
        vol5 = float(np.mean(vol[-5:]))
        vol3 = float(np.mean(vol[-3:]))
        score = 5.0
        if vol20_prev > 0 and vol20_now / vol20_prev < 0.95:
            score += 4.0
        if vol20_now > 0 and vol5 / vol20_now < 0.85:
            score += 4.0
        if vol5 > 0 and (vol3 / vol5 > 1.05 or vol[-1] / vol5 > 1.1):
            score += 5.0
        elif vol5 > 0 and vol3 / vol5 >= 1.0:
            score += 2.0
        return min(15.0, score)

    def _t3_vwap_reclaim(self, last_close, vwap_now, vwap_prev):
        """VWAP收复 0~10。"""
        if vwap_now <= 0:
            return 5.0
        above = last_close > vwap_now
        up = vwap_now >= vwap_prev
        if above and up:
            return 10.0
        if above and not up:
            return 7.0
        if not above and up and last_close > vwap_now * 0.99:
            return 5.0
        return 2.0

    def _t3_rs_stability(self, rs5, rs10, rs20):
        """RS稳定性 0~10。RS5>=0 RS10>=0 RS20 未明显恶化。"""
        score = 3.0
        if rs5 >= 0:
            score += 2.5
        if rs10 >= 0:
            score += 2.5
        if rs20 >= -3:
            score += 2.0
        return min(10.0, score)

    def _t3_theme_momentum(self, theme_sync, stock_theme):
        """主题动量 0~10。升温高分，退潮低分。"""
        if not stock_theme:
            return 5.0
        if theme_sync >= 80:
            return 10.0
        if theme_sync >= 65:
            return 8.0
        if theme_sync >= 50:
            return 6.0
        if theme_sync >= 35:
            return 3.0
        return 1.0

    def _t3_risk_safety(self, r, atr, last_close):
        """风险安全 0~10。无高位爆量/连续长上影/异常乖离。"""
        score = 10.0
        if r.consecutive_up >= 4:
            score -= 3.0
        if r.upper_shadow >= 0.5 and r.close_pos < 0.4:
            score -= 3.0
        if r.bias_ma20 > 15:
            score -= 3.0
        if r.bias_ma20 > 25:
            score -= 4.0
        if atr > 0 and last_close > 0 and atr / last_close > 0.07:
            score -= 3.0
        if r.volume_ratio >= 2.0 and r.bias_ma20 > 10:
            score -= 3.0
        return max(0.0, min(10.0, score))

    # ══════════════════════════════════════════════
    # 假突破风险
    # ══════════════════════════════════════════════
    def _false_breakout_risk(self, r, last_close, today_high,
                             vwap_now, vol20, today_vol, market_env, atr):
        """假突破风险 0~100。9类风险因素加权。"""
        risk = 0.0
        factors = []
        cp = r.confirm_price

        # 1 突破无量
        if cp > 0 and last_close > cp and vol20 > 0 and today_vol / vol20 < 1.0:
            risk += 15
            factors.append('突破无量')
        # 2 放量但收盘弱
        if vol20 > 0 and today_vol / vol20 >= 1.8 and r.close_pos < 0.4:
            risk += 15
            factors.append('放量收盘弱')
        # 3 长上影
        if r.upper_shadow >= 0.5:
            risk += 10
            factors.append('长上影')
        # 4 跌回确认价
        if cp > 0 and today_high > cp and last_close < cp:
            risk += 20
            factors.append('跌回确认价')
        # 5 跌破VWAP
        if vwap_now > 0 and last_close < vwap_now * 0.99:
            risk += 10
            factors.append('跌破VWAP')
        # 6 大盘弱
        ms = market_env.get('market_score', 50)
        if ms < 45:
            risk += 15
            factors.append('大盘弱')
        elif ms < 50:
            risk += 8
            factors.append('大盘偏弱')
        # 7 主题退潮
        if r.theme_sync < 35:
            risk += 10
            factors.append('主题退潮')
        # 8 连续上涨后高位突破
        if r.consecutive_up >= 3 and r.bias_ma20 > 10:
            risk += 10
            factors.append('连续上涨后高位突破')
        # 9 ATR异常扩大（今日上影 > 2.5×ATR%）
        if atr > 0 and last_close > 0 and today_high > 0:
            amp_today = (today_high - last_close) / last_close
            if amp_today > 2.5 * atr / last_close:
                risk += 8
                factors.append('ATR异常扩大')
        return min(100.0, risk), factors

    # ══════════════════════════════════════════════
    # T+1 回踩优先模型
    # ══════════════════════════════════════════════
    def _retest_quality(self, r, last_close, vwap_now, ma5, vol3, vol5, vol20, today_vol, close, n):
        """RETEST_QUALITY 0~100。
        距确认价20 + 回踩缩量20 + VWAP支撑20 + MA5支撑15 + 回踩后阳线15 + 重新放量10
        """
        cp = r.confirm_price
        score = 0.0
        # 距确认价
        if cp > 0:
            dist = (last_close - cp) / cp
            if 0 <= dist <= 0.02:
                score += 20
            elif 0.02 < dist <= 0.04:
                score += 15
            elif -0.02 <= dist < 0:
                score += 18  # 贴着确认价下方，随时突破
            else:
                score += 8
        # 回踩缩量
        if vol5 > 0 and vol3 / vol5 <= 1.0:
            score += 12
        if vol20 > 0 and vol3 / vol20 <= 0.85:
            score += 8
        # VWAP支撑
        if vwap_now > 0:
            d = (last_close - vwap_now) / vwap_now
            if -0.01 <= d <= 0.03:
                score += 20
            elif 0.03 < d <= 0.05:
                score += 14
            elif d < -0.01:
                score += 5
            else:
                score += 10
        # MA5支撑
        if ma5 > 0:
            d5 = (last_close - ma5) / ma5
            if -0.01 <= d5 <= 0.03:
                score += 15
            elif 0.03 < d5 <= 0.05:
                score += 10
            elif d5 < -0.01:
                score += 4
            else:
                score += 7
        # 回踩后阳线
        if n >= 2 and close[-1] > close[-2]:
            score += 8
            if r.close_pos >= 0.6:
                score += 7
        # 重新放量
        if vol5 > 0 and today_vol / vol5 >= 1.2:
            score += 10
        elif vol5 > 0 and today_vol / vol5 >= 1.0:
            score += 6
        # 理想回踩区
        zone_parts = []
        if cp > 0:
            zone_parts.append(f"{cp * 0.99:.2f}~{cp * 1.01:.2f}")
        if vwap_now > 0:
            zone_parts.append(f"VWAP {vwap_now:.2f}")
        if ma5 > 0:
            zone_parts.append(f"MA5 {ma5:.2f}")
        return {'score': min(100.0, score), 'zone': ' / '.join(zone_parts)}

    # ══════════════════════════════════════════════
    # BREAKOUT_WINDOW
    # ══════════════════════════════════════════════
    def _breakout_window(self, r, last_close):
        """D0 / D1 / D2 / D3 / D5_PLUS。"""
        cp = r.confirm_price
        dist = (last_close - cp) / cp if cp > 0 else 1.0
        # D0：已突破但尚未充分扩张（<=2%）
        if 0 < dist <= 0.02:
            return 'D0'
        # D1：T1>=85 + 距确认价<=3% + VolumeReadiness>=70(14/20)
        if r.t1_score >= 85 and dist <= 0.03 and r.vol_readiness >= 14:
            return 'D1'
        # D2：T3>=80 + 平台成熟 + 尚未触发
        if r.t3_score >= 80 and r.base_days >= 3 and dist <= 0.0:
            return 'D2'
        # D3：T3>=75 结构正在形成
        if r.t3_score >= 75:
            return 'D3'
        return 'D5_PLUS'

    # ══════════════════════════════════════════════
    # 状态机
    # ══════════════════════════════════════════════
    def _state_machine(self, r, last_close, market_env):
        """最终交易状态机。按严重度从高到低判定。"""
        cp = r.confirm_price
        dist = (last_close - cp) / cp if cp > 0 else 1.0
        ms = market_env.get('market_score', 50)

        # RiskGate
        r.risk_gate_pass = (not r.event_risk) and r.fbr_level != 'HIGH' and ms >= 40

        # 优先级1: AVOID（假突破风险极端）
        if r.false_breakout_risk > 70:
            r.state = 'AVOID'
            r.state_reason = f"假突破风险{r.false_breakout_risk:.0f}>70"
            return
        # 优先级2: FAILED_STRUCTURE（跌破MA20且放量）
        if r.bias_ma20 < -3 and r.volume_ratio >= 1.5:
            r.state = 'FAILED_STRUCTURE'
            r.state_reason = f"跌破MA20({r.bias_ma20:+.1f}%)且放量({r.volume_ratio}倍)"
            return
        # 优先级3: FALSE_BREAKOUT（突破后跌回+高风险）
        if cp > 0 and dist < 0 and r.false_breakout_risk >= 55:
            r.state = 'FALSE_BREAKOUT'
            r.state_reason = f"跌回确认价下方且假突破风险{r.false_breakout_risk:.0f}"
            return
        # 优先级4: OVERHEATED（过热）
        if r.consecutive_up >= 4 and r.bias_ma20 > 12:
            r.state = 'OVERHEATED'
            r.state_reason = f"连续上涨{r.consecutive_up}日+乖离MA20 {r.bias_ma20:+.1f}%"
            return
        if dist > 0.08:
            r.state = 'OVERHEATED'
            r.state_reason = f"距确认价{dist * 100:.1f}%>8%，脱离最佳风险收益区"
            return
        # 优先级5: PRIMARY_BUY
        if (r.v8_score >= 80 and r.t1_score >= 85 and r.t3_score >= 75
                and r.false_breakout_risk < 35 and r.risk_gate_pass):
            r.state = 'PRIMARY_BUY'
            r.state_reason = (f"V8={r.v8_score:.0f} T1={r.t1_score:.0f} T3={r.t3_score:.0f} "
                              f"FBR={r.false_breakout_risk:.0f} RiskGate=PASS")
            return
        # 优先级6: PRIMARY_RETEST_BUY（已突破+回踩质量>=80）
        if dist > 0 and r.retest_quality >= 80 and r.false_breakout_risk < 55:
            r.state = 'PRIMARY_RETEST_BUY'
            r.state_reason = f"已突破+回踩质量{r.retest_quality:.0f}"
            return
        # 优先级7: WAIT_PULLBACK（已突破但距离>5% 或距MA20过远）
        if dist > 0.05 or r.bias_ma20 > 10:
            r.state = 'WAIT_PULLBACK'
            r.state_reason = f"距确认价{dist * 100:.1f}%，乖离MA20 {r.bias_ma20:+.1f}%，等回踩"
            return
        # 优先级8: NEAR_TRIGGER
        if r.t1_score >= 78 and dist <= 0.03 and r.vol_readiness >= 12:
            r.state = 'NEAR_TRIGGER'
            r.state_reason = f"T1={r.t1_score:.0f} 距确认价{dist * 100:.1f}% 量能就绪{r.vol_readiness:.0f}/20"
            return
        # 优先级9: T3_WATCH
        if r.t3_score >= 75:
            r.state = 'T3_WATCH'
            r.state_reason = f"T3={r.t3_score:.0f} 结构形成中，T1={r.t1_score:.0f}未达78"
            return
        # 兜底
        if r.t3_score >= 65:
            r.state = 'T3_WATCH'
            r.state_reason = f"T3={r.t3_score:.0f} 未达75，结构未成熟"
        else:
            r.state = 'AVOID'
            r.state_reason = f"T3={r.t3_score:.0f} T1={r.t1_score:.0f} 均不达标"

    # ══════════════════════════════════════════════
    # 仓位与计划
    # ══════════════════════════════════════════════
    def _fill_plan(self, r, last_close, ma20, atr):
        """最佳买点 / 触发价 / 失效价 / 仓位 / 核心理由。"""
        cp = r.confirm_price
        if r.state == 'PRIMARY_BUY':
            r.best_buy_zone = (f"回踩{cp * 0.99:.2f}~{cp * 1.01:.2f}不破后走强，或放量突破{max(cp, last_close):.2f}"
                               if cp > 0 else f"现价{last_close:.2f}附近")
            r.position_size = '20%~25%' if r.t1_score >= 90 else '15%~20%'
        elif r.state == 'PRIMARY_RETEST_BUY':
            r.best_buy_zone = f"回踩{r.retest_zone} 缩量不破后重新走强"
            ratio = max(0.0, min(1.0, (r.retest_quality - 80) / 20.0))
            r.position_size = f"{15 + ratio * 5:.0f}%~{20 + ratio * 5:.0f}%"
        elif r.state == 'NEAR_TRIGGER':
            r.best_buy_zone = f"放量突破{cp:.2f}时试仓" if cp > 0 else ''
            r.position_size = '5%~10%试仓'
        elif r.state == 'T3_WATCH':
            r.best_buy_zone = f"等待放量突破{cp:.2f}" if cp > 0 else ''
            r.position_size = '0%（等待触发）'
        else:
            r.best_buy_zone = ''
            r.position_size = '0%'

        r.trigger_price = round(max(cp, last_close * 1.005), 2) if cp > 0 else round(last_close, 2)
        # 失效价：确认价-3% 或 V8结构止损（取高者）
        fail1 = cp * 0.97 if cp > 0 else 0.0
        fail2 = r.stop_loss if r.stop_loss > 0 else 0.0
        if fail1 > 0 and fail2 > 0:
            r.invalid_price = round(max(fail1, fail2), 2)
        else:
            r.invalid_price = round(fail1 or fail2 or last_close * 0.94, 2)

        if r.stop_loss <= 0:
            sup = max(ma20 * 0.98, last_close - 1.5 * atr) if ma20 > 0 and atr > 0 else last_close * 0.93
            r.stop_loss = round(sup, 2)

        # 核心理由（≤3句）
        parts = [f"V8 {r.v8_grade}级{r.v8_score:.0f}分，T1={r.t1_score:.0f}/T3={r.t3_score:.0f}，窗口{r.breakout_window}"]
        if r.t1_eligible:
            parts.append(f"距确认价{r.distance_to_trigger:+.1f}%，量能准备{r.vol_readiness:.0f}/20")
        else:
            parts.append("B级未升级T1（需量能加速+VWAP收复+距确认价≤2%）")
        if r.fbr_level != 'LOW':
            parts.append(f"假突破风险{r.false_breakout_risk:.0f}({r.fbr_level})")
        r.core_reason = '；'.join(parts[:3])

    # ══════════════════════════════════════════════
    # 工具
    # ══════════════════════════════════════════════
    def _ma(self, df, close, col, period):
        if col in df.columns:
            s = df[col].iloc[-1]
            if pd.notna(s):
                return float(s)
        return float(pd.Series(close).rolling(period).mean().iloc[-1]) if len(close) >= period else 0.0

    def _atr(self, df, close, high, low):
        if 'atr_qfq' in df.columns:
            v = df['atr_qfq'].iloc[-1]
            if pd.notna(v) and float(v) > 0:
                return float(v)
        tr = []
        for i in range(1, len(close)):
            tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        return float(np.mean(tr[-14:])) if tr else 0.0

    @staticmethod
    def _vwap(high, low, close, vol):
        if vol is None or len(vol) == 0:
            return None
        typical = (np.asarray(high, dtype=float) + np.asarray(low, dtype=float) + np.asarray(close, dtype=float)) / 3.0
        cum_pv = np.cumsum(typical * vol)
        cum_v = np.cumsum(vol)
        return np.where(cum_v > 0, cum_pv / np.where(cum_v > 0, cum_v, 1), 0)

    def _rs_vs_benchmark(self, close, td):
        """个股 vs 沪深300 相对强度（5/10/20日超额收益，百分点）。"""
        bench = self._load_benchmark(td)
        if bench is None or len(bench) < 25 or len(close) < 25:
            def _abs_ret(k):
                if len(close) > k and close[-1 - k] > 0:
                    return (close[-1] / close[-1 - k] - 1) * 100
                return 0.0
            return _abs_ret(5), _abs_ret(10), _abs_ret(20)

        def _ret(arr, k):
            if len(arr) > k and arr[-1 - k] > 0:
                return (arr[-1] / arr[-1 - k] - 1) * 100
            return 0.0

        m = min(len(close), len(bench))
        c = close[-m:]
        b = bench[-m:]
        return (_ret(c, 5) - _ret(b, 5), _ret(c, 10) - _ret(b, 10), _ret(c, 20) - _ret(b, 20))

    def _load_benchmark(self, td):
        if self._benchmark_cache is not None:
            return self._benchmark_cache
        try:
            start = (pd.to_datetime(td) - pd.Timedelta(days=90)).strftime('%Y%m%d')
            df = self.loader.load_index_data('000300.SH', start, td, silent=True)
            if df is not None and not df.empty and 'close' in df.columns:
                self._benchmark_cache = df['close'].values.astype(float)
                return self._benchmark_cache
        except Exception:
            pass
        self._benchmark_cache = None
        return None

    def _theme_sync_score(self, stock_theme, market_env):
        """主题同步 0~100：theme_scores 强度 + top_themes 排名。"""
        if not stock_theme:
            return 50.0
        theme_scores = market_env.get('theme_scores', {}) or {}
        top_themes = market_env.get('top_themes', []) or []
        strength = theme_scores.get(stock_theme)
        base = float(strength) if strength is not None else 50.0
        in_top3 = False
        in_top = False
        for i, t in enumerate(top_themes):
            tname = t.get('name', '') if isinstance(t, dict) else t
            if tname == stock_theme:
                in_top = True
                in_top3 = i < 3
                break
        score = base
        if in_top3:
            score = min(100.0, base * 0.6 + 45)
        elif in_top:
            score = min(100.0, base * 0.7 + 25)
        elif strength is not None and strength < 45:
            score = max(0.0, base - 15)
        return max(0.0, min(100.0, score))

    def _detect_base(self, close, high, vol, n):
        """识别最近3~15日平台（收盘价boxed区间）。"""
        max_days = self.cfg.get('base', {}).get('max_days', 15)
        best = {'days': 0, 'range': 0.0, 'higher_low': False, 'vol_declining': False,
                'resistance_clear': False}
        days = 0
        for back in range(1, min(max_days + 1, n)):
            i = n - back
            seg = close[i:n]
            hi, lo = float(np.max(seg)), float(np.min(seg))
            rng = (hi - lo) / lo if lo > 0 else 1.0
            if rng <= 0.15:
                days = back
                best['range'] = rng
            else:
                break
        if days >= 3:
            recent_high = float(np.max(high[max(0, n - 20):n])) if n >= 20 else float(np.max(high))
            seg_low = close[n - days:n]
            half = max(1, days // 2)
            first_half = float(np.mean(seg_low[:half]))
            second_half = float(np.mean(seg_low[half:]))
            best['higher_low'] = bool(second_half >= first_half * 0.999)
            if days >= 4:
                v1 = float(np.mean(vol[n - days:n - half]))
                v2 = float(np.mean(vol[n - half:n]))
                best['vol_declining'] = bool(v1 > 0 and v2 / v1 < 1.0)
            base_high = float(np.max(close[n - days:n]))
            best['resistance_clear'] = bool(base_high < recent_high * 0.99)
        best['days'] = days
        return best

    def _consecutive_up(self, close):
        cnt = 0
        for i in range(len(close) - 1, 0, -1):
            if close[i] > close[i - 1]:
                cnt += 1
            else:
                break
        return cnt

    # ──────────────────────────────────────────────
    # 批量
    # ──────────────────────────────────────────────
    def detect_batch(self, v8_signals: list, trade_date: str,
                     market_env: dict = None, theme_of: dict = None) -> List[BreakoutTimingResult]:
        """批量择时。v8_signals: V8 信号 dict 列表（只处理 S/A/B）。"""
        theme_of = theme_of or {}
        results = []
        for s in v8_signals:
            try:
                r = self.detect(s, trade_date, market_env=market_env,
                                stock_theme=theme_of.get(s.get('ts_code', ''), ''))
                if r is not None:
                    results.append(r)
            except Exception as e:
                print(f"  [BTE] {s.get('ts_code', '?')} 异常: {e}")
        results.sort(key=lambda x: x.breakout_priority, reverse=True)
        return results

    # ──────────────────────────────────────────────
    # 报告
    # ──────────────────────────────────────────────
    @staticmethod
    def env_class(market_env: dict) -> str:
        """BREAKOUT_ENVIRONMENT: STRONG / NORMAL / SELECTIVE / DEFENSIVE。"""
        ms = float(market_env.get('market_score', 50) or 50)
        regime = str(market_env.get('regime', '') or '')
        if ms >= 65 and regime in ('Bull', 'Euphoria', 'Recovery'):
            return 'STRONG'
        if ms >= 55:
            return 'NORMAL'
        if ms >= 45:
            return 'SELECTIVE'
        return 'DEFENSIVE'

    def render_report(self, results: List[BreakoutTimingResult], market_env: dict) -> str:
        """按规范格式输出报告文本。"""
        ms = market_env.get('market_score', '-')
        regime = market_env.get('regime', '-')
        env_class = self.env_class(market_env)

        lines = []
        lines.append('=' * 52)
        lines.append('★ V8 -> T+1/T+3 二阶段突破报告')
        lines.append('=' * 52)
        lines.append('')
        lines.append('市场状态：')
        lines.append(f"MARKET_REGIME: {regime} (score={ms})")
        lines.append(f"BREAKOUT_ENVIRONMENT: {env_class}")
        lines.append('')

        pb = [r for r in results if r.state == 'PRIMARY_BUY']
        pr = [r for r in results if r.state == 'PRIMARY_RETEST_BUY']
        nt = [r for r in results if r.state == 'NEAR_TRIGGER']
        t3w = [r for r in results if r.state == 'T3_WATCH']
        waits = [r for r in results if r.state in ('WAIT_PULLBACK', 'FALSE_BREAKOUT', 'OVERHEATED',
                                                   'FAILED_STRUCTURE', 'AVOID')]

        lines.append('━' * 24)
        lines.append('🔥 【PRIMARY BUY】')
        lines.append('━' * 24)
        if pb:
            for r in pb[:5]:
                lines += self._render_primary(r)
                lines.append('')
        else:
            lines.append('（无）')
            lines.append('')

        lines.append('━' * 24)
        lines.append('🎯 【T+1 PRIMARY RETEST】')
        lines.append('━' * 24)
        if pr:
            for r in pr[:5]:
                lines += self._render_retest(r)
                lines.append('')
        else:
            lines.append('（无）')
            lines.append('')

        lines.append('━' * 24)
        lines.append('🟠 【T+3 WATCH】')
        lines.append('━' * 24)
        if t3w:
            for r in t3w[:8]:
                lines += self._render_t3watch(r)
                lines.append('')
        else:
            lines.append('（无）')
            lines.append('')

        lines.append('━' * 24)
        lines.append('⚠️ 【WAIT / AVOID】')
        lines.append('━' * 24)
        if waits:
            for r in waits[:12]:
                lines.append(f"股票：{r.name}({r.ts_code}) | V8 {r.v8_grade} {r.v8_score:.0f} | 状态：{r.state}")
                lines.append(f"原因：{r.state_reason}")
                lines.append('')
        else:
            lines.append('（无）')
            lines.append('')

        # ── 最终强制结论 ──
        lines.append('=' * 52)
        lines.append('【明日最可能启动 TOP3】')
        lines.append('=' * 52)
        t1_cands = [r for r in results if r.t1_eligible and r.state in
                    ('PRIMARY_BUY', 'PRIMARY_RETEST_BUY', 'NEAR_TRIGGER', 'T3_WATCH', 'WAIT_PULLBACK')
                    and (r.distance_to_trigger or 0) <= 5.0]
        t1_cands.sort(key=lambda x: (-x.t1_score, -x.breakout_priority))
        if not [r for r in t1_cands if r.t1_score >= 85]:
            lines.append('当前V8候选中，没有符合T+1高胜率突破条件的股票，不强行交易。')
        else:
            for i, r in enumerate(t1_cands[:3], 1):
                lines.append(f"{i}. {r.name}({r.ts_code})")
                lines.append(f"   T1 Score: {r.t1_score:.0f}")
                lines.append(f"   启动窗口: {r.breakout_window}")
                lines.append(f"   触发价: {r.trigger_price:.2f}")
                lines.append(f"   买入条件: {r.best_buy_zone or '放量突破触发价'}")
                lines.append(f"   失效价: {r.invalid_price:.2f}")
        lines.append('')

        lines.append('=' * 52)
        lines.append('【未来3日最可能突破 TOP5】')
        lines.append('=' * 52)
        t3_top = [r for r in results if r.t3_score >= 65 and r.state not in
                  ('AVOID', 'FALSE_BREAKOUT', 'FAILED_STRUCTURE', 'OVERHEATED')]
        t3_top.sort(key=lambda x: x.t3_score, reverse=True)
        if not [r for r in t3_top if r.t3_score >= 80]:
            lines.append('当前没有明确T+3突破候选，继续等待平台完成。')
        else:
            for i, r in enumerate(t3_top[:5], 1):
                dist = (r.current_price - r.confirm_price) / r.confirm_price * 100 if r.confirm_price > 0 else 0
                lines.append(f"{i}. {r.name}({r.ts_code})")
                lines.append(f"   T3 Score: {r.t3_score:.0f}")
                lines.append(f"   预计窗口: {r.breakout_window}")
                lines.append(f"   关键突破价: {r.confirm_price:.2f}")
                lines.append(f"   当前距离: {dist:+.1f}%")
        lines.append('')
        lines.append('核心纪律：V8高分≠立即买入；PRIMARY_BUY必须突破距离近+量能准备+VWAP支撑+RS不弱+假突破风险低+风险门控通过。')
        return '\n'.join(lines)

    def _render_primary(self, r):
        return [
            f"股票：{r.name}({r.ts_code})",
            f"V8 Score：{r.v8_grade}级 {r.v8_score:.0f}",
            f"T1 Score：{r.t1_score:.0f} | T3 Score：{r.t3_score:.0f}",
            f"Breakout Priority：{r.breakout_priority:.1f}",
            f"Breakout Window：{r.breakout_window}",
            f"False Breakout Risk：{r.false_breakout_risk:.0f} ({r.fbr_level})",
            f"Retest Quality：{r.retest_quality:.0f}",
            f"当前价：{r.current_price:.2f} | 确认价：{r.confirm_price:.2f}",
            f"最佳买点：{r.best_buy_zone}",
            f"失效价：{r.invalid_price:.2f} | 止损：{r.stop_loss:.2f}",
            f"建议仓位：{r.position_size}",
            f"核心理由：{r.core_reason}",
        ]

    def _render_retest(self, r):
        cp = r.confirm_price
        return [
            f"股票：{r.name}({r.ts_code})",
            f"当前状态：{r.state}（{r.state_reason}）",
            f"确认价：{cp:.2f}",
            f"理想回踩区：{r.retest_zone}",
            f"必须满足：",
            f"1. 回踩不破确认价{cp:.2f}",
            f"2. 回踩缩量（量比<1）",
            f"3. VWAP/MA5上方重新走强放量",
            f"重新启动触发：放量收复今日高点或突破{r.trigger_price:.2f}",
            f"止损：{r.stop_loss:.2f}（失效价{r.invalid_price:.2f}）",
            f"建议仓位：{r.position_size}",
        ]

    def _render_t3watch(self, r):
        dist = (r.current_price - r.confirm_price) / r.confirm_price * 100 if r.confirm_price > 0 else 0
        missing = '、'.join(r.missing_signals[:4]) if r.missing_signals else '等待放量突破'
        return [
            f"股票：{r.name}({r.ts_code}) | V8 {r.v8_grade} {r.v8_score:.0f}",
            f"T1：{r.t1_score:.0f} | T3：{r.t3_score:.0f}",
            f"预计窗口：{r.breakout_window}",
            f"关键突破价：{r.confirm_price:.2f}",
            f"当前距离：{dist:+.1f}%",
            f"Volume Readiness：{r.vol_readiness:.0f}/20",
            f"需要补齐的信号：{missing}",
            f"禁止提前买入。",
        ]


# ──────────────────────────────────────────────
# 导出工具
# ──────────────────────────────────────────────
def _to_dict(r: BreakoutTimingResult) -> dict:
    """BreakoutTimingResult -> JSON dict"""
    return {
        "ts_code": r.ts_code,
        "name": r.name,
        "v8_grade": r.v8_grade,
        "v8_score": round(r.v8_score, 1),
        "t1_score": r.t1_score,
        "t3_score": r.t3_score,
        "t1_subs": r.t1_subs,
        "t3_subs": r.t3_subs,
        "t1_eligible": r.t1_eligible,
        "breakout_window": r.breakout_window,
        "breakout_priority": r.breakout_priority,
        "false_breakout_risk": r.false_breakout_risk,
        "fbr_level": r.fbr_level,
        "retest_quality": r.retest_quality,
        "retest_zone": r.retest_zone,
        "state": r.state,
        "state_reason": r.state_reason,
        "current_price": r.current_price,
        "confirm_price": r.confirm_price,
        "best_buy_zone": r.best_buy_zone,
        "trigger_price": r.trigger_price,
        "invalid_price": r.invalid_price,
        "stop_loss": r.stop_loss,
        "tp1": r.tp1,
        "tp2": r.tp2,
        "position_size": r.position_size,
        "distance_to_trigger": r.distance_to_trigger,
        "vol_readiness": r.vol_readiness,
        "volume_ratio": r.volume_ratio,
        "close_pos": r.close_pos,
        "upper_shadow": r.upper_shadow,
        "rs5": r.rs5,
        "rs10": r.rs10,
        "rs20": r.rs20,
        "theme_sync": round(r.theme_sync, 1),
        "base_days": r.base_days,
        "base_range": r.base_range,
        "consecutive_up": r.consecutive_up,
        "bias_ma20": r.bias_ma20,
        "risk_gate_pass": r.risk_gate_pass,
        "missing_signals": r.missing_signals,
        "core_reason": r.core_reason,
    }


# ──────────────────────────────────────────────
# 独立运行入口
# ──────────────────────────────────────────────
def run_standalone(trade_date: str, config_path: str = None, save: bool = True):
    """独立运行：读取 right_confirm_buy_{date}.json 的 S/A/B 信号做二阶段择时。

    用法: python -X utf8 breakout_timing_engine.py --date 20260828
    """
    import json
    import yaml

    base_dir = os.path.dirname(os.path.abspath(__file__))
    solo_dir = os.path.dirname(os.path.dirname(base_dir))
    if config_path is None:
        config_path = os.path.join(base_dir, 'config.yaml')
    config = {}
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}

    # 读取 V8 JSON
    rcb_path = os.path.join(solo_dir, 'report_daily', f'right_confirm_buy_{trade_date}.json')
    if not os.path.exists(rcb_path):
        print(f"未找到 V8 结果文件: {rcb_path}")
        return None
    with open(rcb_path, 'r', encoding='utf-8') as f:
        v8_data = json.load(f)

    signals = [s for s in (v8_data.get('signals') or []) if s.get('signal_level') in ('S', 'A', 'B')]
    print(f"[BTE] V8 S/A/B 级信号: {len(signals)}只 "
          f"(S:{sum(1 for s in signals if s.get('signal_level')=='S')} "
          f"A:{sum(1 for s in signals if s.get('signal_level')=='A')} "
          f"B:{sum(1 for s in signals if s.get('signal_level')=='B')})")
    if not signals:
        print("V8 结果中无 S/A/B 级信号，二阶段择时无输入")
        return None

    market_env = {
        'market_score': v8_data.get('market_score'),
        'regime': v8_data.get('regime'),
        'env_tier': v8_data.get('env_tier'),
    }

    engine = BreakoutTimingEngine(config)
    results = engine.detect_batch(signals, trade_date, market_env=market_env)
    report = engine.render_report(results, market_env)
    print(report)

    if save and results:
        out_dir = os.path.join(solo_dir, 'report_daily')
        os.makedirs(out_dir, exist_ok=True)
        out_json = os.path.join(out_dir, f"breakout_timing_{trade_date}.json")
        payload = {
            "trade_date": trade_date,
            "market_score": v8_data.get('market_score'),
            "regime": v8_data.get('regime'),
            "env_tier": v8_data.get('env_tier'),
            "breakout_environment": BreakoutTimingEngine.env_class(market_env),
            "signals": [_to_dict(r) for r in results],
        }
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        out_md = os.path.join(out_dir, f"breakout_timing_{trade_date}.md")
        with open(out_md, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n✅ 二阶段择时已导出: {out_json} / {out_md} ({len(results)}只)")
    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='V8 -> T+1/T+3 Breakout Timing Engine V1.0')
    parser.add_argument('--date', type=str, required=True, help='交易日期 YYYYMMDD')
    parser.add_argument('--config', type=str, default=None, help='配置文件路径')
    parser.add_argument('--no-save', action='store_true', help='不保存结果文件')
    args = parser.parse_args()
    run_standalone(args.date, config_path=args.config, save=not args.no_save)
