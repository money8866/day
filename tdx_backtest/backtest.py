# -*- coding: utf-8 -*-
"""
Backtester — A股 T+1 回测引擎

交易规则:
  - T+1: 当日买入, 次日才能卖出
  - 手续费: 万分之2.5 (双向)
  - 印花税: 千分之1 (仅卖出)
  - 滑点: 可配置 (默认 0.01元 = 1分)
  - 全仓买入, 单次只持有一只股票

模式:
  - 单标的: 信号在该股票自身 K 线上产生
  - 多标的轮动: 每日在候选池中选一只最强信号, 持有期间忽略新买入信号
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import numpy as np
import pandas as pd


# =========================================================
# 交易成本配置
# =========================================================
@dataclass
class CostConfig:
    commission_rate: float = 0.00025   # 佣金费率 万2.5
    commission_min:  float = 5.0       # 单笔最低佣金 5 元
    stamp_tax_rate:  float = 0.001     # 印花税 千1 (仅卖出)
    slippage:        float = 0.01      # 滑点 1分钱/股

    def buy_cost(self, amount: float) -> float:
        """买入总成本 (佣金)"""
        fee = amount * self.commission_rate
        return max(fee, self.commission_min)

    def sell_cost(self, amount: float) -> float:
        """卖出总成本 (佣金 + 印花税)"""
        fee = amount * self.commission_rate
        return max(fee, self.commission_min) + amount * self.stamp_tax_rate


# =========================================================
# 回测结果
# =========================================================
@dataclass
class TradeRecord:
    """单笔交易记录"""
    buy_date:   str
    buy_price:  float
    sell_date:  str
    sell_price: float
    shares:     int
    profit:     float
    pct_return: float
    hold_days:  int


@dataclass
class BacktestResult:
    """回测结果"""
    equity_curve: pd.Series          # 资金曲线 (index=date, value=总资产)
    trades: List[TradeRecord]        # 交易记录
    position_curve: pd.Series       # 持仓状态 (1=持仓, 0=空仓)
    daily_returns: pd.Series        # 日收益率
    final_equity: float             # 最终权益
    initial_capital: float          # 初始资金

    @property
    def n_trades(self) -> int:
        return len(self.trades)


# =========================================================
# 回测引擎
# =========================================================
class Backtester:
    """T+1 回测引擎

    Args:
        initial_capital: 初始资金
        cost: 交易成本配置
    """

    def __init__(self,
                 initial_capital: float = 100_000,
                 cost: Optional[CostConfig] = None):
        self.initial_capital = initial_capital
        self.cost = cost or CostConfig()

    # -----------------------------------------------------
    # 单标的回测
    # -----------------------------------------------------
    def run_single(self, df: pd.DataFrame) -> BacktestResult:
        """单标的回测

        Args:
            df: K线 + 信号, 必须含 buy_signal, sell_signal 列

        Returns:
            BacktestResult
        """
        if "buy_signal" not in df.columns or "sell_signal" not in df.columns:
            raise ValueError("df 必须含 buy_signal / sell_signal 列")

        cash = self.initial_capital
        shares = 0
        holding = False
        buy_price = 0.0
        buy_date = ""
        buy_idx = -1

        equity = []      # 每日总资产
        position = []    # 每日持仓状态
        trades: List[TradeRecord] = []

        for i, row in df.iterrows():
            date = str(row["trade_date"])
            close = float(row["close"])

            # ========== 信号处理 ==========
            # T+1: 卖出信号在当日收盘卖出, 买入信号在次日开盘买入
            # 简化: 用 close 价成交, 但买入需检查 holding 状态
            if holding:
                # 持仓中: 检查卖出信号
                if row["sell_signal"]:
                    sell_price = close - self.cost.slippage
                    sell_amount = sell_price * shares
                    fee = self.cost.sell_cost(sell_amount)
                    cash += sell_amount - fee
                    profit = (sell_price - buy_price) * shares - fee - self.cost.buy_cost(buy_price * shares)
                    pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                    trades.append(TradeRecord(
                        buy_date=buy_date, buy_price=buy_price,
                        sell_date=date, sell_price=sell_price,
                        shares=shares, profit=profit, pct_return=pct,
                        hold_days=i - buy_idx,
                    ))
                    holding = False
                    shares = 0
            else:
                # 空仓: 检查买入信号
                if row["buy_signal"]:
                    buy_price = close + self.cost.slippage
                    # 全仓买入 (留足手续费)
                    buy_amount = cash
                    shares = int(buy_amount / (buy_price * (1 + self.cost.commission_rate)))
                    if shares <= 0:
                        shares = 0
                        continue
                    fee = self.cost.buy_cost(buy_price * shares)
                    cash -= buy_price * shares + fee
                    holding = True
                    buy_date = date
                    buy_idx = i

            # 当日总资产
            total = cash + (shares * close if holding else 0)
            equity.append(total)
            position.append(1 if holding else 0)

        equity_series = pd.Series(equity, index=df["trade_date"].values, name="equity")
        position_series = pd.Series(position, index=df["trade_date"].values, name="position")
        daily_returns = equity_series.pct_change().fillna(0)

        return BacktestResult(
            equity_curve=equity_series,
            trades=trades,
            position_curve=position_series,
            daily_returns=daily_returns,
            final_equity=equity[-1] if equity else self.initial_capital,
            initial_capital=self.initial_capital,
        )

    # -----------------------------------------------------
    # 多标的轮动回测
    # -----------------------------------------------------
    def run_rotation(self, df_pool: pd.DataFrame, signal_col: str = "buy_signal") -> BacktestResult:
        """多标的轮动回测: 每日在全部候选股中选第一个买入信号

        Args:
            df_pool: 长表, 含 ts_code, trade_date, OHLCV, buy_signal, sell_signal
            signal_col: 买入信号列名

        Returns:
            BacktestResult
        """
        # 透视: 每日每只股票的 close 和信号
        dates = sorted(df_pool["trade_date"].unique())

        cash = self.initial_capital
        shares = 0
        holding_code = None
        buy_price = 0.0
        buy_date = ""
        buy_idx = -1

        equity = []
        position = []
        trades: List[TradeRecord] = []

        for i, date in enumerate(dates):
            day_df = df_pool[df_pool["trade_date"] == date]
            day_df = day_df.sort_values("ts_code")

            # 当前持仓的当日数据
            hold_row = day_df[day_df["ts_code"] == holding_code] if holding_code else None

            # ========== 卖出判断 ==========
            if holding_code is not None and hold_row is not None and len(hold_row) > 0:
                row = hold_row.iloc[0]
                close = float(row["close"])
                if row.get("sell_signal", False):
                    sell_price = close - self.cost.slippage
                    sell_amount = sell_price * shares
                    fee = self.cost.sell_cost(sell_amount)
                    cash += sell_amount - fee
                    profit = (sell_price - buy_price) * shares - fee
                    pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                    trades.append(TradeRecord(
                        buy_date=buy_date, buy_price=buy_price,
                        sell_date=str(date), sell_price=sell_price,
                        shares=shares, profit=profit, pct_return=pct,
                        hold_days=i - buy_idx,
                    ))
                    holding_code = None
                    shares = 0
                # 总资产
                total = cash + shares * close
            else:
                # ========== 买入判断 ==========
                if holding_code is None:
                    # 找当日有买入信号的股票 (按 ts_code 排序取第一只)
                    buy_candidates = day_df[day_df.get(signal_col, False) == True]
                    if len(buy_candidates) > 0:
                        row = buy_candidates.iloc[0]
                        buy_price = float(row["close"]) + self.cost.slippage
                        buy_amount = cash
                        shares = int(buy_amount / (buy_price * (1 + self.cost.commission_rate)))
                        if shares > 0:
                            fee = self.cost.buy_cost(buy_price * shares)
                            cash -= buy_price * shares + fee
                            holding_code = row["ts_code"]
                            buy_date = str(date)
                            buy_idx = i
                # 总资产
                if holding_code is not None:
                    cur = day_df[day_df["ts_code"] == holding_code]
                    if len(cur) > 0:
                        total = cash + shares * float(cur.iloc[0]["close"])
                    else:
                        total = cash  # 停牌等异常
                else:
                    total = cash

            equity.append(total)
            position.append(1 if holding_code else 0)

        equity_series = pd.Series(equity, index=dates, name="equity")
        position_series = pd.Series(position, index=dates, name="position")
        daily_returns = equity_series.pct_change().fillna(0)

        return BacktestResult(
            equity_curve=equity_series,
            trades=trades,
            position_curve=position_series,
            daily_returns=daily_returns,
            final_equity=equity[-1] if equity else self.initial_capital,
            initial_capital=self.initial_capital,
        )


# =========================================================
# 自检
# =========================================================
if __name__ == "__main__":
    from data_loader import load_kline
    from strategy import DoubleMAStrategy

    df = load_kline("000001.SZ", start_date="20230101")
    if df.empty:
        print("无数据")
        raise SystemExit

    strat = DoubleMAStrategy(5, 10)
    df_sig = strat.generate_signals(df)
    print(f"买入信号: {df_sig['buy_signal'].sum()} 次, 卖出: {df_sig['sell_signal'].sum()} 次")

    bt = Backtester(initial_capital=100_000)
    result = bt.run_single(df_sig)

    print(f"\n回测结果:")
    print(f"  交易次数: {result.n_trades}")
    print(f"  初始资金: {result.initial_capital:,.0f}")
    print(f"  最终权益: {result.final_equity:,.0f}")
    print(f"  总收益率: {(result.final_equity / result.initial_capital - 1) * 100:.2f}%")

    if result.trades:
        wins = [t for t in result.trades if t.profit > 0]
        print(f"  胜率: {len(wins)}/{len(result.trades)} = {len(wins)/len(result.trades)*100:.1f}%")
