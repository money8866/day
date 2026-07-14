# -*- coding: utf-8 -*-
import subprocess, json, os, re, time

def _parse_tdx_json(raw_text):
    import re as _re
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start < 0 or end <= start:
        return None
    text = raw_text[start:end+1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lines = text.split('\n')
        for i in range(len(lines)-1, -1, -1):
            ls = lines[i].strip()
            if ls.endswith('},') or ls.endswith('}') or ls.endswith('],') or ls.endswith(']'):
                break
            if _re.match(r'^\s*"[^"]+"\s*:', ls) and not _re.search(r':\s*[0-9"{', ls):
                lines[i] = ''
            elif ls == '"' or ls == '':
                lines[i] = ''
        text2 = '\n'.join(lines).rstrip().rstrip(',').rstrip() + '\n}'
        try:
            return json.loads(text2)
        except:
            return None

def tdx_mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp6.ps1')
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
            return None
        return _parse_tdx_json(result.stdout)
    except Exception as e:
        try: os.remove(ps1)
        except: pass
        return None

# 测试批量查询 - 沪市
print('=== 批量: 000001+600519 (setcode=1) ===')
data = tdx_mcp('tdx_quotes', code='000001,600519', setcode='1')
if data:
    hq = data.get('HQInfo', {})
    print('ItemNum:', hq.get('ItemNum'))
    print('Now:', hq.get('Now'))
    print('Close:', hq.get('Close'))
    print('ZhongWenJianCheng:', hq.get('ZhongWenJianCheng'))
    n = hq.get('Now', 0); c = hq.get('Close', 1)
    print('pct: %.2f%%' % ((n-c)/c*100))
else:
    print('None')

print()
print('=== 单个: 600519 (setcode=1) ===')
data2 = tdx_mcp('tdx_quotes', code='600519', setcode='1')
if data2:
    hq = data2.get('HQInfo', {})
    print('ItemNum:', hq.get('ItemNum'))
    print('Now:', hq.get('Now'), 'Close:', hq.get('Close'))
    print('Name:', hq.get('ZhongWenJianCheng'))
else:
    print('None')

print()
print('=== 批量: 159516+000001 (setcode=0 深市) ===')
data3 = tdx_mcp('tdx_quotes', code='159516,000001', setcode='0')
if data3:
    hq = data3.get('HQInfo', {})
    print('ItemNum:', hq.get('ItemNum'))
    print('Now:', hq.get('Now'), 'Close:', hq.get('Close'))
    print('Name:', hq.get('ZhongWenJianCheng'))
else:
    print('None')

print()
print('=== 性能: 10次查询 ===')
start = time.time()
for code, sc in [('600519','1'),('000001','1'),('159516','0'),('601318','1'),('000002','0')]*2:
    tdx_mcp('tdx_quotes', code=code, setcode=sc)
elapsed = time.time() - start
print('10次: %.2fs (%.2fs/次)' % (elapsed, elapsed/10))
