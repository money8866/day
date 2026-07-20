import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from multi_factor_picker.data_fetcher import DataFetcher
from daily_timing import score_stock, load_timing_params

with open('.env') as f:
    token = [line.split('=',1)[1].strip().strip("'\"") for line in f if line.startswith('TUSHARE_TOKEN')][0]

config = {'cache': {'enabled': True, 'dir': 'cache'}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
fetcher = DataFetcher(token, config)

# 获取002440日线
daily = fetcher.get_daily_history('20260720', 120)
stock = daily[daily['ts_code'] == '002440.SZ'].copy()
stock = stock.sort_values('trade_date').reset_index(drop=True)

# 重点看7月15-17日
focus_dates = ['20260715', '20260716', '20260717', '20260718', '20260719', '20260720']

params = load_timing_params()

for date in focus_dates:
    df_up_to = stock[stock['trade_date'] <= date]
    if len(df_up_to) < 20:
        print(f'{date}: 数据不足')
        continue
    row = df_up_to.iloc[-1]
    result = score_stock('002440.SZ', df_up_to, params)
    print(f"{date} | 收{row['close']:.2f} 涨{row['pct_chg']:.2f}% | 量{row['vol']/10000:.0f}万 | "
          f"综合{result['composite_score']:.0f} 趋势{result['trend_score']:.0f} 低吸{result['dip_score']:.0f} 突破{result['breakout_score']:.0f} | "
          f"{result['signal_level']} | {result['signals'][:3]}")

# 分析7月15日的特征
print("\n=== 20260715 详细分析 ===")
df15 = stock[stock['trade_date'] <= '20260715']
c = df15['close']
v = df15['vol']
h = df15['high']
l = df15['low']

print(f"收盘价: {c.iloc[-1]:.2f}")
print(f"涨幅: {df15.iloc[-1]['pct_chg']:.2f}%")

# MA
from daily_timing import _ma, _macd, _volume_ratio
ma5 = _ma(c, 5); ma10 = _ma(c, 10); ma20 = _ma(c, 20)
print(f"MA5: {ma5:.2f} MA10: {ma10:.2f} MA20: {ma20:.2f}")
print(f"MA5>MA10>MA20: {ma5 > ma10 > ma20}")
print(f"MA5>MA20: {ma5 > ma20}")

# MACD
dif, dea, bar = _macd(c)
print(f"MACD DIF: {dif:.3f} DEA: {dea:.3f} BAR: {bar:.3f}")
print(f"DIF>DEA>0: {dif > dea > 0}")
print(f"DIF>DEA: {dif > dea}")

# 量比
vr = _volume_ratio(v)
print(f"量比: {vr:.2f}")

# 20日涨幅
chg20 = ((c.iloc[-1] / c.iloc[-min(21, len(c))]) - 1) * 100 if len(c) >= 21 else 0
print(f"20日涨幅: {chg20:.2f}%")

# 回撤
dd = (float(c.tail(60).max()) - float(c.iloc[-1])) / max(float(c.tail(60).max()), 1) * 100
print(f"回撤: {dd:.1f}%")

# 7月17日大盘大跌日
print("\n=== 20260717 大盘大跌日 ===")
df17 = stock[stock['trade_date'] <= '20260717']
row17 = df17.iloc[-1]
print(f"收盘价: {row17['close']:.2f} 涨跌幅: {row17['pct_chg']:.2f}%")

# 获取大盘数据
try:
    index_daily = fetcher.get_index_daily('000001.SH', '20260701', '20260720')
    index_0717 = index_daily[index_daily['trade_date'] == '20260717']
    if len(index_0717) > 0:
        print(f"上证指数7/17: {index_0717.iloc[0]['close']:.2f} 涨跌幅: {index_0717.iloc[0]['pct_chg']:.2f}%")
        print(f"个股vs大盘: 002440 {row17['pct_chg']:.2f}% vs 沪指 {index_0717.iloc[0]['pct_chg']:.2f}%")
except Exception as e:
    print(f"大盘数据获取失败: {e}")

# 连续几天走势
print("\n=== 7/15-7/20 走势 ===")
for _, row in stock[stock['trade_date'] >= '20260715'].iterrows():
    print(f"{row['trade_date']} | 收{row['close']:.2f} | 涨{row['pct_chg']:.2f}% | 量{row['vol']/10000:.0f}万")
