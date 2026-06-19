# -*- coding: utf-8 -*-
"""
V4.9 全市场上涨空间扫描（收紧版）
修复V4.8的问题：
  1. 模式B评分收紧（满分115→85），避免全体100分封顶
  2. 提高BUY阈值（75→85），过滤弱信号
  3. 突破确认加强（量比>1.5硬约束+0.5%误差）
  4. 动态主题强度正确路径（report_daily/）
  5. 修复回踩-趋势矛盾（回踩不破趋势奖励）
"""

import sys, os, glob, math, time, json
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import load_dotenv
import tushare as ts

BASE_DIR   = r'D:\mystock'
CACHE_DIR  = os.path.join(BASE_DIR, 'cache_daily')
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')
THEME_DIR  = os.path.join(BASE_DIR, 'report_daily')

# ========== Tushare初始化 ==========
load_dotenv(os.path.join(BASE_DIR, 'config', '.env'))
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ========== 股票名称映射 ==========
def load_stock_name_map():
    try:
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        return {row['ts_code']: row['name'] for _, row in df.iterrows()}
    except Exception as e:
        print(f'  ⚠️ 加载股票名称失败：{e}')
        return {}

STOCK_NAME_MAP = load_stock_name_map()
print(f'  📋 股票名称映射：{len(STOCK_NAME_MAP)}只')

# ========== 动态主题强度 ==========
def load_theme_strength():
    """从report_daily/theme_evolution_*.json加载"""
    pattern = os.path.join(THEME_DIR, 'theme_evolution_*.json')
    files = glob.glob(pattern)
    if not files:
        print('  ⚠️ 未找到theme_evolution文件，使用默认权重')
        # 尝试在solo/report_daily/找
        pattern2 = os.path.join(OUTPUT_DIR, 'theme_evolution_*.json')
        files2 = glob.glob(pattern2)
        if files2:
            files = files2
            print(f'  ✅ 在solo/report_daily找到备份')
        else:
            return {}
    
    latest_file = max(files, key=os.path.getmtime)
    print(f'  📊 加载主题强度：{os.path.basename(latest_file)}')
    
    with open(latest_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    stock_score = {}  # stock_name → theme_score
    
    for theme in data.get('theme_table', []):
        score = theme.get('score', 50)
        for leader in theme.get('leader_stocks', []):
            stock_score[leader] = score
    
    print(f'  ✅ 加载{len(stock_score)}只主题龙头股评分')
    return stock_score

THEME_STOCK_SCORE = load_theme_strength()

def get_theme_bonus(stock_name):
    """根据股票名称获取主题加成"""
    if not stock_name or not THEME_STOCK_SCORE:
        return 1.0
    
    score = THEME_STOCK_SCORE.get(stock_name, 0)
    if score == 0:
        return 1.0
    
    if score >= 80:
        return 1.20   # S级主线 +20%
    elif score >= 65:
        return 1.10   # A级强势 +10%
    elif score >= 50:
        return 1.00   # B级中性
    elif score >= 35:
        return 0.85   # C级弱势 -15%
    else:
        return 0.70   # D级退潮 -30%

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

# ========== 核心评分 ==========
def score_one_stock_v49(csv_path):
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

        # ========== 模式A：回踩买点 ==========
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
        
        # 回踩不破趋势奖励
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

        # ========== 模式B：高位突破跟踪（收紧）==========
        trend_b = 0
        if ma5_c > ma10_c > ma20_c > ma60_c:
            trend_b = 30  # 完美多头
        elif ma5_c > ma20_c > ma60_c:
            trend_b = 22  # 中长期多头
        elif ma5_c > ma20_c:
            trend_b = 15  # 短期多头

        # 突破确认（收紧版）
        high_20d = sf(high.iloc[-21:-1].max()) if len(high) > 20 else c
        # 🆕 收紧突破误差：0.5%（原0.99→0.995）
        breakout_20 = c >= high_20d * 0.995
        
        # 🆕 突破量比硬约束
        valid_breakout = breakout_20 and vol_ratio > 1.3
        
        # 🆕 突破额外加分（只在有效突破时加）
        breakout_score = 0
        if valid_breakout:
            if vol_ratio > 2.0:
                breakout_score = 12  # 放量2倍突破
            elif vol_ratio > 1.5:
                breakout_score = 10  # 放量1.5倍突破
            elif vol_ratio > 1.3:
                breakout_score = 7   # 温和放量突破
        elif breakout_20:
            breakout_score = 3  # 突破但量不够，微加

        volume_b = 0
        if vol_ratio > 2.0:
            volume_b = 25   # 巨量
        elif vol_ratio > 1.5:
            volume_b = 20   # 放量
        elif vol_ratio > 1.2:
            volume_b = 15   # 温和放量
        elif vol_ratio > 1.0:
            volume_b = 8    # 微量
        else:
            volume_b = 0    # 缩量不配做突破

        pct_chg = (c - c1) / c1 * 100 if c1 > 0 else 0
        momentum_b = 0
        if pct_chg > 7:
            momentum_b = 20  # 大阳
        elif pct_chg > 5:
            momentum_b = 18  # 中阳
        elif pct_chg > 3:
            momentum_b = 12  # 小阳
        elif pct_chg > 1:
            momentum_b = 6   # 微涨

        # 🆕 模式B满分85（原115），防止全部封顶100
        score_b = trend_b + volume_b + momentum_b + breakout_score
        score_b = min(85, score_b)

        # ========== 选择模式 ==========
        # 🆕 只有有效突破才选模式B
        if score_b > score_a and valid_breakout:
            mode = 'BREAKOUT'
            raw = score_b
        else:
            mode = 'PULLBACK'
            raw = score_a

        # ========== 主题加成 ==========
        code = df['ts_code'].iloc[0] if 'ts_code' in df.columns else os.path.basename(csv_path).replace('.csv', '')
        
        # 从stock_basic获取名称（如果有缓存）
        stock_name = ''
        try:
            import tushare as ts
            ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
            pro = ts.pro_api(use_pool=True)
            info = pro.stock_basic(ts_code=code, fields='name')
            if not info.empty:
                stock_name = info.iloc[0]['name']
        except:
            # 静默失败，不加名称
            pass
        
        bonus = get_theme_bonus(stock_name)
        total = min(100, raw * bonus)

        # ========== 信号判定（收紧阈值）==========
        # 🆕 BUY: ≥85（原75），WATCH: ≥55
        if total >= 85:
            signal, conf = 'BUY', 'HIGH'
        elif total >= 55:
            signal, conf = 'WATCH', 'MEDIUM'
        else:
            signal, conf = 'AVOID', 'LOW'

        upside = max(0, (total - 55) * 0.6) if total > 55 else 0

        return {
            'code':       code,
            'name':       stock_name,
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
            'total':      round(total, 1),
            'signal':     signal,
            'confidence': conf,
            'upside_pct': round(upside, 1),
        }

    except Exception as e:
        return None

def scan_all_v49():
    print('=' * 70)
    print('V4.9 全市场上涨空间扫描（收紧版）| 5567只A股')
    print('改动：模式B满分85 | 阈值BUY≥85 | 强约束(量比>1.3+突破≤0.5%) | 主题路径修复')
    print('=' * 70)

    csv_files = glob.glob(os.path.join(CACHE_DIR, '*.csv'))
    print(f'\n📂 缓存文件：{len(csv_files)}只')

    results = []
    n_threads = 8

    start = time.time()
    print(f'\n🔍 扫描中（{n_threads}线程）...\n')

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = {pool.submit(score_one_stock_v49, f): f for f in csv_files}
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
    pullback = [r for r in results if r['mode'] == 'PULLBACK']

    print(f'\n📊 信号统计：')
    print(f'  BUY       = {len(buys)}只')
    print(f'  WATCH     = {len(watchs)}只')
    print(f'  BREAKOUT  = {len(breakout)}只')
    print(f'  PULLBACK  = {len(pullback)}只')
    if buys:
        print(f'  📌 BUY评分范围：{min(b["total"] for b in buys):.1f}~{max(b["total"] for b in buys):.1f}')
    if breakout:
        print(f'  📌 BREAKOUT评分范围：{min(b["total"] for b in breakout):.1f}~{max(b["total"] for b in breakout):.1f}')

    # TOP40
    print('\n' + '=' * 120)
    print(f'{"排名":^4} {"代码":<12} {"名称":<10} {"收盘":>7} {"模式":^9} {"综合":>6} {"信号":^6} {"量比":>5} {"突破分":>6} {"主题加成":>8} {"预估"}')
    print('-' * 120)
    for i, r in enumerate(results[:40]):
        sig = '✅' if r['signal'] == 'BUY' else ('🔍' if r['signal'] == 'WATCH' else '❌')
        mode_icon = '🚀突破' if r['mode'] == 'BREAKOUT' else '📉回踩'
        name = r.get('name', '')[:8]
        print(f'{i+1:^4} {r["code"]:<12} {name:<10} {r["close"]:>7.2f} {mode_icon:^9} {r["total"]:>6.1f} {sig:<6} {r["vol_ratio"]:>5.2f} {r["breakout"]:>6.1f} {r["bonus"]:>8.2f} {r["upside_pct"]:>4.0f}%')

    # 保存
    out_file = os.path.join(OUTPUT_DIR, f'v49_full_scan_{datetime.now().strftime("%Y%m%d")}.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': datetime.now().strftime('%Y-%m-%d'),
            'total_stocks': len(results),
            'buy_count': len(buys),
            'watch_count': len(watchs),
            'breakout_count': len(breakout),
            'pullback_count': len(pullback),
            'buy_signals': buys,
            'breakout_signals': breakout[:50],
            'watch_signals': watchs[:100],
            'all_results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存：{out_file}')

    return results

if __name__ == '__main__':
    results = scan_all_v49()
