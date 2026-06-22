import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro

# 1. 检查 stk_factor 所有列
df = pro.stk_factor(ts_code='688809.SH')
if df is not None and not df.empty:
    print('=== stk_factor 列名 ===')
    print(list(df.columns))
    print('\n=== stk_factor 第一行 ===')
    print(df.iloc[0].to_string())

# 2. 检查 daily 接口是否有 ma 字段
print('\n\n=== daily 列名 ===')
df2 = pro.daily(ts_code='688809.SH', start_date='20260501')
if df2 is not None and not df2.empty:
    print(list(df2.columns))
    print('\n=== daily 第一行 ===')
    print(df2.iloc[0].to_string())

# 3. 检查有哪些技术指标接口
print('\n\n=== 尝试其他技术指标接口 ===')
interfaces_to_try = [
    ('ma_daily', 'pro.ma_daily(ts_code="688809.SH", start_date="20260501")'),
    ('stk_factor_indicator', 'pro.stk_factor(ts_code="688809.SH")'),
    ('daily_basic', 'pro.daily_basic(ts_code="688809.SH")'),
]
for name, cmd in interfaces_to_try:
    try:
        if name == 'ma_daily':
            result = pro.ma_daily(ts_code='688809.SH', start_date='20260501')
        elif name == 'daily_basic':
            result = pro.daily_basic(ts_code='688809.SH')
        else:
            continue
        if result is not None and not result.empty:
            print(f'\n{name}: 有数据 {len(result)}行，列={list(result.columns)[:15]}')
            print(result.head(1).to_string())
        else:
            print(f'\n{name}: 空数据')
    except Exception as e:
        print(f'\n{name}: {e}')
