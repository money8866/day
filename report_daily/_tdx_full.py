# -*- coding: utf-8 -*-
import subprocess, json, os, datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
today = datetime.date.today().strftime('%Y%m%d')

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_f.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        print("  [ERR] rc=%d: %s" % (rc, out[:100]))
        return None
    try:
        return json.loads(out.strip())
    except:
        return None

# ==============================
print("=" * 60)
print("  2026-07-10 上午 A股市场走势分析")
print("=" * 60)

# 1. 四大指数实时快照
print("\n【一、四大指数实时行情】")
indices = [
    ('1A0001', '上证指数',  '1'),
    ('399001', '深证成指',  '0'),
    ('399006', '创业板指',  '0'),
    ('399300', '沪深300',  '0'),
    ('1B0016', '科创50',    '1'),
]

idx_data = {}
for code, name, sc in indices:
    resp = mcp('tdx_quotes', code=code, setcode=sc, hasHQInfo='1', hasExtInfo='1', hasCalcInfo='1')
    if not resp:
        continue
    hq = resp.get('HQInfo', {})
    ext = resp.get('ExtInfo', {})
    if not hq:
        continue
    
    now = hq.get('Now', 0)
    close = hq.get('Close', 0)
    open_p = hq.get('Open', 0)
    high = hq.get('MaxP', 0)
    low = hq.get('MinP', 0)
    vol = int(hq.get('Volume', '0'))
    amount = hq.get('Amount', 0)
    hsl = hq.get('HSL', 0)
    inside = int(hq.get('Inside', '0'))
    outside = int(hq.get('Outside', '0'))
    hq_time = hq.get('HQTime', '')
    syl = ext.get('SYL', 0)  # 市盈率
    sjl = ext.get('SJL', 0)  # 市净率
    zgb = ext.get('ZGB', 0)  # 总股本
    
    # 涨跌幅
    if close > 0:
        pct = (now - close) / close * 100
    else:
        pct = 0
    
    flag = '+' if pct > 0 else ''
    
    # 成交额格式化
    if amount >= 1e12:
        amt_str = "%.2f万亿" % (amount / 1e12)
    elif amount >= 1e8:
        amt_str = "%.0f亿" % (amount / 1e8)
    else:
        amt_str = "%.0f万" % (amount / 1e4)
    
    # 内外盘
    wb_ratio = outside / (inside + outside) * 100 if (inside + outside) > 0 else 50
    
    print("\n  %s(%s)" % (name, code))
    print("    时间: %s:%s  (11:30上午盘)" % (hq_time[:2], hq_time[2:4]))
    print("    当前: %.2f  %s%.2f%%" % (now, flag, pct))
    print("    开盘: %.2f  最高: %.2f  最低: %.2f  昨收: %.2f" % (open_p, high, low, close))
    print("    成交量: %d万手  成交额: %s  换手: %.2f%%" % (vol//10000, amt_str, hsl))
    print("    内外盘: 内盘%d万手 / 外盘%d万手  (外内比: %.1f%%)" % (inside//10000, outside//10000, wb_ratio))
    if syl: print("    市盈率: %.1f  市净率: %.2f" % (syl, sjl))
    
    idx_data[name] = {
        'now': now, 'close': close, 'pct': pct,
        'high': high, 'low': low, 'open': open_p,
        'vol': vol, 'amount': amount, 'hsl': hsl,
        'inside': inside, 'outside': outside
    }

# 2. K线五日数据
print("\n【二、近5日K线】")
for code, name, sc in indices:
    resp = mcp('tdx_kline', code=code, setcode=sc, period='4', wantNum='5', tqFlag='11')
    if not resp: continue
    rows = resp.get('Rows', [])
    if not rows: continue
    print("\n  %s:" % name)
    print("  %-10s %7s %7s %7s %7s %6s %8s" % ('日期', '开盘', '最高', '最低', '收盘', '涨跌幅', '成交量(万)'))
    for row in rows:
        t = row.get('Data', '')[:8]
        o = float(row.get('Open', 0))
        h = float(row.get('High', 0))
        l = float(row.get('Low', 0))
        c = float(row.get('Close', 0))
        v = int(row.get('Volume', '0'))
        prev_c = None
        # 计算涨跌幅
        idx_data[name]['_prev'] = idx_data[name].get('_prev', c)
        pct_c = (c - idx_data[name].get('_prev', c)) / idx_data[name].get('_prev', c) * 100 if idx_data[name].get('_prev') else 0
        idx_data[name]['_prev'] = c
        flag = '+' if pct_c > 0 else ('-' if pct_c < 0 else ' ')
        print("  %-10s %7.2f %7.2f %7.2f %7.2f %+6.2f%% %8d" % (t, o, h, l, c, pct_c, v//10000))

# 3. 技术指标
print("\n【三、技术指标快照】")
for code, name, sc in [('1A0001', '上证指数', '1'), ('399006', '创业板指', '0')]:
    resp = mcp('tdx_indicator_select', message='%s 当前价格涨跌幅成交量成交额换手率量比日内波幅' % name, rang='ZS')
    if resp:
        print("\n  %s 指标:" % name)
        data = resp.get('data', [])
        if isinstance(data, list):
            for item in data:
                print("  ", json.dumps(item, ensure_ascii=False)[:200])

# 4. 今日快讯
print("\n【四、今日市场快讯】")
resp = mcp('wenda_news_query', bdate=today, edate=today)
if resp:
    news_items = resp.get('data', [])
    if isinstance(news_items, list) and len(news_items) > 1:
        print("  共 %d 条快讯:\n" % (len(news_items)-1))
        for item in news_items[1:]:  # skip header
            if not isinstance(item, list) or len(item) < 3:
                continue
            title, time_str, link, source = item[0], item[1], item[2] if len(item)>2 else '', item[3] if len(item)>3 else ''
            summary = item[4] if len(item) > 4 else ''
            print("  [%s] %s" % (time_str[:16], title))
            if summary:
                print("    摘要: %s" % summary[:120])
            print()

# 5. 涨停板块
print("\n【五、涨停板块/概念】")
resp = mcp('tdx_screener', message='今日涨停股', rang='AG', pageNo='1', pageSize='20')
if resp:
    data = resp.get('data', [])
    print("  涨停股数量(前20页): %d" % len(data) if isinstance(data, list) else "  无数据")
    if isinstance(data, list):
        zt_stocks = []
        for s in data[:10]:
            if isinstance(s, dict):
                code_s = s.get('code', '')
                name_s = s.get('name', s.get('stock_name', ''))
                pct_s = s.get('pct_change', s.get('chg', 0))
                zt_stocks.append((name_s, code_s, pct_s))
        print("  代表涨停股:")
        for n, c, p in zt_stocks[:10]:
            print("    %s(%s)  +%.2f%%" % (n, c, p))
    else:
        print("  %s" % str(data)[:200])

print("\n" + "=" * 60)
print("  分析完成")
print("=" * 60)
