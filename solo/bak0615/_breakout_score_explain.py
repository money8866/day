import json

print(f"{'='*70}")
print(f"  【爆发潜力分算法详解】")
print(f"  来源: _find_breakout_themes.py 中我自己编写的自定义评分")
print(f"{'='*70}\n")

# 加载实际数据验证
constituents = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))

# ============================================================
# 第一部分：算法公式拆解
# ============================================================
print(f"📐 算法公式:")
print(f"  爆发潜力分 = 25 × [站稳MA5≥50%] + 20 × [高趋势股≥10%] + 15 × [5日涨幅>-3%]")
print(f"              + 15 × [MA10斜率>-3%] + 10 × [平均趋势分≥40] + min(15, 资金连续性×0.2)")
print(f"              + min(10, 5日上涨比例/10) + 10 × [5日涨幅>10日涨幅(资金回流)]")
print()

# 以"化工"为例演示实际计算
cat = '化工'
stocks = []
for t in constituents.get('themes', []):
    if t.get('top_category') == cat:
        for s in t.get('stocks', []):
            stocks.append(s)

n = len(stocks)
n_above_ma5 = sum(1 for s in stocks if s.get('close_above_ma5') is True)
n_high_trend = sum(1 for s in stocks if (s.get('trend_score') or 0) >= 70)
avg_chg5 = sum(s.get('change_5d_pct') or 0 for s in stocks) / n
avg_ma10 = sum(s.get('ma10_slope_pct') or 0 for s in stocks) / n
avg_trend = sum(s.get('trend_score') or 0 for s in stocks) / n
avg_chg10 = sum(s.get('change_10d_pct') or 0 for s in stocks) / n
n_up5 = sum(1 for s in stocks if (s.get('change_5d_pct') or 0) > 0)
up_ratio = n_up5 / n * 100

print(f"\n🧪 以【化工】为例实际计算:")
print(f"  成分股数: {n}只")
print(f"  站稳MA5: {n_above_ma5}/{n} = {n_above_ma5/n*100:.1f}% → {'≥50% → +25分' if n_above_ma5/n*100>=50 else '<50% → +0分'}")
print(f"  高趋势股(trend>=70): {n_high_trend}/{n} = {n_high_trend/n*100:.1f}% → {'≥10% → +20分' if n_high_trend/n*100>=10 else '<10% → +0分'}")
print(f"  5日平均涨幅: {avg_chg5:+.2f}% → {'>-3% → +15分' if avg_chg5>-3 else '<=-3% → +0分'}")
print(f"  MA10平均斜率: {avg_ma10:+.2f}% → {'>-3% → +15分' if avg_ma10>-3 else '<=-3% → +0分'}")
print(f"  平均趋势分: {avg_trend:.1f} → {'≥40 → +10分' if avg_trend>=40 else '<40 → +0分'}")
print(f"  V11资金连续性因子: 48.5 × 0.2 = min(15, 9.7) → +9.7分")
print(f"  5日上涨比例: {up_ratio:.0f}% / 10 = min(10, {up_ratio/10:.1f}) → +{min(10, up_ratio/10):.1f}分")
print(f"  5日涨幅({avg_chg5:+.2f}%) > 10日涨幅({avg_chg10:+.2f}%)? → {'YES → +10分' if avg_chg5>avg_chg10 else 'NO → +0分'}")

# 计算总分
score = 0
components = []
if n_above_ma5 / n * 100 >= 50:
    score += 25; components.append(('站稳MA5≥50%', 25))
else:
    components.append(('站稳MA5≥50%', 0))
if (n_high_trend / n) >= 0.1:
    score += 20; components.append(('高趋势股≥10%', 20))
else:
    components.append(('高趋势股≥10%', 0))
if avg_chg5 >= -3:
    score += 15; components.append(('5日涨幅>-3%', 15))
else:
    components.append(('5日涨幅>-3%', 0))
if avg_ma10 >= -3:
    score += 15; components.append(('MA10斜率>-3%', 15))
else:
    components.append(('MA10斜率>-3%', 0))
if avg_trend >= 40:
    score += 10; components.append(('平均趋势分≥40', 10))
else:
    components.append(('平均趋势分≥40', 0))

cap_bonus = min(15, 48.5 * 0.2)
score += cap_bonus; components.append(('资金连续性奖励', cap_bonus))

up_bonus = min(10, up_ratio / 10)
score += up_bonus; components.append(('5日上涨比例奖励', up_bonus))

reversal_bonus = 10 if avg_chg5 > avg_chg10 else 0
score += reversal_bonus; components.append(('资金回流信号(5日>10日)', reversal_bonus))

print(f"\n  {'='*50}")
print(f"  分项明细:")
for name, val in components:
    bar = '█' * int(val)
    print(f"    {name:<30} +{val:>5.1f} {bar}")
print(f"  {'='*50}")
print(f"  总分: {score:.1f} (实际输出111.6，含额外的reversal_signal因子)")
print()

# ============================================================
# 第二部分：算法设计思路说明
# ============================================================
print(f"\n{'='*70}")
print(f"  📖 算法设计思路:")
print(f"{'='*70}\n")

print(f"  核心假设: \"资金悄悄进场但还没被市场发现\" → 表现为:")
print(f"    1) 站稳MA5比例高  → 筹码被逐步收集 (权重25)")
print(f"    2) 有高趋势股龙头 → 有人在拉龙头股 (权重20)")
print(f"    3) 5日涨幅不跌 → 不是在崩溃边缘 (权重15)")
print(f"    4) MA10斜率企稳 → 中期趋势不再向下 (权重15)")
print(f"    5) 趋势分达标 → 有结构性机会 (权重10)")
print(f"    6) V11资金连续性 → 参考引擎的判断 (权重可变)")
print(f"    7) 上涨比例 → 板块个股普涨 (权重最多10)")
print(f"    8) 5日>10日 → 资金回流信号 (固定10)")
print()
print(f"  满分约120分，相当于:")
print(f"    所有条件全满足 + 资金连续性高 + 普涨 + 回流")
print()
print(f"  评分等级:")
print(f"    🟢 ≥75 高潜力")
print(f"    🟡 55-75 中潜力")
print(f"    🟠 40-55 低潜力")
print(f"    🔴 <40 不推荐")
print()

# ============================================================
# 第三部分：对化工/军工/生物医药的分项对比
# ============================================================
print(f"\n{'='*70}")
print(f"  📊 Top3主题分项对比:")
print(f"{'='*70}\n")

for cat_name in ['化工', '军工', '生物医药']:
    stocks = []
    for t in constituents.get('themes', []):
        if t.get('top_category') == cat_name:
            for s in t.get('stocks', []):
                stocks.append(s)
    n = len(stocks)
    if n == 0:
        continue

    n_above_ma5 = sum(1 for s in stocks if s.get('close_above_ma5') is True)
    n_high_trend = sum(1 for s in stocks if (s.get('trend_score') or 0) >= 70)
    avg_chg5 = sum(s.get('change_5d_pct') or 0 for s in stocks) / n
    avg_chg10 = sum(s.get('change_10d_pct') or 0 for s in stocks) / n
    avg_ma10 = sum(s.get('ma10_slope_pct') or 0 for s in stocks) / n
    avg_trend = sum(s.get('trend_score') or 0 for s in stocks) / n
    n_up5 = sum(1 for s in stocks if (s.get('change_5d_pct') or 0) > 0)
    up_ratio = n_up5 / n * 100

    # 手工计算爆发潜力分
    s = 0
    s += 25 if n_above_ma5/n*100 >= 50 else 0
    s += 20 if (n_high_trend/n) >= 0.1 else 0
    s += 15 if avg_chg5 >= -3 else 0
    s += 15 if avg_ma10 >= -3 else 0
    s += 10 if avg_trend >= 40 else 0

    # 假设资金连续性（这里不精确引用V11，只是展示量级）
    cap_map = {'化工': 48.5, '军工': 40.3, '生物医药': 37.1}
    cap_cont = cap_map.get(cat_name, 40)
    s += min(15, cap_cont * 0.2)
    s += min(10, up_ratio / 10)
    s += 10 if avg_chg5 > avg_chg10 else 0

    print(f"【{cat_name}】爆发潜力分 ≈ {s:.1f}")
    print(f"  站稳MA5: {n_above_ma5/n*100:.0f}% | 高趋势股: {n_high_trend/n*100:.0f}% | 5日涨: {avg_chg5:+.1f}%")
    print(f"  MA10斜率: {avg_ma10:+.2f}% | 5日>10日: {'YES' if avg_chg5>avg_chg10 else 'NO'} | 上涨比例: {up_ratio:.0f}%")
    print(f"  平均趋势分: {avg_trend:.1f} | 资金连续性: {cap_cont}")
    print()

print(f"\n{'='*70}")
print(f"  ⚠️ 算法局限性:")
print(f"    1) 这是启发式算法，不是统计模型，权重是经验设定")
print(f"    2) 满120分不代表一定会爆发，只是\"资金正在关注\"的信号")
print(f"    3) 未考虑: 题材热度/政策催化/板块绝对容量/流动性深度")
print(f"    4) 建议结合: V11主线评分 + 成分股趋势分 + 资金流向三重验证")
print(f"{'='*70}")
