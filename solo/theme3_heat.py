#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
theme3_heat.py —— theme3 主题体系热度分析
════════════════════════════════════════════════════════════

回答三个问题（与 theme_heat_v22 / V2.4 同口径）：
    今日最强主题是谁？   → TODAY
    本周最强主题是谁？   → WEEK（5 个交易日）
    本月最强主题是谁？   → MONTH（20 个交易日）

与 theme_heat_v22 的唯一差别是数据源：
    theme_heat_v22  ← cache_daily/theme_stock_map_v2_{date}.json（V3.1 体系）
    本程序          ← cache_backbone_tushare/theme3_constituents_{date}.json（theme3 体系）

Heat 口径完全对齐 V2.4：
    HeatRaw  = 60%×PricePct + 30%×BreadthPct + 10%×ActivityPct（横截面分位）
    Price    = 70%×P50 + 30%×P75（成员窗口收益，先 clip 到 ±20%）
    Breadth  = 70%×上涨占比 + 30%×强涨(≥3%)占比
    Activity = 今日成交额 / 过去 20 日均额，单股 clip(0.5, 3.0)，取成员中位数
    Rel      = min(1, sqrt(N/50))
    Heat     = 50%×HeatRaw + 50%×(HeatRaw×Rel + MarketMedian×(1−Rel))

统计层级：全部子主题（当前 74 个）+ 各一级目录（当前 14 个，成员取其下所有子主题的并集）。

数据源（只读，不新增下载）：
    cache_backbone_tushare/theme3_constituents_{date}.json
    cache_daily/stock_data.db  →  daily_cache 表

输出：
    report_daily/theme3_heat_{date}.json
    report_daily/theme3_heat_{date}.csv

用法：
    python theme3_heat.py                # 最近一个有成分股文件的交易日
    python theme3_heat.py 20261026       # 指定交易日
"""

import os
import sys
import json
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONST_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
DB_PATH = r'd:\mystock\cache_daily\stock_data.db'

# ── 权重与阈值（与 theme_heat_v22 / V2.4 完全一致，不得自动优化）──
W_PRICE, W_BREADTH, W_ACTIVITY = 0.60, 0.30, 0.10
W_P50, W_P75 = 0.70, 0.30
W_UP, W_STRONG = 0.70, 0.30
STRONG_PCT = 3.0
RET_CLIP = 20.0
WIN_WEEK, WIN_MONTH = 5, 20
ACT_LOOKBACK = 20
ACT_MIN_PERIODS = 10
ACT_FLOOR, ACT_CAP = 0.5, 3.0
MIN_SAMPLE = 5
FULL_RELIABILITY_N = 50
LOW_SAMPLE_MAX = 10
SMALL_SAMPLE_MAX = 30
LOW_BREADTH_HEAT, LOW_BREADTH_MIN = 80.0, 25.0
LIMIT_UP_MAIN = 9.7
WINDOWS = {'TODAY': 1, 'WEEK': WIN_WEEK, 'MONTH': WIN_MONTH}
NEED_DAYS = WIN_MONTH + ACT_LOOKBACK + 5      # 45 个交易日足够
TOP_N = 10


# ═════════════════════ 数据读取 ═════════════════════

def resolve_trade_date(arg=None):
    """未指定日期时，取最新一份 theme3_constituents_YYYYMMDD.json 的日期"""
    if arg:
        return str(arg)
    files = []
    for fn in os.listdir(CONST_DIR):
        if fn.startswith('theme3_constituents_') and fn.endswith('.json'):
            d = fn[len('theme3_constituents_'):-len('.json')]
            if len(d) == 8 and d.isdigit():
                files.append(d)
    if not files:
        raise SystemExit(f'未找到 theme3_constituents_*.json，请先运行 theme3_constituents_v2.py')
    return max(files)


def load_members(trade_date):
    """读成分股 → (subtopics, categories)

    subtopics: [{theme, top_category, stocks:[{ts_code, name, role, ...}]}]
    categories: {一级目录: set(ts_code)}
    """
    path = os.path.join(CONST_DIR, f'theme3_constituents_{trade_date}.json')
    if not os.path.exists(path):
        raise SystemExit(f'成分股文件不存在：{path}')

    with open(path, encoding='utf-8') as f:
        raw = json.load(f)

    subtopics, categories = [], {}
    for t in raw.get('themes', []):
        top = t.get('top_category') or ''
        stocks = [s for s in (t.get('stocks') or []) if s.get('ts_code')]
        sub = {'theme': t.get('theme_name') or '', 'top_category': top, 'stocks': stocks}
        subtopics.append(sub)
        categories.setdefault(top, set()).update(s['ts_code'] for s in stocks)

    # 一稿两份：一级目录成员池转成 stocks 结构，便于复用同一套指标计算
    cat_rows = []
    for top, codes in categories.items():
        cat_rows.append({'theme': top, 'top_category': '（一级目录）',
                         'stocks': [{'ts_code': c} for c in sorted(codes)]})
    return subtopics, cat_rows


def load_panel(codes, trade_date, need_days=NEED_DAYS):
    """从 daily_cache 取区间行情 → (dates, P, C, A, ACT)

    P=日涨跌幅  C=收盘价  A=成交额  ACT=单股量能比（今日÷过去20日均额，已截断）
    """
    if not os.path.exists(DB_PATH):
        raise SystemExit(f'行情库不存在：{DB_PATH}')

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            'SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? '
            'ORDER BY trade_date DESC LIMIT ?', (trade_date, need_days))
        dates = sorted(r[0] for r in cur.fetchall())
        if not dates:
            raise SystemExit(f'daily_cache 中无 {trade_date} 及之前的数据')

        df = pd.read_sql(
            'SELECT ts_code, trade_date, close, pct_chg, amount FROM daily_cache '
            'WHERE trade_date >= ? AND trade_date <= ?',
            conn, params=(dates[0], dates[-1]))
    finally:
        conn.close()

    if df.empty:
        raise SystemExit('daily_cache 未返回任何行情')

    keep = set(codes)
    df = df[df['ts_code'].isin(keep)].copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for c in ('close', 'pct_chg', 'amount'):
        df[c] = pd.to_numeric(df[c], errors='coerce')

    P = df.pivot_table(index='trade_date', columns='ts_code', values='pct_chg', aggfunc='last').reindex(dates)
    C = df.pivot_table(index='trade_date', columns='ts_code', values='close', aggfunc='last').reindex(dates)
    A = df.pivot_table(index='trade_date', columns='ts_code', values='amount', aggfunc='last').reindex(dates)

    MA = A.shift(1).rolling(ACT_LOOKBACK, min_periods=ACT_MIN_PERIODS).mean()
    ACT = (A / MA).clip(lower=ACT_FLOOR, upper=ACT_CAP)
    return dates, P, C, A, ACT


# ═════════════════════ 指标计算 ═════════════════════

def window_metrics(P, C, ACT, cols, n, w):
    """单个主题单窗口指标（口径同 theme_heat_v22.window_metrics）"""
    lo = n - w
    if lo < 0:
        return None

    Pw = P.iloc[lo:][cols]
    mask = Pw.notna().values
    pairs = int(mask.sum())
    if pairs == 0:
        return None

    up_ratio = float(((Pw.values > 0) & mask).sum()) / pairs
    strong_ratio = float(((Pw.values >= STRONG_PCT) & mask).sum()) / pairs
    breadth = W_UP * up_ratio + W_STRONG * strong_ratio

    c_last = C.iloc[-1][cols].astype(float)
    c_base = C.iloc[n - w - 1][cols].astype(float)
    ok = c_last.notna() & c_base.notna() & (c_base != 0) & P.iloc[-1][cols].notna()
    if not bool(ok.any()):
        return None

    r = ((c_last[ok] / c_base[ok] - 1.0) * 100.0).clip(-RET_CLIP, RET_CLIP).values
    p25, p50, p75 = (float(np.percentile(r, q)) for q in (25, 50, 75))
    price = W_P50 * p50 + W_P75 * p75

    day_med = ACT.iloc[lo:][cols].median(axis=1)
    activity = float(day_med.mean()) if day_med.notna().any() else float('nan')

    # 当日涨停家数（9.7% 门槛同时覆盖 10% 板与 20% 板）
    limit_up_n = int((P.iloc[-1][cols].astype(float) >= LIMIT_UP_MAIN).sum())

    return {'n': int(ok.sum()), 'price': price, 'breadth': breadth, 'activity': activity,
            'up_ratio': up_ratio, 'strong_ratio': strong_ratio,
            'p25': p25, 'p50': p50, 'p75': p75, 'ret_med': p50, 'ret_p75': p75,
            'limit_up_n': limit_up_n}


def pct_rank(vals):
    """横截面百分位（0~100，并列取平均名次）"""
    v = np.asarray([float(x) for x in vals], dtype=float)
    m = v.size
    if m == 0:
        return v
    if m == 1:
        return np.full(1, 50.0)
    order = np.argsort(v, kind='mergesort')
    sv = v[order]
    pos = np.empty(m, dtype=float)
    i = 0
    while i < m:
        j = i
        while j + 1 < m and sv[j + 1] == sv[i]:
            j += 1
        pos[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return pos / (m - 1) * 100.0


def _flags(row):
    f = []
    n = row['n']
    if n < LOW_SAMPLE_MAX:
        f.append('LOW_SAMPLE')
    elif n < SMALL_SAMPLE_MAX:
        f.append('SMALL_SAMPLE')
    if row['heat'] >= LOW_BREADTH_HEAT and row['breadth'] * 100 < LOW_BREADTH_MIN:
        f.append('LOW_BREADTH')
    if row['p75'] - row['p50'] >= 8.0:
        f.append('NARROW_STRONG')
    return f


def score_window(raw, wk):
    """单窗口：分位 → HeatRaw → Rel 收缩 → Heat → Rank

    raw: {theme: metrics}；返回 (scored, small_watch)
    """
    elig = {t: v for t, v in raw.items() if v['n'] >= MIN_SAMPLE}
    small = {t: v for t, v in raw.items() if v['n'] < MIN_SAMPLE}
    if not elig:
        return {}, small

    names = list(elig.keys())

    def _fill(key):
        arr = [elig[t][key] for t in names]
        good = [x for x in arr if not (isinstance(x, float) and np.isnan(x))]
        med = float(np.median(good)) if good else 0.0
        return np.asarray([med if (isinstance(x, float) and np.isnan(x)) else x for x in arr],
                          dtype=float)

    price_s = pct_rank(_fill('price'))
    bread_s = pct_rank(_fill('breadth'))
    act_s = pct_rank(_fill('activity'))
    heat_raw = W_PRICE * price_s + W_BREADTH * bread_s + W_ACTIVITY * act_s
    mkt_median = float(np.median(heat_raw))

    scored = {}
    for i, t in enumerate(names):
        v = dict(elig[t])
        rel = min(1.0, (v['n'] / FULL_RELIABILITY_N) ** 0.5)
        adj = float(heat_raw[i]) * rel + mkt_median * (1.0 - rel)
        v.update({'heat_raw': float(heat_raw[i]), 'reliability': rel,
                  'heat': 0.50 * float(heat_raw[i]) + 0.50 * adj,
                  'price_score': float(price_s[i]),
                  'breadth_score': float(bread_s[i]),
                  'activity_score': float(act_s[i])})
        v['flags'] = _flags(v)
        scored[t] = v

    for rk, t in enumerate(sorted(names, key=lambda x: -scored[x]['heat']), 1):
        scored[t]['rank'] = rk
    return scored, small


def quality_of(stocks):
    """映射质量：行业板块直接命中 或 核心链（chain_distance=0）的占比，只报告不改热度"""
    if not stocks:
        return 0.0, 'EMPTY'
    strong = sum(1 for s in stocks
                 if s.get('via') == 'dc_industry_board' or s.get('chain_distance') == 0)
    q = strong / len(stocks)
    return round(q, 4), ('OK' if q >= 0.85 else ('WARN' if q >= 0.70 else 'BAD'))


# ═════════════════════ 主分析 ═════════════════════

def analyze(trade_date):
    subs, cats = load_members(trade_date)
    all_codes = set()
    for s in subs:
        all_codes.update(x['ts_code'] for x in s['stocks'])
    print(f'[Theme3] {len(subs)} 个子主题 / {len(cats)} 个一级目录 / {len(all_codes)} 只唯一股票')

    dates, P, C, A, ACT = load_panel(all_codes, trade_date)
    n = len(dates)
    print(f'[KLine] {dates[0]} ~ {dates[-1]}，共 {n} 个交易日，面板 {P.shape[0]}×{P.shape[1]}')
    if dates[-1] != trade_date:
        print(f'[KLine] ⚠️ 行情末日 {dates[-1]} ≠ 目标日 {trade_date}，按实际末日计算')

    def run_layer(rows, label):
        raw = {wk: {} for wk in WINDOWS}
        for r in rows:
            cols = [c for c in (s['ts_code'] for s in r['stocks']) if c in P.columns]
            if not cols:
                continue
            for wk, w in WINDOWS.items():
                m = window_metrics(P, C, ACT, cols, n, w)
                if m:
                    raw[wk][r['theme']] = m

        scored, small = {}, {}
        for wk in WINDOWS:
            sc, sm = score_window(raw[wk], wk)
            scored[wk], small[wk] = sc, sm
        print(f'[{label}] 可排名：' +
              ' / '.join(f'{wk} {len(scored[wk])}' for wk in WINDOWS))
        return scored, small

    sub_scored, sub_small = run_layer(subs, '子主题')
    cat_scored, cat_small = run_layer(cats, '一级目录')

    # 组装输出行
    def build_rows(rows, scored, small, with_quality=True):
        out = []
        for r in rows:
            t = r['theme']
            if with_quality:
                q, qf = quality_of(r['stocks'])
            else:
                q, qf = None, '—'
            rec = {'theme': t, 'top_category': r['top_category'],
                   'n_members': len(r['stocks']),
                   'member_quality': q, 'quality_flag': qf}
            for wk in WINDOWS:
                if t in scored[wk]:
                    v = scored[wk][t]
                    rec[wk] = {k: _clean(v.get(k)) for k in
                               ('n', 'heat', 'heat_raw', 'rank', 'reliability',
                                'price', 'breadth', 'activity', 'up_ratio',
                                'strong_ratio', 'p50', 'p75', 'limit_up_n',
                                'price_score', 'breadth_score', 'activity_score')}
                    rec[wk]['flags'] = v.get('flags', [])
                elif t in small[wk]:
                    v = small[wk][t]
                    rec[wk] = {'n': int(v['n']), 'heat': None, 'rank': None,
                               'p50': _clean(v['p50']), 'up_ratio': _clean(v['up_ratio']),
                               'flags': ['LOW_SAMPLE']}
                else:
                    rec[wk] = {'n': 0, 'heat': None, 'rank': None, 'flags': ['NO_DATA']}
            out.append(rec)
        out.sort(key=lambda x: (x['TODAY'].get('rank') is None,
                                x['TODAY'].get('rank') or 999))
        return out

    subtopic_rows = build_rows(subs, sub_scored, sub_small, with_quality=True)
    category_rows = build_rows(cats, cat_scored, cat_small, with_quality=False)

    return {'trade_date': trade_date,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': f'theme3_constituents_{trade_date}.json + cache_daily/stock_data.db',
            'params': {'weights': {'price': W_PRICE, 'breadth': W_BREADTH, 'activity': W_ACTIVITY},
                       'windows': WINDOWS, 'min_sample': MIN_SAMPLE,
                       'ret_clip': RET_CLIP, 'strong_pct': STRONG_PCT,
                       'activity_cap': ACT_CAP, 'full_reliability_n': FULL_RELIABILITY_N},
            'summary': {'n_subtopics': len(subs), 'n_categories': len(cats),
                        'n_stocks': len(all_codes), 'n_days': n,
                        'panels': {'subtopics': {wk: len(sub_scored[wk]) for wk in WINDOWS},
                                   'categories': {wk: len(cat_scored[wk]) for wk in WINDOWS}}},
            'subtopics': subtopic_rows,
            'categories': category_rows}


def _clean(v):
    """numpy / NaN → 原生类型或 None"""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        x = float(v)
        return None if np.isnan(x) else round(x, 4)
    return v


# ═════════════════════ 输出 ═════════════════════

def _f(x, d=2, sign=False):
    if x is None:
        return '—'
    try:
        return f'{x:+.{d}f}' if sign else f'{x:.{d}f}'
    except (TypeError, ValueError):
        return '—'


def print_table(rows, title, top=TOP_N):
    print()
    print(f'【{title}】')
    print(f"  {'#':>3}  {'主题':<14} {'N':>4} {'Heat':>6} {'涨幅P50':>8} "
          f"{'涨幅P75':>8} {'上涨占比':>8} {'强涨':>6} {'涨停':>4} {'量能':>5}  标记")
    for r in rows[:top]:
        v = r['TODAY']
        if v.get('rank') is None:
            continue
        up = v.get('up_ratio')
        st = v.get('strong_ratio')
        print(f"  {v['rank']:>3}  {r['theme']:<14} {v['n']:>4} {_f(v['heat'], 1):>6} "
              f"{_f(v['p50'], 2, True):>8} {_f(v['p75'], 2, True):>8} "
              f"{_f(None if up is None else up * 100, 1):>7}% "
              f"{_f(None if st is None else st * 100, 1):>5}% "
              f"{v.get('limit_up_n') or 0:>4} {_f(v.get('activity')):>5}  "
              f"{','.join(v['flags'])}")


def print_cross(rows, top=TOP_N):
    """三窗口 Heat 对照（只列三窗口都可排名的主题）"""
    print()
    print('【三窗口 Heat 对照（可排名主题）】')
    print(f"  {'主题':<14} {'今日':>6}{'名':>4}  {'本周':>6}{'名':>4}  {'本月':>6}{'名':>4}")
    ok = [r for r in rows
          if all(r[wk].get('heat') is not None for wk in WINDOWS)]
    ok.sort(key=lambda r: -r['MONTH']['heat'])
    for r in ok[:top]:
        print(f"  {r['theme']:<14} "
              f"{r['TODAY']['heat']:>6.1f}{r['TODAY']['rank']:>4}  "
              f"{r['WEEK']['heat']:>6.1f}{r['WEEK']['rank']:>4}  "
              f"{r['MONTH']['heat']:>6.1f}{r['MONTH']['rank']:>4}")


CSV_FIELDS = ('theme', 'top_category', 'n_members', 'member_quality', 'quality_flag',
              'today_rank', 'today_heat', 'today_heat_raw', 'today_n', 'today_p50',
              'today_p75', 'today_up_ratio', 'today_strong_ratio', 'today_activity',
              'today_limit_up', 'today_flags',
              'week_rank', 'week_heat', 'week_n', 'week_p50', 'week_p75',
              'week_up_ratio', 'week_activity', 'week_flags',
              'month_rank', 'month_heat', 'month_n', 'month_p50', 'month_p75',
              'month_up_ratio', 'month_activity', 'month_flags')


def _csv_row(r):
    row = {'theme': r['theme'], 'top_category': r['top_category'],
           'n_members': r['n_members'], 'member_quality': r['member_quality'],
           'quality_flag': r['quality_flag']}
    for wk, pre in (('TODAY', 'today'), ('WEEK', 'week'), ('MONTH', 'month')):
        v = r[wk]
        row[f'{pre}_rank'] = v.get('rank')
        row[f'{pre}_heat'] = v.get('heat')
        row[f'{pre}_heat_raw'] = v.get('heat_raw')
        row[f'{pre}_n'] = v.get('n')
        row[f'{pre}_p50'] = v.get('p50')
        row[f'{pre}_p75'] = v.get('p75')
        row[f'{pre}_up_ratio'] = (round(v['up_ratio'] * 100, 2)
                                  if v.get('up_ratio') is not None else None)
        row[f'{pre}_strong_ratio'] = (round(v['strong_ratio'] * 100, 2)
                                      if v.get('strong_ratio') is not None else None)
        row[f'{pre}_activity'] = v.get('activity')
        if pre == 'today':
            row['today_limit_up'] = v.get('limit_up_n')
        row[f'{pre}_flags'] = '|'.join(v.get('flags') or []) or '-'
    return row


def save_outputs(result):
    os.makedirs(REPORT_DIR, exist_ok=True)
    d = result['trade_date']

    jp = os.path.join(REPORT_DIR, f'theme3_heat_{d}.json')
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'\n✅ JSON 输出: {jp}')

    cp = os.path.join(REPORT_DIR, f'theme3_heat_{d}.csv')
    pd.DataFrame([_csv_row(r) for r in result['subtopics']],
                 columns=list(CSV_FIELDS)).to_csv(cp, index=False, encoding='utf-8-sig')
    print(f'✅ CSV  输出: {cp}')

    ccp = os.path.join(REPORT_DIR, f'theme3_heat_category_{d}.csv')
    pd.DataFrame([_csv_row(r) for r in result['categories']],
                 columns=list(CSV_FIELDS)).to_csv(ccp, index=False, encoding='utf-8-sig')
    print(f'✅ 一级目录 CSV: {ccp}')
    return jp, cp, ccp


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    trade_date = resolve_trade_date(arg)
    print('=' * 72)
    print(f'theme3_heat: 主题热度分析（对齐 theme_heat_v22 / V2.4 口径）')
    print(f'交易日: {trade_date}')
    print('=' * 72)

    result = analyze(trade_date)

    print_table(result['subtopics'], f"今日最强子主题 TOP{TOP_N}")
    print_cross(result['subtopics'])
    print_table(result['categories'], f"今日最强一级目录 TOP{min(5, TOP_N)}", top=5)
    print_cross(result['categories'], top=5)
    save_outputs(result)


if __name__ == '__main__':
    main()
