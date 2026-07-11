"""Catchup Elasticity Scorer - 补涨弹性评分器。

策略核心：
  ETF 多头趋势已形成 -> 寻找成份股中刚成立多头趋势、量能温和放大、
  涨幅不大且未连续涨停的标的。这类股票处于补涨初期，安全且空间大。

评分维度：
  1. 多头趋势刚成立 (30%): EMA20刚上穿EMA60、收盘刚站上MA20、MA20拐头向上
  2. 量能温和放大 (25%): 量比在1.2-2.0区间，温和而非暴量追高
  3. 涨幅适中 (20%): 60日涨幅0-20%为佳，没涨过太多才有补涨空间
  4. 未连续涨停 (15%): 近5日涨停天数0-1天，非投机炒作
  5. 补涨空间 (10%): ETF上涨而股票涨幅相对不大，存在合理补涨空间

CatchupScore: 0-100，越高代表补涨潜力越大

---

Momentum Leader Scorer - 强势前排评分器（趋势延续维度）。

策略核心：
  板块大涨日，资金优先涌入创新高/近期强势的龙头股，而非超跌滞涨股。
  本评分器与补涨评分并行运行，捕捉"趋势延续/强势前排"标的。

评分维度：
  1. 创新高/接近新高 (30%): 距60日高点越近越好，创新高满分
  2. 60日趋势向上 (25%): 60日涨幅为正且分档，>50%满分
  3. 20日动量 (20%): 近20日涨幅，>15%满分
  4. 今日涨幅 (15%): 今日涨幅居前，>9%满分
  5. 量能放大 (10%): 量比>2.0满分

MomentumScore: 0-100，越高代表强势前排特征越显著
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass

from etf_resonance.utils.indicators import (
    ema, slope, atr, rolling_beta, rolling_corr,
    rank_score, normalize,
)
from etf_resonance.utils.helpers import safe_div, Config


@dataclass
class CatchupResult:
    """Per-stock catchup scoring result."""
    ts_code: str
    name: str
    etf_code: str
    catchup_score: float          # 0-100 composite
    trend_setup: float            # 多头趋势刚成立
    vol_gentle: float             # 量能温和放大
    gain_moderate: float          # 涨幅适中
    no_limit_up: float            # 未连续涨停
    catchup_space: float          # 补涨空间
    # 诊断字段
    ret_60d: float                # 股票60日涨幅
    etf_ret_60d: float            # ETF60日涨幅
    ret_gap: float                # 涨幅差 (ETF - 股票，正=有补涨空间)
    dist_to_low: float            # 距60日低点
    dist_to_high: float           # 距60日高点
    vol_ratio_5d: float           # 5日量比
    limit_up_5d: int              # 近5日涨停天数
    ma_cross_days: int            # EMA20上穿EMA60天数(0=未交叉)


@dataclass
class MomentumResult:
    """Per-stock momentum leader scoring result."""
    ts_code: str
    name: str
    etf_code: str
    momentum_score: float         # 0-100 composite
    new_high_score: float         # 创新高/接近新高
    trend_60d_score: float        # 60日趋势向上
    mom_20d_score: float          # 20日动量
    today_surge_score: float       # 今日涨幅
    vol_surge_score: float        # 量能放大
    # 诊断字段
    ret_60d: float                # 股票60日涨幅
    ret_20d: float                # 股票20日涨幅
    today_pct: float              # 今日涨幅
    dist_to_high: float           # 距60日高点
    vol_ratio_5d: float           # 5日量比


class CatchupScorer:
    """补涨弹性评分器：寻找ETF趋势形成后成份股中的补涨机会。"""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("catchup", {}) if config else {}
        self.trend_w = cfg.get("trend_weight", 0.30)
        self.vol_w = cfg.get("vol_weight", 0.25)
        self.gain_w = cfg.get("gain_weight", 0.20)
        self.limit_w = cfg.get("limit_weight", 0.15)
        self.space_w = cfg.get("space_weight", 0.10)
        self.period = cfg.get("relative_period", 60)
        self.max_ret_60d = cfg.get("max_ret_60d", 30.0)

    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              etf_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]],
              etf_trend_scores: Dict[str, float]
              ) -> Dict[str, List[CatchupResult]]:
        """Score all stocks for catchup potential."""
        results: Dict[str, List[CatchupResult]] = {}

        for etf_code, stock_codes in constituents.items():
            if etf_code not in etf_data:
                continue
            etf_df = etf_data[etf_code]
            etf_close = etf_df["close"].values.astype(np.float64)

            stock_results = []
            for stock_code in stock_codes:
                if stock_code not in stock_data:
                    continue
                stock_df = stock_data[stock_code]
                if stock_df.empty or len(stock_df) < 60:
                    continue

                result = self._score_single(
                    stock_code, stock_df, etf_close, etf_code
                )
                if result is not None:
                    stock_results.append(result)

            if stock_results:
                stock_results.sort(key=lambda x: -x.catchup_score)
                results[etf_code] = stock_results

        return results

    def _score_single(self, stock_code: str, stock_df: pd.DataFrame,
                      etf_close: np.ndarray, etf_code: str) -> Optional[CatchupResult]:
        """Score a single stock for catchup potential."""
        import traceback
        try:
            P = self.period
            close = stock_df["close"].values.astype(np.float64)
            high = stock_df["high"].values.astype(np.float64)
            low = stock_df["low"].values.astype(np.float64)
            vol = stock_df["vol"].values.astype(np.float64)
            open_p = stock_df["open"].values.astype(np.float64) if "open" in stock_df.columns else close.copy()

            # Align lengths
            min_len = min(len(close), len(etf_close))
            close = close[-min_len:]
            etf_c = etf_close[-min_len:]
            high = high[-min_len:]
            low = low[-min_len:]
            vol = vol[-min_len:]
            open_p = open_p[-min_len:]

            lookback = min(P, len(close))
            if lookback < 30:
                return None

            c = close[-lookback:]
            ec = etf_c[-lookback:]

            # === 诊断字段 ===
            stock_ret_60d = (c[-1] / c[0] - 1) * 100 if c[0] > 0 else 0
            etf_ret_60d = (ec[-1] / ec[0] - 1) * 100 if ec[0] > 0 else 0
            ret_gap = etf_ret_60d - stock_ret_60d

            low_60 = np.min(low[-60:]) if len(low) >= 60 else np.min(low)
            high_60 = np.max(high[-60:]) if len(high) >= 60 else np.max(high)
            dist_to_low = (close[-1] / low_60 - 1) * 100 if low_60 > 0 else 0
            dist_to_high = (close[-1] / high_60 - 1) * 100 if high_60 > 0 else 0

            # === 1. 多头趋势刚成立 (trend_setup) - 30% ===
            score_trend = 0
            ma_cross_days = 0

            # EMA20 / EMA60
            ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values
            ema60 = pd.Series(close).ewm(span=60, adjust=False).mean().values

            # 检查EMA20是否在近10天内上穿EMA60
            if len(ema20) >= 12 and len(ema60) >= 12:
                cross_found = False
                for d in range(1, 11):
                    idx = -d
                    if (not np.isnan(ema20[idx]) and not np.isnan(ema60[idx]) and
                        not np.isnan(ema20[idx - 1]) and not np.isnan(ema60[idx - 1])):
                        if ema20[idx] > ema60[idx] and ema20[idx - 1] <= ema60[idx - 1]:
                            ma_cross_days = d
                            cross_found = True
                            break
                if cross_found:
                    if ma_cross_days <= 3:
                        score_trend += 45
                    elif ma_cross_days <= 5:
                        score_trend += 35
                    elif ma_cross_days <= 10:
                        score_trend += 25

            # EMA20 > EMA60（多头排列，即使非刚交叉也给分）
            if (len(ema20) >= 1 and len(ema60) >= 1 and
                not np.isnan(ema20[-1]) and not np.isnan(ema60[-1]) and
                ema20[-1] > ema60[-1]):
                score_trend += 15

            # 收盘站上MA20
            if len(close) >= 20:
                ma20 = pd.Series(close).rolling(20).mean().values
                if not np.isnan(ma20[-1]) and close[-1] > ma20[-1]:
                    score_trend += 20
                    # MA20拐头向上
                    if len(ma20) >= 5 and not np.isnan(ma20[-5]) and ma20[-1] > ma20[-5]:
                        score_trend += 20

            trend_setup = min(100, score_trend)

            # === 2. 量能温和放大 (vol_gentle) - 25% ===
            score_vol = 0
            vol_ratio_5d = 1.0

            if len(vol) >= 20:
                vol_ma5 = np.mean(vol[-5:])
                vol_ma20 = np.mean(vol[-20:])
                vol_ratio_5d = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
                # 温和放大：1.2-2.0区间满分，暴量(>3)或缩量(<1)扣分
                if 1.2 <= vol_ratio_5d <= 2.0:
                    score_vol = 100
                elif 1.0 <= vol_ratio_5d < 1.2:
                    score_vol = 60
                elif 2.0 < vol_ratio_5d <= 2.5:
                    score_vol = 60
                elif 2.5 < vol_ratio_5d <= 3.0:
                    score_vol = 35
                elif vol_ratio_5d > 3.0:
                    score_vol = 15
                else:
                    score_vol = 20

            vol_gentle = min(100, score_vol)

            # === 3. 涨幅适中 (gain_moderate) - 20% ===
            score_gain = 0
            # 60日涨幅0-20%为佳，负或过大扣分
            if 0 <= stock_ret_60d <= 10:
                score_gain = 100
            elif 10 < stock_ret_60d <= 20:
                score_gain = 85
            elif -5 <= stock_ret_60d < 0:
                score_gain = 75
            elif 20 < stock_ret_60d <= 30:
                score_gain = 60
            elif -10 <= stock_ret_60d < -5:
                score_gain = 55
            elif 30 < stock_ret_60d <= 40:
                score_gain = 40
            elif stock_ret_60d > 40:
                score_gain = 15
            else:
                score_gain = 25

            gain_moderate = min(100, score_gain)

            # 回测验证：60日涨幅>30%补涨动能衰竭(50%胜率)，直接过滤
            if stock_ret_60d > self.max_ret_60d:
                return None

            # === 4. 未连续涨停 (no_limit_up) - 15% ===
            limit_up_5d = 0
            if len(close) >= 6 and len(open_p) >= 6:
                for i in range(-5, 0):
                    daily_pct = (close[i] / close[i - 1] - 1) * 100 if close[i - 1] > 0 else 0
                    if daily_pct >= 9.5:
                        limit_up_5d += 1

            score_limit = 0
            if limit_up_5d == 0:
                score_limit = 100
            elif limit_up_5d == 1:
                score_limit = 70
            elif limit_up_5d == 2:
                score_limit = 35
            else:
                score_limit = 5

            no_limit_up = min(100, score_limit)

            # === 5. 补涨空间 (catchup_space) - 10% ===
            score_space = 0
            # ETF上涨而股票涨幅相对不大，存在合理补涨空间
            if ret_gap >= 15:
                score_space = 100
            elif ret_gap >= 8:
                score_space = 80
            elif ret_gap >= 3:
                score_space = 60
            elif ret_gap >= 0:
                score_space = 40
            else:
                score_space = 10

            catchup_space = min(100, score_space)

            # === 综合评分 ===
            catchup_score = (
                trend_setup * self.trend_w +
                vol_gentle * self.vol_w +
                gain_moderate * self.gain_w +
                no_limit_up * self.limit_w +
                catchup_space * self.space_w
            )

            return CatchupResult(
                ts_code=stock_code,
                name=stock_code,
                etf_code=etf_code,
                catchup_score=round(float(catchup_score), 2),
                trend_setup=round(float(trend_setup), 2),
                vol_gentle=round(float(vol_gentle), 2),
                gain_moderate=round(float(gain_moderate), 2),
                no_limit_up=round(float(no_limit_up), 2),
                catchup_space=round(float(catchup_space), 2),
                ret_60d=round(float(stock_ret_60d), 2),
                etf_ret_60d=round(float(etf_ret_60d), 2),
                ret_gap=round(float(ret_gap), 2),
                dist_to_low=round(float(dist_to_low), 2),
                dist_to_high=round(float(dist_to_high), 2),
                vol_ratio_5d=round(float(vol_ratio_5d), 2),
                limit_up_5d=int(limit_up_5d),
                ma_cross_days=int(ma_cross_days),
            )

        except Exception as e:
            print(f"      [Catchup Error] {stock_code}: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None


class MomentumScorer:
    """强势前排评分器：捕捉板块大涨日中创新高/趋势延续的领涨股。"""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("momentum", {}) if config else {}
        self.new_high_w = cfg.get("new_high_weight", 0.30)
        self.trend_60d_w = cfg.get("trend_60d_weight", 0.25)
        self.mom_20d_w = cfg.get("mom_20d_weight", 0.20)
        self.today_w = cfg.get("today_weight", 0.15)
        self.vol_w = cfg.get("vol_weight", 0.10)
        self.period = cfg.get("relative_period", 60)

    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]]
              ) -> Dict[str, List[MomentumResult]]:
        """Score all stocks for momentum leader potential."""
        results: Dict[str, List[MomentumResult]] = {}

        for etf_code, stock_codes in constituents.items():
            stock_results = []
            for stock_code in stock_codes:
                if stock_code not in stock_data:
                    continue
                stock_df = stock_data[stock_code]
                if stock_df.empty or len(stock_df) < 30:
                    continue

                result = self._score_single(stock_code, stock_df, etf_code)
                if result is not None:
                    stock_results.append(result)

            if stock_results:
                stock_results.sort(key=lambda x: -x.momentum_score)
                results[etf_code] = stock_results

        return results

    def _score_single(self, stock_code: str, stock_df: pd.DataFrame,
                      etf_code: str) -> Optional[MomentumResult]:
        """Score a single stock for momentum leader potential."""
        import traceback
        try:
            P = self.period
            close = stock_df["close"].values.astype(np.float64)
            high = stock_df["high"].values.astype(np.float64)
            low = stock_df["low"].values.astype(np.float64)
            vol = stock_df["vol"].values.astype(np.float64)

            lookback = min(P, len(close))
            if lookback < 30:
                return None

            c = close[-lookback:]
            high_60 = high[-lookback:]
            low_60 = low[-lookback:]

            # === 诊断字段 ===
            today_close = float(close[-1])
            high_max = float(np.max(high_60))
            low_min = float(np.min(low_60))

            dist_to_high = (today_close / high_max - 1) * 100 if high_max > 0 else 0

            ret_60d = (close[-1] / c[0] - 1) * 100 if c[0] > 0 else 0
            ret_20d = (close[-1] / close[-min(20, len(close))] - 1) * 100 \
                if len(close) >= 20 and close[-20] > 0 else 0
            today_pct = (close[-1] / close[-2] - 1) * 100 \
                if len(close) >= 2 and close[-2] > 0 else 0

            # === 1. 创新高/接近新高 (new_high_score) - 30% ===
            score_new_high = 0
            if dist_to_high >= 0:
                score_new_high = 100
            elif dist_to_high >= -2:
                score_new_high = 90
            elif dist_to_high >= -5:
                score_new_high = 75
            elif dist_to_high >= -10:
                score_new_high = 55
            elif dist_to_high >= -20:
                score_new_high = 30
            else:
                score_new_high = 5

            # === 2. 60日趋势向上 (trend_60d_score) - 25% ===
            score_trend_60d = 0
            if ret_60d >= 50:
                score_trend_60d = 100
            elif ret_60d >= 30:
                score_trend_60d = 85
            elif ret_60d >= 15:
                score_trend_60d = 70
            elif ret_60d >= 5:
                score_trend_60d = 50
            elif ret_60d >= 0:
                score_trend_60d = 30
            else:
                score_trend_60d = 5

            # === 3. 20日动量 (mom_20d_score) - 20% ===
            score_mom_20d = 0
            if ret_20d >= 15:
                score_mom_20d = 100
            elif ret_20d >= 10:
                score_mom_20d = 85
            elif ret_20d >= 5:
                score_mom_20d = 65
            elif ret_20d >= 0:
                score_mom_20d = 40
            elif ret_20d >= -5:
                score_mom_20d = 20
            else:
                score_mom_20d = 5

            # === 4. 今日涨幅 (today_surge_score) - 15% ===
            score_today = 0
            if today_pct >= 9:
                score_today = 100
            elif today_pct >= 7:
                score_today = 85
            elif today_pct >= 5:
                score_today = 70
            elif today_pct >= 3:
                score_today = 50
            elif today_pct >= 0:
                score_today = 25
            else:
                score_today = 5

            # === 5. 量能放大 (vol_surge_score) - 10% ===
            score_vol = 0
            vol_ratio_5d = 1.0
            if len(vol) >= 20:
                vol_ma5 = np.mean(vol[-5:])
                vol_ma20 = np.mean(vol[-20:])
                vol_ratio_5d = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
                if vol_ratio_5d > 2.0:
                    score_vol = 100
                elif vol_ratio_5d > 1.5:
                    score_vol = 80
                elif vol_ratio_5d > 1.2:
                    score_vol = 60
                elif vol_ratio_5d > 1.0:
                    score_vol = 35
                else:
                    score_vol = 10

            # === 综合评分 ===
            momentum_score = (
                score_new_high * self.new_high_w +
                score_trend_60d * self.trend_60d_w +
                score_mom_20d * self.mom_20d_w +
                score_today * self.today_w +
                score_vol * self.vol_w
            )

            return MomentumResult(
                ts_code=stock_code,
                name=stock_code,
                etf_code=etf_code,
                momentum_score=round(float(momentum_score), 2),
                new_high_score=round(float(score_new_high), 2),
                trend_60d_score=round(float(score_trend_60d), 2),
                mom_20d_score=round(float(score_mom_20d), 2),
                today_surge_score=round(float(score_today), 2),
                vol_surge_score=round(float(score_vol), 2),
                ret_60d=round(float(ret_60d), 2),
                ret_20d=round(float(ret_20d), 2),
                today_pct=round(float(today_pct), 2),
                dist_to_high=round(float(dist_to_high), 2),
                vol_ratio_5d=round(float(vol_ratio_5d), 2),
            )

        except Exception as e:
            print(f"      [Momentum Error] {stock_code}: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None
