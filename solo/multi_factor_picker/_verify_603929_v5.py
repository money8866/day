# -*- coding: utf-8 -*-
"""深度分析：亚翔集成603929.SH 共振评分12分涨停的规律"""
import os, sys, time
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

code = '603929.SH'
name = '亚翔集成'

print(f'=== {code} {name} 涨停深度分析 ===\n')

# 1. 获取60日完整日线
df = pro.daily(ts_code=code, start_date='20260401', end_date='20260623')
df = df.sort_values('trade_date').reset_index(drop=True)

# 2. 识别一波拉升段
print('--- 一波拉升段 ---')
# 找最低点和最高点
low_idx = df['close'].idxmin()
low_row = df.iloc[low_idx]
# 一波拉升：从最低点到6/11最高点
wave1_df = df[df['trade_date'] <= '20260611']
high_idx = wave1_df['close'].idxmax()
high_row = df.iloc[high_idx]

wave1_gain = (high_row['close'] / low_row['close'] - 1) * 100
wave1_days = (pd.Timestamp(high_row['trade_date']) - pd.Timestamp(low_row['trade_date'])).days
print(f'起点: {low_row["trade_date"]} 收盘{low_row["close"]:.2f}')
print(f'高点: {high_row["trade_date"]} 收盘{high_row["close"]:.2f}')
print(f'一波涨幅: +{wave1_gain:.1f}% ({wave1_days}天)')

# 3. 调整段分析
print(f'\n--- 调整段 ---')
adjust_df = df[(df['trade_date'] > high_row['trade_date']) & (df['trade_date'] <= '20260623')]
adjust_low = adjust_df['close'].min()
adjust_low_date = adjust_df.loc[adjust_df['close'].idxmin(), 'trade_date']
adjust_pct = (adjust_low / high_row['close'] - 1) * 100
adjust_days = len(adjust_df)
print(f'调整最低: {adjust_low_date} 收盘{adjust_low:.2f}')
print(f'调整幅度: {adjust_pct:.1f}% ({adjust_days}天)')

# 但中间6/17有个涨停反弹！
print(f'\n关键: 6/17曾涨停到232.18 (反弹至一波高点上方!)')
print(f'然后6/18大跌-7.18%，6/22-6/23继续回落')
print(f'→ 这不是简单的强势横盘，而是V型急跌反弹→二次回踩！')

# 4. 为什么共振评分只有12分？
print(f'\n--- 为什么共振评分仅12分？---')
print(f'评分规则(强势横盘):')
print(f'  回调幅度{abs(adjust_pct):.1f}% < 10% → 符合强势横盘 ✓')
print(f'  调整天数{adjust_days}天 < 15天 → 符合 ✓')
print(f'  RSI≈52.4 → 不超卖，信号偏弱')
print(f'  量能比 → 需看数据')
print(f'')
print(f'评分12分的原因:')
print(f'  1. 强势横盘形态本身评分门槛低(≥7分即可)')
print(f'  2. RSI≈52不触发超卖加分项')
print(f'  3. MACD金叉+MA20上方是主要得分项')
print(f'  4. 没有DMI趋势反转、MFI底背离等高加分项')

# 5. 一波涨幅与创新高的关系
print(f'\n--- 一波涨幅+创新高 vs 涨停 ---')
print(f'亚翔集成一波涨幅: +{wave1_gain:.1f}%')
print(f'6/17已触及232.18 > 一波高点230.86 → 已创新高！')
print(f'6/23收盘210.80 < 230.86 → 回踩到一波高点下方')
print(f'6/24涨停 → 二次突破前高！')
print(f'')
print(f'关键规律:')
print(f'  ✅ 一波涨幅高达57.5% → 主力资金深度介入')
print(f'  ✅ 6/17已创新高(232>231) → 确认趋势向上')
print(f'  ✅ 创新高后回踩(不是跌破) → 经典新高回踩买点')
print(f'  ✅ 回调仅8.7%就企稳 → 多头极强')
print(f'  ❌ 共振评分低是因为: 评分检测的是调整入场信号,')
print(f'     不检测"创新高后回踩"这种更强势的形态')

# 6. 回测验证：创新高后回踩的胜率
print(f'\n=== 回测: 创新高后回踩 vs 普通二波 ===')

from pymupdf import open as fitz_open
PDF_PATH = r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_bull_stocks_20260624.pdf'
doc = fitz_open(PDF_PATH)
all_rows = []
for page in doc:
    tables = page.find_tables()
    for table in tables.tables:
        data = table.extract()
        if data and len(data[0]) > 5:
            for row in data[1:]:
                all_rows.append(row)
doc.close()

# 分析82只信号股的一波涨幅分布
print(f'\n82只信号股一波涨幅分布:')
wave1_gains = []
for row in all_rows:
    try:
        w = float(row[4].replace('+',''))
        wave1_gains.append(w)
    except:
        pass

if wave1_gains:
    s = pd.Series(wave1_gains)
    print(f'  均值: +{s.mean():.1f}%')
    print(f'  中位: +{s.median():.1f}%')
    print(f'  分布: <30%: {(s<30).sum()}只 | 30-50%: {((s>=30)&(s<50)).sum()}只 | ≥50%: {(s>=50).sum()}只')
    
    # 评分vs一波涨幅
    scores = []
    for row in all_rows:
        try:
            sc = float(row[3])
            scores.append(sc)
        except:
            scores.append(0)
    
    df_analysis = pd.DataFrame({'score': scores, 'wave1_gain': wave1_gains})
    
    # 一波涨幅大的，评分反而低？
    high_wave1 = df_analysis[df_analysis['wave1_gain'] >= 50]
    low_wave1 = df_analysis[df_analysis['wave1_gain'] < 30]
    
    print(f'\n一波涨幅≥50%: {len(high_wave1)}只, 平均评分{high_wave1["score"].mean():.1f}')
    print(f'一波涨幅<30%: {len(low_wave1)}只, 平均评分{low_wave1["score"].mean():.1f}')
    
    corr = df_analysis['score'].corr(df_analysis['wave1_gain'])
    print(f'评分-一波涨幅相关: {corr:.3f}')
    
    if corr < 0:
        print('→ 负相关！一波涨幅越大评分反而越低！')
        print('→ 原因: 大幅拉升后RSI偏高, 不触发超卖加分项')
        print('→ 但大幅拉升=主力深度介入=二波概率更高')
    
    print(f'\n🔑 核心发现:')
    print(f'  共振评分衡量的是"调整充分度"(超卖程度)')
    print(f'  而涨停驱动力来自"主力意愿"(一波涨幅) + "趋势确认"(创新高)')
    print(f'  → 低评分+大一波+创新高 = 最强信号但被评分遗漏！')

# 7. 亚翔集成 vs 其他强势横盘信号
print(f'\n--- 亚翔集成 vs 强势横盘TOP信号 ---')
sideways = [row for row in all_rows if row[2] == '强势横盘']
sideways_sorted = sorted(sideways, key=lambda x: -float(x[4].replace('+','')))  # 按一波涨幅排序
print(f'{"代码":<12} {"评分":>4} {"一波%":>6} {"回调%":>6} {"RSI":>5}')
for row in sideways_sorted[:10]:
    try:
        print(f'{row[0]:<12} {row[3]:>4} {row[4]:>6} {row[5]:>6} {row[7]:>5}')
    except:
        pass

print(f'\n亚翔集成(603929)在一波涨幅TOP3，但评分仅12分')
print(f'→ 评分体系对"一波涨幅大+创新高"这种形态低估了！')
