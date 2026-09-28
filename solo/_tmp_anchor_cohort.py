# -*- coding: utf-8 -*-
"""起量台阶 × VSW 事件池 交叉检验（一次性研究）：
   回答「把起量台阶结构加入策略是否能提升胜率」。
   事件池 = _tmp_vsw_cohort.csv（VSW 基础硬过滤通过的事件，含 ret）
   锚点口径同 _tmp_anchor_scan.py：ratio=ma5/前5日均量 >=1.5 且 当日量/20日均量>=2
"""
import sqlite3
import numpy as np
import pandas as pd

DB = r'd:\mystock\cache_daily\stock_data.db'
EV = r'd:\mystock\solo\_tmp_vsw_cohort.csv'
RATIO_JUMP, VOL20_MIN, KEEP_MIN, MAX_D = 1.5, 2.0, 1.0, 15


def stat(df, name):
    if len(df) == 0:
        print(f'{name}: n=0')
        return
    r = df['ret']
    print(f'{name}: n={len(df):6d} 胜率={(r > 0).mean() * 100:5.1f}% 均={r.mean():+6.2f}% '
          f'大赢={((r >= 5).mean() * 100):4.1f}% 止损={((r <= -6.75).mean() * 100):4.1f}%')


SER = {}
SER2 = {}


def _reflag(df, keep_min, max_d):
    """按给定 (台阶维持下限, 窗口最大天数) 重算 in_win，用于敏感度检验。"""
    out = []
    for code, dt in zip(df['ts_code'], df['date']):
        dates, ratio, anc = SER[code]
        k = int(np.searchsorted(dates, dt))
        hit = 0
        for i in anc:
            if i >= k:
                break
            if k - i > max_d:
                continue
            seg = ratio[i + 1:k + 1]
            if np.isnan(seg).any() or seg.min() < keep_min:
                continue
            hit = 1
        out.append(hit)
    return np.array(out)


def _reflag_step(df, step_min, max_d, start_off=0):
    """台阶口径二：起量后（默认含起量日，start_off=1 则从次日起）
       每日 ma5 均 >= 起量前一日 ma5 × step_min（用户原话的绝对台阶）。"""
    out = []
    for code, dt in zip(df['ts_code'], df['date']):
        dates, anc, ma5 = SER2[code]
        k = int(np.searchsorted(dates, dt))
        hit = 0
        for i in anc:
            if i >= k:
                break
            if k - i > max_d or i - 1 < 0 or not (ma5[i - 1] > 0):
                continue
            seg = ma5[i + start_off:k + 1] / ma5[i - 1]
            if len(seg) == 0 or np.isnan(seg).any() or seg.min() < step_min:
                continue
            hit = 1
        out.append(hit)
    return np.array(out)


def main():
    ev = pd.read_csv(EV, encoding='utf-8-sig')
    ev['date'] = ev['date'].astype(str)
    ev['squat'] = ev['squat'].astype(str).str.lower().isin(['true', '1'])
    want = {}
    for code, dt in zip(ev['ts_code'], ev['date']):
        want.setdefault(code, set()).add(dt)

    con = sqlite3.connect(DB)
    d = pd.read_sql("SELECT ts_code,trade_date,close,vol FROM daily_cache "
                    "WHERE trade_date>='20240401' AND ts_code NOT LIKE '%.BJ'", con)
    con.close()
    d['trade_date'] = d['trade_date'].astype(str)
    d = d.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    g = d.groupby('ts_code')['vol']
    d['ma5'] = g.transform(lambda s: s.rolling(5).mean())
    d['ma5p'] = g.transform(lambda s: s.rolling(5).mean().shift(5))
    d['ma20'] = g.transform(lambda s: s.rolling(20).mean())
    d['ratio'] = d['ma5'] / d['ma5p']
    d['v20r'] = d['vol'] / d['ma20']

    flags = []
    for code, gg in d.groupby('ts_code', sort=False):
        if code not in want:
            continue
        dates = gg['trade_date'].values
        ratio = gg['ratio'].values
        v20r = gg['v20r'].values
        ma5 = gg['ma5'].values
        close = gg['close'].values.astype(float)
        prev = np.concatenate([[np.nan], ratio[:-1]])
        anc = list(np.where((ratio >= RATIO_JUMP) & (prev < RATIO_JUMP) & (v20r >= VOL20_MIN))[0])
        SER[code] = (dates, ratio, anc)
        SER2[code] = (dates, anc, ma5)
        for k, dt in enumerate(dates):
            if dt not in want[code]:
                continue
            hit = None
            for i in anc:
                if i >= k:
                    break
                dd = k - i
                if dd > MAX_D:
                    continue
                seg = ratio[i + 1:k + 1]
                if np.isnan(seg).any() or seg.min() < KEEP_MIN:
                    continue
                hit = i
            if hit is None:
                flags.append({'ts_code': code, 'date': dt, 'in_win': 0, 'd': np.nan, 'px': np.nan})
            else:
                flags.append({'ts_code': code, 'date': dt, 'in_win': 1, 'd': k - hit,
                              'px': close[k] / close[hit] - 1})

    m = ev.merge(pd.DataFrame(flags), on=['ts_code', 'date'], how='left')
    print(f'事件 {len(m):,}, 命中起量窗口 {int(m["in_win"].fillna(0).sum()):,} '
          f'({m["in_win"].mean() * 100:.1f}%)')

    print('\n=== 全事件池 ===')
    stat(m, '基准(全部)          ')
    stat(m[m['in_win'] == 1], '在起量台阶窗口内    ')
    stat(m[m['in_win'] == 0], '不在窗口内          ')

    print('\n=== 下蹲子集 ===')
    sq = m[m['squat']]
    stat(sq, '下蹲基准            ')
    stat(sq[sq['in_win'] == 1], '下蹲∩起量窗口       ')
    stat(sq[sq['in_win'] == 0], '下蹲∩非窗口         ')

    print('\n=== 窗口内·按价格透支程度 ===')
    w = m[m['in_win'] == 1]
    for lo, hi, nm in [(-1, -0.05, '  <-5%'), (-0.05, 0.05, '  -5~+5%'),
                       (0.05, 0.15, '   +5~+15%'), (0.15, 9, '   >+15%')]:
        stat(w[(w['px'] >= lo) & (w['px'] < hi)], nm)

    print('\n=== 窗口内·按下蹲×价格 ===')
    wsq = sq[sq['in_win'] == 1]
    for lo, hi, nm in [(-1, 0.05, '  下蹲∩px<+5%'), (0.05, 9, '  下蹲∩px>=+5%')]:
        stat(wsq[(wsq['px'] >= lo) & (wsq['px'] < hi)], nm)

    print('\n=== 窗口内·按起量后天数 d ===')
    for lo, hi, nm in [(1, 4, '  d 1~3'), (4, 8, '  d 4~7'), (8, 99, '  d >=8')]:
        stat(w[(w['d'] >= lo) & (w['d'] < hi)], nm)

    print('\n=== 全池·窗口×价格（合并「未透支」） ===')
    stat(m[(m['in_win'] == 1) & (m['px'] < 0.05)], '  窗口∩px<+5%       ')
    stat(m[(m['in_win'] == 1) & (m['px'] >= 0.05)], '  窗口∩px>=+5%      ')
    stat(m[(m['in_win'] == 0)], '  非窗口(基准对照)   ')

    print('\n=== 下蹲∩窗口·分年 ===')
    for y in sorted(sq['year'].dropna().unique()):
        stat(wsq[wsq['year'] == y], f'  {int(y)} 下蹲∩窗口     ')
        stat(sq[(sq['year'] == y) & (sq['in_win'] == 0)], f'  {int(y)} 下蹲∩非窗口   ')

    print('\n=== 下蹲·窗口×价格（合并「未透支」） ===')
    stat(sq[(sq['in_win'] == 1) & (sq['px'] < 0.05)], '  下蹲∩窗口∩px<+5%  ')
    stat(sq[(sq['in_win'] == 1) & (sq['px'] >= 0.05)], '  下蹲∩窗口∩px>=+5% ')

    print('\n=== 下蹲·台阶强度敏感度（KEEP_MIN / MAX_D 变体） ===')
    for km in [0.8, 1.0, 1.2, 1.5]:
        sub = _reflag(sq, km, MAX_D)
        stat(sq[sub == 1], f'  KEEP_MIN={km:<4} MAX_D={MAX_D}')
    for md in [5, 10, 15, 25]:
        sub = _reflag(sq, KEEP_MIN, md)
        stat(sq[sub == 1], f'  KEEP_MIN={KEEP_MIN:<4} MAX_D={md}')

    print('\n=== 口径二·绝对台阶 ma5[t] >= ma5[起量前一日] × STEP ===')
    for st in [1.0, 1.2, 1.5, 2.0]:
        for md in [15, 25]:
            sub = _reflag_step(m, st, md)
            stat(m[sub == 1], f'  全池 STEP={st:<4} MAX_D={md:<3}')
            subq = _reflag_step(sq, st, md)
            stat(sq[subq == 1], f'  下蹲 STEP={st:<4} MAX_D={md:<3}')

    print('\n=== 口径三·台阶从起量次日起算（避免锚点日 ma5 未填满的机械压制） ===')
    for st in [1.2, 1.5, 2.0, 2.5]:
        for md in [15, 25]:
            sub = _reflag_step(m, st, md, start_off=1)
            stat(m[sub == 1], f'  全池 STEP={st:<4} MAX_D={md:<3}')
            subq = _reflag_step(sq, st, md, start_off=1)
            stat(sq[subq == 1], f'  下蹲 STEP={st:<4} MAX_D={md:<3}')

    print('\n=== 候选口径·下蹲分年稳健性 ===')
    cands = [('口径二 STEP=1.2 MD=15', lambda df: _reflag_step(df, 1.2, 15)),
             ('口径二 STEP=1.5 MD=15', lambda df: _reflag_step(df, 1.5, 15)),
             ('口径二 STEP=1.5 MD=25', lambda df: _reflag_step(df, 1.5, 25)),
             ('口径三 STEP=2.0 MD=15', lambda df: _reflag_step(df, 2.0, 15, start_off=1)),
             ('口径一 KEEP=0.8 MD=15', lambda df: _reflag(df, 0.8, 15)),
             ('口径一 KEEP=1.0 MD=15', lambda df: _reflag(df, 1.0, 15))]
    for nm, fn in cands:
        sub = fn(sq)
        d = sq[sub == 1]
        stat(d, f'  {nm:<22}')
        stat(sq[sub == 0], f'    台阶外(同口径)        ')
        for y in sorted(d['year'].dropna().unique()):
            stat(d[d['year'] == y], f'      {int(y)}              ')


if __name__ == '__main__':
    main()
