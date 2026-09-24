#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题热度 V2.3 —— Daily / Weekly / Monthly Strength（终极简化稳定版）
════════════════════════════════════════════════════════════

这是一个「主题温度计」，不是交易决策器。只回答三个问题：

    今日最强主题是谁？    → TODAY TOP
    本周最强主题是谁？    → WEEK TOP
    本月最强主题是谁？    → MONTH TOP

V2.3 相对 V2.2.1 的改动（§6.3/§11/§15/§18/§20/§25）：
  · PriceRaw 内部权重 60/40 → 70/30（P50 权重高于 P75：先反映「整体是否变强」）
  · Reliability = min(1, sqrt(N/50))（原 sqrt(N/20)）——样本可靠性门槛提高到 50
  · 新增 §20：成员收益率在算 P25/P50/P75 前 clip 到 [-20%, +20%]
  · 删除 CONCENTRATED，改为 §15 描述标签 BROAD_STRONG / NARROW_STRONG
  · 跨周期观察表删除 COMMON_STRONG，保留 SUSTAINED_STRONG / HOT_TODAY /
    新增 HOT_WEEK / COOLING（§18）
  · 单一修正仅 Reliability；禁止任何人工扣分（§21）

不设计也不输出（§16 永久禁止）：WEAK / RECOVERY / STARTING / STRONG_TREND /
RETREAT / COOLING(交易态) / EXHAUSTION / MAINLINE / MAINLINE_CANDIDATE /
CONFIRMATION / PERSISTENCE / LEADERSHIP / CHASE_RISK / TRADE / BUY / SELL /
POSITION，以及主线判断、买入建议、仓位建议、交易许可、追高建议、止损、
个股推荐。这些属于后续股票级交易模块。

不修改任何其他量化模块（HVT / 突破 / DLG / F120 / 执行买点 / 股票池）。

数据源（不新增数据源、不重新下载全量数据）：
  · 现有 stock_cache SQLite：cache_daily/stock_data.db 的 daily_cache 表（个股日线）
  · 现有主题成员池：cache_daily/theme_stock_map_v2_{date}.json
    （该文件由 theme_config.json + subtheme_map.json 生成，即 §2 所称「现有主题成员映射」）

用法：
    python theme_heat_v22.py              # 最近一个交易日
    python theme_heat_v22.py 20260923     # 指定交易日
"""

import os
import sys
import json

# Windows GBK 控制台
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.dirname(BASE_DIR))

import numpy as np
import pandas as pd

REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
CACHE_DIR = r'd:\mystock\cache_daily'

# ── §10 核心权重（价格是核心、扩散是确认、活跃度只是辅助；不得自动优化权重）──
W_PRICE, W_BREADTH, W_ACTIVITY = 0.60, 0.30, 0.10
# ── §6.3 PriceRaw 内部：P50 权重必须高于 P75（先反映「整体是否普遍变强」）──
W_P50, W_P75 = 0.70, 0.30
# ── §7 BreadthRaw 内部：上涨占比 / 强涨(≥3%)占比 ──
W_UP, W_STRONG = 0.70, 0.30

STRONG_PCT = 3.0                 # §7 强涨门槛（%）
RET_CLIP = 20.0                  # §20 成员收益率 clip 到 [-20%, +20%]（只用于热度统计）
WIN_WEEK, WIN_MONTH = 5, 20      # §4 WEEK / MONTH 观察窗口（交易日）
ACT_LOOKBACK = 20                # §8 过去 20 日平均成交额
ACT_MIN_PERIODS = 10             # 成交额历史不足 10 日则不算 Activity
ACT_FLOOR, ACT_CAP = 0.5, 3.0    # §8 单股 Activity_i 双侧截断（缩量不奖励、妖股不拉爆）
MIN_SAMPLE = 5                   # §3 N < 5 → 不进入正式排行榜（仅 LOW_SAMPLE 观察）
FULL_RELIABILITY_N = 50          # §11 Reliability = min(1, sqrt(N/50))；N ≥ 50 → 不收缩
LOW_SAMPLE_MAX = 20              # §13 5 ≤ N < 20 → LOW_SAMPLE（只标记，不删除、不强制垫底）
LOW_BREADTH_UP = 0.40            # §14 UpRatio < 40% → LOW_BREADTH（只标记，Breadth 已入 BaseHeat）
TOP_N = 10                       # §15/§17/§18/§23 榜单条数与强弱门槛
MID_N = 10                       # §18 跨周期观察表的次级门槛
CROSS_N = 20                     # §18 COOLING 的今日门槛
HIST_DAYS = 60                   # 需覆盖 20(月窗口) + 20(活跃度回看) + 缓冲；实测 60 充足

SEP_FULL = "═" * 62
SEP_THIN = "─" * 62


# ═════════════════════════ 数据读取 ═════════════════════════

def resolve_trade_date(arg=None):
    from stock_cache import get_recent_trade_dates
    if arg:
        return str(arg)
    ds = get_recent_trade_dates(n=1)
    if not ds:
        raise SystemExit('daily_cache 无数据，无法确定最近交易日')
    return str(ds[-1])


def load_theme_members(trade_date):
    """主题 → {code: name}。优先精确日期，其次 latest。"""
    cands = [os.path.join(CACHE_DIR, f'theme_stock_map_v2_{trade_date}.json'),
             os.path.join(CACHE_DIR, 'theme_stock_map_v2_latest.json'),
             os.path.join(REPORT_DIR, 'theme_stock_map_latest_v2.json')]
    for p in cands:
        if not os.path.exists(p):
            continue
        with open(p, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        themes = raw.get('themes') or {}
        out = {}
        for t, lst in themes.items():
            codes = {s.get('code'): (s.get('name') or '')
                     for s in lst if isinstance(s, dict) and s.get('code')}
            if codes:
                out[t] = codes
        if out:
            return out, p
    raise SystemExit('未找到 theme_stock_map_v2 映射文件')


def fetch_kline(codes, start, end):
    from theme_trend_sentiment_score import get_daily_kline
    df = get_daily_kline(sorted(codes), start, end)
    if df is None or df.empty:
        raise SystemExit(f'未取到任何日线数据 [{start} ~ {end}]')
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for c in ('close', 'pct_chg', 'amount'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def build_matrices(df, dates):
    """长表 → 三个对齐到交易日历的宽表 + Activity 宽表"""
    keep = set(dates)
    df = df[df['trade_date'].isin(keep)]
    P = df.pivot(index='trade_date', columns='ts_code', values='pct_chg').reindex(dates)
    C = df.pivot(index='trade_date', columns='ts_code', values='close').reindex(dates)
    A = df.pivot(index='trade_date', columns='ts_code', values='amount').reindex(dates)
    # §6 Activity_i = 今日成交额 / 过去20日平均成交额，单股双侧截断 clip(0.5, 3.0)
    MA = A.shift(1).rolling(ACT_LOOKBACK, min_periods=ACT_MIN_PERIODS).mean()
    ACT = (A / MA).clip(lower=ACT_FLOOR, upper=ACT_CAP)
    return P, C, ACT


# ═════════════════════════ 指标计算 ═════════════════════════

def window_metrics(P, C, ACT, cols, n, w):
    """单主题单窗口 -> {n, price, breadth, activity, up_ratio, p25/p50/p75, ...}

    w=1 为 TODAY（§四），w=5 为 WEEK，w=20 为 MONTH，口径完全一致。
    有效成员（§三 的 N）：窗口末日涨跌幅、基线收盘价、末日前收盘价三者俱全
    （基线 = 窗口起始日的前一交易日）。三个窗口的 N 可以不同（新股在长窗口会被剔除）。
    """
    lo = n - w
    Pw = P.iloc[lo:][cols]
    mask = Pw.notna().values
    pairs = int(mask.sum())
    if pairs == 0:
        return None
    # §7 广度：窗口内「成员×交易日」逐格统计，w=1 时退化为当日口径（用原始涨跌幅，不 clip）
    up_ratio = float(((Pw.values > 0) & mask).sum()) / pairs
    strong_ratio = float(((Pw.values >= STRONG_PCT) & mask).sum()) / pairs
    breadth = W_UP * up_ratio + W_STRONG * strong_ratio

    c_last = C.iloc[-1][cols].astype(float)
    c_base = C.iloc[n - w - 1][cols].astype(float)
    ok = c_last.notna() & c_base.notna() & (c_base != 0) & P.iloc[-1][cols].notna()
    if not bool(ok.any()):
        return None
    # §20 成员收益率先 clip 到 [-20%, +20%]，防极端涨停/异常复牌/数据错误污染主题 Price
    r = ((c_last[ok] / c_base[ok] - 1.0) * 100.0).clip(-RET_CLIP, RET_CLIP).values
    p25, p50, p75 = (float(np.percentile(r, q)) for q in (25, 50, 75))
    # §6.3 价格强度：中位数 70% + 75分位 30%
    price = W_P50 * p50 + W_P75 * p75

    # §8 主题 Activity = 成员 Activity_i 的中位数（逐日），周/月窗口取窗口内日均
    day_med = ACT.iloc[lo:][cols].median(axis=1)
    activity = float(day_med.mean()) if day_med.notna().any() else float('nan')
    return {'n': int(ok.sum()), 'price': price, 'breadth': breadth, 'activity': activity,
            'up_ratio': up_ratio, 'strong_ratio': strong_ratio,
            'p25': p25, 'p50': p50, 'p75': p75, 'ret_med': p50, 'ret_p75': p75}


def pct_rank(vals):
    """§7 横截面 Percentile Rank → 0~100（并列取平均名次；未经 rank 的原始值不做任何截尾）"""
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


def dist_stats(vals):
    """§21 分布统计（不据此自动改权重或阈值）"""
    a = np.asarray([x for x in vals if x is not None and not (isinstance(x, float) and np.isnan(x))],
                   dtype=float)
    keys = ('P01', 'P10', 'P25', 'P50', 'P75', 'P90', 'P95', 'MAX')
    if a.size == 0:
        return {k: float('nan') for k in keys}
    return {'P01': float(np.percentile(a, 1)), 'P10': float(np.percentile(a, 10)),
            'P25': float(np.percentile(a, 25)), 'P50': float(np.percentile(a, 50)),
            'P75': float(np.percentile(a, 75)), 'P90': float(np.percentile(a, 90)),
            'P95': float(np.percentile(a, 95)), 'MAX': float(a.max())}


# ═════════════════════════ 消费端读取入口 ═════════════════════════

def _num(v):
    """JSON 单元格 → float 或 None（空值/NaN → None）"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


def load_heat(trade_date):
    """读取本地已生成的 theme_heat_v23_{trade_date}.json（消费端唯一入口）

    返回 {theme: {...}}；文件不存在或解析失败返回 None，由调用方回退旧主题源。
    strength = TODAY Heat 与 WEEK Heat 的均值（TODAY+WEEK 综合口径），
    某一窗口无数据时以另一窗口为准；两窗口都缺 → strength = None。
    """
    path = os.path.join(REPORT_DIR, f'theme_heat_v23_{trade_date}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            rows = json.load(f)
    except Exception:
        return None
    out = {}
    for r in rows:
        t = str(r.get('theme') or '')
        if not t:
            continue
        td, wk = _num(r.get('today_heat')), _num(r.get('week_heat'))
        avail = [x for x in (td, wk) if x is not None]
        out[t] = {
            'theme': t,
            'strength': round(float(np.mean(avail)), 2) if avail else None,
            'today_heat': td, 'week_heat': wk, 'month_heat': _num(r.get('month_heat')),
            'today_rank': _num(r.get('today_rank')), 'week_rank': _num(r.get('week_rank')),
            'month_rank': _num(r.get('month_rank')),
            'flags': str(r.get('today_flags') or ''),
            'n': _num(r.get('today_n')),
            'up_ratio': _num(r.get('today_up_ratio')),
            'reliability': _num(r.get('today_reliability')),
        }
    return out or None


def top_heat(n=3, trade_date=None):
    """按 TODAY+WEEK 综合强度取前 n 名 → [(theme, strength), ...]；无数据返回 []"""
    heat = load_heat(trade_date or resolve_trade_date())
    if not heat:
        return []
    ranked = [v for v in heat.values() if v['strength'] is not None]
    ranked.sort(key=lambda v: -v['strength'])
    return [(v['theme'], v['strength']) for v in ranked[:n]]


# V2 trend_score → 本模块 Heat 的等分位锚点（由 14 个交易日 × 32 主题的配对样本拟合，
# 样本区间 20260724~20260923）。两边在同一分位上取值，故折算后主题强弱次序与选择性不变。
V2_TO_HEAT_ANCHORS = ((0.0, 0.0), (30.0, 40.0), (40.0, 54.0), (45.0, 62.0),
                      (65.0, 81.0), (70.0, 85.0), (80.0, 94.0), (86.4, 100.0))


def v2_to_heat(score):
    """把 theme_score_v2 的 trend_score 折算成本模块 Heat 的同一分布尺度。

    仅供消费端在 V2.3 结果缺失、回退 theme_score_v2 时使用，避免阈值口径漂移。
    """
    x = _num(score)
    if x is None:
        return None
    return round(float(np.interp(x, [a for a, _ in V2_TO_HEAT_ANCHORS],
                                 [b for _, b in V2_TO_HEAT_ANCHORS])), 2)


# ═════════════════════════ 主流程 ═════════════════════════

def run(trade_date=None):
    from stock_cache import get_recent_trade_dates
    trade_date = resolve_trade_date(trade_date)
    all_dates = [str(d) for d in get_recent_trade_dates(n=HIST_DAYS, end_date=trade_date)]
    if not all_dates or all_dates[-1] != trade_date:
        raise SystemExit(f'{trade_date} 不在 daily_cache 交易日历中')
    n = len(all_dates)
    if n < WIN_MONTH + ACT_LOOKBACK + 2:
        raise SystemExit(f'历史交易日不足（{n}），无法计算 MONTH 与 Activity')
    dates = all_dates

    theme_members, map_path = load_theme_members(trade_date)
    all_codes = sorted({c for m in theme_members.values() for c in m})
    print(f"[V2.3] 交易日 {trade_date}｜主题 {len(theme_members)} 个｜映射 {os.path.basename(map_path)}")
    print(f"[V2.3] 交易日历 {dates[0]} ~ {dates[-1]}（{n} 日）｜成员股票 {len(all_codes)} 只")

    df = fetch_kline(all_codes, dates[0], dates[-1])
    P, C, ACT = build_matrices(df, dates)

    windows = {'TODAY': 1, 'WEEK': WIN_WEEK, 'MONTH': WIN_MONTH}
    raw = {wk: {} for wk in windows}
    missing_themes = []
    for t, members in theme_members.items():
        cols = [c for c in members if c in P.columns]
        if not cols:
            missing_themes.append(t)
            continue
        for wk, w in windows.items():
            m = window_metrics(P, C, ACT, cols, n, w)
            if m:
                raw[wk][t] = m

    # 排除样本不足的主题（§3 N<5 不进入正式排行榜），其原始指标保留供观察
    small_watch = {}
    for wk in windows:
        for t, v in list(raw[wk].items()):
            if v['n'] < MIN_SAMPLE:
                small_watch.setdefault(t, {})[wk] = v

    # ── 横截面标准化（§9 Percentile Rank）+ 加权（§10）+ 样本可靠性修正（§11/§12）──
    scored = {}
    for wk in windows:
        elig = {t: v for t, v in raw[wk].items() if v['n'] >= MIN_SAMPLE}
        if not elig:
            continue
        names = list(elig.keys())

        def _fill(key):
            """缺失分量以横截面中位数补齐（中性），保持主题仍可排序"""
            arr = [elig[t][key] for t in names]
            good = [x for x in arr if not (isinstance(x, float) and np.isnan(x))]
            med = float(np.median(good)) if good else 0.0
            return np.asarray([med if (isinstance(x, float) and np.isnan(x)) else x for x in arr],
                              dtype=float)

        price_s = pct_rank(_fill('price'))
        bread_s = pct_rank(_fill('breadth'))
        act_s = pct_rank(_fill('activity'))
        base = W_PRICE * price_s + W_BREADTH * bread_s + W_ACTIVITY * act_s
        mkt_median = float(np.median(base))

        for i, t in enumerate(names):
            v = elig[t]
            # §11 Reliability = min(1, sqrt(N/50))；N ≥ 50 恒为 1，不做任何收缩
            rel = min(1.0, (v['n'] / FULL_RELIABILITY_N) ** 0.5)
            # §12 Heat = Reliability × BaseHeat + (1 − Reliability) × MarketMedianHeat
            heat = rel * float(base[i]) + (1.0 - rel) * mkt_median
            row = dict(v)
            row.update({'theme': t, 'base_score': float(base[i]), 'reliability': rel,
                        'score': float(heat),
                        'price_score': float(price_s[i]),
                        'breadth_score': float(bread_s[i]),
                        'activity_score': float(act_s[i])})
            flags = []
            if v['n'] < LOW_SAMPLE_MAX:                     # §13（只标记，不扣分、不强制垫底）
                flags.append('LOW_SAMPLE')
            if v['up_ratio'] < LOW_BREADTH_UP:              # §14（Breadth 已入 BaseHeat，不重复惩罚）
                flags.append('LOW_BREADTH')
            row['flags'] = '|'.join(flags)
            scored.setdefault(t, {})[wk] = row
        # §17 排序：Heat DESC（无第二、第三排序键以外的人工修正）
        order = sorted(names, key=lambda t: -scored[t][wk]['score'])
        for rk, t in enumerate(order, 1):
            row = scored[t][wk]
            row['rank'] = rk
            # §15 BROAD_STRONG / NARROW_STRONG：纯描述标签，不参与评分
            if rk <= TOP_N:
                row['flags'] = '|'.join(
                    [f for f in (row['flags'], 'BROAD_STRONG' if row['up_ratio'] >= LOW_BREADTH_UP
                                 else 'NARROW_STRONG') if f])

    # ── 三窗口都可排名的共同样本池（跨周期观察表在此池内定义，避免缺数据歧义）──
    universe = [t for t, v in scored.items() if all(wk in v for wk in windows)]

    report = build_report(trade_date, theme_members, scored, universe,
                          missing_themes, windows, small_watch)
    save_outputs(report, scored, universe, trade_date, small_watch)
    print(report['text'])
    return report


# ═════════════════════════ 输出 ═════════════════════════

def _f(x, d=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return '—'
    return f'{x:.{d}f}'


def _table(scored, universe, wk, ret_label):
    """§17/§23 【TODAY/WEEK/MONTH TOP10】排名 主题 Heat N Price Breadth UpRatio Activity Flags

    · Heat      = §12 的 Heat（已含 §11 Reliability 收缩）
    · Price     = §6.3 的 PriceRaw 原始值（%，非百分位）
    · Breadth   = §7 的 BreadthRaw（合成值）
    · UpRatio   = §7 的上涨成员占比（LOW_BREADTH / BROAD_STRONG / NARROW_STRONG 的判据，
                  故单独列出；1 位小数，避免「39.6% 显示成 40%」）
    · §17 的 BaseHeat / Reliability / P25 / P50 / P75 见 CSV（§22 全字段）
    """
    L = []
    L.append(f"{'排名':<4}{'主题':<12}{'Heat':>7}{'N':>6}{ret_label:>10}"
             f"{'Breadth':>9}{'UpRatio':>9}{'Activity':>9}  Flags")
    sel = [t for t in universe if wk in scored[t]]
    sel.sort(key=lambda t: scored[t][wk]['rank'])
    for t in sel[:TOP_N]:
        r = scored[t][wk]
        L.append(f"{r['rank']:<6}{t:<12}{_f(r['score'],1):>7}{r['n']:>6}"
                 f"{r['price']:>+9.2f}%{r['breadth']*100:>8.1f}%{r['up_ratio']*100:>8.1f}%"
                 f"{r['activity']:>8.2f}x  {r['flags']}")
    if not sel:
        L.append('  （无满足样本要求的主题）')
    return L


def _explain(t, wk, scored):
    """§24 客观解释：Heat/BaseHeat/Reliability 取该榜单自身窗口，只描述数据不做建议"""
    def rk(w):
        d = scored[t].get(w)
        return d['rank'] if d else None
    rt, rw, rm = rk('TODAY'), rk('WEEK'), rk('MONTH')
    d = scored[t][wk]
    ranks = '／'.join('—' if r is None else '#' + str(r) for r in (rt, rw, rm))
    body = (f"今日/本周/本月排名 {ranks}，N={d['n']}，"
            f"{wk} Heat {d['score']:.1f}（BaseHeat {d['base_score']:.1f}，"
            f"Reliability {d['reliability']:.2f}）")
    note = []
    if d['reliability'] < 1.0:                                              # §13
        note.append(f"N={d['n']}，LOW_SAMPLE，统计可靠性低于大样本主题")
    elif all(r is not None and r <= TOP_N for r in (rt, rw, rm)):
        note.append("属于大样本跨周期强势主题")
    if all(r is not None for r in (rt, rw, rm)):
        if rt <= TOP_N and rw <= TOP_N and rm > MID_N:
            note.append("近期明显增强、长期强度尚未同步")
        elif rm <= TOP_N and rt > MID_N:
            note.append("月度强势，但近期排名已回落")
        elif rm <= TOP_N and rt <= TOP_N:
            note.append("周/月维度持续靠前")
        elif rw <= TOP_N and rt > MID_N and rm > MID_N:
            note.append("仅本周进入前十，今日与月度均未进入")
    if 'NARROW_STRONG' in d['flags']:                                        # §15
        note.append(f"榜单靠前但主要由少数成员驱动（UpRatio {d['up_ratio']*100:.1f}%）")
    elif 'BROAD_STRONG' in d['flags']:
        note.append(f"内部扩散尚可（UpRatio {d['up_ratio']*100:.1f}%）")
    return '  · ' + t + '：' + body + ('（' + '；'.join(note) + '）。' if note else '。')


def build_report(trade_date, theme_members, scored, universe, missing_themes, windows, small_watch):
    L = []
    L.append(SEP_FULL)
    L.append('主题热度 V2.3')
    L.append(f'交易日 {trade_date}')
    L.append(SEP_FULL)
    L.append('* 本模块只做主题强弱排序（今日/本周/本月），不含任何交易决策。')
    L.append(f'* 权重 Price {W_PRICE:.0%} + Breadth {W_BREADTH:.0%} + Activity {W_ACTIVITY:.0%}，'
             f'PriceRaw = {W_P50:.0%}×P50 + {W_P75:.0%}×P75（成员收益率 clip±{RET_CLIP:.0f}%）。')
    L.append(f'* 经 Percentile Rank 标准化，按 Reliability = min(1, √(N/{FULL_RELIABILITY_N})) '
             f'向市场中位收缩；仅此一项统计修正，无任何人工扣分。')
    L.append('')

    for wk, label, ret in (('TODAY', '【TODAY TOP10】', '涨幅'),
                           ('WEEK', '【WEEK TOP10】', '5D'),
                           ('MONTH', '【MONTH TOP10】', '20D')):
        L.append(label)
        L += _table(scored, universe, wk, ret)
        L.append('  TOP3 判读（仅客观描述）：')
        top3 = sorted([t for t in universe if wk in scored[t]],
                      key=lambda t: scored[t][wk]['rank'])[:3]
        for t in top3:
            L.append(_explain(t, wk, scored))
        L.append('')

    # ── §18 跨周期观察表：只依据三个 Rank 分类，不重新计算综合分 ──
    def _rk(t, wk):
        return scored[t][wk]['rank']

    def _block(title, rule, pred, sort_key):
        L.append(f'【{title}】')
        L.append(rule)
        L.append(SEP_THIN)
        sel = sorted([t for t in universe if pred(t)], key=sort_key)
        for t in sel:
            L.append(f"  {t}（今日 #{_rk(t, 'TODAY')} / 本周 #{_rk(t, 'WEEK')}"
                     f" / 本月 #{_rk(t, 'MONTH')}）")
        if not sel:
            L.append('  （无）')
        L.append('')

    _block('SUSTAINED STRONG', f'Today ≤{TOP_N} AND Week ≤{TOP_N} AND Month ≤{TOP_N}',
           lambda t: max(_rk(t, 'TODAY'), _rk(t, 'WEEK'), _rk(t, 'MONTH')) <= TOP_N,
           lambda t: _rk(t, 'TODAY') + _rk(t, 'WEEK') + _rk(t, 'MONTH'))

    _block('HOT TODAY', f'Today ≤{TOP_N} AND Week >{MID_N}',
           lambda t: _rk(t, 'TODAY') <= TOP_N and _rk(t, 'WEEK') > MID_N,
           lambda t: _rk(t, 'TODAY'))

    _block('HOT WEEK', f'Week ≤{TOP_N} AND Month >{MID_N}',
           lambda t: _rk(t, 'WEEK') <= TOP_N and _rk(t, 'MONTH') > MID_N,
           lambda t: _rk(t, 'WEEK'))

    _block('COOLING', f'Month ≤{TOP_N} AND Week >{MID_N} AND Today >{CROSS_N}（仅描述热度下降）',
           lambda t: (_rk(t, 'MONTH') <= TOP_N and _rk(t, 'WEEK') > MID_N
                      and _rk(t, 'TODAY') > CROSS_N),
           lambda t: _rk(t, 'MONTH'))

    # ── §3 N<5：不进入正式排行榜，仅留档观察（保留 LOW_SAMPLE）──
    L.append(f'【LOW_SAMPLE 观察】（N<{MIN_SAMPLE}，未参与排名）')
    L.append(SEP_THIN)
    if small_watch:
        def _sw_today(x):
            return small_watch[x].get('TODAY') or next(iter(small_watch[x].values()))
        for t in sorted(small_watch, key=lambda x: -_sw_today(x)['n']):
            d = _sw_today(t)
            L.append(f"  {t}  N={d['n']}  今日涨幅 {d['price']:+.2f}%  "
                     f"UpRatio {d['up_ratio']*100:.1f}%  Activity {d['activity']:.2f}x  LOW_SAMPLE")
    else:
        L.append('  （无）')
    L.append('')

    # ── 数据验证（只报告，不据此改权重/阈值；§26 参数冻结）──
    L.append(SEP_THIN)
    L.append('【数据验证】')
    L.append(SEP_THIN)
    L.append(f'主题数量 {len(theme_members)}｜有效主题数（N≥{MIN_SAMPLE}）{len(universe)}'
             f'｜无数据主题 {len(missing_themes)}｜N<{MIN_SAMPLE} 主题 {len(small_watch)}')
    if missing_themes:
        L.append(f'  无数据主题：{"、".join(missing_themes)}')
    for wk, label in (('TODAY', 'TodayHeat'), ('WEEK', 'WeekHeat'), ('MONTH', 'MonthHeat')):
        st = dist_stats([scored[t][wk]['score'] for t in universe if wk in scored[t]])
        L.append(f'  {label:<10}' + '  '.join(f'{k} {_f(v,1)}' for k, v in st.items()))
    if len(universe) < len(scored):
        drop = [t for t in scored if t not in universe]
        L.append(f'  仅部分窗口可排名的主题（未进入跨周期观察表）：{"、".join(sorted(drop))}')
    L.append(SEP_FULL)
    return {'text': '\n'.join(L), 'trade_date': trade_date,
            'universe': universe, 'windows': windows}


WINDOWS_ORDER = ('TODAY', 'WEEK', 'MONTH')
CSV_FIELDS = ('rank', 'heat', 'base_heat', 'reliability', 'price', 'p25', 'p50', 'p75',
              'breadth', 'up_ratio', 'activity', 'n', 'flags')   # §22 固定字段，不增不减


def _csv_row(t, in_universe):
    """§22 固定列顺序：theme, in_universe, today_*, week_*, month_*"""
    r = {'theme': t, 'in_universe': in_universe}
    for wk in WINDOWS_ORDER:
        pre = wk.lower()
        for k in CSV_FIELDS:
            r[f'{pre}_{k}'] = None
    return r


def save_outputs(report, scored, universe, trade_date, small_watch):
    os.makedirs(REPORT_DIR, exist_ok=True)
    base = os.path.join(REPORT_DIR, f'theme_heat_v23_{trade_date}')
    with open(base + '.md', 'w', encoding='utf-8') as f:
        f.write(report['text'] + '\n')

    rows = []
    for t in sorted(scored, key=lambda x: (scored[x]['TODAY']['rank'] if 'TODAY' in scored[x] else 9999)):
        r = _csv_row(t, t in universe)
        for wk in WINDOWS_ORDER:
            pre = wk.lower()
            d = scored[t].get(wk)
            if d:
                r[f'{pre}_rank'] = d['rank']
                r[f'{pre}_heat'] = round(d['score'], 2)
                r[f'{pre}_base_heat'] = round(d['base_score'], 2)
                r[f'{pre}_reliability'] = round(d['reliability'], 3)
                r[f'{pre}_price'] = round(d['price'], 3)
                r[f'{pre}_p25'] = round(d['p25'], 3)
                r[f'{pre}_p50'] = round(d['p50'], 3)
                r[f'{pre}_p75'] = round(d['p75'], 3)
                r[f'{pre}_breadth'] = round(d['breadth'] * 100, 2)
                r[f'{pre}_up_ratio'] = round(d['up_ratio'] * 100, 2)
                r[f'{pre}_activity'] = None if np.isnan(d['activity']) else round(d['activity'], 3)
                r[f'{pre}_n'] = d['n']
                r[f'{pre}_flags'] = d['flags']
            elif wk in small_watch.get(t, {}):
                # §3 该窗口 N<5：不排名，仅标 LOW_SAMPLE 留档
                d = small_watch[t][wk]
                r[f'{pre}_price'] = round(d['price'], 3)
                r[f'{pre}_p25'] = round(d['p25'], 3)
                r[f'{pre}_p50'] = round(d['p50'], 3)
                r[f'{pre}_p75'] = round(d['p75'], 3)
                r[f'{pre}_breadth'] = round(d['breadth'] * 100, 2)
                r[f'{pre}_up_ratio'] = round(d['up_ratio'] * 100, 2)
                r[f'{pre}_activity'] = None if np.isnan(d['activity']) else round(d['activity'], 3)
                r[f'{pre}_n'] = d['n']
                r[f'{pre}_flags'] = 'LOW_SAMPLE'
        rows.append(r)
    for t in small_watch:                       # 所有窗口 N<5：仅观察行，rank/heat 留空
        if t in scored:
            continue
        r = _csv_row(t, False)
        for wk in WINDOWS_ORDER:
            d = small_watch[t].get(wk)
            if not d:
                continue
            pre = wk.lower()
            r[f'{pre}_price'] = round(d['price'], 3)
            r[f'{pre}_p25'] = round(d['p25'], 3)
            r[f'{pre}_p50'] = round(d['p50'], 3)
            r[f'{pre}_p75'] = round(d['p75'], 3)
            r[f'{pre}_breadth'] = round(d['breadth'] * 100, 2)
            r[f'{pre}_up_ratio'] = round(d['up_ratio'] * 100, 2)
            r[f'{pre}_activity'] = None if np.isnan(d['activity']) else round(d['activity'], 3)
            r[f'{pre}_n'] = d['n']
            r[f'{pre}_flags'] = 'LOW_SAMPLE'
        rows.append(r)
    p_csv = base + '.csv'
    pd.DataFrame(rows).to_csv(p_csv, index=False, encoding='utf-8-sig')
    p_json = base + '.json'
    with open(p_json, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n[保存] {os.path.basename(base)}.md / .csv / .json")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(arg)


if __name__ == '__main__':
    main()
