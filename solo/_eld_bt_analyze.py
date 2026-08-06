# -*- coding: utf-8 -*-
"""ELD 买点回测 - 深度稳健性分析（基于已入库信号库）"""
import sqlite3
import pandas as pd
import numpy as np

BT_DB = r'D:\mystock\cache_daily\eld_buy_backtest_tdx.db'
conn = sqlite3.connect(BT_DB)
df = pd.read_sql_query("SELECT * FROM eld_buy_signals", conn)
conn.close()

print(f"总信号: {len(df)}")
print(f"信号日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")

# 只保留有完整 T+10 未来数据的样本（排除 202608 + 部分202607末）
df_full = df[df['t10'].notna()].copy()
print(f"有完整T+10样本: {len(df_full)} ({df_full['trade_date'].min()} ~ {df_full['trade_date'].max()})")

def stats(g):
    n = len(g)
    return dict(n=n,
                t1_wr=(g['t1'] > 0).mean() * 100, t1_mean=g['t1'].mean() * 100,
                t5_wr=(g['t5'] > 0).mean() * 100, t5_mean=g['t5'].mean() * 100,
                t10_wr=(g['t10'] > 0).mean() * 100, t10_mean=g['t10'].mean() * 100)

def show(title, g, cols=('n', 't1_wr', 't1_mean', 't5_mean', 't10_mean', 't10_wr')):
    print(f"\n===== {title} =====")
    if isinstance(g, pd.DataFrame):
        g = [('all', g)]
    hdr = f"{'分组':<22}" + "".join(f"{c:>10}" for c in cols)
    print(hdr)
    for k, v in g:
        s = stats(v)
        print(f"{str(k):<22}" + "".join(f"{s[c]:>9.2f}" if isinstance(s[c], float) else f"{s[c]:>10}" for c in cols))

# 1. 总体（有完整T+10）
show("总体（完整T+10样本）", df_full)

# 2. ELD场景 vs 非（完整样本）
eld = df_full[df_full['in_eld_window'] == True]
non = df_full[df_full['in_eld_window'] == False]
show("ELD场景 vs 非", [('ELD预增窗口', eld), ('非窗口', non)])

# 3. ELD场景内 按质量分段
def qb(q):
    if q >= 90: return '90+'
    if q >= 80: return '80-90'
    if q >= 70: return '70-80'
    if q >= 60: return '60-70'
    return '50-60'
if len(eld) > 0:
    qg = sorted(eld.groupby(eld['quality'].apply(qb)),
                key=lambda x: 100 if x[0] == '90+' else int(x[0].split('-')[0]), reverse=True)
    show("ELD场景内 按质量分段", qg)

    # 4. ELD场景内 按乖离区间
    def bb(b):
        if b > 10: return '>10'
        if b > 5: return '5~10'
        if b > 0: return '0~5'
        if b > -2: return '-2~0'
        return '<-2'
    bg = sorted(eld.groupby(eld['bias'].apply(bb)),
                key=lambda x: -stats(x[1])['t1_mean'])
    show("ELD场景内 按乖离区间(T+1降序)", bg)

    # 5. ELD场景内 按量比区间
    def vb(v):
        if v < 0.6: return '<0.6'
        if v < 0.8: return '0.6~0.8'
        if v < 1.0: return '0.8~1.0'
        return '>=1.0'
    vg = sorted(eld.groupby(eld['vol_ratio'].apply(vb)),
                key=lambda x: -stats(x[1])['t1_mean'])
    show("ELD场景内 按量比区间(T+1降序)", vg)

    # 6. ELD场景内 质量≥80 单独看
    eld_buy = eld[eld['quality'] >= 80]
    show("ELD场景内 BUY(质量≥80)", eld_buy)

# 7. 全市场 质量分段（完整样本）对照
show("全市场 按质量分段", sorted(df_full.groupby(df_full['quality'].apply(qb)),
     key=lambda x: 100 if x[0] == '90+' else int(x[0].split('-')[0]), reverse=True))

# 8. 全市场 按量比（验证缩量因子方向）
show("全市场 按量比", sorted(df_full.groupby(df_full['vol_ratio'].apply(vb)),
     key=lambda x: -stats(x[1])['t1_mean']))

# 9. 按月（完整样本）
mg = sorted(df_full.groupby(df_full['trade_date'].str[:6]))
show("按月（完整T+10）", mg)
