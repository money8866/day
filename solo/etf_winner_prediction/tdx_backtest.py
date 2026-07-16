#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Winner Prediction - 通达信数据框架回测模块
================================================
基于通达信本地.day文件，进行LightGBM模型的Walk-Forward回测。

数据源: 通达信本地日线 .day 文件 (C:/new_tdx/vipdoc/sh/lday/shXXXXXX.day)
回测方法: Walk-Forward 滚动训练 + 预测 + 实际收益对比
评估指标: IC, Win Rate, Sharpe, MaxDD, 分组收益, 多空收益差

用法:
    python -m etf_winner_prediction.tdx_backtest --start 20250301 --end 20260714
    python -m etf_winner_prediction.tdx_backtest --start 20250301 --horizon 20
    python -m etf_winner_prediction.tdx_backtest --start 20250301 --eval-only
"""
import os
import sys
import struct
import json
import time
import argparse
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_winner_prediction.data_loader import load_config
from etf_winner_prediction.lightgbm_predictor import FeatureBuilder, TargetBuilder, BacktestResult

# ============================================================
# 通达信数据路径
# ============================================================
TDX_PATH = r"C:\new_tdx"


# ============================================================
# 通达信 .day 文件读取 (复用现有框架)
# ============================================================
def parse_tdx_day_file(filepath):
    """解析通达信 .day 文件 (32字节/条记录)"""
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_val = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            date_str = str(date_int)
            records.append({
                "trade_date": date_str,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_val / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0)
    return df


def ts_code_to_tdx_file(ts_code):
    """ts_code → 通达信 .day 文件路径"""
    sym, market = ts_code.split(".")
    if market == "SH":
        prefix = "sh"
        subdir = "sh"
    elif market == "SZ":
        prefix = "sz"
        subdir = "sz"
    else:
        return None
    return os.path.join(TDX_PATH, "vipdoc", subdir, "lday", f"{prefix}{sym}.day")


# ============================================================
# TDX ETF 数据加载器
# ============================================================
class TDXETFLoader:
    """从通达信本地加载ETF日线数据"""

    def __init__(self):
        self._cache = {}

    def load_etf_data(self, etf_codes: List[str],
                      start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        """批量加载ETF日线数据"""
        result = {}
        missing = 0
        for code in etf_codes:
            tdx_file = ts_code_to_tdx_file(code)
            if not tdx_file or not os.path.exists(tdx_file):
                missing += 1
                continue
            df = parse_tdx_day_file(tdx_file)
            if df is None or df.empty:
                missing += 1
                continue
            df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
            if len(df) >= 30:
                result[code] = df
        print(f"  TDX ETFs: {len(result)}/{len(etf_codes)} (missing {missing})")
        return result


# ============================================================
# Walk-Forward 回测引擎
# ============================================================
@dataclass
class TDXBacktestRecord:
    """单条回测记录"""
    trade_date: str = ""
    ts_code: str = ""
    pred_20d: float = 0.0
    pred_40d: float = 0.0
    pred_60d: float = 0.0
    actual_20d: float = 0.0
    actual_40d: float = 0.0
    actual_60d: float = 0.0
    # 特征值（用于分组分析）
    dist_ema20: float = 0.0
    ret_20d: float = 0.0
    vol_20d: float = 0.0
    sharpe_60: float = 0.0
    rsi_14: float = 0.0
    vol_ratio: float = 0.0


class TDXBacktestEngine:
    """通达信 Walk-Forward 回测引擎"""

    def __init__(self, config: dict):
        self.config = config
        self.horizons = config.get("expected_return", {}).get("horizons", [20, 40, 60])
        self.feature_builder = FeatureBuilder(config)
        self.target_builder = TargetBuilder(self.horizons)
        self.loader = TDXETFLoader()
        self.etf_codes = list(config.get("etf_universe", {}).keys())

    def run(self, start_date: str, end_date: str,
            train_window: int = 120, step: int = 10,
            top_k: int = 3) -> Tuple[pd.DataFrame, dict]:
        """
        Walk-Forward 回测

        Args:
            start_date: 回测起始日期
            end_date: 回测结束日期
            train_window: 每次训练使用的历史天数
            step: 步长（每N天重新训练一次）
            top_k: 每期选Top K ETF

        Returns:
            (records_df, eval_result)
        """
        try:
            import lightgbm as lgb
        except ImportError:
            print("[TDX Backtest] lightgbm not installed")
            return pd.DataFrame(), {}

        print("=" * 70)
        print("  ETF Winner Prediction - TDX Walk-Forward Backtest")
        print("=" * 70)
        print(f"  Period: {start_date} ~ {end_date}")
        print(f"  Horizons: {self.horizons}")
        print(f"  Train window: {train_window}d, Step: {step}d")
        print()

        # 1. 加载数据（含缓冲期）
        buffer_start = (datetime.strptime(start_date, "%Y%m%d") -
                        timedelta(days=train_window + 120)).strftime("%Y%m%d")
        buffer_end = (datetime.strptime(end_date, "%Y%m%d") +
                      timedelta(days=80)).strftime("%Y%m%d")
        print(f"[1/4] Loading TDX ETF data: {buffer_start} ~ {buffer_end}")
        t0 = time.time()
        etf_data = self.loader.load_etf_data(self.etf_codes, buffer_start, buffer_end)
        print(f"  Loaded {len(etf_data)} ETFs in {time.time()-t0:.1f}s")

        # 2. 获取交易日列表
        print("[2/4] Building trade date index...")
        sample_df = next(iter(etf_data.values()))
        all_dates = sorted(sample_df["trade_date"].unique())
        all_dates = [d for d in all_dates if start_date <= d <= end_date]
        if len(all_dates) < 60:
            print(f"  Not enough trade dates: {len(all_dates)}")
            return pd.DataFrame(), {}
        print(f"  Trade dates: {len(all_dates)}")

        # 3. 为每个ETF构建日期索引
        date_index = {}
        for code, df in etf_data.items():
            date_index[code] = {d: i for i, d in enumerate(df["trade_date"].tolist())}

        # 4. Walk-Forward 回测
        print("[3/4] Running walk-forward backtest...")
        records = []
        test_dates = all_dates[::step]
        # 确保至少3个测试点
        if len(test_dates) < 3:
            test_dates = all_dates
        test_dates = test_dates[3:]  # 前3个跳过（特征不足）

        n_tests = len(test_dates)
        for i, test_date in enumerate(test_dates):
            train_end = test_date
            pct = (i + 1) / n_tests * 100
            print(f"  [{i+1}/{n_tests}] {test_date} ({pct:.0f}%) "
                  f"records={len(records)}", end="\r")

            # 训练
            train_start = (datetime.strptime(train_end, "%Y%m%d") -
                           timedelta(days=train_window + 120)).strftime("%Y%m%d")
            models_20d, models_40d, models_60d = self._train_window(
                etf_data, train_start, train_end, date_index
            )

            if not models_20d:
                continue

            # 预测
            for code, df in etf_data.items():
                idx_map = date_index.get(code, {})
                pos = idx_map.get(test_date)

                if pos is None or pos < 30:
                    continue

                # 切到test_date的K线
                close = df["close"].values[:pos + 1].astype(float)
                high = df["high"].values[:pos + 1].astype(float)
                low = df["low"].values[:pos + 1].astype(float)
                vol = df["vol"].values[:pos + 1].astype(float)
                amount = df["amount"].values[:pos + 1].astype(float)
                pct = df["pct_chg"].values[:pos + 1].astype(float)

                # 构建特征
                feats = self.feature_builder.build(close, high, low, vol, amount, pct)
                if not feats:
                    continue

                # 预测
                pred_20d = self._predict(models_20d, feats, 0.0)
                pred_40d = self._predict(models_40d, feats, 0.0)
                pred_60d = self._predict(models_60d, feats, 0.0)

                # 实际未来收益
                actual = {}
                for h in self.horizons:
                    if pos + h < len(df):
                        actual[h] = float(df["close"].iloc[pos + h] / df["close"].iloc[pos] - 1)
                    else:
                        actual[h] = np.nan

                rec = TDXBacktestRecord(
                    trade_date=test_date,
                    ts_code=code,
                    pred_20d=pred_20d,
                    pred_40d=pred_40d,
                    pred_60d=pred_60d,
                    actual_20d=actual.get(20, np.nan),
                    actual_40d=actual.get(40, np.nan),
                    actual_60d=actual.get(60, np.nan),
                    dist_ema20=feats.get("dist_ema20", 0),
                    ret_20d=feats.get("ret_20d", 0),
                    vol_20d=feats.get("vol_20d", 0),
                    sharpe_60=feats.get("sharpe_60", 0),
                    rsi_14=feats.get("rsi_14", 0),
                    vol_ratio=feats.get("vol_ratio", 0),
                )
                records.append(rec)

        print(f"\n  Complete: {len(records)} records")

        # 5. 评估
        print("[4/4] Evaluating...")
        df_records = pd.DataFrame([r.__dict__ for r in records])
        eval_result = self._evaluate(df_records)

        return df_records, eval_result

    def _train_window(self, etf_data, train_start, train_end, date_index):
        """训练一个时间窗口的3个模型"""
        try:
            import lightgbm as lgb
        except ImportError:
            return None, None, None

        rows = []
        for code, df in etf_data.items():
            idx_map = date_index.get(code, {})
            dates = df["trade_date"].tolist()
            close_all = df["close"].values.astype(float)
            high_all = df["high"].values.astype(float)
            low_all = df["low"].values.astype(float)
            vol_all = df["vol"].values.astype(float)
            amt_all = df["amount"].values.astype(float)
            pct_all = df["pct_chg"].values.astype(float)

            valid_dates = [d for d in dates if train_start <= d <= train_end]
            for d in valid_dates:
                pos = idx_map.get(d)
                if pos is None or pos < 30 or pos + 60 >= len(dates):
                    continue
                c = close_all[:pos + 1]
                h = high_all[:pos + 1]
                l = low_all[:pos + 1]
                v = vol_all[:pos + 1]
                a = amt_all[:pos + 1]
                p = pct_all[:pos + 1]

                feats = self.feature_builder.build(c, h, l, v, a, p)
                if not feats:
                    continue
                targets = self.target_builder.build(close_all[:pos + 61])
                if any(np.isnan(targets.get(f"fwd_{h}d", np.nan)) for h in self.horizons):
                    continue

                row = {"ts_code": code, "trade_date": d}
                row.update(feats)
                row.update(targets)
                rows.append(row)

        if len(rows) < 100:
            return None, None, None

        train_df = pd.DataFrame(rows)
        exclude = ["ts_code", "trade_date"] + [f"fwd_{h}d" for h in self.horizons]
        feat_cols = [c for c in train_df.columns if c not in exclude]
        X = train_df[feat_cols].fillna(0).values.astype(np.float32)
        self.feature_names = feat_cols

        models = {}
        for h in self.horizons:
            y = train_df[f"fwd_{h}d"].fillna(0).values.astype(np.float32)
            model = lgb.LGBMRegressor(
                n_estimators=100, max_depth=5, num_leaves=31,
                learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                reg_alpha=0.1, reg_lambda=0.1, min_child_samples=20,
                random_state=42, n_jobs=-1, verbose=-1, force_col_wise=True,
            )
            model.fit(X, y)
            models[h] = model

        return models.get(20), models.get(40), models.get(60)

    def _predict(self, model, feats, default=0.0) -> float:
        if model is None or not hasattr(self, "feature_names"):
            return default
        try:
            X = np.array([feats.get(f, 0.0) for f in self.feature_names]).reshape(1, -1).astype(np.float32)
            return float(model.predict(X)[0])
        except Exception:
            return default

    def _evaluate(self, df: pd.DataFrame) -> dict:
        """综合评估回测结果"""
        if df.empty:
            return {}

        result = {"n_records": len(df), "n_dates": df["trade_date"].nunique()}
        result["date_range"] = f"{df['trade_date'].min()}~{df['trade_date'].max()}"

        for h in self.horizons:
            sub = df.dropna(subset=[f"actual_{h}d", f"pred_{h}d"])
            if len(sub) < 20:
                continue

            pred_col = f"pred_{h}d"
            actual_col = f"actual_{h}d"
            key = f"{h}d"

            # IC
            ic = sub[pred_col].corr(sub[actual_col], method="spearman")
            # Rank IC
            rank_ic = sub[pred_col].rank().corr(sub[actual_col].rank(), method="spearman")

            # 分组回测：按预测值分5组
            sub["group"] = pd.qcut(sub[pred_col], q=5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
            groups = sub.groupby("group", observed=True).agg(
                n=(actual_col, "count"),
                avg_pred=(pred_col, "mean"),
                avg_actual=(actual_col, "mean"),
                win_rate=(actual_col, lambda x: (x > 0).mean()),
            ).reset_index()

            # Top-K 模拟
            top_k_returns = []
            for date in sub["trade_date"].unique():
                date_sub = sub[sub["trade_date"] == date]
                if len(date_sub) < 3:
                    continue
                top3 = date_sub.nlargest(3, pred_col)
                top_k_returns.append(top3[actual_col].mean())

            top_k_rets = np.array(top_k_returns) if top_k_returns else np.array([0])
            # 每期独立持有，不跨期复利；用简单累加
            top_k_total = float(np.sum(top_k_rets))
            top_k_win = float(np.mean(top_k_rets > 0))
            top_k_sharpe = float(np.mean(top_k_rets) / max(np.std(top_k_rets), 1e-6) * np.sqrt(252 / h))
            # 最大回撤
            equity = np.cumprod(1 + top_k_rets)
            running_max = np.maximum.accumulate(equity)
            top_k_maxdd = float(np.max((running_max - equity) / np.maximum(running_max, 1e-6)))

            result[key] = {
                "n_samples": len(sub),
                "ic": round(ic, 4),
                "rank_ic": round(rank_ic, 4),
                "top3_total_return": round(top_k_total * 100, 2),
                "top3_win_rate": round(top_k_win * 100, 1),
                "top3_sharpe": round(top_k_sharpe, 2),
                "top3_maxdd": round(top_k_maxdd * 100, 2),
                "groups": groups.to_dict("records"),
                "avg_actual": round(float(sub[actual_col].mean()) * 100, 2),
                "avg_pred": round(float(sub[pred_col].mean()) * 100, 2),
            }

        return result


# ============================================================
# 评估报告生成
# ============================================================
def format_eval_report(eval_result: dict) -> str:
    """格式化回测评估报告"""
    if not eval_result:
        return "无评估数据"

    lines = []
    lines.append("=" * 70)
    lines.append("  ETF Winner Prediction - TDX Walk-Forward 回测报告")
    lines.append("=" * 70)
    lines.append(f"  回测范围: {eval_result.get('date_range', 'N/A')}")
    lines.append(f"  总记录数: {eval_result.get('n_records', 0)}")
    lines.append(f"  覆盖交易日: {eval_result.get('n_dates', 0)}")
    lines.append("")

    for h_key in ["20d", "40d", "60d"]:
        if h_key not in eval_result:
            continue
        h = eval_result[h_key]
        lines.append(f"  ── {h_key} 预测评估 ──")
        lines.append(f"  样本数: {h['n_samples']}")
        lines.append(f"  IC (Spearman): {h['ic']}")
        lines.append(f"  Rank IC: {h['rank_ic']}")
        lines.append(f"  Top3 总收益: {h['top3_total_return']}%")
        lines.append(f"  Top3 胜率: {h['top3_win_rate']}%")
        lines.append(f"  Top3 年化Sharpe: {h['top3_sharpe']}")
        lines.append(f"  Top3 最大回撤: {h['top3_maxdd']}%")
        lines.append(f"  平均预测收益: {h['avg_pred']}%")
        lines.append(f"  平均实际收益: {h['avg_actual']}%")
        lines.append("")

        # 分组明细
        if "groups" in h:
            lines.append(f"  {'分组':<8} {'样本':>6} {'预测均值':>8} {'实际均值':>8} {'胜率':>8}")
            for g in h["groups"]:
                lines.append(f"  {g['group']:<8} {g['n']:>6} "
                              f"{g['avg_pred']*100:>7.1f}% {g['avg_actual']*100:>7.1f}% "
                              f"{g['win_rate']*100:>7.1f}%")
            lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="ETF Winner Prediction - TDX Backtest")
    parser.add_argument("--start", type=str, default="20250401", help="起始日期")
    parser.add_argument("--end", type=str, default="20260714", help="结束日期")
    parser.add_argument("--horizon", type=int, default=0, help="仅评估单个周期(0=全部)")
    parser.add_argument("--train-window", type=int, default=120, help="训练窗口天数")
    parser.add_argument("--step", type=int, default=10, help="重新训练步长")
    parser.add_argument("--top-k", type=int, default=3, help="每期选Top K ETF")
    parser.add_argument("--eval-only", type=str, default="", help="仅评估已有回测CSV文件")
    args = parser.parse_args()

    config_path = os.path.join(BASE_DIR, "config.yaml")
    config = load_config(config_path)

    if args.eval_only:
        # 仅评估模式
        df = pd.read_csv(args.eval_only)
        engine = TDXBacktestEngine(config)
        engine.horizons = [20, 40, 60]
        eval_result = engine._evaluate(df)
        print(format_eval_report(eval_result))
        return

    engine = TDXBacktestEngine(config)
    if args.horizon > 0:
        engine.horizons = [args.horizon]

    df, eval_result = engine.run(
        start_date=args.start,
        end_date=args.end,
        train_window=args.train_window,
        step=args.step,
        top_k=args.top_k,
    )

    if df.empty:
        print("No backtest records generated.")
        return

    # 保存
    output_dir = os.path.join(BASE_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, f"tdx_backtest_{args.start}_{args.end}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nBacktest saved: {csv_path}")

    eval_path = os.path.join(output_dir, f"tdx_backtest_eval_{args.start}_{args.end}.json")
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_result, f, ensure_ascii=False, indent=2, default=str)
    print(f"Evaluation saved: {eval_path}")

    # 打印报告
    print(format_eval_report(eval_result))


if __name__ == "__main__":
    main()