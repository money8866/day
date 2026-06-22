import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro

# 测试 stk_factor_pro
try:
    df = pro.stk_factor_pro(ts_code='688809.SH', start_date='20260501')
    if df is not None and not df.empty:
        print(f'stk_factor_pro: {len(df)}行')
        print(f'列: {list(df.columns)}')
        print('\n最新一行:')
        print(df.iloc[0].to_string())
    else:
        print('stk_factor_pro: 空数据')
except Exception as e:
    print(f'stk_factor_pro 错误: {e}')

# 对比 stk_factor（当前在用）
print('\n=== 当前 stk_factor 对比 ===')
df2 = pro.stk_factor(ts_code='688809.SH')
if df2 is not None and not df2.empty:
    print(f'列: {list(df2.columns)}')
    print(f'最新一行:')
    print(df2.iloc[0].to_string())
