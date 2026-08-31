# -*- coding: utf-8 -*-
"""
每日天量回调选股（Python / tushare 缓存版）

算法移植自 tdx_daily_pullback.js（tdx 本地版保留作回归对照），数据源切换为
stock_cache.py 的 tushare 缓存体系：

  1. 行情扫描：daily_cache 窄表（ts_code,trade_date,open,high,low,close,
     pre_close,vol 等 11 列），有效历史自 2021-01-01 起（HISTORY_START），
     走 idx_daily_code_date 索引逐只点查，避开 stk_factor_pro 261 列宽表。
  2. 流通股本：pro.daily_basic 全市场一次拉取当日快照（float_share，万股），
     本地 py_float_share.json 按日期缓存，缺股回退最近一次快照。
     换手率(%) = vol(手) / float_share(万股)，与 Node 版"当前股本套全历史"一致。
  3. 除权检测：候选股窄查询 stk_factor_pro 的 adj_factor，相邻因子相对变动
     > 0.5%（EXR_TOL）视为除权；因子覆盖不全时退回 Node 版 0.85 跳空启发式。
  4. 上证环境：pro.index_daily('000001.SH')，本地 py_index_000001SH.json
     增量缓存，计算 MA20 强弱分区。

用法：
  python daily_pullback.py             # 默认最近一个已收盘交易日
  python daily_pullback.py -d 20260828
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from stock_cache import get_conn, _get_pro, load_stock_basic, get_effective_date

HISTORY_START = '20210101'
MIN_BARS = 280
VD_MIN_I = 60
VD_GAP = 10
MAX_AGE = 20
R1_WINDOW = 20
LOCK_THR = 0.5
EXR_TOL = 0.005

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


def load_float_share(pro, eff):
    data = {}
    if os.path.exists(FLOAT_JSON):
        try:
            with open(FLOAT_JSON, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    snap = data.get(eff)
    if snap is None:
        snap = {}
        offset = 0
        while True:
            df = pro.daily_basic(trade_date=eff, fields='ts_code,float_share', offset=offset)
            if df is None or df.empty:
                break
            for tc, v in zip(df['ts_code'], df['float_share']):
                snap[tc] = float(v) if v == v else 0.0
            if len(df) < 5000:
                break
            offset += 5000
            time.sleep(0.12)
        if snap:
            data[eff] = snap
            if len(data) > 15:
                for d in sorted(data)[:-15]:
                    data.pop(d, None)
            with open(FLOAT_JSON, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
    out = dict(snap)
    earlier = sorted((d for d in data if d < eff), reverse=True)
    for c, v in out.items():
        if not v or v <= 0:
            for d in earlier:
                v2 = data[d].get(c)
                if v2:
                    out[c] = v2
                    break
    return out


def load_index_closes(pro, eff):
    rows = {}
    if os.path.exists(INDEX_JSON):
        try:
            with open(INDEX_JSON, encoding='utf-8') as f:
                rows = json.load(f).get('rows', {})
        except Exception:
            rows = {}
    mx = max(rows) if rows else ''
    if not mx or mx < eff:
        start = (datetime.strptime(mx, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d') if mx else HISTORY_START
        offset = 0
        while True:
            df = pro.index_daily(ts_code='000001.SH', start_date=start, end_date=eff,
                                 fields='trade_date,close', offset=offset)
            if df is None or df.empty:
                break
            for d, c in zip(df['trade_date'], df['close']):
                rows[d] = float(c)
            if len(df) < 300:
                break
            offset += 300
            time.sleep(0.12)
        with open(INDEX_JSON, 'w', encoding='utf-8') as f:
            json.dump({'ts_code': '000001.SH', 'rows': rows}, f, ensure_ascii=False)
    return {d: c for d, c in rows.items() if d <= eff}


def fetch_bars(conn, code):
    return conn.execute(
        'SELECT trade_date, open, high, low, close, vol, pre_close '
        'FROM daily_cache WHERE ts_code=? AND trade_date>=? ORDER BY trade_date',
        (code, HISTORY_START)).fetchall()


def fetch_adj_factors(conn, code):
    return dict(conn.execute(
        'SELECT trade_date, adj_factor FROM stk_factor_pro '
        'WHERE ts_code=? AND trade_date>=? ORDER BY trade_date',
        (code, HISTORY_START)).fetchall())


def r5n_trigger(bars, vd, n):
    V, vdH = bars[vd][5], bars[vd][2]
    for t in range(vd + 3, min(vd + R1_WINDOW, n - 1) + 1):
        o, c, v = bars[t][1], bars[t][4], bars[t][5]
        if c > vdH and c > o and v <= V and bars[t - 1][5] <= V * LOCK_THR and bars[t - 2][5] <= V * LOCK_THR:
            return t
    return -1


def is_limit_up_close(bars, idx, code):
    if idx < 1:
        return False
    prev = bars[idx][6] or bars[idx - 1][4]
    return bars[idx][4] / prev - 1 >= limit_of(code) - 0.003


def ex_heuristic(bars, vd, n):
    for j in range(max(1, vd - 59), vd + 1):
        if bars[j][1] < bars[j - 1][4] * 0.85:
            return True
    for j in range(vd + 1, n):
        if bars[j][1] < bars[j - 1][4] * 0.85 and bars[j][5] > bars[j - 1][5] * 0.2:
            return True
    return False


def ex_divided(factors, bars, vd, n):
    pts = []
    prev = None
    for i, b in enumerate(bars):
        f = factors.get(b[0])
        if f is None:
            return ex_heuristic(bars, vd, n)
        if prev is not None and abs(f / prev - 1) > EXR_TOL:
            pts.append(i)
        prev = f
    lo = max(1, vd - 59)
    return any(lo <= p <= n - 1 for p in pts)


def main():
    ap = argparse.ArgumentParser(description='每日天量回调选股（tushare 缓存版）')
    ap.add_argument('-d', dest='date', default='', help='交易日 yyyymmdd，默认最近已收盘交易日')
    args = ap.parse_args()
    eff = get_effective_date(args.date)
    pro = _get_pro()

    names = load_names()
    fl_map = load_float_share(pro, eff)
    idx_close = load_index_closes(pro, eff)

    cnt = dict(notstock=0, cap0=0, nofile=0, short=0, susp=0, novd=0, stale=0, ex=0, brk=0)
    tiers = dict(green=[], yellow=[], blue=[])

    with get_conn() as conn:
        all_codes = [r[0] for r in conn.execute('SELECT DISTINCT ts_code FROM daily_cache')]
        stock_codes = sorted(c for c in all_codes if is_stock(c))
        cnt['notstock'] = len(all_codes) - len(stock_codes)

        total = len(stock_codes)
        print(f'扫描 {total} 只（自 {HISTORY_START}）...', file=sys.stderr)
        for k, code in enumerate(stock_codes, 1):
            if k % 1000 == 0:
                print(f'  {k}/{total}', file=sys.stderr)
            fl = fl_map.get(code, 0)
            if not fl or fl <= 0:
                cnt['cap0'] += 1
                continue
            bars = fetch_bars(conn, code)
            n = len(bars)
            if not n:
                cnt['nofile'] += 1
                continue
            if n < MIN_BARS:
                cnt['short'] += 1
                continue
            if bars[-1][0] != eff:
                cnt['susp'] += 1
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
                    continue
                picks.append(i)
                last_pick = i
            if not picks:
                cnt['novd'] += 1
                continue
            vd = picks[-1]
            age = n - 1 - vd
            if age > MAX_AGE:
                cnt['stale'] += 1
                continue

            if ex_divided(fetch_adj_factors(conn, code), bars, vd, n):
                cnt['ex'] += 1
                continue

            vdV, vdC, vdL, vdH = bars[vd][5], bars[vd][4], bars[vd][3], bars[vd][2]
            if any(bars[j][4] < vdL for j in range(vd + 1, n)):
                cnt['brk'] += 1
                continue

            lastb = bars[-1]
            t = r5n_trigger(bars, vd, n)
            item = {
                'code': code,
                'name': names.get(code, ''),
                'vdDate': bars[vd][0],
                'age': age,
                'hsl': vdV / (fl * 100),
                'vdC': vdC,
                'vdL': vdL,
                'vdH': vdH,
                'px': lastb[4],
                'depth': lastb[4] / vdC - 1,
                'vr': lastb[5] / vdV,
                'status': '',
                'note': '',
            }
            if t == n - 1:
                item['status'] = '★今日突破'
                if is_limit_up_close(bars, t, code):
                    item['note'] = '▲今日涨停难买'
                tiers['green'].append(item)
            elif t >= n - 6:
                item['status'] = f'✓{n - 1 - t}日前突破'
                tag = '▲突破日涨停@' if is_limit_up_close(bars, t, code) else '突破@'
                item['note'] = tag + bars[t][0][4:]
                tiers['yellow'].append(item)
            elif lastb[4] <= vdH and lastb[5] <= vdV * LOCK_THR and bars[-2][5] <= vdV * LOCK_THR:
                item['status'] = '○锁筹待突破'
                item['note'] = ('曾突破@' + bars[t][0][4:]) if t >= 0 else '近2日锁筹≤50%'
                tiers['blue'].append(item)

    for arr in tiers.values():
        arr.sort(key=lambda x: x['vr'])

    env_line = '指数数据缺失'
    if idx_close:
        ds = sorted(idx_close)
        if len(ds) >= 20:
            ma = sum(idx_close[d] for d in ds[-20:]) / 20
            c = idx_close[ds[-1]]
            env_line = f'上证 {c:.2f} / MA20 {ma:.2f} → {"强势区" if c > ma else "弱势区"}'

    cand = len(stock_codes) - cnt['cap0']
    print(f'[每日天量回调选股] 数据截至 {eff}')
    print(f'[环境] {env_line}')
    print('[口径] 天量=换手率创本地历史新高且距今≤20个交易日 | 锁筹=近2日量均≤天量50% | 确认(R5n)=天量后≥3日·放量阳线收盘突破天量日高点且突破日量≤天量 | 观察区=近2日锁筹未破位未突破 | 失效=收盘破天量日低 | 已剔除除权失真与停牌')
    print(f"[扫描] 候选={cand} 无天量={cnt['novd']} 天量过旧(>20日)={cnt['stale']} 除权失真={cnt['ex']} 中途破位={cnt['brk']} 停牌={cnt['susp']} 数据不足={cnt['short']} 缺文件={cnt['nofile']}")

    def print_tier(title, arr):
        print(f'\n{title}：{len(arr)}只（按量比升序，缩量越充分越靠前）')
        if not arr:
            print('  （无）')
            return
        print(pad('代码', 8) + pad('名称', 11) + pad('天量日', 10) + pad('距(日)', 7) + pad('天量换手', 9)
              + pad('天量收', 8) + pad('天量高', 8) + pad('天量低', 8) + pad('现价', 8) + pad('深度', 8) + pad('量比', 7)
              + pad('状态', 14) + '备注')
        for v in arr:
            print(pad(v['code'], 8) + pad(v['name'], 11) + pad(v['vdDate'], 10) + pad(v['age'], 7)
                  + pad(f1(v['hsl']), 9) + pad(f2(v['vdC']), 8) + pad(f2(v['vdH']), 8) + pad(f2(v['vdL']), 8) + pad(f2(v['px']), 8)
                  + pad(f1(v['depth']), 8) + pad(f2(v['vr']), 7) + pad(v['status'], 14) + v['note'])

    print_tier('★ 今日突破确认（R5n触发·可执行）', tiers['green'])
    print_tier('✓ 近5日已突破（持有跟踪）', tiers['yellow'])
    print_tier('○ 锁筹待突破（观察区）', tiers['blue'])

    print('\n[纪律] ①观察区票等触发再动手：近2日锁筹≤天量50%+放量阳线收盘突破天量日高点（突破日量≤天量，不创天量新高）；②失效铁律：收盘跌破天量日低点无条件离场；③R5n确认后120日回测（2021起新历史基线，n=4042）：最高涨幅中位22.1%/P90 80.0%、≥20%概率53.7%、≥50%概率22.2%、全程未盈利仅1.9%、达峰中位34日→分批止盈、给足耐心，信号较R1约减半更精；④确认日涨停的次日勿追高开；⑤锁筹阈值40%-60%平台稳健，突破日若放量超天量属天量接天量，另行评估。')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('FATAL', e, file=sys.stderr)
        sys.exit(1)
