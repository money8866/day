"""
批量替换所有硬编码 Tushare Token 的 Python 文件
改为从 D:\mystock\config\.env 加载（第二版：补漏 token/TOKEN/ts.set_token 模式）
"""
import re, os

TOKEN_VAL = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ENV_PATH = r'D:\mystock\config\.env'

ENV_READER_LINES = [
    f"for _l in open(r'{ENV_PATH}'):",
    f"    if _l.strip().startswith('TUSHARE_TOKEN='):",
    f"        token = _l.strip().split('=', 1)[1].strip().strip('\"')",
    f"        break",
]

ENV_READER_LINES_CAP = [
    f"for _l in open(r'{ENV_PATH}'):",
    f"    if _l.strip().startswith('TUSHARE_TOKEN='):",
    f"        TOKEN = _l.strip().split('=', 1)[1].strip().strip('\"')",
    f"        break",
]

ENV_READER_LINES_OS = [
    f"if 'TUSHARE_TOKEN' not in os.environ:",
    f"    for _l in open(r'{ENV_PATH}'):",
    f"        if _l.strip().startswith('TUSHARE_TOKEN='):",
    f"            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('\"')",
    f"            break",
]

results = {'fixed': [], 'error': []}

def check_and_replace(content):
    lines = content.split('\n')
    new_lines = []
    changed = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]

        # Pattern 1: os.environ.setdefault / os.environ[] = / pro = ts.pro_api(
        if (stripped == f"os.environ.setdefault('TUSHARE_TOKEN', '{TOKEN_VAL}')" or
            stripped == f"os.environ['TUSHARE_TOKEN'] = '{TOKEN_VAL}'" or
            stripped == f"pro = ts.pro_api('{TOKEN_VAL}')"):
            new_lines.extend([indent + r for r in ENV_READER_LINES_OS])
            changed = True
            i += 1
            continue

        # Pattern 2: token = 'TOKEN' (lowercase)
        if stripped == f"token = '{TOKEN_VAL}'":
            new_lines.extend([indent + r for r in ENV_READER_LINES])
            changed = True
            i += 1
            continue

        # Pattern 3: TOKEN = 'TOKEN' (uppercase)
        if stripped == f"TOKEN = '{TOKEN_VAL}'":
            new_lines.extend([indent + r for r in ENV_READER_LINES_CAP])
            changed = True
            i += 1
            continue

        # Pattern 4: ts.set_token('TOKEN')
        if stripped == f"ts.set_token('{TOKEN_VAL}')":
            new_lines.extend([indent + r for r in ENV_READER_LINES])
            new_lines.append(f"{indent}ts.set_token(token)")
            changed = True
            i += 1
            continue

        new_lines.append(lines[i])
        i += 1

    if changed:
        return '\n'.join(new_lines), True
    return content, False

def fix_py_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if TOKEN_VAL not in content:
            return False

        new_content, changed = check_and_replace(content)
        if changed:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
        return False
    except Exception as e:
        results['error'].append((filepath, str(e)))
        return False

py_files = set()
for root, dirs, files in os.walk(r'D:\mystock\solo'):
    for f in files:
        if f.endswith('.py'):
            py_files.add(os.path.join(root, f))

print(f"共找到 {len(py_files)} 个 Python 文件")
print("开始扫描并替换...\n")

count = 0
for fp in sorted(py_files):
    if fix_py_file(fp):
        results['fixed'].append(fp)
        count += 1
        if count <= 5 or count % 10 == 0:
            print(f"  [{count}] 修复: {fp}")

# 特殊处理 tushare_quant.py 的 env 回退
tushare_quant = r'D:\mystock\solo\tushare_quant.py'
if os.path.exists(tushare_quant):
    with open(tushare_quant, 'r', encoding='utf-8') as f:
        content = f.read()
    old = f"os.environ.get('TUSHARE_TOKEN', '{TOKEN_VAL}')"
    new = "os.environ.get('TUSHARE_TOKEN', '')"
    if old in content:
        content = content.replace(old, new)
        with open(tushare_quant, 'w', encoding='utf-8') as f:
            f.write(content)
        entry = tushare_quant + " (env fallback)"
        if entry not in results['fixed']:
            results['fixed'].append(entry)
            print(f"  [env回退] 修复: {tushare_quant}")

print(f"\n{'='*60}")
print(f"批量替换完成！")
print(f"  修复文件: {len(results['fixed'])}")
print(f"  错误: {len(results['error'])}")
print(f"{'='*60}")

if results['fixed']:
    print(f"\n本轮修复文件列表：")
    for f in results['fixed']:
        print(f"  \u2713 {f}")

if results['error']:
    print(f"\n错误列表：")
    for f, e in results['error']:
        print(f"  \u2717 {f}: {e}")
