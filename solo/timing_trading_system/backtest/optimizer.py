#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
参数优化器
==========
支持 Walk-Forward 滚动验证和网格搜索两种优化模式，
用于寻找交易系统的最优参数组合。
"""
from __future__ import annotations

import copy
import itertools
import logging
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .engine import BacktestEngine
from .metrics import calc_metrics

LOG = logging.getLogger("timing_trading.backtest.optimizer")


class ParamOptimizer:
    """参数优化器 - Walk-Forward + 网格搜索

    使用方法:
        optimizer = ParamOptimizer(config)
        result = optimizer.walk_forward("20230101", "20231201", param_grid={...})
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.backtest_cfg = config.get("backtest", {})
        wf_cfg = self.backtest_cfg.get("walk_forward", {})

        self.train_window = wf_cfg.get("train_window", 120)   # 训练窗口（交易日数）
        self.test_window = wf_cfg.get("test_window", 20)      # 测试窗口（交易日数）
        self.step = wf_cfg.get("step", 10)                    # 滑动步长
        self.tdx_root = config.get("general", {}).get("tdx_root", "C:\\new_tdx")

    # ─────────────────────────────────────────────────────────────
    # Walk-Forward 滚动验证
    # ─────────────────────────────────────────────────────────────

    def walk_forward(
        self,
        start_date: str,
        end_date: str,
        param_grid: Optional[dict] = None,
    ) -> dict:
        """Walk-Forward 滚动验证

        将历史数据划分为多个滚动窗口，每个窗口：
        1. 使用训练窗口内数据做网格搜索寻找最优参数
        2. 用最优参数在测试窗口上回测验证
        3. 汇总所有测试窗口的结果

        参数:
            start_date: 起始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
            param_grid: 网格搜索参数空间，形如 {"param_name": [value1, value2, ...]}
                        若为 None，则从 config.backtest.grid_search.param_grid 读取

        返回:
            {
                "windows": [
                    {
                        "train_start": "...", "train_end": "...",
                        "test_start": "...", "test_end": "...",
                        "best_params": {...},
                        "test_metrics": {...}
                    },
                    ...
                ],
                "aggregated_metrics": {...},
                "stability": {
                    "param_consistency": 0.8,
                    "sharpe_std": 0.2,
                    ...
                }
            }
        """
        if param_grid is None:
            param_grid = self.backtest_cfg.get("grid_search", {}).get("param_grid", {})

        if not param_grid:
            return {"error": "param_grid 为空，无法执行 Walk-Forward"}

        # ── 加载交易日历 ──
        trade_dates = self._load_trade_dates(start_date, end_date)
        if not trade_dates:
            return {"error": f"无法加载 {start_date} ~ {end_date} 之间的交易日数据"}

        LOG.info("Walk-Forward 配置: train=%d天, test=%d天, step=%d天, 总交易日=%d",
                 self.train_window, self.test_window, self.step, len(trade_dates))

        # ── 生成窗口序列 ──
        windows = self._generate_windows(trade_dates)
        if not windows:
            return {"error": "无法生成有效的窗口序列，请检查日期范围是否足够覆盖 train + test 窗口"}

        LOG.info("Walk-Forward 窗口数: %d", len(windows))

        # ── 逐窗口执行 ──
        window_results = []
        all_test_metrics: List[dict] = []

        for w_idx, (train_dates, test_dates) in enumerate(windows):
            train_start = train_dates[0]
            train_end = train_dates[-1]
            test_start = test_dates[0]
            test_end = test_dates[-1]

            LOG.info("[窗口 %d/%d] 训练: %s~%s 测试: %s~%s",
                     w_idx + 1, len(windows),
                     train_start, train_end, test_start, test_end)

            # 1. 在训练窗口上网格搜索
            def _factory(params: dict) -> BacktestEngine:
                """根据参数字典创建回测引擎实例"""
                merged_config = self._merge_params(self.cfg, params)
                return BacktestEngine(merged_config)

            grid_result = self.grid_search(
                param_grid=param_grid,
                engine_factory=_factory,
                train_dates=(train_start, train_end),
            )

            if "error" in grid_result:
                LOG.warning("窗口 %d 网格搜索失败: %s", w_idx + 1, grid_result["error"])
                continue

            best_params = grid_result["best_params"]

            # 2. 在测试窗口上使用最优参数回测
            test_config = self._merge_params(self.cfg, best_params)
            test_engine = BacktestEngine(test_config)
            test_result = test_engine.run(test_start, test_end)

            if "error" in test_result:
                LOG.warning("窗口 %d 测试回测失败: %s", w_idx + 1, test_result.get("error", ""))
                test_metrics = {"error": test_result.get("error", "")}
            else:
                test_metrics = test_result.get("metrics", {})

            window_result = {
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "best_params": best_params,
                "test_metrics": test_metrics,
                "test_trades": len(test_result.get("trades", [])),
            }
            window_results.append(window_result)

            if "error" not in test_metrics:
                all_test_metrics.append(test_metrics)

        # ── 汇总结果 ──
        aggregated = self._aggregate_metrics(all_test_metrics)
        stability = self._calc_stability(window_results, param_grid)

        return {
            "windows": window_results,
            "aggregated_metrics": aggregated,
            "stability": stability,
        }

    # ─────────────────────────────────────────────────────────────
    # 网格搜索
    # ─────────────────────────────────────────────────────────────

    def grid_search(
        self,
        param_grid: dict,
        engine_factory: Callable,
        train_dates: Tuple[str, str],
    ) -> dict:
        """网格搜索最优参数组合

        遍历 param_grid 中所有参数组合，在训练期上运行回测，
        按夏普比排序找出最优参数。

        参数:
            param_grid: {"param_name": [value1, value2, ...]}
            engine_factory: 接收参数字典返回 BacktestEngine 实例的工厂函数
            train_dates: (start_date, end_date) 训练期范围

        返回:
            {
                "best_params": {...},
                "all_results": [
                    {"params": {...}, "metrics": {...}, "score": float},
                    ...
                ]
            }
        """
        if not param_grid:
            return {"error": "参数网格为空"}

        # ── 生成所有参数组合 ──
        param_keys = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(itertools.product(*param_values))

        LOG.info("网格搜索: %d 个参数, %d 种组合",
                 len(param_keys), len(combinations))

        all_results = []
        best_score = -float("inf")
        best_params = None

        train_start, train_end = train_dates

        for idx, combo in enumerate(combinations):
            params = dict(zip(param_keys, combo))

            if (idx + 1) % 10 == 0 or idx == 0:
                LOG.info("网格搜索进度: %d / %d  %s",
                         idx + 1, len(combinations), params)

            try:
                engine = engine_factory(params)
                result = engine.run(train_start, train_end)

                if "error" in result:
                    LOG.debug("组合 %s 失败: %s", params, result["error"])
                    continue

                metrics = result.get("metrics", {})
                if "error" in metrics:
                    continue

                # 评分指标：夏普比 + 年化收益 - 回撤惩罚
                sharpe = metrics.get("Sharpe", 0)
                annual_return = metrics.get("AnnualReturn", 0)
                max_dd = abs(metrics.get("MaxDrawdown", 0))

                # 综合评分 = sharpe * 10 + annual_return * 0.1 - max_dd * 0.1
                score = (
                    max(sharpe, 0) * 10.0
                    + max(annual_return, 0) * 0.1
                    - max_dd * 0.1
                )

                entry = {
                    "params": params,
                    "metrics": metrics,
                    "score": round(score, 4),
                }
                all_results.append(entry)

                if score > best_score:
                    best_score = score
                    best_params = params

            except Exception as e:
                LOG.debug("网格搜索组合 %s 异常: %s", params, e)
                continue

        if not all_results:
            return {"error": "所有参数组合均失败"}

        # 按评分降序排列
        all_results.sort(key=lambda x: x["score"], reverse=True)

        LOG.info("网格搜索完成, 有效组合: %d/%d, 最优: %s (score=%.4f)",
                 len(all_results), len(combinations), best_params, best_score)

        return {
            "best_params": best_params,
            "all_results": all_results,
        }

    # ─────────────────────────────────────────────────────────────
    # 内部辅助方法
    # ─────────────────────────────────────────────────────────────

    def _load_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """加载目标日期范围内的交易日列表"""
        from data import tdx_loader as tdx

        # 用大盘指数作为交易日参考
        index_codes = ["000001.SH", "399006.SZ"]
        all_dates: set = set()

        for code in index_codes:
            df = tdx.load_daily(code, self.tdx_root, start_date=start_date, end_date=end_date, min_records=1)
            if not df.empty:
                all_dates.update(df["trade_date"].tolist())

        return sorted(all_dates)

    def _generate_windows(
        self,
        trade_dates: List[str],
    ) -> List[Tuple[List[str], List[str]]]:
        """生成 Walk-Forward 的 (训练日期, 测试日期) 窗口序列

        每个窗口：
            - 训练集: 最近 train_window 个交易日
            - 测试集: 后续 test_window 个交易日
            - 步进: step 个交易日
        """
        windows = []
        n = len(trade_dates)
        min_required = self.train_window + self.test_window

        if n < min_required:
            LOG.warning("交易日不足: %d < %d (train %d + test %d)",
                        n, min_required, self.train_window, self.test_window)
            return windows

        # 从第 train_window 个日期开始，每次滑动 step 天
        for i in range(self.train_window, n - self.test_window + 1, self.step):
            train_end = i
            train_start = i - self.train_window

            train_slice = trade_dates[train_start:train_end]
            test_slice = trade_dates[train_end:train_end + self.test_window]

            if len(train_slice) >= self.train_window and len(test_slice) >= self.test_window // 2:
                windows.append((train_slice, test_slice))

        return windows

    def _merge_params(self, base_config: dict, params: dict) -> dict:
        """将优化参数合并到基础配置中

        将 param 中的扁平键（如 "entry_breakout_min_pct"）转换为嵌套结构
        并合并到 base_config 的深层副本中。
        """
        merged = copy.deepcopy(base_config)

        for key, value in params.items():
            # 支持的参数路径映射
            path_map = {
                "entry_breakout_min_pct": ["stock_timing", "entry", "breakout", "min_pct"],
                "entry_breakout_min_vol_ratio": ["stock_timing", "entry", "breakout", "min_vol_ratio"],
                "entry_retrace_max_deviation": ["stock_timing", "entry", "retrace_ma20", "max_deviation"],
                "entry_retrace_resonance_threshold": ["stock_timing", "entry", "retrace_ma20", "resonance_threshold"],
                "entry_wave2_min_prev_wave": ["stock_timing", "entry", "wave2", "min_prev_wave"],
                "entry_vcp_contraction_count": ["stock_timing", "entry", "vcp", "contraction_count"],
                "exit_stop_loss": ["stock_timing", "exit", "stop_loss"],
                "exit_trailing_stop": ["stock_timing", "exit", "trailing_stop"],
                "exit_ma_break": ["stock_timing", "exit", "ma_break"],
                "position_max_per_stock": ["position", "max_per_stock"],
                "position_score_70": ["position", "score_to_position", "70"],
                "risk_max_drawdown": ["risk", "max_drawdown"],
            }

            if key in path_map:
                path = path_map[key]
                target = merged
                for p in path[:-1]:
                    if p not in target:
                        target[p] = {}
                    target = target[p]
                target[path[-1]] = value
            else:
                # 用点分隔的路径尝试
                parts = key.split("_")
                if len(parts) >= 2:
                    target = merged
                    for p in parts:
                        if p not in target:
                            target[p] = {}
                        target = target[p]
                    target = value

        return merged

    def _aggregate_metrics(self, metrics_list: List[dict]) -> dict:
        """汇总多个测试窗口的绩效指标（取均值）"""
        if not metrics_list:
            return {}

        aggregated = {}
        numeric_keys = [
            "TotalReturn", "AnnualReturn", "AnnualVol", "Sharpe",
            "MaxDrawdown", "Calmar", "WinRate", "ProfitLossRatio",
            "AvgHoldDays", "TotalTrades", "MonthlyWinRate",
        ]

        for key in numeric_keys:
            values = [m.get(key, 0) for m in metrics_list if isinstance(m.get(key), (int, float))]
            if values:
                aggregated[key] = round(float(np.mean(values)), 2)
                aggregated[f"{key}_std"] = round(float(np.std(values, ddof=1)), 4)

        # 总交易次数汇总
        total_trades = sum(m.get("TotalTrades", 0) for m in metrics_list)
        aggregated["TotalTrades_Sum"] = int(total_trades)

        # 跨窗口平均值
        aggregated["WindowCount"] = len(metrics_list)

        return aggregated

    def _calc_stability(
        self,
        window_results: List[dict],
        param_grid: dict,
    ) -> dict:
        """评估参数稳定性

        计算：
        - param_consistency: 最优参数在窗口间的一致性比例
        - sharpe_std: 夏普比在窗口间的标准差
        - 参数值分布
        """
        if not window_results:
            return {}

        # 夏普比稳定性
        sharpe_values = []
        for w in window_results:
            tm = w.get("test_metrics", {})
            if "error" not in tm:
                s = tm.get("Sharpe", 0)
                if isinstance(s, (int, float)):
                    sharpe_values.append(s)

        sharpe_std = float(np.std(sharpe_values, ddof=1)) if len(sharpe_values) > 1 else 0.0
        sharpe_mean = float(np.mean(sharpe_values)) if sharpe_values else 0.0

        # 参数一致性：统计每个参数最常出现的值
        param_keys = list(param_grid.keys())
        param_consistency = {}

        for key in param_keys:
            values = []
            for w in window_results:
                bp = w.get("best_params", {})
                if key in bp:
                    values.append(bp[key])

            if values:
                # 最常出现的值
                value_counts = pd.Series(values).value_counts()
                most_common_val = value_counts.index[0]
                most_common_count = value_counts.iloc[0]
                ratio = most_common_count / len(values)
                param_consistency[key] = {
                    "most_common": most_common_val,
                    "ratio": round(ratio, 4),
                    "unique_count": len(value_counts),
                }

        # 整体一致率
        if param_consistency:
            overall_consistency = float(np.mean([v["ratio"] for v in param_consistency.values()]))
        else:
            overall_consistency = 0.0

        return {
            "param_consistency": round(overall_consistency, 4),
            "sharpe_mean": round(sharpe_mean, 4),
            "sharpe_std": round(sharpe_std, 4),
            "sharpe_stability": round(sharpe_mean / (sharpe_std + 1e-10), 4),
            "param_detail": param_consistency,
            "window_count": len(window_results),
            "valid_windows": len(sharpe_values),
        }
