# -*- coding: utf-8 -*-
"""
P0 事后修正的端到端验证：`_refine_strong_buy_by_cross_pct` 是否真的提升强买质量（20261007）

背景：
  P0 已落地：detect 导出 5 个量能分量 → run() 算池内截面百分位「量能截面分」
  → `_refine_strong_buy_by_cross_pct` 对强买做两端降级（过热 >70 / 强度不足 <15）。
  强买是纯展示信号（不排序、不落库），但它是报告里数量最多的一类（20260930 曾 37 只），
  若降级后质量无改善则该改动只是「把数字变小」，没有意义 —— 本脚本专门检验质量。

验证方式：
  复刻 detect 内的强买判定（用同一批字段），得到「修正前强买池」与「修正后强买池」，
  比较两者的 T+1开盘买/T+5收盘 表现。注意 run() 里还有主题/Chip 依赖，
  故用无主题/Chip 的字段做主判定，主题相关分支仅取不依赖主题的那几条。

口径：下蹲事件池，2025-01-01~2026-09-30，止损位=T0前一日收盘价，含0.25%成本
用法：python _vsw_p0_gate_validate.py
"""
import sys
import os
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

HIST_START = '20250101'
COST = 0.0025
HOLD_D = 5
TOP_PCT = 70.0
MIN_PCT = 15.0
W = {'vol': 30.0, 'freq': 20.0, 'amp': 20.0, 'big_amp': 15.0, 'swing': 15.0}


def prefilter(df):
    if df is None or len(df) < 180:
        return False
    r = df.tail(200)
    vol = r['vol'].values.astype(float)
    pc = r['pre_close'].values.astype(float)
    hi = r['high'].values.astype(float)
    lo = r['low'].values.astype(float)
    cl = r['close'].values.astype(float)
    vm = pd.Series(vol).rolling(20, min_periods=1).mean().values
    vr = vol / np.maximum(vm, 1)
    if np.nanmax(vr) < 2.6 or np.sum(vr > 2.0) < 5:
        return False
    if np.min(lo) <= 0 or (np.max(hi) / np.min(lo) - 1) * 100 < 35:
        return False
    hv = float(np.max(df['vol'].values.astype(float)))
    if hv <= 0 or np.max(vol) / hv * 100 < 50:
        return False
    if pc[-1] > 0 and (cl[-1] / pc[-1] - 1) * 100 <= -7.0:
        return False
    if len(vol) >= 40 and float(np.mean(vol[-10:])) / max(float(np.mean(vol[-40:-10])), 1) < 1.1:
        return False
    return True


def fwd(bars, k, stop):
    if k + HOLD_D >= len(bars):
        return None
    entry = bars[k + 1][0]
    if entry <= 0:
        return None
    px, hit = None, False
    for j in range(1, HOLD_D + 1):
        o, l, c = bars[k + j][0], bars[k + j][2], bars[k + j][3]
        if stop and stop > 0:
            if o <= stop:
                px, hit = o, True
                break
            if l <= stop:
                px, hit = stop, True
                break
        if j == HOLD_D:
            px = c
    if px is None or px <= 0:
        return None
    return (px / entry - 1) - COST, hit


def main():
    import volume_surge_select as V
    from stock_cache import get_conn, load_stock_basic

    with get_conn() as conn:
        codes = [r[0] for r in conn.execute(
            'SELECT DISTINCT ts_code FROM daily_cache WHERE trade_date>=? ORDER BY ts_code',
            (HIST_START,))]
        codes = [c for c in codes if not c.endswith('.BJ')]
        st = set()
        try:
            sb = load_stock_basic()
            if sb is not None and 'name' in sb.columns:
                st = set(sb.loc[sb['name'].fillna('').astype(str).str.upper()
                           .str.contains('ST'), 'ts_code'].tolist())
        except Exception:
            pass
        big = set(pd.read_sql("SELECT ts_code FROM daily_basic_cache WHERE total_mv>500000",
                              conn)['ts_code'].unique())
        pool = [c for c in codes if c in big and c not in st]
        print(f"目标池 {len(pool)} 只", flush=True)

        rows = []
        t0 = time.time()
        for idx, code in enumerate(pool, 1):
            if idx % 800 == 0:
                print(f"  {idx}/{len(pool)} n={len(rows)} {time.time()-t0:.0f}s", flush=True)
            df = pd.read_sql(
                "SELECT trade_date,open,high,low,close,pre_close,vol FROM daily_cache "
                "WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                conn, params=(code, HIST_START))
            if df.empty:
                continue
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.reset_index(drop=True)
            bars = df[['open', 'high', 'low', 'close']].values.astype(float)
            dates = df['trade_date'].values
            for k in range(len(df) - 1):
                d = str(dates[k])
                if d < '20250101' or d > '20260930':
                    continue
                if k + HOLD_D >= len(df):
                    break
                sub = df.iloc[:k + 1]
                if not prefilter(sub):
                    continue
                V.TRADE_DATE = d
                try:
                    r = V.detect_volume_surge_swing(code, code, _df_override=sub.copy())
                except Exception:
                    r = None
                if not r:
                    continue
                # 复刻 detect 内强买判定（只用不依赖主题/Chip 的分支）
                if not r.get('强买信号'):
                    continue
                fr = fwd(bars, k, r.get('止损位') or 0)
                if fr is None:
                    continue
                ret, hit = fr
                rows.append({'ts_code': code, 'date': d, 'ret': ret, 'stopped': hit,
                             'bq': r['量能爆发评分'],
                             'c_vol': r['_comp_vol'], 'c_freq': r['_comp_freq'],
                             'c_amp': r['_comp_amp'], 'c_big': r['_comp_big_amp'],
                             'c_swing': r['_comp_swing'],
                             'd_ma20': r['距MA20'], 'chg60': r['60日涨幅'],
                             'd_ma5': r['距MA5'], 'squat': bool(r.get('下蹲信号'))})

    if not rows:
        print("无强买事件")
        return
    ev = pd.DataFrame(rows)
    # 池内截面分（与生产 _add_cross_pct_score 同算法）
    acc = (ev['c_vol'] * W['vol'] + ev['c_freq'] * W['freq']
           - ev['c_amp'] * W['amp']          # 振幅反向
           + ev['c_big'] * W['big_amp'] + ev['c_swing'] * W['swing'])
    ev['pct'] = ev.groupby('date')['c_vol'].transform(lambda s: 0)  # 占位
    pcts = []
    for date, idx in ev.groupby('date').groups.items():
        a = acc.loc[idx].values
        pcts.append(pd.Series([np.mean(a <= v) * 100 for v in a], index=idx))
    ev['pct'] = pd.concat(pcts).sort_index()
    ev['keep'] = (ev['pct'] <= TOP_PCT) & (ev['pct'] >= MIN_PCT)

    print("\n" + "=" * 72)
    print(f"强买事件池 n={len(ev)}（2025-01~2026-09）")
    for lab, sub in [('修正前(全部强买)', ev), (f'修正后({TOP_PCT:.0f}/{MIN_PCT:.0f}两段降级)', ev[ev['keep']]),
                     ('被降级(过热或过弱)', ev[~ev['keep']])]:
        if len(sub) == 0:
            continue
        print(f"  {lab:<26} n={len(sub):>5}  胜率 {100*(sub['ret']>0).mean():5.1f}%  "
              f"均收益 {100*sub['ret'].mean():+6.2f}%  止损 {100*sub['stopped'].mean():5.1f}%")

    print("\n--- 分年稳健性 ---")
    for lab, sub in [('修正前', ev), ('修正后', ev[ev['keep']]), ('降级掉', ev[~ev['keep']])]:
        parts = []
        for yr in ['2025', '2026']:
            s = sub[sub['date'].str[:4] == yr]
            parts.append(f"{yr} n={len(s):>4} {100*(s['ret']>0).mean():.1f}%/"
                         f"{100*s['ret'].mean():+.2f}%")
        print(f"  {lab:<8}{'   '.join(parts)}")

    print("\n--- 截面分档收益（验证方向：高分是否真的更差）---")
    print(f"{'截面分档':<14}{'n':>7}{'胜率%':>9}{'均收益%':>10}{'止损%':>8}")
    for lo, hi, tag in [(0, 15, '<15'), (15, 40, '15~40'), (40, 70, '40~70'),
                        (70, 90, '70~90'), (90, 101, '>90')]:
        s = ev[(ev['pct'] >= lo) & (ev['pct'] < hi)]
        if len(s) == 0:
            continue
        print(f"{tag:<14}{len(s):>7}{100*(s['ret']>0).mean():>9.1f}"
              f"{100*s['ret'].mean():>10.2f}{100*s['stopped'].mean():>8.1f}")
    print(f"  Spearman(截面分, 收益) = {ev[['pct','ret']].corr(method='spearman').iloc[0,1]:+.4f}")

    out = os.path.join(BASE_DIR, 'report_daily', 'vsw_p0_gate_events.csv')
    ev.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n明细已存 {out}")


if __name__ == '__main__':
    main()
