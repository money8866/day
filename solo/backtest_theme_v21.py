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

# V2 基线指纹（用于 PASS8：V2 既有产物必须与 V2.1 落层前一致）
#
# [2026-09-23 重设] 上一版基线（1272 行 / 41 日）是在 V2.1 落层前记录的，其后发生了两件
#   与 V2.1 代码无关的事，导致指纹漂移：
#     1) 62 日回填新增了 20260921 / 20260922 两个交易日（+64 行 = 16 主题×2 日），
#        1272 + 64 = 1336，与实测完全吻合；
#     2) 本会话为刷新 V2.1 单日产物重跑了 20260918，该日 V2 输入里的
#        dc_index/dc_member 板块成份与热榜是实时抓取的，重抓后 0918 的 CSV 随之变化。
#   证据：未被重跑的 20260917 CSV 仍与旧基线逐字一致（e0e8e95d…）→ 变化由「重跑+实时抓取」引起。
#   代码侧核对：git diff 的全部 hunk 均落在 L4373–L5112 的 V2.1 段内，
#   未触及产生 trend/sentiment/composite/lifecycle/gate_tier 的 V2 评分路径。
#   故按当前实测重设基线；若日后再次回填或重跑某历史日，需同步重设本表（属预期行为，非回归）。
V2_BASELINE = {
    'theme_scores_rows': 1336,
    'theme_scores_dates': 43,
    'theme_scores_md5': 'd6b82cba40e6faeff1b5c910e2969330',
    'theme_scores_v2_20260918.csv': '077cf9a382519f657af7f51b7b27ce80',
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


def _synth(**kw):
    """合成特征向量：默认取「弱势中性」基线，逐项覆盖以构造边界场景"""
    f = {'trend': 30.0, 'emotion': 50.0, 'composite': 50.0, 'strength': 50.0,
         'migration': 0.0, 'breadth': 40.0, 'leadership': 60.0, 'persistence': 40.0,
         'confirmation': 40.0, 'flow': 0.0, 'limitup': 0, 'zt_count': 0, 'up_ratio': 50.0,
         'ret_1': 0.0, 'mkt_ret_1': 0.0, 'ma20_b': 0.0, 'pos_in_20': 0.5, 'n_stocks': 50,
         'd_breadth': 0.0, 'd_trend': 0.0, 'd_persistence': 0.0, 'prev_state': '',
         'single_leader_risk': 'LOW', 'zt_expansion': False, 'breadth_expansion': False,
         'pers_d3': 40.0, 'pers_d5': 40.0}
    f.update(kw)
    return f


def rule_checks():
    """规则级验证：把合成特征喂给 V2.1 的真实函数，检验「规则本身」是否成立。

    样本级统计会被行情格局与样本量卡住（如 ACCELERATION 仅 5 条、ChaseRisk≥75 仅 1 条），
    名义 PASS 但实质空洞；规格第二十节要求的是逐条验证规则，故必须补充本组确定性单测。
    返回 [(rid, 名称, ok, 明细)]
    """
    try:
        import theme_score_v2 as ts2
    except Exception as e:              # 导入失败不应让回测整体崩掉
        return [('R0', '规则级单测', False, f'导入 theme_score_v2 失败，规则级验证不可用：{e}')]

    def perm_of(f, comp_q, brd_q, crowd_q):
        """按「当日横截面分位」定档（V2.1 分层重构后许可不再是绝对门槛函数）"""
        g = dict(f)
        g['state'] = ts2.classify_state_v21(g)
        g['quadrant'] = ts2._v21_quadrant(g)
        g['mainline_candidate'] = ts2._v21_mainline_candidate(g)
        g['chase_risk'] = ts2.calc_chase_risk_v21(g)[0]
        g['trade_permission'] = ts2._v21_permission_xs(
            g, {'comp': comp_q, 'brd': brd_q, 'crowd': crowd_q})
        return ts2._v21_action(g)

    out = []

    # R1（PASS1）高迁移 + 低趋势 → 绝不允许判为 STRONG_TREND / ACCELERATION
    f = _synth(migration=35.0, trend=42.0, emotion=80.0, breadth=38.0, up_ratio=40.0,
               composite=78.0, confirmation=72.0, leadership=80.0, persistence=50.0,
               limitup=18, zt_count=18, zt_expansion=True, d_breadth=6.0, d_trend=4.0,
               pers_d3=70.0, pers_d5=60.0)
    s = ts2.classify_state_v21(f)
    out.append(('R1', '高Migration+低Trend 不得判 STRONG_TREND/ACCELERATION',
                s not in ('STRONG_TREND', 'ACCELERATION'), f'合成特征 → state={s}'))

    # R2（PASS2）高综合 + 低确认 → 必须落到 OSCILLATION / HOT_BUT_UNCONFIRMED 出口
    f = _synth(composite=75.0, confirmation=55.0, trend=50.0, emotion=62.0, breadth=45.0,
               migration=8.0, leadership=70.0, persistence=45.0, up_ratio=60.0)
    s = ts2.classify_state_v21(f)
    q = ts2._v21_quadrant({**f, 'state': s})
    out.append(('R2', '高强度+低确认 必须有 OSCILLATION/HOT_BUT_UNCONFIRMED 出口',
                s not in ('STRONG_TREND', 'ACCELERATION') and (s == 'OSCILLATION'
                                                               or q == 'HOT_BUT_UNCONFIRMED'),
                f'合成特征 → state={s} quadrant={q}'))

    # R3（PASS3）低趋势+高迁移+高情绪：广度改善 → EARLY_FLOW；广度恶化 → 不得判 EARLY_FLOW
    f_ok = _synth(trend=42.0, emotion=68.0, migration=30.0, breadth=38.0, d_breadth=8.0,
                  composite=55.0, confirmation=45.0)
    f_bad = _synth(trend=42.0, emotion=68.0, migration=30.0, breadth=38.0, d_breadth=-6.0,
                   composite=55.0, confirmation=45.0)
    s_ok, s_bad = ts2.classify_state_v21(f_ok), ts2.classify_state_v21(f_bad)
    out.append(('R3', '低Trend+高Migration+高Emotion 可识别 EARLY_FLOW（广度恶化不得误判）',
                s_ok == 'EARLY_FLOW' and s_bad != 'EARLY_FLOW',
                f'广度改善 → {s_ok}；广度恶化 → {s_bad}'))

    # R4（PASS4）ACCELERATION 必须四因子同时满足，涨停潮不能单独触发
    f_zt = _synth(trend=52.0, breadth=45.0, leadership=50.0, confirmation=45.0,
                  limitup=25, zt_count=25, zt_expansion=True, emotion=72.0, composite=70.0,
                  d_breadth=5.0, d_persistence=2.0, up_ratio=60.0)
    f_full = _synth(trend=72.0, breadth=68.0, leadership=70.0, confirmation=68.0,
                    persistence=60.0, pers_d3=70.0, pers_d5=64.0, d_trend=4.0, d_breadth=6.0,
                    zt_expansion=True, limitup=18, zt_count=18, emotion=75.0, composite=80.0,
                    prev_state='STARTING', up_ratio=60.0)
    s_zt, s_full = ts2.classify_state_v21(f_zt), ts2.classify_state_v21(f_full)
    out.append(('R4', 'ACCELERATION 需 趋势/广度/龙头/确认 同时满足（涨停潮不可单独触发）',
                s_zt != 'ACCELERATION' and s_full == 'ACCELERATION',
                f'仅涨停潮 → {s_zt}；四因子齐备 → {s_full}'))

    # R5（PASS5）分层语义：强度+广度双前 20% 才可 TRADEABLE；拥挤/单龙头/退潮一律不得进入确认层
    _base = dict(composite=85.0, migration=40.0, emotion=75.0, confirmation=64.0, trend=45.0,
                 breadth=55.0, leadership=62.0, persistence=42.0, up_ratio=60.0)
    p_main, _, _, _ = perm_of(_synth(**_base), 0.99, 0.99, 0.0)        # 强度+广度俱强 → 主线
    p_narrow, _, _, _ = perm_of(_synth(**_base), 0.99, 0.10, 0.0)      # 仅强度强、广度弱
    p_crowd, _, _, _ = perm_of(_synth(**_base), 0.99, 0.99, 0.95)      # 又高又放量（拥挤闸）
    p_leader, _, _, _ = perm_of(_synth(single_leader_risk='HIGH', **_base), 0.99, 0.99, 0.0)
    p_retreat = ts2._v21_permission_xs(
        {'state': 'RETREAT', 'single_leader_risk': 'LOW'}, {'comp': 0.99, 'brd': 0.99, 'crowd': 0.0})
    p_diverge = ts2._v21_permission_xs(
        {'state': 'DIVERGENCE', 'single_leader_risk': 'LOW'}, {'comp': 0.99, 'brd': 0.99, 'crowd': 0.0})
    out.append(('R5', 'TradePermission 不得由 Composite 单独决定（拥挤/单龙头/退潮强制降级）',
                p_main == 'TRADEABLE' and p_narrow == 'CONDITIONAL' and p_crowd == 'WATCH'
                and p_leader == 'WATCH' and p_retreat == 'NO_TRADE' and p_diverge == 'WATCH',
                f'双强→{p_main}；仅强度→{p_narrow}；拥挤→{p_crowd}；单龙头→{p_leader}；'
                f'退潮→{p_retreat}；背离→{p_diverge}'))

    # R6（PASS6）ChaseRisk≥75 时，即使许可=TRADEABLE 也只能 PULLBACK_ONLY
    f = _synth(emotion=90.0, ret_1=8.0, ma20_b=15.0, pos_in_20=1.0, up_ratio=60.0,
               n_stocks=50, zt_count=10, limitup=10)
    cr, _d = ts2.calc_chase_risk_v21(f)
    _p, bm, _a, _av = ts2._v21_action({**f, 'trade_permission': 'TRADEABLE',
                                       'chase_risk': cr})
    out.append(('R6', 'ChaseRisk≥75 → BUY_MODE 必须 PULLBACK_ONLY（禁止 MARKET_BUY）',
                cr >= 75.0 and bm == 'PULLBACK_ONLY',
                f'合成特征 ChaseRisk={cr} → BUY_MODE={bm}'))

    return out


def acceptance(rows):
    """PASS1~PASS8 验收（逐条验证规格硬约束，而非“代码跑通”）

    PASS1~PASS6 = 样本级统计（真实分布）AND 规则级单测（合成边界），两者同时成立才算 PASS，
    避免「样本里恰好没有反例」造成的名义通过。
    """
    out = []
    rules = {r[0]: (r[2], r[3]) for r in rule_checks()}

    NAMES = {
        'PASS1': '高Migration+低Trend 不得直接判强趋势/加速',
        'PASS2': '高强度+低确认 → OSCILLATION / HOT_BUT_UNCONFIRMED',
        'PASS3': '低Trend+高Migration+高Emotion 可识别 EARLY_FLOW',
        'PASS4': 'ACCELERATION 需 趋势/广度/龙头/确认 同时满足',
        'PASS5': 'TradePermission 不得由 Composite 单独决定',
        'PASS6': 'ChaseRisk≥75 → 只可回踩买（PULLBACK_ONLY）',
        'PASS7': '状态连续性：WEAK→…→RETREAT 链条可识别且方向自洽',
        'PASS8': 'V2 原始输出保持不变（A/B 同源）',
    }

    def add(pid, ok, detail):
        out.append((pid, NAMES[pid], 'PASS' if ok else 'FAIL', detail))

    def merge(pid, rid, data_ok, data_detail):
        r_ok, r_detail = rules.get(rid, (False, '规则级单测缺失'))
        add(pid, data_ok and r_ok, f"样本级：{data_detail} ｜ 规则级：{r_detail}")

    # PASS1 高 Migration + 低 Trend 不得自动变成强趋势
    v1 = [r for r in rows if float(r['migration']) >= 25 and float(r['trend']) < 50
          and r['state'] in ('STRONG_TREND', 'ACCELERATION')]
    merge('PASS1', 'R1', not v1,
          f"违例 {len(v1)} 条" + (f"（首个 {v1[0]['theme']}@{v1[0]['trade_date']}）" if v1 else ""))

    # PASS2 高 Composite + 低 Confirmation 必须有 OSCILLATION / HOT_BUT_UNCONFIRMED 出口
    hi_lo = [r for r in rows if float(r['composite']) >= 60 and float(r['confirmation']) < 60]
    bad2 = [r for r in hi_lo if r['state'] in ('STRONG_TREND', 'ACCELERATION')]
    osc = sum(1 for r in hi_lo if r['state'] == 'OSCILLATION')
    hot = sum(1 for r in hi_lo if r['quadrant'] == 'HOT_BUT_UNCONFIRMED')
    merge('PASS2', 'R2', (not bad2) and (osc + hot > 0),
          f"样本 {len(hi_lo)}：OSCILLATION {osc} / HOT_BUT_UNCONFIRMED {hot} / 误判为强趋势 {len(bad2)}")

    # PASS3 低 Trend + 高 Migration + 高 Emotion 必须能识别 EARLY_FLOW
    ef = [r for r in rows if r['state'] == 'EARLY_FLOW']
    ef_c = [r for r in ef if float(r['trend']) < 50 and float(r['migration']) >= 20
            and float(r['emotion']) >= 60]
    merge('PASS3', 'R3', len(ef_c) >= 5,
          f"EARLY_FLOW {len(ef)} 条，其中同时满足低趋势+高迁移+高情绪 {len(ef_c)} 条"
          f"（门槛：≥5 条方具统计意义）")

    # PASS4 ACCELERATION 必须多因子同时确认，不能只依赖涨停数
    acc = [r for r in rows if r['state'] == 'ACCELERATION']
    bad4 = [r for r in acc if not (float(r['trend']) >= 70 and float(r['breadth']) >= 65
                                   and float(r['leadership']) >= 65 and float(r['confirmation']) >= 65)]
    low_zt = sum(1 for r in acc if int(r['limitup'] or 0) < 5)
    merge('PASS4', 'R4', not bad4,
          f"ACCELERATION {len(acc)} 条，缺项违例 {len(bad4)} 条，其中涨停<5家 {low_zt} 条（非涨停数驱动）")

    # PASS5 分层语义（重构后：许可 = 横截面分位 + 风险闸，不再用 confirmation 当门槛）
    #   硬阻断：退潮/透支必须 NO_TRADE；不得越级：背离 / 单龙头依赖不得进入确认层
    bad5a = [r for r in rows if r['state'] in ('RETREAT', 'EXHAUSTION')
             and r['trade_permission'] != 'NO_TRADE']
    bad5b = [r for r in rows if (r['state'] == 'DIVERGENCE'
                                 or str(r['single_leader_risk']) == 'HIGH')
             and r['trade_permission'] in ('TRADEABLE', 'CONDITIONAL')]
    merge('PASS5', 'R5', not bad5a and not bad5b,
          f"退潮/透支未阻断 {len(bad5a)} 条；背离/单龙头依赖越级 {len(bad5b)} 条（样本 {len(rows)}）")

    # PASS6 ChaseRisk≥75 必须 PULLBACK_ONLY，绝不 MARKET_BUY
    risky = [r for r in rows if float(r['chase_risk']) >= 75]
    bad6 = [r for r in risky if r['buy_mode'] == 'MARKET_BUY']
    merge('PASS6', 'R6', not bad6,
          f"追高风险≥75 {len(risky)} 条，其中 MARKET_BUY {len(bad6)} 条"
          + (f"（已降为回踩 {sum(1 for r in risky if r['buy_mode'] == 'PULLBACK_ONLY')} 条）" if risky
             else "（本窗口无 ≥75 触发，改由规则级单测验证机制）"))

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
    add('PASS7', (not miss) and (not bad7),
        f"观测到 {len(seen)}/10 个状态，链条缺失 {miss or '无'}；转换样本 {len(trs)} 条，方向矛盾 {len(bad7)} 条")

    # PASS8 V2 兼容：V2 原始结果保持不变
    ok8, det8 = check_v2_intact()
    add('PASS8', ok8, det8)

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
    W("【规则级单测（合成边界特征 → 直接调用 V2.1 真实函数，与样本量无关）】")
    W(sep)
    for rid, name, ok, detail in rule_checks():
        W(f"  {rid}  [{'PASS' if ok else 'FAIL'}] {name}")
        W(f"        {detail}")
    W("")

    W(sep)
    W("【PASS/FAIL 验收表（规格第二十节；PASS1~6 = 样本级 AND 规则级）】")
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
