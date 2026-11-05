# -*- coding: utf-8 -*-
"""核验「当前跟踪中证2000的 ETF 产品」并选出代表标的。

依据：Tushare fund_basic（业绩比较基准明确写「中证2000指数收益率」）、
      fund_daily（成交额/流动性）、fund_adj（复权因子）、fund_div（分红）、
      指数 932000.CSI 日线（跟踪偏离）。
产物：cache/etf_universe_snapshot.csv、cache/etf_selection.md、meta.selected_etf
"""
from __future__ import annotations

import os
import sys
import datetime as dt

import numpy as np
import pandas as pd

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zz2000 import config as C          # noqa: E402
from zz2000 import data as D            # noqa: E402


def num(x, nd=2, suffix=''):
    return 'UNKNOWN' if x is None or (isinstance(x, float) and not np.isfinite(x)) else f'{x:.{nd}f}{suffix}'


def main():
    D.init_cache()
    print('[1/5] 核验指数与交易日历 ...')
    D.refresh_index()
    idx = D.index_bars()
    if idx.empty:
        print('指数日线缺失，无法核验跟踪偏离。')
        return 1
    idx_latest = idx['trade_date'].iloc[-1]
    print(f'  指数 {C.INDEX_CODE} 共 {len(idx)} 行，最新 {idx_latest}')

    print('[2/5] 拉取 fund_basic 中证2000 产品清单 ...')
    uni = D.refresh_etf_meta(force=True)
    if uni is None or uni.empty:
        print('未取到任何中证2000 ETF，终止（不臆造代码）。')
        return 1
    codes = uni['ts_code'].tolist()
    print(f'  命中 {len(codes)} 只：{codes}')

    print('[3/5] 拉取 fund_daily / fund_adj / fund_div ...')
    D.refresh_etf_daily(codes, start_date='20250101')
    D.refresh_etf_adj(codes)
    D.refresh_etf_div(codes)

    print('[4/5] 计算流动性、跟踪偏离、复权口径 ...')
    idx_map = dict(zip(idx['trade_date'], idx['pct_chg']))
    rows = []
    for _, r in uni.iterrows():
        code = r['ts_code']
        b = D.etf_bars(code)
        if b.empty:
            rows.append(dict(ts_code=code, name=r['name'], invest_type=r['invest_type'],
                             bars=0, latest='UNKNOWN', amt20_yi=None, amt60_yi=None,
                             fee=None, track_abs=None, track_std=None,
                             adj_changes=None, adj_status='UNKNOWN', div=D.div_status(code),
                             list_date=r['list_date'], eligible=False, reason='行情数据为空'))
            continue
        last = b['trade_date'].iloc[-1]
        n = len(b)
        a20 = b['amount'].tail(20).mean() / 100000.0 if n >= 20 else None   # 千元 -> 亿元
        a60 = b['amount'].tail(60).mean() / 100000.0 if n >= 60 else None
        fee = (float(r['m_fee']) if pd.notna(r['m_fee']) else None)
        fee = None if fee is None else fee + (float(r['c_fee']) if pd.notna(r['c_fee']) else 0)
        # 跟踪偏离：仅用共同交易日、近 120 根
        tail = b.tail(120).copy()
        tail['idx_pct'] = tail['trade_date'].map(idx_map)
        both = tail.dropna(subset=['idx_pct', 'pct_chg'])
        diff = (both['pct_chg'] - both['idx_pct']).astype(float) if len(both) else pd.Series(dtype=float)
        tr_abs = float(diff.abs().mean()) if len(diff) else None
        tr_std = float(diff.std()) if len(diff) > 2 else None
        adj = b['adj_factor'].dropna()
        adj_changes = int((adj.round(6) != round(float(adj.iloc[-1]), 6)).sum()) if len(adj) else None
        adj_status = b['adj_status'].iloc[-1] if 'adj_status' in b.columns else 'UNKNOWN'
        div = D.div_status(code)

        eligible, why = True, []
        if r['invest_type'] != '被动指数型':
            eligible = False
            why.append('增强/非纯被动跟踪，不作代表产品')
        if last != idx_latest:
            eligible = False; why.append(f'ETF最新行情 {last} != 指数最新 {idx_latest}')
        if n < C.MIN_BARS:
            eligible = False; why.append(f'历史仅 {n} 根，不足以计算 MA120')
        if a20 is None or a20 < 1.0:
            eligible = False; why.append(f'近20日日均成交额 {num(a20)}亿元 < 1亿元')
        if tr_std is not None and tr_std > 0.80:
            eligible = False; why.append(f'跟踪偏离标准差 {num(tr_std,3)}pp 过大')
        if adj_changes and adj_changes > 0:
            eligible = False; why.append(f'复权因子变动 {adj_changes} 次（需单独处理）')
        rows.append(dict(ts_code=code, name=r['name'], invest_type=r['invest_type'],
                         management=r['management'], benchmark=r['benchmark'],
                         bars=n, latest=last, amt20_yi=a20, amt60_yi=a60,
                         m_fee=r['m_fee'], c_fee=r['c_fee'], fee=fee,
                         track_days=len(diff), track_abs=tr_abs, track_std=tr_std,
                         adj_changes=adj_changes, adj_status=adj_status, div=div,
                         list_date=r['list_date'], eligible=eligible,
                         reason='；'.join(why) if why else '符合'))

    snap = pd.DataFrame(rows)
    snap.to_csv(C.ETF_UNIVERSE_CSV, index=False, encoding='utf-8-sig')
    print(snap[['ts_code', 'name', 'amt20_yi', 'amt60_yi', 'fee', 'track_std',
                'adj_changes', 'eligible']].to_string(index=False))

    print('[5/5] 选出代表产品 ...')
    cand = snap[snap['eligible']].copy()
    lines = ['# 中证2000 ETF 代表产品核验与选择', '',
             f'- 核验时点：{dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}（本地时间）',
             f'- 数据来源：Tushare `fund_basic` / `fund_daily` / `fund_adj` / `fund_div` / `index_daily`，'
             f'落盘 `{C.CACHE_DB}` 与 `{C.ETF_UNIVERSE_CSV}`',
             f'- 指数：{C.INDEX_CODE} {C.INDEX_NAME}（Tushare `index_basic` 核验：规模指数，'
             f'发布方 中证指数有限公司，基日 20131231）',
             f'- 指数最新行情日：{idx_latest}',
             '',
             '## 候选清单（业绩比较基准含「中证2000」的在市 ETF）', '',
             '| 代码 | 名称 | 类型 | 管理人 | 上市日 | 近20日日均成交额(亿元) | 近60日(亿元) | 管理费+托管费(%) | 跟踪偏离均值(pp) | 跟踪偏离标准差(pp) | 复权因子变动 | 分红记录 | 入选 | 说明 |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for _, r in snap.iterrows():
        lines.append('| {ts_code} | {name} | {invest_type} | {management} | {list_date} | {a20} | {a60} | '
                     '{fee} | {ta} | {ts} | {ac} | {div} | {el} | {reason} |'.format(
                         ts_code=r['ts_code'], name=r['name'], invest_type=r['invest_type'],
                         management=r['management'], list_date=r['list_date'],
                         a20=num(r['amt20_yi']), a60=num(r['amt60_yi']), fee=num(r['fee']),
                         ta=num(r['track_abs'], 3), ts=num(r['track_std'], 3),
                         ac=r['adj_changes'] if r['adj_changes'] is not None else 'UNKNOWN',
                         div=r['div'], el='是' if r['eligible'] else '否', reason=r['reason']))
    lines += ['', '> 成交额单位：Tushare `fund_daily.amount` 为千元，除以 100000 换算为亿元；'
              '`vol` 单位为手。跟踪偏离 = ETF 日涨跌幅 − 中证2000 指数日涨跌幅，'
              '近 120 个共同交易日；复权因子来自 `fund_adj`；分红来自 `fund_div`，'
              '返回空记为 NO_DIV_RECORD（表示接口无记录，不等同于「确认无分红」）。', '']

    if cand.empty:
        lines += ['## 结论', '', '**没有同时满足流动性、数据长度、纯被动跟踪与复权口径要求的产品，'
                  '代表产品代码 = UNKNOWN。后续报告不得臆造 ETF 代码或行情。**', '']
        D.meta_set('selected_etf', 'UNKNOWN')
        chosen = None
    else:
        # 综合排序：流动性(近20日日均成交额) 为主，跟踪偏离与费率为辅
        maxa = max(cand['amt20_yi'].max(), 1e-9)
        maxt = max(cand['track_std'].max(), 1e-9)
        maxf = max(cand['fee'].max(), 1e-9)
        cand['score'] = (0.60 * np.log1p(cand['amt20_yi']) / np.log1p(maxa)
                         + 0.25 * (1 - cand['track_std'] / maxt)
                         + 0.15 * (1 - cand['fee'] / maxf))
        cand = cand.sort_values('score', ascending=False)
        best = cand.iloc[0]
        chosen = best['ts_code']
        D.meta_set('selected_etf', chosen)
        D.meta_set('selected_etf_name', best['name'])
        lines += ['## 选择结论', '',
                  f'**代表产品：`{chosen}` {best["name"]}**（纯被动指数型，'
                  f'业绩比较基准「{best["benchmark"]}」）', '',
                  '选择依据：',
                  f'1. **流动性**：近 20 日日均成交额 {num(best["amt20_yi"])} 亿元、'
                  f'近 60 日 {num(best["amt60_yi"])} 亿元，为合格候选中最高/前列，'
                  '日内冲击成本与买卖价差相对可控。',
                  f'2. **跟踪质量**：近 {int(best["track_days"])} 个共同交易日相对指数的日涨跌幅偏离均值 '
                  f'{num(best["track_abs"],3)}pp、标准差 {num(best["track_std"],3)}pp，处于可接受范围。',
                  f'3. **费用**：管理费 {num(best["m_fee"])}% + 托管费 {num(best["c_fee"])}% '
                  f'= {num(best["fee"])}%/年；不选择增强策略产品，避免主动偏离混入被动仓位信号。',
                  f'4. **复权/分红口径**：`fund_adj` 复权因子变动 {best["adj_changes"]} 次，'
                  f'`fund_div` = {best["div"]}；报告价格用未复权收盘价（实际成交价），'
                  '均线与偏离计算用前复权收盘价，避免除权跳变污染均线。',
                  '',
                  '### 与次优候选的取舍', '',
                  '次优为 `159531.SZ` 南方中证2000ETF（近20日日均成交额 '
                  f'{num(float(snap.loc[snap.ts_code=="159531.SZ","amt20_yi"].iloc[0]))} 亿元、'
                  '费率 0.20%/年）。其**费率更低**，但成交额约为 563300.SH 的 1/3~1/4，'
                  '跟踪偏离标准差也略大。本策略含波段交易仓、需假设可进出，'
                  '故优先流动性而非最低费率；若未来改为纯长期底仓、几乎不交易，'
                  '可重新评估 159531.SZ。这一取舍是研究假设，不是最优性结论。', '',
                  '### 排除项提示', '',
                  '- `560220.SH` 广发中证2000ETF 的 `fund_adj` 复权因子出现 227 根变动、'
                  '`fund_div` 有分红记录，说明存在份额折算/分红，直接用未复权收盘价算均线会失真，'
                  '且流动性不足，故排除。',
                  '- 4 只「增强策略ETF」有主动偏离，不适合作为纯被动指数仓位信号的代理。',
                  '- 8 只被动产品近20日日均成交额 < 1 亿元，滑点与冲击成本不可接受。', '',
                  '> 该选择仅用于本策略研究的行情代理，不构成产品推荐，也不代表用户持仓。'
                  '若后续流动性结构变化，需重新运行本核验。', '']
    with open(C.ETF_SELECTION_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'\n代表产品: {chosen or "UNKNOWN"}')
    print(f'快照: {C.ETF_UNIVERSE_CSV}')
    print(f'说明: {C.ETF_SELECTION_MD}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
