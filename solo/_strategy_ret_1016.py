# -*- coding: utf-8 -*-
"""20261016 盘后：各策略收益率综合分析
================================================
口径: 收益基准=选股日收盘价; 第N日=选股日之后第N个交易日收盘 (stock_pick_db 全局口径)
输出: ① 全策略汇总 ② 近10日信号 vs 全样本 ③ 质量门升级后信号表现 ④ 1016 最新信号
"""
import sys, sqlite3, json
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
DB = r'd:\mystock\solo\picks_db\stock_picks.db'


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def load():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT p.pick_date, p.strategy_id, p.ts_code, p.stock_name, p.close,
               p.action, p.signal, p.score, p.indicators,
               t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
               t.max_gain, t.max_drawdown, t.status
        FROM stock_pick p LEFT JOIN pick_tracking t
          ON t.pick_date=p.pick_date AND t.strategy_id=p.strategy_id AND t.ts_code=p.ts_code
        ORDER BY p.strategy_id, p.pick_date""").fetchall()
    c.close()
    out = []
    for r in rows:
        out.append(dict(date=r['pick_date'], sid=r['strategy_id'], code=r['ts_code'],
                        name=r['stock_name'], close=_f(r['close']), action=r['action'] or '',
                        signal=r['signal'] or '', r1=_f(r['ret_1d']), r3=_f(r['ret_3d']),
                        r5=_f(r['ret_5d']), r10=_f(r['ret_10d']), r20=_f(r['ret_20d']),
                        mg=_f(r['max_gain']), mdd=_f(r['max_drawdown']),
                        status=r['status'] or ''))
    return out


def line(sub, indent=2):
    n = len(sub)
    if not n:
        return ' ' * indent + 'n=0'
    def cell(k):
        v = [x[k] for x in sub if x[k] is not None]
        if not v:
            return '--'
        m = sum(v) / len(v)
        w = sum(1 for a in v if a > 0) / len(v) * 100
        return f'{m:+.2f}%({w:.0f}%,n={len(v)})'
    def pl(k):
        v = [x[k] for x in sub if x[k] is not None]
        w_ = [a for a in v if a > 0]
        l_ = [a for a in v if a < 0]
        return f'{sum(w_)/len(w_)/abs(sum(l_)/len(l_)):.2f}' if w_ and l_ else '--'
    stopn = sum(1 for x in sub if x['status'] == 'STOP_HIT')
    tgtn = sum(1 for x in sub if x['status'] == 'TARGET_HIT')
    dd = [x['mdd'] for x in sub if x['mdd'] is not None]
    return (' ' * indent +
            f'1d:{cell("r1"):<24} 3d:{cell("r3"):<24} 5d:{cell("r5"):<24} '
            f'10d:{cell("r10"):<24} | 盈亏比3d={pl("r3"):<5} 止损率={stopn/n*100:>3.0f}% '
            f'达标率={tgtn/n*100:>3.0f}% maxDD={sum(dd)/len(dd):.1f}%' if dd else '')


def main():
    recs = load()
    print('=' * 128)
    print('20261016 盘后 · 各策略收益率综合分析')
    print('=' * 128)
    print(f'样本: {len(recs)} 条信号, pick_date 20260825 ~ 20261016')
    print('口径: 收益基准=选股日收盘价; 胜率=收益>0占比; maxDD=选股后最大回撤\n')

    print('【① 全策略汇总（截至 1016 收盘回填）】')
    print('-' * 128)
    sids = sorted({x['sid'] for x in recs})
    g = defaultdict(list)
    for x in recs:
        g[x['sid']].append(x)
    for sid in sids:
        sub = g[sid]
        dates = sorted({x['date'] for x in sub})
        print(f'{sid:<20} n={len(sub):<4} {dates[0]}~{dates[-1]:<9}({len(dates)}期)')
        print(line(sub, 4))

    print('\n【② 近10个交易日信号（20261009 起）vs 各自全样本】')
    print('-' * 128)
    for sid in ('vsw', 'w7_hvt', 'hvt_bull_fe_watch', 'hvt_bull_te', 'fox_v4',
                'w7_t20', 'hvt_bull_mh', 'hvt_bull_rb', 'breakout_pool'):
        if sid not in g:
            continue
        recent = [x for x in g[sid] if x['date'] >= '20261009']
        print(f'{sid:<20} 近10日 n={len(recent)}')
        print(line(recent, 4))
        print(line(g[sid], 4), '  ← 全样本')
        print()

    print('【③ 质量门升级后的新口径信号表现】')
    print('-' * 128)
    # w7_hvt: 1014 起四道门(A/B/C); vsw: 1016 起四因子门; hvt_bull_fe_watch: 1015 起三道门
    new_w7 = [x for x in g.get('w7_hvt', []) if x['date'] >= '20261014']
    new_vsw = [x for x in g.get('vsw', []) if x['date'] >= '20261016']
    new_fe = [x for x in g.get('hvt_bull_fe_watch', []) if x['date'] >= '20261015']
    for label, sub in (('w7_hvt 门后(≥1014)', new_w7), ('vsw 门后(1016)', new_vsw),
                       ('fe_watch 门后(≥1015)', new_fe)):
        print(f'{label:<24} n={len(sub)}')
        print(line(sub, 4))
    print('\n对照：门前的旧口径（vsw 1009~1015 / w7 1009~1013）')
    print('vsw 旧口径(1009~1015):')
    print(line([x for x in g.get('vsw', []) if '20261009' <= x['date'] <= '20261015'], 4))
    print('w7_hvt 旧口径(1009~1013):')
    print(line([x for x in g.get('w7_hvt', []) if '20261009' <= x['date'] <= '20261013'], 4))

    print('\n【④ 20261016 最新落库信号】')
    print('-' * 128)
    for sid in sids:
        sub = sorted([x for x in g[sid] if x['date'] == '20261016'], key=lambda x: x['code'])
        if not sub:
            continue
        print(f'{sid:<20} n={len(sub)}: ' + ', '.join(f'{x["code"]} {x["name"]}' for x in sub))
    print('\n（1016 无信号的策略: w7_hvt / hvt_bull_te / hvt_bull_rb）')


if __name__ == '__main__':
    main()
