#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Winner Prediction Engine - 主程序编排器
=============================================
机构级 ETF 未来收益预测系统。
预测未来20~60天哪只行业ETF收益最高。

8-Step Pipeline:
  Step 1: Market Regime Filter
  Step 2: Theme Forecast Engine
  Step 3: Lifecycle Prediction
  Step 4: Leader Engine
  Step 5: ETF Trend Engine
  Step 6: Expected Return Model
  Step 7: Expected Rank Model
  Step 8: Risk Engine
  ==> Decision Engine (Hard Filters) ==> Final Output

用法:
  python -m etf_winner_prediction.main
  python -m etf_winner_prediction.main --date 20260714
"""
from __future__ import annotations

import os
import sys
import argparse
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_winner_prediction import __version__
from etf_winner_prediction.data_loader import DataLoader, load_config
from etf_winner_prediction.market_regime import MarketRegimeFilter, MarketRegimeResult
from etf_winner_prediction.theme_forecast import ThemeForecastEngine, ThemeForecastResult
from etf_winner_prediction.lifecycle import LifecyclePredictor, LifecycleResult
from etf_winner_prediction.leader_engine import LeaderEngine, LeaderResult
from etf_winner_prediction.etf_trend import ETFTrendEngine, ETFTrendResult
from etf_winner_prediction.expected_return import ExpectedReturnModel, ExpectedReturnResult
from etf_winner_prediction.expected_rank import ExpectedRankModel, ExpectedRankResult
from etf_winner_prediction.risk_engine import RiskEngine, RiskResult
from etf_winner_prediction.decision import DecisionEngine, DecisionResult
from etf_winner_prediction.reporter import Reporter
from etf_winner_prediction.lightgbm_predictor import LightGBMPredictor, LightGBMPrediction


@dataclass
class FinalWinnerResult:
    """最终结果（一行输出）"""
    # 排名
    predicted_rank: int = 0
    etf_code: str = ""
    etf_name: str = ""
    theme: str = ""
    # 市场
    market_state: str = ""
    market_score: float = 0.0
    # 主题预测
    theme_forecast_rank: int = 0
    theme_forecast_score: float = 0.0
    # 生命周期
    lifecycle_stage: str = ""
    remaining_trend_days: int = 0
    rotation_probability: float = 0.0
    # 龙头
    core_leader: str = ""
    leader_score: float = 0.0
    # ETF趋势
    etf_trend_score: float = 0.0
    # 预期收益
    expected_20d: float = 0.0
    expected_40d: float = 0.0
    expected_60d: float = 0.0
    expected_return: float = 0.0
    # 排名概率
    probability_top1: float = 0.0
    probability_top3: float = 0.0
    probability_top5: float = 0.0
    # 风险
    expected_holding_days: int = 0
    expected_max_drawdown: float = 0.0
    risk_score: float = 0.0
    suggested_position: float = 0.0
    stop_loss: float = 0.0
    # 决策
    decision: str = "REJECT"
    confidence: float = 0.0
    reasons: list = field(default_factory=list)


class ETFWinnerPredictionEngine:
    """ETF Winner Prediction 引擎主流程"""

    def __init__(self, config_path: str = None, use_lightgbm: bool = True):
        self.config_path = config_path or os.path.join(BASE_DIR, "config.yaml")
        self.config = load_config(self.config_path)
        self.dl = DataLoader(self.config)
        self.use_lightgbm = use_lightgbm

        # 各模块
        self.market_regime = MarketRegimeFilter(self.config)
        self.theme_forecast = ThemeForecastEngine(self.config)
        self.lifecycle = LifecyclePredictor(self.config)
        self.leader_engine = LeaderEngine(self.config)
        self.etf_trend = ETFTrendEngine(self.config)
        self.expected_return = ExpectedReturnModel(self.config)
        self.expected_rank = ExpectedRankModel(self.config)
        self.risk_engine = RiskEngine(self.config)
        self.decision = DecisionEngine(self.config)
        self.reporter = Reporter(self.config)

        # LightGBM 预测器
        self.lgbm_predictor: Optional[LightGBMPredictor] = None
        if use_lightgbm:
            try:
                self.lgbm_predictor = LightGBMPredictor(self.config)
                if self.lgbm_predictor.load_models():
                    print(f"[LightGBM] Models loaded: {list(self.lgbm_predictor.models.keys())}")
                else:
                    print("[LightGBM] No pre-trained models, will use rule-based engine")
                    self.lgbm_predictor = None
            except Exception as e:
                print(f"[LightGBM] Init failed: {e}, will use rule-based engine")
                self.lgbm_predictor = None

        # ETF主题映射
        self.etf_theme_map: Dict[str, str] = self.config.get("etf_universe", {})
        self.etf_list = list(self.etf_theme_map.keys())

        # 数据缓存
        self._etf_data: Dict[str, pd.DataFrame] = {}
        self._stock_data: Dict[str, pd.DataFrame] = {}
        self._constituents: Dict[str, List[str]] = {}
        self._benchmark: Optional[np.ndarray] = None
        self._daily_all: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def load_data(self, start_date: str, end_date: str):
        print("=" * 70)
        print("  ETF Winner Prediction Engine - Data Loading")
        print("=" * 70)

        print(f"[1] Loading ETF daily data ({len(self.etf_list)} ETFs)...")
        self._etf_data = self.dl.load_etf_data(self.etf_list, start_date, end_date)
        print(f"    ETFs loaded: {len(self._etf_data)}")

        # ---- 数据可用性检查：确保目标日期数据存在 ----
        self._check_data_availability(end_date)

        print("[2] Loading benchmark (CSI 300)...")
        bm_df = self.dl.load_index("000300.SH", start_date, end_date)
        if not bm_df.empty:
            self._benchmark = bm_df["close"].values.astype(float)

        print("[3] Loading theme-stock mapping...")
        universe = self.dl.load_theme_universe()
        print(f"    Themes: {len(universe)}")

        print("[4] Matching ETF constituents...")
        self._constituents = self._match_etf_to_stocks(universe)
        all_cons = list(set(c for cs in self._constituents.values() for c in cs))
        print(f"    Unique constituents: {len(all_cons)}")

        if all_cons:
            print("[5] Loading stock daily data...")
            self._stock_data = self.dl.load_etf_data(all_cons, start_date, end_date)
            print(f"    Stocks loaded: {len(self._stock_data)}")

        print("[6] Loading market daily (recent 5 days for breadth, base={})...".format(end_date))
        self._daily_all = self.dl.load_market_daily_recent(n_days=5, trade_date=end_date)
        if not self._daily_all.empty:
            print(f"    Market records: {len(self._daily_all)}")

        print("Data loading complete.\n")

    def _check_data_availability(self, target_date: str):
        """检查目标日期的ETF数据是否真实存在，若缺失则给出警告

        避免使用过期缓存数据导致的预测失真。
        """
        if not self._etf_data:
            print(f"    [警告] 未加载到任何ETF数据，无法验证目标日期 {target_date}")
            return

        available_dates = set()
        for code, df in self._etf_data.items():
            if "trade_date" in df.columns and not df.empty:
                available_dates.add(str(df["trade_date"].iloc[-1]))

        if not available_dates:
            print(f"    [警告] ETF数据为空，无法验证目标日期")
            return

        if target_date in available_dates:
            print(f"    [OK] 目标日期 {target_date} 数据存在 ({len(self._etf_data)} 只ETF)")
            return

        # 数据缺失
        most_recent = max(available_dates) if available_dates else "?"
        gap = ""
        try:
            from datetime import datetime
            d1 = datetime.strptime(most_recent, "%Y%m%d")
            d2 = datetime.strptime(target_date, "%Y%m%d")
            gap = f" (相差 {(d2 - d1).days} 天)"
        except Exception:
            pass

        print(f"    [警告] 目标日期 {target_date} 数据缺失！ETF最新日期为 {most_recent}{gap}")
        print(f"    [警告] 请先下载最新数据：python _download_{target_date}.py 或 fill_hist_data.py --date {target_date}")
        print(f"    [警告] 当前将使用 {most_recent} 的数据继续运行（结果可能不准）")

    def _auto_download_if_needed(self, trade_date: str, start_date: str):
        """盘后自动下载缺失数据（ETF + 指数 + 全市场股票）

        检测目标日期的ETF数据是否缺失，若缺失则自动通过Tushare下载。
        """
        import os
        import time as _time

        etf_sample = self.etf_list[:3] if len(self.etf_list) >= 3 else self.etf_list
        missing = False
        for code in etf_sample:
            fp = os.path.join(self.dl.daily_cache, f"{code}.csv")
            if not os.path.exists(fp):
                missing = True
                break
            try:
                df = pd.read_csv(fp)
                if "trade_date" not in df.columns:
                    missing = True
                    break
                df["trade_date"] = df["trade_date"].astype(str)
                if trade_date not in df["trade_date"].values:
                    missing = True
                    break
            except Exception:
                missing = True
                break

        if not missing:
            return

        print("=" * 70)
        print("  自动下载缺失数据")
        print("=" * 70)
        print(f"  目标日期 {trade_date} 数据缺失，正在通过Tushare自动下载...")

        try:
            from dotenv import load_dotenv
            import tushare as ts
            load_dotenv(r"d:/mystock/config/.env")
            token = os.getenv("TUSHARE_TOKEN", "")
            if not token or not token.strip():
                print("  [跳过] TUSHARE_TOKEN 未配置，无法自动下载")
                return
            pro = ts.pro_api(token)
        except Exception as e:
            print(f"  [跳过] Tushare初始化失败: {e}")
            return

        cache = self.dl.daily_cache
        ok_count = 0
        fail_count = 0

        # 1) ETF数据（fund_daily）
        print(f"  [1/3] 下载ETF数据 ({len(self.etf_list)}只)...")
        for code in self.etf_list:
            try:
                fp = os.path.join(cache, f"{code}.csv")
                df_old = None
                if os.path.exists(fp):
                    try:
                        df_old = pd.read_csv(fp)
                        df_old["trade_date"] = df_old["trade_date"].astype(str)
                    except Exception:
                        pass
                df_new = pro.fund_daily(ts_code=code, start_date=start_date, end_date=trade_date)
                if df_new is not None and not df_new.empty:
                    df_new["trade_date"] = df_new["trade_date"].astype(str)
                    if df_old is not None and not df_old.empty:
                        combined = pd.concat([df_old, df_new], ignore_index=True)
                        combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
                        combined = combined.sort_values("trade_date").reset_index(drop=True)
                    else:
                        combined = df_new.sort_values("trade_date").reset_index(drop=True)
                    combined.to_csv(fp, index=False)
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1
            _time.sleep(0.12)
        print(f"    ETF: 成功={ok_count} 失败={fail_count}")

        # 2) 指数数据（index_daily）
        print(f"  [2/3] 下载指数数据...")
        idx_codes = ["000001.SH", "000300.SH", "399001.SZ", "399006.SZ", "000905.SH", "000852.SH"]
        idx_ok = 0
        for code in idx_codes:
            try:
                fp = os.path.join(cache, f"{code.replace('.', '_')}.csv")
                df_old = None
                if os.path.exists(fp):
                    try:
                        df_old = pd.read_csv(fp)
                        df_old["trade_date"] = df_old["trade_date"].astype(str)
                    except Exception:
                        pass
                df_new = pro.index_daily(ts_code=code, start_date=start_date, end_date=trade_date)
                if df_new is not None and not df_new.empty:
                    df_new["trade_date"] = df_new["trade_date"].astype(str)
                    if df_old is not None and not df_old.empty:
                        combined = pd.concat([df_old, df_new], ignore_index=True)
                        combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
                        combined = combined.sort_values("trade_date").reset_index(drop=True)
                    else:
                        combined = df_new.sort_values("trade_date").reset_index(drop=True)
                    combined.to_csv(fp, index=False)
                    idx_ok += 1
            except Exception:
                pass
            _time.sleep(0.12)
        print(f"    指数: {idx_ok}/{len(idx_codes)}")

        # 3) 全市场股票（批量daily）
        print(f"  [3/3] 下载全市场股票数据...")
        try:
            df_stocks = pro.stock_basic(exchange='', list_status='L',
                                        fields='ts_code,symbol')
            if df_stocks is not None and not df_stocks.empty:
                all_codes = []
                for _, row in df_stocks.iterrows():
                    sym = str(row['symbol']).strip()
                    code = str(row['ts_code']).strip()
                    if sym.startswith(('600','601','603','605','000','001','002','003',
                                       '300','301','688','689','4','8','9')) and '.' in code:
                        all_codes.append(code)
                # 去重
                seen = set()
                all_codes = [c for c in all_codes if not (c in seen or seen.add(c))]
                print(f"    全市场股票: {len(all_codes)}只, 批量下载中...")
                stk_ok = 0
                batch_size = 80
                for i in range(0, len(all_codes), batch_size):
                    batch = all_codes[i:i + batch_size]
                    try:
                        # V2: 优先 daily_cache 表（按只读取，未命中再批量API）
                        df_batch = None
                        try:
                            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                            _cached_parts = []
                            _missing = []
                            for _code in batch:
                                _, _max_date = get_daily_cache_range(_code)
                                if _max_date is not None and str(_max_date) >= str(trade_date):
                                    _c = get_daily_cache(_code, start_date, trade_date)
                                    if _c is not None and not _c.empty:
                                        _cached_parts.append(_c)
                                    else:
                                        _missing.append(_code)
                                else:
                                    _missing.append(_code)
                            if _missing:
                                _b = pro.daily(ts_code=",".join(_missing), start_date=start_date, end_date=trade_date)
                                if _b is not None and not _b.empty:
                                    try:
                                        batch_insert_daily_cache(_b)
                                    except Exception:
                                        pass
                                    _cached_parts.append(_b)
                            if _cached_parts:
                                df_batch = pd.concat(_cached_parts, ignore_index=True)
                        except Exception:
                            pass
                        if df_batch is None or df_batch.empty:
                            df_batch = pro.daily(ts_code=",".join(batch),
                                                start_date=start_date, end_date=trade_date)
                            if df_batch is not None and not df_batch.empty:
                                try:
                                    from stock_cache import batch_insert_daily_cache
                                    batch_insert_daily_cache(df_batch)
                                except Exception:
                                    pass
                        if df_batch is not None and not df_batch.empty:
                            df_batch["trade_date"] = df_batch["trade_date"].astype(str)
                            for code, grp in df_batch.groupby("ts_code"):
                                fp = os.path.join(cache, f"{code}.csv")
                                df_old = None
                                if os.path.exists(fp):
                                    try:
                                        df_old = pd.read_csv(fp)
                                        df_old["trade_date"] = df_old["trade_date"].astype(str)
                                    except Exception:
                                        pass
                                if df_old is not None and not df_old.empty:
                                    combined = pd.concat([df_old, grp], ignore_index=True)
                                    combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
                                    combined = combined.sort_values("trade_date").reset_index(drop=True)
                                else:
                                    combined = grp.sort_values("trade_date").reset_index(drop=True)
                                combined.to_csv(fp, index=False)
                                stk_ok += 1
                    except Exception:
                        pass
                    _time.sleep(0.3)
                    if (i + batch_size) % 400 == 0:
                        print(f"      进度: {min(i + batch_size, len(all_codes))}/{len(all_codes)}")
                print(f"    股票: 成功={stk_ok}")
        except Exception as e:
            print(f"    股票下载失败: {e}")

        print("  自动下载完成。\n")

    def _match_etf_to_stocks(self, universe: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """将ETF匹配到主题成份股"""
        result = {}
        for etf_code, theme_name in self.etf_theme_map.items():
            if theme_name in universe:
                result[etf_code] = universe[theme_name]
                continue
            matched = []
            for tname, codes in universe.items():
                if theme_name in tname or tname in theme_name:
                    matched.extend(codes)
            if matched:
                result[etf_code] = list(set(matched))[:50]
        return result

    def _match_theme_name(self, etf_theme: str, theme_keys: set) -> str:
        """将ETF主题名映射到universe中的主题名"""
        if etf_theme in theme_keys:
            return etf_theme
        for tname in theme_keys:
            if etf_theme in tname or tname in etf_theme:
                return tname
        return ""

    # ------------------------------------------------------------------
    # 运行流水线
    # ------------------------------------------------------------------
    def run_pipeline(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = self.dl.get_last_trade_date()

        dt = datetime.strptime(trade_date, "%Y%m%d")
        start_date = (dt - timedelta(days=400)).strftime("%Y%m%d")
        print(f"Analysis period: {start_date} ~ {trade_date}\n")

        # 自动下载缺失数据
        self._auto_download_if_needed(trade_date, start_date)

        # 加载数据
        self.load_data(start_date, trade_date)

        # 辅助数据
        print("=" * 70)
        print("  Loading auxiliary data")
        print("=" * 70)
        limit_df = self.dl.load_limit_list(trade_date)
        top_df = self.dl.load_top_list(trade_date)
        top_inst = self.dl.load_top_inst(trade_date)
        dc_hot = self.dl.load_dc_hot(trade_date)
        moneyflow = self.dl.load_moneyflow_by_date(trade_date)
        print(f"LimitUp: {len(limit_df)}, DragonTiger: {len(top_df)}, "
              f"Inst: {len(top_inst)}, DCHot: {len(dc_hot)}, "
              f"Moneyflow: {len(moneyflow)}\n")

        # ===== Step 1: Market Regime =====
        print("=" * 70)
        print("  Step 1: Market Regime Filter")
        print("=" * 70)
        index_df = self.dl.load_index("000300.SH", start_date, trade_date)
        market_result = self.market_regime.score(
            index_df=index_df, market_daily=self._daily_all,
            limit_df=limit_df, etf_data=self._etf_data,
            northbound_net=0.0, top_df=top_df, top_inst=top_inst,
        )
        print(f"  MarketScore: {market_result.market_score:.1f}")
        print(f"  State: {market_result.market_state} | Risk: {market_result.risk_level}")
        print(f"  Exposure: {market_result.recommended_exposure*100:.0f}%")
        print(f"  Reasons: {', '.join(market_result.reasons)}")
        if market_result.market_score < 50:
            print("  >>> WARNING: MarketScore < 50, NO BUY recommended <<<")
        elif market_result.market_score < 60:
            print("  >>> WARNING: MarketScore < 60, position capped at 30% <<<")
        print()

        # ===== Step 2: Theme Forecast =====
        print("=" * 70)
        print("  Step 2: Theme Forecast Engine")
        print("=" * 70)
        universe = self.dl.load_theme_universe()
        all_daily = pd.concat(
            [df for df in list(self._etf_data.values()) + list(self._stock_data.values())
             if df is not None and not df.empty], ignore_index=True
        ) if self._etf_data else pd.DataFrame()
        theme_results = self.theme_forecast.score(
            all_daily, universe, moneyflow, limit_df, dc_hot, top_df, top_inst
        )
        top_themes = sorted(theme_results.values(), key=lambda x: x.forecast_score, reverse=True)[:5]
        print(f"  Themes: {len(theme_results)}")
        for r in top_themes:
            print(f"    #{r.forecast_rank} {r.theme}: score={r.forecast_score:.1f} "
                  f"remain={r.remaining_trend_days}d Top3P={r.probability_top3:.0%}")
        print()

        # ===== Step 3: Lifecycle =====
        print("=" * 70)
        print("  Step 3: Lifecycle Prediction")
        print("=" * 70)
        lifecycle_results = self.lifecycle.score(all_daily, universe, moneyflow)
        stages = {}
        for r in lifecycle_results.values():
            stages[r.stage] = stages.get(r.stage, 0) + 1
        print(f"  Themes: {len(lifecycle_results)}")
        print(f"  Stage distribution: {stages}")
        for tname, r in list(lifecycle_results.items())[:5]:
            print(f"    {tname}: {r.stage} remain={r.remaining_trend_days}d "
                  f"next={r.next_stage}({r.next_stage_probability:.0%})")
        print()

        # ===== Step 5: ETF Trend (先做, 因为Step 4可能需要) =====
        print("=" * 70)
        print("  Step 5: ETF Trend Engine")
        print("=" * 70)
        etf_trend_results = self.etf_trend.score(self._etf_data, self._benchmark)
        print(f"  ETFs scored: {len(etf_trend_results)}")
        top5 = sorted(etf_trend_results.values(),
                      key=lambda x: x.etf_trend_score, reverse=True)[:5]
        for r in top5:
            print(f"    {r.etf_code}: Trend={r.etf_trend_score:.1f} "
                  f"RS={r.relative_strength:.1f} Mom={r.momentum:.1f}")
        print()

        # ===== Step 4: Leader Engine =====
        print("=" * 70)
        print("  Step 4: Leader Engine")
        print("=" * 70)
        leader_results = self.leader_engine.score(
            self._etf_data, self._constituents, self._stock_data,
            top_df, top_inst, moneyflow,
        )
        print(f"  ETFs: {len(leader_results)}")
        for code, r in list(leader_results.items())[:5]:
            print(f"    {code} [{self.etf_theme_map.get(code, '')}]: "
                  f"leader={r.core_leader} score={r.leader_score:.1f} "
                  f"health={r.leader_health:.1f}")
        print()

        # ===== Step 8: Risk Engine (提前到Step 6之前, 因为LightGBM需要risk_score) =====
        theme_keys = set(theme_results.keys())
        print("=" * 70)
        print("  Step 8: Risk Engine")
        print("=" * 70)
        theme_rotation = {t: r.rotation_probability / 100.0 for t, r in theme_results.items()}
        theme_remaining = {t: r.remaining_trend_days for t, r in theme_results.items()}
        leader_failure = {c: r.failure_risk for c, r in leader_results.items()}
        risk_results = self.risk_engine.score(
            self._etf_data, self._benchmark,
            theme_rotation_prob=theme_rotation,
            theme_remaining_days=theme_remaining,
            leader_failure_risk=leader_failure,
            etf_theme_map={code: self._match_theme_name(self.etf_theme_map.get(code, ""), theme_keys)
                           for code in self.etf_list},
        )
        print(f"  ETFs: {len(risk_results)}")
        for code, r in list(risk_results.items())[:5]:
            print(f"    {code}: risk={r.risk_score:.1f} pos={r.suggested_position*100:.0f}% "
                  f"stop={r.stop_loss*100:.1f}% dd={r.max_dd*100:.1f}%")
        print()

        # ===== Step 6: Expected Return (LightGBM or Rule-Based) =====
        print("=" * 70)
        print("  Step 6: Expected Return Model")
        if self.lgbm_predictor:
            print("    [Using LightGBM]")
        else:
            print("    [Using Rule-Based]")
        print("=" * 70)

        expected_return_results: Dict[str, ExpectedReturnResult] = {}
        lgbm_results: Dict[str, LightGBMPrediction] = {}

        if self.lgbm_predictor:
            # 构建引擎分数字典
            engine_scores = {}
            for code in self._etf_data.keys():
                etf_theme_name = self.etf_theme_map.get(code, "")
                matched_theme = self._match_theme_name(etf_theme_name, theme_keys)
                tr = theme_results.get(matched_theme)
                lr = lifecycle_results.get(matched_theme)
                ler = leader_results.get(code)
                etr = etf_trend_results.get(code)
                rr = risk_results.get(code) if code in risk_results else None
                engine_scores[code] = {
                    "market_score": market_result.market_score,
                    "theme_score": tr.forecast_score if tr else 50.0,
                    "theme_rank": tr.forecast_rank if tr else 99,
                    "lifecycle_signal": lr.stage_signal if lr else 50.0,
                    "remaining_days": lr.remaining_trend_days if lr else 20,
                    "leader_score": ler.leader_score if ler else 50.0,
                    "leader_health": ler.leader_health if ler else 50.0,
                    "etf_trend_score": etr.etf_trend_score if etr else 50.0,
                    "risk_score": rr.risk_score if rr else 50.0,
                    "rotation_prob": tr.rotation_probability if tr else 30.0,
                }
            lgbm_results = self.lgbm_predictor.predict_batch(self._etf_data, engine_scores)
            print(f"  LightGBM predicted: {len(lgbm_results)}")
            for code, r in sorted(lgbm_results.items(), key=lambda x: x[1].expected_return, reverse=True)[:5]:
                print(f"    #{r.predicted_rank} {code}: 20D={r.expected_20d*100:.1f}% "
                      f"40D={r.expected_40d*100:.1f}% 60D={r.expected_60d*100:.1f}% "
                      f"Return={r.expected_return*100:.1f}% Top3={r.probability_top3:.0%}%")
        else:
            for code, df in self._etf_data.items():
                etf_theme_name = self.etf_theme_map.get(code, "")
                matched_theme = self._match_theme_name(etf_theme_name, theme_keys)
                tr = theme_results.get(matched_theme)
                lr = lifecycle_results.get(matched_theme)
                expected_return_results[code] = self.expected_return.predict(
                    etf_code=code,
                    etf_df=df,
                    market_score=market_result.market_score,
                    theme_score=tr.forecast_score if tr else 50.0,
                    theme_persistence=tr.heat_persistence / 100.0 if tr else 0.5,
                    remaining_days=lr.remaining_trend_days if lr else 20,
                    leader_score=leader_results.get(code, LeaderResult()).leader_score,
                    etf_trend_score=etf_trend_results.get(code, ETFTrendResult()).etf_trend_score,
                    capital_flow=lr.capital_flow if lr else 0.0,
                    industry_growth=tr.industry_growth if tr else 50.0,
                )
            print(f"  Predicted: {len(expected_return_results)}")
            for code, r in list(expected_return_results.items())[:5]:
                print(f"    {code}: 20D={r.expected_20d*100:.1f}% 40D={r.expected_40d*100:.1f}% "
                      f"60D={r.expected_60d*100:.1f}% conf={r.return_confidence:.0f}%")
        print()

        # ===== Step 7: Expected Rank =====
        print("=" * 70)
        if self.lgbm_predictor and lgbm_results:
            print("  Step 7: Expected Rank Model [Using LightGBM]")
            print("=" * 70)
            print(f"  Ranked: {len(lgbm_results)}")
            for code, r in sorted(lgbm_results.items(), key=lambda x: x[1].predicted_rank)[:5]:
                print(f"    #{r.predicted_rank} {code}: Top1={r.probability_top1:.0%} "
                      f"Top3={r.probability_top3:.0%} conf={r.confidence:.0f}%")
        else:
            print("  Step 7: Expected Rank Model [Using Rule-Based]")
            print("=" * 70)
            expected_rank_results: Dict[str, ExpectedRankResult] = {}
            for code, df in self._etf_data.items():
                etf_theme_name = self.etf_theme_map.get(code, "")
                matched_theme = self._match_theme_name(etf_theme_name, theme_keys)
                tr = theme_results.get(matched_theme)
                lr = lifecycle_results.get(matched_theme)
                er = expected_return_results.get(code)
                rr = risk_results.get(code)
                etr = etf_trend_results.get(code)
                ler = leader_results.get(code)
                expected_rank_results[code] = self.expected_rank.predict(
                    etf_code=code,
                    etf_trend_score=etr.etf_trend_score if etr else 50.0,
                    theme_forecast_rank=tr.forecast_rank if tr else 99,
                    theme_forecast_score=tr.forecast_score if tr else 50.0,
                    leader_score=ler.leader_score if ler else 50.0,
                    market_score=market_result.market_score,
                    expected_return=er.expected_return if er else 0.0,
                    risk_score=rr.risk_score if rr else 50.0,
                    remaining_days=lr.remaining_trend_days if lr else 20,
                    rotation_prob=tr.rotation_probability / 100.0 if tr else 0.3,
                    etf_df=df,
                )
            print(f"  Ranked: {len(expected_rank_results)}")
            for code, r in sorted(expected_rank_results.items(),
                                  key=lambda x: x[1].predicted_rank)[:5]:
                print(f"    #{r.predicted_rank} {code}: Top1={r.probability_top1:.0%} "
                      f"Top3={r.probability_top3:.0%} hold={r.expected_holding_days}d")
        print()

        # ===== Decision Engine =====
        print("=" * 70)
        print("  Decision Engine (Hard Filters)")
        print("=" * 70)
        final_results: List[FinalWinnerResult] = []
        for code in self.etf_list:
            if code not in self._etf_data:
                continue
            etf_theme_name = self.etf_theme_map.get(code, "")
            matched_theme = self._match_theme_name(etf_theme_name, theme_keys)
            tr = theme_results.get(matched_theme)
            lr = lifecycle_results.get(matched_theme)
            ler = leader_results.get(code)
            etr = etf_trend_results.get(code)
            rr = risk_results.get(code)

            if not all([tr, lr, ler, etr, rr]):
                continue

            # 获取预期收益和排名（LightGBM or Rule-Based）
            if self.lgbm_predictor and lgbm_results and code in lgbm_results:
                lgbm_r = lgbm_results[code]
                exp_ret = lgbm_r.expected_return
                exp_20d = lgbm_r.expected_20d
                exp_40d = lgbm_r.expected_40d
                exp_60d = lgbm_r.expected_60d
                pred_rank = lgbm_r.predicted_rank
                prob_top1 = lgbm_r.probability_top1
                prob_top3 = lgbm_r.probability_top3
                prob_top5 = lgbm_r.probability_top5
                hold_days = 40
                avg_dd = 0.10
                conf = lgbm_r.confidence
            else:
                err = expected_return_results.get(code)
                rank_r = expected_rank_results.get(code)
                if not err or not rank_r:
                    continue
                exp_ret = err.expected_return
                exp_20d = err.expected_20d
                exp_40d = err.expected_40d
                exp_60d = err.expected_60d
                pred_rank = rank_r.predicted_rank
                prob_top1 = rank_r.probability_top1
                prob_top3 = rank_r.probability_top3
                prob_top5 = rank_r.probability_top5
                hold_days = rank_r.expected_holding_days
                avg_dd = rank_r.expected_max_drawdown
                conf = rank_r.confidence

            # 硬过滤器
            dec = self.decision.evaluate(
                market_score=market_result.market_score,
                theme_forecast_rank=tr.forecast_rank,
                remaining_trend_days=lr.remaining_trend_days,
                leader_score=ler.leader_score,
                risk_score=rr.risk_score,
                expected_return=exp_ret,
                probability_top3=prob_top3,
            )

            # 拒绝生命周期阶段
            if lr.is_reject_stage:
                dec.accepted = False
                dec.reject_reasons.append(f"生命周期拒绝({lr.stage})")

            # 仓位调整
            pos = rr.suggested_position * market_result.recommended_exposure
            pos = float(np.clip(pos, 0, 1.0))

            fr = FinalWinnerResult(
                predicted_rank=pred_rank,
                etf_code=code,
                etf_name=code,
                theme=etf_theme_name,
                market_state=market_result.market_state,
                market_score=round(market_result.market_score, 1),
                theme_forecast_rank=tr.forecast_rank,
                theme_forecast_score=round(tr.forecast_score, 1),
                lifecycle_stage=lr.stage,
                remaining_trend_days=lr.remaining_trend_days,
                rotation_probability=round(tr.rotation_probability, 1),
                core_leader=ler.core_leader,
                leader_score=round(ler.leader_score, 1),
                etf_trend_score=round(etr.etf_trend_score, 1),
                expected_20d=round(exp_20d, 4),
                expected_40d=round(exp_40d, 4),
                expected_60d=round(exp_60d, 4),
                expected_return=round(exp_ret, 4),
                probability_top1=round(prob_top1, 4),
                probability_top3=round(prob_top3, 4),
                probability_top5=round(prob_top5, 4),
                expected_holding_days=hold_days,
                expected_max_drawdown=round(avg_dd, 4),
                risk_score=round(rr.risk_score, 1),
                suggested_position=round(pos, 2),
                stop_loss=round(rr.stop_loss, 4),
                decision="ACCEPT" if dec.accepted else "REJECT",
                confidence=round(conf, 1),
                reasons=(
                    [f"ACCEPT: {r}" for r in dec.passed_filters] if dec.accepted
                    else dec.reject_reasons
                ),
            )
            final_results.append(fr)

        accepted = [r for r in final_results if r.decision == "ACCEPT"]
        rejected = [r for r in final_results if r.decision == "REJECT"]
        print(f"  Accepted: {len(accepted)} | Rejected: {len(rejected)}")
        for r in accepted:
            print(f"    >>> ACCEPT: {r.etf_code} [{r.theme}] pred_rank=#{r.predicted_rank}")
        print()

        # ===== 排序 =====
        final_results.sort(key=lambda x: (
            0 if x.decision == "ACCEPT" else 1,  # ACCEPT优先
            x.predicted_rank,
            -x.expected_return,
        ))
        for i, r in enumerate(final_results):
            r.predicted_rank = i + 1

        # ===== 输出 =====
        df = self.reporter.to_dataframe(final_results)
        json_path = self.reporter.to_json(final_results, trade_date)
        csv_path = self.reporter.to_csv(df, trade_date)
        md_path = self.reporter.to_markdown(final_results, trade_date)

        print("=" * 70)
        print("  Final Output")
        print("=" * 70)
        self.reporter.print_summary(df)
        print(f"\n  JSON: {json_path}")
        print(f"  CSV:  {csv_path}")
        print(f"  MD:   {md_path}")
        print("=" * 70)

        return df


def main():
    parser = argparse.ArgumentParser(description="ETF Winner Prediction Engine")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--date", default=None, help="Trade date (YYYYMMDD)")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    if args.version:
        print(f"ETF Winner Prediction Engine v{__version__}")
        return

    engine = ETFWinnerPredictionEngine(args.config)
    df = engine.run_pipeline(trade_date=args.date)
    return df


if __name__ == "__main__":
    main()