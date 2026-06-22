"""
测试：中科三环（000970.SZ）历史高点计算
验证修复后的 calc_tech_indicators 是否正确输出长期高点
"""
import sys, os
import pandas as pd

# 添加当前路径到 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import calc_tech_indicators, get_hist_data

ts_code = '000970.SZ'
print(f"=== 正在获取 {ts_code} 历史数据（从 20230101 起）===")

# 直接获取数据（可能触发tushare API，有Token才能成功）
df = None
try:
    df = get_hist_data(ts_code)
    print(f"获取到 {len(df)} 行历史数据")
    print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
except Exception as e:
    print(f"get_hist_data 失败: {e}")

# 尝试读取缓存文件
cache_file = os.path.join(r'd:\mystock\solo\cache_daily', ts_code + '.csv')
if df is None and os.path.exists(cache_file):
    print(f"\n从缓存文件读取: {cache_file}")
    df = pd.read_csv(cache_file)
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date').reset_index(drop=True)
    print(f"缓存中有 {len(df)} 行数据")
    print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")

if df is None or len(df) < 20:
    print("数据不足，无法测试")
    sys.exit(1)

print(f"\n最近5日数据:")
print(df[['trade_date','open','high','low','close','vol']].tail().to_string())

# 测试 calc_tech_indicators
print(f"\n=== 测试 calc_tech_indicators ===")
tech = calc_tech_indicators(df)
for k, v in tech.items():
    print(f"  {k}: {v}")

# 手动验证高点是否合理
high = df['high']
print(f"\n=== 手动验证（排除当天） ===")
print(f"20日高点: {high.iloc[:-1].tail(20).max():.2f}")
print(f"60日高点: {high.iloc[:-1].tail(60).max():.2f}")
print(f"120日高点: {high.iloc[:-1].tail(120).max():.2f}")
print(f"250日高点: {high.iloc[:-1].tail(250).max():.2f}")
print(f"全历史高点: {high.iloc[:-1].max():.2f}")

# 是否用户报告中出现的14.85元数据问题
print(f"\n=== 用户报告中错误数字验证 ===")
print(f"若输出中曾出现'14.85元'，则该数值应出现在历史 high 数据中")
near_1485 = df[(df['high'] >= 14.80) & (df['high'] <= 14.90)]
if len(near_1485) > 0:
    print(f"high 接近 14.85 的交易日: {len(near_1485)} 个")
    print(near_1485[['trade_date','high','close']].tail().to_string())
else:
    print(f"high 列中无接近 14.85 的值 → 证明'14.85'是编造数据")

# 当前价上方有多少历史高点
current_price = float(df['close'].iloc[-1])
print(f"\n当前价 {current_price:.2f} 元上方的历史 high 统计:")
above = df[df['high'] > current_price]
print(f"  共有 {len(above)} 个交易日 high > {current_price:.2f}")
if len(above) > 0:
    print(f"  最高 high: {above['high'].max():.2f}")
    print(f"  最近一个 high > 当前价的交易日: {above['trade_date'].iloc[-1]}, high={above['high'].iloc[-1]:.2f}")
