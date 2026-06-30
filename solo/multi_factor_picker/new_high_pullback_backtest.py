import tushare as ts

# ── Tushare初始化 ──
TUSHARE_TOKEN = ''
for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        TUSHARE_TOKEN = _l.strip().split('=', 1)[1].strip().strip('"\'')
        break
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ── 参数 ──
