import sqlite3, sys
sys.path.insert(0, '.')
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

# 1. 检查 stk_factor_pro 表的列
cols = [r[1] for r in c.execute('PRAGMA table_info(stk_factor_pro)').fetchall()]
print(f'stk_factor_pro 表列数: {len(cols)}')

# pro.daily 返回的 11 列
daily_cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'amount']
print(f'pro.daily 列数: {len(daily_cols)}')

# 检查哪些 daily 列不在 stk_factor_pro 表中
missing = [col for col in daily_cols if col not in cols]
print(f'缺失列(不在stk_factor_pro中): {missing}')

# 检查哪些 daily 列在 stk_factor_pro 表中
present = [col for col in daily_cols if col in cols]
print(f'存在列(在stk_factor_pro中): {present}')

# 2. 检查 20260731 数据量
has731 = c.execute("SELECT COUNT(DISTINCT ts_code) FROM stk_factor_pro WHERE trade_date='20260731'").fetchone()[0]
print(f'\n有20260731数据的股票数: {has731}')

# 3. 测试 batch_insert_stk_factor_pro 是否能写入 pro.daily 数据
import pandas as pd
test_df = pd.DataFrame([{
    'ts_code': '999999.SZ', 'trade_date': '20260731',
    'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2,
    'pre_close': 10.0, 'change': 0.2, 'pct_chg': 2.0,
    'vol': 100000, 'amount': 1020000
}])

from stock_cache import batch_insert_stk_factor_pro
try:
    n = batch_insert_stk_factor_pro(test_df)
    print(f'\n测试写入 pro.daily 数据: 成功({n}行)')
    # 验证数据
    row = c.execute("SELECT * FROM stk_factor_pro WHERE ts_code='999999.SZ'").fetchone()
    if row:
        col_names = [d[0] for d in c.execute("SELECT * FROM stk_factor_pro WHERE ts_code='999999.SZ'").description]
        print(f'  写入字段数: {len([v for v in row if v is not None])} / {len(col_names)}')
    # 清理测试数据
    c.execute("DELETE FROM stk_factor_pro WHERE ts_code='999999.SZ'")
    c.commit()
except Exception as e:
    print(f'\n测试写入 pro.daily 数据: 失败 - {e}')

c.close()
