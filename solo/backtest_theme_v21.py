# -*- coding: utf-8 -*-
"""
主题量化分析 V2.1 —— 历史回测验证（T+1 / T+3 / T+5 / T+10）

数据源：report_daily/theme_scores.db 的 theme_v21_daily 表（由 theme_score_v2.py --v21-backfill N 生成）
收益口径：成分股等权日收益复利 —— 主题在第 d 日的 ret_1 = 当日成分股等权 pct_chg 均值(%)，
          T→T+k 的持有收益 = Π(1 + ret_1/100) - 1。等于"等权、每日再平衡"的组合 P&L。

验证目标（对应 V2.1 规格第十九节）：
  A. Confirmation >= 70 的未来 T+5 是否显著优于 Confirmation < 50
  B. EARLY_FLOW 状态的未来 T+5 是否有提前预测价值
  C. ACCELERATION 状态是否存在明显追高风险（高收益 vs 高回撤）
  D. HOT_BUT_UNCONFIRMED 是否真的应该禁止追涨

用法：
  python backtest_theme_v21.py            # 全量（table 内所有日期）
  python backtest_theme_v21.py 60         # 只统计最近 60 个交易日
"""
import os
import sys
import math
import sqlite3
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
DB = os.path.join(REPORT_DIR, "theme_scores.db")
TABLE = "theme_v21_daily"
HORIZONS = (1, 3, 5, 10)

# V2 基线指纹（在 V2.1 落层前记录，用于 PASS8：V2 输出必须零改动）
V2_BASELINE = {
    'theme_scores_rows': 1272,
    'theme_scores_dates': 41,
    'theme_scores_md5': '61da2772d3f55d4d626ee1f1b79914a8',
    'theme_scores_v2_20260918.csv': '91a315dc48b350680622388637ceb482',
    'theme_scores_v2_20260917.csv': 'e0e8e95d477ef8aebf1083c2b48abb37',
}


# ─────────── 统计工具（不引入 scipy，手动实现 Welch t 检验） ───────────

def _mean(xs):
    return sum(xs) / len(xs) if xs else float('nan')


def _median(xs):
    if not xs:
        return float('nan')
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _winrate(xs):
    return 100.0 * sum(1 for x in xs if x > 0) / len(xs) if xs else float('nan')


def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def welch_t(a, b):
    """Welch t 检验，返回 (t, p_two_sided)。样本不足返回 (nan, nan)。"""
    if len(a) < 3 or len(b) < 3:
        return float('nan'), float('nan')
    ma, mb = _mean(a), _mean(b)
    va, vb = _std(a) ** 2, _std(b) ** 2
    na, nb = len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return float('nan'), float('nan')
    t = (ma - mb) / se
    p = 2.0 * (1.0 - _norm_cdf(abs(t)))
    return t, p


def _fmt(x, d=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{d}f}"


# ─────────── 读取 V2.1 历史 + 计算前瞻收益 ───────────

def load_rows(days=None):
    if not os.path.exists(DB):
        raise SystemExit(f"未找到 {DB}")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            f"SELECT DISTINCT trade_date FROM {TABLE} ORDER BY trade_date DESC")
        dates = [r[0] for r in cur.fetchall()]
        if days:
            dates = dates[:int(days)]
        if not dates:
            raise SystemExit(f"{TABLE} 无数据：请先执行 python theme_score_v2.py --v21-backfill 60")
        ph = ",".join("?" * len(dates))
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM {TABLE} WHERE trade_date IN ({ph}) ORDER BY trade_date ASC", dates)]
        return rows, sorted(dates)
    finally:
        conn.close()


def attach_forward(rows, all_dates):
    """为每行计算 T+k 前瞻收益（等权复利）+ 持有期内最大回撤"""
    pos = {d: i for i, d in enumerate(all_dates)}
    by_theme = defaultdict(list)
    for r in rows:
        by_theme[r['theme']].append(r)
    for th in by_theme:
        by_theme[th].sort(key=lambda x: x['trade_date'])
    for th, seq in by_theme.items():
        for i, r in enumerate(seq):
            for k in HORIZONS:
                r[f'fwd_{k}'] = None
                r[f'mdd_{k}'] = None
            for k in HORIZONS:
                seg = seq[i + 1:i + 1 + k]
                if len(seg) < k:
                    continue
                eq, peak, mdd = 1.0, 1.0, 0.0
                for s in seg:
                    eq *= (1.0 + float(s.get('ret_1') or 0) / 100.0)
                    peak = max(peak, eq)
                    mdd = min(mdd, eq / peak - 1.0)
                r[f'fwd_{k}'] = (eq - 1.0) * 100.0
                r[f'mdd_{k}'] = mdd * 100.0
    # 状态是否在后续 3 日内升级（EARLY_FLOW 前瞻性验证用）
    for th, seq in by_theme.items():
        for i, r in enumerate(seq):
            nxt = [s.get('state') for s in seq[i + 1:i + 4]]
            r['next3_states'] = nxt
    return rows


def group_stats(rows, key, label):
    out = []
    buckets = defaultdict(list)
    for r in rows:
        buckets[str(r.get(key) or '—')].append(r)
    for b, rs in buckets.items():
        out.append((label, b, rs))
    return out


def describe(rs, k, tag=''):
    """对一组信号在某持有期上的收益描述"""
    vals = [r[f'fwd_{k}'] for r in rs if r.get(f'fwd_{k}') is not None]
    mdds = [r[f'mdd_{k}'] for r in rs if r.get(f'mdd_{k}') is not None]
    return {
        'tag': tag, 'n': len(vals),
        'mean': _mean(vals), 'median': _median(vals),
        'win': _winrate(vals),
        'mdd': _mean(mdds),
        'worst': min(vals) if vals else float('nan'),
        'best': max(vals) if vals else float('nan'),
        'vals': vals,
    }


def fmt_stat(s):
    return (f"n={s['n']:<5} 均值{s['mean']:>7.2f}%  中位{s['median']:>7.2f}%  "
            f"胜率{s['win']:>5.1f}%  平均回撤{s['mdd']:>7.2f}%")


# ─────────── PASS 验收（规格第二十节） ───────────

# 状态「改善阶梯序」（与 theme_score_v2.V21_STATE_LADDER 一致；此处独立定义避免重依赖）
LADDER = ('RETREAT', 'WEAK', 'EXHAUSTION', 'DIVERGENCE', 'RECOVERY',
          'OSCILLATION', 'EARLY_FLOW', 'STARTING', 'STRONG_TREND', 'ACCELERATION')
LADDER_CHAIN = ('WEAK', 'EARLY_FLOW', 'STARTING', 'STRONG_TREND',
                'ACCELERATION', 'OSCILLATION', 'EXHAUSTION', 'RETREAT')


def _li(s):
    return LADDER.index(s) if s in LADDER else None


def check_v2_intact():
    """PASS8：V2 既有产物必须与 V2.1 落层前的基线完全一致"""
    import hashlib
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT trade_date,theme,trend_score,sentiment_score,composite_score,lifecycle,gate_tier "
        "FROM theme_scores ORDER BY trade_date,theme").fetchall()
    conn.close()
    md5 = hashlib.md5(repr(rows).encode()).hexdigest()
    ok = (len(rows) == V2_BASELINE['theme_scores_rows']
          and len(set(r[0] for r in rows)) == V2_BASELINE['theme_scores_dates']
          and md5 == V2_BASELINE['theme_scores_md5'])
    det = [f"theme_scores {len(rows)}行/{len(set(r[0] for r in rows))}日 "
           f"md5 {'一致' if md5 == V2_BASELINE['theme_scores_md5'] else '不一致'}({md5[:8]})"]
    for f, exp in V2_BASELINE.items():
        if not f.endswith('.csv'):
            continue
        p = os.path.join(REPORT_DIR, f)
        if not os.path.exists(p):
            continue
        h = hashlib.md5(open(p, 'rb').read()).hexdigest()
        ok = ok and (h == exp)
        det.append(f"{f} {'一致' if h == exp else '不一致'}")
    return ok, '；'.join(det)


def acceptance(rows):
    """PASS1~PASS8 数据驱动验收（逐条验证规格硬约束，而非“代码跑通”）"""
    out = []

    def add(pid, name, ok, detail):
        out.append((pid, name, 'PASS' if ok else 'FAIL', detail))

    # PASS1 高 Migration + 低 Trend 不得自动变成强趋势
    v1 = [r for r in rows if float(r['migration']) >= 25 and float(r['trend']) < 50
          and r['state'] in ('STRONG_TREND', 'ACCELERATION')]
    add('PASS1', '高Migration+低Trend 不得直接判强趋势/加速', not v1,
        f"违例 {len(v1)} 条" + (f"（首个 {v1[0]['theme']}@{v1[0]['trade_date']}）" if v1 else ""))

    # PASS2 高 Composite + 低 Confirmation 必须有 OSCILLATION / HOT_BUT_UNCONFIRMED 出口
    hi_lo = [r for r in rows if float(r['composite']) >= 60 and float(r['confirmation']) < 60]
    bad2 = [r for r in hi_lo if r['state'] in ('STRONG_TREND', 'ACCELERATION')]
    osc = sum(1 for r in hi_lo if r['state'] == 'OSCILLATION')
    hot = sum(1 for r in hi_lo if r['quadrant'] == 'HOT_BUT_UNCONFIRMED')
    add('PASS2', '高强度+低确认 → OSCILLATION / HOT_BUT_UNCONFIRMED',
        (not bad2) and (osc + hot > 0),
        f"样本 {len(hi_lo)}：OSCILLATION {osc} / HOT_BUT_UNCONFIRMED {hot} / 误判为强趋势 {len(bad2)}")

    # PASS3 低 Trend + 高 Migration + 高 Emotion 必须能识别 EARLY_FLOW
    ef = [r for r in rows if r['state'] == 'EARLY_FLOW']
    ef_c = [r for r in ef if float(r['trend']) < 50 and float(r['migration']) >= 20
            and float(r['emotion']) >= 60]
    add('PASS3', '低Trend+高Migration+高Emotion 可识别 EARLY_FLOW', len(ef_c) > 0,
        f"EARLY_FLOW {len(ef)} 条，其中同时满足低趋势+高迁移+高情绪 {len(ef_c)} 条")

    # PASS4 ACCELERATION 必须多因子同时确认，不能只依赖涨停数
    acc = [r for r in rows if r['state'] == 'ACCELERATION']
    bad4 = [r for r in acc if not (float(r['trend']) >= 70 and float(r['breadth']) >= 65
                                   and float(r['leadership']) >= 65 and float(r['confirmation']) >= 65)]
    low_zt = sum(1 for r in acc if int(r['limitup'] or 0) < 5)
    add('PASS4', 'ACCELERATION 需 趋势/广度/龙头/确认 同时满足', not bad4,
        f"ACCELERATION {len(acc)} 条，缺项违例 {len(bad4)} 条，其中涨停<5家 {low_zt} 条（非涨停数驱动）")

    # PASS5 TradePermission 不得由 Composite 单独决定
    bad5 = [r for r in rows if float(r['composite']) >= 65 and float(r['confirmation']) < 65
            and r['trade_permission'] in ('TRADEABLE', 'CONDITIONAL')]
    add('PASS5', 'TradePermission 不得由 Composite 单独决定', not bad5,
        f"高综合分(≥65)但确认<65 却仍授予交易许可：{len(bad5)} 条")

    # PASS6 ChaseRisk≥75 必须 PULLBACK_ONLY，绝不 MARKET_BUY
    risky = [r for r in rows if float(r['chase_risk']) >= 75]
    bad6 = [r for r in risky if r['buy_mode'] == 'MARKET_BUY']
    add('PASS6', 'ChaseRisk≥75 → 只可回踩买（PULLBACK_ONLY）', not bad6,
        f"追高风险≥75 {len(risky)} 条，其中 MARKET_BUY {len(bad6)} 条"
        + (f"（其中 {sum(1 for r in risky if r['buy_mode'] == 'PULLBACK_ONLY')} 条已降为回踩）" if risky else ""))

    # PASS7 状态连续性：链条可达 + 转换方向标注自洽
    seen = set(r['state'] for r in rows)
    miss = [s for s in LADDER_CHAIN if s not in seen]
    trs = [r for r in rows if r['state_change'] in ('UPGRADE', 'DOWNGRADE')]
    bad7 = []
    for r in trs:
        i0, i1 = _li(r['prev_state'] or ''), _li(r['state'] or '')
        if i0 is None or i1 is None:
            continue
        if (r['state_change'] == 'UPGRADE') != (i1 > i0):
            bad7.append(r)
    add('PASS7', '状态连续性：WEAK→…→RETREAT 链条可识别且方向自洽',
        (not miss) and (not bad7),
        f"观测到 {len(seen)}/10 个状态，链条缺失 {miss or '无'}；转换样本 {len(trs)} 条，方向矛盾 {len(bad7)} 条")

    # PASS8 V2 兼容：V2 原始结果保持不变
    ok8, det8 = check_v2_intact()
    add('PASS8', 'V2 原始输出保持不变（A/B 同源）', ok8, det8)

    return out


# ─────────── 主流程 ───────────

def main(days=None):
    rows, dates = load_rows(days)
    attach_forward(rows, dates)
    L = []
    W = L.append
    sep = "─" * 86
    W("=" * 86)
    W(f"主题量化分析 V2.1 · 历史回测验证（{dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日）")
    W("=" * 86)
    W(f"样本：{len(rows)} 个「主题×交易日」信号；收益口径：成分股等权日收益复利（每日再平衡）")
    W("说明：前瞻收益依赖主题成分股当期快照（池内成分不回溯历史变动），属一致口径下的相对比较，")
    W("       不构成绝对收益预测；T+k 样本随持有期递减。")
    W("")

    # ── 基准 ──
    W(sep)
    W("【基准】全部主题日（不做任何筛选）")
    W(sep)
    base = {}
    for k in HORIZONS:
        base[k] = describe(rows, k, f'ALL T+{k}')
        W(f"  T+{k:<2} {fmt_stat(base[k])}")
    W("")

    # ── 分维度：State / TradePermission / Confirmation 桶 / 四象限 ──
    for key, title in (('state', '按 State 状态分组'),
                       ('trade_permission', '按 TradePermission 分组'),
                       ('quadrant', '按四象限分组')):
        W(sep)
        W(f"【{title}】")
        W(sep)
        buckets = defaultdict(list)
        for r in rows:
            buckets[str(r.get(key) or '—')].append(r)
        order = sorted(buckets.items(), key=lambda z: -len(z[1]))
        for b, rs in order:
            s5 = describe(rs, 5)
            s1 = describe(rs, 1)
            s10 = describe(rs, 10)
            W(f"  {b:<22} n={s5['n']:<5} T+1 均值{s1['mean']:>6.2f}% 胜率{s1['win']:>5.1f}% | "
              f"T+5 均值{s5['mean']:>6.2f}% 中位{s5['median']:>6.2f}% 胜率{s5['win']:>5.1f}% "
              f"回撤{s5['mdd']:>6.2f}% | T+10 均值{s10['mean']:>6.2f}%")
        W("")

    W(sep)
    W("【按 Confirmation 分桶】")
    W(sep)
    conf_bins = [(0, 45, '<45'), (45, 55, '45~55'), (55, 65, '55~65'),
                 (65, 70, '65~70'), (70, 101, '≥70')]
    for lo, hi, name in conf_bins:
        rs = [r for r in rows if lo <= float(r.get('confirmation') or 0) < hi]
        if not rs:
            continue
        W(f"  Confirmation {name:<8} n={len(rs):<5} " + " | ".join(
            f"T+{k} {_fmt(describe(rs, k)['mean'])}%" for k in HORIZONS) +
          f"  T+5胜率{_fmt(describe(rs, 5)['win'], 1)}%")
    W("")

    # ── A / B / C / D 四问 ──
    W("=" * 86)
    W("【验证 A】Confirmation ≥ 70 的未来 T+5 是否显著优于 Confirmation < 50")
    W("=" * 86)
    hi = [r for r in rows if float(r.get('confirmation') or 0) >= 70]
    lo = [r for r in rows if float(r.get('confirmation') or 0) < 50]
    a_hi, a_lo = describe(hi, 5), describe(lo, 5)
    W(f"  Conf≥70 : {fmt_stat(a_hi)}")
    W(f"  Conf<50 : {fmt_stat(a_lo)}")
    t, p = welch_t(a_hi['vals'], a_lo['vals'])
    diff = (a_hi['mean'] - a_lo['mean']) if a_hi['n'] and a_lo['n'] else float('nan')
    W(f"  差异：均值 {_fmt(diff)}pct，Welch t={_fmt(t)}，p={_fmt(p, 4)} → "
      f"{'PASS（显著优于）' if p == p and p < 0.05 and diff > 0 else 'FAIL/不显著'}")
    W("")

    W("=" * 86)
    W("【验证 B】EARLY_FLOW 的未来 T+5 是否有提前预测价值")
    W("=" * 86)
    ef = [r for r in rows if r.get('state') == 'EARLY_FLOW']
    if ef:
        W(f"  EARLY_FLOW : {fmt_stat(describe(ef, 5))}")
        W(f"  相对基准 T+5 差值：{_fmt(describe(ef, 5)['mean'] - base[5]['mean'])}pct")
        up = sum(1 for r in ef if any(s in ('STARTING', 'STRONG_TREND', 'ACCELERATION')
                                      for s in (r.get('next3_states') or [])))
        W(f"  后续 3 日内升级到 STARTING/STRONG_TREND/ACCELERATION 的比例："
          f"{_fmt(100.0 * up / len(ef), 1)}%（{up}/{len(ef)}）")
        t, p = welch_t(describe(ef, 5)['vals'], base[5]['vals'])
        W(f"  Welch t={_fmt(t)}，p={_fmt(p, 4)}")
    else:
        W("  无 EARLY_FLOW 样本")
    W("")

    W("=" * 86)
    W("【验证 C】ACCELERATION 是否存在明显追高风险")
    W("=" * 86)
    acc = [r for r in rows if r.get('state') == 'ACCELERATION']
    if acc:
        for k in HORIZONS:
            W(f"  T+{k:<2} {fmt_stat(describe(acc, k))}")
        s1 = describe(acc, 1)
        W(f"  T+1 回撤均值 {_fmt(s1['mdd'])}%；追高判定：T+1 均值"
          f"{'为正但仍需回踩确认' if s1['mean'] > 0 else '为负（追高即亏）'}")
        cr_hi = [r for r in acc if float(r.get('chase_risk') or 0) >= 75]
        cr_lo = [r for r in acc if float(r.get('chase_risk') or 0) < 75]
        if cr_hi and cr_lo:
            W(f"  拆 ChaseRisk：≥75 {fmt_stat(describe(cr_hi, 5), )}")
            W(f"                <75 {fmt_stat(describe(cr_lo, 5), )}")
    else:
        W("  无 ACCELERATION 样本")
    W("")

    W("=" * 86)
    W("【验证 D】HOT_BUT_UNCONFIRMED 是否真的应该禁止追涨")
    W("=" * 86)
    hot = [r for r in rows if r.get('quadrant') == 'HOT_BUT_UNCONFIRMED']
    core = [r for r in rows if r.get('quadrant') == 'CORE_MAINLINE']
    if hot:
        W(f"  HOT_BUT_UNCONFIRMED : {fmt_stat(describe(hot, 5))}")
        for k in HORIZONS:
            W(f"    T+{k:<2} 均值{_fmt(describe(hot, k)['mean'])}% "
              f"中位{_fmt(describe(hot, k)['median'])}% 胜率{_fmt(describe(hot, k)['win'], 1)}% "
              f"回撤{_fmt(describe(hot, k)['mdd'])}%")
    if core:
        W(f"  CORE_MAINLINE      : {fmt_stat(describe(core, 5))}")
    if hot and core:
        t, p = welch_t(describe(core, 5)['vals'], describe(hot, 5)['vals'])
        W(f"  CORE vs HOT 的 T+5 差异 t={_fmt(t)}，p={_fmt(p, 4)}")
    W("")

    # ── 状态转换价值 ──
    W(sep)
    W("【状态转换（D-1 → D）胜率验证】")
    W(sep)
    tr = defaultdict(list)
    for r in rows:
        if r.get('state_change') in ('UPGRADE', 'DOWNGRADE', 'FLAT'):
            tr[(r.get('prev_state') or 'NEW', r.get('state'), r['state_change'])].append(r)
    for (ps, cs, ch), rs in sorted(tr.items(), key=lambda z: -len(z[1]))[:12]:
        if len(rs) < 3:
            continue
        s = describe(rs, 5)
        W(f"  {ps:<13} → {cs:<13} [{ch:<9}] n={s['n']:<4} T+5 均值{s['mean']:>7.2f}% "
          f"胜率{s['win']:>5.1f}% 回撤{s['mdd']:>7.2f}%")
    W("")

    W(sep)
    W("【PASS/FAIL 验收表（规格第二十节，逐条数据驱动验证）】")
    W(sep)
    res = acceptance(rows)
    for pid, name, verdict, detail in res:
        W(f"  {pid}  [{verdict}] {name}")
        W(f"        {detail}")
    n_pass = sum(1 for r in res if r[2] == 'PASS')
    W(f"  ── 合计 PASS {n_pass}/{len(res)} ──")
    W("")

    txt = "\n".join(L) + "\n"
    print(txt)
    out = os.path.join(REPORT_DIR, f"theme_v21_backtest_{dates[0]}_{dates[-1]}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("```\n" + txt + "```\n")
    print(f"[保存] {out}")
    return rows


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(d)
