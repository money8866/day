# -*- coding: utf-8 -*-
"""
W7 二次发动评分回测（截面版）—— 从"发现已突破"进化为"发现即将二次发动"。

与 py_maxgain_120d.py 的关系：
  天量日事件构造完全同口径（换手率自 20210101 起严格新高、VD_MIN_I=60、
  连续新高 10 根内去重、天量日前 60 根无 0.85 除权缺口、窗口内除权隔离剔除），
  并在同批重算 R1 / R5n 全样本作为对照锚（因数据每日更新，较存档数字可能微漂移）。

截面定义（零裁量，对齐生产 daily_pullback.py 每晚实际可见状态）：
  干净天量日 vd 之后逐日 t ∈ [vd+1, vd+20]（=生产 MAX_AGE，也是 R5n 决策窗口）：
    close[t] > vdH → 突破，截面序列终止（当日不计样本）
    close[t] < vdL → 破位，截面序列终止（当日不计样本）
    否则记一个截面样本（"未破位未突破"观察态，对应生产○锁筹待突破）
  生产可见性补充：
    a) 期间若出现新的换手新高被采集为下一个天量日 p_next，生产自 p_next 晚起只
       跟踪新天量，旧天量样本止于 p_next-1（R5n 触发入场不受此限——用户自选观察
       不会因扫描切换而消失）；
    b) 样本不附加"当前锁量"条件——锁量状态本身交给 L2/LRUN/LRATIO 因子刻画，
       这正是 RE_EXPANSION（量温和回升）因子的存在空间。

因子库 8 个（全部只用 t 日及以前数据，无未来函数）：
  LOCK  L2      近2日均量/天量               越低越好
        LRUN    连续锁筹天数(≤天量50%)        越高越好
        LRATIO  天量后锁筹日占比              越高越好
  BD    BD      距突破位=(天量高-收)/天量高    越低越好
        DP      回调深度=收/天量收-1           仅报告不入合成（方向不明）
  RE    WR3     收盘在近3日高低区间位置        越高越好
        YR3     近3日阳线占比                 越高越好
        VUP     近3日均量/再前3日均量          越高越好
合成：SECOND_WAVE_SCORE = 7 因子全样本秩效用(0~100)等权均值，无拟合权重。

双口径：
  A 直接持有：截面日收盘买入（当日涨停收盘不可成交剔除），看 120 日最高价
  B 等R5n触发：截面序列非破位终止且 R5n 于 [vd+3, vd+20] 触发 → 触发日收盘买入
    （触发日涨停收盘剔除；破位终止=生产已放弃观察不计 B；未触发计触发率）
"""
import sys
import os
import json
import math

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from stock_cache import get_conn

BT_START = '20210101'
W = 120
MIN_BARS = 280
VD_MIN_I = 60
VD_GAP = 10
R1_WINDOW = 20
MAX_AGE = 20
LOCK_THR = 0.5
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
FACTORS = ['L2', 'LRUN', 'LRATIO', 'BD', 'DP', 'WR3', 'YR3', 'VUP']
COMPOSITE = ['L2', 'LRUN', 'LRATIO', 'BD', 'WR3', 'YR3', 'VUP']
LOWER_BETTER = {'L2', 'BD'}
FDESC = {
    'L2': '近2日均量/天量（越低越好）',
    'LRUN': '连续锁筹天数（越高越好）',
    'LRATIO': '天量后锁筹日占比（越高越好）',
    'BD': '距突破位=(天量高-收)/天量高（越低越好）',
    'DP': '回调深度=收/天量收-1（仅报告不入合成）',
    'WR3': '收盘在近3日高低区间位置（越高越好）',
    'YR3': '近3日阳线占比（越高越好）',
    'VUP': '近3日均量/再前3日均量（越高越好）',
}

ROOT = os.path.dirname(os.path.abspath(__file__))
FLOAT_JSON = os.path.join(ROOT, 'py_float_share.json')


def is_stock(code):
    num, suf = code[:6], code[-2:]
    if suf == 'SZ':
        return num.startswith('00') or num.startswith('30')
    if suf == 'SH':
        return num.startswith('60') or num.startswith('68')
    if suf == 'BJ':
        return num.startswith('43') or num.startswith('92') or (num[0] == '8' and '2' <= num[1] <= '8')
    return False


def limit_of(code):
    num = code[:6]
    if num.startswith('30') or num.startswith('68'):
        return 0.20
    if num.startswith('43') or num.startswith('92') or (num[0] == '8' and '2' <= num[1] <= '8'):
        return 0.30
    return 0.10


def pad(s, n):
    s = str(s)
    w = sum(2 if ord(ch) > 255 else 1 for ch in s)
    return s + ' ' * max(0, n - w)


def f1(x):
    return f'{x * 100:.1f}%'


def load_float_map():
    if not os.path.exists(FLOAT_JSON):
        return {}
    try:
        with open(FLOAT_JSON, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    if not data:
        return {}
    snap = data.get(max(data)) or {}
    return {c: v for c, v in snap.items() if v and v > 0}


def find_trigger(rule, bars, i, n):
    if rule == 'R5n':
        V, vdH = bars[i][5], bars[i][2]
        for t in range(i + 3, min(i + R1_WINDOW, n - 1) + 1):
            o, c, v = bars[t][1], bars[t][4], bars[t][5]
            if c > vdH and c > o and v <= V and bars[t - 1][5] <= V * LOCK_THR and bars[t - 2][5] <= V * LOCK_THR:
                return t
        return -1
    if rule == 'R1':
        C, L, V = bars[i][4], bars[i][3], bars[i][5]
        for j in range(i + 1, min(i + R1_WINDOW, n - 1) + 1):
            o, c, v = bars[j][1], bars[j][4], bars[j][5]
            if c > o and v <= V * LOCK_THR and c < C and c > L:
                return j
        return -1
    return -1


def is_limit_up_close(bars, idx, code):
    if idx < 1:
        return False
    prev = bars[idx][6] or bars[idx - 1][4]
    return bars[idx][4] / prev - 1 >= limit_of(code) - 0.003


def fwd(bars, entry_bar, n):
    end = entry_bar + W
    if end > n - 1:
        return None
    mh, mt = float('-inf'), 0
    for j in range(entry_bar + 1, end + 1):
        if bars[j][2] > mh:
            mh, mt = bars[j][2], j - entry_bar
    return mh / bars[entry_bar][4] - 1, mt


def factors_at(bars, t, vd, V, vdC, vdH):
    c, v = bars[t][4], bars[t][5]
    lrun = 0
    j = t
    while j > vd and bars[j][5] <= V * LOCK_THR:
        lrun += 1
        j -= 1
    hi3 = max(bars[x][2] for x in range(t - 2, t + 1))
    lo3 = min(bars[x][3] for x in range(t - 2, t + 1))
    v3 = sum(bars[x][5] for x in range(t - 2, t + 1)) / 3
    p3 = sum(bars[x][5] for x in range(t - 5, t - 2)) / 3
    return {
        'L2': (bars[t - 1][5] + v) / 2 / V,
        'LRUN': lrun,
        'LRATIO': sum(1 for x in range(vd + 1, t + 1) if bars[x][5] <= V * LOCK_THR) / (t - vd),
        'BD': (vdH - c) / vdH,
        'DP': c / vdC - 1,
        'WR3': (c - lo3) / (hi3 - lo3) if hi3 > lo3 else 0.5,
        'YR3': sum(1 for x in range(t - 2, t + 1) if bars[x][4] > bars[x][1]) / 3,
        'VUP': v3 / p3 if p3 > 0 else 1.0,
    }


S = []
ANCH = {'R1': [], 'R5n': []}


def pct(srt, p):
    idx = (len(srt) - 1) * p
    lo, hi = math.floor(idx), math.ceil(idx)
    return srt[lo] + (srt[hi] - srt[lo]) * (idx - lo)


def summ(lst):
    n = len(lst)
    if not n:
        return None
    hs = sorted(r['h'] for r in lst)
    mean = sum(r['h'] for r in lst) / n

    def a(q):
        return sum(1 for r in lst if r['h'] >= q) / n

    ts = sorted(r['t'] for r in lst)
    return dict(n=n, mean=mean, med=pct(hs, 0.5), p75=pct(hs, 0.75), p90=pct(hs, 0.9),
                a10=a(0.10), a20=a(0.20), a30=a(0.30), a50=a(0.50),
                neg=sum(1 for r in lst if r['h'] < 0) / n, medt=pct(ts, 0.5))


def rank_util(values, lower_better):
    m = len(values)
    order = sorted(range(m), key=lambda i: values[i])
    u = [0.0] * m
    pos = 0
    while pos < m:
        hi = pos
        while hi + 1 < m and values[order[hi + 1]] == values[order[pos]]:
            hi += 1
        util = ((pos + hi) / 2 + 0.5) / m
        for x in range(pos, hi + 1):
            u[order[x]] = util
        pos = hi + 1
    if lower_better:
        return [1 - x for x in u]
    return u


def deciles(values, k=10):
    m = len(values)
    order = sorted(range(m), key=lambda i: values[i])
    out = [[] for _ in range(k)]
    for pos, idx in enumerate(order):
        out[min(k - 1, pos * k // m)].append(idx)
    return out


def hcells(lst):
    u = summ(lst)
    if not u:
        return pad('-', 8) + pad('-', 9) + pad('-', 9) + pad('-', 9) + pad('-', 9) + pad('-', 11)
    return (pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['p90']), 9)
            + pad(f1(u['a20']), 9) + pad(f1(u['a50']), 9) + pad(f1(u['neg']), 11))


def full_row(label, lst):
    u = summ(lst)
    if not u:
        return pad(label, 16) + pad('-', 8)
    return (pad(label, 16) + pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['p75']), 9)
            + pad(f1(u['p90']), 9) + pad(f1(u['mean']), 9) + pad(f1(u['a10']), 9) + pad(f1(u['a20']), 9)
            + pad(f1(u['a30']), 9) + pad(f1(u['a50']), 9) + pad(f1(u['neg']), 11) + pad(f"{u['medt']}日", 9))


def cell3(lst):
    u = summ(lst)
    return f"{u['n']}|{f1(u['med'])}|{f1(u['a20'])}" if u else '-'


def fmt_rng(vals, idxs, nd):
    lo = min(vals[i] for i in idxs)
    hi = max(vals[i] for i in idxs)
    return f'{lo:.{nd}f}~{hi:.{nd}f}'


def main():
    fl_map = load_float_map()
    cnt = dict(cap0=0, short=0, ex=0, exwin=0, dup=0)
    st = dict(clean=0, vds_with=0, brk=0, brkd=0,
              a_lu=0, a_cen=0, a_ok=0,
              b_drop=0, b_notrig=0, b_lu=0, b_cen=0, b_ok=0,
              lu1=0, lu5=0, cen1=0, cen5=0)

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

            for ki, i in enumerate(picks):
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
                vdV, vdC, vdL, vdH = bars[i][5], bars[i][4], bars[i][3], bars[i][2]

                t5 = find_trigger('R5n', bars, i, n)
                t1 = find_trigger('R1', bars, i, n)
                for rule, tr, klu, kcen in (('R5n', t5, 'lu5', 'cen5'), ('R1', t1, 'lu1', 'cen1')):
                    if tr < 0:
                        continue
                    if is_limit_up_close(bars, tr, code):
                        st[klu] += 1
                        continue
                    r = fwd(bars, tr, n)
                    if r is None:
                        st[kcen] += 1
                        continue
                    ANCH[rule].append(dict(y=int(bars[tr][0][:4]), h=r[0], t=r[1]))

                p_next = picks[ki + 1] if ki + 1 < len(picks) else n
                hi_t = min(i + MAX_AGE, n - 1, p_next - 1)
                st['vds_with'] += 1
                for t in range(i + 1, hi_t + 1):
                    c = bars[t][4]
                    if c > vdH:
                        st['brk'] += 1
                        break
                    if c < vdL:
                        st['brkd'] += 1
                        break
                    fac = factors_at(bars, t, i, vdV, vdC, vdH)
                    ha = ta = hb = tb = None
                    if is_limit_up_close(bars, t, code):
                        st['a_lu'] += 1
                    else:
                        r = fwd(bars, t, n)
                        if r is None:
                            st['a_cen'] += 1
                        else:
                            st['a_ok'] += 1
                            ha, ta = r
                    b_state = 0
                    for s in range(t + 1, min(i + R1_WINDOW, n - 1) + 1):
                        if bars[s][4] < vdL:
                            b_state = 1
                            break
                        if s >= i + 3 and s == t5:
                            b_state = 2
                            break
                    if b_state == 1:
                        st['b_drop'] += 1
                    elif b_state == 0:
                        st['b_notrig'] += 1
                    elif is_limit_up_close(bars, t5, code):
                        st['b_lu'] += 1
                    else:
                        r = fwd(bars, t5, n)
                        if r is None:
                            st['b_cen'] += 1
                        else:
                            st['b_ok'] += 1
                            hb, tb = r
                    S.append(dict(y=int(bars[t][0][:4]), fac=fac, ha=ha, ta=ta, hb=hb, tb=tb))

    print('=' * 72)
    print(f"[剔除] 流通缺失={cnt['cap0']} 数据不足={cnt['short']} 天量去重={cnt['dup']} 前除权={cnt['ex']} 窗口除权={cnt['exwin']}")
    print(f"[状态] 干净天量日={st['clean']} 有截面窗口={st['vds_with']} 序列终止 突破={st['brk']} 破位={st['brkd']}")
    print(f"[锚] R5n: 触发{len(ANCH['R5n'])}+涨停剔{st['lu5']}+删失{st['cen5']}   R1: 触发{len(ANCH['R1'])}+涨停剔{st['lu1']}+删失{st['cen1']}")
    print(f"[截面样本] 共{len(S)}  A: 有效{st['a_ok']} 涨停剔{st['a_lu']} 删失{st['a_cen']}"
          f"  B: 有效{st['b_ok']} 破位弃{st['b_drop']} 未触发{st['b_notrig']} 涨停剔{st['b_lu']} 删失{st['b_cen']}")

    if not S:
        print('无截面样本，退出')
        return
    util = {}
    for f in COMPOSITE:
        util[f] = rank_util([s['fac'][f] for s in S], f in LOWER_BETTER)
    for idx, s in enumerate(S):
        s['score'] = sum(util[f][idx] for f in COMPOSITE) / len(COMPOSITE) * 100

    a_pop = [s for s in S if s['ha'] is not None]
    b_pop = [s for s in S if s['hb'] is not None]
    ND = {'L2': 2, 'LRUN': 1, 'LRATIO': 2, 'BD': 3, 'DP': 2, 'WR3': 2, 'YR3': 2, 'VUP': 2}
    dh = (pad('桶', 5) + pad('因子值域', 18) + pad('n', 8) + pad('中位', 9) + pad('P90', 9)
          + pad('≥20%', 9) + pad('≥50%', 9) + pad('未盈利', 11))

    print()
    print('表1 单因子十分位（口径A：截面日收盘买入，120日最高价；检验分桶单调性）')
    for f in FACTORS:
        vals = [s['fac'][f] for s in a_pop]
        buckets = deciles(vals)
        print()
        print(f"── {f}  {FDESC[f]}")
        print(dh)
        for bi, idxs in enumerate(buckets, 1):
            lst = [dict(h=a_pop[x]['ha'], t=a_pop[x]['ta']) for x in idxs]
            print(pad(f'D{bi}', 5) + pad(fmt_rng(vals, idxs, ND[f]), 18) + hcells(lst))

    sh = (pad('桶', 5) + pad('分数域', 14) + pad('n', 8) + pad('中位', 9) + pad('P90', 9)
          + pad('≥20%', 9) + pad('≥50%', 9) + pad('未盈利', 11))
    print()
    print('表2 合成评分 SECOND_WAVE_SCORE 十分位（0~100，秩效用等权均值，无拟合权重）')
    for tag, pop, kh, kt in (('A 直接持有', a_pop, 'ha', 'ta'), ('B 等R5n触发', b_pop, 'hb', 'tb')):
        sc = [s['score'] for s in pop]
        buckets = deciles(sc)
        print()
        print(f"── 口径{tag}（有效 n={len(pop)}）")
        print(sh)
        for bi, idxs in enumerate(buckets, 1):
            lst = [dict(h=pop[x][kh], t=pop[x][kt]) for x in idxs]
            print(pad(f'D{bi}', 5) + pad(fmt_rng(sc, idxs, 1), 14) + hcells(lst))

    print()
    print('表3 分年度稳健性（评分底20% vs 顶20% vs 全样本；格 = n|中位|≥20%）')
    for tag, pop, kh, kt in (('A 直接持有', a_pop, 'ha', 'ta'), ('B 等R5n触发', b_pop, 'hb', 'tb')):
        pop2 = sorted(pop, key=lambda s: s['score'])
        q = max(1, len(pop2) // 5)
        top, bot = pop2[-q:], pop2[:q]
        print()
        print(f"── 口径{tag}（顶20% n={len(top)}  底20% n={len(bot)}）")
        print(pad('年份', 8) + pad('底20%', 24) + pad('顶20%', 24) + pad('全样本', 24))
        for yr in YEARS:
            ys = [s for s in pop if s['y'] == yr]
            ytop = [s for s in top if s['y'] == yr]
            ybot = [s for s in bot if s['y'] == yr]
            print(pad(yr, 8)
                  + pad(cell3([dict(h=s[kh], t=s[kt]) for s in ybot]), 24)
                  + pad(cell3([dict(h=s[kh], t=s[kt]) for s in ytop]), 24)
                  + pad(cell3([dict(h=s[kh], t=s[kt]) for s in ys]), 24))

    print()
    print('表4 对照锚与全样本（120日最高价）')
    print((pad('组', 16) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('均值', 9)
           + pad('≥10%', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('≥50%', 9) + pad('未盈利', 11) + pad('达峰中位', 9)))
    print(full_row('R5n全样本锚', ANCH['R5n']))
    print(full_row('R1全样本锚', ANCH['R1']))
    print(full_row('W7截面A全样本', [dict(h=s['ha'], t=s['ta']) for s in a_pop]))
    print(full_row('W7截面B全样本', [dict(h=s['hb'], t=s['tb']) for s in b_pop]))
    a_top = sorted(a_pop, key=lambda s: s['score'])[-max(1, len(a_pop) // 5):]
    b_top = sorted(b_pop, key=lambda s: s['score'])[-max(1, len(b_pop) // 5):]
    print(full_row('W7评分顶20%-A', [dict(h=s['ha'], t=s['ta']) for s in a_top]))
    print(full_row('W7评分顶20%-B', [dict(h=s['hb'], t=s['tb']) for s in b_top]))

    print()
    print('[口径备注] A=截面日收盘直接买入；B=该状态下等R5n触发日收盘买入（破位先行则弃）。'
          '同一天量的多个截面样本共享同一触发结果，B 存在同天量聚集，读表时看相对差异而非绝对 n。')
    print('[判定基准] 存档 R5n 全样本：中位22.1% / ≥20% 53.7% / P90 80.0%（本批重算见表4，允许微漂移）；'
          '口径B 顶20% 相对锚有边际增量，W7 才值得进生产端。')


if __name__ == '__main__':
    main()