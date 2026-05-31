import os
import datetime
import pandas as pd
import numpy as np

# ==================== 配置区域 ====================
TDX_PATH = r"C:\new_tdx\vipdoc"
ETF_CODE = "159531"
MARKET = "sz"
ATR_PERIOD = 14
ATR_MULTIPLIER = 3.0
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
    return df


def backtest_all_variants(df, start_date, end_date):
    """同时回测多个策略变体 + 买入持有基准"""
    df_bt = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy().reset_index(drop=True)
    if len(df_bt) == 0:
        raise ValueError("回测区间内无数据")

    # === 基准：买入持有 ===
    bh_entry = df_bt.iloc[0]["close"]
    bh_exit = df_bt.iloc[-1]["close"]
    bh_return = (bh_exit - bh_entry) / bh_entry * 100

    # === 策略变体 ===
    variants = [
        {"name": "V0-原版", "buy_mom": True, "buy_near_high": True, "buy_near_high_pct": 0.98,
         "sell_mom_neg": True, "sell_atr_stop": True, "atr_mult": 2.0},
        {"name": "V1-放宽止损", "buy_mom": True, "buy_near_high": True, "buy_near_high_pct": 0.98,
         "sell_mom_neg": True, "sell_atr_stop": True, "atr_mult": 3.0},
        {"name": "V2-去掉动能卖", "buy_mom": True, "buy_near_high": True, "buy_near_high_pct": 0.98,
         "sell_mom_neg": False, "sell_atr_stop": True, "atr_mult": 2.0},
        {"name": "V3-放宽止损+去动能卖", "buy_mom": True, "buy_near_high": True, "buy_near_high_pct": 0.98,
         "sell_mom_neg": False, "sell_atr_stop": True, "atr_mult": 3.0},
        {"name": "V4-只买不卖(ATR3)", "buy_mom": True, "buy_near_high": False, "buy_near_high_pct": 1.0,
         "sell_mom_neg": False, "sell_atr_stop": True, "atr_mult": 3.0},
        {"name": "V5-纯ATR3追踪", "buy_mom": False, "buy_near_high": False, "buy_near_high_pct": 1.0,
         "sell_mom_neg": False, "sell_atr_stop": True, "atr_mult": 3.0},
        {"name": "V6-纯ATR2追踪", "buy_mom": False, "buy_near_high": False, "buy_near_high_pct": 1.0,
         "sell_mom_neg": False, "sell_atr_stop": True, "atr_mult": 2.0},
        {"name": "V7-纯ATR4追踪", "buy_mom": False, "buy_near_high": False, "buy_near_high_pct": 1.0,
         "sell_mom_neg": False, "sell_atr_stop": True, "atr_mult": 4.0},
    ]

    results = []

    for v in variants:
        capital = INIT_CAPITAL
        position = 0
        shares = 0
        trades = []
        equity_curve = []
        stop_line_col = f"stop_{v['atr_mult']}"

        # 计算对应ATR倍数的止损线
        df_bt[stop_line_col] = df_bt["highest_close"] - (df_bt["atr"] * v["atr_mult"])

        for idx, row in df_bt.iterrows():
            close = row["close"]
            mom = row["momentum"]
            stop = row[stop_line_col]
            highest_c = row["highest_close"]

            market_value = shares * close if position == 1 else capital
            equity_curve.append(market_value)

            if pd.isna(mom) or pd.isna(stop):
                continue

            if position == 0:
                buy = True
                if v["buy_mom"] and mom <= 0:
                    buy = False
                if v["buy_near_high"] and close <= highest_c * v["buy_near_high_pct"]:
                    buy = False
                if buy:
                    buy_amount = capital * 0.95
                    shares = int(buy_amount / (close * (1 + SLIPPAGE)) / 100) * 100
                    if shares > 0:
                        cost = shares * close * (1 + SLIPPAGE) * (1 + COMMISSION)
                        capital -= cost
                        position = 1
                        trades.append({"entry_price": close, "entry_date": row["date"], "shares": shares})
            elif position == 1:
                sell = False
                if v["sell_mom_neg"] and mom < 0:
                    sell = True
                if v["sell_atr_stop"] and close < stop:
                    sell = True
                if sell and len(trades) > 0:
                    trade = trades[-1]
                    sell_amount = shares * close * (1 - SLIPPAGE) * (1 - COMMISSION)
                    capital += sell_amount
                    profit_pct = (close - trade["entry_price"]) / trade["entry_price"] * 100
                    trade.update({"exit_price": close, "exit_date": row["date"], "profit_pct": profit_pct})
                    position = 0
                    shares = 0

        # 末尾平仓
        if position == 1 and len(trades) > 0:
            trade = trades[-1]
            last_close = df_bt.iloc[-1]["close"]
            profit_pct = (last_close - trade["entry_price"]) / trade["entry_price"] * 100
            trade.update({"exit_price": last_close, "exit_date": df_bt.iloc[-1]["date"], "profit_pct": profit_pct})
            capital += shares * last_close * (1 - SLIPPAGE) * (1 - COMMISSION)

        total_return = (capital - INIT_CAPITAL) / INIT_CAPITAL * 100

        # 最大回撤
        eq = pd.Series(equity_curve)
        cummax = eq.cummax()
        dd = (eq - cummax) / cummax * 100
        max_dd = dd.min() if len(dd) > 0 else 0

        # 夏普
        daily_ret = eq.pct_change().dropna()
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0

        win = sum(1 for t in trades if t.get("profit_pct", 0) > 0)
        total = len(trades)

        results.append({
            "name": v["name"],
            "return": total_return,
            "trades": total,
            "win_rate": win / total * 100 if total > 0 else 0,
            "max_dd": max_dd,
            "sharpe": sharpe,
            "final_capital": capital,
        })

    return results, bh_return


def main():
    tdx_file = os.path.join(TDX_PATH, MARKET, "lday", f"{MARKET}{ETF_CODE}.day")
    print(f"正在读取通达信数据: {tdx_file}")

    try:
        df = parse_tdx_day_file(tdx_file)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        return

    print(f"数据读取完成，共 {len(df)} 条记录")

    df = calculate_indicators(df)

    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365)
    print(f"回测区间: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

    results, bh_return = backtest_all_variants(df, start_date, end_date)

    print("\n" + "=" * 80)
    print("【ETF 策略多版本对比回测】")
    print("=" * 80)
    print(f"标的: {ETF_CODE}.{MARKET.upper()}")
    print(f"区间: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print(f"初始资金: {INIT_CAPITAL:,.0f} 元")
    print()

    # 买入持有
    print(f"  [基准] 买入持有:  +{bh_return:.2f}%")
    print("-" * 80)

    # 策略变体
    for r in results:
        vs_bh = r["return"] - bh_return
        flag = "+" if vs_bh >= 0 else ""
        print(
            f"  {r['name']:20s} | 收益: {r['return']:+7.2f}% | 交易: {r['trades']:2d}笔 | "
            f"胜率: {r['win_rate']:5.1f}% | 回撤: {r['max_dd']:6.2f}% | 夏普: {r['sharpe']:.2f} | "
            f"vs持有: {flag}{vs_bh:.2f}%"
        )

    print("-" * 80)

    # 找最优
    best = max(results, key=lambda x: x["return"])
    print(f"\n  最优策略: {best['name']} (收益 {best['return']:+.2f}%)")
    print(f"  基准收益: 买入持有 (+{bh_return:.2f}%)")

    if best["return"] >= bh_return:
        print("  结论: 策略跑赢买入持有")
    else:
        gap = bh_return - best["return"]
        print(f"  结论: 所有策略均跑输买入持有 {gap:.2f}%")


if __name__ == "__main__":
    main()
