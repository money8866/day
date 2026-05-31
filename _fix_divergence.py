import json, sys
sys.stdout.reconfigure(encoding='utf-8')
path = 'D:/mystock/dragon/cache/scan_20260525.json'
with open(path, 'r', encoding='utf-8') as f:
    d = json.load(f)

# divergence字段被污染了，清掉它（原有6个字符串条目，不影响功能）
d['divergence'] = []
print(f'Cleaned divergence: {d["divergence"]}')

# 写回（先试）
try:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print('Fixed!')
except Exception as e:
    print(f'Write blocked: {e}')
    # 改名备份
    backup = path + '.bak'
    with open(backup, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f'Written to backup: {backup}')
