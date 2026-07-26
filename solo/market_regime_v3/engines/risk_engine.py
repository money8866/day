"""
风险偏好引擎 - Risk Appetite Engine

衡量市场整体的风险偏好水平，综合宽度、情绪、波动率、领涨强度等多个维度，
输出 0~100 的风险偏好评分，用于判断市场处于 risk-on 还是 risk-off 状态。
"""

import os
import sys
import json
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd
import yaml

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'inst_pullback_v2'))

from data.indicators import volatility, percentile_rank
from data.loader import DataLoader
from market_regime_v3.engines import resolve_theme_stock_map_path

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')


@dataclass
class RiskAppetiteResult:
    score: float  # 0-100. Higher = more risk-seeking
    level: str  # "High", "Medium", "Low", "Very Low"
    sub_scores: Dict[str, float]
    explain: Dict[str, str]


class RiskAppetiteEngine:
    """风险偏好引擎

    从宽度、ETF宽度、情绪、波动率、领涨强度五个维度综合评估市场风险偏好。
    """

    def __init__(self, config: dict):
        cfg = config.get('risk_appetite', {})
        self.sub_weights = cfg.get('sub_weights', {})
        self.lookback = cfg.get('lookback', 60)
        # 波动率子配置
        vol_cfg = cfg.get('volatility', {})
        self.high_risk_threshold = vol_cfg.get('high_risk_threshold', 0.8)
        self.low_risk_threshold = vol_cfg.get('low_risk_threshold', 0.2)
        # ETF宽度子配置
        etf_cfg = cfg.get('etf_breadth', {})
        self.up_etf_ratio_excellent = etf_cfg.get('up_etf_ratio_excellent', 0.60)
        self.up_etf_ratio_good = etf_cfg.get('up_etf_ratio_good', 0.40)
        # 指数列表（从indices段获取）
        self.indices = config.get('indices', {}).get('codes', [])
        # 数据加载器（延迟初始化）
        self._loader: Optional[DataLoader] = None

    @property
    def loader(self) -> DataLoader:
        if self._loader is None:
            self._loader = DataLoader()
        return self._loader

    # ──────────────────────────────────────────────
    # 子因子：ETF宽度
    # ──────────────────────────────────────────────

    def _calc_etf_breadth(self, trade_date: str, start_date: str) -> float:
        """计算 ETF 宽度：统计前 30 只 ETF 中 5 日上涨的比例"""
        etf_pool = self.loader.get_etf_pool()
        etf_codes = list(etf_pool.keys())[:30]  # 取前 30 只
        if not etf_codes:
            return 50.0

        up_count = 0
        total = 0
        for code in etf_codes:
            df = self.loader.load_index_data(code, start_date, trade_date, silent=True)
            if df is None or df.empty or len(df) < 6:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            close = df['close'].values
            if len(close) >= 5 and close[-1] > close[-5]:
                up_count += 1
            total += 1

        if total == 0:
            return 50.0
        ratio = up_count / total
        return ratio * 100

    # ──────────────────────────────────────────────
    # 子因子：波动率（反向）
    # ──────────────────────────────────────────────

    def _calc_volatility_score(self, trade_date: str, start_date: str) -> float:
        """计算逆波动率得分：低波动 → 高评分"""
        if not self.indices:
            return 50.0

        scores = []
        for idx_code in self.indices:
            df = self.loader.load_index_data(idx_code, start_date, trade_date, silent=True)
            if df is None or df.empty or len(df) < self.lookback + 5:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            # 计算 20 日波动率
            vol_series = volatility(df['close'], period=20).dropna()
            if len(vol_series) < 2:
                continue
            # 当前波动率
            current_vol = vol_series.iloc[-1]
            # 百分位排名（相对 lookback 窗口）
            window = vol_series.iloc[-self.lookback:] if len(vol_series) >= self.lookback else vol_series
            rank = window.rank(pct=True).iloc[-1]
            # 低波动 → 高得分
            score = (1.0 - rank) * 100.0
            scores.append(score)

        if not scores:
            return 50.0
        return float(np.mean(scores))

    # ──────────────────────────────────────────────
    # 子因子：领涨强度
    # ──────────────────────────────────────────────

    def _calc_leader_strength(self, trade_date: str, start_date: str) -> float:
        """计算领涨板块的强度：取前几个主题的成分股平均 20 日收益"""
        map_path = resolve_theme_stock_map_path(trade_date)
        if not os.path.exists(map_path):
            return 50.0

        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            return 50.0

        # 兼容可能的结构（带 themes 字段或直接是字典）
        if isinstance(raw, dict) and 'themes' in raw:
            theme_map = raw['themes']
        else:
            theme_map = raw

        if not theme_map or not isinstance(theme_map, dict):
            return 50.0

        # 取前 5 个主题
        theme_items = list(theme_map.items())[:5]
        all_returns = []

        for theme_name, stocks in theme_items:
            if not stocks:
                continue
            # stocks 可能是列表，也可能是 dict 的 key
            if isinstance(stocks, dict):
                stock_codes = list(stocks.keys())[:10]
            elif isinstance(stocks, list):
                stock_codes = stocks[:10]
            else:
                continue

            for stock in stock_codes:
                # stocks 列表可能包含 dict（含 code 字段）或纯字符串
                if isinstance(stock, dict):
                    ts_code = stock.get('code', '')
                else:
                    ts_code = stock
                if not ts_code:
                    continue
                df = self.loader.load_stk_factor(ts_code, start_date, trade_date, silent=True)
                if df is None or df.empty or len(df) < 5:
                    continue
                df = df.sort_values('trade_date').reset_index(drop=True)
                # 20 日收益率
                close = df['close'].values
                if len(close) >= 20:
                    ret_20 = close[-1] / close[-20] - 1
                else:
                    ret_20 = close[-1] / close[0] - 1
                all_returns.append(ret_20)

        if not all_returns:
            return 50.0

        avg_return = float(np.mean(all_returns))
        # 将收益率映射到 0~100 分：0% → 50 分，+10% → 80 分，-10% → 20 分
        score = 50.0 + avg_return * 300.0
        return float(np.clip(score, 0.0, 100.0))

    # ──────────────────────────────────────────────
    # 等级映射
    # ──────────────────────────────────────────────

    @staticmethod
    def _map_level(score: float) -> str:
        """将分数映射到风险偏好等级"""
        if score >= 65:
            return "High"
        elif score >= 50:
            return "Medium"
        elif score >= 35:
            return "Low"
        else:
            return "Very Low"

    # ──────────────────────────────────────────────
    # 主评估接口
    # ──────────────────────────────────────────────

    def evaluate(self, breadth_score: float, sentiment_score: float,
                 trade_date: str, start_date: str) -> RiskAppetiteResult:
        """综合评估风险偏好

        Args:
            breadth_score: 市场宽度得分（0-100）
            sentiment_score: 市场情绪得分（0-100）
            trade_date: 交易日 YYYYMMDD
            start_date: 起始日 YYYYMMDD（用于历史数据回看）

        Returns:
            RiskAppetiteResult
        """
        sub_scores: Dict[str, float] = {}
        explain: Dict[str, str] = {}

        # 1. 宽度（直接使用输入的宽度得分）
        sub_scores['breadth'] = breadth_score
        explain['breadth'] = f"市场宽度得分 {breadth_score:.1f} 分（权重 {self.sub_weights.get('breadth', 0.25):.0%}）"

        # 2. ETF 宽度
        etf_score = self._calc_etf_breadth(trade_date, start_date)
        sub_scores['etf_breadth'] = etf_score
        explain['etf_breadth'] = f"ETF 5日上涨比例 {etf_score:.1f}/100"

        # 3. 情绪（直接使用输入的情绪得分）
        sub_scores['sentiment'] = sentiment_score
        explain['sentiment'] = f"市场情绪得分 {sentiment_score:.1f} 分（权重 {self.sub_weights.get('sentiment', 0.20):.0%}）"

        # 4. 波动率（反向指标）
        vol_score = self._calc_volatility_score(trade_date, start_date)
        sub_scores['volatility'] = vol_score
        explain['volatility'] = f"逆波动率得分 {vol_score:.1f} 分（低波动=高偏好）"

        # 5. 领涨强度
        leader_score = self._calc_leader_strength(trade_date, start_date)
        sub_scores['leader_strength'] = leader_score
        explain['leader_strength'] = f"领涨主题强度得分 {leader_score:.1f} 分"

        # 加权合成总分
        weighted_sum = 0.0
        total_weight = 0.0
        for key, weight in self.sub_weights.items():
            if key in sub_scores:
                weighted_sum += sub_scores[key] * weight
                total_weight += weight

        score = (weighted_sum / total_weight) if total_weight > 0 else 50.0
        score = float(np.clip(score, 0.0, 100.0))
        level = self._map_level(score)

        return RiskAppetiteResult(
            score=round(score, 2),
            level=level,
            sub_scores={k: round(v, 2) for k, v in sub_scores.items()},
            explain=explain,
        )


# ──────────────────────────────────────────────
# 工厂函数
# ──────────────────────────────────────────────

def load_config() -> dict:
    """加载配置文件"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def create_risk_appetite_engine() -> RiskAppetiteEngine:
    """从 config.yaml 创建风险偏好引擎实例"""
    config = load_config()
    return RiskAppetiteEngine(config)
