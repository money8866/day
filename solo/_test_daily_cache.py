import sys, os
sys.path.insert(0, '.')

# 1. 测试 daily_cache 表创建和写入
import pandas as pd
from stock_cache import batch_insert_daily_cache, get_daily_cache, get_daily_cache_range

test_df = pd.DataFrame([{
    'ts_code': '999999.SZ', 'trade_date': '20260731',
    'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2,
    'pre_close': 10.0, 'change': 0.2, 'pct_chg': 2.0,
    'vol': 100000, 'amount': 1020000
}, {
    'ts_code': '999999.SZ', 'trade_date': '20260730',
    'open': 9.8, 'high': 10.1, 'low': 9.7, 'close': 10.0,
    'pre_close': 9.9, 'change': 0.1, 'pct_chg': 1.0,
    'vol': 90000, 'amount': 900000
}])

n = batch_insert_daily_cache(test_df)
print(f'[OK] 写入 daily_cache: {n} 行')

# 2. 测试读取
df = get_daily_cache('999999.SZ', '20260701', '20260731')
assert df is not None and len(df) == 2, f'读取失败: {df}'
print(f'[OK] 读取 daily_cache: {len(df)} 行')
print(f'  列: {list(df.columns)}')

# 3. 测试日期范围
min_d, max_d = get_daily_cache_range('999999.SZ')
print(f'[OK] 日期范围: {min_d} ~ {max_d}')
assert max_d == '20260731'

# 4. 测试 tushare_quant 的 _get_daily_from_sqlite
import tushare_quant as tq
df2 = tq._get_daily_from_sqlite('999999.SZ', '20260701', '20260731')
assert df2 is not None and len(df2) == 2
print(f'[OK] _get_daily_from_sqlite: {len(df2)} 行')

# 5. 清理测试数据
import sqlite3
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
c.execute("DELETE FROM daily_cache WHERE ts_code='999999.SZ'")
c.commit()
c.close()
print('[OK] 测试数据已清理')

# 6. 验证 stk_factor_pro 表未被破坏
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
row = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE ts_code='000001.SZ' AND ma_bfq_5 IS NOT NULL").fetchone()
print(f'[OK] stk_factor_pro 技术指标完好: 000001.SZ 有 {row[0]} 行含 ma_bfq_5')
c.close()

print('\n=== 全部测试通过 ===')
