# -*- coding: utf-8 -*-
"""
情绪引擎 - Sentiment Engine
评估市场情绪状态，包括涨停率、炸板率、最高连板、20cm涨停数量、
北向资金净流入、ETF资金流向、成交额变化率等子因子。
所有阈值参数从 config.yaml 读取。
"""

import os
import sys
import sqlite3
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
import numpy as np
import pandas as pd

# 将项目根目录 d:\mystock\solo 加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import stock_cache as sc
from inst_pullback_v2.data.loader import DataLoader
from inst_pullback_v2.data.indicators import sma
from market_regime_v3.factor_registry import (
    FactorRegistry, FactorMeta, FactorCategory, GLOBAL_REGISTRY,
)


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
_STK_FACTOR_DB = sc.DB_PATH  # SQLite 数据库路径


@dataclass
class SentimentResult:
    """情绪评分结果"""
    score: float  # 综合情绪评分 0-100
    limit_up_ratio: float  # 涨停率（涨停股数 / 全市场股票数）
    break_ratio: float  # 炸板率（炸板数 / 曾涨停数）
    max_continuous: int  # 最高连板数
    gem_count: int  # 20cm 涨停数量（300/688 股票涨幅 >= 19.5%）
    north_flow: float  # 北向资金净流入（亿元）
    etf_flow_score: float  # ETF 资金流向得分（5日均额 / 20日均额 - 1）
    amount_change: float  # 全市场成交额变化率（5日均额 / 20日均额 - 1）
    sub_scores: Dict[str, float]  # 各子因子得分 0-100
    explain: Dict[str, str]  # 各子因子解释文本


class SentimentEngine:
    """情绪引擎

    计算市场情绪相关的多个子因子，综合打分。
    所有阈值参数从 config.yaml 读取。
    """

    def __init__(self, config: dict):
        self.cfg = config['sentiment']
        self.loader = DataLoader()
        self._register_factors()

    def _register_factors(self):
        """向全局因子注册器注册本引擎的情绪因子"""
        meta = FactorMeta(
            name="sentiment_composite",
            category=FactorCategory.SENTIMENT,
            description="综合情绪评分",
            version="1.0.0",
            enabled=True,
            weight=1.0,
            min_value=0.0,
            max_value=100.0,
        )
        GLOBAL_REGISTRY.register(meta, lambda **kw: self.evaluate(kw.get('trade_date', '')).score)

    def evaluate(self, trade_date: str) -> SentimentResult:
        """计算指定交易日的情绪评分

        Args:
            trade_date: 交易日 YYYYMMDD

        Returns:
            SentimentResult 包含所有子因子得分
        """
        # ── 1. 涨停率 & 20cm 涨停数量 ──
        limit_up_ratio, gem_count = self._calc_limit_up_stats(trade_date)

        # ── 2. 炸板率 & 最高连板 ──
        break_ratio, max_continuous = self._calc_limit_list_stats(trade_date)

        # ── 3. 北向资金净流入 ──
        north_flow = self._calc_north_flow(trade_date)

        # ── 4. ETF 资金流向 ──
        etf_flow_score = self._calc_etf_flow(trade_date)

        # ── 5. 成交额变化率 ──
        amount_change = self._calc_amount_change(trade_date)

        # ── 各子因子归一化得分 ──
        sub_scores = {}
        explain = {}

        sub_scores['limit_up_ratio'] = self._normalize('limit_up_ratio', limit_up_ratio)
        explain['limit_up_ratio'] = f"涨停率 {limit_up_ratio*100:.1f}% → {sub_scores['limit_up_ratio']:.1f}分"

        sub_scores['break_ratio'] = self._normalize_reverse('break_ratio', break_ratio)
        explain['break_ratio'] = f"炸板率 {break_ratio*100:.1f}% → {sub_scores['break_ratio']:.1f}分"

        sub_scores['max_continuous'] = self._normalize('max_continuous', float(max_continuous))
        explain['max_continuous'] = f"最高连板 {max_continuous}板 → {sub_scores['max_continuous']:.1f}分"

        sub_scores['gem_count'] = self._normalize('gem_count', float(gem_count))
        explain['gem_count'] = f"20cm涨停 {gem_count}只 → {sub_scores['gem_count']:.1f}分"

        sub_scores['north_flow'] = self._normalize('north_flow', north_flow)
        explain['north_flow'] = f"北向资金 {north_flow:.1f}亿 → {sub_scores['north_flow']:.1f}分"

        sub_scores['etf_flow'] = self._normalize('etf_flow', etf_flow_score)
        explain['etf_flow'] = f"ETF资金流向 {etf_flow_score*100:.2f}% → {sub_scores['etf_flow']:.1f}分"

        sub_scores['amount_change'] = self._normalize('amount_change', amount_change)
        explain['amount_change'] = f"成交额变化 {amount_change*100:.2f}% → {sub_scores['amount_change']:.1f}分"

        # ── 综合得分（加权平均） ──
        weights = self.cfg['sub_weights']
        total_weight = sum(weights.values())
        if total_weight > 0:
            score = sum(sub_scores[k] * weights.get(k, 0) for k in sub_scores) / total_weight
        else:
            score = 0.0

        score = max(0.0, min(100.0, score))

        return SentimentResult(
            score=score,
            limit_up_ratio=limit_up_ratio,
            break_ratio=break_ratio,
            max_continuous=max_continuous,
            gem_count=gem_count,
            north_flow=north_flow,
            etf_flow_score=etf_flow_score,
            amount_change=amount_change,
            sub_scores=sub_scores,
            explain=explain,
        )

    # ──────────────────────────────────────────
    # 查询方法
    # ──────────────────────────────────────────

    def _query_stk_factor_by_date(self, trade_date: str) -> pd.DataFrame:
        """从 stk_factor_pro 表查询指定日期的全市场行情数据

        返回包含 ts_code, pct_chg, amount 的 DataFrame
        """
        if not os.path.exists(_STK_FACTOR_DB):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            df = pd.read_sql_query(
                "SELECT ts_code, pct_chg, amount FROM stk_factor_pro WHERE trade_date = ?",
                conn, params=(trade_date,)
            )
            conn.close()
            if df is not None and not df.empty:
                df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce')
                df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    # ──────────────────────────────────────────
    # 子因子计算
    # ──────────────────────────────────────────

    def _calc_limit_up_stats(self, trade_date: str) -> Tuple[float, int]:
        """计算涨停率和 20cm 涨停数量

        涨停判定：
          - 主板（非 300/688）：pct_chg >= 9.5
          - 创业板/科创板（300/688）：pct_chg >= 19.5
        """
        df = self._query_stk_factor_by_date(trade_date)
        if df is None or df.empty:
            return 0.0, 0

        # 过滤正常交易股票（排除北交所 8xx/4xx）
        df = df[df['ts_code'].str.match(r'^(?!8|4)\d+\.(SH|SZ)$', na=False)]
        if df.empty:
            return 0.0, 0

        total = len(df)

        # 区分主板和 300/688
        gem_mask = df['ts_code'].str.match(r'^(300|688)', na=False)
        main_board = df[~gem_mask]
        gem_board = df[gem_mask]

        # 主板涨停条件：pct_chg >= 9.5
        main_limit = main_board[main_board['pct_chg'] >= 9.5]
        # 创业板/科创板涨停条件：pct_chg >= 19.5
        gem_limit = gem_board[gem_board['pct_chg'] >= 19.5]

        limit_up_count = len(main_limit) + len(gem_limit)
        limit_up_ratio = limit_up_count / total if total > 0 else 0.0

        # 20cm 涨停数量
        gem_count = len(gem_limit)

        return limit_up_ratio, gem_count

    def _calc_limit_list_stats(self, trade_date: str) -> Tuple[float, int]:
        """计算炸板率和最高连板数

        从 parquet 文件加载涨停列表数据。
        无数据时返回默认值 (0.3, 3)。
        """
        try:
            df = self.loader.load_limit_list(trade_date)
        except Exception:
            df = None

        if df is None or df.empty:
            return 0.3, 3  # 默认值

        break_ratio = 0.3
        max_continuous = 3

        # ── 炸板率识别（尝试多种常见列名） ──
        # 炸板率 = 炸板数 / 曾涨停总数
        total_ever_limit = len(df)

        if 'is_break' in df.columns:
            # is_break: 1=炸板, 0=封板
            break_ratio = float(pd.to_numeric(df['is_break'], errors='coerce').sum() / total_ever_limit)
        elif 'is_limit' in df.columns:
            # is_limit: 1=封板, 0=炸板
            col = pd.to_numeric(df['is_limit'], errors='coerce')
            break_ratio = float((col == 0).sum() / total_ever_limit)
        elif 'limit_status' in df.columns:
            # limit_status: 1=封板, 0=炸板, -1=跌停
            col = pd.to_numeric(df['limit_status'], errors='coerce')
            total_limit_up = (col != 0).sum()
            if total_limit_up > 0:
                break_ratio = float((col == 0).sum() / total_limit_up)
        elif '封板' in df.columns or '是否封板' in df.columns:
            col_name = '封板' if '封板' in df.columns else '是否封板'
            col = pd.to_numeric(df[col_name], errors='coerce')
            break_ratio = float((total_ever_limit - col.sum()) / total_ever_limit)

        # ── 最高连板识别 ──
        for col in ['consecutive_limit_up', '连续涨停', '连板数', '连续的涨停天数', 'consecutive_days']:
            if col in df.columns:
                vals = pd.to_numeric(df[col], errors='coerce').dropna()
                if not vals.empty:
                    max_continuous = int(vals.max())
                break

        break_ratio = max(0.0, min(1.0, break_ratio))
        max_continuous = max(1, max_continuous)

        return break_ratio, max_continuous

    def _calc_north_flow(self, trade_date: str) -> float:
        """计算北向资金净流入（亿元）

        使用 tushare pro.moneyflow_hsgt 接口。
        """
        try:
            pro = sc._get_pro()
            df = pro.moneyflow_hsgt(start_date=trade_date, end_date=trade_date)
            if df is not None and not df.empty:
                north_net = df['net_hsgt'].sum()
                return float(north_net)
        except Exception:
            pass
        return 0.0

    def _calc_etf_flow(self, trade_date: str) -> float:
        """计算 ETF 资金流向得分

        取前 20 只 ETF，计算每只的 5日均成交额 / 20日均成交额 - 1，
        然后取平均值。
        """
        etf_pool = self.loader.get_etf_pool()
        etf_codes = list(etf_pool.keys())[:20]

        # 计算起始日期（多取几天确保数据量足够）
        try:
            dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
            start_20 = (dt - datetime.timedelta(days=35)).strftime('%Y%m%d')
        except Exception:
            return 0.0

        ratios = []
        for code in etf_codes:
            try:
                df = self.loader.load_index_data(code, start_20, trade_date, silent=True)
                if df is None or df.empty or 'amount' not in df.columns:
                    continue
                df = df.sort_values('trade_date').reset_index(drop=True)
                amounts = df['amount'].astype(float)
                if len(amounts) >= 5:
                    ma5 = amounts.tail(5).mean()
                    ma20 = amounts.mean()
                    if ma20 > 0:
                        ratio = ma5 / ma20 - 1.0
                        ratios.append(ratio)
            except Exception:
                continue

        if not ratios:
            return 0.0
        return float(np.mean(ratios))

    def _calc_amount_change(self, trade_date: str) -> float:
        """计算全市场成交额变化率

        对 stk_factor_pro 按日期聚合，计算全市场总成交额。
        变化率 = 5日均额 / 20日均额 - 1
        """
        try:
            dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
            start_date = (dt - datetime.timedelta(days=40)).strftime('%Y%m%d')
        except Exception:
            return 0.0

        if not os.path.exists(_STK_FACTOR_DB):
            return 0.0

        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            df = pd.read_sql_query(
                "SELECT trade_date, SUM(CAST(amount AS REAL)) as total_amount "
                "FROM stk_factor_pro "
                "WHERE trade_date >= ? AND trade_date <= ? "
                "GROUP BY trade_date ORDER BY trade_date",
                conn, params=(start_date, trade_date)
            )
            conn.close()

            if df is None or df.empty:
                return 0.0

            df['total_amount'] = pd.to_numeric(df['total_amount'], errors='coerce')
            amounts = df['total_amount'].dropna()

            if len(amounts) < 5:
                return 0.0

            ma5 = amounts.tail(5).mean()
            ma20 = amounts.tail(20).mean() if len(amounts) >= 20 else amounts.mean()

            if ma20 > 0:
                return float(ma5 / ma20 - 1.0)
        except Exception:
            pass

        return 0.0

    # ──────────────────────────────────────────
    # 得分归一化
    # ──────────────────────────────────────────

    def _normalize(self, factor_name: str, value: float) -> float:
        """越大越好的因子归一化到 0-100

        阈值对照:
          excellent → 100 分
          good      →  50 分
          poor      →   0 分
        中间值线性插值。
        """
        cfg = self.cfg.get(factor_name, {})
        excellent = cfg.get('excellent', 0)
        good = cfg.get('good', 0)
        poor = cfg.get('poor', 0)

        if value >= excellent:
            return 100.0
        elif value >= good:
            if excellent != good:
                return 50.0 + 50.0 * (value - good) / (excellent - good)
            return 75.0
        elif value >= poor:
            if good != poor:
                return 50.0 * (value - poor) / (good - poor)
            return 25.0
        else:
            return 0.0

    def _normalize_reverse(self, factor_name: str, value: float) -> float:
        """越小越好的因子归一化到 0-100

        用于 break_ratio（炸板率）：值越小情绪越好。
        阈值对照:
          value <= excellent → 100 分
          value <= good      →  50 分
          value <= poor      →   0 分
        中间值线性插值。
        """
        cfg = self.cfg.get(factor_name, {})
        excellent = cfg.get('excellent', 0)
        good = cfg.get('good', 0)
        poor = cfg.get('poor', 0)

        if value <= excellent:
            return 100.0
        elif value <= good:
            if good != excellent:
                return 50.0 + 50.0 * (good - value) / (good - excellent)
            return 75.0
        elif value <= poor:
            if poor != good:
                return 50.0 * (poor - value) / (poor - good)
            return 25.0
        else:
            return 0.0
