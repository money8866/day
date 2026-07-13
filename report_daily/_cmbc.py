# -*- coding: utf-8 -*-
import subprocess, json, os, sys, datetime
import pandas as pd
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
CODE = '600036.SH'
NAME = '招商银行'
today = '20260713'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'cmbc.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

now = datetime.datetime.now()
print("=== %s 今日走势分析  %s ===\n" % (NAME, now.strftime('%Y-%m-%d %H:%M')))

# ── 1. 实时行情 ──
print("--- 实时行情 ---")
# tdx_quotes: code, setcode
resp_quote = mcp('tdx_quotes', code='600036', setcode='1')
if resp_quote:
    data = resp_quote.get('data', [])
    if isinstance(data, list) and len(data) > 0:
        item = data[0]
        if isinstance(item, list):
            # 格式: [name, close, pct_chg, high, low, open, vol, amount, ...]
            name = item[0] if len(item) > 0 else NAME
            close = float(item[1]) if item[1] else 0
            pct = float(item[2]) if item[2] else 0
            high = float(item[3]) if len(item) > 3 and item[3] else 0
            low = float(item[4]) if len(item) > 4 and item[4] else 0
            open_p = float(item[5]) if len(item) > 5 and item[5] else 0
            vol = item[6] if len(item) > 6 else ''
            amount = item[7] if len(item) > 7 else ''
            print("  实时: close=%.2f  pct=%+.2f%%  open=%.2f  H=%.2f  L=%.2f" % (
                close, pct, open_p, high, low))
            if vol: print("  vol=%s  amount=%s" % (vol, amount))
        elif isinstance(item, dict):
            print("  dict keys: %s" % list(item.keys()))
    else:
        print("  格式: %s" % str(data)[:200])

# ── 2. K线数据 ──
print("\n--- K线分析 ---")
# 拉近60日K线
resp_kl = mcp('tdx_kline', code='600036', setcode='1', period='4', wantNum='60', tqFlag='11')
rows = []
if resp_kl:
    items = resp_kl.get('Rows', [])
    for row in items:
        d = row.get('Data', '')
        if len(d) < 29: continue
        try:
            date = d[:8]
            open_p = float(d[9:16])
            close_p = float(d[16:23])
            high_p = float(d[23:30])
            low_p = float(d[30:37])
            vol = int(d[37:50]) if d[37:50].strip() else 0
            if close_p > 0 and 10 < close_p < 1000:
                rows.append({'date': date, 'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p, 'vol': vol})
        except:
            continue

if rows:
    rows.sort(key=lambda x: x['date'])
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    print("  TDX数据: %d条  %s~%s" % (len(df), df['date'].iloc[0].strftime('%m/%d'), df['date'].iloc[-1].strftime('%m/%d')))
    print("  最新: %s  收=%.2f  高=%.2f  低=%.2f" % (
        df.iloc[-1]['date'].strftime('%m/%d'), df.iloc[-1]['close'], df.iloc[-1]['high'], df.iloc[-1]['low']))
else:
    print("  TDX无数据，改用Tushare")
    df = None

if df is None or len(df) < 20:
    try:
        start = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%Y%m%d')
        df_ts = pro.daily(ts_code=CODE, start_date=start)
        if df_ts is not None and len(df_ts) > 0:
            df_ts = df_ts.sort_values('trade_date').reset_index(drop=True)
            df_ts['date'] = pd.to_datetime(df_ts['trade_date'])
            df_ts['close'] = df_ts['close'].astype(float)
            df_ts['high'] = df_ts['high'].astype(float)
            df_ts['low'] = df_ts['low'].astype(float)
            df_ts['open'] = df_ts['open'].astype(float)
            df_ts['vol'] = df_ts['vol'].astype(float)
            df = df_ts
            print("  Tushare数据: %d条  %s~%s" % (len(df), df['date'].iloc[0].strftime('%m/%d'), df['date'].iloc[-1].strftime('%m/%d')))
    except Exception as e:
        print("  Tushare: %s" % e)

# ── 3. 技术指标 ──
print("\n--- 技术指标 ---")
if df is not None and len(df) >= 20:
    df = df.sort_values('date').reset_index(drop=True)
    
    # 均线
    for n in [5, 10, 20, 60]:
        if len(df) >= n:
            df['ma%d' % n] = df['close'].rolling(n).mean()
    
    # RSI(6)
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(6).mean()
    loss = (-delta.clip(upper=0)).rolling(6).mean()
    rs = gain / loss
    df['rsi6'] = 100 - (100 / (1 + rs))
    
    # 量比
    avg_vol5 = df['vol'].tail(5).mean()
    df['vol_ratio'] = df['vol'] / avg_vol5
    
    # MACD
    ema12 = df['close'].ewm(span=12).mean()
    ema26 = df['close'].ewm(span=26).mean()
    df['dif'] = ema12 - ema26
    df['dea'] = df['dif'].ewm(span=9).mean()
    df['macd'] = (df['dif'] - df['dea']) * 2
    
    # 布林带
    df['boll_mid'] = df['close'].rolling(20).mean()
    df['boll_std'] = df['close'].rolling(20).std()
    df['boll_upper'] = df['boll_mid'] + 2 * df['boll_std']
    df['boll_lower'] = df['boll_mid'] - 2 * df['boll_std']
    
    # KDJ
    low9 = df['low'].rolling(9).min()
    high9 = df['high'].rolling(9).max()
    rsv = (df['close'] - low9) / (high9 - low9) * 100
    df['kdj_k'] = rsv.ewm(com=2).mean()
    df['kdj_d'] = df['kdj_k'].ewm(com=2).mean()
    df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    print("  均线:")
    for n in [5, 10, 20, 60]:
        if 'ma%d' % n in df.columns:
            v = df['ma%d' % n].iloc[-1]
            pos = '>' if last['close'] > v else '<'
            print("    MA%d=%.2f  (价格%s均线)" % (n, v, pos))
    
    print("  RSI(6)=%.1f" % last['rsi6'])
    print("  量比=%.2f (5日均值=%.0f万手)" % (last['vol_ratio'], avg_vol5/100))
    print("  MACD: DIF=%.2f  DEA=%.2f  MACD柱=%.2f" % (
        last['dif'], last['dea'], last['macd']))
    
    # 布林带
    print("  布林带: 上轨=%.2f  中轨=%.2f  下轨=%.2f" % (
        last['boll_upper'], last['boll_mid'], last['boll_lower']))
    
    # KDJ
    print("  KDJ: K=%.1f  D=%.1f  J=%.1f" % (
        last['kdj_k'], last['kdj_d'], last['kdj_j']))
    
    # ── 4. 近期走势 ──
    print("\n--- 近30日K线 ---")
    print("  %-6s %-8s %-8s %-8s %-8s %-7s %-6s %-6s %-8s %-8s" % (
        '日期', '开盘', '最高', '最低', '收盘', '涨跌幅', '量比', 'RSI6', 'MA20', 'MACD'))
    prev_c = None
    for _, row in df.tail(30).iterrows():
        pct = (row['close'] - prev_c) / prev_c * 100 if prev_c else 0
        pct_str = '%+.2f%%' % pct
        vr = row.get('vol_ratio', 0)
        rsi = row.get('rsi6', 0)
        ma20 = row.get('ma20', 0)
        macd = row.get('macd', 0)
        ma20_str = '%.1f' % ma20 if ma20 > 0 else '-'
        macd_str = '%.2f' % macd if not pd.isna(macd) else '-'
        bull = 'BULL' if pct > 0 else 'BEAR'
        print("  %s %8.2f %8.2f %8.2f %8.2f %+7.2f%% %5.2f %6.1f %8s %8s %s" % (
            row['date'].strftime('%m/%d'), row['open'], row['high'], row['low'],
            row['close'], pct, vr, rsi, ma20_str, macd_str, bull))
        prev_c = row['close']
    
    # ── 5. 综合分析 ──
    print("\n--- 综合分析 ---")
    close = last['close']
    ma5 = df['ma5'].iloc[-1]
    ma10 = df['ma10'].iloc[-1]
    ma20 = df['ma20'].iloc[-1]
    ma60 = df['ma60'].iloc[-1] if 'ma60' in df.columns and not pd.isna(df['ma60'].iloc[-1]) else 0
    rsi = last['rsi6']
    macd_hist = last['macd']
    dif = last['dif']
    dea = last['dea']
    
    signals = []
    
    # 均线多头/空头
    if close > ma5 > ma10 > ma20:
        signals.append(('均线多头排列', 1, 'MA5>MA10>MA20'))
    elif close < ma5 < ma10 < ma20:
        signals.append(('均线空头排列', -1, 'MA5<MA10<MA20'))
    elif close > ma5:
        signals.append(('站上MA5', 0.5, ''))
    else:
        signals.append(('跌破MA5', -0.5, ''))
    
    # RSI
    if rsi > 80:
        signals.append(('RSI超买', -1, 'RSI=%.0f' % rsi))
    elif rsi < 30:
        signals.append(('RSI超卖', 1, 'RSI=%.0f' % rsi))
    elif 50 < rsi < 70:
        signals.append(('RSI健康', 0.5, 'RSI=%.0f' % rsi))
    
    # MACD
    if macd_hist > 0:
        signals.append(('MACD红柱', 1, '柱=%.2f' % macd_hist))
    else:
        signals.append(('MACD绿柱', -1, '柱=%.2f' % macd_hist))
    
    if dif > dea:
        signals.append(('MACD金叉', 1, 'DIF>DEA'))
    elif dif < dea:
        signals.append(('MACD死叉', -1, 'DIF<DEA'))
    
    # 布林带
    if close > last['boll_upper']:
        signals.append(('突破布林上轨', 0.5, '超买信号'))
    elif close < last['boll_lower']:
        signals.append(('跌破布林下轨', 0.5, '超卖信号'))
    
    # 趋势
    pct5 = (close - df.iloc[-6]['close']) / df.iloc[-6]['close'] * 100 if len(df) >= 6 else 0
    pct10 = (close - df.iloc[-11]['close']) / df.iloc[-11]['close'] * 100 if len(df) >= 11 else 0
    if pct5 > 3: signals.append(('5日强势+%.1f%%' % pct5, 0.5, ''))
    if pct5 < -3: signals.append(('5日弱势%.1f%%' % pct5, -0.5, ''))
    
    # 综合评分
    score = sum(s[1] for s in signals)
    total = len(signals)
    
    print("  信号列表:")
    for name, weight, detail in signals:
        direction = '+' if weight > 0 else ('-' if weight < 0 else '~')
        print("    %s %s  %s" % (direction, name, detail))
    
    print("\n  综合信号分: %+.1f / %d" % (score, total))
    
    # 趋势判断
    if score >= 3:
        verdict = 'STRONG_UP'
    elif score >= 1:
        verdict = 'UP'
    elif score >= -1:
        verdict = 'NEUTRAL'
    elif score >= -3:
        verdict = 'DOWN'
    else:
        verdict = 'STRONG_DOWN'
    
    print("  趋势判断: [%s]" % verdict)
    
    # 支撑/压力
    print("\n  支撑压力位:")
    print("    压力1: %.2f (近期高点)" % df.tail(20)['high'].max())
    print("    压力2: %.2f (MA20)" % ma20)
    print("    当前:   %.2f" % close)
    print("    支撑1: %.2f (MA5)" % ma5)
    print("    支撑2: %.2f (布林下轨)" % last['boll_lower'])
    print("    支撑3: %.2f (近期低点)" % df.tail(20)['low'].min())
    
    # 银行板块相关性
    print("\n  银行板块背景:")
    print("    今日银行ETF逆势上涨+1.28%")
    print("    宁波银行+2.83%，银行为今日最强防御板块")
    print("    分红密集期，高股息吸引力增强")
else:
    print("  数据不足")

print("\nDone")
