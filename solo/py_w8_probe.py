# -*- coding: utf-8 -*-
"""
W8 探针（判定完整性补充，不改变预登记口径；样本 = py_w8_squat.collect() 同一采样）：
  A. MINLP 十分位 —— 补齐 W8 预登记四特征中未出表的最后一个（值域升序，D10 最深）；
  B. 深蹲∩未破位（MAXBD≥thr 且 BROKE=0）阈值扫描 —— 表2 唯一亮点的稳健性检查，
     属事后探索线索，按 EDA 降权读表；
  C. 预登记阈值 0.12 下 A 组分年度；
  D. 深蹲×破位 2×2 镜像矩阵，检验"未破位"是否必要成分。
"""
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from py_w7_score import pad, f1, summ, deciles, fmt_rng, hcells, full_row, cell3, YEARS
from py_w8_squat import collect, load_float_map

SQ_THR = 0.12


def cell5(lst):
    u = summ(lst)
    return f"{u['n']}|{f1(u['med'])}|{f1(u['a20'])}|{f1(u['a50'])}|{f1(u['p90'])}" if u else '-'


def main():
    S = collect(load_float_map())

    print()
    print('表A MINLP 十分位（蓄势期最深盘中低点/天量高；值域升序，D10 = 盘中蹲最深）')
    dh = (pad('桶', 5) + pad('MINLP值域', 18) + pad('n', 8) + pad('中位', 9) + pad('P90', 9)
          + pad('≥20%', 9) + pad('≥50%', 9) + pad('未盈利', 11))
    print(dh)
    vals = [s['minlp'] for s in S]
    for bi, idxs in enumerate(deciles(vals), 1):
        print(pad(f'D{bi}', 5) + pad(fmt_rng(vals, idxs, 3), 18) + hcells([S[x] for x in idxs]))

    print()
    print('表B 深蹲∩未破位 阈值扫描（A组 = MAXBD≥阈值 且 BROKE=0；对照 = 其余全部；格 = n|中位|≥20%|≥50%|P90）')
    print(pad('阈值', 8) + pad('A组', 34) + pad('对照', 34))
    for thr in [0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20]:
        A = [s for s in S if s['maxbd'] >= thr and s['broke'] == 0]
        R = [s for s in S if not (s['maxbd'] >= thr and s['broke'] == 0)]
        print(pad(f'{thr:.2f}', 8) + pad(cell5(A), 34) + pad(cell5(R), 34))

    A12 = [s for s in S if s['maxbd'] >= SQ_THR and s['broke'] == 0]
    R12 = [s for s in S if not (s['maxbd'] >= SQ_THR and s['broke'] == 0)]

    print()
    print(f'表C 深蹲∩未破位（MAXBD≥{SQ_THR:.2f} 且 BROKE=0）分年度（格 = n|中位|≥20%）')
    print(pad('年份', 8) + pad('A组', 24) + pad('对照', 24) + pad('全样本', 24))
    for yr in YEARS:
        print(pad(yr, 8)
              + pad(cell3([s for s in A12 if s['y'] == yr]), 24)
              + pad(cell3([s for s in R12 if s['y'] == yr]), 24)
              + pad(cell3([s for s in S if s['y'] == yr]), 24))
    print(pad('全部', 8) + pad(cell3(A12), 24) + pad(cell3(R12), 24) + pad(cell3(S), 24))

    print()
    print(f'表D 深蹲×破位 2×2 镜像矩阵（深蹲 = MAXBD≥{SQ_THR:.2f}）')
    print((pad('组', 16) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('均值', 9)
           + pad('≥10%', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('≥50%', 9) + pad('未盈利', 11) + pad('达峰中位', 9)))
    print(full_row('深蹲∩未破位', A12))
    print(full_row('深蹲∩破位', [s for s in S if s['maxbd'] >= SQ_THR and s['broke'] == 1]))
    print(full_row('浅蹲∩未破位', [s for s in S if s['maxbd'] < SQ_THR and s['broke'] == 0]))
    print(full_row('浅蹲∩破位', [s for s in S if s['maxbd'] < SQ_THR and s['broke'] == 1]))

    print()
    print('[读表提醒] B/C/D 均为事后探索切分（预登记外），多重比较下最亮格子天然偏亮；'
          '只有跨阈值连续、分年度多数正、镜像矩阵自洽的线索才值得立项 W9。')


if __name__ == '__main__':
    main()
