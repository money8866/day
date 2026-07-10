# -*- coding: utf-8 -*-
import subprocess, json, os, datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
today = datetime.date.today().strftime('%Y%m%d')
print("Today:", today)

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

def mcp_raw(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_r.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    print("\n--- [%s] rc=%d ---" % (tool, rc))
    print(out[:500])
    try:
        j = json.loads(out.strip())
        print("JSON data:", str(j)[:600])
        return j
    except:
        return None

# Test tdx_quotes
print("\n\n========= 测试 tdx_quotes =========")
for code, name, sc in [('1A0001', '上证指数', '1'), ('399001', '深证成指', '0')]:
    mcp_raw('tdx_quotes', code=code, setcode=sc, hasHQInfo='1')

print("\n\n========= 测试 tdx_kline =========")
for code, name, sc in [('1A0001', '上证指数', '1'), ('399001', '深证成指', '0')]:
    mcp_raw('tdx_kline', code=code, setcode=sc, period='4', wantNum='5', tqFlag='11')

print("\n\n========= 测试 tdx_lookup_stock =========")
mcp_raw('tdx_lookup_stock', query='上证指数')

print("\n\n========= 测试 tdx_indicator_select =========")
mcp_raw('tdx_indicator_select', message='上证指数 当前价格涨跌幅成交量')

print("\n\n========= 测试 wenda_news_query =========")
mcp_raw('wenda_news_query', bdate=today, edate=today)

print("\n\n========= Done =========")
