import os
import datetime
import pandas as pd
import numpy as np

# ==================== 配置区域 ====================
TDX_PATH = r"C:\new_tdx\vipdoc"
ETF_CODE = "159531"
MARKET = "sz"
ATR_PERIOD = 14
ATR_MULTIPLIER = 3.0   # 放宽止损：2.0 -> 3.0
MOM_PERIOD = 20
INIT_CAPITAL = 100000
COMMISSION = 0.0003
SLIPPAGE = 0.001
# =================================================


def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = int.from_bytes(chunk[0:4], "little")
            open_price = int.from_bytes(chunk[4:8], "little") / 100
            high_price = int.from_bytes(chunk[8:12], "little") / 100
            low_price = int.from_bytes(chunk[12:16], "little") / 100
            close_price = int.from_bytes(chunk[16:20], "little") / 100
            volume = int.from_bytes(chunk[20:24], "little")
            amount = int.from_bytes(chunk[24:28], "little") / 100.0
            dt = datetime.datetime.strptime(str(date_int), "%Y%m%d")
            data.append({
                "trade_date": date_int, "date": dt,
                "open": open_price, "high": high_price, "low": low_price,
                "close": close_price, "volume": volume, "amount": amount,
            })
    df = pd.DataFrame(data)
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def calculate_indicators(df):
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["close"].shift(1)).abs()
    df["tr3"] = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(window=ATR_PERIOD).mean()

    df["momentum"] = df["close"] - df["close"].shift(MOM_PERIOD)
    df["highest_close"] = df["close"].rolling(window=MOM_PERIOD).max()
    df["long_stop_line"] = df["highest_close"] - (df["atr"] * ATR_MULTIPLIER)

    # 均线
    df["ma10"] = df["close"].rolling(window=10).mean()
    df["ma20"] = df["close"].rolling(window=20).mean()
    df["ma60"] = df["close"].rolling(window=60).mean()

    # 成交量均线
    df["vol_ma5"] = df["volume"].rolling(window=5).mean()

    return df


def generate_signals_v3(df):
    """V3 核心逻辑：牛市跟随策略
    买入：MA10>MA60（中期多头）+ 动能>0（趋势向上）+ 价格在MA20上方（不抄底，确认趋势）
    卖出：仅跌破ATR动态止损线（不因短期动能转负卖出）
    """
    position = 0
    signals = []

    for idx, row in df.iterrows():
        current_close = row["close"]
        current_mom = row["momentum"]
        current_stop = row["long_stop_line"]
        ma10 = row["ma10"]
        ma20 = row["ma20"]
        ma60 = row["ma60"]

        if pd.isna(current_mom) or pd.isna(current_stop) or pd.isna(ma60):
            signals.append(position)
            continue

        if position == 0:
            # 买入条件：
            # 1. 动能>0（趋势向上）
            # 2. MA10 > MA60（中期多头排列）
            # 3. 价格在MA20上方（确认趋势，不抄底）
            mom_ok = current_mom > 0
            trend_ok = ma10 > ma60
            above_ma20 = True if pd.isna(ma20) else current_close > ma20

            if mom_ok and trend_ok and above_ma20:
                position = 1

        elif position == 1:
            # 卖出：仅ATR动态止损
            if current_close < current_stop:
                position = 0

        signals.append(position)

    df["signal"] = signals
    df["action"] = df["signal"].diff().fillna(0)
    return df


def backtest_strategy(df, start_date, end_date):
    df_bt = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy().reset_index(drop=True)
    if len(df_bt) == 0:
        raise ValueError("回测区间内无数据")

    capital = INIT_CAPITAL
    position = 0
    shares = 0
    trades = []
    equity_curve = []

    for idx, row in df_bt.iterrows():
        date = row["date"]
        close = row["close"]
        action = row["action"]

        market_value = shares * close if position == 1 else capital
        equity_curve.append({"date": date, "equity": market_value})

        if action == 1 and position == 0:
            buy_amount = capital * 0.95
            shares = int(buy_amount / (close * (1 + SLIPPAGE)) / 100) * 100
            if shares > 0:
                cost = shares * close * (1 + SLIPPAGE) * (1 + COMMISSION)
                capital -= cost
                position = 1
                trades.append({"entry_date": date, "entry_price": close, "shares": shares})

        elif action == -1 and position == 1:
            if len(trades) > 0:
                trade = trades[-1]
                sell_amount = shares * close * (1 - SLIPPAGE) * (1 - COMMISSION)
                capital += sell_amount
                profit = sell_amount - (trade["shares"] * trade["entry_price"])
                profit_pct = (close - trade["entry_price"]) / trade["entry_price"] * 100
                trade.update({"exit_date": date, "exit_price": close, "profit": profit, "profit_pct": profit_pct})
                position = 0
                shares = 0

    if position == 1 and len(trades) > 0:
        trade = trades[-1]
        last_close = df_bt.iloc[-1]["close"]
        sell_amount = shares * last_close * (1 - SLIPPAGE) * (1 - COMMISSION)
        capital += sell_amount
        profit = sell_amount - (trade["shares"] * trade["entry_price"])
        profit_pct = (last_close - trade["entry_price"]) / trade["entry_price"] * 100
        trade.update({"exit_date": df_bt.iloc[-1]["date"], "exit_price": last_close, "profit": profit, "profit_pct": profit_pct})

    if len(trades) == 0:
        return {"total_trades": 0, "win_rate": 0, "total_return": 0, "annual_return": 0,
                "max_drawdown": 0, "sharpe_ratio": 0, "final_capital": INIT_CAPITAL, "trades": trades}

    final_capital = capital
    total_return = (final_capital - INIT_CAPITAL) / INIT_CAPITAL * 100
    days = (df_bt.iloc[-1]["date"] - df_bt.iloc[0]["date"]).days
    annual_return = ((1 + total_return / 100) ** (365 / days) - 1) * 100 if days > 0 else 0

    win_trades = [t for t in trades if t.get("profit", 0) > 0]
    win_rate = len(win_trades) / len(trades) * 100

    equity_df = pd.DataFrame(equity_curve)
    equity_df["cummax"] = equity_df["equity"].cummax()
    equity_df["drawdown"] = (equity_df["equity"] - equity_df["cummax"]) / equity_df["cummax"] * 100
    max_drawdown = equity_df["drawdown"].min()

    equity_df["daily_return"] = equity_df["equity"].pct_change()
    sharpe = equity_df["daily_return"].mean() / equity_df["daily_return"].std() * np.sqrt(252) if equity_df["daily_return"].std() > 0 else 0

    return {
        "total_trades": len(trades), "win_rate": win_rate,
        "total_return": total_return, "annual_return": annual_return,
        "max_drawdown": max_drawdown, "sharpe_ratio": sharpe,
        "final_capital": final_capital, "trades": trades,
    }


def main():
    tdx_file = os.path.join(TDX_PATH, MARKET, "lday", f"{MARKET}{ETF_CODE}.day")
    print(f"正在读取通达信数据: {tdx_file}")

    try:
        df = parse_tdx_day_file(tdx_file)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        return

    print(f"数据读取完成，共 {len(df)} 条记录")
    print(f"数据区间: {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")

    df = calculate_indicators(df)
    df = generate_signals_v3(df)

    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365)
    print(f"\n开始回测: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

    result = backtest_strategy(df, start_date, end_date)

    print("\n" + "=" * 50)
    print("【ETF 波段策略 V3 回测报告】")
    print("=" * 50)
    print(f"回测区间: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print(f"标的代码: {ETF_CODE}.{MARKET.upper()}")
    print(f"策略参数: ATR={ATR_PERIOD}*{ATR_MULTIPLIER}, MOM={MOM_PERIOD}")
    print(f"初始资金: {INIT_CAPITAL:,.0f} 元")
    print(f"最终资金: {result['final_capital']:,.0f} 元")
    print(f"总收益率: {result['total_return']:.2f}%")
    print(f"年化收益率: {result['annual_return']:.2f}%")
    print(f"最大回撤: {result['max_drawdown']:.2f}%")
    print(f"夏普比率: {result['sharpe_ratio']:.2f}")
    print(f"交易次数: {result['total_trades']}")
    print(f"胜率: {result['win_rate']:.2f}%")
    print("=" * 50)

    if len(result["trades"]) > 0:
        print("\n逐笔交易明细:")
        print("-" * 80)
        for i, trade in enumerate(result["trades"], 1):
            entry_date = trade["entry_date"].strftime("%Y-%m-%d")
            exit_date = trade.get("exit_date", "持仓中")
            if isinstance(exit_date, datetime.datetime):
                exit_date = exit_date.strftime("%Y-%m-%d")
            entry_price = trade["entry_price"]
            exit_price = trade.get("exit_price", 0)
            profit = trade.get("profit", 0)
            profit_pct = trade.get("profit_pct", 0)
            flag = "+" if profit > 0 else "-"
            print(
                f"{i:3d}. {entry_date} 买入 {entry_price:.3f} -> {exit_date} 卖出 {exit_price:.3f} | "
                f"收益: {profit:,.0f} 元 ({profit_pct:+.2f}%) [{flag}]"
            )
        print("-" * 80)

        output_file = r"C:\Users\kongx\mystock\etf_backtest_v3_result.csv"
        trades_df = pd.DataFrame(result["trades"])
        trades_df.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"\n交易明细已保存: {output_file}")


if __name__ == "__main__":
    main()
