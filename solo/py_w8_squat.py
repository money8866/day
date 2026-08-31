# -*- coding: utf-8 -*-
"""
W8 深蹲分层回测（触发级）—— 把 W7 唯一活信号（BD 反向）做成 R5n 的分层特征。

背景（W7 结论）：
  截面回测证伪四组件合成（评分反预测、顶20% 无边际增量），唯一准单调活信号是
  BD 方向反转：天量后蓄势期蹲得越深（收盘离天量高越远），未来 120 日越好。
  W8 把它搬到 R5n 触发级样本上确认。

与 W7 管道的关系：
  天量日事件构造、前除权/窗口除权剔除、R5n 触发判定、涨停剔/删失口径全部
  直接 import py_w7_score，保证与锚（4042 触发）完全同源；样本=触发事件
  （每干净天量日至多一次），天然消除 W7 口径B 同天量多截面的聚集（实验③）。

深蹲特征（只用 vd+1..触发日-1 数据，无未来函数；触发日收盘买入，看 120 日最高价）：
  MAXBD  蓄势期最深收盘距突破位 = max((天量高-收)/天量高)   越深越好（假设）
  MINLP  蓄势期最深盘中低点/天量高                          越深越好（假设，次要）
  AGE    触发日距天量日（3~20）                             时间混杂变量
  BROKE  蓄势期是否曾收盘跌破天量低（破位修复型 vs 未破位型）
预登记阈值 SQ_THR=0.12：取自 W7 十分位 D10 下界（0.120），属发现样本提出的假设、
  在触发级新抽样单元上确认；稳健性靠表4 平台扫描（0.04~0.25）辩护，不靠单点。

三实验：① 表3 MAXBD×AGE 3×3 共线性拆解（深度驱动还是时间驱动）
        ② 表4 阈值平台扫描（找平台不找尖峰）
        ③ 触发级聚合（一事件一样本）
"""
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from stock_cache import get_conn
from py_w7_score import (BT_START, W, MIN_BARS, VD_MIN_I, VD_GAP, YEARS, is_stock, pad, f1,
                         load_float_map, find_trigger, is_limit_up_close, fwd,
                         summ, deciles, hcells, full_row, cell3, fmt_rng)

SQ_THR = 0.12
THRESHOLDS = [0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.25]


def squat_features(bars, vd, tg, vdL, vdH):
    maxbd, minlp, broke, broke_i = 0.0, 1.0, 0, 0
    for j in range(vd + 1, tg):
        bd = (vdH - bars[j][4]) / vdH
        if bd > maxbd:
            maxbd = bd
        lp = bars[j][3] / vdH
        if lp < minlp:
            minlp = lp
        if bars[j][4] < vdL:
            broke = 1
        if bars[j][3] < vdL:
            broke_i = 1
    return maxbd, minlp, broke, broke_i


def collect(fl_map):
    cnt = dict(cap0=0, short=0, dup=0, ex=0, exwin=0)
    st = dict(clean=0, trig=0, lu=0, cen=0)
    S = []

    with get_conn() as conn:
        all_codes = [r[0] for r in conn.execute('SELECT DISTINCT ts_code FROM daily_cache')]
        stock_codes = sorted(c for c in all_codes if is_stock(c))
        total = len(stock_codes)
        print(f'扫描 {total} 只（自 {BT_START}）...', file=sys.stderr)
        for k, code in enumerate(stock_codes, 1):
            if k % 500 == 0:
                print(f'  {k}/{total}', file=sys.stderr)
            fl = fl_map.get(code, 0)
            if not fl or fl <= 0:
                cnt['cap0'] += 1
                continue
            bars = conn.execute(
                'SELECT trade_date, open, high, low, close, vol, pre_close '
                'FROM daily_cache WHERE ts_code=? AND trade_date>=? ORDER BY trade_date',
                (code, BT_START)).fetchall()
            n = len(bars)
            if n < MIN_BARS:
                cnt['short'] += 1
                continue

            pm = -1.0
            days = []
            for i in range(n):
                h = bars[i][5] / fl
                isNew = h > pm
                if isNew:
                    pm = h
                if isNew and VD_MIN_I <= i <= n - 2:
                    days.append(i)
            picks = []
            last_pick = -100
            for i in days:
                if i - last_pick <= VD_GAP:
                    cnt['dup'] += 1
                    continue
                picks.append(i)
                last_pick = i

            for i in picks:
                ex = False
                for j in range(max(1, i - 59), i + 1):
                    if bars[j][1] < bars[j - 1][4] * 0.85:
                        ex = True
                        break
                if ex:
                    cnt['ex'] += 1
                    continue
                window_ex = False
                for j in range(i + 1, min(i + W, n - 1) + 1):
                    if bars[j][1] < bars[j - 1][4] * 0.85 and bars[j][5] > bars[j - 1][5] * 0.2:
                        window_ex = True
                        break
                if window_ex:
                    cnt['exwin'] += 1
                    continue
                st['clean'] += 1
                vdL, vdH = bars[i][3], bars[i][2]
                t5 = find_trigger('R5n', bars, i, n)
                if t5 < 0:
                    continue
                st['trig'] += 1
                if is_limit_up_close(bars, t5, code):
                    st['lu'] += 1
                    continue
                r = fwd(bars, t5, n)
                if r is None:
                    st['cen'] += 1
                    continue
                maxbd, minlp, broke, broke_i = squat_features(bars, i, t5, vdL, vdH)
                S.append(dict(y=int(bars[t5][0][:4]), maxbd=maxbd, minlp=minlp, age=t5 - i,
                              broke=broke, broke_i=broke_i, h=r[0], t=r[1]))

    print('=' * 72)
    print(f"[剔除] 流通缺失={cnt['cap0']} 数据不足={cnt['short']} 天量去重={cnt['dup']} 前除权={cnt['ex']} 窗口除权={cnt['exwin']}")
    print(f"[状态] 干净天量日={st['clean']} 触发={st['trig']} 涨停剔={st['lu']} 删失={st['cen']} 有效样本={len(S)}")
    return S


def main():
    S = collect(load_float_map())

    if not S:
        print('无有效样本，退出')
        return
    deep_all = [s for s in S if s['maxbd'] >= SQ_THR]
    rest_all = [s for s in S if s['maxbd'] < SQ_THR]

    print()
    print('表1 MAXBD 十分位（触发级：触发日收盘买入，120日最高价；检验深蹲方向性）')
    dh = (pad('桶', 5) + pad('MAXBD值域', 18) + pad('n', 8) + pad('中位', 9) + pad('P90', 9)
          + pad('≥20%', 9) + pad('≥50%', 9) + pad('未盈利', 11))
    print(dh)
    vals = [s['maxbd'] for s in S]
    for bi, idxs in enumerate(deciles(vals), 1):
        print(pad(f'D{bi}', 5) + pad(fmt_rng(vals, idxs, 3), 18) + hcells([S[x] for x in idxs]))

    print()
    print(f'表2 关键分组全指标（深蹲阈值 = MAXBD ≥ {SQ_THR:.2f}）')
    print((pad('组', 16) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('均值', 9)
           + pad('≥10%', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('≥50%', 9) + pad('未盈利', 11) + pad('达峰中位', 9)))
    print(full_row('深蹲组', deep_all))
    print(full_row('非深蹲组', rest_all))
    print(full_row('破位修复型', [s for s in S if s['broke'] == 1]))
    print(full_row('未破位型', [s for s in S if s['broke'] == 0]))
    print(full_row('深蹲∩未破位', [s for s in S if s['broke'] == 0 and s['maxbd'] >= SQ_THR]))

    print()
    print('表3 实验① 共线性拆解：MAXBD三分位 × AGE三分位（格 = n|中位|≥20%）')
    print('  深蹲优势在各 AGE 列内均成立 → 深度驱动；只在慢触发列成立 → 时间驱动')
    mvals = [s['maxbd'] for s in S]
    avals = [s['age'] for s in S]
    mb = deciles(mvals, 3)
    ab = deciles(avals, 3)
    asets = [set(x) for x in ab]
    print(pad('', 18) + ''.join(pad('AGE ' + fmt_rng(avals, xs, 1), 26) for xs in ab))
    for ri, ridx in enumerate(mb):
        line = pad('MAXBD ' + fmt_rng(mvals, ridx, 3), 18)
        for ci in range(3):
            line += pad(cell3([S[x] for x in ridx if x in asets[ci]]), 26)
        print(line)
    print(pad('行边际', 18) + ''.join(pad(cell3([S[x] for x in ridx]), 26) for ridx in mb))
    print(pad('列边际', 18) + ''.join(pad(cell3([S[x] for x in xs]), 26) for xs in ab))

    print()
    print('表4 实验② 阈值平台扫描：深蹲组 = MAXBD ≥ 阈值（找平台不找尖峰）')
    print(pad('阈值', 8) + pad('深蹲n', 9) + pad('占比', 9) + pad('深蹲中位', 10) + pad('深蹲≥20%', 10)
          + pad('非深蹲中位', 12) + pad('非深蹲≥20%', 12) + pad('中位差', 10))
    n_all = len(S)
    for thr in THRESHOLDS:
        deep = [s for s in S if s['maxbd'] >= thr]
        rest = [s for s in S if s['maxbd'] < thr]
        ud, ur = summ(deep), summ(rest)
        if not ud or not ur:
            continue
        print(pad(f'{thr:.2f}', 8) + pad(ud['n'], 9) + pad(f1(ud['n'] / n_all), 9)
              + pad(f1(ud['med']), 10) + pad(f1(ud['a20']), 10)
              + pad(f1(ur['med']), 12) + pad(f1(ur['a20']), 12) + pad(f1(ud['med'] - ur['med']), 10))

    print()
    print('表5 实验③+分年度稳健性：触发级一事件一样本，深蹲组 vs 非深蹲组（格 = n|中位|≥20%）')
    print(pad('年份', 8) + pad('深蹲组', 24) + pad('非深蹲组', 24) + pad('全样本', 24))
    for yr in YEARS:
        print(pad(yr, 8)
              + pad(cell3([s for s in deep_all if s['y'] == yr]), 24)
              + pad(cell3([s for s in rest_all if s['y'] == yr]), 24)
              + pad(cell3([s for s in S if s['y'] == yr]), 24))
    print(pad('全部', 8) + pad(cell3(deep_all), 24) + pad(cell3(rest_all), 24) + pad(cell3(S), 24))

    print()
    print('表6 对照（W8 全样本 = R5n 锚本体重算，应与存档锚一致）')
    print((pad('组', 16) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('均值', 9)
           + pad('≥10%', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('≥50%', 9) + pad('未盈利', 11) + pad('达峰中位', 9)))
    print(full_row('W8全样本(锚)', S))
    print(full_row('深蹲组', deep_all))
    print(full_row('非深蹲组', rest_all))

    print()
    print('[判定基准] 存档 R5n 全样本锚：n=4042 中位22.1% ≥20% 53.7% P90 80.0%（表6首行允许微漂移）。')
    print('[W7发现] 截面级口径B 顶20%-BD反向：中位29.3% ≥20% 60.3%（同天量多截面聚集，需触发级确认）。')
    print('[验收线] 深蹲-非深蹲中位边际≥+5pp；表3 各AGE列内深蹲均优；表4 平台存在(0.08~0.16平稳)；表5 分年度多数正。')


if __name__ == '__main__':
    main()
