"""诊断 theme_scores.db：门禁分档 / 分数分布 / 生命周期分布（None 安全）"""
import sqlite3
from collections import defaultdict, Counter

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
f = lambda v: float(v) if v is not None else 0.0

cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date")
dts = [r[0] for r in cur.fetchall()]

cur.execute("SELECT trade_date, MAX(trend_score), MAX(composite_score), MAX(sentiment_score), "
            "MAX(final_trade_score), MAX(days_strong) FROM theme_scores GROUP BY trade_date")
print('日期      max趋势 max综合 max情绪 max交易 max持续')
agg = defaultdict(list)
for d, t, c, s, fs, dsg in cur.fetchall():
    print(f'{d}  {f(t):>6.1f} {f(c):>7.1f} {f(s):>7.1f} {f(fs):>7.1f} {dsg if dsg is not None else "-":>6}')
    agg['t'].append(f(t)); agg['c'].append(f(c)); agg['s'].append(f(s))
print(f"峰值: 趋势={max(agg['t']):.1f} 综合={max(agg['c']):.1f} 情绪={max(agg['s']):.1f}")

cur.execute("SELECT lifecycle, COUNT(*) FROM theme_scores WHERE lifecycle IS NOT NULL GROUP BY lifecycle ORDER BY 2 DESC")
print('\n生命周期分布:', cur.fetchall())

cur.execute("SELECT days_strong, COUNT(*) FROM theme_scores GROUP BY days_strong ORDER BY 1")
print('days_strong 分布:', cur.fetchall())

# 每日头部主题的门禁输入特征
print('\n每日综合分第一名的门禁输入:')
cur.execute("""SELECT trade_date, theme, lifecycle, trend_score, composite_score, sentiment_score,
                      days_strong, fund_acc, up_ratio, zt_count, gate_tier, target_state, migration_score
               FROM theme_scores WHERE trade_date IN (%s) ORDER BY trade_date, rank""" % ','.join('?'*len(dts)), dts)
seen = set()
for row in cur.fetchall():
    d = row[0]
    if d in seen:
        continue
    seen.add(d)
    print(f"{d} {row[1]:<8} lc={row[2] or '-':<4} trend={f(row[3]):>5.1f} comp={f(row[4]):>5.1f} "
          f"sent={f(row[5]):>5.1f} days={row[6]} fund={f(row[7]):>5.1f} up={f(row[8]):>5.1f} "
          f"zt={row[9]} tier={row[10]} tgt={row[11] or '-'} mig={f(row[12]):>5.1f}")

# 区间内综合分 >=70 的主题数量（看 75 门槛是否可达）
cur.execute("SELECT COUNT(*) FROM theme_scores WHERE composite_score >= 75")
print('\n综合分>=75 的主题-日 记录数:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM theme_scores WHERE trend_score >= 75")
print('趋势分>=75 的记录数:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM theme_scores WHERE trend_score >= 70")
print('趋势分>=70 的记录数:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM theme_scores WHERE fund_acc >= 40")
print('fund_acc>=40 的记录数:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM theme_scores WHERE up_ratio >= 55")
print('up_ratio>=55 的记录数:', cur.fetchone()[0])
conn.close()
