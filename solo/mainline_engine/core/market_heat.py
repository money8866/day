"""市场热度引擎 — 对行业/主题的市场热度进行 0-100 综合打分。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, rma, atr, adx, rsi, macd, bollinger, kdj,
    rank_score, normalize, zscore, winsorize,
    max_drawdown, sharpe_ratio, calmar_ratio,
    rolling_corr, beta as rolling_beta,
    new_high_count, consecutive_up_days, above_ema_days,
    volume_ratio, slope, hurst_exponent,
)


@dataclass
class HeatScoreResult:
    ts_code: str
    etf_code: str = ""
    hot_rank_score: float = 0.0
    hot_days_score: float = 0.0
    limit_up_count_score: float = 0.0
    limit_break_rate: float = 0.0
    sector_heat_score: float = 0.0
    broker_recommend_score: float = 0.0
    heat_score: float = 0.0


class MarketHeatEngine:
    """市场热度引擎。

    对每个行业/主题计算热榜排名、连续上榜天数、涨停数量、
    炸板率（反向）、板块相对热度、机构推荐等 6 个维度，
    加权得到 0-100 热度得分。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('market_heat', {})
        self._log_config()

    def _log_config(self) -> None:
        logger.debug(f"MarketHeatEngine config: hot_period={self.cfg.get('hot_period', 10)}, "
                     f"limit_period={self.cfg.get('limit_period', 5)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              dc_hot_data: Optional[pd.DataFrame] = None,
              limit_list_data: Dict[str, pd.DataFrame] = None,
              broker_recommend: Optional[pd.DataFrame] = None,
              sector_map: Optional[Dict[str, List[str]]] = None,
              ) -> Dict[str, HeatScoreResult]:
        """对所有行业/主题进行热度打分。

        Parameters
        ----------
        dc_hot_data : pd.DataFrame, optional
            热榜数据，需含列 [trade_date, ts_code, rank] 或 [trade_date, ts_code, hot_value]。
        limit_list_data : dict
            {ts_code: DataFrame}，每个 DataFrame 需含列 [trade_date, limit_status] 或
            [trade_date, is_limit_up, is_limit_break]。
        broker_recommend : pd.DataFrame, optional
            机构推荐数据，需含列 [trade_date, ts_code]。
        sector_map : dict, optional
            {sector_name: [ts_code_list]}，将个股映射到行业/主题。

        Returns
        -------
        dict[str, HeatScoreResult]
            键为行业/主题名称（若未提供 sector_map 则为个股 ts_code）。
        """
        limit_list_data = limit_list_data or {}
        hot_period = self.cfg.get('hot_period', 10)
        limit_period = self.cfg.get('limit_period', 5)

        # ------ 若无 sector_map，将每只个股视为独立主题 ------
        if sector_map is None or not sector_map:
            sector_map = self._build_default_sectors(limit_list_data, dc_hot_data)

        if not sector_map:
            logger.warning("No sector_map and no data to build default sectors, returning {}")
            return {}

        # ------ 为每个行业计算指标 ------
        metrics_list: List[dict] = []

        for sector_name, members in sector_map.items():
            if not members:
                continue
            try:
                metrics = self._compute_sector_metrics(
                    sector_name, members, dc_hot_data, limit_list_data,
                    broker_recommend, hot_period, limit_period,
                )
                if metrics is not None:
                    metrics_list.append(metrics)
            except Exception as exc:
                logger.error(f"Error computing heat metrics for sector '{sector_name}': {exc}")
                continue

        if not metrics_list:
            logger.warning("No sector passed heat metric computation, returning {}")
            return {}

        # ------ 横截面归一化 ------
        n = len(metrics_list)
        hot_rank_v = np.array([m['hot_rank_raw'] for m in metrics_list], dtype=np.float64)
        hot_days_v = np.array([m['hot_days_raw'] for m in metrics_list], dtype=np.float64)
        limit_up_v = np.array([m['limit_up_count_raw'] for m in metrics_list], dtype=np.float64)
        limit_break_v = np.array([m['limit_break_rate_raw'] for m in metrics_list], dtype=np.float64)
        sector_heat_v = np.array([m['sector_heat_raw'] for m in metrics_list], dtype=np.float64)
        broker_v = np.array([m['broker_recommend_raw'] for m in metrics_list], dtype=np.float64)

        # winsorize + normalize -> 0-100
        # hot_rank: 排名越高得分越高（排名数值小 = 排名高，所以取负）
        hot_rank_s = self._to_score(winsorize(-hot_rank_v, 0.01))
        hot_days_s = self._to_score(winsorize(hot_days_v, 0.01))
        limit_up_s = self._to_score(winsorize(limit_up_v, 0.01))
        # limit_break_rate: 炸板率越低越好（反向）
        limit_break_s = self._to_score(winsorize(-limit_break_v, 0.01))
        sector_heat_s = self._to_score(winsorize(sector_heat_v, 0.01))
        broker_s = self._to_score(winsorize(broker_v, 0.01))

        w_rank = self.cfg.get('hot_rank_weight', 0.25)
        w_days = self.cfg.get('hot_days_weight', 0.15)
        w_limit = self.cfg.get('limit_up_count_weight', 0.15)
        w_break = self.cfg.get('limit_break_rate_weight', 0.10)
        w_sector = self.cfg.get('sector_heat_weight', 0.20)
        w_broker = self.cfg.get('broker_recommend_weight', 0.15)

        final_scores = (
            hot_rank_s * w_rank +
            hot_days_s * w_days +
            limit_up_s * w_limit +
            limit_break_s * w_break +
            sector_heat_s * w_sector +
            broker_s * w_broker
        )
        final_scores = np.clip(final_scores, 0.0, 100.0)

        # ------ 组装结果 ------
        results: Dict[str, HeatScoreResult] = {}
        for m, fs, hr_s, hd_s, lu_s, lb_s, sh_s, br_s in zip(
            metrics_list, final_scores,
            hot_rank_s, hot_days_s, limit_up_s, limit_break_s,
            sector_heat_s, broker_s,
        ):
            sector_name = m['sector_name']
            result = HeatScoreResult(
                ts_code=sector_name,
                etf_code=m.get('etf_code', ''),
                hot_rank_score=round(float(hr_s), 2),
                hot_days_score=round(float(hd_s), 2),
                limit_up_count_score=round(float(lu_s), 2),
                limit_break_rate=round(float(lb_s), 2),
                sector_heat_score=round(float(sh_s), 2),
                broker_recommend_score=round(float(br_s), 2),
                heat_score=round(float(fs), 2),
            )
            results[sector_name] = result

        logger.info(f"MarketHeatEngine scored {len(results)} sectors")
        return results

    # ------------------------------------------------------------------
    # 行业热度指标计算
    # ------------------------------------------------------------------

    def _compute_sector_metrics(self,
                                sector_name: str,
                                members: List[str],
                                dc_hot_data: Optional[pd.DataFrame],
                                limit_list_data: Dict[str, pd.DataFrame],
                                broker_recommend: Optional[pd.DataFrame],
                                hot_period: int,
                                limit_period: int,
                                ) -> Optional[dict]:
        """对单个行业计算所有热度指标。"""
        # ------ 1. 热榜排名 ------
        hot_rank_raw = 50.0
        hot_days_raw = 0.0
        if dc_hot_data is not None and not dc_hot_data.empty:
            hot_data = dc_hot_data.copy()
            # 筛选属于该行业的个股
            if 'ts_code' in hot_data.columns:
                mask = hot_data['ts_code'].isin(members)
                sector_hot = hot_data[mask]
            else:
                sector_hot = hot_data

            if not sector_hot.empty:
                if 'rank' in sector_hot.columns:
                    rank_col = np.asarray(sector_hot['rank'], dtype=np.float64)
                    rank_col = rank_col[np.isfinite(rank_col)]
                    if len(rank_col) > 0:
                        hot_rank_raw = float(np.nanmin(rank_col))
                    else:
                        hot_rank_raw = 50.0
                elif 'hot_value' in sector_hot.columns:
                    hv = np.asarray(sector_hot['hot_value'], dtype=np.float64)
                    hv = hv[np.isfinite(hv)]
                    hot_rank_raw = float(np.nanmax(hv)) if len(hv) > 0 else 0.0

                # 连续热榜天数：按 trade_date 排序后统计尾部连续天数
                if 'trade_date' in sector_hot.columns:
                    sector_hot_sorted = sector_hot.sort_values('trade_date')
                    dates = pd.to_datetime(sector_hot_sorted['trade_date'].unique())
                    if len(dates) > 1:
                        date_diff = np.diff(np.array(dates, dtype='datetime64[D]'))
                        hot_days_raw = float(self._consecutive_count_at_end(date_diff == np.timedelta64(1, 'D')))
                    elif len(dates) == 1:
                        hot_days_raw = 1.0

        # ------ 2. 涨停数量 ------
        limit_up_count_raw = 0.0
        for ts_code in members:
            ldf = limit_list_data.get(ts_code)
            if ldf is None or ldf.empty:
                continue
            limit_up_count_raw += self._count_limit_up(ldf, limit_period)

        # ------ 3. 炸板率 ------
        limit_break_raw = 0.0
        limit_total = 0
        limit_break_total = 0
        for ts_code in members:
            ldf = limit_list_data.get(ts_code)
            if ldf is None or ldf.empty:
                continue
            up_count, break_count = self._count_limit_up_break(ldf, limit_period)
            limit_total += up_count
            limit_break_total += break_count
        limit_break_rate_raw = (limit_break_total / max(limit_total, 1)) * 100.0 if limit_total > 0 else 0.0

        # ------ 4. 板块热度（成员平均涨跌幅 + 成交额放大） ------
        sector_heat_raw = 50.0  # 默认中性
        # 如果有 limit_list_data 中的涨跌幅信息，可以计算平均涨幅
        returns_list = []
        for ts_code in members:
            ldf = limit_list_data.get(ts_code)
            if ldf is None or ldf.empty or 'pct_chg' not in ldf.columns:
                continue
            pct = np.asarray(ldf['pct_chg'].tail(limit_period), dtype=np.float64)
            pct = pct[np.isfinite(pct)]
            if len(pct) > 0:
                returns_list.append(float(np.mean(pct)))
        if returns_list:
            sector_heat_raw = float(np.mean(returns_list) * 100.0)

        # ------ 5. 机构推荐 ------
        broker_recommend_raw = 0.0
        if broker_recommend is not None and not broker_recommend.empty and 'ts_code' in broker_recommend.columns:
            mask = broker_recommend['ts_code'].isin(members)
            sector_recommend = broker_recommend[mask]
            if not sector_recommend.empty:
                # 近 hot_period 天的推荐数量
                if 'trade_date' in sector_recommend.columns:
                    dates = pd.to_datetime(sector_recommend['trade_date'])
                    recent = dates >= dates.max() - pd.Timedelta(days=hot_period)
                    broker_recommend_raw = float(recent.sum())
                else:
                    broker_recommend_raw = float(len(sector_recommend))

        metrics = {
            'sector_name': sector_name,
            'etf_code': '',
            'hot_rank_raw': hot_rank_raw,
            'hot_days_raw': hot_days_raw,
            'limit_up_count_raw': limit_up_count_raw,
            'limit_break_rate_raw': limit_break_rate_raw,
            'sector_heat_raw': sector_heat_raw,
            'broker_recommend_raw': broker_recommend_raw,
        }
        return metrics

    # ------------------------------------------------------------------
    # 涨停数据处理
    # ------------------------------------------------------------------

    @staticmethod
    def _count_limit_up(ldf: pd.DataFrame, period: int) -> float:
        """统计近期涨停次数。"""
        # 尝试多种可能的列名
        if 'limit_status' in ldf.columns:
            status = np.asarray(ldf['limit_status'].tail(period), dtype=np.float64)
            status = status[np.isfinite(status)]
            return float(np.sum(status == 1.0))
        elif 'is_limit_up' in ldf.columns:
            vals = np.asarray(ldf['is_limit_up'].tail(period))
            return float(np.sum(vals))
        elif 'is_zt' in ldf.columns:
            vals = np.asarray(ldf['is_zt'].tail(period))
            return float(np.sum(vals))
        return 0.0

    @staticmethod
    def _count_limit_up_break(ldf: pd.DataFrame, period: int) -> Tuple[int, int]:
        """统计近期涨停尝试次数和炸板次数。"""
        up_count = 0
        break_count = 0

        if 'limit_status' in ldf.columns:
            status = np.asarray(ldf['limit_status'].tail(period), dtype=np.float64)
            up_count = int(np.sum(status >= 1.0))
            break_count = int(np.sum(status == 2.0))
        elif 'is_limit_up' in ldf.columns and 'is_limit_break' in ldf.columns:
            up_vals = np.asarray(ldf['is_limit_up'].tail(period))
            break_vals = np.asarray(ldf['is_limit_break'].tail(period))
            up_count = int(np.sum(up_vals))
            break_count = int(np.sum(break_vals))
        elif 'is_zt' in ldf.columns and 'is_zj' in ldf.columns:
            zt = np.asarray(ldf['is_zt'].tail(period))
            zj = np.asarray(ldf['is_zj'].tail(period))
            up_count = int(np.sum(zt))
            break_count = int(np.sum(zj))
        else:
            # 只有 is_limit_up / is_zt 时，默认炸板率为 0
            up_count = int(MarketHeatEngine._count_limit_up(ldf, period))
            break_count = 0

        return up_count, break_count

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _build_default_sectors(limit_list_data: Dict[str, pd.DataFrame],
                               dc_hot_data: Optional[pd.DataFrame],
                               ) -> Dict[str, List[str]]:
        """当没有提供 sector_map 时，将每只个股作为独立行业。"""
        if limit_list_data:
            sectors = {ts_code: [ts_code] for ts_code in limit_list_data.keys()}
        elif dc_hot_data is not None and 'ts_code' in dc_hot_data.columns:
            codes = dc_hot_data['ts_code'].unique()
            sectors = {str(c): [str(c)] for c in codes}
        else:
            sectors = {}
        return sectors

    @staticmethod
    def _to_score(arr: np.ndarray) -> np.ndarray:
        """Min-Max 归一化到 [0, 100]。"""
        a = np.asarray(arr, dtype=np.float64)
        valid = a[np.isfinite(a)]
        if len(valid) == 0:
            return np.full_like(a, 50.0)
        mn, mx = np.nanmin(a), np.nanmax(a)
        if mx <= mn or not np.isfinite(mx - mn):
            return np.full_like(a, 50.0)
        return np.clip((a - mn) / (mx - mn) * 100.0, 0.0, 100.0)

    @staticmethod
    def _consecutive_count_at_end(condition: np.ndarray) -> int:
        """计算数组尾部连续 True 的个数（全向量化）。"""
        if len(condition) == 0:
            return 0
        false_positions = np.where(~condition)[0]
        if len(false_positions) == 0:
            return len(condition)
        last_false = int(false_positions[-1])
        return len(condition) - last_false - 1
