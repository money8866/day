# -*- coding: utf-8 -*-
"""临时：HVT-BULL 第一梯队（hvt_bull_fe / hvt_bull_fe_watch）历史跟踪收益率分类分析
口径同 W7 分类分析：收益基准=选股日收盘价，第N日=选股日之后第N个交易日收盘。"""
import sys, sqlite3, json
sys.stdout.reconfigure(encoding='utf-8')

db = sqlite3.connect(r'd:\mystock\solo\picks_db\stock_picks.db')
db.row_factory = sqlite3.Row
rows = db.execute("""
    SELECT p.pick_date, p.strategy_id, p.ts_code, p.stock_name, p.close, p.action, p.score,
           p.stop_price, p.target_price, p.indicators,
           t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
           t.max_gain, t.max_drawdown, t.status
    FROM stock_pick p LEFT JOIN pick_tracking t
      ON t.pick_date=p.pick_date AND t.strategy_id=p.strategy_id AND t.ts_code=p.ts_code
    WHERE p.strategy_id IN ('hvt_bull_fe','hvt_bull_fe_watch','hvt_bull_te','hvt_bull_mh','hvt_bull_rb')
    ORDER BY p.strategy_id, p.pick_date""").fetchall()

recs = []
for r in rows:
    ind = {}
    try:
        ind = json.loads(r['indicators'] or '{}')
    except Exception:
        pass
    recs.append(dict(
        pick_date=r['pick_date'], sid=r['strategy_id'], code=r['ts_code'], name=r['stock_name'],
        close=r['close'], action=r['action'], stop=r['stop_price'], tgt=r['target_price'],
        exec_state=ind.get('execution_state','') or '', nda=ind.get('next_day_action','') or '',
        exec_score=ind.get('execution_score'), fe=ind.get('fe_score'),
        entry=ind.get('entry_score'), hvt_grade=ind.get('hvt_grade','') or '',
        pos=ind.get('position_size','') or '',
        r1=r['ret_1d'], r3=r['ret_3d'], r5=r['ret_5d'], r10=r['ret_10d'], r20=r['ret_20d'],
        maxg=r['max_gain'], maxdd=r['max_drawdown'], status=r['status'] or ''))

print(f"总记录: {len(recs)}")
from collections import Counter
print('按策略:', dict(Counter(x['sid'] for x in recs)))
print('日期范围:', min(x['pick_date'] for x in recs), '~', max(x['pick_date'] for x in recs))
# 目标价/止损价覆盖
for sid in ('hvt_bull_fe','hvt_bull_fe_watch','hvt_bull_te','hvt_bull_mh','hvt_bull_rb'):
    sub=[x for x in recs if x['sid']==sid]
    if sub:
        print(f"  {sid}: n={len(sub)} stop_price空={sum(1 for x in sub if not x['stop'])} "
              f"target_price空={sum(1 for x in sub if not x['tgt'])}")

def _agg(sub, label):
    if not sub:
        print(f"  {label:<38} n=0")
        return
    n=len(sub)
    def stat(k):
        vals=[x[k] for x in sub if x[k] is not None]
        if not vals: return f"n=0"
        m=sum(vals)/len(vals)
        win=sum(1 for v in vals if v>0)/len(vals)*100
        return f"{m:+.2f}%(胜{win:.0f}%,n={len(vals)})"
    def pl_ratio(k):
        vals=[x[k] for x in sub if x[k] is not None]
        wins=[v for v in vals if v>0]; losses=[v for v in vals if v<0]
        if not wins or not losses: return '-'
        aw=sum(wins)/len(wins); al=abs(sum(losses)/len(losses))
        return f"{aw/al:.2f}"
    stop_n=sum(1 for x in sub if x['status']=='STOP_HIT')
    tgt_n=sum(1 for x in sub if x['status']=='TARGET_HIT')
    mdd=[x['maxdd'] for x in sub if x['maxdd'] is not None]
    mg=[x['maxg'] for x in sub if x['maxg'] is not None]
    print(f"  {label:<38} n={n:<3} 1d:{stat('r1')} 3d:{stat('r3')} 5d:{stat('r5')} "
          f"10d:{stat('r10')} | 盈亏比3d={pl_ratio('r3')} 止损率={stop_n/n*100:.0f}% "
          f"达标率={tgt_n/n*100:.0f}% maxDD均值={sum(mdd)/len(mdd):.1f}%" if mdd else "")

print("\n=== ① 按策略 ===")
for sid in ('hvt_bull_fe','hvt_bull_fe_watch','hvt_bull_te','hvt_bull_mh','hvt_bull_rb'):
    _agg([x for x in recs if x['sid']==sid], sid)

print("\n=== ② 第一梯队(fe+fe_watch) 按次日动作 next_day_action ===")
fe=[x for x in recs if x['sid'] in ('hvt_bull_fe','hvt_bull_fe_watch')]
from collections import defaultdict
g=defaultdict(list)
for x in fe: g[x['nda'] or '(空)'].append(x)
for k in sorted(g, key=lambda k:-len(g[k])):
    _agg(g[k], k)

print("\n=== ③ 第一梯队 按执行状态 execution_state ===")
g=defaultdict(list)
for x in fe: g[x['exec_state'] or '(空)'].append(x)
for k in sorted(g, key=lambda k:-len(g[k])):
    _agg(g[k], k)

print("\n=== ④ 第一梯队 按A/B级（报告口径 FE≥70=A） ===")
g=defaultdict(list)
for x in fe:
    try: f_=float(x['fe'] or 0)
    except: f_=0
    g['A级(FE>=70)' if f_>=70 else 'B级(FE<70)'].append(x)
for k in sorted(g):
    _agg(g[k], k)

print("\n=== ⑤ 第一梯队 按HVT等级 ===")
g=defaultdict(list)
for x in fe: g[x['hvt_grade'] or '(空)'].append(x)
for k in sorted(g):
    _agg(g[k], k)

print("\n=== ⑥ 第一梯队 按执行分档（exec_score） ===")
g=defaultdict(list)
for x in fe:
    try: s=float(x['exec_score'] or 0)
    except: s=0
    g['≥75(可执行门)' if s>=75 else ('70~75' if s>=70 else '<70')].append(x)
for k in ('≥75(可执行门)','70~75','<70'):
    _agg(g[k], k)

print("\n=== ⑦ 可执行动作细分（BUY/BUY_ON_CONFIRM 只看 fe 正式档 vs watch 档） ===")
g=defaultdict(list)
for x in fe:
    if x['nda'] in ('BUY','BUY_ON_CONFIRM'):
        g[f"{x['nda']}@{x['sid']}"].append(x)
    elif x['nda'] in ('NO_CHASE','WAIT_PULLBACK'):
        g[f"{x['nda']}@{x['sid']}"].append(x)
for k in sorted(g):
    _agg(g[k], k)

print("\n=== ⑧ 逐条明细（第一梯队, 已有3日及以上收益的） ===")
print(f"{'日期':<9}{'代码':<11}{'名称':<9}{'动作':<16}{'exec':<14}{'FE':>5} {'1d':>7} {'3d':>7} {'5d':>7} {'10d':>7} {'maxG':>6} {'maxDD':>6} 状态")
for x in sorted(fe, key=lambda x:(x['nda'],x['pick_date'])):
    if x['r3'] is None and x['r1'] is None: continue
    print(f"{x['pick_date']:<9}{x['code']:<11}{x['name']:<9}{(x['nda'] or '-'):<16}{(x['exec_state'] or '-'):<14}"
          f"{x['fe'] or 0:>5.0f} {x['r1'] if x['r1'] is not None else float('nan'):>7.2f} "
          f"{x['r3'] if x['r3'] is not None else float('nan'):>7.2f} "
          f"{x['r5'] if x['r5'] is not None else float('nan'):>7.2f} "
          f"{x['r10'] if x['r10'] is not None else float('nan'):>7.2f} "
          f"{x['maxg'] if x['maxg'] is not None else float('nan'):>6.1f} "
          f"{x['maxdd'] if x['maxdd'] is not None else float('nan'):>6.1f} {x['status']}")
db.close()
