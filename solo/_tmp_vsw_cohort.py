# -*- coding: utf-8 -*-
"""临时：海峡创新特征画像 + 下蹲分组回测（用完即删）

目的：0923 海峡创新(300300.SZ) 次日 +19.98%，但 FES=71.2 排在下蹲4/落库末位。
      找出「哪种可量化特征组合能把它顶到最优」，并用全样本回测验证，避免单票后视。

口径：T+1 开盘买 / T+5 收盘卖 / 盘中 -7% 止损 / 含 0.25% 成本；
      池子＝基础硬过滤通过的全部标的（新版口径：MACD 硬门槛与阀门均已移除）。
"""
import os
import sys
import time
import sqlite3

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO)
import _tmp_vsw_ab as AB   # noqa: E402  复用硬过滤/字段/收益口径

DB = AB.DB
START, END = AB.START, AB.END
HOLD, STOP, COST = AB.HOLD, AB.STOP, AB.COST

TARGET = '300300.SZ'
PEERS = [('600184.SH', '20260923'), ('002902.SZ', '20260923'), (TARGET, '20260922')]
TDATE = '20260923'


def board_of(code):
    n = code.split('.')[0]
    if code.endswith('.SZ') and n.startswith(('300', '301')):
        return '创业板'
    if code.endswith('.SH') and n.startswith('688'):
        return '科创板'
    return '主板'


def price_bucket(p):
    if p < 8:
        return 'a.<8元'
    if p < 15:
        return 'b.8-15元'
    if p < 25:
        return 'c.15-25元'
    if p < 50:
        return 'd.25-50元'
    return 'e.>50元'


def prep(g, code):
    """返回 (F, dates)，F 已含 MACD bar；不足 180 根返回 (None, None)"""
    mask, F = AB.hard_filter(g)
    if F is None:
        return None, None
    sC = F['sC']
    ema12 = sC.ewm(span=12, adjust=False).mean().values
    ema26 = sC.ewm(span=26, adjust=False).mean().values
    dif = ema12 - ema26
    F['bar'] = 2 * (dif - pd.Series(dif).ewm(span=9, adjust=False).mean().values)
    return F, g['trade_date'].values


def extra(idx, F):
    C, H = F['C'], F['H']
    c60 = (C[idx] / C[idx - 60] - 1) * 100 if idx >= 60 else 0.0
    c120 = (C[idx] / C[idx - 120] - 1) * 100 if idx >= 120 else 0.0
    hi200 = float(np.max(H[max(0, idx - 199):idx + 1]))
    return {'price': float(C[idx]),
            'c60': round(c60, 1), 'c120': round(c120, 1),
            'dist200': round((C[idx] / hi200 - 1) * 100, 1) if hi200 > 0 else 0.0}


def main():
    t0 = time.time()
    con = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT ts_code,trade_date,open,high,low,close,vol FROM daily_cache "
        "WHERE trade_date>='20240201' AND ts_code NOT LIKE '%.BJ'", con)
    con.close()
    df = df[~df['ts_code'].str.startswith(('8', '4', '9'))]
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    print(f"[Load] {len(df):,} 行 / {df['ts_code'].nunique()} 只  {time.time()-t0:.1f}s")

    keys = ['price', 'board', '量能爆发评分', '距MA20', '距MA5', '距MA10', '5日涨幅', '10日涨幅',
            'c60', 'c120', 'dist200', '距20日高', '今日量比', '涨日量/跌日量',
            'MACD状态', '死叉临界', '下蹲信号', 'ret']

    want = {c: set(x for cc, x in PEERS if cc == c) for c, _ in [(TARGET, 0)] + PEERS}
    for c, dtx in PEERS:
        want.setdefault(c, set()).add(dtx)
    portrait = {}

    rows = []
    t1 = time.time()
    for code, g in df.groupby('ts_code', sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 180:
            continue
        F, dates = prep(g, code)
        if F is None:
            continue
        mask = None
        # 画像：不受硬过滤约束，直接定位目标日
        for dtx in want.get(code, ()):
            hit = np.where(dates == dtx)[0]
            if len(hit):
                i = int(hit[0])
                f = AB.day_fields(i, F)
                f.update(extra(i, F))
                f['board'] = board_of(code)
                f['ret'] = AB.ret_of(g, i)
                f['下蹲信号'] = bool(f['下蹲信号'])
                portrait[(code, dtx)] = f
        # cohort：硬过滤通过的事件
        mask, _ = AB.hard_filter(g)
        if not mask.any():
            continue
        for idx in np.where(mask)[0]:
            dt = dates[idx]
            if dt < START or dt > END:
                continue
            r = AB.ret_of(g, idx)
            if r is None:
                continue
            f = AB.day_fields(idx, F)
            e = extra(idx, F)
            rows.append({
                'ts_code': code, 'date': dt, 'ret': r, 'year': dt[:4],
                'price': e['price'], 'board': board_of(code), 'squat': bool(f['下蹲信号']),
                'c60': e['c60'], 'c120': e['c120'], 'dist200': e['dist200'],
                'd5': f['距MA5'], 'c5': f['5日涨幅'], 'c10': f['10日涨幅'],
                'd20': f['距MA20'], 'vr': f['今日量比'], 'dhh20': f['距20日高'],
                'volup': f['涨日量/跌日量'], 'score': f['量能爆发评分'],
                'macd': f['MACD状态'], 'death': f['死叉临界'],
            })
    print(f"[Scan] 事件 {len(rows):,} 条  {time.time()-t1:.1f}s")
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(SOLO, '_tmp_vsw_cohort.csv'), index=False, encoding='utf-8-sig')

    # ---------- ① 画像 ----------
    print("\n" + "=" * 104)
    print("  ① 0923 三只对照（海峡创新 vs 模型 TOP1/TOP2）+ 海峡创新 0922")
    print("=" * 104)
    cols = [(TARGET, TDATE), ('600184.SH', TDATE), ('002902.SZ', TDATE), (TARGET, '20260922')]
    print(f"  {'特征':<14}" + "".join(f"{c[:6]+'/'+x[4:]:>15}" for c, x in cols))
    for k in keys:
        line = f"  {k:<14}"
        for c, x in cols:
            v = portrait.get((c, x), {}).get(k, '—')
            if isinstance(v, float):
                v = f"{v:+.2f}" if k not in ('price',) else f"{v:.2f}"
            elif isinstance(v, bool):
                v = '是' if v else '否'
            line += f"{str(v):>15}"
        print(line)

    # ---------- 分组统计 ----------
    def bucket(sub, col, title):
        print(f"\n  【{title}】样本 {len(sub):,}")
        print(f"  {'分组':<16}{'笔数':>7}{'胜率':>8}{'均收益':>9}{'中位':>8}{'止损率':>8}{'大赢>10%':>10}")
        for name, g in sub.groupby(col, sort=True):
            r = g['ret'].values
            if len(r) < 30:
                continue
            print(f"  {str(name):<16}{len(r):>7}{(r>0).mean()*100:>7.1f}%{r.mean():>+8.2f}%"
                  f"{np.median(r):>+7.2f}%{(r<=STOP).mean()*100:>7.1f}%{(r>10).mean()*100:>9.1f}%")

    # ---------- ② 全池 ----------
    print("\n" + "=" * 104)
    print("  ② 全池（新版口径：基础硬过滤全部通过标的）")
    print("=" * 104)
    r = d['ret'].values
    print(f"  池内基准 胜率{(r>0).mean()*100:.1f}% 均收益{r.mean():+.2f}% "
          f"止损率{(r<=STOP).mean()*100:.1f}% 大赢(>10%){(r>10).mean()*100:.1f}%")
    d['板块'] = d['board'].map(lambda b: '主板' if b == '主板' else '双创')
    d['价格档'] = d['price'].map(price_bucket)
    bucket(d, '板块', '全池·板块')
    bucket(d, '价格档', '全池·价格档')

    # ---------- ③ 下蹲 ----------
    sq = d[d['squat']].copy()
    print("\n" + "=" * 104)
    print(f"  ③ 下蹲买点 cohort（海峡创新来自此分支）样本 {len(sq):,}")
    print("=" * 104)
    r = sq['ret'].values
    print(f"  下蹲基准 胜率{(r>0).mean()*100:.1f}% 均收益{r.mean():+.2f}% "
          f"止损率{(r<=STOP).mean()*100:.1f}% 大赢(>10%){(r>10).mean()*100:.1f}%")
    sq['价格档'] = sq['price'].map(price_bucket)
    sq['板块'] = sq['board'].map(lambda b: '主板' if b == '主板' else '双创')
    bucket(sq, '板块', '下蹲·板块')
    bucket(sq, '价格档', '下蹲·价格档')
    sq['dhh档'] = pd.cut(sq['dhh20'], [-12.01, -8, -6, -4.01],
                         labels=['-12~-8', '-8~-6', '-6~-4'])
    bucket(sq, 'dhh档', '下蹲·距20日高')
    sq['c10档'] = pd.cut(sq['c10'], [-99, 0, 5, 10, 20, 999],
                         labels=['<0', '0~5', '5~10', '10~20', '>20'])
    bucket(sq, 'c10档', '下蹲·10日涨幅')
    sq['c60档'] = pd.cut(sq['c60'], [-99, 0, 15, 30, 999], labels=['<0', '0~15', '15~30', '>30'])
    bucket(sq, 'c60档', '下蹲·60日涨幅')
    sq['vr档'] = pd.cut(sq['vr'], [0, 0.9, 1.0, 1.1, 1.21],
                        labels=['<0.9', '0.9~1.0', '1.0~1.1', '1.1~1.2'])
    bucket(sq, 'vr档', '下蹲·量比')
    sq['d5档'] = pd.cut(sq['d5'], [-99, -3, -1.5, 1.01], labels=['<-3%', '-3~-1.5%', '-1.5~1%'])
    bucket(sq, 'd5档', '下蹲·距MA5')
    sq['volup档'] = pd.cut(sq['volup'], [0, 0.9, 1.2, 99], labels=['<0.9', '0.9~1.2', '>1.2'])
    bucket(sq, 'volup档', '下蹲·涨日量/跌日量')
    sq['score档'] = pd.cut(sq['score'], [65, 75, 85, 100], labels=['65~75', '75~85', '85~100'])
    bucket(sq, 'score档', '下蹲·量能爆发评分')
    sq['dist200档'] = pd.cut(sq['dist200'], [-99, -20, -10, 0.01],
                             labels=['<-20%', '-20~-10%', '>-10%'])
    bucket(sq, 'dist200档', '下蹲·距200日高')
    bucket(sq, 'macd', '下蹲·MACD分支')
    bucket(sq, 'death', '下蹲·死叉临界')

    print(f"\n总耗时 {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
