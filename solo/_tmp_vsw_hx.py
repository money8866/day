# -*- coding: utf-8 -*-
"""临时：海峡创新(300300.SZ) 特征画像 + FES 分量拆解 + 组合条件筛选（用完即删）

回答「海峡创新的特征如何能成为最优信号」：
  A. 硬过滤逐条诊断（解释它为何不在 cohort 回测里）
  B. 与 0923 TOP1/TOP2 的 FES 分量级对照（定位它丢分在哪一项）
  C. 在 cohort CSV 上做组合条件筛选（找把它顶到最优的可量化条件）
"""
import os
import sys
import sqlite3

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO)
import _tmp_vsw_ab as AB       # noqa: E402
import volume_surge_select as V  # noqa: E402

CASES = [('300300.SZ', '20260923'), ('600184.SH', '20260923'),
         ('002902.SZ', '20260923'), ('300300.SZ', '20260922')]


# ---------------- A. 硬过滤逐条诊断 ----------------
def hard_filter_dbg(df):
    """复刻 AB.hard_filter，但逐条记录首个未通过项"""
    n = len(df)
    C = df['close'].values.astype(float)
    H = df['high'].values.astype(float)
    L = df['low'].values.astype(float)
    VOL = df['vol'].values.astype(float)
    PC = np.concatenate([[C[0]], C[:-1]])
    if n < 180:
        return np.zeros(n, dtype=bool), ['样本<180']
    sC, sH, sL, sV = pd.Series(C), pd.Series(H), pd.Series(L), pd.Series(VOL)
    vol_ma20 = sV.rolling(20, min_periods=1).mean().values
    vratio = pd.Series(VOL / np.maximum(vol_ma20, 1))
    max_vr = vratio.rolling(200).max().values
    gt2 = (vratio > 2.0).astype(float).rolling(200).sum().values
    amp = (H - L) / np.maximum(PC, 0.01) * 100
    samp = pd.Series(amp)
    avg_amp120 = samp.rolling(120).mean().values
    rs = (sH.rolling(200).max() / sL.rolling(200).min() - 1) * 100
    hist_max = sV.cummax().values
    vol_vs_hist = sV.rolling(200).max().values / np.maximum(hist_max, 1) * 100
    ma20 = sC.rolling(20).mean().values
    ma20_chg10 = (ma20 / pd.Series(ma20).shift(10) - 1) * 100
    ma20_chg20 = (ma20 / pd.Series(ma20).shift(20) - 1) * 100
    rv10 = sV.rolling(10).mean().values
    base_v = sV.rolling(30).mean().shift(10).values
    vol_vs_base = rv10 / np.maximum(base_v, 1)
    peak5 = sV.rolling(5).mean().rolling(200).max().values
    vol_vs_peak = rv10 / np.maximum(peak5, 1)
    today_pct = (C / np.maximum(PC, 1e-9) - 1) * 100
    v20 = vratio.values
    vr20_max = pd.Series(v20).rolling(20).max().values
    vr20_mean = pd.Series(v20).rolling(20).mean().values
    total = (np.minimum(max_vr / 5.0, 1) * 30 + np.minimum(gt2 / 7, 1) * 20
             + np.minimum(avg_amp120 / 7, 1) * 20
             + np.minimum((samp > 8).astype(float).rolling(200).sum().values / 15, 1) * 15
             + np.minimum(rs / 60, 1) * 15)

    checks = [
        ('max_vr>=2.6', np.nan_to_num(max_vr, nan=-1), 2.6),
        ('gt2>=5', np.nan_to_num(gt2, nan=-1), 5),
        ('vr20_max>=2.0', np.nan_to_num(vr20_max, nan=-1), 2.0),
        ('vr20_mean>=1.4', np.nan_to_num(vr20_mean, nan=-1), 1.4),
        ('amp120>=4.5', avg_amp120, 4.5),
        ('swing>=35', rs, 35),
        ('today_pct>-7', today_pct, -7.0),
        ('vol_vs_hist>=50', vol_vs_hist, 50),
        ('ma20_chg10>=-1', ma20_chg10, -1.0),
        ('ma20_chg20>=-2', ma20_chg20, -2.0),
        ('C>=ma20*0.95', (C / np.maximum(ma20, 1e-9) - 1) * 100, -5.0),
        ('vol_vs_base>=1.1', vol_vs_base, 1.1),
        ('vol_vs_peak>=0.5', vol_vs_peak, 0.5),
        ('total>=65', total, 65.0),
    ]
    ok = np.ones(n, dtype=bool)
    for _, arr, thr in checks:
        ok &= np.nan_to_num(arr, nan=-1e9) >= thr
    ok[:180] = False
    detail = {'total': total, 'checks': checks}
    return ok, detail


def main():
    con = sqlite3.connect(AB.DB)
    codes = sorted({c for c, _ in CASES})
    q = ("SELECT ts_code,trade_date,open,high,low,close,vol FROM daily_cache "
         "WHERE trade_date>='20240201' AND ts_code IN (%s)"
         % ",".join("'%s'" % c for c in codes))
    df = pd.read_sql(q, con)
    con.close()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

    print("=" * 108)
    print("  A. 硬过滤逐条诊断（AB 近似口径 vs 生产 0923 实际入池）")
    print("=" * 108)
    for code, g in df.groupby('ts_code'):
        g = g.reset_index(drop=True)
        dates = g['trade_date'].values
        ok, det = hard_filter_dbg(g)
        for c2, dtx in CASES:
            if c2 != code:
                continue
            hit = np.where(dates == dtx)[0]
            if not len(hit):
                continue
            i = int(hit[0])
            if ok[i]:
                print(f"  {code} {dtx}  PASS")
            else:
                bad = [(nm, arr[i], thr) for nm, arr, thr in det['checks']
                       if not (np.nan_to_num(arr[i], nan=-1e9) >= thr)]
                txt = " | ".join(f"{nm}(now={v:.2f},需>={t})" for nm, v, t in bad)
                print(f"  {code} {dtx}  FAIL: {txt}")

    # ---------------- B. FES 分量拆解 ----------------
    print("\n" + "=" * 108)
    print("  B. FES 分量级对照（生产公式，逐项）")
    print("=" * 108)
    comps = ['et', 'grade', 'vs', 'cs', 'ext', 'ex', 'gap_pen', 'fr', 'tc', 'mf', 'mom', '_pos', 'final', 'rating']
    head = f"  {'分量':<10}" + "".join(f"{c[:6]+'/'+d[4:]:>13}" for c, d in CASES)
    print(head)
    table = {}
    for code, dtx in CASES:
        g = df[df['ts_code'] == code].reset_index(drop=True)
        _m2, F = AB.hard_filter(g)
        if F is None:
            continue
        sC = F['sC']
        dif = sC.ewm(span=12, adjust=False).mean().values - sC.ewm(span=26, adjust=False).mean().values
        F['bar'] = 2 * (dif - pd.Series(dif).ewm(span=9, adjust=False).mean().values)
        i = int(np.where(g['trade_date'].values == dtx)[0][0])
        f = AB.day_fields(i, F)
        et, grade, forbid = V._entry_timing(f)
        ext = V._extension_penalty(f.get('距MA20', 0), f.get('5日涨幅', 0), f.get('10日涨幅', 0))
        ex = V._exhaustion_penalty(f)
        gap, gpen = V._gap_risk(f, False)
        vs = V._volume_structure(f)
        cs = V._chip_structure(f)
        mf = V._market_fit(f, 1.0)
        tc = V._trend_continuation(f)
        fr = V._failure_risk(f)
        mom = V._momentum_strength(f)
        _pos = et * 0.35 + vs + cs + ext + ex + gpen - fr
        final = max(0, min(100, round(1.43 * _pos + 16.9, 1)))
        rating = 'S' if (final >= 85 and gap != 'Extreme' and et >= 80) else \
                 'A' if (final >= 75 and gap != 'Extreme') else 'B' if final >= 65 else 'C'
        table[(code, dtx)] = dict(et=et, grade=grade, vs=vs, cs=cs, ext=ext, ex=ex,
                                  gap_pen=gpen, fr=fr, tc=tc, mf=mf, mom=mom,
                                  _pos=round(_pos, 2), final=final, rating=rating)
        C_, H_ = F['C'], F['H']
        c60 = (C_[i] / C_[i - 60] - 1) * 100
        c120 = (C_[i] / C_[i - 120] - 1) * 100
        dist200 = (C_[i] / float(np.max(H_[max(0, i - 199):i + 1])) - 1) * 100
        print(f"  -- {code} {dtx} | 量能爆发评分={f['量能爆发评分']} 量比={f['今日量比']} "
              f"距MA20={f['距MA20']} 距MA5={f['距MA5']} 距20日高={f['距20日高']} "
              f"5日={f['5日涨幅']} 10日={f['10日涨幅']} 60日={c60:.1f} 120日={c120:.1f} "
              f"距200日高={dist200:.1f} MA20趋势={f['MA20趋势']} "
              f"下蹲={f['下蹲信号']} 死叉临界={f['死叉临界']} MACD={f['MACD状态']}")
    for k in comps:
        line = f"  {k:<10}"
        for c, d in CASES:
            v = table.get((c, d), {}).get(k, '—')
            line += f"{str(v):>13}"
        print(line)

    # ---------------- C. cohort 组合条件筛选 ----------------
    csv = os.path.join(SOLO, '_tmp_vsw_cohort.csv')
    if not os.path.exists(csv):
        return
    d = pd.read_csv(csv)
    d['squat'] = d['squat'].astype(str).str.lower() == 'true'
    d['death'] = d['death'].astype(str).str.lower() == 'true'
    STOP = AB.STOP

    def stat(mask, name, pool):
        r = pool.loc[mask, 'ret'].values
        if len(r) < 25:
            return None
        return (f"  {name:<44}{len(r):>6}{(r>0).mean()*100:>7.1f}%{r.mean():>+8.2f}%"
                f"{np.median(r):>+7.2f}%{(r<=STOP).mean()*100:>7.1f}%{(r>10).mean()*100:>8.1f}%")

    def report(pool, title):
        print(f"\n  【{title}】样本 {len(pool):,} | 基准 胜率{(pool['ret']>0).mean()*100:.1f}% "
              f"均{pool['ret'].mean():+.2f}%")
        print(f"  {'条件':<44}{'笔数':>6}{'胜率':>8}{'均收益':>9}{'中位':>8}{'止损率':>8}{'大赢>10%':>9}")
        T = pd.Series(True, index=pool.index)
        s85 = pool['score'] < 85
        c60 = pool['c60'] < 0
        d5 = pool['d5'] < -3
        d200 = pool['dist200'] < -20
        vr = pool['vr'] >= 1.0
        nd = pool['death'] == False  # noqa: E712
        conds = [
            ('全部', T),
            ('量能爆发评分<85', s85),
            ('量能爆发评分75~85', (pool['score'] >= 75) & s85),
            ('60日涨幅<0', c60),
            ('距MA5<-3%', d5),
            ('距200日高<-20%', d200),
            ('量比>=1.0', vr),
            ('量比1.0~1.2', vr & (pool['vr'] <= 1.2)),
            ('非死叉临界', nd),
            ('距20日高<=-6', pool['dhh20'] <= -6),
            ('评分<85 & 60日<0', s85 & c60),
            ('评分<85 & 距MA5<-3', s85 & d5),
            ('60日<0 & 距200日高<-20', c60 & d200),
            ('量比>=1.0 & 60日<0', vr & c60),
            ('评分<85 & 非死叉', s85 & nd),
            ('评分<85 & 60日<0 & 非死叉', s85 & c60 & nd),
            ('评分<85 & 距MA5<-3 & 60日<0', s85 & d5 & c60),
        ]
        for nm, m in conds:
            line = stat(m, nm, pool)
            if line:
                print(line)

    print("\n" + "=" * 108)
    print("  C. 组合条件筛选（cohort CSV）")
    print("=" * 108)
    report(d, '全池')
    report(d[d['squat']], '下蹲 分支')


if __name__ == '__main__':
    main()
