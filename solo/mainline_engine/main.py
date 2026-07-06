"""
机构主线识别系统 - 主流程编排器
=================================
Pipeline orchestrator for the Institutional Mainline Engine.

完整流程:
  1. 数据加载 (DataSource)
  2. ETF轮动评分 (ETFRotationEngine)
  3. 机构资金评分 (InstitutionFlowEngine)
  4. 市场热度评分 (MarketHeatEngine)
  5. 产业生命周期识别 (LifecycleEngine)
  6. 龙头发现 (LeaderDiscoveryEngine)
  7. 龙头持续性评分 (LeaderPersistenceEngine)
  8. 共振评分 (ResonanceEngine)
  9. 主题轮动 (ThemeRotationEngine)
  10. 风险评分 (RiskEngine)
  11. 买入信号检测 (BuyEngine)
  12. 卖出信号检测 (SellEngine)
  13. 综合评分 (CompositeEngine)
  14. 最终输出 (OutputReporter)

用法:
  python -m mainline_engine.main
  python -m mainline_engine.main --config config.yaml --top-n 30
  python -m mainline_engine.main --backtest --start 20240101 --end 20250706
"""

import os
import sys
import argparse
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from loguru import logger

from mainline_engine import __version__
from mainline_engine.data.source import DataSource, create_from_config
from mainline_engine.core.etf_rotation import ETFRotationEngine, ETFScoreResult
from mainline_engine.core.institution_flow import InstitutionFlowEngine, CapitalScoreResult
from mainline_engine.core.market_heat import MarketHeatEngine, HeatScoreResult
from mainline_engine.core.lifecycle import LifecycleEngine, LifecycleResult
from mainline_engine.core.leader import LeaderDiscoveryEngine, LeaderResult
from mainline_engine.core.persistence import LeaderPersistenceEngine, PersistenceResult
from mainline_engine.core.resonance import ResonanceEngine, ResonanceResult
from mainline_engine.core.theme_rotation import ThemeRotationEngine, ThemeResult
from mainline_engine.core.risk import RiskEngine, RiskResult
from mainline_engine.core.buy import BuyEngine, BuySignalResult
from mainline_engine.core.sell import SellEngine, SellSignalResult
from mainline_engine.core.composite import CompositeEngine, CompositeResult
from mainline_engine.output.reporter import OutputReporter, print_pipeline_summary


class MainlineEngine:
    """机构主线识别系统 主流程编排器"""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "config.yaml"
        )
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.data_source: Optional[DataSource] = None

        # 模块引擎
        self.etf_rotation = ETFRotationEngine(self.config)
        self.institution_flow = InstitutionFlowEngine(self.config)
        self.market_heat = MarketHeatEngine(self.config)
        self.lifecycle = LifecycleEngine(self.config)
        self.leader = LeaderDiscoveryEngine(self.config)
        self.persistence = LeaderPersistenceEngine(self.config)
        self.resonance = ResonanceEngine(self.config)
        self.theme_rotation = ThemeRotationEngine(self.config)
        self.risk = RiskEngine(self.config)
        self.buy_engine = BuyEngine(self.config)
        self.sell_engine = SellEngine(self.config)
        self.composite = CompositeEngine(self.config)
        self.reporter = OutputReporter(self.config)

        # Pipeline state
        self.etf_scores: Dict[str, ETFScoreResult] = {}
        self.capital_scores: Dict[str, CapitalScoreResult] = {}
        self.heat_scores: Dict[str, HeatScoreResult] = {}
        self.lifecycle_data: Dict[str, LifecycleResult] = {}
        self.leader_data: Dict[str, List[LeaderResult]] = {}
        self.persistence_data: Dict[str, PersistenceResult] = {}
        self.resonance_data: Dict[str, List[ResonanceResult]] = {}
        self.theme_data: Dict[str, ThemeResult] = {}
        self.risk_data: Dict[str, RiskResult] = {}
        self.buy_signals: Dict[str, BuySignalResult] = {}
        self.sell_signals: Dict[str, SellSignalResult] = {}
        self.composite_results: List[CompositeResult] = []

        # Data caches
        self._etf_data: Dict[str, pd.DataFrame] = {}
        self._stock_data: Dict[str, pd.DataFrame] = {}
        self._constituents: Dict[str, List[str]] = {}
        self._moneyflow_data: Dict[str, pd.DataFrame] = {}
        self._limit_up_data: Dict[str, pd.DataFrame] = {}
        self._benchmark_close: Optional[pd.Series] = None

        # ETF主题映射
        self.etf_theme_map: Dict[str, str] = self.config.get("etf_themes", {})

    def load_data(self, start_date: str, end_date: str) -> None:
        """加载全量数据"""
        logger.info("=" * 60)
        logger.info("阶段 0/14: 数据加载")
        logger.info("=" * 60)
        t0 = time.time()

        self.data_source = create_from_config(self.config_path)

        etf_list = self.config.get("etf_rotation", {}).get("etf_list", [])
        logger.info(f"目标ETF数量: {len(etf_list)}")

        # 加载ETF数据
        logger.info("加载ETF日线数据...")
        batch_etf = self.data_source.batch_load_etf_data(
            etf_list, start_date, end_date
        )
        # batch_load返回 {code: {daily: df, ...}}, 提取daily
        self._etf_data = {}
        for code, data in batch_etf.items():
            if isinstance(data, dict) and 'daily' in data:
                self._etf_data[code] = data['daily']
            elif isinstance(data, pd.DataFrame):
                self._etf_data[code] = data
        logger.info(f"  ETF数据: {len(self._etf_data)} 只")

        # 获取ETF成份股（从batch数据中提取）
        logger.info("获取ETF成份股列表...")
        for etf_code in etf_list:
            if etf_code in batch_etf:
                cons = batch_etf[etf_code].get('constituents', []) if isinstance(batch_etf[etf_code], dict) else []
                if cons:
                    self._constituents[etf_code] = cons
            else:
                try:
                    cons = self.data_source.get_etf_constituents(etf_code)
                    if cons:
                        self._constituents[etf_code] = cons
                except Exception:
                    pass
        all_cons = list(set(
            c for cons in self._constituents.values() for c in cons
        ))
        logger.info(f"  去重后成份股: {len(all_cons)} 只")

        if all_cons:
            # 加载个股日线
            logger.info("加载个股日线数据...")
            batch_stock = self.data_source.batch_load_stock_data(
                all_cons, start_date, end_date
            )
            self._stock_data = {}
            for code, data in batch_stock.items():
                if isinstance(data, dict) and 'daily' in data:
                    self._stock_data[code] = data['daily']
                elif isinstance(data, pd.DataFrame):
                    self._stock_data[code] = data
            logger.info(f"  个股数据: {len(self._stock_data)} 只")
        else:
            logger.warning("  ETF成份股为空，无法加载个股数据")

        # 加载基准指数
        logger.info("加载沪深300基准...")
        bm = self.data_source.get_index_daily(
            "000300.SH", start_date, end_date
        )
        if bm is not None and not bm.empty:
            bm = bm.sort_values("trade_date")
            self._benchmark_close = bm["close"].values

        dt = time.time() - t0
        logger.info(f"数据加载完成, 耗时 {dt:.1f}s")

    def _get_effective_date(self) -> str:
        """获取有效交易日：16点前用上个交易日（当天日线16点才更新）"""
        now = datetime.now()
        if now.hour < 16:
            target = now - timedelta(days=1)
        else:
            target = now
        while target.weekday() >= 5:
            target -= timedelta(days=1)
        return target.strftime("%Y%m%d")

    def load_etf_data_only(self, start_date: str, end_date: str) -> None:
        """仅加载ETF数据和基准（第一阶段）"""
        logger.info("=" * 60)
        logger.info("阶段 0a/14: 加载ETF数据")
        logger.info("=" * 60)
        t0 = time.time()

        if self.data_source is None:
            self.data_source = create_from_config(self.config_path)

        etf_list = self.config.get("etf_rotation", {}).get("etf_list", [])
        logger.info(f"目标ETF数量: {len(etf_list)}")

        logger.info("加载ETF日线数据...")
        batch_etf = self.data_source.batch_load_etf_data(etf_list, start_date, end_date)
        self._etf_data = {}
        for code, data in batch_etf.items():
            if isinstance(data, dict) and 'daily' in data:
                self._etf_data[code] = data['daily']
            elif isinstance(data, pd.DataFrame):
                self._etf_data[code] = data
        logger.info(f"  ETF数据: {len(self._etf_data)} 只")

        # 获取成份股列表（轻量API，不加载个股数据）
        self._constituents = {}
        for etf_code in etf_list:
            if etf_code in batch_etf:
                cons = batch_etf[etf_code].get('constituents', []) if isinstance(batch_etf[etf_code], dict) else []
                if cons:
                    self._constituents[etf_code] = cons

        logger.info("加载沪深300基准...")
        bm = self.data_source.get_index_daily("000300.SH", start_date, end_date)
        if bm is not None and not bm.empty:
            bm = bm.sort_values("trade_date")
            self._benchmark_close = bm["close"].values

        dt = time.time() - t0
        logger.info(f"ETF数据加载完成, 耗时 {dt:.1f}s")

    def load_stock_data_for_strong_etfs(self, start_date: str, end_date: str) -> None:
        """仅加载强势ETF的成份股数据（第二阶段，减少不必要的数据加载）"""
        logger.info("=" * 60)
        logger.info("阶段 0b/14: 加载强势ETF成份股")
        logger.info("=" * 60)
        t0 = time.time()

        strong_etfs = list(self.etf_scores.keys())
        filtered_constituents = {
            k: v for k, v in self._constituents.items() if k in strong_etfs
        }
        all_cons = list(set(c for cons in filtered_constituents.values() for c in cons))
        logger.info(f"强势ETF: {len(strong_etfs)} 只, 去重后成份股: {len(all_cons)} 只")

        if all_cons:
            batch_stock = self.data_source.batch_load_stock_data(all_cons, start_date, end_date)
            self._stock_data = {}
            for code, data in batch_stock.items():
                if isinstance(data, dict) and 'daily' in data:
                    self._stock_data[code] = data['daily']
                elif isinstance(data, pd.DataFrame):
                    self._stock_data[code] = data
            logger.info(f"  个股数据: {len(self._stock_data)} 只")
        else:
            logger.warning("  无成份股需加载")

        dt = time.time() - t0
        logger.info(f"成份股数据加载完成, 耗时 {dt:.1f}s")

    def run_pipeline(self, start_date: str = None, end_date: str = None) -> List[CompositeResult]:
        """运行完整评分流水线"""
        if end_date is None:
            end_date = self._get_effective_date()
        if start_date is None:
            d = datetime.strptime(end_date, "%Y%m%d") - timedelta(days=365)
            start_date = d.strftime("%Y%m%d")

        logger.info(f"数据区间: {start_date} ~ {end_date} (16点前使用上个交易日)")

        # 阶段0a: 加载ETF数据
        if not self._etf_data:
            self.load_etf_data_only(start_date, end_date)

        step_times = {}
        pipeline_start = time.time()

        # ==========================================
        # 阶段 1: ETF轮动评分
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 1/14: ETF轮动评分 (ETFRotationEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        all_etf_scores = self.etf_rotation.score(self._etf_data, self._benchmark_close)
        score_threshold = self.config.get("etf_rotation", {}).get("score_threshold", 50)
        self.etf_scores = {
            k: v for k, v in all_etf_scores.items()
            if v.rotation_score >= score_threshold
        }
        step_times["1_etf_rotation"] = time.time() - t1
        logger.info(f"  评分ETF: {len(all_etf_scores)}, 通过阈值({score_threshold}): {len(self.etf_scores)}")

        if not self.etf_scores:
            logger.warning("没有ETF通过评分阈值，提前结束")
            return []
        strong_etfs = list(self.etf_scores.keys())

        # ==========================================
        # 阶段 0b: 加载强势ETF成份股（仅加载通过阈值的ETF成份股）
        # ==========================================
        if not self._stock_data:
            self.load_stock_data_for_strong_etfs(start_date, end_date)

        # ==========================================
        # 阶段 2: 机构资金评分
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 2/14: 机构资金评分 (InstitutionFlowEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.capital_scores = self.institution_flow.score(
            self._moneyflow_data, None, None, None, None
        )
        step_times["2_capital"] = time.time() - t1
        logger.info(f"  资金评分: {len(self.capital_scores)}")

        # ==========================================
        # 阶段 3: 市场热度评分
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 3/14: 市场热度评分 (MarketHeatEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.heat_scores = self.market_heat.score(
            None, self._limit_up_data, None, None
        )
        step_times["3_heat"] = time.time() - t1
        logger.info(f"  热度评分: {len(self.heat_scores)}")

        # ==========================================
        # 阶段 4: 产业生命周期
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 4/14: 产业生命周期识别 (LifecycleEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.lifecycle_data = self.lifecycle.score(
            self._etf_data, self.etf_scores, self.capital_scores,
            self.heat_scores, self._limit_up_data
        )
        step_times["4_lifecycle"] = time.time() - t1
        stages = {}
        for r in self.lifecycle_data.values():
            stages[r.lifecycle_stage] = stages.get(r.lifecycle_stage, 0) + 1
        logger.info(f"  生命周期分布: {stages}")

        # ==========================================
        # 阶段 5: 龙头发现
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 5/14: 龙头发现 (LeaderDiscoveryEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        filtered_constituents = {
            k: v for k, v in self._constituents.items() if k in strong_etfs
        }
        etf_trend_scores = {k: v.rotation_score for k, v in self.etf_scores.items()}
        self.leader_data = self.leader.score(
            self._stock_data, self._etf_data, filtered_constituents,
            etf_trend_scores, self._benchmark_close
        )
        step_times["5_leader"] = time.time() - t1
        total_leaders = sum(len(v) for v in self.leader_data.values())
        logger.info(f"  龙头候选: {total_leaders} 只, 覆盖ETF: {len(self.leader_data)}")

        if not self.leader_data:
            logger.warning("没有发现龙头候选，提前结束")
            return []

        # ==========================================
        # 阶段 6: 龙头持续性
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 6/14: 龙头持续性评分 (LeaderPersistenceEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.persistence_data = self.persistence.score(
            self._stock_data, self.leader_data, self._etf_data
        )
        step_times["6_persistence"] = time.time() - t1
        logger.info(f"  持续性评分: {len(self.persistence_data)}")

        # ==========================================
        # 阶段 7: 共振评分
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 7/14: 共振评分 (ResonanceEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        capital_scores_flat = {k: v.capital_score for k, v in self.capital_scores.items()}
        heat_scores_flat = {k: v.heat_score for k, v in self.heat_scores.items()}
        persist_scores_flat = {k: v.persistence_score for k, v in self.persistence_data.items()}
        self.resonance_data = self.resonance.score(
            self.leader_data, etf_trend_scores, capital_scores_flat,
            heat_scores_flat, persist_scores_flat, self.etf_theme_map
        )
        step_times["7_resonance"] = time.time() - t1
        total_res = sum(len(v) for v in self.resonance_data.values())
        logger.info(f"  共振评分: {total_res} 对")

        # ==========================================
        # 阶段 8: 主题轮动
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 8/14: 主题轮动 (ThemeRotationEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.theme_data = self.theme_rotation.score(
            self.resonance_data, self.lifecycle_data, self.etf_theme_map,
            etf_trend_scores, capital_scores_flat, heat_scores_flat
        )
        step_times["8_theme"] = time.time() - t1
        main_themes = [t for t in self.theme_data.values() if t.theme_type == "main"]
        sec_themes = [t for t in self.theme_data.values() if t.theme_type == "secondary"]
        logger.info(f"  主线主题: {len(main_themes)}, 次线主题: {len(sec_themes)}")

        # ==========================================
        # 阶段 9: 风险评分
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 9/14: 风险评分 (RiskEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        all_candidate_codes = list(set(
            rr.ts_code
            for etf_list in self.resonance_data.values()
            for rr in etf_list
        ))
        candidate_stock_data = {
            k: v for k, v in self._stock_data.items() if k in all_candidate_codes
        }
        self.risk_data = self.risk.score(
            candidate_stock_data, self._etf_data, None
        )
        step_times["9_risk"] = time.time() - t1
        logger.info(f"  风险评分: {len(self.risk_data)} 只")

        # ==========================================
        # 阶段 10: 买入信号检测
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 10/14: 买入信号检测 (BuyEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.buy_signals = self.buy_engine.detect(
            candidate_stock_data, self._etf_data, self.leader_data, etf_trend_scores
        )
        step_times["10_buy"] = time.time() - t1
        buy_count = sum(1 for s in self.buy_signals.values() if s.signal_strength > 0)
        logger.info(f"  买入信号: {buy_count} 只")

        # ==========================================
        # 阶段 11: 卖出信号检测
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 11/14: 卖出信号检测 (SellEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.sell_signals = self.sell_engine.detect(
            candidate_stock_data, self.buy_signals,
            self._etf_data, etf_trend_scores, self.theme_data, self.leader_data
        )
        step_times["11_sell"] = time.time() - t1
        sell_count = sum(1 for s in self.sell_signals.values() if s.signal_strength > 0)
        logger.info(f"  卖出信号: {sell_count} 只")

        # ==========================================
        # 阶段 12: 综合评分
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 12/14: 综合评分 (CompositeEngine)")
        logger.info("=" * 60)
        t1 = time.time()
        self.composite_results = self.composite.compute(
            self.resonance_data, self.etf_scores, self.capital_scores,
            self.heat_scores, self.lifecycle_data, self.leader_data,
            self.persistence_data, self.risk_data,
            self.buy_signals, self.sell_signals, self.etf_theme_map
        )
        step_times["12_composite"] = time.time() - t1
        logger.info(f"  综合评分: {len(self.composite_results)} 只")

        # ==========================================
        # 阶段 13-14: 输出报告
        # ==========================================
        logger.info("")
        logger.info("=" * 60)
        logger.info("阶段 13/14: 报告生成 (OutputReporter)")
        logger.info("=" * 60)
        t1 = time.time()
        self._generate_reports()
        step_times["13_report"] = time.time() - t1

        total_time = time.time() - pipeline_start
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"流水线完成, 总耗时 {total_time:.1f}s")
        logger.info("=" * 60)

        for step, t in step_times.items():
            logger.debug(f"  {step}: {t:.2f}s")

        print_pipeline_summary(self.composite_results, total_time)

        return self.composite_results

    def _generate_reports(self, top_n: int = None) -> None:
        """生成所有输出报告"""
        if top_n is None:
            top_n = self.config.get("general", {}).get("top_n", 20)

        if not self.composite_results:
            logger.warning("无结果可输出")
            return

        top_picks = self.composite_results[:top_n]

        # HTML报告
        html_path = self.reporter.generate_html_report(top_picks)
        if html_path:
            logger.info(f"  HTML报告: {html_path}")

        # JSON导出
        json_path = self.reporter.generate_json_export(self.composite_results)
        if json_path:
            logger.info(f"  JSON导出: {json_path}")

        # 主题汇总
        theme_df = self.reporter.generate_theme_summary(
            self.composite_results, self.theme_data
        )
        if theme_df is not None and not theme_df.empty:
            logger.info(f"  主题数量: {len(theme_df)}")

        # Markdown摘要
        md = self.reporter.generate_markdown_summary(top_picks)
        logger.debug(f"  Markdown摘要已生成")

        # 打印前3名
        logger.info(f"  Top 3:")
        for i, r in enumerate(top_picks[:3]):
            logger.info(f"    {i+1}. {r.ts_code} {r.stock_name} "
                       f"[{r.theme}] 综合={r.composite_score:.1f} "
                       f"置信度={r.confidence}")

    def run_backtest(self, start_date: str, end_date: str, strategy: str = "composite") -> None:
        """运行回测"""
        from mainline_engine.backtest.engine import BacktestEngine
        from mainline_engine.backtest.metrics import compute_metrics

        logger.info("=" * 60)
        logger.info("回测模式启动")
        logger.info("=" * 60)

        # 加载数据
        self.load_data(start_date, end_date)

        # 运行流水线获取信号
        results = self.run_pipeline(start_date, end_date)

        if not results:
            logger.warning("无信号，无法回测")
            return

        # 构建信号DataFrame
        signals_list = []
        for r in results:
            if r.buy_signal:
                signals_list.append({
                    "date": end_date,
                    "ts_code": r.ts_code,
                    "signal_type": r.buy_signal,
                    "composite_score": r.composite_score,
                })

        if not signals_list:
            logger.warning("无买入信号，无法回测")
            return

        signals_df = pd.DataFrame(signals_list)

        # 构建价格DataFrame
        price_rows = []
        for ts_code, df in self._stock_data.items():
            if df is None or df.empty:
                continue
            df = df.tail(5)
            for _, row in df.iterrows():
                price_rows.append({
                    "date": row.get("trade_date", ""),
                    "ts_code": ts_code,
                    "open": row.get("open", 0),
                    "high": row.get("high", 0),
                    "low": row.get("low", 0),
                    "close": row.get("close", 0),
                })

        prices_df = pd.DataFrame(price_rows)

        if prices_df.empty:
            logger.warning("无价格数据，无法回测")
            return

        # 执行回测
        bt_engine = BacktestEngine(self.config)
        result = bt_engine.run_backtest(signals_df, prices_df)

        metrics = result.get("metrics")
        if metrics:
            logger.info(f"回测结果:")
            logger.info(f"  总交易: {metrics.total_trades}")
            logger.info(f"  胜率: {metrics.win_rate:.1%}")
            logger.info(f"  年化收益: {metrics.annual_return:.1%}")
            logger.info(f"  最大回撤: {metrics.max_drawdown_pct:.1%}")
            logger.info(f"  Sharpe: {metrics.sharpe_ratio:.2f}")
            logger.info(f"  Sortino: {metrics.sortino_ratio:.2f}")
            logger.info(f"  Calmar: {metrics.calmar_ratio:.2f}")

        # 可选: 参数优化
        logger.info("")
        logger.info("执行Grid Search...")
        param_grid = {
            "score_threshold": [40, 50, 60],
            "max_positions": [5, 10, 15],
        }

        def scoring_func(df):
            return len(df)

        grid_results = bt_engine.grid_search(prices_df, scoring_func, param_grid)
        if grid_results is not None and not grid_results.empty:
            logger.info(f"  Grid Search完成, 共{len(grid_results)}组")

    def score_etfs_only(self) -> pd.DataFrame:
        """仅运行ETF轮动评分（快速模式）"""
        etf_list = self.config.get("etf_rotation", {}).get("etf_list", [])
        end_date = self._get_effective_date()
        start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")

        self.data_source = create_from_config(self.config_path)
        # 直接获取ETF日线（不经过batch_load的复杂缓存）
        etf_data = self.data_source.get_etf_data(etf_list, start, end_date)

        bm = self.data_source.get_index_daily("000300.SH", start, end_date)
        bm_close = bm["close"].values if bm is not None and not bm.empty else None

        scores = self.etf_rotation.score(etf_data, bm_close)
        rows = []
        for code, r in scores.items():
            rows.append({
                "etf_code": code,
                "rotation_score": r.rotation_score,
                "trend": r.trend_score,
                "rs": r.rs_score,
                "momentum": r.momentum_score,
                "adx": r.adx_score,
                "theme": self.etf_theme_map.get(code, ""),
            })
        df = pd.DataFrame(rows)
        df = df.sort_values("rotation_score", ascending=False).reset_index(drop=True)
        df.index = df.index + 1
        df.index.name = "rank"
        return df


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="机构主线识别系统 (Institutional Mainline Engine)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m mainline_engine.main
  python -m mainline_engine.main --top-n 30
  python -m mainline_engine.main --etf-only
  python -m mainline_engine.main --backtest --start 20240101 --end 20250706
  python -m mainline_engine.main --config my_config.yaml
        """
    )
    parser.add_argument("--config", default=None,
                        help="配置文件路径")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Top N 输出")
    parser.add_argument("--start", default=None,
                        help="开始日期 YYYYMMDD")
    parser.add_argument("--end", default=None,
                        help="结束日期 YYYYMMDD")
    parser.add_argument("--etf-only", action="store_true",
                        help="仅运行ETF轮动评分（快速模式）")
    parser.add_argument("--backtest", action="store_true",
                        help="运行回测")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", action="store_true",
                        help="显示版本号")
    return parser.parse_args()


def main():
    """主入口"""
    args = parse_args()

    if args.version:
        print(f"机构主线识别系统 v{__version__}")
        return

    # 配置日志
    logger.remove()
    logger.add(
        sys.stderr,
        level=getattr(logging, args.log_level),
        format="<green>{time:HH:mm:ss}</green> | <level>{level:5s}</level> | <level>{message}</level>",
        colorize=True,
    )

    engine = MainlineEngine(args.config)

    if args.etf_only:
        df = engine.score_etfs_only()
        print("\nETF轮动评分排名:")
        print("=" * 80)
        print(df.to_string())
        return

    if args.backtest:
        start = args.start or "20240101"
        end = args.end or datetime.now().strftime("%Y%m%d")
        engine.run_backtest(start, end)
        return

    # 全流水线
    start = args.start
    end = args.end
    results = engine.run_pipeline(start_date=start, end_date=end)

    if results:
        top_n = args.top_n
        df = engine.reporter.to_dataframe(results[:top_n])
        print(f"\nTop {top_n} 机构主线候选:")
        print("=" * 100)
        print(df.to_string())
    else:
        print("\n无结果输出。请检查数据源和配置。")
        print("提示: 使用 --etf-only 可快速验证ETF数据是否正常")


if __name__ == "__main__":
    main()
