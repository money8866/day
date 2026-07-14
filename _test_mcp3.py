# -*- coding: utf-8 -*-
import subprocess, json, os, re

def tdx_mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp_test3.ps1')
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
            print('returncode:', result.returncode)
            return None
        
        raw = result.stdout.strip()
        # 策略1: 正常解析
        for line in raw.split('\n'):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    return json.loads(line)
                except: pass
        
        # 策略2: 提取JSON块（从{开始到最后一个完整的}）
        # 找第一个{和最后一个}
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            candidate = raw[start:end+1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                # 策略3: 去掉末尾不完整的字段
                # 去掉最后的 , "XX": 或 , "XX": 后面不完整的内容
                # 找最后一个完整的 "value" 模式: "...", 或 "...\n
                # 简单做法: 找最后一个有值的行尾 "value"\n 然后截断
                lines = candidate.split('\n')
                # 从后往前找，最后一行必须完整（有,闭合）
                # 去掉末尾不完整行
                while lines:
                    last = lines[-1].strip()
                    if not last or last.endswith(',') or re.match(r'^\s*"[^"]+":\s*$', last):
                        lines.pop()
                    else:
                        break
                if lines:
                    candidate2 = '\n'.join(lines)
                    # 确保以}结尾
                    candidate2 = candidate2.rstrip().rstrip(',').rstrip() + '\n}'
                    try:
                        return json.loads(candidate2)
                    except json.JSONDecodeError as e2:
                        print('Final parse error:', e2, 'Lines left:', len(lines))
                        print('Last line:', lines[-1][:100] if lines else 'none')
        return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

# 测试1: 指数
print('=== 上证指数 ===')
data = tdx_mcp('tdx_quotes', code='000001', setcode='1')
if data:
    info = data.get('HQInfo', {})
    now = info.get('Now', 0)
    close = info.get('Close', 0)
    pct = (now - close) / close * 100 if close else 0
    print(f"现价: {now}  昨收: {close}  涨幅: {pct:.2f}%  时间: {info.get('HQTime')}  成分数: {info.get('ItemNum')}")

# 测试2: ETF
print('\n=== 半导体设备ETF ===')
data2 = tdx_mcp('tdx_quotes', code='159516', setcode='0')
if data2:
    info2 = data2.get('HQInfo', {})
    now2 = info2.get('Now', 0)
    close2 = info2.get('Close', 0)
    pct2 = (now2 - close2) / close2 * 100 if close2 else 0
    print(f"现价: {now2}  昨收: {close2}  涨幅: {pct2:.2f}%  HSL(换手率): {info2.get('HSL')}")
