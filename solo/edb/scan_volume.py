# -*- coding: utf-8 -*-
"""
EDB 信号量扩容 —— 闸门参数扫描
================================================================================
目标：在「剔事件口径（T+20 中位 / 胜率 / 剔前二）不恶化」的前提下，
扫描各准入闸门的放松空间，找「信号数↑ × 干净指标不塌」的平台档，
而不是拍脑袋放宽（backtest-expert：seek plateaus not peaks / n>=30 / punish）。

架构（零口径漂移）：
  · 单趟数据加载 × 多组参数变体：worker 内逐采样点循环
    「EDB_CONFIG.update(变体) → 跑真实 eng.score() → 恢复基线」；
    引擎无实例级配置快照（cfg 为同一 dict 引用），换配置即时生效；
  · 未来收益 / 事件标注与参数无关 → 每采样点算一次、全变体共用；
  · 预筛固定放宽到 PRESCREEN_DRY_MAX（引擎闸门保证判定正确性，
    基线变体多算的样本会被引擎原样拒绝，不影响行集）；
  · 去重与统计复用 backtest.py 的 dedup_episodes / _trim_mean（单一代码源）。

自校验：BASE 变体（第 0 组）跑完与生产回测 CSV 逐行对齐（行数 / 键集 /
fut20 / edb_score_adj），对不齐则全表结果作废重查。
注意：EVENT_ONLY（一字/连板另分类）在引擎中先于一切缩量闸门短路返回，
其行集随预筛采样密度漂移、与参数闸门无关，且规范定义「不作为 EDB 主信号」
→ 扫描统一排除，自校验在生产基线上同样剔除后再对齐。

用法:
  python -m edb.scan_volume                          # 默认 20240601~20260918
  python -m edb.scan_volume --limit 30               # 试跑前30只（跳过自校验）
  python -m edb.scan_volume --jobs 8 --step 5
"""
import argparse
import os
import sys
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

from edb.config import EDB_CONFIG, REPORT_DIR
from edb.data import load_daily, get_stock_pool
from edb.engine import EdbEngine
from edb.backtest import _cal, _event_flags, _trim_mean, dedup_episodes

# 预筛上限：取扫描矩阵中最宽的 EDB 准入带（0.65）再留一档余量
PRESCREEN_DRY_MAX = 0.70

# ── 参数变体矩阵：第 0 组为基线（自校验锚点），其余为组合叠加验证 ──
# 单杠杆扫描（variant_stats_single.csv）已定出活杠杆：vr_valid / min_amount_yi_20 /
# dry_up_normal / pre_max_gap（edb_score=60 仅主池+2）；本矩阵量化组合叠加：
#   C1/C2 主池双杆 → C3/C4/C5 逐步加观察池杆（保守）→ C6 极限包（平台边缘）
VARIANTS = [
    ('BASE', {}),
    ('C1 vr18+amt020', {'vr_valid': 1.8, 'min_amount_yi_20': 0.20}),
    ('C2 vr16+amt020', {'vr_valid': 1.6, 'min_amount_yi_20': 0.20}),
    ('C3 C2+score60', {'vr_valid': 1.6, 'min_amount_yi_20': 0.20,
                       'edb_score': 60}),
    ('C4 C2+drynorm065', {'vr_valid': 1.6, 'min_amount_yi_20': 0.20,
                          'dry_up_normal': 0.65}),
    ('C5 C4+pregap010', {'vr_valid': 1.6, 'min_amount_yi_20': 0.20,
                         'dry_up_normal': 0.65, 'pre_max_gap': 0.10}),
    ('C6 极限vr16+amt020+dn070+gap012',
     {'vr_valid': 1.6, 'min_amount_yi_20': 0.20, 'dry_up_normal': 0.70,
      'pre_max_gap': 0.12}),
]


# ═══════════════════════════════════════════════════════════
# 工作进程（模块级可 picklable；Windows spawn 下重新 import 本模块）
# ═══════════════════════════════════════════════════════════
def _scan_one(args):
    ts_code, name, industry, start, end, step = args
    df = load_daily(ts_code, end, lookback_bars=1100)
    if df is None or len(df) < EDB_CONFIG['min_bars'] + 30:
        return None
    dates = df['trade_date'].astype(str).tolist()
    close = df['close'].to_numpy(dtype=float)
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    vol = df['vol'].to_numpy(dtype=float)
    n = len(df)
    dates_int = np.array([int(x) for x in dates], dtype=np.int64)
    cal_arr = np.array(_cal(start, end), dtype=np.int64)

    # DryUpRatio 预筛序列（与生产同式：排除当日的 20/120 日均量比）
    v = pd.Series(vol)
    ma20v = v.rolling(20).mean().shift(1).to_numpy()
    ma120v = v.rolling(120).mean().shift(1).to_numpy()
    with np.errstate(divide='ignore', invalid='ignore'):
        dry = np.where((ma120v > 0), ma20v / ma120v, np.nan)

    eng = EdbEngine()
    baseline = dict(EDB_CONFIG)          # 基线快照：变体只覆盖声明的键，跑完恢复
    lo_i = EDB_CONFIG['min_bars'] - 1
    rows = {vn: [] for vn, _ in VARIANTS}
    for idx in range(lo_i, n):
        d = dates[idx]
        if d < start or d > end:
            continue
        if step > 1 and (idx - lo_i) % step != 0:
            continue
        # 预筛固定放宽：最近 recent_breakout_window 日内存在 dry <= 0.70
        w = dry[max(0, idx - baseline['recent_breakout_window']):idx + 1]
        if not np.any(np.isfinite(w) & (w <= PRESCREEN_DRY_MAX)):
            continue
        # 未来 label 与事件标注与参数无关 → 每采样点算一次共用
        buy = close[idx]
        fut = close[idx + 1:]
        sd, es, el = _event_flags(high, low, close, dates_int, cal_arr, idx, n)
        label = {'ev_susp_days': sd, 'ev_susp': es, 'ev_limit': el}
        if buy > 0 and len(fut) > 0:
            chg = np.concatenate([[buy], fut[:20]])
            if np.any(np.abs(chg[1:] / chg[:-1] - 1) > 0.25):
                continue                  # 除权缺口：与生产一致，整点剔除
            label['fut3'] = (fut[2] / buy - 1) * 100 if len(fut) >= 3 else np.nan
            label['fut5'] = (fut[4] / buy - 1) * 100 if len(fut) >= 5 else np.nan
            label['fut10'] = (fut[9] / buy - 1) * 100 if len(fut) >= 10 else np.nan
            label['fut20'] = (fut[19] / buy - 1) * 100 if len(fut) >= 20 else np.nan
            peak = np.maximum.accumulate(fut[:20])
            label['fut_max_dd'] = (float(np.min(fut[:20] / peak - 1)) * 100
                                   if len(fut) else np.nan)
        else:
            label.update({'fut3': np.nan, 'fut5': np.nan, 'fut10': np.nan,
                          'fut20': np.nan, 'fut_max_dd': np.nan})
        sub = df.iloc[:idx + 1]
        for vname, override in VARIANTS:
            try:
                if override:
                    EDB_CONFIG.update(override)
                r = eng.score(sub, ts_code=ts_code, name=name,
                              industry=industry, date=d)
            finally:
                if override:
                    for k in override:
                        EDB_CONFIG[k] = baseline[k]
            if r is None:
                continue
            if r.status == 'EVENT_ONLY':
                continue              # 先于一切闸门短路、行集随预筛密度漂移，规范定义为非主信号
            rec = {
                'ts_code': ts_code, 'name': name, 'industry': industry, 'date': d,
                'status': r.status, 'action': r.action,
                'edb_score': r.edb_score, 'risk_penalty': r.risk_penalty,
                'edb_score_adj': r.edb_score_adj,
                'base_days': r.base_days, 'base_range': r.base_range,
                'range60': r.range60,
                'dry_up_ratio': r.dry_up_ratio, 'minvol10_ratio': r.minvol10_ratio,
                'breakout_vr': r.breakout_vr,
                'breakout_distance': r.breakout_distance,
                'day_gain': r.day_gain, 'close_position': r.close_position,
                'atr_ratio': r.atr_ratio, 'breakout_date': r.breakout_date,
                'days_after': r.days_after, 'risk_tags': '；'.join(r.risk_tags),
            }
            rec.update(label)
            rows[vname].append(rec)
    return ts_code, {vn: recs for vn, recs in rows.items() if recs}


# ═══════════════════════════════════════════════════════════
# 变体统计（剔事件口径与生产报告改革同式）
# ═══════════════════════════════════════════════════════════
MAIN_POOL = ('EDB', 'EDB_STRONG')
_EMPTY_B = {'n': 0, 'mean': np.nan, 'med': np.nan, 'win': np.nan, 'trim': np.nan}


def _num(v, default=-999.0) -> float:
    try:
        f = float(v)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _fmt(v, spec: str) -> str:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ''
        return format(v, spec)
    except Exception:
        return str(v)


def _fut20_block(sub: pd.DataFrame) -> dict:
    s = pd.to_numeric(sub['fut20'], errors='coerce').dropna()
    if not len(s):
        return {'n': 0, 'mean': np.nan, 'med': np.nan, 'win': np.nan, 'trim': np.nan}
    return {'n': int(len(s)), 'mean': float(s.mean()), 'med': float(s.median()),
            'win': float((s > 0).mean() * 100), 'trim': _trim_mean(s)}


def variant_stats(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {'n': 0, 'f20': _EMPTY_B, 'f20_exev': _EMPTY_B,
                'f20_main': _EMPTY_B, 'f20_main_exev': _EMPTY_B}
    st = {'n': len(df)}
    for s, key in (('EDB', 'edb'), ('EDB_STRONG', 'strong'), ('EDB_WATCH', 'watch'),
                   ('PRE_EDB', 'pre'), ('EDB_PULLBACK', 'pullback'),
                   ('EDB_REBREAKOUT', 'rebo'), ('EVENT_ONLY', 'evonly')):
        st[f'n_{key}'] = int((df['status'] == s).sum())
    st['n_main'] = st['n_edb'] + st['n_strong']
    ev = np.zeros(len(df))
    if 'ev_susp' in df.columns:
        ev = ev + pd.to_numeric(df['ev_susp'], errors='coerce').fillna(0).to_numpy()
    if 'ev_limit' in df.columns:
        ev = ev + pd.to_numeric(df['ev_limit'], errors='coerce').fillna(0).to_numpy()
    m_exev = pd.Series(ev == 0, index=df.index)
    st['f20'] = _fut20_block(df)
    st['f20_exev'] = _fut20_block(df[m_exev])
    st['f20_main'] = _fut20_block(df[df['status'].isin(MAIN_POOL)])
    st['f20_main_exev'] = _fut20_block(df[df['status'].isin(MAIN_POOL) & m_exev])
    return st


def stats_row(name: str, df: pd.DataFrame, base: dict) -> dict:
    st = variant_stats(df)
    f, fe, fm, fme = st.get('f20', {}), st.get('f20_exev', {}), \
        st.get('f20_main', {}), st.get('f20_main_exev', {})
    return {
        'variant': name,
        'n': st.get('n', 0),
        'n_main': st.get('n_main', 0),
        'n_pre': st.get('n_pre', 0),
        'n_watch': st.get('n_watch', 0),
        'dn': (st.get('n', 0) - base.get('n', 0)) if base else np.nan,
        'dn_main': (st.get('n_main', 0) - base.get('n_main', 0)) if base else np.nan,
        'f20_n': f.get('n', 0),
        'f20_med': f.get('med'), 'f20_win': f.get('win'), 'f20_trim': f.get('trim'),
        'exev_n': fe.get('n', 0),
        'exev_med': fe.get('med'), 'exev_win': fe.get('win'),
        'exev_trim': fe.get('trim'),
        'main_n': fm.get('n', 0), 'main_med': fm.get('med'),
        'main_exev_n': fme.get('n', 0), 'main_exev_med': fme.get('med'),
        'main_exev_win': fme.get('win'), 'main_exev_trim': fme.get('trim'),
        'd_exev_med': ((fe.get('med') - base.get('f20_exev', {}).get('med'))
                       if base and fe.get('n', 0) and base.get('f20_exev', {}).get('med')
                       is not None and fe.get('med') is not None else np.nan),
        'd_exev_win': ((fe.get('win') - base.get('f20_exev', {}).get('win'))
                       if base and fe.get('n', 0) and base.get('f20_exev', {}).get('win')
                       is not None and fe.get('win') is not None else np.nan),
    }


# ═══════════════════════════════════════════════════════════
# 基线自校验：BASE 变体必须与生产回测 CSV 逐行对齐
# ═══════════════════════════════════════════════════════════
def selfcheck(base_df: pd.DataFrame, start: str, end: str) -> bool:
    p = os.path.join(REPORT_DIR, f'edb_backtest_{start}_{end}.csv')
    if not os.path.exists(p):
        print(f'[自校验] 未找到生产基线 {p} —— 跳过（BASE 统计仍输出）')
        return True
    prod = pd.read_csv(p, dtype={'ts_code': str, 'date': str, 'breakout_date': str})
    # EVENT_ONLY 行集随预筛采样密度漂移（引擎先于闸门短路返回）→ 两侧同剔后再对齐
    prod = prod[prod['status'] != 'EVENT_ONLY']
    base_df = base_df[base_df['status'] != 'EVENT_ONLY']
    print(f'[自校验] 排除 EVENT_ONLY: 生产 {len(prod)} 行 / 扫描BASE {len(base_df)} 行（非主信号，统一剔除）')
    ok = True
    if len(prod) != len(base_df):
        print(f'[自校验] ⚠ 行数不一致: 生产 {len(prod)} vs 扫描BASE {len(base_df)}')
        ok = False
    k1 = set(map(tuple, prod[['ts_code', 'date', 'status']].fillna('').values))
    k2 = set(map(tuple, base_df[['ts_code', 'date', 'status']].fillna('').values))
    if k1 != k2:
        print(f'[自校验] ⚠ 键集不一致: 仅生产 {len(k1 - k2)} 例, 仅扫描 {len(k2 - k1)} 例')
        for t in sorted(k1 - k2)[:5]:
            print('   仅生产:', t)
        for t in sorted(k2 - k1)[:5]:
            print('   仅扫描:', t)
        ok = False
    mg = prod.merge(base_df, on=['ts_code', 'date', 'status'], suffixes=('_p', '_s'))
    for col in ('fut20', 'edb_score_adj'):
        if f'{col}_p' not in mg.columns or f'{col}_s' not in mg.columns:
            ok = False
            print(f'[自校验] ⚠ 缺列 {col}')
            continue
        a = pd.to_numeric(mg[f'{col}_p'], errors='coerce')
        b = pd.to_numeric(mg[f'{col}_s'], errors='coerce')
        dmax = float(np.nanmax(np.abs(a - b))) if len(mg) else 0.0
        if dmax >= 1e-6:
            print(f'[自校验] ⚠ {col} 最大偏差 {dmax:.6f}')
            ok = False
        else:
            print(f'[自校验] {col} 一致（最大偏差 {dmax:.2e}）')
    print('[自校验] 通过 ✅ BASE 变体与生产回测完全对齐'
          if ok else '[自校验] 未通过 ❌ 扫描实现存在漂移，结果不可用')
    return ok


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
def run_scan(start: str, end: str, step: int = 5, jobs: int = 8, limit: int = 0):
    pool = get_stock_pool(exclude_st=EDB_CONFIG['exclude_st'])
    if pool.empty:
        print('[扫描] 股票池为空')
        return
    if limit > 0:
        pool = pool.head(limit)
    print(f'[扫描] {start}~{end} 步长 {step} 股票池 {len(pool)} 只 | '
          f'变体 {len(VARIANTS)} 组 | 预筛放宽 {PRESCREEN_DRY_MAX} | 并行 {jobs}')
    tasks = [(r['ts_code'], r.get('name', ''), r.get('industry', ''), start, end, step)
             for _, r in pool.iterrows()]
    per_variant = {vn: [] for vn, _ in VARIANTS}
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, res in enumerate(ex.map(_scan_one, tasks, chunksize=8)):
            if res:
                _, rows_by_var = res
                for vn, recs in rows_by_var.items():
                    per_variant[vn].extend(recs)
            if (i + 1) % 500 == 0:
                print(f'  进度 {i + 1}/{len(tasks)} 累计BASE信号 '
                      f'{len(per_variant["BASE"])}')

    dfs = {}
    for vn, recs in per_variant.items():
        dfs[vn] = dedup_episodes(pd.DataFrame(recs))
    base_df = dfs['BASE']

    print()
    if limit <= 0:
        if not selfcheck(base_df, start, end):
            print('[扫描] 自校验未通过，终止输出（请勿引用本结果）')
            return
    else:
        print(f'[扫描] --limit {limit} 试跑模式：跳过自校验')

    base_st = variant_stats(base_df)
    rows = [stats_row(vn, dfs[vn], base_st) for vn, _ in VARIANTS]
    tbl = pd.DataFrame(rows)

    out_dir = os.path.join(REPORT_DIR, 'scan_volume')
    os.makedirs(out_dir, exist_ok=True)
    tbl.to_csv(os.path.join(out_dir, 'variant_stats.csv'),
               index=False, encoding='utf-8-sig')
    det = pd.concat(
        [d.assign(variant=vn) for vn, d in dfs.items() if not d.empty],
        ignore_index=True)
    det.to_csv(os.path.join(out_dir, 'variant_details.csv'),
               index=False, encoding='utf-8-sig')
    print(f'[输出] {out_dir}\\variant_stats.csv')
    print(f'[输出] {out_dir}\\variant_details.csv ({len(det)} 行)')

    print()
    line = '─' * 100
    print(line)
    print('变体总表（T+20，exev=剔停牌/连板事件；med/win 为 %；判据：n↑ 且 exev 中位/胜率不塌）')
    print(line)
    hdr = (f'{"变体":<28}{"n":>5}{"Δn":>5}{"主池":>5}{"Δ主池":>6}'
           f'{"exev_n":>7}{"exev_med":>9}{"exev_win":>9}{"exev_trim":>10}'
           f'{"主池exev_med":>12}')
    print(hdr)
    print('-' * len(hdr))
    bexev = base_st.get('f20_exev', {})
    for r in rows:
        verdict = ''
        if r['variant'] != 'BASE':
            ok_n = r['dn_main'] > 0 or r['dn'] > 0
            med_ok = _num(r['d_exev_med']) >= -1.0
            win_ok = _num(r['d_exev_win']) >= -3.0
            verdict = ('放行' if (ok_n and med_ok and win_ok and r['exev_n'] >= 30)
                       else ('样本<30' if not (r['exev_n'] >= 30)
                             else ('观察' if ok_n else '拒绝')))
        print(f'{r["variant"]:<28}{r["n"]:>5}{_fmt(r["dn"], ".0f"):>5}{r["n_main"]:>5}'
              f'{_fmt(r["dn_main"], ".0f"):>6}{r["exev_n"]:>7}'
              f'{_fmt(r["exev_med"], ".2f"):>9}{_fmt(r["exev_win"], ".1f"):>9}'
              f'{_fmt(r["exev_trim"], ".2f"):>10}'
              f'{_fmt(r["main_exev_med"], ".2f"):>12}  {verdict}')
    print(line)
    print(f'[基线] BASE: n={base_st.get("n", 0)} 主池={base_st.get("n_main", 0)} '
          f'exev_med={bexev.get("med")} exev_win={bexev.get("win")}')


def main():
    ap = argparse.ArgumentParser(description='EDB 信号量扩容 —— 闸门参数扫描')
    ap.add_argument('--start', default='20240601')
    ap.add_argument('--end', default='20260918')
    ap.add_argument('--step', type=int, default=5)
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0, help='只跑前N只（试跑用，跳过自校验）')
    a = ap.parse_args()
    run_scan(a.start, a.end, step=a.step, jobs=a.jobs, limit=a.limit)


if __name__ == '__main__':
    main()
