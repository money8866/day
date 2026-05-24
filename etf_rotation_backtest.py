import os, datetime, pandas as pd, numpy as np

# ==================== 主流行业ETF轮动策略 ====================
TDX_PATH = r"C:\new_tdx\vipdoc"

# 主流行业ETF池（代码, 市场, 名称）
ETF_POOL = [
    ("512880", "sh", "证券ETF"),
    ("512800", "sh", "银行ETF"),
    ("512660", "sh", "军工ETF"),
    ("512010", "sh", "医药ETF"),
    ("512480", "sh", "半导体ETF"),
    ("512400", "sh", "有色ETF"),
    ("512580", "sh", "环保ETF"),
    ("512700", "sh", "新能源车ETF"),
    ("512690", "sh", "酒ETF"),
    ("512200", "sh", "房地产ETF"),
    ("512560", "sh", "中证500ETF"),
    ("510300", "sh", "沪深300ETF"),
    ("510050", "sh", "上证50ETF"),
    ("159825", "sz", "农业ETF"),
    ("159915", "sz", "创业板ETF"),
    ("159996", "sz", "家电ETF"),
    ("159813", "sz", "芯片ETF"),
    ("159901", "sz", "证券ETF深"),
    ("159949", "sz", "创新药ETF"),
    ("159919", "sz", "沪深300ETF深"),
]

# 策略参数
MOM_SHORT = 10   # 短期动量（10日涨幅）
MOM_LONG = 20    # 长期动量（20日涨幅）
INIT_CAPITAL = 100000
COMMISSION = 0.0003
SLIPPAGE = 0.001
COOLDOWN = 5     # 轮动冷却期（天）


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
            close = int.from_bytes(chunk[16:20], "little") / 100
            volume = int.from_bytes(chunk[20:24], "little")
            dt = datetime.datetime.strptime(str(date_int), "%Y%m%d")
            data.append({"trade_date": date_int, "date": dt, "close": close, "volume": volume})
    if not data:
        return None
    df = pd.DataFrame(data).sort_values("trade_date").reset_index(drop=True)
    return df


def calc_momentum(df, short, long_):
    """计算动量得分"""
    df["mom_short"] = df["close"].pct_change(short) * 100
    df["mom_long"] = df["close"].pct_change(long_) * 100
    # 综合动量 = 短期60% + 长期40%
    df["momentum_score"] = df["mom_short"] * 0.6 + df["mom_long"] * 0.4
    return df


def run_rotation(etf_data, start_date, end_date, cooldown=COOLDOWN):
    """
    轮动策略：
    - 每天计算所有ETF的动量得分
    - 选择动量最高的ETF持有
    - 如果当前持有ETF动量排名跌出前50%或低于0，切换到排名第一的
    - 冷却期内不切换
    """
    # 合并所有ETF日期
    all_dates = set()
    for code, df in etf_data.items():
        for d in df["date"]:
            all_dates.add(d)
    
    dates = sorted(all_dates)
    dates = [d for d in dates if start_date <= d <= end_date]
    
    if len(dates) < 30:
        return None
    
    capital = INIT_CAPITAL
    position_code = None  # 当前持有的ETF代码
    entry_price = 0
    shares = 0
    last_switch_date = None
    trades = []
    equity_curve = []
    
    for i, date in enumerate(dates):
        # 当天各ETF动量
        scores = {}
        prices = {}
        for code, df in etf_data.items():
            row = df[df["date"] == date]
            if len(row) == 0:
                continue
            row = row.iloc[0]
            if pd.isna(row.get("momentum_score")):
                continue
            scores[code] = row["momentum_score"]
            prices[code] = row["close"]
        
        if len(scores) < 3:
            # 计算当日权益
            mv = shares * prices.get(position_code, entry_price) if position_code else capital
            equity_curve.append({"date": date, "equity": mv})
            continue
        
        # 排名
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_code = ranked[0][0]
        best_score = ranked[0][1]
        my_score = scores.get(position_code, -999)
        my_rank = next((i for i, (c, s) in enumerate(ranked) if c == position_code), 999)
        total = len(ranked)
        
        # 切换条件
        need_switch = False
        if position_code is None:
            # 空仓：只要最强ETF动量>0就买入
            if best_score > 0:
                need_switch = True
        else:
            # 持仓中：冷却期内不切换
            if last_switch_date is None or (date - last_switch_date).days >= cooldown:
                # 条件1：当前ETF动量排名跌出前50%
                if my_rank >= total // 2:
                    need_switch = True
                # 条件2：当前ETF动量<0 且有更好的
                if my_score < 0 and best_score > 0:
                    need_switch = True
                # 条件3：有ETF动量远超当前（>当前+5%）
                if best_score > my_score + 5:
                    need_switch = True
        
        # 执行切换
        if need_switch and best_code != position_code:
            # 先卖
            if position_code and position_code in prices:
                sell_price = prices[position_code]
                if shares > 0:
                    sell_amt = shares * sell_price * (1 - SLIPPAGE) * (1 - COMMISSION)
                    capital += sell_amt
            
            # 再买
            buy_price = prices[best_code]
            buy_amount = capital * 0.95
            shares = int(buy_amount / (buy_price * (1 + SLIPPAGE)) / 100) * 100
            if shares > 0:
                cost = shares * buy_price * (1 + SLIPPAGE) * (1 + COMMISSION)
                capital -= cost
                
                # 记录上一笔交易
                if position_code and "entry_price" in dir() and entry_price > 0:
                    pass  # 简化
                
                entry_price = buy_price
                position_code = best_code
                last_switch_date = date
                trades.append({
                    "date": date,
                    "action": "BUY",
                    "code": best_code,
                    "price": buy_price,
                    "score": scores.get(best_code, 0),
                    "capital_after": capital,
                    "shares": shares,
                })
        
        # 计算当日权益
        if position_code and position_code in prices:
            mv = shares * prices[position_code]
        else:
            mv = capital
        equity_curve.append({"date": date, "equity": mv})
    
    # 最后平仓
    if position_code and shares > 0:
        last_date = dates[-1]
        for code, df in etf_data.items():
            if code == position_code:
                row = df[df["date"] == last_date]
                if len(row) > 0:
                    sell_price = row.iloc[0]["close"]
                    sell_amt = shares * sell_price * (1 - SLIPPAGE) * (1 - COMMISSION)
                    capital += sell_amt
                    trades.append({
                        "date": last_date, "action": "SELL",
                        "code": position_code, "price": sell_price,
                    })
                    break
        final_capital = capital
    else:
        final_capital = capital
    
    # 计算指标
    eq = pd.DataFrame(equity_curve)
    total_return = (final_capital - INIT_CAPITAL) / INIT_CAPITAL * 100
    days = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    annual_return = ((1 + total_return/100) ** (365/days) - 1) * 100
    
    eq["cummax"] = eq["equity"].cummax()
    eq["drawdown"] = (eq["equity"] - eq["cummax"]) / eq["cummax"] * 100
    max_dd = eq["drawdown"].min()
    
    eq["daily_ret"] = eq["equity"].pct_change()
    sharpe = eq["daily_ret"].mean() / eq["daily_ret"].std() * np.sqrt(252) if eq["daily_ret"].std() > 0 else 0
    
    # 持仓分布
    buy_trades = [t for t in trades if t["action"] == "BUY"]
    
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "final_capital": final_capital,
        "switches": len(buy_trades),
        "trades": trades,
        "equity_curve": equity_curve,
    }


def run_buy_hold(etf_data, start_date, end_date, code):
    """买入某个ETF持有不动的基准"""
    df = etf_data[code]
    rows_start = df[df["date"] >= start_date]
    if len(rows_start) == 0:
        return None
    entry = rows_start.iloc[0]["close"]
    
    rows_end = df[df["date"] <= end_date]
    if len(rows_end) == 0:
        return None
    exit_ = rows_end.iloc[-1]["close"]
    
    ret = (exit_ - entry) / entry * 100
    return ret


def main():
    print("=" * 70)
    print("  行业ETF动量轮动策略回测")
    print("=" * 70)
    
    # 1. 读取数据
    etf_data = {}
    etf_names = {}
    available = []
    
    for code, market, name in ETF_POOL:
        filepath = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
        df = parse_tdx(filepath)
        if df is not None and len(df) > 200:
            df = calc_momentum(df, MOM_SHORT, MOM_LONG)
            etf_data[code] = df
            etf_names[code] = name
            available.append((code, name))
            print(f"  [OK] {code} {name} ({len(df)} bars, {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')})")
        else:
            print(f"  [SKIP] {code} {name} (no data)")
    
    print(f"\n  有效ETF数量: {len(available)}")
    
    # 2. 回测区间
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365)
    print(f"  回测区间: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    
    # 3. 各ETF买入持有收益
    print("\n" + "-" * 70)
    print("  [基准] 各ETF买入持有收益:")
    print("-" * 70)
    bh_returns = {}
    for code, name in available:
        ret = run_buy_hold(etf_data, start_date, end_date, code)
        if ret is not None:
            bh_returns[code] = ret
            print(f"    {code:8s} {name:12s}  {ret:+8.2f}%")
    
    # 找最强ETF
    if bh_returns:
        best_bh = max(bh_returns, key=bh_returns.get)
        print(f"\n    最强ETF: {best_bh} {etf_names[best_bh]} ({bh_returns[best_bh]:+.2f}%)")
    
    # 4. 运行轮动策略
    print("\n" + "-" * 70)
    print("  [策略] 动量轮动回测:")
    print("-" * 70)
    
    result = run_rotation(etf_data, start_date, end_date, cooldown=5)
    
    if result:
        print(f"    总收益率:     {result['total_return']:+8.2f}%")
        print(f"    年化收益率:   {result['annual_return']:+8.2f}%")
        print(f"    最大回撤:     {result['max_dd']:8.2f}%")
        print(f"    夏普比率:     {result['sharpe']:8.2f}")
        print(f"    最终资金:     {result['final_capital']:>10,.0f} 元")
        print(f"    轮动次数:     {result['switches']}")
        
        # 对比最强买入持有
        if bh_returns:
            gap = result["total_return"] - bh_returns[best_bh]
            flag = "+" if gap >= 0 else ""
            print(f"    vs最强持有:   {flag}{gap:.2f}% ({best_bh})")
        
        # 轮动明细
        buy_trades = [t for t in result["trades"] if t["action"] == "BUY"]
        if buy_trades:
            print(f"\n    轮动明细:")
            for i, t in enumerate(buy_trades):
                code = t["code"]
                print(f"      {i+1:2d}. {t['date'].strftime('%Y-%m-%d')} -> 买入 {code} {etf_names.get(code, '')} @ {t['price']:.3f} (动量:{t['score']:+.1f})")
    
    # 5. 不同冷却期对比
    print("\n" + "-" * 70)
    print("  [参数优化] 冷却期对比:")
    print("-" * 70)
    print(f"    {'冷却期':>6s} | {'收益率':>8s} | {'年化':>8s} | {'回撤':>8s} | {'夏普':>6s} | {'轮动':>4s} | vs最强持有")
    print(f"    {'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*6}-+-{'-'*4}-+-{'-'*10}")
    
    for cd in [3, 5, 10, 15, 20, 30]:
        r = run_rotation(etf_data, start_date, end_date, cooldown=cd)
        if r and bh_returns:
            gap = r["total_return"] - bh_returns[best_bh]
            flag = "+" if gap >= 0 else ""
            print(f"    {cd:>4d}天  | {r['total_return']:>+7.2f}% | {r['annual_return']:>+7.2f}% | {r['max_dd']:>7.2f}% | {r['sharpe']:>6.2f} | {r['switches']:>3d}次  | {flag}{gap:.2f}%")
    
    # 6. 等权持有基准
    if bh_returns:
        avg_bh = np.mean(list(bh_returns.values()))
        print(f"\n    等权持有平均: {avg_bh:+.2f}%")
        if result:
            gap2 = result["total_return"] - avg_bh
            print(f"    轮动 vs 等权: {gap2:+.2f}%")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
