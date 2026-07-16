#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 2: Theme Forecast Engine 主题预测引擎
=============================================
预测未来20/40/60天哪些主题将跑赢。

Features:
  - Theme Momentum (多周期)
  - Capital Flow (资金流向)
  - Industry Growth (产业成长)
  - Policy Strength (政策强度-用热度近似)
  - Heat Persistence (热度持续性)
  - Institutional Buying (机构买入)
  - Northbound Buying (北向买入)
  - ETF Flow (ETF资金流)
  - Leader Breadth (龙头宽度)
  - News Sentiment (情绪-用DC热度近似)
  - Industry Earnings Revision (盈利修正-用动量近似)

Output:
  - ThemeForecastScore (0-100)
  - ThemeRank (未来预期排名)
  - RemainingTrendDays (剩余趋势天数)
  - ProbabilityTop3 (进入Top3概率)
  - RotationProbability (轮动概率)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import (
    ema, slope, percentile_rank, winsorize,
    consecutive_up_days, volume_ratio,
)


@dataclass
class ThemeForecastResult:
    theme: str = ""
    forecast_score: float = 0.0
    forecast_rank: int = 0
    # 子维度
    theme_momentum: float = 0.0
    capital_flow: float = 0.0
    industry_growth: float = 0.0
    policy_strength: float = 0.0
    heat_persistence: float = 0.0
    institutional_buying: float = 0.0
    northbound_buying: float = 0.0
    etf_flow: float = 0.0
    leader_breadth: float = 0.0
    news_sentiment: float = 0.0
    earnings_revision: float = 0.0
    # 预测输出
    remaining_trend_days: int = 0
    probability_top3: float = 0.0
    rotation_probability: float = 0.0
    # 元数据
    codes: list = field(default_factory=list)
    leader_code: str = ""
    reasons: list = field(default_factory=list)


class ThemeForecastEngine:
    """主题预测引擎 - Step 2"""

    def __init__(self, config: dict):
        self.cfg = config.get("theme_forecast", {})
        self.w_momentum = self.cfg.get("theme_momentum_weight", 0.20)
        self.w_capital = self.cfg.get("capital_flow_weight", 0.15)
        self.w_growth = self.cfg.get("industry_growth_weight", 0.15)
        self.w_policy = self.cfg.get("policy_strength_weight", 0.10)
        self.w_heat = self.cfg.get("heat_persistence_weight", 0.10)
        self.w_inst = self.cfg.get("institutional_buying_weight", 0.10)
        self.w_north = self.cfg.get("northbound_buying_weight", 0.05)
        self.w_etf = self.cfg.get("etf_flow_weight", 0.05)
        self.w_breadth = self.cfg.get("leader_breadth_weight", 0.05)
        self.w_news = self.cfg.get("news_sentiment_weight", 0.03)
        self.w_earn = self.cfg.get("earnings_revision_weight", 0.02)
        self.mom_periods = self.cfg.get("momentum_periods", [5, 10, 20, 40])
        self.mom_weights = self.cfg.get("momentum_weights", [0.15, 0.25, 0.30, 0.30])
        self.top_n = self.cfg.get("top_themes", 5)
        self.min_stocks = self.cfg.get("min_theme_stocks", 5)
        self.horizons = self.cfg.get("forecast_horizons", [20, 40, 60])

    def score(self, daily: pd.DataFrame,
              universe: Dict[str, List[str]],
              moneyflow: Optional[pd.DataFrame] = None,
              limit_df: Optional[pd.DataFrame] = None,
              dc_hot: Optional[pd.DataFrame] = None,
              top_df: Optional[pd.DataFrame] = None,
              top_inst: Optional[pd.DataFrame] = None) -> Dict[str, ThemeForecastResult]:
        """预测所有主题未来排名"""
        if not universe:
            return {}

        # 计算所有主题动量（用于横截面排名）
        all_momentums = {}
        for tname, codes in universe.items():
            all_momentums[tname] = self._compute_momentum(daily, codes)

        results = {}
        for tname, codes in universe.items():
            if len(codes) < self.min_stocks:
                continue
            r = self._score_theme(daily, codes, tname, all_momentums,
                                  moneyflow, limit_df, dc_hot, top_df, top_inst)
            results[tname] = r

        # 排序
        sorted_themes = sorted(results.values(), key=lambda x: x.forecast_score, reverse=True)
        for i, r in enumerate(sorted_themes):
            r.forecast_rank = i + 1

        # 计算Top3概率（基于分数差距）
        self._compute_rank_probabilities(sorted_themes)
        return {r.theme: r for r in sorted_themes}

    def _score_theme(self, daily, codes, tname, all_momentums,
                     moneyflow, limit_df, dc_hot, top_df, top_inst) -> ThemeForecastResult:
        r = ThemeForecastResult(theme=tname, codes=list(codes))

        # 1. Theme Momentum
        mom_raw = all_momentums.get(tname, 0.0)
        all_mom = list(all_momentums.values())
        r.theme_momentum = self._score_momentum(mom_raw, all_mom)

        # 2. Capital Flow
        r.capital_flow = self._score_capital_flow(daily, codes, moneyflow)

        # 3. Industry Growth
        r.industry_growth = self._score_industry_growth(daily, codes)

        # 4. Policy Strength (用DC热度近似)
        r.policy_strength = self._score_policy_strength(daily, codes, dc_hot)

        # 5. Heat Persistence
        r.heat_persistence = self._score_heat_persistence(daily, codes)

        # 6. Institutional Buying
        r.institutional_buying = self._score_institutional(codes, top_df, top_inst)

        # 7. Northbound Buying
        r.northbound_buying = self._score_northbound(codes, moneyflow)

        # 8. ETF Flow
        r.etf_flow = self._score_etf_flow(daily, codes)

        # 9. Leader Breadth
        r.leader_breadth = self._score_leader_breadth(daily, codes)

        # 10. News Sentiment
        r.news_sentiment = self._score_news_sentiment(daily, codes, dc_hot, limit_df)

        # 11. Earnings Revision
        r.earnings_revision = self._score_earnings_revision(daily, codes)

        # 加权
        final = (
            r.theme_momentum * self.w_momentum +
            r.capital_flow * self.w_capital +
            r.industry_growth * self.w_growth +
            r.policy_strength * self.w_policy +
            r.heat_persistence * self.w_heat +
            r.institutional_buying * self.w_inst +
            r.northbound_buying * self.w_north +
            r.etf_flow * self.w_etf +
            r.leader_breadth * self.w_breadth +
            r.news_sentiment * self.w_news +
            r.earnings_revision * self.w_earn
        )
        r.forecast_score = float(np.clip(final, 0, 100))
        r.reasons = self._build_reasons(r)
        return r

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

    def _score_momentum(self, mom_raw, all_momentums) -> float:
        if not all_momentums:
            return 50.0
        arr = np.array(all_momentums)
        pct = float(np.sum(arr <= mom_raw) / len(arr))
        abs_s = float(np.clip(mom_raw * 500 + 50, 0, 100))
        return float(np.clip(pct * 60 + abs_s * 0.4, 0, 100))

    def _score_capital_flow(self, daily, codes, moneyflow) -> float:
        s = 50.0
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
        # 成交额增长
        sub = daily[daily["ts_code"].isin(codes)]
        if not sub.empty:
            amt = sub.groupby("trade_date")["amount"].sum().sort_index()
            if len(amt) >= 20:
                amt_5 = amt.iloc[-5:].mean()
                amt_20 = amt.iloc[-20:].mean()
                if amt_20 > 0:
                    growth = (amt_5 / amt_20 - 1.0)
                    s += float(np.clip(growth * 100, -15, 15))
        return float(np.clip(s, 0, 100))

    def _score_industry_growth(self, daily, codes) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0
        price = sub.groupby("trade_date")["close"].mean().sort_index()
        n = len(price)
        if n < 20:
            return 50.0
        r20 = price.iloc[-1] / price.iloc[-21] - 1
        r40 = price.iloc[-1] / price.iloc[-41] - 1 if n > 40 else r20
        s = 50 + float(np.clip(r20 * 200, -20, 30) + np.clip(r40 * 100, -10, 20))
        return float(np.clip(s, 0, 100))

    def _score_policy_strength(self, daily, codes, dc_hot) -> float:
        s = 50.0
        if dc_hot is not None and not dc_hot.empty and "ts_code" in dc_hot.columns:
            hot_codes = set(dc_hot["ts_code"].tolist())
            overlap = len(set(codes) & hot_codes)
            s += float(np.clip(overlap / max(len(codes), 1) * 50, 0, 50))
        return float(np.clip(s, 0, 100))

    def _score_heat_persistence(self, daily, codes) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0
        pct = sub.groupby("trade_date")["pct_chg"].mean().sort_index().values
        n = len(pct)
        if n < 20:
            return 50.0
        consec = consecutive_up_days(pct)
        up_ratio = float(np.sum(pct[-20:] > 0) / min(n, 20))
        s = min(consec / 10, 1) * 40 + up_ratio * 40
        # 波动率评估
        if n >= 20:
            std = float(np.std(pct[-20:]))
            if std < 0.02:
                s += 20
            elif std > 0.05:
                s -= 10
        return float(np.clip(s, 0, 100))

    def _score_institutional(self, codes, top_df, top_inst) -> float:
        s = 50.0
        if top_df is not None and not top_df.empty and "ts_code" in top_df.columns:
            cnt = len(set(codes) & set(top_df["ts_code"].tolist()))
            if cnt >= 3:
                s += 25
            elif cnt >= 1:
                s += 12
        if top_inst is not None and not top_inst.empty and "ts_code" in top_inst.columns:
            cnt = len(set(codes) & set(top_inst["ts_code"].tolist()))
            if cnt >= 1:
                s += 20
        return float(np.clip(s, 0, 100))

    def _score_northbound(self, codes, moneyflow) -> float:
        s = 50.0
        if moneyflow is None or moneyflow.empty:
            return s
        mf = moneyflow[moneyflow["ts_code"].isin(codes)]
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

    def _score_etf_flow(self, daily, codes) -> float:
        s = 50.0
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return s
        amt = sub.groupby("trade_date")["amount"].sum().sort_index()
        if len(amt) >= 20:
            # 近5日成交额 vs 近20日均值
            amt_5 = amt.iloc[-5:].mean()
            amt_20 = amt.iloc[-20:].mean()
            if amt_20 > 0:
                ratio = amt_5 / amt_20
                s += float(np.clip((ratio - 1) * 50, -20, 30))
        return float(np.clip(s, 0, 100))

    def _score_leader_breadth(self, daily, codes) -> float:
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0
        latest_day = sub["trade_date"].max()
        latest = sub[sub["trade_date"] == latest_day]
        above_ma20 = 0
        total = 0
        for code, sd in sub.groupby("ts_code"):
            if len(sd) < 20:
                continue
            total += 1
            if sd["close"].iloc[-1] > sd["close"].iloc[-20:].mean():
                above_ma20 += 1
        if total == 0:
            return 50.0
        return float(above_ma20 / total * 100)

    def _score_news_sentiment(self, daily, codes, dc_hot, limit_df) -> float:
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
        if limit_df is not None and not limit_df.empty and "ts_code" in limit_df.columns:
            lu = len(set(codes) & set(limit_df["ts_code"].tolist()))
            s += float(np.clip(lu / n * 200, 0, 30))
        return float(np.clip(s, 0, 100))

    def _score_earnings_revision(self, daily, codes) -> float:
        # 用动量持续性近似盈利修正
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return 50.0
        price = sub.groupby("trade_date")["close"].mean().sort_index()
        n = len(price)
        if n < 40:
            return 50.0
        r20 = price.iloc[-1] / price.iloc[-21] - 1
        r20_prev = price.iloc[-21] / price.iloc[-41] - 1
        # 动量加速 = 盈利修正向上
        accel = r20 - r20_prev
        s = 50 + float(np.clip(accel * 300, -20, 25))
        return float(np.clip(s, 0, 100))

    def _compute_rank_probabilities(self, sorted_results):
        """基于分数差距计算Top1/Top3概率"""
        if not sorted_results:
            return
        scores = np.array([r.forecast_score for r in sorted_results])
        max_score = scores[0] if len(scores) > 0 else 0
        for i, r in enumerate(sorted_results):
            gap = (max_score - r.forecast_score) / max(max_score, 1)
            r.probability_top3 = float(np.clip(1.0 - gap * 0.5, 0.05, 0.95))
            # 估计剩余趋势天数
            r.remaining_trend_days = self._estimate_remaining_days(r)
            r.rotation_probability = self._estimate_rotation(r)

    def _estimate_remaining_days(self, r: ThemeForecastResult) -> int:
        base = 30
        if r.forecast_score >= 80:
            base = 55
        elif r.forecast_score >= 70:
            base = 45
        elif r.forecast_score >= 60:
            base = 35
        elif r.forecast_score >= 50:
            base = 25
        else:
            base = 15
        if r.heat_persistence >= 70:
            base += 10
        if r.industry_growth >= 70:
            base += 5
        return int(np.clip(base, 5, 60))

    def _estimate_rotation(self, r: ThemeForecastResult) -> float:
        base = 30.0
        if r.forecast_score >= 80:
            base = 10.0
        elif r.forecast_score >= 60:
            base = 25.0
        elif r.forecast_score >= 40:
            base = 45.0
        else:
            base = 65.0
        if r.capital_flow < 50:
            base += 10
        return float(np.clip(base, 5, 90))

    def _build_reasons(self, r: ThemeForecastResult) -> list:
        parts = []
        if r.forecast_score >= 75:
            parts.append(f"预测排名#{r.forecast_rank}, 预期Top3")
        if r.theme_momentum >= 70:
            parts.append("动量强劲")
        if r.capital_flow >= 65:
            parts.append("资金流入")
        if r.industry_growth >= 65:
            parts.append("产业景气上行")
        if r.heat_persistence >= 65:
            parts.append("热度持续")
        if r.institutional_buying >= 65:
            parts.append("机构参与")
        if r.leader_breadth >= 60:
            parts.append(f"宽度扩散({r.leader_breadth:.0f}%)")
        if r.remaining_trend_days >= 30:
            parts.append(f"预计持续{r.remaining_trend_days}天")
        return parts or ["中性"]