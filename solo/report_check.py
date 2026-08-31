# -*- coding: utf-8 -*-
"""交叉验证：定位 BUY 池全输与 action 分布脱节的根因（决策时点字段泄漏假设）。

假设：decision_point=T0（lag=0）时 update_tracking early return，
ev_dt 残留全跟踪（含未来）字段 → 未来 breakout_date/pb_verdict 泄漏 → 误判 PULLBACK_BUY。
验证点：PULLBACK_BUY/BUY 是否全部集中在 T0 决策点（突破日决策点上 pb_verdict 恒为 NA，
PULLBACK_BUY 理论上不可能出现）。
"""
import pandas as pd

CSV = r'd:\mystock\solo\report_daily\te_backtest_events_20250101_20260828.csv'
df = pd.read_csv(CSV, encoding='utf-8-sig')
print('总事件:', len(df))

print('\n[1] decision_point 分布:')
print(df['decision_point'].value_counts().to_string())

print('\n[2] action × decision_point 交叉表:')
print(pd.crosstab(df['next_day_action'], df['decision_point']).to_string())

buy = df[df['next_day_action'].isin(['BUY', 'BUY_ON_CONFIRM'])]
print('\n[3] BUY 样本 n=%d  decision_point=%s  execution_state=%s' % (
    len(buy), dict(buy['decision_point'].value_counts()), dict(buy['execution_state'].value_counts())))
cols = ['ts_code', 'name', 'signal_date', 'decision_date', 'decision_point', 'v3_state',
        'execution_score', 'gap_pct', 'r20', 'er20', 'r60']
print(buy[cols].to_string(index=False))

print('\n[4] execution_state=PULLBACK_BUY × decision_point（关键证据）:')
pb = df[df['execution_state'] == 'PULLBACK_BUY']
print(pd.crosstab(pb['next_day_action'], pb['decision_point']).to_string())

print('\n[5] T0 决策点（泄漏影响面）execution_state 分布:')
t0 = df[df['decision_point'] == 'T0']
print(t0['execution_state'].value_counts().to_string())

print('\n[6] 75-85 分档 × decision_point × action:')
mid = df[(df['execution_score'] >= 75) & (df['execution_score'] < 85)]
print(pd.crosstab(mid['decision_point'], mid['next_day_action']).to_string())

print('\n[7] platform_breakout=True 占比（T0 决策点上为真 = 突破发生在未来的证据）:')
bo = df[df['decision_point'] == 'BREAKOUT']
print('  T0:       %.1f%% (n=%d)' % ((t0['platform_breakout'] == True).mean() * 100, len(t0)))
print('  BREAKOUT: %.1f%% (n=%d)' % ((bo['platform_breakout'] == True).mean() * 100, len(bo)))

print('\n[8] 对照：BREAKOUT 决策点（重放正确路径）的 action 分布 vs T0 决策点:')
print('BREAKOUT:')
print(bo['next_day_action'].value_counts().to_string())
print('T0:')
print(t0['next_day_action'].value_counts().to_string())

print('\n[9] v3_state=PRIMARY_BUY 事件的 decision_point 分布（READY_BUY 门为何零产出）:')
pbuy = df[df['v3_state'] == 'PRIMARY_BUY']
print('n=%d' % len(pbuy))
print(pd.crosstab(pbuy['decision_point'], pbuy['next_day_action']).to_string())
