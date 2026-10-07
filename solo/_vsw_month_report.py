# -*- coding: utf-8 -*-
"""
202609 月度信号实跑 + 收益率分析（20261007）

按真实选股路径（`volume_surge_select.run` 的同一套 detect 逻辑）逐日跑 202609 全月，
并用 daily_cache 计算 T+1开盘买 / T+5收盘 的实际收益，口径与历史标定完全一致：
  · 入池/信号：detect_volume_surge_swing（含 P0 截面分与强买降级）
  · 买入：T+1 开盘
  · 卖出：T+5 收盘，或盘中跌破「T0 前一交易日收盘价」即止损离场
  · 成本：0.25%
  · 止损位：T0 前一交易日收盘价（20261006 起口径）

与其他标定脚本的区别：
  `_vsw_squat_recalib.py` 是**全历史区间**扫描（2025-01~2026-09，n=8733），
  用于策略口径标定；本脚本只跑 202609 单月（21 个交易日），用于**近期实况复盘**，
  且按每日「下蹲排序键 Top3」与「全池等权」对照，检验线上执行效果。

用法：
  python _vsw_month_report.py [--month 202609] [--topn 3]
"""
import sys
import os
import time
import argparse
from collections import Counter

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

HIST_START = '20250101'
COST = 0.0025
HOLD_D = 5


def prefilter(df):
    """L1 向量化预筛（与标定脚本同口径，避免对明显不合格的票调用 detect）"""
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
    if len(vol) >= 40:
        if float(np.mean(vol[-10:])) / max(float(np.mean(vol[-40:-10])), 1) < 1.1:
            return False
    return True


def fwd_return(bars, k, stop_px):
    """T+1开盘买 / T+5收盘 or 盘中跌破止损位 → 净收益率"""
    if k + HOLD_D >= len(bars):
        return None
    entry = bars[k + 1][0]
    if entry <= 0:
        return None
    px, day, hit = None, None, False
    for j in range(1, HOLD_D + 1):
        o, l, c = bars[k + j][0], bars[k + j][2], bars[k + j][3]
        if stop_px and stop_px > 0:
            if o <= stop_px:
                px, day, hit = o, j, True
                break
            if l <= stop_px:
                px, day, hit = stop_px, j, True
                break
        if j == HOLD_D:
            px, day = c, j
    if px is None or px <= 0:
        return None
    return (px / entry - 1) - COST, hit, day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', default='202609')
    ap.add_argument('--topn', type=int, default=3)
    args = ap.parse_args()
    M = args.month

    import volume_surge_select as V
    from stock_cache import get_conn, load_stock_basic

    t_start = time.time()
    with get_conn() as conn:
        dates = [r[0] for r in conn.execute(
            'SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date LIKE ? ORDER BY trade_date',
            (M + '%',))]
        print(f"{M} 交易日 {len(dates)} 个：{dates[0]} ~ {dates[-1]}")
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
        big = set(pd.read_sql(
            "SELECT ts_code FROM daily_basic_cache WHERE trade_date>=? GROUP BY ts_code "
            "HAVING MAX(total_mv)>500000", conn, params=(M + '01',))['ts_code'].unique())
        pool = [c for c in codes if c in big and c not in st]
        print(f"目标池 {len(pool)} 只（市值>50亿 · 剔BJ/ST）", flush=True)

        rows = []
        for idx, code in enumerate(pool, 1):
            if idx % 500 == 0:
                print(f"  {idx}/{len(pool)} 信号={len(rows)} {time.time()-t_start:.0f}s", flush=True)
            df = pd.read_sql(
                "SELECT trade_date,open,high,low,close,pre_close,vol FROM daily_cache "
                "WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                conn, params=(code, HIST_START))
            if df.empty:
                continue
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.reset_index(drop=True)
            bars = df[['open', 'high', 'low', 'close']].values.astype(float)
            dts = df['trade_date'].values
            dset = set(dts)
            for k, d in enumerate(dts):
                if d not in dates:
                    continue
                if k + HOLD_D >= len(df):
                    continue
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
                fr = fwd_return(bars, k, r.get('止损位') or 0)
                if fr is None:
                    continue
                ret, hit, exday = fr
                rows.append({
                    'date': d, 'ts_code': code, 'name': r.get('名称', code),
                    'squat': bool(r.get('下蹲信号')), 'grade': r.get('下蹲等级', ''),
                    'strong': bool(r.get('强买信号')), 'watch': bool(r.get('观察信号')),
                    'wave': bool(r.get('蓄势大涨信号')),
                    'bq': r.get('量能爆发评分', 0),
                    'd_ma5': r.get('距MA5', 0), 'd_ma20': r.get('距MA20', 0),
                    'chg60': r.get('60日涨幅', 0), 'shrink': r.get('缩量比', 0),
                    'stepd': r.get('起量台阶天数', 0),
                    'stop': r.get('止损位'), 'close': r.get('close'),
                    'ret': ret, 'stopped': hit, 'exit_day': exday,
                })

    if not rows:
        print("本月无信号")
        return
    ev = pd.DataFrame(rows)
    ev['ym'] = ev['date'].str[:6]

    # ===== 汇总报表 =====
    print("\n" + "=" * 78)
    print(f"VSW {M} 月度实跑  共 {len(ev)} 个标的信号  用时 {time.time()-t_start:.0f}s")
    print("=" * 78)

    # 1. 每日信号数
    print("\n【一】每日信号数与收益")
    print(f"{'日期':<10}{'命中':>5}{'下蹲':>5}{'优选':>5}{'扩大':>5}{'强买':>5}{'观察':>5}{'蓄势':>5}"
          f"{'下蹲胜率':>10}{'下蹲均收益':>11}")
    for d in dates:
        g = ev[ev['date'] == d]
        if len(g) == 0:
            print(f"{d:<10}{0:>5}{0:>5}{0:>5}{0:>5}{0:>5}{0:>5}{0:>5}{'-':>10}{'-':>11}")
            continue
        s = g[g['squat']]
        if len(s):
            wr = f"{100*(s['ret']>0).mean():.0f}%"
            mr = f"{100*s['ret'].mean():+.2f}%"
        else:
            wr = mr = '-'
        print(f"{d:<10}{len(g):>5}{len(s):>5}{len(s[s['grade']=='优选']):>5}"
              f"{len(s[s['grade']!='优选']):>5}{len(g[g['strong']]):>5}{len(g[g['watch']]):>5}"
              f"{len(g[g['wave']]):>5}{wr:>10}{mr:>11}")

    sq = ev[ev['squat']].copy()
    print(f"\n【二】月度汇总")
    print(f"  命中 {len(ev)} | 下蹲 {len(sq)} | 强买 {len(ev[ev['strong']])} | "
          f"观察 {len(ev[ev['watch']])} | 蓄势 {len(ev[ev['wave']])}")
    if len(sq) == 0:
        print("  本月无下蹲买点 → 无落库、无实际执行")
        return
    print(f"\n  下蹲全池等权：胜率 {100*(sq['ret']>0).mean():.1f}%  "
          f"均收益 {100*sq['ret'].mean():+.2f}%  止损率 {100*sq['stopped'].mean():.1f}%  "
          f"（{len(sq)} 笔）")

    # 2. 优选/扩大
    print(f"\n【三】下蹲分级")
    for lab, tag in [('优选', '优选'), ('扩大', '扩大')]:
        s = sq[sq['grade'] == tag]
        if len(s) == 0:
            continue
        print(f"  {lab}：n={len(s):>3}  胜率 {100*(s['ret']>0).mean():5.1f}%  "
              f"均收益 {100*s['ret'].mean():+6.2f}%  止损 {100*s['stopped'].mean():5.1f}%")

    # 3. 每日 Top-N（真实执行口径）
    print(f"\n【四】按每日排序键取 Top{args.topn}（=实际执行口径）")
    sq['_m5'] = -sq['d_ma5']
    tn = sq.sort_values(['date', '_m5', 'bq', 'chg60']).groupby('date').head(args.topn)
    print(f"  Top{args.topn}：n={len(tn):>3}  胜率 {100*(tn['ret']>0).mean():.1f}%  "
          f"均收益 {100*tn['ret'].mean():+.2f}%  止损率 {100*tn['stopped'].mean():.1f}%")
    for lab, tag in [('优选', '优选'), ('扩大', '扩大')]:
        s = tn[tn['grade'] == tag]
        if len(s) == 0:
            continue
        print(f"    其中{lab}：n={len(s):>3}  胜率 {100*(s['ret']>0).mean():5.1f}%  "
              f"均收益 {100*s['ret'].mean():+6.2f}%  止损 {100*s['stopped'].mean():5.1f}%")

    # 4. 因子分档
    print(f"\n【五】下蹲因子分档（本月样本小，看方向不下结论）")
    for col, pairs in [
        ('d_ma5', [(-100, -3, '<-3%'), (-3, -1.5, '-3~-1.5%'), (-1.5, 0, '-1.5~0%'),
                   (0, 3, '0~3%'), (3, 100, '>3%')]),
        ('bq', [(0, 85, '<85'), (85, 90, '85~90'), (90, 95, '90~95'), (95, 101, '>=95')]),
        ('chg60', [(-100, 0, '<0%'), (0, 15, '0~15%'), (15, 30, '15~30%'), (30, 1000, '>30%')]),
        ('shrink', [(0, 0.6, '<0.6'), (0.6, 0.8, '0.6~0.8'), (0.8, 1.0, '0.8~1.0'), (1.0, 99, '>=1.0')]),
        ('stepd', [(0, 5, '<5日'), (5, 15, '5~15日'), (15, 30, '15~30日'), (30, 100, '>30日')]),
    ]:
        print(f"  -- {col} --")
        for lo, hi, lab in pairs:
            s = sq[(sq[col] >= lo) & (sq[col] < hi)]
            if len(s) == 0:
                continue
            print(f"     {lab:<10} n={len(s):>3}  胜率 {100*(s['ret']>0).mean():5.1f}%  "
                  f"均收益 {100*s['ret'].mean():+6.2f}%  止损 {100*s['stopped'].mean():5.1f}%  "
                  f"中位{s[col].median():.2f}")

    # 5. 个股明细
    print(f"\n【六】下蹲买点明细（按每日排序键）")
    print(f"{'日期':<10}{'代码':<12}{'等级':<5}{'评分':>5}{'距MA5':>8}{'距MA20':>8}{'60日':>8}"
          f"{'缩量':>6}{'止损位':>9}{'收益%':>8}{'卖出日':>6}")
    for _, r in tn.iterrows():
        print(f"{r['date']:<10}{r['ts_code']:<12}{r['grade']:<5}{r['bq']:>5.0f}"
              f"{r['d_ma5']:>+8.1f}{r['d_ma20']:>+8.1f}{r['chg60']:>+8.1f}{r['shrink']:>6.2f}"
              f"{r['stop']:>9.2f}{100*r['ret']:>+8.2f}{r['exit_day']:>6}")

    out = os.path.join(BASE_DIR, 'report_daily', f'vsw_month_{M}.csv')
    ev.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n明细已存 {out}")


if __name__ == '__main__':
    main()
