# -*- coding: utf-8 -*-
"""诊断3：SQL 快查换手率质量 + 近60天天量命中统计（针对抽样股票精确统计）"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import sqlite3

conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

# 1. 换手率质量
r1 = conn.execute("SELECT COUNT(*), SUM(CASE WHEN turnover_rate_f IS NULL OR turnover_rate_f<=0 THEN 1 ELSE 0 END) FROM stk_factor_pro WHERE trade_date>='20230101'").fetchone()
print(f"换手率全表: total={r1[0]} zero/null={r1[1]} 占比={r1[1]/r1[0]*100:.1f}%")

# 2. 近60天有交易的股票数
n_active = conn.execute("SELECT COUNT(DISTINCT ts_code) FROM stk_factor_pro WHERE trade_date>'20260628'").fetchone()[0]
print(f"近60天有交易股票: {n_active}")

# 3. 关键验证：单日换手率达到"该股全历史P98"的股票分布
#    先确认 turnover_rate_f 是否大部分时间正常。抽样100只近期活跃股
codes = [r[0] for r in conn.execute("SELECT DISTINCT ts_code FROM stk_factor_pro WHERE trade_date='20260828'").fetchall()]
print(f"8/28当日有交易股票: {len(codes)}")

# 统计这些股票中，有多少"最近60天内出现过换手率达到自身全历史P98"的日子
# 用近似：对每只股票，全历史P98换手 vs 近60天最大换手
import random
random.seed(42)
sample = random.sample(codes, min(100, len(codes)))
hit = 0
miss = 0
for code in sample:
    hist = conn.execute("SELECT turnover_rate_f FROM stk_factor_pro WHERE ts_code=? AND trade_date>='20230101' AND trade_date<='20260628' AND turnover_rate_f>0", (code,)).fetchall()
    recent = conn.execute("SELECT turnover_rate_f FROM stk_factor_pro WHERE ts_code=? AND trade_date>'20260628' AND turnover_rate_f>0", (code,)).fetchall()
    if len(hist) < 100 or len(recent) == 0:
        continue
    h = np.array([x[0] for x in hist], dtype=float)
    rmax = max(x[0] for x in recent)
    p98 = np.percentile(h, 98)
    if rmax >= p98:
        hit += 1
    else:
        miss += 1
print(f"抽样{hit+miss}只：近60天最大换手>=全历史P98的: {hit} ({hit/(hit+miss)*100:.0f}%)")

conn.close()
