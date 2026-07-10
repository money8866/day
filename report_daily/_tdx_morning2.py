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

# Init MCP
rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)
    print("MCP configured")
else:
    print("MCP ready")

def mcp_call(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_m.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        return None
    try:
        return json.loads(out.strip())
    except:
        return None

# === 四大指数实时行情 ===
print("\n=== 四大指数 实时行情 ===")
indices = [
    ('1A0001', '上证指数',  '1'),
    ('399001', '深证成指',  '0'),
    ('399006', '创业板指',  '0'),
    ('399300', '沪深300',  '0'),
]
idx_results = {}
for code, name, setcode in indices:
    resp = mcp_call('tdx_quotes', code=code, setcode=setcode, hasHQInfo='1', hasExtInfo='1', hasCalcInfo='1')
    if resp:
        data = resp.get('data')
        if data:
            print("\n[%s %s]" % (name, code))
            # 基本行情
            hq = data.get('hq_str', data.get('hq', ''))
            if isinstance(data, dict):
                close = data.get('price', data.get('close', 0))
                pct = data.get('pct_change', data.get('zd', 0))
                vol = data.get('volume', 0)
                amount = data.get('amount', 0)
                high = data.get('high', 0)
                low = data.get('low', 0)
                open_p = data.get('open', 0)
                print("  字段: %s" % str(data)[:300])
            else:
                print("  数据: %s" % str(data)[:300])
            idx_results[name] = resp
    else:
        print("[%s] 无数据" % name)

# === 四大指数日K线（近5日）===
print("\n=== 四大指数 日K线 ===")
for code, name, setcode in indices:
    resp = mcp_call('tdx_kline', code=code, setcode=setcode, period='4', wantNum='5', tqFlag='11', hasAttachInfo='0')
    if resp:
        data = resp.get('data', [])
        print("\n[%s]" % name)
        if isinstance(data, list):
            for bar in data:
                t = bar.get('time', bar.get('datetime', ''))[:10]
                c = bar.get('close', 0)
                o = bar.get('open', 0)
                h = bar.get('high', 0)
                l = bar.get('low', 0)
                pct = bar.get('pct_change', 0)
                vr = bar.get('volume_ratio', 1)
                vol = bar.get('volume', 0)
                flag = '+' if pct > 0 else ('-' if pct < 0 else ' ')
                print("  %s | 开:%.2f 收:%.2f 高:%.2f 低:%.2f 涨跌:%+.2f%% 量比:%.2f" % (
                    t, o, c, h, l, pct, vr))
        else:
            print("  数据: %s" % str(data)[:300])
    else:
        print("[%s] K线无数据" % name)

# === 涨跌停 ===
print("\n=== 涨停板扫描 ===")
for setcode, mname in [('1', '沪市'), ('0', '深市')]:
    resp = mcp_call('tdx_screener', message='今日涨停', rang='AG', pageNo='1', pageSize='20')
    if resp:
        data = resp.get('data', [])
        cnt = len(data) if isinstance(data, list) else 0
        print("  %s 涨停（部分）: %d 家" % (mname, cnt))
        if isinstance(data, list):
            for s in data[:5]:
                print("    - %s" % str(s)[:100])
    else:
        print("  %s 涨停扫描失败" % mname)

# === 指数技术指标 ===
print("\n=== 指数技术指标 ===")
for code, name, setcode in indices:
    resp = mcp_call('tdx_indicator_select', message='%s 当前市盈率市净率成交量' % name, rang='ZS')
    if resp:
        print("\n[%s]" % name)
        print("  %s" % str(resp.get('data', ''))[:400])
    else:
        print("[%s] 指标查询失败" % name)

# === 今日资讯 ===
print("\n=== 今日市场快讯 ===")
resp = mcp_call('wenda_news_query', bdate=today, edate=today, keywords='A股,大盘,指数')
if resp:
    data = resp.get('data', [])
    print("快讯 %d 条:" % len(data) if isinstance(data, list) else "")
    if isinstance(data, list):
        for item in data[:5]:
            t = item.get('time', '')[:16]
            print("  [%s] %s" % (t, item.get('title', item.get('content', ''))[:80]))
    else:
        print("  %s" % str(data)[:300])

print("\n=== 完成 ===")
