# -*- coding: utf-8 -*-
import sys, subprocess, json, os, datetime
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mkt.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=90)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        print("  MCP call failed rc=%d: %s" % (rc, out[:200]))
        return None
    try:
        return json.loads(out.strip())
    except:
        print("  Parse error: %s" % out[:200])
        return None

# === Init MCP ===
print("=== Init MCP ===")
rc_tok, out_tok = ps_run('& "%s\\get-token.ps1"' % SKILL_DIR, timeout=15)
token = out_tok.strip() if rc_tok == 0 else None
print("Token: %s" % ('OK' if token else 'FAIL'))

rc_c, out_c = ps_run("mcporter config get tdx-finance_qclaw 2>$null")
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)
    print("MCP configured")
else:
    print("MCP already configured")

# === 四大指数最新行情 ===
print("\n=== 四大指数 最新K线 ===")
indices = [
    ('1A0001', '上证指数',  1),
    ('399001', '深证成指',  0),
    ('399006', '创业板指', 0),
    ('399300', '沪深300',  0),
]
today_str = datetime.date.today().strftime('%Y%m%d')
print("Today: %s" % today_str)

for code, name, market in indices:
    resp = mcp_call('get_security_bars',
                     category='0',    # 日线
                     gqtType='1',
                     reqNum='5',
                     code=code,
                     market=str(market))
    if resp:
        bars = resp.get('data', [])
        print("\n[%s (%s)]" % (name, code))
        for bar in bars:
            time_str = bar.get('time', '')[:10]
            pct = bar.get('pct_change', 0)
            flag = '↑' if pct > 0 else ('↓' if pct < 0 else '-')
            print("  %s | 收:%.2f 涨跌幅:%+.2f%% 量比:%.2f 成交额:%.0f亿" % (
                time_str, bar.get('close', 0), pct,
                bar.get('volume_ratio', 1),
                bar.get('amount', 0) / 1e8))
    else:
        print("\n[%s] 无数据" % name)

# === 涨跌停统计 ===
print("\n=== 涨跌停统计 (%s) ===" % today_str)
for market, mname in [('1', '沪市'), ('0', '深市')]:
    resp = mcp_call('get_limit_list', market=market, date=today_str)
    if resp:
        data = resp.get('data', [])
        print("  %s: 涨停 %d 家" % (mname, len(data)))
        if data:
            for item in data[:5]:
                print("    - %s (%.2f%%)" % (item.get('code',''), item.get('pct_change', 0)))
            if len(data) > 5:
                print("    ... 共 %d 家" % len(data))
    else:
        print("  %s: 获取失败" % mname)

# === 实时行情快照 ===
print("\n=== 实时行情快照 ===")
for code, name, market in indices:
    resp = mcp_call('get_security_quotes', market=str(market), code=code)
    if resp:
        quotes = resp.get('data', [])
        if quotes:
            q = quotes[0]
            print("  %-8s 最新:%.2f 涨跌:%+.2f%% 成交量:%.2f亿 成交额:%.0f亿" % (
                name, q.get('close', 0), q.get('pct_change', 0),
                q.get('volume', 0)/1e8 if q.get('volume') else 0,
                q.get('amount', 0)/1e8 if q.get('amount') else 0))
    else:
        print("  %s: 快照获取失败" % name)

# === 上证/深证 日内分时（今日上午） ===
print("\n=== 今日上午分时走势 ===")
for code, name, market in [('1A0001', '上证指数', 1), ('399001', '深证成指', 0)]:
    resp = mcp_call('get_minute_time_data', market=str(market), code=code)
    if resp:
        data = resp.get('data', [])
        print("\n[%s] 分时数据 %d 条" % (name, len(data)))
        if data:
            # 取今天的数据
            today_data = [d for d in data if d.get('time','')[:8].startswith(today_str[-4:])] if data else data
            if today_data:
                print("  今日共 %d 个分时点" % len(today_data))
                first = today_data[0]
                last = today_data[-1]
                print("  开盘: %.2f | 当前: %.2f | 最高: %.2f | 最低: %.2f | 涨幅: %+.2f%%" % (
                    first.get('close', 0), last.get('close', 0),
                    max(d.get('close', 0) for d in today_data),
                    min(d.get('close', 0) for d in today_data),
                    last.get('pct_change', 0)))
            else:
                print("  无今日数据，展示最后几条:")
                for d in data[-3:]:
                    print("    %s | %.2f" % (d.get('time',''), d.get('close', 0)))
        else:
            print("  无分时数据")
    else:
        print("\n[%s] 分时获取失败" % name)

print("\n=== 完成 ===")
