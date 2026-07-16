#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Alpha Engine - 主程序编排器
=================================
机构级 ETF Alpha 引擎，针对A股行业ETF。

持仓周期: 20~60 交易日
最大持仓: 单只ETF
目标: 最大化年化收益，控制回撤
优化目标: Expected Return × Win Rate × Trend Persistence

六大独立模块:
  Module 1: Market Regime Engine      市场状态
  Module 2: Theme Alpha Engine         主题Alpha
  Module 3: Theme Lifecycle Engine     主题生命周期
  Module 4: ETF Ranking Engine          ETF排名
  Module 5: Leader Confirmation Engine 龙头确认
  Module 6: Risk Engine                风险引擎

用法:
  python -m etf_alpha_engine.main
  python -m etf_alpha_engine.main --top-n 10
  python -m etf_alpha_engine.main --etf-only
"""
from __future__ import annotations

import os
import sys
import argparse
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 确保可以 import etf_alpha_engine 包
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_alpha_engine import __version__
from etf_alpha_engine.data_loader import DataLoader, load_config
from etf_alpha_engine.market_regime import MarketRegimeEngine, MarketRegimeResult
from etf_alpha_engine.theme_alpha import ThemeAlphaEngine, ThemeAlphaResult
from etf_alpha_engine.theme_lifecycle import ThemeLifecycleEngine, LifecycleResult
from etf_alpha_engine.etf_ranking import ETFRankingEngine, ETFRankingResult
from etf_alpha_engine.leader_confirm import LeaderConfirmEngine, LeaderConfirmResult
from etf_alpha_engine.risk_engine import RiskEngine, RiskResult
from etf_alpha_engine.composite import CompositeEngine, FinalETFResult
from etf_alpha_engine.rules import RulesEngine, SignalResult
from etf_alpha_engine.reporter import OutputReporter


class ETFAlphaEngine:
    """ETF Alpha 引擎主流程编排器"""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(BASE_DIR, "config.yaml")
        self.config = load_config(self.config_path)
        self.dl = DataLoader(self.config)

        # 模块引擎
        self.market_regime = MarketRegimeEngine(self.config)
        self.theme_alpha = ThemeAlphaEngine(self.config)
        self.theme_lifecycle = ThemeLifecycleEngine(self.config)
        self.etf_ranking = ETFRankingEngine(self.config)
        self.leader_confirm = LeaderConfirmEngine(self.config)
        self.risk = RiskEngine(self.config)
        self.composite = CompositeEngine(self.config)
        self.rules = RulesEngine(self.config)
        self.reporter = OutputReporter(self.config)

        # ETF主题映射
        self.etf_theme_map: Dict[str, str] = self.config.get("etf_universe", {})
        self.etf_list = list(self.etf_theme_map.keys())

        # 结果缓存
        self.market_result: Optional[MarketRegimeResult] = None
        self.theme_results: Dict[str, ThemeAlphaResult] = {}
        self.lifecycle_results: Dict[str, LifecycleResult] = {}
        self.etf_ranking_results: Dict[str, ETFRankingResult] = {}
        self.leader_results: Dict[str, LeaderConfirmResult] = {}
        self.risk_results: Dict[str, RiskResult] = {}
        self.final_results: List[FinalETFResult] = []
        self.signals: Dict[str, SignalResult] = {}

        # 数据缓存
        self._etf_data: Dict[str, pd.DataFrame] = {}
        self._stock_data: Dict[str, pd.DataFrame] = {}
        self._constituents: Dict[str, List[str]] = {}
        self._benchmark: Optional[np.ndarray] = None
        self._daily_all: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def load_data(self, start_date: str, end_date: str) -> None:
        print("=" * 70)
        print("  ETF Alpha Engine - 数据加载")
        print("=" * 70)

        # 1. 加载ETF日线
        print(f"[1] 加载ETF日线 ({len(self.etf_list)}只)...")
        self._etf_data = self.dl.load_etf_data(self.etf_list, start_date, end_date)
        print(f"    ETF数据: {len(self._etf_data)} 只")

        # 2. 加载基准
        print("[2] 加载沪深300基准...")
        bm_df = self.dl.load_index("000300.SH", start_date, end_date)
        if not bm_df.empty:
            self._benchmark = bm_df["close"].values.astype(float)

        # 3. 加载主题-股票映射
        print("[3] 加载主题-股票映射...")
        universe = self.dl.load_theme_universe()
        print(f"    主题数: {len(universe)}")

        # 4. 加载成份股数据（用于龙头确认）
        print("[4] 加载ETF成份股...")
        self._constituents = self._match_etf_to_stocks(universe)
        all_cons = list(set(c for cs in self._constituents.values() for c in cs))
        print(f"    去重成份股: {len(all_cons)} 只")

        if all_cons:
            print("[5] 加载成份股日线...")
            self._stock_data = self.dl.load_etf_data(all_cons, start_date, end_date)
            print(f"    个股数据: {len(self._stock_data)} 只")

        # 6. 加载全市场日线（用于市场宽度）- 用最近几天
        print("[6] 加载全市场日线(近5日,用于宽度)...")
        self._daily_all = self.dl.load_market_daily_recent(n_days=5)
        if not self._daily_all.empty:
            print(f"    全市场记录: {len(self._daily_all)} 条, "
                  f"股票数: {self._daily_all['ts_code'].nunique() if 'ts_code' in self._daily_all.columns else 0}")

        print("数据加载完成\n")

    def _match_etf_to_stocks(self, universe: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """将ETF匹配到主题成份股（用主题名匹配）"""
        result = {}
        for etf_code, theme_name in self.etf_theme_map.items():
            # 精确匹配
            if theme_name in universe:
                result[etf_code] = universe[theme_name]
                continue
            # 模糊匹配
            matched = []
            for tname, codes in universe.items():
                if theme_name in tname or tname in theme_name:
                    matched.extend(codes)
            if matched:
                result[etf_code] = list(set(matched))[:50]  # 限制数量
        return result

    # ------------------------------------------------------------------
    # 运行流水线
    # ------------------------------------------------------------------
    def run_pipeline(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = self.dl.get_last_trade_date()

        dt = datetime.strptime(trade_date, "%Y%m%d")
        start_date = (dt - timedelta(days=400)).strftime("%Y%m%d")
        print(f"分析区间: {start_date} ~ {trade_date}")

        # 加载数据
        self.load_data(start_date, trade_date)

        # 辅助数据
        print("=" * 70)
        print("  加载辅助数据")
        print("=" * 70)
        limit_df = self.dl.load_limit_list(trade_date)
        top_df = self.dl.load_top_list(trade_date)
        top_inst = self.dl.load_top_inst(trade_date)
        dc_hot = self.dl.load_dc_hot(trade_date)
        moneyflow = self.dl.load_moneyflow_by_date(trade_date)
        print(f"涨停: {len(limit_df)}, 龙虎榜: {len(top_df)}, "
              f"机构: {len(top_inst)}, DC热度: {len(dc_hot)}, "
              f"moneyflow: {len(moneyflow)}\n")

        # ===== Module 1: Market Regime =====
        print("=" * 70)
        print("  Module 1: Market Regime Engine")
        print("=" * 70)
        index_df = self.dl.load_index("000300.SH", start_date, trade_date)
        self.market_result = self.market_regime.score(
            index_df=index_df,
            market_daily=self._daily_all,
            limit_df=limit_df,
            etf_data=self._etf_data,
            northbound_net=0.0,
        )
        print(f"  Market Score: {self.market_result.market_score:.1f}")
        print(f"  Market State: {self.market_result.market_state}")
        print(f"  Suggested Exposure: {self.market_result.suggested_exposure*100:.0f}%")
        print(f"  Reasons: {', '.join(self.market_result.reasons)}\n")

        # ===== Module 4: ETF Ranking (先运行, 提供ETF筛选) =====
        print("=" * 70)
        print("  Module 4: ETF Ranking Engine")
        print("=" * 70)
        self.etf_ranking_results = self.etf_ranking.score(self._etf_data, self._benchmark)
        print(f"  评分ETF: {len(self.etf_ranking_results)}")
        if self.etf_ranking_results:
            top5 = sorted(self.etf_ranking_results.values(),
                          key=lambda x: x.etf_alpha_score, reverse=True)[:5]
            for r in top5:
                print(f"    {r.etf_code} [{r.theme}] Alpha={r.etf_alpha_score:.1f} "
                      f"RS={r.relative_strength:.1f} Trend={r.trend_quality:.1f}")
        print()

        # ===== Module 2: Theme Alpha =====
        print("=" * 70)
        print("  Module 2: Theme Alpha Engine")
        print("=" * 70)
        universe = self.dl.load_theme_universe()
        # 合并全部日线（主题+ETF成份股）
        all_daily_frames = [df for df in self._etf_data.values() if df is not None and not df.empty]
        if self._stock_data:
            all_daily_frames.extend([df for df in self._stock_data.values() if df is not None and not df.empty])
        if all_daily_frames:
            daily_all = pd.concat(all_daily_frames, ignore_index=True)
        else:
            daily_all = pd.DataFrame()
        self.theme_results = self.theme_alpha.score(
            daily_all, universe, moneyflow, limit_df, dc_hot, top_df
        )
        top_themes = sorted(self.theme_results.values(), key=lambda x: x.theme_score, reverse=True)[:5]
        print(f"  主题数: {len(self.theme_results)}")
        for r in top_themes:
            print(f"    #{r.rank} {r.theme}: score={r.theme_score:.1f} "
                  f"alpha={r.theme_alpha:.1f} leader={r.leader_code}")
        print()

        # ===== Module 3: Theme Lifecycle =====
        print("=" * 70)
        print("  Module 3: Theme Lifecycle Engine")
        print("=" * 70)
        self.lifecycle_results = self.theme_lifecycle.score(daily_all, universe, moneyflow)
        stages = {}
        for r in self.lifecycle_results.values():
            stages[r.stage] = stages.get(r.stage, 0) + 1
        print(f"  主题数: {len(self.lifecycle_results)}")
        print(f"  阶段分布: {stages}")
        for tname, r in list(self.lifecycle_results.items())[:5]:
            print(f"    {tname}: {r.stage} 趋势概率={r.trend_probability:.0f}% "
                  f"剩余{r.remaining_trend_duration}天 轮动={r.rotation_probability:.0f}%")
        print()

        # ===== Module 5: Leader Confirmation =====
        print("=" * 70)
        print("  Module 5: Leader Confirmation Engine")
        print("=" * 70)
        etf_close_map = {}
        for code, df in self._etf_data.items():
            if df is not None and not df.empty:
                etf_close_map[code] = df["close"].values.astype(float)
        self.leader_results = self.leader_confirm.score(
            self._etf_data, self._constituents, self._stock_data,
            etf_close_map, top_df, top_inst, moneyflow
        )
        print(f"  ETF数: {len(self.leader_results)}")
        for code, r in list(self.leader_results.items())[:5]:
            print(f"    {code} [{self.etf_theme_map.get(code,'')}]: "
                  f"leader={r.core_leader} score={r.leader_score:.1f}")
        print()

        # ===== Module 6: Risk Engine =====
        print("=" * 70)
        print("  Module 6: Risk Engine")
        print("=" * 70)
        self.risk_results = self.risk.score(self._etf_data, self._benchmark)
        print(f"  ETF数: {len(self.risk_results)}")
        for code, r in list(self.risk_results.items())[:5]:
            print(f"    {code}: risk={r.risk_score:.1f} pos={r.suggested_position*100:.0f}% "
                  f"stop={r.stop_loss*100:.1f}% tp={r.take_profit*100:.1f}%")
        print()

        # ===== Final Scoring =====
        print("=" * 70)
        print("  Final Scoring (Composite Engine)")
        print("=" * 70)
        self.final_results = self.composite.compute(
            self.etf_ranking_results, self.market_result,
            self.theme_results, self.lifecycle_results,
            self.leader_results, self.risk_results, self.etf_theme_map
        )
        print(f"  最终ETF数: {len(self.final_results)}")

        # ===== Buy/Sell Rules =====
        print("=" * 70)
        print("  Buy/Sell Rules Engine")
        print("=" * 70)
        for r in self.final_results:
            etf_df = self._etf_data.get(r.etf_code)
            leader_code = self.leader_results.get(r.etf_code)
            leader_df = None
            if leader_code and leader_code.core_leader:
                leader_df = self._stock_data.get(leader_code.core_leader)
            sig = self.rules.evaluate(r, etf_df, leader_df)
            r.buy = sig.buy
            r.hold = sig.hold
            r.sell = sig.sell
            self.signals[r.etf_code] = sig

        buy_count = sum(1 for r in self.final_results if r.buy)
        sell_count = sum(1 for r in self.final_results if r.sell)
        print(f"  买入信号: {buy_count}, 卖出信号: {sell_count}")
        print()

        # ===== 输出 =====
        results_with_signals = [(r, self.signals[r.etf_code]) for r in self.final_results]
        df = self.reporter.to_dataframe(results_with_signals)

        # 保存
        json_path = self.reporter.to_json(results_with_signals, trade_date)
        csv_path = self.reporter.to_csv(df, trade_date)
        print("=" * 70)
        print("  输出报告")
        print("=" * 70)
        top_n = self.config.get("general", {}).get("top_n", 10)
        self.reporter.print_top(df, top_n)
        print(f"\n  JSON: {json_path}")
        print(f"  CSV:  {csv_path}")
        print("=" * 70)
        return df


def main():
    parser = argparse.ArgumentParser(description="ETF Alpha Engine")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--date", default=None, help="交易日(YYYYMMDD)")
    parser.add_argument("--top-n", type=int, default=10, help="Top N输出")
    parser.add_argument("--etf-only", action="store_true", help="仅ETF排名(快速模式)")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    if args.version:
        print(f"ETF Alpha Engine v{__version__}")
        return

    engine = ETFAlphaEngine(args.config)
    df = engine.run_pipeline(trade_date=args.date)
    return df


if __name__ == "__main__":
    main()
