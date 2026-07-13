# -*- coding: utf-8 -*-
import subprocess, json, os, sys, datetime
import pandas as pd
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()

SKILL_DIR = r'C:\Users\kongx\.qclaw\sskills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'sent3.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== 今日市场情绪分析  %s ===\n" % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))

# 1. 今日快讯（市场情绪类）
print("--- 今日市场快讯 ---")
keywords = ['上涨', '下跌', '涨停', '跌停', '反弹', '暴跌', '恐慌', '资金', '护盘', '主力', '外资', '北向']
news_items = []
for kw in keywords:
    resp = mcp_call('wenda_news_query', bdate='20260713', edate='20260713')
    if resp:
        items = resp.get('data', [])
        if isinstance(items, list) and len(items) > 1:
            for item in items[1:8]:
                if not isinstance(item, list) or len(item) < 4: continue
                title = item[0] if len(item) > 0 else ''
                t_str = item[1] if len(item) > 1 else ''
                src = item[3] if len(item) > 3 else ''
                summary = item[4] if len(item) > 4 else ''
                if title and len(title) > 5 and title not in [n[0] for n in news_items]:
                    news_items.append((t_str, title, summary, src))

# 情绪关键词统计
sentiment_count = {'bull': 0, 'bear': 0, 'neutral': 0}
for t, title, summary, src in news_items:
    bull_kw = ['上涨', '反弹', '涨停', '护盘', '资金流入', '净流入', '超跌反弹', '突破', '多头', '拉升']
    bear_kw = ['下跌', '跌停', '暴跌', '恐慌', '资金流出', '净流出', '杀跌', '破位', '空头', '砸盘']
    s = title + summary
    is_bull = any(k in s for k in bull_kw)
    is_bear = any(k in s for k in bear_kw)
    if is_bull and not is_bear:
        sentiment_count['bull'] += 1
    elif is_bear:
        sentiment_count['bear'] += 1
    else:
        sentiment_count['neutral'] += 1

print("  快讯情绪: bull=%d  bear=%d  neutral=%d" % (
    sentiment_count['bull'], sentiment_count['bear'], sentiment_count['neutral']))
print("  今日重要快讯:")
for t, title, summary, src in news_items[:8]:
    print("    [%s] %s" % (t[:16], title[:70]))
    if summary: print("         %s" % summary[:80])

# 2. 近期日K趋势（Tushare收盘数据）
print("\n--- 指数趋势分析（最后交易日: 2026-07-10）---")
idx_list = [
    ('000001.SH', '上证指数'),
    ('399001.SZ', '深证成指'),
    ('399006.SZ', '创业板指'),
    ('399300.SZ', '沪深300'),
    ('932000.CSI', '中证2000'),
    ('000852.SH', '中证1000'),
]

idx_results = []
for code, name in idx_list:
    try:
        start = (datetime.date.today() - datetime.timedelta(days=60)).strftime('%Y%m%d')
        df = pro.index_daily(ts_code=code, start_date=start)
        if df is None or len(df) < 5:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['pct_chg'] = df['pct_chg'].astype(float)
        
        last5 = df.tail(5)
        ma5 = last5['close'].mean()
        ma10 = df.tail(10)['close'].mean()
        ma20 = df.tail(20)['close'].mean() if len(df) >= 20 else ma10
        
        last = df.iloc[-1]
        pct_1d = last['pct_chg']
        pct_5d = (df.iloc[-1]['close'] - df.iloc[-6]['close']) / df.iloc[-6]['close'] * 100 if len(df) >= 6 else 0
        pct_10d = (df.iloc[-1]['close'] - df.iloc[-11]['close']) / df.iloc[-11]['close'] * 100 if len(df) >= 11 else 0
        
        # 趋势判断
        above_ma5 = last['close'] > ma5
        above_ma20 = last['close'] > ma20
        ma5_above_ma10 = ma5 > ma10
        ma10_above_ma20 = ma10 > ma20
        
        if above_ma5 and above_ma20 and ma5_above_ma10 and ma10_above_ma20:
            trend = 'STRONG_UP'
        elif not above_ma5 and not above_ma20 and not ma5_above_ma10 and not ma10_above_ma20:
            trend = 'STRONG_DOWN'
        elif above_ma5 and above_ma20:
            trend = 'UP'
        elif not above_ma5 and not above_ma20:
            trend = 'DOWN'
        else:
            trend = 'FLAT'
        
        idx_results.append({
            'name': name, 'code': code,
            'close': last['close'], 'pct_1d': pct_1d,
            'pct_5d': pct_5d, 'pct_10d': pct_10d,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
            'trend': trend, 'high_20d': df.tail(20)['high'].max(),
            'low_20d': df.tail(20)['low'].min()
        })
        
        pct1 = '+%.2f' % pct_1d if pct_1d >= 0 else '%.2f' % pct_1d
        pct5 = '+%.1f' % pct_5d if pct_5d >= 0 else '%.1f' % pct_5d
        pct10 = '+%.1f' % pct_10d if pct_10d >= 0 else '%.1f' % pct_10d
        print("  %s: %.2f  1d%s%%  5d%s%%  10d%s%%" % (name, last['close'], pct1, pct5, pct10))
        print("    MA5=%.1f  MA10=%.1f  MA20=%.1f  [%s]" % (ma5, ma10, ma20, trend))
    except Exception as e:
        print("  %s: error - %s" % (name, e))

# 3. 综合情绪评分
print("\n--- 综合情绪评分 ---")
if idx_results:
    score = 50
    parts = []
    
    sh = next((r for r in idx_results if '上证' in r['name']), None)
    csi2k = next((r for r in idx_results if '2000' in r['name']), None)
    cyb = next((r for r in idx_results if '创业' in r['name']), None)
    hs300 = next((r for r in idx_results if '沪深' in r['name']), None)
    
    # 1日涨跌
    if sh and sh['pct_1d'] > 0: score += 3
    if sh and sh['pct_1d'] < 0: score -= 3
    
    # 小盘股（最敏感）
    if csi2k:
        if csi2k['pct_1d'] > 1: score += 8
        if 0 < csi2k['pct_1d'] <= 1: score += 4
        if csi2k['pct_1d'] < 0: score -= 5
        if csi2k['pct_1d'] < -2: score -= 8
        if csi2k['trend'] == 'STRONG_DOWN': score -= 10
        if csi2k['trend'] == 'DOWN': score -= 5
        parts.append('中证2000 1d%+d 趋势[%s]' % csi2k['trend'])
    
    # 创业板
    if cyb:
        if cyb['pct_5d'] < -5: score -= 5
        if cyb['trend'] == 'STRONG_DOWN': score -= 5
        parts.append('创业板5d%+d' % cyb['pct_5d'])
    
    # 沪深300（大票稳定性）
    if hs300:
        if hs300['trend'] == 'STRONG_UP': score += 5
        if hs300['trend'] == 'STRONG_DOWN': score -= 5
        parts.append('沪深300[%s]' % hs300['trend'])
    
    # 快讯情绪
    if sentiment_count['bull'] > sentiment_count['bear']:
        score += 5
    if sentiment_count['bear'] > sentiment_count['bull']:
        score -= 5
    parts.append('快讯bull=%d bear=%d' % (sentiment_count['bull'], sentiment_count['bear']))
    
    # 10日趋势（动量）
    if csi2k and csi2k['pct_10d'] < -5: score -= 5
    if cyb and cyb['pct_10d'] < -10: score -= 5
    
    score = max(0, min(100, score))
    
    if score >= 70: label = '[STRONG BULL]'
    elif score >= 58: label = '[BULLISH]'
    elif score >= 45: label = '[NEUTRAL]'
    elif score >= 32: label = '[BEARISH]'
    else: label = '[STRONG BEAR]'
    
    print("  综合情绪分: %d/100  %s" % (score, label))
    print("  构成: %s" % ' | '.join(parts))
    
    # 关键信号
    print("\n--- 关键信号 ---")
    if csi2k and csi2k['trend'] == 'STRONG_DOWN':
        print("  [WARNING] 中证2000: 均线空头排列，持续下行趋势")
    if cyb and cyb['pct_5d'] < -8:
        print("  [WARNING] 创业板: 5日跌幅超8%，动能极弱")
    if csi2k and csi2k['low_20d'] == csi2k['close']:
        print("  [CRITICAL] 中证2000: 创20日新低！")
    if sentiment_count['bear'] > sentiment_count['bull']:
        print("  [WARNING] 快讯偏空为主")
    if sh and sh['pct_1d'] > 1 and csi2k and csi2k['pct_1d'] < 0:
        print("  [SPLIT] 主板护盘 vs 小盘领跌，分化格局")

print("\nDone")
