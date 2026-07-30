# -*- coding: utf-8 -*-
"""验证洗盘修复TOP 5的择时信号是否符合算法条件"""
import pandas as pd

df = pd.read_csv('d:/mystock/solo/report_daily/enhanced_timing_bull_all_20260729.csv', encoding='utf-8-sig')

# 精选标的：S/A级 + 洗盘修复分>=80 + 无兑现冲击
top5 = df[
    (df['修正后胜率分级'].isin(['S', 'A'])) &
    (df['洗盘修复分'] >= 80) &
    (df['兑现冲击过滤'].str.contains('✅'))
].sort_values('洗盘修复分', ascending=False).head(5)

print(f'共找到 {len(top5)} 只精选标的')
print()

check_cols = ['名称','代码','修正后胜率分级','洗盘修复分','量化择时分','修正后评分',
              '真突破判定','回踩确认','VWAP突破','筹码峰突破','VWAP','现价','MA20',
              'ATR动态止损价','ATR跟踪止盈价','推荐买点类型','交易决策']

for _, r in top5.iterrows():
    price = r['现价']
    vwap = r['VWAP']
    ma20 = r['MA20']
    stop_loss = r['ATR动态止损价']
    trail_stop = r['ATR跟踪止盈价']
    
    # 计算关键指标
    dev_vwap = (price - vwap) / vwap * 100 if vwap else 0
    dev_ma20 = (price - ma20) / ma20 * 100 if ma20 else 0
    profit_risk = (trail_stop - price) / (price - stop_loss) if stop_loss and (price - stop_loss) > 0 else 0
    
    print('=' * 60)
    print(f'{r["名称"]}({r["代码"]})  评级={r["修正后胜率分级"]}  洗盘修复分={r["洗盘修复分"]:.0f}')
    print('=' * 60)
    print(f'  量化择时分: {r["量化择时分"]:.1f}  (S级需>=85, A级需>=75)')
    print(f'  修正后评分: {r["修正后评分"]:.1f}')
    print()
    
    # 条件1: 真突破 = VWAP突破 + 筹码峰突破
    true_bt = '✅' in str(r['真突破判定'])
    vwap_bt = r['VWAP突破'] == '是'
    chip_bt = r['筹码峰突破'] == '是'
    print(f'  ■ 条件1: 真突破判定 = {r["真突破判定"]}')
    print(f'      VWAP突破: {"✅" if vwap_bt else "❌"}  筹码峰突破: {"✅" if chip_bt else "❌"}')
    print(f'      → 结论: {"通过" if true_bt else "不通过"}')
    
    # 条件2: 回踩确认
    pc = r['回踩确认'] == '✅ 是'
    print(f'  ■ 条件2: 回踩确认 = {r["回踩确认"]}')
    print(f'      → 结论: {"通过" if pc else "不通过"}')
    
    # 条件3: 价格相对位置
    print(f'  ■ 条件3: 价格位置')
    print(f'      VWAP={vwap:.2f}  现价={price:.2f}  偏离VWAP: {dev_vwap:+.2f}%')
    print(f'      MA20={ma20:.2f}  现价={price:.2f}  偏离MA20: {dev_ma20:+.2f}%')
    
    # 条件4: 风险收益比
    print(f'  ■ 条件4: 风报比')
    print(f'      止损价={stop_loss:.2f}  止盈价={trail_stop:.2f}')
    print(f'      风险={price-stop_loss:.2f}  收益={trail_stop-price:.2f}  盈亏比={profit_risk:.2f}')
    
    # 条件5: 评级判定
    print(f'  ■ 条件5: 修正后胜率分级 = {r["修正后胜率分级"]}')
    print(f'      → 算法条件: S = 真突破+回踩确认+量化分>=85')
    print(f'                 A = 真突破+量化分>=75')
    print(f'                 B = VWAP突破+量化分>=60')
    
    # 综合验证
    print(f'  ■ 综合验证:')
    if true_bt and pc and r['量化择时分'] >= 85:
        if r['修正后胜率分级'] == 'S':
            print(f'      ✅ S级条件全部满足')
        else:
            print(f'      ⚠️ 满足S级条件但被评{r["修正后胜率分级"]}')
    elif true_bt and r['量化择时分'] >= 75:
        if r['修正后胜率分级'] == 'A':
            print(f'      ✅ A级条件全部满足')
        else:
            print(f'      ⚠️ 满足A级条件但被评{r["修正后胜率分级"]}')
    elif vwap_bt and r['量化择时分'] >= 60:
        print(f'      ✅ B级条件满足')
    else:
        print(f'      ❌ 条件不满足')
    
    # 修正分限制（兑现冲击）
    if r['兑现冲击过滤'] != '✅ 否':
        print(f'      ⚠️ 有兑现冲击，修正分上限50')
    
    print(f'  ── 买点: {r["推荐买点类型"]}')
    print(f'  ── 决策: {r["交易决策"]}')
    print()

# 闰土股份也验证
print()
print('=' * 60)
print(f'【参考】闰土股份(002440.SZ) — S级龙头')
print('=' * 60)
rt = df[df['名称'].str.strip() == '闰土股份'].iloc[0]
print(f'  量化择时分: {rt["量化择时分"]:.1f}')
print(f'  修正后评分: {rt["修正后评分"]:.1f}')
print(f'  真突破: {rt["真突破判定"]}')
print(f'  回踩确认: {rt["回踩确认"]}')
print(f'  洗盘修复分: {rt["洗盘修复分"]:.0f}')
print(f'  推荐买点: {rt["推荐买点类型"]}')
print(f'  交易决策: {rt["交易决策"]}')

# 统计S/A级中洗盘修复分高的数量
print()
print('=' * 60)
print('S/A级 + 洗盘修复分>=80 + 无兑现冲击 统计')
print('=' * 60)
filtered = df[
    (df['修正后胜率分级'].isin(['S', 'A'])) &
    (df['洗盘修复分'] >= 80) &
    (df['兑现冲击过滤'].str.contains('✅'))
]
for _, r in filtered.iterrows():
    print(f'  [{r["修正后胜率分级"]}] {r["名称"]:8s}  修复分={r["洗盘修复分"]:.0f}  量化分={r["量化择时分"]:.1f}  真突破={r["真突破判定"]}  回踩={r["回踩确认"]}')
print(f'  共 {len(filtered)} 只')
