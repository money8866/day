# -*- coding: utf-8 -*-
"""
市场热度引擎 - Heat Engine V3
评估市场热度和温度。热度 ≠ 强度。
热度衡量市场的亢奋程度、资金参与度、情绪温度等。
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import stock_cache as sc
from inst_pullback_v2.data.loader import DataLoader
from inst_pullback_v2.data.indicators import atr, sma
from market_regime_v3.engines import resolve_theme_stock_map_path

# 常量
_STK_FACTOR_DB = sc.DB_PATH
CACHE_DIR = r"D:\mystock\cache_daily"


@dataclass
class HeatResult:
    score: float  # 0-100
    level: str  # Extreme Hot, Very Hot, Hot, Warm, Normal, Cool, Cold, Ice
    trend: str  # Heating, Stable, Cooling, Collapse
    cycle: str  # Cold Start, Heating, Boom, Peak, Cooling, Ice
    adjustment_factor: float  # exposure multiplier
    max_trades_per_day: int
    trading_style: str
    sub_scores: Dict[str, float]
    explain: Dict[str, str]


class HeatEngine:
    """市场热度引擎

    计算市场热度（温度），不同于市场强度。
    热度越高表示市场越亢奋，过热时需要警惕回调。
    """

    def __init__(self, config: dict):
        self.cfg = config['heat_engine']
        self.loader = DataLoader()
        # 缓存历史热度分数 {trade_date: score}，用于趋势计算
        self._heat_score_cache = {}

    def evaluate(self, trade_date: str) -> HeatResult:
        """评估指定交易日的市场热度"""
        # 获取该日期的全市场行情数据
        df_day = self._query_stk_factor_by_date(trade_date)

        # ── 1. 成交量热度 (Volume Heat) ──
        vol_score, vol_explain = self._calc_volume_heat(trade_date, df_day)

        # ── 2. 赚钱效应热度 (Profit Heat) ──
        profit_score, profit_explain = self._calc_profit_heat(trade_date, df_day)

        # ── 3. 涨停热度 (Limit Up Heat) ──
        limit_up_score, limit_up_explain = self._calc_limit_up_heat(trade_date, df_day)

        # ── 4. 龙头热度 (Leader Heat) ──
        leader_score, leader_explain = self._calc_leader_heat(trade_date)

        # ── 5. ETF热度 (ETF Heat) ──
        etf_score, etf_explain = self._calc_etf_heat(trade_date)

        # ── 6. 主题热度 (Theme Heat) ──
        theme_score, theme_explain = self._calc_theme_heat(trade_date, df_day)

        # ── 7. 资金流热度 (Capital Flow Heat) ──
        cap_flow_score, cap_flow_explain = self._calc_capital_flow_heat(trade_date)

        # ── 8. 波动率热度 (Volatility Heat) ──
        vola_score, vola_explain = self._calc_volatility_heat(trade_date)

        # ── 综合得分 ──
        weights = self.cfg['sub_weights']
        sub_scores = {
            'volume_heat': vol_score,
            'profit_heat': profit_score,
            'limit_up_heat': limit_up_score,
            'leader_heat': leader_score,
            'etf_heat': etf_score,
            'theme_heat': theme_score,
            'capital_flow_heat': cap_flow_score,
            'volatility_heat': vola_score,
        }

        explain = {
            'volume_heat': vol_explain,
            'profit_heat': profit_explain,
            'limit_up_heat': limit_up_explain,
            'leader_heat': leader_explain,
            'etf_heat': etf_explain,
            'theme_heat': theme_explain,
            'capital_flow_heat': cap_flow_explain,
            'volatility_heat': vola_explain,
        }

        total_weight = sum(weights.values())
        if total_weight > 0:
            heat_score = sum(sub_scores[k] * weights.get(k, 0) for k in sub_scores if k in weights) / total_weight
        else:
            heat_score = 0.0

        heat_score = max(0.0, min(100.0, heat_score))

        # ── 缓存当前热度 ──
        self._heat_score_cache[trade_date] = heat_score

        # ── 趋势判定 ──
        trend = self._determine_trend(trade_date)

        # ── 等级判定 ──
        level = self._determine_level(heat_score)

        # ── 周期判定 ──
        cycle = self._determine_cycle(level, trend)

        # ── 调整因子（查表） ──
        adj_info = self._get_adjustment_info(heat_score)

        return HeatResult(
            score=round(heat_score, 2),
            level=level,
            trend=trend,
            cycle=cycle,
            adjustment_factor=adj_info['exposure_multiplier'],
            max_trades_per_day=adj_info['max_trades_per_day'],
            trading_style=adj_info['style'],
            sub_scores={k: round(v, 2) for k, v in sub_scores.items()},
            explain=explain,
        )

    # ────────────────────────────────────────────────────────
    # 数据库查询
    # ────────────────────────────────────────────────────────

    def _query_stk_factor_by_date(self, trade_date: str) -> pd.DataFrame:
        """从 stk_factor_pro 表查询指定日期的全市场行情数据"""
        if not os.path.exists(_STK_FACTOR_DB):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            df = pd.read_sql_query(
                "SELECT ts_code, pct_chg, amount, close_hfq, high, low "
                "FROM stk_factor_pro WHERE trade_date = ?",
                conn, params=(trade_date,)
            )
            conn.close()
            if df is not None and not df.empty:
                df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce')
                df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
                df['close_hfq'] = pd.to_numeric(df['close_hfq'], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def _query_stk_factor_dates(self, start_date: str, end_date: str,
                                fields: str = "trade_date, ts_code, pct_chg, amount, close_hfq, high, low") -> pd.DataFrame:
        """从 stk_factor_pro 查询日期范围内的数据"""
        if not os.path.exists(_STK_FACTOR_DB):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            df = pd.read_sql_query(
                f"SELECT {fields} FROM stk_factor_pro "
                "WHERE trade_date >= ? AND trade_date <= ?",
                conn, params=(start_date, end_date)
            )
            conn.close()
            if df is not None and not df.empty:
                for col in ['pct_chg', 'amount', 'close_hfq', 'high', 'low']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def _get_trade_dates_before(self, trade_date: str, n: int) -> List[str]:
        """获取 trade_date 之前的 n 个交易日（从 stk_factor_pro 的日期列表中取）"""
        if not os.path.exists(_STK_FACTOR_DB):
            return []
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            df = pd.read_sql_query(
                "SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC",
                conn
            )
            conn.close()
            if df is not None and not df.empty:
                dates = df['trade_date'].astype(str).tolist()
                if trade_date in dates:
                    idx = dates.index(trade_date)
                    return dates[:idx + 1][:n + 1]
            return []
        except Exception:
            return []

    # ────────────────────────────────────────────────────────
    # 1. 成交量热度 (Volume Heat)
    # ────────────────────────────────────────────────────────

    def _calc_volume_heat(self, trade_date: str, df_day: pd.DataFrame) -> Tuple[float, str]:
        """计算成交量热度

        获取全市场总成交额，与 5 日 / 20 日均值比较。
        成交量大幅增长 = 高热度。
        """
        if df_day is None or df_day.empty:
            return 0.0, "无当日行情数据"

        today_amount = df_day['amount'].sum()
        if today_amount == 0 or np.isnan(today_amount):
            return 0.0, "当日成交额为 0"

        # 获取历史交易日日期列表
        dates = self._get_trade_dates_before(trade_date, 25)
        if len(dates) < 5:
            return 50.0, f"历史数据不足({len(dates)}天)，默认中性"

        # 计算 5 日均额
        df_5d = self._query_stk_factor_dates(dates[min(5, len(dates)) - 1], trade_date,
                                              fields="trade_date, amount")
        if df_5d.empty:
            return 50.0, "无法获取 5 日数据"

        daily_amounts = df_5d.groupby('trade_date')['amount'].sum().sort_index()
        if len(daily_amounts) < 2:
            return 50.0, "5 日数据不足"

        ma5 = daily_amounts.mean()
        # 20 日均额
        if len(dates) >= 20:
            df_20d = self._query_stk_factor_dates(dates[19], trade_date,
                                                  fields="trade_date, amount")
            if not df_20d.empty:
                daily_20d = df_20d.groupby('trade_date')['amount'].sum().sort_index()
                ma20 = daily_20d.mean() if len(daily_20d) >= 2 else ma5
            else:
                ma20 = ma5
        else:
            ma20 = ma5

        # 今日 vs 5日均
        ratio_vs_5 = today_amount / ma5 if ma5 > 0 else 1.0
        # 今日 vs 20日均
        ratio_vs_20 = today_amount / ma20 if ma20 > 0 else 1.0

        # 综合评分
        # ratio_vs_5: 1.0=中性, >1.3=高, <0.7=低
        # ratio_vs_20: 1.0=中性, >1.5=很高
        score_5 = min(100, max(0, (ratio_vs_5 - 0.5) / 1.0 * 100))
        score_20 = min(100, max(0, (ratio_vs_20 - 0.5) / 1.5 * 100))
        vol_score = score_5 * 0.4 + score_20 * 0.6
        vol_score = max(0.0, min(100.0, vol_score))

        explain = (f"今日成交额 {today_amount / 1e8:.1f}亿, "
                   f"5日均 {ma5 / 1e8:.1f}亿(比 {ratio_vs_5:.2f}), "
                   f"20日均 {ma20 / 1e8:.1f}亿(比 {ratio_vs_20:.2f}) → {vol_score:.1f}分")
        return vol_score, explain

    # ────────────────────────────────────────────────────────
    # 2. 赚钱效应热度 (Profit Heat)
    # ────────────────────────────────────────────────────────

    def _calc_profit_heat(self, trade_date: str, df_day: pd.DataFrame) -> Tuple[float, str]:
        """计算赚钱效应热度

        子因子：
        - 上涨股票比例 (up_ratio)
        - 中位数涨幅 (median_return)
        - 创 20 日新高比例 (new_high_ratio)
        """
        if df_day is None or df_day.empty:
            return 0.0, "无当日行情数据"

        # 过滤北交所等
        df_valid = df_day[df_day['ts_code'].str.match(r'^(?!8|4)\d+\.(SH|SZ)$', na=False)]
        if df_valid.empty:
            return 0.0, "无可分析股票"

        # 上涨比例
        up_count = (df_valid['pct_chg'] > 0).sum()
        up_ratio = up_count / len(df_valid)
        up_score = up_ratio * 100  # 直接映射到 0-100

        # 中位数涨幅
        median_ret = df_valid['pct_chg'].median()
        # 中位数涨幅 0% → 50分, +3% → 100分, -3% → 0分
        if np.isnan(median_ret):
            median_score = 50.0
        else:
            median_score = max(0, min(100, 50 + median_ret / 3.0 * 50))

        # 创 20 日新高比例
        new_high_ratio = self._calc_new_20d_high_ratio(trade_date, df_valid)
        new_high_score = new_high_ratio * 100  # 0-100

        # 综合（上涨比例 35%, 中位数涨幅 35%, 新高 30%）
        profit_score = up_score * 0.35 + median_score * 0.35 + new_high_score * 0.30
        profit_score = max(0.0, min(100.0, profit_score))

        explain = (f"上涨比 {up_ratio*100:.1f}%, "
                   f"中位数涨幅 {median_ret:.2f}%, "
                   f"20日新高比 {new_high_ratio*100:.2f}% → {profit_score:.1f}分")
        return profit_score, explain

    def _calc_new_20d_high_ratio(self, trade_date: str, df_today: pd.DataFrame) -> float:
        """计算创 20 日新高的股票比例"""
        dates = self._get_trade_dates_before(trade_date, 20)
        if len(dates) < 2:
            return 0.0

        # 取 20 天前的日期
        date_20d_ago = dates[min(19, len(dates) - 1)]
        # 取该日期的收盘价
        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            df_20d = pd.read_sql_query(
                "SELECT ts_code, close_hfq FROM stk_factor_pro WHERE trade_date = ?",
                conn, params=(date_20d_ago,)
            )
            conn.close()
        except Exception:
            return 0.0

        if df_20d.empty:
            return 0.0

        df_20d['close_hfq'] = pd.to_numeric(df_20d['close_hfq'], errors='coerce')

        # 合并两日数据
        merged = df_today[['ts_code', 'close_hfq']].merge(
            df_20d[['ts_code', 'close_hfq']],
            on='ts_code', suffixes=('_today', '_20d')
        )
        merged['close_hfq_today'] = pd.to_numeric(merged['close_hfq_today'], errors='coerce')
        merged['close_hfq_20d'] = pd.to_numeric(merged['close_hfq_20d'], errors='coerce')
        merged = merged.dropna()

        if merged.empty:
            return 0.0

        # 创新高 = 今日收盘 > 20天前收盘
        new_high_count = (merged['close_hfq_today'] > merged['close_hfq_20d']).sum()
        return new_high_count / len(merged)

    # ────────────────────────────────────────────────────────
    # 3. 涨停热度 (Limit Up Heat)
    # ────────────────────────────────────────────────────────

    def _calc_limit_up_heat(self, trade_date: str, df_day: pd.DataFrame) -> Tuple[float, str]:
        """计算涨停热度

        子因子：
        - 涨停股票数量
        - 封板率（如有涨停列表数据）
        - 20cm 涨停数量
        - 连板股票数量
        """
        if df_day is None or df_day.empty:
            return 0.0, "无当日行情数据"

        # 区分主板与双创
        gem_mask = df_day['ts_code'].str.match(r'^(300|688)', na=False)
        main_board = df_day[~gem_mask]
        gem_board = df_day[gem_mask]

        # 涨停判定
        main_limit = main_board[main_board['pct_chg'] >= 9.5]
        gem_limit = gem_board[gem_board['pct_chg'] >= 19.5]
        limit_up_count = len(main_limit) + len(gem_limit)
        gem_limit_count = len(gem_limit)  # 20cm 涨停数量

        # 涨停率得分
        total_valid = len(df_day)
        limit_up_ratio = limit_up_count / total_valid if total_valid > 0 else 0
        # 涨停率 0% → 0分, 5% → 100分
        limit_score = min(100, limit_up_ratio / 0.05 * 100)

        # 20cm 涨停得分（>10只满分）
        gem_score = min(100, gem_limit_count / 10 * 100)

        # 封板率（从涨停列表数据获取）
        break_ratio = 0.3  # 默认值
        consecutive_count = 3  # 默认值
        try:
            limit_df = self.loader.load_limit_list(trade_date)
            if limit_df is not None and not limit_df.empty:
                # 封板率
                if 'is_break' in limit_df.columns:
                    col = pd.to_numeric(limit_df['is_break'], errors='coerce')
                    break_ratio = float(col.sum() / len(limit_df)) if len(limit_df) > 0 else 0.3
                elif 'is_limit' in limit_df.columns:
                    col = pd.to_numeric(limit_df['is_limit'], errors='coerce')
                    total_ever = len(col)
                    break_ratio = float((col == 0).sum() / total_ever) if total_ever > 0 else 0.3
                # 连板数
                for ccol in ['consecutive_limit_up', '连续涨停', '连板数', 'consecutive_days']:
                    if ccol in limit_df.columns:
                        vals = pd.to_numeric(limit_df[ccol], errors='coerce').dropna()
                        if not vals.empty:
                            consecutive_count = int(vals.max())
                        break
        except Exception:
            pass

        # 封板率越低→热度越高（因为炸板率高说明市场活跃）
        # break_ratio 0 → 50分, 0.5 → 100分, 1.0 → 50分（非线性）
        break_score = 100 - abs(break_ratio - 0.5) * 100

        # 连板得分
        consecutive_score = min(100, consecutive_count / 7 * 100)

        # 综合（涨停数量 35%, 20cm 15%, 封板率 25%, 连板 25%）
        limit_up_score = limit_score * 0.35 + gem_score * 0.15 + break_score * 0.25 + consecutive_score * 0.25
        limit_up_score = max(0.0, min(100.0, limit_up_score))

        explain = (f"涨停 {limit_up_count}只(率 {limit_up_ratio*100:.1f}%), "
                   f"20cm {gem_limit_count}只, "
                   f"封板率 {(1-break_ratio)*100:.1f}%, "
                   f"最高连板 {consecutive_count} → {limit_up_score:.1f}分")
        return limit_up_score, explain

    # ────────────────────────────────────────────────────────
    # 4. 龙头热度 (Leader Heat)
    # ────────────────────────────────────────────────────────

    def _calc_leader_heat(self, trade_date: str) -> Tuple[float, str]:
        """计算龙头热度

        取近 20 日涨幅前 50 的股票，计算平均涨幅。
        涨幅越高 = 龙头赚钱效应越强 = 热度越高。
        """
        dates = self._get_trade_dates_before(trade_date, 20)
        if len(dates) < 2:
            return 50.0, f"历史数据不足({len(dates)}天)，默认中性"

        date_today = dates[0]
        date_20d = dates[min(19, len(dates) - 1)]

        try:
            conn = sqlite3.connect(_STK_FACTOR_DB)
            # 取今日收盘价
            df_today = pd.read_sql_query(
                "SELECT ts_code, close_hfq FROM stk_factor_pro WHERE trade_date = ?",
                conn, params=(date_today,)
            )
            # 取 20 天前收盘价
            df_20d = pd.read_sql_query(
                "SELECT ts_code, close_hfq FROM stk_factor_pro WHERE trade_date = ?",
                conn, params=(date_20d,)
            )
            conn.close()
        except Exception:
            return 50.0, "查询龙头数据失败"

        if df_today.empty or df_20d.empty:
            return 50.0, "龙头数据不足"

        df_today['close_hfq'] = pd.to_numeric(df_today['close_hfq'], errors='coerce')
        df_20d['close_hfq'] = pd.to_numeric(df_20d['close_hfq'], errors='coerce')

        merged = df_today.merge(df_20d, on='ts_code', suffixes=('_today', '_20d'))
        merged['return_20d'] = (merged['close_hfq_today'] - merged['close_hfq_20d']) / merged['close_hfq_20d']
        merged = merged.dropna(subset=['return_20d'])

        if merged.empty:
            return 50.0, "无可比较的龙头数据"

        # 取前 50
        top50 = merged.nlargest(50, 'return_20d')
        if top50.empty:
            return 50.0, "无龙头数据"

        avg_return = top50['return_20d'].mean() * 100  # 百分比
        # 龙头平均涨幅 0% → 50分, 50% → 100分, -10% → 0分
        leader_score = max(0, min(100, 50 + avg_return / 40 * 50))
        leader_score = max(0.0, min(100.0, leader_score))

        explain = (f"前50龙头20日平均涨幅 {avg_return:.2f}% → {leader_score:.1f}分")
        return leader_score, explain

    # ────────────────────────────────────────────────────────
    # 5. ETF 热度 (ETF Heat)
    # ────────────────────────────────────────────────────────

    def _calc_etf_heat(self, trade_date: str) -> Tuple[float, str]:
        """计算 ETF 热度

        子因子：
        - ETF 成交额比率（近 5 日均 / 20 日均）
        - 20 日上涨的 ETF 比例
        - 处于/接近 20 日高位的 ETF 比例
        """
        etf_pool = self.loader.get_etf_pool()
        etf_codes = list(etf_pool.keys())

        try:
            dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
            start_20 = (dt - datetime.timedelta(days=35)).strftime('%Y%m%d')
        except Exception:
            return 50.0, "日期解析失败"

        amount_ratios = []  # 5d/20d 成交额比
        pos_20d_count = 0   # 20日上涨的ETF数
        near_high_count = 0  # 接近20日高位的ETF数
        total_valid = 0

        for code in etf_codes:
            try:
                df = self.loader.load_index_data(code, start_20, trade_date, silent=True)
                if df is None or df.empty or 'amount' not in df.columns:
                    continue
                df = df.sort_values('trade_date').reset_index(drop=True)
                amounts = df['amount'].astype(float)
                closes = df['close'].astype(float) if 'close' in df.columns else None

                if len(amounts) >= 5:
                    ma5 = amounts.tail(5).mean()
                    ma20 = amounts.mean()
                    if ma20 > 0:
                        amount_ratios.append(ma5 / ma20)

                if closes is not None and len(closes) >= 2:
                    # 20日上涨
                    if len(closes) >= 20:
                        ret_20d = (closes.iloc[-1] - closes.iloc[-20]) / closes.iloc[-20]
                    else:
                        ret_20d = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0]
                    if ret_20d > 0:
                        pos_20d_count += 1

                    # 接近 20 日高位（当前价 >= 20日最高价的 95%）
                    high_20d = closes.max()
                    if high_20d > 0 and closes.iloc[-1] >= high_20d * 0.95:
                        near_high_count += 1

                    total_valid += 1
            except Exception:
                continue

        if total_valid == 0:
            return 50.0, "ETF 数据不足，默认中性"

        # ETF 成交额比率得分（>1.1 高分, <0.9 低分）
        avg_amount_ratio = np.mean(amount_ratios) if amount_ratios else 1.0
        amount_score = max(0, min(100, (avg_amount_ratio - 0.7) / 0.6 * 100))

        # 上涨比例得分
        pos_ratio = pos_20d_count / total_valid
        pos_score = pos_ratio * 100

        # 高位比例得分
        high_ratio = near_high_count / total_valid
        high_score = high_ratio * 100

        # 综合（成交额 40%, 上涨比例 30%, 高位比例 30%）
        etf_score = amount_score * 0.40 + pos_score * 0.30 + high_score * 0.30
        etf_score = max(0.0, min(100.0, etf_score))

        explain = (f"ETF成交额比 {avg_amount_ratio:.2f}, "
                   f"上涨比 {pos_ratio*100:.1f}%, "
                   f"高位比 {high_ratio*100:.1f}% → {etf_score:.1f}分")
        return etf_score, explain

    # ────────────────────────────────────────────────────────
    # 6. 主题热度 (Theme Heat)
    # ────────────────────────────────────────────────────────

    def _calc_theme_heat(self, trade_date: str, df_day: pd.DataFrame) -> Tuple[float, str]:
        """计算主题热度

        从 theme_stock_map 加载主题信息，选取前 20 个主题，
        统计各主题包含的股票数量，估算其平均表现。
        """
        # 加载主题映射
        theme_map = self._load_theme_stock_map(trade_date)
        if not theme_map:
            return 50.0, "无主题数据，默认中性"

        # 取前 20 个主题
        if isinstance(theme_map, dict):
            theme_items = list(theme_map.items())[:20]
        else:
            return 50.0, "主题数据格式异常"

        if df_day is None or df_day.empty:
            return 50.0, "无当日行情数据"

        # 建立 ts_code → pct_chg 的映射
        pct_map = {}
        for _, row in df_day.iterrows():
            code = row['ts_code']
            # 去除后缀用于匹配
            base = code.split('.')[0]
            pct_map[code] = row['pct_chg']
            pct_map[base] = row['pct_chg']

        theme_scores = []
        total_stock_count = 0

        for theme_name, stock_list in theme_items:
            if isinstance(stock_list, list):
                stocks = stock_list
            elif isinstance(stock_list, dict):
                # 可能是 {ts_code: info} 或 {"stocks": [...]} 形式
                if 'stocks' in stock_list:
                    stocks = stock_list['stocks']
                else:
                    stocks = list(stock_list.keys())
            else:
                continue

            # 计算该主题内股票的平均涨幅
            theme_returns = []
            for s in stocks:
                if isinstance(s, dict):
                    s_code = s.get('ts_code', s.get('code', ''))
                else:
                    s_code = str(s)
                ret = pct_map.get(s_code, pct_map.get(s_code.split('.')[0], np.nan))
                if not np.isnan(ret):
                    theme_returns.append(ret)

            if theme_returns:
                avg_ret = np.mean(theme_returns)
                theme_scores.append(avg_ret)
                total_stock_count += len(stocks)

        if not theme_scores:
            return 50.0, "主题股票表现数据不足"

        # 主题平均涨幅得分
        avg_theme_return = np.mean(theme_scores)
        # 0% → 50分, +5% → 100分, -5% → 0分
        theme_score = max(0, min(100, 50 + avg_theme_return / 5 * 50))
        theme_score = max(0.0, min(100.0, theme_score))

        explain = (f"前20主题平均涨幅 {avg_theme_return:.2f}%, "
                   f"涉及 {total_stock_count} 只股票 → {theme_score:.1f}分")
        return theme_score, explain

    def _load_theme_stock_map(self, trade_date: str = None) -> Optional[dict]:
        """加载主题-股票映射"""
        map_path = resolve_theme_stock_map_path(trade_date)
        if not os.path.exists(map_path):
            return None
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                if 'themes' in raw:
                    return raw['themes']
                return raw
            return None
        except Exception:
            return None

    # ────────────────────────────────────────────────────────
    # 7. 资金流热度 (Capital Flow Heat)
    # ────────────────────────────────────────────────────────

    def _calc_capital_flow_heat(self, trade_date: str) -> Tuple[float, str]:
        """计算北向资金流热度

        北向资金净流入为正 = 热度高，为负 = 热度低。
        """
        try:
            pro = sc._get_pro()
            df = pro.moneyflow_hsgt(start_date=trade_date, end_date=trade_date)
            if df is not None and not df.empty:
                north_net = float(df['net_hsgt'].sum())
            else:
                return 50.0, "无北向资金数据"
        except Exception:
            return 50.0, "获取北向资金失败"

        # 评分
        if north_net > 0:
            # 净流入：50 + min(50, net_flow/100 * 10)
            flow_score = 50 + min(50, north_net / 100 * 10)
        else:
            # 净流出：50 - min(50, abs(net_flow)/100 * 10)
            flow_score = 50 - min(50, abs(north_net) / 100 * 10)

        flow_score = max(0.0, min(100.0, flow_score))
        explain = (f"北向资金净流入 {north_net:.1f}亿 → {flow_score:.1f}分")
        return flow_score, explain

    # ────────────────────────────────────────────────────────
    # 8. 波动率热度 (Volatility Heat)
    # ────────────────────────────────────────────────────────

    def _calc_volatility_heat(self, trade_date: str) -> Tuple[float, str]:
        """计算波动率热度

        计算主要指数的平均 ATR(14)/Close 比率。
        高波动 = 高热度。
        """
        # 从配置获取指数列表
        indices_cfg = self.loader  # 使用 loader 的 load_index_data 方法
        index_codes = ["000001.SH", "000300.SH", "000852.SH", "399006.SZ", "000688.SH"]

        try:
            dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
            start_date = (dt - datetime.timedelta(days=30)).strftime('%Y%m%d')
        except Exception:
            return 50.0, "日期解析失败"

        atr_ratios = []
        for idx_code in index_codes:
            try:
                df = self.loader.load_index_data(idx_code, start_date, trade_date, silent=True)
                if df is None or df.empty:
                    continue
                df = df.sort_values('trade_date').reset_index(drop=True)
                needed_cols = ['high', 'low', 'close']
                if not all(c in df.columns for c in needed_cols):
                    continue

                df['high'] = pd.to_numeric(df['high'], errors='coerce')
                df['low'] = pd.to_numeric(df['low'], errors='coerce')
                df['close'] = pd.to_numeric(df['close'], errors='coerce')
                df = df.dropna(subset=['high', 'low', 'close'])

                if len(df) < 15:  # ATR需要至少14+1个数据点
                    continue

                atr_vals = atr(df['high'], df['low'], df['close'], period=14)
                if atr_vals is None or atr_vals.empty:
                    continue

                latest_atr = atr_vals.iloc[-1]
                latest_close = df['close'].iloc[-1]
                if latest_close > 0 and not np.isnan(latest_atr):
                    ratio = latest_atr / latest_close
                    atr_ratios.append(ratio)
            except Exception:
                continue

        if not atr_ratios:
            return 50.0, "指数波动率数据不足，默认中性"

        avg_ratio = np.mean(atr_ratios)
        # score = min(100, ratio * 1000)  -> ATR/close通常<0.03
        vola_score = min(100, avg_ratio * 1000)
        vola_score = max(0.0, min(100.0, vola_score))

        explain = (f"指数平均 ATR/Close = {avg_ratio:.4f} → {vola_score:.1f}分")
        return vola_score, explain

    # ────────────────────────────────────────────────────────
    # 综合判定方法
    # ────────────────────────────────────────────────────────

    def _determine_level(self, heat_score: float) -> str:
        """根据 heat_score 确定热度等级"""
        levels = self.cfg['heat_adjustment']['levels']
        for lv in levels:
            if lv['heat_min'] <= heat_score <= lv['heat_max']:
                return lv['label']
        # 兜底
        return "Normal"

    def _determine_trend(self, trade_date: str) -> str:
        """计算 20 日热度变化趋势"""
        cfg_trend = self.cfg.get('heat_trend', {})
        heating_threshold = cfg_trend.get('heating_threshold', 5)
        cooling_threshold = cfg_trend.get('cooling_threshold', -5)
        collapse_threshold = cfg_trend.get('collapse_threshold', -15)

        # 获取 20 天前的热度
        dates = self._get_trade_dates_before(trade_date, 20)
        if len(dates) < 2:
            return "Stable"

        # 尝试从缓存取 20 天前的热度
        date_20d = dates[min(19, len(dates) - 1)]
        score_20d = self._heat_score_cache.get(date_20d)

        # 如果缓存中没有，尝试计算
        if score_20d is None:
            score_20d = self._compute_cached_heat_score(date_20d)

        current_score = self._heat_score_cache.get(trade_date, 50.0)
        if score_20d is None:
            return "Stable"

        change = current_score - score_20d

        if change > heating_threshold:
            return "Heating"
        elif change < collapse_threshold:
            return "Collapse"
        elif change < cooling_threshold:
            return "Cooling"
        else:
            return "Stable"

    def _compute_cached_heat_score(self, trade_date: str) -> Optional[float]:
        """计算并缓存历史热度分数"""
        if trade_date in self._heat_score_cache:
            return self._heat_score_cache[trade_date]
        try:
            # 只做简要计算（仅用于趋势比较）
            df_day = self._query_stk_factor_by_date(trade_date)
            if df_day is None or df_day.empty:
                return None

            # 快速估算：基于成交量和涨幅
            vol_score, _ = self._calc_volume_heat(trade_date, df_day)
            profit_score, _ = self._calc_profit_heat(trade_date, df_day)
            limit_up_score, _ = self._calc_limit_up_heat(trade_date, df_day)

            weights = self.cfg['sub_weights']
            total_w = sum(weights.get(k, 0) for k in ['volume_heat', 'profit_heat', 'limit_up_heat'])
            if total_w > 0:
                score = (vol_score * weights.get('volume_heat', 0) +
                         profit_score * weights.get('profit_heat', 0) +
                         limit_up_score * weights.get('limit_up_heat', 0)) / total_w
            else:
                score = 50.0

            score = max(0.0, min(100.0, score))
            self._heat_score_cache[trade_date] = score
            return score
        except Exception:
            return None

    def _determine_cycle(self, level: str, trend: str) -> str:
        """根据等级和趋势判断市场周期阶段"""
        cold_levels = ["Ice", "Cold"]

        if level in cold_levels and trend == "Heating":
            return "Cold Start"
        elif trend == "Heating" and level not in cold_levels:
            return "Heating"
        elif level in ("Hot", "Very Hot") and trend in ("Stable", "Heating"):
            return "Boom"
        elif level == "Extreme Hot" or (level == "Very Hot" and trend == "Cooling"):
            return "Peak"
        elif trend == "Cooling" and level in ("Warm", "Hot"):
            return "Cooling"
        elif level in cold_levels and trend in ("Stable", "Cooling", "Collapse"):
            return "Ice"
        else:
            return "Heating"

    def _get_adjustment_info(self, heat_score: float) -> dict:
        """根据 heat_score 查表获取调整因子、最大交易数、交易风格"""
        levels = self.cfg['heat_adjustment']['levels']
        for lv in levels:
            if lv['heat_min'] <= heat_score <= lv['heat_max']:
                return {
                    'exposure_multiplier': lv.get('exposure_multiplier', 1.0),
                    'max_trades_per_day': lv.get('max_trades_per_day', 3),
                    'style': lv.get('style', '正常交易'),
                }
        # 兜底
        return {
            'exposure_multiplier': 1.0,
            'max_trades_per_day': 3,
            'style': '正常交易',
        }
