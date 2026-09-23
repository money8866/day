# -*- coding: utf-8 -*-
"""临时脚本（第十一轮）：验证「生产端原生字段」能否复现已通过检验的拥挤度因子

背景
  【J】段已证明（_v21_wave2_scan.txt）：主题指数口径的
    拥挤度 = pos20 分位 + shrink5_20 分位（越接近前高且越放量）
  在未来跑输，5 个 regime 段中 4 段为负、3 段显著 —— 是唯一跨期方向一致的维度。

  但那两个因子是**主题合成指数**口径，生产管线里不存在。生产端可得的是
  个股字段的主题均值：
    pos20_prod = mean(个股 pos_in_20) × 100
                 pos_in_20 = (close - min(close,20)) / (max(close,20) - min(close,20))
    shr_prod   = mean(个股 vol_ratio)
                 vol_ratio = mean(vol[-5:]) / mean(vol[-25:-5])

  口径差异（必须验证，不能假设等价）：
    · 我的因子：主题等权指数（先平均收益再算序列）→ close 口径、主题层
    · 生产字段：先算个股序列指标再平均       → close 口径、个股层
    两者方向可能一致也可能发散 —— 本脚本按【J】段同一协议复检。

数据源：d:\\mystock\\cache_daily\\stock_data.db 表 daily_cache（20210104~20260922）
        d:\\mystock\\cache_daily\\theme_stock_map_v2_20260922.json（32 主题 / 4096 股）
"""
import os
import sys
import sqlite3
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt
import _v21_wave2_build as wb

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v21_proxy_check.txt')
L = []
W = L.append
START = '20250508'          # 与第六轮样本窗口对齐
SEGS = (('长窗口', '20250508', '20260922'), ('2025H1', '20250508', '20250630'),
        ('2025H2', '20250701', '20251231'), ('2026H1', '20260101', '20260630'),
        ('2026H2', '20260701', '20260922'))


def main():
    themes = wb.load_map()
    allcodes = {c for v in themes.values() for c in v}
    conn = sqlite3.connect(wb.KDB)
    all_dates = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>=? ORDER BY trade_date",
        ('20241201',))]
    conn.close()
    W(f"主题 {len(themes)} 个 / 个股 {len(allcodes)} 只；交易日 {len(all_dates)} 日 "
      f"（{all_dates[0]} ~ {all_dates[-1]}）")
    bars = wb.load_bars(allcodes, all_dates[0], all_dates[-1])
    ts = wb.build_theme_series(themes, bars, all_dates)

    th_of = {c: th for th, codes in themes.items() for c in codes}

    # ── 逐股重建 close 与量能，算生产同式指标后按主题聚合 ──
    agg_pos, agg_shr = defaultdict(list), defaultdict(list)
    for c, b in bars.items():
        th = th_of.get(c)
        if not th:
            continue
        ds = sorted(b)
        cl, vl = [], []
        eq = 1.0
        for d in ds:
            eq *= (1.0 + b[d][0] / 100.0)
            cl.append(eq)
            vl.append(b[d][1])
        for i, d in enumerate(ds):
            if i >= 20:
                w = cl[i - 19:i + 1]
                hi, lo = max(w), min(w)
                agg_pos[(th, d)].append((cl[i] - lo) / (hi - lo) * 100.0 if hi > lo else 50.0)
            if i >= 25:
                v5 = sum(vl[i - 4:i + 1]) / 5.0
                vb = sum(vl[i - 24:i - 4]) / 20.0
                if vb > 0:
                    agg_shr[(th, d)].append(v5 / vb)

    P = {k: sum(v) / len(v) for k, v in agg_pos.items() if v}
    S = {k: sum(v) / len(v) for k, v in agg_shr.items() if v}
    W(f"pos20_prod 覆盖 {len(P)} 个主题-日；shr_prod 覆盖 {len(S)} 个主题-日")
    W(f"pos20_prod 均值 {bt._mean(list(P.values())):.1f}（中位 {bt._median(list(P.values())):.1f}）")
    W(f"shr_prod   均值 {bt._mean(list(S.values())):.3f}（中位 {bt._median(list(S.values())):.3f}）")

    # ── 前瞻收益（主题指数）+ 同日横截面去均值 ──
    seq = {th: sorted(ts[th][0]) for th in ts}
    posmap = {th: {d: i for i, d in enumerate(seq[th])} for th in ts}

    def fwd(th, d, k):
        idx = ts[th][0]
        i = posmap[th].get(d)
        if i is None or i + k >= len(seq[th]):
            return None
        return (idx[seq[th][i + k]] / idx[d] - 1.0) * 100.0

    rows = []
    for (th, d), pv in P.items():
        if d < START:
            continue
        r = {'theme': th, 'trade_date': d, 'pos20_prod': pv, 'shr_prod': S.get((th, d))}
        for k in (1, 5, 10):
            r[f'fwd_{k}'] = fwd(th, d, k)
        rows.append(r)

    bd = defaultdict(list)
    for r in rows:
        bd[r['trade_date']].append(r)
    for d, rs in bd.items():
        for k in (1, 5, 10):
            vs = [r[f'fwd_{k}'] for r in rs if r.get(f'fwd_{k}') is not None]
            if len(vs) >= 5:
                m = sum(vs) / len(vs)
                for r in rs:
                    if r.get(f'fwd_{k}') is not None:
                        r[f'ex_{k}'] = r[f'fwd_{k}'] - m
    W(f"有效样本 {len(rows)} 条 / {len(bd)} 日（{min(bd)} ~ {max(bd)}）")

    def quintile(score_fn, seg_rows):
        """按 score_fn 逐日五分档，返回 {q: [rows]}"""
        agg = defaultdict(list)
        bd2 = defaultdict(list)
        for r in seg_rows:
            if score_fn(r) is not None:
                bd2[r['trade_date']].append(r)
        for d, rs in bd2.items():
            if len(rs) < 5:
                continue
            rs = sorted(rs, key=score_fn)
            for pos, r in enumerate(rs):
                agg[min(4, int(pos * 5 / len(rs)))].append(r)
        return agg

    def q5q1(score_fn, seg_rows, k):
        agg = quintile(score_fn, seg_rows)
        a = [r[f'ex_{k}'] for r in agg.get(4, []) if r.get(f'ex_{k}') is not None]
        b = [r[f'ex_{k}'] for r in agg.get(0, []) if r.get(f'ex_{k}') is not None]
        if len(a) < 10 or len(b) < 10:
            return None
        m = bt._mean(a) - bt._mean(b)
        t, p = bt.welch_t(a, b)
        return m, t, p, len(a) + len(b)

    def crowd(seg_rows):
        """组合拥挤度 = pos20_prod 分位 + shr_prod 分位（逐日秩和）"""
        sc = {}
        bd2 = defaultdict(list)
        for r in seg_rows:
            if r.get('pos20_prod') is not None and r.get('shr_prod') is not None:
                bd2[r['trade_date']].append(r)
        for d, rs in bd2.items():
            if len(rs) < 5:
                continue
            r1 = {id(r): i for i, r in enumerate(sorted(rs, key=lambda x: x['pos20_prod']))}
            r2 = {id(r): i for i, r in enumerate(sorted(rs, key=lambda x: x['shr_prod']))}
            for r in rs:
                sc[id(r)] = r1[id(r)] + r2[id(r)]
        return sc

    W("\n" + "=" * 104)
    W("【1】单因子 / 组合 拥挤度 Q5−Q1（正=拥挤有效，负=拥挤回避有效）")
    W("=" * 104)

    for tag, a, b in SEGS:
        sub = [r for r in rows if a <= r['trade_date'] <= b]
        if not sub:
            continue
        sc = crowd(sub)
        sub = [r for r in sub if id(r) in sc]
        W(f"\n  ── {tag}（n={len(sub)}）──")
        W(f"      {'因子':<16}{'Q5n':>6}{'Q1n':>6}{'T+5 差':>11}{'p':>9}{'T+10 差':>11}{'p':>9}")
        for nm, fn in (('pos20_prod', lambda r: r.get('pos20_prod')),
                       ('shr_prod', lambda r: r.get('shr_prod')),
                       ('组合拥挤度', lambda r: sc.get(id(r)))):
            c5 = q5q1(fn, sub, 5)
            c10 = q5q1(fn, sub, 10)
            if not c5:
                W(f"      {nm:<16}  样本不足")
                continue
            n5 = sum(1 for r in sub if fn(r) is not None)
            W(f"      {nm:<16}{n5:>6}{n5:>6}{c5[0]:>+11.3f}{bt._fmt(c5[2], 4):>9}"
              f"{(c10[0] if c10 else float('nan')):>+11.3f}"
              f"{(bt._fmt(c10[2], 4) if c10 else '—'):>9}"
              f"{'  ★' if (c5[2] == c5[2] and c5[2] < 0.05) else ''}")

    W("\n" + "=" * 104)
    W("【2】组合拥挤度五分档明细（T+5 超额）—— 检查是否单调")
    W("=" * 104)
    for tag, a, b in SEGS:
        sub = [r for r in rows if a <= r['trade_date'] <= b]
        sc = crowd(sub)
        sub = [r for r in sub if id(r) in sc]
        if not sub:
            continue
        agg = quintile(lambda r: sc.get(id(r)), sub)
        cells = []
        for q in range(5):
            vs = [r['ex_5'] for r in agg.get(q, []) if r.get('ex_5') is not None]
            cells.append(f"{bt._mean(vs):+.2f}" if len(vs) >= 10 else "—")
        W(f"  {tag:<10}  Q1 {cells[0]:>7}  Q2 {cells[1]:>7}  Q3 {cells[2]:>7}  "
          f"Q4 {cells[3]:>7}  Q5 {cells[4]:>7}")

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
