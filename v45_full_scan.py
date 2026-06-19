# -*- coding: utf-8 -*-
"""
V4.5 全市场上涨空间扫描（使用本地缓存）
扫描5567只A股，基于cache_daily目录下的一年K线
"""

import sys, os, glob, math, time, json
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR  = r'D:\mystock'
CACHE_DIR = os.path.join(BASE_DIR, 'cache_daily')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')

# ========== 板块评级（后续可由block.py注入）==========
SECTOR_BONUS = {'S': 1.20, 'A': 1.10, 'B': 1.00, 'C': 0.85, 'D': 0.70}
DEFAULT_SECTOR = 'B'

# 已知主线板块股票（今日验证）
HOT_STOCKS = {
    '688525.SH': 'S',  # 佰维存储 — 先进封装主线
    '300438.SZ': 'S',  # 鹏辉能源 — 固态电池主线
    '688498.SH': 'A',  # 源杰科技 — 光通信
    '301308.SZ': 'B',  # 江波龙
}

# ========== 工具函数 ==========
def sf(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except:
        return default

def calc_ma(s, n):
    return s.rolling(n, min_periods=1).mean()

def calc_rsi(close, period=2):
    """RSI计算（Wilder平滑）"""
    if len(close) < period + 1:
        return pd.Series([50] * len(close), index=close.index)
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta.where(delta < 0, 0))
    # Wilder平滑
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.inf)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def macd_line(close, fast=12, slow=26, signal=9):
    if len(close) < slow + signal:
        return None, None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea

# ========== 单股评分核心 ==========
def score_one_stock(csv_path):
    """对单只股票进行V4.5评分（满分100）"""
    try:
        df = pd.read_csv(csv_path)
        if df.empty or len(df) < 60:
            return None

        # 数据准备
        close = df['close'].astype(float)
        vol   = df['vol'].astype(float) if 'vol' in df.columns else df.get('volume', df['amount'])
        high  = df['high'].astype(float) if 'high' in df.columns else close
        low   = df['low'].astype(float)  if 'low' in df.columns else close

        # 均线
        ma5   = calc_ma(close, 5)
        ma10  = calc_ma(close, 10)
        ma20  = calc_ma(close, 20)
        ma60  = calc_ma(close, 60)

        # 最新值
        c   = sf(close.iloc[-1])
        c1  = sf(close.iloc[-2])
        ma5_c  = sf(ma5.iloc[-1])
        ma10_c = sf(ma10.iloc[-1])
        ma20_c = sf(ma20.iloc[-1])
        ma60_c = sf(ma60.iloc[-1])

        # 量能
        vol_ma20 = vol.rolling(20, min_periods=1).mean()
        vol_ratio = sf(vol.iloc[-1]) / sf(vol_ma20.iloc[-1]) if sf(vol_ma20.iloc[-1]) > 0 else 1.0

        # RSI
        rsi2  = calc_rsi(close, 2)
        rsi14 = calc_rsi(close, 14)
        rsi2_c  = sf(rsi2.iloc[-1])
        rsi14_c = sf(rsi14.iloc[-1])

        # MACD
        dif, dea = macd_line(close)
        if dif is not None and len(dif) > 1:
            dif_c  = sf(dif.iloc[-1])
            dea_c  = sf(dea.iloc[-1])
            dif_p  = sf(dif.iloc[-2])
            macd_cross = (dif_p < dea_c) and (dif_c >= dea_c)  # 黄金交叉
            macd_above = dif_c > 0 and dea_c > 0
        else:
            macd_cross = False
            macd_above = False

        # ========== 因子1：趋势强度（满分30）==========
        trend_score = 0
        if ma5_c > ma10_c > ma20_c:
            trend_score += 15  # 多头排列
        elif ma5_c > ma20_c:
            trend_score += 8
        elif ma10_c > ma20_c:
            trend_score += 5
        # 均线方向
        if ma5_c > sf(ma5.iloc[-5]):
            trend_score += 8
        if ma20_c > sf(ma20.iloc[-5]):
            trend_score += 7
        trend_score = min(30, trend_score)

        # ========== 因子2：回档评分（满分25）==========
        pullback_score = 0
        ma5_dev = (c - ma5_c) / ma5_c * 100
        ma10_dev = (c - ma10_c) / ma10_c * 100

        if -3 <= ma5_dev <= 0:
            pullback_score += 10
        elif -5 <= ma5_dev < -3:
            pullback_score += 8
        elif -8 <= ma5_dev < -5:
            pullback_score += 12
        elif -15 <= ma5_dev < -8:
            pullback_score += 15
        elif ma5_dev > 5:
            pullback_score -= 5

        if -3 <= ma10_dev <= 0:
            pullback_score += 5
        elif -5 <= ma10_dev < -3:
            pullback_score += 8
        elif ma10_dev > 5:
            pullback_score -= 3
        pullback_score = min(25, max(0, pullback_score))

        # ========== 因子3：量能验证（满分20）==========
        volume_score = 0
        if 0.3 <= vol_ratio < 0.6:
            volume_score = 20
        elif 0.6 <= vol_ratio < 0.8:
            volume_score = 15
        elif vol_ratio < 0.3:
            volume_score = 18
        elif 0.8 <= vol_ratio <= 1.2:
            volume_score = 8

        # ========== 因子4：RSI确认（满分15）==========
        rsi_score = 0
        if rsi2_c < 5 and rsi14_c < 35:
            rsi_score = 15
        elif rsi2_c < 10 and rsi14_c < 40:
            rsi_score = 12
        elif rsi2_c < 20 and rsi14_c < 45:
            rsi_score = 8
        elif rsi14_c > 70:
            rsi_score = -5
        rsi_score = max(0, rsi_score)

        # ========== 因子5：MACD底背离（满分10）==========
        macd_score = 0
        if macd_cross:
            macd_score += 7
        if macd_above:
            macd_score += 3
        macd_score = min(10, macd_score)

        # ========== 板块加成 ==========
        code = df['ts_code'].iloc[0] if 'ts_code' in df.columns else os.path.basename(csv_path).replace('.csv', '')
        sector = HOT_STOCKS.get(code, DEFAULT_SECTOR)
        bonus = SECTOR_BONUS.get(sector, 1.0)

        # ========== 综合评分 ==========
        raw = trend_score + pullback_score + volume_score + rsi_score + macd_score
        total = min(100, raw * bonus)

        # ========== 信号判定 ==========
        if total >= 75:
            signal, conf = 'BUY', 'HIGH'
        elif total >= 55:
            signal, conf = 'WATCH', 'MEDIUM'
        else:
            signal, conf = 'AVOID', 'LOW'

        # 预估上涨空间
        upside = max(0, (total - 55) * 0.6) if total > 55 else 0

        return {
            'code':          code,
            'close':         round(c, 2),
            'ma5':           round(ma5_c, 2),
            'ma20':          round(ma20_c, 2),
            'ma5_dev':       round(ma5_dev, 1),
            'vol_ratio':     round(vol_ratio, 2),
            'rsi2':          round(rsi2_c, 1),
            'rsi14':         round(rsi14_c, 1),
            'trend':         round(trend_score * bonus, 1),
            'pullback':      round(pullback_score * bonus, 1),
            'volume':        round(volume_score, 1),
            'rsi':           round(rsi_score, 1),
            'macd':          round(macd_score, 1),
            'sector':        sector,
            'bonus':         bonus,
            'total':         round(total, 1),
            'signal':        signal,
            'confidence':    conf,
            'upside_pct':    round(upside, 1),
        }

    except Exception as e:
        return None

# ========== 批量扫描 ==========
def scan_all_stocks():
    """扫描全部5567只股票"""
    print('=' * 70)
    print('V4.5 全市场上涨空间扫描  |  cache_daily目录  |  5567只A股')
    print('=' * 70)

    csv_files = glob.glob(os.path.join(CACHE_DIR, '*.csv'))
    print(f'\n📂 发现缓存文件：{len(csv_files)}只')

    results = []
    n_threads = 8

    start = time.time()
    print(f'\n🔍 开始扫描（{n_threads}线程）...\n')

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = {pool.submit(score_one_stock, f): f for f in csv_files}
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
            done += 1
            if done % 500 == 0:
                print(f'  已完成 {done}/{len(csv_files)} ({done*100//len(csv_files)}%)')

    elapsed = time.time() - start
    print(f'\n⏱️ 扫描耗时：{elapsed:.1f}秒')

    # 排序
    results.sort(key=lambda x: x['total'], reverse=True)

    # 统计
    buys   = [r for r in results if r['signal'] == 'BUY']
    watchs = [r for r in results if r['signal'] == 'WATCH']

    print(f'\n📊 信号统计：')
    print(f'  BUY   = {len(buys)}只')
    print(f'  WATCH = {len(watchs)}只')
    print(f'  AVOID = {len(results) - len(buys) - len(watchs)}只')

    # TOP40
    print('\n' + '=' * 100)
    print(f'{"排名":^4} {"代码":<12} {"收盘":>7} {"MA5偏离":>7} {"量比":>5} {"RSI2":>5} {"综合":>6} {"趋势":>5} {"回档":>5} {"量能":>5} {"RSI":>5} {"MACD":>5} {"信号":<6}')
    print('-' * 100)
    for i, r in enumerate(results[:40]):
        sig = '✅' if r['signal'] == 'BUY' else ('🔍' if r['signal'] == 'WATCH' else '❌')
        print(f'{i+1:^4} {r["code"]:<12} {r["close"]:>7.2f} {r["ma5_dev"]:>+6.1f}% {r["vol_ratio"]:>5.2f} '
              f'{r["rsi2"]:>5.1f} {r["total"]:>6.1f} {r["trend"]:>5.1f} {r["pullback"]:>5.1f} '
              f'{r["volume"]:>5.1f} {r["rsi"]:>5.1f} {r["macd"]:>5.1f} {sig:<4}')

    # BUY详情
    if buys:
        print('\n' + '=' * 100)
        print(f'✅ BUY 信号详情（{len(buys)}只）')
        print('=' * 100)
        for r in buys[:20]:
            print(f'{r["code"]} | 收盘{r["close"]:.2f} | MA5偏离{r["ma5_dev"]:+.1f}% | '
                  f'量比{r["vol_ratio"]:.2f} | RSI2={r["rsi2"]:.1f} | '
                  f'综合{r["total"]:.1f}分 | 预估上涨{r["upside_pct"]:.0f}%')

    # 保存
    out_file = os.path.join(OUTPUT_DIR, 'v45_full_scan_20260619.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': '2026-06-19',
            'total_stocks': len(results),
            'buy_count': len(buys),
            'watch_count': len(watchs),
            'buy_signals': buys,
            'watch_signals': watchs[:100],
            'all_results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')

    return results

if __name__ == '__main__':
    results = scan_all_stocks()
