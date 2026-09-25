# -*- coding: utf-8 -*-
"""三花聚顶研究 · 失败案例研究（第六阶段）

问题: 什么样的三花最容易失败?
口径:
  A 组 = 默认 H1 定义 + Entry A（三花确认日收盘买入）
  C 组 = 默认 H1 定义 + Entry C（三花后二次突破收盘买入）
  "失败" = 买入后 T+10 净收益 <= 0
方法: 特征分箱失败率 + 人工命名的 Failure Pattern 组合条件
输出: out/tf_failures.csv / out/tf_failure_patterns.csv / out/tf_failures_breakout.csv
"""
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'out')


def mdn(a):
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    return round(float(np.median(a)), 5) if len(a) else None


def mn(a):
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    return round(float(a.mean()), 5) if len(a) else None


def pc(x):
    return ('%+.2f%%' % (100 * x)) if x is not None else '  n/a'


def cut(v, edges, labels):
    return pd.Series(pd.cut(np.asarray(v, dtype=np.float64), edges, labels=labels,
                            include_lowest=True)).astype(object)


def stat_block(sub, rcols):
    """对子样本返回失败率/中位/胜率等"""
    o = {'n': int(len(sub))}
    for tag, c in rcols.items():
        v = sub[c].astype(float).values
        v = v[np.isfinite(v)]
        if len(v) == 0:
            continue
        o['fail_' + tag] = round(float((v <= 0).mean()), 4)
        o['med_' + tag] = round(float(np.median(v)), 5)
        o['win_' + tag] = round(float((v > 0).mean()), 4)
    return o


def main():
    df = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet')).reset_index(drop=True)
    dim = pd.read_parquet(os.path.join(OUT, 'tf_events_dim.parquet'))
    df = df.merge(dim[['ts_code', 'trade_date', 'regime', 'mv_grp', 'to_grp']],
                  on=['ts_code', 'trade_date'], how='left')

    # ══════════ A 组: H1 + 确认日收盘买入 ══════════
    mA = (df['d_H1'].values & df['A_r10'].notna().values)
    d = df[mA].copy()
    for c in ['A_r1', 'A_r3', 'A_r5', 'A_r10', 'A_r20', 'A_mfe', 'A_mae', 'A_path_dd']:
        d[c] = d[c].astype(float)
    base_fail = float((d['A_r10'] <= 0).mean())
    print('A组 H1+确认日收盘  n=%d | T+10 失败率 %.1f%% | 中位 %+.2f%% | 中位MFE %+.2f%% 中位MAE %+.2f%%'
          % (len(d), 100 * base_fail, 100 * np.nanmedian(d['A_r10']),
             100 * np.nanmedian(d['A_mfe']), 100 * np.nanmedian(d['A_mae'])))

    # ── 1. 特征分箱 ──
    feats = {
        'volume_decay(V3/V1)': cut(d['volume_decay'], [0, .7, .85, 1.0, 1.2, 9],
                                   ['<0.7', '0.7-0.85', '0.85-1.0', '1.0-1.2', '>1.2']),
        'price_progression': cut(d['price_progression'], [-1, -.03, 0, .05, .12, 9],
                                 ['<-3%', '-3~0%', '0~5%', '5-12%', '>12%']),
        'struct_dd': cut(d['struct_dd'], [0, .05, .08, .12, .18, 1],
                         ['<5%', '5-8%', '8-12%', '12-18%', '>18%']),
        'ma_conv': cut(d['ma_conv'], [0, .02, .04, .06, .10, 9],
                       ['<2%', '2-4%', '4-6%', '6-10%', '>10%']),
        'span(F3-F1)': cut(d['span'], [0, 3, 4, 5, 6, 99], ['<=3', '4', '5', '6', '>6']),
        'n_flower': cut(d['n_flower'], [0, 3, 4, 5, 99], ['3', '4', '5', '>=6']),
        'k_off(确认日)': cut(d['k_A'], [0, 8, 10, 12, 15, 99],
                             ['<=8', '9-10', '11-12', '13-15', '>15']),
        't0_vr20(首板量比)': cut(d['t0_vr20'], [0, 1.5, 2.5, 4, 7, 1e9],
                                 ['<1.5', '1.5-2.5', '2.5-4', '4-7', '>7']),
        'min_close_vs_t0': cut(d['min_close_vs_t0'], [-1, -.12, -.06, 0, .05, 1],
                               ['<-12%', '-12~-6%', '-6~0%', '0~5%', '>5%']),
        'ma_stack_up': d['ma_stack_up'].map({0.0: '否', 1.0: '是'}).astype(object),
        't0_quality': d['t0_quality'].astype(object),
        'board': d['board'].astype(object),
        'regime': d['regime'].astype(object),
        'to_grp(换手分位)': d['to_grp'].astype(object),
        'mv_grp(市值分位)': d['mv_grp'].astype(object),
    }
    rows = []
    for name, b in feats.items():
        bv = pd.Series(b).values
        for lv in pd.Series(b).dropna().unique():
            m = np.asarray(bv == lv)
            if m.sum() < 30:
                continue
            o = {'feature': name, 'level': str(lv)}
            o.update(stat_block(d[m], {'5': 'A_r5', '10': 'A_r10', '20': 'A_r20'}))
            o['lift'] = round(float(o.get('fail_10', 0) / base_fail), 3)
            o['med_mfe'] = mdn(d['A_mfe'].values[m])
            o['med_mae'] = mdn(d['A_mae'].values[m])
            rows.append(o)
    fb = pd.DataFrame(rows)
    fb.to_csv(os.path.join(OUT, 'tf_failures.csv'), index=False, encoding='utf-8-sig')

    print('\n=== 特征分箱失败率（A组基线 %.1f%%）===' % (100 * base_fail))
    for name in feats:
        sub = fb[fb['feature'] == name].sort_values('fail_10', ascending=False)
        if sub.empty:
            continue
        print('--', name)
        for _, r in sub.iterrows():
            print('   %-11s n=%-5d 失败率T+10 %5.1f%% (lift %.2f) | 失败率T+5 %5.1f%% | med T+10 %s T+20 %s | MFE %s MAE %s'
                  % (r['level'], r['n'], 100 * r['fail_10'], r['lift'], 100 * r['fail_5'],
                     pc(r.get('med_10')), pc(r.get('med_20')), pc(r.get('med_mfe')), pc(r.get('med_mae'))))

    # ── 2. Failure Pattern（A 组）──
    v = d
    patsA = {
        'P1_首板天量(VR>=4)+结构松散(DD>12%)': (v['t0_vr20'] >= 4) & (v['struct_dd'] > 0.12),
        'P2_高换手(前1/3分位)': (v['to_grp'] == 'High'),
        'P3_量不衰减(V3>=V1*1.1)': v['volume_decay'] >= 1.10,
        'P4_花数少(=3)': v['n_flower'] <= 3,
        'P5_均线未收敛(>6%)+价重心走平': (v['ma_conv'] > 0.06) & (v['price_progression'] <= 0.02),
        'P6_结构深破首板收盘(<-12%)': v['min_close_vs_t0'] < -0.12,
        'P7_确认日过早(<=T+8)': v['k_A'] <= 8,
        'P8_首板质量Weak': (v['t0_quality'] == 'Weak'),
        'P9_双创(GEM/STAR)': v['board'].isin(['GEM', 'STAR']),
        'P10_强势市(regime=strong)': (v['regime'] == 'strong'),
        'P11_高换手+花数少': (v['to_grp'] == 'High') & (v['n_flower'] <= 3),
        'P12_高换手+确认过早': (v['to_grp'] == 'High') & (v['k_A'] <= 10),
    }
    prows = []
    for name, m in patsA.items():
        m = np.asarray(pd.Series(m).fillna(False))
        if m.sum() < 20:
            prows.append({'group': 'A', 'pattern': name, 'n': int(m.sum()), 'note': '样本不足'})
            continue
        o = {'group': 'A', 'pattern': name, 'n': int(m.sum()), 'share': round(float(m.mean()), 4)}
        o.update(stat_block(v[m], {'5': 'A_r5', '10': 'A_r10', '20': 'A_r20'}))
        o['lift'] = round(float(o.get('fail_10', 0) / base_fail), 3)
        prows.append(o)

    # ── 3. C 组（突破）──
    mC = (df['Cb0_r10'].notna().values & df['d_H1'].values)
    b = df[mC].copy()
    for c in ['Cb0_r1', 'Cb0_r3', 'Cb0_r5', 'Cb0_r10', 'Cb0_r20',
              'Cb0_mfe', 'Cb0_mae', 'Cb0_path_dd']:
        b[c] = b[c].astype(float)
    bf = float((b['Cb0_r10'] <= 0).mean())
    print('\n=== C组 H1+突破收盘  n=%d | T+10 失败率 %.1f%% | 中位 %+.2f%% | 突破后3日内跌破-5%%比例 %.1f%% ==='
          % (len(b), 100 * bf, 100 * np.nanmedian(b['Cb0_r10']),
             100 * float((b['Cb0_r3'] <= -0.05).mean())))

    patsC = {
        'Q1_突破天量(VR>=1.5)': b['brk_b0_vr'] >= 1.5,
        'Q2_突破温和量(VR<1.0)': b['brk_b0_vr'] < 1.0,
        'Q3_高开低走突破(gap>2%且收阴)': (b['brk_b0_gap'] > 0.02) & (b['brk_b0_idc'] < 0),
        'Q4_突破日收盘破MA20': b['brk_b0_abv'] <= 0.5,
        'Q5_突破日高位收(收盘位置>0.9)': b['brk_b0_pos'] > 0.9,
        'Q6_突破滞后(确认后>5日才突破)': (b['Cb0_k'] - b['k_A']) > 5,
        'Q7_高换手(前1/3)': (b['to_grp'] == 'High'),
        'Q8_天量+高换手': (b['brk_b0_vr'] >= 1.5) & (b['to_grp'] == 'High'),
    }
    for name, m in patsC.items():
        m = np.asarray(pd.Series(m).fillna(False))
        if m.sum() < 20:
            prows.append({'group': 'C', 'pattern': name, 'n': int(m.sum()), 'note': '样本不足'})
            continue
        o = {'group': 'C', 'pattern': name, 'n': int(m.sum()), 'share': round(float(m.mean()), 4)}
        o.update(stat_block(b[m], {'5': 'Cb0_r5', '10': 'Cb0_r10', '20': 'Cb0_r20'}))
        o['fast_break'] = round(float((b['Cb0_r3'].values[m] <= -0.05).mean()), 4)
        o['lift'] = round(float(o.get('fail_10', 0) / bf), 3)
        prows.append(o)

    pf = pd.DataFrame(prows)
    pf.to_csv(os.path.join(OUT, 'tf_failure_patterns.csv'), index=False, encoding='utf-8-sig')

    print('\n=== Failure Pattern ===')
    for grp, base in (('A', base_fail), ('C', bf)):
        sub = pf[(pf['group'] == grp)]
        if sub.empty:
            continue
        print('-- %s 组（基线失败率 %.1f%%）' % (grp, 100 * base))
        for _, r in sub.sort_values('fail_10', ascending=False, na_position='last').iterrows():
            if r.get('note') == '样本不足':
                print('   %-32s n=%-4d 样本不足' % (r['pattern'], r['n']))
                continue
            print('   %-32s n=%-5d 占比%4.0f%% 失败率 %5.1f%%(lift %.2f) | med T+10 %s T+20 %s%s'
                  % (r['pattern'], r['n'], 100 * r['share'], 100 * r['fail_10'], r['lift'],
                     pc(r.get('med_10')), pc(r.get('med_20')),
                     (' | 3日内破-5%% %.0f%%' % (100 * r['fast_break'])) if pd.notna(r.get('fast_break')) else ''))

    # ── 4. 突破 VR 分档 ──
    print('\n=== 突破量能分档（C 组）===')
    brk_rows = []
    for lo, hi, nm in [(0, 1.0, 'VR<1.0'), (1.0, 1.3, '1.0-1.3'), (1.3, 1.5, '1.3-1.5'),
                       (1.5, 2.0, '1.5-2.0'), (2.0, 1e9, '>2.0')]:
        vr = b['brk_b0_vr'].astype(float).values
        m = np.asarray((vr >= lo) & (vr < hi))
        if m.sum() < 15:
            continue
        o = {'band': nm}
        o.update(stat_block(b[m], {'5': 'Cb0_r5', '10': 'Cb0_r10', '20': 'Cb0_r20'}))
        o['fast_break'] = round(float((b['Cb0_r3'].values[m] <= -0.05).mean()), 4)
        o['med_mae'] = mdn(b['Cb0_mae'].values[m])
        o['med_mfe'] = mdn(b['Cb0_mfe'].values[m])
        brk_rows.append(o)
        print('   %-9s n=%-5d T+10失败率 %5.1f%% | 3日内破-5%% %5.1f%% | med T+10 %s T+20 %s | MFE %s MAE %s'
              % (nm, o['n'], 100 * o['fail_10'], 100 * o['fast_break'],
                 pc(o.get('med_10')), pc(o.get('med_20')), pc(o.get('med_mfe')), pc(o.get('med_mae'))))
    pd.DataFrame(brk_rows).to_csv(os.path.join(OUT, 'tf_failures_breakout.csv'),
                                  index=False, encoding='utf-8-sig')
    print('\n已写 tf_failures.csv / tf_failure_patterns.csv / tf_failures_breakout.csv')


if __name__ == '__main__':
    main()
