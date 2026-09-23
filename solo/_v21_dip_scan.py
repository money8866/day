# -*- coding: utf-8 -*-
"""临时脚本（第五轮）：低吸 / 二波因子候选 —— 方向筛选

背景：第四轮证明 V2.1 四维（confirmation/breadth/leadership/persistence）在本窗口是
      **反转因子**（D9 在 6/7 维上最差），且 TRADEABLE 再剥主题后 T+5 -2.33%（p=0.017）显著为负。
      → 用户决策：重构为「低吸 / 二波」因子层。

原料：report_daily/theme_scores_v2_*.csv（43 天，143 列，含 t_* 主题趋势特征 / s_* 主题情绪特征）
      前瞻收益复用 theme_v21_daily.ret_1 的等权复利口径。

本脚本只做**单变量方向筛选**（十档 T+5 横截面超额是否单调），不合成、不调参：
  低吸/二波假设 = 中期趋势未破 + 短期超跌 + 缩量整理 + 位置偏低
"""
import os
import sys
import glob
import csv
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, '_v21_dip_scan.txt')
L = []
W = L.append
KS = (1, 3, 5, 10)

# 待筛选的候选因子（全部来自 V2 CSV，已有字段）
CAND = [
    ('t_avg_max_dd_10',    '成分股10日最大回撤均值(%) → 回调深度'),
    ('t_pos_in_20_score',  '20日位置分 → 位置高低'),
    ('t_rel_ret_10',       '10日相对收益(%) → 近期相对强弱'),
    ('t_avg_ret_3',        '3日涨幅均值(%) → 短期动能'),
    ('t_avg_ret_5',        '5日涨幅均值(%)'),
    ('t_avg_ret_10',       '10日涨幅均值(%)'),
    ('t_avg_ret_20',       '20日涨幅均值(%) → 前波涨幅'),
    ('t_pct_above_ma5',    '站上MA5的成分股占比(%)'),
    ('t_pct_above_ma10',   '站上MA10的成分股占比(%)'),
    ('t_pct_above_ma20',   '站上MA20的成分股占比(%)'),
    ('t_pct_above_ma60',   '站上MA60的成分股占比(%) → 中期趋势'),
    ('t_avg_slope_60',     '60日斜率均值 → 中期趋势方向'),
    ('t_avg_acc_3_5',      '3-5日加速度'),
    ('s_avg_vol_ratio',    '量比均值 → 放量/缩量'),
    ('s_avg_turnover',     '换手率均值(%)'),
    ('s_up_ratio',         '上涨家数占比(%)'),
    ('s_strong_ratio',     '强势股占比(%)'),
    ('s_median_pct',       '成分股涨幅中位数(%)'),
]


def num(x, d=float('nan')):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def load_v2_feats():
    feats = {}
    files = sorted(glob.glob(os.path.join(BASE, 'report_daily', 'theme_scores_v2_*.csv')))
    for p in files:
        d = os.path.basename(p)[-12:-4]
        with open(p, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                feats[(d, row['theme'])] = row
    return feats, files


def decile(rows, key, k=5, asc=True):
    rs = [r for r in rows if num(r.get(key)) == num(r.get(key))]
    if len(rs) < 50:
        return None
    rs.sort(key=lambda r: num(r.get(key)), reverse=not asc)
    n = len(rs)
    out = []
    for i in range(10):
        seg = rs[int(i * n / 10):int((i + 1) * n / 10)]
        vals = [r[f'ex_{k}'] for r in seg if r.get(f'ex_{k}') is not None]
        if len(vals) >= 5:
            out.append((i, num(seg[0].get(key)), len(vals), bt._mean(vals), bt._winrate(vals)))
    return out


def main():
    feats, files = load_v2_feats()
    W(f"V2 CSV 文件 {len(files)} 个：{os.path.basename(files[0])[-12:-4]} ~ {os.path.basename(files[-1])[-12:-4]}")

    rows, dates = bt.load_rows(None)
    rows = bt.attach_forward(rows, dates)
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

    # 关联 V2 特征
    hit = 0
    for r in rows:
        f = feats.get((r['trade_date'], r['theme']))
        r['_v2'] = f
        if f:
            hit += 1
    W(f"theme_v21_daily {len(rows)} 条；成功关联 V2 特征 {hit} 条（覆盖率 {100.0*hit/len(rows):.1f}%）")
    use = [r for r in rows if r.get('_v2')]
    # 展开特征列到行上
    keys = [c for c in CAND]
    for r in use:
        for kk, _ in CAND:
            r[kk] = num(r['_v2'].get(kk))
    days = sorted({r['trade_date'] for r in use})
    W(f"可用样本 {len(use)} 条 / {len(days)} 日（{days[0]} ~ {days[-1]}）")

    # ── 基准：整个可用样本的平均超额（作为 0 基准的参照）──
    W("\n" + "=" * 104)
    W("【1】单变量十档 → T+5 横截面超额（asc=True 表示第0档=因子最小）")
    W("=" * 104)
    for kk, desc in CAND:
        tab = decile(use, kk, 5)
        if not tab:
            W(f"\n── {kk}（样本不足）──")
            continue
        W(f"\n── {kk}  {desc} ──")
        W(f"{'档':<4}{'该档下界':>10}{'n':>6}{'T+5超额':>10}{'胜率':>8}   {'':<24}")
        prev = None
        mono = []
        for i, lo, n, m, wr in tab:
            if prev is not None:
                mono.append(1 if m > prev else -1)
            prev = m
            # 简易条形
            bar = ('+' if m > 0 else '-') * min(12, max(1, int(abs(m) * 6)))
            W(f"D{i:<3}{lo:>10.1f}{n:>6}{m:>10.2f}{wr:>8.1f}   {bar}")
        if mono:
            up = sum(1 for x in mono if x > 0)
            W(f"     单调性：{up}/{len(mono)} 段上升   "
              f"{'↑单调' if up == len(mono) else ('↓单调' if up == 0 else '非单调')}")

    # ── 2. 关键结构变量：MA5 vs MA10 与 MA60 趋势的组合 ──
    W("\n" + "=" * 104)
    W("【2】结构组合检验：中期趋势(MA60) × 短期均线(MA5 vs MA10) × 缩量")
    W("=" * 104)

    def grp(tag, sel, k=5):
        s = [r for r in use if sel(r)]
        vals = [r[f'ex_{k}'] for r in s if r.get(f'ex_{k}') is not None]
        if len(vals) < 20:
            W(f"  {tag:<42}n={len(vals):<5}（样本不足）")
            return
        t, p = bt.welch_t(vals, [r[f'ex_{k}'] for r in use if r.get(f'ex_{k}') is not None])
        W(f"  {tag:<42}n={len(vals):<5} 超额{bt._mean(vals):>7.2f}%  胜率{bt._winrate(vals):>5.1f}%"
          f"   vs全体 t={bt._fmt(t):>6} p={bt._fmt(p, 4)}")

    mid_ok = lambda r: num(r.get('t_pct_above_ma60')) >= 40 or num(r.get('t_avg_slope_60')) > 0
    ma5_lt_10 = lambda r: num(r.get('t_pct_above_ma5')) < num(r.get('t_pct_above_ma10'))
    ma5_gt_10 = lambda r: num(r.get('t_pct_above_ma5')) >= num(r.get('t_pct_above_ma10'))
    shrink = lambda r: num(r.get('s_avg_vol_ratio')) < 1.0
    top_ma60 = lambda r: num(r.get('t_pct_above_ma60')) >= 60

    grp('全体可用样本', lambda r: True)
    grp('中期趋势OK(MA60≥40 或 slope60>0)', mid_ok)
    grp('MA5 < MA10（二波特征）', ma5_lt_10)
    grp('MA5 ≥ MA10（趋势延续）', ma5_gt_10)
    grp('缩量(量比<1.0)', shrink)
    grp('中期趋势OK + MA5<MA10', lambda r: mid_ok(r) and ma5_lt_10(r))
    grp('中期趋势OK + MA5<MA10 + 缩量', lambda r: mid_ok(r) and ma5_lt_10(r) and shrink(r))
    grp('强中期趋势(MA60≥60) + MA5<MA10 + 缩量',
        lambda r: top_ma60(r) and ma5_lt_10(r) and shrink(r))
    grp('V2.1 旧四维高分(confirmation≥47)', lambda r: num(r.get('confirmation')) >= 47)

    # ── 3. 前波涨幅 ≥ 25% 的样本（用户硬条件）──
    W("\n" + "=" * 104)
    W("【3】用户硬条件交叉：前期涨幅 ≥25% 与回调结构")
    W("=" * 104)
    W("  注：V2 CSV 最长只有 20 日涨幅均值，无法直接表达「上一波涨幅≥25%」；")
    W("      此处用 t_avg_ret_20 分档近似，并叠加回调条件。")
    for lo, hi in ((25, 999), (15, 25), (5, 15), (-999, 5)):
        sel = [r for r in use if lo <= num(r.get('t_avg_ret_20')) < hi]
        vals = [r[f'ex_5'] for r in sel if r.get('ex_5') is not None]
        if len(vals) >= 20:
            W(f"  t_avg_ret_20 ∈ [{lo}, {hi})  n={len(vals):<5} 超额{bt._mean(vals):>7.2f}%  胜率{bt._winrate(vals):>5.1f}%")

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
