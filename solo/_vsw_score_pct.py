# -*- coding: utf-8 -*-
"""
P0 验证：量能爆发评分「绝对阈值封顶」→「自身历史分位」重标定（20261007）

背景：
  20260930 池内 68 只命中、评分中位 93~95、大量顶格 100，强买 37 只。
  根因：5 个评分分量全是 min(当前值/固定阈值, 1) * 权重 的封顶项：
      vol_score    = min(max_vol_ratio / 5.0, 1) * 30
      freq_score   = min(vol_ratio_gt2 / 7, 1) * 20
      amp_score    = min(avg_amplitude / 7, 1) * 20
      big_amp_score= min(amp_gt8_count / 15, 1) * 15
      swing_score  = min(range_swing / 60, 1) * 15
  放量行情中这些量普遍超过阈值 → 全部顶格 → 评分失去区分度，
  下游「total_score>=65/70/75」等门槛形同虚设。

处置方案（本脚本验证）：
  把每个分量改为「该股自身近 60 日历史分布中的分位」× 权重。
  优点：① detect() 内单股即可算，无需全池截面 → 改动小、可回测历史；
        ② 分位天然落在 0~1，永不饱和；
        ③ 语义更合理：「相对自身历史是否异常放量」比「绝对量比多大」更贴合策略意图。

口径：与 _vsw_squat_recalib.py 完全一致
  事件池 = 基础硬过滤 + 下蹲信号；T+1开盘买/T+5收盘/止损位=T0前一日收盘价/含0.25%成本
  区间 2025-01-01~2026-09-30

用法：python _vsw_score_pct.py [--start 20250101] [--end 20260930] [--max-stocks 0]
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

HIST_START = '20250101'
COST = 0.0025
HOLD_D = 5
STOP_PCT = -0.07
PCT_WIN = 60      # 自身历史分位回看窗口（交易日）
PCT_MIN = 20      # rolling 最小样本

WEIGHTS = {'vol': 30.0, 'freq': 20.0, 'amp': 20.0, 'big_amp': 15.0, 'swing': 15.0}


def prefilter(df):
    """L1 向量化预筛（与 _vsw_squat_recalib.py 同口径）"""
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
    vr = vol / np.maximum(vol_ma20, 1)
    if np.nanmax(vr) < 2.6:
        return False
    if int(np.sum(vr > 2.0)) < 5:
        return False
    rh, rl = float(np.max(high)), float(np.min(low))
    if rl <= 0 or (rh / rl - 1) * 100 < 35:
        return False
    hv = float(np.max(df['vol'].values.astype(float)))
    if hv <= 0 or np.max(vol) / hv * 100 < 50:
        return False
    tp = (close[-1] / pre_close[-1] - 1) * 100 if pre_close[-1] > 0 else 0
    if tp <= -7.0:
        return False
    rv = float(np.mean(vol[-10:]))
    bv = max(float(np.mean(vol[-40:-10])), 1)
    if rv / bv < 1.1:
        return False
    return True


def _pct_rank(cur, hist):
    """当前值在历史序列中的分位（0~1）。历史为 rolling 序列，含 NaN 自动剔除。"""
    h = hist[~np.isnan(hist)]
    if len(h) == 0:
        return 0.5
    return float(np.mean(h <= cur))


def score_abs(df, t0_i):
    """旧口径：绝对阈值封顶（5 个分量全用 min(x/阈值, 1) * 权重）"""
    recent = df.tail(200)
    vol = recent['vol'].values.astype(float)
    pre_close = recent['pre_close'].values.astype(float)
    high = recent['high'].values.astype(float)
    low = recent['low'].values.astype(float)
    vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().values
    vr = vol / np.maximum(vol_ma20, 1)
    amp = (high - low) / np.maximum(pre_close, 0.01) * 100
    cur = {
        'vol': float(np.nanmax(vr)),
        'freq': float(np.sum(vr > 2.0)),
        'amp': float(np.mean(amp[t0_i:])),
        'big_amp': float(np.sum(amp > 8)),
        'swing': float((np.max(high) / np.min(low) - 1) * 100) if np.min(low) > 0 else 0.0,
    }
    s = (min(cur['vol'] / 5.0, 1) * WEIGHTS['vol']
         + min(cur['freq'] / 7, 1) * WEIGHTS['freq']
         + min(cur['amp'] / 7, 1) * WEIGHTS['amp']
         + min(cur['big_amp'] / 15, 1) * WEIGHTS['big_amp']
         + min(cur['swing'] / 60, 1) * WEIGHTS['swing'])
    return s, cur


def score_hist_pct(df, t0_i):
    """候选口径①：自身近 60 日历史分位（**已验证退化，仅保留作对照**）

    退化原因：池内标的均已通过 L1 硬门槛（量比≥2.6、量比>2天数≥5、区间振幅≥35%…），
    这些量相对自身历史必然接近最大值 → 分位恒≈1.0 → 评分全部顶格。
    实测 n=1211 中 1011 只得分完全相同（80.0），唯一值仅 22 个。
    保留仅为记录「为何不采用自身历史分位」。
    """
    recent = df.tail(200)
    vol = recent['vol'].values.astype(float)
    pre_close = recent['pre_close'].values.astype(float)
    high = recent['high'].values.astype(float)
    low = recent['low'].values.astype(float)
    vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().values
    vr = vol / np.maximum(vol_ma20, 1)
    amp = (high - low) / np.maximum(pre_close, 0.01) * 100
    cur = {
        'vol': float(np.nanmax(vr)),
        'freq': float(np.sum(vr > 2.0)),
        'amp': float(np.mean(amp[t0_i:])),
        'big_amp': float(np.sum(amp > 8)),
        'swing': float((np.max(high) / np.min(low) - 1) * 100) if np.min(low) > 0 else 0.0,
    }
    hist = {
        'vol': pd.Series(vr).rolling(PCT_WIN, min_periods=PCT_MIN).max().values,
        'freq': pd.Series((vr > 2.0).astype(float)).rolling(PCT_WIN, min_periods=PCT_MIN).sum().values,
        'amp': pd.Series(amp).rolling(PCT_WIN, min_periods=PCT_MIN).mean().values,
        'big_amp': pd.Series((amp > 8).astype(float)).rolling(PCT_WIN, min_periods=PCT_MIN).sum().values,
        'swing': (pd.Series(high).rolling(PCT_WIN, min_periods=PCT_MIN).max().values
                  / pd.Series(low).rolling(PCT_WIN, min_periods=PCT_MIN).min().values - 1) * 100,
    }
    s = 0.0
    for k, w in WEIGHTS.items():
        h = hist[k]
        h = h[~np.isnan(h)]
        s += _pct_rank(cur[k], h) * w
    return s, cur


def score_cross_pct(df, t0_i):
    """候选口径②：返回 5 个分量的**原始值**（供池内截面百分位换算）

    截面百分位必须在全池范围内算（每日池内排名），故本函数只导出原始分量，
    由 main() 在收集完当日全部事件后统一做 rank(pct=True)。
    """
    return score_abs(df, t0_i)


def fwd_return(bars, k, stop_px):
    n = len(bars)
    if k + HOLD_D >= n:
        return None
    entry = bars[k + 1][0]
    if entry <= 0:
        return None
    px, day, hit = None, None, False
    for j in range(1, HOLD_D + 1):
        o, l, c = bars[k + 1][0], bars[k + j][2], bars[k + j][3]
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
    return (px / entry - 1) - COST, hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20250101')
    ap.add_argument('--end', default='20260930')
    ap.add_argument('--max-stocks', type=int, default=0)
    args = ap.parse_args()

    import volume_surge_select as V
    from stock_cache import get_conn, load_stock_basic

    with get_conn() as conn:
        codes = [r[0] for r in conn.execute(
            'SELECT DISTINCT ts_code FROM daily_cache WHERE trade_date>=? ORDER BY ts_code',
            (HIST_START,))]
        codes = [c for c in codes if not c.endswith('.BJ')]
        st_set = set()
        try:
            sb = load_stock_basic()
            if sb is not None and 'name' in sb.columns:
                st_set = set(sb.loc[sb['name'].fillna('').astype(str).str.upper()
                               .str.contains('ST'), 'ts_code'].tolist())
        except Exception:
            pass
        mv = pd.read_sql("SELECT ts_code FROM daily_basic_cache WHERE total_mv>500000", conn)
        big = set(mv['ts_code'].unique())
        pool = [c for c in codes if c in big and c not in st_set]
        if args.max_stocks:
            pool = pool[:args.max_stocks]
        print(f"目标池 {len(pool)} 只（{args.start}~{args.end}）", flush=True)

        rows = []
        t0 = time.time()
        for idx, code in enumerate(pool, 1):
            if idx % 800 == 0:
                print(f"  {idx}/{len(pool)} 事件={len(rows)} {time.time()-t0:.0f}s", flush=True)
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
                sub = df.iloc[:k + 1]
                if not prefilter(sub):
                    continue
                V.TRADE_DATE = d
                try:
                    r = V.detect_volume_surge_swing(code, code, _df_override=sub.copy())
                except Exception:
                    r = None
                if not r or not r.get('下蹲信号'):
                    continue
                t0i = len(sub) - 1 - int(r['起量台阶天数'])
                if t0i < 0:
                    continue
                abs_s, comp = score_abs(sub, t0i)
                hist_s, _ = score_hist_pct(sub, t0i)
                fr = fwd_return(bars, k, r.get('止损位') or 0)
                if fr is None:
                    continue
                ret, hit = fr
                rows.append({'ts_code': code, 'date': d, 'grade': r.get('下蹲等级', ''),
                             'old_score': abs_s, 'hist_score': hist_s,
                             'bq_old': r['量能爆发评分'],
                             # 5 个分量原始值 → 供池内截面百分位
                             'c_vol': comp['vol'], 'c_freq': comp['freq'],
                             'c_amp': comp['amp'], 'c_bigamp': comp['big_amp'],
                             'c_swing': comp['swing'],
                             'd_ma5': r['距MA5'], 'chg60': r['60日涨幅'],
                             'd_ma20': r['距MA20'], 'ret': ret, 'stopped': hit})

    if not rows:
        print("无事件")
        return
    ev = pd.DataFrame(rows)

    # ===== 候选口径②：全池截面百分位（每日池内 rank，0~100 均匀分布）=====
    # 分量方向：vol/freq/big_amp/swing 为「越大越爆发」→ 排名升序即分位升序；
    #           amp（日均振幅）过大者收益反而差（见 _extension_penalty 倒U型结论）→ 取反向
    ev['cross_score'] = 0.0
    for date, idx in ev.groupby('date').groups.items():
        g = ev.loc[idx]
        acc = np.zeros(len(g))
        for col, w, asc in [('c_vol', WEIGHTS['vol'], True),
                            ('c_freq', WEIGHTS['freq'], True),
                            ('c_amp', WEIGHTS['amp'], False),
                            ('c_bigamp', WEIGHTS['big_amp'], True),
                            ('c_swing', WEIGHTS['swing'], True)]:
            r = g[col].rank(pct=True, ascending=asc)
            acc += r.fillna(0.5).values * w
        ev.loc[idx, 'cross_score'] = acc

    print("\n" + "=" * 72)
    print(f"事件池 n={len(ev)}  区间 {args.start}~{args.end}")
    print(f"全池基准：胜率 {100*(ev['ret']>0).mean():.1f}%  均收益 {100*ev['ret'].mean():+.2f}%  "
          f"止损 {100*ev['stopped'].mean():.1f}%")

    print("\n--- 评分饱和度对比 ---")
    for col, lab in [('bq_old', '生产口径(绝对封顶)'), ('old_score', '脚本复算(绝对封顶)'),
                     ('hist_score', '候选①(自身历史分位)'), ('cross_score', '候选②(全池截面分位)')]:
        s = ev[col]
        print(f"  {lab:<24} 中位{s.median():5.1f}  p75={s.quantile(.75):5.1f}  "
              f">=95占比{100*(s>=95).mean():5.1f}%  =100占比{100*(s>=100).mean():5.1f}%  "
              f"唯一值{s.nunique():4d}")

    print("\n--- 每日 Top-N（各口径各自排序）---")
    def topn(col, ntop):
        g = ev.sort_values([col, 'ts_code', 'date']).groupby('date').head(ntop)
        return (len(g), round(float((g['ret'] > 0).mean() * 100), 1),
                round(float(g['ret'].mean() * 100), 2), round(float(g['stopped'].mean() * 100), 1))
    print(f"{'排序依据':<26}{'Top1':>20}{'Top3':>22}{'Top5':>22}")
    for col, lab in [('bq_old', '旧评分(生产口径)'), ('cross_score', '新评分(截面分位)')]:
        cells = []
        for n in (1, 3, 5):
            c, w, m, st = topn(col, n)
            cells.append(f"{c}笔 {w}%/{m:+.2f}%")
        print(f"{lab:<26}{cells[0]:>20}{cells[1]:>22}{cells[2]:>22}")

    print("\n--- 分年稳健性（Top3）---")
    for col, lab in [('bq_old', '旧评分'), ('cross_score', '新评分')]:
        g = ev.sort_values([col, 'ts_code', 'date']).groupby('date').head(3)
        parts = []
        for yr in ['2025', '2026']:
            s = g[g['date'].str[:4] == yr]
            parts.append(f"{yr} n={len(s):>3} {100*(s['ret']>0).mean():.1f}%/"
                         f"{100*s['ret'].mean():+.2f}%/止{100*s['stopped'].mean():.0f}%")
        print(f"  {lab:<12}{'   '.join(parts)}")

    print("\n--- Spearman IC vs 收益 ---")
    for col, lab in [('bq_old', '旧评分'), ('hist_score', '候选①历史分位'), ('cross_score', '候选②截面分位')]:
        print(f"  {lab:<16}{ev[[col, 'ret']].corr(method='spearman').iloc[0,1]:+.4f}")

    print("\n--- 门槛通过率（门槛形同虚设的直接证据）---")
    for thr in [65, 70, 75]:
        a = 100 * (ev['bq_old'] >= thr).mean()
        b = 100 * (ev['cross_score'] >= thr).mean()
        print(f"  >={thr}:  旧口径 {a:5.1f}%   新口径 {b:5.1f}%")

    out = os.path.join(BASE_DIR, 'report_daily', 'vsw_score_pct_events.csv')
    ev.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n明细已存 {out}")


if __name__ == '__main__':
    main()
