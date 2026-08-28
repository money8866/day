# -*- coding: utf-8 -*-
"""PEA 因子方向交叉验证: Spearman IC + 五分位单调性 + 触发类型分组

用法:
    python -X utf8 pea_factor_check.py report_daily\\pea_absorption_backtest_2025H1_ann25.csv [csv2 ...]

可选:
    --y fwd15x    指定收益锚列; 缺省自动选择非空率>=50%的最长 fwd 窗(fwd5x~fwd30x)
                  (2026H1 回测 fwd=6, fwd15x 全空, 会自动落到 fwd5x)

每个 CSV 输出一节:
  1) 样本概览: 行数/股票数/扫描日范围/收益锚选择
  2) IC 表: 各因子 Spearman IC(vs 收益锚) + 有效样本 (|IC| 无门槛, 只看方向与强弱)
  3) 五分位表: Q1(因子最低)..Q5(因子最高) 的收益均值/胜率/样本 + Q5-Q1 价差 + 单调破坏数
  4) 触发类型分组: 按 trigger_type 的收益均值降序
最后输出 跨文件 IC 汇总矩阵(交叉验证核心交付)。
"""
import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

FACTOR_CANDIDATES = ('fq', 'rqs', 'gap_s', 'ars', 'pqs', 'tqs', 'ts', 'ees', 'alpha', 'conf')
Y_CANDIDATES = ('fwd30x', 'fwd20x', 'fwd15x', 'fwd10x', 'fwd5x')
MIN_Y_COVER = 0.5
MIN_IC_N = 30
MIN_QT_N = 100


def pick_y(df):
    best, best_cov = None, 0.0
    for c in Y_CANDIDATES:
        if c in df.columns:
            cov = float(df[c].notna().mean())
            if cov > best_cov:
                best, best_cov = c, cov
    return best, best_cov


def spearman_ic(df, col, y):
    sub = df[[col, y]].dropna()
    n = len(sub)
    if n < MIN_IC_N or sub[col].nunique() < 2 or sub[y].nunique() < 2:
        return np.nan, n
    return float(sub[col].corr(sub[y], method='spearman')), n


def quintile_line(df, col, y):
    sub = df[[col, y]].dropna().copy()
    if len(sub) < MIN_QT_N or sub[col].nunique() < 5:
        return None
    try:
        sub['q'] = pd.qcut(sub[col], 5, labels=False, duplicates='drop')
    except ValueError:
        return None
    means, wins, cnts = {}, {}, {}
    for q, grp in sub.groupby('q')[y]:
        means[q] = float(grp.mean())
        wins[q] = float((grp > 0).mean() * 100)
        cnts[q] = len(grp)
    qs = sorted(means)
    cells = ' | '.join(f'Q{q + 1} {means[q]:+.2f}%/{wins[q]:.0f}%/n={cnts[q]}' for q in qs)
    spread = means[qs[-1]] - means[qs[0]]
    direction = 1 if spread > 0 else -1
    viol = sum(1 for a, b in zip(qs, qs[1:]) if (means[b] - means[a]) * direction < 0)
    return cells, spread, viol


def trigger_table(df, y):
    if 'trigger_type' not in df.columns:
        return None
    sub = df[['trigger_type', y]].dropna()
    if sub.empty:
        return None
    rows = []
    for k, grp in sub.groupby('trigger_type'):
        rows.append((str(k), float(grp[y].mean()), float((grp[y] > 0).mean() * 100), len(grp)))
    rows.sort(key=lambda r: -r[1])
    return rows


def file_label(path):
    m = re.search(r'pea_absorption_backtest_(\w+)_(\w+)\.csv$', os.path.basename(path))
    if m:
        return f'{m.group(1)}_{m.group(2)}'
    return os.path.splitext(os.path.basename(path))[0]


def process_one(path, y_override, ic_matrix):
    label = file_label(path)
    print('=' * 78)
    print(f'## {label}  ({path})')
    print('=' * 78)
    try:
        df = pd.read_csv(path, encoding='utf-8-sig')
    except Exception as e:
        print(f'[跳过] 读取失败: {e}')
        return
    if df.empty:
        print('[跳过] 空文件')
        return

    if y_override and y_override in df.columns:
        y, cov = y_override, float(df[y_override].notna().mean())
    else:
        y, cov = pick_y(df)
    if y is None:
        print('[跳过] 找不到任何 fwd*x 收益锚列')
        return
    ustocks = df['ts_code'].nunique() if 'ts_code' in df.columns else '--'
    if 'scan_date' in df.columns:
        drange = f"{df['scan_date'].astype(str).min()}~{df['scan_date'].astype(str).max()}"
    else:
        drange = '--'
    print(f'样本: {len(df)} 行 / {ustocks} 只 / 扫描日 {drange}')
    print(f'收益锚: {y} (非空率 {cov * 100:.0f}%)  全体均值 {df[y].mean():+.2f}% '
          f'胜率 {(df[y] > 0).mean() * 100:.0f}%')

    factors, skipped = [], []
    for c in FACTOR_CANDIDATES:
        if c not in df.columns:
            skipped.append(f'{c}(缺列)')
        elif df[c].nunique(dropna=True) < 2:
            skipped.append(f'{c}(常数)')
        else:
            factors.append(c)
    if skipped:
        print(f'剔除因子: {", ".join(skipped)}')

    print('\n-- Spearman IC (因子 vs 收益锚) --')
    ics = {}
    for c in factors:
        ic, n = spearman_ic(df, c, y)
        ics[c] = ic
        tag = '' if ic == ic else '  (样本不足)'
        print(f'  {c:<8} IC={ic:+.3f}  n={n}{tag}')
    ic_matrix[label] = ics

    print('\n-- 五分位 (Q1=因子最低 .. Q5=因子最高; 收益均值%/胜率%/样本) --')
    for c in factors:
        r = quintile_line(df, c, y)
        if r is None:
            print(f'  {c:<8} (样本不足, 跳过)')
        else:
            cells, spread, viol = r
            print(f'  {c:<8} {cells}')
            print(f'           Q5-Q1 价差 {spread:+.2f}% | 单调破坏 {viol}/4')

    tt = trigger_table(df, y)
    if tt:
        print('\n-- 触发类型分组 (收益均值降序) --')
        for k, m, w, n in tt:
            print(f'  {k:<14} {m:+.2f}%  胜率{w:.0f}%  n={n}')


def print_matrix(ic_matrix):
    if len(ic_matrix) < 2:
        return
    labels = list(ic_matrix)
    print()
    print('=' * 78)
    print('## 跨文件 IC 汇总 (Spearman, vs 各自收益锚; 正=越高越好, 负=越高越差)')
    print('=' * 78)
    width = max(14, max(len(l) + 2 for l in labels))
    header = f"{'factor':<8}" + ''.join(f'{l:>{width}}' for l in labels)
    print(header)
    for f in FACTOR_CANDIDATES:
        if not any(f in m for m in ic_matrix.values()):
            continue
        row = f'{f:<8}'
        for l in labels:
            v = ic_matrix[l].get(f)
            row += f'{v:>{width}.3f}' if v == v else f'{"--":>{width}}'
        print(row)


def main():
    ap = argparse.ArgumentParser(description='PEA 因子方向交叉验证')
    ap.add_argument('csvs', nargs='+', help='回测明细 CSV(可多个)')
    ap.add_argument('--y', default=None, help='收益锚列(缺省自动选)')
    args = ap.parse_args()

    ic_matrix = {}
    for p in args.csvs:
        process_one(p, args.y, ic_matrix)
    print_matrix(ic_matrix)
    return 0


if __name__ == '__main__':
    sys.exit(main())
