# -*- coding: utf-8 -*-
"""临时脚本（第二轮）：V2.1 三层许可 = 候选 / 确认 / 核心主线

背景（第一轮扫描结论）：
  - 四维与 State 的「确认度阶梯」在本窗口内与未来收益**反向**（详见 _v21_layer_scan.txt）；
    因此任何单调门槛都不可能在样本内跑出正超额 —— 门槛取值必须按**分位**定，不能按收益定。
  - 四维量纲不可比（leadership p50=56 vs persistence p50=25），故 raw min() 作为短板分效果差，
    改用「各维对自身历史分位」的对齐后短板。

本脚本做什么：
  1. 以 60 日回填（<=20260918）为**标定集**，算四维的 p30/p60/p80 绝对阈值；
  2. 用标定出的**固定绝对阈值**在全部样本上分层，输出各层样本量与 T+1/3/5/10 分布；
  3. 把 State 门与 SLR 门叠加，看最终三层（WATCH / CONDITIONAL / TRADEABLE）落位情况。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v21_layer_scan2.txt')
L = []
W = L.append
DIMS = ('confirmation', 'breadth', 'leadership', 'persistence')
CALIB_END = '20260918'


def pct(xs, q):
    if not xs:
        return float('nan')
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def main():
    rows, dates = bt.load_rows(None)
    rows = bt.attach_forward(rows, dates)
    calib = [r for r in rows if r['trade_date'] <= CALIB_END]
    W(f"全样本 {len(rows)} 条 / {len(dates)} 日；标定集 {len(calib)} 条（<= {CALIB_END}）")

    # ── 1. 标定：四维分位阈值 ──
    W("\n" + "=" * 92)
    W("【1】标定集四维分位 → 各层绝对阈值（按分位定，不看收益）")
    W("=" * 92)
    th = {}
    W(f"{'维度':<14}" + "".join(f"{'p%d' % q:>9}" for q in (30, 60, 80)))
    for d in DIMS:
        vs = [float(r.get(d) or 0) for r in calib]
        th[d] = {q: pct(vs, q) for q in (30, 60, 80)}
        W(f"{d:<14}" + "".join(f"{th[d][q]:>9.1f}" for q in (30, 60, 80)))

    # ── 2. 分层定义（固定绝对阈值 + 状态门 + 单龙头门）──
    EXCL_HARD = ('RETREAT', 'EXHAUSTION')

    def floors_ok(r, q):
        return all(float(r.get(d) or 0) >= th[d][q] for d in DIMS)

    def lay(r):
        st = r.get('state')
        if st in EXCL_HARD:
            return 'NO_TRADE'
        slr_high = r.get('single_leader_risk') == 'HIGH'
        # L3 核心主线：四维≥p80 + 状态必须是趋势确认态 + 非单龙头依赖
        if (floors_ok(r, 80) and not slr_high and float(r.get('chase_risk') or 0) < 75
                and st in ('STRONG_TREND', 'ACCELERATION', 'STARTING')):
            return 'TRADEABLE'
        # L2 条件交易：四维≥p60 + 非背离 + 非单龙头依赖
        if floors_ok(r, 60) and not slr_high and st != 'DIVERGENCE':
            return 'CONDITIONAL'
        # L1 入池观察：四维≥p30 或 象限属于 HOT/EARLY_FLOW
        if floors_ok(r, 30) or r.get('quadrant') in ('HOT_BUT_UNCONFIRMED', 'EARLY_FLOW'):
            return 'WATCH'
        return 'NO_TRADE'

    for r in rows:
        r['layer'] = lay(r)

    W("\n" + "=" * 92)
    W("【2】三层落位（全样本 62 日）")
    W("=" * 92)
    for lp in ('TRADEABLE', 'CONDITIONAL', 'WATCH', 'NO_TRADE'):
        sel = [r for r in rows if r['layer'] == lp]
        W(f"\n■ {lp}  n={len(sel)}  ({100.0 * len(sel) / len(rows):.1f}%)")
        for k in (1, 3, 5, 10):
            s = bt.describe(sel, k)
            if s['n']:
                W(f"    T+{k:<3}" + bt.fmt_stat(s))
        if sel:
            d = {}
            for r in sel:
                d[r['state']] = d.get(r['state'], 0) + 1
            W("    状态构成：" + "、".join(f"{a}{b}" for a, b in sorted(d.items(), key=lambda z: -z[1])))

    # ── 3. 关键检验：CONDITIONAL vs WATCH（T+3/5/10） ──
    W("\n" + "=" * 92)
    W("【3】关键检验：CONDITIONAL vs WATCH（T+3/5/10 分布 + Welch t）")
    W("=" * 92)
    cond = [r for r in rows if r['layer'] == 'CONDITIONAL']
    watch = [r for r in rows if r['layer'] == 'WATCH']
    for k in (1, 3, 5, 10):
        sa, sb = bt.describe(cond, k), bt.describe(watch, k)
        t, p = bt.welch_t(sa['vals'], sb['vals'])
        W(f"  T+{k:<3} CONDITIONAL  " + bt.fmt_stat(sa))
        W(f"       WATCH        " + bt.fmt_stat(sb))
        W(f"       差异 {sa['mean'] - sb['mean']:+.2f}pct   Welch t={bt._fmt(t)}  p={bt._fmt(p, 4)}")
    W(f"\n  CONDITIONAL n={len(cond)} / WATCH n={len(watch)} "
      f"→ 样本量{'充足' if len(cond) >= 100 else '偏少'}（100 为最低统计功效门槛）")

    # ── 4. 逐层对照：检验「层越高收益越好」是否成立 ──
    W("\n" + "=" * 92)
    W("【4】层级序检验：是否 层越高 → 未来收益越好（若不成立，则分层只是风控分层）")
    W("=" * 92)
    order = ['NO_TRADE', 'WATCH', 'CONDITIONAL', 'TRADEABLE']
    for k in (3, 5, 10):
        W(f"\n  T+{k}:")
        for lp in order:
            s = bt.describe([r for r in rows if r['layer'] == lp], k)
            if s['n']:
                W(f"    {lp:<12}" + bt.fmt_stat(s))
    # 分层序 Spearman 近似（用层序秩 → 收益的秩相关）
    rank = {lp: i for i, lp in enumerate(order)}
    xs = [rank[r['layer']] for r in rows if r.get('fwd_5') is not None]
    ys = [r['fwd_5'] for r in rows if r.get('fwd_5') is not None]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs) ** 0.5
    vy = sum((b - my) ** 2 for b in ys) ** 0.5
    rs = cov / (vx * vy) if vx * vy else float('nan')
    W(f"\n  层序秩 与 T+5 的相关系数 r = {rs:+.4f}（>0 才代表层越高收益越好）")

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
