# -*- coding: utf-8 -*-
"""
VSW 下蹲排序键重标定（20261006）—— 为修正 _squat_rank_key 方向反转问题。

背景：
  volume_surge_select._squat_rank_key 的注释如实记录了新旧口径结论冲突：
    旧口径(n=453)：距MA5 越负越好（<-3% 档 47.7%/+2.41% vs -1.5~1% 档 35.9%/-1.11%）
    新口径(n=27) ：距MA5 越负越差（<-3% 档 16.7%/-2.10% vs -3~-1.5% 档 50.0%/+2.58%）
  但排序键仍沿用旧结论（距MA5 升序），实际选中了新口径下最差的一档。
  n=27 样本过小，不足以直接推翻 → 本脚本用 daily_cache 全市场重跑，
  在足够样本上重新标定「距MA5 / 60日涨幅 / 量能爆发评分」三个排序键。

口径（与策略注释一致）：
  事件池 = detect_volume_surge_swing 基础硬过滤 + 下蹲信号（优选/扩大）
  买入 = T+1 开盘；卖出 = T+5 收盘；盘中 -7% 止损（止损位 = T0 开盘价，与策略「止损位」字段同源）
  成本 = 0.25%
  回测区间 = 2025-01-01 ~ 2026-09-30（与 _squat_rank_key 注释的标定区间一致）

用法：python _vsw_squat_recalib.py [--start 20250101] [--end 20260930]
"""
import sys
import os
import time
import argparse

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

HIST_START = '20250101'   # 与 get_hist_data 的取数起点一致
COST = 0.0025
HOLD_D = 5
STOP_PCT = -0.07

# L1 预筛阈值（与 volume_surge_select L1 硬条件一致，向量化预筛以加速）
P_MAX_VOL_RATIO = 2.6
P_VOLR_GT2_MIN = 5
P_RANGE_SWING_MIN = 35.0
P_VOL_VS_HIST_MIN = 50.0
P_VOL_VS_BASE_MIN = 1.1


def prefilter(df):
    """向量化 L0/L1 预筛：返回是否可能通过基础硬条件（避免对全市场逐只跑 detect）"""
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
    """T+1开盘买 / T+5收盘卖 / 盘中-7%止损（止损位=T0开盘价），返回净收益率或None

    bars: 该股按日期升序的 numpy 数组，k = 事件日下标
    """
    n = len(bars)
    if k + HOLD_D >= n:
        return None
    entry = bars[k + 1][0]                     # T+1 开盘
    if entry <= 0:
        return None
    # 策略止损位 = T0 开盘价。若相对 T+1 开盘已低出 STOP_PCT 以上，则「一开仓即已止损」——
    # 这不是盘中触发，而是入场即无效。单独标记，避免与盘中止损混在一起。
    gap_stop = bool(stop_price and stop_price > 0
                    and stop_price / entry - 1 <= STOP_PCT)
    exit_px, exit_day, hit_stop = None, None, False
    for j in range(1, HOLD_D + 1):
        o, l, c = bars[k + j][0], bars[k + j][2], bars[k + j][3]
        if stop_price and stop_price > 0:
            if o <= stop_price:                # 跳空低开 → 按开盘价成交（保守）
                exit_px, exit_day, hit_stop = o, j, True
                break
            if l <= stop_price:                # 盘中触及 → 按止损价成交
                exit_px, exit_day, hit_stop = stop_price, j, True
                break
        if j == HOLD_D:
            exit_px, exit_day = c, j
    if exit_px is None or exit_px <= 0:
        return None
    return (exit_px / entry - 1) - COST, exit_day, hit_stop, gap_stop


def fwd_return_multi(bars, k, stop_px, cost=COST, hold_d=HOLD_D, stop_pct=None):
    """多口径前视收益：同时按多种止损锚定口径计算 T+1开盘买 / T+hold_d收盘卖 的净收益。

    stop_px   : 绝对价格止损线（如 T0开盘价）。None 表示不用绝对价止损。
    stop_pct  : 相对 T+1 开盘价的百分比止损（如 -0.07）。
    返回 dict: {口径名: (ret, hit_stop, gap_stop)}
      gap_stop = 入场即已低于止损线（开盘就触发，非盘中触发）
    """
    n = len(bars)
    out = {}
    if k + hold_d >= n:
        return out
    entry = bars[k + 1][0]
    if entry <= 0:
        return out

    def run(name, sp):
        if not sp or sp <= 0:
            out[name] = None
            return
        gap = (sp / entry - 1) <= STOP_PCT
        px, day, hit = None, None, False
        for j in range(1, hold_d + 1):
            o, l, c = bars[k + j][0], bars[k + j][2], bars[k + j][3]
            if o <= sp:
                px, day, hit = o, j, True
                break
            if l <= sp:
                px, day, hit = sp, j, True
                break
            if j == hold_d:
                px, day = c, j
        if px is None or px <= 0:
            out[name] = None
            return
        out[name] = ((px / entry - 1) - cost, hit, gap)

    run('t0_open', stop_px)                    # 现行：T0 标志日开盘价
    run('prev_close', bars[k][3])              # 备选：前一日（T-1，即事件日当天的收盘价）
    if stop_pct is not None:
        run('pct7', entry * (1 + stop_pct))     # 备选：T+1 开盘价 × (1-7%)
    return out


def bucket_stats(df, col, bins, labels):
    """按分档统计：n / 胜率 / 均收益 / 止损率"""
    out = []
    for (lo, hi), lab in zip(bins, labels):
        sub = df[(df[col] >= lo) & (df[col] < hi)] if hi is not None else df[df[col] >= lo]
        if len(sub) == 0:
            out.append((lab, 0, None, None, None))
            continue
        out.append((lab, len(sub), round(float((sub['ret'] > 0).mean() * 100), 1),
                    round(float(sub['ret'].mean() * 100), 2),
                    round(float(sub['stopped'].mean() * 100), 1)))
    return out


def print_table(title, rows):
    print(f"\n--- {title} ---")
    print(f"{'档位':<16}{'n':>7}{'胜率%':>9}{'均收益%':>10}{'止损率%':>10}")
    for lab, n, wr, mr, sl in rows:
        if n == 0:
            print(f"{lab:<16}{0:>7}{'-':>9}{'-':>10}{'-':>10}")
        else:
            print(f"{lab:<16}{n:>7}{wr:>9}{mr:>10}{sl:>10}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20250101')
    ap.add_argument('--end', default='20260930')
    ap.add_argument('--max-stocks', type=int, default=0, help='>0 则只取前 N 只（冒烟测试）')
    args = ap.parse_args()

    import volume_surge_select as V
    from stock_cache import get_conn, load_stock_basic

    print(f"载入 daily_cache（{HIST_START} 起）...")
    with get_conn() as conn:
        codes = [r[0] for r in conn.execute(
            'SELECT DISTINCT ts_code FROM daily_cache WHERE trade_date>=? ORDER BY ts_code',
            (HIST_START,))]
        codes = [c for c in codes if not c.endswith('.BJ')]
        print(f"  股票 {len(codes)} 只")

        try:
            sb = load_stock_basic()
            st_set = set()
            if sb is not None and 'name' in sb.columns and 'ts_code' in sb.columns:
                st_set = set(sb.loc[sb['name'].fillna('').astype(str).str.upper().str.contains('ST'),
                                'ts_code'].tolist())
            print(f"  ST 池 {len(st_set)} 只")
        except Exception as e:
            print(f"  [警告] ST 名单读取失败({e})，不剔除")

        # 市值>50亿（万单位 → 500000）
        print("  载入市值快照...")
        mv = pd.read_sql(
            "SELECT ts_code, trade_date, total_mv FROM daily_basic_cache WHERE trade_date>=?",
            conn, params=(args.start,))
        mv['trade_date'] = mv['trade_date'].astype(str)
        mv = mv[mv['total_mv'] > 500000]
        big = set(mv['ts_code'].unique())
        print(f"  市值>50亿 覆盖 {len(big)} 只")

        pool = [c for c in codes if c in big and c not in st_set]
        if args.max_stocks:
            pool = pool[:args.max_stocks]
        print(f"  目标池 {len(pool)} 只")

        events, n_pre, n_pass = [], 0, 0
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
                # 事件日需留出 T+5
                if k + HOLD_D >= len(df):
                    break
                n_pre += 1
                if not prefilter(df.iloc[:k + 1]):
                    continue
                n_pass += 1
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
                # 多止损口径对照（同一批事件，仅止损锚不同）
                alt = fwd_return_multi(bars, k, stop_px, stop_pct=-0.07)
                events.append({
                    'ts_code': code, 'date': d, 'grade': r.get('下蹲等级', ''),
                    'bq': r.get('量能爆发评分', 0), 'd_ma5': r.get('距MA5', 0),
                    'd_ma20': r.get('距MA20', 0), 'chg60': r.get('60日涨幅', 0),
                    'chg5': r.get('5日涨幅', 0), 'chg10': r.get('10日涨幅', 0),
                    'shrink': r.get('缩量比', 0), 'dvol5': r.get('5日量能变化', 0),
                    't0pct': r.get('T0涨幅', 0), 'stepd': r.get('起量台阶天数', 0),
                    'd_hh20': r.get('距20日高', 0), 'theme': r.get('所属主题', '') or '',
                    'ret': ret, 'stopped': hit_stop, 'gap_stop': gap_stop,
                    'exit_day': exit_day,
                    'ret_prevclose': (alt['prev_close'][0] if alt.get('prev_close') else None),
                    'stop_prevclose': (alt['prev_close'][1] if alt.get('prev_close') else None),
                    'gap_prevclose': (alt['prev_close'][2] if alt.get('prev_close') else None),
                    'ret_pct7': (alt['pct7'][0] if alt.get('pct7') else None),
                    'stop_pct7': (alt['pct7'][1] if alt.get('pct7') else None),
                })

    if not events:
        print("\n未产生任何下蹲事件，无法标定。请检查区间/阈值。")
        return

    ev = pd.DataFrame(events)
    n = len(ev)
    print("\n" + "=" * 72)
    print(f"下蹲事件池 n={n}  区间 {args.start}~{args.end}")
    print(f"全池：胜率 {100*(ev['ret']>0).mean():.1f}%  均收益 {100*ev['ret'].mean():+.2f}%  "
          f"止损率 {100*ev['stopped'].mean():.1f}%")
    print(f"  其中「入场即已低于止损位」gap_stop n={int(ev['gap_stop'].sum())} "
          f"({100*ev['gap_stop'].mean():.1f}%)，该子集均收益 {100*ev.loc[ev['gap_stop'],'ret'].mean() if ev['gap_stop'].any() else 0:+.2f}%")
    print(f"  剔除 gap_stop 后：n={int((~ev['gap_stop']).sum())} 胜率 {100*(ev.loc[~ev['gap_stop'],'ret']>0).mean():.1f}% "
          f"均收益 {100*ev.loc[~ev['gap_stop'],'ret'].mean():+.2f}% 止损率 {100*ev.loc[~ev['gap_stop'],'stopped'].mean():.1f}%")
    pref = ev['ret'] > 0
    print(f"  优选 n={int((ev['grade']=='优选').sum())} 胜率 {100*pref[ev['grade']=='优选'].mean() if (ev['grade']=='优选').any() else 0:.1f}% "
          f"均收益 {100*ev.loc[ev['grade']=='优选','ret'].mean() if (ev['grade']=='优选').any() else 0:+.2f}%")
    m = ev['grade'] != '优选'
    if m.any():
        print(f"  扩大 n={int(m.sum())} 胜率 {100*pref[m].mean():.1f}% 均收益 {100*ev.loc[m,'ret'].mean():+.2f}%")
    for yr in sorted(ev['date'].str[:4].unique()):
        s = ev[ev['date'].str[:4] == yr]
        print(f"  {yr}: n={len(s)} 胜率 {100*(s['ret']>0).mean():.1f}% 均收益 {100*s['ret'].mean():+.2f}%")

    print_table("① 距MA5 分档（当前排序键 #2，旧结论：越负越优）",
                bucket_stats(ev, 'd_ma5',
                              [(-100, -5), (-5, -3), (-3, -1.5), (-1.5, 0), (0, 3), (3, 100)],
                              ['<-5%', '-5~-3%', '-3~-1.5%', '-1.5~0%', '0~3%', '>3%']))
    print_table("② 60日涨幅 分档（当前排序键 #3，旧结论：越低越优）",
                bucket_stats(ev, 'chg60',
                              [(-100, 0), (0, 15), (15, 30), (30, 60), (60, 1000)],
                              ['<0%', '0~15%', '15~30%', '30~60%', '>60%']))
    print_table("③ 量能爆发评分 分档（当前排序键 #1，旧结论：越低越优）",
                bucket_stats(ev, 'bq',
                              [(0, 70), (70, 80), (80, 90), (90, 95), (95, 101)],
                              ['<70', '70~80', '80~90', '90~95', '>=95']))
    print_table("④ 距20日高 分档（补充参考）",
                bucket_stats(ev, 'd_hh20',
                              [(-100, -15), (-15, -8), (-8, -3), (-3, 0), (0, 100)],
                              ['<-15%', '-15~-8%', '-8~-3%', '-3~0%', '>=0%']))
    print_table("⑤ 5日涨幅 分档（补充参考）",
                bucket_stats(ev, 'chg5',
                              [(-100, -3), (-3, 0), (0, 3), (3, 8), (8, 100)],
                              ['<-3%', '-3~0%', '0~3%', '3~8%', '>8%']))
    print_table("⑥ 缩量比 分档（补充参考）",
                bucket_stats(ev, 'shrink',
                              [(0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 99)],
                              ['<0.4', '0.4~0.6', '0.6~0.8', '0.8~1.0', '>=1.0']))

    # Spearman IC：三个候选排序键 vs 收益
    def ic(col, ascending):
        v = ev[[col, 'ret']].corr(method='spearman').iloc[0, 1]
        return round(float(v), 4) * (1 if ascending else -1)

    print("\n--- Spearman IC（原始值，正=与收益正相关；排序升序时原始负相关=升序更优）---")
    print(f"  距MA5        raw IC {ev[['d_ma5','ret']].corr(method='spearman').iloc[0,1]:+.4f}")
    print(f"  60日涨幅     raw IC {ev[['chg60','ret']].corr(method='spearman').iloc[0,1]:+.4f}")
    print(f"  量能爆发评分 raw IC {ev[['bq','ret']].corr(method='spearman').iloc[0,1]:+.4f}")

    # 当前排序键 vs 候选排序键 的分段对比（首尾各取 1/3）
    print("\n--- 当前键 vs 候选键 首尾对比（各取 1/3 池）---")
    n3 = max(1, n // 3)
    cur = ev.sort_values(['bq', 'd_ma5', 'chg60']).head(n3)
    print(f"  当前键(评分升/距MA5升/60日升) 前1/3: n={len(cur)} 胜率 {100*(cur['ret']>0).mean():.1f}% "
          f"均收益 {100*cur['ret'].mean():+.2f}% 止损 {100*cur['stopped'].mean():.1f}%")

    # 候选：评分降序（IC 为负 → 高分反而差）+ 距MA5 取「距 -3% 最近」+ 60日涨幅升序
    ev['_bq_neg'] = -ev['bq']
    ev['_d_ma5_band'] = (ev['d_ma5'] + 3.0).abs()   # -3% 附近最优 → 到 -3% 的距离
    for label, key in [
        ('候选A 评分降/距MA5近-3%/60日升', ['_bq_neg', '_d_ma5_band', 'chg60']),
        ('候选B 仅距MA5近-3% 单键', ['_d_ma5_band']),
        ('候选C 评分降/距MA5升/60日升', ['_bq_neg', 'd_ma5', 'chg60']),
        ('候选D 距MA5升/评分降/60日升', ['d_ma5', '_bq_neg', 'chg60']),
    ]:
        cand = ev.sort_values(key).head(n3)
        print(f"  {label} 前1/3: n={len(cand)} 胜率 {100*(cand['ret']>0).mean():.1f}% "
              f"均收益 {100*cand['ret'].mean():+.2f}% 止损 {100*cand['stopped'].mean():.1f}%")
    for label, key in [('当前键', ['bq', 'd_ma5', 'chg60']), ]:
        cand = ev.sort_values(key).tail(n3)
        print(f"  {label} 后1/3: n={len(cand)} 胜率 {100*(cand['ret']>0).mean():.1f}% "
              f"均收益 {100*cand['ret'].mean():+.2f}% 止损 {100*cand['stopped'].mean():.1f}%")

    # ===== 止损位口径对照（20261006 用户要求改为「前一日收盘价」）=====
    print("\n" + "=" * 72)
    print("止损位口径对照（T+1开盘买 / T+5收盘 / 含0.25%成本）")
    variants = [
        ('t0_open  T0开盘价（现行）', 'ret', 'stopped', 'gap_stop'),
        ('prev_close 前一日收盘价', 'ret_prevclose', 'stop_prevclose', 'gap_prevclose'),
        ('pct7      开盘价×(1-7%)', 'ret_pct7', 'stop_pct7', None),
    ]
    print(f"{'口径':<30}{'n':>7}{'胜率%':>9}{'均收益%':>10}{'止损率%':>10}{'入场即失效%':>13}")
    for lab, rc, sc, gc in variants:
        sub = ev[ev[rc].notna()]
        if len(sub) == 0:
            print(f"{lab:<30}{0:>7}")
            continue
        g = f"{100*sub[gc].mean():.1f}" if gc else "-"
        print(f"{lab:<30}{len(sub):>7}{100*(sub[rc]>0).mean():>9.1f}"
              f"{100*sub[rc].mean():>10.2f}{100*sub[sc].mean():>10.1f}{g:>13}")

    # 分年稳健性
    print(f"\n{'口径':<30}{'2025 胜率/均收益/止损':>26}{'2026 胜率/均收益/止损':>26}")
    for lab, rc, sc, gc in variants:
        cells = []
        for yr in ['2025', '2026']:
            s = ev[(ev['date'].str[:4] == yr) & ev[rc].notna()]
            if len(s) == 0:
                cells.append('-')
                continue
            cells.append(f"{100*(s[rc]>0).mean():.1f}%/{100*s[rc].mean():+.2f}%/{100*s[sc].mean():.0f}%")
        print(f"{lab:<30}{cells[0]:>26}{cells[1]:>26}")

    # 新排序键 + 新止损位的组合效果
    print("\n--- 新排序键(-距MA5/评分/60日) Top3 在不同止损口径下 ---")
    for lab, rc, sc, gc in variants:
        s = ev.sort_values(['_m5' if '_m5' in ev.columns else 'd_ma5', 'bq', 'chg60',
                            'ts_code', 'date']).copy()
        s['_m5'] = -s['d_ma5']
        s = s.sort_values(['_m5', 'bq', 'chg60', 'ts_code', 'date']).groupby('date').head(3)
        s = s[s[rc].notna()]
        g = f"{100*s[gc].mean():.1f}%" if gc else "-"
        print(f"  {lab:<30} Top3 n={len(s)} 胜率{100*(s[rc]>0).mean():.1f}% "
              f"均收益{100*s[rc].mean():+.2f}% 止损{100*s[sc].mean():.1f}% 入场即失效{g}")
    for yr in ['2025', '2026']:
        s = ev.copy()
        s['_m5'] = -s['d_ma5']
        s = s.sort_values(['_m5', 'bq', 'chg60', 'ts_code', 'date']).groupby('date').head(3)
        s = s[(s['date'].str[:4] == yr) & s['ret_prevclose'].notna()]
        print(f"    {yr} prev_close: n={len(s)} 胜率{100*(s['ret_prevclose']>0).mean():.1f}% "
              f"均收益{100*s['ret_prevclose'].mean():+.2f}% 止损{100*s['stop_prevclose'].mean():.1f}%")

    print("\n--- 每日 Top-N 组合（T+1开盘买/T+5收盘，最贴近实盘「每日下蹲池取前N」）---")
    def daily_top_n(key, ntop=1):
        g = ev.sort_values(key + ['ts_code', 'date']).groupby('date').head(ntop)
        if len(g) == 0:
            return 0, None, None, None
        return (len(g), round(float((g['ret'] > 0).mean() * 100), 1),
                round(float(g['ret'].mean() * 100), 2), round(float(g['stopped'].mean() * 100), 1))

    keys = {
        '当前键(评分升/距MA5升/60日升)': ['bq', 'd_ma5', 'chg60'],
        'A 评分降/距MA5近-3%/60日升': ['_bq_neg', '_d_ma5_band', 'chg60'],
        'B 仅距MA5近-3%': ['_d_ma5_band'],
        'C 评分降/距MA5升/60日升': ['_bq_neg', 'd_ma5', 'chg60'],
        'E 评分升/距MA5近0%/60日近20%': ['bq', '_ma5_near0', '_chg60_near20'],
        'F 评分升/距MA5近0%': ['bq', '_ma5_near0'],
        'G 仅距20日高升序': ['d_hh20'],
        'H 评分升/距20日高升/距MA5近0%': ['bq', 'd_hh20', '_ma5_near0'],
    }
    ev['_ma5_near0'] = (ev['d_ma5'] + 0.75).abs()   # -1.5~0% 档最优 → 到 -0.75% 的距离
    ev['_chg60_near20'] = (ev['chg60'] - 22.0).abs()  # 15~30% 档最优 → 到 22% 的距离
    print(f"{'排序键':<32}{'Top1 胜率/均收益':>22}{'Top3 胜率/均收益':>24}")
    for label, key in keys.items():
        n1, w1, m1, _ = daily_top_n(key, 1)
        n3_, w3, m3, _ = daily_top_n(key, 3)
        print(f"{label:<32}{f'{n1}笔 {w1}%/{m1:+.2f}%':>22}{f'{n3_}笔 {w3}%/{m3:+.2f}%':>24}")
    # 随机基准（同池随机等权，即不做任何排序）
    base_n = len(ev.groupby('date').head(1))
    base_w = round(float((ev['ret'] > 0).mean() * 100), 1)
    base_m = round(float(ev['ret'].mean() * 100), 2)
    print(f"{'(参考)全池随机等权':<30}{f'{base_n}笔 {base_w}%/{base_m:+.2f}%':>22}")

    ev.to_csv(os.path.join(BASE_DIR, 'report_daily', 'vsw_squat_recalib_events.csv'),
              index=False, encoding='utf-8-sig')
    print(f"\n事件明细已存 report_daily/vsw_squat_recalib_events.csv")


if __name__ == '__main__':
    main()
