"""
市场宽度引擎 - Breadth Engine

衡量全市场参与度（赚钱效应）
从SQLite全量查询stk_factor_pro表，计算各子因子并合成宽度分数
"""

import os
import sys
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'inst_pullback_v2'))

from data.indicators import sma
from market_regime_v3.factor_registry import GLOBAL_REGISTRY, FactorCategory, FactorMeta, FactorResult

# 数据库路径
DB_PATH = r"D:\mystock\cache_daily\stock_data.db"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')


@dataclass
class BreadthResult:
    score: float  # 0-100
    up_ratio: float
    down_ratio: float
    limit_up_count: int
    limit_down_count: int
    new_high_20_ratio: float
    new_high_60_ratio: float
    above_ma20_ratio: float
    above_ma60_ratio: float
    above_ma120_ratio: float
    amount_spread_score: float
    median_return: float
    sub_scores: Dict[str, float]
    explain: Dict[str, str]


class BreadthEngine:
    """市场宽度引擎

    从stk_factor_pro表全量查询当日所有股票数据，计算全市场宽度指标。
    包括：上涨/下跌比例、涨跌停家数、创新高比例、站上均线比例、成交额集中度、中位数涨幅。
    """

    def __init__(self, config: dict):
        cfg = config.get('breadth', {})
        self.sample_size = cfg.get('sample_size', 5000)
        self.sub_weights = cfg.get('sub_weights', {})
        self.lookback_ma = cfg.get('lookback_ma', [5, 20, 60, 120])
        self.new_high_lookback = cfg.get('new_high_lookback', [20, 60])
        # 各子因子阈值
        self.thresholds = {
            'up_ratio': cfg.get('up_ratio', {}),
            'limit_up': cfg.get('limit_up', {}),
            'limit_down': cfg.get('limit_down', {}),
            'new_high': cfg.get('new_high', {}),
            'above_ma': cfg.get('above_ma', {}),
        }

    # ──────────────────────────────────────────────
    # SQL 查询
    # ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(DB_PATH)

    def query_today_data(self, trade_date: str) -> Optional[pd.DataFrame]:
        """查询当日全市场数据（含均线字段）"""
        conn = self._get_conn()
        try:
            sql = """
                SELECT ts_code, close_hfq, pct_chg, amount,
                       ma_bfq_20, ma_bfq_60, ma_bfq_250 as ma_bfq_120
                FROM stk_factor_pro
                WHERE trade_date = ?
            """
            df = pd.read_sql(sql, conn, params=(trade_date,))
            if df.empty:
                return None
            # 全市场采样
            if len(df) > self.sample_size:
                df = df.sample(n=self.sample_size, random_state=42)
            return df
        finally:
            conn.close()

    def query_hist_close(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """查询历史区间后复权收盘价（用于创新高计算）"""
        conn = self._get_conn()
        try:
            sql = """
                SELECT ts_code, trade_date, close_hfq
                FROM stk_factor_pro
                WHERE trade_date BETWEEN ? AND ?
                ORDER BY ts_code, trade_date
            """
            df = pd.read_sql(sql, conn, params=(start_date, end_date))
            return df
        finally:
            conn.close()

    def query_stock_basic(self) -> Optional[pd.DataFrame]:
        """从数据库获取股票基本信息（用于识别所属板块）"""
        conn = self._get_conn()
        try:
            sql = """
                SELECT DISTINCT ts_code FROM stk_factor_pro
            """
            df = pd.read_sql(sql, conn)
            return df
        finally:
            conn.close()

    # ──────────────────────────────────────────────
    # 板块识别
    # ──────────────────────────────────────────────

    @staticmethod
    def _is_main_board(ts_code: str) -> bool:
        """判断是否主板（10%涨跌停）
        上证：600/601/603/605
        深证主板：000/001/002
        """
        prefix = ts_code[:3]
        return prefix in ('600', '601', '603', '605', '000', '001', '002')

    @staticmethod
    def _is_gem_star(ts_code: str) -> bool:
        """判断是否双创（20%涨跌停）
        创业板：300
        科创板：688
        """
        prefix = ts_code[:3]
        return prefix in ('300', '688')

    @staticmethod
    def _get_limit_threshold(ts_code: str) -> float:
        """获取涨停阈值"""
        if ts_code[:3] in ('300', '688'):
            return 19.5
        return 9.5

    # ──────────────────────────────────────────────
    # 子因子计算
    # ──────────────────────────────────────────────

    def _calc_up_down_ratios(self, df: pd.DataFrame) -> Tuple[float, float, int, int]:
        """计算上涨/下跌比例和涨跌停家数"""
        total = len(df)
        if total == 0:
            return 0.0, 0.0, 0, 0

        up_count = int((df['pct_chg'] > 0).sum())
        down_count = int((df['pct_chg'] < 0).sum())
        up_ratio = up_count / total
        down_ratio = down_count / total

        # 涨跌停计数（区分主板和双创）
        limit_up = 0
        limit_down = 0
        for _, row in df.iterrows():
            threshold = self._get_limit_threshold(row['ts_code'])
            if row['pct_chg'] >= threshold:
                limit_up += 1
            elif row['pct_chg'] <= -threshold:
                limit_down += 1

        return up_ratio, down_ratio, limit_up, limit_down

    def _calc_new_high_ratio(self, df_today: pd.DataFrame, df_hist: pd.DataFrame) -> float:
        """计算创N日新高的股票比例

        df_hist 包含日期范围 [start_date, trade_date] 的全部数据，
        对每只股票取 close_hfq 最大值作为期间高点，
        比较当日 close_hfq 是否达到该高点。
        """
        if df_today is None or df_today.empty or df_hist is None or df_hist.empty:
            return 0.0

        # 每只股票在区间内的最高价
        hist_max = df_hist.groupby('ts_code')['close_hfq'].max().reset_index()
        hist_max.columns = ['ts_code', 'hist_max']

        # 与当日数据合并
        merged = pd.merge(
            df_today[['ts_code', 'close_hfq']],
            hist_max,
            on='ts_code',
            how='inner',
        )
        merged = merged.dropna(subset=['close_hfq', 'hist_max'])
        if len(merged) == 0:
            return 0.0

        new_high_count = int((merged['close_hfq'] >= merged['hist_max']).sum())
        return new_high_count / len(merged)

    def _calc_above_ma_ratio(self, df: pd.DataFrame, ma_field: str) -> float:
        """计算收盘价站上某均线的股票比例"""
        if df is None or df.empty:
            return 0.0
        valid = df.dropna(subset=[ma_field])
        if len(valid) == 0:
            return 0.0
        above_count = int((valid['close_hfq'] > valid[ma_field]).sum())
        return above_count / len(valid)

    def _calc_amount_spread(self, df: pd.DataFrame) -> float:
        """计算成交额集中度分数

        以中位数成交额为界区分"活跃股"与"不活跃股"，
        活跃股成交额占比越接近50%表示分布越均匀（健康），
        偏离越大表示越集中（少数股票成交额占比过高）。
        返回 0~100 分数。
        """
        if df is None or df.empty or 'amount' not in df.columns:
            return 50.0

        valid = df.dropna(subset=['amount'])
        if len(valid) == 0:
            return 50.0

        median_amt = valid['amount'].median()
        active = valid[valid['amount'] > median_amt]
        total_amt = valid['amount'].sum()
        if total_amt <= 0:
            return 50.0

        top_ratio = active['amount'].sum() / total_amt
        # top_ratio 范围 ~0.5(均匀) ~ 接近1.0(高度集中)
        # 分数 = (1 - |top_ratio - 0.5| / 0.5) * 100
        spread_score = 100.0 * (1.0 - abs(top_ratio - 0.5) / 0.5)
        return float(np.clip(spread_score, 0.0, 100.0))

    # ──────────────────────────────────────────────
    # 日期计算
    # ──────────────────────────────────────────────

    @staticmethod
    def _calc_date_range(trade_date: str, lookback_trading_days: int) -> Tuple[str, str]:
        """根据目标交易日数估算日历天数范围"""
        from datetime import datetime, timedelta
        td = datetime.strptime(trade_date, '%Y%m%d')
        # 交易日约占自然日的 70%，额外缓冲 10 天确保数据充足
        cal_days = int(lookback_trading_days * 1.4) + 15
        start = td - timedelta(days=cal_days)
        return start.strftime('%Y%m%d'), trade_date

    # ──────────────────────────────────────────────
    # 打分逻辑
    # ──────────────────────────────────────────────

    @staticmethod
    def _score_by_threshold(value: float, excellent: float, good: float, poor: float) -> float:
        """三档阈值线性插值打分

        参数:
            value: 原始值
            excellent: 优秀阈值（>= 此值得 100 分）
            good: 良好阈值
            poor: 较差阈值（<= 此值得 0 分）
        """
        if value >= excellent:
            return 100.0
        if value >= good:
            ratio = (value - good) / (excellent - good + 1e-10)
            return 60.0 + 40.0 * ratio
        if value >= poor:
            ratio = (value - poor) / (good - poor + 1e-10)
            return 20.0 + 40.0 * ratio
        return max(0.0, 20.0 * value / (poor + 1e-10))

    def _score_up_ratio(self, up_ratio: float) -> Tuple[float, str]:
        """上涨比例打分"""
        t = self.thresholds['up_ratio']
        score = self._score_by_threshold(
            up_ratio, t.get('excellent', 0.60), t.get('good', 0.50), t.get('poor', 0.30))
        explain = f"上涨比例{up_ratio:.1%}，高于{t['excellent']:.0%}为优秀，低于{t['poor']:.0%}为较差"
        return score, explain

    def _score_limit_up(self, count: int, total: int) -> Tuple[float, str]:
        """涨停家数打分"""
        t = self.thresholds['limit_up']
        excellent = t.get('excellent', 80)
        good = t.get('good', 50)
        poor = t.get('poor', 20)
        ratio = count / total if total > 0 else 0
        score = self._score_by_threshold(count, excellent, good, poor)
        explain = f"涨停{count}家(占比{ratio:.1%})，高于{excellent}家为优秀，低于{poor}家为较差"
        return score, explain

    def _score_limit_down(self, count: int) -> Tuple[float, str]:
        """跌停家数打分（反向，越少越好）"""
        max_acc = self.thresholds.get('limit_down', {}).get('max_acceptable', 10)
        if count <= 0:
            score = 100.0
        elif count >= max_acc * 3:
            score = 0.0
        else:
            score = 100.0 * (1.0 - count / (max_acc * 3))
        explain = f"跌停{count}家，可接受上限{max_acc}家"
        return score, explain

    def _score_new_high(self, ratio: float, lookback: int) -> Tuple[float, str]:
        """创新高比例打分"""
        t = self.thresholds['new_high']
        score = self._score_by_threshold(
            ratio, t.get('excellent', 0.10), t.get('good', 0.05), t.get('poor', 0.02))
        explain = f"创{lookback}日新高比例{ratio:.1%}，高于{t['excellent']:.0%}为优秀"
        return score, explain

    def _score_above_ma(self, ratio: float, period: int) -> Tuple[float, str]:
        """站上均线比例打分"""
        t = self.thresholds['above_ma']
        score = self._score_by_threshold(
            ratio, t.get('excellent', 0.50), t.get('good', 0.35), t.get('poor', 0.20))
        explain = f"站上MA{period}比例{ratio:.1%}，高于{t['excellent']:.0%}为优秀"
        return score, explain

    # ──────────────────────────────────────────────
    # 主评估接口
    # ──────────────────────────────────────────────

    def evaluate(self, trade_date: str) -> Optional[BreadthResult]:
        """计算全市场宽度

        Args:
            trade_date: 交易日 YYYYMMDD

        Returns:
            BreadthResult 或 None（无数据时）
        """
        # 1. 获取当日全市场数据
        today_df = self.query_today_data(trade_date)
        if today_df is None or today_df.empty:
            return None

        total = len(today_df)

        # 2. 涨跌比例 + 涨跌停
        up_ratio, down_ratio, limit_up_cnt, limit_down_cnt = self._calc_up_down_ratios(today_df)

        # 3. 创新高比例
        # 20日新高
        s20, e20 = self._calc_date_range(trade_date, 20)
        hist_20 = self.query_hist_close(s20, e20)
        new_high_20_ratio = self._calc_new_high_ratio(today_df, hist_20)

        # 60日新高
        s60, e60 = self._calc_date_range(trade_date, 60)
        hist_60 = self.query_hist_close(s60, e60)
        new_high_60_ratio = self._calc_new_high_ratio(today_df, hist_60)

        # 4. 站上均线比例
        above_ma20 = self._calc_above_ma_ratio(today_df, 'ma_bfq_20')
        above_ma60 = self._calc_above_ma_ratio(today_df, 'ma_bfq_60')
        above_ma120 = self._calc_above_ma_ratio(today_df, 'ma_bfq_120')

        # 5. 成交额集中度
        amount_spread = self._calc_amount_spread(today_df)

        # 6. 中位数涨幅
        median_return = float(today_df['pct_chg'].median()) if 'pct_chg' in today_df.columns else 0.0

        # 7. 各子因子打分
        sub_scores: Dict[str, float] = {}
        explain: Dict[str, str] = {}

        s, e = self._score_up_ratio(up_ratio)
        sub_scores['up_ratio'] = s
        explain['up_ratio'] = e

        # 下跌比例作为反向指标：比例越低分越高
        sub_scores['down_ratio'] = max(0.0, 100.0 - down_ratio * 100.0)
        explain['down_ratio'] = f"下跌比例{down_ratio:.1%}（反向指标）"

        s, e = self._score_limit_up(limit_up_cnt, total)
        sub_scores['limit_up'] = s
        explain['limit_up'] = e

        s, e = self._score_limit_down(limit_down_cnt)
        sub_scores['limit_down'] = s
        explain['limit_down'] = e

        s, e = self._score_new_high(new_high_20_ratio, 20)
        sub_scores['new_high_20'] = s
        explain['new_high_20'] = e

        s, e = self._score_new_high(new_high_60_ratio, 60)
        sub_scores['new_high_60'] = s
        explain['new_high_60'] = e

        s, e = self._score_above_ma(above_ma20, 20)
        sub_scores['above_ma20'] = s
        explain['above_ma20'] = e

        s, e = self._score_above_ma(above_ma60, 60)
        sub_scores['above_ma60'] = s
        explain['above_ma60'] = e

        s, e = self._score_above_ma(above_ma120, 120)
        sub_scores['above_ma120'] = s
        explain['above_ma120'] = e

        sub_scores['amount_spread'] = amount_spread
        explain['amount_spread'] = f"成交额集中度{amount_spread:.1f}分（越高越均匀）"

        # 中位数涨幅映射：0%→50分, +2%→80分, -2%→20分
        median_score = 50.0 + median_return * 15.0
        median_score = float(np.clip(median_score, 0.0, 100.0))
        sub_scores['median_return'] = median_score
        explain['median_return'] = f"中位数涨幅{median_return:.2f}%"

        # 8. 加权合成总分
        weighted_sum = 0.0
        total_weight = 0.0
        for key, weight in self.sub_weights.items():
            if key in sub_scores:
                weighted_sum += sub_scores[key] * weight
                total_weight += weight

        score = (weighted_sum / total_weight) if total_weight > 0 else 50.0

        return BreadthResult(
            score=round(score, 2),
            up_ratio=round(up_ratio, 4),
            down_ratio=round(down_ratio, 4),
            limit_up_count=limit_up_cnt,
            limit_down_count=limit_down_cnt,
            new_high_20_ratio=round(new_high_20_ratio, 4),
            new_high_60_ratio=round(new_high_60_ratio, 4),
            above_ma20_ratio=round(above_ma20, 4),
            above_ma60_ratio=round(above_ma60, 4),
            above_ma120_ratio=round(above_ma120, 4),
            amount_spread_score=round(amount_spread, 2),
            median_return=round(median_return, 2),
            sub_scores={k: round(v, 2) for k, v in sub_scores.items()},
            explain=explain,
        )


# ──────────────────────────────────────────────
# 工厂函数 & 因子注册
# ──────────────────────────────────────────────

def load_config() -> dict:
    """加载配置文件"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def create_breadth_engine() -> BreadthEngine:
    """从 config.yaml 创建广度引擎实例"""
    config = load_config()
    return BreadthEngine(config)


def register_breadth_factors():
    """将广度引擎各子因子注册到全局因子注册表"""
    config = load_config()
    engine = BreadthEngine(config)

    # 注册计算函数：外部传入 trade_date 触发评估
    def _computer(**kwargs) -> float:
        trade_date = kwargs.get('trade_date', None)
        if not trade_date:
            return 50.0
        result = engine.evaluate(trade_date)
        if result is None:
            return 50.0
        return result.score

    GLOBAL_REGISTRY.register(
        FactorMeta(
            name='breadth_score',
            category=FactorCategory.BREADTH,
            description='全市场宽度综合得分（0~100）',
            weight=1.0,
            min_value=0.0,
            max_value=100.0,
        ),
        _computer,
    )
