# -*- coding: utf-8 -*-
"""
V4.6 全市场上涨空间扫描（双模式）
模式A：回踩买点（原V4.5）— 缩量回踩MA5/MA10
模式B：高位突破跟踪（新增）— 突破MA5且放量，趋势确认
"""

import sys, os, glob, math, time, json
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR  = r'D:\mystock'
CACHE_DIR = os.path.join(BASE_DIR, 'cache_daily')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')

SECTOR_BONUS = {'S': 1.20, 'A': 1.10, 'B': 1.00, 'C': 0.85, 'D': 0.70}
DEFAULT_SECTOR = 'B'

HOT_STOCKS = {
    '688525.SH': 'S', '300438.SZ': 'S', '688498.SH': 'A', '301308.SZ': 'B',
}

def sf(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except:
        return default

def calc_ma(s, n):
    return s.rolling(n, min_periods=1).mean()

def calc_rsi(close, period=2):
    if len(close) < period + 1:
        return pd.Series([50] * len(close), index=close.index)
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta.where(delta < 0, 0))
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.inf)
    return 100 - (100 / (1 + rs))

def macd_line(close, fast=12, slow=26, signal=9):
    if len(close) < slow + signal:
        return None, None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea

def score_one_stock_v46(csv_path):
    """V4.6双模式评分"""
    try:
        df = pd.read_csv(csv_path)
        if df.empty or len(df) < 60:
            return None

        close = df['close'].astype(float)
        vol   = df['vol'].astype(float) if 'vol' in df.columns else df.get('volume', df['amount'])
        high  = df['high'].astype(float) if 'high' in df.columns else close
        low   = df['low'].astype(float)  if 'low' in df.columns else close

        ma5   = calc_ma(close, 5)
        ma10  = calc_ma(close, 10)
        ma20  = calc_ma(close, 20)
        ma60  = calc_ma(close, 60)

        c   = sf(close.iloc[-1])
        c1  = sf(close.iloc[-2])
        c5d = sf(close.iloc[-6]) if len(close) > 5 else c
        ma5_c  = sf(ma5.iloc[-1])
        ma10_c = sf(ma10.iloc[-1])
        ma20_c = sf(ma20.iloc[-1])
        ma60_c = sf(ma60.iloc[-1])

        vol_ma20 = vol.rolling(20, min_periods=1).mean()
        vol_ratio = sf(vol.iloc[-1]) / sf(vol_ma20.iloc[-1]) if sf(vol_ma20.iloc[-1]) > 0 else 1.0
        vol_ratio_1 = sf(vol.iloc[-2]) / sf(vol_ma20.iloc[-2]) if sf(vol_ma20.iloc[-2]) > 0 else 1.0

        rsi2  = calc_rsi(close, 2)
        rsi14 = calc_rsi(close, 14)
        rsi2_c  = sf(rsi2.iloc[-1])
        rsi14_c = sf(rsi14.iloc[-1])

        dif, dea = macd_line(close)
        if dif is not None and len(dif) > 1:
            dif_c = sf(dif.iloc[-1])
            dea_c = sf(dea.iloc[-1])
            dif_p = sf(dif.iloc[-2])
            macd_cross = (dif_p < dea_c) and (dif_c >= dea_c)
            macd_above = dif_c > 0 and dea_c > 0
        else:
            macd_cross = False
            macd_above = False

        # ========== 模式A：回踩买点 ==========
        trend_a = 0
        if ma5_c > ma10_c > ma20_c:
            trend_a = 15
        elif ma5_c > ma20_c:
            trend_a = 8
        if ma5_c > sf(ma5.iloc[-5]):
            trend_a += 8
        if ma20_c > sf(ma20.iloc[-5]):
            trend_a += 7
        trend_a = min(30, trend_a)

        pullback_a = 0
        ma5_dev = (c - ma5_c) / ma5_c * 100
        ma10_dev = (c - ma10_c) / ma10_c * 100

        if -3 <= ma5_dev <= 0:
            pullback_a += 10
        elif -5 <= ma5_dev < -3:
            pullback_a += 8
        elif -8 <= ma5_dev < -5:
            pullback_a += 12
        elif -15 <= ma5_dev < -8:
            pullback_a += 15
        elif ma5_dev > 5:
            pullback_a -= 5

        if -3 <= ma10_dev <= 0:
            pullback_a += 5
        elif -5 <= ma10_dev < -3:
            pullback_a += 8
        pullback_a = min(25, max(0, pullback_a))

        volume_a = 0
        if 0.3 <= vol_ratio < 0.6:
            volume_a = 20
        elif 0.6 <= vol_ratio < 0.8:
            volume_a = 15
        elif vol_ratio < 0.3:
            volume_a = 18
        elif 0.8 <= vol_ratio <= 1.2:
            volume_a = 8

        rsi_a = 0
        if rsi2_c < 5 and rsi14_c < 35:
            rsi_a = 15
        elif rsi2_c < 10 and rsi14_c < 40:
            rsi_a = 12
        elif rsi2_c < 20 and rsi14_c < 45:
            rsi_a = 8
        elif rsi14_c > 70:
            rsi_a = -5
        rsi_a = max(0, rsi_a)

        macd_a = 0
        if macd_cross:
            macd_a += 7
        if macd_above:
            macd_a += 3
        macd_a = min(10, macd_a)

        score_a = trend_a + pullback_a + volume_a + rsi_a + macd_a

        # ========== 模式B：高位突破跟踪 ==========
        trend_b = 0
        if ma5_c > ma10_c > ma20_c > ma60_c:
            trend_b = 30  # 完美多头
        elif ma5_c > ma20_c > ma60_c:
            trend_b = 22
        elif ma5_c > ma20_c:
            trend_b = 15

        # 突破信号：收盘价创20日新高
        high_20d = sf(high.iloc[-21:-1].max()) if len(high) > 20 else c
        breakout_20 = c >= high_20d * 0.99

        # 放量确认
        volume_b = 0
        if vol_ratio > 1.5:
            volume_b = 20
        elif vol_ratio > 1.2:
            volume_b = 15
        elif vol_ratio > 1.0:
            volume_b = 10

        # 涨幅确认（今日涨幅>3%）
        pct_chg = (c - c1) / c1 * 100 if c1 > 0 else 0
        momentum_b = 0
        if pct_chg > 5:
            momentum_b = 20
        elif pct_chg > 3:
            momentum_b = 15
        elif pct_chg > 1:
            momentum_b = 10

        score_b = trend_b + volume_b + momentum_b

        # ========== 选择更优模式 ==========
        if score_b > score_a and breakout_20:
            mode = 'BREAKOUT'
            raw = score_b
        else:
            mode = 'PULLBACK'
            raw = score_a

        # ========== 板块加成 ==========
        code = df['ts_code'].iloc[0] if 'ts_code' in df.columns else os.path.basename(csv_path).replace('.csv', '')
        sector = HOT_STOCKS.get(code, DEFAULT_SECTOR)
        bonus = SECTOR_BONUS.get(sector, 1.0)
        total = min(100, raw * bonus)

        # ========== 信号判定 ==========
        if total >= 75:
            signal, conf = 'BUY', 'HIGH'
        elif total >= 55:
            signal, conf = 'WATCH', 'MEDIUM'
        else:
            signal, conf = 'AVOID', 'LOW'

        upside = max(0, (total - 55) * 0.6) if total > 55 else 0

        return {
            'code':       code,
            'close':      round(c, 2),
            'ma5':        round(ma5_c, 2),
            'ma20':       round(ma20_c, 2),
            'ma5_dev':    round(ma5_dev, 1),
            'vol_ratio':  round(vol_ratio, 2),
            'rsi2':       round(rsi2_c, 1),
            'rsi14':      round(rsi14_c, 1),
            'trend':      round(trend_a if mode == 'PULLBACK' else trend_b, 1),
            'pullback':   round(pullback_a, 1),
            'volume':     round(volume_a if mode == 'PULLBACK' else volume_b, 1),
            'rsi':        round(rsi_a, 1),
            'macd':       round(macd_a, 1),
            'momentum':   round(momentum_b, 1) if mode == 'BREAKOUT' else 0,
            'mode':       mode,
            'sector':     sector,
            'bonus':      bonus,
            'total':      round(total, 1),
            'signal':     signal,
            'confidence': conf,
            'upside_pct': round(upside, 1),
        }

    except Exception as e:
        return None

def scan_all_v46():
    print('=' * 70)
    print('V4.6 全市场上涨空间扫描（双模式）| 5567只A股 | cache_daily')
    print('模式A：回踩买点（缩量回踩MA5/MA10）')
    print('模式B：高位突破跟踪（放量突破20日新高）')
    print('=' * 70)

    csv_files = glob.glob(os.path.join(CACHE_DIR, '*.csv'))
    print(f'\n📂 缓存文件：{len(csv_files)}只')

    results = []
    n_threads = 8

    start = time.time()
    print(f'\n🔍 扫描中（{n_threads}线程）...\n')

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = {pool.submit(score_one_stock_v46, f): f for f in csv_files}
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
            done += 1
            if done % 500 == 0:
                print(f'  已完成 {done}/{len(csv_files)} ({done*100//len(csv_files)}%)')

    elapsed = time.time() - start
    print(f'\n⏱️ 耗时：{elapsed:.1f}秒')

    results.sort(key=lambda x: x['total'], reverse=True)

    # 统计
    buys    = [r for r in results if r['signal'] == 'BUY']
    watchs  = [r for r in results if r['signal'] == 'WATCH']
    breakout = [r for r in results if r['mode'] == 'BREAKOUT']

    print(f'\n📊 信号统计：')
    print(f'  BUY      = {len(buys)}只')
    print(f'  WATCH    = {len(watchs)}只')
    print(f'  BREAKOUT = {len(breakout)}只')

    # TOP40
    print('\n' + '=' * 110)
    print(f'{"排名":^4} {"代码":<12} {"收盘":>7} {"MA5偏离":>7} {"量比":>5} {"RSI2":>5} {"模式":^9} {"综合":>6} {"信号":^6} {"预估上涨"}')
    print('-' * 110)
    for i, r in enumerate(results[:40]):
        sig = '✅' if r['signal'] == 'BUY' else ('🔍' if r['signal'] == 'WATCH' else '❌')
        mode_icon = '🚀突破' if r['mode'] == 'BREAKOUT' else '📍回踩'
        print(f'{i+1:^4} {r["code"]:<12} {r["close"]:>7.2f} {r["ma5_dev"]:>+6.1f}% {r["vol_ratio"]:>5.2f} '
              f'{r["rsi2"]:>5.1f} {mode_icon:^9} {r["total"]:>6.1f} {sig:<6} {r["upside_pct"]:>5.0f}%')

    # BREAKOUT详情
    if breakout:
        print('\n' + '=' * 110)
        print(f'🚀 BREAKOUT 模式详情（{len(breakout)}只）— 放量突破跟踪')
        print('=' * 110)
        for r in sorted(breakout, key=lambda x: x['total'], reverse=True)[:20]:
            print(f'{r["code"]} | 收盘{r["close"]:.2f} | MA5偏离{r["ma5_dev"]:+.1f}% | '
                  f'量比{r["vol_ratio"]:.2f} | RSI2={r["rsi2"]:.1f} | '
                  f'综合{r["total"]:.1f}分 | 模式={r["mode"]}')

    # 保存
    out_file = os.path.join(OUTPUT_DIR, 'v46_full_scan_20260619.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': '2026-06-19',
            'total_stocks': len(results),
            'buy_count': len(buys),
            'watch_count': len(watchs),
            'breakout_count': len(breakout),
            'buy_signals': buys,
            'breakout_signals': breakout[:50],
            'watch_signals': watchs[:100],
            'all_results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')

    return results

if __name__ == '__main__':
    results = scan_all_v46()
