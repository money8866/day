"""回溯159516历史上什么时候20日动量排名第1"""
import tushare as ts, pandas as pd, time

TS_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '黄金': '518880', '沪深300': '510300', '创业板': '159915',
    '上证50': '510050',
}

ts.set_token(TS_TOKEN)
pro = ts.pro_api()

codes_ts = {}
for code in ETF_POOL.values():
    codes_ts[code] = code + '.SH' if code.startswith(('5', '6')) else code + '.SZ'

# Fetch daily data
print("获取数据...")
all_data = {}
for name, code in ETF_POOL.items():
    ts_code = codes_ts[code]
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date='20260101',
                            fields='ts_code,trade_date,close')
        if df is not None and len(df) > 0:
            df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
            df = df.sort_values('trade_date').reset_index(drop=True)
            all_data[code] = df
        time.sleep(0.25)
    except:
        time.sleep(0.5)

# Pivot
dates = sorted(set(d for df in all_data.values() for d in df['trade_date'].tolist()))
price_df = pd.DataFrame(index=dates)
for code, df in all_data.items():
    price_df[code] = df.set_index('trade_date')['close']

# 20-day momentum
mom = price_df.pct_change(20) * 100

# Find when 159516 was rank #1
target = '159516'
rankings = mom.rank(axis=1, ascending=False, method='min')[target]
rank1_mask = rankings == 1
rank1_dates = mom.index[rank1_mask].tolist()

latest_date = price_df.index[-1]
latest_price = price_df[target].iloc[-1]

print("=" * 50)
print("159516 半导体设备 动量排名第1的日期")
print("=" * 50)
for d in rank1_dates:
    m = mom.loc[d, target]
    p = price_df.loc[d, target]
    print("  %s  动量:%+.2f%%  收盘:%.3f" % (d.strftime("%Y-%m-%d"), m, p))

if not rank1_dates:
    print("  (无)")
else:
    # Find the most recent rank-1 date
    last_rank1 = rank1_dates[-1]
    buy_price = price_df.loc[last_rank1, target]
    print("\n--- 最优建仓分析 ---")
    print("最佳买入日: %s" % last_rank1.strftime("%Y-%m-%d"))
    print("买入价格: %.3f" % buy_price)

    # Trade days since
    trade_days_since = [d for d in dates if d > last_rank1 and d <= latest_date]
    passed = len(trade_days_since)
    remain = max(0, 60 - passed)
    print("已过交易日: %d/60" % passed)
    print("距调仓: %d个交易日" % remain)

    pnl = (latest_price - buy_price) / buy_price * 100
    print("现价: %.3f  持仓收益: %+.2f%%" % (latest_price, pnl))

    # Est rebalance date
    if remain > 0:
        all_since = [d for d in dates if d > last_rank1]
        if len(all_since) >= 60:
            rebal = all_since[59]
            print("预计调仓日: %s" % rebal.strftime("%Y-%m-%d"))
        else:
            # extrapolate: ~4.5 trade days per week
            from datetime import timedelta
            est = latest_date + timedelta(days=int(remain * 1.4))
            print("预计调仓日(估): %s" % est.strftime("%Y-%m-%d"))

    # Also show: what if we use the FIRST time it became #1 in recent streak
    print("\n--- 连续排名第1的区间 ---")
    in_streak = False
    streak_start = None
    for d in dates:
        if d in rank1_dates:
            if not in_streak:
                streak_start = d
                in_streak = True
        else:
            if in_streak:
                streak_end = prev_d
                print("  %s ~ %s  (%d天)" % (
                    streak_start.strftime("%Y-%m-%d"),
                    streak_end.strftime("%Y-%m-%d"),
                    len([x for x in dates if streak_start <= x <= streak_end])
                ))
                in_streak = False
        prev_d = d
    if in_streak:
        print("  %s ~ 至今" % streak_start.strftime("%Y-%m-%d"))
