"""
详细测试：中科三环筹码分布 —— 计算当前价上方/下方累积筹码
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tushare_quant import pro

ts_code = '000970.SZ'
trade_date = '20260618'
current_price = 14.94  # 20260618 收盘价

# ===== 筹码分布 =====
df = pro.cyq_chips(ts_code=ts_code, trade_date=trade_date)
print(f"=== cyq_chips {ts_code} {trade_date}（共 {len(df)} 行）===")
if df is not None and len(df) > 0:
    above = df[df['price'] > current_price]
    below = df[df['price'] <= current_price]
    above_pct = above['percent'].sum()
    below_pct = below['percent'].sum()
    print(f"  当前价: {current_price} 元")
    print(f"  上方套牢盘: {above_pct:.2f}% （共 {len(above)} 个价格段）")
    print(f"  下方获利盘: {below_pct:.2f}%")
    print(f"  总和校验: {above_pct + below_pct:.2f}%")

    # 上方最近的三个压力峰
    above_peaks = above.nlargest(5, 'percent')
    print(f"\n  上方压力位（当前价上方最大的5个筹码峰）:")
    for _, row in above_peaks.iterrows():
        print(f"    {row['price']:.1f}元: {row['percent']:.2f}%")

    # 下方支撑位
    below_peaks = below.nlargest(5, 'percent')
    print(f"\n  下方支撑位（当前价下方最大的5个筹码峰）:")
    for _, row in below_peaks.iterrows():
        print(f"    {row['price']:.1f}元: {row['percent']:.2f}%")

    # 全量筹码峰（排序后的完整图）
    top10 = df.nlargest(10, 'percent')
    print(f"\n  全量前10大筹码密集区:")
    for _, row in top10.iterrows():
        mark = "↓↓" if row['price'] < current_price else "↑↑"
        print(f"    {row['price']:.1f}元: {row['percent']:.2f}% {mark}")

# ===== 筹码及胜率 =====
perf = pro.cyq_perf(ts_code=ts_code, trade_date=trade_date)
print(f"\n=== cyq_perf {ts_code} {trade_date} ===")
if perf is not None and len(perf) > 0:
    row = perf.iloc[0]
    print(f"  历史最低: {row['his_low']:.1f} 元")
    print(f"  历史最高: {row['his_high']:.1f} 元")
    print(f"  5%成本分位: {row['cost_5pct']:.2f} 元")
    print(f"  15%成本分位: {row['cost_15pct']:.2f} 元")
    print(f"  50%成本分位(中位数): {row['cost_50pct']:.2f} 元")
    print(f"  85%成本分位: {row['cost_85pct']:.2f} 元")
    print(f"  95%成本分位: {row['cost_95pct']:.2f} 元")
    print(f"  加权平均成本: {row['weight_avg']:.2f} 元")
    print(f"  胜率(盈利筹码占比): {row['winner_rate']:.2f}%")
    print(f"  套牢盘 = 100 - {row['winner_rate']:.2f} = {100 - row['winner_rate']:.2f}%")
    print(f"  现价相对平均成本: {((current_price - row['weight_avg']) / row['weight_avg']) * 100:+.2f}%")
