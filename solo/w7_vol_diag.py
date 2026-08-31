# -*- coding: utf-8 -*-
"""诊断：98%分位天量为什么这么多？检查换手率/成交量数据质量与分位分布"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from w7_second_wave_engine import CacheReader, extreme_event, percentile_rank, MIN_BARS, MAX_EVENT_AGE

reader = CacheReader()
# 取全市场股票列表
q = "SELECT DISTINCT ts_code FROM stk_factor_pro"
codes = [r[0] for r in reader.conn.execute(q)]
print(f"全市场股票数: {len(codes)}")

total_have_event = 0
p_turn_only = 0
p_vol_only = 0
both = 0
turn_zero_ratio_list = []
sample_events = []

for code in codes:
    df = reader.bars_sql(code, "20260828")
    if df is None or len(df) < MIN_BARS + 20:
        continue
    # 换手率0值/缺失占比
    tr = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0)
    turn_zero_ratio_list.append((code, float((tr <= 0).mean()), len(df)))
    # 最近60天窗口扫描
    found = False
    for i in range(max(MIN_BARS, len(df) - MAX_EVENT_AGE - 1), len(df) - 2):
        ok, ep = extreme_event(df, i)
        if ok:
            # 单独看 turn/vol 各自分位
            tr_i = float(df.iloc[i].turnover_rate_f) if pd.notna(df.iloc[i].turnover_rate_f) else 0.0
            vol_i = float(df.iloc[i].vol) if pd.notna(df.iloc[i].vol) else 0.0
            start = 0
            turns = pd.to_numeric(df.turnover_rate_f.iloc[start:i], errors="coerce").fillna(0).values
            vols = pd.to_numeric(df.vol.iloc[start:i], errors="coerce").fillna(0).values
            pt = percentile_rank(turns, tr_i)
            pv = percentile_rank(vols, vol_i)
            if pt >= 98 and pv >= 98:
                both += 1
            elif pt >= 98:
                p_turn_only += 1
            else:
                p_vol_only += 1
            if len(sample_events) < 15:
                sample_events.append((code, df.iloc[i].trade_date, round(pt,1), round(pv,1), round(tr_i,2), int(vol_i)))
            found = True
            break
    if found:
        total_have_event += 1

print(f"\n=== 最近{MAX_EVENT_AGE}天窗口内有 P98 天量事件的股票: {total_have_event} ===")
print(f"  仅换手率达标: {p_turn_only}  仅成交量达标: {p_vol_only}  两者都达: {both}")

# 换手率0值分布
nz = [(c, r, n) for c, r, n in turn_zero_ratio_list if r > 0.5]
print(f"\n=== 换手率0值/缺失占比>50% 的股票数: {len(nz)} / {len(turn_zero_ratio_list)} ===")
if nz[:5]:
    for c, r, n in nz[:5]:
        print(f"  {c}: 0值占比{r:.0%}, K线{n}根")

# 样例事件
print("\n=== 事件样例（分位来源）===")
for s in sample_events:
    print(f"  {s[0]} {s[1]}: p_turn={s[2]:.0f} p_vol={s[3]:.0f} 换手={s[4]:.2f}% vol={s[5]}")

reader.close()
