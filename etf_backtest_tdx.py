import os
import datetime
import pandas as pd
import numpy as np

# ==================== 配置区域 ====================
TDX_PATH = r"C:\new_tdx\vipdoc"  # 通达信数据根目录
ETF_CODE = "159531"  # ETF 代码（不含后缀）
MARKET = "sz"  # 市场：sh/sz
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0
MOM_PERIOD = 20
INIT_CAPITAL = 100000  # 初始资金
COMMISSION = 0.0003  # 交易佣金（双边）
SLIPPAGE = 0.001  # 滑点
# =================================================


def parse_tdx_day_file(filepath):
    """解析通达信 .day 文件，返回 DataFrame"""
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

            # 转换日期格式：YYYYMMDD -> datetime
            date_str = str(date_int)
            dt = datetime.datetime.strptime(date_str, "%Y%m%d")

            data.append(
                {
                    "trade_date": date_int,
                    "date": dt,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "amount": amount,
                }
            )

    df = pd.DataFrame(data)
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def calculate_indicators(df):
    """计算 ATR、动能、动态止损线"""
    # 1. 计算 ATR
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["close"].shift(1)).abs()
    df["tr3"] = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(window=ATR_PERIOD).mean()

    # 2. 计算动能和跟踪止损线
    df["momentum"] = df["close"] - df["close"].shift(MOM_PERIOD)
    df["highest_close"] = df["close"].rolling(window=MOM_PERIOD).max()
    df["long_stop_line"] = df["highest_close"] - (df["atr"] * ATR_MULTIPLIER)

    return df


def generate_signals(df):
    """生成买卖信号"""
    position = 0
    signals = []
    stop_lines = []

    for idx, row in df.iterrows():
        current_close = row["close"]
        current_mom = row["momentum"]
        current_stop = row["long_stop_line"]
        highest_c = row["highest_close"]

        if pd.isna(current_mom) or pd.isna(current_stop):
            signals.append(0)
            stop_lines.append(None)
            continue

        if position == 0:
            # 买入条件：动能为正 且 价格接近或突破近期高点
            if current_mom > 0 and current_close > highest_c * 0.98:
                position = 1
        elif position == 1:
            # 卖出条件：跌破动态下轨 或 动能转负
            if current_close < current_stop or current_mom < 0:
                position = 0

        signals.append(position)
        stop_lines.append(current_stop if position == 1 else None)

    df["signal"] = signals
    df["stop_line"] = stop_lines
    df["action"] = df["signal"].diff().fillna(0)

    return df


def backtest_strategy(df, start_date, end_date):
    """回测策略"""
    # 筛选回测区间
    df_bt = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy()
    df_bt = df_bt.reset_index(drop=True)

    if len(df_bt) == 0:
        raise ValueError("回测区间内无数据")

    # 回测变量
    capital = INIT_CAPITAL
    position = 0
    shares = 0
    entry_price = 0
    trades = []
    equity_curve = []

    for idx, row in df_bt.iterrows():
        date = row["date"]
        close = row["close"]
        signal = row["signal"]
        action = row["action"]

        # 记录当日权益
        if position == 1:
            market_value = shares * close
        else:
            market_value = capital
        equity_curve.append({"date": date, "equity": market_value})

        # 买入
        if action == 1 and position == 0:
            # 计算买入数量和成本
            buy_amount = capital * 0.95  # 留 5% 现金
            shares = int(buy_amount / (close * (1 + SLIPPAGE)) / 100) * 100  # 整百股
            if shares > 0:
                cost = shares * close * (1 + SLIPPAGE) * (1 + COMMISSION)
                capital -= cost
                position = 1
                entry_price = close
                trades.append(
                    {
                        "entry_date": date,
                        "entry_price": close,
                        "shares": shares,
                    }
                )

        # 卖出
        elif action == -1 and position == 1:
            if len(trades) > 0:
                trade = trades[-1]
                sell_amount = shares * close * (1 - SLIPPAGE) * (1 - COMMISSION)
                capital += sell_amount
                profit = sell_amount - (trade["shares"] * trade["entry_price"])
                profit_pct = (close - trade["entry_price"]) / trade["entry_price"] * 100

                # 更新交易记录
                trade["exit_date"] = date
                trade["exit_price"] = close
                trade["profit"] = profit
                trade["profit_pct"] = profit_pct

                position = 0
                shares = 0
                entry_price = 0

    # 如果最后一天还持仓，按收盘价平仓
    if position == 1 and len(trades) > 0:
        trade = trades[-1]
        sell_amount = shares * df_bt.iloc[-1]["close"] * (1 - SLIPPAGE) * (1 - COMMISSION)
        capital += sell_amount
        profit = sell_amount - (trade["shares"] * trade["entry_price"])
        profit_pct = (df_bt.iloc[-1]["close"] - trade["entry_price"]) / trade["entry_price"] * 100

        trade["exit_date"] = df_bt.iloc[-1]["date"]
        trade["exit_price"] = df_bt.iloc[-1]["close"]
        trade["profit"] = profit
        trade["profit_pct"] = profit_pct

    # 计算回测指标
    if len(trades) == 0:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "total_return": 0,
            "annual_return": 0,
            "max_drawdown": 0,
            "sharpe_ratio": 0,
            "trades": [],
        }

    # 转换权益曲线
    equity_df = pd.DataFrame(equity_curve)
    equity_df["equity"] = equity_df["equity"].astype(float)

    # 计算收益
    final_capital = capital
    total_return = (final_capital - INIT_CAPITAL) / INIT_CAPITAL * 100

    # 年化收益
    days = (df_bt.iloc[-1]["date"] - df_bt.iloc[0]["date"]).days
    annual_return = (1 + total_return / 100) ** (365 / days) - 1 if days > 0 else 0

    # 胜率
    win_trades = [t for t in trades if t.get("profit", 0) > 0]
    win_rate = len(win_trades) / len(trades) * 100 if len(trades) > 0 else 0

    # 最大回撤
    equity_df["cummax"] = equity_df["equity"].cummax()
    equity_df["drawdown"] = (equity_df["equity"] - equity_df["cummax"]) / equity_df["cummax"] * 100
    max_drawdown = equity_df["drawdown"].min()

    # 夏普比率（简化版，假设无风险利率为 0）
    equity_df["daily_return"] = equity_df["equity"].pct_change()
    sharpe_ratio = (
        equity_df["daily_return"].mean() / equity_df["daily_return"].std() * np.sqrt(252)
        if equity_df["daily_return"].std() > 0
        else 0
    )

    return {
        "total_trades": len(trades),
        "win_rate": win_rate,
        "total_return": total_return,
        "annual_return": annual_return * 100,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "final_capital": final_capital,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def main():
    # 1. 读取通达信数据
    tdx_file = os.path.join(TDX_PATH, MARKET, "lday", f"{MARKET}{ETF_CODE}.day")
    print(f"正在读取通达信数据: {tdx_file}")

    try:
        df = parse_tdx_day_file(tdx_file)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        return

    print(f"数据读取完成，共 {len(df)} 条记录")
    print(f"数据区间: {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")

    # 2. 计算指标
    print("正在计算技术指标...")
    df = calculate_indicators(df)

    # 3. 生成信号
    print("正在生成交易信号...")
    df = generate_signals(df)

    # 4. 回测（一年期）
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365)
    print(f"\n开始回测: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

    result = backtest_strategy(df, start_date, end_date)

    # 5. 输出结果
    print("\n" + "=" * 50)
    print("【ETF 波段策略回测报告】")
    print("=" * 50)
    print(f"回测区间: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print(f"标的代码: {ETF_CODE}.{MARKET.upper()}")
    print(f"初始资金: {INIT_CAPITAL:,.0f} 元")
    print(f"最终资金: {result['final_capital']:,.0f} 元")
    print(f"总收益率: {result['total_return']:.2f}%")
    print(f"年化收益率: {result['annual_return']:.2f}%")
    print(f"最大回撤: {result['max_drawdown']:.2f}%")
    print(f"夏普比率: {result['sharpe_ratio']:.2f}")
    print(f"交易次数: {result['total_trades']}")
    print(f"胜率: {result['win_rate']:.2f}%")
    print("=" * 50)

    # 6. 输出逐笔交易
    if len(result["trades"]) > 0:
        print("\n逐笔交易明细:")
        print("-" * 80)
        for i, trade in enumerate(result["trades"], 1):
            entry_date = trade["entry_date"].strftime("%Y-%m-%d")
            exit_date = trade.get("exit_date", "持仓中").strftime("%Y-%m-%d") if trade.get("exit_date") else "持仓中"
            entry_price = trade["entry_price"]
            exit_price = trade.get("exit_price", 0)
            profit = trade.get("profit", 0)
            profit_pct = trade.get("profit_pct", 0)
            print(
                f"{i:3d}. {entry_date} 买入 {entry_price:.3f} -> {exit_date} 卖出 {exit_price:.3f} | "
                f"收益: {profit:,.0f} 元 ({profit_pct:+.2f}%)"
            )
        print("-" * 80)

    # 7. 保存结果
    output_file = r"C:\Users\kongx\mystock\etf_backtest_result.csv"
    if len(result["trades"]) > 0:
        trades_df = pd.DataFrame(result["trades"])
        trades_df.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"\n交易明细已保存: {output_file}")


if __name__ == "__main__":
    main()
