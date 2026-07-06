"""回测引擎 — 支持 Walk Forward、Rolling Window、Grid Search、Bayesian Optimization。"""

from __future__ import annotations

import itertools
import json
import os
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger
from scipy.optimize import minimize
from scipy.special import erf

from mainline_engine.backtest.metrics import (
    BacktestMetrics,
    compute_metrics,
    compute_max_drawdown,
    compute_sharpe,
)

warnings.filterwarnings("ignore", category=RuntimeWarning)

_EPS = 1e-10
_SLIPPAGE = 0.001  # 0.1% 滑点


@dataclass
class BacktestConfig:
    """回测配置参数。"""
    walk_forward_window: int = 252
    rolling_window: int = 60
    retrain_frequency: int = 20
    initial_capital: float = 1_000_000.0
    commission_pct: float = 0.0003
    max_positions: int = 10
    stop_loss_atr: float = 2.0
    take_profit_atr: float = 6.0


class BacktestEngine:
    """全功能回测引擎。

    支持:
    - Walk Forward Analysis（滚动训练测试）
    - Rolling Window Validation（固定窗宽滚动验证）
    - Grid Search（网格搜索并行优化）
    - Bayesian Optimization（高斯过程贝叶斯优化）
    - 向量化交易模拟（ATR止损止盈、T+1执行、滑点与佣金）
    """

    def __init__(self, config: dict):
        cfg_sec = config.get('backtest', {})
        self.cfg = BacktestConfig(
            walk_forward_window=cfg_sec.get('walk_forward_window', 252),
            rolling_window=cfg_sec.get('rolling_window', 60),
            retrain_frequency=cfg_sec.get('retrain_frequency', 20),
            initial_capital=cfg_sec.get('initial_capital', 1_000_000.0),
            commission_pct=cfg_sec.get('commission_pct', 0.0003),
            max_positions=cfg_sec.get('max_positions', 10),
            stop_loss_atr=cfg_sec.get('stop_loss_atr', 2.0),
            take_profit_atr=cfg_sec.get('take_profit_atr', 6.0),
        )
        logger.info(f"BacktestEngine initialized with config: {self.cfg}")

    # ═══════════════════════════════════════════════════════════════
    # 1. Walk Forward Analysis
    # ═══════════════════════════════════════════════════════════════

    def run_walk_forward(self,
                         data: pd.DataFrame,
                         score_func: Callable,
                         window: Optional[int] = None,
                         retrain_freq: Optional[int] = None) -> Dict:
        """Walk Forward Analysis（滚动训练测试）。

        在 window 天数据上训练，在之后 retrain_freq 天上测试，
        不断向前滚动，避免前视偏差。

        Parameters
        ----------
        data : pd.DataFrame
            需含 'trade_date' 列，按日期升序排列。
        score_func : Callable
            score_func(train_data) -> model (可生成信号的训练结果)。
            score_func 应返回包含 'predict' 方法的对象，
            或直接返回 signals DataFrame（此时忽略 predict 步骤）。
        window : int, optional
            训练窗口长度（天），默认 self.cfg.walk_forward_window。
        retrain_freq : int, optional
            重训练频率（天），默认 self.cfg.retrain_frequency。

        Returns
        -------
        dict
            {
                'folds': [{'fold_id', 'train_range', 'test_range',
                           'metrics', 'trades', 'equity_curve'}, ...],
                'aggregate': {'metrics': BacktestMetrics, ...}
            }
        """
        window = window or self.cfg.walk_forward_window
        retrain_freq = retrain_freq or self.cfg.retrain_frequency

        data = data.sort_values('trade_date').reset_index(drop=True)
        all_dates = data['trade_date'].values
        n = len(data)

        if n < window + retrain_freq:
            logger.error(
                f"Insufficient data: {n} rows, need at least {window + retrain_freq}"
            )
            return {'folds': [], 'aggregate': {'metrics': BacktestMetrics()}}

        folds = []
        start = 0

        while start + window + retrain_freq <= n:
            train_end = start + window
            test_end = min(train_end + retrain_freq, n)

            train_data = data.iloc[start:train_end].copy()
            test_data = data.iloc[train_end:test_end].copy()

            if len(test_data) < 5:
                logger.warning(f"Test set too small ({len(test_data)}), skipping fold")
                start += retrain_freq
                continue

            try:
                result = score_func(train_data, test_data)
                fold_result = self._process_fold_result(
                    result, train_data, test_data, start, train_end, test_end
                )
                folds.append(fold_result)
            except Exception as exc:
                logger.error(f"Fold failed at train_end={train_end}: {exc}")
                import traceback
                logger.debug(traceback.format_exc())

            start += retrain_freq

        if not folds:
            logger.warning("No folds completed in walk forward")
            return {'folds': [], 'aggregate': {'metrics': BacktestMetrics()}}

        aggregate = self._aggregate_folds(folds)
        logger.info(
            f"Walk forward: {len(folds)} folds, "
            f"aggregate Sharpe={aggregate['metrics'].sharpe_ratio:.2f}"
        )
        return {'folds': folds, 'aggregate': aggregate}

    # ═══════════════════════════════════════════════════════════════
    # 2. Rolling Window Validation
    # ═══════════════════════════════════════════════════════════════

    def run_rolling_window(self,
                           data: pd.DataFrame,
                           score_func: Callable,
                           window: Optional[int] = None) -> Dict:
        """Rolling Window Validation（固定窗宽滚动验证）。

        每个窗口期在 window 天数据上训练，测试下一天（或下一小段）。
        实际每 retrain_frequency 天滚动一次。

        Parameters
        ----------
        data : pd.DataFrame
            需含 'trade_date' 列，按日期升序排列。
        score_func : Callable
            同 run_walk_forward。
        window : int, optional
            训练窗口长度，默认 self.cfg.rolling_window。

        Returns
        -------
        dict
            同 walk_forward 格式。
        """
        window = window or self.cfg.rolling_window
        retrain_freq = self.cfg.retrain_frequency

        data = data.sort_values('trade_date').reset_index(drop=True)
        n = len(data)

        if n < window + retrain_freq:
            logger.error(
                f"Insufficient data: {n} rows, need at least {window + retrain_freq}"
            )
            return {'folds': [], 'aggregate': {'metrics': BacktestMetrics()}}

        folds = []
        start = 0

        while start + window + retrain_freq <= n:
            train_end = start + window
            test_end = min(train_end + retrain_freq, n)

            train_data = data.iloc[start:train_end].copy()
            test_data = data.iloc[train_end:test_end].copy()

            if len(test_data) < 3:
                start += retrain_freq
                continue

            try:
                result = score_func(train_data, test_data)
                fold_result = self._process_fold_result(
                    result, train_data, test_data, start, train_end, test_end
                )
                folds.append(fold_result)
            except Exception as exc:
                logger.error(f"Rolling fold failed at train_end={train_end}: {exc}")
                import traceback
                logger.debug(traceback.format_exc())

            start += retrain_freq

        if not folds:
            return {'folds': [], 'aggregate': {'metrics': BacktestMetrics()}}

        aggregate = self._aggregate_folds(folds)
        logger.info(
            f"Rolling window: {len(folds)} folds, "
            f"aggregate Sharpe={aggregate['metrics'].sharpe_ratio:.2f}"
        )
        return {'folds': folds, 'aggregate': aggregate}

    # ═══════════════════════════════════════════════════════════════
    # 3. Core Backtest Execution
    # ═══════════════════════════════════════════════════════════════

    def run_backtest(self,
                     signals: pd.DataFrame,
                     prices: pd.DataFrame) -> Dict:
        """核心回测执行。

        根据买卖信号和价格数据执行回测。

        signals: DataFrame，需含列 [trade_date, ts_code, signal_type, atr_stop, target]
        prices:  DataFrame，需含列 [trade_date, ts_code, open, high, low, close]

        执行规则:
        - T+1 执行（今日信号，明日开盘成交）
        - 等权分配资金（最多 max_positions 个持仓）
        - ATR 移动止损（trailing stop）
        - ATR 止盈
        - 交易成本：佣金 + 滑点

        Returns
        -------
        dict
            {'metrics': BacktestMetrics, 'trades': list[dict], 'equity_curve': np.ndarray}
        """
        signals_df = signals.copy()
        prices_df = prices.copy()

        required_signal_cols = {'trade_date', 'ts_code', 'signal_type'}
        required_price_cols = {'trade_date', 'ts_code', 'open', 'high', 'low', 'close'}
        if not required_signal_cols.issubset(signals_df.columns):
            missing = required_signal_cols - set(signals_df.columns)
            logger.error(f"Signals missing columns: {missing}")
            return self._empty_result()
        if not required_price_cols.issubset(prices_df.columns):
            missing = required_price_cols - set(prices_df.columns)
            logger.error(f"Prices missing columns: {missing}")
            return self._empty_result()

        equity_curve, trades = self._simulate_trades(signals_df, prices_df)
        metrics = compute_metrics(
            equity_curve, trades,
            initial_capital=self.cfg.initial_capital,
        )
        return {
            'metrics': metrics,
            'trades': trades,
            'equity_curve': equity_curve,
        }

    # ═══════════════════════════════════════════════════════════════
    # 4. Grid Search
    # ═══════════════════════════════════════════════════════════════

    def grid_search(self,
                    data: pd.DataFrame,
                    score_func: Callable,
                    param_grid: Dict[str, List],
                    metric: str = 'sharpe_ratio',
                    maximize: bool = True,
                    n_jobs: int = -1) -> pd.DataFrame:
        """Grid Search 参数网格搜索。

        param_grid: {param_name: [values]}
        使用 ProcessPoolExecutor 并行评估所有参数组合。

        Returns
        -------
        pd.DataFrame
            所有结果按 metric 排序，包含参数列和指标列。
        """
        keys = list(param_grid.keys())
        value_combos = list(itertools.product(*[param_grid[k] for k in keys]))
        n_combos = len(value_combos)

        if n_combos == 0:
            logger.warning("Empty param_grid")
            return pd.DataFrame()

        logger.info(f"Grid search: {n_combos} combinations over {keys}")

        n_workers = n_jobs if n_jobs > 0 else min(os.cpu_count() or 4, n_combos)
        n_workers = min(n_workers, n_combos)

        if n_workers <= 1:
            results = []
            for vals in value_combos:
                params = dict(zip(keys, vals))
                res = self._eval_params(data, score_func, params, metric)
                results.append(res)
        else:
            results = []
            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                fut_to_params = {}
                for vals in value_combos:
                    params = dict(zip(keys, vals))
                    fut = executor.submit(
                        self._eval_params_static,
                        data, score_func, params, metric,
                        self.cfg,
                    )
                    fut_to_params[fut] = params
                for fut in as_completed(fut_to_params):
                    try:
                        results.append(fut.result())
                    except Exception as exc:
                        logger.error(f"Grid search subprocess failed: {exc}")

        df = pd.DataFrame(results)
        if not df.empty and metric in df.columns:
            ascending = not maximize
            df = df.sort_values(metric, ascending=ascending).reset_index(drop=True)
            df['rank'] = range(1, len(df) + 1)

        logger.info(
            f"Grid search completed: best {metric}={df.iloc[0][metric]:.4f}"
            if not df.empty else "Grid search completed: no results"
        )
        return df

    # ═══════════════════════════════════════════════════════════════
    # 5. Bayesian Optimization
    # ═══════════════════════════════════════════════════════════════

    def bayesian_optimize(self,
                          data: pd.DataFrame,
                          score_func: Callable,
                          param_bounds: Dict[str, Tuple[float, float]],
                          n_iter: int = 50,
                          n_initial: int = 10,
                          metric: str = 'sharpe_ratio',
                          maximize: bool = True) -> Dict:
        """Bayesian Optimization（高斯过程代理 + Expected Improvement）。

        使用自实现 RBF 核 GP 和 EI 采集函数。
        每次迭代用 scipy.minimize 优化 EI 选择下一个评估点。

        Parameters
        ----------
        data : pd.DataFrame
            回测数据。
        score_func : Callable
            评分函数。
        param_bounds : dict
            {param_name: (min, max)} 连续参数边界。
        n_iter : int
            总迭代次数（包括初始采样）。
        n_initial : int
            初始随机采样点数。
        metric : str
            优化目标指标名。
        maximize : bool
            是否最大化。

        Returns
        -------
        dict
            {'best_params': dict, 'best_score': float,
             'history': list[dict], 'gp': GaussianProcess}
        """
        keys = list(param_bounds.keys())
        dim = len(keys)
        bounds = np.array([param_bounds[k] for k in keys], dtype=np.float64)

        # --- 初始随机采样 ---
        X = np.random.uniform(
            low=bounds[:, 0], high=bounds[:, 1],
            size=(min(n_initial, n_iter), dim),
        )
        y = []
        for i in range(len(X)):
            params = dict(zip(keys, X[i]))
            res = self._eval_params(data, score_func, params, metric)
            score = res.get(metric, 0.0)
            if not maximize:
                score = -score
            y.append(score)
            logger.info(f"BO init [{i + 1}/{len(X)}] {params}: {metric}={score:.4f}")

        X = np.array(X, dtype=np.float64)
        y = np.array(y, dtype=np.float64)

        # --- 贝叶斯迭代 ---
        gp = _GaussianProcess()
        gp.fit(X, y)

        for it in range(len(X), n_iter):
            best_idx = int(np.argmax(y))
            best_so_far = float(y[best_idx])

            def acquisition(x: np.ndarray) -> float:
                x = np.atleast_2d(x).astype(np.float64)
                mu, sigma = gp.predict(x)
                return -_expected_improvement(mu, sigma, best_so_far)

            x0 = X[best_idx] + np.random.normal(0, 0.05, size=dim)
            x0 = np.clip(x0, bounds[:, 0], bounds[:, 1])

            res = minimize(
                acquisition, x0, method='L-BFGS-B',
                bounds=bounds, options={'maxiter': 200, 'ftol': 1e-8},
            )
            x_next = np.atleast_1d(res.x).astype(np.float64)

            # 避免重复采样
            dists = np.linalg.norm(X - x_next, axis=1)
            if dists.min() < 1e-6:
                x_next = np.random.uniform(
                    low=bounds[:, 0], high=bounds[:, 1], size=dim,
                )

            params = dict(zip(keys, x_next))
            eval_res = self._eval_params(data, score_func, params, metric)
            score = eval_res.get(metric, 0.0)
            if not maximize:
                score = -score

            logger.info(
                f"BO iter [{it + 1}/{n_iter}] {params}: "
                f"{metric}={score:.4f}, best={best_so_far:.4f}"
            )

            X = np.vstack([X, x_next.reshape(1, -1)])
            y = np.append(y, score)
            gp.fit(X, y)

        # --- 结果 ---
        if maximize:
            best_idx = int(np.argmax(y))
            best_score = float(y[best_idx])
        else:
            best_idx = int(np.argmin(y))
            best_score = float(y[best_idx])

        best_params = dict(zip(keys, X[best_idx]))
        best_params = {k: float(v) for k, v in best_params.items()}

        history = []
        for i in range(len(X)):
            entry = dict(zip(keys, X[i]))
            entry[metric] = float(y[i]) if maximize else float(-y[i])
            history.append(entry)

        logger.info(
            f"Bayesian optimization done: best={best_params}, "
            f"{metric}={best_score:.4f}"
        )
        return {
            'best_params': best_params,
            'best_score': best_score,
            'history': history,
            'gp': gp,
        }

    # ═══════════════════════════════════════════════════════════════
    # 6. Core Trade Simulation（向量化交易模拟）
    # ═══════════════════════════════════════════════════════════════

    def _simulate_trades(self,
                         signals_df: pd.DataFrame,
                         prices_df: pd.DataFrame) -> Tuple[np.ndarray, List[dict]]:
        """向量化交易模拟。

        逐笔交易计算退出条件（ATR 移动止损 / ATR 止盈），
        然后通过日期索引聚合为权益曲线。

        Returns
        -------
        tuple[np.ndarray, list[dict]]
            (equity_curve, trades_list)
            equity_curve 每日 NAV（首日为初始资金）。
        """
        sig = signals_df.copy()
        prc = prices_df.copy()

        # 统一日期格式
        for df_ in (sig, prc):
            if 'trade_date' in df_.columns:
                df_['trade_date'] = pd.to_datetime(df_['trade_date'])

        # 合并信号与价格
        merged = pd.merge(
            sig, prc, on=['trade_date', 'ts_code'], how='left', suffixes=('', '_price'),
        )
        merged = merged.dropna(subset=['open', 'high', 'low', 'close']).copy()
        merged = merged.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

        if merged.empty:
            logger.warning("No valid signal-price pairs after merge")
            nav = np.full(1, self.cfg.initial_capital, dtype=np.float64)
            return nav, []

        # 确保 ATR stop/target 列存在
        if 'atr_stop' not in merged.columns:
            merged['atr_stop'] = self.cfg.stop_loss_atr
        if 'target' not in merged.columns:
            merged['target'] = self.cfg.take_profit_atr
        merged['atr_stop'] = pd.to_numeric(merged['atr_stop'], errors='coerce').fillna(
            self.cfg.stop_loss_atr
        )
        merged['target'] = pd.to_numeric(merged['target'], errors='coerce').fillna(
            self.cfg.take_profit_atr
        )

        # 获取全局日期序列
        all_dates_prc = prc['trade_date'].unique()
        all_dates_prc = np.sort(all_dates_prc)
        date_to_idx = {d: i for i, d in enumerate(all_dates_prc)}
        n_days = len(all_dates_prc)

        # 准备价格查找表: (ts_code, date) -> price row
        prc_lookup = prc.set_index(['ts_code', 'trade_date']).sort_index()

        # 按股票分组处理信号
        trades: List[dict] = []
        position_map: Dict[str, dict] = {}  # ts_code -> active trade info

        commission_rate = self.cfg.commission_pct
        slippage = _SLIPPAGE
        max_pos = self.cfg.max_positions

        # 按日期+股票遍历信号
        grouped = merged.groupby('trade_date')
        equity_values = np.full(n_days, np.nan, dtype=np.float64)
        cash = self.cfg.initial_capital
        positions_value_hist = np.zeros(n_days, dtype=np.float64)

        for day_idx, date in enumerate(all_dates_prc):
            if pd.isna(date):
                continue
            date_key = pd.Timestamp(date)

            # --- 检查现有持仓的退出条件 ---
            closed_trades = []
            for ts_code, pos in list(position_map.items()):
                try:
                    price_row = prc_lookup.loc[(ts_code, date_key)]
                except (KeyError, ValueError):
                    # 当天无数据，沿用前值
                    continue

                high = float(price_row['high'])
                low = float(price_row['low'])
                close_px = float(price_row['close'])

                atr_val = pos['atr_value']
                if atr_val <= _EPS:
                    atr_val = pos['entry_price'] * 0.02

                # 移动止损: 取 entry_stop 与 trailing_stop 的较大值
                trailing_stop = high - self.cfg.stop_loss_atr * atr_val
                pos['stop_price'] = max(pos['stop_price'], trailing_stop)

                # 止盈价
                tp_price = pos['entry_price'] + self.cfg.take_profit_atr * atr_val

                exit_price = 0.0
                exit_reason = ''

                if low <= pos['stop_price']:
                    exit_price = pos['stop_price'] * (1 - slippage)
                    exit_reason = 'stop_loss'
                elif high >= tp_price:
                    exit_price = tp_price * (1 - slippage)
                    exit_reason = 'take_profit'
                elif date_key >= pos['max_hold_date']:
                    exit_price = close_px * (1 - slippage)
                    exit_reason = 'max_hold'

                if exit_price > 0:
                    pos['exit_date'] = date_key
                    pos['exit_price'] = exit_price
                    pos['exit_reason'] = exit_reason
                    shares = pos['shares']
                    cost = shares * pos['entry_price']
                    proceeds = shares * exit_price
                    fee = (cost + proceeds) * commission_rate
                    pnl = proceeds - cost - fee
                    pos['pnl'] = pnl
                    pos['pnl_pct'] = pnl / max(cost, _EPS)
                    pos['fee'] = fee
                    cash += proceeds - fee
                    closed_trades.append(ts_code)

            for ts_code in closed_trades:
                t = position_map.pop(ts_code)
                t.pop('price_slice', None)
                trades.append(t)

            # --- 检查当天新信号 ---
            if day_idx < n_days - 1 and len(position_map) < max_pos:
                try:
                    day_signals = grouped.get_group(date_key)
                except KeyError:
                    day_signals = pd.DataFrame()

                if not day_signals.empty:
                    available_slots = max_pos - len(position_map)
                    day_signals = day_signals.head(available_slots)

                    for _, row in day_signals.iterrows():
                        ts_code = row['ts_code']
                        if ts_code in position_map:
                            continue

                        # T+1: 用下一日开盘价
                        next_date_idx = day_idx + 1
                        if next_date_idx >= n_days:
                            continue
                        next_date = all_dates_prc[next_date_idx]
                        next_date_key = pd.Timestamp(next_date)

                        try:
                            next_row = prc_lookup.loc[(ts_code, next_date_key)]
                        except (KeyError, ValueError):
                            continue

                        entry_price = float(next_row['open']) * (1 + slippage)
                        atr_value = float(row.get('atr_stop', self.cfg.stop_loss_atr))

                        shares = (cash / max_pos) / max(entry_price, _EPS)
                        shares = np.floor(shares / 100) * 100  # 整手
                        if shares < 100:
                            continue

                        fee = shares * entry_price * commission_rate
                        cash -= shares * entry_price + fee

                        stop_price = entry_price - self.cfg.stop_loss_atr * atr_value
                        hold_days = int(self.cfg.take_profit_atr * 5)
                        max_hold_date = next_date_key + pd.Timedelta(days=hold_days)

                        position_map[ts_code] = {
                            'ts_code': ts_code,
                            'entry_date': next_date_key,
                            'entry_price': entry_price,
                            'exit_date': None,
                            'exit_price': 0.0,
                            'exit_reason': '',
                            'shares': shares,
                            'atr_value': atr_value,
                            'stop_price': stop_price,
                            'max_hold_date': max_hold_date,
                            'direction': row.get('signal_type', 'long'),
                            'pnl': 0.0,
                            'pnl_pct': 0.0,
                            'fee': fee,
                        }

            # 计算当日持仓市值
            pos_value = 0.0
            for ts_code, pos in list(position_map.items()):
                try:
                    price_row = prc_lookup.loc[(ts_code, date_key)]
                    close_px = float(price_row['close'])
                except (KeyError, ValueError):
                    continue
                pos_value += pos['shares'] * close_px

            positions_value_hist[day_idx] = pos_value
            equity_values[day_idx] = cash + pos_value

        # 强制平仓所有剩余持仓（最后一天）
        final_date_key = pd.Timestamp(all_dates_prc[-1])
        for ts_code, pos in list(position_map.items()):
            try:
                price_row = prc_lookup.loc[(ts_code, final_date_key)]
                exit_price = float(price_row['close']) * (1 - slippage)
            except (KeyError, ValueError):
                exit_price = pos['entry_price'] * 0.9

            shares = pos['shares']
            cost = shares * pos['entry_price']
            proceeds = shares * exit_price
            fee = (cost + proceeds) * commission_rate
            pnl = proceeds - cost - fee
            pos['exit_date'] = final_date_key
            pos['exit_price'] = exit_price
            pos['exit_reason'] = 'force_close'
            pos['pnl'] = pnl
            pos['pnl_pct'] = pnl / max(cost, _EPS)
            pos['fee'] = pos.get('fee', 0.0) + fee
            trades.append(pos)

        # 填充 equity_values 中的 NaN
        valid_mask = np.isfinite(equity_values)
        if not valid_mask.any():
            nav = np.full(1, self.cfg.initial_capital, dtype=np.float64)
        elif not valid_mask.all():
            first_valid = int(np.where(valid_mask)[0][0])
            equity_values[:first_valid] = self.cfg.initial_capital
            equity_values = _ffill_nan(equity_values)
        else:
            equity_values = np.asarray(equity_values, dtype=np.float64)

        if len(equity_values) == 0:
            equity_values = np.array([self.cfg.initial_capital], dtype=np.float64)

        logger.info(
            f"Simulated {len(trades)} trades, "
            f"final NAV={equity_values[-1]:.2f}, "
            f"return={(equity_values[-1] / self.cfg.initial_capital - 1) * 100:.2f}%"
        )
        return equity_values, trades

    # ═══════════════════════════════════════════════════════════════
    # 内部辅助方法
    # ═══════════════════════════════════════════════════════════════

    def _process_fold_result(self,
                             result: Any,
                             train_data: pd.DataFrame,
                             test_data: pd.DataFrame,
                             start: int,
                             train_end: int,
                             test_end: int) -> Dict:
        """处理 fold 结果，统一返回格式。"""
        train_range = f"{train_data['trade_date'].iloc[0]} ~ {train_data['trade_date'].iloc[-1]}"
        test_range = f"{test_data['trade_date'].iloc[0]} ~ {test_data['trade_date'].iloc[-1]}"

        fold_id = f"fold_{start}_{train_end}_{test_end}"

        # 如果 result 是 dict 且有 signals，直接回测
        if isinstance(result, dict) and 'signals' in result:
            prices_for_test = self._get_prices_for_dates(
                test_data['trade_date'].values
            )
            bt_result = self.run_backtest(result['signals'], prices_for_test)
        elif isinstance(result, pd.DataFrame):
            prices_for_test = self._get_prices_for_dates(
                test_data['trade_date'].values
            )
            bt_result = self.run_backtest(result, prices_for_test)
        else:
            logger.warning(f"Fold {fold_id}: unexpected result type {type(result)}")
            return {
                'fold_id': fold_id,
                'train_range': train_range,
                'test_range': test_range,
                'metrics': BacktestMetrics(),
                'trades': [],
                'equity_curve': np.array([self.cfg.initial_capital]),
            }

        return {
            'fold_id': fold_id,
            'train_range': train_range,
            'test_range': test_range,
            'metrics': bt_result['metrics'],
            'trades': bt_result['trades'],
            'equity_curve': bt_result['equity_curve'],
        }

    def _get_prices_for_dates(self, dates: np.ndarray) -> pd.DataFrame:
        """预留：从全局数据源获取指定日期的价格数据。"""
        return pd.DataFrame(columns=['trade_date', 'ts_code', 'open', 'high', 'low', 'close'])

    def _aggregate_folds(self, folds: List[Dict]) -> Dict:
        """聚合多个 fold 的结果。"""
        all_trades = []
        all_curves = []
        for f in folds:
            all_trades.extend(f.get('trades', []))
            all_curves.append(f.get('equity_curve', np.array([self.cfg.initial_capital])))

        if all_curves:
            max_len = max(len(c) for c in all_curves)
            padded = []
            for c in all_curves:
                if len(c) < max_len:
                    c = np.pad(c, (0, max_len - len(c)), 'edge')
                padded.append(c)
            agg_curve = np.mean(padded, axis=0)
        else:
            agg_curve = np.array([self.cfg.initial_capital])

        agg_metrics = compute_metrics(
            agg_curve, all_trades,
            initial_capital=self.cfg.initial_capital,
        )
        return {
            'metrics': agg_metrics,
            'trades': all_trades,
            'equity_curve': agg_curve,
        }

    def _empty_result(self) -> Dict:
        metrics = BacktestMetrics()
        return {
            'metrics': metrics,
            'trades': [],
            'equity_curve': np.array([self.cfg.initial_capital]),
        }

    def _eval_params(self,
                     data: pd.DataFrame,
                     score_func: Callable,
                     params: Dict[str, Any],
                     metric: str) -> Dict:
        """评估一组参数，返回 {**params, **metrics}。"""
        try:
            bt = BacktestEngine({'backtest': {**self.cfg.__dict__, **params}})
            result = bt.run_walk_forward(data, score_func)
            agg = result.get('aggregate', {})
            m = agg.get('metrics', BacktestMetrics())
            row = {**params}
            for field in BacktestMetrics.__dataclass_fields__:
                row[field] = getattr(m, field)
            return row
        except Exception as exc:
            logger.error(f"Eval failed for {params}: {exc}")
            return {**params, metric: -999.0}

    @staticmethod
    def _eval_params_static(data, score_func, params, metric, cfg):
        """静态方法版本用于 ProcessPoolExecutor。"""
        engine = BacktestEngine({'backtest': {**cfg.__dict__, **params}})
        result = engine.run_walk_forward(data, score_func)
        agg = result.get('aggregate', {})
        m = agg.get('metrics', BacktestMetrics())
        row = {**params}
        for field in BacktestMetrics.__dataclass_fields__:
            row[field] = getattr(m, field)
        return row


# ═══════════════════════════════════════════════════════════════
# 高斯过程（RBF核 + 解析预测）
# ═══════════════════════════════════════════════════════════════

class _GaussianProcess:
    """简约高斯过程回归（RBF核），用于贝叶斯优化。"""

    def __init__(self, length_scale: float = 1.0, sigma_f: float = 1.0,
                 sigma_n: float = 1e-6):
        self.length_scale = length_scale
        self.sigma_f = sigma_f
        self.sigma_n = sigma_n
        self.X_train = None
        self.y_train = None
        self.K_inv = None

    def _rbf(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        sq_dist = (
            np.sum(a ** 2, axis=1, keepdims=True) +
            np.sum(b ** 2, axis=1, keepdims=True).T -
            2.0 * a @ b.T
        )
        sq_dist = np.maximum(sq_dist, 0.0)
        return self.sigma_f ** 2 * np.exp(-0.5 * sq_dist / (self.length_scale ** 2))

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.X_train = np.asarray(X, dtype=np.float64)
        self.y_train = np.asarray(y, dtype=np.float64).ravel()
        n = len(self.X_train)
        K = self._rbf(self.X_train, self.X_train) + self.sigma_n ** 2 * np.eye(n)
        try:
            self.K_inv = np.linalg.inv(K)
        except np.linalg.LinAlgError:
            self.K_inv = np.linalg.pinv(K)

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        X = np.asarray(X, dtype=np.float64).reshape(-1, self.X_train.shape[1])
        K_s = self._rbf(X, self.X_train)
        mu = K_s @ (self.K_inv @ self.y_train)
        K_ss = self._rbf(X, X) + self.sigma_n ** 2
        sigma2 = np.diag(K_ss) - np.sum(K_s @ self.K_inv * K_s, axis=1)
        sigma2 = np.maximum(sigma2, 1e-12)
        return mu.ravel(), np.sqrt(sigma2)


def _expected_improvement(mu: np.ndarray, sigma: np.ndarray,
                          best_so_far: float, xi: float = 0.01) -> np.ndarray:
    """Expected Improvement 采集函数。"""
    imp = mu - best_so_far - xi
    Z = imp / np.maximum(sigma, _EPS)
    ei = imp * _norm_cdf(Z) + sigma * _norm_pdf(Z)
    ei[sigma < _EPS] = 0.0
    return ei.ravel()


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x ** 2) / np.sqrt(2.0 * np.pi)


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + erf(x / np.sqrt(2.0)))


def _ffill_nan(arr: np.ndarray) -> np.ndarray:
    """前向填充 NaN。"""
    mask = np.isnan(arr)
    if mask.all():
        return np.full_like(arr, 0.0)
    idx = np.where(~mask, np.arange(len(arr)), 0)
    np.maximum.accumulate(idx, out=idx)
    return arr[idx]
