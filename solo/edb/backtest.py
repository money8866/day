# -*- coding: utf-8 -*-
"""
EDB 历史回测（规范第 24 节）
================================================================================
目标：
  ① 验证 EDB / EDB_STRONG / EDB_PULLBACK / EDB_REBREAKOUT 的
     T+3 / T+5 / T+10 / T+20 胜率、平均收益、中位收益、最大回撤、盈亏比；
  ② 特别验证 BreakoutVR 区间（2.0~2.5 / 2.5~3.0 / 3.0~5.0 / >5.0）的未来收益差异，
     **不假设「量越大越好」**，一律以历史回测为准；
  ③ 事件标注：信号后 20 根K线内的「停牌复牌 / 连板一字板」路径单独标记
     （ev_susp / ev_limit 列）。此类样本收益属事件驱动（如停牌重组→复牌连板），
     不代表形态可复制，统计层同时给出含/剔事件双口径（T+20 三件套）。

无未来函数：
  · 信号日只用 <= 信号日 的行情（load_daily 出口统一截断 + df.iloc[:idx+1]）；
  · 未来收益仅作为 label 统计，不参与任何信号判定。
"""
import functools
import os

import numpy as np
import pandas as pd

from edb.config import EDB_CONFIG, REPORT_DIR
from edb.data import load_daily, get_stock_pool, get_trade_dates
from edb.engine import EdbEngine


# ═══════════════════════════════════════════════════════════
# 事件标注与工具（工作进程内使用）
# ═══════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=8)
def _cal(start: str, end: str) -> tuple:
    """全市场交易日历（每个工作进程缓存一次；获取失败返回空 → 停牌检测降级关闭）"""
    try:
        return tuple(get_trade_dates(start, end))
    except Exception:
        return ()


def _trim_mean(values) -> float:
    """剔前二离群均值（样本 <8 不修剪，直接返回均值；空返回 NaN）"""
    s = np.sort(np.asarray(values, dtype=float))
    if s.size == 0:
        return np.nan
    k = 2 if s.size >= 8 else 0
    return float(s[:s.size - k].mean())


def _event_flags(high, low, close, dates_int, cal_arr, idx: int, n: int):
    """信号后 20 根K线内的事件标注（未来窗口仅作 label，不影响信号）。

    ev_limit 连板/一字板：窗口内「连续 >=2 日涨幅 >=+9.5%」，
      或任一日一字板（high==low 且涨幅 >=+5%，全天单一价格=无法正常买入）；
    ev_susp 停牌：窗口内「市场开市但个股无K线」累计 >=2 个交易日
      （基于全市场交易日历，节假日天然不误报）。
    返回 (停牌交易日数, ev_susp, ev_limit)。
    """
    w1 = min(idx + 20, n - 1)
    susp_days = 0
    if cal_arr.size and w1 > idx:
        lo = np.searchsorted(cal_arr, dates_int[idx:w1], side='right')
        hi = np.searchsorted(cal_arr, dates_int[idx + 1:w1 + 1], side='left')
        susp_days = int(np.sum(hi - lo))
    ev_limit = 0
    if w1 > idx:
        pc = close[idx:w1]
        cc = close[idx + 1:w1 + 1]
        with np.errstate(divide='ignore', invalid='ignore'):
            mv = cc / pc - 1
        mv = np.where(np.isfinite(mv), mv, 0.0)
        one_price = (high[idx + 1:w1 + 1] == low[idx + 1:w1 + 1]) & (mv >= 0.05)
        streak = mv >= 0.095
        pair = (streak[:-1] & streak[1:]) if streak.size >= 2 else np.array([], dtype=bool)
        ev_limit = int(bool(one_price.any() or pair.any()))
    return susp_days, int(susp_days >= 2), ev_limit


# ═══════════════════════════════════════════════════════════
# 工作进程
# ═══════════════════════════════════════════════════════════
def _backtest_one(args):
    ts_code, name, industry, start, end, step = args
    df = load_daily(ts_code, end, lookback_bars=1100)
    if df is None or len(df) < EDB_CONFIG['min_bars'] + 30:
        return []
    dates = df['trade_date'].astype(str).tolist()
    close = df['close'].to_numpy(dtype=float)
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    vol = df['vol'].to_numpy(dtype=float)
    n = len(df)
    dates_int = np.array([int(x) for x in dates], dtype=np.int64)
    cal_arr = np.array(_cal(start, end), dtype=np.int64)

    # ── 向量化预筛：DryUpRatio（排除当日）< 0.45 的日子才值得评分 ──
    v = pd.Series(vol)
    ma20v = v.rolling(20).mean().shift(1).to_numpy()
    ma120v = v.rolling(120).mean().shift(1).to_numpy()
    with np.errstate(divide='ignore', invalid='ignore'):
        dry = np.where((ma120v > 0), ma20v / ma120v, np.nan)

    eng = EdbEngine()
    lo_i = EDB_CONFIG['min_bars'] - 1
    out = []
    for idx in range(lo_i, n):
        d = dates[idx]
        if d < start or d > end:
            continue
        if step > 1 and (idx - lo_i) % step != 0:
            continue
        # 预筛：最近10日内存在 DryUpRatio <= dry_up_normal
        # （= 引擎最宽 dry 准入边界：突破日 _classify_today 与突破后 _classify_after
        #   的闸门均为 dry_up_normal，PRE_EDB 的 dry_up_pass 更窄；
        #   仅性能预筛，判定正确性由引擎闸门保证）
        w = dry[max(0, idx - EDB_CONFIG['recent_breakout_window']):idx + 1]
        if not np.any(np.isfinite(w) & (w <= EDB_CONFIG['dry_up_normal'])):
            continue
        sub = df.iloc[:idx + 1]
        r = eng.score(sub, ts_code=ts_code, name=name, industry=industry, date=d)
        if r is None:
            continue
        rec = {
            'ts_code': ts_code, 'name': name, 'industry': industry, 'date': d,
            'status': r.status, 'action': r.action,
            'edb_score': r.edb_score, 'risk_penalty': r.risk_penalty,
            'edb_score_adj': r.edb_score_adj,
            'base_days': r.base_days, 'base_range': r.base_range, 'range60': r.range60,
            'dry_up_ratio': r.dry_up_ratio, 'minvol10_ratio': r.minvol10_ratio,
            'breakout_vr': r.breakout_vr, 'breakout_distance': r.breakout_distance,
            'day_gain': r.day_gain, 'close_position': r.close_position,
            'atr_ratio': r.atr_ratio, 'breakout_date': r.breakout_date,
            'days_after': r.days_after, 'risk_tags': '；'.join(r.risk_tags),
        }
        buy = close[idx]
        fut = close[idx + 1:]
        sd, es, el = _event_flags(high, low, close, dates_int, cal_arr, idx, n)
        rec.update({'ev_susp_days': sd, 'ev_susp': es, 'ev_limit': el})
        if buy > 0 and len(fut) > 0:
            # 除权缺口检测（不复权数据的高送转/配股缺口）→ 该样本未来收益不可信，剔除
            chg = np.concatenate([[buy], fut[:20]])
            if np.any(np.abs(chg[1:] / chg[:-1] - 1) > 0.25):
                continue
            rec['fut3'] = (fut[2] / buy - 1) * 100 if len(fut) >= 3 else np.nan
            rec['fut5'] = (fut[4] / buy - 1) * 100 if len(fut) >= 5 else np.nan
            rec['fut10'] = (fut[9] / buy - 1) * 100 if len(fut) >= 10 else np.nan
            rec['fut20'] = (fut[19] / buy - 1) * 100 if len(fut) >= 20 else np.nan
            peak = np.maximum.accumulate(fut[:20])
            rec['fut_max_dd'] = float(np.min(fut[:20] / peak - 1)) * 100 if len(fut) else np.nan
        else:
            rec.update({'fut3': np.nan, 'fut5': np.nan, 'fut10': np.nan,
                        'fut20': np.nan, 'fut_max_dd': np.nan})
        out.append(rec)
    return out


def dedup_episodes(df: pd.DataFrame) -> pd.DataFrame:
    """按「每股 × 每状态 × 事件」去重（backtest 与参数扫描共用，单一代码源）。

    突破类：以「突破日」为事件键 —— 同一次突破被多次采样只计一次；
    状态类（PRE_EDB 等无突破日）：同一股票同一状态、采样间隔 <=14 日历日
    视为同一段持续状态，只保留首次出现，避免把一段横盘重复计数。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    bd = df['breakout_date'].fillna('').astype(str)
    df['_key'] = bd
    m_st = bd.str.len() == 0
    if m_st.any():
        sub = df.loc[m_st, ['ts_code', 'status', 'date']].copy()
        sub['_d'] = pd.to_datetime(sub['date'], format='%Y%m%d')
        sub = sub.sort_values(['ts_code', 'status', '_d'])
        gap = sub.groupby(['ts_code', 'status'])['_d'].diff().dt.days.fillna(999.0)
        sub['_ep'] = (gap > 14).cumsum()
        df.loc[m_st, '_key'] = 'EP' + sub['_ep'].astype(str)
    df = (df.sort_values(['date', 'edb_score_adj'], ascending=[True, False])
            .drop_duplicates(subset=['ts_code', 'status', '_key'], keep='first')
            .drop(columns=['_key']).reset_index(drop=True))
    return df


def backtest(start: str, end: str, step: int = 5, jobs: int = 8, dedup: bool = True) -> pd.DataFrame:
    from concurrent.futures import ProcessPoolExecutor
    pool = get_stock_pool(exclude_st=EDB_CONFIG['exclude_st'])
    if pool.empty:
        print('[回测] 股票池为空')
        return pd.DataFrame()
    print(f'[回测] {start}~{end} 步长 {step} 交易日 股票池 {len(pool)} 只')
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
    if dedup:
        raw = len(df)
        df = dedup_episodes(df)
        print(f'[回测] 去重(每股每状态每事件保留首次): {raw} -> {len(df)}')
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        p = os.path.join(REPORT_DIR, f'edb_backtest_{start}_{end}.csv')
        df.to_csv(p, index=False, encoding='utf-8-sig')
        print(f'[回测] 明细已保存 {p}')
    except Exception as e:
        print(f'[回测] CSV保存失败: {e}')
    return df


# ═══════════════════════════════════════════════════════════
# 统计
# ═══════════════════════════════════════════════════════════
def backtest_stats(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}
    st = {'n': len(df)}
    for h in ('fut3', 'fut5', 'fut10', 'fut20'):
        s = pd.to_numeric(df[h], errors='coerce').dropna()
        st[f'{h}_mean'] = float(s.mean()) if len(s) else np.nan
        st[f'{h}_med'] = float(s.median()) if len(s) else np.nan
        st[f'{h}_win'] = float((s > 0).mean() * 100) if len(s) else np.nan
    dd = pd.to_numeric(df['fut_max_dd'], errors='coerce').dropna()
    st['max_dd'] = float(dd.min()) if len(dd) else np.nan
    # ── T+20 三件套（中位/胜率已在上方循环给出；此处补剔前二均值与事件双口径）──
    s20 = pd.to_numeric(df['fut20'], errors='coerce').dropna()
    ev = pd.Series(0, index=df.index, dtype=float)
    if 'ev_susp' in df.columns:
        ev = ev + pd.to_numeric(df['ev_susp'], errors='coerce').fillna(0)
    if 'ev_limit' in df.columns:
        ev = ev + pd.to_numeric(df['ev_limit'], errors='coerce').fillna(0)
    st['fut20_n'] = int(len(s20))
    st['fut20_ev_n'] = int((ev > 0).sum())
    if len(s20):
        st['fut20_trim'] = _trim_mean(s20)
        st['fut20_out_pp'] = float(s20.mean()) - st['fut20_trim']
        exev = s20[ev.reindex(s20.index).fillna(0) == 0]
        st['fut20_trim_exev'] = _trim_mean(exev) if len(exev) else np.nan
    else:
        st.update({'fut20_trim': np.nan, 'fut20_out_pp': np.nan,
                   'fut20_trim_exev': np.nan})
    s5 = pd.to_numeric(df['fut5'], errors='coerce').dropna()
    if len(s5):
        wins = float(s5[s5 > 0].sum())
        losses = float(-s5[s5 < 0].sum())
        st['profit_factor'] = wins / losses if losses > 0 else np.nan
        st['avg_win'] = float(s5[s5 > 0].mean()) if (s5 > 0).any() else np.nan
        st['avg_loss'] = float(s5[s5 < 0].mean()) if (s5 < 0).any() else np.nan
        st['profit_ratio'] = (st['avg_win'] / abs(st['avg_loss'])
                              if st.get('avg_loss') and abs(st['avg_loss']) > 0 else np.nan)
    else:
        st.update({'profit_factor': np.nan, 'profit_ratio': np.nan,
                   'avg_win': np.nan, 'avg_loss': np.nan})
    return st


def _pf(v, w: int = 7) -> str:
    """终端数值格式化：NaN/异常 → 右对齐占位"""
    try:
        f = float(v)
        return f'{f:+{w}.2f}' if np.isfinite(f) else '—'.rjust(w)
    except (TypeError, ValueError):
        return '—'.rjust(w)


def _tri_line(st: dict) -> str:
    """T+20 三件套补充行：中位 / 胜率 / 剔前二均值 + 离群贡献 + 含/剔事件双口径"""
    if not np.isfinite(st.get('fut20_trim', np.nan)):
        return ''
    try:
        win = f'{float(st["fut20_win"]):5.1f}'
    except (TypeError, ValueError, KeyError):
        win = '  —  '
    return (f'  {"":<18} {"":>6}   └ 三件套T+20：中位 {_pf(st.get("fut20_med"))}% | '
            f'胜率 {win}% | 剔前二均值 {_pf(st.get("fut20_trim"))}%'
            f'（离群贡献 {_pf(st.get("fut20_out_pp"), 5)}pp | 事件样本 {st.get("fut20_ev_n", 0)}，'
            f'剔事件 {_pf(st.get("fut20_trim_exev"))}%）')


def _row(label: str, st: dict) -> str:
    if not st.get('n'):
        return f'  {label:<18} {"—":>6}'
    base = (f'  {label:<18} {st["n"]:>6} | 3日 {st["fut3_mean"]:+6.2f}% | 5日 {st["fut5_mean"]:+6.2f}% | '
            f'10日 {st["fut10_mean"]:+6.2f}% | 20日 {st["fut20_mean"]:+6.2f}% | '
            f'5日胜率 {st["fut5_win"]:5.1f}% | 5日中位 {st["fut5_med"]:+6.2f}% | '
            f'最大回撤 {st["max_dd"]:6.2f}% | 盈亏比 {st["profit_ratio"]:.2f}')
    tri = _tri_line(st)
    return base + '\n' + tri if tri else base


def print_backtest_report(df: pd.DataFrame):
    if df is None or df.empty:
        print('回测无信号')
        return
    print()
    print('═' * 108)
    print('EDB 历史回测结果（T+N 收益；未来数据仅作 label；T+20 附中位/胜率/剔前二三件套）')
    print('═' * 108)
    print(_row('全部', backtest_stats(df)))
    if 'ev_susp' in df.columns or 'ev_limit' in df.columns:
        e1 = (pd.to_numeric(df['ev_susp'], errors='coerce').fillna(0)
              if 'ev_susp' in df.columns else pd.Series(0, index=df.index))
        e2 = (pd.to_numeric(df['ev_limit'], errors='coerce').fillna(0)
              if 'ev_limit' in df.columns else pd.Series(0, index=df.index))
        evm = df[(e1 + e2) > 0]
        print()
        print(f'[事件标注] 停牌复牌 / 连板一字板样本 {len(evm)} 只'
              f'（事件驱动收益，不代表形态可复制；逐笔标注见 CSV 列 ev_susp / ev_limit）')
        for _, r in evm.iterrows():
            tags = []
            try:
                if float(r.get('ev_susp', 0) or 0) == 1:
                    tags.append(f'停牌{int(float(r.get("ev_susp_days", 0) or 0))}日')
            except (TypeError, ValueError):
                pass
            try:
                if float(r.get('ev_limit', 0) or 0) == 1:
                    tags.append('连板/一字板')
            except (TypeError, ValueError):
                pass
            f20 = pd.to_numeric(r.get('fut20'), errors='coerce')
            f20s = f'{f20:+.2f}%' if np.isfinite(f20) else '—'
            print(f'  {r.get("ts_code", "")} {str(r.get("name", "")):<8} {r.get("date", "")} '
                  f'{str(r.get("status", "")):<14} {"+".join(tags)}  T+20 {f20s}')
    print()
    print('[按 EDB 状态]')
    for st_name in ('EDB_STRONG', 'EDB', 'EDB_WATCH', 'PRE_EDB',
                    'EDB_PULLBACK', 'EDB_REBREAKOUT', 'EVENT_ONLY'):
        g = df[df['status'] == st_name]
        if len(g):
            print(_row(st_name, backtest_stats(g)))
    print()
    print('[BreakoutVR 分区间验证] —— 核心：是否「量越大越好」')
    bo = df[df['status'].isin(('EDB_STRONG', 'EDB', 'EDB_WATCH'))]
    if bo.empty:
        print('  无突破类样本')
    else:
        vr = pd.to_numeric(bo['breakout_vr'], errors='coerce')
        for lab, lo, hi in (('2.0~2.5', 2.0, 2.5), ('2.5~3.0', 2.5, 3.0),
                            ('3.0~5.0', 3.0, 5.0), ('>5.0', 5.0, 1e9)):
            g = bo[(vr >= lo) & (vr < hi)]
            if len(g):
                print(_row(f'VR {lab}', backtest_stats(g)))
    print()
    print('[DryUpRatio 分区间验证]')
    dry = pd.to_numeric(df['dry_up_ratio'], errors='coerce')
    for lab, lo, hi in (('<0.35', 0.0, 0.35), ('0.35~0.45', 0.35, 0.45),
                        ('0.45~0.55', 0.45, 0.55), ('>0.55', 0.55, 9.0)):
        g = df[(dry >= lo) & (dry < hi)]
        if len(g):
            print(_row(f'DryUp {lab}', backtest_stats(g)))
    print()
    print('[EDB_SCORE_ADJ 分档单调性验证]')
    sc = pd.to_numeric(df['edb_score_adj'], errors='coerce')
    for lo, hi in ((80, 101), (70, 80), (60, 70), (0, 60)):
        g = df[(sc >= lo) & (sc < hi)]
        if len(g):
            print(_row(f'Score [{lo},{hi})', backtest_stats(g)))
    print('═' * 108)
