# -*- coding: utf-8 -*-
"""VSW FinalEntryScore「量能分剥离」A/B 回测（临时脚本，用完即删）

问题：FinalEntryScore 中的量能分量（BaseQuality=量能爆发评分归一 + MomentumStrength 的量比/涨跌量项）
      是否为负向贡献？剥离后只留位置/缩量类分量重排，TOP3 胜率是否提升？

口径：T+1 开盘买入 / T+5 收盘卖出（买入日不算，共5个交易日）/ 盘中 -7% 止损 / 含 0.25% 成本
池子：daily_cache 2024-02 起，VSW 基础硬过滤 + (MACD确认 or 下蹲) ，再套生产 MACD 阀门
评分：直接调用 volume_surge_select 的生产评分函数，保证公式一致（筹码 cs 无历史数据→置常数不影响组内排序）
"""
import os
import sys
import time
import sqlite3

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO)
import volume_surge_select as V   # noqa: E402

DB = r"d:\mystock\cache_daily\stock_data.db"
START, END = "20250101", "20260924"
HOLD, STOP, COST = 5, -7.0, 0.25


# ---------------- 生产硬过滤（向量化镜像） ----------------
def hard_filter(df):
    n = len(df)
    C = df['close'].values.astype(float)
    H = df['high'].values.astype(float)
    L = df['low'].values.astype(float)
    VOL = df['vol'].values.astype(float)
    PC = np.concatenate([[C[0]], C[:-1]])

    ok = np.ones(n, dtype=bool)
    if n < 180:
        return np.zeros(n, dtype=bool), None

    sC, sH, sL, sV = pd.Series(C), pd.Series(H), pd.Series(L), pd.Series(VOL)
    vol_ma20 = sV.rolling(20, min_periods=1).mean().values
    vratio = pd.Series(VOL / np.maximum(vol_ma20, 1))
    max_vr = vratio.rolling(200).max().values
    gt2 = (vratio > 2.0).astype(float).rolling(200).sum().values

    amp = (H - L) / np.maximum(PC, 0.01) * 100
    samp = pd.Series(amp)
    avg_amp120 = samp.rolling(120).mean().values
    gt8 = (samp > 8).astype(float).rolling(200).sum().values
    rs = (sH.rolling(200).max() / sL.rolling(200).min() - 1) * 100

    hist_max = sV.cummax().values
    vol_vs_hist = sV.rolling(200).max().values / np.maximum(hist_max, 1) * 100

    ma20 = sC.rolling(20).mean().values
    ma20_10 = pd.Series(ma20).shift(10).values
    ma20_20 = pd.Series(ma20).shift(20).values
    ma20_chg10 = (ma20 / ma20_10 - 1) * 100
    ma20_chg20 = (ma20 / ma20_20 - 1) * 100

    rv10 = sV.rolling(10).mean().values
    base_v = sV.rolling(30).mean().shift(10).values
    vol_vs_base = rv10 / np.maximum(base_v, 1)
    peak5 = sV.rolling(5).mean().rolling(200).max().values
    vol_vs_peak = rv10 / np.maximum(peak5, 1)

    today_pct = (C / np.maximum(PC, 1e-9) - 1) * 100

    vol_score = np.minimum(max_vr / 5.0, 1) * 30
    freq = np.minimum(gt2 / 7, 1) * 20
    ampc = np.minimum(avg_amp120 / 7, 1) * 20
    bigc = np.minimum(gt8 / 15, 1) * 15
    swc = np.minimum(rs / 60, 1) * 15
    total = vol_score + freq + ampc + bigc + swc

    v20 = vratio.values
    vr20_max = pd.Series(v20).rolling(20).max().values
    vr20_mean = pd.Series(v20).rolling(20).mean().values

    ok &= np.nan_to_num(max_vr, nan=-1) >= 2.6
    ok &= np.nan_to_num(gt2, nan=-1) >= 5
    ok &= np.nan_to_num(vr20_max, nan=-1) >= 2.0
    ok &= np.nan_to_num(vr20_mean, nan=-1) >= 1.4
    ok &= avg_amp120 >= 4.5
    ok &= rs >= 35
    ok &= today_pct > -7.0
    ok &= vol_vs_hist >= 50
    ok &= ~((ma20_chg10 < -1.0) | (ma20_chg20 < -2.0))
    ok &= ~(C < ma20 * 0.95)
    ok &= vol_vs_base >= 1.1
    ok &= vol_vs_peak >= 0.5
    ok &= total >= 65
    ok[:180] = False
    return ok, dict(C=C, H=H, L=L, VOL=VOL, PC=PC, total=total, max_vr=max_vr,
                    sC=sC, sV=sV, ma20=ma20, ma5=sC.rolling(5).mean().values,
                    ma10=sC.rolling(10).mean().values)


# ---------------- 逐候选日字段（与生产 detect 完全同口径） ----------------
def day_fields(idx, F):
    C, H, L, VOL, PC = F['C'], F['H'], F['L'], F['VOL'], F['PC']
    sC, sV = F['sC'], F['sV']
    lo = max(0, idx - 200 + 1)
    close_arr, high_arr, low_arr = C[lo:idx + 1], H[lo:idx + 1], L[lo:idx + 1]

    vma = pd.Series(VOL[lo:idx + 1]).rolling(20, min_periods=1).mean().values
    vr_arr = VOL[lo:idx + 1] / np.maximum(vma, 1)
    today_vr = float(vr_arr[-1])

    # MACD（全序列口径）
    bar = F['bar']
    cur, prev, p2 = bar[idx], bar[idx - 1], bar[idx - 2]
    st, macd_pass = '', False
    if prev < 0 < cur:
        st, macd_pass = '刚刚红柱 ✅', True
    elif cur < 0 and cur > prev > p2:
        st, macd_pass = '即将红柱（绿柱连续缩短）', True
    elif cur > 0 and prev > 0 and cur < abs(bar[idx - 3]) * 0.7:
        st, macd_pass = '红柱回调缩短（趋势延续）', True
    elif cur > 0 and prev > 0 and cur > prev and prev < p2:
        st, macd_pass = '红柱回调后反弹（趋势延续）', True

    death = False
    if st == '红柱回调缩短（趋势延续）':
        rp20 = float(np.max(np.maximum(bar[max(0, idx - 19):idx + 1], 0)))
        death = cur < max(0.15, rp20 * 0.2)

    chg5 = (C[idx] / C[idx - 5] - 1) * 100 if idx >= 5 else 0.0
    chg10 = (C[idx] / C[idx - 10] - 1) * 100 if idx >= 10 else 0.0
    pos20 = (C[idx] / F['ma20'][idx] - 1) * 100
    pos5 = (C[idx] / F['ma5'][idx] - 1) * 100
    pos10 = (C[idx] / F['ma10'][idx] - 1) * 100
    today_pct = (C[idx] / PC[idx] - 1) * 100

    us = 0.0
    for k in range(max(0, idx - 2), idx + 1):
        rng = max(H[k] - L[k], 0.01)
        us = max(us, (H[k] - max(C[k], PC[k])) / rng)

    tr = np.maximum(H - L, np.abs(C - PC))
    atr_now = float(np.mean(tr[max(0, idx - 19):idx + 1]))
    atr_prev = float(np.mean(tr[max(0, idx - 39):idx - 19])) if idx >= 40 else atr_now
    atr_exp = atr_now / max(atr_prev, 0.01)

    red_shrink = 0
    k = idx
    while k > max(0, idx - 5) and bar[k] < bar[k - 1] and bar[k] > 0:
        red_shrink += 1
        k -= 1

    uv, dv = [], []
    for k in range(max(0, idx - 9), idx + 1):
        pc = C[k - 1] if k > 0 else C[k]
        if C[k] > pc:
            uv.append(VOL[k])
        elif C[k] < pc:
            dv.append(VOL[k])
    vol_up = (np.mean(uv) / max(np.mean(dv), 0.01)) if (uv and dv) else 1.0

    up_str = yg_str = 0
    for k in range(idx, 0, -1):
        if C[k] > C[k - 1]:
            up_str += 1
        else:
            break
    for k in range(idx, 0, -1):
        if C[k] > PC[k]:
            yg_str += 1
        else:
            break

    ma20_chg = (F['ma20'][idx] / F['ma20'][idx - 10] - 1) * 100
    ma20_trend = 'up' if ma20_chg >= 0.5 else ('flat' if ma20_chg >= -0.5 else 'down')

    hh20 = float(np.max(high_arr[-20:]))
    dist_hh20 = (C[idx] / hh20 - 1) * 100 if hh20 > 0 else 0.0
    squat = (F['total'][idx] >= 65 and F['ma5'][idx] > F['ma10'][idx] > F['ma20'][idx]
             and today_vr <= 1.2 and today_pct <= 1.0 and pos5 <= 1.0
             and 0.0 <= pos20 <= 5.0 and -12.0 <= dist_hh20 <= -4.0)

    return {
        '量能爆发评分': round(float(F['total'][idx]), 1),
        '今日量比': round(today_vr, 2), '死叉临界': bool(death), 'MACD状态': st,
        '距MA20': round(pos20, 1), '距MA5': round(pos5, 1), '距MA10': round(pos10, 1),
        '5日涨幅': round(chg5, 1), '10日涨幅': round(chg10, 1),
        '连续阳线天数': yg_str, '连续上涨天数': up_str,
        'ATR扩张': round(atr_exp, 2), '近3日最大上影': round(us, 2),
        '红柱缩短天数': red_shrink, '涨日量/跌日量': round(float(vol_up), 2),
        'MA20趋势': ma20_trend, '距20日高': round(dist_hh20, 1),
        '_macd_pass': macd_pass, '下蹲信号': bool(squat),
    }


def mom_novol(s):
    """_momentum_strength 去掉量能项（量比 + 涨跌量比）"""
    sc = 0
    c5, c10 = s.get('5日涨幅', 0), s.get('10日涨幅', 0)
    streak = s.get('连续阳线天数', 0)
    if 3 <= c5 <= 18:
        sc += 3
    elif 0 <= c5 < 3:
        sc += 2
    elif 18 < c5 <= 30:
        sc += 1
    if 3 <= c10 <= 25:
        sc += 2
    elif 0 <= c10 < 3:
        sc += 1
    if streak >= 1:
        sc += 1
    if streak >= 2:
        sc += 1
    return max(0, min(10, sc))


def score_all(s):
    et, grade, forbid = V._entry_timing(s)
    s['_et_score'] = et
    ext = V._extension_penalty(s.get('距MA20', 0), s.get('5日涨幅', 0), s.get('10日涨幅', 0))
    ex = V._exhaustion_penalty(s)
    gap, gap_pen = V._gap_risk(s, False)
    vs = V._volume_structure(s)
    cs = 0.0                      # 筹码无历史数据 → 常数（不影响组内排序）
    mf = V._market_fit(s, 1.0)
    bq = V._base_quality(s)
    tc = V._trend_continuation(s)
    fr = V._failure_risk(s)
    mom = V._momentum_strength(s)
    mnov = mom_novol(s)

    def cap(x):
        return max(0.0, min(100.0, x))

    return {
        'et': et, 'grade': grade, 'forbid': forbid, 'gap': gap,
        'base': cap(bq + tc + et * 0.35 + vs + cs + mf + mom + ext + ex + gap_pen - fr),
        'nobq': cap(tc + et * 0.35 + vs + cs + mf + mom + ext + ex + gap_pen - fr),
        'novm': cap(bq + tc + et * 0.35 + vs + cs + mf + mnov + ext + ex + gap_pen - fr),
        'nolowq': cap(tc + et * 0.35 + vs + cs + mf + mnov + ext + ex + gap_pen - fr),
        'posonly': cap(et * 0.35 + vs + ext + ex + gap_pen - fr),
        'posnovs': cap(et * 0.35 + ext + ex + gap_pen - fr),
    }


SC_VARIANTS = ['base', 'nobq', 'novm', 'nolowq', 'posonly', 'posnovs']
VARIANTS = SC_VARIANTS + ['etonly', 'd20asc']


def ret_of(df, i):
    """信号日 i → 次日开盘买 → 持有5个交易日 → 盘中-7%止损，返回净收益%"""
    if i + 1 >= len(df):
        return None
    buy = float(df['open'].values[i + 1])
    if buy <= 0:
        return None
    C, L = df['close'].values, df['low'].values
    end = i + 1 + HOLD
    if end >= len(df):
        return None
    for j in range(i + 2, end + 1):
        if L[j] / buy - 1 <= STOP / 100.0:
            return STOP - COST
    return (C[end] / buy - 1) * 100 - COST


def main():
    t0 = time.time()
    con = sqlite3.connect(DB)
    print("[Load] 读取 daily_cache ...")
    df = pd.read_sql(
        "SELECT ts_code,trade_date,open,high,low,close,vol FROM daily_cache "
        "WHERE trade_date>='20240201' AND ts_code NOT LIKE '%.BJ'", con)
    con.close()
    df = df[~df['ts_code'].str.startswith(('8', '4', '9'))]
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    print(f"  {len(df):,} 行 / {df['ts_code'].nunique()} 只，{time.time()-t0:.1f}s")

    rows = []
    t1 = time.time()
    for code, g in df.groupby('ts_code', sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 180:
            continue
        mask, F = hard_filter(g)
        if F is None or not mask.any():
            continue
        sC = F['sC']
        ema12 = sC.ewm(span=12, adjust=False).mean().values
        ema26 = sC.ewm(span=26, adjust=False).mean().values
        dif = ema12 - ema26
        F['bar'] = 2 * (dif - pd.Series(dif).ewm(span=9, adjust=False).mean().values)
        dates = g['trade_date'].values
        for idx in np.where(mask)[0]:
            if dates[idx] < START or dates[idx] > END:
                continue
            f = day_fields(idx, F)
            if not (f['_macd_pass'] or f['下蹲信号']):
                continue
            blocked = bool(f['死叉临界']) or f['MACD状态'] == '即将红柱（绿柱连续缩短）'
            blocked = blocked and not f['下蹲信号']
            if blocked:
                continue
            sc = score_all(f)
            if sc['forbid']:
                continue
            r = ret_of(g, idx)
            if r is None:
                continue
            removed_all = (V._base_quality(f) + V._trend_continuation(f) + V._market_fit(f, 1.0)
                           + V._momentum_strength(f))
            removed_vol = V._momentum_strength(f) - mom_novol(f)
            rows.append({'ts_code': code, 'date': dates[idx], 'ret': r,
                         'squat': f['下蹲信号'], 'grade': sc['grade'],
                         'd20asc': -f['距MA20'], 'etonly': sc['et'],
                         'removed_all': removed_all, 'removed_vol': removed_vol,
                         'pos_part': sc['posonly'],
                         **{v: sc[v] for v in SC_VARIANTS}})
    print(f"[Scan] 事件 {len(rows):,} 条，{time.time()-t1:.1f}s")

    d = pd.DataFrame(rows)
    d['year'] = d['date'].str[:4]
    print("\n" + "=" * 92)
    print("  VSW 量能分剥离 A/B —— 每日 TOP3（T+1开盘买 / T+5收盘 / 盘中-7%止损 / 含0.25%成本）")
    print("=" * 92)
    print(f"  事件池 {len(d):,} 条 | 池内基准 胜率 {(d['ret']>0).mean()*100:.1f}% "
          f"均收益 {d['ret'].mean():+.2f}% 止损率 {(d['ret']<=STOP).mean()*100:.1f}%")

    rm = d['removed_all'].mean()
    newf = d['pos_part'] + rm
    print("\n[标尺校准] 被剥离分量 bq+tc+mf+mom 均值 = %.2f" % rm)
    print(f"  {'':<8}{'min':>8}{'P25':>8}{'中位':>8}{'P75':>8}{'max':>8}"
          f"{'>=65':>8}{'>=75':>8}{'>=85':>8}")
    for lbl, col in (('base', d['base']), ('pos+常数', newf)):
        print(f"  {lbl:<8}{col.min():>8.1f}{col.quantile(.25):>8.1f}{col.median():>8.1f}"
              f"{col.quantile(.75):>8.1f}{col.max():>8.1f}"
              f"{(col>=65).mean()*100:>7.1f}%{(col>=75).mean()*100:>7.1f}%{(col>=85).mean()*100:>7.1f}%")

    def top3_report(sub, title, ntop=3):
        print(f"\n  【{title}】事件 {len(sub):,} | TOP{ntop}")
        print(f"  {'排序方案':<12}{'笔数':>6}{'胜率':>8}{'均收益':>9}{'中位':>8}{'止损率':>8}{'盈亏比':>8}{'池内IC':>9}{'去重只数':>8}")
        for v in VARIANTS:
            picks = sub.sort_values(v, ascending=False).groupby('date', sort=False).head(ntop)
            r = picks['ret'].values
            if len(r) == 0:
                continue
            wr = (r > 0).mean() * 100
            w, l = r[r > 0], r[r <= 0]
            pl = (w.mean() / abs(l.mean())) if (len(w) and len(l)) else np.inf
            ic = sub[[v, 'ret']].corr(method='spearman').iloc[0, 1]
            print(f"  {v:<12}{len(r):>6}{wr:>7.1f}%{r.mean():>+8.2f}%{np.median(r):>+7.2f}%"
                  f"{(r<=STOP).mean()*100:>7.1f}%{pl:>8.2f}{ic:>9.3f}{picks['ts_code'].nunique():>7}")

    top3_report(d, '全样本 2025-01 ~ 2026-09')
    top3_report(d, '全样本 TOP1', 1)
    top3_report(d[d['year'] == '2025'], '2025 TOP3')
    top3_report(d[d['year'] == '2025'], '2025 TOP1', 1)
    top3_report(d[d['year'] == '2026'], '2026 TOP3')
    top3_report(d[d['year'] == '2026'], '2026 TOP1', 1)
    print(f"\n总耗时 {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
