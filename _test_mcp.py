# -*- coding: utf-8 -*-
import subprocess, json, os

def tdx_mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp_test.ps1')
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
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line.startswith('{') or line.startswith('['):
                try:
                    return json.loads(line)
                except Exception as e:
                    print('JSON parse error:', e, line[:100])
                    pass
        print('No JSON found. stdout:', result.stdout[:300])
        return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

print('=== 测试1: 上证指数 ===')
data = tdx_mcp('tdx_quotes', code='000001', setcode='1')
print('Result:', data)

print()
print('=== 测试2: 半导体设备ETF ===')
data2 = tdx_mcp('tdx_quotes', code='159516', setcode='0')
print('Result:', data2)

print()
print('=== 测试3: 批量行情 ===')
data3 = tdx_mcp('tdx_quotes', code='000001,399001,399006', setcode='1')
print('Result:', data3)
