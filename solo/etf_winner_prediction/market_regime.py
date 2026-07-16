#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 1: Market Regime Filter 市场状态过滤器
=============================================
判断是否允许建仓。计算7个维度:
  1. Market Trend      市场趋势
  2. Breadth           市场宽度
  3. Liquidity         流动性
  4. Volatility        波动率
  5. Sentiment         市场情绪
  6. Institutional     机构参与度
  7. Northbound Flow   北向资金

硬过滤器: MarketScore >= 60 才允许建仓
          MarketScore < 50 禁止买入
          MarketScore < 60 仓位 <= 30%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, List

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import ema, slope, volatility, volume_ratio


@dataclass
class MarketRegimeResult:
    market_score: float = 50.0
    market_state: str = "Neutral"
    risk_level: str = "Medium"
    recommended_exposure: float = 0.3
    # 子维度
    trend_score: float = 0.0
    breadth_score: float = 0.0
    liquidity_score: float = 0.0
    volatility_score: float = 0.0
    sentiment_score: float = 0.0
    institutional_score: float = 0.0
    northbound_score: float = 0.0
    etf_turnover_score: float = 0.0
    # 原始指标
    num_above_ma20: int = 0
    num_above_ma60: int = 0
    total_amount_yi: float = 0.0
    limit_up_count: int = 0
    limit_down_count: int = 0
    northbound_net: float = 0.0
    etf_amount_yi: float = 0.0
    reasons: list = field(default_factory=list)


class MarketRegimeFilter:
    """市场状态过滤器 - Step 1"""

    def __init__(self, config: dict):
        self.cfg = config.get("market_regime", {})
        self.w_trend = self.cfg.get("trend_weight", 0.20)
        self.w_breadth = self.cfg.get("breadth_weight", 0.15)
        self.w_liquidity = self.cfg.get("liquidity_weight", 0.15)
        self.w_volatility = self.cfg.get("volatility_weight", 0.15)
        self.w_sentiment = self.cfg.get("sentiment_weight", 0.15)
        self.w_institutional = self.cfg.get("institutional_weight", 0.10)
        self.w_northbound = self.cfg.get("northbound_weight", 0.05)
        self.w_etf_turnover = self.cfg.get("etf_turnover_weight", 0.05)
        self.ma_short = self.cfg.get("ma_short", 20)
        self.ma_long = self.cfg.get("ma_long", 60)
        self.amount_ma = self.cfg.get("amount_ma_period", 20)
        self.exposure_map = self.cfg.get("exposure", {
            "Bull": 1.0, "Recovery": 0.8, "Neutral": 0.5, "Weak": 0.3, "Bear": 0.0
        })
        self.no_buy_below = self.cfg.get("no_buy_below", 50)
        self.reduced_below = self.cfg.get("reduced_exposure_below", 60)

    def score(self, index_df: Optional[pd.DataFrame],
              market_daily: Optional[pd.DataFrame],
              limit_df: Optional[pd.DataFrame] = None,
              etf_data: Optional[Dict[str, pd.DataFrame]] = None,
              northbound_net: float = 0.0,
              top_df: Optional[pd.DataFrame] = None,
              top_inst: Optional[pd.DataFrame] = None) -> MarketRegimeResult:
        """计算市场状态"""
        result = MarketRegimeResult()

        # 1. 趋势
        result.trend_score, _ = self._score_trend(index_df)

        # 2. 宽度
        result.breadth_score, bm = self._score_breadth(market_daily)
        result.num_above_ma20 = bm.get("above_ma20", 0)
        result.num_above_ma60 = bm.get("above_ma60", 0)

        # 3. 流动性
        result.liquidity_score, lm = self._score_liquidity(market_daily, etf_data)
        result.total_amount_yi = lm.get("total_amount", 0.0)
        result.etf_amount_yi = lm.get("etf_amount", 0.0)

        # 4. 波动率
        result.volatility_score = self._score_volatility(index_df)

        # 5. 情绪
        result.sentiment_score, sm = self._score_sentiment(market_daily, limit_df)
        result.limit_up_count = sm.get("limit_up", 0)
        result.limit_down_count = sm.get("limit_down", 0)

        # 6. 机构参与度
        result.institutional_score = self._score_institutional(top_df, top_inst, limit_df)

        # 7. 北向资金
        result.northbound_score = self._score_northbound(northbound_net)
        result.northbound_net = northbound_net

        # 8. ETF成交额
        result.etf_turnover_score = self._score_etf_turnover(etf_data)

        # 综合
        final = (
            result.trend_score * self.w_trend +
            result.breadth_score * self.w_breadth +
            result.liquidity_score * self.w_liquidity +
            result.volatility_score * self.w_volatility +
            result.sentiment_score * self.w_sentiment +
            result.institutional_score * self.w_institutional +
            result.northbound_score * self.w_northbound +
            result.etf_turnover_score * self.w_etf_turnover
        )
        result.market_score = float(np.clip(final, 0, 100))

        # ---- 反转预警信号（直接调整最终分数）----
        penalty_total = 0.0
        vp_div = self._detect_volume_price_divergence(market_daily)
        if vp_div != 0:
            penalty_total += vp_div
            result.reasons.append(f"量价背离: {vp_div:+.1f}")

        rb_fatigue = self._detect_rebound_fatigue(market_daily)
        if rb_fatigue != 0:
            penalty_total += rb_fatigue
            result.reasons.append(f"反弹力度衰减: {rb_fatigue:+.1f}")

        ld_anomaly = self._detect_limit_down_anomaly(market_daily)
        if ld_anomaly != 0:
            penalty_total += ld_anomaly
            result.reasons.append(f"跌停家数异常: {ld_anomaly:+.1f}")

        lr_sent = self._detect_limit_ratio_sentiment(market_daily)
        if lr_sent != 0:
            penalty_total += lr_sent
            result.reasons.append(f"涨跌停比情绪: {lr_sent:+.1f}")

        if penalty_total != 0:
            result.market_score = float(np.clip(result.market_score + penalty_total, 0, 100))

        result.market_state = self._classify_state(result.market_score)
        result.risk_level = self._classify_risk(result.market_score)
        result.recommended_exposure = self._calc_exposure(result.market_score)
        result.reasons = self._build_reasons(result)
        return result

    def _score_trend(self, index_df) -> tuple:
        if index_df is None or index_df.empty:
            return 50.0, {}
        df = index_df.sort_values("trade_date")
        close = df["close"].values.astype(float)
        if len(close) < self.ma_long + 5:
            return 50.0, {}
        ma_s = ema(close, self.ma_short)
        ma_l = ema(close, self.ma_long)
        latest = close[-1]
        s = 50.0
        if latest > ma_s[-1] > ma_l[-1]:
            s += 25
        elif latest > ma_s[-1]:
            s += 12
        elif latest < ma_s[-1] < ma_l[-1]:
            s -= 25
        elif latest < ma_l[-1]:
            s -= 12
        sl = slope(close, self.ma_short)
        norm_sl = sl / max(latest, 1e-6) * 10000
        s += float(np.clip(norm_sl * 5, -15, 15))
        if len(close) > 20:
            r20 = close[-1] / close[-21] - 1
            s += float(np.clip(r20 * 200, -15, 15))
        return float(np.clip(s, 0, 100)), {}

    def _score_breadth(self, market_daily) -> tuple:
        if market_daily is None or market_daily.empty:
            return 50.0, {"above_ma20": 0, "above_ma60": 0}
        latest_date = market_daily["trade_date"].max()
        above_ma20 = 0
        above_ma60 = 0
        total = 0
        for code, sub in market_daily.groupby("ts_code"):
            sub = sub.sort_values("trade_date")
            if len(sub) < self.ma_long:
                continue
            total += 1
            ma20 = sub["close"].rolling(self.ma_short).mean().iloc[-1]
            ma60 = sub["close"].rolling(self.ma_long).mean().iloc[-1]
            cur = sub["close"].iloc[-1]
            if pd.notna(ma20) and cur > ma20:
                above_ma20 += 1
            if pd.notna(ma60) and cur > ma60:
                above_ma60 += 1
        if total == 0:
            return 50.0, {"above_ma20": 0, "above_ma60": 0}
        ratio_20 = above_ma20 / total
        ratio_60 = above_ma60 / total
        s = ratio_20 * 60 + ratio_60 * 40
        return float(np.clip(s * 100, 0, 100)), {"above_ma20": above_ma20, "above_ma60": above_ma60}

    def _score_liquidity(self, market_daily, etf_data) -> tuple:
        metrics = {"total_amount": 0.0, "etf_amount": 0.0}
        if market_daily is None or market_daily.empty:
            return 50.0, metrics
        latest_date = market_daily["trade_date"].max()
        latest = market_daily[market_daily["trade_date"] == latest_date]
        if latest.empty or "amount" not in latest.columns:
            return 50.0, metrics
        total_amt = float(latest["amount"].sum() / 100000.0)
        metrics["total_amount"] = total_amt
        etf_amt = 0.0
        if etf_data:
            for df in etf_data.values():
                if df is not None and not df.empty and "amount" in df.columns:
                    etf_amt += float(df["amount"].iloc[-1] / 100000.0)
        metrics["etf_amount"] = etf_amt
        s = float(np.clip((total_amt - 3000) / 5000 * 60 + 40, 0, 100))
        return s, metrics

    def _score_volatility(self, index_df) -> float:
        if index_df is None or index_df.empty:
            return 50.0
        close = index_df["close"].values.astype(float)
        if len(close) < 30:
            return 50.0
        vol = volatility(close, 20)
        # 低波动=高分
        if vol < 0.15:
            s = 90.0
        elif vol < 0.20:
            s = 80.0
        elif vol < 0.25:
            s = 65.0
        elif vol < 0.35:
            s = 50.0
        elif vol < 0.45:
            s = 35.0
        else:
            s = 20.0
        return float(np.clip(s, 0, 100))

    def _score_sentiment(self, market_daily, limit_df) -> tuple:
        metrics = {"limit_up": 0, "limit_down": 0}
        if market_daily is None or market_daily.empty:
            return 50.0, metrics
        latest_date = market_daily["trade_date"].max()
        latest = market_daily[market_daily["trade_date"] == latest_date]
        if latest.empty:
            return 50.0, metrics
        pct = latest["pct_chg"].values
        n = len(pct)
        if n == 0:
            return 50.0, metrics
        limit_up = int(np.sum(pct >= 9.8))
        limit_down = int(np.sum(pct <= -9.8))
        if limit_df is not None and not limit_df.empty and "ts_code" in limit_df.columns:
            limit_up = len(limit_df)
        metrics["limit_up"] = limit_up
        metrics["limit_down"] = limit_down
        up_ratio = float(np.sum(pct > 0) / n)
        strong_ratio = float(np.sum(pct > 3) / n)
        s = up_ratio * 40 + strong_ratio * 20
        if limit_up > 50:
            s += 20
        elif limit_up > 20:
            s += 12
        elif limit_up > 5:
            s += 6
        if limit_down > 20:
            s -= 15
        elif limit_down > 5:
            s -= 8
        return float(np.clip(s, 0, 100)), metrics

    def _score_institutional(self, top_df, top_inst, limit_df) -> float:
        s = 50.0
        if top_df is not None and not top_df.empty:
            s += min(len(top_df) * 0.2, 25)
        if top_inst is not None and not top_inst.empty:
            s += min(len(top_inst) * 0.3, 25)
        return float(np.clip(s, 0, 100))

    def _score_northbound(self, northbound_net: float) -> float:
        s = 50.0
        if northbound_net > 50:
            s += 25
        elif northbound_net > 20:
            s += 15
        elif northbound_net > 0:
            s += 5
        elif northbound_net < -50:
            s -= 25
        elif northbound_net < -20:
            s -= 15
        elif northbound_net < 0:
            s -= 5
        return float(np.clip(s, 0, 100))

    def _score_etf_turnover(self, etf_data) -> float:
        if not etf_data:
            return 50.0
        s = 50.0
        count = 0
        for df in etf_data.values():
            if df is None or df.empty or "amount" not in df.columns:
                continue
            if len(df) < 20:
                continue
            vr = volume_ratio(df["vol"].values.astype(float), 20)
            if vr > 1.5:
                count += 1
        s += min(count * 5, 25)
        return float(np.clip(s, 0, 100))

    def _detect_volume_price_divergence(self, market_daily) -> float:
        """量价背离检测：缩量上涨扣分（最多-8分）

        - 涨幅 > 0 且成交额5日衰减 > 5% -> 扣分
        - 用于捕捉反弹末期缩量上涨的反转信号
        """
        if market_daily is None or market_daily.empty:
            return 0.0
        daily = market_daily.groupby("trade_date").agg(
            total_amt=("amount", "sum"),
            avg_pct=("pct_chg", "mean"),
            up_count=("pct_chg", lambda x: (x > 0).sum()),
            down_count=("pct_chg", lambda x: (x < 0).sum()),
        ).sort_index()
        if len(daily) < 5:
            return 0.0
        recent = daily.tail(5)
        last_up = recent["avg_pct"].iloc[-1] > 0
        last_amt = recent["total_amt"].iloc[-1]
        avg_amt = recent["total_amt"].iloc[:-1].mean()
        if avg_amt <= 0:
            return 0.0
        decay_ratio = (avg_amt - last_amt) / avg_amt
        if last_up and decay_ratio > 0.05:
            penalty = min(decay_ratio * 40, 8.0)
            return -penalty
        return 0.0

    def _detect_rebound_fatigue(self, market_daily) -> float:
        """反弹力度衰减检测：5日涨跌比递减 -> 扣分（最多-5分）

        - 最近3天涨跌比呈递减趋势 -> 反弹力度衰竭
        """
        if market_daily is None or market_daily.empty:
            return 0.0
        daily = market_daily.groupby("trade_date").agg(
            up_count=("pct_chg", lambda x: (x > 0).sum()),
            down_count=("pct_chg", lambda x: (x < 0).sum()),
        ).sort_index()
        if len(daily) < 3:
            return 0.0
        recent = daily.tail(3)
        ratios = recent["up_count"] / recent["down_count"].clip(lower=1)
        if ratios.iloc[0] > ratios.iloc[1] > ratios.iloc[2]:
            decay = (ratios.iloc[0] - ratios.iloc[2]) / max(ratios.iloc[0], 1e-6)
            penalty = min(decay * 5, 5.0)
            return -penalty
        return 0.0

    def _detect_limit_down_anomaly(self, market_daily) -> float:
        """跌停家数异常检测：跌停 >= 涨停*0.8 -> 扣分（最多-5分）

        - 涨跌停比 < 1.3 -> 市场情绪转弱
        """
        if market_daily is None or market_daily.empty:
            return 0.0
        latest_date = market_daily["trade_date"].max()
        latest = market_daily[market_daily["trade_date"] == latest_date]
        if latest.empty or "pct_chg" not in latest.columns:
            return 0.0
        pct = latest["pct_chg"]
        limit_up = int((pct >= 9.8).sum())
        limit_down = int((pct <= -9.8).sum())
        if limit_down > 0 and limit_up > 0:
            ratio = limit_up / limit_down
            if ratio < 1.0:
                return -5.0
            elif ratio < 1.3:
                return -3.0
            elif ratio < 2.0:
                return -1.5
        elif limit_down > 50 and limit_up < 20:
            return -4.0
        return 0.0

    def _detect_limit_ratio_sentiment(self, market_daily) -> float:
        """涨跌停比情绪指标（额外加减分，最多±3分）

        - 涨跌停比 > 5 强势 -> +3
        - 涨跌停比 2-5 正常 -> +1
        - 涨跌停比 < 1 弱势 -> -2
        """
        if market_daily is None or market_daily.empty:
            return 0.0
        latest_date = market_daily["trade_date"].max()
        latest = market_daily[market_daily["trade_date"] == latest_date]
        if latest.empty or "pct_chg" not in latest.columns:
            return 0.0
        pct = latest["pct_chg"]
        limit_up = int((pct >= 9.8).sum())
        limit_down = int((pct <= -9.8).sum())
        if limit_down == 0:
            if limit_up > 100:
                return 3.0
            elif limit_up > 30:
                return 2.0
            return 0.0
        ratio = limit_up / limit_down
        if ratio >= 5:
            return 3.0
        elif ratio >= 2:
            return 1.0
        elif ratio < 1:
            return -2.0
        return 0.0

    def _classify_state(self, score: float) -> str:
        for th, st in [(75, "Bull"), (60, "Recovery"), (45, "Neutral"), (30, "Weak")]:
            if score >= th:
                return st
        return "Bear"

    def _classify_risk(self, score: float) -> str:
        if score >= 70:
            return "Low"
        if score >= 50:
            return "Medium"
        if score >= 35:
            return "High"
        return "Extreme"

    def _calc_exposure(self, score: float) -> float:
        if score < self.no_buy_below:
            return 0.0
        if score < self.reduced_below:
            return 0.3
        return self.exposure_map.get(self._classify_state(score), 0.3)

    def _build_reasons(self, r: MarketRegimeResult) -> list:
        parts = []
        if r.trend_score >= 70:
            parts.append("市场趋势向上")
        elif r.trend_score <= 35:
            parts.append("市场趋势向下")
        if r.breadth_score >= 70:
            parts.append(f"宽度强({r.num_above_ma20}家站上MA20)")
        if r.sentiment_score >= 70:
            parts.append(f"情绪高涨(涨停{r.limit_up_count})")
        elif r.sentiment_score <= 35:
            parts.append("情绪低迷")
        if r.liquidity_score >= 70:
            parts.append(f"流动性充沛({r.total_amount_yi:.0f}亿)")
        if r.northbound_net > 0:
            parts.append(f"北向净流入{r.northbound_net:.1f}亿")
        elif r.northbound_net < 0:
            parts.append(f"北向净流出{abs(r.northbound_net):.1f}亿")
        return parts or ["市场中性"]