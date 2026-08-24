# -*- coding: utf-8 -*-
"""
PBP Scanner -- CLI 入口
================================================
用法:
  python -m pbp --date 20260821                    # 全市场扫描
  python -m pbp --date 20260821 --top 30           # 只输出 TOP30
  python -m pbp --symbol 300404                    # 单股诊断（最近交易日）
  python -m pbp --symbol 300404 --date 20260818,20260821   # 单股多日诊断
  python -m pbp --backtest 20260101 20260821       # 历史回测
  python -m pbp --report                           # 扫描后生成 Markdown 报告
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

from pbp.config import PBP_CONFIG, REPORT_DIR
from pbp.data import (load_daily, get_stock_pool, get_name_map, get_trade_dates,
                      to_ts_code, last_trade_date_on_or_before, load_stock_basic)
from pbp.market import market_regime, theme_strength, theme_score
from pbp.engine import PBPEngine, PBPResult


# ── 工作进程函数（multiprocessing 需要 picklable）──
_G_IND_MAP = {}


def _get_industry_map():
    if not _G_IND_MAP:
        sb = load_stock_basic()
        if not sb.empty and 'industry' in sb.columns:
            _G_IND_MAP.update(dict(zip(sb['ts_code'], sb['industry'].fillna(''))))
    return _G_IND_MAP


def _get_theme(date_str: str):
    """工作进程内的行业主题缓存"""
    global _THEME_CACHE_PROC
    try:
        key = str(date_str)
        if key not in _THEME_CACHE_PROC:
            ind_map = _get_industry_map()
            _THEME_CACHE_PROC[key] = theme_strength(key, ind_map)
        return _THEME_CACHE_PROC[key]
    except Exception:
        return {}


_THEME_CACHE_PROC = {}


def _theme_info_for(industry: str, date_str: str) -> dict:
    if not industry:
        return {'score': 0.0}
    ts = _get_theme(date_str)
    if not ts or industry not in ts:
        return {'score': 0.0}
    t = ts[industry]
    ret5_all = [v['ret5'] for v in ts.values()]
    med = float(np.median(ret5_all)) if ret5_all else 0.0
    s = 0.0
    if t['ret5'] > med:
        s += 2.0
    if t['up_ratio'] > PBP_CONFIG['theme_up_ratio_min']:
        s += 1.5
    if t['amount_chg'] > 0:
        s += 1.0
    if t.get('rank_pct') is not None and t['rank_pct'] <= PBP_CONFIG['extreme_theme_rank']:
        s += 0.5
    return {
        'score': min(5.0, s), 'ret5': t['ret5'], 'up_ratio': t['up_ratio'],
        'rank_pct': t.get('rank_pct'), 'amount_chg': t['amount_chg'], 'rank': t.get('rank'),
    }


_REGIME_CACHE = {}


def _get_regime(d):
    if d not in _REGIME_CACHE:
        _REGIME_CACHE[d] = market_regime(d)
    return _REGIME_CACHE[d]


def _score_one(args):
    ts_code, name, industry, date = args
    df = load_daily(ts_code, date, PBP_CONFIG['lookback_bars'])
    if df is None or len(df) < PBP_CONFIG['min_bars']:
        return None
    eng = PBPEngine()
    ti = _theme_info_for(industry, date)
    r = eng.score(df, ts_code=ts_code, name=name, industry=industry,
                  market_regime=_get_regime(date), theme_info=ti)
    # 过滤纯观察态（无任何结构信息）
    if r.state in ('PLATFORM_BUILDING', 'INVALIDATED') and not r.platform_found:
        return None
    if r.action in ('NO_TRADE', 'WAIT_PLATFORM') and r.final_score < 50:
        return None
    return r


# ── 回测：单股全程 ──
def _backtest_one(args):
    ts_code, name, industry, start, end = args
    df = load_daily(ts_code, end, lookback_bars=900)
    if df is None or len(df) < 200:
        return []
    from pbp.indicators import enrich
    df = enrich(df)  # 预计算指标，score() 内跳过重复计算
    dates = df['trade_date'].astype(str).tolist()
    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    vols = df['vol'].values.astype(float)
    vma20 = pd.Series(vols).rolling(20).mean().values
    n = len(df)
    # 向量化预筛：突破候选日（收盘创近25日高点95分位 且 放量>=1.3*volMA20）
    is_break_cand = np.zeros(n, dtype=bool)
    for i in range(25, n):
        p95 = float(np.quantile(highs[i - 25:i], 0.95))
        vv = vma20[i]
        if closes[i] > p95 and (not np.isnan(vv) and vv > 0 and vols[i] > vv * 1.3):
            is_break_cand[i] = True
    eng = PBPEngine()
    out = []
    for idx in range(PBP_CONFIG['min_bars'], n):
        d = dates[idx]
        if d < start or d > end:
            continue
        # 预筛：近15日内存在突破候选日（无突破则必无回踩/转强信号）
        if not is_break_cand[max(0, idx - 15):idx + 1].any():
            continue
        sub = df.iloc[:idx + 1]
        ti = _theme_info_for(industry, d)
        r = eng.score(sub, ts_code=ts_code, name=name, industry=industry,
                      market_regime=_get_regime(d), theme_info=ti, cache_key=id(df))
        if r.action in ('NO_TRADE', 'WAIT_PLATFORM', 'BREAKOUT_FAILED', 'PULLBACK_FAILED'):
            continue
        rec = r2dict(r)
        # 未来收益（label，仅统计用，不进入评分）
        fut = df.iloc[idx + 1:]
        buy = closes[idx]
        if buy > 0 and len(fut) > 0:
            fc = fut['close'].values.astype(float)
            rec['fut3'] = (fc[2] / buy - 1) * 100 if len(fc) >= 3 else np.nan
            rec['fut5'] = (fc[4] / buy - 1) * 100 if len(fc) >= 5 else np.nan
            rec['fut10'] = (fc[9] / buy - 1) * 100 if len(fc) >= 10 else np.nan
            rec['fut20'] = (fc[19] / buy - 1) * 100 if len(fc) >= 20 else np.nan
        # EARLY_BUY 门槛诊断（仅回踩类信号）
        if r.action in ('WAIT_REACCELERATION', 'EARLY_BUY', 'PRIMARY_BUY', 'CONFIRMED_BUY'):
            low_today = lows[idx]
            rec['eb_depth_ok'] = bool(0.15 <= r.pullback_depth <= 0.80) if r.pullback_started else False
            rec['eb_vol_ok'] = bool(r.pullback_vol_ratio <= 0.80) if r.pullback_started else False
            rec['eb_broke_ok'] = bool(not r.pullback_broke_level)
            rec['eb_c5_ok'] = bool(r.close <= r.ma5) if r.ma5 > 0 else False
            rec['eb_low_ok'] = bool(r.pullback_low_date < r.date) if r.pullback_low_date else False
            rec['eb_nl_ok'] = bool(low_today >= r.pullback_low) if r.pullback_started else False
            rec['eb_all_ok'] = bool(rec['eb_depth_ok'] and rec['eb_vol_ok'] and rec['eb_broke_ok']
                                    and rec['eb_c5_ok'] and rec['eb_low_ok'] and rec['eb_nl_ok'])
        out.append(rec)
    return out


def r2dict(r: PBPResult) -> dict:
    return {
        'ts_code': r.ts_code, 'name': r.name, 'industry': r.industry, 'date': r.date,
        'state': r.state, 'action': r.action,
        'platform_start': r.platform_start, 'platform_end': r.platform_end,
        'platform_high': r.platform_high, 'platform_low': r.platform_low,
        'platform_score': r.platform_score, 'platform_grade': r.platform_grade,
        'platform_days': r.platform_days, 'platform_range': r.platform_range,
        'res_tests': r.resistance_tests, 'sup_tests': r.support_tests,
        'breakout_date': r.breakout_date, 'breakout_pct': r.breakout_pct,
        'breakout_price': r.breakout_price, 'breakout_level': r.breakout_level,
        'breakout_grade': r.breakout_grade,
        'breakout_close_loc': r.breakout_close_loc,
        'breakout_vol_ratio': r.breakout_vol_ratio, 'breakout_score': r.breakout_score,
        'days_after': r.breakout_days_ago,
        'pullback_start': r.pullback_start, 'pullback_low_date': r.pullback_low_date,
        'pullback_days': r.pullback_days, 'pullback_depth': r.pullback_depth,
        'pullback_vol_ratio': r.pullback_vol_ratio, 'pullback_score': r.pullback_score,
        'pullback_low': r.pullback_low, 'broke_level': r.pullback_broke_level,
        'n_evidence': r.pullback_end_evidence,
        'evidences': '、'.join(r.pullback_end_evidences),
        'reacc_date': r.reacc_date, 'reacc_price': r.reacc_price,
        'reacc_vol_ratio': r.reacc_vol_ratio,
        'reacc_close_loc': r.reacc_close_loc,
        'final_score': r.final_score, 'stars': r.stars,
        'score_platform': r.score_platform, 'score_breakout': r.score_breakout,
        'score_pullback': r.score_pullback, 'score_reacc': r.score_reacc,
        'theme_score': r.theme_score, 'market_regime': r.market_regime,
        'close': r.close, 'ma5': r.ma5, 'atr20': r.atr20,
        'reasons': '；'.join(r.reasons),
    }


# ── 全市场扫描 ──
def scan_date(date: str, jobs: int = 8, top: int = 0) -> pd.DataFrame:
    pool = get_stock_pool()
    if pool.empty:
        print('[扫描] 股票池为空')
        return pd.DataFrame()
    regime = market_regime(date)
    print(f'[扫描] {date} 全市场 {len(pool)} 只 | 市场环境: {regime}')
    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), date)
             for _, r in pool.iterrows()]
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, res in enumerate(ex.map(_score_one, tasks, chunksize=32)):
            if res is not None:
                rows.append(r2dict(res))
            if (i + 1) % 1000 == 0:
                print(f'  进度 {i + 1}/{len(tasks)} 有效结果 {len(rows)}')
    df = pd.DataFrame(rows)
    if df.empty:
        print('[扫描] 无有效结果')
        return df
    # 过滤非目标交易日（停牌股）
    df = df[df['date'].astype(int) == int(date)].reset_index(drop=True)
    df = df.sort_values(['final_score'], ascending=False).reset_index(drop=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    csv_path = os.path.join(REPORT_DIR, f'pbp_daily_{date}.csv')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'[扫描] 完成 {len(df)} 条 | 已保存 {csv_path}')
    return df


# ── 单股诊断 ──
def diagnose(ts_code: str, dates, name: str = ''):
    eng = PBPEngine()
    if not name:
        name = get_name_map().get(ts_code, '')
    for d in dates:
        df = load_daily(ts_code, d, PBP_CONFIG['lookback_bars'])
        if df is None or len(df) < PBP_CONFIG['min_bars']:
            print(f'[{d}] 数据不足')
            continue
        regime = _get_regime(d)
        ind_map = _get_industry_map()
        ti = _theme_info_for(ind_map.get(ts_code, ''), d)
        r = eng.score(df, ts_code=ts_code, name=name,
                      industry=ind_map.get(ts_code, ''),
                      market_regime=regime, theme_info=ti)
        print_diag(r, d)


def print_diag(r: PBPResult, date: str):
    line = '=' * 62
    print(line)
    print(f'PBP 诊断  {r.name} {r.ts_code}  @ {date}  市场环境: {r.market_regime}')
    print(line)
    print(f'状态: {r.state}')
    print(f'交易结论: {r.action}')
    print()
    print('[平台]')
    if r.platform_found:
        print(f'  区间 {r.platform_start} ~ {r.platform_end} ({r.platform_days}日)')
        print(f'  高点 {r.platform_high:.2f} / 低点 {r.platform_low:.2f} / 幅度 {r.platform_range*100:.1f}%')
        print(f'  阻力测试 {r.resistance_tests}次 / 支撑测试 {r.support_tests}次')
        print(f'  量能收缩 {r.platform_vol_shrink:.2f} / ATR压缩 {r.platform_atr_compress:.2f}'
              f'{"(收敛)" if r.platform_volatility_converge else ""}')
        print(f'  PLATFORM_SCORE = {r.platform_score:.1f} ({r.platform_grade}级)')
    else:
        print('  未识别到有效平台')
    print()
    print('[突破]')
    if r.breakout_found:
        print(f'  突破日 {r.breakout_date}  突破位 {r.breakout_level:.2f}  突破价 {r.breakout_price:.2f}')
        print(f'  幅度 {r.breakout_pct*100:+.2f}%  量比 {r.breakout_vol_ratio:.2f}  '
              f'收盘位置 {r.breakout_close_loc:.2f}')
        print(f'  BREAKOUT_SCORE = {r.breakout_score:.1f} ({r.breakout_grade})  已过{r.breakout_days_ago}日')
    else:
        print('  尚无有效突破')
    print()
    print('[回踩]')
    if r.pullback_started:
        print(f'  起点 {r.pullback_start}  最低 {r.pullback_low:.2f}({r.pullback_low_date})  '
              f'天数 {r.pullback_days}日')
        print(f'  深度 {r.pullback_depth*100:.0f}%  量/突破量 {r.pullback_vol_ratio:.2f}  '
              f'是否跌破突破位: {"是" if r.pullback_broke_level else "否"}')
        print(f'  止跌证据 {r.pullback_end_evidence}项: {"、".join(r.pullback_end_evidences)}')
        print(f'  PULLBACK_SCORE = {r.pullback_score:.1f}')
    else:
        print('  尚未进入回踩阶段')
    print()
    print('[重新转强]')
    if r.reacc_found:
        print(f'  日期 {r.reacc_date}  价格 {r.reacc_price:.2f}  量比 {r.reacc_vol_ratio:.2f}  '
              f'收盘位置 {r.reacc_close_loc:.2f}')
    else:
        print('  未触发')
    print()
    print('[最终评分]')
    print(f'  平台 {r.score_platform:.1f}/30 + 突破 {r.score_breakout:.1f}/25 + '
          f'回踩 {r.score_pullback:.1f}/25 + 转强 {r.score_reacc:.1f}/20')
    print(f'  最终评分 = {r.final_score:.1f}  {r.grade}')
    print()
    if r.reasons:
        print('判定过程:')
        for x in r.reasons:
            print(f'  - {x}')
    print()


# ── 历史回测 ──
def backtest(start: str, end: str, jobs: int = 8) -> pd.DataFrame:
    pool = get_stock_pool()
    if pool.empty:
        print('[回测] 股票池为空')
        return pd.DataFrame()
    print(f'[回测] {start}~{end} 股票池 {len(pool)} 只')
    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), start, end)
             for _, r in pool.iterrows()]
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, res in enumerate(ex.map(_backtest_one, tasks, chunksize=8)):
            if res:
                rows.extend(res)
            if (i + 1) % 500 == 0:
                print(f'  进度 {i + 1}/{len(tasks)} 累计信号 {len(rows)}')
    df = pd.DataFrame(rows)
    if df.empty:
        print('[回测] 无信号')
        return df
    # 去重：每个(股票, 突破日)只保留最终分最高的一笔
    raw_n = len(df)
    df = (df.sort_values(['date', 'final_score'], ascending=[True, False])
            .drop_duplicates(subset=['ts_code', 'breakout_date'], keep='first')
            .reset_index(drop=True))
    print(f'[回测] 去重 {raw_n} -> {len(df)}')
    os.makedirs(REPORT_DIR, exist_ok=True)
    csv_path = os.path.join(REPORT_DIR, f'pbp_backtest_{start}_{end}.csv')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'[回测] 已保存 {csv_path}')
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
    s5 = g['fut5'].dropna()
    wins = s5[s5 > 0].sum()
    losses = -s5[s5 < 0].sum()
    st['pf'] = float(wins / losses) if losses > 0 else np.nan
    return st


def print_backtest_report(df: pd.DataFrame):
    if df.empty:
        print('回测无信号')
        return
    print()
    print('=' * 90)
    print('PBP 历史回测结果（3-5日短线：平台->突破->首踩->再启动）')
    print('=' * 90)
    for act in ('PRIMARY_BUY', 'CONFIRMED_BUY', 'EARLY_BUY', 'WAIT_REACCELERATION'):
        st = backtest_stats(df, act)
        if st.get('n'):
            print(f'\n[{act}] {st["n"]} 笔')
            print(f'  3日: {st["fut3_mean"]:+.2f}% (胜率{st["fut3_win"]:.0f}%)  '
                  f'5日: {st["fut5_mean"]:+.2f}% (胜率{st["fut5_win"]:.0f}%)  '
                  f'10日: {st["fut10_mean"]:+.2f}%  20日: {st["fut20_mean"]:+.2f}%  PF: {st["pf"]:.2f}')
    st = backtest_stats(df)
    print(f'\n[全部信号] {st["n"]} 笔')
    print(f'  3日: {st["fut3_mean"]:+.2f}%  5日: {st["fut5_mean"]:+.2f}% (胜率{st["fut5_win"]:.0f}%)  '
          f'10日: {st["fut10_mean"]:+.2f}%  20日: {st["fut20_mean"]:+.2f}%  PF: {st["pf"]:.2f}')
    # 最终分分档单调性
    print('\n[最终分分档] (5日/10日/20日均值)')
    for lo, hi in ((90, 101), (85, 90), (78, 85), (70, 78), (0, 70)):
        g = df[(df['final_score'] >= lo) & (df['final_score'] < hi)]
        if len(g):
            st = backtest_stats(g)
            print(f'  [{lo},{hi}): {st["n"]}笔 | 5日 {st["fut5_mean"]:+.2f}% | 10日 {st["fut10_mean"]:+.2f}% | '
                  f'20日 {st["fut20_mean"]:+.2f}% | 5日胜率 {st["fut5_win"]:.0f}%')
    print()


def print_table(df: pd.DataFrame, top: int = 20):
    if df.empty:
        return
    cols = ['ts_code', 'name', 'action', 'final_score', 'stars', 'state',
            'breakout_date', 'days_after', 'pullback_days', 'pullback_vol_ratio',
            'reacc_vol_ratio', 'platform_score', 'breakout_score', 'pullback_score']
    header = {'ts_code': '代码', 'name': '名称', 'action': '结论', 'final_score': '总分',
              'stars': '星', 'state': '状态', 'breakout_date': '突破日', 'days_after': '后N日',
              'pullback_days': '回踩日', 'pullback_vol_ratio': '踩量/突量',
              'reacc_vol_ratio': '转强量比', 'platform_score': '平台分',
              'breakout_score': '突破分', 'pullback_score': '回踩分'}
    print()
    print(' | '.join([header[c] for c in cols]))
    print('-' * 150)
    for _, r in df.head(top).iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in ('final_score', 'platform_score', 'breakout_score', 'pullback_score'):
                cells.append(f'{v:.1f}')
            elif c in ('pullback_vol_ratio', 'reacc_vol_ratio'):
                cells.append(f'{v:.2f}')
            elif c in ('days_after', 'pullback_days', 'stars'):
                cells.append(f'{int(v)}' if pd.notna(v) else '')
            else:
                cells.append(str(v))
        print(' | '.join(cells))


def main():
    ap = argparse.ArgumentParser(description='PBP 平台->突破->首踩->再启动 买点识别引擎 V1.0')
    ap.add_argument('--date', default='', help='交易日 YYYYMMDD，可逗号分隔多个')
    ap.add_argument('--symbol', default='', help='单股诊断 6位代码')
    ap.add_argument('--top', type=int, default=30, help='TOP N')
    ap.add_argument('--backtest', nargs=2, metavar=('START', 'END'), help='历史回测区间')
    ap.add_argument('--jobs', type=int, default=8, help='并行进程数')
    ap.add_argument('--report', action='store_true', help='生成 Markdown 报告')
    args = ap.parse_args()

    if args.backtest:
        start, end = args.backtest
        df = backtest(start, end, jobs=args.jobs)
        print_backtest_report(df)
        return

    dates = []
    if args.date:
        dates = [d.strip() for d in args.date.split(',') if d.strip()]
    if not dates:
        last = last_trade_date_on_or_before(datetime.date.today().strftime('%Y%m%d'))
        dates = [last] if last else [datetime.date.today().strftime('%Y%m%d')]

    if args.symbol:
        ts_code = to_ts_code(args.symbol)
        name_map = get_name_map()
        name = name_map.get(ts_code, '')
        for d in dates:
            print(f'>>> 诊断 {ts_code} {name} @ {d}')
            diagnose(ts_code, [d], name=name)
        return

    df = scan_date(dates[0], jobs=args.jobs)
    if not df.empty:
        print_table(df, top=args.top)
        if args.report:
            from pbp.report import write_daily_report
            regime = _get_regime(dates[0])
            write_daily_report(df, dates[0], regime=regime)


if __name__ == '__main__':
    main()
