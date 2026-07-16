#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module 5: Leader Confirmation Engine 龙头确认引擎
================================================
Every ETF must be validated by constituent leaders.

For each ETF identify:
  - Leader (core leader / second leader / trend leader)

Calculate:
  - Leader Trend
  - Leader Breakout
  - Leader Breadth
  - Institutional Buying
  - Northbound Buying
  - Relative Strength
  - Industry Dominance
  - Leader Persistence

Output:
  - Leader Score (0-100)

ETF without strong leaders receives penalties.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from etf_alpha_engine.indicators import (
    ema, sma, slope, breakout_pct, new_high_count,
    relative_strength, consecutive_up_days, above_ema_days,
    volume_ratio, normalize, percentile_rank, winsorize,
)


@dataclass
class LeaderInfo:
    """单个龙头信息"""
    code: str = ""
    role: str = ""           # core / second / trend
    rs_score: float = 0.0
    trend_score: float = 0.0
    breakout_score: float = 0.0


@dataclass
class LeaderConfirmResult:
    """龙头确认结果"""
    etf_code: str = ""
    leader_score: float = 0.0
    # 龙头列表
    core_leader: str = ""
    second_leader: str = ""
    trend_leader: str = ""
    leaders: list = field(default_factory=list)
    # 子维度
    leader_trend: float = 0.0
    leader_breakout: float = 0.0
    leader_breadth: float = 0.0
    institution_buying: float = 0.0
    northbound_buying: float = 0.0
    relative_strength: float = 0.0
    industry_dominance: float = 0.0
    leader_persistence: float = 0.0
    # 惩罚
    penalty: float = 0.0
    reasons: list = field(default_factory=list)


class LeaderConfirmEngine:
    """龙头确认引擎

    独立可运行，输出每只ETF的龙头确认分数。
    所有子维度独立计算、可复用、可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("leader_confirm", {})
        self.w_trend = self.cfg.get("leader_trend_weight", 0.20)
        self.w_breakout = self.cfg.get("leader_breakout_weight", 0.15)
        self.w_breadth = self.cfg.get("leader_breadth_weight", 0.15)
        self.w_inst = self.cfg.get("institution_buying_weight", 0.15)
        self.w_north = self.cfg.get("northbound_buying_weight", 0.10)
        self.w_rs = self.cfg.get("relative_strength_weight", 0.15)
        self.w_dom = self.cfg.get("industry_dominance_weight", 0.05)
        self.w_persist = self.cfg.get("leader_persistence_weight", 0.05)
        self.leader_count = self.cfg.get("leader_count", 3)
        self.ma_period = self.cfg.get("ma_period", 20)
        self.breakout_period = self.cfg.get("breakout_period", 60)
        self.relative_period = self.cfg.get("relative_period", 60)
        self.no_leader_penalty = self.cfg.get("no_leader_penalty", 15)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score(self,
              etf_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]],
              stock_data: Dict[str, pd.DataFrame],
              etf_close: Dict[str, np.ndarray] = None,
              top_df: Optional[pd.DataFrame] = None,
              top_inst: Optional[pd.DataFrame] = None,
              moneyflow: Optional[pd.DataFrame] = None,
              ) -> Dict[str, LeaderConfirmResult]:
        """对所有ETF做龙头确认"""
        results = {}
        for etf_code, df in etf_data.items():
            cons = constituents.get(etf_code, [])
            if not cons:
                # 无成份股 -> 惩罚
                r = LeaderConfirmResult(etf_code=etf_code, penalty=self.no_leader_penalty)
                r.leader_score = 50.0 - self.no_leader_penalty
                r.reasons = ["无成份股数据"]
                results[etf_code] = r
                continue
            ec = etf_close.get(etf_code) if etf_close else None
            r = self._score_one(etf_code, df, cons, stock_data, ec, top_df, top_inst, moneyflow)
            results[etf_code] = r
        return results

    # ------------------------------------------------------------------
    # 单ETF龙头确认
    # ------------------------------------------------------------------
    def _score_one(self, etf_code, etf_df, constituents, stock_data,
                   etf_close_arr, top_df, top_inst, moneyflow) -> LeaderConfirmResult:
        r = LeaderConfirmResult(etf_code=etf_code)

        # 识别龙头（前N名）
        leaders = self._identify_leaders(constituents, stock_data, etf_close_arr, top_df)
        if not leaders:
            r.penalty = self.no_leader_penalty
            r.leader_score = float(np.clip(50.0 - self.no_leader_penalty, 0, 100))
            r.reasons = ["无强龙头(惩罚)"]
            return r

        # 标记龙头角色
        if len(leaders) >= 1:
            r.core_leader = leaders[0].code
        if len(leaders) >= 2:
            r.second_leader = leaders[1].code
        if len(leaders) >= 3:
            r.trend_leader = leaders[2].code
        r.leaders = [l.code for l in leaders]

        # 子维度1: Leader Trend（龙头趋势）
        r.leader_trend = self._score_leader_trend(leaders, stock_data)

        # 子维度2: Leader Breakout（龙头突破）
        r.leader_breakout = self._score_leader_breakout(leaders, stock_data)

        # 子维度3: Leader Breadth（龙头宽度：成份股中强势比例）
        r.leader_breadth = self._score_leader_breadth(constituents, stock_data)

        # 子维度4: Institutional Buying（机构买入）
        r.institution_buying = self._score_institution_buying(leaders, top_df, top_inst)

        # 子维度5: Northbound Buying（北向买入）
        r.northbound_buying = self._score_northbound(leaders, moneyflow)

        # 子维度6: Relative Strength（龙头相对强度）
        r.relative_strength = float(np.mean([l.rs_score for l in leaders]))

        # 子维度7: Industry Dominance（行业主导力）
        r.industry_dominance = self._score_industry_dominance(leaders, constituents, stock_data)

        # 子维度8: Leader Persistence（龙头持续性）
        r.leader_persistence = self._score_leader_persistence(leaders, stock_data)

        # 加权
        final = (
            r.leader_trend * self.w_trend +
            r.leader_breakout * self.w_breakout +
            r.leader_breadth * self.w_breadth +
            r.institution_buying * self.w_inst +
            r.northbound_buying * self.w_north +
            r.relative_strength * self.w_rs +
            r.industry_dominance * self.w_dom +
            r.leader_persistence * self.w_persist
        )
        r.leader_score = float(np.clip(final, 0, 100))
        r.reasons = self._build_reasons(r, leaders)
        return r

    # ------------------------------------------------------------------
    # 识别龙头（按RS+趋势+成交额综合排序）
    # ------------------------------------------------------------------
    def _identify_leaders(self, constituents, stock_data, etf_close_arr, top_df) -> List[LeaderInfo]:
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
            p = sd["pct_chg"].values.astype(float) if "pct_chg" in sd.columns else np.zeros_like(c)

            # RS评分
            r5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
            r10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
            r20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0
            rs = float(np.clip((r5 * 0.4 + r10 * 0.35 + r20 * 0.25) * 300 + 40, 0, 100))

            # 趋势评分
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

            # 突破评分
            br = float(np.clip(100 + breakout_pct(c, sd["high"].values, self.breakout_period) * 5, 0, 100))

            # 成交额（amount单位为千元，/1e5转换为亿元）
            avg_amt = float(np.mean(a[-10:]) / 1e5) if len(a) >= 10 else 0
            amt_s = float(np.clip(avg_amt * 5, 0, 100))

            # 龙虎榜加分
            top_bonus = 15.0 if code in top_set else 0.0

            total = rs * 0.35 + trend * 0.25 + br * 0.15 + amt_s * 0.15 + top_bonus
            scores.append((code, total, rs, trend, br))

        if not scores:
            return []

        scores.sort(key=lambda x: x[1], reverse=True)
        leaders = []
        for i, (code, total, rs, trend, br) in enumerate(scores[:self.leader_count]):
            role = "core" if i == 0 else ("second" if i == 1 else "trend")
            leaders.append(LeaderInfo(code=code, role=role, rs_score=rs,
                                       trend_score=trend, breakout_score=br))
        return leaders

    # ------------------------------------------------------------------
    # 子维度1: Leader Trend
    # ------------------------------------------------------------------
    def _score_leader_trend(self, leaders, stock_data) -> float:
        scores = []
        for l in leaders:
            sd = stock_data.get(l.code)
            if sd is None or sd.empty:
                continue
            sd = sd.sort_values("trade_date")
            c = sd["close"].values.astype(float)
            if len(c) < self.ma_period:
                continue
            ma5 = float(np.mean(c[-5:]))
            ma10 = float(np.mean(c[-10:]))
            ma20 = float(np.mean(c[-20:]))
            s = 40.0
            if c[-1] > ma5 > ma10 > ma20:
                s = 100.0
            elif c[-1] > ma10 > ma20:
                s = 75.0
            elif c[-1] > ma20:
                s = 60.0
            elif c[-1] < ma20:
                s = 20.0
            scores.append(s)
        return float(np.mean(scores)) if scores else 50.0

    # ------------------------------------------------------------------
    # 子维度2: Leader Breakout
    # ------------------------------------------------------------------
    def _score_leader_breakout(self, leaders, stock_data) -> float:
        scores = []
        for l in leaders:
            sd = stock_data.get(l.code)
            if sd is None or sd.empty:
                continue
            sd = sd.sort_values("trade_date")
            c = sd["close"].values.astype(float)
            h = sd["high"].values.astype(float) if "high" in sd.columns else c
            if len(c) < self.breakout_period:
                continue
            pct = breakout_pct(c, h, self.breakout_period)
            # 接近高点=高分
            s = float(np.clip(100 + pct * 5, 0, 100))
            scores.append(s)
        return float(np.mean(scores)) if scores else 50.0

    # ------------------------------------------------------------------
    # 子维度3: Leader Breadth
    # ------------------------------------------------------------------
    def _score_leader_breadth(self, constituents, stock_data) -> float:
        above_ma20 = 0
        total = 0
        for code in constituents:
            sd = stock_data.get(code)
            if sd is None or sd.empty or len(sd) < self.ma_period:
                continue
            sd = sd.sort_values("trade_date")
            c = sd["close"].values.astype(float)
            ma20 = float(np.mean(c[-20:]))
            total += 1
            if c[-1] > ma20:
                above_ma20 += 1
        if total == 0:
            return 50.0
        return float(above_ma20 / total * 100)

    # ------------------------------------------------------------------
    # 子维度4: Institutional Buying
    # ------------------------------------------------------------------
    def _score_institution_buying(self, leaders, top_df, top_inst) -> float:
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

    # ------------------------------------------------------------------
    # 子维度5: Northbound Buying
    # ------------------------------------------------------------------
    def _score_northbound(self, leaders, moneyflow) -> float:
        s = 50.0
        if moneyflow is None or moneyflow.empty:
            return s
        leader_codes = set(l.code for l in leaders)
        mf = moneyflow[moneyflow["ts_code"].isin(leader_codes)]
        if mf.empty:
            return s
        # 用大单/超大单净流入近似北向
        buy_cols = [c for c in ["buy_elg_amount", "buy_elg_amounts"] if c in mf.columns]
        sell_cols = [c for c in ["sell_elg_amount", "sell_elg_amounts"] if c in mf.columns]
        if buy_cols and sell_cols:
            buy = float(mf[buy_cols].sum().sum())
            sell = float(mf[sell_cols].sum().sum())
            if buy + sell > 0:
                net = (buy - sell) / (buy + sell)
                s += float(np.clip(net * 40, -25, 25))
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度7: Industry Dominance
    # ------------------------------------------------------------------
    def _score_industry_dominance(self, leaders, constituents, stock_data) -> float:
        # 龙头成交额占主题比例
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
        # 适度集中=好，过度集中=风险
        if 0.2 <= ratio <= 0.5:
            return 80.0
        elif ratio > 0.5:
            return 60.0  # 过度集中
        elif ratio > 0.1:
            return 60.0
        return 40.0

    # ------------------------------------------------------------------
    # 子维度8: Leader Persistence
    # ------------------------------------------------------------------
    def _score_leader_persistence(self, leaders, stock_data) -> float:
        scores = []
        for l in leaders:
            sd = stock_data.get(l.code)
            if sd is None or sd.empty or "pct_chg" not in sd.columns:
                continue
            sd = sd.sort_values("trade_date")
            p = sd["pct_chg"].values.astype(float)
            consec = consecutive_up_days(p)
            s = float(np.clip(consec * 12, 0, 100))
            scores.append(s)
        return float(np.mean(scores)) if scores else 50.0

    def _build_reasons(self, r: LeaderConfirmResult, leaders) -> list:
        parts = []
        if r.core_leader:
            parts.append(f"核心龙头={r.core_leader}")
        if r.leader_trend >= 70:
            parts.append("龙头趋势强")
        if r.leader_breakout >= 70:
            parts.append("龙头突破高点")
        if r.leader_breadth >= 60:
            parts.append(f"宽度扩散({r.leader_breadth:.0f}%)")
        if r.institution_buying >= 65:
            parts.append("机构买入")
        if r.northbound_buying >= 65:
            parts.append("北向/主力流入")
        if r.relative_strength >= 70:
            parts.append("龙头RS强")
        if r.penalty > 0:
            parts.append(f"惩罚-{r.penalty}")
        return parts or ["中性"]
