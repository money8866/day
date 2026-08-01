#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stock Alpha Engine V4.3 - Entry Timing Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
目标：不是推荐最强股票，而是推荐当前最值得买的股票。

根据 Role / Stock Alpha / Sub-theme Stage / Theme Stage / Market Regime
自动识别最佳买点。

信号定义：
  BREAKOUT BUY  - 放量突破平台，创新高，加速度上升
  PULLBACK BUY  - 主升阶段回踩均线，缩量企稳
  PRE_ROTATE BUY - 子主题轮动预信号，资金改善
  HOLD          - 趋势持续，无卖点
  REDUCE        - 加速度下降，量能衰减
  SELL          - 趋势破坏，主力流出
  WATCH         - 无明确信号，观望

每只股票输出：
  entry_signal, entry_score, entry_reason, risk_level, holding_priority

完全独立模块，不修改任何现有接口。
"""

import sys
import os
import math
import json
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

import theme_trend_sentiment_score as theme_ts

# ── 信号优先级排序（分数越高越推荐买入） ──
SIGNAL_PRIORITY = {
    'BREAKOUT BUY': 70,
    'PULLBACK BUY': 65,
    'PRE_ROTATE BUY': 60,
    'HOLD': 50,
    'WATCH': 35,
    'REDUCE': 20,
    'SELL': 5,
}

SIGNAL_LIST = ['BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY', 'HOLD', 'WATCH', 'REDUCE', 'SELL']

# ── 风险等级 ──
RISK_LEVELS = ['low', 'medium', 'high']

# ── 买方向信号（测试/观察/持有可参与的） ──
BUY_SIGNALS = {'BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY'}
HOLD_SIGNALS = {'HOLD'}

# ── 信号等级（分数越高越值得买入） ──
SIGNAL_RANK = {
    'BREAKOUT BUY': 70,
    'PULLBACK BUY': 65,
    'PRE_ROTATE BUY': 60,
    'HOLD': 50,
    'WATCH': 35,
    'REDUCE': 20,
    'SELL': 5,
}


# ═══════════════════════════════════════════════════════════
# Three Gate Filter（三道门过滤系统）
# ═══════════════════════════════════════════════════════════

class ThreeGateFilter:
    """
    三道门过滤架构

    过滤层      条件                          不满足时
    ─────────────────────────────────────────────────────
    Theme Gate  Theme Score ≥ 60 且非 Declining  禁止 BUY，仅 WATCH/REDUCE
    Sub-theme   Sub-theme Score ≥ 65 或         禁止 BREAKOUT，仅允许观察
    Gate        Transition=PRE_ROTATE
    Stock Gate  Alpha、Role、Entry Pattern       才能输出 BUY
                同时达标
    """

    # Theme Gate: 退潮/弱势阶段视为 Declining
    DECLINING_STAGES = {'退潮', '弱势'}

    # Sub-theme Gate: BREAKOUT BUY 最低子主题分
    SUBTHEME_BREAKOUT_MIN = 65

    # Stock Gate: 各信号的角色+Alpha 门槛
    STOCK_GATE_RULES = {
        'BREAKOUT BUY': {'roles': {'Momentum', 'Beta', 'Leader'},    'alpha_min': 60},
        'PULLBACK BUY': {'roles': {'Core', 'Leader', 'Momentum'},    'alpha_min': 50},
        'PRE_ROTATE BUY': {'roles': {'Follower', 'Momentum', 'Beta'}, 'alpha_min': 40},
    }

    def __init__(self, theme_scores: Dict[str, float] = None,
                 theme_stages: Dict[str, str] = None,
                 subtheme_scores: Dict[str, Dict[str, float]] = None,
                 stocks_output: Dict[str, Dict] = None,
                 normalize_threshold: bool = True):
        """
        参数:
          theme_scores: {母主题: 综合分(0-100)}
          theme_stages: {母主题: 阶段}
          subtheme_scores: {母主题: {子主题: 分数}}
          stocks_output: {code: {role, stock_alpha, ...}}
          normalize_threshold: 是否将分数归一化到 0-100（适应实际范围）
        """
        self.theme_scores = theme_scores or {}
        self.theme_stages = theme_stages or {}
        self.subtheme_scores = subtheme_scores or {}
        self.stocks_output = stocks_output or {}
        self.normalize_threshold = normalize_threshold

        # 归一化：将原始分数线性映射到 0-100
        self._raw_min, self._raw_max = self._compute_score_range()

    def _compute_score_range(self) -> Tuple[float, float]:
        """计算所有分数的实际最小/最大值，用于归一化"""
        all_scores = list(self.theme_scores.values())
        for subs in self.subtheme_scores.values():
            all_scores.extend(subs.values())
        if not all_scores:
            return 0, 100
        raw_min = min(all_scores)
        raw_max = max(all_scores)
        # 保底：如果范围太小或为零，不做有意义的缩放
        if raw_max - raw_min < 1:
            return raw_min, raw_max + 100
        return raw_min, raw_max

    def _normalize(self, raw: float) -> float:
        """归一化 0-100"""
        if not self.normalize_threshold:
            return raw
        rng = self._raw_max - self._raw_min
        if rng == 0:
            return 50.0
        return (raw - self._raw_min) / rng * 100.0

    # ── 各门检测 ──

    def _check_theme_gate(self, parent_theme: str) -> Tuple[bool, str]:
        """
        Theme Gate:
          PASS: Theme Score ≥ 60 AND Stage NOT Declining
          BLOCK: 禁止所有买入信号
        """
        raw_score = self.theme_scores.get(parent_theme, 0)
        score = self._normalize(raw_score)
        stage = self.theme_stages.get(parent_theme, '潜伏')

        score_ok = score >= 60
        stage_ok = stage not in self.DECLINING_STAGES

        if not score_ok:
            return False, f'主题评分({raw_score:.0f}→{score:.0f}归一化)<60'
        if not stage_ok:
            return False, f'主题阶段({stage})衰退'
        return True, ''

    def _check_subtheme_gate(self, parent_theme: str,
                             subtheme_name: str,
                             subtheme_signal: str) -> Tuple[bool, str]:
        """
        Sub-theme Gate:
          PASS: Sub-theme Score ≥ 65 OR PRE_ROTATE
          BLOCK: 禁止 BREAKOUT BUY（回踩/轮动依旧允许）
        """
        raw_score = self.subtheme_scores.get(parent_theme, {}).get(subtheme_name, 0)
        score = self._normalize(raw_score)
        is_pre_rotate = 'pre_rotate' in str(subtheme_signal).lower()

        score_ok = score >= 65

        if score_ok or is_pre_rotate:
            return True, ''
        return False, f'子主题评分({raw_score:.0f}→{score:.0f}归一化)<65'

    def _check_stock_gate(self, code: str, signal: str) -> Tuple[bool, str]:
        """
        Stock Gate:
          PASS: Role + Alpha 满足该信号的规则
          BLOCK: 降级（PULLBACK→WATCH, 等）
        """
        if signal not in self.STOCK_GATE_RULES:
            return True, ''  # 非买入信号无需检查

        rule = self.STOCK_GATE_RULES[signal]
        stock_info = self.stocks_output.get(code, {})
        role = stock_info.get('role', '')
        alpha = stock_info.get('stock_alpha', 50) or 50

        role_ok = role in rule['roles']
        alpha_ok = alpha >= rule['alpha_min']

        reasons = []
        if not role_ok:
            reasons.append(f'role={role}∉{rule["roles"]}')
        if not alpha_ok:
            reasons.append(f'alpha({alpha:.0f})<{rule["alpha_min"]}')

        if role_ok and alpha_ok:
            return True, ''
        return False, ';'.join(reasons)

    # ── 门控过滤 ──

    def apply(self, entry_results: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        对 Entry Timing 结果应用三道门过滤

        输入格式（与 run_entry_timing_for_all 输出一致）:
          {母主题: {子主题: {'stocks': {code: {...}}, 'subtheme_stage': '...', ...}}}

        输出: 同上，但 entry_signal/entry_score/entry_reason 已被门控修正
        """
        stats = {'total': 0, 'theme_blocked': 0, 'subtheme_blocked': 0,
                 'stock_blocked': 0, 'passed': 0}

        for parent, subs in entry_results.items():
            for sub_name, data in subs.items():
                stocks_data = data.get('stocks', {})
                if not stocks_data:
                    continue

                sub_signal = data.get('subtheme_signal', data.get('subtheme_stage', ''))

                for code, entry in stocks_data.items():
                    stats['total'] += 1
                    original_signal = entry.get('entry_signal', 'WATCH')
                    is_buy = original_signal in BUY_SIGNALS
                    gate_reasons = []

                    # ── Gate 1: Theme Gate ──
                    tg_pass, tg_reason = self._check_theme_gate(parent)
                    if not tg_pass and is_buy:
                        entry['entry_signal'] = 'WATCH'
                        entry['entry_reason'] = f'主题门禁({tg_reason})'
                        # 重新计算得分
                        entry['entry_score'] = SIGNAL_RANK['WATCH']
                        entry['risk_level'] = 'medium'
                        entry['holding_priority'] = 2
                        gate_reasons.append(tg_reason)
                        stats['theme_blocked'] += 1
                        continue  # 跳过后续门

                    # ── Gate 2: Sub-theme Gate ──
                    if is_buy and original_signal == 'BREAKOUT BUY':
                        sg_pass, sg_reason = self._check_subtheme_gate(parent, sub_name, sub_signal)
                        if not sg_pass:
                            entry['entry_signal'] = 'WATCH'
                            entry['entry_reason'] = f'子主题门禁({sg_reason});禁止突破买入'
                            entry['entry_score'] = SIGNAL_RANK['WATCH']
                            entry['risk_level'] = 'medium'
                            entry['holding_priority'] = 2
                            gate_reasons.append(sg_reason)
                            stats['subtheme_blocked'] += 1
                            continue

                    # ── Gate 3: Stock Gate ──
                    if is_buy:
                        stk_pass, stk_reason = self._check_stock_gate(code, entry['entry_signal'])
                        if not stk_pass:
                            entry['entry_signal'] = 'WATCH'
                            entry['entry_reason'] = f'个股门禁({stk_reason})'
                            entry['entry_score'] = SIGNAL_RANK['WATCH']
                            entry['risk_level'] = 'medium'
                            entry['holding_priority'] = 2
                            gate_reasons.append(stk_reason)
                            stats['stock_blocked'] += 1
                            continue

                    stats['passed'] += 1

        # 统计
        n_buy_after = sum(1 for p in entry_results.values()
                          for s in p.values()
                          for e in s.get('stocks', {}).values()
                          if e.get('entry_signal') in BUY_SIGNALS)
        print(f"  [ThreeGate] 过滤统计:")
        print(f"    总检查: {stats['total']}")
        print(f"    主题门禁: {stats['theme_blocked']}")
        print(f"    子主题门禁: {stats['subtheme_blocked']}")
        print(f"    个股门禁: {stats['stock_blocked']}")
        print(f"    通过: {stats['passed']}")
        print(f"    最终买入信号: {n_buy_after}")

        return entry_results


# ═══════════════════════════════════════════════════════════
# 双评分体系：Trade Score + Investment Score
# ═══════════════════════════════════════════════════════════

# Trade Score: "今天是不是一个好买点？"
# 基于 Entry Signal / 门禁状态 / Alpha / 风险 综合判断
def compute_trade_score(final_score: float, entry_score: float) -> float:
    """
    Trade Score (0-100) — 真正用于排序和实盘推荐

    Trade Score = 0.70 × Final Score + 0.30 × Entry Score

    Final Score：衡量"这只股票整体有多值得关注"
    Entry Score：衡量"今天是不是一个好的介入时点"
    """
    raw = 0.70 * min(final_score, 100) + 0.30 * min(entry_score, 100)
    return round(min(100, max(0, raw)), 1)


def compute_investment_score(
    code: str, stock_alpha: float, role: str,
    role_results: Dict[str, Dict],
    kline_groups: Dict[str, pd.DataFrame],
    theme_stage: str = '',
    daily_basic: Dict = None,
) -> float:
    """
    Investment Score (0-100) — 中长期配置价值

    权重分配：
      Stock Alpha   40%  — 股票自身强度
      Role Sta      25%  — 角色稳定性（Leader长期持有价值高）
      Trend Hlth    15%  — 中长期均线趋势
      Theme Qual    10%  — 主题非衰退加分
      Mkt Cap        5%  — 中大市值偏好
      Risk/Fund      5%  — 低回撤 + 基本面
    """
    # 1. Stock Alpha base (40%)
    alpha_base = min(stock_alpha, 100) / 100 * 40

    # 2. Role stability (25%)
    role_map = {
        'Leader': 25, 'Core': 22, 'Momentum': 18,
        'Beta': 15, 'Follower': 12, 'Defensive': 8, 'Weak': 5,
    }
    role_base = role_map.get(role, 10)

    # 3. Trend health (15%) — 中长期均线
    trend_score = 7.5  # 默认中值
    kdf = kline_groups.get(code) if kline_groups else None
    if kdf is not None and len(kdf) >= 20:
        closes = kdf['close'].astype(float).values[-30:]
        if len(closes) >= 20:
            ma10 = np.mean(closes[-10:])
            ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else ma10
            ma30 = np.mean(closes) if len(closes) >= 30 else ma20
            # 中长期多头排列
            if ma10 > ma20 > ma30:
                trend_score = 15
            elif ma10 > ma20 and ma20 <= ma30:
                trend_score = 11
            elif ma10 > ma30 > ma20:
                trend_score = 10
            elif ma10 > ma20 * 0.97:
                trend_score = 8
            else:
                trend_score = 4

    # 4. Theme quality (10%) — 主题非衰退
    declining_stages = {'退潮', '弱势'}
    theme_bonus = 0 if theme_stage in declining_stages else 10

    # 5. Market cap (5%)
    mv_score = 2.5
    if daily_basic:
        db = daily_basic.get(code, {})
        mv = db.get('total_mv', db.get('circ_mv', 0))
        if mv and mv > 0:
            mv_yi = mv / 1e8
            if 50 <= mv_yi <= 500:
                mv_score = 5
            elif 20 <= mv_yi < 50:
                mv_score = 4
            elif 500 < mv_yi <= 1000:
                mv_score = 4
            elif 10 <= mv_yi < 20:
                mv_score = 3
            elif mv_yi >= 1000:
                mv_score = 3
            else:
                mv_score = 1

    # 6. Risk + Fundamental (5%)
    extra = 2.5  # 默认中值
    if kdf is not None and len(kdf) >= 20:
        closes = kdf['close'].astype(float).values[-20:]
        peak = np.maximum.accumulate(closes)
        dd = (peak - closes) / peak
        max_dd = np.max(dd) if len(dd) > 0 else 0.1
        # max_dd < 5% → +2.5, < 10% → +1.5, < 20% → +0.5
        if max_dd < 0.05:
            extra = 2.5
        elif max_dd < 0.10:
            extra = 2.0
        elif max_dd < 0.20:
            extra = 1.0
        else:
            extra = 0.5

    raw = alpha_base + role_base + trend_score + theme_bonus + mv_score + extra
    return round(min(100, max(0, raw)), 1)


# ═══════════════════════════════════════════════════════════
# 主题评分计算（从 subtheme 聚合）
# ═══════════════════════════════════════════════════════════

def _compute_theme_scores_from_report(subtheme_report: Dict) -> Tuple[Dict, Dict, Dict]:
    """
    从 subtheme_report 聚合出母主题评分和阶段

    输出:
      theme_scores: {母主题: 综合分(0-100)}
      theme_stages: {母主题: 阶段}
      subtheme_scores: {母主题: {子主题: 分数}}
    """
    matrix = subtheme_report.get('subtheme_matrix', {})
    theme_scores = {}
    theme_stages = {}
    subtheme_scores = {}

    stage_weights = {'主升': 10, '升温': 8, '分歧': 5, '潜伏': 3, '退潮': 2, '弱势': 1}

    for parent, subs in matrix.items():
        scores = []
        stages = []
        sub_scores = {}
        for s in subs:
            score = s.get('score', 0)
            stage = s.get('stage', '潜伏')
            name = s.get('name', '')
            scores.append(score)
            stages.append(stage)
            sub_scores[name] = score

        # 母主题分 = 子主题平均分
        theme_scores[parent] = round(sum(scores) / len(scores), 1) if scores else 50.0
        subtheme_scores[parent] = sub_scores

        # 母主题阶段 = 按权重取主导阶段
        weighted = defaultdict(float)
        for s in stages:
            weighted[s] += stage_weights.get(s, 0)
        theme_stages[parent] = max(weighted, key=weighted.get) if weighted else '潜伏'

    return theme_scores, theme_stages, subtheme_scores


class EntryTimingEngine:
    """
    入场时机引擎

    输入:
      subtheme_name: str
      stocks: List[Dict], [{code, name, role, stock_alpha, final_score, leader_similarity}]
      kline_groups: {code: kline_df}
      subtheme_stage: str, 子主题生命周期阶段
      subtheme_signal: str, 子主题信号 (pre_rotate等)
      theme_stage: str, 母主题阶段（综合）
      market_regime: str, 市场状态
      role_results: {code: role_info}
    """

    # 子主题阶段 → 推荐策略映射
    STAGE_STRATEGY = {
        '潜伏': {'primary': 'WATCH', 'secondary': 'PULLBACK BUY', 'avoid': 'BREAKOUT BUY'},
        '升温': {'primary': 'BREAKOUT BUY', 'secondary': 'PRE_ROTATE BUY', 'avoid': 'SELL'},
        '主升': {'primary': 'HOLD', 'secondary': 'PULLBACK BUY', 'avoid': 'SELL'},
        '分歧': {'primary': 'REDUCE', 'secondary': 'WATCH', 'avoid': 'BREAKOUT BUY'},
        '退潮': {'primary': 'SELL', 'secondary': 'REDUCE', 'avoid': 'BREAKOUT BUY'},
        '弱势': {'primary': 'WATCH', 'secondary': 'PULLBACK BUY', 'avoid': 'BREAKOUT BUY'},
    }

    # 角色 → 推荐信号偏好
    ROLE_SIGNAL_BIAS = {
        'Emotion Leader': 'HOLD',
        'Momentum Leader': 'HOLD',
        'Institution Core': 'PULLBACK BUY',
        'Momentum': 'BREAKOUT BUY',
        'Beta': 'BREAKOUT BUY',
        'Follower': 'PRE_ROTATE BUY',
        'Defensive': 'WATCH',
        'Weak': 'WATCH',
        # 兼容旧角色名
        'Leader': 'HOLD',
        'Core': 'PULLBACK BUY',
    }

    def __init__(self, subtheme_name: str, stocks: List[Dict],
                 kline_groups: Dict, subtheme_stage: str = '潜伏',
                 subtheme_signal: str = 'hold', theme_stage: str = '潜伏',
                 market_regime: str = '震荡', role_results: Dict = None,
                 stock_alpha_map: Dict = None):
        self.subtheme_name = subtheme_name
        self.stocks = stocks
        self.kline_groups = kline_groups
        self.subtheme_stage = subtheme_stage
        self.subtheme_signal = subtheme_signal
        self.theme_stage = theme_stage
        self.market_regime = market_regime
        self.role_results = role_results or {}
        self.stock_alpha_map = stock_alpha_map or {}

        self._codes = [s['code'] for s in stocks]

    def _safe_kline(self, code: str) -> Optional[pd.DataFrame]:
        return self.kline_groups.get(code)

    # ═══════════════════════════════════════════════════════════
    # 买点条件检测
    # ═══════════════════════════════════════════════════════════

    def _check_breakout(self, code: str) -> Tuple[bool, str]:
        """检测 BREAKOUT BUY: 放量突破 + 创新高 + 加速度上升"""
        kdf = self._safe_kline(code)
        if kdf is None or len(kdf) < 20:
            return False, 'K线不足'

        closes = kdf['close'].astype(float).values
        highs = kdf['high'].astype(float).values if 'high' in kdf.columns else closes
        vols = kdf['vol'].astype(float).values

        # 1. 创新高：收盘价 > 近20日最高
        high_20 = np.max(highs[-20:-1])
        latest_close = closes[-1]
        is_new_high = latest_close >= high_20 * 0.995  # 允许微小误差

        # 2. 放量：近3日均量 > 近20日均量的1.2倍
        vol_3 = np.mean(vols[-3:])
        vol_20 = np.mean(vols[-20:])
        vol_surge = vol_3 > vol_20 * 1.2 if vol_20 > 0 else False

        # 3. 加速度上升：近3日涨幅 > 近10日涨幅 且 近3日为正
        if len(closes) >= 11:
            ret_3 = (closes[-1] / closes[-4] - 1) * 100
            ret_10 = (closes[-1] / closes[-11] - 1) * 100
            accel_up = ret_3 > 0 and ret_3 > ret_10
        else:
            accel_up = False

        # 4. 突破平台：近5日有阳线突破20日最高
        recent = closes[-5:]
        platform_top = np.max(closes[-20:-5]) if len(closes) >= 25 else np.max(closes[:-5])
        break_platform = any(c >= platform_top * 1.01 for c in recent)

        conditions = [is_new_high, vol_surge, accel_up, break_platform]
        n_met = sum(conditions)
        reasons = []
        if is_new_high: reasons.append('新高')
        if vol_surge: reasons.append('放量')
        if accel_up: reasons.append('加速')
        if break_platform: reasons.append('突破平台')

        if n_met >= 3:
            return True, ';'.join(reasons)
        elif n_met >= 2:
            # 部分满足但不够强
            return False, f'部分满足({n_met}/4)'
        return False, f'条件不足({n_met}/4)'

    def _check_pullback(self, code: str) -> Tuple[bool, str]:
        """检测 PULLBACK BUY: 回踩均线 + 缩量企稳 + 主力未流出"""
        kdf = self._safe_kline(code)
        if kdf is None or len(kdf) < 20:
            return False, 'K线不足'

        closes = kdf['close'].astype(float).values
        vols = kdf['vol'].astype(float).values

        # 1. 处于主升/升温阶段
        if self.subtheme_stage not in ('主升', '升温', '潜伏'):
            return False, f'阶段={self.subtheme_stage}不适合回踩买'

        # 2. 价格在MA5和MA10附近（回踩）
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else ma5
        latest = closes[-1]
        pct_from_ma5 = abs(latest - ma5) / ma5 * 100 if ma5 > 0 else 999
        pct_from_ma10 = abs(latest - ma10) / ma10 * 100 if ma10 > 0 else 999

        near_ma = (pct_from_ma5 <= 3) or (pct_from_ma10 <= 3)
        if not near_ma:
            return False, f'偏离均线(MA5:{pct_from_ma5:.1f}% MA10:{pct_from_ma10:.1f}%)'

        # 3. 缩量：近3日均量 < 近10日均量
        vol_3 = np.mean(vols[-3:])
        vol_10 = np.mean(vols[-10:]) if len(vols) >= 10 else 1
        shrinking = vol_3 < vol_10 * 0.9 if vol_10 > 0 else False

        # 4. 价格企稳：近3日跌幅收窄
        if len(closes) >= 6:
            ret_3 = (closes[-1] / closes[-4] - 1) * 100
            ret_5 = (closes[-1] / closes[-6] - 1) * 100
            stabilizing = ret_3 > ret_5  # 近3日比近5日好
        else:
            stabilizing = False

        # 5. 主力未流出：价格未跌破MA10
        not_broken = latest >= ma10 * 0.97

        conditions = [near_ma, shrinking, stabilizing, not_broken]
        n_met = sum(conditions)
        reasons = []
        if near_ma: reasons.append('回踩均线')
        if shrinking: reasons.append('缩量')
        if stabilizing: reasons.append('企稳')
        if not_broken: reasons.append('未破位')

        if n_met >= 3:
            return True, ';'.join(reasons)
        return False, f'条件不足({n_met}/4)'

    def _check_pre_rotate(self, code: str) -> Tuple[bool, str]:
        """检测 PRE_ROTATE BUY: 子主题轮动预信号"""
        kdf = self._safe_kline(code)
        if kdf is None or len(kdf) < 10:
            return False, 'K线不足'

        rr = self.role_results.get(code, {})
        role = rr.get('role', '')

        # 1. 子主题有 PRE_ROTATE 信号
        has_pre_rotate = 'pre_rotate' in str(self.subtheme_signal).lower()

        # 2. 角色为 Follower 或 Momentum
        role_ok = role in ('Follower', 'Momentum', 'Beta')

        # 3. Leader Similarity 高
        ls = rr.get('leader_similarity', 0)
        ls_high = ls >= 0.5

        # 4. Money Flow 改善
        closes = kdf['close'].astype(float).values
        vols = kdf['vol'].astype(float).values
        if len(closes) >= 6:
            ret_3 = (closes[-1] / closes[-4] - 1) * 100
            ret_5 = (closes[-1] / closes[-6] - 1) * 100
            vol_3 = np.mean(vols[-3:])
            vol_5 = np.mean(vols[-6:-3]) if len(vols) >= 6 else 1
            flow_improving = (ret_3 > ret_5) and (vol_3 > vol_5 * 0.8)
        else:
            flow_improving = False

        conditions = [has_pre_rotate, role_ok, ls_high, flow_improving]
        n_met = sum(conditions)
        reasons = []
        if has_pre_rotate: reasons.append('子主题轮动信号')
        if role_ok: reasons.append(f'角色={role}')
        if ls_high: reasons.append('高Leader相似度')
        if flow_improving: reasons.append('资金改善')

        if n_met >= 2:
            return True, ';'.join(reasons)
        return False, f'条件不足({n_met}/4)'

    def _check_hold(self, code: str) -> Tuple[bool, str]:
        """检测 HOLD: 趋势持续，无卖点"""
        kdf = self._safe_kline(code)
        if kdf is None or len(kdf) < 10:
            return False, 'K线不足'

        rr = self.role_results.get(code, {})
        role = rr.get('role', '')

        # Leader/Core 默认倾向持有
        if role not in ('Leader', 'Core'):
            return False, f'角色={role}非核心持仓'

        closes = kdf['close'].astype(float).values
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else ma5

        # 趋势完好：MA5 > MA10 或价格在MA5上方
        trend_ok = (ma5 > ma10) or (closes[-1] >= ma5 * 0.98)

        if trend_ok and self.subtheme_stage in ('主升', '升温'):
            return True, '趋势完好;核心持仓'
        return False, '趋势偏弱'

    def _check_reduce(self, code: str) -> Tuple[bool, str]:
        """检测 REDUCE: 加速度下降 + 量能衰减"""
        kdf = self._safe_kline(code)
        if kdf is None or len(kdf) < 15:
            return False, 'K线不足'

        closes = kdf['close'].astype(float).values
        vols = kdf['vol'].astype(float).values

        # 1. 加速度下降
        if len(closes) >= 11:
            accel_3 = (closes[-1] / closes[-4] - 1) * 100  # 近3日
            accel_7 = (closes[-1] / closes[-8] - 1) * 100  # 近7日
            accel_decline = accel_3 < accel_7 * 0.5
        else:
            accel_decline = False

        # 2. 量能衰减
        vol_3 = np.mean(vols[-3:])
        vol_10 = np.mean(vols[-10:]) if len(vols) >= 10 else 1
        vol_decline = vol_3 < vol_10 * 0.7

        # 3. 阶段在分歧/退潮
        stage_decline = self.subtheme_stage in ('分歧', '退潮')

        conditions = [accel_decline, vol_decline, stage_decline]
        n_met = sum(conditions)
        reasons = []
        if accel_decline: reasons.append('加速度下降')
        if vol_decline: reasons.append('量能衰减')
        if stage_decline: reasons.append(f'阶段={self.subtheme_stage}')

        if n_met >= 2:
            return True, ';'.join(reasons)
        return False, f'条件不足({n_met}/3)'

    def _check_sell(self, code: str) -> Tuple[bool, str]:
        """检测 SELL: 趋势破坏 + 主力流出 + 跌破关键均线"""
        kdf = self._safe_kline(code)
        if kdf is None or len(kdf) < 20:
            return False, 'K线不足'

        closes = kdf['close'].astype(float).values
        vols = kdf['vol'].astype(float).values
        latest = closes[-1]

        # 1. 跌破关键均线（MA20）
        ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else 1
        broken_ma20 = latest < ma20 * 0.95

        # 2. 主力持续流出（缩量下跌 + 价格新低）
        if len(closes) >= 10:
            low_5 = np.min(closes[-5:])
            low_10 = np.min(closes[-10:-5]) if len(closes) >= 15 else low_5
            new_low = low_5 < low_10
        else:
            new_low = False

        # 3. 趋势破坏：MA5 < MA10 且价格在MA5下方
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else ma5
        trend_broken = (ma5 < ma10 * 0.98) and (latest < ma5 * 0.98)

        # 4. 阶段在退潮/弱势
        stage_bad = self.subtheme_stage in ('退潮', '弱势')

        conditions = [broken_ma20, new_low, trend_broken, stage_bad]
        n_met = sum(conditions)
        reasons = []
        if broken_ma20: reasons.append('跌破MA20')
        if new_low: reasons.append('新低')
        if trend_broken: reasons.append('趋势破坏')
        if stage_bad: reasons.append(f'阶段={self.subtheme_stage}')

        if n_met >= 2:
            return True, ';'.join(reasons)
        return False, f'条件不足({n_met}/4)'

    # ═══════════════════════════════════════════════════════════
    # 综合判定
    # ═══════════════════════════════════════════════════════════

    def _calc_entry_score(self, signal: str, code: str) -> int:
        """
        计算入场分 (0-100)

        公式：信号基础分 + Alpha×0.25 + Confidence微调
        - BREAKOUT BUY: 70 + alpha×0.25 + (conf-0.5)×10  → alpha=100 → 95
        - PULLBACK BUY: 65 + alpha×0.25 + (conf-0.5)×10  → alpha=100 → 90
        - PRE_ROTATE BUY: 60 + alpha×0.25 → alpha=100 → 85
        - HOLD: 50 + alpha×0.15 → alpha=100 → 65
        """
        base = SIGNAL_PRIORITY.get(signal, 35)
        rr = self.role_results.get(code, {})
        alpha = self.stock_alpha_map.get(code, 50)
        confidence = rr.get('confidence', 0.5)

        # Alpha 作为核心输入：alpha=0→0, alpha=100→25
        alpha_score = alpha * 0.25

        # Confidence 微调（±5）
        conf_adj = (confidence - 0.5) * 10

        score = min(100, max(0, base + alpha_score + conf_adj))
        return int(round(score))

    def _calc_risk_level(self, signal: str, code: str) -> str:
        """计算风险等级"""
        if signal in ('SELL', 'REDUCE'):
            return 'high'
        if signal in ('BREAKOUT BUY',):
            # 突破买点风险中等（追高风险）
            return 'medium'
        if signal in ('PULLBACK BUY', 'PRE_ROTATE BUY'):
            # 回踩/轮动买点风险较低
            return 'low'
        if signal == 'HOLD':
            return 'low'
        return 'medium'

    def _calc_holding_priority(self, signal: str, code: str) -> int:
        """计算持有优先级 (1-5)"""
        score = self._calc_entry_score(signal, code)
        if score >= 85:
            return 5
        elif score >= 70:
            return 4
        elif score >= 55:
            return 3
        elif score >= 35:
            return 2
        else:
            return 1

    def evaluate(self, code: str) -> Dict[str, Any]:
        """
        对一只股票进行全面入场评估

        输出:
          {entry_signal, entry_score, entry_reason, risk_level, holding_priority}
        """
        rr = self.role_results.get(code, {})
        role = rr.get('role', '')
        alpha = self.stock_alpha_map.get(code, 50)

        # 按优先级顺序检测信号（从最积极到最消极）
        # 用check函数逐一检测，命中即返回
        checks = [
            ('BREAKOUT BUY', self._check_breakout),
            ('PULLBACK BUY', self._check_pullback),
            ('PRE_ROTATE BUY', self._check_pre_rotate),
            ('HOLD', self._check_hold),
            ('REDUCE', self._check_reduce),
            ('SELL', self._check_sell),
        ]

        best_signal = 'WATCH'
        best_reason = '无明确信号'
        best_score = 0

        for signal_name, check_func in checks:
            ok, reason = check_func(code)
            score = self._calc_entry_score(signal_name, code)
            # 取最积极的买入信号（优先买入）
            if ok and score > best_score:
                best_signal = signal_name
                best_reason = reason
                best_score = score

        # 如果没有任何信号命中但 Stock Alpha 很高，给 WATCH+
        if best_signal == 'WATCH' and alpha >= 70:
            best_reason = f'Alpha高分({alpha:.0f});等待买点'
            best_score = 50

        entry_score = max(best_score, self._calc_entry_score(best_signal, code))
        risk_level = self._calc_risk_level(best_signal, code)
        holding_priority = self._calc_holding_priority(best_signal, code)

        return {
            'entry_signal': best_signal,
            'entry_score': entry_score,
            'entry_reason': best_reason,
            'risk_level': risk_level,
            'holding_priority': holding_priority,
        }

    def run_all(self) -> Dict[str, Any]:
        """运行完整入场时机评估"""
        result = {}
        for s in self.stocks:
            code = s['code']
            result[code] = self.evaluate(code)
        return result


# ═══════════════════════════════════════════════════════════
# 市场状态判断
# ═══════════════════════════════════════════════════════════

def detect_market_regime(trade_date: str) -> str:
    """
    检测市场状态

    输出: '牛市' | '震荡' | '熊市'
    """
    try:
        kdf = theme_ts.get_daily_kline(['000300.SH'], '20260101', trade_date)
        if kdf is None or kdf.empty:
            return '震荡'
        closes = kdf['close'].astype(float).values
        if len(closes) < 20:
            return '震荡'

        ret_20 = (closes[-1] / closes[-20] - 1) * 100
        ret_60 = (closes[-1] / closes[-60] - 1) * 100 if len(closes) >= 60 else ret_20

        if ret_60 > 10 and ret_20 > 3:
            return '牛市'
        elif ret_60 < -10 and ret_20 < -3:
            return '熊市'
        else:
            return '震荡'
    except Exception:
        return '震荡'


def calc_theme_stage(subtheme_stages: List[str]) -> str:
    """
    从子主题阶段综合计算母主题阶段

    取出现频率最高的阶段，加权 by severity
    """
    if not subtheme_stages:
        return '潜伏'

    stage_weights = {
        '主升': 10, '升温': 8, '分歧': 5,
        '潜伏': 3, '退潮': 2, '弱势': 1,
    }
    weighted = defaultdict(float)
    for s in subtheme_stages:
        weighted[s] += stage_weights.get(s, 1)

    return max(weighted, key=weighted.get)


# ═══════════════════════════════════════════════════════════
# 便利函数
# ═══════════════════════════════════════════════════════════

def build_subtheme_stage_map(subtheme_report: Dict) -> Dict[str, Dict]:
    """
    从 subtheme_report 提取子主题阶段和信号

    输出: {母主题: {子主题: {stage, signal, pre_rotate}}}
    """
    matrix = subtheme_report.get('subtheme_matrix', {})
    result = {}
    for parent, subs in matrix.items():
        result[parent] = {}
        for s in subs:
            name = s.get('name', '')
            result[parent][name] = {
                'stage': s.get('stage', '潜伏'),
                'signal': s.get('signal', 'hold'),
                'pre_rotate': s.get('pre_rotate', False),
            }
    return result


def run_entry_timing_for_all(
    stocks_output: Dict[str, Dict],
    subtheme_report: Dict,
    role_results: Dict[str, Dict],
    kline_groups: Dict[str, pd.DataFrame],
    stock_alpha_map: Dict[str, float] = None,
    daily_basic: Dict = None,
) -> Dict[str, Dict]:
    """
    为所有子主题运行 Entry Timing Engine

    输出: {母主题: {子主题: {code: {entry_signal, entry_score, ...}}}}
    """
    # 构建子主题股票索引
    subtheme_stock_index = defaultdict(lambda: defaultdict(list))
    for code, info in stocks_output.items():
        st = info.get('subtheme', '')
        if not st:
            continue
        themes = info.get('themes', [])
        parent = themes[0] if themes else ''
        role = role_results.get(code, {}).get('role', '')
        alpha = stock_alpha_map.get(code, 50) if stock_alpha_map else info.get('stock_alpha', 50)
        if parent and st:
            subtheme_stock_index[parent][st].append({
                'code': code,
                'name': info.get('name', ''),
                'role': role,
                'stock_alpha': alpha,
                'final_score': info.get('final_score', 50),
                'leader_similarity': role_results.get(code, {}).get('leader_similarity', 0),
            })

    # 提取子主题阶段
    stage_map = build_subtheme_stage_map(subtheme_report)

    # 检测市场状态
    trade_date = subtheme_report.get('report_metadata', {}).get('trade_date', '')
    market_regime = detect_market_regime(trade_date) if trade_date else '震荡'
    print(f"  [EntryTiming] 市场状态: {market_regime}")

    all_results = {}
    total_stocks = 0

    for parent, subs in subtheme_stock_index.items():
        all_results[parent] = {}
        parent_stages = []
        parent_rotation = {}

        for sub_name, stocks in subs.items():
            if not stocks:
                continue

            sub_info = stage_map.get(parent, {}).get(sub_name, {})
            stage = sub_info.get('stage', '潜伏')
            signal = sub_info.get('signal', 'hold')
            pre_rotate = sub_info.get('pre_rotate', False)
            parent_stages.append(stage)

            # 子主题信号中携带 pre_rotate
            sub_signal = 'pre_rotate' if pre_rotate else signal

            engine = EntryTimingEngine(
                subtheme_name=sub_name,
                stocks=stocks,
                kline_groups=kline_groups,
                subtheme_stage=stage,
                subtheme_signal=sub_signal,
                theme_stage='',  # 后续填充
                market_regime=market_regime,
                role_results=role_results,
                stock_alpha_map=stock_alpha_map,
            )
            result = engine.run_all()
            all_results[parent][sub_name] = result
            total_stocks += len(stocks)

        # 计算母主题阶段
        parent_theme_stage = calc_theme_stage(parent_stages)

        # 回填 theme_stage 到每个子主题的结果（其实只是元数据）
        for sub_name in subs:
            if sub_name in all_results[parent]:
                all_results[parent][sub_name] = {
                    'stocks': all_results[parent][sub_name],
                    'subtheme_stage': stage_map.get(parent, {}).get(sub_name, {}).get('stage', ''),
                    'subtheme_signal': sub_signal,
                    'theme_stage': parent_theme_stage,
                    'market_regime': market_regime,
                }

    print(f"  [EntryTiming] 完成: {total_stocks} 只股票入场时机评估")

    # ── 应用三道门过滤 ──
    theme_scores, theme_stages, subtheme_scores = _compute_theme_scores_from_report(subtheme_report)
    gate = ThreeGateFilter(
        theme_scores=theme_scores,
        theme_stages=theme_stages,
        subtheme_scores=subtheme_scores,
        stocks_output=stocks_output,
    )
    gate.apply(all_results)

    # ── 双评分：为每只股票计算 Trade Score + Investment Score ──
    trade_count = 0
    inv_count = 0
    for parent, subs in all_results.items():
        p_stage = theme_stages.get(parent, '')
        for sub_name, sub_data in subs.items():
            stocks_data = sub_data.get('stocks', {})
            sub_stage = sub_data.get('subtheme_stage', '')
            for code, entry in stocks_data.items():
                sa = stock_alpha_map.get(code, 50) if stock_alpha_map else 50
                fs = stocks_output.get(code, {}).get('final_score', 50) or 50
                # Trade Score = 0.70 × Final + 0.30 × Entry
                es = entry.get('entry_score', 0) or 0
                entry['trade_score'] = compute_trade_score(fs, es)
                trade_count += 1
                # Investment Score
                rr = role_results.get(code, {})
                entry['investment_score'] = compute_investment_score(
                    code, sa, rr.get('role', ''),
                    role_results, kline_groups,
                    p_stage, daily_basic,
                )
                inv_count += 1
    print(f"  [DualScore] Trade Score: {trade_count} 只, Investment Score: {inv_count} 只")

    return all_results


def print_entry_timing_report(all_results: Dict[str, Dict], top_n: int = 10,
                              stock_alpha_map: Dict[str, float] = None):
    """打印入场时机报告"""
    flat = []
    for parent, subs in all_results.items():
        for sub_name, data in subs.items():
            stocks_data = data.get('stocks', {})
            stage = data.get('subtheme_stage', '')
            theme_st = data.get('theme_stage', '')
            for code, entry in stocks_data.items():
                sa = (stock_alpha_map.get(code, 50) if stock_alpha_map
                      else entry.get('stock_alpha', 50))
                flat.append({
                    'parent': parent,
                    'subtheme': sub_name,
                    'code': code,
                    'subtheme_stage': stage,
                    'theme_stage': theme_st,
                    'stock_alpha': sa,
                    **entry,
                })

    # 按 Trade Score 排序（用于实盘推荐）
    for item in flat:
        item['_sort_key'] = item.get('trade_score', item.get('entry_score', 0))
    flat.sort(key=lambda x: -x['_sort_key'])

    print(f"\n  [EntryTiming] 入场优先级 Top {min(top_n, len(flat))} (按 Trade Score):")
    header = (f"  {'代码':<10} {'名称':<8} {'信号':<16} {'E':<5} "
              f"{'T':<5} {'I':<5} {'α':<5} {'风险':<6} {'星级':<4}")
    print(header)
    print(f"  {'─'*len(header)}")
    for item in flat[:top_n]:
        code = item['code']
        name = ''
        if hasattr(print_entry_timing_report, '_name_map'):
            name = print_entry_timing_report._name_map.get(code, code)
        t_score = item.get('trade_score', 0)
        i_score = item.get('investment_score', 0)
        print(f"  {code:<10} {name:<8} "
              f"{item['entry_signal']:<16} {item['entry_score']:<5} "
              f"{t_score:<5} {i_score:<5} {item.get('stock_alpha',50):<5.0f} "
              f"{item['risk_level']:<6} {'★'*item['holding_priority']:<4}")

    return flat


def print_subtheme_report(all_results: Dict[str, Dict], name_map: Dict = None):
    """打印子主题级别入场时机报告（类似用户要求的格式）"""
    print(f"\n  [EntryTiming] 子主题入场策略报告:")
    print(f"  {'─'*60}")

    for parent, subs in all_results.items():
        for sub_name, data in subs.items():
            stocks_data = data.get('stocks', {})
            stage = data.get('subtheme_stage', '')
            theme_st = data.get('theme_stage', '')

            if not stocks_data:
                continue

            # 按入场分排序
            sorted_codes = sorted(stocks_data.keys(),
                                  key=lambda c: stocks_data[c]['entry_score'], reverse=True)

            print(f"\n  Sub-theme: {sub_name}")
            print(f"  主题: {parent}")
            print(f"  生命周期: {stage}")
            print(f"  市场状态: {data.get('market_regime', '')}")

            # 推荐策略: 找该子主题中买入信号的股票
            buy_signals = [c for c in sorted_codes
                           if stocks_data[c]['entry_signal'] in
                           ('BREAKOUT BUY', 'PULLBACK BUY', 'PRE_ROTATE BUY')]
            hold_signals = [c for c in sorted_codes
                            if stocks_data[c]['entry_signal'] == 'HOLD']

            if buy_signals:
                strategy_roles = set()
                for c in buy_signals[:5]:
                    stk = stocks_data[c]
                    print(f"  >> {stk['entry_signal']} - {c} ({stk['entry_score']}分) "
                          f"{'★'*stk['holding_priority']} 风险:{stk['risk_level']}")
            elif hold_signals:
                for c in hold_signals[:3]:
                    stk = stocks_data[c]
                    print(f"  >> HOLD - {c} ({stk['entry_score']}分) "
                          f"{'★'*stk['holding_priority']}")
            else:
                print(f"  >> 当前无推荐买点")

            print(f"  {'─'*40}")


if __name__ == '__main__':
    print("Stock Alpha Engine V4.3 - Entry Timing Engine")
    print("=" * 60)

    # 从缓存加载
    cache_dir = os.path.join(parent_dir, "cache_daily")
    json_file = os.path.join(cache_dir, "theme_stock_map_v2_20260724.json")
    if not os.path.exists(json_file):
        cache_dir = os.path.join(BASE_DIR, "cache_daily")
        json_file = os.path.join(cache_dir, "theme_stock_map_v2_20260724.json")
    if not os.path.exists(json_file):
        print(f"[错误] 未找到数据文件: {json_file}")
        sys.exit(1)

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    stocks_output = data.get('stocks', {})
    role_results = data.get('role_evolution', {})
    subtheme_report = data.get('subtheme_report', {})

    # 加载K线
    trade_date = data.get('trade_date', '20260724')
    dt = datetime.strptime(str(trade_date), "%Y%m%d")
    start = (dt - timedelta(days=90)).strftime("%Y%m%d")
    end = str(trade_date)

    codes = list(role_results.keys())
    print(f"加载 {len(codes)} 只股票K线...")
    kline_df = theme_ts.get_daily_kline(codes, start, end)

    kline_groups = {}
    if kline_df is not None and not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub.sort_values("trade_date")
    print(f"K线加载完成: {len(kline_groups)} 只")

    # 构建 stock_alpha_map
    stock_alpha_map = {}
    for code, info in stocks_output.items():
        sa = info.get('stock_alpha')
        if sa is not None:
            stock_alpha_map[code] = sa

    results = run_entry_timing_for_all(
        stocks_output, subtheme_report, role_results,
        kline_groups, stock_alpha_map
    )

    print_entry_timing_report(results, stock_alpha_map=stock_alpha_map)
    print_subtheme_report(results)
