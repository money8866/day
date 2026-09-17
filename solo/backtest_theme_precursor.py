# -*- coding: utf-8 -*-
"""
主题爆发前兆回测（无未来函数）
═══════════════════════════════════════════════════════════
问题：主题（板块）爆发之前，能否提前若干天发现可观测前兆？
      各前兆的命中率、平均提前期、相对普通交易日的提升度分别是多少？

数据：
  · report_daily/theme_scores.db                 主题日度指标
  · report_daily/theme_stock_map_latest_v2.json  主题→成分股映射
  · stock_cache.get_daily_cache()                个股日线（量比/涨停/成交额）

事件（爆发日 T，发生在主题 t）：
      zt_count[T] >= --zt-min(默认5) 且 >= --ratio(默认1.5) × 前5日均值
      同主题 --cluster(默认5) 个交易日内只保留首个 → 事件簇去重

前兆（阈值均可通过命令行参数调整）：
      P1 广度普涨      up_ratio >= --p1-up(默认70，由60收紧)
      P2 资金流放大    fund_acc >= --fund(默认35)      （历史无值则不计入该样本分母）
      P3 涨停递增      zt_count >= --p3-zt(默认3) 且 > 前一日
      P4 迁移分抬升    migration_score >= --p4-mig(默认10，由8收紧)
      P5 热度跳升      hot_score - 前5日均值 >= --p5-hot(默认30)
      P6 个股放量共振  主题内 量比>=--p6-vr(2.0) 且 涨幅>=--p6-chg(3.0) 的只数 >= --p6-n(3)
      P7 龙头率先涨停  主题内当日成交额前3名含涨停股

两套口径：
      窗口口径（表①）：爆发日前 lookback 日窗口内是否出现过该前兆 → P(前兆|爆发) vs 基线
      当日口径（表③④）：某日"当天"出现该前兆 → 看未来 lookback 日内
                        是否爆发、以及未来累计超额收益

基线：同主题"非事件日且不落在任何事件前 lookback 窗口内"的交易日，同法检测；
      lift = 事件前命中率 / 基线命中率

输出：
      report_daily/theme_precursor_backtest_{end}[_tag].txt   四张表 + 事件明细
      report_daily/theme_precursor_backtest_{end}[_tag].csv   事件×前兆明细
      表① 窗口口径：事件前命中率 / 基线率 / lift / 平均提前期
      表② ≥k 项组合门槛（事件 vs 基线）
      表③ 当日口径：P(爆发|有信号) / P(爆发|无信号) / lift
      表④ 当日口径：信号后 N 日 主题等权收益 - 沪深300 超额（含近似 t 值）

用法：
      python backtest_theme_precursor.py [--start 20260724] [--end 20260915]
                                         [--lookback 5] [--zt-min 5] [--ratio 1.5]
                                         [--p1-up 70] [--p4-mig 10] [--p3-zt 3]
                                         [--p5-hot 30] [--p6-n 3] [--p6-vr 2.0]
                                         [--p6-chg 3.0] [--fund-min 35] [--horizon 5]
                                         [--tag name]
"""
import os
import sys
import json
import sqlite3
import argparse
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_cache as sc

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, 'report_daily')
DB_PATH = os.path.join(OUT_DIR, 'theme_scores.db')
MAP_PATH = os.path.join(OUT_DIR, 'theme_stock_map_latest_v2.json')

LINE = '═' * 66      # U+2550
LINE2 = '─' * 66     # U+2500

# 前兆定义（id → 说明）
PRECURSORS = [
    ('P1', '广度普涨 up_ratio>=60'),
    ('P2', '资金流放大 fund_acc>=35'),
    ('P3', '涨停递增 zt>=3 且超前日'),
    ('P4', '迁移分抬升 mig>=8'),
    ('P5', '热度跳升 hot超前5日均值30+'),
    ('P6', '个股放量共振 量比>=2&涨>=3% 家数>=3'),
    ('P7', '龙头率先涨停 成交额前3含涨停'),
]
PIDS = [p for p, _ in PRECURSORS]


def is_zt(code, pct):
    """涨停近似判定：主板9.8% / 双创19.8%"""
    lim = 19.8 if code[:2] in ('30', '68') else 9.8
    return pct >= lim


def load_theme_df(start, end):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        'SELECT trade_date, theme, zt_count, up_ratio, migration_score, hot_score, '
        'fund_acc, trend_score, sentiment_score, composite_score, lifecycle '
        'FROM theme_scores WHERE trade_date >= ? AND trade_date <= ? '
        'ORDER BY theme, trade_date',
        conn, params=(str(start), str(end)))
    conn.close()
    df['trade_date'] = df['trade_date'].astype(str)
    return df


def load_members():
    with open(MAP_PATH, encoding='utf-8') as f:
        j = json.load(f)
    out = {}
    for th, lst in j.get('themes', {}).items():
        codes = [x['code'] for x in lst if x.get('code')]
        if codes:
            out[th] = codes
    return out


def build_stock_feat(codes, start, end, silent=False):
    """{(code,date): (pct_chg, vol_ratio, amount, is_zt)}，量比=当日量/前5日均量"""
    feat = {}
    n = 0
    for c in codes:
        try:
            df = sc.get_daily_cache(c, start, end)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.sort_values('trade_date')
        vol = pd.to_numeric(df['vol'], errors='coerce')
        base = vol.shift(1).rolling(5).mean()
        vr = (vol / base.replace(0, np.nan)).to_numpy(dtype=float)
        dates = df['trade_date'].astype(str).to_numpy()
        pct = pd.to_numeric(df['pct_chg'], errors='coerce').to_numpy(dtype=float)
        amt = pd.to_numeric(df['amount'], errors='coerce').to_numpy(dtype=float)
        for k in range(len(dates)):
            feat[(c, dates[k])] = (pct[k], vr[k], amt[k], is_zt(c, pct[k]))
        n += 1
    if not silent:
        print(f'[个股] 载入 {n}/{len(codes)} 只有效K线，特征 {len(feat)} 条')
    return feat


def build_theme_daily(members, feat, dates, vr_min=2.0, chg_min=3.0, silent=False):
    """{(theme,date): (n_resonance, leader_zt)}"""
    out = {}
    for th, codes in members.items():
        for d in dates:
            n_res = 0
            lst = []
            for c in codes:
                f = feat.get((c, d))
                if f is None:
                    continue
                p, vr, amt, zt = f
                if p == p and p >= chg_min and vr == vr and vr >= vr_min:
                    n_res += 1
                lst.append((amt if amt == amt else 0.0, zt))
            lst.sort(reverse=True)
            out[(th, d)] = (n_res, any(z for _, z in lst[:3]))
    if not silent:
        print(f'[主题] 个股层聚合完成 {len(out)} 个(主题,交易日)组合')
    return out


def detect_events(df, zt_min, ratio, cluster):
    """事件簇去重的爆发日：[(theme, date, 序列索引)]"""
    events = []
    for th, g in df.groupby('theme', sort=True):
        g = g.sort_values('trade_date')
        zt = g['zt_count'].to_numpy(dtype=float)
        dts = g['trade_date'].tolist()
        last = -10 ** 9
        for i in range(5, len(g)):
            base = zt[i - 5:i].mean()
            if base > 0 and zt[i] >= zt_min and zt[i] >= ratio * base and (i - last) > cluster:
                events.append((th, dts[i], i))
                last = i
    return events


def check_prefix(g, i, lb, th, tmap, P, covered):
    """窗口口径：检测 T-lb .. T-1 内的前兆，返回 (hits{pid:提前期}, valid_pids)"""
    dd = g['trade_date'].tolist()
    zt = g['zt_count'].to_numpy(dtype=float)
    up = g['up_ratio'].to_numpy(dtype=float)
    mig = g['migration_score'].to_numpy(dtype=float)
    hot = g['hot_score'].to_numpy(dtype=float)
    fund = pd.to_numeric(g['fund_acc'], errors='coerce').to_numpy(dtype=float)

    hits = {}
    # 无成分股映射的主题无法评估个股层前兆，从分母中剔除，避免被误判为"未命中"
    valid = {'P1', 'P3', 'P4', 'P5'}
    valid |= {'P6', 'P7'} if th in covered else set()
    lo = max(0, i - lb)
    # P2 仅在该窗口内存在有效值时纳入分母，避免历史缺失导致偏差
    if not np.all(np.isnan(fund[lo:i])):
        valid.add('P2')
    for j in range(lo, i):
        lead = i - j
        d = dd[j]
        if 'P1' not in hits and up[j] == up[j] and up[j] >= P['up']:
            hits['P1'] = lead
        if 'P2' not in hits and fund[j] == fund[j] and fund[j] >= P['fund']:
            hits['P2'] = lead
        if 'P3' not in hits and j >= 1 and zt[j] >= P['zt3'] and zt[j] > zt[j - 1]:
            hits['P3'] = lead
        if 'P4' not in hits and mig[j] == mig[j] and mig[j] >= P['mig']:
            hits['P4'] = lead
        if 'P5' not in hits and j >= 5:
            m5 = np.nanmean(hot[j - 5:j])
            if m5 == m5 and hot[j] - m5 >= P['hot']:
                hits['P5'] = lead
        tf = tmap.get((th, d))
        if tf:
            if 'P6' not in hits and tf[0] >= P['nres']:
                hits['P6'] = lead
            if 'P7' not in hits and tf[1]:
                hits['P7'] = lead
    return hits, valid


def check_day(g, i, th, tmap, P, covered):
    """当日口径：检测第 i 日"当天"是否出现该前兆，返回 (hits_set, valid_pids)"""
    dd = g['trade_date'].tolist()
    zt = g['zt_count'].to_numpy(dtype=float)
    up = g['up_ratio'].to_numpy(dtype=float)
    mig = g['migration_score'].to_numpy(dtype=float)
    hot = g['hot_score'].to_numpy(dtype=float)
    fund = pd.to_numeric(g['fund_acc'], errors='coerce').to_numpy(dtype=float)

    hits = set()
    valid = {'P1', 'P3', 'P4', 'P5'}
    valid |= {'P6', 'P7'} if th in covered else set()
    if not np.isnan(fund[i]):
        valid.add('P2')
        if fund[i] >= P['fund']:
            hits.add('P2')
    if up[i] == up[i] and up[i] >= P['up']:
        hits.add('P1')
    if i >= 1 and zt[i] >= P['zt3'] and zt[i] > zt[i - 1]:
        hits.add('P3')
    if mig[i] == mig[i] and mig[i] >= P['mig']:
        hits.add('P4')
    if i >= 5:
        m5 = np.nanmean(hot[i - 5:i])
        if m5 == m5 and hot[i] - m5 >= P['hot']:
            hits.add('P5')
    tf = tmap.get((th, dd[i]))
    if tf:
        if tf[0] >= P['nres']:
            hits.add('P6')
        if tf[1]:
            hits.add('P7')
    return hits, valid


def build_theme_ret(members, feat, dates):
    """{(theme,date): 成分股等权当日收益%}"""
    out = {}
    for th, codes in members.items():
        for d in dates:
            ps = [feat[(c, d)][0] for c in codes
                  if (c, d) in feat and feat[(c, d)][0] == feat[(c, d)][0]]
            if ps:
                out[(th, d)] = float(np.mean(ps))
    return out


def load_index_ret(s, e, code='000300.SH'):
    """{date: 指数当日涨跌%}（走 index_daily_cache，非个股日线表）"""
    df = None
    try:
        df = sc.get_index_cache(code, s, e)
    except Exception:
        df = None
    if df is None or df.empty:
        try:
            df = sc.cached_index_daily(code, s, e)
        except Exception:
            return {}
    if df is None or df.empty:
        return {}
    return {str(d): float(p) for d, p in zip(df['trade_date'], df['pct_chg'])
            if p == p}


def _welch_t(a, b):
    """Welch 近似 t 值（仅作粗判，|t|>2 视为有差异）"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 2 or len(b) < 2:
        return float('nan')
    se = (a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)) ** 0.5
    if se <= 0:
        return float('nan')
    return float((a.mean() - b.mean()) / se)


def signal_study(df, events, tmap, tret, idx_ret, P, horizon, covered):
    """当日口径：P(爆发|信号) 与 信号后 horizon 日主题超额收益"""
    ev_set = {(th, d) for th, d, _ in events}
    sig = {p: {'ns': 0, 'es': 0, 'nn': 0, 'en': 0, 'rs': [], 'rn': []} for p in PIDS}
    for th, g in df.groupby('theme', sort=True):
        g = g.reset_index(drop=True)
        dts = g['trade_date'].tolist()
        for i in range(len(dts) - horizon):
            hits, valid = check_day(g, i, th, tmap, P, covered)
            fut_ev = any((th, dts[k]) in ev_set for k in range(i + 1, i + 1 + horizon))
            rs = [tret.get((th, dts[k])) for k in range(i + 1, i + 1 + horizon)]
            bs = [idx_ret.get(dts[k]) for k in range(i + 1, i + 1 + horizon)]
            if any(x is None or x != x for x in rs + bs):
                ex = None
            else:
                ex = sum(a - b for a, b in zip(rs, bs))
            for p in PIDS:
                if p not in valid:
                    continue
                if p in hits:
                    sig[p]['ns'] += 1
                    if fut_ev:
                        sig[p]['es'] += 1
                    if ex is not None:
                        sig[p]['rs'].append(ex)
                else:
                    sig[p]['nn'] += 1
                    if fut_ev:
                        sig[p]['en'] += 1
                    if ex is not None:
                        sig[p]['rn'].append(ex)
    return sig


def run(args):
    df = load_theme_df(args.start, args.end)
    if df.empty:
        print('[回测] theme_scores 无数据')
        return
    members = load_members()
    dates = sorted(df['trade_date'].unique())
    print(f'[回测] 主题日度 {len(df)} 行 / {df["theme"].nunique()} 主题 / {len(dates)} 交易日 '
          f'({dates[0]}~{dates[-1]})')

    # 个股特征需早于起始日取数（算5日均量）
    feat_start = (pd.Timestamp(dates[0]) - pd.Timedelta(days=25)).strftime('%Y%m%d')
    all_codes = sorted({c for cs in members.values() for c in cs})
    print(f'[个股] 待载入 {len(all_codes)} 只（模板成分股去重）')
    P = {'up': args.p1_up, 'mig': args.p4_mig, 'zt3': args.p3_zt, 'hot': args.p5_hot,
         'nres': args.p6_n, 'vr': args.p6_vr, 'chg': args.p6_chg, 'fund': args.fund_min}
    print(f'[阈值] P1 up>={P["up"]}  P4 mig>={P["mig"]}  P3 zt>={P["zt3"]}  '
          f'P5 hot+{P["hot"]}  P6 {P["nres"]}只(量比>={P["vr"]},涨>={P["chg"]}%)')
    feat = build_stock_feat(all_codes, feat_start, dates[-1])
    tmap = build_theme_daily(members, feat, dates, P['vr'], P['chg'])
    covered = {th for th, _ in tmap}

    events = detect_events(df, args.zt_min, args.ratio, args.cluster)
    print(f'[事件] 爆发日 {len(events)} 个（zt>={args.zt_min} 且 >= {args.ratio}×前5日均）')

    # 表③④：当日口径的信号可用性与信号后超额收益
    tret = build_theme_ret(members, feat, dates)
    idx_ret = load_index_ret(feat_start, dates[-1])
    if not idx_ret:
        print('[警告] 未取到 000300.SH 基准，表④超额收益不可用')
    sig = signal_study(df, events, tmap, tret, idx_ret, P, args.horizon, covered)

    # 事件样本与其"前 lookback 窗口"用于剔除基线污染
    ev_banned = {}
    for th, d, i in events:
        ev_banned.setdefault(th, set())
        for k in range(i - args.lookback, i + 1):
            ev_banned[th].add(k)

    ev_stats = {p: {'n': 0, 'hit': 0, 'leads': []} for p in PIDS}
    base_stats = {p: {'n': 0, 'hit': 0} for p in PIDS}
    combo_ev, combo_base = Counter(), Counter()
    rows_csv = []

    for th, g in df.groupby('theme', sort=True):
        g = g.sort_values('trade_date').reset_index(drop=True)
        dts = g['trade_date'].tolist()
        ev_idx = {i for t2, d2, i in events if t2 == th}
        ban = ev_banned.get(th, set())
        for i in range(len(g)):
            if i < args.lookback:
                continue
            hits, valid = check_prefix(g, i, args.lookback, th, tmap, P, covered)
            is_ev = i in ev_idx
            if is_ev:
                for pid in valid:
                    ev_stats[pid]['n'] += 1
                    if pid in hits:
                        ev_stats[pid]['hit'] += 1
                        ev_stats[pid]['leads'].append(hits[pid])
                combo_ev[(len([p for p in valid if p in hits]), len(valid))] += 1
                rows_csv.append([th, dts[i], 'EVENT', len(hits), '|'.join(
                    f'{p}:{hits.get(p, "-")}' for p in PIDS)])
            elif i not in ban:
                for pid in valid:
                    base_stats[pid]['n'] += 1
                    if pid in hits:
                        base_stats[pid]['hit'] += 1
                combo_base[(len([p for p in valid if p in hits]), len(valid))] += 1

    _render(args, P, dates, events, ev_stats, base_stats, combo_ev, combo_base, rows_csv, sig)


def _rate(s):
    return (s['hit'] / s['n'] * 100) if s['n'] else float('nan')


def _plabels(P):
    """按当前阈值动态生成前兆名称，便于多参数对比时直接读出配置"""
    return {
        'P1': f'广度普涨 up_ratio>={P["up"]:.0f}',
        'P2': f'资金流放大 fund_acc>={P["fund"]:.0f}',
        'P3': f'涨停递增 zt>={P["zt3"]} 且超前日',
        'P4': f'迁移分抬升 mig>={P["mig"]:.0f}',
        'P5': f'热度跳升 hot超前5日均值{P["hot"]:.0f}+',
        'P6': f'个股放量共振 量比>={P["vr"]} 涨>={P["chg"]}% 家数>={P["nres"]}',
        'P7': '龙头率先涨停 成交额前3含涨停',
    }


def _render(args, P, dates, events, ev_stats, base_stats, combo_ev, combo_base, rows_csv, sig):
    n_ev = len(events)
    labels = _plabels(P)
    out = []
    w = out.append
    w(LINE)
    w('  主题爆发前兆回测报告')
    w(f'  区间 {dates[0]}~{dates[-1]}（{len(dates)} 交易日）  爆发事件 {n_ev} 个  '
      f'前兆窗口 T-1..T-{args.lookback}')
    w(f'  事件定义 zt>={args.zt_min} 且 >= {args.ratio}×前5日均，同主题 {args.cluster} 日内去重')
    w(LINE)
    w('① 窗口口径：爆发日"之前"是否出现过该前兆（P(前兆|爆发)）')
    w(f'{"前兆":<40}{"事件前命中率":>12}{"基线率":>9}{"lift":>7}{"样本":>7}{"平均提前期":>10}')
    w(LINE2)
    for pid in PIDS:
        s, b = ev_stats[pid], base_stats[pid]
        r_ev, r_ba = _rate(s), _rate(b)
        lift = (r_ev / r_ba) if (r_ba and r_ba == r_ba) else float('nan')
        leads = s['leads']
        lead = f'{np.mean(leads):.1f}日' if leads else '—'
        w(f'{pid} {labels[pid]:<37}{r_ev:>11.1f}%{r_ba:>8.1f}%{lift:>7.2f}{s["n"]:>7}{lead:>10}')
    w(LINE2)
    w('命中率=事件前窗口内出现过该前兆的比例；基线率=同主题普通交易日（剔除事件窗口）的出现比例')
    w('lift=命中率/基线率（>1 表示该前兆在爆发前更常见）；平均提前期=首次命中距爆发日交易日数')

    def _combo_line(counter, label):
        tot = sum(counter.values())
        if not tot:
            return
        w(f'\n{label}（命中前兆数/可评估前兆数 分布，共 {tot} 个样本）')
        for k in range(0, 8):
            c = sum(v for (h, n), v in counter.items() if h == k)
            if c:
                w(f'  命中 {k} 项：{c} 个（{c / tot * 100:.0f}%）')

    _combo_line(combo_ev, ' · 爆发事件前窗口')
    _combo_line(combo_base, ' · 基线（普通交易日）')

    # 组合门槛的有效性：≥k 项命中在事件 vs 基线中的占比
    w('\n② 组合门槛对比（≥k 项命中占比）')
    w(LINE2)
    w(f'{"门槛":<10}{"事件前":>10}{"基线":>10}{"lift":>8}')
    for k in (1, 2, 3, 4, 5):
        te, tb = sum(combo_ev.values()), sum(combo_base.values())
        ce = sum(v for (h, n), v in combo_ev.items() if h >= k)
        cb = sum(v for (h, n), v in combo_base.items() if h >= k)
        pe = ce / te * 100 if te else float('nan')
        pb = cb / tb * 100 if tb else float('nan')
        w(f'≥{k:<9}{pe:>9.1f}%{pb:>9.1f}%{(pe / pb if pb else float("nan")):>8.2f}')

    # ── 表③：当日口径 P(爆发|信号) ─────────────────────────────
    hz = args.horizon
    w(f'\n③ 当日口径：当日出现该前兆后，未来 {hz} 个交易日内该主题是否爆发')
    w(f'{"前兆":<40}{"有信号":>7}{"P(爆发|信号)":>13}{"无信号":>7}{"P(爆发|无)":>12}{"lift":>7}')
    w(LINE2)
    for pid in PIDS:
        s = sig.get(pid) or {}
        ns, es = s.get('ns', 0), s.get('es', 0)
        nn, en = s.get('nn', 0), s.get('en', 0)
        p1 = (es / ns * 100) if ns else float('nan')
        p0 = (en / nn * 100) if nn else float('nan')
        lift = (p1 / p0) if (p0 and p0 == p0) else float('nan')
        w(f'{pid} {labels[pid]:<37}{ns:>7}{p1:>12.1f}%{nn:>7}{p0:>11.1f}%{lift:>7.2f}')
    w(LINE2)
    w('P(爆发|信号)=出现前兆的交易日中，未来 horizon 日内爆发的比例；')
    w('lift>1 表示出现该前兆后更易爆发（与"当天无该前兆"的交易日相比）')

    # ── 表④：信号后 horizon 日超额收益 ────────────────────────
    w(f'\n④ 信号后 {hz} 交易日：主题成分股等权累计收益 − 沪深300（单位：%）')
    w(f'{"前兆":<40}{"信号数":>7}{"有信号":>10}{"无信号":>11}{"差值":>8}{"t值":>8}')
    w(LINE2)
    for pid in PIDS:
        s = sig.get(pid) or {}
        rs, rn = s.get('rs', []), s.get('rn', [])
        m1 = float(np.mean(rs)) if rs else float('nan')
        m0 = float(np.mean(rn)) if rn else float('nan')
        d = m1 - m0
        t = _welch_t(rs, rn)
        w(f'{pid} {labels[pid]:<37}{len(rs):>7}{m1:>10.2f}{m0:>11.2f}{d:>8.2f}{t:>8.2f}')
    w(LINE2)
    w('超额=信号日后 horizon 日主题等权收益合计 − 同期沪深300收益合计（逐日超额求和）；')
    w('t值=Welch 近似（|t|>2 视为有统计差异）；样本含重叠窗口，t 值偏乐观，仅作粗判')

    w('\n爆发事件明细（命中前兆:提前期(交易日)）')
    w(LINE2)
    for th, d, tag, nh, detail in rows_csv[:60]:
        w(f'  {d} {th:<10} 命中{nh}项  {detail}')
    if len(rows_csv) > 60:
        w(f'  … 其余 {len(rows_csv) - 60} 条见 CSV')

    w(LINE)
    w('局限：样本仅覆盖 theme_scores 已有历史的交易日；事件阈值(zt-min/ratio)对结果敏感，')
    w('      建议用不同参数重复运行做稳健性检查；P2 因 fund_acc 历史覆盖有限，样本数偏小。')
    w('      主题成分股取自当前快照(theme_stock_map_latest_v2)，存在成分股前视偏差；')
    w('      表③④为滚动重叠样本，概率与 t 值偏乐观，只用于前兆排序，不作绝对水平使用。')

    text = '\n'.join(out) + '\n'
    sys.stdout.write(text)
    os.makedirs(OUT_DIR, exist_ok=True)
    sfx = f'_{args.tag}' if args.tag else ''
    txt = os.path.join(OUT_DIR, f'theme_precursor_backtest_{dates[-1]}{sfx}.txt')
    with open(txt, 'w', encoding='utf-8') as f:
        f.write(text)
    csv = os.path.join(OUT_DIR, f'theme_precursor_backtest_{dates[-1]}{sfx}.csv')
    pd.DataFrame(rows_csv, columns=['theme', 'trade_date', 'type', 'n_hit', 'detail']).to_csv(
        csv, index=False, encoding='utf-8-sig')
    print(f'[已归档] {txt}')
    print(f'[已归档] {csv}')


def main():
    ap = argparse.ArgumentParser(description='主题爆发前兆回测')
    ap.add_argument('--start', default='20260724', help='起始交易日')
    ap.add_argument('--end', default='20260915', help='结束交易日')
    ap.add_argument('--lookback', type=int, default=5, help='前兆检测窗口（T-1..T-N）')
    ap.add_argument('--zt-min', type=int, default=5, help='爆发日最小涨停家数')
    ap.add_argument('--ratio', type=float, default=1.5, help='爆发日涨停家数/前5日均值 下限')
    ap.add_argument('--cluster', type=int, default=5, help='同主题事件去重间隔（交易日）')
    ap.add_argument('--p1-up', type=float, default=70, help='P1 广度普涨：up_ratio 下限（默认70）')
    ap.add_argument('--p4-mig', type=float, default=10, help='P4 迁移分抬升：migration_score 下限（默认10）')
    ap.add_argument('--p3-zt', type=int, default=3, help='P3 涨停递增：涨停家数下限（默认3）')
    ap.add_argument('--p5-hot', type=float, default=30, help='P5 热度跳升：hot 超前5日均值下限（默认30）')
    ap.add_argument('--p6-n', type=int, default=3, help='P6 个股放量共振：最少只数（默认3）')
    ap.add_argument('--p6-vr', type=float, default=2.0, help='P6 个股放量共振：量比下限（默认2.0）')
    ap.add_argument('--p6-chg', type=float, default=3.0, help='P6 个股放量共振：涨幅下限%%（默认3.0）')
    ap.add_argument('--fund-min', type=float, default=35, help='P2 资金流放大：fund_acc 下限（默认35）')
    ap.add_argument('--horizon', type=int, default=5, help='表③④的信号观察期（交易日，默认5）')
    ap.add_argument('--tag', default='', help='输出文件名后缀（多参数稳健性对比用）')
    args = ap.parse_args()
    run(args)


if __name__ == '__main__':
    main()
