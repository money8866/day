# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

# 1. token检查
rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
print("Token存在:", token is not None)
print("Token前20位:", token[:20] if token else "无")

# 2. config检查
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
config_ok = (rc_c == 0 and token and token in out_c)
print("Config状态:", "OK" if config_ok else "需要初始化")

if not config_ok:
    rc_r = ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    rc_a = ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)
    print("重新添加config:", rc_a == 0)

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'test_mcp.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        print("  MCP错误 rc=%d: %s" % (rc, out[:200]))
        return None
    try:
        return json.loads(out.strip())
    except:
        print("  解析失败，原始输出:")
        print("  " + out[:500])
        return None

# 3. 测试各个实时工具
print("\n=== 测试实时行情工具 ===")

# 3.1 tdx_quotes - 实时行情快照
print("\n[1] tdx_quotes 测试:")
resp = mcp('tdx_quotes', code='600036', setcode='1')
if resp:
    print("  响应keys:", list(resp.keys()) if isinstance(resp, dict) else type(resp))
    print("  data:", str(resp.get('data', resp))[:300])

# 3.2 tdx_quotes 批量
print("\n[2] tdx_quotes 批量测试:")
resp2 = mcp('tdx_quotes', code='000001.SH,600036.SH,399006.SZ,932000.SH', setcode='0')
if resp2:
    print("  data:", str(resp2.get('data', resp2))[:500])

# 3.3 用不同setcode
print("\n[3] tdx_quotes setcode=1:")
resp3 = mcp('tdx_quotes', code='000001,600036', setcode='1')
if resp3:
    print("  data:", str(resp3.get('data', resp3))[:500])

# 3.4 换不同code格式
print("\n[4] tdx_quotes code格式1:")
resp4 = mcp('tdx_quotes', code='1A0001,600036', setcode='1')
if resp4:
    print("  data:", str(resp4.get('data', resp4))[:500])

# 3.5 用index代码
print("\n[5] tdx_quotes 指数:")
resp5 = mcp('tdx_quotes', code='000001,399001,399006,399300', setcode='0')
if resp5:
    print("  data:", str(resp5.get('data', resp5))[:500])

print("\n=== 测试非实时工具(对比) ===")
# 3.6 tdx_kline 已知能用的
print("\n[6] tdx_kline 日K测试:")
resp6 = mcp('tdx_kline', code='600036', setcode='1', period='4', wantNum='5', tqFlag='11')
if resp6:
    print("  Rows:", len(resp6.get('Rows', [])), "条")
    print("  Sample:", str(resp6.get('Rows', [{}])[0])[:200])

# 3.7 wenda_news_query 快讯
print("\n[7] wenda_news_query:")
resp7 = mcp('wenda_news_query', bdate='20260713', edate='20260713')
if resp7:
    rows = resp7.get('data', [])
    print("  条数:", len(rows) if isinstance(rows, list) else "?")
    if isinstance(rows, list) and len(rows) > 1:
        print("  第一条:", str(rows[1])[:200])

print("\n完成")
