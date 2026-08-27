# -*- coding: utf-8 -*-
"""ER20 V2.2 P1 验证套件
A) 决策层参数 ±20% 敏感性测试 — 在已落库的回测明细上重放选信号，检验结论对参数的依赖度
B) Walk-forward 时间切分 — 8月调参 / 9月验证，检验参数是否过拟合时间窗

只依赖 report_daily/er20_v22_backtest_2025H1_base.csv 及其标签列，秒级完成。
"""
import os
import sys
import itertools
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
CSV = os.path.join(REPORT_DIR, 'er20_v22_backtest_2025H1_base.csv')

W_DEF = {'alpha': 0.35, 'ees': 0.25, 'ts': 0.20, 'conf': 0.10, 'risk': 0.10}
P_DEF = {
    'test_alpha': 80.0, 'test_ees': 72.0, 'test_ts': 72.0, 'test_risk': 50.0,
    'probe_alpha': 72.0, 'probe_ees': 60.0,
    'core_alpha': 85.0, 'core_ees': 80.0, 'core_ts': 80.0,
    'core_risk': 35.0, 'core_conf': 80.0, 'core_fq': 35.0, 'core_overheat': 20.0,
    'fq_reject': 25.0, 'conf_watch': 50.0, 'rqs_high': 70.0,
}
BUY_GRADES = ['CORE_BUY', 'TEST_BUY', 'PROBE_BUY']


def _num(x, d=0.0):
    try:
        x = float(x)
        return d if not np.isfinite(x) else x
    except Exception:
        return d


def regrade(df, p):
    """完整复刻生产 grade_v22 状态机（er20_v22.py:537-601），可调阈值来自 p"""
    g = pd.Series('WATCH', index=df.index)
    alpha = df['alpha'].astype(float).fillna(0)
    ees = df['ees'].astype(float).fillna(0)
    ts = df['ts'].astype(float).fillna(0)
    risk = df['rel_risk'].astype(float).fillna(100)
    conf = df['conf'].astype(float).fillna(0)
    fq = df['fq'].astype(float)
    oh = df['overheat'].astype(float).fillna(0)
    ttype = df.get('ttype', '')
    strategy = df.get('strategy', '')
    eq_label = df.get('eq_label', '')
    has_trigger = ~(ttype.eq('NO_TRIGGER') | ts.eq(0))

    watch_a = 60.0
    g[strategy.eq('D_FALSE_SIGNAL')] = 'WATCH'
    g[strategy.eq('C_EVENT_SPEC')] = 'WATCH'
    rej = eq_label.eq('ONE_OFF_DOMINATED')
    floor = fq.lt(p['fq_reject'])
    exempt = strategy.eq('B_REVERSAL')
    if 'rqs' in df.columns:
        rq = pd.to_numeric(df['rqs'], errors='coerce')
        exempt = exempt & rq.ge(p.get('rqs_high', 70.0)).fillna(False)
        exempt = exempt.fillna(False)
    g[floor & ~exempt] = 'REJECT'
    g[conf < p['conf_watch']] = 'WATCH'
    wait_confirm = has_trigger & ~rej & ~floor & (conf >= p['conf_watch']) \
        & ~strategy.isin(['D_FALSE_SIGNAL', 'C_EVENT_SPEC']) & (alpha >= watch_a)
    g[(~has_trigger) & (alpha >= watch_a)] = 'WAIT_CONFIRM'
    over_pull = oh > 25.0
    g[wait_confirm & over_pull] = 'WAIT_PULLBACK'
    g[wait_confirm & ttype.eq('T1_BREAKOUT')] = 'WAIT_PULLBACK'

    ok = has_trigger & (~rej) & ~(floor & ~exempt) \
        & (conf >= p['conf_watch']) & (oh <= 25.0) & (~ttype.eq('T1_BREAKOUT')) \
        & (~strategy.isin(['D_FALSE_SIGNAL', 'C_EVENT_SPEC']))

    core = ok & (alpha >= p['core_alpha']) & (ees >= p['core_ees']) \
        & (ts >= p['core_ts']) & (risk <= p['core_risk']) \
        & (conf >= p['core_conf']) & fq.ge(p['core_fq']).fillna(False) \
        & (oh <= p['core_overheat'])
    g[core] = 'CORE_BUY'
    test = ok & ~core & (alpha >= p['test_alpha']) & (ees >= p['test_ees']) \
        & (ts >= p['test_ts']) & (risk <= p['test_risk'])
    g[test] = 'TEST_BUY'
    probe = ok & ~core & ~test & (alpha >= p['probe_alpha']) \
        & (ees >= p['probe_ees'])
    g[probe] = 'PROBE_BUY'
    return g


def score_next5(df, w):
    s = (
        w['alpha'] * df['alpha'].astype(float).fillna(0)
        + w['ees'] * df['ees'].astype(float).fillna(0)
        + w['ts'] * df['ts'].astype(float).fillna(0)
        + w['conf'] * df['conf'].astype(float).fillna(0)
        + w['risk'] * (100.0 - df['rel_risk'].astype(float).fillna(100))
    )
    return s


def pick_top2(df, mask, w):
    """复刻回测 next5 选信号：按日分组取分高前2。返回信号 index"""
    sub = df[mask].copy()
    if sub.empty:
        return df.index[:0]
    sub['_s'] = score_next5(sub, w)
    keeps = []
    for _, grp in sub.groupby('scan_date'):
        keeps.append(grp.sort_values('_s', ascending=False).head(2).index)
    keep = keeps[0]
    for k in keeps[1:]:
        keep = keep.append(k)
    return keep


def metrics(df, sig_idx):
    sub = df.loc[sig_idx]
    n = len(sub)
    if n == 0:
        return {'n': 0, 'net_ex': np.nan, 'net_ret': np.nan, 'win': np.nan}
    return {
        'n': n,
        'net_ex': round(_num(sub['max5_excess_net'].mean()), 2),
        'net_ret': round(_num(sub['max5_ret_net'].mean()), 2),
        'win': round((sub['max5_ret_net'] > 0).mean() * 100, 0),
    }


def calibrate_mask(df):
    """自动挑出能复现 CSV next5_signal 的掩码定义"""
    y = df['next5_signal'].eq(1)
    buy = df['grade'].isin(BUY_GRADES)
    cands = {
        'buy&rank_eligible': buy & df.get('rank_eligible', 1).eq(1),
        'buy': buy,
        'buy&entry_exec': buy & df['entry_executable'].eq(1),
        'rank_eligible': df.get('rank_eligible', 1).eq(1),
    }
    best, best_rate, best_name = None, -1.0, ''
    for name, m in cands.items():
        hit = float((m & y).sum()) / max(int(y.sum()), 1)
        prec = float((m & y).sum()) / max(int(m.sum()), 1)
        rate = hit * prec
        print(f'  掩码[{name}]: 覆盖{hit:.0%} 精确{prec:.0%}')
        if rate > best_rate:
            best, best_rate, best_name = m, rate, name
    # 若任何组合都无法完美复现，则退化用原信号做基线，仅对扰动用统一重放
    perfect = float(((best) & y).sum() == y.sum() and (~(best) | y.any()).all())
    return best, best_name, perfect


def part_a_sensitivity(df):
    print('\n' + '=' * 64)
    print('A. 参数 ±20% 敏感性测试（决策层重放，标签固定不动）')
    print('=' * 64)

    # 校准 regrade 与原始 grade 的一致率
    rg = regrade(df, P_DEF)
    buy_raw = df['grade'].isin(BUY_GRADES)
    agree = float((rg.isin(BUY_GRADES) == buy_raw)[buy_raw | rg.isin(BUY_GRADES)].mean())
    print(f'regrade 校准: 重建BUY与原grade BUY 一致率 {agree:.1%}')

    results = []
    base_m = metrics(df, pick_top2(df, buy_grades_mask(df, P_DEF, None), W_DEF))
    print(f"基准: n={base_m['n']} 净超额={base_m['net_ex']}% 净收益={base_m['net_ret']}% "
          f"胜率={base_m['win']:.0f}%")

    def perturb(name, pm=None, wm=None, direction=''):
        p = dict(P_DEF)
        if pm:
            p.update(pm)
        w = dict(W_DEF)
        if wm:
            w.update(wm)
        g = regrade(df, p)
        idx = pick_top2(df, g.isin(BUY_GRADES), w)
        m = metrics(df, idx)
        if base_m['net_ex'] and not np.isnan(base_m['net_ex']) and base_m['net_ex'] > 0:
            dr = (m['net_ex'] - base_m['net_ex']) / abs(base_m['net_ex']) * 100
        else:
            dr = np.nan
        results.append({
            'param': name + (' [↓20%]' if direction == 'down' else ' [↑20%]' if direction == 'up' else ''),
            'n': m['n'], 'net_excess': m['net_ex'], 'net_ret': m['net_ret'],
            'win%': m['win'], 'delta_ex%': round(dr, 1) if np.isfinite(dr) else '',
        })

    grid_params = ['test_alpha', 'test_ees', 'test_ts', 'test_risk',
                   'probe_alpha', 'fq_reject']
    for k in grid_params:
        perturb(k, pm={k: round(P_DEF[k] * 0.8, 1)}, direction='down')
        perturb(k, pm={k: round(P_DEF[k] * 1.2, 1)}, direction='up')
    for k in W_DEF:
        perturb(f'w_{k}', wm=multi_weight(k, W_DEF[k] * 0.8), direction='down')
        perturb(f'w_{k}', wm=multi_weight(k, W_DEF[k] * 1.2), direction='up')

    perturb('ALL放宽', pm={k: v * (0.8 if k.endswith(
        ('alpha', 'ees', 'ts')) or k == 'fq_reject'
        else 1.0) if k != 'test_risk' else v * 1.2
        for k, v in P_DEF.items()})
    perturb('ALL收紧', pm={k: v * (1.2 if k.endswith(
        ('alpha', 'ees', 'ts')) or k == 'fq_reject'
        else 1.0) if k != 'test_risk' else v * 0.8
        for k, v in P_DEF.items()})

    tbl = pd.DataFrame(results)
    def _cls(r):
        try:
            d = abs(float(r['delta_ex%']))
        except Exception:
            return ''
        if d <= 20:
            return '稳健'
        if d <= 40:
            return '关注'
        return '脆弱'
    tbl['verdict'] = tbl.apply(_cls, axis=1)
    print(tbl.to_string(index=False))
    n_fragile = int((tbl['verdict'] == '脆弱').sum())
    print(f'\n结论: 26组扰动中 {len(tbl[tbl.verdict == "稳健"])} 组稳健, '
          f'{len(tbl[tbl.verdict == "关注"])} 组关注, {n_fragile} 组脆弱')


def multi_weight(key, new_val):
    w = {}
    w[key] = round(new_val, 3)
    return w


def buy_grades_mask(df, p, _w_unused):
    return regrade(df, p).isin(BUY_GRADES)


GRID_W = {
    'default': W_DEF,
    'equal': {k: 0.2 for k in W_DEF},
    'alpha_light': {'alpha': 0.25, 'ees': 0.30, 'ts': 0.25, 'conf': 0.10, 'risk': 0.10},
}


def part_b_walkforward(df, split='20250831'):
    print('\n' + '=' * 64)
    print(f'B. Walk-forward 时间切分（train ≤{split}, val ≥ 后续首日）')
    print('=' * 64)
    tr = df[df['scan_date'] <= int(split)]
    va = df[df['scan_date'] > int(split)]
    print(f'train 样本 {len(tr)} ({tr.scan_date.nunique()}天), '
          f'val 样本 {len(va)} ({va.scan_date.nunique()}天)')
    if len(tr) < 200 or len(va) < 100:
        print('[warn] 切分后样本不足，结果仅供参考')

    rows = []
    for ta, pa, wm in itertools.product([76, 80, 84], [66, 72, 78], GRID_W.keys()):
        p = dict(P_DEF)
        p['test_alpha'] = float(ta)
        p['probe_alpha'] = float(pa)
        g = regrade(tr, p)
        idx = pick_top2(tr, g.isin(BUY_GRADES), GRID_W[wm])
        m = metrics(tr, idx)
        rows.append({'test_alpha': ta, 'probe_alpha': pa, 'w': wm,
                     **m})
    trt = pd.DataFrame(rows).dropna(subset=['net_ex'])
    ok = trt[trt['n'] >= 4]
    pool = ok if len(ok) >= 3 else trt
    best = pool.sort_values('net_ex', ascending=False).iloc[0]
    print(f'\ntrain 最优参数: test_alpha={int(best.test_alpha)} '
          f'probe_alpha={int(best.probe_alpha)} weights={best.w} '
          f"(净超额 {best.net_ex}%, n={int(best.n)})")

    out_rows = []
    for tag, pa_ in [('chosen', best), ('default', None)]:
        p = dict(P_DEF)
        w = GRID_W[best.w]
        if pa_ is not None:
            p['test_alpha'] = float(pa_.test_alpha)
            p['probe_alpha'] = float(pa_.probe_alpha)
        gv = regrade(va, p)
        idx = pick_top2(va, gv.isin(BUY_GRADES), w)
        m = metrics(va, idx)
        out_rows.append({'set': tag + f'({"" if pa_ is None else "ta=" + str(int(pa_.test_alpha)) + ",pa=" + str(int(pa_.probe_alpha)) + "," + str(best.w)})',
                         'n': m['n'], 'net_excess': m['net_ex'],
                         'net_ret': m['net_ret'], 'win%': m['win']})
    vt = pd.DataFrame(out_rows)
    print('\nval 对照:')
    print(vt.to_string(index=False))
    chosen_row = vt.iloc[0]
    default_row = vt.iloc[1]
    try:
        gap = float(chosen_row['net_excess']) - float(default_row['net_excess'])
        verdict = '训练段调参未见效(默认参数即可)' if gap <= 0 else \
            (f'训练段调参在验证段提升 {gap:+.2f}% — 但 val 样本量小, 需更多季节确认'
             if float(chosen_row['n']) >= 3 else 'val 信号过少(<3), 无法采信')
    except Exception:
        verdict = '无法比较'
    print(f'\nwalk-forward 结论: {verdict}')


def main():
    if not os.path.exists(CSV):
        print(f'缺少明细文件: {CSV}')
        sys.exit(1)
    df = pd.read_csv(CSV)
    need = {'ts_code', 'scan_date', 'alpha', 'ees', 'ts', 'conf', 'rel_risk',
            'fq', 'overheat', 'grade', 'next5_signal',
            'max5_ret_net', 'max5_excess_net'}
    miss = need - set(df.columns)
    if miss:
        print(f'缺字段: {miss}')
        sys.exit(1)
    print(f'[P1] 明细加载 {len(df)} 行 ({df.ts_code.nunique()} 只事件股, '
          f'{df.scan_date.nunique()} 个扫描日)')

    part_a_sensitivity(df)
    part_b_walkforward(df)


if __name__ == '__main__':
    main()
