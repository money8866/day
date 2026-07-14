# -*- coding: utf-8 -*-
import subprocess, json, os, re

def tdx_mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp5.ps1')
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
            print('PowerShell error:', result.returncode, result.stderr[:200])
            return None
        
        raw = result.stdout
        print(f'DEBUG stdout len={len(raw)}, starts with: {repr(raw[:50])}')
        
        # 提取JSON
        start = raw.find('{')
        end = raw.rfind('}')
        if start < 0 or end <= start:
            print('No JSON braces found')
            return None
        text = raw[start:end+1]
        
        # 解析
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # 修复: 去掉末尾不完整的行
            lines = text.split('\n')
            print(f'DEBUG lines={len(lines)}, last={repr(lines[-1][:80])}')
            
            # 从后往前，找第一个完整行（有值的行）
            good_lines = []
            for line in reversed(lines):
                ls = line.strip()
                # 完整行: 包含: 后面有数字/字符串/}/]
                if ':' in ls or ls in ('}', ']', '{', '['):
                    # 把这行和之前的所有行都加回来，break
                    idx = lines.index(line)
                    good_lines = lines[:idx+1]
                    break
            else:
                good_lines = lines
            
            good_text = '\n'.join(good_lines).rstrip().rstrip(',') + '\n}'
            try:
                return json.loads(good_text)
            except json.JSONDecodeError as e2:
                print(f'JSON修复失败: {e2}')
                # 最后一招: 用正则搜索
                m = re.search(r'("Now":\s*[0-9.]+)', text)
                if m:
                    print(f'找到数据: {m.group(1)}')
                return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

print('=== 测试指数 ===')
for name, code, market in [('上证', '000001', '1'), ('深成', '399001', '0'), ('创业', '399006', '0')]:
    data = tdx_mcp('tdx_quotes', code=code, setcode=market)
    if data:
        info = data.get('HQInfo', {})
        now = info.get('Now', 0)
        close = info.get('Close', 0)
        pct = (now - close) / close * 100 if close else 0
        print(f'{name}: Now={now} Close={close} Pct={pct:+.2f}%')
    else:
        print(f'{name}: None')
    print()
