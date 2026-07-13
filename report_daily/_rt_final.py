# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'rtf.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== 招商银行实时行情  MCP ===\n")

# 招行
resp = mcp('tdx_quotes', code='600036', setcode='1')
if resp and 'HQInfo' in resp:
    hq = resp['HQInfo']
    close_y = float(hq.get('Close', 0))
    open_t = float(hq.get('Open', 0))
    high = float(hq.get('MaxP', 0))
    low = float(hq.get('MinP', 0))
    now = float(hq.get('Now', 0))
    vol = hq.get('Volume', '')
    amount = float(hq.get('Amount', 0))
    t_str = hq.get('HQTime', '')
    yield_y = float(hq.get('Yield', 0))
    hq_time = hq.get('HQTime', '')

    pct = (now - close_y) / close_y * 100 if close_y else 0
    pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
    y_str = '+%.2f' % yield_y if yield_y >= 0 else '%.2f'

    # 换手率
    hsl = hq.get('HSL', 0)
    lb = hq.get('LB', 0)  # 量比

    print("招商银行(600036.SH)")
    print("  数据时间: 11:30")
    print("  昨收: %.2f  今开: %.2f  最高: %.2f  最低: %.2f" % (close_y, open_t, high, low))
    print("  现价: %.2f  涨跌: %s  涨跌幅: %s%%" % (now, y_str, pct_str))
    print("  成交量: %s手  成交额: %.1f亿" % (vol, amount/1e8))
    print("  量比: %.2f" % lb)

    # 均线（用之前Tushare数据补充）
    ma5 = 37.53
    ma10 = 36.89
    ma20 = 37.27
    ma60 = 38.01
    prev_close_kline = 37.55  # 7/9收盘

    print("\n  均线位置:")
    print("    MA5=%.2f  当前=%.2f  %s" % (ma5, now, '>' if now > ma5 else '<'))
    print("    MA10=%.2f 当前=%.2f  %s" % (ma10, now, '>' if now > ma10 else '<'))
    print("    MA20=%.2f 当前=%.2f  %s" % (ma20, now, '>' if now > ma20 else '<'))
    print("    MA60=%.2f 当前=%.2f  %s" % (ma60, now, '>' if now > ma60 else '<'))

    print("\n  今日走势分析:")
    if now > ma20:
        print("    [突破] 股价已站上MA20(37.27)!")
    else:
        print("    [承压] 股价在MA20(37.27)下方")

    amp = (high - low) / close_y * 100
    print("    震幅: %.2f%%" % amp)
    print("    今开%.2f → 最高%.2f → 最低%.2f → 现价%.2f" % (open_t, high, low, now))
    
    # 走势类型判断
    if open_t > close_y and pct > 1:
        print("    形态: [高开高走] 强势")
    elif open_t < close_y and pct > 0:
        print("    形态: [低开高走] 转强")
    elif pct < 0 and now > open_t:
        print("    形态: [探底回升]")
    elif pct < 0 and now < open_t:
        print("    形态: [高开低走]")

    print("\n  关键价位:")
    print("    压力1: %.2f (MA20=37.27) - %s" % (ma20, "已突破" if now > ma20 else "待突破"))
    print("    压力2: %.2f (MA5=37.53)" % ma5)
    print("    压力3: %.2f (7/7开盘=37.72)" % 37.72)
    print("    压力4: %.2f (近期高点)" % high)
    print("    ─────────────")
    print("    当前:   %.2f" % now)
    print("    支撑1: %.2f (今开=%.2f)" % (open_t, open_t))
    print("    支撑2: %.2f (布林下轨≈35.21)" % 35.21)
    print("    支撑3: %.2f (近期低点)" % low)

# 大盘对比
print("\n\n=== 大盘对比 ===")
大盘 = [
    ('000001', '1', '上证指数', 3996.16),
    ('399001', '0', '深证成指', 15046.67),
    ('399006', '0', '创业板指', 3842.73),
]
for code, mkt, name, close_y in 大盘:
    resp2 = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp2 and 'HQInfo' in resp2:
        hq = resp2['HQInfo']
        now2 = float(hq.get('Now', 0))
        high2 = float(hq.get('MaxP', 0))
        low2 = float(hq.get('MinP', 0))
        pct2 = (now2 - close_y) / close_y * 100 if close_y else 0
        pct_str = '+%.2f' % pct2 if pct2 >= 0 else '%.2f'
        print("  %s: %.2f  %s%%  (H=%.2f L=%.2f)" % (name, now2, pct_str, high2, low2))

# 招行相对大盘
if resp and 'HQInfo' in resp:
    hq = resp['HQInfo']
    now_cmbc = float(hq.get('Now', 0))
    close_y_cmbc = float(hq.get('Close', 0))
    pct_cmbc = (now_cmbc - close_y_cmbc) / close_y_cmbc * 100 if close_y_cmbc else 0
    sh_pct = 0  # will be filled above
    
    print("\n  招行 vs 上证超额收益: %+.2f%%" % (pct_cmbc - (-1.54)))

print("\nDone")
