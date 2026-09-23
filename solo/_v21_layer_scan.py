# -*- coding: utf-8 -*-
"""临时脚本：V2.1 分层许可（Candidate / Conditional / Tradeable）门槛扫描

目的：在不重跑 60 日管线的前提下，用已落库的 V2.1 特征 + 已实现的前瞻收益，
      回答两件事：
  1) 是否存在一条「单调的确认度阶梯」，其上层样本的未来 T+1/3/5/10 收益
     优于下层（若连单调性都不存在，则任何分层门槛都只是分层，不产生 alpha）；
  2) 新 CONDITIONAL 门槛能产生多少样本、其收益分布是否优于 WATCH。

用法：python _v21_layer_scan.py   → 结果同时打印并写入 _v21_layer_scan.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v21_layer_scan.txt')
L = []
W = L.append


def pct(xs, q):
    if not xs:
        return float('nan')
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def decile_table(rows, key, k=5, title=''):
    """按 key 分十档看 fwd_k 均值/胜率，检验单调性"""
    vals = sorted(float(r.get(key) or 0) for r in rows)
    if not vals:
        return
    qs = [pct(vals, q) for q in range(0, 101, 10)]
    W(f"\n── 单调性检验：{title or key} → T+{k} ──")
    W(f"{'档位':<8}{'阈值':>7}{'n':>6}{'T+%d均值' % k:>11}{'中位':>9}{'胜率':>8}{'回撤':>9}")
    for i in range(10):
        lo, hi = qs[i], qs[i + 1]
        sel = [r for r in rows
               if float(r.get(key) or 0) > lo and (float(r.get(key) or 0) <= hi if i < 9 else True)]
        s = bt.describe(sel, k)
        if s['n'] == 0:
            continue
        W(f"D{i:<7}{lo:>7.1f}{s['n']:>6}{s['mean']:>11.2f}{s['median']:>9.2f}"
          f"{s['win']:>8.1f}{s['mdd']:>9.2f}")


def bucket_vs(rows, sel_fn, base_fn, k, tag):
    a = [r for r in rows if sel_fn(r)]
    b = [r for r in rows if base_fn(r)]
    sa, sb = bt.describe(a, k, tag), bt.describe(b, k, 'base')
    t, p = bt.welch_t(sa['vals'], sb['vals'])
    W(f"  T+{k:<3} {tag:<26} " + bt.fmt_stat(sa))
    W(f"       {'(对照)':<26} " + bt.fmt_stat(sb))
    W(f"       差异 {sa['mean'] - sb['mean']:+.2f}pct   Welch t={bt._fmt(t)}  p={bt._fmt(p, 4)}")
    return sa, sb, t, p


def main():
    rows, dates = bt.load_rows(None)
    rows = bt.attach_forward(rows, dates)
    W(f"样本 {len(rows)} 条 / {len(dates)} 个交易日（{dates[0]} ~ {dates[-1]}）")

    # ── 0. 单变量分位 ──
    W("\n" + "=" * 88)
    W("【0】四维与短板的实测分位（用于按分位而非按收益挑门槛）")
    W("=" * 88)
    W(f"{'指标':<14}" + "".join(f"{'p%d' % q:>9}" for q in (10, 30, 50, 60, 70, 80, 90)) + f"{'max':>9}")
    for key in ('confirmation', 'breadth', 'leadership', 'persistence', 'trend', 'emotion', 'migration'):
        vs = [float(r.get(key) or 0) for r in rows]
        W(f"{key:<14}" + "".join(f"{pct(vs, q):>9.1f}" for q in (10, 30, 50, 60, 70, 80, 90))
          + f"{max(vs):>9.1f}")
    barrel = [min(float(r.get(k) or 0) for k in ('confirmation', 'breadth', 'leadership', 'persistence'))
              for r in rows]
    W(f"{'barrel(min四维)':<14}" + "".join(f"{pct(barrel, q):>9.1f}" for q in (10, 30, 50, 60, 70, 80, 90))
      + f"{max(barrel):>9.1f}")

    # ── 1. 单调性：各种候选「确认度阶梯」 ──
    for key in ('confirmation', 'breadth', 'leadership', 'persistence'):
        decile_table(rows, key, 5)
    rows2 = rows
    for r in rows2:
        r['barrel'] = min(float(r.get(k) or 0)
                          for k in ('confirmation', 'breadth', 'leadership', 'persistence'))
    decile_table(rows2, 'barrel', 5, 'barrel = min(Conf,Brd,Lead,Pers)')

    # 状态阶梯
    W("\n── 单调性检验：State（改善阶梯序） → T+5 ──")
    for st in bt.LADDER:
        sel = [r for r in rows2 if r.get('state') == st]
        s = bt.describe(sel, 5)
        if s['n']:
            W(f"  {st:<14}" + bt.fmt_stat(s))

    # ── 2. CONDITIONAL 门槛扫描（barrel 制） ──
    W("\n" + "=" * 88)
    W("【2】CONDITIONAL 门槛扫描：barrel >= cut 且 非退潮/透支 且 SLR≠HIGH")
    W("   对照组 = 已具备 WATCH 资格但不达该 cut 的样本（即当前 WATCH 层）")
    W("=" * 88)
    EXCL = ('RETREAT', 'EXHAUSTION')

    def watch_base(r, cut):
        return (r.get('state') not in EXCL
                and r.get('single_leader_risk') != 'HIGH'
                and min(float(r.get(k) or 0)
                        for k in ('confirmation', 'breadth', 'leadership', 'persistence')) < cut)

    for cut in (35, 40, 45, 50, 55, 60, 65, 70):
        W(f"\n■ cut = {cut}")
        for k in (3, 5, 10):
            bucket_vs(rows2,
                      lambda r, c=cut: (r.get('state') not in EXCL
                                        and r.get('single_leader_risk') != 'HIGH'
                                        and min(float(r.get(x) or 0) for x in
                                                ('confirmation', 'breadth', 'leadership', 'persistence')) >= c),
                      lambda r, c=cut: watch_base(r, c), k, f'barrel>={cut}')

    # ── 3. 对照：纯 confirmation 制 ──
    W("\n" + "=" * 88)
    W("【3】对照扫描：单用 confirmation >= cut（规格原始阈值 65 附近）")
    W("=" * 88)
    for cut in (50, 55, 60, 65, 70):
        W(f"\n■ confirmation cut = {cut}")
        for k in (3, 5, 10):
            bucket_vs(rows2,
                      lambda r, c=cut: (r.get('state') not in EXCL
                                        and r.get('single_leader_risk') != 'HIGH'
                                        and float(r.get('confirmation') or 0) >= c),
                      lambda r, c=cut: (r.get('state') not in EXCL
                                        and r.get('single_leader_risk') != 'HIGH'
                                        and float(r.get('confirmation') or 0) < c),
                      k, f'conf>={cut}')

    # ── 4. 现状基线（当前 DB 里的 permission） ──
    W("\n" + "=" * 88)
    W("【4】现状 permission 分层（作为对照基线）")
    W("=" * 88)
    for perm in ('NO_TRADE', 'WATCH', 'CONDITIONAL', 'TRADEABLE'):
        sel = [r for r in rows2 if r.get('trade_permission') == perm]
        for k in (1, 3, 5, 10):
            s = bt.describe(sel, k)
            if s['n']:
                W(f"  {perm:<12} T+{k:<3}" + bt.fmt_stat(s))

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
