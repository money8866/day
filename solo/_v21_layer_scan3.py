# -*- coding: utf-8 -*-
"""临时脚本（第三轮）：把「层间差异」从市场 beta 里剥离出来

第二轮（_v21_layer_scan2.txt）结论：
  - 样本量问题已解决：CONDITIONAL n=302 / WATCH n=511，统计功效充足；
  - 但绝对收益上各层全线为负、且层序与收益反向（r = -0.0671），
    NO_TRADE（多为 RETREAT）反而最好 → 高度疑似「市场整体下跌 + 超跌反弹」的窗口特征，
    而不是分层无效。

因此本轮做三件事：
  1. 刻画窗口的市场基调（横截面均值收益、涨跌家数），确认是不是普跌窗口；
  2. 把每一条的前瞻收益做**同日横截面去均值**（相对全主题等权的超额），
     再检验 CONDITIONAL 是否显著优于 WATCH —— 这才是「选股 alpha」的正确口径；
  3. 用 confirmation 十档做同样的去均值单调性检验，看是否有任何一维具备横截面区分力。

注意：去均值会同时抹掉「主题整体 vs 大盘」，剩下的是主题之间的相对强弱，这正是分层要解决的问题。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v21_layer_scan3.txt')
L = []
W = L.append
DIMS = ('confirmation', 'breadth', 'leadership', 'persistence')
CALIB_END = '20260918'
KS = (1, 3, 5, 10)


def pct(xs, q):
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def demean(rows, keys):
    """对每个 (trade_date) 做横截面去均值，写入 ex_{k}"""
    by_date = defaultdict(list)
    for r in rows:
        by_date[r['trade_date']].append(r)
    for d, rs in by_date.items():
        for k in keys:
            vs = [r[f'fwd_{k}'] for r in rs if r.get(f'fwd_{k}') is not None]
            if len(vs) < 3:
                for r in rs:
                    r[f'ex_{k}'] = None
                continue
            m = sum(vs) / len(vs)
            for r in rs:
                r[f'ex_{k}'] = (r[f'fwd_{k}'] - m) if r.get(f'fwd_{k}') is not None else None
    return rows


def describe_ex(rs, k):
    vals = [r[f'ex_{k}'] for r in rs if r.get(f'ex_{k}') is not None]
    return {
        'tag': '', 'n': len(vals),
        'mean': bt._mean(vals), 'median': bt._median(vals),
        'win': bt._winrate(vals), 'mdd': float('nan'),
        'worst': min(vals) if vals else float('nan'),
        'best': max(vals) if vals else float('nan'),
        'vals': vals,
    }


def fmt_ex(s):
    return f"n={s['n']:<5} 超额均值{s['mean']:>7.2f}%  中位{s['median']:>7.2f}%  胜率{s['win']:>5.1f}%"


def main():
    rows, dates = bt.load_rows(None)
    rows = bt.attach_forward(rows, dates)
    calib = [r for r in rows if r['trade_date'] <= CALIB_END]
    W(f"全样本 {len(rows)} 条 / {len(dates)} 日；标定集 {len(calib)} 条（<= {CALIB_END}）")

    # ── 1. 窗口市场基调 ──
    W("\n" + "=" * 96)
    W("【1】窗口市场基调（判断是否普跌/反弹窗口）")
    W("=" * 96)
    by_date = defaultdict(list)
    for r in rows:
        by_date[r['trade_date']].append(r)
    cum = 1.0
    up_days = 0
    for d in sorted(by_date):
        vs = [float(r.get('ret_1') or 0) for r in by_date[d]]
        m = sum(vs) / len(vs)
        if m > 0:
            up_days += 1
        cum *= (1 + m / 100.0)
    W(f"  全主题等权组合区间累计收益 = {(cum - 1) * 100:+.2f}%（{len(by_date)} 日）")
    W(f"  日均涨的主题占比 = 横截面均值 > 0 的天数 {up_days}/{len(by_date)}")
    mk = [float(r.get('mkt_ret_1') or 0) for r in rows if r.get('mkt_ret_1') is not None]
    if mk:
        W(f"  mkt_ret_1 样本均值 = {bt._mean(mk):+.3f}%")

    # ── 2. 去均值 ──
    demean(rows, KS)

    # ── 3. 三层定义（与第二轮完全一致，仅换成超额口径）──
    th = {d: {q: pct([float(r.get(d) or 0) for r in calib], q) for q in (30, 60, 80)} for d in DIMS}
    EXCL_HARD = ('RETREAT', 'EXHAUSTION')

    def floors_ok(r, q):
        return all(float(r.get(d) or 0) >= th[d][q] for d in DIMS)

    def lay(r):
        st = r.get('state')
        if st in EXCL_HARD:
            return 'NO_TRADE'
        slr_high = r.get('single_leader_risk') == 'HIGH'
        if (floors_ok(r, 80) and not slr_high and float(r.get('chase_risk') or 0) < 75
                and st in ('STRONG_TREND', 'ACCELERATION', 'STARTING')):
            return 'TRADEABLE'
        if floors_ok(r, 60) and not slr_high and st != 'DIVERGENCE':
            return 'CONDITIONAL'
        if floors_ok(r, 30) or r.get('quadrant') in ('HOT_BUT_UNCONFIRMED', 'EARLY_FLOW'):
            return 'WATCH'
        return 'NO_TRADE'

    for r in rows:
        r['layer'] = lay(r)

    W("\n" + "=" * 96)
    W("【2】各层「同日横截面超额」分布（剔除市场 beta 后的真实区分力）")
    W("=" * 96)
    for lp in ('TRADEABLE', 'CONDITIONAL', 'WATCH', 'NO_TRADE'):
        sel = [r for r in rows if r['layer'] == lp]
        W(f"\n■ {lp}  n={len(sel)}")
        for k in KS:
            s = describe_ex(sel, k)
            if s['n']:
                W(f"    T+{k:<3}" + fmt_ex(s))

    # ── 4. 关键检验（超额口径）──
    W("\n" + "=" * 96)
    W("【3】关键检验（超额口径）：CONDITIONAL vs WATCH")
    W("=" * 96)
    cond = [r for r in rows if r['layer'] == 'CONDITIONAL']
    watch = [r for r in rows if r['layer'] == 'WATCH']
    for k in KS:
        sa, sb = describe_ex(cond, k), describe_ex(watch, k)
        t, p = bt.welch_t(sa['vals'], sb['vals'])
        W(f"  T+{k:<3} CONDITIONAL  " + fmt_ex(sa))
        W(f"       WATCH        " + fmt_ex(sb))
        W(f"       超额差 {sa['mean'] - sb['mean']:+.2f}pct   Welch t={bt._fmt(t)}  p={bt._fmt(p, 4)}"
          f"   {'★显著' if (p == p and p < 0.05) else ''}")

    # ── 5. 各维十分档的超额单调性 ──
    W("\n" + "=" * 96)
    W("【4】各维十分档 → T+5 超额（检验是否真的没有任何一维具备横截面区分力）")
    W("=" * 96)
    for key in ('confirmation', 'breadth', 'leadership', 'persistence', 'trend', 'emotion', 'migration'):
        rs = sorted([r for r in rows if float(r.get(key) or 0) > 0], key=lambda r: float(r.get(key) or 0))
        n = len(rs)
        W(f"\n── {key} (有效 n={n}) ──")
        W(f"{'档':<5}{'下界':>8}{'n':>6}{'T+5超额':>11}{'胜率':>8}")
        for i in range(10):
            seg = rs[int(i * n / 10):int((i + 1) * n / 10)]
            s = describe_ex(seg, 5)
            if s['n']:
                W(f"D{i:<4}{float(seg[0].get(key) or 0):>8.1f}{s['n']:>6}{s['mean']:>11.2f}{s['win']:>8.1f}")

    # ── 6. State 阶梯的超额序 ──
    W("\n" + "=" * 96)
    W("【5】State → T+5 超额（状态阶梯在超额口径下是否恢复正向）")
    W("=" * 96)
    for st in bt.LADDER:
        sel = [r for r in rows if r.get('state') == st]
        s = describe_ex(sel, 5)
        if s['n']:
            W(f"  {st:<13}" + fmt_ex(s))

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
