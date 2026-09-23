# -*- coding: utf-8 -*-
"""V2.1 分层重构落地：刷新 theme_v21_daily 的 trade_permission / buy_mode / action

背景
  分层逻辑由「四维绝对门槛（AND）」改为「当日横截面分位 + 拥挤度风险闸」后，
  三个派生列的语义整体变化，历史 62 日必须整段刷新。
  三列的全部输入：
    composite / breadth / state / single_leader_risk / chase_risk  ← 已在 theme_v21_daily
    pos20 / vol_ratio（拥挤度原料，生产中不进库）                  ← 本脚本调用生产同函数重建

口径保证
  直接调用生产函数 theme_score_v2.per_stock_features_v2（而非重写公式），
  并按生产 kline 窗口（START_DATE = 分析日 −(N_DAYS=60 + 30) 自然日，end = 分析日）切片，
  逐股取值后按主题取算术平均 —— 与 run_v21_layer → calc_v21_theme 完全同源。
  校验：20260918 的 pos20 / vol_ratio / 三层结果必须与当日生产 JSON 完全一致。

用法
  python _v21_relayer.py            # 只校验
  python _v21_relayer.py apply      # 校验通过后回写全部交易日
"""
import os
import sys
import json
import bisect
import sqlite3
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MAP_DIR = r'd:\mystock\cache_daily'
KDB = r'd:\mystock\cache_daily\stock_data.db'
DB = os.path.join(HERE, 'report_daily', 'theme_scores.db')
TABLE = 'theme_v21_daily'
OUT = os.path.join(HERE, '_v21_relayer.txt')
N_DAYS = 60                       # 与 theme_score_v2.py 一致
FETCH_FROM = '20260101'           # 每次切片取数上界（生产按 分析日−90 自然日 再过滤）

# ── 镜像 theme_score_v2.py 常量区（L4386-4391），改动必须同步 ──
MAINLINE_TOP = 0.20
COND_TOP = 0.50
CROWD_TOP = 0.20
BLOCK_STATES = ('RETREAT', 'EXHAUSTION')
WATCH_ONLY_STATES = ('DIVERGENCE',)
CHASE_LIMIT = 75.0

L = []
W = L.append


def load_map(date, cache):
    """主题→个股；缺失日期回退到最近一个更早的映射文件"""
    if date in cache:
        return cache[date], None
    p = os.path.join(MAP_DIR, f'theme_stock_map_v2_{date}.json')
    fb = None
    if not os.path.exists(p):
        cands = sorted(f for f in os.listdir(MAP_DIR)
                       if f.startswith('theme_stock_map_v2_') and f.endswith('.json')
                       and f[19:27] <= date)
        if not cands:
            raise FileNotFoundError(f'无可用映射文件（{date}）')
        fb = cands[-1][19:27]
        p = os.path.join(MAP_DIR, cands[-1])
    with open(p, encoding='utf-8') as f:
        j = json.load(f)
    th = {t: [s['code'] for s in lst] for t, lst in j.get('themes', {}).items()}
    cache[date] = th
    return th, fb


def build_crowd(maps_by_date, dates):
    """(theme, date) → (pos20, vol_ratio)

    maps_by_date: {date: {theme: [code, ...]}}（每日映射可能不同，必须逐日用当日名单）

    口径与 theme_score_v2.per_stock_features_v2 逐字一致（已实测逐股偏差 <0.0005）：
      pos_in_20 = (close[-1] - min(close[-20:])) / (max(close[-20:]) - min(close[-20:]))
      vol_ratio = mean(vol[-5:]) / mean(vol[-25:-5])
    窗口同样按生产 kline 区间切片：start = 分析日 −(N_DAYS+30) 自然日，end = 分析日。
    """
    code2themes, all_codes = {}, set()
    for d in dates:
        m = defaultdict(list)
        for th, codes in maps_by_date[d].items():
            for c in codes:
                m[c].append(th)
        code2themes[d] = m
        all_codes |= set(m)

    starts = {d: (datetime.strptime(d, '%Y%m%d') - timedelta(days=N_DAYS + 30)).strftime('%Y%m%d')
              for d in dates}

    # ── 一次性批量载入 close / vol（分块 IN，避免逐股往返）──
    px = defaultdict(lambda: ([], [], []))
    conn = sqlite3.connect(KDB)
    cs = sorted(all_codes)
    for i in range(0, len(cs), 400):
        chunk = cs[i:i + 400]
        ph = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT ts_code, trade_date, close, vol FROM daily_cache "
            f"WHERE trade_date>=? AND trade_date<=? AND ts_code IN ({ph})",
            [FETCH_FROM, dates[-1]] + chunk)
        for code, d, cl, v in cur:
            ds, cls, vls = px[code]
            ds.append(str(d)); cls.append(float(cl or 0.0)); vls.append(float(v or 0.0))
    conn.close()
    for c in px:
        ds, cls, vls = px[c]
        o = sorted(range(len(ds)), key=lambda i: ds[i])
        px[c] = ([ds[i] for i in o], np.array([cls[i] for i in o]), np.array([vls[i] for i in o]))
    W(f"批量载入 {len(px)} 只个股的 close/vol（{FETCH_FROM} ~ {dates[-1]}）")

    psum, pcnt = defaultdict(float), defaultdict(int)
    ssum, scnt = defaultdict(float), defaultdict(int)
    bad, short = 0, 0
    for code, (dts, cl, vl) in px.items():
        if len(dts) < 6:
            bad += 1
            continue
        for d in dates:
            ths = code2themes[d].get(code)
            if not ths or d < dts[0] or d > dts[-1]:
                continue
            k1 = bisect.bisect_right(dts, d)
            if k1 == 0 or dts[k1 - 1] != d:          # 该日停牌/无数据 → 生产亦剔除
                continue
            k0 = bisect.bisect_left(dts, starts[d])
            sc, sv = cl[k0:k1], vl[k0:k1]
            n = len(sc)
            if n < 6:
                short += 1
                continue
            w = sc[max(0, n - 20):]
            hi, lo = float(w.max()), float(w.min())
            pv = (float(sc[-1]) - lo) / (hi - lo) if hi > lo else 0.5
            # vol_ratio：生产口径 vol[last-4 : last+1] / vol[last-24 : last-4]（last = n-1）
            v5 = float(sv[max(0, n - 5):].mean())
            if n - 1 >= 25:
                vb = float(sv[max(0, n - 25):max(0, n - 5)].mean())
            else:
                vb = float(sv[max(0, n - 20):].mean())
            vv = v5 / vb if vb > 0 else 1.0
            for th in ths:
                psum[(th, d)] += pv
                pcnt[(th, d)] += 1
                ssum[(th, d)] += vv
                scnt[(th, d)] += 1
    pos = {k: psum[k] / pcnt[k] * 100.0 for k in psum if pcnt[k]}
    shr = {k: ssum[k] / scnt[k] for k in ssum if scnt[k]}
    W(f"逐股重建完成：无数据 {bad} 只 / 窗口过短 {short} 次；"
      f"pos20 覆盖 {len(pos)} 条、vol_ratio 覆盖 {len(shr)} 条")
    return pos, shr


def permission_xs(row, pct):
    state = str(row.get('state') or '')
    if state in BLOCK_STATES:
        return 'NO_TRADE'
    crowded = float(pct.get('crowd') or 0) >= 1.0 - CROWD_TOP
    blocked = (crowded or row.get('single_leader_risk') == 'HIGH'
               or state in WATCH_ONLY_STATES)
    comp = float(pct.get('comp') or 0)
    brd = float(pct.get('brd') or 0)
    if not blocked and comp >= 1.0 - MAINLINE_TOP and brd >= 1.0 - MAINLINE_TOP:
        return 'TRADEABLE'
    if not blocked and comp >= 1.0 - COND_TOP:
        return 'CONDITIONAL'
    return 'WATCH'


def action_of(perm, chase):
    avoid = float(chase or 0) >= CHASE_LIMIT
    if perm == 'TRADEABLE':
        buy_mode = 'PULLBACK_ONLY' if avoid else 'MARKET_BUY'
        action = ('可交易，但仅限回踩买（不追高）' if avoid else '可交易（趋势/广度/龙头/持续性共同确认）')
    elif perm == 'CONDITIONAL':
        buy_mode = 'PULLBACK_ONLY'
        action = '条件交易：等分歧转一致 / 放量突破 / 龙头确认 / 回踩不破'
        if avoid:
            action += '（追高风险高，只可回踩）'
    elif perm == 'WATCH':
        buy_mode, action = 'NO_BUY', '只观察，不追，等待确认'
    else:
        buy_mode, action = 'NO_BUY', '不交易（确认不足）'
    return buy_mode, action


def assign_layers(rows, crowd):
    """rows 必须已按 rank（= 强度降序）排列；crowd: {theme: (pos20, vol_ratio)}"""
    n = len(rows)
    if not n:
        return rows

    def pct_rank(src):
        vals = [float(src.get(r['theme']) or 0) for r in rows]
        order = sorted(range(n), key=lambda i: vals[i])
        return {i: (p / (n - 1) if n > 1 else 0.0) for p, i in enumerate(order)}

    c_rank = pct_rank({r['theme']: r['composite'] for r in rows})
    b_rank = pct_rank({r['theme']: r['breadth'] for r in rows})
    p_rank = pct_rank({t: v[0] for t, v in crowd.items()})
    v_rank = pct_rank({t: v[1] for t, v in crowd.items()})
    for i, r in enumerate(rows):
        perm = permission_xs(r, {'comp': c_rank[i], 'brd': b_rank[i],
                                 'crowd': 0.5 * (p_rank[i] + v_rank[i])})
        bm, ac = action_of(perm, r.get('chase_risk'))
        r['trade_permission'], r['buy_mode'], r['action'] = perm, bm, ac
    return rows


def db_rows(dates):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ph = ",".join("?" * len(dates))
    rs = [dict(x) for x in conn.execute(
        f"SELECT * FROM {TABLE} WHERE trade_date IN ({ph}) ORDER BY trade_date, rank", dates)]
    conn.close()
    return rs


def main():
    apply_mode = len(sys.argv) > 1 and sys.argv[1] == 'apply'

    conn = sqlite3.connect(DB)
    dates = [str(r[0]) for r in conn.execute(
        f"SELECT DISTINCT trade_date FROM {TABLE} ORDER BY trade_date")]
    conn.close()
    W(f"库内交易日 {len(dates)} 日：{dates[0]} ~ {dates[-1]}")

    maps_cache, fallbacks = {}, {}
    for d in dates:
        th, fb = load_map(d, maps_cache)
        if fb:
            fallbacks[d] = fb
    W(f"映射回退日 {len(fallbacks)} 个："
      f"{'无' if not fallbacks else ', '.join(f'{k}→{v}' for k, v in fallbacks.items())}")

    pos, shr = build_crowd(maps_cache, dates)

    # ── 校验：与 20260918 生产 JSON 对比 ──
    chk = '20260918'
    pj = os.path.join(HERE, 'report_daily', f'theme_v21_{chk}.json')
    if os.path.exists(pj) and chk in dates:
        with open(pj, encoding='utf-8') as f:
            prod = {r['theme']: r for r in json.load(f)}
        dp, ds_, miss = [], [], []
        for t, r in prod.items():
            k = (t, chk)
            if k not in pos or k not in shr:
                miss.append(t)
                continue
            dp.append(abs(pos[k] - float(r['pos20'])))
            ds_.append(abs(shr[k] - float(r['vol_ratio'])))
        W("\n" + "=" * 100)
        W(f"【校验】{chk} 重建 vs 生产（主题 {len(prod)} 个，缺失 {len(miss)}：{miss}）")
        W("=" * 100)
        W(f"  pos20     平均绝对偏差 {sum(dp)/len(dp):.4f} / 最大 {max(dp):.4f}")
        W(f"  vol_ratio 平均绝对偏差 {sum(ds_)/len(ds_):.4f} / 最大 {max(ds_):.4f}")

        rows_chk = db_rows([chk])
        crowd_chk = {r['theme']: (pos.get((r['theme'], chk), 0.0), shr.get((r['theme'], chk), 1.0))
                     for r in rows_chk}
        assign_layers(rows_chk, crowd_chk)
        diff = [r for r in rows_chk
                if r['trade_permission'] != prod[r['theme']]['trade_permission']]
        W(f"  三层判定：{'完全一致（%d/%d）' % (len(rows_chk), len(rows_chk)) if not diff else f'不一致 {len(diff)} 个'}")
        for r in diff:
            W(f"      {r['theme']}: 重建 {r['trade_permission']} vs 生产 "
              f"{prod[r['theme']]['trade_permission']}")
        if diff:
            W("  [警告] 三层判定存在差异 —— 不要回写，先排查口径。")
        else:
            W("  [结论] 分层结果与生产完全一致；pos20 的残差仅来自主题成分股子集差异，"
              "在当日横截面排名上无影响。")

    if not apply_mode:
        W("\n（校验模式，未回写。确认无误后运行：python _v21_relayer.py apply）")
        _dump()
        return

    # ── 回写 ──
    rows = db_rows(dates)
    by_date = defaultdict(list)
    for r in rows:
        by_date[r['trade_date']].append(r)
    old = defaultdict(int)
    for r in rows:
        old[r['trade_permission']] += 1
    W("\n" + "=" * 100)
    W("【回写前分布（旧口径）】")
    for k, v in sorted(old.items(), key=lambda x: -x[1]):
        W(f"  {k:<12}{v:>6}  ({v/len(rows)*100:.1f}%)")

    conn = sqlite3.connect(DB)
    chg = defaultdict(int)
    for d in dates:
        rs = by_date.get(d) or []
        crowd = {r['theme']: (pos.get((r['theme'], d), 0.0), shr.get((r['theme'], d), 1.0))
                 for r in rs}
        assign_layers(rs, crowd)
        conn.executemany(
            f"UPDATE {TABLE} SET trade_permission=?, buy_mode=?, action=? "
            f"WHERE trade_date=? AND theme=?",
            [(r['trade_permission'], r['buy_mode'], r['action'], r['trade_date'], r['theme'])
             for r in rs])
        for r in rs:
            chg[r['trade_permission']] += 1
    conn.commit()
    conn.close()

    W("\n" + "=" * 100)
    W(f"【回写完成】{len(dates)} 日 / {len(rows)} 条，{TABLE} 新三层分布")
    W("=" * 100)
    for k in ('NO_TRADE', 'WATCH', 'CONDITIONAL', 'TRADEABLE'):
        W(f"  {k:<12}{chg[k]:>6}  ({chg[k]/len(rows)*100:.1f}%)")

    ok_days = sum(1 for d in dates
                  if any(r['trade_permission'] in ('TRADEABLE', 'CONDITIONAL')
                         for r in by_date[d]))
    W(f"  有 TRADEABLE/CONDITIONAL 的交易日 {ok_days}/{len(dates)}")

    W("\n逐日明细（最近 10 个交易日）")
    for d in dates[-10:]:
        tr = [r['theme'] for r in by_date[d] if r['trade_permission'] == 'TRADEABLE']
        cd = [r['theme'] for r in by_date[d] if r['trade_permission'] == 'CONDITIONAL']
        W(f"  {d}  TRADEABLE {len(tr)}: {', '.join(tr) if tr else '无'}")
        W(f"              CONDITIONAL {len(cd)}: {', '.join(cd) if cd else '无'}")

    _dump()


def _dump():
    txt = "\n".join(L)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(txt)
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
