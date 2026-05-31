path = r'D:\mystock\etf_quant.py'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

# Add 'import io' if not already imported
if 'import io' not in c:
    c = c.replace('import os\n', 'import os\nimport io\n', 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(c)
    print('Added import io')
else:
    print('io already imported')
