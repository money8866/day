# -*- coding: utf-8 -*-
"""闸门收尾：实盘约束（mv≥80亿 ∧ 非北交所）的「排序口径」二选一

动机
  gate1 的 GATE-C 与 gate2 的 *_mv80 变体，对「实盘约束下还剩多少 Alpha」
  给出方向相反的读数。根因不是数据，是**排名的先后顺序**被写成了同一个名字：

    (I)  RANK-IN   : 先过滤 mv80，再在 mv80 内排 Top 十分位       ← gate2 用的
    (II) RANK-POST : 先在全市场排 Top 十分位，再把 <80亿 的剔掉  ← 未检验

  两者经济学含义不同：
    RANK-IN   把「大盘股内部的 rev_acc 极值」当信号；
    RANK-POST 把「全市场 rev_acc 极值」当信号，只是不交易小票。
  真实交易里两者都合法（都可以下单），所以必须都跑，由事实裁决。

预先写死（跑之前定，不因结果修改）
  与 gate2 完全同记账口径：sleeve = 一个公告入场日 k1 的等权篮子，
  k1 开盘买 / k1+5 收盘卖，每个 sleeve 投 1/6 当期 NAV，每笔扣 30bp。
  RANK-POST 的容量风险由「篮子只数」如实暴露（可能只剩 1~2 只），不设地板。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log
from _fs_trade_gate2 import (load_px, top_decile, build_day_baskets,
                             simulate_nav, metrics, COST, COST_HI, MV_MIN, HOLD)

log = Log('_fs_trade_gate3.txt')


def rp(df, col, mask):
    """RANK-POST：先在全样本排 Top 十分位，只保留 mask 内的成员"""
    A = top_decile(df, col)
    return A[mask.reindex(A.index).fillna(False).values].copy()


def main():
    log('=' * 70)
    log('S0 载入（与 gate2 同一记账口径：sleeve=入场日等权篮子，1/6 资金，30bp）')
    td, cmap, C, O, lvl = load_px()
    df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    df = E.add_derived(df)
    df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
    df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
    mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))
    log('  全样本事件 %d / mv80 事件 %d (%.2f%%)'
        % (len(df), int(mv80.sum()), 100.0 * mv80.mean()))

    # ---- 两个口径的成员重叠度（先看清它们到底是不是一回事）
    log('')
    log('S1 两个口径的 Top 十分位成员重叠（rev_acc / np_qoq）')
    for f in ('rev_acc', 'np_qoq'):
        A = top_decile(df, f)                                   # 全样本 top
        B = top_decile(df, f, mv80)                             # RANK-IN
        Cp = rp(df, f, mv80)                                    # RANK-POST
        sa = set(zip(A['ts_code'], A['k1']))
        sb = set(zip(B['ts_code'], B['k1']))
        sc = set(zip(Cp['ts_code'], Cp['k1']))
        log('  %-8s 全样本top=%-6d RANK-IN=%-6d RANK-POST=%-6d | '
            'POST⊂全top=%s  POST∩RANKIN=%d  RANKIN独有=%d'
            % (f, len(A), len(B), len(Cp),
               True, len(sc & sb), len(sb - sc)))
        for nm, x in (('全样本top', A), ('RANK-IN', B), ('RANK-POST', Cp)):
            n = x.groupby('k1').size()
            log('      %-12s 事件=%-6d 入场日=%-5d 每篮只数 中位=%.1f p10=%.1f 最小=%d'
                % (nm, len(x), len(n), n.median(), n.quantile(0.10), n.min()))

    # ---- 变体
    variants = []
    for f in ('rev_acc', 'np_qoq'):
        variants.append(('FULL_%s' % f, top_decile(df, f)))
        variants.append(('RANKIN_%s_mv80' % f, top_decile(df, f, mv80)))
        variants.append(('RANKPOST_%s_mv80' % f, rp(df, f, mv80)))
    variants.append(('B1_ALL_EVENTS', df.copy()))
    variants.append(('B2_MV80_ALL_EVENTS', df[mv80].copy()))

    rows, navs, info = [], {}, []
    for nm, ev in variants:
        for c, tag in ((COST, ''), (COST_HI, '_c60')):
            ret_map, n, n_days = build_day_baskets(ev, cmap, C, O, len(td), c)
            if tag == '':
                nav = simulate_nav(ret_map, len(td))
                mt = metrics(nav, lvl, td, nm)
                if mt:
                    rows.append(mt)
                    navs[nm] = nav
            info.append(dict(name=nm + tag, n_event=n, n_entry_days=n_days,
                             avg_size=round(n / max(n_days, 1), 2)))
        log('  %-24s 事件=%-6d 入场日=%-5d 均只数=%.2f'
            % (nm, info[-2]['n_event'], info[-2]['n_entry_days'],
               info[-2]['avg_size']))

    base = pd.Series(lvl, index=td)
    if 'B2_HS300' not in navs:
        navs['B2_HS300'] = (base / base.iloc[0]).values
    rows.append(metrics(navs['B2_HS300'], lvl, td, 'B2_HS300'))

    log('')
    log('S2 组合绩效（同一 sleeve 记账，唯一差别 = 排序口径）')
    log(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: '%.4f' % v))

    log('')
    log('S3 分期（区间收益 / 超额）')
    per = {'TRAIN': ('20180101', '20231231'), 'VALID': ('20240101', '20251231'),
           'OOS': ('20260101', '20260930')}
    pr = []
    for nm, nav in navs.items():
        s = pd.Series(nav, index=td).dropna()
        for pn, (a, b) in per.items():
            x = s[(s.index >= a) & (s.index <= b)]
            if len(x) < 30:
                continue
            b0 = base[(base.index >= a) & (base.index <= b)]
            yrs = len(x) / 242.0
            ret = x.iloc[-1] / x.iloc[0] - 1
            br = b0.iloc[-1] / b0.iloc[0] - 1
            pr.append(dict(name=nm, period=pn, ret=ret,
                           cagr=(1 + ret) ** (1 / yrs) - 1 if ret > -1 else np.nan,
                           bench=br, excess=ret - br,
                           mdd=float((x / x.cummax() - 1).min())))
    log(pd.DataFrame(pr).to_string(index=False, float_format=lambda v: '%.4f' % v))

    log('')
    log('S4 口径裁决（只看同一约束下的两个排法）')
    M = pd.DataFrame(rows).set_index('name')
    for f in ('rev_acc', 'np_qoq'):
        a, b = 'RANKIN_%s_mv80' % f, 'RANKPOST_%s_mv80' % f
        full = 'FULL_%s' % f
        if a in M.index and b in M.index:
            log('  %-8s 全样本 超额CAGR=%.4f IR=%.3f | RANK-IN 超额=%.4f IR=%.3f '
                '| RANK-POST 超额=%.4f IR=%.3f'
                % (f, M.loc[full, 'excess_cagr'], M.loc[full, 'ir'],
                   M.loc[a, 'excess_cagr'], M.loc[a, 'ir'],
                   M.loc[b, 'excess_cagr'], M.loc[b, 'ir']))

    pd.DataFrame(rows).to_csv(os.path.join(OUTD, 'trade_gate_nav_v2.csv'),
                              index=False, encoding='utf-8-sig')
    pd.DataFrame(info).to_csv(os.path.join(OUTD, 'trade_gate_info_v2.csv'),
                              index=False, encoding='utf-8-sig')
    log('')
    log('  已写出 out/trade_gate_nav_v2.csv / out/trade_gate_info_v2.csv')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
