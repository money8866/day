# -*- coding: utf-8 -*-
"""ZZ2000 每日收盘后仓位信号报告 —— 唯一入口

用法：
  python -m zz2000.daily                      # 生成报告（不推送）
  python -m zz2000.daily --push               # 生成 + 邮件推送（每日定时任务使用）
  python -m zz2000.daily --date 20261104      # 指定数据时点（回溯复核）
  python -m zz2000.daily --etf-code 159531.SZ
  python -m zz2000.daily --no-replay          # 不做前一交易日规则回溯基线

每次运行都会输出完整报告；信号无变化也明确写「信号不变」，不静默。
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import subprocess
import sys
import datetime as dt

import pandas as pd

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zz2000 import config as C          # noqa: E402
from zz2000 import data as D            # noqa: E402
from zz2000 import strategy as S        # noqa: E402


# ============================================================ 小工具
def fnum(v, nd=2, suffix='', pct_sign=False):
    if v is None or v == 'UNKNOWN':
        return 'UNKNOWN'
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x != x:
        return 'UNKNOWN'
    s = f'{x:+.{nd}f}' if pct_sign else f'{x:.{nd}f}'
    return s + suffix


def tone(v):
    if v is None or v == 'UNKNOWN':
        return 'unknown'
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 'neutral'
    return 'up' if x > 0 else ('down' if x < 0 else 'neutral')


LEVEL_LABELS = {
    'close': '今日收盘价', 'MA20': 'MA20（20日均线）', 'MA60': 'MA60（60日均线）',
    'MA120': 'MA120（120日均线）', 'today_high': '今日最高价',
    'today_low': '今日最低价', 'platform_high': '近10日平台高点',
}


def c(v, t=None):
    """单元格：可带涨跌色调（红涨绿跌）。"""
    return (str(v), t if t is not None else 'neutral')


def plain(s):
    """HTML 渲染时去掉 Markdown 强调标记，避免邮件里出现字面 ** 。"""
    return str(s).replace('**', '')


def cell_text(x):
    return x[0] if isinstance(x, tuple) else str(x)


def cell_tone(x):
    return x[1] if isinstance(x, tuple) else 'neutral'


# ============================================================ 上下文
def build_ctx(date: str, prev_date: str, code: str, name: str) -> dict:
    D.refresh_index()
    bars = D.etf_bars(code)
    bars = bars[bars['trade_date'] <= date].reset_index(drop=True)
    idx = D.index_bars()
    idx = idx[idx['trade_date'] <= date].reset_index(drop=True)
    sent = D.sentiment(date)
    sent_prev = D.sentiment(prev_date)
    yzt = D.yzt_performance(date, prev_date)
    cache = D.daily_cache_status(date)

    hard, soft = [], []
    if not cache['ok']:
        hard.append(f'当日个股日线缓存仅 {cache["rows"]} 行（<3000），全市场口径不可交叉核对')
    for m in sent['missing']:
        soft.append(f'{date} 情绪指标缺失：{m}')
    for m in sent_prev['missing']:
        soft.append(f'{prev_date} 情绪指标缺失：{m}（恐慌衰竭形态判定受影响）')
    for m in yzt.get('missing', []):
        soft.append(f'昨日涨停股次日表现：{m} = UNKNOWN')
    if yzt.get('status') != 'OK':
        soft.append('昨日涨停股次日表现整体 = UNKNOWN（不做推断）')

    adj_status = bars['adj_status'].iloc[-1] if (len(bars) and 'adj_status' in bars.columns) else 'UNKNOWN'
    if len(bars) and (bars['adj_status'] != 'OK').any():
        hard.append('ETF 前复权因子存在 PARTIAL/UNKNOWN 行，复权口径未确认')

    src = {
        '交易日历': ('Tushare trade_cal（本地缓存）', 'OK' if D.is_open(date) is not None else 'UNKNOWN'),
        '指数日线 932000.CSI': ('本地 index_daily_cache（Tushare）+ Tushare index_daily 增量',
                            'OK' if (not idx.empty and idx['trade_date'].iloc[-1] == date) else 'UNKNOWN'),
        'ETF 日线': ('Tushare fund_daily（本地缓存）',
                   'OK' if (not bars.empty and bars['trade_date'].iloc[-1] == date) else 'UNKNOWN'),
        'ETF 复权因子': ('Tushare fund_adj', 'OK' if adj_status == 'OK' else 'UNKNOWN'),
        'ETF 分红记录': ('Tushare fund_div', D.div_status(code)),
        '全市场涨跌家数/涨跌停': ('；'.join(sent.get('source', [])) or 'UNKNOWN',
                          'OK' if sent.get('total') else 'UNKNOWN'),
        '炸板率/连板高度': ('market_analysis.db / Tushare limit_list_d(Z/U)',
                       'OK' if sent.get('broken_rate') is not None else 'UNKNOWN'),
        '昨日涨停池': ('Tushare limit_list_d（本地缓存）',
                   'OK' if yzt.get('status') == 'OK' else 'UNKNOWN'),
        '当日个股日线': (cache.get('source', 'UNKNOWN'),
                    f"OK / {cache['rows']} 行" if cache['ok'] else f"UNKNOWN / {cache['rows']} 行"),
    }
    return dict(date=date, prev_date=prev_date, code=code, name=name,
                bars=bars, idx=idx, sent=sent, sent_prev=sent_prev, yzt=yzt,
                cache_ok=cache['ok'], adj_status=adj_status,
                div_status=D.div_status(code),
                integrity_missing=hard, sentiment_missing=soft, sources=src)


# ============================================================ 状态 / 基准台账
def load_state() -> dict:
    if os.path.exists(C.STATE_JSON):
        try:
            return json.load(open(C.STATE_JSON, encoding='utf-8'))
        except Exception:
            pass
    return {'version': 1, 'created_at': dt.datetime.now().isoformat(timespec='seconds'),
            'etf_code': None, 'runs': []}


def save_state(st: dict) -> None:
    st['updated_at'] = dt.datetime.now().isoformat(timespec='seconds')
    with open(C.STATE_JSON, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def state_upsert(st: dict, rec: dict) -> None:
    st['runs'] = [r for r in st['runs'] if r.get('date') != rec['date']]
    st['runs'].append(rec)
    st['runs'].sort(key=lambda r: r['date'])


def state_get(st: dict, date: str, origin_filter=None):
    for r in st['runs']:
        if r.get('date') == date and (origin_filter is None or r.get('origin') in origin_filter):
            return r
    return None


def ledger_upsert(rec: dict) -> int:
    cols = ['date', 'index_close', 'etf_close', 'base', 'trend', 'swing',
            'total', 'action', 'origin']
    rows = {}
    if os.path.exists(C.LEDGER_CSV):
        with open(C.LEDGER_CSV, encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f):
                rows[r['date']] = r
    rows[rec['date']] = {k: rec.get(k, '') for k in cols}
    with open(C.LEDGER_CSV, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    return len(rows)


# ============================================================ 报告块
def build_blocks(ctx, cur, prev, dec, market_status, run_at, selected_md) -> list:
    date, prev_date = ctx['date'], ctx['prev_date']
    sent, yzt = ctx['sent'], ctx['yzt']
    bars, idx = ctx['bars'], ctx['idx']
    code = ctx['code']
    ok = cur.get('ok')
    b = bars.iloc[-1] if (ok and not bars.empty) else None
    i = idx.iloc[-1] if not idx.empty else None
    B = []

    # ---------- 0 结论 ----------
    tot = cur['total']
    delta = dec['delta']
    if delta is None:
        chg = 'UNKNOWN（无可比基线，不推断）'
    else:
        chg = f'{delta:+.0f} pp（前一交易日建议 {dec["prev_total"]:.0f}%）'
    unchanged = '是 —— 信号不变' if dec['signal_unchanged'] else '否'
    B.append({'title': '0 · 一句话结论', 'items': [
        {'kind': 'callout', 'text': f'动作 {dec["action"]} ｜ 目标敞口 {fnum(tot,0,"%")} ｜ 较前一交易日 {chg}',
         'tone': dec['action']},
        {'kind': 'kv', 'rows': [
            ('数据时点（收盘日）', date),
            ('运行时点 / 市场状态', f'{run_at} ／ {market_status["label"]}'),
            ('信号不变', unchanged),
            ('底仓 / 趋势仓 / 波段仓', ' / '.join(
                fnum(cur['sleeves'][k], 0, '%') for k in ('base', 'trend', 'swing'))),
            ('基准口径', '策略建议记录 vs 中证2000 买入持有；本次不报告超额'),
        ]},
        {'kind': 'note', 'text': market_status['notice']},
        {'kind': 'note', 'text': '30%/40%/30% 为策略初始研究预算，不代表用户实际持仓，也未经最优性验证；'
                                 '本报告仅研究与决策辅助，不代替下单。'},
    ]})

    # ---------- 1 数据 ----------
    rows = [
        ('交易日期（收盘日）', date),
        ('ETF 代码 / 名称', f'{code} {ctx["name"]}'),
        ('ETF 收盘价（未复权，元）', fnum(b['close'], 3) if b is not None else 'UNKNOWN'),
        ('ETF 涨跌幅', fnum(b['pct_chg'], 2, '%', True) if b is not None else 'UNKNOWN'),
        ('ETF 成交额（亿元）', fnum(b['amount'] / 100000.0, 2) if b is not None else 'UNKNOWN'),
        ('ETF 成交量（手）', fnum(b['vol'], 0) if b is not None else 'UNKNOWN'),
        ('中证2000 收盘 / 涨跌幅', (fnum(i['close'], 2) + ' / ' + fnum(i['pct_chg'], 2, '%', True)) if i is not None else 'UNKNOWN'),
        ('复权 / 分红口径', f'前复权因子状态 {ctx["adj_status"]}；fund_div = {ctx["div_status"]}'
                          '（NO_DIV_RECORD 表示接口无记录，不等同于确认无分红）；'
                          '收盘价/涨跌幅/成交额用未复权，均线与偏离用前复权'),
    ]
    src_rows = [[c(k), c(v), c(s)] for k, (v, s) in ctx['sources'].items()]
    B.append({'title': '1 · 交易日期、标的与数据来源/完整性', 'items': [
        {'kind': 'kv', 'rows': rows},
        {'kind': 'table', 'headers': ['数据项', '来源', '状态'], 'rows': src_rows},
        {'kind': 'note', 'text': '成交额单位换算：Tushare fund_daily.amount 为千元 ÷ 100000 = 亿元；'
                                 '指数 amount 同为千元口径。Tushare 涨跌停池与 micro_daily 全市场统计'
                                 '口径可能不完全一致（ST／北交所／20% 涨跌幅板），已分别标注来源。'},
        {'kind': 'bullets', 'items': (['**无法确认的结论：**'] + [f'- {x}' for x in cur['unknowns']])
                                     if cur['unknowns'] else ['数据完整性：全部关键项 OK，无 UNKNOWN。']},
    ]})

    # ---------- 2 均线 ----------
    if ok:
        ma_rows = []
        for n in C.MA_WINDOWS:
            m = cur['ma'][n]
            ma_rows.append([c(f'MA{n}'), c(fnum(m['value'], 3, ' 元')),
                            c(fnum(m['slope_pct'], 2, '%', True), tone(m['slope_pct'])),
                            c(fnum(m['dev_pct'], 2, '%', True), tone(m['dev_pct'])),
                            c(f'{m["slope_lookback"]} 日')])
        voli = cur['volume']
        B.append({'title': '2 · MA20 / MA60 / MA120 及斜率、价格偏离', 'items': [
            {'kind': 'table', 'headers': ['均线', '数值', '斜率', '价格偏离', '斜率窗口'], 'rows': ma_rows},
            {'kind': 'kv', 'rows': [
                ('均线排列', cur['alignment']),
                ('趋势仓打分', f'价格位置 {cur["trend_scores"]["position"]:.2f} / '
                              f'排列 {cur["trend_scores"]["alignment"]:.2f} / '
                              f'斜率 {cur["trend_scores"]["slope"]:.2f} → 原始 {cur["trend_scores"]["raw"]:.1f}%'),
                ('量能', f'量比 {fnum(voli["vol_ratio"],2)}（成交量 / MA20量），'
                        f'当日成交额 {fnum(voli["amount_yi"],2," 亿元")}'),
                ('单日金叉 / 追涨抑制', f'{"是" if cur["single_day_cross"] else "否"} / '
                                    f'{"命中" if cur["spike_flag"] else "未命中"}'),
            ]},
        ]})
        levels = cur['levels']
        B[-1]['items'].append({'kind': 'note', 'text':
            f'关键价位（ETF 交易价）：MA20 {levels["MA20"]:.3f} ｜ MA60 {levels["MA60"]:.3f} ｜ '
            f'MA120 {levels["MA120"]:.3f} ｜ 今日高/低 {levels["today_high"]:.3f}/{levels["today_low"]:.3f}。'})
    else:
        B.append({'title': '2 · MA20 / MA60 / MA120 及斜率、价格偏离', 'items': [
            {'kind': 'callout', 'text': 'UNKNOWN —— 价格/完整性数据不达标，不计算均线、斜率与偏离，也不给交易价位。',
             'tone': 'NO TRADE'}]})

    # ---------- 3 市场广度 ----------
    up_ratio = sent.get('up_ratio')
    sr = [
        [c('上涨 / 下跌 / 平盘'), c(f'{sent.get("up_count","UNKNOWN")} / '
                                   f'{sent.get("down_count","UNKNOWN")} / '
                                   f'{sent.get("flat_count","UNKNOWN")}')],
        [c('全市场上涨占比'), c(fnum(up_ratio*100 if up_ratio is not None else None, 1, '%'))],
        [c('涨停 / 跌停家数'), c(f'{sent.get("zt_count","UNKNOWN")} / {sent.get("dt_count","UNKNOWN")}')],
        [c('炸板率 / 炸板数 / 最高连板'),
         c(f'{fnum(sent.get("broken_rate"),1,"%")} / {sent.get("zhaban_count","UNKNOWN")} / '
           f'{sent.get("max_limit_height","UNKNOWN")}')],
        [c('全市场成交额'), c(fnum(sent.get("amount_yi"), 0, ' 亿元'))],
        [c('昨日涨停股次日平均涨跌幅'), c(fnum(yzt.get('avg_pct'), 2, '%', True), tone(yzt.get('avg_pct')))],
        [c('昨日涨停股次日上涨占比'), c(fnum(yzt['up_ratio']*100, 1, '%')
                                   if yzt.get('up_ratio') is not None else 'UNKNOWN')],
        [c('样本数 / 匹配数 / 晋级率'),
         c(f'{yzt.get("sample_n","UNKNOWN")} / {yzt.get("matched_n","UNKNOWN")} / '
           f'{fnum(yzt["promote_rate"]*100,1,"%") if yzt.get("promote_rate") is not None else "UNKNOWN"}')],
    ]
    gate_rows = [[c(k), c('PASS' if v is True else ('FAIL' if v is False else 'UNKNOWN'),
                         'up' if v is True else ('down' if v is False else 'unknown'))]
                 for k, v in cur.get('gates', {}).items()] or [['UNKNOWN', 'UNKNOWN']]
    B.append({'title': '3 · 市场广度与涨跌停情绪', 'items': [
        {'kind': 'table', 'headers': ['指标', '数值'], 'rows': sr},
        {'kind': 'table', 'headers': ['情绪过滤条件', '结果'], 'rows': gate_rows},
        {'kind': 'note', 'text': f'口径：上涨占比 = up_count / total（分母 {sent.get("total","UNKNOWN")} 只，'
                                 f'micro_daily.db）；涨停/跌停为 Tushare 口径；昨日涨停股次日表现 = '
                                 f'{ctx["prev_date"]} 涨停池（limit_list_d）成分在 {date} 的 '
                                 'daily_cache.pct_chg 等权均值；晋级率 = 今日仍在涨停池的占比。'
                                 '缺失项一律 UNKNOWN，不做推断。'},
        {'kind': 'note', 'text': (
            ('系统性风险触发：' + '；'.join(cur['sysrisk']['hits']) + '。')
            if cur['sysrisk']['hits'] else
            ('系统性风险未触发；指数20日涨跌幅 '
             f'{fnum(cur.get("sysrisk",{}).get("idx_ret20_pct"),2,"%",True)}。'))
        } if ok else {'kind': 'note', 'text': '系统性风险判定：UNKNOWN（价格数据不达标）'},
    ]})

    # ---------- 4 三仓 ----------
    sl = cur['sleeves']
    budget = {'base': C.BUDGET_BASE, 'trend': C.BUDGET_TREND, 'swing': C.BUDGET_SWING}
    reason = {
        'base': f'MA120 {"上方" if ok and cur["ma"][120]["value"] and cur["levels"]["close"] > cur["ma"][120]["value"] else "下方"}'
                f'、MA120 斜率 {fnum(cur["ma"][120]["slope_pct"],2,"%",True) if ok else "UNKNOWN"}'
                f'；系统性风险 {"触发" if ok and cur["sysrisk"]["hits"] else "未触发/UNKNOWN"}',
        'trend': f'均线{cur["alignment"] if ok else "UNKNOWN"}；位置/排列/斜率加权打分',
        'swing': 'A/B/C 三形态命中情况见下',
    }
    cr = [[c(n), c(fnum(budget[k], 0, '%')), c(fnum(sl[k], 0, '%')), c(reason[k])]
          for k, n in [('base', '底仓'), ('trend', '均线趋势仓'), ('swing', '波段交易仓')]]
    setups = cur.get('setups', {})
    setup_rows = [[c(k), c('命中' if v['hit'] else '未命中', 'up' if v['hit'] else 'neutral'),
                   c(fnum(v['budget'], 0, '%')), c(v['detail'])]
                  for k, v in setups.items()] if setups else [[c('UNKNOWN'), c('UNKNOWN'), c('UNKNOWN'), c('UNKNOWN')]]
    B.append({'title': '4 · 三仓建议比例、合计目标敞口与变化', 'items': [
        {'kind': 'table', 'headers': ['仓位', '初始研究预算', '本日建议', '主要依据'], 'rows': cr},
        {'kind': 'table', 'headers': ['波段形态', '状态', '预算上限', '判定明细'], 'rows': setup_rows},
        {'kind': 'kv', 'rows': [
            ('合计目标敞口', fnum(tot, 0, '%') + f'（上限 {C.MAX_EXPOSURE:.0f}%）'),
            ('相对前一交易日变化', chg),
            ('基线来源', dec['baseline_origin'] or '无'),
            ('信号不变', unchanged),
            ('动作理由', dec['action_reason']),
        ]},
        {'kind': 'note', 'text': '仓位按 5pp 粒度取整，是规则化研究预算而非精确下单量；'
                                 '波段仓为条件性仓位，未触发即 0；情绪过滤不过关时对波段仓按 50% 逐项打折。'},
        {'kind': 'note', 'text': (
            '变化基线说明：前一交易日建议来自「同一规则回溯计算」，不是当时的实盘建议或实际持仓；'
            '首次运行无历史建议记录，故明确标注来源，不推断、不伪造前值。'
            if dec['baseline_origin'] == 'rule_replay' else
            '变化基线说明：前一交易日建议来自本模块当日实际生成的报告记录（live）。'
            if dec['baseline_origin'] == 'live' else
            '变化基线说明：无可比基线，变化 = UNKNOWN，不推断前值。')},
    ]})

    # ---------- 5 动作 ----------
    if ok:
        B.append({'title': '5 · 明确动作、触发条件、失效条件与关键价位', 'items': [
            {'kind': 'callout', 'text': f'{dec["action"]} —— {dec["action_reason"]}', 'tone': dec['action']},
            {'kind': 'bullets', 'items': ['**触发条件（需同时/逐项确认，非单日金叉或大阳线）：**'] +
                                        [f'- {x}' for x in cur['triggers']]},
            {'kind': 'bullets', 'items': ['**失效条件（任一成立即重算，不再新增敞口）：**'] +
                                        [f'- {x}' for x in cur['invalidation']]},
            {'kind': 'bullets', 'items': ['**关键价位（均由当日真实行情计算，非预测点位）：**'] +
                                        [f'- {LEVEL_LABELS.get(k,k)}：{fnum(v,3," 元")}'
                                         for k, v in cur['levels'].items()]},
        ]})
    else:
        B.append({'title': '5 · 明确动作、触发条件、失效条件与关键价位', 'items': [
            {'kind': 'callout', 'text': 'NO TRADE —— 数据不达标，触发条件、失效条件与关键价位均为 UNKNOWN，'
                                        '不给出虚假的精确仓位或交易价位。', 'tone': 'NO TRADE'}]})

    # ---------- 6 风险 / 观察 ----------
    risks = cur.get('risks') if ok else [
        '价格/完整性数据不达标，所有仓位与价位结论均无法确认。',
        '本报告为研究辅助，不代表用户实际持仓，不代替下单。',
    ]
    watch = cur.get('watch') if ok else ['次日先补齐当日 ETF/指数/个股日线/情绪四项数据后再重算。']
    B.append({'title': '6 · 风险提示与次日观察项', 'items': [
        {'kind': 'bullets', 'items': ['**风险提示**'] + [f'- {x}' for x in risks]},
        {'kind': 'bullets', 'items': ['**次日观察项**'] + [f'- {x}' for x in watch]},
    ]})

    # ---------- 7 绩效记录 ----------
    n = ledger_upsert({
        'date': date,
        'index_close': fnum(i['close'], 4) if i is not None else 'UNKNOWN',
        'etf_close': fnum(b['close'], 4) if b is not None else 'UNKNOWN',
        'base': fnum(sl['base'], 0), 'trend': fnum(sl['trend'], 0), 'swing': fnum(sl['swing'], 0),
        'total': fnum(tot, 0), 'action': dec['action'],
        'origin': 'live',
    })
    B.append({'title': '7 · 策略建议与买入持有基准（持续记录）', 'items': [
        {'kind': 'kv', 'rows': [
            ('已记录交易日数', f'{n}（台账 {C.LEDGER_CSV}）'),
            ('本日买入持有基准点位', fnum(i['close'], 2) if i is not None else 'UNKNOWN'),
            ('本日策略建议敞口', fnum(tot, 0, '%')),
            ('超额收益', f'不报告：样本 {n} 个交易日 < {C.MIN_PERF_SAMPLE}，且未运行带成本/滑点的正式回测。'),
        ]},
        {'kind': 'note', 'text': '不声称策略已实现超额。未来如报告回测/绩效，必须注明区间、'
                                 f'单边成本假设（{C.BACKTEST_COST_BPS:.0f} bps）、'
                                 f'单边滑点假设（{C.BACKTEST_SLIPPAGE_BPS:.0f} bps）、'
                                 '样本区间与样本限制；当前台账只记录建议与基准点位，不计算收益。'},
    ]})

    # ---------- 附 ----------
    B.append({'title': '附 · ETF 选择依据（核验摘要）', 'items': [
        {'kind': 'kv', 'rows': [
            ('指数', f'{C.INDEX_CODE} {C.INDEX_NAME}（Tushare index_basic 核验）'),
            ('代表 ETF', f'{code} {ctx["name"]}'),
            ('核验文件', f'{C.ETF_SELECTION_MD}'),
        ]},
        {'kind': 'note', 'text': selected_md},
    ]})
    B.append({'title': '免责声明', 'items': [{'kind': 'note', 'text': C.DISCLAIMER}]})
    return B


def render_md(blocks, title: str) -> str:
    out = [f'# {title}', '']
    for blk in blocks:
        out += [f'## {blk["title"]}', '']
        for it in blk['items']:
            k = it['kind']
            if k == 'callout':
                out += [f'> **{it["text"]}**', '']
            elif k == 'note':
                out += [f'> {it["text"]}', '']
            elif k == 'kv':
                for a, b_ in it['rows']:
                    out.append(f'- **{a}**：{b_}')
                out.append('')
            elif k == 'bullets':
                out += it['items'] + ['']
            elif k == 'table':
                hs = it['headers']
                out.append('| ' + ' | '.join(hs) + ' |')
                out.append('|' + '|'.join(['---'] * len(hs)) + '|')
                for row in it['rows']:
                    out.append('| ' + ' | '.join(cell_text(x) for x in row) + ' |')
                out.append('')
    return '\n'.join(out)


def render_html(blocks, title: str) -> str:
    colors = {'up': '#d9342b', 'down': '#0f9d58', 'neutral': '#222', 'unknown': '#8a6d00'}
    act_bg = {'BUY': '#fdecea', 'HOLD': '#fff7e6', 'REDUCE': '#e8f5e9', 'NO TRADE': '#eceff1'}
    h = [f'<!DOCTYPE html><html><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>{html.escape(title)}</title></head>'
         f'<body style="margin:0;padding:18px;background:#f5f6f8;">'
         f'<div style="max-width:860px;margin:0 auto;background:#fff;padding:26px;'
         f'font-size:20px;line-height:1.85;color:#222;font-family:-apple-system,'
         f'BlinkMacSystemFont,\'Segoe UI\',\'Microsoft YaHei\',sans-serif;">'
         f'<h1 style="font-size:28px;line-height:1.4;margin:0 0 18px;">{html.escape(title)}</h1>']
    for blk in blocks:
        h.append(f'<h2 style="font-size:23px;margin:28px 0 12px;padding-left:10px;'
                 f'border-left:5px solid #2f6fed;">{html.escape(blk["title"])}</h2>')
        for it in blk['items']:
            k = it['kind']
            if k == 'callout':
                bg = act_bg.get(it.get('tone'), '#f0f4ff')
                h.append(f'<div style="background:{bg};padding:14px 16px;border-radius:8px;'
                         f'margin:10px 0;font-size:23px;font-weight:700;">'
                         f'{html.escape(plain(it["text"]))}</div>')
            elif k == 'note':
                h.append(f'<p style="font-size:16px;color:#555;background:#f7f8fa;'
                         f'padding:10px 12px;border-radius:6px;margin:10px 0;">'
                         f'{html.escape(plain(it["text"]))}</p>')
            elif k == 'kv':
                h.append('<table style="width:100%;border-collapse:collapse;margin:8px 0;">')
                for a, b_ in it['rows']:
                    h.append(f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee;'
                             f'width:34%;color:#555;">{html.escape(plain(a))}</td>'
                             f'<td style="padding:8px 12px;border-bottom:1px solid #eee;">'
                             f'{html.escape(plain(b_))}</td></tr>')
                h.append('</table>')
            elif k == 'bullets':
                h.append('<ul style="padding-left:26px;margin:8px 0;">')
                for x in it['items']:
                    if x.startswith('**') and x.endswith('**'):
                        h.append(f'<li style="list-style:none;font-weight:700;margin-top:8px;">'
                                 f'{html.escape(plain(x.strip("*")))}</li>')
                    else:
                        h.append(f'<li>{html.escape(plain(x.lstrip("- ")))}</li>')
                h.append('</ul>')
            elif k == 'table':
                h.append('<table style="width:100%;border-collapse:collapse;margin:10px 0;">')
                h.append('<tr>' + ''.join(
                    f'<th style="padding:9px 12px;background:#eef2f8;border:1px solid #dde3ec;'
                    f'text-align:left;">{html.escape(str(x))}</th>' for x in it['headers']) + '</tr>')
                for row in it['rows']:
                    tds = []
                    for x in row:
                        col = colors.get(cell_tone(x), '#222')
                        tds.append(f'<td style="padding:9px 12px;border:1px solid #e5e8ee;color:{col};">'
                                   f'{html.escape(plain(cell_text(x)))}</td>')
                    h.append('<tr>' + ''.join(tds) + '</tr>')
                h.append('</table>')
    h.append(f'<p style="font-size:16px;color:#666;border-top:1px solid #ddd;margin-top:28px;'
             f'padding-top:14px;">{html.escape(C.DISCLAIMER)}</p>')
    h.append('</div></body></html>')
    return '\n'.join(h)


# ============================================================ 邮件
def push_mail(html_path: str, date: str, action: str, total) -> dict:
    if not os.path.exists(C.AGENT_MAIL_CLI):
        return {'sent': False, 'reason': f'Agent Mail CLI 不存在: {C.AGENT_MAIL_CLI}'}
    subject = f'【中证2000仓位信号】{date} · {action} {fnum(total,0,"%")}'
    base = [C.AGENT_MAIL_CLI, 'message', '+send', '--to', C.MAIL_TO,
            '--subject', subject, '--body-file', os.path.basename(html_path),
            '--body-format', 'html', '--confirmed']
    attempts = [base, ['cmd', '/c'] + base]
    last = ''
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, cwd=C.REPORT_DIR, capture_output=True, text=True,
                               timeout=180, encoding='utf-8', errors='ignore')
            last = (r.stdout or '') + (r.stderr or '')
            if r.returncode == 0 and ('"ok": true' in last or '"ok":true' in last or '已发送' in last):
                return {'sent': True, 'reason': last.strip()[-300:]}
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
    return {'sent': False, 'reason': last.strip()[-400:]}


# ============================================================ 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='ZZ2000 每日收盘后仓位信号报告')
    ap.add_argument('--date', default=None, help='强制指定数据时点 YYYYMMDD')
    ap.add_argument('--etf-code', default=None, help='覆盖已核验的代表 ETF 代码')
    ap.add_argument('--push', action='store_true', help='邮件推送（每次都发，不静默）')
    ap.add_argument('--no-replay', action='store_true', help='不做前一交易日规则回溯基线')
    ap.add_argument('--force-universe', action='store_true', help='重新核验 ETF 清单')
    a = ap.parse_args(argv)

    D.init_cache()
    D.ensure_trade_cal()
    run_at = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = D.today_str()
    is_open = D.is_open(today)

    if a.force_universe:
        from zz2000 import verify_etf as V
        V.main()

    code = a.etf_code or D.meta_get('selected_etf')
    name = D.meta_get('selected_etf_name') or ''
    if a.etf_code:
        with D.rw_connect() as con:
            r = con.execute('SELECT name FROM etf_meta WHERE ts_code=?', (a.etf_code,)).fetchone()
        name = r[0] if r else '（代码已被 --etf-code 覆盖，名称未核验）'
    if not code or code == 'UNKNOWN':
        print('[fatal] 代表 ETF 未核验（selected_etf=UNKNOWN）。请先运行 python -m zz2000.verify_etf')
        return 2

    # 刷新：ETF 日线（增量）、复权/分红（按日一次）
    D.refresh_etf_daily([code])
    if D.meta_get('adj_refresh_date') != today:
        D.refresh_etf_adj([code])
        D.refresh_etf_div([code])
        D.meta_set('adj_refresh_date', today)

    # 确定数据时点：指数与 ETF 共同的最新交易日
    with D.rw_connect() as con:
        dates = [r[0] for r in con.execute('''
            SELECT i.trade_date FROM index_daily i
            JOIN etf_daily e ON e.trade_date=i.trade_date AND e.ts_code=?
            WHERE i.ts_code=? ORDER BY i.trade_date DESC LIMIT 1''',
            (code, C.INDEX_CODE)).fetchall()]
    data_date = a.date or (dates[0] if dates else None)
    if not data_date:
        print('[fatal] 指数与 ETF 无共同交易日数据，无法生成报告（不臆造行情）。')
        return 2

    tdays = D.trading_days()
    prev_date = None
    for i, d in enumerate(tdays):
        if d == data_date and i > 0:
            prev_date = tdays[i - 1]
            break
    if prev_date is None:
        prev_date = max([d for d in tdays if d < data_date], default=None)
    if prev_date is None:
        print('[fatal] 交易日历不足，无法确定前一交易日。')
        return 2

    # 市场状态（严禁把旧数据说成当日行情）
    if is_open == 0:
        ms = '休市（非交易日）'
        notice = (f'{today} 为非交易日（Tushare trade_cal is_open=0），休市。'
                  f'本报告沿用最近已完成交易日 {data_date} 的收盘数据，'
                  f'**不是 {today} 的行情**。')
    elif is_open is None:
        ms = '交易日历 UNKNOWN'
        notice = (f'{today} 的交易日历状态 UNKNOWN，无法判定是否休市；'
                  f'本报告基于缓存中最近交易日 {data_date} 的收盘数据，不推断当日行情。')
    elif data_date == today and D.now_hhmm() >= C.CLOSE_TIME:
        ms = '今日收盘后'
        notice = f'本报告基于 {data_date} 收盘数据；运行时点 {run_at}。'
    elif data_date == today:
        ms = '今日为交易日，但尚未收盘'
        notice = (f'{today} 为交易日，当前 {D.now_hhmm()} 尚未到 {C.CLOSE_TIME} 收盘；'
                  f'缓存中的 {data_date} 数据不能当作已确认的当日收盘行情，本次按最近已完成交易日口径处理。')
    else:
        ms = '今日为交易日，当日收盘数据尚未生成/未落库'
        notice = (f'{today} 为交易日，但最新可用收盘数据为 {data_date}（当前 {D.now_hhmm()}）。'
                  f'**本报告是 {data_date} 的收盘信号，不是 {today} 的行情**；'
                  '不把旧数据说成当日行情，也不推断当日收盘价。')
    market_status = {'label': ms, 'notice': notice}

    # 上下文 + 前一日基线
    ctx = build_ctx(data_date, prev_date, code, name)
    st = load_state()
    st['etf_code'] = code
    prev = state_get(st, prev_date)
    if prev is None and not a.no_replay:
        ptdays = D.trading_days()
        pi = ptdays.index(prev_date) if prev_date in ptdays else None
        pprev = ptdays[pi - 1] if (pi is not None and pi > 0) else None
        if pprev:
            pctx = build_ctx(prev_date, pprev, code, name)
            pres = S.compute(pctx)
            prev = {'date': prev_date, 'origin': 'rule_replay', 'ok': pres['ok'],
                    'total': pres['total'], 'signal_key': pres.get('signal_key'),
                    'note': '规则回溯计算（非当时实盘建议）'}
            state_upsert(st, prev)
            print(f'[baseline] 无前一交易日实盘建议，已用同一规则回溯 {prev_date} 作为基线（明确标注）')
    cur = S.compute(ctx)
    dec = S.decide(cur, prev)

    # 状态记录（本日 live）
    live = {'date': data_date, 'origin': 'live', 'ok': cur['ok'], 'total': cur['total'],
            'base': cur['sleeves']['base'], 'trend': cur['sleeves']['trend'],
            'swing': cur['sleeves']['swing'], 'action': dec['action'],
            'signal_key': cur.get('signal_key'), 'generated_at': run_at,
            'market_status': ms}
    state_upsert(st, live)
    save_state(st)

    # 选择依据摘要
    sel = ''
    if os.path.exists(C.ETF_SELECTION_MD):
        txt = open(C.ETF_SELECTION_MD, encoding='utf-8').read()
        sel = ' '.join([x.strip() for x in txt.splitlines()
                        if x.startswith('**代表产品') or x.startswith('1. **流动性')
                        or x.startswith('2. **跟踪质量') or x.startswith('3. **费用')])
    if not sel:
        sel = '详见核验文件（本次未读到摘要）。'

    title = f'【中证2000·ZZ2000 仓位信号日报】数据时点 {data_date}'
    blocks = build_blocks(ctx, cur, prev, dec, market_status, run_at, sel)
    md = render_md(blocks, title)
    htm = render_html(blocks, title)
    md_path = os.path.join(C.REPORT_DIR, f'ZZ2000_Daily_{data_date}.md')
    html_path = os.path.join(C.REPORT_DIR, f'ZZ2000_Daily_{data_date}.html')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(htm)

    mail = {'sent': False, 'reason': '未请求推送'} if not a.push else push_mail(
        html_path, data_date, dec['action'], cur['total'])

    print(md)
    print('\n--- RESULT JSON ---')
    print(json.dumps({
        'ok': True, 'run_at': run_at, 'today': today, 'data_date': data_date,
        'prev_date': prev_date, 'market_status': ms, 'etf_code': code,
        'action': dec['action'], 'total': cur['total'], 'delta': dec['delta'],
        'signal_unchanged': dec['signal_unchanged'], 'baseline_origin': dec['baseline_origin'],
        'unknowns': cur['unknowns'], 'mail_sent': mail['sent'], 'mail_reason': mail['reason'],
        'md': md_path, 'html': html_path,
    }, ensure_ascii=False, indent=2))
    return 0 if mail['sent'] or not a.push else 3


if __name__ == '__main__':
    sys.exit(main())
