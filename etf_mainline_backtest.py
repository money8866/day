import os, datetime, pandas as pd, numpy as np

TDX_PATH = r"C:\new_tdx\vipdoc"

# 38只行业ETF - 全部经过Tushare验证代码和名称
ETF_POOL = {
    '半导体': ('512480', 'sh'),
    '芯片': ('159995', 'sz'),
    '半导体设备': ('159516', 'sz'),
    '人工智能': ('159819', 'sz'),
    '软件': ('515230', 'sh'),
    '通信': ('515880', 'sh'),
    '消费电子': ('159732', 'sz'),
    '金融科技': ('159851', 'sz'),
    '游戏': ('159869', 'sz'),
    '新能源': ('516160', 'sh'),
    '光伏': ('515790', 'sh'),
    '储能': ('159566', 'sz'),
    '电池': ('159755', 'sz'),
    '新能源车': ('515030', 'sh'),
    '创新药': ('159992', 'sz'),
    '医疗器械': ('159883', 'sz'),
    '医药': ('512010', 'sh'),
    '军工': ('512660', 'sh'),
    '航空航天': ('159227', 'sz'),
    '机器人': ('562500', 'sh'),
    '有色金属': ('516650', 'sh'),
    '化工': ('159870', 'sz'),
    '煤炭': ('515220', 'sh'),
    '钢铁': ('515210', 'sh'),
    '电力': ('159611', 'sz'),
    '电网设备': ('561380', 'sh'),
    '消费': ('159928', 'sz'),
    '食品饮料': ('159736', 'sz'),
    '酒': ('512690', 'sh'),
    '家电': ('159996', 'sz'),
    '证券': ('512880', 'sh'),
    '银行': ('512800', 'sh'),
    '红利': ('515180', 'sh'),
    '黄金': ('518880', 'sh'),
    '沪深300': ('510300', 'sh'),
    '创业板': ('159915', 'sz'),
    '上证50': ('510050', 'sh'),
}

INIT_CAPITAL = 100000
COMMISSION = 0.0003
SLIPPAGE = 0.001


def parse_tdx(filepath):
    if not os.path.exists(filepath):
        return None
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = int.from_bytes(chunk[0:4], "little")
            close = int.from_bytes(chunk[16:20], "little") / 1000  # ETF divisor
            dt = datetime.datetime.strptime(str(date_int), "%Y%m%d")
            data.append({"trade_date": date_int, "date": dt, "close": close})
    if not data:
        return None
    return pd.DataFrame(data).sort_values("trade_date").reset_index(drop=True)


def run_mainline_strategy(etf_data, start_date, end_date, rebalance_days, mom_period, top_n):
    all_dates = set()
    for code, df in etf_data.items():
        for d in df["date"]:
            all_dates.add(d)
    dates = sorted(d for d in all_dates if start_date <= d <= end_date)
    if len(dates) < 100:
        return None

    for code, df in etf_data.items():
        df["mom"] = df["close"].pct_change(mom_period) * 100

    capital = INIT_CAPITAL
    holdings = {}
    trades = []
    equity_curve = []
    rebalance_count = 0

    for i, date in enumerate(dates):
        if i % rebalance_days == 0:
            rebalance_count += 1
            for code, shares in holdings.items():
                df = etf_data[code]
                row = df[df["date"] == date]
                if len(row) > 0:
                    capital += shares * row.iloc[0]["close"] * (1 - SLIPPAGE) * (1 - COMMISSION)
            holdings = {}

            scores = {}
            for code, df in etf_data.items():
                row = df[df["date"] == date]
                if len(row) > 0:
                    m = row.iloc[0].get("mom")
                    if pd.notna(m):
                        scores[code] = m

            if len(scores) >= top_n:
                ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
                per_capital = capital / top_n
                for code, score in ranked:
                    df = etf_data[code]
                    row = df[df["date"] == date]
                    if len(row) > 0:
                        buy_price = row.iloc[0]["close"]
                        shares = int(per_capital * 0.95 / (buy_price * (1 + SLIPPAGE)) / 100) * 100
                        if shares > 0:
                            capital -= shares * buy_price * (1 + SLIPPAGE) * (1 + COMMISSION)
                            holdings[code] = shares
                            trades.append({
                                "date": date, "code": code, "price": buy_price,
                                "score": score, "shares": shares, "period": rebalance_count,
                            })

        mv = capital
        for code, shares in holdings.items():
            df = etf_data[code]
            row = df[df["date"] == date]
            if len(row) > 0:
                mv += shares * row.iloc[0]["close"]
        equity_curve.append({"date": date, "equity": mv})

    last_date = dates[-1]
    for code, shares in holdings.items():
        row = etf_data[code][etf_data[code]["date"] == last_date]
        if len(row) > 0:
            capital += shares * row.iloc[0]["close"] * (1 - SLIPPAGE) * (1 - COMMISSION)

    total_return = (capital - INIT_CAPITAL) / INIT_CAPITAL * 100
    days = (dates[-1] - dates[0]).days
    annual_return = ((1 + total_return / 100) ** (365 / days) - 1) * 100 if days > 0 else 0

    eq = pd.DataFrame(equity_curve)
    eq["cummax"] = eq["equity"].cummax()
    eq["drawdown"] = (eq["equity"] - eq["cummax"]) / eq["cummax"] * 100
    max_dd = eq["drawdown"].min()
    eq["daily_ret"] = eq["equity"].pct_change()
    sharpe = eq["daily_ret"].mean() / eq["daily_ret"].std() * np.sqrt(252) if eq["daily_ret"].std() > 0 else 0

    return {
        "total_return": total_return, "annual_return": annual_return,
        "max_dd": max_dd, "sharpe": sharpe, "final_capital": capital,
        "trades": trades, "rebalances": rebalance_count, "top_n": top_n,
        "rebal_days": rebalance_days, "mom": mom_period,
    }


def main():
    print("=" * 75)
    print("  大周期主线持有策略回测 (38只行业ETF - 全验证)")
    print("=" * 75)

    etf_data = {}
    etf_names = {}
    available = []
    skipped = []

    for name, (code, market) in ETF_POOL.items():
        filepath = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
        df = parse_tdx(filepath)
        if df is not None and len(df) > 200:
            etf_data[code] = df
            etf_names[code] = name
            available.append((code, name))
        else:
            skipped.append((code, name))

    print(f"  可用ETF: {len(available)} 只")
    if skipped:
        print(f"  缺失: {len(skipped)} 只")
        for c, n in skipped:
            print(f"    - {n} ({c})")

    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365)
    print(f"  回测区间: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

    # benchmarks
    print(f"\n{'='*75}")
    print("  [基准] 买入持有收益排名:")
    print(f"{'='*75}")
    bh = {}
    for code, name in available:
        df = etf_data[code]
        rows = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        if len(rows) >= 2:
            ret = (rows.iloc[-1]["close"] - rows.iloc[0]["close"]) / rows.iloc[0]["close"] * 100
            bh[code] = ret

    for code, ret in sorted(bh.items(), key=lambda x: x[1], reverse=True):
        print(f"    {etf_names[code]:10s} {code:>8s}  {ret:+8.2f}%")

    best_bh = max(bh, key=bh.get)
    avg_bh = np.mean(list(bh.values()))
    print(f"\n    最强: {etf_names[best_bh]} ({bh[best_bh]:+.2f}%)")
    print(f"    等权平均: {avg_bh:+.2f}%")

    # parameter sweep
    print(f"\n{'='*75}")
    print("  [主线策略] 参数矩阵回测 (TOP 15):")
    print(f"{'='*75}")
    print(f"  {'持仓':>3s} | {'调仓':>4s} | {'动量':>4s} | {'收益率':>8s} | {'年化':>8s} | {'回撤':>8s} | {'夏普':>6s} | {'调仓':>3s} | vs最强")
    print(f"  {'-'*3}-+-{'-'*4}-+-{'-'*4}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*6}-+-{'-'*3}-+-{'-'*8}")

    results = []
    for top_n in [1, 2, 3, 5]:
        for rebal_days in [20, 40, 60, 80, 120]:
            for mom_period in [20, 40, 60, 120]:
                r = run_mainline_strategy(etf_data, start_date, end_date, rebal_days, mom_period, top_n)
                if r:
                    r["gap"] = r["total_return"] - bh[best_bh]
                    results.append(r)

    results.sort(key=lambda x: x["total_return"], reverse=True)

    for r in results[:15]:
        flag = "+" if r["gap"] >= 0 else ""
        print(
            f"  {r['top_n']:>2d}只  | {r['rebal_days']:>3d}天  | {r['mom']:>3d}天  | "
            f"{r['total_return']:>+7.2f}% | {r['annual_return']:>+7.2f}% | "
            f"{r['max_dd']:>7.2f}% | {r['sharpe']:>6.2f} | {r['rebalances']:>2d}次 | "
            f"{flag}{r['gap']:.2f}%"
        )

    # best detail
    best = results[0]
    print(f"\n{'='*75}")
    print(f"  [最优] {best['top_n']}只 | {best['rebal_days']}天调仓 | {best['mom']}日动量")
    print(f"  收益: {best['total_return']:+.2f}%  年化: {best['annual_return']:+.2f}%  "
          f"回撤: {best['max_dd']:.2f}%  夏普: {best['sharpe']:.2f}")
    print(f"{'='*75}")

    period_trades = {}
    for t in best["trades"]:
        period_trades.setdefault(t["period"], []).append(t)

    for p in sorted(period_trades.keys()):
        ts_list = period_trades[p]
        date_str = ts_list[0]["date"].strftime("%Y-%m-%d")
        codes = " + ".join(f"{etf_names[t['code']]}({t['score']:+.1f}%)" for t in ts_list)
        print(f"  第{p:>2d}期 {date_str}: {codes}")

    # best sharpe
    by_sharpe = sorted(results, key=lambda x: x["sharpe"], reverse=True)[0]
    print(f"\n{'='*75}")
    print(f"  [最高夏普] {by_sharpe['top_n']}只 | {by_sharpe['rebal_days']}天调仓 | {by_sharpe['mom']}日动量")
    print(f"  收益: {by_sharpe['total_return']:+.2f}%  年化: {by_sharpe['annual_return']:+.2f}%  "
          f"回撤: {by_sharpe['max_dd']:.2f}%  夏普: {by_sharpe['sharpe']:.2f}")
    print(f"{'='*75}")

    period_trades2 = {}
    for t in by_sharpe["trades"]:
        period_trades2.setdefault(t["period"], []).append(t)

    for p in sorted(period_trades2.keys()):
        ts_list = period_trades2[p]
        date_str = ts_list[0]["date"].strftime("%Y-%m-%d")
        codes = " + ".join(f"{etf_names[t['code']]}({t['score']:+.1f}%)" for t in ts_list)
        print(f"  第{p:>2d}期 {date_str}: {codes}")

    # fixed top=3 comparison
    print(f"\n{'='*75}")
    print("  [对比] 固定3只持仓:")
    print(f"{'='*75}")
    print(f"  {'调仓':>4s} | {'动量':>4s} | {'收益率':>8s} | {'年化':>8s} | {'回撤':>8s} | {'夏普':>6s} | {'调仓':>3s}")
    print(f"  {'-'*4}-+-{'-'*4}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*6}-+-{'-'*3}")

    for rebal_days in [20, 40, 60, 80, 120]:
        for mom_period in [20, 40, 60, 120]:
            r = run_mainline_strategy(etf_data, start_date, end_date, rebal_days, mom_period, 3)
            if r:
                gap = r["total_return"] - bh[best_bh]
                flag = "+" if gap >= 0 else ""
                print(
                    f"  {r['rebal_days']:>3d}天  | {r['mom']:>3d}天  | {r['total_return']:>+7.2f}% | "
                    f"{r['annual_return']:>+7.2f}% | {r['max_dd']:>7.2f}% | {r['sharpe']:>6.2f} | {r['rebalances']:>2d}次"
                )

    print(f"\n  最强单只: {etf_names[best_bh]} ({bh[best_bh]:+.2f}%)")
    print(f"  等权{len(available)}只: {avg_bh:+.2f}%")


if __name__ == "__main__":
    main()
