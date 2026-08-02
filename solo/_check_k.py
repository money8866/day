# -*- coding: utf-8 -*-
"""分析K=0信号的原因分布"""
import sqlite3

conn = sqlite3.connect(r'D:\mystock\cache_daily\tail_backtest_tdx.db')

rows = conn.execute('''
  SELECT signal_date, ts_code, name, theme, total_score, tech_score, trap_penalty, k_filtered
  FROM tail_backtest
  WHERE signal_date >= 20260727 AND k_filtered = 0
  ORDER BY total_score DESC
  LIMIT 20
''').fetchall()
print('K=0信号(总分从高到低):')
for r in rows:
    print(f'  {r[0]} {r[1]} {r[2]:<8} {r[3]:<10} 总分={r[4]} 技分={r[5]} 诱多={r[6]}')

# 统计K=0的原因分布
total = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=0').fetchone()[0]
cnt1 = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=0 AND total_score < 88').fetchone()[0]
cnt2 = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=0 AND total_score >= 88 AND trap_penalty > 0').fetchone()[0]
cnt3 = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=0 AND total_score >= 88 AND trap_penalty = 0 AND tech_score < 12').fetchone()[0]
cnt4 = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=0 AND total_score >= 88 AND trap_penalty = 0 AND tech_score >= 12 AND (ts_code LIKE "9%" OR ts_code LIKE "4%")').fetchone()[0]
cnt5 = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=0 AND total_score >= 88 AND trap_penalty = 0 AND tech_score >= 12 AND ts_code NOT LIKE "9%" AND ts_code NOT LIKE "4%"').fetchone()[0]

print(f'\nK=0信号共{total}只，原因分布:')
print(f'  总分<88: {cnt1}只')
print(f'  有诱多扣分(总分>=88): {cnt2}只')
print(f'  技术分<12: {cnt3}只')
print(f'  北交所排除: {cnt4}只')
print(f'  非主题TOP2: {cnt5}只')

# 共多少K=1
k1 = conn.execute('SELECT COUNT(*) FROM tail_backtest WHERE signal_date >= 20260727 AND k_filtered=1').fetchone()[0]
print(f'\nK=1信号: {k1}只')
print(f'合计: {total + k1}只')

conn.close()