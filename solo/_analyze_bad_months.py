import pandas as pd, numpy as np

df = pd.read_csv('report_daily/backtest_oversold_dynamic_20260724.csv', encoding='utf-8-sig')

mar = df[df['信号日期'].astype(str).str.startswith('202603')].copy()
may = df[df['信号日期'].astype(str).str.startswith('202605')].copy()

print('='*70)
print(f'3月信号: {len(mar)}个, 胜率: {(mar["是否盈利"]=="是").mean()*100:.1f}%')
print(f'5月信号: {len(may)}个, 胜率: {(may["是否盈利"]=="是").mean()*100:.1f}%')
print()

for label, m in [('3月', mar), ('5月', may)]:
    print(f'── {label} 市场状态分布 ──')
    print(m['市场状态'].value_counts().to_string())
    print()
    
    print(f'── {label} 触发结果分布 ──')
    print(m['触发结果'].value_counts().to_string())
    print()
    
    print(f'── {label} 平均因子分 ──')
    for col in ['超跌分','F1回撤深度','F2缩量程度','F3支撑强度','F4_RSI超卖','F5_K线止跌','F6基本面锚定','F7趋势保护','动态阈值']:
        vals = m[col].dropna()
        if len(vals) > 0:
            print(f'  {col}: {vals.mean():.1f}')
    print()
    
    # 20日收益分布
    print(f'── {label} 20日收益分布 ──')
    rets = m['20日收益%'].dropna()
    print(f'  均值: {rets.mean():+.2f}%  中位数: {rets.median():+.2f}%')
    print(f'  正收益占比: {(rets>0).mean()*100:.1f}%')
    print(f'  最大: {rets.max():+.2f}%  最小: {rets.min():+.2f}%')
    r25, r75 = rets.quantile(0.25), rets.quantile(0.75)
    print(f'  25分位: {r25:+.2f}%  75分位: {r75:+.2f}%')
    print()
    
    # 胜/负各自因子对比
    win = m[m['是否盈利']=='是']
    loss = m[m['是否盈利']=='否']
    print(f'── {label} 盈利vs亏损因子对比 ──')
    for col in ['超跌分','F1回撤深度','F2缩量程度','F3支撑强度','F4_RSI超卖','F5_K线止跌','动态阈值']:
        wv = win[col].dropna().mean() if len(win)>0 else 'N/A'
        lv = loss[col].dropna().mean() if len(loss)>0 else 'N/A'
        print(f'  {col}: 盈利={wv}  亏损={lv}')
    print()
    
    # 极端值对比
    print(f'── {label} 浮盈浮亏 ──')
    print(f'  盈利组 最大浮盈={win["最大浮盈%"].mean():.1f}%  最大浮亏={win["最大浮亏%"].mean():.1f}%')
    print(f'  亏损组 最大浮盈={loss["最大浮盈%"].mean():.1f}%  最大浮亏={loss["最大浮亏%"].mean():.1f}%')
    print()

# 检查有信号的日期分布
print('='*70)
print('信号日期分布')
for label in ['202603', '202605']:
    m = df[df['信号日期'].astype(str).str.startswith(label)]
    dates = sorted(m['信号日期'].astype(str).unique())
    print(f'\n{label} 有信号天数: {len(dates)}天')
    print(f'  日期范围: {dates[0]} ~ {dates[-1]}')
    
    # 每天信号的平均收益
    daily = m.groupby('信号日期').agg(
        信号数=('是否盈利','count'),
        胜率=('是否盈利', lambda x: (x=='是').mean()*100),
        平均20日收益=('20日收益%','mean')
    )
    # 胜率<40%的天数
    bad_days = daily[daily['胜率'] < 40]
    good_days = daily[daily['胜率'] >= 60]
    print(f'  胜率<40%: {len(bad_days)}天  胜率>=60%: {len(good_days)}天')
    
    # 连续亏损天数
    daily_sorted = daily.sort_index()
    consec_loss = 0
    max_consec_loss = 0
    for _, row in daily_sorted.iterrows():
        if row['胜率'] < 40:
            consec_loss += 1
            max_consec_loss = max(max_consec_loss, consec_loss)
        else:
            consec_loss = 0
    print(f'  最长连续低胜率天数: {max_consec_loss}天')

print()
# 总体胜率 vs 阈值的关系
print('='*70)
print('胜率 vs 阈值分析（3+5月合并）')
combined = pd.concat([mar, may])
for thresh in sorted(combined['动态阈值'].unique()):
    sub = combined[combined['动态阈值'] == thresh]
    wr = (sub['是否盈利']=='是').mean()*100
    print(f'  阈值={thresh}: {len(sub)}个信号, 胜率{wr:.1f}%')
