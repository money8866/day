# -*- coding: utf-8 -*-
"""检查回测数据库状态"""
import sqlite3, os

db = r'D:\mystock\cache_daily\tail_backtest_tdx.db'
if not os.path.exists(db):
    print('数据库不存在')
    exit()

conn = sqlite3.connect(db)
cur = conn.execute('SELECT COUNT(*) FROM tail_backtest')
print(f'总记录数: {cur.fetchone()[0]}')

cur = conn.execute('SELECT signal_date, COUNT(*) FROM tail_backtest GROUP BY signal_date ORDER BY signal_date')
rows = cur.fetchall()
print(f'交易日数: {len(rows)}')
for r in rows[:5]:
    print(f'  {r[0]}: {r[1]}条')
if len(rows) > 5:
    print(f'  ... 共{len(rows)}个交易日')

# 分数分布
cur = conn.execute('SELECT total_score, COUNT(*) FROM tail_backtest GROUP BY total_score ORDER BY total_score')
print('\n分数分布:')
for r in cur.fetchall():
    print(f'  {r[0]}分: {r[1]}条')

# 盈亏数据
cur = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE pnl IS NOT NULL')
print(f'\n已有盈亏数据: {cur.fetchone()[0]}条')

# 已平仓统计
cur = conn.execute('''
    SELECT signal_date, MIN(total_score), MAX(total_score), AVG(total_score)
    FROM tail_backtest WHERE status = 'closed'
    GROUP BY signal_date ORDER BY signal_date
''')
rows = cur.fetchall()
print(f'已平仓交易日: {len(rows)}')
if rows:
    print(f'  最早: {rows[0][0]}, 最晚: {rows[-1][0]}')
    print(f'  分数范围: {min(r[1] for r in rows)}-{max(r[2] for r in rows)}')

# 按分数段统计胜率
cur = conn.execute('''
    SELECT
        CASE
            WHEN total_score >= 85 THEN '>=85'
            WHEN total_score >= 75 THEN '75-84'
            WHEN total_score >= 65 THEN '65-74'
            ELSE '<65'
        END as score_range,
        COUNT(*) as total,
        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
        AVG(CASE WHEN pnl IS NOT NULL THEN pnl ELSE NULL END) as avg_pnl
    FROM tail_backtest
    WHERE pnl IS NOT NULL
    GROUP BY score_range
    ORDER BY score_range DESC
''')
print('\n按分数段胜率:')
print(f'{"分段":<10} {"总数":<6} {"胜":<6} {"胜率":<8} {"平均收益":<10}')
print('-' * 40)
for r in cur.fetchall():
    win_rate = r[2] / r[1] * 100 if r[1] > 0 else 0
    avg_pnl = r[3] if r[3] is not None else 0
    print(f'{r[0]:<10} {r[1]:<6} {r[2]:<6} {win_rate:<7.1f}% {avg_pnl:<+7.2f}%')

conn.close()