# -*- coding: utf-8 -*-
"""
W9 最小确认实验 —— W8 事后 EDA 线索"深蹲∧未破位"的廉价确认（预登记）。

线索（W8 探针，事后发现，多重比较下最亮格子天然偏亮）：
  深蹲(MAXBD≥0.12)∧未收盘破位：n=116 中位33.9% ≥20% 65.5%（锚 4042|22.1%|53.7%）
  跨阈值递增 0.08→0.10→0.12：中位 27.2%→29.1%→33.9%；分年度 3正2平1负。

预登记单一规则（零裁量）：
  A 组 = MAXBD ≥ thr 且 未破位；R 组 = 其余全部触发样本。
  入场/前瞻与锚完全同源：R5n 触发日收盘买入，看 120 日最高价；样本 = 触发事件。

两条破位口径：
  收盘破 BROKE（W8 原口径）：蓄势期任一日收盘 < 天量低
  盘中破 BROKE_I（更严格）：蓄势期任一日盘中低 < 天量低；盘中未破 ⊆ 收盘未破

预登记验收线（全部通过 → Refine；任一失败 → Abandon，W8 主判定维持）：
  L1 参数邻域·收盘破口径：thr∈{0.06..0.11} 中 ≥4/6 档中位边际≥+3pp 且无档位<0
  L2 破位定义敏感性·盘中破口径：thr∈{0.08..0.11} 中 ≥3/4 档中位边际≥+3pp
  L3 可操作性·收盘破口径@0.10：2021-2025 每年信号数 ≥5
  L4 分年度·收盘破口径@0.10：中位边际为正 ≥4/6 年 且 最差年边际 > -5pp
注：Refine ≠ 上生产；生产落地需另行评估触发顺序可行性并经用户确认。
自校验点：表1 的 0.08/0.10/0.12 行应复现 W8 探针表B（433|27.2% / 233|29.1% / 116|33.9%）。
"""
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from py_w7_score import pad, f1, summ, YEARS, cell3
from py_w8_squat import collect, load_float_map

GRID = [0.06, 0.07, 0.08, 0.09, 0.10, 0.11]
GRID_EXT = GRID + [0.12, 0.14]
CONFIRM_THR = 0.10


def cell5(lst):
    u = summ(lst)
    return f"{u['n']}|{f1(u['med'])}|{f1(u['a20'])}|{f1(u['a50'])}|{f1(u['p90'])}" if u else '-'


def scan(S, thr, intra):
    key = 'broke_i' if intra else 'broke'
    A = [s for s in S if s['maxbd'] >= thr and s[key] == 0]
    R = [s for s in S if not (s['maxbd'] >= thr and s[key] == 0)]
    return A, R


def margin(A, R):
    ua, ur = summ(A), summ(R)
    return (ua['med'] - ur['med']) if ua and ur else None


def show_margins(ms):
    return '  '.join(f'{thr:.2f}:{f1(m)}' if m is not None else f'{thr:.2f}:-' for thr, m in ms)


def main():
    S = collect(load_float_map())
    if not S:
        print('无有效样本，退出')
        return

    print()
    print('表0 锚自校验（应与存档锚一致：n=4042 中位22.1% ≥20% 53.7% P90 80.0%）')
    print(pad('组', 16) + pad('n', 8) + pad('中位', 9) + pad('≥20%', 9) + pad('≥50%', 9) + pad('P90', 9))
    u = summ(S)
    print(pad('W9全样本', 16) + pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['a20']), 9)
          + pad(f1(u['a50']), 9) + pad(f1(u['p90']), 9))

    print()
    print('表1 参数邻域·收盘破口径（A=MAXBD≥thr且未破位，R=其余；格 = n|中位|≥20%|≥50%|P90）')
    print(pad('阈值', 8) + pad('A组', 34) + pad('R组', 34) + pad('中位边际', 10))
    for thr in GRID_EXT:
        A, R = scan(S, thr, False)
        m = margin(A, R)
        print(pad(f'{thr:.2f}', 8) + pad(cell5(A), 34) + pad(cell5(R), 34)
              + pad(f1(m) if m is not None else '-', 10))

    print()
    print('表2 参数邻域·盘中破口径（更严格的未破位定义；0.08~0.11 为 L2 验收段）')
    print(pad('阈值', 8) + pad('A组', 34) + pad('R组', 34) + pad('中位边际', 10))
    for thr in GRID_EXT:
        A, R = scan(S, thr, True)
        m = margin(A, R)
        print(pad(f'{thr:.2f}', 8) + pad(cell5(A), 34) + pad(cell5(R), 34)
              + pad(f1(m) if m is not None else '-', 10))

    print()
    print(f'表3 分年度·收盘破口径 @{CONFIRM_THR:.2f}（L3/L4 验收表；格 = n|中位|≥20%）')
    print(pad('年份', 8) + pad('A组', 24) + pad('R组', 24) + pad('中位边际', 10))
    A10, R10 = scan(S, CONFIRM_THR, False)
    for yr in YEARS:
        Ay = [s for s in A10 if s['y'] == yr]
        Ry = [s for s in R10 if s['y'] == yr]
        m = margin(Ay, Ry)
        print(pad(yr, 8) + pad(cell3(Ay), 24) + pad(cell3(Ry), 24)
              + pad(f1(m) if m is not None else '-', 10))
    m = margin(A10, R10)
    print(pad('全部', 8) + pad(cell3(A10), 24) + pad(cell3(R10), 24)
          + pad(f1(m) if m is not None else '-', 10))

    print()
    print(f'表4 分年度·盘中破口径 @{CONFIRM_THR:.2f}（口径敏感性下的可操作性；格 = n|中位|≥20%）')
    print(pad('年份', 8) + pad('A组', 24) + pad('R组', 24) + pad('中位边际', 10))
    A10i, R10i = scan(S, CONFIRM_THR, True)
    for yr in YEARS:
        Ay = [s for s in A10i if s['y'] == yr]
        Ry = [s for s in R10i if s['y'] == yr]
        m = margin(Ay, Ry)
        print(pad(yr, 8) + pad(cell3(Ay), 24) + pad(cell3(Ry), 24)
              + pad(f1(m) if m is not None else '-', 10))
    m = margin(A10i, R10i)
    print(pad('全部', 8) + pad(cell3(A10i), 24) + pad(cell3(R10i), 24)
          + pad(f1(m) if m is not None else '-', 10))

    print()
    print(f'表5 A组(收盘破@{CONFIRM_THR:.2f}) AGE 分布（信号时间成本；格 = n|中位|≥20%）')
    for lo, hi in [(3, 6), (6, 9), (9, 12), (12, 21)]:
        g = [s for s in A10 if lo <= s['age'] < hi]
        share = f1(len(g) / len(A10)) if A10 else '-'
        print(pad(f'AGE {lo}~{hi - 1}', 16) + pad(cell3(g), 24) + pad(share, 10))

    m1 = [(thr, margin(*scan(S, thr, False))) for thr in GRID]
    m2 = [(thr, margin(*scan(S, thr, True))) for thr in GRID[2:]]
    cnt_y = {yr: sum(1 for s in A10 if s['y'] == yr) for yr in YEARS}
    marg_y = [margin([s for s in A10 if s['y'] == yr], [s for s in R10 if s['y'] == yr])
              for yr in YEARS]

    l1 = sum(m is not None and m >= 0.03 for _, m in m1) >= 4 and all(m is not None and m >= 0 for _, m in m1)
    l2 = sum(m is not None and m >= 0.03 for _, m in m2) >= 3
    l3 = all(cnt_y.get(yr, 0) >= 5 for yr in YEARS if yr <= 2025)
    l4 = (sum(m is not None and m > 0 for m in marg_y) >= 4
          and all(m is not None and m > -0.05 for m in marg_y))

    print()
    print('预登记验收线判定：')
    print(f"  L1 参数邻域(收盘破 0.06~0.11，≥4/6档≥+3pp且无负档): {'通过' if l1 else '不通过'}  [{show_margins(m1)}]")
    print(f"  L2 盘中破口径(0.08~0.11，≥3/4档≥+3pp): {'通过' if l2 else '不通过'}  [{show_margins(m2)}]")
    print(f"  L3 可操作性(收盘破@0.10，2021-2025每年≥5): {'通过' if l3 else '不通过'}  每年信号数={[cnt_y.get(yr, 0) for yr in YEARS]}")
    print(f"  L4 分年度(收盘破@0.10，≥4/6正且最差>-5pp): {'通过' if l4 else '不通过'}  [{show_margins(list(zip(YEARS, marg_y)))}]")
    ok = all([l1, l2, l3, l4])
    print()
    print(f"[W9终审] {'Refine —— 四线全过，线索存活，可评估生产落地（需用户确认）' if ok else 'Abandon —— EDA 亮格未通过廉价确认，W8 主判定维持，生产端不动'}")


if __name__ == '__main__':
    main()
