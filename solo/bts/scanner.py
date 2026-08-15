# -*- coding: utf-8 -*-
"""
BTS Trend Start Scanner V1.0 —— CLI 入口
================================================
用法:
  python -m bts.scanner --date 20260814               # 全市场扫描某交易日
  python -m bts.scanner --date 20260814 --top 20      # 只输出 TOP20
  python -m bts.scanner --date 20260810,20260811 --symbol 300404   # 单股诊断
  python -m bts.scanner --backtest 20240101 20260814  # 历史回测
  python -m bts.scanner --report                      # 生成 Markdown 报告
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

from bts.config import BTS_CONFIG, REPORT_DIR
from bts.data import (load_daily, get_stock_pool, get_name_map, market_regime,
                      get_trade_dates, to_ts_code, last_trade_date_on_or_before,
                      load_total_mv, load_total_mv_series)
from bts.engine import BTSEngine, BTSResult


# ── 工作进程函数（multiprocessing 需要模块级可 picklable）──
def _score_one(args):
    ts_code, name, industry, date, regime = args
    df = load_daily(ts_code, date, BTS_CONFIG['lookback_bars'])
    if df is None or len(df) < BTS_CONFIG['min_bars']:
        return None
    eng = BTSEngine()
    mv = load_total_mv(ts_code, date) or 0.0
    r = eng.score(df, ts_code=ts_code, name=name, industry=industry,
                  market_regime=regime, market_cap=mv)
    if r.signal == 'NO_SIGNAL':
        return None
    # 前一交易日快照（用于 NEW/CONTINUE/UPGRADE/DOWNGRADE 状态判定）
    r.status = _cross_day_status(eng, df, r.entry_score)
    return r


def _cross_day_status(eng: 'BTSEngine', df: pd.DataFrame, entry_now: float) -> str:
    """对比前一日评分：NEW=昨日无信号；UPGRADE/DOWNGRADE=Entry变化>=5；其余 CONTINUE"""
    if len(df) < 2:
        return 'NEW'
    prev = df.iloc[:-1].reset_index(drop=True)
    if len(prev) < BTS_CONFIG['min_bars']:
        return 'NEW'
    rp = eng.score(prev, ts_code='', name='', market_regime='neutral')
    if rp.signal == 'NO_SIGNAL':
        return 'NEW'
    diff = entry_now - rp.entry_score
    if diff >= 5:
        return 'UPGRADE'
    if diff <= -5:
        return 'DOWNGRADE'
    return 'CONTINUE'


_REGIME_CACHE = {}


def _get_regime(d):
    """工作进程内的市场状态缓存（避免逐任务传输大字典）"""
    if d not in _REGIME_CACHE:
        _REGIME_CACHE[d] = market_regime(d)
    return _REGIME_CACHE[d]


def _backtest_one(args):
    """单只股票全程回测：加载一次全历史，逐调仓日截断评分，附未来收益(labels)"""
    ts_code, name, industry, start, end, step = args
    df = load_daily(ts_code, end, lookback_bars=1100)
    if df is None or len(df) < 200:
        return []
    dates = df['trade_date'].astype(str).tolist()
    # V1.3：市值序列 {date: 亿元}（一次加载）
    mv_series = load_total_mv_series(ts_code) or {}
    # 预计算 vol_ma20 用于快速预筛
    vol = df['vol'].values.astype(float)
    vma20 = pd.Series(vol).rolling(20).mean().values
    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    eng = BTSEngine()
    out = []
    n = len(df)
    # 只处理 [start, end] 且落在调仓步长的日期
    for idx in range(60, n):
        d = dates[idx]
        if d < start or d > end:
            continue
        if step > 1 and (idx - 60) % step != 0:
            continue
        # 快速预筛：价格接近/突破近25日高点，或明显放量
        lo = idx - 25
        p25 = highs[lo:idx].max()
        c = closes[idx]
        v = vol[idx]
        vv = vma20[idx]
        if not (c > p25 * 0.99 or (not np.isnan(vv) and vv > 0 and v > vv * 1.5)):
            continue
        sub = df.iloc[:idx + 1]
        if len(sub) < BTS_CONFIG['min_bars']:
            continue
        r = eng.score(sub, ts_code=ts_code, name=name, industry=industry,
                      market_regime=_get_regime(d), market_cap=mv_series.get(d, 0.0))
        if r.signal == 'NO_SIGNAL':
            continue
        rec = r2dict(r)
        # 未来收益（label，仅用于统计）
        fut = df.iloc[idx + 1:]
        buy = closes[idx]
        if buy > 0 and len(fut) > 0:
            fc = fut['close'].values.astype(float)
            # 除权缺口检测：未来20日窗口内任一日相邻涨跌幅绝对值 > 25%（不复权数据的高送转/配股缺口）
            chg_all = np.concatenate([[closes[idx]], fc[:20]])
            adj_gap = bool(np.any(np.abs(chg_all[1:] / chg_all[:-1] - 1) > 0.25))
            if adj_gap:
                # 该信号未来收益被除权缺口污染，剔除
                return []
            rec['fut3'] = (fc[2] / buy - 1) * 100 if len(fc) >= 3 else np.nan
            rec['fut5'] = (fc[4] / buy - 1) * 100 if len(fc) >= 5 else np.nan
            rec['fut10'] = (fc[9] / buy - 1) * 100 if len(fc) >= 10 else np.nan
            rec['fut20'] = (fc[19] / buy - 1) * 100 if len(fc) >= 20 else np.nan
            peak = np.maximum.accumulate(fc[:20])
            rec['fut_max_dd'] = float(np.min(fc[:20] / peak - 1)) * 100 if len(fc) else np.nan
        else:
            rec.update({'fut3': np.nan, 'fut5': np.nan, 'fut10': np.nan, 'fut20': np.nan, 'fut_max_dd': np.nan})
        out.append(rec)
    return out


def r2dict(r: BTSResult) -> dict:
    return {
        'ts_code': r.ts_code, 'name': r.name, 'industry': r.industry, 'date': r.date,
        'bts': r.bts_score, 'entry': r.entry_score, 'grade': r.grade,
        'signal': r.signal, 'signal_cn': r.signal_cn, 'buy_point': r.buy_point,
        'breakout_date': r.breakout_date, 'days_after': r.days_after_breakout,
        'base_days': r.base_days, 'base_range': r.base_range, 'base_slope': r.base_slope,
        'resistance': r.resistance, 'touches': r.resistance_touches,
        'close': r.close, 'ma5': r.ma5, 'ma10': r.ma10, 'ma20': r.ma20, 'ma60': r.ma60,
        'dist_ma5': r.distance_ma5, 'dist_ma20': r.distance_ma20,
        'vol': r.vol, 'vol_ma5': r.vol_ma5, 'vol_ma20': r.vol_ma20,
        'vol_ratio': r.vol_ratio, 'vol_ratio_bo': r.vol_ratio_breakout,
        'persist': r.volume_persistence, 'up_dn': r.up_down_ratio,
        'ma5_slope1': r.ma5_slope_1, 'ma5_streak': r.ma5_up_streak,
        'pullback_depth': r.pullback_depth, 'trend_eff': r.trend_eff,
        'ma5_track': r.ma5_track, 'rsi': r.rsi,
        'day1_premium': r.day1_premium, 'sector_heat': r.sector_heat,
        'market_cap': r.market_cap, 'score_mv': r.score_mv,
        'extra_raw': r.extra_raw,
        'sustained_ok': r.sustained_ok,
        'is_mainline': False, 'mainline_heat': 0.0,
        'high_120d_new': r.high_120d_new, 'high_120d_prev': r.high_120d_prev,
        'core': r.core_reason, 'risk': '；'.join(r.risk_factors), 'action': r.action,
        'regime': r.market_regime, 'status': r.status,
    }


def _extra_weight(bts: pd.Series, cfg: dict) -> pd.Series:
    """BTS 分档附加分权重（V1.4）：高分股附加分压缩，避免 Entry 饱和失真"""
    w = pd.Series(1.0, index=bts.index)
    # 按 BTS 下限降序遍历（高门槛先赋权），只给未赋权的行赋值（高权重档不会被低权重档覆盖）
    for lo, wi in sorted(cfg['extra_compress'], key=lambda x: -x[0]):
        m = (bts >= lo) & (w == 1.0)
        if m.any():
            w.loc[m] = wi
    return w


def apply_sector_heat(df: pd.DataFrame) -> pd.DataFrame:
    """行业/板块共振因子（V1.1）：按(日期×行业)统计当日信号数，>=min_sig 后每1只 +per_sig 加到 Entry。

    引擎单股无法看到全市场，故由 scanner 汇总后叠加；回测按 (date, industry) 分组建模同样逻辑。
    V1.4：叠加后按 BTS 分档压缩附加分，防止高分股 Entry 饱和 100、排序失去区分度。
    """
    if df is None or df.empty or 'industry' not in df.columns:
        return df
    cfg = BTS_CONFIG
    min_sig = cfg['sector_heat_min_sig']
    per_sig = cfg['sector_heat_per_sig']
    heat_max = cfg['sector_heat_max']
    df = df.copy()
    df['sector_heat'] = 0.0
    df['mainline_heat'] = 0.0
    df['is_mainline'] = False
    ml = cfg.get('mainline', {})
    for (dt, ind), grp in df.groupby(['date', 'industry']):
        cnt = len(grp)
        if cnt >= min_sig:
            heat = min(heat_max, cnt * per_sig)
            df.loc[grp.index, 'sector_heat'] = heat
    # V1.8：主线板块识别——当日信号数最多的 TOP_N 行业（且 >= min_sig 防噪声）
    # kind='stable' 保证并列时优先信号质量更高的行业（df 已按 entry 降序），结果确定可复现
    if ml.get('enabled', True) and len(df) >= ml.get('min_sig', 5):
        top_ind = (df.groupby(['date', 'industry']).size()
                   .reset_index(name='n').sort_values(['date', 'n'], ascending=[True, False], kind='stable')
                   .groupby('date').head(ml.get('top_n', 2)))
        top_set = set(zip(top_ind['date'], top_ind['industry']))
        m = df.set_index(['date', 'industry']).index.isin(top_set)
        df.loc[m, 'is_mainline'] = True
        df.loc[m, 'mainline_heat'] = ml.get('premium', 4.0)
    # 附加分 = 引擎未压缩(day1+市值) + 行业共振 + 主线加分，统一压缩后重算 Entry
    if 'extra_raw' in df.columns:
        extra = (df['extra_raw'].fillna(0.0) + df['sector_heat'] + df['mainline_heat']).clip(0, 99)
        w = _extra_weight(df['bts'], cfg)
        df['entry'] = (df['bts'] + extra * w).clip(0, 100)
    df = df.sort_values(['entry', 'bts'], ascending=[False, False]).reset_index(drop=True)
    return df


# ── 全市场扫描 ──
def scan_date(date: str, jobs: int = 8, min_grade: str = 'C') -> pd.DataFrame:
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
            if res is not None and res.grade not in ('NO_BUY',):
                rows.append(r2dict(res))
            if (i + 1) % 1000 == 0:
                print(f'  进度 {i + 1}/{len(tasks)} 已出信号 {len(rows)}')
    df = pd.DataFrame(rows)
    if not df.empty:
        # 过滤非目标交易日（停牌股最后交易日 < 扫描日），避免昨日信号混入污染行业共振/主线识别
        df = df[df['date'].astype(int) == int(date)].reset_index(drop=True)
        df = apply_sector_heat(df)
        # V1.2 买入池强过滤：只收 Day1 且 S/A/B；V1.7 扩展：突破后稳步向上+量能充沛(持续确认)也可进池
        if BTS_CONFIG['buy_pool_day1_only'] and 'day1_premium' in df.columns:
            sb = BTS_CONFIG.get('sustained_buy', {})
            sb_ok = df.get('sustained_ok', pd.Series(False, index=df.index))
            keep = ((df['day1_premium'] > 0) | sb_ok.fillna(False)) & df['grade'].isin(('S', 'A', 'B'))
            n_pool = int(keep.sum())
            drop = ~keep
            df.loc[drop & df['buy_point'].isin(('BUY-A', 'BUY-B', 'BUY-C')), 'buy_point'] = 'WATCH'
            print(f'[扫描] 买入池过滤(Day1+持续确认且S/A/B): 买入池 {n_pool} 只')
        df = df.sort_values(['entry', 'bts'], ascending=[False, False]).reset_index(drop=True)
        csv_path = os.path.join(REPORT_DIR, f'bts_daily_{date}.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'[扫描] 完成，共 {len(df)} 条有效信号')
    return df


# ── 单股诊断 ──
def diagnose(ts_code: str, dates, regime: str = None, name: str = ''):
    eng = BTSEngine()
    if not name:
        name = get_name_map().get(ts_code, '')
    for d in dates:
        df = load_daily(ts_code, d, BTS_CONFIG['lookback_bars'])
        if df is None or len(df) < BTS_CONFIG['min_bars']:
            print(f'[{d}] 数据不足')
            continue
        rg = regime or market_regime(d)
        mv = load_total_mv(ts_code, d) or 0.0
        r = eng.score(df, ts_code=ts_code, name=name, market_regime=rg, market_cap=mv)
        _print_diag(r, d)


def _print_diag(r: BTSResult, date: str):
    line = '=' * 60
    print(line)
    print('BTS Trend Start Diagnostic')
    print(line)
    print(f'股票：{r.name} {r.ts_code.split(".")[0]}')
    print(f'日期：{date}')
    print()
    print(f'BTS Score       {r.bts_score}')
    print(f'Entry Score     {r.entry_score}')
    print(f'Signal          {r.signal} ({r.signal_cn})')
    print(f'Grade           {r.grade}')
    print(f'Days Breakout   {r.days_after_breakout}')
    print(f'Breakout Date   {r.breakout_date}')
    print()
    print('[Base]')
    print(f'  Base Range    {r.base_range * 100:.1f}%     {"✓" if r.base_range <= 0.20 else "○"}')
    print(f'  Resistance    {r.resistance:.2f}       ✓')
    print(f'  Touches       {r.resistance_touches}          {"✓" if r.resistance_touches >= 3 else "○"}')
    print(f'  Base Days     {r.base_days}日')
    print()
    print('[Breakout]')
    print(f'  Breakout      {r.breakout_amp * 100:+.1f}%       {"✓" if r.breakout_amp >= 0.01 else "✗"}')
    print(f'  Volume Ratio  {r.vol_ratio_breakout:.2f}        {"✓" if r.vol_ratio_breakout >= 1.3 else "✗"}')
    print(f'  Candle Pos    {r.candle_pos * 100:.0f}%         {"✓" if r.candle_pos >= 0.60 else "✗"}')
    print()
    print('[MA5]')
    print(f'  MA5           {r.ma5:.2f}')
    print(f'  MA5 Slope     {r.ma5_slope_1 * 100:+.2f}%      {"✓" if r.ma5_slope_1 > 0 else "✗"}')
    print(f'  Close/MA5     {r.distance_ma5 * 100:+.2f}%      {"✓" if 0 <= r.distance_ma5 <= 0.10 else "✗"}')
    print(f'  MA5 Hold      {r.ma5_track}/5       {"✓" if r.ma5_track >= 4 else "○"}')
    print()
    print('[Volume]')
    print(f'  VOL/MA20      {r.vol_ratio:.2f}        {"✓" if r.vol_ratio >= 1.2 else "✗"}')
    print(f'  Persistence   {r.volume_persistence}/5      {"✓" if r.volume_persistence >= 3 else "✗"}')
    print(f'  Up/Down Vol   {r.up_down_ratio:.2f}        {"✓" if r.up_down_ratio >= 1.2 else "✗"}')
    print()
    print('[Risk]')
    ext_ok = r.distance_ma5 <= BTS_CONFIG['max_ma5_distance']
    ext_tag = 'LOW' if r.distance_ma5 <= 0.08 else ('MEDIUM' if ext_ok else 'HIGH')
    print(f'  Extension     {ext_tag:5s}     {"✓" if ext_ok else "✗"}')
    print(f'  Fake Breakout {"LOW" if not r.post_breakout_failed else "HIGH":5s}     {"✓" if not r.post_breakout_failed else "✗"}')
    print(f'  Over Volume   {"LOW" if not r.spike_volume else "HIGH":5s}     {"✓" if not r.spike_volume else "✗"}')
    print()
    if r.market_cap > 0:
        print(f'[Fundamental]')
        print(f'  总市值        {r.market_cap:.1f} 亿    {r.score_mv:+.1f}')
        print()
    print('[Decision]')
    stars = {'S': 5, 'A': 4, 'B': 3, 'C': 2, 'D': 1}.get(r.grade, 0)
    print(f'{"★" * stars}{"☆" * (5 - stars)} {r.grade}')
    print(r.signal_cn if r.signal != 'NO_SIGNAL' else '无信号')
    print()
    print('条件拆解：')
    for k, v in r.checks.items():
        mark = '✓' if v['ok'] else '✗'
        print(f'  {k:<12} {mark}  {v["detail"]}')
    if r.core_reason:
        print(f'  核心原因: {r.core_reason}')
    if r.risk_factors:
        print(f'  风险因素: {"; ".join(r.risk_factors)}')
    if r.action:
        print(f'  建议操作: {r.action}')
    print()


# ── 历史回测 ──
def backtest(start: str, end: str, step: int = 5, jobs: int = 8, dedup: bool = True) -> pd.DataFrame:
    pool = get_stock_pool()
    if pool.empty:
        print('[回测] 股票池为空')
        return pd.DataFrame()
    # 交易日历（用于进度提示）
    trade_dates = get_trade_dates(start, end)
    print(f'[回测] {start}~{end} 交易日 {len(trade_dates)} 天, 步长 {step} 天, 股票池 {len(pool)} 只')

    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), start, end, step)
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
    # V1.1：行业共振按 (date, industry) 汇总后叠加 Entry（须在去重前，去重依赖 Entry 排序）
    df = apply_sector_heat(df)
    if dedup:
        # 每个"股票×突破事件"只保留 Entry 最高的一笔（同一突破段的重复信号去重）
        raw_n = len(df)
        df = (df.sort_values(['date', 'entry', 'bts'], ascending=[True, False, False])
                .drop_duplicates(subset=['ts_code', 'breakout_date'], keep='first')
                .reset_index(drop=True))
        print(f'[回测] 去重(每股每突破保留最高Entry): {raw_n} -> {len(df)}')
    # 保存 CSV 便于分析
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        csv_path = os.path.join(REPORT_DIR, f'bts_backtest_{start}_{end}.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f'[回测] 明细已保存 {csv_path}')
    except Exception as e:
        print(f'[回测] CSV保存失败: {e}')
    return df


# ── 回测统计 ──
def backtest_stats(df: pd.DataFrame, grade_filter=None) -> dict:
    if df.empty:
        return {}
    g = df if grade_filter is None else df[df['grade'] == grade_filter]
    if g.empty:
        return {}
    st = {}
    st['n'] = len(g)
    for h in ('fut3', 'fut5', 'fut10', 'fut20'):
        s = g[h].dropna()
        st[f'{h}_mean'] = float(s.mean()) if len(s) else np.nan
        st[f'{h}_med'] = float(s.median()) if len(s) else np.nan
        st[f'{h}_win'] = float((s > 0).mean() * 100) if len(s) else np.nan
        st[f'{h}_max'] = float(s.max()) if len(s) else np.nan
        st[f'{h}_min'] = float(s.min()) if len(s) else np.nan
    dd = g['fut_max_dd'].dropna()
    st['max_dd'] = float(dd.min()) if len(dd) else np.nan
    s5 = g['fut5'].dropna()
    wins = s5[s5 > 0].sum()
    losses = -s5[s5 < 0].sum()
    st['profit_factor'] = float(wins / losses) if losses > 0 else np.nan
    st['avg_win'] = float(s5[s5 > 0].mean()) if (s5 > 0).any() else np.nan
    st['avg_loss'] = float(s5[s5 < 0].mean()) if (s5 < 0).any() else np.nan
    st['profit_ratio'] = (st['avg_win'] / abs(st['avg_loss'])) if st.get('avg_loss') and abs(st['avg_loss']) > 0 else np.nan
    return st


# ── 主入口 ──
def main():
    ap = argparse.ArgumentParser(description='BTS 平台突破+MA5趋势启动 选股引擎 V1.0')
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
            from bts.report import write_backtest_report
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
        name_map = get_name_map()
        name = name_map.get(ts_code, '')
        for d in dates:
            print(f'>>> 诊断 {ts_code} {name} @ {d}')
            diagnose(ts_code, [d])
        return

    # 全市场扫描
    regime = market_regime(dates[0])
    df = scan_date(dates[0], jobs=args.jobs)
    if df.empty:
        print('无有效信号')
        return
    print_table(df.head(args.top))
    if args.report:
        from bts.report import write_daily_report
        write_daily_report(df, dates[0], regime=regime)


def print_table(df: pd.DataFrame):
    cols = ['ts_code', 'name', 'bts', 'entry', 'grade', 'signal_cn', 'buy_point', 'status',
            'breakout_date', 'days_after', 'dist_ma5', 'vol_ratio', 'persist', 'up_dn']
    header = {'ts_code': '代码', 'name': '名称', 'bts': 'BTS', 'entry': 'Entry',
              'grade': '级', 'signal_cn': '信号', 'buy_point': '买点', 'status': '状态',
              'breakout_date': '突破日', 'days_after': '后N日',
              'dist_ma5': '距MA5', 'vol_ratio': '量比', 'persist': '量持续', 'up_dn': '涨跌量比'}
    fmt = {'ts_code': '{}', 'name': '{}', 'bts': '{:.1f}', 'entry': '{:.1f}', 'grade': '{}',
           'signal_cn': '{}', 'buy_point': '{}', 'status': '{}', 'breakout_date': '{}', 'days_after': '{:d}',
           'dist_ma5': '{:+.1%}', 'vol_ratio': '{:.2f}', 'persist': '{:d}', 'up_dn': '{:.2f}'}
    print()
    line = ' | '.join([header[c].rjust(8) for c in cols])
    print(line)
    print('-' * len(line))
    for _, r in df.head(50).iterrows():
        cells = []
        for c in cols:
            v = r[c]
            try:
                cells.append(fmt[c].format(v).rjust(8))
            except Exception:
                cells.append(str(v).rjust(8))
        print(' | '.join(cells))


def print_backtest_report(df: pd.DataFrame):
    if df.empty:
        print('回测无信号')
        return
    print()
    print('=' * 90)
    print('BTS 历史回测结果')
    print('=' * 90)
    overall = backtest_stats(df)
    print(f'\n全部信号: {overall["n"]} 笔')
    print(f'  未来3日: 均值 {overall["fut3_mean"]:+.2f}% | 胜率 {overall["fut3_win"]:.1f}%')
    print(f'  未来5日: 均值 {overall["fut5_mean"]:+.2f}% | 中位 {overall["fut5_med"]:+.2f}% | 胜率 {overall["fut5_win"]:.1f}% | 盈亏比 {overall["profit_ratio"]:.2f} | PF {overall["profit_factor"]:.2f}')
    print(f'  未来10日: 均值 {overall["fut10_mean"]:+.2f}% | 胜率 {overall["fut10_win"]:.1f}%')
    print(f'  未来20日: 均值 {overall["fut20_mean"]:+.2f}% | 胜率 {overall["fut20_win"]:.1f}%')
    print(f'  最大回撤: {overall["max_dd"]:.2f}%')
    for gr in ('S', 'A', 'B', 'C'):
        st = backtest_stats(df, gr)
        if st.get('n'):
            print(f'\n  {gr}级: {st["n"]} 笔 | 5日均值 {st["fut5_mean"]:+.2f}% | 10日均值 {st["fut10_mean"]:+.2f}% | '
                  f'20日均值 {st["fut20_mean"]:+.2f}% | 5日胜率 {st["fut5_win"]:.1f}% | 盈亏比 {st["profit_ratio"]:.2f}')
    # 买入池（S/A/B）合计
    buy_pool = df[df['grade'].isin(('S', 'A', 'B'))]
    if len(buy_pool):
        st = backtest_stats(buy_pool)
        print(f'\n[买入池 S+A+B] {len(buy_pool)} 笔 | 3日 {st["fut3_mean"]:+.2f}% | 5日 {st["fut5_mean"]:+.2f}% | '
              f'10日 {st["fut10_mean"]:+.2f}% | 20日 {st["fut20_mean"]:+.2f}% | 5日胜率 {st["fut5_win"]:.1f}% | '
              f'盈亏比 {st["profit_ratio"]:.2f} | PF {st["profit_factor"]:.2f}')
    # 信号类型分项
    print('\n[信号类型] (5日/10日/20日均值, 笔数)')
    for sig in ('BREAKOUT_NOW', 'PULLBACK_BUY', 'TREND_START', 'TREND_EXTENDED', 'FAILED_BREAKOUT'):
        g = df[df['signal'] == sig]
        if len(g):
            st = backtest_stats(g)
            print(f'  {sig:<16} {len(g):6d} | 5日 {st["fut5_mean"]:+.2f}% | 10日 {st["fut10_mean"]:+.2f}% | '
                  f'20日 {st["fut20_mean"]:+.2f}% | 胜率 {st["fut5_win"]:.1f}%')
    # 分数分档单调性验证（BTS越高收益应越高）
    print('\n[BTS 分数分档] (5日/10日/20日均值)')
    for lo, hi in ((85, 101), (78, 85), (70, 78), (60, 70), (50, 60)):
        g = df[(df['bts'] >= lo) & (df['bts'] < hi)]
        if len(g):
            st = backtest_stats(g)
            print(f'  BTS[{lo},{hi}): {len(g)}笔 | 5日 {st["fut5_mean"]:+.2f}% | 10日 {st["fut10_mean"]:+.2f}% | 20日 {st["fut20_mean"]:+.2f}% | 胜率 {st["fut5_win"]:.1f}%')
    print()


if __name__ == '__main__':
    main()
