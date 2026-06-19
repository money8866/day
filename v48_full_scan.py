# -*- coding: utf-8 -*-
"""
V4.8 全市场上涨空间扫描（双模式 + 动态主题强度 + 大盘过滤）
改进点：
  1. 模式B得分上限提升至100分（原70分）
  2. 加载动态主题强度（从theme_evolution_*.json）
  3. 加大盘环境过滤（SH指数跌破MA60时全池降权）
  4. 修复回踩-趋势矛盾（加"回踩不破趋势"奖励）
"""

import sys, os, glob, math, time, json
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE_DIR   = r'D:\mystock'
CACHE_DIR  = os.path.join(BASE_DIR, 'cache_daily')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')

# ========== 股票名称映射 ==========
def load_stock_name_map():
    """加载ts_code → name映射"""
    try:
        import tushare as ts
        ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
        pro = ts.pro_api()
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        return {row['ts_code']: row['name'] for _, row in df.iterrows()}
    except Exception as e:
        print(f'  ⚠️ 加载股票名称失败：{e}')
        return {}

STOCK_NAME_MAP = load_stock_name_map()  # ts_code → name

# ========== 动态主题强度加载 ==========
def load_theme_strength():
    """从最新的theme_evolution_*.json加载主题强度"""
    pattern = os.path.join(OUTPUT_DIR, 'theme_evolution_*.json')
    files = glob.glob(pattern)
    if not files:
        print('  ⚠️ 未找到theme_evolution文件，使用默认权重')
        return {}
    
    latest_file = max(files, key=os.path.getmtime)
    print(f'  📊 加载主题强度：{os.path.basename(latest_file)}')
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 构建股票名称 → 主题评分映射
    stock_score = {}  # name → score
    
    for theme in data.get('theme_table', []):
        theme_name = theme['theme']
        score = theme.get('score', 50)
        
        for leader in theme.get('leader_stocks', []):
            stock_score[leader] = score
    
    print(f'  ✅ 加载{len(stock_score)}只主题龙头股评分')
    return stock_score

THEME_STOCK_SCORE = load_theme_strength()

def get_theme_bonus(ts_code):
    """根据ts_code获取主题加成系数"""
    if not STOCK_NAME_MAP or not THEME_STOCK_SCORE:
        return 1.0
    
    name = STOCK_NAME_MAP.get(ts_code, '')
    if name in THEME_STOCK_SCORE:
        score = THEME_STOCK_SCORE[name]
        if score >= 80:
            return 1.20
        elif score >= 65:
            return 1.10
        elif score >= 50:
            return 1.00
        elif score >= 35:
            return 0.85
        else:
            return 0.70
    
    return 1.0

# ========== 大盘环境检测 ==========
def check_market_env():
    """检测大盘环境（SH指数是否在MA60上方）"""
    sh_file = os.path.join(CACHE_DIR, '000001.SH.csv')
    if not os.path.exists(sh_file):
        print('  ⚠️ 未找到上证指数缓存，跳过市场环境检测')
        return True, 1.0
    
    try:
        df = pd.read_csv(sh_file)
        close = df['close'].astype(float)
        ma60 = close.rolling(60, min_periods=1).mean()
        
        sh_close = float(close.iloc[-1])
        sh_ma60 = float(ma60.iloc[-1])
        
        if sh_close < sh_ma60:
            print(f'  🔴 大盘环境：跌破MA60（{sh_close:.2f} < {sh_ma60:.2f}），降低权重')
            return False, 0.85
        else:
            print(f'  🟢 大盘环境：MA60上方（{sh_close:.2f} > {sh_ma60:.2f}），正常权重')
            return True, 1.0
    except Exception as e:
        print(f'  ⚠️ 大盘环境检测失败：{e}')
        return True, 1.0

MARKET_OK, MARKET_WEIGHT = check_market_env()

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

# ========== 核心评分函数 ==========
def score_one_stock_v48(csv_path):
    """V4.8双模式评分（改进版）"""
    try:
        df = pd.read_csv(csv_path)
        if df.empty or len(df) < 60:
            return None

        close = df['close'].astype(float)
        vol   = df['vol'].astype(float) if 'vol' in df.columns else df.get('volume', df['amount'])
        high  = df['high'].astype(float) if 'high' in df.columns else close

        ma5   = calc_ma(close, 5)
        ma10  = calc_ma(close, 10)
        ma20  = calc_ma(close, 20)
        ma60  = calc_ma(close, 60)

        c   = sf(close.iloc[-1])
        c1  = sf(close.iloc[-2])
        ma5_c  = sf(ma5.iloc[-1])
        ma10_c = sf(ma10.iloc[-1])
        ma20_c = sf(ma20.iloc[-1])
        ma60_c = sf(ma60.iloc[-1])
        
        ma5_5d  = sf(ma5.iloc[-6])  if len(ma5) > 5 else ma5_c
        ma20_5d = sf(ma20.iloc[-6]) if len(ma20) > 5 else ma20_c

        vol_ma20 = vol.rolling(20, min_periods=1).mean()
        vol_ratio = sf(vol.iloc[-1]) / sf(vol_ma20.iloc[-1]) if sf(vol_ma20.iloc[-1]) > 0 else 1.0

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

        # ========== 模式A：回踩买点（修复矛盾）==========
        trend_a = 0
        if ma5_c > ma10_c > ma20_c:
            trend_a = 15
        elif ma5_c > ma20_c:
            trend_a = 8
        if ma5_c > ma5_5d:
            trend_a += 8
        if ma20_c > ma20_5d:
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
        
        # 🆕 修复矛盾：回踩不破趋势奖励
        if -5 <= ma5_dev <= 0 and ma5_c > ma20_c:
            pullback_a = min(25, pullback_a + 5)

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

        # ========== 模式B：高位突破跟踪（提升上限）==========
        trend_b = 0
        if ma5_c > ma10_c > ma20_c > ma60_c:
            trend_b = 40  # 🆕 提升权重（原30）
        elif ma5_c > ma20_c > ma60_c:
            trend_b = 30  # 🆕 提升权重（原22）
        elif ma5_c > ma20_c:
            trend_b = 20  # 🆕 提升权重（原15）

        # 突破信号：收盘价创20日新高
        high_20d = sf(high.iloc[-21:-1].max()) if len(high) > 20 else c
        breakout_20 = c >= high_20d * 0.99

        # 🆕 突破确认加分
        breakout_score = 0
        if breakout_20:
            breakout_score = 10
        if c >= high_20d:
            breakout_score = 15

        volume_b = 0
        if vol_ratio > 1.5:
            volume_b = 30  # 🆕 提升权重（原20）
        elif vol_ratio > 1.2:
            volume_b = 20  # 🆕 提升权重（原15）
        elif vol_ratio > 1.0:
            volume_b = 10

        pct_chg = (c - c1) / c1 * 100 if c1 > 0 else 0
        momentum_b = 0
        if pct_chg > 5:
            momentum_b = 30  # 🆕 提升权重（原20）
        elif pct_chg > 3:
            momentum_b = 20  # 🆕 提升权重（原15）
        elif pct_chg > 1:
            momentum_b = 10

        score_b = trend_b + volume_b + momentum_b + breakout_score  # 🆕 满分100

        # ========== 选择更优模式 ==========
        if score_b > score_a and breakout_20:
            mode = 'BREAKOUT'
            raw = score_b
        else:
            mode = 'PULLBACK'
            raw = score_a

        # ========== 🆕 动态主题加成 ==========
        code = df['ts_code'].iloc[0] if 'ts_code' in df.columns else os.path.basename(csv_path).replace('.csv', '')
        bonus = get_theme_bonus(code)
        
        # 🆕 大盘环境降权
        total = min(100, raw * bonus * MARKET_WEIGHT)

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
            'name':       STOCK_NAME_MAP.get(code, ''),
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
            'breakout':   round(breakout_score, 1) if mode == 'BREAKOUT' else 0,
            'mode':       mode,
            'bonus':      round(bonus, 2),
            'market_w':   round(MARKET_WEIGHT, 2),
            'total':      round(total, 1),
            'signal':     signal,
            'confidence': conf,
            'upside_pct': round(upside, 1),
        }

    except Exception as e:
        return None

def scan_all_v48():
    print('=' * 70)
    print('V4.8 全市场上涨空间扫描（双模式 + 动态主题 + 大盘过滤）| 5567只A股')
    print('改进：模式B上限100分 | 动态主题强度 | 大盘环境过滤 | 回踩矛盾修复')
    print('=' * 70)

    csv_files = glob.glob(os.path.join(CACHE_DIR, '*.csv'))
    print(f'\n📂 缓存文件：{len(csv_files)}只')

    results = []
    n_threads = 8

    start = time.time()
    print(f'\n🔍 扫描中（{n_threads}线程）...\n')

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = {pool.submit(score_one_stock_v48, f): f for f in csv_files}
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
    pullback = [r for r in results if r['mode'] == 'PULLBACK']

    print(f'\n📊 信号统计：')
    print(f'  BUY       = {len(buys)}只')
    print(f'  WATCH     = {len(watchs)}只')
    print(f'  BREAKOUT  = {len(breakout)}只')
    print(f'  PULLBACK  = {len(pullback)}只')

    # TOP40
    print('\n' + '=' * 120)
    print(f'{"排名":^4} {"代码":<12} {"名称":<10} {"收盘":>7} {"模式":^9} {"综合":>6} {"信号":^6} {"主题加成":>8} {"大盘权重":>8} {"预估上涨"}')
    print('-' * 120)
    for i, r in enumerate(results[:40]):
        sig = '✅' if r['signal'] == 'BUY' else ('🔍' if r['signal'] == 'WATCH' else '❌')
        mode_icon = '🚀突破' if r['mode'] == 'BREAKOUT' else '📉回踩'
        name = r.get('name', '')[:8]
        print(f'{i+1:^4} {r["code"]:<12} {name:<10} {r["close"]:>7.2f} {mode_icon:^9} {r["total"]:>6.1f} {sig:<6} {r["bonus"]:>8.2f} {r["market_w"]:>8.2f} {r["upside_pct"]:>5.0f}%')

    # 保存
    out_file = os.path.join(OUTPUT_DIR, f'v48_full_scan_{datetime.now().strftime("%Y%m%d")}.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': datetime.now().strftime('%Y-%m-%d'),
            'total_stocks': len(results),
            'buy_count': len(buys),
            'watch_count': len(watchs),
            'breakout_count': len(breakout),
            'pullback_count': len(pullback),
            'market_env': 'BULL' if MARKET_OK else 'BEAR',
            'market_weight': MARKET_WEIGHT,
            'buy_signals': buys,
            'breakout_signals': breakout[:50],
            'watch_signals': watchs[:100],
            'all_results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')

    return results

if __name__ == '__main__':
    results = scan_all_v48()
