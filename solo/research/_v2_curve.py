# -*- coding: utf-8 -*-
"""三花聚顶 V2 · VR-response 曲线与四象限热图（第 2 节「绘制」要求）

输入: report_daily/research/three_flower_v2_volume_curve.csv
      report_daily/research/three_flower_v2_breakout_analysis.csv
      report_daily/research/three_flower_v2_oos.csv
输出: report_daily/research/three_flower_v2_curve.png
说明: 只画已算出的分层结果，不重新计算任何指标，不调参。
"""
import os
import re
import warnings

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RD = os.path.join(ROOT, 'report_daily', 'research')

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

VR_ORDER = ['<0.6', '0.6-0.8', '0.8-1.0', '1.0-1.1', '1.1-1.2',
            '1.2-1.3', '1.3-1.5', '1.5-2.0', '>2.0']
VR_MID = [0.45, 0.7, 0.9, 1.05, 1.15, 1.25, 1.4, 1.75, 2.5]
HOR = (5, 10, 20)
PAT = re.compile(r'网格\(幅(\d+)%/量([\d.]+)\)')


def main():
    vc = pd.read_csv(os.path.join(RD, 'three_flower_v2_volume_curve.csv'))
    ba = pd.read_csv(os.path.join(RD, 'three_flower_v2_breakout_analysis.csv'))
    oo = pd.read_csv(os.path.join(RD, 'three_flower_v2_oos.csv'))

    vr = vc[vc['section'] == 'VR_全体突破'].copy()
    fig = plt.figure(figsize=(13.5, 12.5), dpi=130)
    gs = fig.add_gridspec(3, 2, height_ratios=[1.0, 1.0, 0.85], hspace=0.42, wspace=0.26)

    for k, (col, ttl) in enumerate((('mean', 'VR → 未来收益（均值，已扣成本）'),
                                    ('med', 'VR → 未来收益（中位数）'))):
        ax = fig.add_subplot(gs[0, k])
        for N, mk in zip(HOR, ('o', 's', '^')):
            y, cnt = [], []
            for g in VR_ORDER:
                r = vr[(vr['group'] == g) & (vr['horizon'] == N)]
                y.append(100 * float(r[col].iloc[0]) if len(r) else np.nan)
                cnt.append(int(r['n'].iloc[0]) if len(r) else 0)
            ax.plot(VR_MID, y, marker=mk, lw=1.8, ms=6, label='T+%d' % N)
        ax.axhline(0, color='#888', lw=0.9, ls='--')
        ax.axvline(1.2, color='#c0392b', lw=1.2, ls=':')
        ax.text(1.22, ax.get_ylim()[1] * 0.92, 'VR=1.2', color='#c0392b', fontsize=9)
        ax.set_xlabel('VR = 突破日量 / MA20量')
        ax.set_ylabel('收益 %')
        ax.set_title(ttl, fontsize=11)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=9)
        if k == 0:
            for x, g in zip(VR_MID, VR_ORDER):
                n = int(vr[(vr['group'] == g) & (vr['horizon'] == 10)]['n'].iloc[0])
                ax.annotate('n=%d' % n, (x, ax.get_ylim()[0] * 0.98), fontsize=8,
                            ha='center', va='top', color='#555')

    # ── 四象限敏感性网格（T+10 配对超额 adv_fix）
    grid = {}
    for _, r in ba[ba['section'].str.startswith('QUADGRID') & (ba['horizon'] == 10)].iterrows():
        m = PAT.search(r['section'])
        if not m:
            continue
        at, vt = int(m.group(1)), float(m.group(2))
        grid.setdefault(r['group'], {})[(at, vt)] = 100 * float(r['adv_fix']) \
            if pd.notna(r['adv_fix']) else np.nan
    ats, vts = [1, 2, 3], [0.8, 1.0, 1.2]
    for k, gname in enumerate(('A', 'D')):
        ax = fig.add_subplot(gs[1, k])
        M = np.array([[grid.get(gname, {}).get((a, v), np.nan) for v in vts] for a in ats])
        im = ax.imshow(M, cmap='RdYlGn', vmin=-2.2, vmax=2.2, aspect='auto')
        for i in range(len(ats)):
            for j in range(len(vts)):
                if np.isfinite(M[i, j]):
                    ax.text(j, i, '%+.2f' % M[i, j], ha='center', va='center', fontsize=10)
        ax.set_xticks(range(len(vts)), ['%.1f' % v for v in vts])
        ax.set_yticks(range(len(ats)), ['%d%%' % a for a in ats])
        ax.set_xlabel('突破量能阈值 VR')
        ax.set_ylabel('突破幅度阈值')
        ax.set_title('%s 象限（%s）T+10 配对超额 %%' % (
            gname, '低量+小幅' if gname == 'A' else '高量+大幅'), fontsize=11)
        fig.colorbar(im, ax=ax, fraction=0.045)

    # ── 分期（TRAIN/VALID/OOS）T+10 均值
    ax = fig.add_subplot(gs[2, :])
    names = ['A_三花+低量突破(VR<1.2)', 'A2_三花+高量突破(VR>=1.2)',
             'C_三花形成无突破', 'D_无三花(offset10)']
    labs = ['A 低量突破', 'A2 高量突破', 'C 三花无突破', 'D 无三花']
    per = ['TRAIN', 'VALID', 'OOS']
    x = np.arange(len(names))
    w = 0.26
    for i, p in enumerate(per):
        y = []
        for nm in names:
            r = oo[(oo['section'] == 'PERIOD_%s' % p) & (oo['group'] == nm) & (oo['horizon'] == 10)]
            y.append(100 * float(r['mean'].iloc[0]) if len(r) else np.nan)
        b = ax.bar(x + (i - 1) * w, y, w, label=p)
        for xx, yy in zip(x + (i - 1) * w, y):
            if np.isfinite(yy):
                ax.text(xx, yy + (0.08 if yy >= 0 else -0.22), '%+.2f' % yy, ha='center', fontsize=8.5)
    ax.axhline(0, color='#333', lw=1.0)
    ax.set_xticks(x, labs)
    ax.set_ylabel('T+10 收益 %')
    ax.set_title('分期对照：三花本身 / 突破 / 低量突破 Alpha 拆解（T+10 均值，已扣成本）', fontsize=11)
    ax.grid(alpha=0.25, axis='y')
    ax.legend(fontsize=9)

    fp = os.path.join(RD, 'three_flower_v2_curve.png')
    fig.savefig(fp, bbox_inches='tight')
    print('已写 %s' % fp)

    print('\nVR 分层样本量（T+10）:')
    for g in VR_ORDER:
        r = vr[(vr['group'] == g) & (vr['horizon'] == 10)]
        print('  %-8s n=%-5d mean %+0.2f%% med %+0.2f%%' % (
            g, int(r['n'].iloc[0]), 100 * float(r['mean'].iloc[0]), 100 * float(r['med'].iloc[0])))


if __name__ == '__main__':
    main()
