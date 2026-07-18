#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Alpha Ranking System - Main Orchestrator
=============================================
Daily production pipeline (8 steps, <5 min target) + training entry points.

Daily Pipeline:
  Step 1: Update TDX data
  Step 2: Update low-frequency Tushare data if needed
  Step 3: Calculate theme persistence (Module D)
  Step 4: Calculate leader persistence (Module E)
  Step 5: Generate ETF features (Modules A-G)
  Step 6: Load LightGBM model
  Step 7: Predict ETF ranking
  Step 8: Generate trading report (CSV + portfolio decision)

Training:
  python -m etf_alpha_ranking.main train
  python -m etf_alpha_ranking.main --date 20260717
  python -m etf_alpha_ranking.main backtest
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_alpha_ranking import __version__
from etf_alpha_ranking.tdx_reader import TDXReader
from etf_alpha_ranking.database import Database
from etf_alpha_ranking.tushare_ref import TushareRef
from etf_alpha_ranking.features import FeatureEngine
from etf_alpha_ranking.theme_persistence import ThemePersistenceEngine, ThemePersistenceResult
from etf_alpha_ranking.leader_persistence import LeaderPersistenceEngine, LeaderResult
from etf_alpha_ranking.breadth import BreadthEngine, BreadthResult
from etf_alpha_ranking.market_regime import MarketRegimeEngine, MarketRegimeResult
from etf_alpha_ranking.labels import LabelBuilder
from etf_alpha_ranking.ranker import LGBRankerModel
from etf_alpha_ranking.trainer import Trainer, TrainResult
from etf_alpha_ranking.backtest import WalkForwardBacktester, BacktestMetrics
from etf_alpha_ranking.portfolio import PortfolioEngine, TradeSignal
from etf_alpha_ranking.explainer import Explainer
from etf_alpha_ranking.reporter import Reporter

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

LOG = logging.getLogger("etf_alpha_ranking")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def load_config(path: str = None) -> dict:
    path = path or os.path.join(BASE_DIR, "config.yaml")
    if yaml is None:
        with open(path, "r", encoding="utf-8") as f:
            # very small yaml subset fallback
            return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class AlphaRankingEngine:
    """Main orchestrator."""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(BASE_DIR, "config.yaml")
        self.config = load_config(self.config_path)
        data_cfg = self.config.get("data", {})
        self.tdx = TDXReader(data_cfg.get("tdx_root", "C:/new_tdx"),
                             data_cfg.get("daily_cache_path", ""))
        self.db = Database(self.config.get("database", {}).get(
            "path", os.path.join(BASE_DIR, "data/database/market.db")))
        self.ref = TushareRef(self.config, self.db)
        self.feat_engine = FeatureEngine(self.config)
        self.theme_engine = ThemePersistenceEngine(self.config)
        self.leader_engine = LeaderPersistenceEngine(self.config)
        self.breadth_engine = BreadthEngine(self.config)
        self.market_engine = MarketRegimeEngine(self.config)
        self.label_builder = LabelBuilder(self.config)
        self.trainer = Trainer(self.config)
        self.backtester = WalkForwardBacktester(self.config)
        self.portfolio = PortfolioEngine(self.config)
        self.reporter = Reporter(self.config)
        self.etf_theme_map: Dict[str, str] = self.config.get("etf_universe", {})
        self.etf_list = list(self.etf_theme_map.keys())
        self.benchmark = self.config.get("general", {}).get("benchmark", "000300.SH")
        self.lookback = self.config.get("general", {}).get("lookback_days", 500)
        self.target_horizon = self.config.get("general", {}).get("target_horizon", 40)

    # ==================================================================
    # DATA LOADING
    # ==================================================================
    def load_market_data(self, start_date: str, end_date: str) -> Tuple[
            Dict[str, pd.DataFrame], pd.DataFrame, Dict[str, List[str]], Dict[str, pd.DataFrame]]:
        """Load ETFs, benchmark, theme mapping, and constituent stocks."""
        etf_data = self.tdx.load_batch_etf(self.etf_list, start_date, end_date)
        bench_df = self.tdx.load_index(self.benchmark, start_date, end_date)
        # If the benchmark history is much shorter than the ETFs', build a
        # market proxy from the equal-weight ETF universe so training can use
        # the full available ETF history.
        etf_max_dates = max((len(df) for df in etf_data.values()), default=0)
        if bench_df.empty or len(bench_df) < 60 or len(bench_df) < etf_max_dates * 0.5:
            bench_df = self._build_market_proxy(etf_data, start_date, end_date)
            LOG.info("benchmark short/missing -> using ETF equal-weight proxy (%d rows)", len(bench_df))
        bench_close = (bench_df["close"].values.astype(float)
                       if not bench_df.empty else np.array([]))
        self.leader_engine.set_benchmark(bench_close)
        self.breadth_engine.set_benchmark(bench_close)

        # theme mapping (JSON first, then Tushare)
        theme_stocks = self.ref.update_theme_mapping(self.etf_theme_map)
        if not theme_stocks:
            # fallback: derive from db cache
            theme_stocks = self.db.get_theme_mapping()
        # fill missing themes with ETF constituents (Tushare fund_portfolio)
        n_before = len(theme_stocks)
        theme_stocks = self.ref.fill_missing_with_etf_components(
            self.etf_theme_map, theme_stocks)
        if len(theme_stocks) > n_before:
            LOG.info("theme mapping expanded: %d -> %d (ETF constituent fallback)",
                     n_before, len(theme_stocks))
        # collect unique constituent codes
        all_stocks = list({s for stocks in theme_stocks.values() for s in stocks})
        # limit per-theme to keep runtime bounded
        for k in list(theme_stocks.keys()):
            theme_stocks[k] = theme_stocks[k][:60]
        stock_data = self.tdx.load_batch_stocks(all_stocks, start_date, end_date)
        return etf_data, bench_df, theme_stocks, stock_data

    @staticmethod
    def _build_market_proxy(etf_data: Dict[str, pd.DataFrame],
                            start_date: str, end_date: str) -> pd.DataFrame:
        """Equal-weight ETF-universe index as a benchmark fallback.

        Each ETF is re-based to 1.0 at its first observation, then the
        cross-sectional mean is taken per date.
        """
        series = []
        for code, df in etf_data.items():
            if df is None or df.empty:
                continue
            d = df[["trade_date", "close"]].copy()
            d["trade_date"] = d["trade_date"].astype(str)
            d = d[(d["trade_date"] >= start_date) & (d["trade_date"] <= end_date)]
            if d.empty:
                continue
            d["close"] = d["close"].astype(float) / d["close"].iloc[0]
            d = d.rename(columns={"close": code})
            series.append(d.set_index("trade_date"))
        if not series:
            return pd.DataFrame(columns=["trade_date", "close"])
        joined = pd.concat(series, axis=1).sort_index()
        # equal-weight average across available ETFs each day
        joined["close"] = joined.mean(axis=1, skipna=True)
        out = joined[["close"]].reset_index().rename(columns={"index": "trade_date"})
        out["trade_date"] = out["trade_date"].astype(str)
        # fill other OHLCV columns for downstream compatibility
        out["open"] = out["high"] = out["low"] = out["close"]
        out["vol"] = 0.0
        out["amount"] = 0.0
        out["pct_chg"] = out["close"].pct_change().fillna(0.0) * 100.0
        return out

    # ==================================================================
    # ENGINE COMPUTATION (single date)
    # ==================================================================
    def compute_engines(self, date: str, etf_data: Dict[str, pd.DataFrame],
                        bench_df: pd.DataFrame, theme_stocks: Dict[str, List[str]],
                        stock_data: Dict[str, pd.DataFrame]) -> Tuple[
                            MarketRegimeResult,
                            Dict[str, ThemePersistenceResult],
                            Dict[str, LeaderResult],
                            Dict[str, BreadthResult]]:
        """Compute Modules D/E/F/G for a single date."""
        # Module G: market regime
        market_r = self.market_engine.score(bench_df, etf_data)
        # Module F: breadth
        breadth_results = self.breadth_engine.score_all(theme_stocks, stock_data)
        # Module E: leader
        leader_results = self.leader_engine.score_all(theme_stocks, stock_data)
        # Module D: theme persistence (uses breadth + leader scores per theme)
        breadth_scores = {t: r.breadth_score for t, r in breadth_results.items()}
        leader_scores = {t: r.leader_score for t, r in leader_results.items()}
        theme_results = self.theme_engine.score_all(theme_stocks, stock_data,
                                                    breadth_scores, leader_scores)
        LOG.info("engines for %s: market=%.1f, themes=%d, leaders=%d, breadth=%d",
                 date, market_r.market_score, len(theme_results),
                 len(leader_results), len(breadth_results))
        return market_r, theme_results, leader_results, breadth_results

    def _etf_theme(self, etf_code: str, theme_keys: set) -> str:
        t = self.etf_theme_map.get(etf_code, "")
        if t in theme_keys:
            return t
        for k in theme_keys:
            if t in k or k in t:
                return k
        return t

    # ==================================================================
    # FEATURE SNAPSHOT for one date
    # ==================================================================
    def snapshot_features(self, date: str,
                          etf_data: Dict[str, pd.DataFrame],
                          bench_df: pd.DataFrame,
                          market_r: MarketRegimeResult,
                          theme_results: Dict[str, ThemePersistenceResult],
                          leader_results: Dict[str, LeaderResult],
                          breadth_results: Dict[str, BreadthResult]) -> List[dict]:
        """Build one feature row per ETF for the given date (data up to date)."""
        rows = []
        theme_keys = set(theme_results.keys())
        bench_df = bench_df.sort_values("trade_date").reset_index(drop=True) if not bench_df.empty else bench_df
        bench_dates = (bench_df["trade_date"].astype(str).tolist()
                       if not bench_df.empty else [])
        for code, df in etf_data.items():
            df = df.sort_values("trade_date").reset_index(drop=True)
            seg = df[df["trade_date"] <= date]
            if len(seg) < 60:
                continue
            # align benchmark to this ETF's date range (up to date)
            seg_dates = seg["trade_date"].astype(str).tolist()
            if bench_dates:
                last_bench_idx = max(0, min(len(bench_dates) - 1,
                                            len(seg_dates) - 1))
                # take the benchmark tail matching the segment length
                bench_seg = bench_df[bench_df["trade_date"].astype(str).isin(seg_dates)]
                if len(bench_seg) >= 60:
                    bench_close = bench_seg["close"].values.astype(float)
                else:
                    # fall back to a length-matched tail of the benchmark
                    bench_close = (bench_df["close"].values.astype(float)[-len(seg_dates):]
                                   if len(bench_df) >= len(seg_dates) else
                                   bench_df["close"].values.astype(float))
            else:
                bench_close = seg["close"].values.astype(float)  # self-reference fallback
            theme_name = self._etf_theme(code, theme_keys)
            theme_r = theme_results.get(theme_name)
            leader_r = leader_results.get(theme_name)
            breadth_r = breadth_results.get(theme_name)
            feats = self.feat_engine.build(code, seg, bench_close, theme_r,
                                           leader_r, breadth_r, market_r)
            if not feats:
                continue
            feats["date"] = date
            feats["etf"] = code
            feats["theme_persistence"] = feats.get("theme_persistence", 0.0)
            feats["leader_score"] = feats.get("leader_score", 0.0)
            rows.append(feats)
        return rows

    # ==================================================================
    # PANEL BUILDER (training data)
    # ==================================================================
    def build_panel(self, start_date: str = "", end_date: str = "",
                    stride: int = 3) -> pd.DataFrame:
        """Build the training panel: one row per (date, ETF) with features + labels.

        Args:
            start_date/end_date: panel window (defaults from config.training)
            stride: sample every N trading days (controls panel size & build time)
        """
        tcfg = self.config.get("training", {})
        start = start_date or tcfg.get("train_start", "20180101")
        end = end_date or tcfg.get("test_end", "") or self.tdx.get_last_trade_date(self.etf_list)
        lookback = max(self.lookback, 250)
        dt_start = datetime.strptime(start, "%Y%m%d") - timedelta(days=int(lookback * 1.6))
        data_start = dt_start.strftime("%Y%m%d")
        LOG.info("build_panel: data %s ~ %s, sample %s ~ %s, stride=%d",
                 data_start, end, start, end, stride)

        etf_data, bench_df, theme_stocks, stock_data = self.load_market_data(data_start, end)
        if not etf_data:
            LOG.error("no ETF data loaded")
            return pd.DataFrame()
        bench_close = (bench_df["close"].values.astype(float)
                       if not bench_df.empty else np.array([]))

        # collect sample dates from the ETF universe (common trading calendar)
        # Use the union of all ETF dates so we don't get cut off by a short benchmark.
        all_etf_dates: set = set()
        for df in etf_data.values():
            all_etf_dates.update(df["trade_date"].astype(str).tolist())
        all_dates = sorted([d for d in all_etf_dates
                            if start <= d <= end])
        if not all_dates:
            LOG.error("no common trade dates between %s and %s", start, end)
            return pd.DataFrame()
        sample_dates = all_dates[::max(stride, 1)]
        LOG.info("panel sample dates: %d (of %d trading days)", len(sample_dates), len(all_dates))

        # Pre-compute engines at coarse dates and forward-fill to sample dates.
        # Engines are expensive (theme/leader/breadth), so compute weekly then ffill.
        coarse_stride = 5
        engine_cache: Dict[str, Tuple] = {}
        coarse_dates = sample_dates[::coarse_stride]
        # ensure the last sample date is included
        if sample_dates and sample_dates[-1] not in coarse_dates:
            coarse_dates.append(sample_dates[-1])
        LOG.info("computing engines at %d coarse dates...", len(coarse_dates))
        for i, d in enumerate(coarse_dates):
            if i % 10 == 0:
                LOG.info("  engine progress %d/%d", i, len(coarse_dates))
            engine_cache[str(d)] = self.compute_engines(d, etf_data, bench_df,
                                                        theme_stocks, stock_data)

        def _nearest_engine(d: str) -> Tuple:
            if d in engine_cache:
                return engine_cache[d]
            # find the nearest prior coarse date
            prior = [k for k in engine_cache if k <= d]
            if prior:
                return engine_cache[max(prior)]
            return engine_cache[min(engine_cache)] if engine_cache else None

        # Build feature rows
        all_rows: List[dict] = []
        for i, d in enumerate(sample_dates):
            d = str(d)
            eng = _nearest_engine(d)
            if eng is None:
                continue
            market_r, theme_r, leader_r, breadth_r = eng
            rows = self.snapshot_features(d, etf_data, bench_df, market_r,
                                          theme_r, leader_r, breadth_r)
            all_rows.extend(rows)
        if not all_rows:
            LOG.error("no feature rows built")
            return pd.DataFrame()
        panel = pd.DataFrame(all_rows)
        LOG.info("panel rows before labels: %d", len(panel))

        # Add labels (future returns + rank label)
        close_map = {c: df for c, df in etf_data.items()}
        panel = self.label_builder.add_future_returns(panel, close_map=close_map)
        panel = self.label_builder.add_rank_label(panel, self.target_horizon)
        # drop rows without a label (incomplete forward window)
        before = len(panel)
        panel = panel.dropna(subset=[self.label_builder.get_target_col()])
        LOG.info("panel after labels: %d (dropped %d unlabeled)", len(panel), before - len(panel))

        # cross-sectional RS rank features
        rs_cols = ["alpha20", "alpha40", "alpha60", "ret_20d", "ret_60d"]
        panel = self.feat_engine.add_cross_sectional(panel, rs_cols)
        return panel

    # ==================================================================
    # TRAINING
    # ==================================================================
    def train(self, stride: int = 3) -> TrainResult:
        LOG.info("=" * 70)
        LOG.info("  Training LGBMRanker (time-series split)")
        LOG.info("=" * 70)
        panel = self.build_panel(stride=stride)
        if panel.empty:
            LOG.error("empty panel, abort training")
            return TrainResult()
        LOG.info("panel: %d rows, %d ETFs, %d dates",
                 len(panel), panel["etf"].nunique(), panel["date"].nunique())
        res = self.trainer.train(panel, target_col="rank_label")
        print("\n" + "=" * 70)
        print(f"  Training complete")
        print(f"  Train samples: {res.n_train}")
        print(f"  Valid samples: {res.n_valid}")
        print(f"  Features:      {res.n_features}")
        print(f"  Best iter:     {res.best_iteration}")
        print(f"  Valid NDCG@5:  {res.valid_ndcg_5:.4f}")
        print("  Top features:")
        for fi in (res.feature_importance_top or [])[:10]:
            print(f"    {fi['feature']:<30} {int(fi['importance'])}")
        print("=" * 70)
        return res

    # ==================================================================
    # DAILY PIPELINE
    # ==================================================================
    def run_pipeline(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = self.tdx.get_last_trade_date(self.etf_list)
        if not trade_date:
            LOG.error("no trade date available")
            return pd.DataFrame()
        t0 = time.time()
        dt = datetime.strptime(trade_date, "%Y%m%d")
        start = (dt - timedelta(days=int(self.lookback * 1.6))).strftime("%Y%m%d")
        print("=" * 70)
        print(f"  ETF Alpha Ranking Pipeline  date={trade_date}")
        print("=" * 70)

        # Step 1: Update TDX (no-op if files current; persist to CSV cache)
        print("[Step 1] Update TDX data")
        self.tdx.update_daily_cache(trade_date, self.etf_list, start)

        # Step 2: Update low-frequency Tushare reference data
        print("[Step 2] Update Tushare reference (weekly/quarterly)")
        self.ref.update_etf_basic(self.etf_list)

        # Load market data
        etf_data, bench_df, theme_stocks, stock_data = self.load_market_data(start, trade_date)
        if not etf_data:
            print("  [ERROR] no ETF data")
            return pd.DataFrame()
        bench_close = (bench_df["close"].values.astype(float)
                       if not bench_df.empty else np.array([]))

        # Steps 3-5: compute engines + features
        print("[Step 3] Calculate theme persistence")
        print("[Step 4] Calculate leader persistence")
        print("[Step 5] Generate ETF features")
        market_r, theme_r, leader_r, breadth_r = self.compute_engines(
            trade_date, etf_data, bench_df, theme_stocks, stock_data)
        rows = self.snapshot_features(trade_date, etf_data, bench_df,
                                      market_r, theme_r, leader_r, breadth_r)
        if not rows:
            print("  [ERROR] no features computed")
            return pd.DataFrame()
        feat_df = pd.DataFrame(rows)
        feat_df = self.feat_engine.add_cross_sectional(
            feat_df, ["alpha20", "alpha40", "alpha60", "ret_20d", "ret_60d"])

        # Step 6: Load model
        print("[Step 6] Load LightGBM model")
        model_ok = False
        if self.trainer.ranker.load():
            qcheck = self.trainer.ranker.check_quality(feat_df)
            if qcheck["ok"]:
                model_ok = True
            else:
                print(f"  [WARN] model quality check failed: {qcheck['reason']}")
                print("  [WARN] falling back to multi-factor rule-based score")
        if not model_ok:
            pred_df = self._rule_based_predict(feat_df)
        else:
            # Step 7: Predict ranking
            print("[Step 7] Predict ETF ranking")
            pred_df = self.trainer.ranker.predict(feat_df)

        # persist predictions + features
        for _, row in pred_df.iterrows():
            feats = {k: v for k, v in row.to_dict().items()
                     if k not in {"date", "etf", "rank", "prediction_score", "raw_score"}}
            self.db.upsert_etf_features(trade_date, str(row["etf"]), feats)
        self.db.upsert_predictions_batch(trade_date, [
            {"etf": str(r["etf"]), "rank": int(r["rank"]),
             "score": float(r.get("prediction_score", 0.0))}
            for _, r in pred_df.iterrows()])
        # persist theme features
        self.db.upsert_theme_features(trade_date, [
            self.theme_engine.to_dict(t) for t in theme_r.values()])

        # Portfolio decision: top-1 strategy -> rank-1 is BUY
        report_df = self.reporter.build_report_df(pred_df)
        signal_etf, signal_type = "", ""
        if not report_df.empty:
            top = report_df.iloc[0]
            if int(top["Rank"]) == self.portfolio.buy_rank:
                sig_type = "BUY"
            elif float(top["ThemePersistence"]) < self.portfolio.sell_theme:
                sig_type = "WATCH"
            else:
                sig_type = "WATCH"
            report_df.loc[report_df.index[0], "Signal"] = sig_type
            signal_etf, signal_type = str(top["ETF"]), sig_type

        # Step 8: Generate report
        print("[Step 8] Generate trading report")
        csv_path = self.reporter.to_csv(report_df, trade_date)
        self.reporter.print_summary(report_df, trade_date)
        md = self.reporter.to_markdown(report_df, trade_date,
                                       market_state=market_r.market_state,
                                       market_score=market_r.market_score,
                                       signal_etf=signal_etf, signal_type=signal_type)
        md_path = os.path.join(self.reporter.csv_dir, f"etf_ranking_{trade_date}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        elapsed = time.time() - t0
        print(f"\n  CSV: {csv_path}")
        print(f"  MD:  {md_path}")
        print(f"  Elapsed: {elapsed:.1f}s  (target <300s)")
        print("=" * 70)
        return pred_df

    def _rule_based_predict(self, feat_df: pd.DataFrame) -> pd.DataFrame:
        """Multi-factor composite score when model is unavailable or underfit.

        Weights: 28% Theme, 22% Leader, 25% Momentum, 15% Risk, 10% Market
        Each sub-score is mapped to [0,100] via tanh so the composite is
        always in a comparable range regardless of return sign.
        """
        df = feat_df.copy()

        def _score(row):
            tp = float(row.get("theme_persistence", 0))
            ls = float(row.get("leader_score", 0))
            ret20 = float(row.get("ret_20d", 0))
            ret60 = float(row.get("ret_60d", 0))
            alpha60 = float(row.get("alpha60", 0))
            sharpe60 = float(row.get("sharpe_60", 0))
            sortino60 = float(row.get("sortino_60", 0))
            rsi = float(row.get("rsi_14", 50))
            adx = float(row.get("adx_14", 20))
            breakout = float(row.get("breakout_dist", 0))
            vol20 = float(row.get("vol_20d", 0))
            max_dd60 = float(row.get("max_dd_60", 0))
            ms = float(row.get("market_score", 50))

            mom = 50.0 + 50.0 * np.tanh(ret20 * 3.0 + ret60 * 1.5 + alpha60 * 2.0)
            tech = (np.clip(50.0 + 50.0 * np.tanh(breakout * 5.0), 0, 100) * 0.35
                    + np.clip(adx, 0, 100) * 0.35
                    + np.clip(50.0 - abs(rsi - 55.0), 0, 100) * 0.30)
            risk = (np.clip(50.0 + 50.0 * np.tanh(sharpe60), 0, 100) * 0.35
                    + np.clip(50.0 + 50.0 * np.tanh(sortino60), 0, 100) * 0.35
                    + np.clip(100.0 - vol20 * 100, 0, 100) * 0.15
                    + np.clip(100.0 + max_dd60 * 100, 0, 100) * 0.15)
            return (0.28 * tp + 0.22 * ls + 0.25 * (0.6 * mom + 0.4 * tech)
                    + 0.15 * risk + 0.10 * ms)

        df["prediction_score"] = df.apply(_score, axis=1)
        df["rank"] = (df.groupby("date")["prediction_score"]
                      .rank(ascending=False, method="first").astype(int))
        return df

    # ==================================================================
    # BACKTEST
    # ==================================================================
    def run_backtest(self) -> BacktestMetrics:
        print("=" * 70)
        print("  Walk-Forward Backtest")
        print("=" * 70)
        # Build a predictions panel: predict at each historical sample date
        tcfg = self.config.get("training", {})
        bcfg = self.config.get("backtest", {})
        start = bcfg.get("start_date", tcfg.get("test_start", "20250101"))
        end = bcfg.get("end_date", "") or self.tdx.get_last_trade_date(self.etf_list)
        panel = self.build_panel(start_date=start, end_date=end, stride=bcfg.get("rebalance_freq", 5))
        if panel.empty:
            print("  [ERROR] empty panel")
            return BacktestMetrics()
        # Load model (with quality check)
        model_ok = False
        if self.trainer.ranker.load():
            qcheck = self.trainer.ranker.check_quality(panel)
            if qcheck["ok"]:
                model_ok = True
            else:
                print(f"  [WARN] model quality check failed: {qcheck['reason']}")
                print("  [WARN] falling back to multi-factor rule-based score")
        if model_ok:
            pred_panel = self.trainer.ranker.predict(panel)
        else:
            pred_panel = self._rule_based_predict(panel)
        # prices for trade execution
        lookback = max(self.lookback, 250)
        data_start = (datetime.strptime(start, "%Y%m%d") - timedelta(days=int(lookback * 1.6))).strftime("%Y%m%d")
        etf_data, bench_df, _, _ = self.load_market_data(data_start, end)
        metrics = self.backtester.run(pred_panel, etf_data, bench_df)
        d = self.backtester.to_dict(metrics)
        print("\n" + "-" * 50)
        for k, v in d.items():
            print(f"  {k:<22} {v}")
        print("-" * 50)
        # save
        rep = os.path.join(self.reporter.csv_dir, f"backtest_{start}_{end}.json")
        with open(rep, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print(f"  saved -> {rep}")
        return metrics

    # ==================================================================
    # EXPLAIN
    # ==================================================================
    def explain(self, trade_date: str = None, top_n: int = 5):
        if not self.trainer.ranker.load():
            print("no model loaded")
            return
        date = trade_date or self.tdx.get_last_trade_date(self.etf_list)
        # compute features on-the-fly (more reliable than DB cache)
        lookback = max(self.lookback, 250)
        data_start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=int(lookback * 1.6))).strftime("%Y%m%d")
        etf_data, bench_df, theme_stocks, stock_data = self.load_market_data(data_start, date)
        if not etf_data:
            print("no ETF data")
            return
        market_r, theme_r, leader_r, breadth_r = self.compute_engines(
            date, etf_data, bench_df, theme_stocks, stock_data)
        rows = self.snapshot_features(date, etf_data, bench_df, market_r,
                                      theme_r, leader_r, breadth_r)
        if not rows:
            print("no features computed")
            return
        df = pd.DataFrame(rows)
        # predict scores & ranks
        pred_df = self.trainer.ranker.predict(df)
        df = df.merge(pred_df[["date", "etf", "prediction_score", "rank"]],
                      on=["date", "etf"], how="left")
        ex = Explainer(self.trainer.ranker)
        imp = ex.global_importance(top_n=20)
        print("\nGlobal feature importance (top 20):")
        print(imp.to_string(index=False))
        # explain top-N ETFs
        if "rank" not in df.columns:
            df["rank"] = df["prediction_score"].rank(ascending=False).astype(int)
        top = df.sort_values("rank").iloc[:top_n]
        feat_cols = self.trainer.ranker.feature_names
        for _, row in top.iterrows():
            X = pd.DataFrame([{c: row.get(c, 0) for c in feat_cols}])
            contribs = ex.top_contributors(X, top_n=5)
            print(f"\n{row.get('etf')} (rank={int(row.get('rank', 0))}, "
                  f"score={row.get('prediction_score', 0):.1f}): top contributors")
            for c in contribs:
                print(f"  {c['feature']:<28} {c['contribution']:+.3f}")


def main():
    parser = argparse.ArgumentParser(description="ETF Alpha Ranking System")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "train", "backtest", "explain"])
    parser.add_argument("--config", default=None)
    parser.add_argument("--date", default=None, help="trade date YYYYMMDD")
    parser.add_argument("--stride", type=int, default=3, help="panel sampling stride")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    if args.version:
        print(f"ETF Alpha Ranking System v{__version__}")
        return
    engine = AlphaRankingEngine(args.config)
    if args.command == "train":
        engine.train(stride=args.stride)
    elif args.command == "backtest":
        engine.run_backtest()
    elif args.command == "explain":
        engine.explain(args.date)
    else:
        engine.run_pipeline(trade_date=args.date)


if __name__ == "__main__":
    main()
