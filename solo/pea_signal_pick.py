# -*- coding: utf-8 -*-
"""PEA 盘后高胜率信号筛选（交叉验证口径，配套 pea_factor_check.py 使用）

用法:
    python -X utf8 pea_signal_pick.py                     # 默认取库内最新 scan_date
    python -X utf8 pea_signal_pick.py --date 20260827
    python -X utf8 pea_signal_pick.py --top 15 --include-t1

筛选规则（源自 2025H1 五窗口 + 2026H1 OOS 交叉验证结论）:
  硬排除  trigger=T3_RECLAIM（两季禁入维持）/ absorption_state=PRICED_IN
  放行    trigger=T2_PULLBACK（OOS +0.90%/胜率60%, n=109）；T1_BREAKOUT 备选（--include-t1, OOS +0.25%）
  警惕区  alpha>=池p80 或 tqs>=池p80 → 剔除（OOS 反向: alpha IC-0.163 Q5胜率41% / tqs IC-0.161 Q5胜率39%）
  数据不足 event_age==0 且 state=UNKNOWN → 剔除
  排序    fq 降（T+15 慢变量, IC +0.05~+0.07）> tqs 升 > ees 降

胜率锚点（引用自验证报告）: T2 +0.90%/60% | tqs Q1 59% vs Q5 39% | alpha Q1 62% vs Q5 41%
"""
import argparse
import os
import sqlite3

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'pea_absorption.db')

COLS_B = ['ts_code', 'industry', 'theme', 'fq', 'tqs', 'alpha', 'ees', 'ts',
          'absorption_state', 'event_age', 'rel_str', 'close']
ROUND_B = ('fq', 'tqs', 'alpha', 'ees', 'ts', 'rel_str')

STATE_CN = {'NOT_ABSORBED': '未吸收', 'SECONDARY_CONFIRM': '二次确认',
            'PRICED_IN': '已定价', 'UNKNOWN': '未知'}
TRIGGER_CN = {'T2_PULLBACK': 'T2回踩', 'T1_BREAKOUT': 'T1突破',
              'T3_RECLAIM': 'T3收复', 'NO_TRIGGER': '无触发'}
GRADE_CN = {'PROBE': '试仓', 'WATCH': '观察', 'WAIT_CONFIRM': '待确认',
            'WAIT_PULLBACK': '待回踩', 'REJECT': '拒绝'}
HEADER_CN = {'ts_code': '代码', 'industry': '行业', 'theme': '主题',
             'absorption_state': '吸收状态', 'trigger_type': '触发',
             'event_age': '事件龄', 'rel_str': '相对强度%', 'close': '收盘',
             'grade': '分级', 'warn': '警惕'}


def zh_col(s, mapping):
    return s.map(lambda v: mapping.get(v, v))


def load(scan_date):
    conn = sqlite3.connect(DB_PATH)
    try:
        if scan_date is None:
            row = pd.read_sql("SELECT MAX(scan_date) AS d FROM scan_pea", conn)
            scan_date = str(row['d'].iloc[0])
        df = pd.read_sql("SELECT * FROM scan_pea WHERE scan_date=?", conn, params=(scan_date,))
        try:
            cands = pd.read_sql("SELECT * FROM candidates_pea WHERE scan_date=?", conn, params=(scan_date,))
        except Exception:
            cands = df[df['filter_pass'] == 1].copy()
    finally:
        conn.close()
    return scan_date, df, cands


def pick(df, include_t1=False, top=12):
    a80 = df['alpha'].quantile(0.8)
    t80 = df['tqs'].quantile(0.8)
    f80 = df['fq'].quantile(0.8)

    uni = df[(df['trigger_type'] != 'T3_RECLAIM') & (df['absorption_state'] != 'PRICED_IN')]
    allowed = ['T2_PULLBACK'] + (['T1_BREAKOUT'] if include_t1 else [])
    sig = uni[uni['trigger_type'].isin(allowed)].copy()
    n_bad_data = int(((sig['event_age'] == 0) & (sig['absorption_state'] == 'UNKNOWN')).sum())
    sig = sig[~((sig['event_age'] == 0) & (sig['absorption_state'] == 'UNKNOWN'))]
    sig['warn_alpha'] = sig['alpha'] >= a80
    sig['warn_tqs'] = sig['tqs'] >= t80
    core = sig[~sig['warn_alpha'] & ~sig['warn_tqs']].copy()
    core = core.sort_values(['fq', 'tqs', 'ees'], ascending=[False, True, False])
    return core.head(top), dict(a80=a80, t80=t80, f80=f80, n_uni=len(uni),
                                n_sig=len(sig), n_bad_data=n_bad_data, n_core=len(core))


def main():
    ap = argparse.ArgumentParser(description='PEA 盘后高胜率信号筛选（验证口径）')
    ap.add_argument('--date', default=None, help='scan_date, 缺省取库内最新')
    ap.add_argument('--top', type=int, default=12)
    ap.add_argument('--include-t1', action='store_true', help='纳入 T1_BREAKOUT 备选')
    args = ap.parse_args()

    scan_date, df, cands = load(args.date)
    if len(df) == 0:
        print(f'库内无 scan_date={args.date} 的记录')
        return 1

    top, st = pick(df, include_t1=args.include_t1, top=args.top)
    print('=' * 72)
    print(f'PEA 高胜率信号筛选（验证口径）| {scan_date} | 池 {len(df)}')
    print(f'分位阈值 p80: alpha={st["a80"]:.1f} tqs={st["t80"]:.1f} fq={st["f80"]:.1f}')
    print(f'硬排除 T3收复={int((df.trigger_type == "T3_RECLAIM").sum())} 已定价={int((df.absorption_state == "PRICED_IN").sum())}'
          f' → 放行池 {st["n_uni"]} → 触发筛选 {st["n_sig"]}（剔数据不足 {st["n_bad_data"]}）'
          f' → 剔α/tqs警惕区后 {st["n_core"]}')
    print('-' * 72)

    print('[视图A] 引擎官方组合（按α入池, ★=落在验证警惕区）')
    a80, t80 = st['a80'], st['t80']
    if len(cands):
        ca = cands.copy()
        ca['warn'] = [('★' + ('α' if r.alpha >= a80 else '') + ('tqs' if r.tqs >= t80 else '')) if (r.alpha >= a80 or r.tqs >= t80) else ''
                      for r in ca.itertuples()]
        cols = ['ts_code', 'industry', 'fq', 'tqs', 'alpha', 'ees', 'absorption_state',
                'trigger_type', 'grade', 'warn']
        ca_show = ca[cols].copy()
        ca_show['absorption_state'] = zh_col(ca_show['absorption_state'], STATE_CN)
        ca_show['trigger_type'] = zh_col(ca_show['trigger_type'], TRIGGER_CN)
        ca_show['grade'] = zh_col(ca_show['grade'], GRADE_CN)
        for c in ('fq', 'tqs', 'alpha', 'ees'):
            ca_show[c] = ca_show[c].round(1)
        print(ca_show.rename(columns=HEADER_CN).to_string(index=False))
    print('-' * 72)

    print('[视图B] 验证口径信号（排序: fq降 > tqs升 > ees降）')
    out = top[COLS_B].copy()
    out['absorption_state'] = zh_col(out['absorption_state'], STATE_CN)
    for c in ROUND_B:
        out[c] = out[c].round(1)
    print(out.rename(columns=HEADER_CN).to_string(index=False))

    hi_fq = top[top['fq'] >= st['f80']]
    print('-' * 72)
    print(f'参考: 名单内 fq>=p80({st["f80"]:.0f}) 的 {len(hi_fq)} 只（fq 为 T+15 口径唯一实证正向因子）')
    print('纪律: T+15 收盘离场 | 收盘<买价x0.92 止损 | 开盘>前收x1.08 放弃 | T3/PRICED_IN 禁入')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
