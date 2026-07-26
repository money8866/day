# -*- coding: utf-8 -*-
"""
动态主题相关性分析引擎 - Theme Correlation Engine V1

【顶级量化方案】
纯静态概念重叠无法解决"双叙事交叉股"的归属问题(如飞龙同时有液冷概念+国产芯片)。
本引擎通过量价动态相关性分析，识别个股当前的市场炒作叙事：

三因子模型:
  1. ETF收益率相关性 (权重0.50): 个股日收益率 vs 主题ETF日收益率的滚动相关性
  2. 成交量协同性 (权重0.25): 个股是否随主题放量而放量
  3. 龙头协同性 (权重0.25): 个股是否随主题龙头股同涨同跌

使用方法:
    engine = ThemeCorrelationEngine(config)
    result = engine.evaluate("002536.SZ", "20260724", ["AI算力", "新能源车"], theme_config)
    # -> {"AI算力": 0.72, "新能源车": 0.35}  # 飞龙与AI算力的动态相关性更高
"""

import os
import sys
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from inst_pullback_v2.data.loader import DataLoader
import stock_cache as sc


class ThemeCorrelationEngine:
    """动态主题相关性分析引擎

    通过量价三因子模型判断个股当前与各主题的动态关联度。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('theme_correlation', {})
        self.loader = DataLoader()

        # 配置参数
        self._lookback = self.cfg.get('lookback_days', 60)
        self._corr_windows = self.cfg.get('corr_windows', [5, 10, 20])
        self._corr_weights = self.cfg.get('corr_weights', [0.50, 0.30, 0.20])
        self._min_data_points = self.cfg.get('min_data_points', 5)
        self._vol_surge_threshold = self.cfg.get('vol_surge_threshold', 1.5)
        self._volume_weight = self.cfg.get('volume_weight', 0.25)
        self._corr_weight = self.cfg.get('corr_weight', 0.50)
        self._leader_weight = self.cfg.get('leader_weight', 0.25)

        # {theme_cn: {etf_codes, leaders}}
        self._theme_asset_map: Dict[str, dict] = {}

    def prepare_theme_assets(self, theme_config_path: str, top_themes: List[str]):
        """从 theme_config.json 提取每个主题的ETF+龙头代码"""
        import json
        if not os.path.exists(theme_config_path):
            return

        with open(theme_config_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        top_set = set(top_themes) if top_themes else None
        for eng_key, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            cn_name = cfg.get('name_cn', '')
            if top_set and cn_name not in top_set:
                continue
            self._theme_asset_map[cn_name] = {
                'etf_codes': cfg.get('etf_codes', []),
                'main_etf': cfg.get('main_etf', ''),
                'leaders': cfg.get('leaders', []),
            }

    def evaluate(self, ts_code: str, trade_date: str,
                 top_themes: List[str]) -> Dict[str, float]:
        """计算个股与各活跃主题的动态相关性得分

        Args:
            ts_code: 个股代码(如 '002536.SZ')
            trade_date: 交易日 YYYYMMDD
            top_themes: 活跃主题列表

        Returns:
            {theme_cn: 综合相关性得分(0~1)}, 得分越高关联越强
        """
        start_date = self._calc_start_date(trade_date, self._lookback)

        # 1. 加载个股日线数据
        stock_df = self.loader.load_stk_factor(ts_code, start_date, trade_date)
        if stock_df is None or len(stock_df) < self._min_data_points:
            return {t: 0.0 for t in top_themes}

        stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)
        stock_close = stock_df['close_hfq'].values if 'close_hfq' in stock_df.columns else stock_df['close'].values
        stock_vol = stock_df['vol'].values if 'vol' in stock_df.columns else stock_df.get('volume', pd.Series([0]*len(stock_df))).values
        stock_ret = np.diff(stock_close) / (stock_close[:-1] + 1e-10)

        results = {}
        for theme_name in top_themes:
            assets = self._theme_asset_map.get(theme_name, {})
            score = self._compute_theme_score(
                stock_ret, stock_vol, stock_df,
                assets, trade_date, start_date
            )
            results[theme_name] = round(score, 4)

        return results

    def _compute_theme_score(self, stock_ret: np.ndarray,
                              stock_vol: np.ndarray,
                              stock_df: pd.DataFrame,
                              assets: dict,
                              trade_date: str,
                              start_date: str) -> float:
        """计算个股与单个主题的综合相关性得分"""
        # ── 因子1: ETF相关性 ──
        etf_corr = self._calc_etf_correlation(
            stock_ret, assets.get('etf_codes', []),
            assets.get('main_etf', ''),
            trade_date, start_date
        )

        # ── 因子2: 成交量协同 ──
        vol_sync = self._calc_volume_sync(
            stock_vol, stock_df,
            assets.get('etf_codes', []),
            trade_date, start_date
        )

        # ── 因子3: 龙头协同 ──
        leader_sync = self._calc_leader_sync(
            stock_ret,
            assets.get('leaders', []),
            trade_date, start_date
        )

        # 综合加权
        score = (etf_corr * self._corr_weight +
                 vol_sync * self._volume_weight +
                 leader_sync * self._leader_weight)

        return score

    def _calc_etf_correlation(self, stock_ret: np.ndarray,
                               etf_codes: List[str],
                               main_etf: str,
                               trade_date: str,
                               start_date: str) -> float:
        """因子1: 多窗口ETF收益率相关性

        使用 main_etf 优先，若不可用则取第一个可用ETF。
        分5/10/20日窗口计算Pearson相关性，加权平均后映射到[0,1]。
        """
        if not etf_codes and not main_etf:
            return 0.0

        # 优先使用主ETF
        target_etf = main_etf if main_etf else etf_codes[0]
        df_etf = self._load_etf_data(target_etf, start_date, trade_date)
        if df_etf is None or len(df_etf) < self._min_data_points:
            # 尝试其他ETF
            for code in etf_codes:
                if code != target_etf:
                    df_etf = self._load_etf_data(code, start_date, trade_date)
                    if df_etf is not None and len(df_etf) >= self._min_data_points:
                        break
        if df_etf is None or len(df_etf) < self._min_data_points:
            return 0.0

        etf_close = df_etf['close'].values.astype(float)
        etf_ret = np.diff(etf_close) / (etf_close[:-1] + 1e-10)

        # 对齐长度
        min_len = min(len(stock_ret), len(etf_ret))
        if min_len < self._min_data_points:
            return 0.0
        s_ret = stock_ret[-min_len:]
        e_ret = etf_ret[-min_len:]

        # 多窗口加权
        total_score = 0.0
        total_w = 0.0
        for window, w in zip(self._corr_windows, self._corr_weights):
            if min_len < window + 1:
                continue
            s_slice = s_ret[-window:]
            e_slice = e_ret[-window:]
            corr = self._safe_pearson(s_slice, e_slice)
            if corr is not None:
                # tanh映射到[0,1]：corr=0→0.5, corr=0.3→0.65, corr=0.7→0.9
                mapped = (np.tanh(corr * 2.0) + 1.0) * 0.5
                total_score += mapped * w
                total_w += w

        return total_score / total_w if total_w > 0 else 0.0

    def _calc_volume_sync(self, stock_vol: np.ndarray,
                           stock_df: pd.DataFrame,
                           etf_codes: List[str],
                           trade_date: str,
                           start_date: str) -> float:
        """因子2: 成交量协同性

        检查个股近期是否随主题ETF放量而放量。
        计算个股和ETF的"量比"序列的相关性。
        """
        if not etf_codes or len(stock_vol) < 20:
            return 0.0

        target_etf = etf_codes[0]
        df_etf = self._load_etf_data(target_etf, start_date, trade_date)
        if df_etf is None or len(df_etf) < 20:
            return 0.0

        etf_vol = df_etf['vol'].values.astype(float) if 'vol' in df_etf.columns else np.ones(len(df_etf))

        # 计算量比(当前量/20日均量)
        min_len = min(len(stock_vol), len(etf_vol))
        if min_len < 21:
            return 0.0
        sv = stock_vol[-min_len:]
        ev = etf_vol[-min_len:]

        sv_ratio = sv[20:] / (np.mean(sv[:20]) + 1e-10)
        ev_ratio = ev[20:] / (np.mean(ev[:20]) + 1e-10)

        # 量比方向一致性: 两者同时放量或同时缩量的比例
        sv_surge = sv_ratio > self._vol_surge_threshold
        ev_surge = ev_ratio > self._vol_surge_threshold

        if ev_surge.sum() == 0:
            return 0.5  # ETF无放量信号，中性

        # 命中率: ETF放量时个股也放量的比例
        hit_rate = (sv_surge & ev_surge).sum() / max(ev_surge.sum(), 1)
        return min(1.0, hit_rate * 1.5)

    def _calc_leader_sync(self, stock_ret: np.ndarray,
                           leader_codes: List[str],
                           trade_date: str,
                           start_date: str) -> float:
        """因子3: 龙头协同性

        个股收益率是否与主题龙头股收益率同步。
        取龙头股的平均收益率序列与个股做相关性分析。
        """
        if not leader_codes or len(stock_ret) < self._min_data_points:
            return 0.0

        leader_rets = []
        for code in leader_codes[:3]:  # 最多3只龙头
            df = self.loader.load_stk_factor(code, start_date, trade_date)
            if df is None or len(df) < self._min_data_points + 1:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            close = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values
            rets = np.diff(close) / (close[:-1] + 1e-10)
            leader_rets.append(rets)

        if not leader_rets:
            return 0.0

        # 对齐长度
        min_len = min(len(stock_ret), min(len(r) for r in leader_rets))
        if min_len < self._min_data_points:
            return 0.0

        s_ret = stock_ret[-min_len:]
        # 龙头平均收益率
        l_ret = np.mean([r[-min_len:] for r in leader_rets], axis=0)

        corr = self._safe_pearson(s_ret, l_ret)
        if corr is None:
            return 0.0
        # tanh映射到[0,1]
        return (np.tanh(corr * 2.0) + 1.0) * 0.5

    def _load_etf_data(self, etf_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """加载ETF日线数据"""
        try:
            return self.loader.load_index_data(etf_code, start_date, end_date)
        except Exception:
            return None

    def _calc_start_date(self, trade_date: str, lookback: int) -> str:
        """计算起始日期"""
        from datetime import datetime, timedelta
        dt = datetime.strptime(trade_date, '%Y%m%d')
        start = dt - timedelta(days=int(lookback * 1.4))
        return start.strftime('%Y%m%d')

    @staticmethod
    def _safe_pearson(a: np.ndarray, b: np.ndarray) -> Optional[float]:
        """安全计算Pearson相关系数"""
        if len(a) < 3 or len(b) < 3:
            return None
        try:
            corr = np.corrcoef(a, b)[0, 1]
            if np.isnan(corr) or np.isinf(corr):
                return None
            return float(corr)
        except Exception:
            return None


def build_theme_corr_result(corr_scores: Dict[str, float],
                            current_theme: str,
                            threshold: float = 0.05) -> Tuple[str, float, bool]:
    """根据相关性得分确定最佳主题归属

    Returns:
        (best_theme, confidence, should_reassign)
    """
    if not corr_scores:
        return (current_theme, 0.0, False)

    sorted_themes = sorted(corr_scores.items(), key=lambda x: x[1], reverse=True)
    best_theme = sorted_themes[0][0]
    best_score = sorted_themes[0][1]
    second_score = sorted_themes[1][1] if len(sorted_themes) > 1 else 0.0

    # 只在相关性有显著差异时才建议迁移
    should_reassign = (best_theme != current_theme and
                       best_score > second_score + threshold)
    confidence = best_score - second_score if should_reassign else 0.0

    return (best_theme, best_score, should_reassign)
