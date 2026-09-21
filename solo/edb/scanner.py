# -*- coding: utf-8 -*-
"""
EDB 全市场扫描 / 单股诊断 / 历史回测 —— CLI 入口
================================================================================
用法:
  python -m edb.scanner --date 20260918                 # 全市场扫描某交易日
  python -m edb.scanner --date 20260918 --top 30        # 只输出 TOP30
  python -m edb.scanner --date 20260918 --report        # 生成 Markdown 报告
  python -m edb.scanner --symbol 600519                 # 单股诊断
  python -m edb.scanner --backtest 20250601 20260918    # 历史回测（含 VR 分区间验证）
  python -m edb.scanner --date 20260918 --limit 300     # 快速调试（只跑前N只）

输出:
  output/edb/edb_daily_<date>.csv      TOP N（按 EDB_SCORE_ADJ 排序）
  output/edb/edb_full_<date>.csv       全部有效候选（供二筛）

说明:
  结构类字段（BaseDays / DryUpRatio / BreakoutVR / PlatformHigh / ClosePosition …）
  取「事件日快照」——突破类取突破日指标，PRE_EDB 取当日指标；
  现价 / MA20 / MA60 / MA120 / 距250日高 取当日值，便于直接判断当前位置。
"""
import os
import sys
import argparse
import datetime
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)
if SOLO_DIR not in sys.path:
    sys.path.insert(0, SOLO_DIR)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from edb.config import EDB_CONFIG, REPORT_DIR, STATUS_CN, STATUS_PRIORITY
from edb.data import (load_daily, get_stock_pool, get_name_map, load_hvt_map,
                      load_fundamental_map, load_event_map, events_available,
                      get_trade_dates, last_trade_date_on_or_before, to_ts_code)
from edb.engine import EdbEngine, result_to_dict


# ═══════════════════════════════════════════════════════════
# 工作进程（模块级可 picklable）
# ═══════════════════════════════════════════════════════════
_ENGINE = None


def _engine() -> EdbEngine:
    """工作进程内单例（避免每只股票重复构造）"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = EdbEngine()
    return _ENGINE


def _score_one(args):
    ts_code, name, industry, date = args
    df = load_daily(ts_code, date, EDB_CONFIG['lookback_bars'])
    if df is None or len(df) < EDB_CONFIG['min_bars']:
        return None
    return _engine().score(df, ts_code=ts_code, name=name, industry=industry, date=date)


# ═══════════════════════════════════════════════════════════
# 板块记录（不剔除科创/创业板，但必须单独记录）
# ═══════════════════════════════════════════════════════════
def _board(ts_code: str) -> str:
    p = str(ts_code)[:2]
    return {'60': '沪主板', '00': '深主板', '30': '创业板', '68': '科创板'}.get(p, '其他')


# ═══════════════════════════════════════════════════════════
# 联动数据注入（在主进程做，避免大字典跨进程传输）
# ═══════════════════════════════════════════════════════════
def _enrich(results: list, date: str) -> list:
    hvt = load_hvt_map(date)
    fund = load_fundamental_map(date)
    ev = load_event_map(date)
    for r in results:
        hv = hvt.get(r.ts_code) or {}
        r.hvt_state = str(hv.get('hvt_state', ''))
        r.hvt_band = str(hv.get('hvt_band', ''))
        r.hvt_quality = hv.get('hvt_quality', np.nan)
        fn = fund.get(r.ts_code) or {}
        r.fundamental = str(fn.get('status', 'NA'))
        r.fund_np_yoy = fn.get('np_yoy', np.nan)
        r.fund_q2_yoy = fn.get('q2_yoy', np.nan)
        e = ev.get(r.ts_code)
        if e:
            r.event_driven = True
            r.event_types = list(e.get('types', []))
            if 'EVENT_DRIVEN' not in r.risk_tags:
                r.risk_tags.append('EVENT_DRIVEN')
            if 'EVENT_DRIVEN' not in r.flags:
                r.flags.append('EVENT_DRIVEN')
    return results


# ═══════════════════════════════════════════════════════════
# 全市场扫描
# ═══════════════════════════════════════════════════════════
def scan_date(date: str, jobs: int = 8, limit: int = 0):
    """返回 (全部候选 DataFrame, TOP N DataFrame)；按 EDB_SCORE_ADJ 降序"""
    pool = get_stock_pool(exclude_st=EDB_CONFIG['exclude_st'])
    if pool.empty:
        print('[扫描] 股票池为空')
        return pd.DataFrame(), pd.DataFrame()
    if limit > 0:
        pool = pool.head(limit)
    n_pool = len(pool)
    print(f'[扫描] {date} 股票池 {n_pool} 只 | 并行 {jobs} | 回看 {EDB_CONFIG["lookback_bars"]} 根K线')

    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), date)
             for _, r in pool.iterrows()]
    results = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, res in enumerate(ex.map(_score_one, tasks, chunksize=64)):
            if res is not None:
                results.append(res)
            if (i + 1) % 1000 == 0:
                print(f'  进度 {i + 1}/{n_pool} 已出候选 {len(results)}')

    if not results:
        print('[扫描] 无有效候选')
        return pd.DataFrame(), pd.DataFrame()

    results = _enrich(results, date)
    rows = []
    for r in results:
        d = result_to_dict(r)
        d['板块'] = _board(r.ts_code)
        rows.append(d)
    df = pd.DataFrame(rows)
    df = df.sort_values(['EDB_SCORE_ADJ', 'EDB_SCORE'], ascending=[False, False]).reset_index(drop=True)
    df['排名'] = range(1, len(df) + 1)
    df.attrs['n_pool'] = n_pool          # 供报告复用同一口径的股票池总数

    os.makedirs(REPORT_DIR, exist_ok=True)
    full_path = os.path.join(REPORT_DIR, f'edb_full_{date}.csv')
    df.to_csv(full_path, index=False, encoding='utf-8-sig')
    top = df.head(EDB_CONFIG['top_n'])
    top_path = os.path.join(REPORT_DIR, f'edb_daily_{date}.csv')
    top.to_csv(top_path, index=False, encoding='utf-8-sig')
    print(f'[扫描] 完成：候选 {len(df)} 只，TOP{len(top)} -> {top_path}')
    print('[统计] 见下方（规范第 24 节）')
    print_stats(df, n_pool)
    return df, top


# ═══════════════════════════════════════════════════════════
# 运行后统计（规范第 24 节）
# ═══════════════════════════════════════════════════════════
def _dist(series: pd.Series, bins, labels) -> list:
    s = pd.to_numeric(series, errors='coerce').dropna()
    if s.empty:
        return []
    cut = pd.cut(s, bins=bins, labels=labels, right=False)
    out = []
    for lab in labels:
        n = int((cut == lab).sum())
        if n:
            out.append((str(lab), n, 100.0 * n / len(s)))
    return out


def print_stats(df: pd.DataFrame, n_pool: int):
    line = '─' * 74
    print(line)
    print('EDB 运行统计')
    print(line)
    print(f'股票池总数            {n_pool}')
    if df is None or df.empty:
        print('无有效候选')
        print(line)
        return
    print(f'有效候选总数          {len(df)}')
    for st in STATUS_PRIORITY:
        n = int((df['EDB状态'] == st).sum())
        print(f'{st:<14}{STATUS_CN.get(st, ""):<20} {n}')
    n_ev = int(df['EVENT_DRIVEN'].fillna(False).astype(bool).sum())
    print(f'{"EVENT_DRIVEN":<14}{"事件驱动（另分类）":<20} {n_ev}')
    climax = int(df['风险标签'].fillna('').str.contains('CLIMAX_WARNING|CLIMAX_VOL').sum())
    print(f'{"CLIMAX_WARNING":<14}{"天量/高潮警戒":<20} {climax}')
    tags = df['风险标签'].fillna('')
    gate = int((tags.str.contains('CLIMAX_VOL') | tags.str.contains('DRY_UP_SHALLOW')
                ).sum())
    print(f'{"硬闸门标签命中":<14}{"天量/缩量不足":<20} {gate}')
    if not events_available():
        from edb.data import _EVENT_STATE
        print(f'  ⚠ 事件数据不可用（{_EVENT_STATE.get("note", "") or "接口权限不足"}）——'
              f'EVENT_DRIVEN 标记可能不完整，未臆造')

    print()
    print('[BaseDays 分布]')
    for lab, n, p in _dist(df['BaseDays'], [0, 60, 90, 150, 500],
                           ['<60', '60~90', '90~150', '>150']):
        print(f'  {lab:<10} {n:5d}  {p:5.1f}%')
    print('[DryUpRatio 分布]')
    for lab, n, p in _dist(df['DryUpRatio'], [0, 0.35, 0.45, 0.55, 0.70, 9],
                           ['<0.35超极致', '0.35~0.45极致', '0.45~0.55明显', '0.55~0.70普通', '>0.70无缩量']):
        print(f'  {lab:<12} {n:5d}  {p:5.1f}%')
    print('[BreakoutVR 分布]（仅突破类状态：EDB_STRONG / EDB / EDB_WATCH / 二次机会）')
    bo = df[df['EDB状态'].isin(('EDB_STRONG', 'EDB', 'EDB_WATCH', 'EDB_PULLBACK', 'EDB_REBREAKOUT'))]
    if bo.empty:
        print('  无突破类候选')
    else:
        for lab, n, p in _dist(bo['BreakoutVR'], [0, 2.0, 2.5, 3.0, 5.0, 999],
                               ['<2.0', '2.0~2.5', '2.5~3.0', '3.0~5.0', '>5.0']):
            print(f'  {lab:<10} {n:5d}  {p:5.1f}%')
    print('[EDB_SCORE_ADJ 分布]')
    for lab, n, p in _dist(df['EDB_SCORE_ADJ'], [0, 60, 70, 80, 90, 101],
                           ['<60', '60~70', '70~80', '80~90', '>=90']):
        print(f'  {lab:<10} {n:5d}  {p:5.1f}%')
    print('[板块分布]（不剔除，仅记录）')
    for b, n in df['板块'].value_counts().items():
        print(f'  {b:<8} {n:5d}  {100.0 * n / len(df):5.1f}%')
    print(line)


# ═══════════════════════════════════════════════════════════
# 单股诊断
# ═══════════════════════════════════════════════════════════
def diagnose(ts_code: str, dates: list):
    name = get_name_map().get(ts_code, '')
    industry = ''
    eng = EdbEngine()
    for d in dates:
        df = load_daily(ts_code, d, EDB_CONFIG['lookback_bars'])
        if df is None or len(df) < EDB_CONFIG['min_bars']:
            print(f'[{d}] 数据不足（需 >= {EDB_CONFIG["min_bars"]} 根K线）')
            continue
        r = eng.score(df, ts_code=ts_code, name=name, industry=industry, date=d)
        print('=' * 74)
        print(f'EDB 单股诊断  {name} {ts_code} @ {d}')
        print('=' * 74)
        if r is None:
            print('无 EDB 信号（未通过股票池过滤 / Base 有效性 / 缩量-压缩-突破条件）')
            continue
        for k, v in result_to_dict(r).items():
            print(f'  {k:<14} {v}')
    print('=' * 74)


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='EDB 极致缩量→爆量突破 选股引擎 V1.0')
    ap.add_argument('--date', default='', help='交易日 YYYYMMDD，可逗号分隔多个')
    ap.add_argument('--symbol', default='', help='单股诊断 6位代码')
    ap.add_argument('--top', type=int, default=EDB_CONFIG['top_n'], help='TOP N')
    ap.add_argument('--jobs', type=int, default=8, help='并行进程数')
    ap.add_argument('--limit', type=int, default=0, help='只扫描前N只（调试用）')
    ap.add_argument('--backtest', nargs=2, metavar=('START', 'END'), help='历史回测区间')
    ap.add_argument('--step', type=int, default=5, help='回测调仓步长(交易日)')
    ap.add_argument('--report', action='store_true', help='生成 Markdown 报告')
    args = ap.parse_args()

    if args.backtest:
        from edb.backtest import backtest, print_backtest_report, backtest_stats
        start, end = args.backtest
        df = backtest(start, end, step=args.step, jobs=args.jobs)
        print_backtest_report(df)
        if args.report and not df.empty:
            from edb.report import write_backtest_report
            write_backtest_report(df, start, end, backtest_stats)
        return

    dates = [d.strip() for d in args.date.split(',') if d.strip()]
    if not dates:
        last = last_trade_date_on_or_before(datetime.date.today().strftime('%Y%m%d'))
        dates = [last] if last else [datetime.date.today().strftime('%Y%m%d')]

    if args.symbol:
        diagnose(to_ts_code(args.symbol), dates)
        return

    df, top = scan_date(dates[0], jobs=args.jobs, limit=args.limit)
    if df.empty:
        print('无有效信号')
        return
    print_table(top.head(args.top))
    if args.report:
        from edb.report import write_daily_report
        write_daily_report(df, dates[0], print_stats)


def print_table(df: pd.DataFrame):
    cols = ['排名', '代码', '名称', 'EDB状态', 'EDB_SCORE_ADJ', 'BaseDays', 'DryUpRatio',
            'BreakoutVR', 'BreakoutDistance', 'DayGain', 'ClosePosition', '建议动作']
    header = {'排名': 'No', '代码': '代码', '名称': '名称', 'EDB状态': 'EDB状态',
              'EDB_SCORE_ADJ': 'SCORE', 'BaseDays': 'Base', 'DryUpRatio': '缩量比',
              'BreakoutVR': '爆量VR', 'BreakoutDistance': '距平台%', 'DayGain': '涨幅%',
              'ClosePosition': '收盘位', '建议动作': '建议动作'}
    fmt = {'排名': '{:d}', 'EDB_SCORE_ADJ': '{:.1f}', 'BaseDays': '{:d}',
           'DryUpRatio': '{:.3f}', 'BreakoutVR': '{:.2f}', 'BreakoutDistance': '{:+.2f}',
           'DayGain': '{:+.2f}', 'ClosePosition': '{:.2f}'}
    w = {'排名': 4, '代码': 10, '名称': 10, 'EDB状态': 14, 'EDB_SCORE_ADJ': 6, 'BaseDays': 5,
         'DryUpRatio': 7, 'BreakoutVR': 7, 'BreakoutDistance': 8, 'DayGain': 7,
         'ClosePosition': 7, '建议动作': 24}
    print()
    head = ' '.join(header[c].ljust(w[c]) for c in cols)
    print(head)
    print('-' * len(head))
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r.get(c)
            try:
                if c in fmt and v is not None and not (isinstance(v, float) and np.isnan(v)):
                    cells.append(fmt[c].format(v).ljust(w[c]))
                else:
                    cells.append(str(v if v is not None else '').ljust(w[c]))
            except Exception:
                cells.append(str(v).ljust(w[c]))
        print(' '.join(cells))
    print()


if __name__ == '__main__':
    main()
