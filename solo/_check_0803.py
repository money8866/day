# -*- coding: utf-8 -*-
"""查看20260803信号"""
import sqlite3
conn = sqlite3.connect(r'D:\mystock\cache_daily\tail_backtest_tdx.db')
rows = conn.execute('''
  SELECT ts_code, name, theme, total_score, attack_score, structure_score,
         position_score, theme_score, tech_score, trap_penalty, k_filtered
  FROM tail_backtest
  WHERE signal_date = 20260803
  ORDER BY total_score DESC
''').fetchall()
print(f'20260803信号共{len(rows)}只:')
print(f'{"代码":<12}{"名称":<10}{"主题":<12}{"总分":>5}{"攻击":>5}{"结构":>5}{"位置":>5}{"主题":>5}{"技分":>5}{"诱多":>5}{"K":>3}')
print('-' * 80)
for r in rows:
    print(f'{r[0]:<12}{r[1]:<10}{r[2]:<12}{r[3]:>5}{r[4]:>5}{r[5]:>5}{r[6]:>5}{r[7]:>5}{r[8]:>5}{r[9]:>5}{r[10]:>3}')
conn.close()