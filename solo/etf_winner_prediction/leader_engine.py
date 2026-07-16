#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 4: Leader Engine 龙头引擎
==================================
每只ETF必须通过成份股龙头验证。

识别5种龙头:
  - Leader (核心龙头)
  - Core Leader (核心龙头)
  - Trend Leader (趋势龙头)
  - Second Leader (次龙头)
  - Institutional Leader (机构龙头)

对每个龙头计算:
  - Relative Strength
  - Trend
  - Breakout
  - Market Share (市占率)
  - Institutional Holding (机构持仓)
  - Northbound Holding (北向持仓)
  - Leadership Persistence (龙头持续性)
  - Leader Breadth (龙头宽度)

硬过滤器: LeaderScore >= 75
ETF无龙头：强惩罚
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import (
    ema, breakout_pct, consecutive_up_days,
)


@dataclass
class LeaderInfo:
    """单个龙头信息"""
    code: str = ""
    role: str = ""
    rs_score: float = 0.0
    trend_score: float = 0.0
    breakout_score: float = 0.0
    market_share: float = 0.0
    persistence: float = 0.0


@dataclass
class LeaderResult:
    """龙头引擎结果"""
    etf_code: str = ""
    leader_score: float = 0.0
    core_leader: str = ""
    second_leader: str = ""
    trend_leader: str = ""
    institutional_leader: str = ""
    leaders: list = field(default_factory=list)
    # 子维度
    relative_strength: float = 0.0
    trend: float = 0.0
    breakout: float = 0.0
    market_share: float = 0.0
    institutional_holding: float = 0.0
    northbound_holding: float = 0.0
    leader_persistence: float = 0.0
    leader_breadth: float = 0.0
    # 健康度
    leader_health: float = 0.0
    failure_risk: float = 0.0
    # 惩罚
    penalty: float = 0.0
    reasons: list = field(default_factory=list)


class LeaderEngine:
    """龙头引擎 - Step 4"""

    def __init__(self, config: dict):
        self.cfg = config.get("leader_engine", {})
        self.w_rs = self.cfg.get("relative_strength_weight", 0.20)
        self.w_trend = self.cfg.get("trend_weight", 0.15)
        self.w_breakout = self.cfg.get("breakout_weight", 0.10)
        self.w_market = self.cfg.get("market_share_weight", 0.10)
        self.w_inst = self.cfg.get("institutional_holding_weight", 0.15)
        self.w_north = self.cfg.get("northbound_holding_weight", 0.10)
        self.w_persist = self.cfg.get("persistence_weight", 0.10)
        self.w_breadth = self.cfg.get("leader_breadth_weight", 0.10)
        self.leader_count = self.cfg.get("leader_count", 5)
        self.ma_period = self.cfg.get("ma_period", 20)
        self.breakout_period = self.cfg.get("breakout_period", 60)
        self.no_leader_penalty = self.cfg.get("no_leader_penalty", 30)
        self.min_leader_score = self.cfg.get("min_leader_score", 75)

    def score(self, etf_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]],
              stock_data: Dict[str, pd.DataFrame],
              top_df: Optional[pd.DataFrame] = None,
              top_inst: Optional[pd.DataFrame] = None,
              moneyflow: Optional[pd.DataFrame] = None) -> Dict[str, LeaderResult]:
        """对所有ETF做龙头确认"""
        results = {}
        for etf_code, df in etf_data.items():
            cons = constituents.get(etf_code, [])
            if not cons:
                r = LeaderResult(etf_code=etf_code, penalty=self.no_leader_penalty)
                r.leader_score = float(np.clip(50.0 - self.no_leader_penalty, 0, 100))
                r.reasons = ["无成份股数据(强惩罚)"]
                results[etf_code] = r
                continue
            r = self._score_one(etf_code, cons, stock_data, top_df, top_inst, moneyflow)
            results[etf_code] = r
        return results

    def _score_one(self, etf_code, constituents, stock_data,
                   top_df, top_inst, moneyflow) -> LeaderResult:
        r = LeaderResult(etf_code=etf_code)

        leaders = self._identify_leaders(constituents, stock_data, top_df)
        if not leaders:
            r.penalty = self.no_leader_penalty
            r.leader_score = float(np.clip(50.0 - self.no_leader_penalty, 0, 100))
            r.reasons = ["无强龙头(惩罚)"]
            return r

        # 角色标记
        if len(leaders) >= 1:
            r.core_leader = leaders[0].code
        if len(leaders) >= 2:
            r.second_leader = leaders[1].code
        if len(leaders) >= 3:
            r.trend_leader = leaders[2].code
        if len(leaders) >= 4:
            r.institutional_leader = leaders[3].code
        r.leaders = [l.code for l in leaders]

        # 子维度
        r.trend = self._score_trend(leaders, stock_data)
        r.breakout = self._score_breakout(leaders, stock_data)
        r.leader_breadth = self._score_breadth(constituents, stock_data)
        r.relative_strength = float(np.mean([l.rs_score for l in leaders]))
        r.market_share = self._score_market_share(leaders, constituents, stock_data)
        r.institutional_holding = self._score_inst_holding(leaders, top_df, top_inst)
        r.northbound_holding = self._score_north_holding(leaders, moneyflow)
        r.leader_persistence = self._score_persistence(leaders, stock_data)

        # 加权
        final = (
            r.relative_strength * self.w_rs +
            r.trend * self.w_trend +
            r.breakout * self.w_breakout +
            r.market_share * self.w_market +
            r.institutional_holding * self.w_inst +
            r.northbound_holding * self.w_north +
            r.leader_persistence * self.w_persist +
            r.leader_breadth * self.w_breadth
        )
        r.leader_score = float(np.clip(final, 0, 100))

        # 健康度
        r.leader_health = self._calc_health(leaders, stock_data)

        # 失败风险
        r.failure_risk = self._calc_failure_risk(leaders, stock_data)

        r.reasons = self._build_reasons(r, leaders)
        return r

    def _identify_leaders(self, constituents, stock_data, top_df) -> List[LeaderInfo]:
        top_set = set()
        if top_df is not None and not top_df.empty and "ts_code" in top_df.columns:
            top_set = set(top_df["ts_code"].tolist())

        scores = []
        for code in constituents:
            sd = stock_data.get(code)
            if sd is None or sd.empty or len(sd) < 20:
                continue
            sd = sd.sort_values("trade_date")
            c = sd["close"].values.astype(float)
            a = sd["amount"].values.astype(float) if "amount" in sd.columns else np.zeros_like(c)

            # RS
            r5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
            r10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
            r20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0
            rs = float(np.clip((r5 * 0.4 + r10 * 0.35 + r20 * 0.25) * 300 + 40, 0, 100))

            # 趋势
            ma5 = float(np.mean(c[-5:]))
            ma10 = float(np.mean(c[-10:]))
            ma20 = float(np.mean(c[-20:]))
            trend = 40.0
            if c[-1] > ma5 > ma10 > ma20:
                trend = 100.0
            elif c[-1] > ma10 > ma20:
                trend = 75.0
            elif c[-1] > ma20:
                trend = 60.0

            # 突破
            h = sd["high"].values.astype(float) if "high" in sd.columns else c
            br = float(np.clip(100 + breakout_pct(c, h, self.breakout_period) * 5, 0, 100))

            # 成交额
            avg_amt = float(np.mean(a[-10:]) / 1e5) if len(a) >= 10 else 0
            amt_s = float(np.clip(avg_amt * 5, 0, 100))

            # 龙虎榜加分
            top_bonus = 15.0 if code in top_set else 0.0

            total = rs * 0.35 + trend * 0.25 + br * 0.15 + amt_s * 0.15 + top_bonus

            # 市场占有率
            market_share = 0.0
            if "amount" in sd.columns and len(sd) >= 10:
                market_share = float(np.mean(sd["amount"].iloc[-10:]) / 1e5)

            # 持续性
            p = sd["pct_chg"].values.astype(float) if "pct_chg" in sd.columns else np.zeros(len(c))
            persist = float(np.clip(consecutive_up_days(p) * 12, 0, 100))

            scores.append((code, total, rs, trend, br, market_share, persist))

        if not scores:
            return []

        scores.sort(key=lambda x: x[1], reverse=True)
        leaders = []
        roles = ["core", "second", "trend", "institutional", "leader"]
        for i, (code, total, rs, trend, br, ms, persist) in enumerate(scores[:self.leader_count]):
            role = roles[i] if i < len(roles) else "leader"
            leaders.append(LeaderInfo(code=code, role=role, rs_score=rs,
                                       trend_score=trend, breakout_score=br,
                                       market_share=ms, persistence=persist))
        return leaders

    def _score_trend(self, leaders, stock_data) -> float:
        scores = []
        for l in leaders:
            sd = stock_data.get(l.code)
            if sd is None or sd.empty or len(sd) < self.ma_period:
                continue
            c = sd["close"].values.astype(float)
            ma5 = float(np.mean(c[-5:]))
            ma10 = float(np.mean(c[-10:]))
            ma20 = float(np.mean(c[-20:]))
            if c[-1] > ma5 > ma10 > ma20:
                scores.append(100.0)
            elif c[-1] > ma10 > ma20:
                scores.append(75.0)
            elif c[-1] > ma20:
                scores.append(60.0)
            elif c[-1] < ma20:
                scores.append(20.0)
            else:
                scores.append(40.0)
        return float(np.mean(scores)) if scores else 50.0

    def _score_breakout(self, leaders, stock_data) -> float:
        scores = []
        for l in leaders:
            sd = stock_data.get(l.code)
            if sd is None or sd.empty or len(sd) < self.breakout_period:
                continue
            c = sd["close"].values.astype(float)
            h = sd["high"].values.astype(float) if "high" in sd.columns else c
            pct = breakout_pct(c, h, self.breakout_period)
            scores.append(float(np.clip(100 + pct * 5, 0, 100)))
        return float(np.mean(scores)) if scores else 50.0

    def _score_breadth(self, constituents, stock_data) -> float:
        above_ma20 = 0
        total = 0
        for code in constituents:
            sd = stock_data.get(code)
            if sd is None or sd.empty or len(sd) < self.ma_period:
                continue
            c = sd["close"].values.astype(float)
            ma20 = float(np.mean(c[-20:]))
            total += 1
            if c[-1] > ma20:
                above_ma20 += 1
        if total == 0:
            return 50.0
        return float(above_ma20 / total * 100)

    def _score_market_share(self, leaders, constituents, stock_data) -> float:
        if not leaders:
            return 50.0
        leader_codes = set(l.code for l in leaders)
        leader_amt = 0.0
        total_amt = 0.0
        for code in constituents:
            sd = stock_data.get(code)
            if sd is None or sd.empty or "amount" not in sd.columns:
                continue
            amt = float(sd["amount"].iloc[-1])
            total_amt += amt
            if code in leader_codes:
                leader_amt += amt
        if total_amt <= 0:
            return 50.0
        ratio = leader_amt / total_amt
        if 0.2 <= ratio <= 0.5:
            return 80.0
        elif ratio > 0.5:
            return 60.0
        elif ratio > 0.1:
            return 60.0
        return 40.0

    def _score_inst_holding(self, leaders, top_df, top_inst) -> float:
        s = 50.0
        leader_codes = set(l.code for l in leaders)
        if top_df is not None and not top_df.empty and "ts_code" in top_df.columns:
            cnt = len(leader_codes & set(top_df["ts_code"].tolist()))
            if cnt >= 2:
                s += 25
            elif cnt >= 1:
                s += 12
        if top_inst is not None and not top_inst.empty and "ts_code" in top_inst.columns:
            cnt = len(leader_codes & set(top_inst["ts_code"].tolist()))
            if cnt >= 1:
                s += 20
        return float(np.clip(s, 0, 100))

    def _score_north_holding(self, leaders, moneyflow) -> float:
        s = 50.0
        if moneyflow is None or moneyflow.empty:
            return s
        leader_codes = set(l.code for l in leaders)
        mf = moneyflow[moneyflow["ts_code"].isin(leader_codes)]
        if mf.empty:
            return s
        buy_cols = [c for c in ["buy_elg_amount", "buy_elg_amounts"] if c in mf.columns]
        sell_cols = [c for c in ["sell_elg_amount", "sell_elg_amounts"] if c in mf.columns]
        if buy_cols and sell_cols:
            buy = float(mf[buy_cols].sum().sum())
            sell = float(mf[sell_cols].sum().sum())
            if buy + sell > 0:
                net = (buy - sell) / (buy + sell)
                s += float(np.clip(net * 40, -25, 25))
        return float(np.clip(s, 0, 100))

    def _score_persistence(self, leaders, stock_data) -> float:
        scores = []
        for l in leaders:
            sd = stock_data.get(l.code)
            if sd is None or sd.empty or "pct_chg" not in sd.columns:
                continue
            p = sd["pct_chg"].values.astype(float)
            scores.append(float(np.clip(consecutive_up_days(p) * 12, 0, 100)))
        return float(np.mean(scores)) if scores else 50.0

    def _calc_health(self, leaders, stock_data) -> float:
        """龙头健康度 = 趋势+RS+持续性综合"""
        if not leaders:
            return 0.0
        health = 0.0
        for l in leaders:
            health += l.trend_score * 0.4 + l.rs_score * 0.3 + l.persistence * 0.3
        return float(np.clip(health / len(leaders), 0, 100))

    def _calc_failure_risk(self, leaders, stock_data) -> float:
        """龙头失败风险 = 1 - 健康度/100"""
        health = self._calc_health(leaders, stock_data)
        return float(np.clip(100 - health, 0, 100))

    def _build_reasons(self, r: LeaderResult, leaders) -> list:
        parts = []
        if r.core_leader:
            parts.append(f"核心龙头={r.core_leader}")
        if r.trend >= 70:
            parts.append("龙头趋势强")
        if r.breakout >= 70:
            parts.append("龙头突破高点")
        if r.leader_breadth >= 60:
            parts.append(f"宽度扩散({r.leader_breadth:.0f}%)")
        if r.institutional_holding >= 65:
            parts.append("机构持仓")
        if r.northbound_holding >= 65:
            parts.append("北向/主力持仓")
        if r.relative_strength >= 70:
            parts.append("龙头RS强")
        if r.leader_health >= 70:
            parts.append("龙头健康")
        if r.penalty > 0:
            parts.append(f"惩罚-{r.penalty}")
        return parts or ["中性"]