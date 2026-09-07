# -*- coding: utf-8 -*-
"""补充验证：按 IC 重新配权 + 加入换手率，整合评分能提升多少？

所有因子先做「当日截面 rank 标准化」再加权 —— 顺便验证截面标准化的效果。
对照组：原 V11 权重（技术版）。
"""
import numpy as np
import pandas as pd
import ic_analysis_v11 as m


def rank_ic(lt, col, ret='fwd_ret', min_n=10):
    ics = []
    for d, g in lt.groupby('trade_date'):
        if len(g) < min_n:
            continue
        x, y = g[col], g[ret]
        k = x.notna() & y.notna()
        if k.sum() < min_n:
            continue
        ics.append(x[k].rank().corr(y[k].rank()))
    s = pd.Series(ics).dropna()
    if len(s) < 5:
        return dict(IC均值=np.nan, t值=np.nan, 天数=len(s))
    icir = s.mean() / s.std() if s.std() > 0 else np.nan
    return dict(IC均值=s.mean(), ICIR=icir, t值=icir * np.sqrt(len(s)), 天数=len(s))


def main():
    mats, dates, codes = m.load_panel()
    names = m.load_names()
    fina = m.load_fina_pit(dates)
    roe_pit, npy_pit = m.build_fina_matrix(fina, dates, codes)
    F = m.compute_features(mats)
    mask = m.strategy_mask(mats, F, names)
    dim = m.score_dims(mats, F, roe_pit, npy_pit)
    fwd = m.forward_returns(mats, m.HOLD_DAYS)
    lt = m.build_long_table(mask, dim, fwd)
    print(f'\n样本 {len(lt):,} 条, {lt.trade_date.nunique()} 个截面日')

    # ---- 截面 rank 标准化（z-score of rank）----
    base_cols = ['突破质量', '资金行为', '位置安全', '动量爆发', '基本面']
    for c in base_cols + ['[对照]换手率', '[对照]20日反转', '[对照]市值对数']:
        lt['z_' + c] = lt.groupby('trade_date')[c].transform(
            lambda s: (s.rank(pct=True) - 0.5) * 2)      # [-1, 1]

    # ---- 各维度 ICIR（用于配权）----
    print('\n各维度原始 ICIR（用于重新配权）:')
    icir_map = {}
    for c in base_cols + ['[对照]换手率', '[对照]20日反转']:
        r = rank_ic(lt, 'z_' + c)
        icir_map[c] = r['ICIR']
        print(f"  {c:<16} IC={r['IC均值']:+.4f}  ICIR={r['ICIR']:+.3f}  t={r['t值']:+.2f}")

    # ---- 构造几个组合 ----
    W_old = {'突破质量': .25, '资金行为': .24, '位置安全': .18, '基本面': .15, '动量爆发': .10}

    lt['C1_原V11权重'] = sum(lt['z_' + c] * w for c, w in W_old.items())

    # C2: ICIR 加权（符号保留，含负权重）
    tot = sum(abs(icir_map[c]) for c in base_cols)
    lt['C2_ICIR加权'] = sum(lt['z_' + c] * (icir_map[c] / tot) for c in base_cols)

    # C3: 只用统计显著的 V11 维度（位置安全 + 基本面）
    lt['C3_仅显著维度'] = lt['z_位置安全'] * 0.5 + lt['z_基本面'] * 0.5

    # C4: 显著维度 + 换手率(负向) —— 换手率是最强单因子
    lt['C4_显著+换手率'] = (lt['z_位置安全'] * 0.35 + lt['z_基本面'] * 0.35
                            - lt['z_[对照]换手率'] * 0.30)

    # C5: 显著维度 + 换手率 + 反转
    lt['C5_显著+换手+反转'] = (lt['z_位置安全'] * 0.30 + lt['z_基本面'] * 0.30
                               - lt['z_[对照]换手率'] * 0.25 + lt['z_[对照]20日反转'] * 0.15)

    # C6: 纯换手率（反向）
    lt['C6_纯换手率反向'] = -lt['z_[对照]换手率']

    print('\n' + '=' * 78)
    print(' 组合对比（截面 rank 标准化后加权）')
    print('=' * 78)
    print(f"{'组合':<22}{'IC均值':>10}{'ICIR':>9}{'t值':>9}{'天数':>7}")
    print('-' * 78)
    for c in ['C1_原V11权重', 'C2_ICIR加权', 'C3_仅显著维度', 'C4_显著+换手率',
              'C5_显著+换手+反转', 'C6_纯换手率反向', '整合评分(技术版)']:
        if c not in lt.columns:
            continue
        r = rank_ic(lt, c)
        star = '***' if abs(r['t值']) >= 3 else ('**' if abs(r['t值']) >= 2 else '')
        print(f"{c:<22}{r['IC均值']:>10.4f}{r['ICIR']:>9.3f}{r['t值']:>9.2f}{r['天数']:>7} {star}")

    # ---- 分组收益对比 ----
    print('\n' + '=' * 78)
    print(' 分组多空收益（Q5 - Q1，%）')
    print('=' * 78)
    rows = []
    for c in ['C1_原V11权重', 'C4_显著+换手率', 'C6_纯换手率反向', '整合评分(技术版)']:
        if c not in lt.columns:
            continue
        tmp = lt[['trade_date', c, 'fwd_ret']].dropna()
        parts = []
        for d, g in tmp.groupby('trade_date'):
            if len(g) < 10:
                continue
            try:
                g = g.assign(_q=pd.qcut(g[c].rank(method='first'), 5, labels=False))
            except Exception:
                continue
            parts.append(g)
        if not parts:
            continue
        gg = pd.concat(parts)
        mu = gg.groupby('_q')['fwd_ret'].mean()
        rows.append({'组合': c, 'Q1': mu.get(0), 'Q3': mu.get(2), 'Q5': mu.get(4),
                     '多空': mu.get(4, np.nan) - mu.get(0, np.nan)})
    qdf = pd.DataFrame(rows)
    print(f"{'组合':<22}{'Q1':>9}{'Q3':>9}{'Q5':>9}{'多空':>9}")
    print('-' * 78)
    for _, r in qdf.iterrows():
        print(f"{r['组合']:<22}{r['Q1']:>9.2f}{r['Q3']:>9.2f}{r['Q5']:>9.2f}{r['多空']:>9.2f}")

    lt.to_csv(r'D:\mystock\solo\ic_output\combos.csv', index=False, encoding='utf-8-sig')
    print('\n已保存 ic_output/combos.csv')


if __name__ == '__main__':
    main()
