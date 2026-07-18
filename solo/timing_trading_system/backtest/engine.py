#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
事件驱动回测引擎
================
在历史日线上模拟三层择时系统的完整交易流程：
  1. 加载股池、指数、主题数据
  2. 生成交易日历
  3. 每日遍历：检查出场 → 检查入场 → 执行交易 → 记录净值
  4. 汇总绩效指标
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger("timing_trading.backtest.engine")


# ─────────────────────────────────────────────────────────────────
# 单笔交易记录
# ─────────────────────────────────────────────────────────────────


@dataclass
class BacktestTrade:
    """单笔交易记录"""
    ts_code: str
    stock_name: str
    buy_date: str
    buy_price: float
    shares: float
    position_ratio: float       # 仓位比例
    composite_score: float      # 买入时综合评分
    primary_entry: str = ""     # 入场信号类型
    sell_date: str = ""
    sell_price: float = 0.0
    pnl_pct: float = 0.0        # 收益率%
    pnl_amount: float = 0.0     # 盈亏金额
    hold_days: int = 0
    exit_reason: str = ""       # 卖出原因
    # 运行时跟踪
    _highest_price: float = 0.0  # 持仓期间最高价（用于移动止损）


# ─────────────────────────────────────────────────────────────────
# 回测引擎
# ─────────────────────────────────────────────────────────────────


class BacktestEngine:
    """回测引擎

    事件驱动，逐日模拟三层择时交易信号。
    所有延迟导入放在方法内部，防止循环依赖。
    """

    def __init__(self, config: dict):
        self.root_cfg = config                      # 完整根配置
        self.cfg = config.get("backtest", {})       # 回测子配置
        self.initial_capital = self.cfg.get("initial_capital", 1_000_000)
        self.commission = self.cfg.get("commission_rate", 0.0003)
        self.slippage = self.cfg.get("slippage", 0.001)
        self.tdx_root = config.get("general", {}).get("tdx_root", "C:\\new_tdx")
        self.stock_pool_path = config.get("general", {}).get("stock_pool_path", "")
        self.theme_map_path = config.get("general", {}).get("theme_map_path", "")

        # 运行时
        self.capital = float(self.initial_capital)
        self.peak_capital = float(self.initial_capital)
        self.positions: Dict[str, BacktestTrade] = {}       # 当前持仓 {ts_code: trade}
        self.closed_trades: List[BacktestTrade] = []        # 已平仓交易
        self.equity_curve: List[dict] = []                  # 净值曲线
        self.trade_dates: List[str] = []                    # 交易日历

        # 外部引擎（运行时注入或延迟初始化）
        self.fusion_engine = None
        self.risk_manager = None

        # 日线数据缓存 {ts_code: DataFrame} —— run() 中填充
        self._daily_cache: Dict[str, pd.DataFrame] = {}

    # ─────────────────────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────────────────────

    def run(self, start_date: str, end_date: str = "") -> dict:
        """运行回测

        流程：
        1. 加载股池、指数、主题数据
        2. 生成交易日历（取所有TDX数据中的交易日并集）
        3. 遍历每个交易日：
           a. 检查现有持仓的出场信号（调用exit_signals.composite_exit_signal）
           b. 如果卖出，记录交易，更新资金
           c. 调用SignalFusionEngine.evaluate生成新的买入信号
           d. 计算仓位分配（position.calculate_positions）
           e. 执行买入，记录持仓
           f. 记录当日净值
        4. 计算绩效指标

        返回:
            {"trades": [...], "metrics": {...}, "equity_curve": [...], "config": {...}}
        """
        # ── 延迟导入（防止循环依赖） ──────────────────────────
        from data import pool_loader, tdx_loader as tdx
        from trading.signal import SignalFusionEngine
        from trading.position import calculate_positions
        from trading.risk import RiskManager
        from stock import exit_signals

        # ── 预热天数：回测正式开始之前需要积累足够数据计算指标 ──
        warmup_days = 120

        # =========================================================
        # 1. 准备工作：加载数据
        # =========================================================
        LOG.info("====== 回测启动 ======")
        LOG.info("初始资金: %.2f  佣金: %.4f  滑点: %.4f",
                 self.initial_capital, self.commission, self.slippage)
        LOG.info("日期范围: %s ~ %s", start_date, end_date or "最新")

        # 1a. 加载股池
        if not self.stock_pool_path:
            return {"error": "stock_pool_path 未配置"}
        pool_df = pool_loader.load_pool(self.stock_pool_path)
        if pool_df.empty:
            return {"error": f"股池为空: {self.stock_pool_path}"}

        stock_codes = pool_df["ts_code"].tolist()
        LOG.info("股池加载: %d 只股票", len(stock_codes))

        # 1b. 确定实际结束日期（取股池中最新的数据日期）
        effective_end = end_date
        if not effective_end:
            # 从所有股票数据中取最大trade_date
            end_candidates = []
            for code in stock_codes[:10]:  # 抽样前10只股票判断
                tmp = tdx.load_daily(code, self.tdx_root, min_records=1)
                if not tmp.empty:
                    end_candidates.append(str(tmp["trade_date"].iloc[-1]))
            if end_candidates:
                effective_end = max(end_candidates)
                LOG.info("自动确定结束日期: %s", effective_end)
            else:
                return {"error": "无法确定结束日期"}

        # 1c. 一次性缓存所有股池个股的日线数据（含完整技术指标）
        LOG.info("开始批量加载日线数据并计算技术指标...")
        loaded_count = 0
        for code in stock_codes:
            df = tdx.load_daily(
                code, self.tdx_root,
                start_date="",            # 加载全部历史数据以支持指标计算
                end_date=effective_end,
                min_records=warmup_days,  # 数据量不足以支撑指标的跳过
            )
            if df.empty:
                continue
            df = tdx.calc_all_indicators(df)
            self._daily_cache[code] = df
            loaded_count += 1

        LOG.info("日线数据缓存: %d / %d 只股票加载成功", loaded_count, len(stock_codes))
        if not self._daily_cache:
            return {"error": "无任何股票数据可加载"}

        # 1d. 加载主题映射 + 主题评分
        theme_map = {}
        if self.theme_map_path:
            theme_map = pool_loader.load_theme_map(self.theme_map_path)
        # 用股池数据做一次主题评分，打上best_theme_name/best_theme_score标签
        try:
            from theme.theme_timing import ThemeTimingEngine, match_pool_to_themes
            theme_engine = ThemeTimingEngine(self.root_cfg)
            theme_engine.load_theme_map(self.theme_map_path)
            theme_states = theme_engine.evaluate(pool_df)
            pool_df = match_pool_to_themes(pool_df, theme_states)
            if "best_theme_score" in pool_df.columns:
                n_with_theme = (pool_df["best_theme_score"] > 0).sum()
                LOG.info("主题标签: %d/%d 只有主题评分", n_with_theme, len(pool_df))
        except Exception as e:
            LOG.warning("主题评分失败(跳过): %s", e)

        # 1e. 初始化信号融合引擎（需传入完整根配置）和风控管理器
        self.fusion_engine = SignalFusionEngine(self.root_cfg)
        self.risk_manager = RiskManager(self.cfg)

        # =========================================================
        # 2. 生成交易日历
        # =========================================================
        all_dates = set()
        for code, df in self._daily_cache.items():
            all_dates.update(df["trade_date"].tolist())
        all_dates = sorted(all_dates)

        # 筛选回测日期范围
        self.trade_dates = [d for d in all_dates
                            if d >= start_date and d <= effective_end]
        if not self.trade_dates:
            return {"error": f"在 {start_date}~{effective_end} 范围内无交易日"}

        # 找到预热起始日期前 warmup_days 天的日期
        first_date_idx = max(0, all_dates.index(self.trade_dates[0]) - warmup_days)
        warmup_start = all_dates[first_date_idx]

        LOG.info("交易日历: %d 个交易日 (回测 %d 天, 预热起始 %s)",
                 len(all_dates), len(self.trade_dates), warmup_start)

        # =========================================================
        # 3. 逐日回测
        # =========================================================
        LOG.info("开始逐日回测...")

        # 用于信号检测的候选股票（筛选出当天有完整缓存的代码）
        cached_codes = list(self._daily_cache.keys())

        for day_idx, trade_date in enumerate(self.trade_dates):
            # ---------- 进度日志 ----------
            if (day_idx + 1) % 20 == 0 or day_idx == 0:
                LOG.info("回测进度: %d / %d  (%s)",
                         day_idx + 1, len(self.trade_dates), trade_date)

            # ---------- 获取当日各股票的切片数据 ----------
            # 对每只持仓股票，获取包含当前日期的完整切片（用于出场信号检测）
            slice_cache: Dict[str, pd.DataFrame] = {}
            for code in cached_codes:
                full = self._daily_cache[code]
                mask = full["trade_date"] <= trade_date
                sliced = full[mask].tail(warmup_days).copy()  # 取最近120天足够
                if len(sliced) >= 30:  # 至少30根K线才能做信号判断
                    slice_cache[code] = sliced

            # ---------- a. 检查现有持仓的出场信号 ----------
            exit_codes = list(self.positions.keys())
            for ts_code in exit_codes:
                trade = self.positions[ts_code]
                df_slice = slice_cache.get(ts_code)

                if df_slice is None or df_slice.empty:
                    continue

                # 更新持仓期间最高价
                current_high = float(df_slice.iloc[-1].get("high", 0))
                if current_high > trade._highest_price:
                    trade._highest_price = current_high

                # 检测综合出场信号
                exit_result = exit_signals.composite_exit_signal(
                    df=df_slice,
                    entry_price=trade.buy_price,
                    highest_price=trade._highest_price,
                    config=self.cfg,
                )

                if exit_result.get("should_exit", False):
                    self._execute_sell(
                        ts_code=ts_code,
                        trade_date=trade_date,
                        df=df_slice,
                        reason=exit_result.get("reason", "出场信号"),
                    )

            # ---------- b. 风控检查 ----------
            # 使用总资产（现金+持仓市值）而非仅现金进行风控
            prev_equity = self.initial_capital
            if self.equity_curve:
                prev_equity = self.equity_curve[-1]["total_equity"]
            daily_pnl_pct = ((self.capital - prev_equity) / prev_equity) * 100

            risk_results = self.risk_manager.check_all(
                capital=prev_equity,
                daily_pnl_pct=daily_pnl_pct,
                position_df=pd.DataFrame(),  # 暂不传持仓DataFrame
            )
            # 每日风控仅做警告，不阻断交易（回测中由后续的组合表现自然约束）
            risk_passed = True

            # ---------- c. 生成买入信号 ----------
            # 预热已通过数据切片处理（取回测日前120天计算指标），不再额外跳过
            is_warmup = False

            new_signals = []
            if not is_warmup and risk_passed:
                # 获取当日股池中所有在缓存中有数据的股票
                today_pool = pool_df[pool_df["ts_code"].isin(slice_cache.keys())].copy()
                if not today_pool.empty:
                    try:
                        new_signals = self.fusion_engine.evaluate(
                            pool_df=today_pool,
                            trade_date=trade_date,
                            etf_data=None,
                            daily_cache=self._daily_cache,
                        )
                    except Exception as e:
                        LOG.error("信号生成失败 %s: %s", trade_date, e)

            # ---------- d. 计算仓位分配 ----------
            buy_signals = [s for s in new_signals if s.signal_type == "buy"]
            position_df = calculate_positions(
                signals=buy_signals,
                config=self.cfg,
                market_position_suggest=1.0,
            )

            # ---------- e. 执行买入 ----------
            if not position_df.empty:
                max_pos = self.root_cfg.get("position", {}).get("max_positions", 80)
                max_new = self.root_cfg.get("position", {}).get("max_new_positions_per_day", 15)
                buys_placed = 0
                for _, pos_row in position_df.iterrows():
                    if len(self.positions) + buys_placed >= max_pos:
                        break
                    if buys_placed >= max_new:
                        break
                    ts_code = pos_row["ts_code"]
                    # 如果已持仓则不再买入
                    if ts_code in self.positions:
                        continue
                    # 风控：黑名单检查
                    rc = self.risk_manager.check_ts_code(ts_code)
                    if not rc.passed:
                        continue

                    df_slice = slice_cache.get(ts_code)
                    if df_slice is None or df_slice.empty:
                        continue

                    # 查找对应信号的详细信息
                    matched_signal = None
                    for s in buy_signals:
                        if s.ts_code == ts_code:
                            matched_signal = s
                            break

                    self._execute_buy(
                        signal=matched_signal,
                        trade_date=trade_date,
                        df=df_slice,
                        position_ratio=float(pos_row["position"]),
                    )
                    buys_placed += 1

            # ---------- f. 记录当日净值 ----------
            self._record_equity(trade_date)

        # =========================================================
        # 4. 计算绩效指标
        # =========================================================
        LOG.info("回测完成, 交易次数: %d, 最终资金: %.2f",
                 len(self.closed_trades), self.capital)

        # 延迟导入绩效模块
        from .metrics import calc_metrics

        metrics = calc_metrics(
            equity_curve=self.equity_curve,
            trades=self.closed_trades,
            initial_capital=self.initial_capital,
            risk_free_rate=0.02,
        )

        # 构建Trade列表（序列化输出）
        trades_list = list(self.closed_trades)

        # 返回结果
        result = {
            "trades": trades_list,
            "metrics": metrics,
            "equity_curve": self.equity_curve,
            "config": {
                "initial_capital": self.initial_capital,
                "commission": self.commission,
                "slippage": self.slippage,
                "start_date": start_date,
                "end_date": effective_end,
            },
        }

        LOG.info("====== 回测结束 ======")
        return result

    # ─────────────────────────────────────────────────────────────
    # 买卖执行
    # ─────────────────────────────────────────────────────────────

    def _execute_buy(
        self,
        signal,
        trade_date: str,
        df: pd.DataFrame,
        position_ratio: float = 0.0,
    ) -> Optional[BacktestTrade]:
        """执行买入

        买入价 = close * (1 + slippage)
        买入金额 = capital * position_ratio
        成交股数 = 向下取整到100股整手
        扣除佣金
        """
        if df.empty:
            return None

        last_row = df.iloc[-1]
        close_price = float(last_row.get("close", 0))
        if close_price <= 0:
            return None

        # 买入价（含滑点）
        buy_price = close_price * (1 + self.slippage)

        # 分配资金
        if signal is not None and hasattr(signal, "composite_score"):
            composite_score = signal.composite_score
            primary_entry = getattr(signal, "primary_entry", "")
            stock_name = getattr(signal, "stock_name", last_row.get("ts_code", ""))
            # 如果没传position_ratio，从信号中取
            if position_ratio <= 0 and hasattr(signal, "position_ratio"):
                position_ratio = signal.position_ratio
        else:
            composite_score = 0.0
            primary_entry = ""
            stock_name = str(last_row.get("ts_code", ""))

        # 仓位比例不能超过1
        position_ratio = min(position_ratio, 1.0)

        # 买入金额 = 当前资金 * 仓位比例
        buy_amount = self.capital * position_ratio

        # 计算可买股数（向下取整到100股）
        raw_shares = buy_amount / buy_price
        shares = np.floor(raw_shares / 100) * 100  # 整手

        if shares <= 0:
            LOG.debug("资金不足买入 %s: 需 %.2f, 有 %.2f",
                      signal.ts_code if signal else "?", buy_amount, self.capital)
            return None

        # 实际买入金额
        actual_cost = shares * buy_price
        # 扣除佣金
        commission_cost = actual_cost * self.commission
        total_cost = actual_cost + commission_cost

        if total_cost > self.capital:
            # 调整股数
            max_shares = np.floor((self.capital / (buy_price * (1 + self.commission))) / 100) * 100
            if max_shares <= 0:
                return None
            shares = max_shares
            actual_cost = shares * buy_price
            commission_cost = actual_cost * self.commission
            total_cost = actual_cost + commission_cost

        # 扣减资金
        self.capital -= total_cost

        ts_code = signal.ts_code if signal else last_row.get("ts_code", "")

        trade = BacktestTrade(
            ts_code=ts_code,
            stock_name=stock_name,
            buy_date=trade_date,
            buy_price=round(buy_price, 3),
            shares=shares,
            position_ratio=position_ratio,
            composite_score=composite_score,
            primary_entry=primary_entry,
            _highest_price=buy_price,  # 初始最高价 = 买入价
        )

        self.positions[ts_code] = trade

        LOG.debug("买入 %s %.0f股 @ %.3f (%.2f%%) 金额: %.2f 佣金: %.2f",
                  ts_code, shares, buy_price, position_ratio * 100,
                  actual_cost, commission_cost)
        return trade

    def _execute_sell(
        self,
        ts_code: str,
        trade_date: str,
        df: pd.DataFrame,
        reason: str = "",
    ) -> Optional[BacktestTrade]:
        """执行卖出

        卖出价 = close * (1 - slippage)
        卖出收入 = 股数 * 卖出价 * (1 - commission)
        """
        trade = self.positions.get(ts_code)
        if trade is None:
            return None

        if df.empty:
            return None

        last_row = df.iloc[-1]
        close_price = float(last_row.get("close", 0))
        if close_price <= 0:
            return None

        # 卖出价（含滑点）
        sell_price = close_price * (1 - self.slippage)

        # 卖出收入（扣除佣金）
        gross_proceeds = trade.shares * sell_price
        commission_cost = gross_proceeds * self.commission
        net_proceeds = gross_proceeds - commission_cost

        # 计算盈亏
        total_cost = trade.shares * trade.buy_price * (1 + self.commission)
        pnl_amount = net_proceeds - total_cost
        pnl_pct = (net_proceeds / total_cost - 1) * 100 if total_cost > 0 else 0.0

        # 持仓天数
        try:
            buy_dt = pd.Timestamp(trade.buy_date)
            sell_dt = pd.Timestamp(trade_date)
            hold_days = max(1, (sell_dt - buy_dt).days)
        except Exception:
            hold_days = 1

        # 更新成交信息
        trade.sell_date = trade_date
        trade.sell_price = round(sell_price, 3)
        trade.pnl_pct = round(pnl_pct, 2)
        trade.pnl_amount = round(pnl_amount, 2)
        trade.hold_days = hold_days
        trade.exit_reason = reason

        # 回收资金
        self.capital += net_proceeds
        if self.capital > self.peak_capital:
            self.peak_capital = self.capital

        # 移出持仓
        del self.positions[ts_code]
        self.closed_trades.append(trade)

        LOG.debug("卖出 %s @ %.3f  盈亏: %.2f%% (%.2f)  持仓%d天  原因: %s",
                  ts_code, sell_price, pnl_pct, pnl_amount, hold_days, reason)
        return trade

    # ─────────────────────────────────────────────────────────────
    # 净值记录
    # ─────────────────────────────────────────────────────────────

    def _record_equity(self, trade_date: str) -> dict:
        """记录当日净值

        总资产 = 现金 + 所有持仓市值
        """
        # 计算持仓市值
        position_value = 0.0
        for ts_code, trade in list(self.positions.items()):
            cache_df = self._daily_cache.get(ts_code)
            if cache_df is not None:
                # 取当日收盘价
                day_data = cache_df[cache_df["trade_date"] == trade_date]
                if not day_data.empty:
                    cur_price = float(day_data.iloc[-1].get("close", 0))
                    mv = trade.shares * cur_price
                    trade.shares  # 保持引用
                else:
                    mv = trade.shares * trade.buy_price  # 使用买入价近似
            else:
                mv = trade.shares * trade.buy_price
            position_value += mv

        total_equity = self.capital + position_value
        if total_equity > self.peak_capital:
            self.peak_capital = total_equity

        # 当日收益率
        if self.equity_curve:
            prev_equity = self.equity_curve[-1]["total_equity"]
            daily_return = ((total_equity - prev_equity) / prev_equity) * 100
        else:
            daily_return = 0.0

        record = {
            "trade_date": trade_date,
            "cash": round(self.capital, 2),
            "position_value": round(position_value, 2),
            "total_equity": round(total_equity, 2),
            "daily_return": round(daily_return, 4),
            "peak_capital": round(self.peak_capital, 2),
            "drawdown": round((total_equity - self.peak_capital) / self.peak_capital * 100, 4)
                if self.peak_capital > 0 else 0.0,
            "position_count": len(self.positions),
        }

        self.equity_curve.append(record)
        return record
