# -*- coding: utf-8 -*-
"""
120 日最大涨幅回测（Python / tushare 缓存版）
移植自 tdx_maxgain_120d.js，数据源切换为 stock_cache.py 缓存体系。

与 Node 版的口径差异（均为切换后生产环境的真实行为，非遗漏）：
  1. running-max 自 BT_START('20210101') 起算：缓存历史即生产可见历史，
     2021 年初候选密度偏高属冷启动伪影，以 Node 表5-2021 对照量化；
  2. 流通股本用 py_float_share.json 当前快照套全历史，与 Node LTAG 快照同构；
  3. 涨停判定用 pre_close（tushare 昨收口径，除权日更准），与 daily_pullback.py 生产引擎一致。
除权检测沿用 0.85 启发式：天量日前 60 根剔除 + 入场后 120 日窗口内隔离（exwin）。
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
GRPS = ['all', 'strong', 'weak', 'ex', 'exwin']
MAIN = ['R1', 'R2', 'R3', 'R4', 'R5', 'R5n', 'B_close', 'B_next']
ALL_RULES = MAIN + ['R1_40', 'R1_60', 'R5_40', 'R5_60', 'R5n_40', 'R5n_60']
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]

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
    if rule in ('R5', 'R5_40', 'R5_60'):
        lock = 0.5
        if rule == 'R5_40':
            lock = 0.4
        elif rule == 'R5_60':
            lock = 0.6
        V, vdH = bars[i][5], bars[i][2]
        quiet = True
        for t in range(i + 1, min(i + R1_WINDOW, n - 1) + 1):
            if t >= i + 3 and quiet and bars[t][4] > vdH and bars[t][4] > bars[t][1] and bars[t][5] <= V:
                return t
            if bars[t][5] > V * lock:
                quiet = False
                break
        return -1
    if rule in ('R5n', 'R5n_40', 'R5n_60'):
        lock = 0.5
        if rule == 'R5n_40':
            lock = 0.4
        elif rule == 'R5n_60':
            lock = 0.6
        V, vdH = bars[i][5], bars[i][2]
        for t in range(i + 3, min(i + R1_WINDOW, n - 1) + 1):
            o, c, v = bars[t][1], bars[t][4], bars[t][5]
            if c > vdH and c > o and v <= V and bars[t - 1][5] <= V * lock and bars[t - 2][5] <= V * lock:
                return t
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


rec = {r: {g: {'list': [], 'cen': 0} for g in GRPS} for r in ALL_RULES}


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


def summC(lst):
    n = len(lst)
    if not n:
        return None
    cs = sorted(r['c'] for r in lst)
    return dict(n=n, med=pct(cs, 0.5), p75=pct(cs, 0.75), p90=pct(cs, 0.9),
                a20=sum(1 for r in lst if r['c'] >= 0.20) / n,
                a30=sum(1 for r in lst if r['c'] >= 0.30) / n)


def feed(rule, entry_bar, start_j, entry, groups, n, bars, vd_i):
    end = entry_bar + W
    if end > n - 1:
        for g in groups:
            rec[rule][g]['cen'] += 1
        return
    mh, mc, mt = float('-inf'), float('-inf'), 0
    for j in range(start_j, end + 1):
        if bars[j][2] > mh:
            mh = bars[j][2]
            mt = j - entry_bar
        if bars[j][4] > mc:
            mc = bars[j][4]
    item = dict(y=int(bars[vd_i][0][:4]), h=mh / entry - 1, c=mc / entry - 1, t=mt)
    for g in groups:
        rec[rule][g]['list'].append(item)


def main():
    fl_map = load_float_map()
    idx_map = load_index_ma20()
    cnt = dict(notstock=0, cap0=0, short=0, ex=0, exwin=0, dup=0, noidx=0, lu=0)
    samples = 0

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
                window_ex = False
                for j in range(i + 1, min(i + W, n - 1) + 1):
                    if bars[j][1] < bars[j - 1][4] * 0.85 and bars[j][5] > bars[j - 1][5] * 0.2:
                        window_ex = True
                        break
                if not ex and window_ex:
                    cnt['exwin'] += 1
                env_grp = None
                if not ex:
                    idrec = idx_map.get(bars[i][0])
                    if idrec and idrec[1] is not None:
                        env_grp = 'strong' if idrec[0] > idrec[1] else 'weak'
                    else:
                        cnt['noidx'] += 1
                groups = ['ex'] if ex else (['exwin'] if window_ex else (['all', env_grp] if env_grp else ['all']))

                if not is_limit_up_close(bars, i, code):
                    feed('B_close', i, i + 1, bars[i][4], groups, n, bars, i)
                else:
                    cnt['lu'] += 1
                if not is_limit_up_open(bars, i + 1, code):
                    feed('B_next', i + 1, i + 1, bars[i + 1][1], groups, n, bars, i)
                for rule in ('R1', 'R2', 'R3', 'R4', 'R5', 'R5n', 'R1_40', 'R1_60', 'R5_40', 'R5_60', 'R5n_40', 'R5n_60'):
                    t = find_trigger(rule, bars, i, n)
                    if t < 0:
                        continue
                    if is_limit_up_close(bars, t, code):
                        cnt['lu'] += 1
                        continue
                    feed(rule, t, t + 1, bars[t][4], groups, n, bars, i)
                if not ex and not window_ex:
                    samples += 1

    print(f"[样本] 干净天量日事件={samples}（换手率自 {BT_START} 起严格创新高、连续新高10根内去重、天量日前60根及入场后120日内均无除权缺口）")
    print(f"[剔除/隔离] 天量日前除权={cnt['ex']} 窗口内除权={cnt['exwin']} 连续新高去重={cnt['dup']} 买入日涨停不可成交={cnt['lu']} 指数对齐缺失={cnt['noidx']}")
    print('[删失] 天量日后不足120个交易日(不计入分布): ' + ' '.join(f"{r}={rec[r]['all']['cen']}" for r in MAIN))

    print('\n表1 120日内最高涨幅分布（最高价口径=理想出场；入场价: B*=天量日收盘/次日开盘, R*=触发日收盘）:')
    print(pad('规则', 9) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('均值', 9) + pad('≥10%', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('≥50%', 9) + pad('全程未盈利', 11) + pad('中位达峰', 9))
    for r in MAIN:
        u = summ(rec[r]['all']['list'])
        if not u:
            print(pad(r, 9) + pad('-', 8))
            continue
        print(pad(r, 9) + pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['p75']), 9) + pad(f1(u['p90']), 9) + pad(f1(u['mean']), 9) + pad(f1(u['a10']), 9) + pad(f1(u['a20']), 9) + pad(f1(u['a30']), 9) + pad(f1(u['a50']), 9) + pad(f1(u['neg']), 11) + pad(f"{u['medt']}日", 9))

    print('\n表2 收盘价口径（最高收盘=可实现性更强的出场代理，不需精确卖在最高点）:')
    print(pad('规则', 9) + pad('n', 8) + pad('中位', 9) + pad('P75', 9) + pad('P90', 9) + pad('≥20%', 9) + pad('≥30%', 9))
    for r in MAIN:
        u = summC(rec[r]['all']['list'])
        if not u:
            print(pad(r, 9) + pad('-', 8))
            continue
        print(pad(r, 9) + pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['p75']), 9) + pad(f1(u['p90']), 9) + pad(f1(u['a20']), 9) + pad(f1(u['a30']), 9))

    print('\n表3 环境分组（天量日当日上证 vs MA20；格=中位|≥20%，最高价口径）:')
    for g, label in (('all', '全样本'), ('strong', '上证在MA20上方'), ('weak', '上证在MA20下方')):
        print(f'-- {label} --')
        print(pad('规则', 9) + pad('n', 8) + pad('中位', 9) + pad('≥20%', 9) + pad('≥30%', 9) + pad('全程未盈利', 11))
        for r in MAIN:
            u = summ(rec[r][g]['list'])
            if not u:
                print(pad(r, 9) + pad('-', 8))
                continue
            print(pad(r, 9) + pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['a20']), 9) + pad(f1(u['a30']), 9) + pad(f1(u['neg']), 11))

    print('\n表4 窗口内除权失真影响（不复权最高价会被除权压低，验证剔除必要性；格=中位|≥20%）:')
    print(pad('规则', 9) + pad('干净n', 9) + pad('干净中位|≥20%', 20) + pad('窗口除权n', 10) + pad('除权中位|≥20%', 20))
    for r in MAIN:
        a = summ(rec[r]['all']['list'])
        e = summ(rec[r]['exwin']['list'])
        print(pad(r, 9) + pad(a['n'] if a else '-', 9) + pad(f"{f1(a['med'])}|{f1(a['a20'])}" if a else '-', 20) + pad(e['n'] if e else '-', 10) + pad(f"{f1(e['med'])}|{f1(e['a20'])}" if e else '-', 20))

    print('\n表5 年度稳健性（R1/R5/R5n/B_close 按天量日年份；格=n|中位|≥20%）:')
    print(pad('规则', 9) + ''.join(pad(str(y), 20) for y in YEARS))
    for r in ('R1', 'R5', 'R5n', 'B_close'):
        cells = [pad(r, 9)]
        for y in YEARS:
            u = summ([x for x in rec[r]['all']['list'] if x['y'] == y])
            cells.append(pad(f"{u['n']}|{f1(u['med'])}|{f1(u['a20'])}" if u else '-', 20))
        print(''.join(cells))

    print('\n表6 缩量阈值参数平台（检验参数稳健性；格=中位|P90|≥20%|≥30%）:')
    for r, label in (('R1_40', 'R1@量≤40%天量'), ('R1', 'R1@量≤50%天量'), ('R1_60', 'R1@量≤60%天量'),
                     ('R5_40', 'R5@锁筹≤40%天量'), ('R5', 'R5@锁筹≤50%天量'), ('R5_60', 'R5@锁筹≤60%天量'),
                     ('R5n_40', 'R5n@近端锁筹≤40%'), ('R5n', 'R5n@近端锁筹≤50%'), ('R5n_60', 'R5n@近端锁筹≤60%')):
        u = summ(rec[r]['all']['list'])
        print(pad(label, 15) + (pad(u['n'], 8) + pad(f1(u['med']), 9) + pad(f1(u['p90']), 9) + pad(f1(u['a20']), 9) + pad(f1(u['a30']), 9) if u else pad('-', 8)))

    print('\n表7 达峰时间分布（窗口内最高价出现在入场后第几个交易日，占比）:')
    print(pad('规则', 9) + pad('≤5日', 10) + pad('6~20日', 10) + pad('21~60日', 10) + pad('61~120日', 10))
    for r in ('R1', 'R5', 'B_close'):
        L = rec[r]['all']['list']
        n = len(L)
        if not n:
            print(pad(r, 9) + pad('-', 10))
            continue
        b5 = sum(1 for x in L if x['t'] <= 5) / n
        b20 = sum(1 for x in L if 5 < x['t'] <= 20) / n
        b60 = sum(1 for x in L if 20 < x['t'] <= 60) / n
        b120 = sum(1 for x in L if x['t'] > 60) / n
        print(pad(r, 9) + pad(f1(b5), 10) + pad(f1(b20), 10) + pad(f1(b60), 10) + pad(f1(b120), 10))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('FATAL', e, file=sys.stderr)
        sys.exit(1)
