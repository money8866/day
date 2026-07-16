#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module 2: Theme Alpha Engine 主题Alpha引擎
================================================
Purpose: 识别未来20~60天将跑赢的机构主题

Input: ETF/行业/概念/主题映射 + 资金流 + 龙头强度 + 产业基本面 + 热度 + 趋势 + 机构持仓

Suggested formula:
  Theme Score = Theme Alpha (40%)
              + Trend Persistence (20%)
              + Industry Growth (15%)
              + Leader Strength (10%)
              + Institutional Participation (10%)
              + Sentiment (5%)

Only keep Top 5 themes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_alpha_engine.indicators import (
    ema, slope, normalize, percentile_rank, winsorize,
    consecutive_up_days, above_ema_days,
)


@dataclass
class ThemeAlphaResult:
    """主题Alpha结果"""
    theme: str = ""
    theme_score: float = 0.0
    # 子维度
    theme_alpha: float = 0.0           # 40% 动量+相对强度
    trend_persistence: float = 0.0    # 20% 趋势持续性
    industry_growth: float = 0.0       # 15% 产业成长性
    leader_strength: float = 0.0       # 10% 龙头强度
    institution_participation: float = 0.0  # 10% 机构参与度
    sentiment: float = 0.0             # 5% 情绪
    # 元数据
    rank: int = 0
    leader_code: str = ""
    codes: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


class ThemeAlphaEngine:
    """主题Alpha引擎

    独立可运行，输出每个主题的0-100分数。
    所有子维度独立计算、可复用、可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("theme_alpha", {})
        self.w_alpha = self.cfg.get("theme_alpha_weight", 0.40)
        self.w_persist = self.cfg.get("trend_persistence_weight", 0.20)
        self.w_growth = self.cfg.get("industry_growth_weight", 0.15)
        self.w_leader = self.cfg.get("leader_strength_weight", 0.10)
        self.w_inst = self.cfg.get("institution_weight", 0.10)
        self.w_sent = self.cfg.get("sentiment_weight", 0.05)
        self.top_n = self.cfg.get("top_themes", 5)
        self.min_stocks = self.cfg.get("min_theme_stocks", 5)
        self.mom_periods = self.cfg.get("momentum_periods", [5, 10, 20, 40])
        self.mom_weights = self.cfg.get("momentum_weights", [0.25, 0.30, 0.25, 0.20])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score(self,
              daily: pd.DataFrame,
              universe: Dict[str, List[str]],
              moneyflow: Optional[pd.DataFrame] = None,
              limit_df: Optional[pd.DataFrame] = None,
              dc_hot: Optional[pd.DataFrame] = None,
              top_df: Optional[pd.DataFrame] = None,
              ) -> Dict[str, ThemeAlphaResult]:
        """对所有主题打分

        Parameters
        ----------
        daily : 全市场日线 [ts_code, trade_date, close, pct_chg, amount, high, low, vol]
        universe : {theme_name: [ts_code, ...]}
        moneyflow : 资金流数据
        limit_df : 涨停数据
        dc_hot : DC热度
        top_df : 龙虎榜
        """
        if not universe:
            return {}

        # 先计算所有主题的动量（用于跨主题百分位排名）
        all_momentums = []
        theme_mom = {}
        for tname, codes in universe.items():
            m = self._compute_momentum(daily, codes)
            theme_mom[tname] = m
            all_momentums.append(m)

        results = {}
        for tname, codes in universe.items():
            if len(codes) < self.min_stocks:
                continue
            r = self._score_theme(daily, codes, tname, theme_mom.get(tname, 0.0),
                                   all_momentums, moneyflow, limit_df, dc_hot, top_df)
            results[tname] = r

        # 排序并保留Top N（但全部返回，rank标记排名）
        sorted_themes = sorted(results.values(), key=lambda x: x.theme_score, reverse=True)
        for i, r in enumerate(sorted_themes):
            r.rank = i + 1
        return {r.theme: r for r in sorted_themes}

    # ------------------------------------------------------------------
    # 单主题评分
    # ------------------------------------------------------------------
    def _score_theme(self, daily, codes, tname, mom_raw, all_momentums,
                     moneyflow, limit_df, dc_hot, top_df) -> ThemeAlphaResult:
        r = ThemeAlphaResult(theme=tname, codes=list(codes))

        # 1. Theme Alpha (40%) - 动量 + 相对强度
        alpha_s = self._score_theme_alpha(daily, codes, mom_raw, all_momentums)
        r.theme_alpha = alpha_s

        # 2. Trend Persistence (20%)
        persist_s = self._score_trend_persistence(daily, codes)
        r.trend_persistence = persist_s

        # 3. Industry Growth (15%) - 用成交额增长近似
        growth_s = self._score_industry_growth(daily, codes)
        r.industry_growth = growth_s

        # 4. Leader Strength (10%)
        leader_s, leader_code = self._score_leader_strength(daily, codes, top_df)
        r.leader_strength = leader_s
        r.leader_code = leader_code or ""

        # 5. Institutional Participation (10%)
        inst_s = self._score_institution(daily, codes, moneyflow, top_df)
        r.institution_participation = inst_s

        # 6. Sentiment (5%)
        sent_s = self._score_sentiment(daily, codes, limit_df, dc_hot)
        r.sentiment = sent_s

        # 加权
        final = (
            alpha_s * self.w_alpha +
            persist_s * self.w_persist +
            growth_s * self.w_growth +
            leader_s * self.w_leader +
            inst_s * self.w_inst +
            sent_s * self.w_sent
        )
        r.theme_score = float(np.clip(final, 0.0, 100.0))
        r.reasons = self._build_reasons(r)
        return r

    # ------------------------------------------------------------------
    # 子维度1: Theme Alpha（动量+相对强度）
    # ------------------------------------------------------------------
    def _compute_momentum(self, daily, codes) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 0.0
        price = sub.groupby("trade_date")["close"].mean().sort_index()
        n = len(price)
        if n < 5:
            return 0.0
        rets = []
        for p, w in zip(self.mom_periods, self.mom_weights):
            if n > p:
                rets.append((price.iloc[-1] / price.iloc[-p - 1] - 1) * w)
        return float(sum(rets)) if rets else 0.0

    def _score_theme_alpha(self, daily, codes, mom_raw, all_momentums) -> float:
        if not all_momentums:
            return 50.0
        arr = np.array(all_momentums)
        # 百分位排名
        pct = float(np.sum(arr <= mom_raw) / len(arr))
        # 动量绝对值评分
        abs_s = float(np.clip(mom_raw * 500 + 50, 0, 100))
        # 综合
        return float(np.clip(pct * 60 + abs_s * 0.4, 0, 100))

    # ------------------------------------------------------------------
    # 子维度2: Trend Persistence 趋势持续性
    # ------------------------------------------------------------------
    def _score_trend_persistence(self, daily, codes) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty or len(codes) < 3:
            return 50.0
        price = sub.groupby("trade_date")["close"].mean().sort_index()
        n = len(price)
        if n < 20:
            return 50.0

        # ① EMA20方向
        ema20 = ema(price.values, 20)
        ema_up = 1.0 if ema20[-1] > ema20[-6] else 0.0

        # ② 连续上涨天数
        pct = sub.groupby("trade_date")["pct_chg"].mean().sort_index().values
        consec = consecutive_up_days(pct)

        # ③ 近20日上涨比例
        up_ratio = float(np.sum(pct[-20:] > 0) / min(n, 20))

        # ④ Higher High
        hh = 0
        if n >= 10:
            vals = price.values[-10:]
            hh = int(np.sum(np.diff(vals) > 0))

        s = (ema_up * 35 +
             min(consec / 10, 1) * 25 +
             up_ratio * 25 +
             min(hh / 9, 1) * 15)
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度3: Industry Growth 产业成长性
    # ------------------------------------------------------------------
    def _score_industry_growth(self, daily, codes) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0
        amt = sub.groupby("trade_date")["amount"].sum().sort_index()
        n = len(amt)
        if n < 20:
            return 50.0
        # 近10日均量 vs 前10日均量
        amt_10 = amt.iloc[-10:].mean()
        amt_prev = amt.iloc[-20:-10].mean()
        if amt_prev <= 0:
            return 50.0
        growth = (amt_10 / amt_prev - 1.0)
        # 成交额持续放大=资金关注=产业景气
        s = 50 + float(np.clip(growth * 200, -30, 50))
        # 价格上行确认
        price = sub.groupby("trade_date")["close"].mean().sort_index()
        if n > 20:
            r20 = price.iloc[-1] / price.iloc[-21] - 1
            s += float(np.clip(r20 * 100, -15, 15))
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度4: Leader Strength 龙头强度
    # ------------------------------------------------------------------
    def _score_leader_strength(self, daily, codes, top_df) -> tuple:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0, None

        top_set = set()
        if top_df is not None and not top_df.empty and "ts_code" in top_df.columns:
            top_set = set(top_df["ts_code"].tolist())

        scores = {}
        for code in codes:
            sd = sub[sub["ts_code"] == code].sort_values("trade_date")
            if len(sd) < 10:
                continue
            c = sd["close"].values
            a = sd["amount"].values
            p = sd["pct_chg"].values

            r5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
            r10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
            rs = float(np.clip((r5 * 0.6 + r10 * 0.4) * 300 + 40, 0, 100))

            avg_amt = float(np.mean(a[-10:]) / 1e8) if len(a) >= 10 else 0
            amt_s = float(np.clip(avg_amt * 5, 0, 100))

            ma5 = float(np.mean(c[-5:]))
            ma10 = float(np.mean(c[-10:]))
            ma20 = float(np.mean(c[-20:])) if len(c) >= 20 else ma10
            trend = 40.0
            if c[-1] > ma5 > ma10 > ma20:
                trend = 100.0
            elif c[-1] > ma10 > ma20:
                trend = 75.0
            elif c[-1] > ma20:
                trend = 60.0

            top_bonus = 30.0 if code in top_set else 0.0
            total = rs * 0.30 + amt_s * 0.25 + trend * 0.20 + top_bonus * 0.10 + \
                    float(np.clip(consecutive_up_days(p) * 12, 0, 100)) * 0.15
            scores[code] = total

        if not scores:
            return 50.0, None
        best = max(scores, key=scores.get)
        return float(scores[best]), best

    # ------------------------------------------------------------------
    # 子维度5: Institutional Participation 机构参与度
    # ------------------------------------------------------------------
    def _score_institution(self, daily, codes, moneyflow, top_df) -> float:
        s = 50.0
        # 龙虎榜机构
        if top_df is not None and not top_df.empty and "ts_code" in top_df.columns:
            top_count = len(set(top_df["ts_code"].tolist()) & set(codes))
            if top_count >= 3:
                s += 25
            elif top_count >= 1:
                s += 12

        # moneyflow机构净流入
        if moneyflow is not None and not moneyflow.empty:
            mf = moneyflow[moneyflow["ts_code"].isin(codes)]
            if not mf.empty:
                buy_cols = [c for c in ["buy_elg_amount", "buy_elg_amounts"] if c in mf.columns]
                sell_cols = [c for c in ["sell_elg_amount", "sell_elg_amounts"] if c in mf.columns]
                if buy_cols and sell_cols:
                    buy = float(mf[buy_cols].sum().sum())
                    sell = float(mf[sell_cols].sum().sum())
                    if buy + sell > 0:
                        net_ratio = (buy - sell) / (buy + sell)
                        s += float(np.clip(net_ratio * 50, -25, 25))

        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度6: Sentiment 情绪
    # ------------------------------------------------------------------
    def _score_sentiment(self, daily, codes, limit_df, dc_hot) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0
        latest_day = sub["trade_date"].max()
        latest = sub[sub["trade_date"] == latest_day]
        pct = latest["pct_chg"].values
        n = len(pct)
        if n == 0:
            return 50.0

        up_ratio = float(np.sum(pct > 0) / n)
        strong_ratio = float(np.sum(pct > 3) / n)
        s = up_ratio * 40 + strong_ratio * 30

        # 涨停加分
        if limit_df is not None and not limit_df.empty and "ts_code" in limit_df.columns:
            lu = len(set(limit_df["ts_code"].tolist()) & set(codes))
            s += float(np.clip(lu / n * 200, 0, 30))

        return float(np.clip(s, 0, 100))

    def _build_reasons(self, r: ThemeAlphaResult) -> list:
        parts = []
        if r.theme_alpha >= 70:
            parts.append("Alpha强(动量领先)")
        elif r.theme_alpha <= 35:
            parts.append("Alpha弱")
        if r.trend_persistence >= 70:
            parts.append("趋势持续性强")
        if r.industry_growth >= 65:
            parts.append("产业景气上行")
        if r.leader_strength >= 70:
            parts.append(f"龙头强({r.leader_code})")
        if r.institution_participation >= 65:
            parts.append("机构参与度高")
        return parts or ["中性"]
