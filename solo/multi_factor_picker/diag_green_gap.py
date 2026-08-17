# -*- coding: utf-8 -*-
"""诊断: 各交易日绿灯信号每层过滤拦截情况"""
import os
import pandas as pd

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
DAYS = ['20260803', '20260804', '20260805', '20260806', '20260807',
        '20260810', '20260811', '20260812', '20260813', '20260814']


def parse_growth(v):
    s = str(v).replace('%', '').replace('+', '').strip()
    try:
        return float(s)
    except Exception:
        return float('nan')


for d in DAYS:
    fp = os.path.join(REPORT_DIR, f'enhanced_timing_bull_all_{d}.csv')
    if not os.path.exists(fp):
        print(f'{d}: 无数据文件')
        continue
    df = pd.read_csv(fp, encoding='utf-8-sig')
    n_all = len(df)
    has_op = '次日操作' in df.columns
    if not has_op:
        print(f'{d}: 旧版无次日操作字段 (n={n_all})')
        continue
    growth = pd.to_numeric(df['中报业绩亮点'].apply(parse_growth), errors='coerce')
    m1 = (df['次日操作'] == '✅ 次日可买入')
    m2 = m1 & (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False))
    m3 = m2 & (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0)
    m4 = m3 & (df['修正后胜率分级'].isin(['S', 'A', 'B']) | (growth.fillna(-1) > 0))
    # 补充: 若无绿灯, 看看"观察"类信号有多少
    obs = (df['次日操作'].astype(str).str.contains('观察', na=False))
    obs_grade = obs & df['修正后胜率分级'].isin(['S', 'A', 'B'])
    obs_grade_good = obs_grade & (growth.fillna(-1) > 0)
    print(f'{d}: 总数{n_all} | ✅次日可买入{m1.sum()} | +无冲击{m2.sum()} | +评分>0{m3.sum()} | +评级/业绩(绿灯){m4.sum()} | 观察类{obs.sum()} | 观察且S/A/B{obs_grade.sum()} | +业绩正{obs_grade_good.sum()}')
    if m1.sum() > 0 and m4.sum() == 0:
        blocked = df[m1][~m3]
        for _, r in blocked.iterrows():
            print(f'    拦截: {r["名称"]} 评级{r["修正后胜率分级"]} 业绩{r["中报业绩亮点"]} 冲击{r["兑现冲击过滤"]} 评分{r.get("修正后评分")} 决策{r["交易决策"]}')
