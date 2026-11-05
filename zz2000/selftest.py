# -*- coding: utf-8 -*-
"""ZZ2000 规则自检：验证数据完整性约束与「不推断」口径。

用法： python -m zz2000.selftest
"""
from __future__ import annotations
import os
import sys

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zz2000 import config as C          # noqa: E402
from zz2000 import data as D            # noqa: E402
from zz2000 import strategy as S        # noqa: E402
from zz2000 import daily as RD          # noqa: E402


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('  ' + detail if detail else ''))
    return bool(cond)


def main() -> int:
    D.init_cache()
    code = D.meta_get('selected_etf')
    name = D.meta_get('selected_etf_name') or ''
    if not code or code == 'UNKNOWN':
        print('FAIL  代表 ETF 未核验')
        return 1
    tdays = D.trading_days()
    date = tdays[-1] if tdays and tdays[-1] <= D.today_str() else None
    # 取缓存里真正有数据的最近交易日
    with D.rw_connect() as con:
        r = con.execute('''SELECT i.trade_date FROM index_daily i JOIN etf_daily e
                           ON e.trade_date=i.trade_date AND e.ts_code=?
                           WHERE i.ts_code=? ORDER BY i.trade_date DESC LIMIT 1''',
                        (code, C.INDEX_CODE)).fetchone()
    date = r[0]
    prev = tdays[tdays.index(date) - 1]
    ctx = RD.build_ctx(date, prev, code, name)
    results = []

    # 1 正常日
    cur = S.compute(ctx)
    results.append(check('正常日：计算成功且总敞口为数值',
                         cur['ok'] and isinstance(cur['total'], (int, float))))
    results.append(check('正常日：三仓合计不超过 100%',
                         cur['total'] is not None and cur['total'] <= C.MAX_EXPOSURE))
    results.append(check('正常日：动作在允许集合内',
                         S.decide(cur, {'ok': True, 'total': cur['total'],
                                        'signal_key': cur['signal_key']})['action']
                         in ('BUY', 'HOLD', 'REDUCE', 'NO TRADE')))

    # 2 价格数据缺失（截断 ETF 行情）
    bad = dict(ctx)
    bad['bars'] = ctx['bars'].iloc[:-1].reset_index(drop=True)
    bad['integrity_missing'] = [f'ETF 缺 {date} 当日行情（自检模拟）']
    c2 = S.compute(bad)
    results.append(check('行情缺失：不产出任何精确仓位',
                         not c2['ok'] and all(v is None for v in c2['sleeves'].values())
                         and c2['total'] is None))
    results.append(check('行情缺失：动作 NO TRADE 且不输出关键价位',
                         c2['action'] == 'NO TRADE' and c2['levels'] == {}))
    results.append(check('行情缺失：明确列出无法确认的结论', len(c2['unknowns']) > 0))

    # 3 情绪指标 UNKNOWN（不推断）
    soft = dict(ctx)
    soft['sent'] = {'date': date, 'missing': ['全市场涨跌家数/涨停跌停数'], 'source': []}
    soft['sent_prev'] = {'date': prev, 'missing': [], 'source': []}
    soft['yzt'] = {'date': date, 'prev_date': prev, 'status': 'UNKNOWN',
                   'missing': [f'{prev} 涨停池(Tushare limit_list_d)']}
    soft['sentiment_missing'] = ['自检：情绪指标全部 UNKNOWN']
    c3 = S.compute(soft)
    results.append(check('情绪 UNKNOWN：价格结论仍可算，但过滤标 UNKNOWN',
                         c3['ok'] and any(v == 'UNKNOWN' for v in c3['gates'].values())))
    results.append(check('情绪 UNKNOWN：unknowns 列明并禁止 BUY',
                         len(c3['unknowns']) > 0 and
                         S.decide(c3, {'ok': True, 'total': c3['total'] - 10,
                                       'signal_key': 'x'})['action'] != 'BUY'))

    # 4 无前值基线
    d4 = S.decide(cur, None)
    results.append(check('无前值：变化 UNKNOWN / 不推断 / NO TRADE',
                         d4['delta'] is None and d4['action'] == 'NO TRADE'))

    # 5 非交易日口径
    results.append(check('交易日历可判定休市日',
                         D.is_open('20261101') == 0 and D.is_open('20261104') == 1))

    print('\n%d/%d passed' % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
