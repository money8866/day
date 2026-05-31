path = r'D:\mystock\etf_quant.py'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

if 'sys.stdout = io.TextIOWrapper' not in c:
    # Add after the import block
    import_str = 'import sqlite3'
    idx = c.index(import_str) + len(import_str)
    patch = '''

# =========================
# Windows UTF-8 fix
# =========================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
'''
    c = c[:idx] + patch + c[idx:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(c)
    print('Added UTF-8 fix')
else:
    print('Already has UTF-8 fix')
