# -*- coding: utf-8 -*-
"""临时脚本（第四轮）：诊断「层序反向」的真因

第三轮（_v21_layer_scan3.txt）发现：剥离市场 beta 后层序依旧反向，
TRADEABLE T+5 超额 -1.73% / 胜率 31.6%，NO_TRADE +0.23% / 胜率 51.6%。

但在下结论前必须排除两种「伪反向」：
  A. **主题固定效应**：若 TRADEABLE 长期集中在少数几个主题（半导体/AI…），
     而这些主题恰好在这 62 日系统性回调，则反向来自「主题 beta」而非「分层失效」。
     → 检验：对每条记录，减去**其所属主题**在本窗口的平均前瞻收益（within-theme demean），
       再看层序是否仍反向。
  B. **时间不稳定**：62 日可能只是单边窗口，前半/后半符号不一致即为噪音。
     → 检验：把 62 日按时间对切，分别看层序。

另外做 C：层序的「时间独立样本数」提醒 —— 62 日横截面彼此高度相关，
   有效独立样本远小于 1984，任何 p 值都存在横截面相关性低估问题（块状相关）。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v21_layer_scan4.txt')
L = []
W = L.append
DIMS = ('confirmation', 'breadth', 'leadership', 'persistence')
CALIB_END = '20260918'
KS = (1, 3, 5, 10)
LAYERS = ('TRADEABLE', 'CONDITIONAL', 'WATCH', 'NO_TRADE')


def pct(xs, q):
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def ds(rs, k, field='ex'):
    vals = [r[f'{field}_{k}'] for r in rs if r.get(f'{field}_{k}') is not None]
    return (len(vals), bt._mean(vals), bt._median(vals), bt._winrate(vals), vals)


def line(tag, rs, k, field='ex'):
    n, m, md, wr, _ = ds(rs, k, field)
    if not n:
        return
    W(f"    {tag:<13}n={n:<5} 超额{m:>7.2f}%  中位{md:>7.2f}%  胜率{wr:>5.1f}%")


def main():
    rows, dates = bt.load_rows(None)
    rows = bt.attach_forward(rows, dates)
    calib = [r for r in rows if r['trade_date'] <= CALIB_END]
    W(f"全样本 {len(rows)} 条 / {len(dates)} 日；标定集 {len(calib)} 条")

    # 同日横截面去均值
    for k in KS:
        bd = defaultdict(list)
        for r in rows:
            bd[r['trade_date']].append(r)
        for d, rs in bd.items():
            vs = [r[f'fwd_{k}'] for r in rs if r.get(f'fwd_{k}') is not None]
            m = sum(vs) / len(vs) if vs else 0.0
            for r in rs:
                r[f'ex_{k}'] = (r[f'fwd_{k}'] - m) if r.get(f'fwd_{k}') is not None else None

    # within-theme 去均值（对 ex 再减主题均值）
    for k in KS:
        bt_ = defaultdict(list)
        for r in rows:
            bt_[r['theme']].append(r)
        for th, rs in bt_.items():
            vs = [r[f'ex_{k}'] for r in rs if r.get(f'ex_{k}') is not None]
            m = sum(vs) / len(vs) if vs else 0.0
            for r in rs:
                r[f'exw_{k}'] = (r[f'ex_{k}'] - m) if r.get(f'ex_{k}') is not None else None

    # 分层
    th = {d: {q: pct([float(r.get(d) or 0) for r in calib], q) for q in (30, 60, 80)} for d in DIMS}
    EXCL_HARD = ('RETREAT', 'EXHAUSTION')

    def floors_ok(r, q):
        return all(float(r.get(d) or 0) >= th[d][q] for d in DIMS)

    def lay(r):
        st = r.get('state')
        if st in EXCL_HARD:
            return 'NO_TRADE'
        slr = r.get('single_leader_risk') == 'HIGH'
        if (floors_ok(r, 80) and not slr and float(r.get('chase_risk') or 0) < 75
                and st in ('STRONG_TREND', 'ACCELERATION', 'STARTING')):
            return 'TRADEABLE'
        if floors_ok(r, 60) and not slr and st != 'DIVERGENCE':
            return 'CONDITIONAL'
        if floors_ok(r, 30) or r.get('quadrant') in ('HOT_BUT_UNCONFIRMED', 'EARLY_FLOW'):
            return 'WATCH'
        return 'NO_TRADE'

    for r in rows:
        r['layer'] = lay(r)

    # ── A. 主题固定效应：TRADEABLE 是否集中在少数主题 ──
    W("\n" + "=" * 100)
    W("【A】主题集中度：TRADEABLE / CONDITIONAL 到底落在哪些主题上")
    W("=" * 100)
    for lp in ('TRADEABLE', 'CONDITIONAL'):
        sel = [r for r in rows if r['layer'] == lp]
        cnt = defaultdict(int)
        for r in sel:
            cnt[r['theme']] += 1
        tot = len(sel)
        W(f"\n■ {lp}  n={tot}，分布在 {len(cnt)} 个主题上（全样本主题数 {len({r['theme'] for r in rows})}）")
        top = sorted(cnt.items(), key=lambda z: -z[1])[:8]
        W("   前 8 大主题：" + "、".join(f"{a}({b},{100.0*b/tot:.0f}%)" for a, b in top))
        head = sum(b for _, b in top)
        W(f"   前 8 主题占比 {100.0*head/tot:.1f}%  → {'高度集中' if head/tot > 0.6 else '较为分散'}")

    # ── B. within-theme 去均值后的层序 ──
    W("\n" + "=" * 100)
    W("【B】层序检验：三种口径对比（原始 / 剥大盘 / 再剥主题）")
    W("=" * 100)
    for k in (3, 5, 10):
        W(f"\n  ── T+{k} ──")
        W(f"    {'层':<13}{'原始均值':>11}{'剥大盘':>11}{'再剥主题':>11}")
        for lp in LAYERS:
            sel = [r for r in rows if r['layer'] == lp]
            n1, m1, _, _, _ = ds(sel, k, 'fwd')
            n2, m2, _, _, _ = ds(sel, k, 'ex')
            n3, m3, _, _, _ = ds(sel, k, 'exw')
            W(f"    {lp:<13}{m1:>11.2f}{m2:>11.2f}{m3:>11.2f}   (n={n3})")

    W("\n  ── B2. CONDITIONAL vs WATCH（再剥主题口径）──")
    cond = [r for r in rows if r['layer'] == 'CONDITIONAL']
    watch = [r for r in rows if r['layer'] == 'WATCH']
    for k in KS:
        _, ma, _, _, va = ds(cond, k, 'exw')
        _, mb, _, _, vb = ds(watch, k, 'exw')
        t, p = bt.welch_t(va, vb)
        W(f"    T+{k:<4} COND {ma:+.2f}%  vs  WATCH {mb:+.2f}%   差 {ma-mb:+.2f}pct  t={bt._fmt(t)}  p={bt._fmt(p,4)}")

    W("\n  ── B3. TRADEABLE vs 其余（再剥主题口径）──")
    tr = [r for r in rows if r['layer'] == 'TRADEABLE']
    rest = [r for r in rows if r['layer'] != 'TRADEABLE']
    for k in KS:
        _, ma, _, _, va = ds(tr, k, 'exw')
        _, mb, _, _, vb = ds(rest, k, 'exw')
        t, p = bt.welch_t(va, vb)
        W(f"    T+{k:<4} TRADE {ma:+.2f}%  vs  其余 {mb:+.2f}%   差 {ma-mb:+.2f}pct  t={bt._fmt(t)}  p={bt._fmt(p,4)}")

    # ── C. 时间对切稳定性 ──
    W("\n" + "=" * 100)
    W("【C】时间稳定性：62 日对切，层序符号是否一致")
    W("=" * 100)
    ds_sorted = sorted({r['trade_date'] for r in rows})
    mid = ds_sorted[len(ds_sorted) // 2]
    W(f"  前半：{ds_sorted[0]} ~ {mid}；后半：{ds_sorted[len(ds_sorted)//2+1]} ~ {ds_sorted[-1]}")
    for half, sel_dates in (('前半', set(ds_sorted[:len(ds_sorted)//2])),
                            ('后半', set(ds_sorted[len(ds_sorted)//2:]))):
        W(f"\n  ── {half} ──")
        sub = [r for r in rows if r['trade_date'] in sel_dates]
        W(f"    {'层':<13}{'n':>6}{'T+5剥大盘':>12}{'T+5再剥主题':>14}")
        for lp in LAYERS:
            sel = [r for r in sub if r['layer'] == lp]
            n2, m2, _, _, _ = ds(sel, 5, 'ex')
            n3, m3, _, _, _ = ds(sel, 5, 'exw')
            if n3:
                W(f"    {lp:<13}{n3:>6}{m2:>12.2f}{m3:>14.2f}")

    # ── D. 独立样本提醒：按日聚合的分层序 ──
    W("\n" + "=" * 100)
    W("【D】按日聚合的独立样本检验（消除横截面相关导致的 p 值低估）")
    W("=" * 100)
    by_d = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_d[r['trade_date']][r['layer']].append(r)
    for a, b in (('CONDITIONAL', 'WATCH'), ('CONDITIONAL', 'NO_TRADE')):
        diffs = []
        for d, g in by_d.items():
            va = [r['exw_5'] for r in g.get(a, []) if r.get('exw_5') is not None]
            vb = [r['exw_5'] for r in g.get(b, []) if r.get('exw_5') is not None]
            if len(va) >= 1 and len(vb) >= 3:
                diffs.append(sum(va) / len(va) - sum(vb) / len(vb))
        if len(diffs) >= 5:
            m = sum(diffs) / len(diffs)
            sd = bt._std(diffs)
            se = sd / (len(diffs) ** 0.5)
            t = m / se if se else float('nan')
            p = 2.0 * (1.0 - bt._norm_cdf(abs(t))) if se else float('nan')
            pos = sum(1 for x in diffs if x > 0)
            W(f"  {a} - {b}（按日配对，T+5 再剥主题）")
            W(f"    有效日数 {len(diffs)}  日均差 {m:+.3f}pct  标准差 {sd:.3f}  "
              f"t={bt._fmt(t)}  p={bt._fmt(p,4)}  差值>0 的天数 {pos}/{len(diffs)}")

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
