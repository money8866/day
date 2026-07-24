import sqlite3, os, json

base = r'd:\mystock\solo\cache_backbone_tushare'
conn = sqlite3.connect(os.path.join(base, 'cache.db'))

# 查找包含 20260724 的 daily_kline 缓存
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%daily_kline%' AND key LIKE '%20260724%' ORDER BY key")
rows = cur.fetchall()
print(f'找到 {len(rows)} 个 daily_kline 缓存 (20260724)')

# 也可能缓存了 daily_quotes 或 daily_trade 格式
# 看看各种缓存类型
cur = conn.execute("SELECT DISTINCT SUBSTR(key,1,INSTR(key,'_')-1) || 'xxx' FROM cache_data WHERE key LIKE '%20260724%'")
types = cur.fetchall()
print('20260724 缓存类型:')
for t in types:
    cur2 = conn.execute(f"SELECT COUNT(*) FROM cache_data WHERE key LIKE '%20260724%' AND key LIKE '{t[0].replace('xxx','')}%'")
    cnt = cur2.fetchone()[0]
    print(f'  {t[0]}: {cnt} entries')

# 直接检查 wh_xxxx（每日收盘）的缓存
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%wh_%' AND key LIKE '%20260724%' LIMIT 5")
print('\nwh_ 缓存:')
for r in cur.fetchall():
    print(f'  {r[0]}')

# 检查 tsc_daily_trade_all 格式 
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%tsc_daily_trade%20260724%' LIMIT 3")
print('\ntsc_daily_trade 缓存:')
for r in cur.fetchall():
    print(f'  {r[0]}')

# 查看最近日期的缓存类型
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%tsc_daily_kline%600089%' ORDER BY key DESC LIMIT 5")
print('\ntsc_daily_kline 600089.SH 最近缓存:')
for r in cur.fetchall():
    print(f'  {r[0]}')

cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%tsc_daily_kline%301012%' ORDER BY key DESC LIMIT 5")
print('\ntsc_daily_kline 301012.SZ 最近缓存:')
for r in cur.fetchall():
    print(f'  {r[0]}')

cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%tsc_daily_kline%601700%' ORDER BY key DESC LIMIT 5")
print('\ntsc_daily_kline 601700.SH 最近缓存:')
for r in cur.fetchall():
    print(f'  {r[0]}')

conn.close()
