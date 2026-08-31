# -*- coding: utf-8 -*-
"""V3 PRIMARY 门控放松实验：当前门控 vs 放松扩张分门槛（§18 消融证据驱动）

数据源: 全量回测事件CSV(20250101-20260828), 口径与 backtest._v3_analysis 一致。
比较各组合的 T+20 右尾: n / win / mean / median / p75 / p90 / max / ge10 / ge20 / ge30 / ge50 / top10_avg / top5_avg
"""
import os
import sys
import json
import numpy as np
import pandas as pd

BASE = r"d:\mystock\solo"
sys.path.insert(0, BASE)
from hvt_bull.backtest import _tail_stat

CSV = os.path.join(BASE, "report_daily", "hvt_bull_backtest_events_20250101_20260828.csv")


def load():
    df = pd.read_csv(CSV)
    if 'breakout_realtime' in df.columns:
        m = df['breakout_realtime'].astype(str).eq('True')
        if 'breakout' in df.columns:
            m &= df['breakout'].astype(str).eq('True')
        if 'false_breakout' in df.columns:
            m &= ~df['false_breakout'].astype(str).eq('True')
        df = df[m].copy()
    return df


def parse_subs(series):
    out = pd.DataFrame(index=series.index)
    for key in ('扩张空间', '压缩结构', '动量加速', 'RS加速', '量效',
                '供给吸收', '基本面加速', '催化剂'):
        vals = []
        for v in series:
            try:
                d = json.loads(v) if isinstance(v, str) and v.strip() else {}
                vals.append(float(d.get(key, 0) or 0))
            except Exception:
                vals.append(0.0)
        out[key] = vals
    return out


def main():
    x = load()
    print("事件样本(base 口径):", len(x))
    if 'v3_state' in x.columns:
        print("CSV中 v3_state 分布:", x['v3_state'].value_counts().to_dict())
    subs = parse_subs(x['exp_subs'])
    for c in subs.columns:
        x[c] = subs[c]
    x['xs'] = (x['扩张空间'] + x['压缩结构'] + x['动量加速'] + x['RS加速']
               + x['量效'] + x['供给吸收'] + x['基本面加速'] + x['催化剂'])
    x['es'] = pd.to_numeric(x['entry_score'], errors='coerce')
    x['supply'] = x['供给吸收']
    x['vgrade'] = x['volume_grade'].fillna('')
    x['rs20'] = pd.to_numeric(x['rs20'], errors='coerce')

    diff = (x['xs'] - pd.to_numeric(x['expansion_score'], errors='coerce')).abs()
    print("xs 与 expansion_score 列差异 中位数: %.4f (应≈0)" % diff.median())

    trio = x['supply'].ge(12) & x['vgrade'].isin(['A', 'A+']) & x['rs20'].ge(70)
    combos = {
        'current(es>=70&xs>=70&trio)': trio & x['es'].ge(70) & x['xs'].ge(70),
        'es>=70&xs>=60&trio': trio & x['es'].ge(70) & x['xs'].ge(60),
        'es>=70&xs>=55&trio': trio & x['es'].ge(70) & x['xs'].ge(55),
        'es>=70&trio(无扩张门控)': trio & x['es'].ge(70),
        'es>=65&xs>=55&trio': trio & x['es'].ge(65) & x['xs'].ge(55),
        'es>=70&xs>=70&无trio': x['es'].ge(70) & x['xs'].ge(70),
        'es>=60&xs>=60&无trio': x['es'].ge(60) & x['xs'].ge(60),
        'xs>=85(Rocket池)': x['xs'].ge(85),
        'base(全部事件)': pd.Series(True, index=x.index),
    }
    cols = ['n', 'win', 'mean', 'median', 'p75', 'p90', 'max',
            'ge10', 'ge20', 'ge30', 'ge50', 'top10_avg', 'top5_avg']
    print()
    print("组合 | " + " | ".join(cols))
    for name, mask in combos.items():
        sub = x[mask.reindex(x.index, fill_value=False)]
        st = _tail_stat(sub)
        line = name
        for c in cols:
            line += f" | {st.get(c, '')}"
        print(line)
    st = _tail_stat(x[trio])
    print()
    print("trio 独立: n=%d win=%.1f mean=%.2f median=%.2f p75=%.2f p90=%.1f "
          "ge20=%.1f ge30=%.1f ge50=%.1f top10_avg=%.2f top5_avg=%.2f" % (
              st['n'], st['win'], st['mean'], st['median'], st['p75'], st['p90'],
              st['ge20'], st['ge30'], st['ge50'], st['top10_avg'], st['top5_avg']))
    # 分年稳健性: 对三个放松候选分 2025/2026
    print()
    y = x['t0_date'].astype(str).str[:4]
    for name in ('es>=70&xs>=60&trio', 'es>=70&trio(无扩张门控)', 'es>=65&xs>=55&trio'):
        mask = combos[name]
        for yr in ('2025', '2026'):
            sub = x[(mask.reindex(x.index, fill_value=False)) & (y == yr)]
            st = _tail_stat(sub)
            if st['n']:
                print("%s | %s: n=%d win=%.1f mean=%.2f p90=%.1f ge20=%.1f ge30=%.1f" % (
                    name, yr, st['n'], st['win'], st['mean'], st['p90'],
                    st['ge20'], st['ge30']))
    # 赢家贡献（验证放松后是否仍是 RIGHT_TAIL）
    for name in ('es>=70&xs>=60&trio', 'es>=70&trio(无扩张门控)', 'es>=65&xs>=55&trio'):
        mask = combos[name]
        sub = x[mask.reindex(x.index, fill_value=False)]
        s = pd.to_numeric(sub['r_break_20'], errors='coerce').dropna()
        if len(s) >= 20:
            tot = float(s.sum())
            if tot > 0:
                k10 = max(1, int(len(s) * 0.10))
                c10 = float(s.nlargest(k10).sum()) / tot * 100.0
                print("Winner贡献 %s: n=%d top10_contrib=%.1f%% (%s)" % (
                    name, len(s), c10, 'RIGHT_TAIL' if c10 >= 60 else 'BROAD'))


if __name__ == '__main__':
    main()
