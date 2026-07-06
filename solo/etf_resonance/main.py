#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Resonance Stock Selection System - Main Entry Point
========================================================

机构主线龙头排行榜 Pipeline:

1. Load data (ETFs, stocks, constituents)
2. ETF Trend Score & Filter
3. ETF Persistence Score
4. Leader Score (stocks vs ETF)
5. Resonance Score (stock-ETF synergy)
6. Risk Score
7. Composite Ranking
8. Buy/Sell Signal Detection
9. Output Report

Usage:
    python -m etf_resonance.main
    python -m etf_resonance.main --config config.yaml
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from etf_resonance import __version__
from etf_resonance.utils.helpers import Config, setup_logger
from etf_resonance.data.loader import DataLoader, MarketData
from etf_resonance.core.trend import TrendScorer, TrendResult
from etf_resonance.core.persistence import PersistenceScorer, PersistenceResult
from etf_resonance.core.leader import LeaderScorer, LeaderResult
from etf_resonance.core.resonance import ResonanceScorer, ResonanceResult
from etf_resonance.core.risk import RiskScorer, RiskResult
from etf_resonance.core.ranking import RankingEngine, CompositeResult
from etf_resonance.core.signal import SignalDetector, BuySignal, SellSignal
from etf_resonance.ml.ranking_model import MLRankingModel, MLConfig

logger = logging.getLogger(__name__)


class ETFResonanceSystem:
    """Main pipeline orchestrator for the ETF Resonance System."""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = Config(config_path) if os.path.exists(config_path) else None
        self.data_loader = DataLoader()
        self.trend_scorer = TrendScorer(self.config)
        self.persistence_scorer = PersistenceScorer(self.config)
        self.leader_scorer = LeaderScorer(self.config)
        self.resonance_scorer = ResonanceScorer(self.config)
        self.risk_scorer = RiskScorer(self.config)
        self.ranking_engine = RankingEngine(self.config)
        self.signal_detector = SignalDetector(self.config)

        self.ml_model = MLRankingModel()
        ml_cfg = self.config.get("ml") if self.config else None
        self.ml_enabled = ml_cfg and ml_cfg.get("enabled", False)

        # Pipeline state
        self.market_data: Optional[MarketData] = None
        self.trend_results: Dict[str, TrendResult] = {}
        self.persistence_results: Dict[str, PersistenceResult] = {}
        self.leader_results: Dict[str, List[LeaderResult]] = {}
        self.resonance_results: Dict[str, List[ResonanceResult]] = {}
        self.risk_results: Dict[str, RiskResult] = {}
        self.ranking_results: List[CompositeResult] = []

    def load_data(self,
                  etf_data: Dict[str, pd.DataFrame],
                  stock_data: Dict[str, pd.DataFrame],
                  constituents: Dict[str, List[str]],
                  etf_theme_map: Optional[Dict[str, str]] = None,
                  start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> None:
        """Load and prepare all market data."""
        logger.info(f"Loading data: {len(etf_data)} ETFs, {len(stock_data)} stocks")
        self.market_data = self.data_loader.prepare_market_data(
            etf_data, stock_data, constituents, etf_theme_map,
            start_date, end_date,
        )
        logger.info(f"Data ready: {len(self.market_data.etf_list)} ETFs, "
                   f"{len(self.market_data.stock_list)} stocks")

    def run_pipeline(self, benchmark_close: Optional[pd.Series] = None) -> List[CompositeResult]:
        """Run the full scoring and ranking pipeline."""
        if self.market_data is None:
            raise ValueError("No data loaded. Call load_data() first.")

        step_times = {}
        t0 = datetime.now()

        # ════════════════════════════════════════
        # Step 1: ETF Trend Score
        # ════════════════════════════════════════
        logger.info("Step 1/7: ETF Trend Score...")
        t1 = datetime.now()
        all_trends = self.trend_scorer.score(
            self.market_data.etf_data, benchmark_close
        )
        self.trend_results = self.trend_scorer.filter_etfs(all_trends, self.config)
        step_times["1_trend"] = (datetime.now() - t1).total_seconds()
        logger.info(f"  {len(all_trends)} scored → {len(self.trend_results)} passed filter")

        if not self.trend_results:
            logger.warning("No ETFs passed the trend filter. Check config thresholds.")
            return []

        # ════════════════════════════════════════
        # Step 2: ETF Persistence Score
        # ════════════════════════════════════════
        logger.info("Step 2/7: ETF Persistence Score...")
        t1 = datetime.now()
        # Only score ETFs that passed trend filter
        filtered_etf_data = {
            k: v for k, v in self.market_data.etf_data.items()
            if k in self.trend_results
        }
        self.persistence_results = self.persistence_scorer.score(filtered_etf_data)
        step_times["2_persistence"] = (datetime.now() - t1).total_seconds()
        logger.info(f"  {len(self.persistence_results)} scored")

        # Filter by persistence
        etf_filter_cfg = self.config.get("etf_filter") if self.config else {}
        persist_min = etf_filter_cfg.get("persistence_min", 70) if etf_filter_cfg else 70
        for code in list(self.trend_results.keys()):
            if code in self.persistence_results:
                if self.persistence_results[code].persistence_score < persist_min:
                    del self.trend_results[code]
            else:
                del self.trend_results[code]
        logger.info(f"  After persistence filter: {len(self.trend_results)} ETFs")

        # ════════════════════════════════════════
        # Step 3: Leader Score (stocks within ETFs)
        # ════════════════════════════════════════
        logger.info("Step 3/7: Leader Score...")
        t1 = datetime.now()
        etf_trend_scores = {k: v.trend_score for k, v in self.trend_results.items()}
        filtered_constituents = {
            k: v for k, v in self.market_data.constituents.items()
            if k in self.trend_results
        }
        self.leader_results = self.leader_scorer.score(
            self.market_data.stock_data,
            self.market_data.etf_data,
            filtered_constituents,
            etf_trend_scores,
        )
        step_times["3_leader"] = (datetime.now() - t1).total_seconds()
        total_stocks = sum(len(v) for v in self.leader_results.values())
        logger.info(f"  {total_stocks} stocks scored across {len(self.leader_results)} ETFs")

        if not self.leader_results:
            logger.warning("No leader results. Check stock data coverage.")
            return []

        # ════════════════════════════════════════
        # Step 4: Resonance Score
        # ════════════════════════════════════════
        logger.info("Step 4/7: Resonance Score...")
        t1 = datetime.now()
        self.resonance_results = self.resonance_scorer.score(
            self.leader_results,
            self.trend_results,
            self.persistence_results,
            self.market_data.etf_theme,
        )
        step_times["4_resonance"] = (datetime.now() - t1).total_seconds()
        logger.info(f"  {sum(len(v) for v in self.resonance_results.values())} pairs scored")

        # ════════════════════════════════════════
        # Step 5: Risk Score
        # ════════════════════════════════════════
        logger.info("Step 5/7: Risk Score...")
        t1 = datetime.now()
        all_candidate_codes = list(set(
            rr.ts_code
            for etf_list in self.leader_results.values()
            for rr in etf_list
        ))
        candidate_stock_data = {
            k: v for k, v in self.market_data.stock_data.items()
            if k in all_candidate_codes
        }
        self.risk_results = self.risk_scorer.score(candidate_stock_data)
        step_times["5_risk"] = (datetime.now() - t1).total_seconds()
        logger.info(f"  {len(self.risk_results)} stocks risk-scored")

        # ════════════════════════════════════════
        # Step 6: Composite Ranking
        # ════════════════════════════════════════
        logger.info("Step 6/7: Composite Ranking...")
        t1 = datetime.now()
        self.ranking_results = self.ranking_engine.rank(
            self.resonance_results,
            self.trend_results,
            self.persistence_results,
            self.leader_results,
            self.risk_results,
            self.market_data.etf_theme,
        )
        step_times["6_ranking"] = (datetime.now() - t1).total_seconds()
        logger.info(f"  {len(self.ranking_results)} ranked")

        # Apply ML ranking if enabled
        if self.ml_enabled:
            logger.info("  Applying ML ranking overlay...")
            df_ranking = self.ranking_engine.to_dataframe(self.ranking_results)
            features = self.ml_model.prepare_features(df_ranking)
            ml_scores = self.ml_model.predict(features)
            for i, r in enumerate(self.ranking_results):
                if i < len(ml_scores):
                    r.composite_score = np.clip(
                        0.5 * r.composite_score + 0.5 * ml_scores[i], 0, 100
                    )
            self.ranking_results.sort(key=lambda x: -x.composite_score)
            for i, r in enumerate(self.ranking_results):
                r.rank = i + 1

        # ════════════════════════════════════════
        # Step 7: Signal Detection (Top N only)
        # ════════════════════════════════════════
        logger.info("Step 7/7: Signal Detection (Top picks)...")
        t1 = datetime.now()
        general_cfg = self.config.get("general") if self.config else {}
        top_n = general_cfg.get("top_n", 20) if general_cfg else 20
        top_picks = self.ranking_results[:top_n]
        buy_signals = self._detect_signals(top_picks)
        step_times["7_signal"] = (datetime.now() - t1).total_seconds()
        logger.info(f"  Buy signals detected for {sum(1 for s in buy_signals if s)} stocks")

        total_time = (datetime.now() - t0).total_seconds()
        logger.info(f"Pipeline complete in {total_time:.2f}s")
        for step, t in step_times.items():
            logger.debug(f"  {step}: {t:.3f}s")

        return self.ranking_results

    def _detect_signals(self, top_picks: List[CompositeResult]) -> List[Optional[BuySignal]]:
        """Detect buy/sell signals for top ranked picks."""
        signals = []
        for pick in top_picks:
            df = self.market_data.stock_data.get(pick.ts_code)
            if df is not None:
                buy_signal = self.signal_detector.detect_buy(df)
                signals.append(buy_signal)
            else:
                signals.append(None)

        # Attach buy signals back to ranking results
        for i, signal in enumerate(signals):
            if signal is not None and i < len(top_picks):
                pass  # Signals are attached via the CompositeResult if needed

        return signals

    def get_report(self, top_n: int = 20) -> str:
        """Get the final formatted ranking report."""
        if not self.ranking_results:
            return "No ranking results available. Run run_pipeline() first."

        return self.ranking_engine.format_report(self.ranking_results, top_n)

    def get_top_picks(self, top_n: int = 20) -> List[CompositeResult]:
        """Get the top N ranked stock picks."""
        return self.ranking_results[:top_n]

    def get_score_summary(self, top_n: int = 20) -> pd.DataFrame:
        """Get a DataFrame summary of the top picks."""
        return self.ranking_engine.to_dataframe(self.ranking_results[:top_n])


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="ETF Resonance Stock Selection System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m etf_resonance.main
  python -m etf_resonance.main --config my_config.yaml
  python -m etf_resonance.main --top-n 30
        """
    )
    parser.add_argument("--config", default="config.yaml",
                       help="Configuration file path")
    parser.add_argument("--top-n", type=int, default=20,
                       help="Number of top picks to display")
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", action="store_true",
                       help="Show version and exit")
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    if args.version:
        print(f"ETF Resonance System v{__version__}")
        return

    # Setup logging
    setup_logger(level=getattr(logging, args.log_level))
    logger.info(f"ETF Resonance System v{__version__} initializing...")

    # Load config
    config_path = os.path.join(os.path.dirname(__file__), args.config)
    if not os.path.exists(config_path):
        config_path = args.config

    system = ETFResonanceSystem(config_path)

    # ── Example: Load data from existing project DataFrames ──
    # This section shows how to load from external sources.
    # Replace these with your actual data loading code.
    #
    # etf_data = {...}   # Dict[str, pd.DataFrame]
    # stock_data = {...}  # Dict[str, pd.DataFrame]
    # constituents = {...}  # Dict[str, List[str]]
    #
    # system.load_data(etf_data, stock_data, constituents)

    logger.info("System ready. Call load_data() with your market data, "
               "then run_pipeline() to generate rankings.")
    logger.info("")
    logger.info("Example usage in your code:")
    logger.info("  from etf_resonance.main import ETFResonanceSystem")
    logger.info("  system = ETFResonanceSystem()")
    logger.info("  system.load_data(etf_df_dict, stock_df_dict, const_map)")
    logger.info("  results = system.run_pipeline()")
    logger.info("  print(system.get_report(top_n=20))")
    logger.info("")

    # If data is provided via command line, run full pipeline
    # For now, print the config summary
    print("\n" + "=" * 60)
    print("ETF Resonance Stock Selection System")
    print("=" * 60)
    print(f"  Version: {__version__}")
    print(f"  Config: {config_path}")
    print(f"  Top N: {args.top_n}")
    print(f"  Log Level: {args.log_level}")
    print("=" * 60)
    print()
    print("Configuration sections:")
    if system.config:
        for section in ["trend", "persistence", "etf_filter", "leader",
                        "resonance", "composite", "risk", "buy_signal",
                        "sell_signal", "backtest"]:
            data = system.config.get(section) or {}
            if data:
                print(f"  [{section}]")
                for k, v in (data.items() if isinstance(data, dict) else data.__dict__.items()):
                    print(f"    {k}: {v}")
    print()


if __name__ == "__main__":
    main()
