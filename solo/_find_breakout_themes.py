import json
from collections import defaultdict

# 数据源说明
CONSTITUENTS_FILE = 'cache_backbone_tushare/theme3_constituents_20260612.json'
V11_FILE = 'cache_backbone_tushare/trend_lifecycle_v11_20260612.json'

print(f"{'='*70}")
print(f"  数据源: {CONSTITUENTS_FILE}")
print(f"          {V11_FILE}")
print(f"{'='*70}")

# 读取数据
constituents = json.load(open(CONSTITUENTS_FILE, encoding='utf-8'))
v11 = json.load(open(V11_FILE, encoding='utf-8'))

# 获取全主题评分
all_ranking = v11.get('all_theme_ranking', [])
mainline = v11.get('primary_mainline', '')
market_total = v11.get('market_total_amount_yi', 0)

print(f"\n📅 交易日期: {constituents.get('trade_date', '20260612')}")
print(f"  市场总成交额: {market_total:.1f}亿")
print(f"  当前主线: {mainline}")
print(f"\n{'='*70}")
print(f"  寻找：未在主线但有爆发潜力的主题")
print(f"  标准：资金悄悄进场 + 趋势底背离 + 龙头结构完整")
print(f"{'='*70}\n")

# === 逐一分析每个主题 ===
cat_data = {}

# 1. 先收集每一级主题的成分股数据
for cat in set(t.get('top_category') for t in constituents.get('themes', [])):
    stocks = []
    sub_themes = []
    for t in constituents.get('themes', []):
        if t.get('top_category') == cat:
            sub_themes.append(t.get('theme_name', ''))
            for s in t.get('stocks', []):
                s = dict(s)
                s['_sub_theme'] = t.get('theme_name', '')
                stocks.append(s)

    n = len(stocks)
    if n == 0:
        continue

    # 计算各项指标
    avg_trend = sum(s.get('trend_score', 0) or 0 for s in stocks) / n
    avg_combined = sum(s.get('combined_score', 0) or 0 for s in stocks) / n
    avg_chg5 = sum(s.get('change_5d_pct', 0) or 0 for s in stocks) / n
    avg_chg10 = sum(s.get('change_10d_pct', 0) or 0 for s in stocks) / n
    avg_ma10 = sum(s.get('ma10_slope_pct', 0) or 0 for s in stocks) / n

    n_pos_ma10 = sum(1 for s in stocks if (s.get('ma10_slope_pct') or 0) > 0)
    n_above_ma5 = sum(1 for s in stocks if s.get('close_above_ma5') is True)
    n_high_trend = sum(1 for s in stocks if (s.get('trend_score') or 0) >= 70)
    n_mid_trend = sum(1 for s in stocks if 50 <= (s.get('trend_score') or 0) < 70)

    # 大票分析
    total_amt = sum(s.get('avg_amount_5d', 0) or 0 for s in stocks) / 1e8
    n_large = sum(1 for s in stocks if (s.get('avg_amount_5d', 0) or 0) >= 10e8)
    n_top = sum(1 for s in stocks if (s.get('avg_amount_5d', 0) or 0) >= 3e8 and (s.get('trend_score') or 0) >= 60)

    # 关键：寻找"低评分但有资金迹象"的主题
    #  - 站稳MA5>50%（有资金关注）
    #  - 但主线分<60（未被识别为主线）
    #  - MA10正在反转（虽然还没大面积转上，但转上的比例在上升）
    #  - 5日平均涨幅为正但不过高（温和上涨，不是脉冲）
    #  - 有龙头股结构（trend>=70的股票）

    # 寻找资金悄悄进场信号：站稳MA5的比例
    above_ratio = n_above_ma5 / n * 100

    # 趋势反转信号：MA10斜率为正的比例 + 站稳MA5的比例
    reversal_signal = (
        (n_pos_ma10 / n * 100) * 0.3
        + above_ratio * 0.3
        + avg_trend * 0.2
        + min(100, avg_ma10 * 10 + 50) * 0.2
    )

    cat_data[cat] = {
        'stocks': stocks,
        'sub_themes': sub_themes,
        'n': n,
        'avg_trend': avg_trend,
        'avg_combined': avg_combined,
        'avg_chg5': avg_chg5,
        'avg_chg10': avg_chg10,
        'avg_ma10': avg_ma10,
        'n_pos_ma10': n_pos_ma10,
        'n_above_ma5': n_above_ma5,
        'n_high_trend': n_high_trend,
        'n_mid_trend': n_mid_trend,
        'total_amt': total_amt,
        'n_large': n_large,
        'n_top': n_top,
        'above_ratio': above_ratio,
        'reversal_signal': reversal_signal,
    }

# === 展示：当前主线 vs 潜在爆发主题 ===
print("当前主线及评分:")
for t in all_ranking:
    cat = t['theme']
    data = cat_data.get(cat, {})
    is_main = "⭐" if cat == mainline else " "
    print(f"  {is_main} {cat:<8} 主线分={t['mainline_score']:<6.1f} "
          f"资金连续性={t['capital_continuity']:<6.1f} "
          f"生命周期={t['lifecycle_stage']:<8} "
          f"分类={t['classification']}")

print(f"\n{'='*70}")
print("  【潜在爆发主题筛选】")
print("  标准: 主线分<60 (非主线) + 站稳MA5>50% (资金关注)")
print("        + 平均趋势分>45 (有结构) + 5日涨幅>-3% (非退潮)")
print(f"{'='*70}\n")

# 2. 对每个主题打分：寻找"非主线但有资金迹象"的
breakout_candidates = []

for cat, d in cat_data.items():
    # 排除当前主线
    v11_info = next((x for x in all_ranking if x['theme'] == cat), None)
    if not v11_info:
        continue

    mainline_score = v11_info['mainline_score']
    capital_continuity = v11_info['capital_continuity']
    lifecycle = v11_info['lifecycle_stage']

    # 筛选逻辑：非主线但有资金进场迹象
    if mainline_score >= 60:
        continue  # 已是主线级别

    # 核心条件：
    # 1) 站稳MA5比例>50% → 资金在关注
    # 2) 高趋势分股票>10% → 有龙头结构
    # 3) 5日涨幅不是大幅下跌 → 不是在退潮
    # 4) MA10斜率不是大幅负值 → 中期在企稳

    condition1 = d['above_ratio'] >= 50
    condition2 = (d['n_high_trend'] / d['n']) >= 0.1
    condition3 = d['avg_chg5'] >= -3
    condition4 = d['avg_ma10'] >= -3
    condition5 = d['avg_trend'] >= 40

    score = 0
    score += 25 if condition1 else 0
    score += 20 if condition2 else 0
    score += 15 if condition3 else 0
    score += 15 if condition4 else 0
    score += 10 if condition5 else 0
    score += min(15, capital_continuity * 0.2)  # 资金连续性奖励

    # 加分：5日上涨股票比例
    n_up5 = sum(1 for s in d['stocks'] if (s.get('change_5d_pct') or 0) > 0)
    up_ratio = n_up5 / d['n'] * 100
    score += min(10, up_ratio / 10)  # 最高加10分

    # 加分：中期趋势企稳（avg_chg5 > avg_chg10，表示短期在修复）
    if d['avg_chg5'] > d['avg_chg10']:
        score += 10

    breakout_candidates.append({
        'cat': cat,
        'score': score,
        'mainline_score': mainline_score,
        'capital_continuity': capital_continuity,
        'lifecycle': lifecycle,
        'avg_trend': d['avg_trend'],
        'avg_chg5': d['avg_chg5'],
        'avg_chg10': d['avg_chg10'],
        'avg_ma10': d['avg_ma10'],
        'above_ratio': d['above_ratio'],
        'n_high_trend_ratio': d['n_high_trend'] / d['n'] * 100,
        'n_top': d['n_top'],
        'total_amt': d['total_amt'],
        'stocks': d['stocks'],
        'sub_themes': d['sub_themes'],
        'reversal': d['reversal_signal'],
        'up_ratio': up_ratio,
    })

breakout_candidates.sort(key=lambda x: -x['score'])

print("=== 排名 ===")
for i, c in enumerate(breakout_candidates):
    level = "🟢 高潜力" if c['score'] >= 75 else "🟡 中潜力" if c['score'] >= 55 else "🟠 低潜力" if c['score'] >= 40 else "🔴 不推荐"
    print(f"\n{i+1}. 【{c['cat']}】 {level} (爆发潜力分={c['score']})")
    print(f"   V11主线分={c['mainline_score']:.1f} | 资金连续性={c['capital_continuity']:.1f} | 生命周期={c['lifecycle']}")
    print(f"   站稳MA5={c['above_ratio']:.0f}% | 5日上涨={c['up_ratio']:.0f}% | 高趋势股={c['n_high_trend_ratio']:.0f}%")
    print(f"   5日均涨={c['avg_chg5']:+.2f}% | 10日均涨={c['avg_chg10']:+.2f}% | MA10斜率={c['avg_ma10']:+.2f}%")
    print(f"   总成交额={c['total_amt']:.0f}亿 | 活跃大票(trend>=60, amt>=3亿)={c['n_top']}只")
    print(f"   子主题: {', '.join(c['sub_themes'])}")

    # 显示Top活跃股
    top_stocks = sorted(c['stocks'], key=lambda s: -(s.get('trend_score', 0) or 0))[:5]
    print(f"   龙头候选: ", end="")
    for s in top_stocks[:5]:
        amt = (s.get('avg_amount_5d', 0) or 0) / 1e8
        chg5 = s.get('change_5d_pct') or 0
        print(f"{s.get('name')}(trend={s.get('trend_score')}, amt={amt:.0f}亿, 5日={chg5:+.1f}%) ", end="")
    print()

print(f"\n{'='*70}")
print("  【总结推荐】")
print(f"{'='*70}\n")

# 取Top3进行详细分析
top3 = breakout_candidates[:3]
for i, c in enumerate(top3):
    if c['score'] < 50:
        continue
    print(f"{i+1}. 【{c['cat']}】爆发潜力分={c['score']}")

    # 风险和机会分析
    print(f"   📈 机会:")
    if c['up_ratio'] > 60:
        print(f"      → 60%+股票5日上涨，资金在积极布局")
    if c['above_ratio'] > 70:
        print(f"      → 70%+股票站稳MA5，结构稳定")
    if c['avg_ma10'] > -2:
        print(f"      → MA10斜率企稳，中期趋势开始反转")
    if c['avg_chg5'] > c['avg_chg10']:
        print(f"      → 5日涨幅好于10日涨幅，资金在回流")

    print(f"   ⚠️ 风险:")
    if c['mainline_score'] < 50:
        print(f"      → 主线分偏低({c['mainline_score']:.1f})，缺乏主升确认")
    if c['capital_continuity'] < 50:
        print(f"      → 资金连续性偏低({c['capital_continuity']:.1f})，需持续验证")
    if c['avg_chg10'] < -5:
        print(f"      → 10日平均跌幅过大({c['avg_chg10']:.1f}%)，仍在下跌趋势中")
    if c['total_amt'] < 500:
        print(f"      → 板块成交额偏低({c['total_amt']:.0f}亿)，容纳量有限")

    print()
