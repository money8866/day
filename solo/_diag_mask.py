# -*- coding: utf-8 -*-
"""诊断：strategy() 复刻版的每个过滤条件各自淘汰多少，定位候选池过小的原因。"""
import numpy as np
import pandas as pd
import ic_analysis_v11 as m


def main():
    mats, dates, codes = m.load_panel()
    names = m.load_names()
    F = m.compute_features(mats)

    C, H, L, V = mats['close'], mats['high'], mats['low'], mats['vol']
    MV, PCT = mats['total_mv'], mats['pct_chg']
    ok = pd.DataFrame(True, index=C.index, columns=C.columns)

    def step(tag, cond, cur):
        cond = cond.fillna(False).astype(bool) if hasattr(cond, 'fillna') else cond
        nxt = cur & cond
        b, a = cur.sum(axis=1), nxt.sum(axis=1)
        print(f'  {tag:<34} 日均存活 {a.mean():>8.1f}   本步淘汰 {(b-a).mean():>8.1f}   '
              f'非零天数 {(a>0).sum():>4}')
        return nxt

    print('\n=== 逐条件诊断（全市场日均约 5400 只）===')
    ok = step('起始(全市场)', pd.DataFrame(True, index=C.index, columns=C.columns), ok)

    ok = step('历史>=80日', C.rolling(80).count() >= 80, ok)
    ok = step('总市值>=80亿', MV >= m.MIN_MV_YI * 10000, ok)

    bad = pd.DataFrame(False, index=C.index, columns=C.columns)
    for p in ('1', '2'):
        bad |= pd.DataFrame([[str(c).startswith(p) for c in C.columns]],
                            index=C.index, columns=C.columns)
    ok = step('排除前缀1/2', ~bad, ok)

    ok = step('40日涨幅<=100%', F['ret40'] <= 1.0, ok)

    ratio = C / C.shift(1)
    is_cyb = pd.DataFrame([[str(c).startswith(('3', '688', '689')) for c in C.columns]],
                          index=C.index, columns=C.columns)
    zt_up = pd.DataFrame(np.where(is_cyb.values, 1.198, 1.098), index=C.index, columns=C.columns)
    ok = step('当日未涨停', ratio < zt_up, ok)

    st = [c for c in C.columns if str(names.get(c, '')).upper().startswith(('ST', '*ST'))]
    cond = pd.DataFrame(True, index=C.index, columns=C.columns)
    if st:
        cond[st] = False
    ok = step(f'排除ST({len(st)}只, 用最新名)', cond, ok)

    hh20, ll20 = H.rolling(20).max(), L.rolling(20).min()
    ok = step('20日振幅<=180%', (hh20 / ll20 - 1) <= 1.8, ok)
    ok = step('C < MA20*1.3 且 C/MA60<=2', (C < F['ma20'] * 1.3) & (C / F['ma60'] <= 2), ok)
    ok = step('站上MA20/10/60(0.97)',
              (C >= F['ma20']) & (F['ma10'] >= F['ma60'] * .97) & (F['ma5'] >= F['ma60'] * .97), ok)

    ma120_slope = (F['ma120'] - F['ma120'].shift(10)) / F['ma120']
    ok = step('P0:不在下行MA120下',
              ~((C <= F['ma120']) & (ma120_slope < 0) & F['ma120'].notna()), ok)
    ok = step('P0:距120日高点回撤<=45%', (C / H.rolling(120).max() - 1) >= -0.45, ok)

    ztts = m.barslast_matrix(F['zt'].fillna(False)).astype('float64')
    ztts_eff = ztts.where(ztts != 0, ztts.shift(1))
    ok = step('ztts in [3,90]', (ztts_eff >= 3) & (ztts_eff <= 90) & np.isfinite(ztts_eff), ok)
    ok = step('近5日涨幅<=30%', C / C.shift(5) - 1 <= 0.30, ok)

    ok = step('cond5 近高点>=120日高点*0.8', F['hhv20_20d'] >= F['hhv120_prev'] * 0.8, ok)
    ok = step('cond6 MA30上行', F['ma30'] >= F['ma30'].shift(1), ok)
    dd = (C - C.rolling(20).max()) / C.rolling(20).max()
    ok = step('回撤>=-15%(20日近似)', dd >= -0.15, ok)

    bad_k = (PCT < -5) & (V > F['vol_ma5'] * 1.5)
    ok = step('近20日无放量大阴', ~bad_k.rolling(20).max().fillna(0).astype(bool), ok)

    highest = C.shift(1).rolling(20).max()
    vpeak = V.shift(1).rolling(20).max()
    vol_cond = V >= vpeak * 0.7
    is_main = pd.DataFrame(
        [[str(c).startswith(('600', '601', '603', '000', '001', '002')) for c in C.columns]],
        index=C.index, columns=C.columns)
    main_ok = ((C > C.shift(1)) & (C > C.shift(2)) & (C / C.shift(1) > 1.05) & vol_cond) | \
              ((C.shift(1) < highest) & (C >= highest) & (C / C.shift(1) < 1.09))
    main_ok &= (C >= F['ma5'] * .97) & (C / F['ma5'] < 1.11)
    dist = (highest - C) / highest
    chi_ok = (dist > .03) & (dist < .15) & (C >= F['ma20'] * .97) & (C <= F['ma20'] * 1.15) & \
             (V < vpeak * .6) & ((C / C.shift(1) - 1).abs() < .04) & ((C / C.shift(2) - 1).abs() < .06)
    ok = step('XH 主板突破', main_ok & is_main, ok)
    ok = step('XH 双创低吸', chi_ok & is_cyb, ok)

    xh = (main_ok & is_main) | (chi_ok & is_cyb)
    final = ok  # ok 已含 XH(主板) 与 XH(双创) 的与，需重算
    print(f'\n  最终(XH合并) 日均存活 {xh.sum(axis=1).mean():.1f}')

    cnt = xh.sum(axis=1)
    print(f'\n  候选池分布: 均值{cnt.mean():.1f} 中位{cnt.median():.0f} '
          f'最大{cnt.max()} 非零{(cnt>0).sum()}/{len(cnt)}天')
    vc = cnt.value_counts().sort_index().head(8)
    print('  每日候选数分布(前8):')
    for k, v in vc.items():
        print(f'    {k:>3} 只: {v:>4} 天')


if __name__ == '__main__':
    main()
