# -*- coding: utf-8 -*-
"""诊断2：SQL 直查——换手率0值占比、分位命中统计（快）"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from w7_second_wave_engine import CacheReader

reader = CacheReader()
conn = reader.conn

# 1. 换手率质量：全表 turnover_rate_f 的0/空值占比
q1 = """SELECT COUNT(*) total, SUM(CASE WHEN turnover_rate_f IS NULL OR turnover_rate_f<=0 THEN 1 ELSE 0 END) zero_cnt
        FROM stk_factor_pro WHERE trade_date>='20230101'"""
r1 = conn.execute(q1).fetchone()
print(f"== 换手率 turnover_rate_f 全表质量 ==")
print(f"  总行: {r1[0]}  0/空值: {r1[1]}  占比: {r1[1]/r1[0]:.1%}\n")

# 2. 按股票统计换手率0值占比（抽样200只）
q2 = """SELECT ts_code, COUNT(*) n, SUM(CASE WHEN turnover_rate_f IS NULL OR turnover_rate_f<=0 THEN 1 ELSE 0 END) z
        FROM stk_factor_pro WHERE trade_date>='20230101' GROUP BY ts_code"""
df2 = pd.read_sql_query(q2, conn)
df2['zero_ratio'] = df2['z'] / df2['n']
print("== 个股换手率0值占比分布 ==")
print(f"  股票总数: {len(df2)}")
for thr in (0.2, 0.5, 0.8):
    print(f"  0值占比>{thr:.0%} 的股票: {(df2['zero_ratio']>thr).sum()} 只 ({(df2['zero_ratio']>thr).mean():.1%})")

# 3. 最近60天窗口：每只股票是否有任一天换手率或量达到其自身P98
#    用 SQL 直接验证"候选多"到底因为什么
print("\n== 候选池膨胀来源分析 ==")
q3 = """SELECT COUNT(DISTINCT ts_code) FROM stk_factor_pro
        WHERE trade_date>='20260630'"""  # 近60天有交易的股票
n_active = conn.execute(q3).fetchone()[0]
print(f"  近60天有交易股票: {n_active}")

# 抽样30只，检查它们"最近60天最大换手/最大量 vs 全历史分位"
q4 = """SELECT ts_code, MAX(trade_date) md FROM stk_factor_pro WHERE trade_date<='20260828' GROUP BY ts_code"""
df4 = pd.read_sql_query(q4, conn)
df4 = df4[df4['md'] >= '20260601'].head(30)  # 抽30只近期活跃
print(f"\n== 抽样{len(df4)}只股票：全历史换手率分位（最近60天内最大值所在分位）==")
high_hist_turn = 0
high_hist_vol = 0
for _, row in df4.iterrows():
    code = row['ts_code']
    # 全历史换手率
    q5 = f"""SELECT turnover_rate_f FROM stk_factor_pro
             WHERE ts_code='{code}' AND trade_date>='20230101' AND trade_date<='20260628'
             AND turnover_rate_f>0"""
    hist = pd.to_numeric(conn.execute(q5).fetchall()).to_numpy() if False else np.array([x[0] for x in conn.execute(q5)])
    q6 = f"""SELECT turnover_rate_f FROM stk_factor_pro
             WHERE ts_code='{code}' AND trade_date>'20260628' AND trade_date<='20260828'
             AND turnover_rate_f>0"""
    recent = np.array([x[0] for x in conn.execute(q6)])
    if len(hist) < 50 or len(recent) == 0:
        continue
    rmax = recent.max()
    p = float(np.mean(hist <= rmax) * 100)
    if p >= 98:
        high_hist_turn += 1
print(f"  最近60天内最大换手达到全历史P98+的股票: {high_hist_turn}/{len(df4)}")
reader.close()
