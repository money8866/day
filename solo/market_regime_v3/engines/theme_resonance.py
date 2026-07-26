# -*- coding: utf-8 -*-
"""
主题共振引擎 - Theme Resonance Engine
评估各主题与市场的共振程度，包括主题强度、ETF表现、领涨股强度、
宽度贡献、趋势一致性等子因子。
所有阈值参数从 config.yaml 读取。
"""

import os
import sys
import json
import sqlite3
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

# 将项目根目录 d:\mystock\solo 加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import stock_cache as sc
from inst_pullback_v2.data.loader import DataLoader


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
_STK_FACTOR_DB = sc.DB_PATH  # SQLite 数据库路径
_THEME_CONFIG_PATH = r"D:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json"


@dataclass
class ThemeResonanceResult:
    """主题共振评分结果"""
    score: float  # 综合共振评分 0-100
    top_themes: List[Dict]  # [{name, strength, etf_score, leader_count}]
    theme_count: int  # 活跃共振主题数量
    sub_scores: Dict[str, float]  # 各子因子平均得分 0-100
    explain: Dict[str, str]  # 各子因子解释文本


class ThemeResonanceEngine:
    """主题共振引擎

    评估各主题与市场的共振程度，包括：
      - theme_strength: 主题强度（股票数占比）
      - etf_strength: ETF 强度（20日收益 + 量能趋势）
      - leader_strength: 领涨股强度（20日涨幅 > 10% 的股票占比）
      - breadth_contribution: 宽度贡献（站上 MA20 的股票占比）
      - trend_alignment: 趋势一致性（MA60 趋势向上的股票占比）
    所有权重参数从 config.yaml 读取。
    """

    def __init__(self, config: dict):
        self.cfg = config['theme_resonance']
        self.loader = DataLoader()
        # 主题中文名 -> ETF代码列表 的映射（从 theme_config.json 加载）
        self._theme_etf_map: Dict[str, List[str]] = {}
        self._load_theme_config()

    # ──────────────────────────────────────────────
    # 主题配置加载
    # ──────────────────────────────────────────────

    def _load_theme_config(self):
        """加载主题ETF映射配置（非必须，找不到时仅打印警告）"""
        if not os.path.exists(_THEME_CONFIG_PATH):
            print(f"[ThemeResonance] 警告: 主题ETF配置文件不存在 {_THEME_CONFIG_PATH}")
            return
        try:
            with open(_THEME_CONFIG_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            # theme_config.json 是 dict，key=主题ID（如 AI_COMPUTE）, value=包含 name_cn 和 etf_codes 的dict
            for theme_id, info in raw.items():
                if theme_id.startswith('_'):
                    continue  # 跳过 _flow 等元数据条目
                name = info.get('name_cn', theme_id)
                etf_codes = info.get('etf_codes', [])
                if etf_codes:
                    # 清理 ETF 代码后缀（如 "515980.SH" -> "515980"）
                    clean_codes = [c.split('.')[0] for c in etf_codes]
                    self._theme_etf_map[name] = clean_codes
        except Exception as e:
            print(f"[ThemeResonance] 警告: 读取主题配置失败: {e}")

    # ──────────────────────────────────────────────
    # 日期工具
    # ──────────────────────────────────────────────

    @staticmethod
    def _calc_start_date(trade_date: str, lookback_days: int) -> str:
        """根据目标交易日数估算起始日历日期"""
        dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
        cal_days = int(lookback_days * 1.4) + 20
        start = dt - datetime.timedelta(days=cal_days)
        return start.strftime('%Y%m%d')

    # ──────────────────────────────────────────────
    # 主题-股票映射工具
    # ──────────────────────────────────────────────

    @staticmethod
    def _get_theme_stocks(theme_stock_map: dict, theme_name: str) -> List[str]:
        """从主题-股票映射中获取该主题的股票代码列表

        支持三种格式：
        - list of dict: [{'code': '300502.SZ', 'name': '...'}, ...]
        - list of str: ['300502.SZ', '300308.SZ']
        - dict: {ts_code: {...}, ...}
        """
        data = theme_stock_map.get(theme_name, [])
        codes = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    code = item.get('code', '')
                    if code:
                        codes.append(code)
                elif isinstance(item, str):
                    codes.append(item)
            return codes
        if isinstance(data, dict):
            return list(data.keys())
        return []

    @staticmethod
    def _count_total_stocks(theme_stock_map: dict) -> int:
        """统计所有主题覆盖的总股票数（去重）"""
        all_stocks = set()
        for name in theme_stock_map:
            stocks = ThemeResonanceEngine._get_theme_stocks(theme_stock_map, name)
            all_stocks.update(stocks)
        return len(all_stocks)

    # ──────────────────────────────────────────────
    # SQL 批量查询
    # ──────────────────────────────────────────────

    def _batch_query_today_data(self, ts_codes: List[str], trade_date: str) -> pd.DataFrame:
        """批量查询指定股票在 trade_date 的当日行情数据（含均线字段）"""
        if not ts_codes or not os.path.exists(_STK_FACTOR_DB):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            placeholders = ','.join(['?'] * len(ts_codes))
            sql = f"""
                SELECT ts_code, close_hfq, pct_chg, amount, ma_bfq_20, ma_bfq_60
                FROM stk_factor_pro
                WHERE trade_date = ? AND ts_code IN ({placeholders})
            """
            params = [trade_date] + ts_codes
            df = pd.read_sql(sql, conn, params=params)
            conn.close()
            for col in ['close_hfq', 'pct_chg', 'amount', 'ma_bfq_20', 'ma_bfq_60']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def _batch_query_hist_closes(self, ts_codes: List[str], start_date: str,
                                  end_date: str) -> pd.DataFrame:
        """批量查询指定股票在日期范围内的历史收盘价"""
        if not ts_codes or not os.path.exists(_STK_FACTOR_DB):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            placeholders = ','.join(['?'] * len(ts_codes))
            sql = f"""
                SELECT ts_code, trade_date, close_hfq
                FROM stk_factor_pro
                WHERE trade_date BETWEEN ? AND ? AND ts_code IN ({placeholders})
                ORDER BY ts_code, trade_date
            """
            params = [start_date, end_date] + ts_codes
            df = pd.read_sql(sql, conn, params=params)
            conn.close()
            if 'close_hfq' in df.columns:
                df['close_hfq'] = pd.to_numeric(df['close_hfq'], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def _batch_query_ma60_range(self, ts_codes: List[str], start_date: str,
                                 end_date: str) -> pd.DataFrame:
        """批量查询指定股票在日期范围内的 MA60 值（用于趋势判断）"""
        if not ts_codes or not os.path.exists(_STK_FACTOR_DB):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            placeholders = ','.join(['?'] * len(ts_codes))
            sql = f"""
                SELECT ts_code, trade_date, ma_bfq_60
                FROM stk_factor_pro
                WHERE trade_date BETWEEN ? AND ? AND ts_code IN ({placeholders})
                ORDER BY ts_code, trade_date
            """
            params = [start_date, end_date] + ts_codes
            df = pd.read_sql(sql, conn, params=params)
            conn.close()
            if 'ma_bfq_60' in df.columns:
                df['ma_bfq_60'] = pd.to_numeric(df['ma_bfq_60'], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    # ──────────────────────────────────────────────
    # 子因子计算
    # ──────────────────────────────────────────────

    @staticmethod
    def _calc_theme_strength(n_stocks: int, total_mapped: int) -> float:
        """主题强度：主题内股票数 / 总映射股票数，映射到 0-100"""
        if total_mapped <= 0:
            return 0.0
        return min(100.0, (n_stocks / total_mapped) * 100.0)

    def _calc_etf_strength(self, theme_name: str, lookback_start: str,
                            trade_date: str) -> Optional[float]:
        """ETF 强度

        如果主题有 ETF 映射，加载 ETF 数据评估：
          - ETF 20日收益率（正收益 = 强）
          - ETF 量能趋势（近5日均量 / 近20日均量）
        如果没有 ETF，返回 None（由上层用默认值填充）
        """
        etf_codes = self._theme_etf_map.get(theme_name, [])
        if not etf_codes:
            return None

        etf_scores = []
        for etf_code in etf_codes:
            try:
                df = self.loader.load_index_data(etf_code, lookback_start, trade_date, silent=True)
                if df is None or df.empty:
                    continue
                df = df.sort_values('trade_date').reset_index(drop=True)
                close_col = 'close' if 'close' in df.columns else 'close_hfq'
                amount_col = 'amount' if 'amount' in df.columns else 'vol'

                closes = df[close_col].astype(float).dropna()
                if len(closes) < 2:
                    continue

                # ETF 20日收益率
                ret_20d = (closes.iloc[-1] / closes.iloc[0] - 1.0) * 100.0

                # 量能趋势（近5日均量 / 近20日均量）
                amounts = df[amount_col].astype(float).dropna() if amount_col in df.columns else None
                vol_trend = 0.0
                if amounts is not None and len(amounts) >= 5:
                    ma5 = amounts.tail(5).mean()
                    ma20 = amounts.mean()
                    if ma20 > 0:
                        vol_trend = (ma5 / ma20 - 1.0) * 100.0

                # 合成 ETF 得分：收益率占 60%，量能趋势占 40%
                # 收益率 0% -> 50分，+10% -> 100分，-10% -> 0分
                ret_score = np.clip(50.0 + ret_20d * 5.0, 0.0, 100.0)
                # 量能趋势 0% -> 50分，+20% -> 100分，-20% -> 0分
                vol_score = np.clip(50.0 + vol_trend * 2.5, 0.0, 100.0)

                combined = ret_score * 0.6 + vol_score * 0.4
                etf_scores.append(combined)
            except Exception:
                continue

        if etf_scores:
            return float(np.mean(etf_scores))
        return None

    def _compute_20d_returns(self, hist_df: pd.DataFrame) -> Dict[str, float]:
        """计算各股票的20日收益率（%）

        对每只股票，取日期范围内最早和最新的 close_hfq 计算收益率。
        """
        if hist_df.empty:
            return {}

        returns = {}
        for code, grp in hist_df.groupby('ts_code'):
            grp = grp.sort_values('trade_date')
            closes = grp['close_hfq'].dropna()
            if len(closes) >= 2:
                ret = (closes.iloc[-1] / closes.iloc[0] - 1.0) * 100.0
                returns[code] = ret
        return returns

    def _compute_ma60_trend(self, ma60_df: pd.DataFrame) -> Dict[str, bool]:
        """判断各股票的 MA60 趋势是否向上

        取每只股票在日期范围内的最早和最新 MA60 值比较。
        """
        if ma60_df.empty:
            return {}

        trends = {}
        for code, grp in ma60_df.groupby('ts_code'):
            grp = grp.sort_values('trade_date')
            vals = grp['ma_bfq_60'].dropna()
            if len(vals) >= 2:
                trends[code] = bool(vals.iloc[-1] > vals.iloc[0])
            else:
                trends[code] = False
        return trends

    # ──────────────────────────────────────────────
    # 主评估接口
    # ──────────────────────────────────────────────

    def evaluate(self, trade_date: str) -> ThemeResonanceResult:
        """计算指定交易日的主题共振评分

        Args:
            trade_date: 交易日 YYYYMMDD

        Returns:
            ThemeResonanceResult 包含所有子因子得分
        """
        # ── 1. 加载主题-股票映射 ──
        theme_stock_map = self.loader.load_theme_stock_map()
        if not theme_stock_map:
            return ThemeResonanceResult(
                score=50.0, top_themes=[], theme_count=0,
                sub_scores={}, explain={"error": "无主题数据"}
            )

        # ── 2. 统计总映射股票数（用于主题强度归一化） ──
        total_mapped = self._count_total_stocks(theme_stock_map)

        # ── 3. 按主题内股票数排序，取 Top N ──
        theme_sizes = {}
        for name in theme_stock_map:
            stocks = self._get_theme_stocks(theme_stock_map, name)
            if stocks:
                theme_sizes[name] = len(stocks)

        top_n = self.cfg.get('top_n', 10)
        sorted_themes = sorted(theme_sizes.items(), key=lambda x: x[1], reverse=True)
        top_theme_names = [t[0] for t in sorted_themes[:top_n]]

        if not top_theme_names:
            return ThemeResonanceResult(
                score=50.0, top_themes=[], theme_count=0,
                sub_scores={}, explain={"error": "主题列表为空"}
            )

        # ── 4. 收集所有 Top 主题中的股票，批量查询数据库 ──
        all_stocks = set()
        for name in top_theme_names:
            stocks = self._get_theme_stocks(theme_stock_map, name)
            all_stocks.update(stocks)
        stock_list = list(all_stocks)

        # 查询当日数据
        today_df = self._batch_query_today_data(stock_list, trade_date)

        # 查询历史数据
        lookback = self.cfg.get('lookback', 60)
        start_date = self._calc_start_date(trade_date, lookback)

        # 20日收益率所需数据
        start_20d = self._calc_start_date(trade_date, 20)
        hist_close_df = self._batch_query_hist_closes(stock_list, start_20d, trade_date)
        ret_20d_map = self._compute_20d_returns(hist_close_df)

        # MA60 趋势数据
        ma60_df = self._batch_query_ma60_range(stock_list, start_date, trade_date)
        ma60_trend_map = self._compute_ma60_trend(ma60_df)

        # ── 5. 计算各主题的 ETF 强度 ──
        etf_scores = {}
        for name in top_theme_names:
            score = self._calc_etf_strength(name, start_date, trade_date)
            if score is not None:
                etf_scores[name] = score
        # 没有 ETF 映射的主题使用所有主题 ETF 得分的平均值或默认 50
        default_etf_score = float(np.mean(list(etf_scores.values()))) if etf_scores else 50.0

        # ── 6. 对每个 Top 主题计算子因子 ──
        weights = self.cfg.get('sub_weights', {})
        total_weight = sum(weights.values())

        top_themes_detail = []
        all_sub_scores: Dict[str, List[float]] = {
            'theme_strength': [],
            'etf_strength': [],
            'leader_strength': [],
            'breadth_contribution': [],
            'trend_alignment': [],
        }

        for name in top_theme_names:
            stocks = self._get_theme_stocks(theme_stock_map, name)
            n_stocks = len(stocks)

            # ── 6a. 主题强度：主题内股票数 / 总映射股票数 ──
            ts_score = self._calc_theme_strength(n_stocks, total_mapped)

            # ── 6b. ETF 强度 ──
            es_score = etf_scores.get(name, default_etf_score)

            # ── 6c~6e. 需要当日行情数据 ──
            theme_today = today_df[today_df['ts_code'].isin(stocks)] if not today_df.empty else pd.DataFrame()

            if not theme_today.empty:
                # 领涨股强度：20日涨幅 > 10% 的股票占比
                leader_count = sum(
                    1 for _, row in theme_today.iterrows()
                    if ret_20d_map.get(row['ts_code'], 0) > 10.0
                )
                ls_score = min(100.0, (leader_count / max(n_stocks, 1)) * 100.0)

                # 宽度贡献：站上 MA20 的股票占比
                valid_ma20 = theme_today.dropna(subset=['close_hfq', 'ma_bfq_20'])
                if not valid_ma20.empty:
                    above_ma20 = (valid_ma20['close_hfq'] > valid_ma20['ma_bfq_20']).sum()
                    bc_score = min(100.0, (above_ma20 / len(valid_ma20)) * 100.0)
                else:
                    bc_score = 50.0

                # 趋势一致性：MA60 趋势向上的股票占比
                uptrend_count = sum(
                    1 for code in stocks if ma60_trend_map.get(code, False)
                )
                ta_score = min(100.0, (uptrend_count / max(n_stocks, 1)) * 100.0)
            else:
                leader_count = 0
                ls_score = 50.0
                bc_score = 50.0
                ta_score = 50.0

            # ── 6f. 合成该主题的综合得分（加权平均） ──
            if total_weight > 0:
                theme_score = (
                    ts_score * weights.get('theme_strength', 0) +
                    es_score * weights.get('etf_strength', 0) +
                    ls_score * weights.get('leader_strength', 0) +
                    bc_score * weights.get('breadth_contribution', 0) +
                    ta_score * weights.get('trend_alignment', 0)
                ) / total_weight
            else:
                theme_score = 50.0

            top_themes_detail.append({
                'name': name,
                'strength': round(theme_score, 2),
                'etf_score': round(es_score, 2),
                'leader_count': leader_count,
            })

            # 收集各子因子值用于计算总平均
            all_sub_scores['theme_strength'].append(ts_score)
            all_sub_scores['etf_strength'].append(es_score)
            all_sub_scores['leader_strength'].append(ls_score)
            all_sub_scores['breadth_contribution'].append(bc_score)
            all_sub_scores['trend_alignment'].append(ta_score)

        # ── 7. 计算总得分 = Top 主题综合得分的平均值 ──
        theme_composites = [t['strength'] for t in top_themes_detail]
        total_score = float(np.mean(theme_composites)) if theme_composites else 50.0
        total_score = max(0.0, min(100.0, total_score))

        # ── 8. 子因子平均分 ──
        sub_scores = {}
        explain = {}
        for k, vals in all_sub_scores.items():
            avg_val = float(np.mean(vals)) if vals else 50.0
            sub_scores[k] = round(avg_val, 2)

        explain['theme_strength'] = (
            f"主题强度均分 {sub_scores['theme_strength']:.1f}分"
            f"（Top{top_n}主题股票数占比均值）"
        )
        explain['etf_strength'] = (
            f"ETF强度均分 {sub_scores['etf_strength']:.1f}分"
            f"（含ETF映射的主题数 {len(etf_scores)}/{top_n}）"
        )
        explain['leader_strength'] = (
            f"领涨股强度均分 {sub_scores['leader_strength']:.1f}分"
            f"（20日涨幅>10%股票占比均值）"
        )
        explain['breadth_contribution'] = (
            f"宽度贡献均分 {sub_scores['breadth_contribution']:.1f}分"
            f"（站上MA20股票占比均值）"
        )
        explain['trend_alignment'] = (
            f"趋势一致性均分 {sub_scores['trend_alignment']:.1f}分"
            f"（MA60趋势向上股票占比均值）"
        )

        return ThemeResonanceResult(
            score=round(total_score, 2),
            top_themes=top_themes_detail,
            theme_count=len(top_themes_detail),
            sub_scores=sub_scores,
            explain=explain,
        )
