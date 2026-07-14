# -*- coding: utf-8 -*-
import subprocess, json, os

def tdx_mcp_raw(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp_raw.ps1')
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
        raw = result.stdout.strip()
        # 找JSON块
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            candidate = raw[start:end+1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                # 尝试修复: 去掉末尾不完整行
                lines = candidate.split('\n')
                while lines:
                    last = lines[-1].strip()
                    if not last or last.endswith(',') or (re_match(r'^\s*"[^"]+"\s*:', last) if 're_match' in dir() else re.match(r'^\s*"[^"]+"\s*:', last)):
                        lines.pop()
                    else:
                        break
                import re as _re
                while lines:
                    last = lines[-1].strip()
                    if _re.match(r'^\s*"[^"]+"\s*$', last):
                        lines.pop()
                    elif _re.match(r'^\s*"[^"]+"\s*:\s*$', last):
                        lines.pop()
                    else:
                        break
                if lines:
                    cand2 = '\n'.join(lines).rstrip().rstrip(',').rstrip() + '\n}'
                    try:
                        return json.loads(cand2)
                    except:
                        pass
                return None
        return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

import re

def parse_tdx_json(raw_text):
    """从mcporter原始输出中解析JSON"""
    # 找 { 到 最后一个}
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start < 0 or end <= start:
        return None
    text = raw_text[start:end+1]
    
    # 去掉末尾不完整行（没有值的字段名）
    lines = text.split('\n')
    # 从后往前删，直到遇到完整行
    fixed_lines = []
    for line in reversed(lines):
        stripped = line.strip()
        # 完整行判断: 有值 (数字, 字符串, }, ])
        if stripped.endswith('},') or stripped.endswith('}') or \
           stripped.endswith('],') or stripped.endswith(']') or \
           re.search(r':\s*[0-9"', stripped) or \
           re.search(r':\s*true|false|null', stripped):
            fixed_lines.insert(0, line)
            break
        elif re.match(r'^\s*"[^"]+"\s*:', stripped) and not re.search(r':\s*[0-9"', stripped):
            # 不完整行（只有字段名没有值），跳过
            continue
        else:
            fixed_lines.insert(0, line)
    return '\n'.join(fixed_lines)

def tdx_mcp_fixed(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp_fix.ps1')
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
        raw = result.stdout.strip()
        fixed = parse_tdx_json(raw)
        if fixed:
            return json.loads(fixed)
        return None
    except Exception as e:
        print('Exception:', e)
        try: os.remove(ps1)
        except: pass
        return None

# 测试指数
print('=== 测试各指数 ===')
for name, code, market in [('上证指数', '000001', '1'), ('深证成指', '399001', '0'), ('创业板指', '399006', '0'), ('沪深300', '000300', '1')]:
    data = tdx_mcp_fixed('tdx_quotes', code=code, setcode=market)
    if data:
        info = data.get('HQInfo', {})
        now = info.get('Now', 0)
        close = info.get('Close', 0)
        pct = (now - close) / close * 100 if close else 0
        print(f'{name}: 现价={now} 昨收={close} 涨跌幅={pct:+.2f}%')
    else:
        print(f'{name}: 解析失败')

print()
print('=== 测试持仓股 ===')
for code, market in [('159516', '0'), ('600519', '1'), ('000001', '0')]:
    data = tdx_mcp_fixed('tdx_quotes', code=code, setcode=market)
    if data:
        info = data.get('HQInfo', {})
        now = info.get('Now', 0)
        close = info.get('Close', 0)
        pct = (now - close) / close * 100 if close else 0
        name = info.get('ZhongWenJianCheng', code)
        print(f'{name}: 现价={now} 昨收={close} 涨跌幅={pct:+.2f}%')
    else:
        print(f'{code}: 解析失败')
