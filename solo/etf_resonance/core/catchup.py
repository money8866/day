"""Catchup Elasticity Scorer - 补涨弹性评分器。

策略核心：
  ETF 趋势已形成 → 龙头已涨 → 寻找成份股中相对滞涨但有补涨潜力的标的

评分维度：
  1. 相对滞涨度 (30%): 股票相对ETF的落后程度，落后越多补涨空间越大
  2. 底部启动信号 (25%): 刚突破短期均线、量能开始放大、MACD金叉
  3. 补涨弹性 (20%): 历史Beta高、波动率大、前期涨幅低
  4. 资金承接 (15%): 近期量比放大、北向/融资承接
  5. 估值安全垫 (10%): 距60日低点近、距60日高点远

CatchupScore: 0-100，越高代表补涨潜力越大
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
    lag_degree: float             # 相对滞涨度
    startup_signal: float         # 底部启动信号
    elasticity: float             # 补涨弹性
    capital_inflow: float         # 资金承接
    valuation_safety: float       # 估值安全垫
    # 诊断字段
    ret_60d: float                # 股票60日涨幅
    etf_ret_60d: float            # ETF60日涨幅
    ret_gap: float                # 涨幅差 (ETF - 股票，正=落后)
    dist_to_low: float            # 距60日低点
    dist_to_high: float           # 距60日高点
    vol_ratio_5d: float           # 5日量比
    beta: float                   # 相对ETF的Beta


class CatchupScorer:
    """补涨弹性评分器：寻找ETF趋势形成后成份股中的补涨机会。"""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("catchup", {}) if config else {}
        self.lag_w = cfg.get("lag_weight", 0.30)
        self.startup_w = cfg.get("startup_weight", 0.25)
        self.elastic_w = cfg.get("elasticity_weight", 0.20)
        self.capital_w = cfg.get("capital_weight", 0.15)
        self.safety_w = cfg.get("safety_weight", 0.10)
        self.period = cfg.get("relative_period", 60)

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

            # Align lengths
            min_len = min(len(close), len(etf_close))
            close = close[-min_len:]
            etf_c = etf_close[-min_len:]
            high = high[-min_len:]
            low = low[-min_len:]
            vol = vol[-min_len:]

            lookback = min(P, len(close))
            if lookback < 30:
                return None

            c = close[-lookback:]
            ec = etf_c[-lookback:]

            # === 1. 相对滞涨度 (lag_degree) ===
            stock_ret_60d = (c[-1] / c[0] - 1) * 100 if c[0] > 0 else 0
            etf_ret_60d = (ec[-1] / ec[0] - 1) * 100 if ec[0] > 0 else 0
            ret_gap = etf_ret_60d - stock_ret_60d

            stock_ret_20d = (close[-1] / close[-min(20, len(close))] - 1) * 100
            etf_ret_20d = (etf_c[-1] / etf_c[-min(20, len(etf_c))] - 1) * 100
            ret_gap_20d = etf_ret_20d - stock_ret_20d

            lag_raw = max(0, ret_gap) * 1.5 + max(0, ret_gap_20d) * 0.5
            lag_degree = min(100, lag_raw * 2)

            # === 2. 底部启动信号 (startup_signal) ===
            score_startup = 0

            if len(close) >= 20:
                ma20_arr = pd.Series(close).rolling(20).mean().values
                if not np.isnan(ma20_arr[-1]):
                    if close[-1] > ma20_arr[-1]:
                        score_startup += 25
                    if len(ma20_arr) >= 5 and not np.isnan(ma20_arr[-5]) and ma20_arr[-1] > ma20_arr[-5]:
                        score_startup += 15

            if len(close) >= 10:
                ma5_arr = pd.Series(close).rolling(5).mean().values
                ma10_arr = pd.Series(close).rolling(10).mean().values
                if (len(ma5_arr) >= 2 and len(ma10_arr) >= 2 and
                    not np.isnan(ma5_arr[-1]) and not np.isnan(ma10_arr[-1]) and
                    not np.isnan(ma5_arr[-2]) and not np.isnan(ma10_arr[-2])):
                    if ma5_arr[-1] > ma10_arr[-1] and ma5_arr[-2] <= ma10_arr[-2]:
                        score_startup += 20

            if len(vol) >= 20:
                vol_ma5 = np.mean(vol[-5:])
                vol_ma20 = np.mean(vol[-20:])
                if vol_ma20 > 0:
                    vol_ratio = vol_ma5 / vol_ma20
                    if vol_ratio > 1.2:
                        score_startup += 20
                    elif vol_ratio > 1.0:
                        score_startup += 10

            if len(close) >= 2:
                daily_ret = (close[-1] / close[-2] - 1) * 100
                if 2 <= daily_ret <= 7:
                    score_startup += 20
                elif 0 < daily_ret < 2:
                    score_startup += 10

            startup_signal = min(100, score_startup)

            # === 3. 补涨弹性 (elasticity) ===
            score_elastic = 0

            # 注意：close 和 etf_c 已对齐到相同长度（min_len）
            # 但 c 和 ec 是 lookback 切片，需要用对齐后的数组算 daily_ret
            stock_daily_ret = np.diff(c) / c[:-1]
            etf_daily_ret = np.diff(ec) / ec[:-1]
            min_len_ret = min(len(stock_daily_ret), len(etf_daily_ret))

            beta = 1.0
            corr = 0.5
            if min_len_ret >= 20:
                # 用 try 防止 rolling_beta/rolling_corr 内部异常
                try:
                    beta_arr = rolling_beta(stock_daily_ret[-min_len_ret:],
                                            etf_daily_ret[-min_len_ret:], min(60, min_len_ret))
                    beta = float(beta_arr[-1]) if not np.isnan(beta_arr[-1]) else 1.0
                except Exception:
                    beta = 1.0
                try:
                    corr_arr = rolling_corr(stock_daily_ret[-min_len_ret:],
                                            etf_daily_ret[-min_len_ret:], min(20, min_len_ret))
                    corr = float(corr_arr[-1]) if not np.isnan(corr_arr[-1]) else 0.5
                except Exception:
                    corr = 0.5

            if beta > 1.5:
                score_elastic += 40
            elif beta > 1.2:
                score_elastic += 30
            elif beta > 1.0:
                score_elastic += 20
            else:
                score_elastic += 10

            if len(close) >= 20 and len(high) >= 20 and len(low) >= 20:
                try:
                    # 简单 ATR 计算：14日TR均值
                    tr_list = []
                    for i in range(-14, 0):
                        if i == -14:
                            tr = high[i] - low[i]
                        else:
                            tr = max(high[i] - low[i],
                                     abs(high[i] - close[i-1]),
                                     abs(low[i] - close[i-1]))
                        tr_list.append(tr)
                    atr_val = np.mean(tr_list) if tr_list else 0
                    if close[-1] > 0:
                        atr_pct = atr_val / close[-1] * 100
                        if atr_pct > 4:
                            score_elastic += 30
                        elif atr_pct > 2.5:
                            score_elastic += 20
                        else:
                            score_elastic += 10
                    else:
                        score_elastic += 10
                except Exception:
                    score_elastic += 10
            else:
                score_elastic += 10

            if corr > 0.7:
                score_elastic += 30
            elif corr > 0.5:
                score_elastic += 20
            else:
                score_elastic += 5

            elasticity = min(100, score_elastic)

            # === 4. 资金承接 (capital_inflow) ===
            score_capital = 0

            if len(vol) >= 20:
                vol_ma5 = np.mean(vol[-5:])
                vol_ma20 = np.mean(vol[-20:])
                vol_ratio_5d = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
                if vol_ratio_5d > 1.5:
                    score_capital += 40
                elif vol_ratio_5d > 1.2:
                    score_capital += 30
                elif vol_ratio_5d > 1.0:
                    score_capital += 20
                else:
                    score_capital += 5
            else:
                vol_ratio_5d = 1.0

            if len(close) >= 4 and len(vol) >= 4:
                inflow_days = 0
                for i in range(-3, 0):
                    if close[i] > close[i - 1] and vol[i] > vol[i - 1]:
                        inflow_days += 1
                score_capital += inflow_days * 20

            capital_inflow = min(100, score_capital)

            # === 5. 估值安全垫 (valuation_safety) ===
            score_safety = 0

            low_60 = np.min(low[-60:]) if len(low) >= 60 else np.min(low)
            high_60 = np.max(high[-60:]) if len(high) >= 60 else np.max(high)
            dist_to_low = (close[-1] / low_60 - 1) * 100 if low_60 > 0 else 0
            dist_to_high = (close[-1] / high_60 - 1) * 100 if high_60 > 0 else 0

            if dist_to_low < 10:
                score_safety += 50
            elif dist_to_low < 20:
                score_safety += 35
            elif dist_to_low < 30:
                score_safety += 20
            else:
                score_safety += 5

            if dist_to_high < -20:
                score_safety += 50
            elif dist_to_high < -10:
                score_safety += 35
            elif dist_to_high < 0:
                score_safety += 20
            else:
                score_safety += 5

            valuation_safety = min(100, score_safety)

            # === 综合评分 ===
            catchup_score = (
                lag_degree * self.lag_w +
                startup_signal * self.startup_w +
                elasticity * self.elastic_w +
                capital_inflow * self.capital_w +
                valuation_safety * self.safety_w
            )

            return CatchupResult(
                ts_code=stock_code,
                name=stock_code,
                etf_code=etf_code,
                catchup_score=round(float(catchup_score), 2),
                lag_degree=round(float(lag_degree), 2),
                startup_signal=round(float(startup_signal), 2),
                elasticity=round(float(elasticity), 2),
                capital_inflow=round(float(capital_inflow), 2),
                valuation_safety=round(float(valuation_safety), 2),
                ret_60d=round(float(stock_ret_60d), 2),
                etf_ret_60d=round(float(etf_ret_60d), 2),
                ret_gap=round(float(ret_gap), 2),
                dist_to_low=round(float(dist_to_low), 2),
                dist_to_high=round(float(dist_to_high), 2),
                vol_ratio_5d=round(float(vol_ratio_5d), 2),
                beta=round(float(beta), 2),
            )

        except Exception as e:
            print(f"      [Catchup Error] {stock_code}: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None
