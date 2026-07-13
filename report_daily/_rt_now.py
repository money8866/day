# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'rtnow.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== MCP实时行情 11:30 ===\n")

# 拉多只股票实时行情
# 招行/上证/深证/创业板/中证2000/中证1000
resp = mcp('tdx_quotes', code='600036,000001,399001,399006,399300,932000', setcode='0')

if resp and 'HQInfo' in resp:
    hq = resp['HQInfo']
    ext = resp.get('ExtInfo', {})
    print("服务器时间: %s %s" % (hq.get('HQDate',''), hq.get('HQTime','')))
    print()

    # 解析: 每只股票的数据在HQInfo里是item[0], item[1]...
    # 根据ItemNum=2397,每6个字段一个股票
    # 格式: [代码, 名称, 昨收, 今开, 最高, 最低, 现价, 涨跌额, 涨跌幅, 成交量, 成交额, ...]
    # 让我从原始响应提取
    print("HQInfo字段:", list(hq.keys())[:20])
    
    # 尝试解析股票数组
    # 在Tdx返回中,每只股票6个字段: 序号/代码/名称/.../现价/涨跌/涨跌幅
    # 让我直接从响应结构看
    
    # 直接用已知字段
    print("\n主要字段:")
    print("  Close(昨收):", hq.get('Close'))
    print("  Open(今开):", hq.get('Open'))
    print("  MaxP(最高):", hq.get('MaxP'))
    print("  MinP(最低):", hq.get('MinP'))
    print("  Now(现价):", hq.get('Now'))
    print("  Volume:", hq.get('Volume'))
    print("  Amount:", hq.get('Amount'))
    print("  ItemNum:", hq.get('ItemNum'))

print()

# 单独查每只股票
stocks = [
    ('600036', '1', '招商银行'),
    ('000001', '1', '上证指数'),
    ('399001', '0', '深证成指'),
    ('399006', '0', '创业板指'),
    ('932000', '1', '中证2000'),
]

results = []
for code, mkt, name in stocks:
    resp2 = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp2 and 'HQInfo' in resp2:
        hq = resp2['HQInfo']
        close_y = hq.get('Close', 0)       # 昨收
        open_t = hq.get('Open', 0)          # 今开
        high = hq.get('MaxP', 0)            # 最高
        low = hq.get('MinP', 0)            # 最低
        now = hq.get('Now', 0)              # 现价
        vol = hq.get('Volume', 0)           # 成交量
        amount = hq.get('Amount', 0)         # 成交额
        t_str = hq.get('HQTime', '')        # 时间
        yield_y = hq.get('Yield', 0)        # 涨跌额
        y_close_pct = (now - close_y) / close_y * 100 if close_y else 0
        
        # 找涨跌幅
        pct = 0
        for key in ['UpDown', 'pct_chg', 'RiseFall', 'ZDF']:
            if key in hq:
                pct = hq[key]
                break
        
        # 从昨收和现价算涨跌
        if close_y and now:
            pct = (now - close_y) / close_y * 100
        
        results.append({
            'name': name, 'code': code,
            'close_y': close_y, 'open': open_t,
            'high': high, 'low': low, 'now': now,
            'vol': vol, 'amount': amount,
            'time': t_str, 'pct': pct, 'yield_y': yield_y
        })
        
        pct_str = '+%.2f' % pct if pct >= 0 else '%.2f' % pct
        y_str = '+%.2f' % yield_y if yield_y >= 0 else '%.2f' % yield_y
        print("%s(%s):" % (name, code))
        print("  昨收=%.2f  今开=%.2f  最高=%.2f  最低=%.2f  现价=%.2f" % (
            close_y, open_t, high, low, now))
        print("  涨跌=%.2f  涨跌幅=%s%%" % (yield_y, pct_str))
        print("  成交量=%s  成交额=%.1f亿" % (vol, amount/1e8))
        print()

# 招行专项分析
print("=" * 50)
print("招商银行 实时分析")
print("=" * 50)
cmbc = next((r for r in results if '招行' in r['name']), None)
if cmbc:
    now = cmbc['now']
    close_y = cmbc['close_y']
    open_t = cmbc['open']
    high = cmbc['high']
    low = cmbc['low']
    pct = cmbc['pct']
    amount = cmbc['amount']
    
    print("实时价格: %.2f 元" % now)
    print("涨跌幅: %+.2f%%" % pct)
    print("今日区间: %.2f ~ %.2f (震幅=%.2f%%)" % (low, high, (high-low)/close_y*100))
    
    # 关键价位
    ma5_tgt = 37.53  # 从之前数据
    ma20_tgt = 37.27
    ma60_tgt = 38.01
    
    print()
    print("关键价位:")
    print("  压力1: %.2f (MA20) - 已突破!" % ma20_tgt if now > ma20_tgt else "  压力1: %.2f (MA20)" % ma20_tgt)
    print("  压力2: %.2f (MA5)" % ma5_tgt)
    print("  压力3: %.2f (MA60)" % ma60_tgt)
    print("  当前:   %.2f" % now)
    print("  支撑1: %.2f (今开/布林下)" % open_t)
    print("  支撑2: %.2f (今日低点)" % low)
    
    # 银行板块今日情况
    print()
    print("银行板块背景:")
    print("  今日上证-1.54%, 银行逆势护盘")
    print("  招行当前%+d%%, 强于大盘%+d%%" % (pct, pct + 1.54))

print("\n完成")
