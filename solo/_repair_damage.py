import sqlite3, time
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

# 检查被破坏的行数
damaged = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE ma_bfq_5 IS NULL").fetchone()[0]
print(f'被破坏的行数(ma_bfq_5 IS NULL): {damaged}')

# 删除被破坏的行（技术指标已丢失，基础行情在 daily_cache 表中有备份）
if damaged > 0:
    t0 = time.time()
    c.execute("DELETE FROM stk_factor_pro WHERE ma_bfq_5 IS NULL")
    c.commit()
    print(f'已删除 {damaged} 行被破坏的数据, 耗时 {time.time()-t0:.1f}s')

# 验证清理结果
remaining = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE ma_bfq_5 IS NULL").fetchone()[0]
total = c.execute("SELECT COUNT(*) FROM stk_factor_pro").fetchone()[0]
print(f'清理后: 总 {total} 行, 技术指标NULL {remaining} 行')

# VACUUM 回收空间
print('执行 VACUUM...')
t0 = time.time()
c.execute("VACUUM")
print(f'VACUUM 完成, 耗时 {time.time()-t0:.1f}s')

c.close()
