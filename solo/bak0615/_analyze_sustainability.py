import json
from collections import defaultdict

# 读取数据
constituents = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))
v11 = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

TARGETS = ['金融', '资源']

for cat in TARGETS:
    print(f"\n{'='*60}")
    print(f"  【{cat}】持续性深度分析")
    print(f"{'='*60}")

    # 收集该一级主题下所有股票
    all_stocks = []
    sub_themes = []
    for t in constituents.get('themes', []):
        if t.get('top_category') == cat:
            theme_name = t.get('theme_name', '')
            sub_themes.append(theme_name)
            for s in t.get('stocks', []):
                s = dict(s)
                s['_sub_theme'] = theme_name
                all_stocks.append(s)

    n = len(all_stocks)
    print(f"\n📊 基础数据: {n} 只股票, 子主题: {', '.join(sub_themes)}")

    # === 1. 资金连续性 ===
    n_pos_ma10 = sum(1 for s in all_stocks if (s.get('ma10_slope_pct') or 0) > 0)
    n_above_ma5 = sum(1 for s in all_stocks if s.get('close_above_ma5') is True)
    n_high_trend = sum(1 for s in all_stocks if (s.get('trend_score') or 0) >= 70)
    avg_trend = sum(s.get('trend_score', 0) or 0 for s in all_stocks) / n

    print(f"\n📈 资金连续性指标:")
    print(f"   MA10向上股票: {n_pos_ma10}/{n} = {n_pos_ma10/n*100:.1f}%")
    print(f"   站稳MA5股票: {n_above_ma5}/{n} = {n_above_ma5/n*100:.1f}%")
    print(f"   高趋势分(trend>=70): {n_high_trend}/{n} = {n_high_trend/n*100:.1f}%")
    print(f"   平均趋势分: {avg_trend:.1f}")

    # === 2. 涨幅结构分析 ===
    avg_chg5 = sum(s.get('change_5d_pct') or 0 for s in all_stocks) / n
    avg_chg10 = sum(s.get('change_10d_pct') or 0 for s in all_stocks) / n

    n_up5 = sum(1 for s in all_stocks if (s.get('change_5d_pct') or 0) > 0)
    n_up10 = sum(1 for s in all_stocks if (s.get('change_10d_pct') or 0) > 0)
    n_strong_up5 = sum(1 for s in all_stocks if (s.get('change_5d_pct') or 0) > 5)
    n_strong_up10 = sum(1 for s in all_stocks if (s.get('change_10d_pct') or 0) > 10)

    print(f"\n📊 涨幅结构分析:")
    print(f"   5日平均涨幅: {avg_chg5:.2f}%")
    print(f"   10日平均涨幅: {avg_chg10:.2f}%")
    print(f"   5日上涨股票: {n_up5}/{n} = {n_up5/n*100:.1f}%")
    print(f"   10日上涨股票: {n_up10}/{n} = {n_up10/n*100:.1f}%")
    print(f"   5日强势(>5%): {n_strong_up5}/{n} = {n_strong_up5/n*100:.1f}%")
    print(f"   10日强势(>10%): {n_strong_up10}/{n} = {n_strong_up10/n*100:.1f}%")

    # === 3. 扩散能力 ===
    sub_avg_trends = []
    sub_avg_chg5 = []
    for t in constituents.get('themes', []):
        if t.get('top_category') == cat:
            stocks = t.get('stocks', [])
            if stocks:
                avg_t = sum(s.get('trend_score', 0) or 0 for s in stocks) / len(stocks)
                avg_c5 = sum(s.get('change_5d_pct') or 0 for s in stocks) / len(stocks)
                sub_avg_trends.append(avg_t)
                sub_avg_chg5.append(avg_c5)
                active_n = sum(1 for s in stocks if (s.get('trend_score', 0) or 0) >= 50)
                print(f"\n   📌 子主题【{t.get('theme_name')}】({len(stocks)}只)")
                print(f"      趋势均值: {avg_t:.1f}, 5日均涨: {avg_c5:.2f}%, 活跃(趋势>=50): {active_n}/{len(stocks)}")

    avg_sub_trend = sum(sub_avg_trends) / len(sub_avg_trends) if sub_avg_trends else 0
    diff_sub_trend = max(sub_avg_trends) - min(sub_avg_trends) if sub_avg_trends else 0
    print(f"\n   子主题趋势一致性: {avg_sub_trend:.1f} (差值: {diff_sub_trend:.1f})")

    # === 4. 容量分析 ===
    total_amt = sum(s.get('avg_amount_5d', 0) or 0 for s in all_stocks) / 1e8
    n_large_cap = sum(1 for s in all_stocks if (s.get('avg_amount_5d', 0) or 0) >= 10e8)
    print(f"\n💰 容量分析:")
    print(f"   总成交额(5日均): {total_amt:.1f}亿")
    print(f"   大票(>=10亿): {n_large_cap}/{n} = {n_large_cap/n*100:.1f}%")
    print(f"   市场占比(估算): {total_amt / v11.get('market_total_amount_yi', 1) * 100:.1f}%")

    # === 5. 生命周期判断 ===
    m = None
    for x in v11.get('all_theme_ranking', []):
        if x['theme'] == cat:
            m = x
            break

    if m:
        print(f"\n🔄 生命周期判断:")
        print(f"   当前阶段: {m['lifecycle_stage']}")
        print(f"   主线评分: {m['mainline_score']:.1f}")
        print(f"   分类: {m['classification']}")

        cc = m['capital_continuity']
        diff = m['diffusion_score']

        # 持续性预测
        if m['lifecycle_stage'] == '主升期':
            if cc >= 60 and diff >= 50:
                outlook = "持续性强 → 主升中段，预计仍有上行空间"
            elif n_high_trend/n >= 0.5:
                outlook = "持续性中强 → 但短期涨幅过大，注意震荡"
            else:
                outlook = "持续性偏弱 → 资金分散，难以形成合力"
        elif m['lifecycle_stage'] == '启动期':
            if cc >= 45 and n_up10/n >= 0.6:
                outlook = "持续性较强 → 资金刚启动，尚未高潮"
            else:
                outlook = "持续性一般 → 等待信号确认"
        elif m['lifecycle_stage'] == '高潮期':
            outlook = "⚠️ 风险警示 → 已进入高潮，随时可能分歧退潮"
        elif m['lifecycle_stage'] == '分歧期':
            outlook = "⚠️ 方向未明 → 等待资金选择后再参与"
        else:
            outlook = "❌ 持续性差 → 退潮期，不参与"

        print(f"   持续性预判: {outlook}")

    # === 6. Top5 龙头状态 ===
    print(f"\n🐉 Top5 龙头状态:")
    top5 = sorted(all_stocks, key=lambda s: -(s.get('combined_score', 0) or 0))[:5]
    for i, s in enumerate(top5, 1):
        amt = (s.get('avg_amount_5d', 0) or 0) / 1e8
        chg5 = s.get('change_5d_pct') or 0
        chg10 = s.get('change_10d_pct') or 0
        ma10 = s.get('ma10_slope_pct') or 0
        above = "✅MA5" if s.get('close_above_ma5') else "❌MA5下"
        print(f"   {i}. {s.get('name')}({s.get('ts_code')}) "
              f"趋势={s.get('trend_score')} 综合={s.get('combined_score'):.1f} "
              f"5日={chg5:+.1f}% 10日={chg10:+.1f}% MA10斜率={ma10:+.2f}% {above}")

    # === 7. 风险点识别 ===
    print(f"\n⚠️ 风险点识别:")
    risks = []

    # 短期涨幅过大
    if n_strong_up10/n > 0.4:
        risks.append(f"短期涨幅过大: {n_strong_up10/n*100:.0f}%股票10日涨超10%")

    # 资金分散
    if diff_sub_trend > 40:
        risks.append(f"子主题分化严重: 趋势差{diff_sub_trend:.0f}，资金分散")

    # MA10向下
    neg_ma10 = sum(1 for s in all_stocks if (s.get('ma10_slope_pct') or 0) < 0)
    if neg_ma10/n > 0.3:
        risks.append(f"均线走弱: {neg_ma10/n*100:.0f}%股票MA10向下")

    # 5日回调
    n_down5 = sum(1 for s in all_stocks if (s.get('change_5d_pct') or 0) < -3)
    if n_down5/n > 0.25:
        risks.append(f"短期回调压力: {n_down5/n*100:.0f}%股票5日跌超3%")

    if risks:
        for r in risks:
            print(f"   - {r}")
    else:
        print(f"   ✅ 未发现明显风险点")

    # === 8. 综合评分 ===
    cap_score = m['capital_continuity'] if m else 0
    diff_score = m['diffusion_score'] if m else 0
    leader_score = m['leader_structure'] if m else 0

    # 持续性因子
    consistency = (
        (n_pos_ma10 / n * 100) * 0.25
        + (n_above_ma5 / n * 100) * 0.20
        + avg_trend * 0.25
        + (n_up10 / n * 100) * 0.15
        + min(100, diff_score) * 0.15
    )

    print(f"\n📋 综合持续性评分: {consistency:.1f}/100")
    if consistency >= 70:
        print(f"   评级: 🟢 强持续")
    elif consistency >= 55:
        print(f"   评级: 🟡 中持续")
    elif consistency >= 40:
        print(f"   评级: 🟠 弱持续")
    else:
        print(f"   评级: 🔴 高风险")

print(f"\n{'='*60}")
print("  【总结对比】")
print(f"{'='*60}")

v11_all = v11.get('all_theme_ranking', [])
for cat in TARGETS:
    for x in v11_all:
        if x['theme'] == cat:
            cc = x['capital_continuity']
            diff = x['diffusion_score']
            ls = x['leader_structure']
            stage = x['lifecycle_stage']

            # 持续性预测
            if stage == '主升期' and cc >= 60:
                pred = "🟢 预计持续2-4周"
            elif stage == '主升期' and cc >= 50:
                pred = "🟡 预计持续1-2周"
            elif stage == '启动期' and cc >= 45:
                pred = "🟡 需观察资金跟进"
            elif stage == '高潮期':
                pred = "🔴 随时可能退潮"
            else:
                pred = "⚠️ 持续性存疑"

            print(f"\n【{cat}】")
            print(f"  资金连续性: {cc:.1f} {'✅' if cc >= 50 else '⚠️'}")
            print(f"  扩散能力: {diff:.1f} {'✅' if diff >= 50 else '⚠️'}")
            print(f"  龙头结构: {ls:.1f} {'✅' if ls >= 55 else '⚠️'}")
            print(f"  生命周期: {stage}")
            print(f"  持续性预判: {pred}")
