# -*- coding: utf-8 -*-
import subprocess, json, os, re

def tdx_mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp_test2.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    try:
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1],
            capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace'
        )
        try: os.remove(ps1)
        except: pass
        if result.returncode != 0:
            print('returncode:', result.returncode, result.stderr[:200])
            return None
        
        # 找到JSON块
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line.startswith('{') or line.startswith('['):
                try:
                    # 修复: 去掉末尾不完整的字段如 "Yield": 
                    # 策略: 把 "...": 或 "...", 形式的残留修复
                    # 更简单: 用正则去掉不完整的行尾
                    fixed = re.sub(r',?\s*"[A-Za-z]+":\s*$', '', line)
                    return json.loads(fixed)
                except Exception as e:
                    print('JSON parse error:', e, '| Fixed line:', fixed[:200])
                    pass
        print('No JSON found. stdout preview:', result.stdout[:400])
        return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

print('=== 测试: 上证指数 ===')
data = tdx_mcp('tdx_quotes', code='000001', setcode='1')
if data:
    print('Keys:', list(data.keys()) if isinstance(data, dict) else type(data))
    if 'data' in data:
        print('ItemNum:', data['data'].get('ItemNum') if isinstance(data['data'], dict) else len(data['data']))
        items = data['data']
        if isinstance(items, list):
            for item in items[:3]:
                print('  Code:', item.get('Code'), 'Now:', item.get('Now'), 'Close:', item.get('Close'), 'Pct:', item.get('ZhongWenJianCheng'))
        elif isinstance(items, dict):
            for k, v in list(items.items())[:5]:
                print(f'  {k}:', v)
else:
    print('None')

print()
print('=== 测试: 持仓ETF ===')
data2 = tdx_mcp('tdx_quotes', code='159516', setcode='0')
if data2 and 'data' in data2:
    items = data2['data']
    if isinstance(items, list) and items:
        item = items[0]
        now = item.get('Now', 0)
        close = item.get('Close', 0)
        pct = (now - close) / close * 100 if close > 0 else 0
        print('名称:', item.get('ZhongWenJianCheng'))
        print('现价:', now, '昨收:', close, '涨幅:', f'{pct:.2f}%')
        print('成交量:', item.get('Volume'))
        print('成交额:', item.get('Amount'))
    elif isinstance(items, dict):
        print(items)
