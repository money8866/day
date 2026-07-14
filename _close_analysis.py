# -*- coding: utf-8 -*-
"""收盘分析 - 获取今日K线数据"""
import sys, os, time, json, subprocess, re
sys.path.insert(0, r'D:\mystock\solo')
import realtime_monitor_tdx as m

def tdx_kline(code, setcode, period='4', wantNum=5, tqFlag='11'):
    """获取K线数据"""
    args = "code='%s' setcode='%s' period='%s' wantNum=%d tqFlag='%s'" % (
        code, setcode, period, wantNum, tqFlag)
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'kline.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.tdx_kline %s\n" % args)
    try:
        r = subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1],
            capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace')
        try: os.remove(ps1)
        except: pass
        if r.returncode != 0:
            print('RC:', r.returncode, r.stderr[:100])
            return None
        raw = r.stdout
        start = raw.find('{')
        end = raw.rfind('}')
        if start < 0 or end <= start:
            return None
        text = raw[start:end+1]
        try:
            d = json.loads(text)
            return d.get('data')
        except:
            lines = text.split('\n')
            for i in range(len(lines)-1, -1, -1):
                ls = lines[i].strip()
                if ls.endswith('},') or ls.endswith('}') or ls.endswith('],') or ls.endswith(']'):
                    break
                if re.match(r'^\s*"[^"]+"\s*:', ls) and not re.search(r':\s*[0-9"{]', ls):
                    lines[i] = ''
            text2 = '\n'.join(lines).rstrip().rstrip(',').rstrip() + '\n}'
            try:
                d = json.loads(text2)
                return d.get('data')
            except:
                print('parse error:', text2[:100])
                return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

def parse_kline_date(k):
    """解析日期"""
    year = k.get('year', '')
    month = k.get('month', '')
    day = k.get('day', '')
    if year and month and day:
        return '%s-%s-%s' % (year, str(month).zfill(2), str(day).zfill(2))
    return k.get('date', '')

print('=== 主要指数K线 (最近5日) ===')
indices = [
    ('上证指数', '000001', '1'),
    ('深证成指', '399001', '0'),
    ('创业板指', '399006', '0'),
    ('科创50', '000688', '1'),
    ('沪深300', '000300', '1'),
    ('中证500', '000905', '1'),
]

for name, code, sc in indices:
    data = tdx_kline(code, sc, wantNum=5)
    if not data:
        print('%s: 获取失败' % name)
        continue
    rows = []
    for k in data[-5:]:
        date = parse_kline_date(k)
        close = float(k.get('close', 0))
        open_p = float(k.get('open', 0))
        high = float(k.get('high', 0))
        low = float(k.get('low', 0))
        vol = float(k.get('vol', 0))
        rows.append({'date': date, 'close': close, 'open': open_p, 'high': high, 'low': low, 'vol': vol})

    # 计算涨跌幅
    for i, row in enumerate(rows):
        if i == 0:
            row['pct'] = 0
        else:
            pct = (row['close'] - rows[i-1]['close']) / rows[i-1]['close'] * 100
            row['pct'] = round(pct, 2)

    print('\n[%s] 最新收盘: %.2f (%.2f%%)' % (name, rows[-1]['close'], rows[-1]['pct']))
    for row in rows[-3:]:
        pct_str = '+' if row['pct'] >= 0 else ''
        print('  %s  收=%.2f 开=%.2f 高=%.2f 低=%.2f %s%.2f%% Vol=%.0f' % (
            row['date'], row['close'], row['open'], row['high'], row['low'],
            pct_str, row['pct'], row['vol']))

    # 量比
    if len(rows) >= 2:
        vr = rows[-1]['vol'] / rows[-2]['vol'] if rows[-2]['vol'] else 0
        print('  量比: %.1fx' % vr)

print()
print('=== 持仓ETF ===')
mon = m.RealtimeMonitor()
mon.fetch_all()
for code, pos in mon.positions.items():
    q = mon.quotes.get(code)
    if q:
        entry = pos.get('entry', 0)
        cur = q.get('price', 0)
        pct_total = (cur - entry) / entry * 100 if entry else 0
        pct_today = q.get('pct_chg', 0)
        name = q.get('name', code)
        amount = q.get('amount', 0)
        print('%s (%s): 现价=%.3f 持仓盈亏=%.1f%% 今日涨跌=%.2f%% 成交额=%.0f亿' % (
            name, code, cur, pct_total, pct_today, amount/1e8))

print()
print('=== 主题强弱 ===')
theme_data = mon.analyze_themes()
for theme, data in sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True):
    stocks_str = ' '.join(['%s(%+.1f%%)' % (c[-6:], p) for c, p in sorted(data['stocks'], key=lambda x: x[1], reverse=True)])
    print('%s: 均=%.1f%% 高=%.1f%% 低=%.1f%% %d/%d上涨' % (theme, data['avg_pct'], data['max_pct'], data['min_pct'], data['up_count'], data['total']))
    print('  %s' % stocks_str)

score, status, pos = mon.calc_market_score()
print('\n市场评分: %.1f %s 建议仓位%d%%' % (score, status, pos))
