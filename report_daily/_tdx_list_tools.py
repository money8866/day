# -*- coding: utf-8 -*-
import subprocess, json, os
import sys
sys.stdout.reconfigure(encoding='utf-8')

def ps_run(cmd, timeout=15):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
print('Token:', 'OK' if token else 'FAIL')

rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

# Try known TDX tools
known_tools = [
    ('get_security_bars', {'category': '0', 'gqtType': '1', 'reqNum': '3', 'code': '1A0001', 'market': '1'}),
    ('get_security_quotes', {'market': '1', 'code': '1A0001'}),
    ('get_limit_list', {'market': '1', 'date': '20260710'}),
    ('get_minute_time_data', {'market': '1', 'code': '1A0001'}),
    ('tdx_api_data', {'mode': 'raw', 'entry': 'bars', 'code': '1A0001', 'count': '5'}),
    ('tdx_api_data', {'mode': 'raw', 'entry': 'quotes', 'code': '1A0001'}),
    ('tdx_api_data', {'mode': 'raw', 'entry': 'index', 'code': '1A0001'}),
    ('get_index', {'market': '1'}),
    ('get_market_quotes', {'market': '1'}),
    ('get_realtime_quotes', {'codes': '1A0001,399001'}),
    ('get_finance_data', {'code': '1A0001', 'start_date': '20260701', 'end_date': '20260710'}),
    ('gg_search', {'query': '上证指数 2026-07-10 行情', 'count': '3'}),
    ('tdx_news', {'code': '1A0001', 'start_date': '20260710', 'count': '5'}),
    ('tdx_f10_data', {'code': '1A0001', 'title': '公司概况'}),
    ('wenda_report_query', {'query': '市场|20260710|20260710|', 'pageSize': '5'}),
]

results = {}
for tool_name, params in known_tools:
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_t.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write('mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n' % (tool_name, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    ok = (rc == 0)
    if ok:
        try:
            j = json.loads(out.strip())
            has_data = bool(j.get('data'))
            print('[OK]  %-30s data=%s' % (tool_name, '有' if has_data else '空'))
            results[tool_name] = j
        except:
            print('[OK]  %-30s raw=%s' % (tool_name, out.strip()[:80]))
    else:
        # 从错误信息提取工具名
        err = out.strip()[:100]
        print('[NO]  %-30s %s' % (tool_name, err))

print('\n可用工具列表:')
for t, v in results.items():
    if v and v.get('data') is not None:
        print('  +', t)
