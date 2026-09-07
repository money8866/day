#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backtest_theme_gate_v41.py  —  V4.1 三级主线门禁(L2/L1/L0) 回测校准脚本
════════════════════════════════════════════════════════════════════════
目的
   对 V4.1 门禁分级做“分层前瞻收益”校准：
   * 每档(L2/L1/L0/NONE) 在 T 日收盘判定 → 统计其后 1/3/5 个交易日主题主ETF收益
   * 校验单调性期望：L2 ≥ L1 ≥ L0 ≥ NONE（越严格档位，前瞻表现越好）
   * --sensitivity：对 days_strong / mig / up_ratio 阈值做小网格扫描，供阈值再标定
   * --backfill：按时间正序对缺失 lifecycle 的历史交易日重跑 theme_score_v2（需该日
     theme_stock_map_v2_*.json 在缓存中），使门禁链(days_strong)可连续追溯

数据源（只读，唯一权威库）
   1) d:\\mystock\\solo\\report_daily\\theme_scores.db      —— 每日落库的 gate_tier/lifecycle/
      days_strong/gate_feat/fund_acc/gate_cap_amt 等（theme_score_v2 save_to_sqlite_v2 写入）
   2) cache_backbone_tushare/cache.db（tsc sqlite）        —— 各主题 main_etf 的日线(fund_daily)，
      键形如 tsc_etf_kline_end_<END>_start_<START>_ts_code_<CODE>_<TRADE_DATE>，data 为 CSV 字符串；
      本脚本对每个代码取 end 最大的条目作为前瞻收益序列（不新建任何缓存）

已知边界（勿误读）
   * 前瞻收益代理 = 主题 main_ETF 收盘→收盘收益，非个股口径；无主ETF的主题不计入样本
   * T 日门禁由 T 日收盘数据判定、用 T 日收盘买入，H 日后收盘卖出（close-to-close，未含成本）
   * 现网仅 20260901 起有 lifecycle 落库（更早日期的逐股 sentiment_detail 已不可还原），
     因此可回测证据窗口 = lifecycle 覆盖日；H5 需 ≥5 个已实现交易日，初期样本天然偏小，
     报告会对每个单元格输出 n，样本 <10 的结果仅供参考、不用于改阈值

用法
   python backtest_theme_gate_v41.py                     # 全窗口分层前瞻报告
   python backtest_theme_gate_v41.py --sensitivity        # 追加阈值敏感性扫描
   python backtest_theme_gate_v41.py --backfill           # 先按序回填缺 lifecycle 的日期再报告
   python backtest_theme_gate_v41.py --backfill --force   # 强制重跑回填窗口（用于链修正）
"""

import sys, os, json, re, sqlite3, glob, argparse
from datetime import datetime

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

OUTPUT_DB = os.path.join(BASE_DIR, "report_daily", "theme_scores.db")
# 主题 ETF 日线缓存在 theme_trend_sentiment_score 的 tsc sqlite（键名含 tts.TRADE_DATE）
TSC_DB = os.path.join(BASE_DIR, "cache_backbone_tushare", "cache.db")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

TIER_ORDER = ['L2', 'L1', 'L0', 'NONE']
HORIZONS = (1, 3, 5)

# ─────────── 与 theme_score_v2.calc_mainline_tier_v4 逐字段对齐的默认门禁 ───────────
DEFAULT_P = dict(
    days=2,          # days_strong 连续强态要求
    mig=8,           # 迁移分阈值(L1)
    up=55,           # 宽度阈值(L1/L2)
    comp_hi=75,      # L2 综合阈值
    trend_hi=75,     # L2 趋势阈值
    fund_hi=40,      # L2 主力资金强度阈值
    cap=8.0,         # 容量(亿)：梯队最大成交额
    qzt=4, qtrend=70,   # q_base 强度子条件
)


def db_connect():
    conn = sqlite3.connect(OUTPUT_DB)
    conn.row_factory = sqlite3.Row
    return conn


def load_all_rows(conn):
    """读入 theme_scores 全量行（按日期升序），并检测每列可用性"""
    cur = conn.execute("PRAGMA table_info(theme_scores)")
    cols = {r[1] for r in cur.fetchall()}
    need = ['rank', 'theme', 'n_stocks', 'trend_score', 'sentiment_score', 'composite_score',
            'up_ratio', 'zt_count', 'trade_date', 'theme_state', 'hot_score', 'hot_percentile',
            'hot_phase', 'hot_warning', 'migration_score', 'migration_direction', 'target_state',
            'trade_action', 'position_pct', 'position_label', 'suggested_position', 'lifecycle',
            'base_trade_score', 'final_trade_score', 'trade_rank', 'mainline_type', 'fund_acc',
            'gate_tier', 'days_strong', 'gate_cap_amt', 'gate_feat']
    have = [c for c in need if c in cols]
    cur.execute(f"SELECT {', '.join(have)} FROM theme_scores ORDER BY trade_date, rank")
    return [dict(r) for r in cur.fetchall()]


def load_etf_closes():
    """从 tsc sqlite(cache_backbone_tushare/cache.db) 读 ETF 日线 → {6位代码: DataFrame}

    键形如 tsc_etf_kline_end_<END>_start_<START>_ts_code_<CODE>_<TRADE_DATE>，data 为 CSV 字符串。
    同一代码可能有多条(不同 run 窗口)，取 end 最大者以保证前瞻区间最长。
    """
    import pandas as pd
    from io import StringIO
    out = {}
    if not os.path.exists(TSC_DB):
        print(f"[warn] tsc 缓存库不存在: {TSC_DB}")
        return out
    conn = sqlite3.connect(TSC_DB)
    try:
        cur = conn.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_etf_kline_%'")
        rows = cur.fetchall()
    finally:
        conn.close()
    pat = re.compile(r"^tsc_etf_kline_end_(\d{8})_start_\d{8}_ts_code_([A-Z0-9.]+)_\d{8}$")
    best = {}   # code6 -> (end, key)
    for key, _data in rows:
        m = pat.match(key)
        if not m:
            continue
        end, code = m.groups()
        code6 = code.split('.')[0]
        if code6 not in best or end > best[code6][0]:
            best[code6] = (end, key)
    # 读取每个代码的 CSV → 去重+升序 DataFrame
    conn = sqlite3.connect(TSC_DB)
    try:
        for code6, (_end, key) in best.items():
            data_str = conn.execute("SELECT data FROM cache_data WHERE key=?", (key,)).fetchone()[0]
            try:
                df = pd.read_csv(StringIO(data_str))
            except Exception as e:
                print(f"[warn] 解析 {key} 失败: {e}")
                continue
            if 'trade_date' not in df.columns or 'close' not in df.columns:
                continue
            df['trade_date'] = df['trade_date'].astype(str).str[:8]  # int 会丢前导0，统一转字符串
            df = df[['trade_date', 'close']].drop_duplicates('trade_date')
            df = df.sort_values('trade_date').reset_index(drop=True)
            out[code6] = df
    finally:
        conn.close()
    print(f"[ETF] tsc sqlite 载入 {len(best)} 只主题 ETF 日线（end≤最新缓存日）")
    return out


def theme2etf6():
    """kg_v3 配置 → {中文主题: 6位主ETF代码}；优先 main_etf，其次 etf_codes[0]"""
    try:
        import theme_score_v2 as v2
        cfg = v2.load_kg_v3_config()
    except Exception as e:
        print(f"[warn] 读取 kg_v3 配置失败: {e}")
        return {}
    cn2etf = {}
    for key, c in (cfg or {}).items():
        if key.startswith('_'):
            continue
        cn = c.get("name_cn", key)
        etf = str(c.get("main_etf", "") or "")
        if not etf or not etf[0].isdigit():
            codes = [str(x) for x in (c.get("etf_codes") or [])]
            etf = codes[0] if codes else ''
        if etf:
            cn2etf[cn] = etf.split('.')[0]
    return cn2etf


def forward_ret(df, date_str, h):
    """df 为升序 trade_date/close；返回 date 之后第 h 个交易日的收益，不足返回 None"""
    if date_str not in df['trade_date'].values:
        return None
    i = int(df.index[df['trade_date'] == date_str][0])
    j = i + h
    if j >= len(df):
        return None
    c0 = float(df.loc[i, 'close'])
    c1 = float(df.loc[j, 'close'])
    if c0 <= 0:
        return None
    return c1 / c0 - 1.0


# ─────────── calc_mainline_tier_v4 的离线复刻（阈值参数化，用于敏感性扫描）───────────
STRONG_LC = ('主升', '升温')

def _overheat(tr, se, hot_pct, hot_phase):
    climax = 1 if (tr >= 70 and se >= 85) else 0
    return (hot_pct >= 85) or (climax == 1) or (str(hot_phase) == '高潮')


def replica_gate(row, prev_lc, prev_state, P):
    lc = str(row.get('lifecycle', '') or '')
    tr = float(row.get('trend_score', 0) or 0)
    se = float(row.get('sentiment_score', 0) or 0)
    comp = float(row.get('composite_score', 0) or 0)
    mig = float(row.get('migration_score', 0) or 0)
    zt = int(row.get('zt_count', 0) or 0)
    up = float(row.get('up_ratio', 0) or 0)
    fund = float(row.get('fund_acc', 0) or 0)
    target = str(row.get('target_state', '') or '')
    hot_pct = float(row.get('hot_percentile', 50) or 50)
    hot_phase = str(row.get('hot_phase', '') or '')
    cap = float(row.get('gate_cap_amt', 0) or 0)

    strong_today = (lc in STRONG_LC) or ('分歧转一致' in target and lc == '分歧')
    strong_prev = any(k in str(prev_lc or '') for k in STRONG_LC) or \
        any(k in str(prev_state or '') for k in STRONG_LC)
    days = int(strong_today) + int(strong_prev)

    overheat = _overheat(tr, se, hot_pct, hot_phase)
    capacity = cap >= P['cap']
    q_base = (zt >= P['qzt'] or tr >= P['qtrend']) and capacity and not overheat

    if (lc == '主升' and comp >= P['comp_hi'] and tr >= P['trend_hi']
            and days >= P['days'] and q_base and up >= P['up'] and fund >= P['fund_hi']):
        return 'L2', days
    if (lc in ('升温', '分歧') and ('分歧转一致' in target or lc == '升温')
            and q_base and days >= P['days'] and mig >= P['mig'] and up >= P['up']):
        return 'L1', days
    # L0：与 calc_mainline_tier_v4 对齐——涨停>=2 或 首日强广度(up>=70 且 趋势/情绪达标)
    if (lc in ('启动', '升温', '分歧') and not overheat and mig > 0
            and (zt >= 2 or (up >= 70 and (tr >= 45 or se >= 45)))):
        return 'L0', days
    return 'NONE', days


# ─────────── 回填：缺 lifecycle 的历史日按序重跑 pipeline ───────────
def backfill_dates(conn, start, end, force=False):
    import theme_score_v2 as v2
    v2_map_dir = v2.V2_MAP_DIR
    cur = conn.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date")
    db_dates = [r[0] for r in cur.fetchall()]
    cand = sorted(d for d in db_dates if start <= d <= end)
    todo = []
    for d in cand:
        has_map = os.path.exists(os.path.join(v2_map_dir, f"theme_stock_map_v2_{d}.json"))
        has_lc = conn.execute("SELECT COUNT(*) FROM theme_scores WHERE trade_date=? AND lifecycle<>''",
                              (d,)).fetchone()[0] > 0
        if not has_map:
            print(f"  [skip] {d} 无 theme_stock_map_v2_{d}.json，无法重跑")
            continue
        if has_lc and not force:
            continue
        todo.append(d)
    if not todo:
        print("  [backfill] 无需回填（窗口内日期均已含 lifecycle）")
        return
    print(f"  [backfill] 按序重跑 {len(todo)} 日: {todo}")
    import theme_trend_sentiment_score as tts
    for d in todo:
        tts.TRADE_DATE = d            # 关键：prev_day 查询按当日定位，避免取到未来日
        print(f"\n────── 重跑 {d} ──────")
        v2.run_v2_analysis(d)
    print("  [backfill] 完成")


# ─────────── 统计 ───────────
def build_gate_frame(rows, etf_closes, cn2etf):
    """筛出 lifecycle 行；记录真实门禁 + 前瞻收益(ret1/3/5 可能为 None)"""
    recs = []
    for r in rows:
        lc = str(r.get('lifecycle', '') or '')
        if not lc:
            continue
        theme = r['theme']
        etf6 = cn2etf.get(theme, '')
        df = etf_closes.get(etf6) if etf6 else None
        d = r['trade_date']
        fwd = {}
        if df is not None:
            for h in HORIZONS:
                fwd[h] = forward_ret(df, d, h)
        recs.append({
            'date': d, 'theme': theme, 'lifecycle': lc,
            'tier': str(r.get('gate_tier', 'NONE') or 'NONE'),
            'days_strong': int(r.get('days_strong', 0) or 0),
            'has_etf': df is not None, 'etf': etf6,
            'comp': float(r.get('composite_score', 0) or 0),
            'fwd': fwd,
        })
    return recs


def bucket_stats(recs, tier, h):
    xs = [x['fwd'][h] for x in recs if x['tier'] == tier and x['fwd'].get(h) is not None]
    if not xs:
        return None
    xs = [100.0 * v for v in xs]
    xs_sorted = sorted(xs)
    n = len(xs_sorted)
    mean = sum(xs_sorted) / n
    med = xs_sorted[n // 2] if n % 2 else (xs_sorted[n // 2 - 1] + xs_sorted[n // 2]) / 2
    hit = sum(1 for v in xs_sorted if v > 0) / n * 100
    return dict(n=n, mean=mean, med=med, hit=hit)


def daily_tier_hist(recs):
    dates = sorted({x['date'] for x in recs})
    hist = {d: {t: 0 for t in TIER_ORDER} for d in dates}
    for x in recs:
        if x['tier'] in hist.get(x['date'], {}):
            hist[x['date']][x['tier']] += 1
    return dates, hist


def replay_parity(recs_rows_by_theme):
    """用 DEFAULT_P 复刻门禁，与 DB 落库 gate_tier 对比（校验回放一致性）"""
    from collections import OrderedDict
    rows_sorted = sorted([r for r in recs_rows_by_theme if (r.get('lifecycle') or '')], key=lambda x: x['trade_date'])
    by_theme = OrderedDict()
    for r in rows_sorted:
        by_theme.setdefault(r['theme'], []).append(r)
    mism = []
    for theme, lst in by_theme.items():
        prev = None
        for r in lst:
            pl = prev['lifecycle'] if prev else ''
            ps = prev['theme_state'] if prev else ''
            tier, _ = replica_gate(r, pl, ps, DEFAULT_P)
            db_tier = str(r.get('gate_tier', 'NONE') or 'NONE')
            if tier != db_tier:
                mism.append((r['trade_date'], theme, db_tier, tier))
            prev = r
    return mism


def sensitivity(recs_rows_by_theme, hist):
    """在 lifecycle 覆盖窗口上扫描 days/mig/up，输出 L1∪L2 的 H3 表现随阈值变化"""
    rows = sorted([r for r in recs_rows_by_theme if (r.get('lifecycle') or '')], key=lambda x: (x['trade_date'], x['theme']))
    from collections import OrderedDict
    by_theme = OrderedDict()
    for r in rows:
        by_theme.setdefault(r['theme'], []).append(r)
    # 每个主题按日链 prev
    enriched = []
    for theme, lst in by_theme.items():
        prev = None
        for r in lst:
            pl = prev['lifecycle'] if prev else ''
            ps = prev['theme_state'] if prev else ''
            enriched.append((r, pl, ps))
            prev = r
    cn2etf_ = theme2etf6()
    etf_closes = load_etf_closes()
    out_rows = []
    for days in (1, 2, 3):
        for mig in (5, 8, 10):
            for up in (50, 55, 60):
                P = dict(DEFAULT_P); P.update(days=days, mig=mig, up=up)
                g1 = []  # L1∪L2 H3
                for (r, pl, ps) in enriched:
                    tier, _ = replica_gate(r, pl, ps, P)
                    if tier not in ('L1', 'L2'):
                        continue
                    df = etf_closes.get(cn2etf_.get(r['theme'], ''))
                    v = forward_ret(df, r['trade_date'], 3) if df is not None else None
                    if v is not None:
                        g1.append(100.0 * v)
                out_rows.append((days, mig, up, len(g1),
                                 (sum(g1) / len(g1)) if g1 else None))
    return out_rows


# ─────────── 报告 ───────────
def fmt_pct(x, nd=2):
    return f"{x:+.{nd}f}%" if x is not None else "   -  "


def build_report(recs, parity_mism, hist_rows, sens=None, start='', end=''):
    L = []
    w = L.append
    w("V4.1 三级主线门禁 回测校准报告")
    w("═" * 72)
    w(f"信号窗口: {start} ~ {end}  |  主题-日样本: {len(recs)}  |  复刻一致性: "
      f"{'OK' if not parity_mism else f'不一致{len(parity_mism)}处(见下)'}")
    if parity_mism:
        for m in parity_mism[:10]:
            w(f"   mismatch {m}")
    # 每日档位分布
    w("")
    w("一、每日门禁分级分布")
    w("─" * 44)
    w(f"{'日期':<10}{'L2':>4}{'L1':>4}{'L0':>4}{'NONE':>6}")
    for d, c in hist_rows:
        w(f"{d:<10}{c['L2']:>4}{c['L1']:>4}{c['L0']:>4}{c['NONE']:>6}")
    w(f"{'合计':<10}{sum(c['L2'] for _, c in hist_rows):>4}"
      f"{sum(c['L1'] for _, c in hist_rows):>4}{sum(c['L0'] for _, c in hist_rows):>4}"
      f"{sum(c['NONE'] for _, c in hist_rows):>6}")
    # 分层前瞻收益
    w("")
    w("二、分档前瞻收益（主题主ETF close→close，越严格档位应越高）")
    w("─" * 92)
    for h in HORIZONS:
        w(f"H+{h} | {'档位':<5}{'n':>5}{'均收益':>10}{'中位':>9}{'胜率(>0)':>10}")
        for t in TIER_ORDER:
            s = bucket_stats(recs, t, h)
            if s is None:
                w(f"     {t:<5}{0:>5}{'无样本':>10}")
            else:
                _hit = f"{s['hit']:.0f}%"
                w(f"     {t:<5}{s['n']:>5}{fmt_pct(s['mean'], 2):>10}"
                  f"{fmt_pct(s['med'], 2):>9}{_hit:>10}")
    # 单调性检查
    w("")
    w("三、单调性检查（L2≥L1≥L0 为门禁设计目标）")
    w("─" * 92)
    for h in HORIZONS:
        seq = []
        for t in TIER_ORDER[:-1]:
            s = bucket_stats(recs, t, h)
            seq.append(s['mean'] if s and s['n'] >= 3 else None)
        if all(v is not None for v in seq):
            ok = seq[0] >= seq[1] >= seq[2]
            w(f"H+{h}: L2/L1/L0 = {[fmt_pct(v,1) for v in seq]}  "
              f"{'单调 ✓' if ok else '单调 ✗ (样本小或市场结构所致, 勿单日下结论)'}")
        else:
            w(f"H+{h}: L2/L1/L0 有效样本(<3)不足，暂不判单调")
    # 敏感性
    w("")
    if sens:
        w("四、阈值敏感性（L1∪L2 前瞻 H+3，days×mig×up 网格）")
        w("─" * 92)
        w(f"{'days_strong':>11}{'mig':>5}{'up_ratio':>9}{'n':>5}{'均收益H3':>11}")
        for days, mig, up, n, mean in sens:
            w(f"{days:>11}{mig:>5}{up:>9}{n:>5}{fmt_pct(mean, 2):>11}")
        w("  注: 样本为当前窗口（多日样本会快速放大），阈值的正式取舍建议累计 ≥30 个 H3 样本后再定")
    else:
        w("四、阈值敏感性：--sensitivity 未开启")
    # 边界
    w("")
    w("边界说明")
    w("─" * 92)
    w("* 收益口径为 主题 main_ETF 收盘→收盘，未含交易成本；无主ETF的主题不计入分层表")
    w("* 每格输出真实 n；n<10 时均值仅供参考、不据此改阈值")
    w("* 门禁自 20260901 起有 lifecycle 落库；H+5 需更长时间积累，随每日运行样本自动增长")
    return "\n".join(L)


def main(argv):
    ap = argparse.ArgumentParser(description="V4.1 门禁三档回测校准")
    ap.add_argument("--start", default="", help="起点 YYYYMMDD（默认=最早含 lifecycle 日）")
    ap.add_argument("--end", default="", help="终点 YYYYMMDD（默认=DB最新日）")
    ap.add_argument("--backfill", action="store_true", help="先对缺 lifecycle 的历史日按序重跑 pipeline")
    ap.add_argument("--force", action="store_true", help="配合 --backfill 强制重跑窗口全部日期")
    ap.add_argument("--sensitivity", action="store_true", help="输出阈值敏感性网格")
    ap.add_argument("--no-save", action="store_true", help="不写 markdown 文件")
    a = ap.parse_args(argv)

    conn = db_connect()
    all_rows = load_all_rows(conn)
    dates_all = sorted({r['trade_date'] for r in all_rows})
    lc_dates = sorted({r['trade_date'] for r in all_rows if (r.get('lifecycle') or '')})
    start = a.start or (lc_dates[0] if lc_dates else '')
    end = a.end or (dates_all[-1] if dates_all else '')

    if a.backfill:
        backfill_dates(conn, start, end, force=a.force)
        all_rows = load_all_rows(conn)  # 重新读取
        lc_dates = sorted({r['trade_date'] for r in all_rows if (r.get('lifecycle') or '')})

    if not lc_dates:
        print("尚无含 lifecycle 的交易日落库。请先运行 theme_score_v2.py（或 --backfill）积累门禁历史。")
        return

    # 只取有 lifecycle 的窗口行
    rows_in = [r for r in all_rows if (r.get('lifecycle') or '') and start <= r['trade_date'] <= end]
    cn2etf = theme2etf6()
    etf_closes = load_etf_closes()
    recs = build_gate_frame(rows_in, etf_closes, cn2etf)
    parity = replay_parity(rows_in)
    dates, hist = daily_tier_hist(recs)

    sens = None
    if a.sensitivity:
        sens = sensitivity(rows_in, hist)

    print()
    report = build_report(recs, parity, list(zip(dates, [hist[d] for d in dates])),
                          sens=sens, start=start, end=end)
    print(report)
    print()

    if not a.no_save:
        os.makedirs(REPORT_DIR, exist_ok=True)
        out = os.path.join(REPORT_DIR, f"backtest_gate_v41_{start}_{end}.md")
        with open(out, 'w', encoding='utf-8') as f:
            f.write(report + "\n")
        print(f"[保存] {out}")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
