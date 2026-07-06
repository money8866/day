"""补涨弹性发现引擎 — 在强势ETF中挖掘低吸补涨个股。

核心逻辑：不追已涨高的龙头，而是在强势ETF的成份股中
找到涨幅落后于ETF但有补涨潜力的个股（低吸补涨弹性）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, rma, atr, adx, rsi, macd, macd_hist, bollinger, kdj,
    rank_score, normalize, zscore, winsorize,
    max_drawdown, sharpe_ratio, calmar_ratio,
    rolling_corr, beta as rolling_beta,
    new_high_count, consecutive_up_days, above_ema_days,
    volume_ratio, slope, hurst_exponent, natr,
)


@dataclass
class LeaderResult:
    ts_code: str
    etf_code: str
    stock_name: str = ""
    # 补涨弹性维度
    lag_factor_score: float = 0.0          # 相对滞后度：落后ETF越多分越高
    bottom_stability_score: float = 0.0    # 底部企稳度：站上MA20且趋势向上
    volume_initiation_score: float = 0.0   # 量能启动度：量比从低位回升
    pullback_depth_score: float = 0.0      # 回调深度：适度回调5%-15%
    rsi_position_score: float = 0.0        # RSI位置：35-55区间得分高
    etf_transfer_score: float = 0.0        # ETF强度传导：ETF涨幅-个股涨幅
    vol_contraction_score: float = 0.0     # 波动率收缩：蓄势待发
    correlation_etf_score: float = 0.0     # 与ETF相关性：需正相关确保跟涨
    liquidity_score: float = 0.0           # 流动性
    ma_convergence_score: float = 0.0      # 均线收敛度：MA5向MA10靠拢
    leader_score: float = 0.0


class LeaderDiscoveryEngine:
    """补涨弹性发现引擎。

    在强势ETF的成份股中，寻找涨幅落后于ETF但有补涨潜力的个股。
    核心思路：ETF已涨但个股未涨 → 补涨空间大 → 低吸入场。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('leader', {})
        self._log_config()

    def _log_config(self) -> None:
        logger.debug(f"LeaderDiscoveryEngine config: "
                     f"relative_period={self.cfg.get('relative_period', 60)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              etf_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]],
              etf_trend_scores: Dict[str, float],
              benchmark_close: Optional[pd.Series] = None,
              ) -> Dict[str, List[LeaderResult]]:
        """对每个强势ETF的成分股进行龙头评分。

        Parameters
        ----------
        stock_data : dict
            {ts_code: DataFrame}，每只个股的日线数据，需含列
            [trade_date, open, high, low, close, vol]。
        etf_data : dict
            {ts_code: DataFrame}，ETF日线数据。
        constituents : dict
            {etf_code: [ts_code, ...]}，ETF的成分股列表。
        etf_trend_scores : dict
            {etf_code: score}，Module 1输出的ETF趋势得分（0-100）。
        benchmark_close : pd.Series, optional
            市场基准（沪深300）收盘价序列。

        Returns
        -------
        dict[str, list[LeaderResult]]
            {etf_code: [LeaderResult, ...]}，按 leader_score 降序排列。
            仅包含 correlation_etf >= min_correlation 的个股。
        """
        if not stock_data or not constituents:
            logger.warning("stock_data or constituents is empty, returning {}")
            return {}

        relative_period = self.cfg.get('relative_period', 60)
        min_corr = self.cfg.get('min_correlation', 0.3)
        lookback = max(relative_period, 60) + 5

        results: Dict[str, List[LeaderResult]] = {}

        for etf_code, members in constituents.items():
            if not members:
                continue

            etf_trend = etf_trend_scores.get(etf_code, 0.0)
            if etf_trend < 50.0:
                continue

            etf_df = etf_data.get(etf_code)
            if etf_df is None or len(etf_df) < lookback:
                logger.debug(f"Skipping ETF {etf_code}: insufficient ETF data")
                continue

            logger.debug(f"Scoring leaders for ETF {etf_code} ({len(members)} constituents)")

            try:
                etf_close_arr = np.asarray(etf_df['close'].values, dtype=np.float64)
                etf_vol_arr = np.asarray(etf_df['vol'].values, dtype=np.float64)
                etf_high_arr = np.asarray(etf_df['high'].values, dtype=np.float64)
                etf_low_arr = np.asarray(etf_df['low'].values, dtype=np.float64)
                etf_dates = etf_df['trade_date'].values if 'trade_date' in etf_df.columns else None
            except Exception as exc:
                logger.error(f"Error reading ETF {etf_code} data: {exc}")
                continue

            # Prepare benchmark returns if available
            bench_returns = None
            if benchmark_close is not None and len(benchmark_close) > 1:
                bc = np.asarray(benchmark_close, dtype=np.float64)
                bench_returns = (bc[1:] - bc[:-1]) / np.maximum(bc[:-1], 1e-10)
                bench_returns = np.concatenate([[0.0], bench_returns])

            etf_returns = np.diff(etf_close_arr, prepend=etf_close_arr[0:1]) / np.maximum(
                np.roll(etf_close_arr, 1), 1e-10)
            etf_returns[0] = 0.0

            etf_vol_ratio = volume_ratio(etf_vol_arr, 20)
            etf_dd = max_drawdown(etf_close_arr)

            # Compute ETF high proximity
            etf_high_60 = pd.Series(etf_high_arr).rolling(60, min_periods=1).max().values
            etf_high_prox = (etf_close_arr[-1] / max(etf_high_60[-1], 1e-10) - 1.0) * 100.0

            stock_metrics_list: List[dict] = []

            for ts_code in members:
                sdf = stock_data.get(ts_code)
                if sdf is None or len(sdf) < lookback:
                    continue
                if 'close' not in sdf.columns:
                    continue

                try:
                    metrics = self._compute_stock_metrics(
                        ts_code, etf_code, sdf,
                        etf_close_arr, etf_returns, etf_vol_arr, etf_vol_ratio,
                        etf_high_prox, etf_dd,
                        etf_close_arr, etf_high_arr, etf_low_arr,
                        etf_dates,
                        bench_returns,
                        relative_period,
                    )
                    if metrics is not None:
                        stock_metrics_list.append(metrics)
                except Exception as exc:
                    logger.debug(f"Error computing leader metrics for {ts_code}: {exc}")
                    continue

            if not stock_metrics_list:
                logger.debug(f"No leaders found for ETF {etf_code}")
                continue

            # Cross-sectional normalization within ETF — 补涨弹性评分
            n_stocks = len(stock_metrics_list)

            # 提取原始指标
            lag_v = np.array([m['lag_raw'] for m in stock_metrics_list], dtype=np.float64)
            bs_v = np.array([m['bs_raw'] for m in stock_metrics_list], dtype=np.float64)
            vi_v = np.array([m['vi_raw'] for m in stock_metrics_list], dtype=np.float64)
            pd_v = np.array([m['pd_raw'] for m in stock_metrics_list], dtype=np.float64)
            rsi_v = np.array([m['rsi_pos_raw'] for m in stock_metrics_list], dtype=np.float64)
            et_v = np.array([m['etf_lag_raw'] for m in stock_metrics_list], dtype=np.float64)
            vc_v = np.array([m['vc_raw'] for m in stock_metrics_list], dtype=np.float64)
            corr_v = np.array([m['corr_raw'] for m in stock_metrics_list], dtype=np.float64)
            liq_v = np.array([m['liq_raw'] for m in stock_metrics_list], dtype=np.float64)
            ma_v = np.array([m['ma_conv_raw'] for m in stock_metrics_list], dtype=np.float64)

            # 标准化为0-100分
            lag_s = self._to_score(winsorize(lag_v, 0.01))
            bs_s = self._to_score(winsorize(bs_v, 0.01))
            vi_s = self._to_score(winsorize(vi_v, 0.01))
            pd_s = self._to_score(winsorize(pd_v, 0.01))
            rsi_s = self._to_score(winsorize(rsi_v, 0.01))
            et_s = self._to_score(winsorize(et_v, 0.01))
            vc_s = self._to_score(winsorize(vc_v, 0.01))
            corr_s = self._to_score(winsorize(corr_v, 0.01))
            liq_s = self._to_score(winsorize(liq_v, 0.01))
            ma_s = self._to_score(winsorize(ma_v, 0.01))

            # 补涨弹性权重
            w_lag = self.cfg.get('lag_factor_weight', 0.25)        # 相对滞后度
            w_bs = self.cfg.get('bottom_stability_weight', 0.20)   # 底部企稳度
            w_vi = self.cfg.get('volume_initiation_weight', 0.15)  # 量能启动度
            w_pd = self.cfg.get('pullback_depth_weight', 0.15)     # 回调深度
            w_rsi = self.cfg.get('rsi_position_weight', 0.10)      # RSI位置
            w_et = self.cfg.get('etf_transfer_weight', 0.05)       # ETF强度传导
            w_vc = self.cfg.get('vol_contraction_weight', 0.03)    # 波动率收缩
            w_corr = self.cfg.get('correlation_etf_weight', 0.04)  # 相关性
            w_ma = self.cfg.get('ma_convergence_weight', 0.02)     # 均线收敛
            w_liq = self.cfg.get('liquidity_weight', 0.01)         # 流动性

            # 综合补涨弹性分
            leader_scores = (
                lag_s * w_lag +
                bs_s * w_bs +
                vi_s * w_vi +
                pd_s * w_pd +
                rsi_s * w_rsi +
                et_s * w_et +
                vc_s * w_vc +
                corr_s * w_corr +
                ma_s * w_ma +
                liq_s * w_liq
            )
            leader_scores = np.clip(leader_scores, 0.0, 100.0)

            # 相关性过滤：确保个股与ETF正相关（会跟涨）
            corr_mask = corr_v >= min_corr

            etf_leaders: List[LeaderResult] = []
            for i, m in enumerate(stock_metrics_list):
                if not corr_mask[i]:
                    continue

                result = LeaderResult(
                    ts_code=m['ts_code'],
                    etf_code=etf_code,
                    stock_name=m.get('stock_name', ''),
                    lag_factor_score=round(float(lag_s[i]), 2),
                    bottom_stability_score=round(float(bs_s[i]), 2),
                    volume_initiation_score=round(float(vi_s[i]), 2),
                    pullback_depth_score=round(float(pd_s[i]), 2),
                    rsi_position_score=round(float(rsi_s[i]), 2),
                    etf_transfer_score=round(float(et_s[i]), 2),
                    vol_contraction_score=round(float(vc_s[i]), 2),
                    correlation_etf_score=round(float(corr_s[i]), 2),
                    liquidity_score=round(float(liq_s[i]), 2),
                    ma_convergence_score=round(float(ma_s[i]), 2),
                    leader_score=round(float(leader_scores[i]), 2),
                )
                etf_leaders.append(result)

            etf_leaders.sort(key=lambda x: x.leader_score, reverse=True)
            results[etf_code] = etf_leaders

        total_leaders = sum(len(v) for v in results.values())
        logger.info(f"LeaderDiscoveryEngine scored {total_leaders} leaders across {len(results)} ETFs")
        return results

    # ------------------------------------------------------------------
    # 单只个股 vs ETF 的指标计算（全向量化）
    # ------------------------------------------------------------------

    def _compute_stock_metrics(self,
                               ts_code: str,
                               etf_code: str,
                               sdf: pd.DataFrame,
                               etf_close: np.ndarray,
                               etf_returns: np.ndarray,
                               etf_vol: np.ndarray,
                               etf_vol_ratio: np.ndarray,
                               etf_high_prox: float,
                               etf_dd: float,
                               etf_close_full: np.ndarray,
                               etf_high_full: np.ndarray,
                               etf_low_full: np.ndarray,
                               etf_dates: Optional[np.ndarray],
                               bench_returns: Optional[np.ndarray],
                               period: int,
                               ) -> Optional[dict]:
        """计算单只个股的补涨弹性指标（10个维度）。"""
        stock_close = np.asarray(sdf['close'].values, dtype=np.float64)
        stock_high = np.asarray(sdf['high'].values, dtype=np.float64)
        stock_low = np.asarray(sdf['low'].values, dtype=np.float64)
        stock_vol = np.asarray(sdf['vol'].values, dtype=np.float64)
        n_stock = len(stock_close)

        # Align stock data to ETF dates
        if etf_dates is not None and 'trade_date' in sdf.columns:
            sdf_aligned = sdf[['trade_date', 'close', 'high', 'low', 'vol']].copy()
            sdf_aligned = sdf_aligned.set_index('trade_date')
            etf_df_idx = pd.DataFrame({'trade_date': etf_dates,
                                       'etf_close': etf_close_full,
                                       'etf_high': etf_high_full,
                                       'etf_low': etf_low_full,
                                       'etf_vol': etf_vol,
                                       'etf_ret': etf_returns})
            etf_df_idx = etf_df_idx.set_index('trade_date')
            merged = sdf_aligned.join(etf_df_idx, how='inner')
            if len(merged) < period + 5:
                return None

            sc = np.asarray(merged['close'].values, dtype=np.float64)
            sh = np.asarray(merged['high'].values, dtype=np.float64)
            sl = np.asarray(merged['low'].values, dtype=np.float64)
            sv = np.asarray(merged['vol'].values, dtype=np.float64)
            ec = np.asarray(merged['etf_close'].values, dtype=np.float64)
            eh = np.asarray(merged['etf_high'].values, dtype=np.float64)
            el = np.asarray(merged['etf_low'].values, dtype=np.float64)
            ev = np.asarray(merged['etf_vol'].values, dtype=np.float64)
            er = np.asarray(merged['etf_ret'].values, dtype=np.float64)
        else:
            min_len = min(n_stock, len(etf_close))
            if min_len < period + 5:
                return None
            sc = stock_close[-min_len:]
            sh = stock_high[-min_len:]
            sl = stock_low[-min_len:]
            sv = stock_vol[-min_len:]
            ec = etf_close[-min_len:]
            eh = etf_high_full[-min_len:]
            el = etf_low_full[-min_len:]
            ev = etf_vol[-min_len:]
            er = etf_returns[-min_len:]

        n = len(sc)
        if n < period + 5:
            return None

        # 日收益率
        sr = np.diff(sc, prepend=sc[0:1]) / np.maximum(np.roll(sc, 1), 1e-10)
        sr[0] = 0.0

        # 技术指标
        ema20 = ema(sc, 20)
        ema60 = ema(sc, 60)
        ema5 = ema(sc, 5)
        ema10 = ema(sc, 10)
        rsi6_arr = rsi(sc, 6)
        atr14 = atr(sh, sl, sc, 14)
        vr = volume_ratio(sv, 20)

        # ── 1. 相对滞后度 (lag_raw): 个股涨幅 / ETF涨幅，越低越好 ──
        cum_stock = sc[-1] / max(sc[max(-period, -n)], 1e-10)
        cum_etf = ec[-1] / max(ec[max(-period, -n)], 1e-10)
        rs_ratio = cum_stock / max(cum_etf, 1e-10)
        # rs_ratio < 1 表示个股落后ETF，滞后度越高分越高
        lag_raw = (1.0 - min(rs_ratio, 2.0)) * 100.0

        # ── 2. 底部企稳度 (bs_raw): 站上MA20且MA20>MA60，距MA20近 ──
        above_ma20 = float(ema20[-1] > ema60[-1])
        price_above_ma20 = float(sc[-1] >= ema20[-1] * 0.97)
        dist_ma20 = abs(sc[-1] / max(ema20[-1], 1e-10) - 1.0) * 100.0
        near_ma20 = float(np.exp(-(dist_ma20 ** 2) / (2.0 * 5.0 ** 2)))
        ma20_slope_up = float(ema20[-1] > ema20[-5])
        bs_raw = (above_ma20 * 0.3 + price_above_ma20 * 0.2 +
                  near_ma20 * 0.3 + ma20_slope_up * 0.2) * 100.0

        # ── 3. 量能启动度 (vi_raw): 量比从低位回升 ──
        vr_last = float(vr[-1])
        vr_prev5 = float(np.mean(vr[-6:-1])) if n >= 6 else vr_last
        vr_in_range = float(0.6 <= vr_last <= 1.5)
        vr_rising = float(vr_last > vr_prev5 * 0.95)
        vr_score = float(np.exp(-((vr_last - 1.0) ** 2) / (2.0 * 0.4 ** 2)))
        vi_raw = (vr_in_range * 0.3 + vr_rising * 0.3 + vr_score * 0.4) * 100.0

        # ── 4. 回调深度 (pd_raw): 从60日高点回调5%-15%最佳 ──
        high_60 = float(np.max(sh[-min(60, n):]))
        pullback_pct = (sc[-1] / max(high_60, 1e-10) - 1.0) * 100.0
        if -15.0 <= pullback_pct <= -3.0:
            pd_raw = float(np.exp(-((pullback_pct + 8.0) ** 2) / (2.0 * 4.0 ** 2))) * 100.0
        elif -3.0 < pullback_pct <= 2.0:
            pd_raw = 40.0
        elif pullback_pct > 2.0:
            pd_raw = 10.0
        else:
            pd_raw = max(0.0, 50.0 + pullback_pct * 2.0)

        # ── 5. RSI位置 (rsi_pos_raw): RSI6在35-55区间最佳 ──
        rsi6_last = float(rsi6_arr[-1]) if np.isfinite(rsi6_arr[-1]) else 50.0
        if 35.0 <= rsi6_last <= 55.0:
            rsi_pos_raw = 100.0 - abs(rsi6_last - 45.0) * 2.0
        elif 30.0 <= rsi6_last < 35.0:
            rsi_pos_raw = 70.0
        elif 55.0 < rsi6_last <= 65.0:
            rsi_pos_raw = 50.0
        elif rsi6_last > 65.0:
            rsi_pos_raw = 10.0
        else:
            rsi_pos_raw = 30.0

        # ── 6. ETF强度传导 (etf_lag_raw): ETF涨幅-个股涨幅 ──
        etf_ret_pct = (cum_etf - 1.0) * 100.0
        stock_ret_pct = (cum_stock - 1.0) * 100.0
        etf_lag_raw = max(0.0, etf_ret_pct - stock_ret_pct)

        # ── 7. 波动率收缩 (vc_raw): 近期ATR/价格下降 ──
        if n >= 40:
            natr_recent = float(np.mean(atr14[-20:]) / max(np.mean(sc[-20:]), 1e-10))
            natr_prev = float(np.mean(atr14[-40:-20]) / max(np.mean(sc[-40:-20]), 1e-10))
            vc_raw = max(0.0, (natr_prev - natr_recent) * 1000.0)
        else:
            vc_raw = 50.0

        # ── 8. 与ETF相关性 (corr_raw) ──
        corr_raw = float(rolling_corr(sr, er, 60)[-1]) if n >= 60 else 0.0
        corr_raw = 0.0 if np.isnan(corr_raw) else corr_raw

        # ── 9. 流动性 (liq_raw) ──
        liq_raw = self._calc_liquidity(sv, sc, n)

        # ── 10. 均线收敛度 (ma_conv_raw): MA5向MA10靠拢 ──
        ma_gap = abs(ema5[-1] - ema10[-1]) / max(ema10[-1], 1e-10) * 100.0
        ma_conv_raw = float(np.exp(-(ma_gap ** 2) / (2.0 * 2.0 ** 2))) * 100.0

        # 个股名称
        stock_name = ''
        if 'stock_name' in sdf.columns:
            stock_name = str(sdf['stock_name'].iloc[0])

        metrics = {
            'ts_code': ts_code,
            'etf_code': etf_code,
            'stock_name': stock_name,
            'lag_raw': lag_raw,
            'bs_raw': bs_raw,
            'vi_raw': vi_raw,
            'pd_raw': pd_raw,
            'rsi_pos_raw': rsi_pos_raw,
            'etf_lag_raw': etf_lag_raw,
            'vc_raw': vc_raw,
            'corr_raw': corr_raw,
            'liq_raw': liq_raw,
            'ma_conv_raw': ma_conv_raw,
        }
        return metrics

    # ------------------------------------------------------------------
    # 子维度计算方法
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_breakout_quality(stock_close: np.ndarray,
                               stock_vol: np.ndarray,
                               etf_close: np.ndarray,
                               etf_vol: np.ndarray,
                               n: int) -> float:
        """计算突破质量：成交量确认的突破次数占比。"""
        if n < 40:
            return 50.0

        stock_returns = np.diff(stock_close, prepend=stock_close[0:1]) / np.maximum(
            np.roll(stock_close, 1), 1e-10)
        stock_vol_ema = ema(stock_vol, 20)
        vol_surge = stock_vol / np.maximum(stock_vol_ema, 1e-10) > 1.5

        etf_returns = np.diff(etf_close, prepend=etf_close[0:1]) / np.maximum(
            np.roll(etf_close, 1), 1e-10)

        recent_30 = min(30, n)
        lookback = min(n, recent_30)

        stock_up = stock_returns[-lookback:] > 0.02
        stock_strong = stock_returns[-lookback:] > etf_returns[-lookback:] + 0.01
        volume_confirmed = vol_surge[-lookback:]

        breakout_days = stock_up & stock_strong
        confirmed_breakouts = breakout_days & volume_confirmed

        total_breakout = int(np.sum(breakout_days))
        if total_breakout == 0:
            return 50.0

        quality = float(np.sum(confirmed_breakouts)) / max(total_breakout, 1) * 100.0
        return np.clip(quality, 0.0, 100.0)

    @staticmethod
    def _calc_liquidity(vol: np.ndarray, close: np.ndarray, n: int) -> float:
        """计算流动性评分。"""
        if n < 20:
            return 50.0

        turnover = vol * close
        avg_turnover = float(np.mean(turnover[-20:]))
        if avg_turnover <= 0:
            return 50.0

        vol_std = float(np.std(vol[-20:]))
        vol_cv = vol_std / max(float(np.mean(vol[-20:])), 1e-10)

        liq = np.clip(np.log1p(avg_turnover) / 10.0 * 100.0, 0.0, 100.0)
        if vol_cv > 1.0:
            liq *= 0.8

        return np.clip(liq, 0.0, 100.0)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _to_score(arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float64)
        valid = a[np.isfinite(a)]
        if len(valid) == 0:
            return np.full_like(a, 50.0)
        mn, mx = np.nanmin(a), np.nanmax(a)
        if mx <= mn or not np.isfinite(mx - mn):
            return np.full_like(a, 50.0)
        return np.clip((a - mn) / (mx - mn) * 100.0, 0.0, 100.0)
