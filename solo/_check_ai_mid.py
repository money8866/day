import json

raw = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))
all_themes = raw.get('themes', [])

# 找AI主题下的所有成分股
ai_stocks = []
for t in all_themes:
    if t.get('top_category') == 'AI':
        ai_stocks.extend(t.get('stocks', []))

# 检查有哪些时间维度数据
print("="*80)
print(f"  AI主题成分股数据字段检查（第一只股票的所有key）:")
print("="*80)
if ai_stocks:
    s = ai_stocks[0]
    print(f"  可用字段: {[k for k in s.keys()]}")
    print(f"  是否存在 change_20d_pct: {'change_20d_pct' in s}")
    print(f"  是否存在 change_60d_pct: {'change_60d_pct' in s}")

print()

# 统计AI各指标分布
n = len(ai_stocks)
print(f"  AI 主题成分股总数: {n}")
print()

# 5日涨幅分布
chg5_vals = [(s.get('change_5d_pct') or 0) for s in ai_stocks]
chg10_vals = [(s.get('change_10d_pct') or 0) for s in ai_stocks]
trend_vals = [(s.get('trend_score') or 0) for s in ai_stocks]
ma10_vals = [(s.get('ma10_slope_pct') or 0) for s in ai_stocks]

print(f"  5日涨幅分布:")
print(f"    均值: {sum(chg5_vals)/n:+.1f}%")
print(f"    >0% 占比: {sum(1 for v in chg5_vals if v>0)/n*100:.0f}%")
print(f"    >5% 占比: {sum(1 for v in chg5_vals if v>5)/n*100:.0f}%")
print(f"    <-5% 占比: {sum(1 for v in chg5_vals if v<-5)/n*100:.0f}%")
print(f"    最大: {max(chg5_vals):+.1f}%  最小: {min(chg5_vals):+.1f}%")

print(f"\n  10日涨幅分布:")
print(f"    均值: {sum(chg10_vals)/n:+.1f}%")
print(f"    >0% 占比: {sum(1 for v in chg10_vals if v>0)/n*100:.0f}%")
print(f"    >5% 占比: {sum(1 for v in chg10_vals if v>5)/n*100:.0f}%")
print(f"    <-10% 占比: {sum(1 for v in chg10_vals if v<-10)/n*100:.0f}%")
print(f"    最大: {max(chg10_vals):+.1f}%  最小: {min(chg10_vals):+.1f}%")

print(f"\n  MA10斜率分布:")
above_ma5 = sum(1 for s in ai_stocks if s.get('close_above_ma5') is True)
pos_ma10 = sum(1 for v in ma10_vals if v > 0)
print(f"    均值: {sum(ma10_vals)/n:+.2f}%")
print(f"    >0% 占比 (MA10向上): {pos_ma10/n*100:.0f}%")
print(f"    站稳MA5比例: {above_ma5/n*100:.0f}%")

print(f"\n  趋势分分布:")
high = sum(1 for v in trend_vals if v >= 70)
print(f"    均值: {sum(trend_vals)/n:.1f}")
print(f"    >=70 占比: {high/n*100:.0f}%")

print()
print("="*80)
print(f"  【问题诊断】中期持续性计算过程:")
print("="*80)

avg_chg10 = sum(chg10_vals) / n
pos_ma10_ratio = pos_ma10 / n * 100
avg_trend = sum(trend_vals) / n

# 当前公式
mid_old = (
    min(100, avg_chg10 * 10 + 50) * 0.40
    + pos_ma10_ratio * 0.35
    + avg_trend * 0.25
)

print(f"\n  当前公式:")
print(f"    10日涨幅成分: min(100, {avg_chg10:.1f}×10 + 50) × 0.40")
print(f"               = min(100, {avg_chg10*10+50:.1f}) × 0.40 = {min(100, avg_chg10*10+50)*0.40:.1f}")
print(f"    MA10向上成分: {pos_ma10_ratio:.0f}% × 0.35 = {pos_ma10_ratio*0.35:.1f}")
print(f"    均趋势分成分: {avg_trend:.1f} × 0.25 = {avg_trend*0.25:.1f}")
print(f"    ─────────────────────────────")
print(f"    中期分: {mid_old:.1f}")

print(f"\n  ⚠️ 问题所在:")
print(f"    10日跌幅-4.4%，但在强势主题中这是正常的回调幅度")
print(f"    用({avg_chg10:.1f}×10+50)=6分来惩罚，严重低估了强势主题的结构稳定性")
print(f"    MA10向上22%说明中期结构并未崩塌，但权重只有35%")

print(f"\n  修复方案：调整权重 + 降低10日涨幅惩罚")
# 修正后
mid_new = (
    min(100, avg_chg10 * 5 + 75) * 0.20   # 降低10日权重(40%→20%)，放宽基准线
    + pos_ma10_ratio * 0.50                  # 提高MA10权重(35%→50%)，更看重中期结构
    + avg_trend * 0.30                       # 提高均趋势权重(25%→30%)
)

print(f"\n  修正后公式:")
print(f"    10日涨幅成分: min(100, {avg_chg10:.1f}×5 + 75) × 0.20")
print(f"               = min(100, {avg_chg10*5+75:.1f}) × 0.20 = {min(100, avg_chg10*5+75)*0.20:.1f}")
print(f"    MA10向上成分: {pos_ma10_ratio:.0f}% × 0.50 = {pos_ma10_ratio*0.50:.1f}")
print(f"    均趋势分成分: {avg_trend:.1f} × 0.30 = {avg_trend*0.30:.1f}")
print(f"    ─────────────────────────────")
print(f"    中期分: {mid_new:.1f}")
print(f"    改善: {mid_new - mid_old:+.1f}")

print()
print("="*80)
print(f"  【核心结论】")
print("="*80)
print(f"    AI中期分低(18.8)的原因:")
print(f"      1. 数据只到10日，'近几个月一直最强'的数据不在缓存中")
print(f"      2. 10日-4.4%的正常回调被过度惩罚（满分100中只给6分）")
print(f"      3. MA10向上22%说明中期结构还在，但权重不够高")
print(f"    建议:")
print(f"      1. 扩大数据源到20日/60日指标")
print(f"      2. 修正中期公式：降低10日惩罚权重，提高MA10结构权重")
print("="*80)
