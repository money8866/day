# -*- coding: utf-8 -*-
"""EGPT 8月绿灯信号(次日可买入·双确认)复盘：信号日收盘 -> 8/17收盘（含期间峰值）"""
import os
import pandas as pd

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
DAYS = ['20260803', '20260804', '20260805', '20260806', '20260807',
        '20260810', '20260811', '20260812', '20260813', '20260814']

# 各股日收盘价 (ms -> close)，与历史K线一致
DATES = ['20260803','20260804','20260805','20260806','20260807',
         '20260810','20260811','20260812','20260813','20260814','20260817']
CLOSES = {
 '688202.SH': [68.88,78.01,79.16,81.80,95.10,103.10,103.70,106.00,107.35,108.76,107.36],
 '688293.SH': [48.79,54.83,54.60,53.76,63.22,66.85,63.71,61.84,67.53,66.37,66.50],
 '688046.SH': [23.39,26.62,27.26,27.76,33.31,37.39,39.52,38.60,40.95,40.50,40.18],
 '603002.SH': [11.75,12.51,13.52,14.04,15.05,15.18,14.93,15.31,15.13,15.99,17.59],
 '688655.SH': [29.39,31.57,33.16,34.10,38.66,35.71,35.14,37.32,36.05,37.75,42.78],
}
DATE_INDEX = {d: i for i, d in enumerate(DATES)}
IDX_17 = DATE_INDEX['20260817']


def parse_growth(v):
    s = str(v).replace('%', '').replace('+', '').strip()
    try:
        return float(s)
    except Exception:
        return float('nan')


rows = []
for d in DAYS:
    fp = os.path.join(REPORT_DIR, f'enhanced_timing_bull_all_{d}.csv')
    if not os.path.exists(fp):
        continue
    df = pd.read_csv(fp, encoding='utf-8-sig')
    if '次日操作' not in df.columns:
        continue
    growth = pd.to_numeric(df['中报业绩亮点'].apply(parse_growth), errors='coerce')
    # 复现 push_washout_recovery.py buy_mask
    buy = df[
        (df['次日操作'] == '✅ 次日可买入') &
        (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
        (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
        (df['修正后胜率分级'].isin(['S', 'A', 'B']) | (growth.fillna(-1) > 0))
    ].sort_values('回踩买点分', ascending=False)
    for _, r in buy.iterrows():
        code = str(r['代码'])
        ref = float(r['现价'])
        close17 = CLOSES[code][IDX_17]
        ret = (close17 / ref - 1) * 100
        window = CLOSES[code][DATE_INDEX[d]+1:IDX_17+1]
        peak = max(window) if window else close17
        peak_ret = (peak / ref - 1) * 100
        rows.append({
            '信号日': d, '代码': code, '名称': r['名称'], '评级': r['修正后胜率分级'],
            '回踩买点分': r['回踩买点分'], '业绩%': r['中报业绩亮点'],
            '信号日收盘': ref, '8/17收盘': close17, '累计收益%': round(ret, 1),
            '期间峰值%': round(peak_ret, 1), '交易决策': r['交易决策'],
        })

df = pd.DataFrame(rows)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 250)
print(df.to_string(index=False))

if len(df):
    print(f"\n===== 汇总({len(df)}笔) =====")
    print(f"累计收益 均值 {df['累计收益%'].mean():+.1f}% 中位 {df['累计收益%'].median():+.1f}% 胜率 {(df['累计收益%']>0).mean()*100:.0f}%")
    print(f"期间峰值 均值 {df['期间峰值%'].mean():+.1f}% 中位 {df['期间峰值%'].median():+.1f}%")
    print(f"止损触发(跌超系统止损): {((df['信号日收盘']*0.82)>df['8/17收盘']).sum()}")  # 粗略参考
    df.to_csv(os.path.join(REPORT_DIR, 'egpt_aug_green_review.csv'), index=False, encoding='utf-8-sig')
    print(f"\n已保存: report_daily/egpt_aug_green_review.csv")
