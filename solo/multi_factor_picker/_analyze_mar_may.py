"""
3月和5月超跌择时回测结果专项分析
================================
从多维度分析3月和5月的低胜率原因，给出理论优化建议。
"""
import pandas as pd
import numpy as np
import os
import json

# ── 加载数据 ──
csv_path = r"d:\mystock\solo\report_daily\backtest_oversold_dynamic_20260724.csv"
df = pd.read_csv(csv_path, encoding='utf-8-sig')

# 筛选3月和5月
mar = df[df['信号日期'].astype(str).str.startswith('202603')].copy()
may = df[df['信号日期'].astype(str).str.startswith('202605')].copy()

print("═══════════════════════════════════════════════════════════════")
print("  超跌择时 3月 & 5月 专项分析报告")
print("═══════════════════════════════════════════════════════════════")
print(f"\n数据区间: {df['信号日期'].min()} ~ {df['信号日期'].max()}")
print(f"全量回测信号: {len(df)} 个")
print(f"3月信号: {len(mar)} 个 | 5月信号: {len(may)} 个")
print()

# ═══════════════════════════════════════════
# 1. 基础统计
# ═══════════════════════════════════════════
print("─" * 80)
print("  1. 基础统计")
print("─" * 80)

for label, m in [('3月', mar), ('5月', may)]:
    total = len(m)
    wins = (m['是否盈利'] == '是').sum()
    wr = wins / total * 100 if total > 0 else 0
    avg5 = m['5日收益%'].mean()
    avg10 = m['10日收益%'].mean()
    avg20 = m['20日收益%'].mean()
    med20 = m['20日收益%'].median()
    outcome = m['触发结果'].value_counts().to_dict()

    print(f"\n  [{label}] 信号数={total}, 胜率={wr:.1f}%")
    print(f"    平均5日收益={avg5:+.2f}% | 平均10日收益={avg10:+.2f}% | 平均20日收益={avg20:+.2f}%")
    print(f"    中位数20日收益={med20:+.2f}%")
    print(f"    触发分布: 止盈={outcome.get('止盈',0)} 止损={outcome.get('止损',0)} 盈利={outcome.get('盈利',0)} 亏损={outcome.get('亏损',0)}")

    # 正收益占比
    pos_20 = (m['20日收益%'] > 0).sum()
    neg_20 = (m['20日收益%'] <= 0).sum()
    print(f"    20日正收益={pos_20}({pos_20/total*100:.1f}%) | 20日负收益={neg_20}({neg_20/total*100:.1f}%)")

    # 涨幅>5%、>10%的比例
    gt5 = (m['20日收益%'] > 5).sum()
    gt10 = (m['20日收益%'] > 10).sum()
    lt5 = (m['20日收益%'] < -5).sum()
    lt10 = (m['20日收益%'] < -10).sum()
    print(f"    20日涨幅>5%: {gt5}({gt5/total*100:.1f}%) | >10%: {gt10}({gt10/total*100:.1f}%)")
    print(f"    20日跌幅>5%: {lt5}({lt5/total*100:.1f}%) | >10%: {lt10}({lt10/total*100:.1f}%)")

# ═══════════════════════════════════════════
# 2. 市场状态分布分析
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  2. 市场状态分布")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    regime_grp = m.groupby('市场状态')
    for regime, grp in sorted(regime_grp.groups.items()):
        sub = regime_grp.get_group(regime)
        wins = (sub['是否盈利'] == '是').sum()
        total = len(sub)
        wr = wins / total * 100 if total > 0 else 0
        avg20 = sub['20日收益%'].mean()
        print(f"    {regime:<12}: {total:4d}个信号 | 胜率{wr:5.1f}% | 平均20日收益{avg20:+7.2f}%")

# ═══════════════════════════════════════════
# 3. 各因子得分对比 — 盈利 vs 亏损
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  3. 因子得分对比 — 盈利 vs 亏损")
print("─"*80)

factor_cols = ['F1回撤深度', 'F2缩量程度', 'F3支撑强度', 'F4_RSI超卖', 'F5_K线止跌', 'F6基本面锚定', 'F7趋势保护']

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    wins = m[m['是否盈利'] == '是']
    losses = m[m['是否盈利'] == '否']
    print(f"    {'因子':<12} {'盈利均值':<10} {'亏损均值':<10} {'差值':<10} {'说明':<30}")
    print(f"    {'-'*72}")
    for col in factor_cols:
        w_mean = wins[col].mean()
        l_mean = losses[col].mean()
        diff = w_mean - l_mean
        note = ""
        if abs(diff) > 5:
            note = "★ 有明显区分度" if diff > 0 else "★ 反向特征（亏损端更高）"
        elif abs(diff) > 2:
            note = "有一定区分度"
        else:
            note = "区分度不足"
        print(f"    {col:<12} {w_mean:>8.1f}  {l_mean:>8.1f}  {diff:>+8.1f}  {note:<30}")

# ═══════════════════════════════════════════
# 4. 动态阈值分析
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  4. 动态阈值与胜率关系")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    thresh_grp = m.groupby('动态阈值')
    for thresh in sorted(thresh_grp.groups.keys()):
        sub = thresh_grp.get_group(thresh)
        total = len(sub)
        wins = (sub['是否盈利'] == '是').sum()
        wr = wins / total * 100 if total > 0 else 0
        avg20 = sub['20日收益%'].mean()
        print(f"    阈值={int(thresh):2d}: {total:3d}个信号 | 胜率{wr:5.1f}% | 平均20日收益{avg20:+7.2f}%")

# ═══════════════════════════════════════════
# 5. 超跌分分布
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  5. 超跌分区间胜率")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    bins = [75, 80, 82, 85, 88, 90, 95, 100]
    labels_bin = ['75-80', '80-82', '82-85', '85-88', '88-90', '90-95', '95+']
    m['分档'] = pd.cut(m['超跌分'], bins=bins, labels=labels_bin)
    for lbl in labels_bin:
        sub = m[m['分档'] == lbl]
        if len(sub) > 0:
            wins = (sub['是否盈利'] == '是').sum()
            total = len(sub)
            wr = wins / total * 100
            print(f"    超跌分{lbl:>8}: {total:3d}个 | 胜率{wr:5.1f}%")

# ═══════════════════════════════════════════
# 6. 每日胜率分析（连续低胜率日）
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  6. 每日胜率 & 连续低胜率日")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    daily = m.groupby('信号日期').agg(
        信号数=('是否盈利', 'count'),
        胜率=('是否盈利', lambda x: (x == '是').mean() * 100),
    )
    # 连续低胜率日
    low_streak = 0
    max_low_streak = 0
    low_dates = []
    for date, row in daily.iterrows():
        if row['胜率'] < 40 and row['信号数'] >= 3:
            low_streak += 1
            low_dates.append(f"{date}({row['胜率']:.0f}%,{int(row['信号数'])}个)")
            max_low_streak = max(max_low_streak, low_streak)
        else:
            low_streak = 0

    above50 = (daily['胜率'] >= 50).sum()
    below50 = (daily['胜率'] < 50).sum()
    print(f"    交易日: {len(daily)}天 | 日胜率≥50%: {above50}天 | 日胜率<50%: {below50}天")
    print(f"    连续低胜率最长:{max_low_streak}天")
    if low_dates:
        print(f"    低胜率日(胜率<40%且信号≥3):")
        for d in low_dates:
            print(f"      {d}")

# ═══════════════════════════════════════════
# 7. 中报增速分析
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  7. 中报增速分档胜率")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    m['增速档'] = pd.cut(m['中报增速%'], bins=[0, 50, 100, 200, 500, 99999], labels=['<50%', '50-100%', '100-200%', '200-500%', '>500%'])
    grp = m.groupby('增速档', observed=True)
    for lbl, sub in grp:
        total = len(sub)
        wins = (sub['是否盈利'] == '是').sum()
        wr = wins / total * 100 if total > 0 else 0
        avg20 = sub['20日收益%'].mean()
        print(f"    {lbl:<10}: {total:3d}个 | 胜率{wr:5.1f}% | 平均20日收益{avg20:+7.2f}%")

# ═══════════════════════════════════════════
# 8. 止损分析 — 什么信号容易止损
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  8. 止损信号特征分析")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    sl = m[m['触发结果'] == '止损']
    tp = m[m['触发结果'] == '止盈']
    total_sl = len(sl)
    total_tp = len(tp)

    print(f"    止损: {total_sl}个 | 止盈: {total_tp}个 | 止盈/止损比 = {total_tp/max(total_sl,1):.2f}")

    if total_sl > 0:
        print(f"\n    止损信号平均特征:")
        for col in factor_cols + ['超跌分', '动态阈值', '中报增速%']:
            sl_mean = sl[col].mean()
            tp_mean = tp[col].mean() if total_tp > 0 else 0
            print(f"      {col:<12}: 止损均值={sl_mean:>8.1f} | 止盈均值={tp_mean:>8.1f} | 差值={sl_mean-tp_mean:+8.1f}")

    # 止损最快/最慢的股票
    if total_sl > 0:
        # 计算止损前的持仓天数（通过最大浮亏出现时间粗略估计）
        print(f"\n    止损信号Top5亏损:")
        top5_sl = sl.nlargest(5, '最大浮亏%')
        for _, r in top5_sl.iterrows():
            print(f"      {r['股票']}({r['信号日期']}): 入场{r['入场价']} | 20日收益{r['20日收益%']:+.1f}% | 最大浮亏{r['最大浮亏%']:.1f}% | F1={r['F1回撤深度']:.0f} F2={r['F2缩量程度']:.0f} F4={r['F4_RSI超卖']:.0f}")

# ═══════════════════════════════════════════
# 9. 综合诊断
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  9. 多维度交叉分析 — 找出最差的信号组合")
print("─"*80)

for label, m in [('3月', mar), ('5月', may)]:
    print(f"\n  [{label}]")
    # 低F1回撤 + 低F2缩量 = 回调不够+卖压未衰竭
    poor_f1f2 = m[(m['F1回撤深度'] < 60) & (m['F2缩量程度'] < 60)]
    # 低F4 RSI + 低F5 K线 = RSI未超卖+无K线止跌
    poor_f4f5 = m[(m['F4_RSI超卖'] < 50) & (m['F5_K线止跌'] < 50)]
    # 低F7趋势保护
    weak_trend = m[m['F7趋势保护'] < 50]
    # F1极端高（回调过深）
    over_drawdown = m[m['F1回撤深度'] >= 95]
    # 动态阈值低 + 胜率
    low_thresh = m[m['动态阈值'] < 80]

    for desc, subset in [
        ('F1<60 且 F2<60（回调不足+卖压未衰竭）', poor_f1f2),
        ('F4<50 且 F5<50（RSI未超卖+无止跌K线）', poor_f4f5),
        ('F7<50（趋势保护不足）', weak_trend),
        ('F1>=95（回调过深）', over_drawdown),
        ('动态阈值<80（低门槛信号）', low_thresh),
    ]:
        if len(subset) > 0:
            total = len(subset)
            wins = (subset['是否盈利'] == '是').sum()
            wr = wins / total * 100
            print(f"    {desc:<40}: {total:3d}个 | 胜率{wr:5.1f}%")
        else:
            print(f"    {desc:<40}: 无数据")

# ═══════════════════════════════════════════
# 10. 结论与建议
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  10. 总结与理论优化建议")
print("─"*80)

print("""
  3月核心问题:
    1. 震荡市环境下，胜率普遍偏低(信号多但质量参差)
    2. 止损信号占比过高，止盈/止损比失衡
    3. F7趋势保护的区分度不足（大部分信号趋势保护好但依然亏损）
    
  5月核心问题:
    1. 震荡偏强+强势市场环境下，信号最多但胜率不升反降
    2. 部分信号入场后遭遇快速回调，最大浮亏大
    3. 高增速（>500%）的信号反而不如中等增速的稳定

  理论优化方向:
""")

# 分析两个月份联合的可行改进
both = pd.concat([mar, may])
print(f"  【方向1 — 提升回撤深度门槛】")
print(f"    现状: 当前震荡市最优回撤区间为8-15%")
f1_bins = [0, 3, 5, 8, 10, 12, 15, 20, 30]
for i in range(len(f1_bins)-1):
    lo, hi = f1_bins[i], f1_bins[i+1]
    sub = both[(both['F1回撤深度'] >= lo) & (both['F1回撤深度'] < hi)]
    if len(sub) > 0:
        wr = (sub['是否盈利'] == '是').mean() * 100
        n = len(sub)
        avg20 = sub['20日收益%'].mean()
        print(f"      F1[{lo:2d}-{hi:2d}]: {n:3d}个 | 胜率{wr:5.1f}% | 平均20日{avg20:+7.2f}%")

print(f"\n  【方向2 — 提高缩量要求F2>70分才入场】")
for th in [60, 70, 80, 90]:
    sub = both[both['F2缩量程度'] >= th]
    if len(sub) > 0:
        wr = (sub['是否盈利'] == '是').mean() * 100
        n = len(sub)
        avg20 = sub['20日收益%'].mean()
        print(f"      F2>={th}: {n:3d}个 | 胜率{wr:5.1f}% | 平均20日{avg20:+7.2f}%")

print(f"\n  【方向3 — F4(RSI超卖)+F5(K线止跌)双共振过滤】")
for f4_th in [50, 60, 70]:
    for f5_th in [50, 60]:
        sub = both[(both['F4_RSI超卖'] >= f4_th) & (both['F5_K线止跌'] >= f5_th)]
        if len(sub) > 0:
            wr = (sub['是否盈利'] == '是').mean() * 100
            n = len(sub)
            print(f"      F4>={f4_th} & F5>={f5_th}: {n:3d}个 | 胜率{wr:5.1f}%")

print(f"\n  【方向4 — 按市场状态差异化设置F1/F2/F4最低分】")
for regime in ['震荡市', '震荡偏强', '强势市场']:
    sub = both[both['市场状态'] == regime]
    if len(sub) == 0:
        continue
    for f1_min in [60, 70]:
        for f2_min in [60, 70]:
            sub_f = sub[(sub['F1回撤深度'] >= f1_min) & (sub['F2缩量程度'] >= f2_min)]
            if len(sub_f) > 0:
                wr = (sub_f['是否盈利'] == '是').mean() * 100
                n = len(sub_f)
                total = len(sub)
                print(f"      {regime} F1>={f1_min} F2>={f2_min}: {n:3d}/{total:3d}个 | 胜率{wr:5.1f}%")

# ═══════════════════════════════════════════
# 11. 最差个股分析
# ═══════════════════════════════════════════
print(f"\n{'─'*80}")
print("  11. 表现最差个股（3月+5月联合，信号≥2且胜率0%）")
print("─"*80)

stock_stats = both.groupby(['股票', '代码']).agg(
    信号数=('是否盈利', 'count'),
    胜率=('是否盈利', lambda x: (x == '是').mean() * 100),
    平均20日收益=('20日收益%', 'mean'),
    中报增速=('中报增速%', 'mean'),
).reset_index()
worst = stock_stats[(stock_stats['信号数'] >= 2) & (stock_stats['胜率'] == 0)].sort_values('平均20日收益')
for _, r in worst.iterrows():
    print(f"    {r['股票']:<8}({r['代码']:<12}): 信号{r['信号数']}次 | 胜率{r['胜率']:.0f}% | 平均20日{r['平均20日收益']:+.1f}% | 增速{r['中报增速']:.0f}%")

print(f"\n{'─'*80}")
print("  分析完成")
print("─"*80)
