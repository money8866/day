#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module 1: Market Regime Engine 市场状态引擎
================================================
Purpose: 判断是否允许激进建仓

计算维度:
  1. Market Trend Score   市场趋势（指数MA位置+斜率）
  2. Market Breadth       市场宽度（站上MA20/MA60家数）
  3. Market Sentiment     市场情绪（涨停/跌停/炸板率/20cm）
  4. Liquidity            流动性（成交额/ETF成交额）
  5. Risk Appetite        风险偏好（北向资金/涨停连板高度）

Output:
  - Market Score (0-100)
  - Market State (Bull/Recovery/Neutral/Weak/Bear)
  - Suggested Exposure (0% / 30% / 60% / 100%)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict

import numpy as np
import pandas as pd

from etf_alpha_engine.indicators import ema, slope, normalize, percentile_rank


@dataclass
class MarketRegimeResult:
    """市场状态引擎结果"""
    market_score: float = 50.0
    market_state: str = "Neutral"
    suggested_exposure: float = 0.3
    # 子维度
    trend_score: float = 0.0
    breadth_score: float = 0.0
    sentiment_score: float = 0.0
    liquidity_score: float = 0.0
    risk_appetite_score: float = 0.0
    # 原始指标
    num_above_ma20: int = 0
    num_above_ma60: int = 0
    total_amount_yi: float = 0.0          # 亿元
    limit_up_count: int = 0
    limit_down_count: int = 0
    zhaban_rate: float = 0.0              # 炸板率%
    count_20cm: int = 0                   # 20cm涨停数
    northbound_net: float = 0.0           # 北向资金净流入(亿)
    etf_amount_yi: float = 0.0
    reasons: list = field(default_factory=list)


class MarketRegimeEngine:
    """市场状态引擎

    可独立运行，输出0-100分数和状态。
    所有子维度独立计算、可复用。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("market_regime", {})
        self.w_trend = self.cfg.get("trend_weight", 0.25)
        self.w_breadth = self.cfg.get("breadth_weight", 0.20)
        self.w_sentiment = self.cfg.get("sentiment_weight", 0.20)
        self.w_liquidity = self.cfg.get("liquidity_weight", 0.15)
        self.w_risk = self.cfg.get("risk_appetite_weight", 0.20)
        self.ma_short = self.cfg.get("ma_short", 20)
        self.ma_long = self.cfg.get("ma_long", 60)
        self.amount_ma = self.cfg.get("amount_ma_period", 20)
        self.exposure_map = self.cfg.get("exposure", {
            "Bull": 1.0, "Recovery": 0.6, "Neutral": 0.3, "Weak": 0.0, "Bear": 0.0
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score(self,
              index_df: Optional[pd.DataFrame],
              market_daily: Optional[pd.DataFrame],
              limit_df: Optional[pd.DataFrame],
              etf_data: Optional[Dict[str, pd.DataFrame]] = None,
              northbound_net: float = 0.0,
              ) -> MarketRegimeResult:
        """计算市场状态

        Parameters
        ----------
        index_df : 沪深300指数日线 [trade_date, close, pct_chg, amount]
        market_daily : 全市场日线快照 [ts_code, trade_date, close, pct_chg, amount]
        limit_df : 涨停数据 [ts_code, ...]
        etf_data : ETF日线字典（用于ETF成交额）
        northbound_net : 北向资金净流入(亿元)
        """
        result = MarketRegimeResult()

        # 1. 趋势分
        trend_s, trend_metrics = self._score_trend(index_df)
        result.trend_score = trend_s

        # 2. 宽度分
        breadth_s, breadth_metrics = self._score_breadth(market_daily)
        result.breadth_score = breadth_s
        result.num_above_ma20 = breadth_metrics.get("above_ma20", 0)
        result.num_above_ma60 = breadth_metrics.get("above_ma60", 0)

        # 3. 情绪分
        sentiment_s, sent_metrics = self._score_sentiment(market_daily, limit_df)
        result.sentiment_score = sentiment_s
        result.limit_up_count = sent_metrics.get("limit_up", 0)
        result.limit_down_count = sent_metrics.get("limit_down", 0)
        result.zhaban_rate = sent_metrics.get("zhaban_rate", 0.0)
        result.count_20cm = sent_metrics.get("count_20cm", 0)

        # 4. 流动性分
        liq_s, liq_metrics = self._score_liquidity(market_daily, etf_data)
        result.liquidity_score = liq_s
        result.total_amount_yi = liq_metrics.get("total_amount", 0.0)
        result.etf_amount_yi = liq_metrics.get("etf_amount", 0.0)

        # 5. 风险偏好分
        risk_s = self._score_risk_appetite(sent_metrics, northbound_net)
        result.risk_appetite_score = risk_s
        result.northbound_net = northbound_net

        # 综合
        final = (
            trend_s * self.w_trend +
            breadth_s * self.w_breadth +
            sentiment_s * self.w_sentiment +
            liq_s * self.w_liquidity +
            risk_s * self.w_risk
        )
        result.market_score = float(np.clip(final, 0.0, 100.0))
        result.market_state = self._classify_state(result.market_score)
        result.suggested_exposure = self.exposure_map.get(result.market_state, 0.0)
        result.reasons = self._build_reasons(result)
        return result

    # ------------------------------------------------------------------
    # 子维度1: 市场趋势
    # ------------------------------------------------------------------
    def _score_trend(self, index_df: Optional[pd.DataFrame]) -> tuple:
        if index_df is None or index_df.empty:
            return 50.0, {}
        df = index_df.sort_values("trade_date")
        close = df["close"].values.astype(float)
        if len(close) < self.ma_long + 5:
            return 50.0, {}

        ma_short = ema(close, self.ma_short)
        ma_long = ema(close, self.ma_long)
        latest = close[-1]

        s = 50.0
        # 均线位置
        if latest > ma_short[-1] > ma_long[-1]:
            s += 25
        elif latest > ma_short[-1]:
            s += 12
        elif latest < ma_short[-1] < ma_long[-1]:
            s -= 25
        elif latest < ma_long[-1]:
            s -= 12

        # 斜率
        sl = slope(close, self.ma_short)
        norm_sl = sl / max(latest, 1e-6) * 10000  # 归一化斜率
        s += float(np.clip(norm_sl * 5, -15, 15))

        # 近20日动量
        if len(close) > 20:
            r20 = close[-1] / close[-21] - 1
            s += float(np.clip(r20 * 200, -15, 15))

        return float(np.clip(s, 0, 100)), {"ma_short": ma_short[-1], "ma_long": ma_long[-1]}

    # ------------------------------------------------------------------
    # 子维度2: 市场宽度
    # ------------------------------------------------------------------
    def _score_breadth(self, market_daily: Optional[pd.DataFrame]) -> tuple:
        if market_daily is None or market_daily.empty:
            return 50.0, {"above_ma20": 0, "above_ma60": 0}

        latest_date = market_daily["trade_date"].max()
        latest = market_daily[market_daily["trade_date"] == latest_date].copy()
        if latest.empty:
            return 50.0, {"above_ma20": 0, "above_ma60": 0}

        # 计算每只股票MA20/MA60
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
        # 宽度评分：站上MA20的占比 + 站上MA60的占比
        s = ratio_20 * 60 + ratio_60 * 40
        return float(np.clip(s * 100, 0, 100)), {
            "above_ma20": above_ma20, "above_ma60": above_ma60, "total": total
        }

    # ------------------------------------------------------------------
    # 子维度3: 市场情绪
    # ------------------------------------------------------------------
    def _score_sentiment(self, market_daily: Optional[pd.DataFrame],
                         limit_df: Optional[pd.DataFrame]) -> tuple:
        metrics = {"limit_up": 0, "limit_down": 0, "zhaban_rate": 0.0, "count_20cm": 0}

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

        # 涨跌停统计（用pct_chg近似）
        limit_up = int(np.sum(pct >= 9.8))
        limit_down = int(np.sum(pct <= -9.8))
        count_20cm = int(np.sum(pct >= 19.8))

        # 涨停数据（更精确）
        if limit_df is not None and not limit_df.empty and "ts_code" in limit_df.columns:
            limit_up = len(limit_df)

        metrics["limit_up"] = limit_up
        metrics["limit_down"] = limit_down
        metrics["count_20cm"] = count_20cm

        # 上涨家数占比
        up_ratio = float(np.sum(pct > 0) / n)
        # 强势股占比（>3%）
        strong_ratio = float(np.sum(pct > 3) / n)

        # 情绪评分
        s = up_ratio * 40 + strong_ratio * 20
        # 涨停加分
        if limit_up > 50:
            s += 20
        elif limit_up > 20:
            s += 12
        elif limit_up > 5:
            s += 6
        # 跌停扣分
        if limit_down > 20:
            s -= 15
        elif limit_down > 5:
            s -= 8
        # 20cm加分（高弹性=风险偏好高）
        s += float(np.clip(count_20cm * 0.5, 0, 10))

        # 炸板率（如果有涨停数据，估算）
        if limit_up + count_20cm > 0:
            # 无炸板池数据时，用limit_down比例近似
            metrics["zhaban_rate"] = float(np.clip(limit_down / max(limit_up + limit_down, 1), 0, 1) * 100)

        return float(np.clip(s, 0, 100)), metrics

    # ------------------------------------------------------------------
    # 子维度4: 流动性
    # ------------------------------------------------------------------
    def _score_liquidity(self, market_daily: Optional[pd.DataFrame],
                        etf_data: Optional[Dict[str, pd.DataFrame]] = None) -> tuple:
        metrics = {"total_amount": 0.0, "etf_amount": 0.0}

        if market_daily is None or market_daily.empty:
            return 50.0, metrics

        latest_date = market_daily["trade_date"].max()
        latest = market_daily[market_daily["trade_date"] == latest_date]
        if latest.empty or "amount" not in latest.columns:
            return 50.0, metrics

        # 全市场成交额（千元->亿元）
        total_amt = float(latest["amount"].sum() / 100000.0)
        metrics["total_amount"] = total_amt

        # ETF成交额
        etf_amt = 0.0
        if etf_data:
            for code, df in etf_data.items():
                if df is not None and not df.empty and "amount" in df.columns:
                    etf_amt += float(df["amount"].iloc[-1] / 100000.0)
        metrics["etf_amount"] = etf_amt

        # 评分：成交额 8000亿=满分，3000亿=中性
        s = float(np.clip((total_amt - 3000) / 5000 * 60 + 40, 0, 100))

        return s, metrics

    # ------------------------------------------------------------------
    # 子维度5: 风险偏好
    # ------------------------------------------------------------------
    def _score_risk_appetite(self, sent_metrics: dict, northbound_net: float) -> float:
        s = 50.0
        limit_up = sent_metrics.get("limit_up", 0)
        count_20cm = sent_metrics.get("count_20cm", 0)

        # 涨停数量高=风险偏好高
        if limit_up > 50:
            s += 20
        elif limit_up > 20:
            s += 10
        elif limit_up < 5:
            s -= 10

        # 20cm数量
        s += float(np.clip(count_20cm, 0, 15))

        # 北向资金（亿元）
        if northbound_net > 50:
            s += 15
        elif northbound_net > 0:
            s += 5
        elif northbound_net < -50:
            s -= 15
        elif northbound_net < 0:
            s -= 5

        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 状态分类
    # ------------------------------------------------------------------
    def _classify_state(self, score: float) -> str:
        bull_t = self.cfg.get("bull_threshold", 75)
        rec_t = self.cfg.get("recovery_threshold", 60)
        neu_t = self.cfg.get("neutral_threshold", 45)
        weak_t = self.cfg.get("weak_threshold", 30)
        if score >= bull_t:
            return "Bull"
        if score >= rec_t:
            return "Recovery"
        if score >= neu_t:
            return "Neutral"
        if score >= weak_t:
            return "Weak"
        return "Bear"

    def _build_reasons(self, r: MarketRegimeResult) -> list:
        parts = []
        if r.trend_score >= 70:
            parts.append("市场趋势向上")
        elif r.trend_score <= 35:
            parts.append("市场趋势向下")
        if r.breadth_score >= 70:
            parts.append(f"宽度强({r.num_above_ma20}家站上MA20)")
        elif r.breadth_score <= 35:
            parts.append("宽度弱")
        if r.sentiment_score >= 70:
            parts.append(f"情绪高涨(涨停{r.limit_up_count})")
        elif r.sentiment_score <= 35:
            parts.append("情绪低迷")
        if r.liquidity_score >= 70:
            parts.append(f"流动性充沛({r.total_amount_yi:.0f}亿)")
        if r.risk_appetite_score >= 70:
            parts.append("风险偏好高")
        elif r.risk_appetite_score <= 35:
            parts.append("风险偏好低")
        if r.northbound_net > 0:
            parts.append(f"北向净流入{r.northbound_net:.1f}亿")
        elif r.northbound_net < 0:
            parts.append(f"北向净流出{abs(r.northbound_net):.1f}亿")
        return parts or ["市场中性"]
