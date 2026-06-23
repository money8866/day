# -*- coding: utf-8 -*-
"""
V4.7 合并版 - 全市场扫描 + 高胜率低回撤精选
整合 v46_full_scan 的扫描逻辑 和 v47_best_picks 的过滤逻辑

用法：
  python v47_best_picks.py                     # 默认今天 (2026-06-22)
  python v47_best_picks.py 20260619            # 指定日期
"""

import sys, os, glob, math, time, json
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict

BASE_DIR   = r'D:\mystock'
CACHE_DIR  = os.path.join(BASE_DIR, 'cache_daily')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')

SECTOR_BONUS = {'S': 1.20, 'A': 1.10, 'B': 1.00, 'C': 0.85, 'D': 0.70}
DEFAULT_SECTOR = 'B'

HOT_STOCKS = {
    '688525.SH': 'S', '300438.SZ': 'S', '688498.SH': 'A', '301308.SZ': 'B',
}

# ===================== 工具函数 =====================

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

# ===================== 股票名称解析 =====================

_STOCK_NAME_CACHE = None

def load_stock_names():
    """加载股票名称映射表（优先从tushare获取，失败则用CSV目录扫描）"""
    global _STOCK_NAME_CACHE
    if _STOCK_NAME_CACHE is not None:
        return _STOCK_NAME_CACHE

    name_map = {}

    # 方式1：尝试从 tushare_quant 导入已有字典
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, 'solo'))
        from tushare_quant import STOCK_DICT
        if isinstance(STOCK_DICT, dict) and len(STOCK_DICT) > 100:
            _STOCK_NAME_CACHE = STOCK_DICT
            print(f"[名称] 从 tushare_quant 加载 {len(STOCK_DICT)} 条股票名称")
            return _STOCK_NAME_CACHE
    except Exception:
        pass

    # 方式2：遍历cache_daily，用ts_code前缀匹配
    try:
        csv_files = glob.glob(os.path.join(CACHE_DIR, '*.csv'))
        # 取每个文件的第一行数据获取名称
        # cache_daily文件名为 ts_code.csv，取前500个文件加速
        for f in csv_files[:500]:
            base = os.path.basename(f).replace('.csv', '')
            if '.' in base:
                name_map[base] = base  # 先用代码占位
        _STOCK_NAME_CACHE = name_map
        print(f"[名称] 从缓存目录加载 {len(name_map)} 个代码（无名称信息）")
    except Exception:
        pass

    _STOCK_NAME_CACHE = name_map
    return _STOCK_NAME_CACHE

def get_stock_name(ts_code):
    """获取股票名称"""
    names = load_stock_names()
    return names.get(ts_code, ts_code)

# ===================== v46 评分 =====================

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
            trend_b = 30
        elif ma5_c > ma20_c > ma60_c:
            trend_b = 22
        elif ma5_c > ma20_c:
            trend_b = 15

        high_20d = sf(high.iloc[-21:-1].max()) if len(high) > 20 else c
        breakout_20 = c >= high_20d * 0.99

        volume_b = 0
        if vol_ratio > 1.5:
            volume_b = 20
        elif vol_ratio > 1.2:
            volume_b = 15
        elif vol_ratio > 1.0:
            volume_b = 10

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

        if total >= 75:
            signal, conf = 'BUY', 'HIGH'
        elif total >= 55:
            signal, conf = 'WATCH', 'MEDIUM'
        else:
            signal, conf = 'AVOID', 'LOW'

        upside = max(0, (total - 55) * 0.6) if total > 55 else 0

        # 额外保存回撤计算所需的历史数据
        high_series = high.astype(float)
        cummax = close.expanding().max()
        dd = (close - cummax) / cummax
        max_dd_20d = float(dd.iloc[-20:].min())
        ret_5d = (c - c5d) / c5d * 100

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
            # 供v47过滤用
            '_max_dd_20d': round(max_dd_20d * 100, 1) if not math.isnan(max_dd_20d) else 0,
            '_pct_5d':    round(ret_5d, 1) if not math.isnan(ret_5d) else 0,
        }

    except Exception as e:
        return None


def scan_all_v46(date_str):
    """V4.6 全市场扫描"""
    print('=' * 70)
    print(f'V4.6 全市场扫描（{date_str}）| 双模式')
    print('模式A：回踩买点 | 模式B：高位突破跟踪')
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

    buys    = [r for r in results if r['signal'] == 'BUY']
    watchs  = [r for r in results if r['signal'] == 'WATCH']
    breakout = [r for r in results if r['mode'] == 'BREAKOUT']

    print(f'\n📊 信号统计：')
    print(f'  BUY/BREAKOUT = {len(buys)}/{len(breakout)}只')

    # 保存全量扫描结果（供v47过滤用）
    scan_file = os.path.join(OUTPUT_DIR, f'v46_full_scan_{date_str}.json')
    with open(scan_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': date_str,
            'total_stocks': len(results),
            'buy_count': len(buys),
            'watch_count': len(watchs),
            'breakout_count': len(breakout),
            'all_results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存全量扫描：{scan_file}')

    return results


# ===================== v47 过滤 =====================

def screen_best(results, date_str, top_n=15):
    """V4.7 高胜率低回撤精选（纯技术面）"""
    print()
    print('=' * 80)
    print(f'V4.7 高胜率低回撤精选（{date_str}）')
    print('=' * 80)

    # 仅从BREAKOUT模式中筛选
    breakout = [r for r in results if r.get('mode') == 'BREAKOUT']
    print(f'\n📊 突破股总数：{len(breakout)}只')

    # 先对breakout按code去重，保留total最高的
    breakout_dedup = {}
    for r in breakout:
        code = r['code']
        if code not in breakout_dedup or r['total'] > breakout_dedup[code]['total']:
            breakout_dedup[code] = r
    breakout = list(breakout_dedup.values())

    candidates = []
    for r in breakout:
        # 基础过滤
        if r.get('rsi14', 0) > 75:
            continue
        if r.get('vol_ratio', 0) > 3:
            continue

        # 风险指标（从v46评分中已计算）
        pct_5d = r.get('_pct_5d', 0)
        max_dd = r.get('_max_dd_20d', 0)

        if pct_5d > 20:
            continue
        if max_dd > -35:  # 回撤不足-35%的过滤掉
            continue

        # 综合质量评分
        # max_dd是百分比，如-40.0表示回撤40%
        # 回撤越大(越负)分数越高：超过35%的部分每1%得1分
        dd_score = max(0, abs(max_dd) - 35)

        vol_score = max(0, 15 - abs(r.get('momentum', 0) - 50) * 0.3)
        rsi_score = max(0, 15 - (r.get('rsi14', 50) - 50) * 0.5)

        quality = r['total'] + dd_score + vol_score + rsi_score

        name = get_stock_name(r['code'])

        candidates.append({
            'code':         r['code'],
            'name':         name,
            'close':        r['close'],
            'ma5_dev':      r['ma5_dev'],
            'rsi2':         r['rsi2'],
            'rsi14':        r['rsi14'],
            'vol_ratio':    r['vol_ratio'],
            'base_score':   r['total'],
            'max_dd':       max_dd,
            'pct_5d':       pct_5d,
            'upside_pct':   r['upside_pct'],
            'quality_score': round(quality, 1),
        })

    candidates.sort(key=lambda x: x['quality_score'], reverse=True)

    print(f'✅ 过滤后：{len(candidates)}只')
    print()

    # ========== 输出表格 ==========
    print('=' * 120)
    print(f'{"排名":^4} {"代码":<12} {"名称":<10} {"收盘":>7} {"RSI14":>5} {"量比":>5} {"5日涨":>6} {"回撤":>6} {"基础分":>6} {"质量分":>7}')
    print('-' * 120)
    for i, c in enumerate(candidates[:top_n]):
        sig = '✅' if c['quality_score'] > 70 else ('🔍' if c['quality_score'] > 60 else '⚠️')
        print(f'{i+1:^4} {c["code"]:<12} {c["name"]:<10} {c["close"]:>7.2f} {c["rsi14"]:>5.1f} '
              f'{c["vol_ratio"]:>5.2f} {c["pct_5d"]:>+5.1f}% {c["max_dd"]:>+5.1f}% '
              f'{c["base_score"]:>6.1f} {c["quality_score"]:>7.1f} {sig}')

    # ========== TOP5 交易计划 ==========
    print()
    print('=' * 100)
    print('🏅 TOP5 交易计划')
    print('=' * 100)
    for i, c in enumerate(candidates[:5]):
        entry = c['close'] * 0.98   # 回调2%入场
        stop  = c['close'] * 0.93   # 止损-7%
        target = c['close'] * 1.15  # 止盈+15%

        print(f'\n{i+1}. {c["name"]} ({c["code"]}) | 收盘 {c["close"]:.2f} | 质量分 {c["quality_score"]:.1f}')
        print(f'   技术：RSI14={c["rsi14"]:.1f}  量比={c["vol_ratio"]:.2f}  '
              f'5日{c["pct_5d"]:+.1f}%  回撤{c["max_dd"]:+.1f}%')
        print(f'   交易：入场 {entry:.2f}(-2%) | 止损 {stop:.2f}(-7%) | 止盈 {target:.2f}(+15%)')
        print(f'   盈亏比：{(target-entry)/(entry-stop):.1f}:1 | 建议仓位：{min(10, max(3, c["quality_score"]-55))}%')

    # ========== 保存 ==========
    out_file = os.path.join(OUTPUT_DIR, f'v47_best_picks_{date_str}.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': date_str,
            'breakout_total': len(breakout),
            'filtered': len(candidates),
            'top15': candidates[:top_n],
            'all_candidates': candidates,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')

    return candidates


# ===================== 主入口 =====================

def main():
    # 日期参数
    if len(sys.argv) >= 2:
        date_str = sys.argv[1]
    else:
        from datetime import datetime
        date_str = datetime.now().strftime('%Y%m%d')

    print(f'📅 运行日期：{date_str}')
    print()

    # 步骤1：全市场扫描
    all_results = scan_all_v46(date_str)

    # 步骤2：高胜率低回撤精选
    best = screen_best(all_results, date_str)

    print()
    print('=' * 80)
    print(f'✅ 完成！共扫描 {len(all_results)} 只，精选 {len(best)} 只')
    print('=' * 80)


if __name__ == '__main__':
    main()
