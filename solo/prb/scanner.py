# -*- coding: utf-8 -*-
"""
PRB Scanner V1.0 -- CLI 入口
================================================
用法:
  python -m prb.scanner --date 20260814                # 全市场扫描某交易日
  python -m prb.scanner --date 20260814 --top 20       # 只输出 TOP20
  python -m prb.scanner --symbol 300404                # 单股状态机诊断
  python -m prb.scanner --report                       # 生成 Markdown 报告
"""
import os
import sys
import argparse
import datetime
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from prb.config import PRB_CONFIG, REPORT_DIR
from bts.data import (load_daily, get_stock_pool, get_name_map, market_regime,
                      get_trade_dates, to_ts_code, last_trade_date_on_or_before)
from prb.engine import PRBEngine, PRBResult


# ── 工作进程函数（multiprocessing 需要模块级可 picklable）──
def _score_one(args):
    ts_code, name, industry, date, regime = args
    df = load_daily(ts_code, date, PRB_CONFIG['lookback_bars'])
    if df is None or len(df) < PRB_CONFIG['min_bars']:
        return None
    eng = PRBEngine()
    r = eng.score(df, ts_code=ts_code, name=name, industry=industry,
                  market_regime=regime)
    # 只保留有实质进展的信号（至少平台确认）
    if r.platform_score <= 0:
        return None
    return r


def r2dict(r: PRBResult) -> dict:
    return {
        'ts_code': r.ts_code, 'name': r.name, 'industry': r.industry, 'date': r.date,
        'state': r.state, 'state_cn': r.state_cn,
        'action': r.action, 'action_cn': r.action_cn, 'action_reason': r.action_reason,
        'platform_start': r.platform_start, 'platform_end': r.platform_end,
        'platform_days': r.platform_days, 'platform_high': r.platform_high,
        'platform_low': r.platform_low, 'platform_range': r.platform_range,
        'resistance_tests': r.resistance_tests, 'support_tests': r.support_tests,
        'vol_shrink_ratio': r.vol_shrink_ratio, 'platform_score': r.platform_score,
        'platform_grade': r.platform_grade,
        'breakout_date': r.breakout_date, 'breakout_price': r.breakout_price,
        'breakout_pct': r.breakout_pct, 'breakout_vr': r.breakout_vr,
        'breakout_candle_pos': r.breakout_candle_pos,
        'breakout_score': r.breakout_score, 'breakout_grade': r.breakout_grade,
        'post_breakout_days': r.post_breakout_days,
        'pullback_start': r.pullback_start, 'pullback_low': r.pullback_low,
        'pullback_depth': r.pullback_depth, 'pullback_days': r.pullback_days,
        'pullback_vol_ratio': r.pullback_vol_ratio,
        'pullback_below_bl': r.pullback_below_bl, 'pullback_end_ok': r.pullback_end_ok,
        'pullback_score': r.pullback_score,
        'reaccel_date': r.reaccel_date, 'reaccel_price': r.reaccel_price,
        'reaccel_vol_ratio': r.reaccel_vol_ratio, 'reaccel_candle_pos': r.reaccel_candle_pos,
        'reaccel_ok': r.reaccel_ok,
        'final_score': r.final_score, 'grade': r.grade, 'grade_cn': r.grade_cn,
        'close': r.close, 'pct_chg': r.pct_chg, 'dist_ma5': r.dist_ma5,
        'dist_breakout_level': r.dist_breakout_level,
        'vol_ratio_today': r.vol_ratio_today,
        'forbidden': '；'.join(r.forbidden_reasons),
        'warnings': '；'.join(r.warnings),
        'market_regime': r.market_regime,
    }


_REGIME_CACHE = {}


def _get_regime(d):
    """工作进程内的市场状态缓存"""
    if d not in _REGIME_CACHE:
        _REGIME_CACHE[d] = market_regime(d)
    return _REGIME_CACHE[d]


def _backtest_one(args):
    """单只股票全程回测：加载一次全历史，逐调仓日截断评分，附未来收益(labels)"""
    ts_code, name, industry, start, end, step = args
    df = load_daily(ts_code, end, lookback_bars=900)
    if df is None or len(df) < 200:
        return []
    dates = df['trade_date'].astype(str).tolist()
    eng = PRBEngine()
    out = []
    n = len(df)
    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    vols = df['vol'].values.astype(float)
    vma20 = pd.Series(vols).rolling(20).mean().values
    for idx in range(160, n):
        d = dates[idx]
        if d < start or d > end:
            continue
        if step > 1 and (idx - 160) % step != 0:
            continue
        # 快速预筛：价格接近/突破近25日高点，或明显放量（平台突破回踩形态的必要条件）
        lo = idx - 25
        p25 = highs[lo:idx].max()
        c = closes[idx]
        v = vols[idx]
        vv = vma20[idx]
        if not (c > p25 * 0.97 or (not np.isnan(vv) and vv > 0 and v > vv * 1.3)):
            continue
        sub = df.iloc[:idx + 1]
        if len(sub) < PRB_CONFIG['min_bars']:
            continue
        r = eng.score(sub, ts_code=ts_code, name=name, industry=industry,
                      market_regime=_get_regime(d))
        if r.platform_score <= 0:
            continue
        rec = r2dict(r)
        fut = df.iloc[idx + 1:]
        buy = closes[idx]
        if buy > 0 and len(fut) > 0:
            fc = fut['close'].values.astype(float)
            rec['fut3'] = (fc[2] / buy - 1) * 100 if len(fc) >= 3 else np.nan
            rec['fut5'] = (fc[4] / buy - 1) * 100 if len(fc) >= 5 else np.nan
            rec['fut10'] = (fc[9] / buy - 1) * 100 if len(fc) >= 10 else np.nan
            rec['fut20'] = (fc[19] / buy - 1) * 100 if len(fc) >= 20 else np.nan
        else:
            rec.update({'fut3': np.nan, 'fut5': np.nan, 'fut10': np.nan, 'fut20': np.nan})
        out.append(rec)
    return out


# ── 全市场扫描 ──
def scan_date(date: str, jobs: int = 8) -> pd.DataFrame:
    pool = get_stock_pool()
    if pool.empty:
        print('[扫描] 股票池为空')
        return pd.DataFrame()
    regime = market_regime(date)
    print(f'[扫描] {date} 全市场 {len(pool)} 只 | 市场状态: {regime}')
    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), date, regime)
             for _, r in pool.iterrows()]
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, res in enumerate(ex.map(_score_one, tasks, chunksize=64)):
            if res is not None:
                rows.append(r2dict(res))
            if (i + 1) % 1000 == 0:
                print(f'  进度 {i + 1}/{len(tasks)} 已出信号 {len(rows)}')
    df = pd.DataFrame(rows)
    if not df.empty:
        # 过滤非目标交易日（停牌股最后交易日 < 扫描日）
        df = df[df['date'].astype(int) == int(date)].reset_index(drop=True)
        # 按最终评分排序
        df = df.sort_values(['final_score', 'platform_score'], ascending=[False, False]).reset_index(drop=True)
        os.makedirs(REPORT_DIR, exist_ok=True)
        csv_path = os.path.join(REPORT_DIR, f'prb_daily_{date}.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f'[扫描] 保存 {csv_path}')
    print(f'[扫描] 完成，共 {len(df)} 条有效信号')
    return df


# ── 单股状态机诊断 ──
def diagnose(ts_code: str, dates, name: str = ''):
    eng = PRBEngine()
    if not name:
        name = get_name_map().get(ts_code, '')
    for d in dates:
        df = load_daily(ts_code, d, PRB_CONFIG['lookback_bars'])
        if df is None or len(df) < PRB_CONFIG['min_bars']:
            print(f'[{d}] 数据不足')
            continue
        rg = market_regime(d)
        r = eng.score(df, ts_code=ts_code, name=name, market_regime=rg)
        _print_diag(r, d)


def _print_diag(r: PRBResult, date: str):
    line = '=' * 70
    print(line)
    print('PRB 状态机诊断：平台 -> 突破 -> 首次回踩 -> 再启动')
    print(line)
    print(f'股票：{r.name} {r.ts_code}')
    print(f'日期：{date}')
    print()
    print(f'状态机:     {r.state} ({r.state_cn})')
    print(f'交易结论:   {r.action} ({r.action_cn})')
    print(f'原因:       {r.action_reason}')
    print()
    print('[平台]')
    if r.platform_score > 0:
        print(f'  区间:       {r.platform_start} ~ {r.platform_end} ({r.platform_days}日)')
        print(f'  高/低:      {r.platform_high:.2f} / {r.platform_low:.2f}  振幅 {r.platform_range * 100:.1f}%')
        print(f'  上测/下承:  {r.resistance_tests}次 / {r.support_tests}次')
        print(f'  量缩比:     {r.vol_shrink_ratio:.2f}')
        print(f'  分数:       {r.platform_score:.1f} ({r.platform_grade}级)')
    else:
        print('  无合格平台')
    print()
    print('[突破]')
    if r.breakout_score > 0:
        print(f'  日期:       {r.breakout_date}  价格 {r.breakout_price:.2f}')
        print(f'  幅度/量比:  +{r.breakout_pct * 100:.1f}% / {r.breakout_vr:.2f}')
        print(f'  收盘位置:   {r.breakout_candle_pos * 100:.0f}%')
        print(f'  分数:       {r.breakout_score:.1f} ({r.breakout_grade})')
        print(f'  突破后天数: {r.post_breakout_days}')
    else:
        print('  无有效突破')
    print()
    print('[回踩]')
    if r.pullback_days > 0:
        print(f'  低点:       {r.pullback_low:.2f} @ {r.pullback_low_date}')
        print(f'  深度/天数:  {r.pullback_depth * 100:.0f}% / {r.pullback_days}日')
        print(f'  量/突破量:  {r.pullback_vol_ratio:.2f}')
        print(f'  跌破BL:     {"是" if r.pullback_below_bl else "否"}')
        print(f'  分数:       {r.pullback_score:.1f}')
    else:
        print('  未发生回踩（或回踩未确认）')
    print()
    print('[再启动]')
    print(f'  ok:         {r.reaccel_ok}')
    print(f'  量比/收盘位: {r.reaccel_vol_ratio:.2f} / {r.reaccel_candle_pos * 100:.0f}%')
    print(f'  close>前日高: {r.reaccel_close_above_prev_high}  MA5>MA10: {r.reaccel_ma5_above_ma10}')
    print()
    print(f'[最终评分]   {r.final_score:.1f}  {r.grade} ({r.grade_cn})')
    if r.forbidden_reasons:
        print(f'[严禁买入]   {"；".join(r.forbidden_reasons)}')
    if r.warnings:
        print(f'[警示]       {"；".join(r.warnings)}')
    print()
    print('条件拆解：')
    for k, v in r.checks.items():
        mark = '✓' if v['ok'] else '✗'
        print(f'  {k:<8} {mark}  {v["detail"]}')
    print()


# ── 历史回测 ──
def backtest(start: str, end: str, step: int = 5, jobs: int = 8) -> pd.DataFrame:
    pool = get_stock_pool()
    if pool.empty:
        print('[回测] 股票池为空')
        return pd.DataFrame()
    trade_dates = get_trade_dates(start, end)
    print(f'[回测] {start}~{end} 交易日 {len(trade_dates)} 天, 步长 {step} 天, 股票池 {len(pool)} 只')
    csv_path = os.path.join(REPORT_DIR, f'prb_backtest_{start}_{end}.csv')
    os.makedirs(REPORT_DIR, exist_ok=True)
    # 断点续跑：若已有部分结果文件，跳过已完成的股票
    done = set()
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        try:
            prev = pd.read_csv(csv_path)
            if 'ts_code' in prev.columns:
                done = set(prev['ts_code'].astype(str))
                print(f'[回测] 检测到已存在 {len(done)} 只股票的结果，断点续跑')
        except Exception:
            done = set()
    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), start, end, step)
             for _, r in pool.iterrows()
             if str(r['ts_code']) not in done]
    print(f'[回测] 本次需处理 {len(tasks)} 只（已跳过 {len(done)} 只）')
    rows = []
    saved_any = False
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, res in enumerate(ex.map(_backtest_one, tasks, chunksize=4)):
            if res:
                rows.extend(res)
            if (i + 1) % 200 == 0:
                print(f'  进度 {i + 1}/{len(tasks)} 累计信号 {len(rows)}', flush=True)
                # 增量 checkpoint：把新结果追加到已有文件
                if rows:
                    new_df = pd.DataFrame(rows)
                    prev_df = pd.read_csv(csv_path) if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0 else pd.DataFrame()
                    merged = pd.concat([prev_df, new_df], ignore_index=True) if not prev_df.empty else new_df
                    merged.to_csv(csv_path, index=False, encoding='utf-8-sig')
                    saved_any = True
                    rows = []
    if rows:
        new_df = pd.DataFrame(rows)
        prev_df = pd.read_csv(csv_path) if (saved_any or (os.path.exists(csv_path) and os.path.getsize(csv_path) > 0)) else pd.DataFrame()
        merged = pd.concat([prev_df, new_df], ignore_index=True) if not prev_df.empty else new_df
        merged.to_csv(csv_path, index=False, encoding='utf-8-sig')
    df = pd.read_csv(csv_path)
    if df.empty:
        print('[回测] 无信号')
        return df
    # 每个股票×突破事件只保留 final 最高的一笔
    raw_n = len(df)
    df = (df.sort_values(['date', 'final_score'], ascending=[True, False])
            .drop_duplicates(subset=['ts_code', 'breakout_date'], keep='first')
            .reset_index(drop=True))
    print(f'[回测] 去重(每股每突破保留最高分): {raw_n} -> {len(df)}')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'[回测] 明细已保存 {csv_path}')
    return df


def backtest_stats(df: pd.DataFrame, action_filter=None) -> dict:
    if df.empty:
        return {}
    g = df if action_filter is None else df[df['action'] == action_filter]
    if g.empty:
        return {}
    st = {'n': len(g)}
    for h in ('fut3', 'fut5', 'fut10', 'fut20'):
        s = g[h].dropna()
        st[f'{h}_mean'] = float(s.mean()) if len(s) else np.nan
        st[f'{h}_win'] = float((s > 0).mean() * 100) if len(s) else np.nan
    return st


# ── 主入口 ──
def main():
    ap = argparse.ArgumentParser(description='PRB 平台突破回踩再启动 买点引擎 V1.0')
    ap.add_argument('--date', default='', help='交易日 YYYYMMDD，可逗号分隔多个')
    ap.add_argument('--symbol', default='', help='单股诊断 6位代码')
    ap.add_argument('--top', type=int, default=20, help='TOP N')
    ap.add_argument('--backtest', nargs=2, metavar=('START', 'END'), help='历史回测区间')
    ap.add_argument('--step', type=int, default=5, help='回测调仓步长(交易日)')
    ap.add_argument('--jobs', type=int, default=8, help='并行进程数')
    ap.add_argument('--report', action='store_true', help='生成 Markdown 报告')
    args = ap.parse_args()

    if args.backtest:
        start, end = args.backtest
        df = backtest(start, end, step=args.step, jobs=args.jobs)
        print_backtest_report(df)
        if args.report:
            from prb.report import write_backtest_report
            write_backtest_report(df, start, end)
        return

    # 确定目标日期
    dates = []
    if args.date:
        dates = [d.strip() for d in args.date.split(',') if d.strip()]
    if not dates:
        last = last_trade_date_on_or_before(datetime.date.today().strftime('%Y%m%d'))
        dates = [last] if last else [datetime.date.today().strftime('%Y%m%d')]

    if args.symbol:
        ts_code = to_ts_code(args.symbol)
        name = get_name_map().get(ts_code, '')
        for d in dates:
            print(f'>>> 诊断 {ts_code} {name} @ {d}')
            diagnose(ts_code, [d], name=name)
        return

    # 全市场扫描
    regime = market_regime(dates[0])
    df = scan_date(dates[0], jobs=args.jobs)
    if df.empty:
        print('无有效信号')
        return
    print_table(df.head(args.top))
    if args.report:
        from prb.report import write_daily_report
        write_daily_report(df, dates[0], regime=regime)


def print_table(df: pd.DataFrame):
    cols = ['ts_code', 'name', 'state_cn', 'action_cn', 'platform_score',
            'breakout_score', 'pullback_score', 'final_score', 'grade',
            'breakout_date', 'post_breakout_days', 'pullback_depth', 'pullback_vol_ratio']
    header = {'ts_code': '代码', 'name': '名称', 'state_cn': '状态', 'action_cn': '结论',
              'platform_score': '平台分', 'breakout_score': '突破分',
              'pullback_score': '回踩分', 'final_score': '总分', 'grade': '级',
              'breakout_date': '突破日', 'post_breakout_days': '后天数',
              'pullback_depth': '回深', 'pullback_vol_ratio': '回/突量'}
    fmt = {'ts_code': '{}', 'name': '{}', 'state_cn': '{}', 'action_cn': '{}',
           'platform_score': '{:.1f}', 'breakout_score': '{:.1f}',
           'pullback_score': '{:.1f}', 'final_score': '{:.1f}', 'grade': '{}',
           'breakout_date': '{}', 'post_breakout_days': '{:d}',
           'pullback_depth': '{:.0%}', 'pullback_vol_ratio': '{:.2f}'}
    print()
    line = ' | '.join([str(header[c]).ljust(8) for c in cols])
    print(line)
    print('-' * len(line))
    for _, r in df.head(50).iterrows():
        cells = []
        for c in cols:
            v = r[c]
            try:
                cells.append(fmt[c].format(v).ljust(8))
            except Exception:
                cells.append(str(v).ljust(8))
        print(' | '.join(cells))


def print_backtest_report(df: pd.DataFrame):
    if df.empty:
        print('回测无信号')
        return
    print()
    print('=' * 90)
    print('PRB 历史回测结果')
    print('=' * 90)
    overall = backtest_stats(df)
    print(f'\n全部信号: {overall["n"]} 笔')
    print(f'  未来5日: 均值 {overall["fut5_mean"]:+.2f}% | 胜率 {overall["fut5_win"]:.1f}%')
    print(f'  未来10日: 均值 {overall["fut10_mean"]:+.2f}% | 胜率 {overall["fut10_win"]:.1f}%')
    print(f'  未来20日: 均值 {overall["fut20_mean"]:+.2f}% | 胜率 {overall["fut20_win"]:.1f}%')
    print('\n[按交易结论]')
    for act in ('PRIMARY_BUY', 'EARLY_BUY', 'CONFIRMED_BUY', 'WAIT_PULLBACK', 'WAIT_REACCELERATION'):
        g = df[df['action'] == act]
        if len(g):
            st = backtest_stats(g)
            print(f'  {act:<20} {len(g):5d} 笔 | 5日 {st["fut5_mean"]:+.2f}% | 10日 {st["fut10_mean"]:+.2f}% | '
                  f'20日 {st["fut20_mean"]:+.2f}% | 5日胜率 {st["fut5_win"]:.1f}%')
    print()


if __name__ == '__main__':
    main()
