"""
完整测试：中科三环的筹码 + K线高点 双重判断
验证 get_chip_distribution 和修改后的 calc_tech_indicators 是否正常工作
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, get_chip_distribution, calc_tech_indicators, TRADE_DATE

ts_code = '000970.SZ'

print(f"=== 测试 {ts_code}（TRADE_DATE={TRADE_DATE}）===")

# 1. 获取K线数据
print("\n[1/3] 获取K线数据")
df = get_hist_data(ts_code)
if df is None:
    print("  ERROR: K线数据获取失败")
    sys.exit(1)
print(f"  行数: {len(df)}")
print(f"  日期范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
print(f"  最新收盘价: {df['close'].iloc[-1]:.2f}")

# 2. 获取筹码分布
print("\n[2/3] 获取筹码分布")
current_price = float(df['close'].iloc[-1])
chip = get_chip_distribution(ts_code, str(df['trade_date'].iloc[-1]), current_price)
if chip:
    for k, v in chip.items():
        print(f"  {k}: {v}")
else:
    print("  WARNING: 筹码数据为空")

# 3. 调用 calc_tech_indicators(ts_code=xxx, trade_date=xxx)
print("\n[3/3] calc_tech_indicators 结果")
tech = calc_tech_indicators(df, ts_code, str(df['trade_date'].iloc[-1]))
for k, v in tech.items():
    print(f"  {k}: {v}")

# 4. 文本输出（模拟Top10的文本输出逻辑）
print("\n=== 模拟 AI 文本输入（与Top10格式一致）===")
today_close = current_price
print(f"  【技术价位】MA5={tech.get('ma5', today_close):.2f}元 MA10={tech.get('ma10', today_close):.2f}元 MA20={tech.get('ma20', today_close):.2f}元({tech.get('ma20_trend','')}) MA60={tech.get('ma60', today_close):.2f}元({tech.get('ma60_trend','')})")
d20 = tech.get('dist_to_high20_pct', 0)
d60 = tech.get('dist_to_high60_pct', 0)
d20_desc = "已突破" if d20 < 0 else "未突破"
d60_desc = "已突破" if d60 < 0 else "未突破"
print(f"  【参考位-短线】20日高点={tech.get('high_20d', today_close):.2f}元({d20_desc}{abs(d20):.1f}%) 60日高点={tech.get('high_60d', today_close):.2f}元({d60_desc}{abs(d60):.1f}%)")
print(f"  【参考位-长线】120日高点={tech.get('high_120d', today_close):.2f}元 250日高点={tech.get('high_250d', today_close):.2f}元 全历史高点={tech.get('high_all', today_close):.2f}元")
pressure_desc = tech.get('upper_pressure_desc', '')
has_pressure = '有' if tech.get('has_upper_pressure', False) else '无'
print(f"  【压力判断】上方是否有套牢盘={has_pressure}；压力位描述={pressure_desc}")

chip_pct = tech.get('above_chips_pct', -1)
if chip_pct >= 0:
    avg_cost = tech.get('avg_cost', today_close)
    winner = tech.get('winner_rate', 0)
    p_desc = tech.get('pressure_desc', '')
    nearest_p = tech.get('nearest_pressure', 0)
    print(f"  【筹码分布】平均成本={avg_cost:.2f}元 上方套牢盘={chip_pct:.1f}% 盈利筹码={winner:.1f}% 最近压力位={nearest_p:.2f}元")
    print(f"  【筹码结论】{tech.get('breakout_status', '')}；压力等级={tech.get('pressure_level', 'K线估算')}；{p_desc}")

print("\n=== 对比：旧逻辑 vs 新逻辑 ===")
print(f"  旧逻辑（K线判断）：120日高点={tech.get('high_120d', 0):.2f}元 → 如果 {today_close} > {tech.get('high_120d', 0):.2f}，则误判'上方无套牢盘'")
print(f"  新逻辑（筹码判断）：上方套牢盘={chip_pct:.1f}%（>3%则标记有压力）")
if chip_pct >= 0 and chip_pct >= 3:
    print(f"  → 结论：新逻辑准确识别了上方套牢盘，不会误判")
else:
    print(f"  → 结论：上方套牢盘较轻，突破压力小")
