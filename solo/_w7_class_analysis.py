# -*- coding: utf-8 -*-
"""W7 二波/突破当日买点（w7_hvt）已跟踪信号 —— 按「信号分支 × 执行状态(w7_status)」分类收益率分析。

口径：
  - 仅取 stock_pick JOIN pick_tracking（已跟踪）记录；收益基准 = 选股日收盘价（前复权），
    ret_Nd = 之后第 N 个交易日收盘相对基准涨幅（%）；max_gain/max_drawdown = 跟踪窗口内
    最高/最低价相对基准的最大浮盈/浮亏（%）。
  - 信号分支  = stock_pick.signal（状态机分支）
  - 执行状态  = indicators.w7_status（20261001 三状态执行分层：EXECUTION / EXECUTION_WAIT_VOLUME /
                TRIGGER_WATCH / PULLBACK_WATCH；RIGHT_BOTTOM 结构未失效即 EXECUTION）
  - 买点档位  = action 字段「·」后的后缀（CONFIRMED / WATCH / T120_ROCKET，补充维度）
"""
import json
import sqlite3
import pandas as pd

pd.set_option('display.unicode.east_asian_width', True)
pd.set_option('display.width', 260)
pd.set_option('display.max_columns', 60)

DB = r'd:\mystock\solo\picks_db\stock_picks.db'
HZ = (1, 3, 5, 10, 20)

conn = sqlite3.connect(DB)
df = pd.read_sql_query("""
SELECT p.pick_date, p.ts_code, p.stock_name, p.close, p.pct_chg,
       p.signal, p.action, p.score, p.stop_price, p.indicators,
       t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
       t.max_gain, t.max_drawdown, t.status, t.last_date
FROM stock_pick p
JOIN pick_tracking t
  ON t.pick_date = p.pick_date AND t.strategy_id = p.strategy_id AND t.ts_code = p.ts_code
WHERE p.strategy_id = 'w7_hvt'
ORDER BY p.pick_date
""", conn)


def _w7status(s):
    try:
        return (json.loads(s or '{}') or {}).get('w7_status') or '—(旧记录未分层)'
    except Exception:
        return '—(旧记录未分层)'


df['signal'] = df['signal'].astype(str)
df['w7_status'] = df['indicators'].map(_w7status)
df['tier'] = df['action'].astype(str).str.split('·').str[-1]


def _agg(g, label):
    r = {'分类': label, 'n': len(g)}
    for h in HZ:
        v = g[f'ret_{h}d'].dropna()
        r[f'{h}日_n'] = len(v)
        r[f'{h}日_均%'] = round(v.mean(), 2) if len(v) else None
        r[f'{h}日_胜%'] = round((v > 0).mean() * 100, 1) if len(v) else None
    r['最大浮盈%'] = round(g['max_gain'].mean(), 2) if g['max_gain'].notna().any() else None
    r['最大浮亏%'] = round(g['max_drawdown'].mean(), 2) if g['max_drawdown'].notna().any() else None
    r['盈亏比'] = round(r['最大浮盈%'] / r['最大浮亏%'], 2) if r['最大浮亏%'] else None
    r['止损%'] = round((g['status'] == 'STOP_HIT').mean() * 100, 1)
    r['进行中'] = int((g['status'] == 'ACTIVE').sum())
    return r


def _table(df, key, title, order=None):
    keys = order or sorted(df[key].unique())
    rows = [_agg(df, '全部')] + [_agg(df[df[key] == k], str(k)) for k in keys if (df[key] == k).any()]
    out = pd.DataFrame(rows)
    print(f'\n===== {title} =====')
    print(out.to_string(index=False))
    return out


print(f"样本区间 {df.pick_date.min()} ~ {df.pick_date.max()}；已跟踪 {len(df)} 条 / {df.ts_code.nunique()} 只")
print(f"止损 {int((df.status=='STOP_HIT').sum())} / 止盈 {int((df.status=='TARGET_HIT').sum())} / "
      f"进行中 {int((df.status=='ACTIVE').sum())} / 到期 {int((df.status=='EXPIRED').sum())}")
layered = df[df['w7_status'] != '—(旧记录未分层)']
print(f"含执行状态(w7_status)分层记录的区间：{layered.pick_date.min() if len(layered) else '无'} ~ "
      f"{layered.pick_date.max() if len(layered) else '无'}（{len(layered)} 条）")

_table(df, 'signal', '按信号分支（signal）')
_table(df, 'w7_status', '按执行状态（w7_status）')
_table(df, 'tier', '按买点档位（action 后缀，补充）')
_table(df, 'status', '按跟踪状态（pick_tracking.status）')

print('\n===== 信号分支 × 执行状态 交叉 =====')
rows = [_agg(g, f'{s} · {t}') for (s, t), g in
        df.groupby(['signal', 'w7_status'], sort=True) if len(g)]
print(pd.DataFrame(rows).to_string(index=False))

print('\n===== 分层区间内（20261001 起，即 w7_status 有效样本）=====')
_table(layered, 'w7_status', '分层区间 · 按执行状态')
_table(layered, 'signal', '分层区间 · 按信号分支')
