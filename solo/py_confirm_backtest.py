# -*- coding: utf-8 -*-
"""
R1 确认回测（Python / tushare 缓存版）
移植自 tdx_confirm_backtest.js，数据源切换为 stock_cache.py 缓存体系。

与 Node 版的口径差异（均为切换后生产环境的真实行为，非遗漏）：
  1. running-max 自 BT_START('20210101') 起算：缓存历史即生产可见历史；
  2. 流通股本用 py_float_share.json 当前快照套全历史，与 Node LTAG 快照同构；
  3. 涨停判定用 pre_close（tushare 昨收口径，除权日更准），与 daily_pullback.py 生产引擎一致。
表4 实时窗口与 Node 同参（TODAY/WIN 同值），用于引擎级对照。
"""
import sys
import os
import json
import math

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from stock_cache import get_conn, load_stock_basic

BT_START = '20210101'
MIN_BARS = 280
VD_MIN_I = 60
VD_GAP = 10
R1_WINDOW = 20
KS = [1, 3, 5, 10, 20]
GRPS = ['all', 'strong', 'weak', 'ex']
RULES = ['R1', 'R2', 'R3', 'R4', 'B_close', 'B_next']
TODAY = '20260828'
WIN = '20260729'

ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX_JSON = os.path.join(ROOT, 'py_index_000001SH.json')
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


def f2(x):
    return f'{x:.2f}'


def load_names():
    sb = load_stock_basic()
    if sb is None or sb.empty:
        return {}
    return dict(zip(sb['ts_code'], sb['name'].astype(str)))


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


def load_index_ma20():
    if not os.path.exists(INDEX_JSON):
        return {}
    try:
        with open(INDEX_JSON, encoding='utf-8') as f:
            rows = json.load(f).get('rows', {})
    except Exception:
        return {}
    ds = sorted(rows)
    out = {}
    ssum = 0.0
    for i, d in enumerate(ds):
        ssum += rows[d]
        if i >= 20:
            ssum -= rows[ds[i - 20]]
        out[d] = (rows[d], ssum / 20 if i >= 19 else None)
    return out


def find_trigger(rule, bars, i, n):
    thr = 0.5
    if rule == 'R1_40':
        thr = 0.4
    elif rule == 'R1_60':
        thr = 0.6
    if rule in ('R1', 'R1_40', 'R1_60'):
        C, L, V = bars[i][4], bars[i][3], bars[i][5]
        for j in range(i + 1, min(i + R1_WINDOW, n - 1) + 1):
            o, c, v = bars[j][1], bars[j][4], bars[j][5]
            if c > o and v <= V * thr and c < C and c > L:
                return j
        return -1
    if rule == 'R2':
        H, V = bars[i][2], bars[i][5]
        for j in range(i + 1, min(i + R1_WINDOW, n - 1) + 1):
            if bars[j][4] > H and bars[j][5] <= V:
                return j
        return -1
    if rule == 'R3':
        broke = False
        for j in range(i + 1, min(i + R1_WINDOW, n - 1) + 1):
            ma5 = sum(bars[x][4] for x in range(j - 4, j + 1)) / 5
            if not broke:
                if bars[j][4] < ma5:
                    broke = True
                continue
            ma5p = sum(bars[x][4] for x in range(j - 5, j)) / 5
            if bars[j][4] > ma5 and ma5 > ma5p:
                return j
        return -1
    if rule == 'R4':
        V = bars[i][5]
        quiet = 0
        for j in range(i + 1, min(i + R1_WINDOW, n - 1) + 1):
            v, c, o = bars[j][5], bars[j][4], bars[j][1]
            if v <= V * 0.6:
                quiet += 1
                continue
            if quiet >= 3:
                s = sum(bars[x][5] for x in range(j - 5, j))
                if v >= (s / 5) * 1.8 and c > o and c > bars[j - 1][4]:
                    return j
            quiet = 0
        return -1
    return -1


def is_limit_up_close(bars, idx, code):
    if idx < 1:
        return False
    prev = bars[idx][6] or bars[idx - 1][4]
    return bars[idx][4] / prev - 1 >= limit_of(code) - 0.003


def is_limit_up_open(bars, idx, code):
    if idx < 1:
        return False
    prev = bars[idx][6] or bars[idx - 1][4]
    return bars[idx][1] / prev - 1 >= limit_of(code) - 0.003


stat = {r: {g: {k: dict(n=0, win=0, s=0.0, rets=[], win_sum=0.0, lose_sum=0.0)
            for k in KS} for g in GRPS} for r in RULES}


def add_stat(rule, grp, k, ret):
    b = stat[rule][grp][k]
    b['n'] += 1
    b['s'] += ret
    b['rets'].append(ret)
    if ret > 0:
        b['win'] += 1
        b['win_sum'] += ret
    else:
        b['lose_sum'] += ret


def feed(rule, t, entry, groups, n, bars):
    for k in KS:
        if t + k > n - 1:
            continue
        ret = bars[t + k][4] / entry - 1
        for g in groups:
            add_stat(rule, g, k, ret)


def main():
    names = load_names()
    fl_map = load_float_map()
    idx_map = load_index_ma20()
    cnt = dict(notstock=0, cap0=0, short=0, ex=0, dup=0, noidx=0, lu=0)
    samples = 0
    live = []

    with get_conn() as conn:
        all_codes = [r[0] for r in conn.execute('SELECT DISTINCT ts_code FROM daily_cache')]
        stock_codes = sorted(c for c in all_codes if is_stock(c))
        cnt['notstock'] = len(all_codes) - len(stock_codes)
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
                env_grp = None
                if not ex:
                    idrec = idx_map.get(bars[i][0])
                    if idrec and idrec[1] is not None:
                        env_grp = 'strong' if idrec[0] > idrec[1] else 'weak'
                    else:
                        cnt['noidx'] += 1
                groups = ['ex'] if ex else (['all', env_grp] if env_grp else ['all'])

                if not is_limit_up_close(bars, i, code):
                    feed('B_close', i, bars[i][4], groups, n, bars)
                else:
                    cnt['lu'] += 1
                if not is_limit_up_open(bars, i + 1, code):
                    feed('B_next', i + 1, bars[i + 1][1], groups, n, bars)
                for rule in ('R1', 'R2', 'R3', 'R4'):
                    t = find_trigger(rule, bars, i, n)
                    if t < 0:
                        continue
                    if is_limit_up_close(bars, t, code):
                        cnt['lu'] += 1
                        continue
                    feed(rule, t, bars[t][4], groups, n, bars)
                if not ex:
                    samples += 1
                if not ex and bars[n - 1][0] == TODAY and bars[i][0] >= WIN and n >= 250:
                    ma = sum(bars[x][4] for x in range(n - 250, n)) / 250
                    last = bars[n - 1]
                    if last[4] < ma and last[4] > last[1]:
                        st = {}
                        for rule in ('R1', 'R2', 'R3', 'R4'):
                            t = find_trigger(rule, bars, i, n)
                            st[rule] = (bars[t][0], bars[t][4]) if t >= 0 else None
                        live.append(dict(code=code, name=names.get(code, ''), i=i, st=st, bars=bars))

    print(f"[样本] 干净天量日样本={samples}（换手率自 {BT_START} 起严格创新高、连续新高10根内去重、天量日前60根无除权缺口）")
    print(f"[剔除] 除权失真样本={cnt['ex']} 连续新高去重={cnt['dup']} 买入日涨停不可成交={cnt['lu']} 指数对齐缺失={cnt['noidx']} 数据不足={cnt['short']}")
    print('\n表1 全部干净样本（格=样本数|胜率|平均收益%）:')
    print(pad('规则', 9) + ''.join(pad(f'{k}日', 17) for k in KS))
    for r in RULES:
        cells = [pad(r, 9)]
        for k in KS:
            b = stat[r]['all'][k]
            cells.append(pad(f"{b['n']}|{f1(b['win'] / b['n'])}|{b['s'] / b['n'] * 100:.1f}" if b['n'] else '-', 17))
        print(''.join(cells))

    print('\n表2 明细（10日/20日 格=胜率|均值%|中位%|盈亏比）:')
    for g, label in (('all', '全样本'), ('strong', '天量日时上证在MA20上方'), ('weak', '天量日时上证在MA20下方')):
        print(f'-- {label} --')
        print(pad('规则', 9) + pad('10日', 32) + pad('20日', 32))
        for r in RULES:
            cells = [pad(r, 9)]
            for k in (10, 20):
                b = stat[r][g][k]
                if not b['n']:
                    cells.append(pad('-', 32))
                    continue
                srt = sorted(b['rets'])
                med = srt[len(srt) // 2]
                wn, ln = b['win'], b['n'] - b['win']
                pl = (b['win_sum'] / wn) / abs(b['lose_sum'] / ln) if wn > 0 and ln > 0 else math.inf
                cells.append(pad(f"{f1(b['win'] / b['n'])}|{b['s'] / b['n'] * 100:.1f}|{med * 100:.1f}|{f'{pl:.2f}' if math.isfinite(pl) else 'INF'}", 32))
            print(''.join(cells))

    print('\n表3 除权失真样本 vs 干净样本（10日胜率对比，验证失真危害）:')
    for r in RULES:
        a, e = stat[r]['all'][10], stat[r]['ex'][10]
        print(pad(r, 9)
              + pad(f"干净:{f1(a['win'] / a['n']) if a['n'] else '-'}", 14)
              + pad(f"除权:{f1(e['win'] / e['n']) if e['n'] else '-'}", 14))

    by_code = {}
    for v in live:
        p = by_code.get(v['code'])
        if p is None or v['i'] > p['i']:
            by_code[v['code']] = v
    lv = sorted(by_code.values(), key=lambda x: x['code'])
    print(f"\n表4 当前窗口天量票({len(lv)}只)的确认买点状态（✓=已触发@日期(入场价) / 待=观察中）:")
    print(pad('代码', 8) + pad('名称', 11) + pad('天量日', 10) + pad('天量收', 8) + pad('天量高', 8) + pad('天量低', 8) + pad('现价', 8) + pad('R1', 20) + pad('R2', 20) + pad('R3', 18) + pad('R4', 18))
    for v in lv:
        vb = v['bars']
        H, L, C = vb[v['i']][2], vb[v['i']][3], vb[v['i']][4]
        last = vb[-1]

        def st(r, v=v):
            x = v['st'][r]
            return f"✓{x[0]}({f2(x[1])})" if x else '待'

        print(pad(v['code'], 8) + pad(v['name'], 11) + pad(vb[v['i']][0], 10) + pad(f2(C), 8) + pad(f2(H), 8) + pad(f2(L), 8) + pad(f2(last[4]), 8) + pad(st('R1'), 20) + pad(st('R2'), 20) + pad(st('R3'), 18) + pad(st('R4'), 18))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('FATAL', e, file=sys.stderr)
        sys.exit(1)
