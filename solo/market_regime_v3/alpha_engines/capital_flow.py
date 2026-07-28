# -*- coding: utf-8 -*-
"""资金行为引擎 — 机构资金流、北向资金、大单强度分析

核心功能：
  1. 北向资金监测：日度变化、累计趋势、分行业分布
  2. 机构资金流：主力净流入/流出、超大单/大单强度
  3. 资金恢复检测：连续净流入/流出模式识别
  4. 资金情绪评分：综合多维度资金行为打分

数据来源：
  - tushare pro.moneyflow（个股资金流）
  - tushare pro.moneyflow_hsgt（北向资金）
  - 本地 Parquet/CSV 缓存
"""

import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tushare as ts
try:
    import stock_cache as sc
except ImportError:
    sc = None


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class MoneyFlowResult:
    """单只股票资金流结果"""
    ts_code: str
    name: str = ''
    net_inflow_1d: float = 0.0       # 当日净流入（万元）
    net_inflow_5d: float = 0.0       # 5日累计净流入
    net_inflow_10d: float = 0.0      # 10日累计净流入
    large_order_net: float = 0.0     # 大单净流入（万元）
    large_order_intensity: float = 0.0  # 大单强度 [-1, 1]
    buy_elder_ratio: float = 0.0     # 买入特大单占比
    sell_elder_ratio: float = 0.0    # 卖出特大单占比
    is_recovering: bool = False      # 资金流是否恢复
    flow_score: float = 50.0         # 资金流综合评分 0~100


@dataclass
class NorthBoundResult:
    """北向资金结果"""
    total_inflow_today: float = 0.0    # 当日净流入（亿元）
    total_inflow_5d: float = 0.0       # 5日累计
    total_inflow_20d: float = 0.0      # 20日累计
    sh_inflow: float = 0.0             # 沪股通
    sz_inflow: float = 0.0             # 深股通
    trend: str = 'neutral'             # 趋势: inflow/outflow/neutral
    score: float = 50.0


@dataclass
class CapitalFlowResult:
    """市场资金流综合结果"""
    trade_date: str
    north_bound: Optional[NorthBoundResult] = None
    stock_flows: Dict[str, MoneyFlowResult] = field(default_factory=dict)
    market_net_inflow: float = 0.0
    market_large_order_score: float = 50.0
    top_inflow_stocks: List[MoneyFlowResult] = field(default_factory=list)
    top_outflow_stocks: List[MoneyFlowResult] = field(default_factory=list)
    composite_score: float = 50.0       # 资金面综合评分


# ──────────────────────────────────────────────
# 主引擎
# ──────────────────────────────────────────────

class CapitalFlowEngine:
    """资金行为引擎"""

    def __init__(self, config: dict):
        cfg = config.get('capital_flow', {})
        self.north_lookback = cfg.get('north_lookback_days', 20)
        self.moneyflow_lookback = cfg.get('moneyflow_lookback_days', 10)
        self.recovery_window = cfg.get('recovery_window', 3)
        self.recovery_threshold = cfg.get('recovery_threshold', 0)  # 万
        self._pro = None  # 懒加载
        # 缓存
        self._moneyflow_cache = {}

    @property
    def pro(self):
        if self._pro is None:
            try:
                import tushare as ts
                self._pro = ts.pro_api()
            except Exception:
                self._pro = None
        return self._pro

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def evaluate(self, trade_date: str,
                 codes: List[str] = None) -> CapitalFlowResult:
        """资金流评估主入口

        Args:
            trade_date: 交易日
            codes: 待分析的股票代码列表（None=全市场）

        Returns:
            CapitalFlowResult
        """
        result = CapitalFlowResult(trade_date=trade_date)

        # 1) 北向资金
        nb = self._evaluate_north_bound(trade_date)
        result.north_bound = nb

        # 2) 个股资金流
        if codes:
            stock_flow_results = []
            for code in codes:
                mf = self._analyze_stock_moneyflow(code, trade_date)
                if mf is not None:
                    result.stock_flows[code] = mf
                    stock_flow_results.append(mf)

            # Top流入/流出
            stock_flow_results.sort(key=lambda x: x.net_inflow_5d, reverse=True)
            result.top_inflow_stocks = stock_flow_results[:10]
            result.top_outflow_stocks = stock_flow_results[-10:] if len(stock_flow_results) >= 10 else stock_flow_results

        # 3) 全市场资金流概况
        market_flow = self._get_market_flow_snapshot(trade_date)
        result.market_net_inflow = market_flow.get('net_inflow', 0.0)
        result.market_large_order_score = market_flow.get('large_order_score', 50.0)

        # 4) 综合评分
        result.composite_score = self._calc_composite_score(result)

        return result

    # ──────────────────────────────────────────────
    # 北向资金
    # ──────────────────────────────────────────────

    def _evaluate_north_bound(self, trade_date: str) -> Optional[NorthBoundResult]:
        """评估北向资金"""
        try:
            start_5d = (pd.to_datetime(trade_date) - timedelta(days=10)).strftime('%Y%m%d')
            start_20d = (pd.to_datetime(trade_date) - timedelta(days=25)).strftime('%Y%m%d')

            # 获取北向资金数据（沪股通+深股通）
            df_sh = self._get_moneyflow_hsgt('sh', start_20d, trade_date)
            df_sz = self._get_moneyflow_hsgt('sz', start_20d, trade_date)

            if df_sh is None or df_sh.empty:
                return None

            # 计算总净流入
            total = df_sh['net'] + df_sz['net'] if df_sz is not None and not df_sz.empty else df_sh['net']

            result = NorthBoundResult()
            result.total_inflow_today = float(total.iloc[-1]) if len(total) > 0 else 0.0
            result.total_inflow_5d = float(total.tail(5).sum()) if len(total) >= 5 else result.total_inflow_today * 5
            result.total_inflow_20d = float(total.sum()) if len(total) > 0 else 0.0
            result.sh_inflow = float(df_sh['net'].iloc[-1]) if len(df_sh) > 0 else 0.0
            result.sz_inflow = float(df_sz['net'].iloc[-1]) if df_sz is not None and len(df_sz) > 0 else 0.0

            # 趋势判断
            if len(total) >= 5:
                recent = total.tail(5).mean()
                older = total.tail(10).head(5).mean() if len(total) >= 10 else recent
                if recent > 0 and older > 0:
                    result.trend = '持续流入'
                    score = 80 + min(20, recent / 10)
                elif recent > older:
                    result.trend = '边际改善'
                    score = 60 + min(20, recent / 10)
                elif recent < 0 and older < 0:
                    result.trend = '持续流出'
                    score = max(10, 30 + recent / 10)
                else:
                    result.trend = 'neutral'
                    score = 50
                result.score = min(100, max(0, score))

            return result
        except Exception:
            return None

    def _get_moneyflow_hsgt(self, hsgt_type: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取沪深港通资金流"""
        try:
            cache_key = f"moneyflow_hsgt_{hsgt_type}_{start_date}_{end_date}"
            if cache_key in self._moneyflow_cache:
                return self._moneyflow_cache[cache_key]

            if hsgt_type == 'sh':
                df = self.pro.moneyflow_hsgt(start_date=start_date, end_date=end_date, hsgt_type='sh')
            else:
                df = self.pro.moneyflow_hsgt(start_date=start_date, end_date=end_date, hsgt_type='sz')

            if df is not None and not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                df = df.sort_values('trade_date')
                self._moneyflow_cache[cache_key] = df
            return df
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 个股资金流
    # ──────────────────────────────────────────────

    def _analyze_stock_moneyflow(self, ts_code: str, trade_date: str) -> Optional[MoneyFlowResult]:
        """分析单只股票的资金流"""
        try:
            start_date = (pd.to_datetime(trade_date) - timedelta(days=self.moneyflow_lookback + 5)).strftime('%Y%m%d')

            # 获取个股资金流
            df = self.pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=trade_date)
            if df is None or df.empty:
                return None

            df = df.sort_values('trade_date').reset_index(drop=True)
            if len(df) < 1:
                return None

            result = MoneyFlowResult(ts_code=ts_code)

            # 净流入
            columns = df.columns.tolist()
            # 尝试不同的列名（tushare版本差异）
            net_col = None
            for col in ['net_mf_amount', 'net_amount', 'net_inflow']:
                if col in columns:
                    net_col = col
                    break

            if net_col:
                result.net_inflow_1d = float(df[net_col].iloc[-1]) if pd.notna(df[net_col].iloc[-1]) else 0.0
                result.net_inflow_5d = float(df[net_col].tail(min(5, len(df))).sum())
                result.net_inflow_10d = float(df[net_col].tail(min(10, len(df))).sum())

            # 大单强度
            buy_large = None
            sell_large = None
            for col, target in [('buy_lg_amount', 'buy'), ('sell_lg_amount', 'sell'),
                                ('buy_elder_amount', 'buy'), ('sell_elder_amount', 'sell')]:
                if col in columns:
                    val = float(df[col].iloc[-1]) if pd.notna(df[col].iloc[-1]) else 0.0
                    if target == 'buy':
                        buy_large = val
                    else:
                        sell_large = val

            if buy_large is not None and sell_large is not None and (buy_large + sell_large) > 0:
                result.large_order_net = buy_large - sell_large
                result.large_order_intensity = (buy_large - sell_large) / (buy_large + sell_large)

            # 特大单占比
            for col, target in [('buy_elder_amount', 'buy'), ('sell_elder_amount', 'sell')]:
                if col in columns:
                    val = float(df[col].iloc[-1]) if pd.notna(df[col].iloc[-1]) else 0.0
                    if target == 'buy':
                        result.buy_elder_ratio = val / df['amount'].iloc[-1] * 100 if df['amount'].iloc[-1] > 0 else 0
                    else:
                        result.sell_elder_ratio = val / df['amount'].iloc[-1] * 100 if df['amount'].iloc[-1] > 0 else 0

            # 资金流恢复检测
            result.is_recovering = self._detect_flow_recovery(df, net_col)
            result.flow_score = self._calc_flow_score(result)
            return result
        except Exception:
            return None

    def _detect_flow_recovery(self, df: pd.DataFrame, net_col: str) -> bool:
        """检测资金流是否恢复

        条件：近期连续净流入且最近交易日为正
        """
        if net_col is None or net_col not in df.columns:
            return False
        window = min(self.recovery_window, len(df))
        recent = df[net_col].tail(window)
        # 要求窗口内净流入和>0，且最后一期为正
        return float(recent.sum()) > self.recovery_threshold and float(recent.iloc[-1]) > 0

    def _calc_flow_score(self, mf: MoneyFlowResult) -> float:
        """资金流综合评分"""
        score = 50.0
        # 5日净流入
        if mf.net_inflow_5d > 0:
            score += min(20, mf.net_inflow_5d / 1000)
        else:
            score -= min(20, abs(mf.net_inflow_5d) / 1000)

        # 大单强度
        score += mf.large_order_intensity * 15

        # 恢复信号
        if mf.is_recovering:
            score += 10

        return max(0, min(100, score))

    # ──────────────────────────────────────────────
    # 全市场资金流概况
    # ──────────────────────────────────────────────

    def _get_market_flow_snapshot(self, trade_date: str) -> Dict:
        """获取全市场资金流概况"""
        result = {'net_inflow': 0.0, 'large_order_score': 50.0}
        try:
            df = self.pro.moneyflow(trade_date=trade_date)
            if df is not None and not df.empty:
                # 净流入合计
                for col in ['net_mf_amount', 'net_amount', 'net_inflow']:
                    if col in df.columns:
                        result['net_inflow'] = float(df[col].sum())
                        break

                # 大单强度评分
                buy_cols = ['buy_lg_amount', 'buy_elder_amount']
                sell_cols = ['sell_lg_amount', 'sell_elder_amount']
                buy_total = 0.0
                sell_total = 0.0
                for bc in buy_cols:
                    if bc in df.columns:
                        buy_total += float(df[bc].sum())
                for sc in sell_cols:
                    if sc in df.columns:
                        sell_total += float(df[sc].sum())

                if buy_total + sell_total > 0:
                    intensity = (buy_total - sell_total) / (buy_total + sell_total)
                    result['large_order_score'] = 50 + intensity * 50
        except Exception:
            pass
        return result

    # ──────────────────────────────────────────────
    # 综合评分
    # ──────────────────────────────────────────────

    def _calc_composite_score(self, result: CapitalFlowResult) -> float:
        """资金面综合评分"""
        scores = []

        # 北向资金
        if result.north_bound:
            scores.append(result.north_bound.score)

        # 市场大单
        scores.append(result.market_large_order_score)

        # 个股平均
        if result.stock_flows:
            avg_flow = np.mean([mf.flow_score for mf in result.stock_flows.values()])
            scores.append(avg_flow)

        if not scores:
            return 50.0
        return round(float(np.mean(scores)), 1)


# ──────────────────────────────────────────────
# 快速本地资金流分析（无API调用版）
# ──────────────────────────────────────────────

class LocalFlowAnalyzer:
    """本地缓存资金流分析器 — 从 Parquet/CSV 缓存读取资金流数据

    适用于批量分析场景，减少 API 调用
    """

    def __init__(self, parquet_dir: str = None):
        if parquet_dir is None:
            parquet_dir = r"D:\mystock\cache_daily"
        self.parquet_dir = parquet_dir

    def load_moneyflow_by_date(self, trade_date: str) -> Optional[pd.DataFrame]:
        """按日期加载全市场资金流快照"""
        fp = os.path.join(self.parquet_dir, f"moneyflow_{trade_date}.parquet")
        if os.path.exists(fp):
            try:
                return pd.read_parquet(fp)
            except Exception:
                pass
        return None

    def get_stock_flow(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """从缓存获取个股资金流时序"""
        fp = os.path.join(self.parquet_dir, "moneyflow", f"{ts_code}.parquet")
        if os.path.exists(fp):
            try:
                df = pd.read_parquet(fp)
                df['trade_date'] = df['trade_date'].astype(str)
                mask = (df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)
                return df.loc[mask].sort_values('trade_date')
            except Exception:
                pass
        return None


# ──────────────────────────────────────────────
# CLI 测试
# ──────────────────────────────────────────────

if __name__ == '__main__':
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    engine = CapitalFlowEngine(cfg)
    td = sc.get_effective_date()
    result = engine.evaluate(td, codes=[])
    print(f"\n资金行为引擎结果 ({td}):")
    print(f"  综合评分: {result.composite_score:.1f}分")
    if result.north_bound:
        nb = result.north_bound
        print(f"  北向资金: 当日{nb.total_inflow_today:+.1f}亿 | 5日{nb.total_inflow_5d:+.1f}亿 | {nb.trend}")
    print(f"  市场净流入: {result.market_net_inflow:.0f}万")
    print(f"  大单强度评分: {result.market_large_order_score:.1f}分")
