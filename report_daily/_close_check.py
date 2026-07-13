# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'close.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== 收盘数据验证 7/13 ===\n")

# 报告中的关键股票
stocks = [
    # 持仓
    ('159516', '0', '半导体设备ETF(持仓)'),
    # 明日操作组合
    ('300083', '0', '创世纪'),
    ('000620', '0', '盈新发展'),
    ('002965', '0', '祥鑫科技'),
    # 商业航天
    ('300762', '0', '上海瀚讯'),
    ('002025', '0', '航天电器'),
    # 医药
    ('600276', '1', '恒瑞医药'),
    # 半导体设备相关
    ('688432', '1', '有研硅'),
    ('688120', '1', '华海清科'),
    # 创新药ETF
    ('159992', '0', '创新药ETF'),
    # B浪信号
    ('605060', '1', '联德股份'),
    # 指数
    ('932000', '1', '中证2000'),
]

results = []
for code, mkt, name in stocks:
    resp = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp and 'HQInfo' in resp:
        hq = resp['HQInfo']
        close_y = float(hq.get('Close', 0))
        now = float(hq.get('Now', 0))
        high = float(hq.get('MaxP', 0))
        low = float(hq.get('MinP', 0))
        vol = hq.get('Volume', '')
        t = hq.get('HQTime', '')
        pct = (now - close_y) / close_y * 100 if close_y else 0
        results.append({
            'name': name, 'code': code,
            'close_y': close_y, 'now': now,
            'high': high, 'low': low,
            'pct': pct, 'vol': vol, 'time': t
        })
        pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
        print("%-20s %-10s  昨%8.2f  今%8.2f  %7s%%  H%8.2f  L%8.2f" % (
            name[:18], code, close_y, now, pct_str, high, low))

# 大盘
print()
大盘 = [
    ('000001', '1', '上证指数'),
    ('399001', '0', '深证成指'),
    ('399006', '0', '创业板指'),
    ('399300', '0', '沪深300'),
]
大盘基准 = {'上证指数': 3996.16, '深证成指': 15046.67, '创业板指': 3842.73, '沪深300': 4780.79}
for code, mkt, name in 大盘:
    resp2 = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp2 and 'HQInfo' in resp2:
        hq = resp2['HQInfo']
        now2 = float(hq.get('Now', 0))
        high2 = float(hq.get('MaxP', 0))
        low2 = float(hq.get('MinP', 0))
        close_y2 = float(hq.get('Close', 0))
        pct2 = (now2 - close_y2) / close_y2 * 100 if close_y2 else 0
        pct_str = '+%.2f' % pct2 if pct2 >= 0 else '%.2f'
        print("%-20s %-10s  昨%8.2f  今%8.2f  %7s%%  H%8.2f  L%8.2f" % (
            name[:18], code, close_y2, now2, pct_str, high2, low2))

print("\n完成")
