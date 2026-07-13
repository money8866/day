# -*- coding: utf-8 -*-
import subprocess, json, os, datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
today = datetime.date.today().strftime('%Y%m%d')
now = datetime.datetime.now()
print(f"时间: {now.strftime('%H:%M:%S')} 市场开盘{'✅' if 9*60+15<=now.hour*60+now.minute<=11*60+30 or 13*60<=now.hour*60+now.minute<=15*60+5 else '❌'}")

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)
print("MCP ready")

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_m.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        return None
    try:
        return json.loads(out.strip())
    except:
        return None

# ── 四大指数实时行情 ──
print("\n=== 四大指数 实时行情 ===")
indices = [
    ('1A0001', '上证指数',  '1'),
    ('399001', '深证成指',  '0'),
    ('399006', '创业板指',  '0'),
    ('399300', '沪深300',  '0'),
    ('1B0016', '科创50',    '1'),
]

results = {}
for code, name, sc in indices:
    resp = mcp('tdx_quotes', code=code, setcode=sc, hasHQInfo='1', hasExtInfo='1', hasCalcInfo='1')
    if resp:
        hq = resp.get('HQInfo', {})
        ext = resp.get('ExtInfo', {})
        if hq:
            now_p = hq.get('Now', 0)
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
            syl = ext.get('SYL', 0)
            lb = hq.get('LB', 0)  # 量比
            lead = hq.get('Lead', 0)  # 涨跌
            
            pct = (now_p - close) / close * 100 if close > 0 else 0
            flag = '🔴' if pct > 0 else ('🟢' if pct < 0 else '⚪')
            
            amt_str = "%.2f万亿" % (amount/1e12) if amount >= 1e12 else "%.0f亿" % (amount/1e8)
            wb_ratio = outside/(inside+outside)*100 if (inside+outside)>0 else 50
            
            print(f"\n  {flag} {name}({code})  [{hq_time}]")
            print(f"    当前: {now_p:.2f}  {flag}{pct:+.2f}%")
            print(f"    开盘: {open_p:.2f}  最高: {high:.2f}  最低: {low:.2f}  昨收: {close:.2f}")
            print(f"    成交额: {amt_str}  换手: {hsl:.2f}%  量比: {lb:.2f}倍")
            print(f"    内外盘: 内{int(inside/10000)}万手 / 外{int(outside/10000)}万手  外内比={wb_ratio:.1f}%")
            if syl: print(f"    市盈率: {syl:.1f}")
            results[name] = {'pct': pct, 'close': close, 'now': now_p, 'high': high, 'low': low, 'amount': amount, 'lb': lb}
    else:
        print(f"  ❌ {name} 无数据")

# ── 近5日K线 ──
print("\n\n=== 四大指数 近5日K线 ===")
for code, name, sc in indices:
    resp = mcp('tdx_kline', code=code, setcode=sc, period='4', wantNum='5', tqFlag='11')
    if resp:
        rows = resp.get('Rows', [])
        if rows:
            print(f"\n  {name}:")
            print("  %-10s %7s %7s %7s %7s %7s" % ('日期','开盘','最高','最低','收盘','涨跌幅'))
            prev_c = None
            for row in rows:
                t = row.get('Data','')[:8]
                o = float(row.get('Open', 0))
                h = float(row.get('High', 0))
                l = float(row.get('Low', 0))
                c = float(row.get('Close', 0))
                v = int(row.get('Volume','0'))
                pct_c = (c-prev_c)/prev_c*100 if prev_c else 0
                prev_c = c
                flag = '+' if pct_c > 0 else '-'
                print("  %-10s %7.2f %7.2f %7.2f %7.2f %+7.2f%% %8d万手" % (t,o,h,l,c,pct_c,v//10000))

# ── 今日快讯 ──
print("\n\n=== 今日市场快讯 ===")
resp = mcp('wenda_news_query', bdate=today, edate=today)
if resp:
    items = resp.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        print(f"  共 {len(items)-1} 条快讯:\n")
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 2: continue
            title = item[0] if len(item)>0 else ''
            t_str = item[1] if len(item)>1 else ''
            src = item[3] if len(item)>3 else ''
            summary = item[4] if len(item)>4 else ''
            print(f"  [{t_str[:16]}] {title}")
            if summary: print(f"    → {summary[:100]}")
            print()

print("\n=== 完成 ===")
