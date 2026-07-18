# -*- coding: utf-8 -*-
"""
Theme Persistence Score Engine — 主引擎

整合6大模块, 输出主题持续性评分 (0-100)

Theme Persistence Score =
  0.25 × Trend Stability       (趋势稳定性)
+ 0.25 × Breadth Expansion     (广度扩张)
+ 0.20 × Leader Persistence    (龙头持续性)
+ 0.15 × Capital Consistency   (资金一致性)
+ 0.15 × Catalyst Duration     (催化剂持续)
-     Crowding Penalty         (拥挤度惩罚)
"""
import os
import pandas as pd
import numpy as np
from datetime import datetime

from .trend_score import calculate_trend_stability
from .breadth_score import calculate_breadth_expansion
from .leader_score import calculate_leader_persistence
from .capital_score import calculate_capital_consistency
from .catalyst_score import calculate_catalyst_duration
from .crowding import calculate_crowding_penalty


# 模块权重
WEIGHTS = {
    'trend': 0.25,
    'breadth': 0.25,
    'leader': 0.20,
    'capital': 0.15,
    'catalyst': 0.15,
}

# 主题状态分类
THEME_STATE = {
    (90, 100): 'Super Main Trend',   # 超级主线
    (80, 90): 'Strong Trend',        # 强趋势
    (70, 80): 'Healthy Trend',       # 健康趋势
    (60, 70): 'Observation',         # 观察
    (0, 60): 'Weak / Avoid',         # 回避
}

# 预期趋势持续时间 (月)
EXPECTED_DURATION = {
    'Super Main Trend': '3-6个月',
    'Strong Trend': '2-4个月',
    'Healthy Trend': '1-3个月',
    'Observation': '2-4周',
    'Weak / Avoid': '<2周',
}

# 轮动风险
ROTATION_RISK = {
    'Super Main Trend': '低',
    'Strong Trend': '低-中',
    'Healthy Trend': '中',
    'Observation': '中-高',
    'Weak / Avoid': '高',
}


class ThemePersistenceEngine:
    """主题持续性评分引擎"""

    def __init__(self, cache_dir: str = None):
        """
        Args:
            cache_dir: 中间结果缓存目录 (可选)
        """
        self.cache_dir = cache_dir
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def score_theme(self,
                    theme_name: str,
                    etf_code: str,
                    etf_df: pd.DataFrame,
                    benchmark_df: pd.DataFrame = None,
                    stock_data: dict = None,
                    trade_date: str = None) -> dict:
        """
        计算单个主题的持续性评分

        Args:
            theme_name: 主题名称 (如 '半导体')
            etf_code: ETF代码 (如 '512480')
            etf_df: ETF日线数据 (含 close, vol, amount, trade_date)
            benchmark_df: 沪深300日线 (含 close)
            stock_data: {ts_code: DataFrame} 成份股日线数据
            trade_date: 交易日 'YYYYMMDD'

        Returns:
            完整评分结果 dict
        """
        if trade_date is None and etf_df is not None:
            td = etf_df['trade_date'].max()
            trade_date = td.strftime('%Y%m%d') if hasattr(td, 'strftime') else str(td).replace('-', '')

        # === 模块1: 趋势稳定性 (25%) ===
        trend_result = calculate_trend_stability(etf_df, benchmark_df)

        # === 模块2: 广度扩张 (25%) ===
        breadth_result = calculate_breadth_expansion(stock_data or {}, trade_date)

        # === 模块3: 龙头持续性 (20%) ===
        leader_result = calculate_leader_persistence(stock_data or {}, trade_date)

        # === 模块4: 资金一致性 (15%) ===
        capital_result = calculate_capital_consistency(etf_df, stock_data)

        # === 模块5: 催化剂持续 (15%) ===
        catalyst_result = calculate_catalyst_duration(theme_name)

        # === 模块6: 拥挤度惩罚 ===
        crowding_result = calculate_crowding_penalty(etf_df, stock_data)

        # === 综合评分 ===
        persistence_score = (
            WEIGHTS['trend'] * trend_result['score'] +
            WEIGHTS['breadth'] * breadth_result['score'] +
            WEIGHTS['leader'] * leader_result['score'] +
            WEIGHTS['capital'] * capital_result['score'] +
            WEIGHTS['catalyst'] * catalyst_result['score'] +
            crowding_result['penalty']  # 负值或0
        )
        persistence_score = max(0, min(100, persistence_score))

        # === 主题状态分类 ===
        theme_state = self._classify_state(persistence_score)
        expected_duration = EXPECTED_DURATION.get(theme_state, '未知')
        rotation_risk = ROTATION_RISK.get(theme_state, '未知')

        # === 投资信号 ===
        signal = self._investment_signal(
            persistence_score, breadth_result['score'],
            leader_result['score'], crowding_result['crowding_score']
        )

        # === 龙头列表 ===
        top_leaders = leader_result.get('details', {}).get('top_leaders', [])

        return {
            'date': trade_date,
            'theme': theme_name,
            'etf_code': etf_code,
            'persistence_score': round(persistence_score, 2),
            'trend_stability': round(trend_result['score'], 2),
            'breadth_expansion': round(breadth_result['score'], 2),
            'leader_persistence': round(leader_result['score'], 2),
            'capital_consistency': round(capital_result['score'], 2),
            'catalyst_duration': round(catalyst_result['score'], 2),
            'crowding_penalty': crowding_result['penalty'],
            'crowding_score': round(crowding_result['crowding_score'], 2),
            'theme_state': theme_state,
            'expected_duration': expected_duration,
            'rotation_risk': rotation_risk,
            'top_leaders': top_leaders,
            'investment_signal': signal,
            # 子项详情
            'trend_detail': trend_result.get('details', {}),
            'breadth_detail': breadth_result.get('details', {}),
            'leader_detail': leader_result.get('details', {}),
            'capital_detail': capital_result.get('details', {}),
            'catalyst_detail': catalyst_result.get('details', {}),
            'crowding_detail': crowding_result.get('details', {}),
        }

    def score_themes_batch(self,
                            themes: list,
                            etf_data: dict,
                            benchmark_df: pd.DataFrame,
                            stock_data_map: dict,
                            trade_date: str) -> pd.DataFrame:
        """
        批量评分多个主题

        Args:
            themes: [(theme_name, etf_code), ...]
            etf_data: {etf_code: DataFrame} ETF日线
            benchmark_df: 沪深300日线
            stock_data_map: {etf_code: {ts_code: DataFrame}} 每个ETF的成份股数据
            trade_date: 'YYYYMMDD'

        Returns:
            DataFrame, 按持续性评分降序排列
        """
        results = []
        for theme_name, etf_code in themes:
            etf_df = etf_data.get(etf_code)
            if etf_df is None or len(etf_df) < 60:
                continue

            stock_data = stock_data_map.get(etf_code, {})
            result = self.score_theme(
                theme_name=theme_name,
                etf_code=etf_code,
                etf_df=etf_df,
                benchmark_df=benchmark_df,
                stock_data=stock_data,
                trade_date=trade_date
            )
            results.append(result)

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results)
        df = df.sort_values('persistence_score', ascending=False).reset_index(drop=True)
        df['theme_rank'] = df.index + 1

        return df

    def _classify_state(self, score: float) -> str:
        """主题状态分类"""
        for (low, high), state in THEME_STATE.items():
            if low <= score < high:
                return state
        if score >= 100:
            return 'Super Main Trend'
        return 'Weak / Avoid'

    def _investment_signal(self, score: float, breadth: float,
                           leader: float, crowding: float) -> str:
        """
        投资信号

        BUY:  持续性>80 且 广度>70 且 龙头>75 且 拥挤<80
        HOLD: 70-80
        SELL: <60 或 龙头失败(leader<40) 或 广度收缩(breadth<30)
        """
        if score > 80 and breadth > 70 and leader > 75 and crowding < 80:
            return 'BUY'
        elif score >= 70:
            return 'HOLD'
        elif score < 60 or leader < 40 or breadth < 30:
            return 'SELL'
        else:
            return 'WATCH'

    def generate_daily_report(self, df: pd.DataFrame, trade_date: str) -> str:
        """
        生成日报文本

        Args:
            df: score_themes_batch 返回的 DataFrame
            trade_date: 交易日

        Returns:
            日报文本 (控制台格式)
        """
        lines = []
        lines.append(f"\n{'═'*70}")
        lines.append(f"  Theme Persistence Score Report — {trade_date}")
        lines.append(f"{'═'*70}")
        lines.append(f"  {'排名':>2} {'主题':<10} {'ETF':<8} {'持续性':>6} {'趋势':>5} {'广度':>5} "
                     f"{'龙头':>5} {'资金':>5} {'催化':>5} {'拥挤':>5} {'信号':>5} {'状态'}")
        lines.append(f"  {'─'*68}")

        for _, r in df.iterrows():
            lines.append(
                f"  {r['theme_rank']:>2} {r['theme']:<10} {r['etf_code']:<8} "
                f"{r['persistence_score']:>6.1f} {r['trend_stability']:>5.1f} "
                f"{r['breadth_expansion']:>5.1f} {r['leader_persistence']:>5.1f} "
                f"{r['capital_consistency']:>5.1f} {r['catalyst_duration']:>5.1f} "
                f"{r['crowding_score']:>5.1f} {r['investment_signal']:>5} {r['theme_state']}"
            )

        # BUY信号
        buy_themes = df[df['investment_signal'] == 'BUY']
        if not buy_themes.empty:
            lines.append(f"\n  ★ BUY 信号:")
            for _, r in buy_themes.iterrows():
                lines.append(f"    {r['theme']}({r['etf_code']}) "
                             f"持续性={r['persistence_score']:.1f} "
                             f"龙头={r['top_leaders'][:3]} "
                             f"预期持续={r['expected_duration']}")

        lines.append(f"{'═'*70}\n")
        return '\n'.join(lines)
