# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

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
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'sent2.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

# 四大指数快照
print("=== INDEX SNAPSHOT ===")
stocks = '000001.SH,399001.SZ,399006.SZ,399300.SZ,932000.SH'
resp = mcp('tdx_quotes', code=stocks, setcode='0')

indices = []
if resp:
    data = resp.get('data', [])
    if isinstance(data, list):
        for item in data:
            if isinstance(item, (list, dict)):
                if isinstance(item, list):
                    if len(item) < 3: continue
                    name = str(item[0]) if item[0] else ''
                    close = float(item[1]) if item[1] else 0
                    pct = float(item[2]) if item[2] else 0
                    high = float(item[3]) if len(item) > 3 and item[3] else 0
                    low = float(item[4]) if len(item) > 4 and item[4] else 0
                else:
                    name = str(item.get('name', ''))
                    close = float(item.get('close', 0))
                    pct = float(item.get('pct_chg', 0))
                    high = float(item.get('high', 0))
                    low = float(item.get('low', 0))
                if close > 0:
                    indices.append({'name': name, 'close': close, 'pct': pct, 'high': high, 'low': low})
                    pct_str = '+%.2f' % pct if pct >= 0 else '%.2f' % pct
                    print('  %s: %.2f  %s%%  H=%.2f L=%.2f' % (name, close, pct_str, high, low))
else:
    print('  [MCP quotes failed]')

# 今日快讯涨跌停统计
print()
print("=== TODAY NEWS ZT/DT ===")
resp_news = mcp('wenda_news_query', bdate='20260713', edate='20260713')
zt_titles = []
dt_titles = []
if resp_news:
    items = resp_news.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            if '涨停' in title:
                zt_titles.append((t_str, title))
            if '跌停' in title:
                dt_titles.append((t_str, title))

print('  ZT: %d mentions  DT: %d mentions' % (len(zt_titles), len(dt_titles)))
for t, title in zt_titles[:5]:
    print('    ZT> %s %s' % (t[:16], title[:60]))
for t, title in dt_titles[:5]:
    print('    DT> %s %s' % (t[:16], title[:60]))

# 四大指数近期K线
print()
print("=== INDEX KLINE (recent) ===")
idx_codes = [
    ('000001', '1', 'SH000001'),
    ('399001', '0', 'SZ399001'),
    ('399006', '0', 'SZ399006'),
    ('399300', '0', 'SZ399300'),
]
for code, market, label in idx_codes:
    resp2 = mcp('tdx_kline', code=code, setcode=market, period='4', wantNum='20', tqFlag='11')
    if resp2:
        rows = resp2.get('Rows', [])
        valid = []
        for row in rows:
            d = row.get('Data', '')
            if len(d) < 29: continue
            try:
                close = float(d[16:23])
                high = float(d[23:30])
                low = float(d[30:37])
                date = d[:8]
                if 1000 < close < 100000 and 1000 < high < 100000:
                    valid.append({'date': date, 'high': high, 'low': low, 'close': close})
            except:
                continue
        if valid:
            valid.sort(key=lambda x: x['date'])
            last5 = valid[-5:] if len(valid) >= 5 else valid
            ma5 = sum(v['close'] for v in last5) / len(last5)
            ma10_data = valid[-10:] if len(valid) >= 10 else valid
            ma10 = sum(v['close'] for v in ma10_data) / len(ma10_data)
            ma20_data = valid[-20:] if len(valid) >= 20 else valid
            ma20 = sum(v['close'] for v in ma20_data) / len(ma20_data)
            pct5 = (valid[-1]['close'] - valid[0]['close']) / valid[0]['close'] * 100 if len(valid) >= 2 else 0
            if valid[-1]['close'] > ma5: trend = 'MA5+'
            elif valid[-1]['close'] < ma5 * 0.98: trend = 'MA5-'
            else: trend = 'MA5='
            last5_str = ' '.join(['%.0f(%s)' % (v['close'], v['date'][4:]) for v in last5])
            print('  [%s] %.2f  %+.1f%%/period  MA5=%.1f MA10=%.1f MA20=%.1f %s' % (
                label, valid[-1]['close'], pct5, ma5, ma10, ma20, trend))
            print('    last5: %s' % last5_str)
    else:
        print('  [%s] MCP failed' % label)

print()
print('DONE')
