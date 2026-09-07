import sqlite3
c = sqlite3.connect('report_daily/theme_scores.db')
cols = [r[1] for r in c.execute('PRAGMA table_info(theme_scores)')]
print('新列存在:', all(x in cols for x in ['lifecycle', 'gate_tier', 'days_strong', 'gate_cap_amt', 'gate_feat']))
q = "SELECT theme,lifecycle,gate_tier,days_strong,gate_cap_amt,gate_feat,trade_action,position_pct FROM theme_scores WHERE trade_date='20260904' ORDER BY gate_tier DESC, final_trade_score DESC"
for r in c.execute(q):
    if r[2] != 'NONE' or r[0] in ('传媒', '游戏', '消费'):
        print(r)
q2 = "SELECT gate_tier,COUNT(*) FROM theme_scores WHERE trade_date='20260904' GROUP BY gate_tier"
print('tier分布:', dict(c.execute(q2).fetchall()))
