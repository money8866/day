# -*- coding: utf-8 -*-
"""
临时：VSW「量能活跃度」（T0后均量 / T0前20日均量）分档标定（用后即删）

口径与 _vsw_squat_recalib.py 一致：
  事件池 = detect_volume_surge_swing 基础硬过滤 + 下蹲信号（优选/扩大）
  买入 = T+1 开盘；卖出 = T+5 收盘；盘中 -7% 止损（止损位 = T0 前一交易日收盘价，与现行「止损位」字段同源）
  成本 = 0.25%   区间 = 2025-01-01 ~ 2026-09-30

关键：本脚本把 VOL_ACTIVITY_MIN 临时置 0，使「量能活跃度 < 2.0」的下蹲事件也进池，
      从而能看清 2.0 这道闸门到底在切什么、往上提能否提纯。

用法：python _vsw_activity_analysis.py [--start 20250101] [--end 20260930] [--max-stocks 300]
"""
import sys
import os
import time
import json
import argparse
import sqlite3

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

HIST_START = '20250101'
COST = 0.0025
HOLD_D = 5
STOP_PCT = -0.07
MKT_MIN = 300000   # 30亿（万元），与现行生产一致

P_MAX_VOL_RATIO = 2.6
P_VOLR_GT2_MIN = 5
P_RANGE_SWING_MIN = 35.0
P_VOL_VS_HIST_MIN = 50.0
P_VOL_VS_BASE_MIN = 1.1


def prefilter(df):
    if df is None or len(df) < 180:
        return False
    recent = df.tail(200)
    if len(recent) < 60:
        return False
    vol = recent['vol'].values.astype(float)
    pre_close = recent['pre_close'].values.astype(float)
    high = recent['high'].values.astype(float)
    low = recent['low'].values.astype(float)
    close = recent['close'].values.astype(float)
    vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().values
    vol_ratio = vol / np.maximum(vol_ma20, 1)
    if np.nanmax(vol_ratio) < P_MAX_VOL_RATIO:
        return False
    if int(np.sum(vol_ratio > 2.0)) < P_VOLR_GT2_MIN:
        return False
    rng_high, rng_low = float(np.max(high)), float(np.min(low))
    if rng_low <= 0 or (rng_high / rng_low - 1) * 100 < P_RANGE_SWING_MIN:
        return False
    hist_vol_max = float(np.max(df['vol'].values.astype(float)))
    if hist_vol_max <= 0 or np.max(vol) / hist_vol_max * 100 < P_VOL_VS_HIST_MIN:
        return False
    today_pct = (close[-1] / pre_close[-1] - 1) * 100 if pre_close[-1] > 0 else 0
    if today_pct <= -7.0:
        return False
    recent_vol = float(np.mean(vol[-10:]))
    base_vol = max(float(np.mean(vol[-40:-10])), 1)
    if recent_vol / base_vol < P_VOL_VS_BASE_MIN:
        return False
    return True


def fwd_return(bars, k, stop_price):
    n = len(bars)
    if k + HOLD_D >= n:
        return None
    entry = bars[k + 1][0]
    if entry <= 0:
        return None
    gap_stop = bool(stop_price and stop_price > 0 and stop_price / entry - 1 <= STOP_PCT)
    exit_px, exit_day, hit_stop = None, None, False
    for j in range(1, HOLD_D + 1):
        o, l, c = bars[k + j][0], bars[k + j][2], bars[k + j][3]
        if stop_price and stop_price > 0:
            if o <= stop_price:
                exit_px, exit_day, hit_stop = o, j, True
                break
            if l <= stop_price:
                exit_px, exit_day, hit_stop = stop_price, j, True
                break
        if j == HOLD_D:
            exit_px, exit_day = c, j
    if exit_px is None or exit_px <= 0:
        return None
    return (exit_px / entry - 1) - COST, exit_day, hit_stop, gap_stop


def stats(sub):
    if len(sub) == 0:
        return (0, None, None, None)
    return (len(sub), round(float((sub['ret'] > 0).mean() * 100), 1),
            round(float(sub['ret'].mean() * 100), 2),
            round(float(sub['stopped'].mean() * 100), 1))


def print_table(title, rows):
    print(f"\n--- {title} ---")
    print(f"{'档位':<16}{'n':>7}{'胜率%':>9}{'均收益%':>10}{'止损率%':>10}")
    for lab, n, wr, mr, sl in rows:
        if n == 0:
            print(f"{lab:<16}{0:>7}{'-':>9}{'-':>10}{'-':>10}")
        else:
            print(f"{lab:<16}{n:>7}{wr:>9}{mr:>10}{sl:>10}")


def today_pool_activity():
    """今日（20261008）落库的 vsw 池，逐只算量能活跃度"""
    import volume_surge_select as V
    db = os.path.join(BASE_DIR, 'picks_db', 'stock_picks.db')
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM stock_pick WHERE pick_date='20261008' AND strategy_id='vsw'").fetchall()
    c.close()
    V.TRADE_DATE = '20261008'
    out = []
    for r in rows:
        try:
            res = V.detect_volume_surge_swing(r['ts_code'], r['stock_name'])
        except Exception:
            res = None
        act = (res or {}).get('量能活跃度', None)
        shrink = (res or {}).get('缩量比', None)
        out.append((r['ts_code'], r['stock_name'], r['signal'], act, shrink))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20250101')
    ap.add_argument('--end', default='20260930')
    ap.add_argument('--max-stocks', type=int, default=0)
    args = ap.parse_args()

    import volume_surge_select as V
    from stock_cache import get_conn, load_stock_basic

    # 放开量能活跃度闸门：让 <2.0 的事件也进池，才能看清这道闸门的作用
    _orig_gate = V.VOL_ACTIVITY_MIN
    V.VOL_ACTIVITY_MIN = 0.0

    print(f"载入 daily_cache（{HIST_START} 起）...")
    with get_conn() as conn:
        codes = [r[0] for r in conn.execute(
            'SELECT DISTINCT ts_code FROM daily_cache WHERE trade_date>=? ORDER BY ts_code',
            (HIST_START,))]
        codes = [c for c in codes if not c.endswith('.BJ')]
        print(f"  股票 {len(codes)} 只")

        st_set = set()
        try:
            sb = load_stock_basic()
            if sb is not None and 'name' in sb.columns and 'ts_code' in sb.columns:
                st_set = set(sb.loc[sb['name'].fillna('').astype(str).str.upper().str.contains('ST'),
                                    'ts_code'].tolist())
        except Exception as e:
            print(f"  [警告] ST 名单失败({e})")

        mv = pd.read_sql(
            "SELECT ts_code, trade_date, total_mv FROM daily_basic_cache WHERE trade_date>=?",
            conn, params=(args.start,))
        mv['trade_date'] = mv['trade_date'].astype(str)
        mv = mv[mv['total_mv'] > MKT_MIN]
        big = set(mv['ts_code'].unique())
        print(f"  市值>30亿 覆盖 {len(big)} 只")

        # 目标池：区间内曾出现 >30亿 且 非 ST 非北交所
        pool = [c for c in codes if c in big and c not in st_set]
        if args.max_stocks:
            pool = pool[:args.max_stocks]
        print(f"  目标池 {len(pool)} 只")

        events = []
        t0 = time.time()
        for idx, code in enumerate(pool, 1):
            if idx % 500 == 0:
                print(f"  {idx}/{len(pool)} 事件={len(events)} 用时{time.time()-t0:.0f}s", flush=True)
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
                if d < args.start or d > args.end:
                    continue
                if k + HOLD_D >= len(df):
                    break
                if not prefilter(df.iloc[:k + 1]):
                    continue
                V.TRADE_DATE = d
                try:
                    r = V.detect_volume_surge_swing(code, code, _df_override=df.iloc[:k + 1].copy())
                except Exception:
                    r = None
                if not r or not r.get('下蹲信号'):
                    continue
                stop_px = r.get('止损位') or 0
                fr = fwd_return(bars, k, stop_px)
                if fr is None:
                    continue
                ret, exit_day, hit_stop, gap_stop = fr
                events.append({
                    'ts_code': code, 'date': d, 'grade': r.get('下蹲等级', ''),
                    'activity': r.get('量能活跃度', 0), 'shrink': r.get('缩量比', 0),
                    'bq': r.get('量能爆发评分', 0), 'd_ma5': r.get('距MA5', 0),
                    'd_ma20': r.get('距MA20', 0), 'chg60': r.get('60日涨幅', 0),
                    't0pct': r.get('T0涨幅', 0), 'stepd': r.get('起量台阶天数', 0),
                    'dvol5': r.get('5日量能变化', 0),
                    'ret': ret, 'stopped': hit_stop, 'gap_stop': gap_stop,
                })

    if not events:
        print("\n未产生任何下蹲事件。")
        return

    ev = pd.DataFrame(events)
    n = len(ev)
    print("\n" + "=" * 72)
    print(f"下蹲事件池（活跃度闸门已放开）n={n}  区间 {args.start}~{args.end}")
    print(f"全池：胜率 {100*(ev['ret']>0).mean():.1f}%  均收益 {100*ev['ret'].mean():+.2f}%  "
          f"止损率 {100*ev['stopped'].mean():.1f}%")
    print(f"  其中 gap_stop n={int(ev['gap_stop'].sum())} ({100*ev['gap_stop'].mean():.1f}%)")

    _bins = [(0.0, 1.5, '<1.5'), (1.5, 2.0, '1.5~2.0'), (2.0, 2.5, '2.0~2.5'),
             (2.5, 3.0, '2.5~3.0'), (3.0, 4.0, '3.0~4.0'), (4.0, 5.0, '4.0~5.0'),
             (5.0, 7.0, '5.0~7.0'), (7.0, 10.0, '7.0~10.0'), (10.0, 1e9, '>=10')]
    print_table("量能活跃度 分档（细）",
                [(lab,) + stats(ev[(ev['activity'] >= lo) & (ev['activity'] < hi)])
                 for (lo, hi, lab) in _bins])

    print_table("量能活跃度 门槛提升（累计 >= 阈值，模拟提高 VOL_ACTIVITY_MIN）",
                [(f">={th}",) + stats(ev[ev['activity'] >= th])
                 for th in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 7.0, 10.0]])

    # 分年稳健性（现行 2.0 vs 候选 3.0/4.0/5.0）
    print("\n--- 门槛提升的分年稳健性（胜率/均收益/止损率）---")
    print(f"{'门槛':<10}{'2025':>26}{'2026':>26}")
    for th in [2.0, 3.0, 4.0, 5.0]:
        cells = []
        for yr in ['2025', '2026']:
            s = ev[(ev['activity'] >= th) & (ev['date'].str[:4] == yr)]
            if len(s) == 0:
                cells.append('-')
            else:
                cells.append(f"n{len(s)} {100*(s['ret']>0).mean():.1f}%/{100*s['ret'].mean():+.2f}%/{100*s['stopped'].mean():.0f}%")
        print(f"v>={th:<8}{cells[0]:>26}{cells[1]:>26}")

    ic = ev[['activity', 'ret']].corr(method='spearman').iloc[0, 1]
    print(f"\n--- Spearman raw IC（活跃度 vs 收益）: {ic:+.4f} ---")

    # 活跃度 × 缩量比 交叉（现行两闸门是否重复）
    print_table("交叉参考：活跃度>=3.0 且 缩量比>=0.55",
                [("双条件满足",) + stats(ev[(ev['activity'] >= 3.0) & (ev['shrink'] >= 0.55)]),
                 ("仅活跃度>=3.0",) + stats(ev[ev['activity'] >= 3.0]),
                 ("仅缩量比>=0.55",) + stats(ev[ev['shrink'] >= 0.55])])

    ev.to_csv(os.path.join(BASE_DIR, 'report_daily', 'vsw_activity_events.csv'),
              index=False, encoding='utf-8-sig')
    print(f"\n事件明细已存 report_daily/vsw_activity_events.csv")

    V.VOL_ACTIVITY_MIN = _orig_gate

    # 今日池（20261008）逐只活跃度
    print("\n" + "=" * 72)
    print("今日（20261008）落库下蹲池的活跃度分布")
    tp = today_pool_activity()
    vals = [a for (_, _, _, a, _) in tp if a is not None]
    print(f"  共 {len(tp)} 只，取到活跃度 {len(vals)} 只")
    for th in [2.0, 2.5, 3.0, 4.0, 5.0, 7.0]:
        print(f"  活跃度>={th}: 保留 {sum(1 for x in vals if x >= th)} 只（剔除 {sum(1 for x in vals if x < th)}）")
    print("  明细（活跃度升序）:")
    for tc, nm, sig, act, shr in sorted(tp, key=lambda x: (x[3] is None, x[3])):
        print(f"    {nm}({tc}) {sig} 活跃度={act} 缩量比={shr}")


if __name__ == '__main__':
    main()
