# -*- coding: utf-8 -*-
"""临时脚本（第六轮）：低吸 / 二波因子的正确验证

为什么必须新写：
  第五轮用的是 V2 CSV 的「当日状态快照」字段（MA位置、涨幅、量比），
  无法表达「前波涨了 X% → 回调 Y% → 缩量 Z 天」这种**路径依赖**特征 —— 而二波的本质就是路径。
  第六轮改用真实原料自建主题合成指数：
    · 主题→个股映射：d:\\mystock\\cache_daily\\theme_stock_map_v2_{date}.json（32 主题 × ~300 股）
    · 个股日线：d:\\mystock\\cache_daily\\stock_data.db 表 daily_cache（20210104~20260922，5779 只）

样本设计（两段，用于区分「长期因子」与「当前 regime」）：
  A. 长窗口静态池：20250401 ~ 20260922（约 355 个交易日，用 20260922 固定映射）
     —— 统计功效充足；代价是**成分股前视/幸存者偏差**（映射是当前的）。仅用于看因子的
        方向是否跨 regime 稳定，绝对值不可直接当收益预期。
  B. 生产窗口：20260629 ~ 20260922（62 日，与 V2.1 回填窗口一致）—— 无额外偏差。

口径：主题等权合成指数；前瞻收益 = 指数 T+k 收益；组间比较一律用**同日横截面超额**。
"""
import os
import sys
import json
import sqlite3
import datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_theme_v21 as bt

MAP_FILE = r'd:\mystock\cache_daily\theme_stock_map_v2_20260922.json'
KDB = r'd:\mystock\cache_daily\stock_data.db'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v21_wave2_scan.txt')
L = []
W = L.append
KS = (1, 3, 5, 10)
WARM = 80          # 特征预热交易日
CALIB = ['20260629', '20260922']   # 生产窗口


def load_map():
    with open(MAP_FILE, encoding='utf-8') as f:
        j = json.load(f)
    themes = {t: [s['code'] for s in lst] for t, lst in j.get('themes', {}).items()}
    return themes


def load_bars(codes, start, end):
    """返回 {code: {date: (pct_chg, vol)}}"""
    conn = sqlite3.connect(KDB)
    out = defaultdict(dict)
    cs = sorted(codes)
    CH = 400
    for i in range(0, len(cs), CH):
        chunk = cs[i:i + CH]
        ph = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT ts_code, trade_date, pct_chg, vol FROM daily_cache "
            f"WHERE trade_date>=? AND trade_date<=? AND ts_code IN ({ph})",
            [start, end] + chunk)
        for code, d, pc, v in cur:
            out[code][str(d)] = (float(pc or 0.0), float(v or 0.0))
    conn.close()
    return out


def build_theme_series(themes, bars, all_dates):
    """主题等权合成指数 + 成交量序列：{theme: (idx_by_date, vol_by_date)}"""
    res = {}
    for th, codes in themes.items():
        rets, vols = defaultdict(list), defaultdict(list)
        for c in codes:
            b = bars.get(c)
            if not b:
                continue
            for d in all_dates:
                t = b.get(d)
                if t is not None:
                    rets[d].append(t[0])
                    vols[d].append(t[1])
        idx, vseries = {}, {}
        eq = 1.0
        for d in all_dates:
            rs = rets.get(d)
            if rs:
                eq *= (1.0 + (sum(rs) / len(rs)) / 100.0)
                vv = vols.get(d)
                idx[d] = eq
                vseries[d] = sum(vv) if vv else 0.0
        res[th] = (idx, vseries)
    return res


def pctile(xs, q):
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def main():
    themes = load_map()
    allcodes = {c for v in themes.values() for c in v}
    W(f"主题 {len(themes)} 个，映射个股 {len(allcodes)} 只（映射文件 {os.path.basename(MAP_FILE)}）")

    conn = sqlite3.connect(KDB)
    all_dates = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
        ('20250101', '20260922'))]
    conn.close()
    W(f"交易日 {len(all_dates)} 日：{all_dates[0]} ~ {all_dates[-1]}")

    bars = load_bars(allcodes, all_dates[0], all_dates[-1])
    W(f"载入有日线的个股 {len(bars)} 只")
    ts = build_theme_series(themes, bars, all_dates)

    # ── 特征计算（全部只用 t 及之前的数据）──
    pos = {d: i for i, d in enumerate(all_dates)}
    rows = []
    for th, (idx, vs) in ts.items():
        ds = [d for d in all_dates if d in idx]
        if len(ds) < WARM + 12:
            continue
        for j, d in enumerate(ds):
            if j < WARM:
                continue
            i = pos[d]
            win = ds[max(0, j - 79):j + 1]
            if len(win) < 80:
                continue
            p = [idx[x] for x in win]
            v = [vs[x] for x in win]
            f = {'theme': th, 'trade_date': d}
            # 收益
            for k in (3, 5, 10, 20, 40, 60):
                f[f'r{k}'] = (p[-1] / p[-1 - k] - 1) * 100 if len(p) > k else None
            # 均线（指数）
            for k in (5, 10, 20, 60):
                f[f'ma{k}'] = sum(p[-k:]) / k
                f[f'dev_ma{k}'] = (p[-1] / f[f'ma{k}'] - 1) * 100
            f['ma5_lt_ma10'] = 1.0 if f['ma5'] < f['ma10'] else 0.0
            f['ma20_slope'] = (f['ma20'] / (sum(p[-25:-5]) / 20) - 1) * 100 if len(p) >= 25 else None
            f['ma60_slope'] = (f['ma60'] / (sum(p[-65:-5]) / 60) - 1) * 100 if len(p) >= 65 else None
            # 位置 / 回撤
            for k in (20, 40):
                hi, lo = max(p[-k:]), min(p[-k:])
                f[f'dd{k}'] = (p[-1] / hi - 1) * 100
                f[f'pos{k}'] = (p[-1] - lo) / (hi - lo) * 100 if hi > lo else 50.0
                f[f'ds_hi{k}'] = k - 1 - p[-k:].index(hi)
            # 前波涨幅：近 k 日高点 / 其前 k 日低点
            f['wave20'] = (max(p[-20:]) / min(p[-40:-20]) - 1) * 100 if len(p) >= 40 else None
            f['wave40'] = (max(p[-40:]) / min(p[-80:-40]) - 1) * 100 if len(p) >= 80 else None
            # 量能
            v5, v20, v60 = sum(v[-5:]) / 5, sum(v[-20:]) / 20, sum(v[-60:]) / 60
            f['shrink5_20'] = v5 / v20 if v20 else None
            f['shrink5_60'] = v5 / v60 if v60 else None
            f['vol_heat'] = v5 / v60 * 100 if v60 else None
            rows.append(f)

    # ── 前瞻收益（用主题指数）──
    seq = {th: sorted([d for d in all_dates if d in ts[th][0]]) for th in ts}
    seqpos = {th: {d: i for i, d in enumerate(seq[th])} for th in ts}
    for f in rows:
        th, d = f['theme'], f['trade_date']
        idx = ts[th][0]
        j = seqpos[th][d]
        for k in KS:
            seg = seq[th][j + 1:j + 1 + k]
            if len(seg) < k:
                f[f'fwd_{k}'] = None
                continue
            eq = 1.0
            for x in seg:
                eq *= idx[x] / idx[seq[th][seqpos[th][x] - 1]]
            f[f'fwd_{k}'] = (eq - 1) * 100

    # ── 同日横截面超额 ──
    for k in KS:
        bd = defaultdict(list)
        for f in rows:
            bd[f['trade_date']].append(f)
        for d, rs in bd.items():
            vs = [r[f'fwd_{k}'] for r in rs if r.get(f'fwd_{k}') is not None]
            m = sum(vs) / len(vs) if vs else 0.0
            for r in rs:
                r[f'ex_{k}'] = (r[f'fwd_{k}'] - m) if r.get(f'fwd_{k}') is not None else None

    W(f"有效样本 {len(rows)} 条（{rows[0]['trade_date']} ~ {rows[-1]['trade_date']}），"
      f"主题 {len({r['theme'] for r in rows})} 个")

    def run(tag, sub, feats, combo=None):
        W("\n" + "=" * 104)
        W(f"【{tag}】窗口 {sub[0]['trade_date']} ~ {sub[-1]['trade_date']}，"
          f"{len(sub)} 条 / {len({r['trade_date'] for r in sub})} 日")
        W("=" * 104)
        for key, desc in feats:
            rs = [r for r in sub if r.get(key) is not None]
            if len(rs) < 120:
                continue
            rs.sort(key=lambda r: r[key])
            n = len(rs)
            cells = []
            for i in range(10):
                seg = rs[int(i * n / 10):int((i + 1) * n / 10)]
                vals = [r['ex_5'] for r in seg if r.get('ex_5') is not None]
                if len(vals) >= 4:
                    cells.append((bt._mean(vals), bt._winrate(vals)))
                else:
                    cells.append((float('nan'), float('nan')))
            W(f"{key:<13}{desc:<30}" + "".join(f"{c[0]:>7.2f}" for c in cells))
            W(f"{'':<13}{'（胜率）':<30}" + "".join(f"{c[1]:>7.1f}" for c in cells))
        if combo:
            W("\n  ── 组合条件（T+5 超额 vs 全体）──")
            for name, fn in combo:
                sel = [r for r in sub if fn(r)]
                vals = [r['ex_5'] for r in sel if r.get('ex_5') is not None]
                allv = [r['ex_5'] for r in sub if r.get('ex_5') is not None]
                if len(vals) < 20:
                    W(f"    {name:<44}n={len(vals):<5}（样本不足）")
                    continue
                t, p = bt.welch_t(vals, allv)
                W(f"    {name:<44}n={len(vals):<5}超额{bt._mean(vals):>7.2f}%  "
                  f"胜率{bt._winrate(vals):>5.1f}%  t={bt._fmt(t):>6}  p={bt._fmt(p, 4)}"
                  f"{'  ★显著' if (p == p and p < 0.05) else ''}")

    FEATS = [
        ('wave40',      '前波涨幅(近40日高/前40日低-1)'),
        ('wave20',      '前波涨幅(近20日高/前20日低-1)'),
        ('dd20',        '回调深度(距20日高点%)'),
        ('dd40',        '回调深度(距40日高点%)'),
        ('pos20',       '20日位置分'),
        ('ds_hi20',     '距20日高点天数'),
        ('shrink5_20',  '缩量比(5日量/20日量)'),
        ('shrink5_60',  '缩量比(5日量/60日量)'),
        ('r5',          '5日涨幅'),
        ('r20',         '20日涨幅'),
        ('r60',         '60日涨幅'),
        ('dev_ma20',    '距MA20偏离(%)'),
        ('dev_ma60',    '距MA60偏离(%)'),
        ('ma60_slope',  'MA60斜率(%)'),
    ]
    COMBO = [
        ('中期趋势未破(20日涨幅>0)', lambda r: (r.get('r20') or -99) > 0),
        ('中期趋势强(60日涨幅>15)', lambda r: (r.get('r60') or -99) > 15),
        ('MA5<MA10', lambda r: r.get('ma5_lt_ma10') == 1.0),
        ('缩量(shrink5_20<1)', lambda r: (r.get('shrink5_20') or 9) < 1.0),
        ('回调≥8%(dd20<-8)', lambda r: (r.get('dd20') or 0) < -8),
        ('前波涨幅≥25%', lambda r: (r.get('wave40') or -99) >= 25),
        ('前波≥25% + 回调≥8%', lambda r: (r.get('wave40') or -99) >= 25 and (r.get('dd20') or 0) < -8),
        ('前波≥25% + 回调≥8% + 缩量',
         lambda r: (r.get('wave40') or -99) >= 25 and (r.get('dd20') or 0) < -8 and (r.get('shrink5_20') or 9) < 1.0),
        ('前波≥25% + 回调≥8% + 缩量 + 20日涨幅>0',
         lambda r: (r.get('wave40') or -99) >= 25 and (r.get('dd20') or 0) < -8
         and (r.get('shrink5_20') or 9) < 1.0 and (r.get('r20') or -99) > 0),
        ('超跌(r20<-15%)', lambda r: (r.get('r20') or 0) < -15),
        ('缩量 + 超跌(r20<-15%)',
         lambda r: (r.get('shrink5_20') or 9) < 1.0 and (r.get('r20') or 0) < -15),
        ('缩量 + 位置低(pos20<30)',
         lambda r: (r.get('shrink5_20') or 9) < 1.0 and (r.get('pos20') or 99) < 30),
    ]

    long_sub = [r for r in rows if r['trade_date'] >= '20250401']
    run('A 长窗口静态池', long_sub, FEATS, COMBO)
    prod_sub = [r for r in rows if CALIB[0] <= r['trade_date'] <= CALIB[1]]
    run('B 生产窗口', prod_sub, FEATS, COMBO)

    # ── C. 焦点诊断：唯一双窗口显著为正的因子「超跌 r20<-15%」──
    W("\n" + "=" * 104)
    W("【C】焦点诊断：超跌因子（r20 < -15%）—— 期限结构 / 按日配对 / 与四维及状态的关系")
    W("=" * 104)

    def focus(tag, sub):
        sel = [r for r in sub if (r.get('r20') or 99) < -15]
        W(f"\n  ── {tag}：n={len(sel)} / {len(sub)}（{100.0*len(sel)/len(sub):.1f}%），"
          f"分布在 {len({r['trade_date'] for r in sel})} 个交易日 ──")
        for k in KS:
            va = [r[f'ex_{k}'] for r in sel if r.get(f'ex_{k}') is not None]
            vb = [r[f'ex_{k}'] for r in sub if r.get(f'ex_{k}') is not None]
            vab = [r[f'fwd_{k}'] for r in sel if r.get(f'fwd_{k}') is not None]
            vbb = [r[f'fwd_{k}'] for r in sub if r.get(f'fwd_{k}') is not None]
            if len(va) < 15:
                continue
            t, p = bt.welch_t(va, vb)
            W(f"    T+{k:<3} 超额{bt._mean(va):>7.2f}% (绝对{bt._mean(vab):>7.2f}% vs 全体{bt._mean(vbb):>7.2f}%)  "
              f"中位{bt._median(va):>7.2f}%  胜率{bt._winrate(va):>5.1f}%  "
              f"t={bt._fmt(t):>6} p={bt._fmt(p, 4)}{'  ★显著' if (p == p and p < 0.05) else ''}")
        # 按日配对（去横截面相关）
        for k in (5, 10):
            bd = defaultdict(list)
            for r in sub:
                bd[r['trade_date']].append(r)
            diffs = []
            for d, rs in bd.items():
                va = [r[f'ex_{k}'] for r in rs if (r.get('r20') or 99) < -15 and r.get(f'ex_{k}') is not None]
                vb = [r[f'ex_{k}'] for r in rs if (r.get('r20') or 99) >= -15 and r.get(f'ex_{k}') is not None]
                if va and len(vb) >= 3:
                    diffs.append(sum(va) / len(va) - sum(vb) / len(vb))
            if len(diffs) >= 5:
                m = sum(diffs) / len(diffs)
                sd = bt._std(diffs)
                se = sd / (len(diffs) ** 0.5)
                t = m / se if se else float('nan')
                p = 2.0 * (1.0 - bt._norm_cdf(abs(t))) if se else float('nan')
                W(f"    按日配对 T+{k}：有效日 {len(diffs)}  日均差 {m:+.3f}pct  "
                  f"t={bt._fmt(t)}  p={bt._fmt(p,4)}  差>0 天数 {sum(1 for x in diffs if x>0)}/{len(diffs)}")
        # 超跌日的四维画像（对照全体）
        for key in ('r60', 'dev_ma60', 'pos20', 'shrink5_20', 'wave40', 'dd40'):
            a = [r[key] for r in sel if r.get(key) is not None]
            b = [r[key] for r in sub if r.get(key) is not None]
            if a and b:
                W(f"    特征对照 {key:<12} 信号中位 {bt._median(a):>8.2f}   全体中位 {bt._median(b):>8.2f}")

    focus('A 长窗口静态池', long_sub)
    focus('B 生产窗口', prod_sub)

    # 与 V2.1 状态的重叠（仅生产窗口可join）
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            'report_daily', 'theme_scores.db'))
        st = {(str(a), b): c for a, b, c in conn.execute(
            "SELECT trade_date, theme, state FROM theme_v21_daily")}
        conn.close()
        sel = [r for r in prod_sub if (r.get('r20') or 99) < -15]
        cnt = defaultdict(int)
        for r in sel:
            cnt[st.get((r['trade_date'], r['theme']), 'NA')] += 1
        W(f"\n  超跌信号（生产窗口）的状态构成：" +
          "、".join(f"{a}{b}" for a, b in sorted(cnt.items(), key=lambda z: -z[1])))
        W("  对照：全生产窗口状态构成（前 5）")
        c2 = defaultdict(int)
        for r in prod_sub:
            c2[st.get((r['trade_date'], r['theme']), 'NA')] += 1
        W("    " + "、".join(f"{a}{b}" for a, b in sorted(c2.items(), key=lambda z: -z[1])[:5]))
    except Exception as e:
        W(f"\n  [状态重叠] 跳过：{e}")

    # ── D. 增量与稳定性：超跌 是否只是 RETREAT 的代理 / 跨年度是否稳定 ──
    W("\n" + "=" * 104)
    W("【D】增量与稳定性检验")
    W("=" * 104)
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            'report_daily', 'theme_scores.db'))
        st = {(str(a), b): c for a, b, c in conn.execute(
            "SELECT trade_date, theme, state FROM theme_v21_daily")}
        conn.close()

        def dep(tag, sel, base):
            for k in (5, 10):
                va = [r[f'ex_{k}'] for r in sel if r.get(f'ex_{k}') is not None]
                vb = [r[f'ex_{k}'] for r in base if r.get(f'ex_{k}') is not None]
                if len(va) < 10:
                    continue
                t, p = bt.welch_t(va, vb)
                W(f"  {tag:<40}T+{k:<3} n={len(va):<5} 超额{bt._mean(va):>7.2f}%  "
                  f"胜率{bt._winrate(va):>5.1f}%  vs对照 t={bt._fmt(t):>6} p={bt._fmt(p,4)}"
                  f"{'  ★' if (p == p and p < 0.05) else ''}")

        for r in prod_sub:
            r['_state'] = st.get((r['trade_date'], r['theme']), 'NA')
        retreat = [r for r in prod_sub if r['_state'] == 'RETREAT']
        dep('① 生产窗 RETREAT 内：超跌 vs RETREAT其余',
            [r for r in retreat if (r.get('r20') or 99) < -15],
            [r for r in retreat if (r.get('r20') or 99) >= -15])
        dep('② 生产窗：超跌 vs RETREAT整体',
            [r for r in prod_sub if (r.get('r20') or 99) < -15], retreat)
        dep('③ 生产窗：超跌 vs 全体', 
            [r for r in prod_sub if (r.get('r20') or 99) < -15], prod_sub)
    except Exception as e:
        W(f"  [增量检验] 跳过：{e}")

    W("\n  ── 跨年度稳定性（长窗口按日历切段，因子 = r20<-15%）──")
    for tag, a, b in (('2025上', '20250101', '20250630'), ('2025下', '20250701', '20251231'),
                      ('2026上', '20260101', '20260630'), ('2026下', '20260701', '20260922')):
        sub = [r for r in rows if a <= r['trade_date'] <= b]
        sel = [r for r in sub if (r.get('r20') or 99) < -15]
        va = [r['ex_5'] for r in sel if r.get('ex_5') is not None]
        vb = [r['ex_5'] for r in sub if r.get('ex_5') is not None]
        if len(va) < 8:
            W(f"    {tag}  信号 n={len(va)}（不足）")
            continue
        t, p = bt.welch_t(va, vb)
        W(f"    {tag}  信号 n={len(va):<5} T+5超额{bt._mean(va):>7.2f}%  胜率{bt._winrate(va):>5.1f}%  "
          f"t={bt._fmt(t):>6} p={bt._fmt(p,4)}{'  ★' if (p == p and p < 0.05) else ''}")

    # ── E. 新三层定档（按分位、不看收益）—— 直接回答「CONDITIONAL 是否显著优于 WATCH」──
    W("\n" + "=" * 104)
    W("【E】新三层：以主题 20 日收益(r20)分位定档，双窗口检验")
    W("=" * 104)
    calib = [r for r in rows if r['trade_date'] <= '20260918']
    qs = {}
    for q in (5, 10, 20, 30):
        qs[q] = pctile([r['r20'] for r in calib if r.get('r20') is not None], q)
    W("  标定集（<=20260918）r20 分位：" + "  ".join(f"p{q}={qs[q]:.2f}%" for q in (5, 10, 20, 30)))

    def layer(r, lo_q, hi_q):
        v = r.get('r20')
        if v is None:
            return 'NA'
        if v <= qs[lo_q]:
            return 'TRADEABLE'
        if v <= qs[hi_q]:
            return 'CONDITIONAL'
        return 'WATCH'

    for tag, sub in (('A 长窗口静态池', long_sub), ('B 生产窗口', prod_sub)):
        W(f"\n  ── {tag}（TRADEABLE = r20 ≤ p5={qs[5]:.2f}%，CONDITIONAL = p5~p20，WATCH = 其余）──")
        lay = {}
        for r in sub:
            r['_lay'] = layer(r, 5, 20)
            lay.setdefault(r['_lay'], []).append(r)
        for lp in ('TRADEABLE', 'CONDITIONAL', 'WATCH'):
            sel = lay.get(lp, [])
            if not sel:
                continue
            W(f"    ■ {lp}  n={len(sel)}（{100.0*len(sel)/len(sub):.1f}%），"
              f"{len({r['trade_date'] for r in sel})} 日")
            for k in KS:
                va = [r[f'ex_{k}'] for r in sel if r.get(f'ex_{k}') is not None]
                vab = [r[f'fwd_{k}'] for r in sel if r.get(f'fwd_{k}') is not None]
                if len(va) < 10:
                    continue
                W(f"        T+{k:<3} 超额{bt._mean(va):>7.2f}%  绝对{bt._mean(vab):>7.2f}%  "
                  f"中位{bt._median(va):>7.2f}%  胜率{bt._winrate(va):>5.1f}%")
        c = lay.get('CONDITIONAL', [])
        w = lay.get('WATCH', [])
        t3 = lay.get('TRADEABLE', [])
        W("    ── 关键检验 ──")
        for k in KS:
            for nm, aa, bb in (('CONDITIONAL vs WATCH', c, w), ('TRADEABLE vs WATCH', t3, w),
                               ('TRADEABLE vs CONDITIONAL', t3, c)):
                va = [r[f'ex_{k}'] for r in aa if r.get(f'ex_{k}') is not None]
                vb = [r[f'ex_{k}'] for r in bb if r.get(f'ex_{k}') is not None]
                if len(va) < 10 or len(vb) < 10:
                    continue
                t, p = bt.welch_t(va, vb)
                W(f"      T+{k:<3} {nm:<26} 差{bt._mean(va)-bt._mean(vb):>+7.2f}pct  "
                  f"t={bt._fmt(t):>6} p={bt._fmt(p,4)}"
                  f"{'  ★显著' if (p == p and p < 0.05) else ''}")
        # 按日配对（CONDITIONAL vs WATCH）
        for k in (5, 10):
            bd = defaultdict(list)
            for r in sub:
                bd[r['trade_date']].append(r)
            diffs = []
            for d, rs in bd.items():
                va = [r[f'ex_{k}'] for r in rs if r.get('_lay') == 'CONDITIONAL' and r.get(f'ex_{k}') is not None]
                vb = [r[f'ex_{k}'] for r in rs if r.get('_lay') == 'WATCH' and r.get(f'ex_{k}') is not None]
                if va and len(vb) >= 3:
                    diffs.append(sum(va) / len(va) - sum(vb) / len(vb))
            if len(diffs) >= 5:
                m = sum(diffs) / len(diffs)
                sd = bt._std(diffs)
                se = sd / (len(diffs) ** 0.5)
                t = m / se if se else float('nan')
                p = 2.0 * (1.0 - bt._norm_cdf(abs(t))) if se else float('nan')
                W(f"      按日配对 T+{k} COND-WATCH：有效日 {len(diffs)}  日均差 {m:+.3f}pct  "
                  f"t={bt._fmt(t)}  p={bt._fmt(p,4)}  差>0 {sum(1 for x in diffs if x>0)}/{len(diffs)}")

    # ── F. 真·时间外检验：2025 段相对 2026 标定集 ──
    W("\n" + "=" * 104)
    W("【F】时间外检验：阈值由 2026(<=20260918) 标定，在 2025 段上是否仍成立")
    W("=" * 104)
    for tag, a, b in (('2025H1', '20250101', '20250630'), ('2025H2', '20250701', '20251231'),
                      ('2026H1', '20260101', '20260630'), ('2026H2', '20260701', '20260922')):
        sub = [r for r in rows if a <= r['trade_date'] <= b]
        if not sub:
            continue
        for r in sub:
            r['_lay'] = layer(r, 5, 20)
        W(f"\n  ── {tag}（{len(sub)} 条 / {len({r['trade_date'] for r in sub})} 日）──")
        for lp in ('TRADEABLE', 'CONDITIONAL', 'WATCH'):
            sel = [r for r in sub if r['_lay'] == lp]
            va = [r['ex_5'] for r in sel if r.get('ex_5') is not None]
            v10 = [r['ex_10'] for r in sel if r.get('ex_10') is not None]
            ab = [r['fwd_5'] for r in sel if r.get('fwd_5') is not None]
            if len(va) < 10:
                W(f"    {lp:<12} n={len(va)}（不足）")
                continue
            W(f"    {lp:<12} n={len(va):<5} T+5超额{bt._mean(va):>7.2f}% (绝对{bt._mean(ab):>6.2f}%)  "
              f"胜率{bt._winrate(va):>5.1f}%   T+10超额{bt._mean(v10):>7.2f}%")
        c = [r for r in sub if r['_lay'] == 'CONDITIONAL']
        w = [r for r in sub if r['_lay'] == 'WATCH']
        t3 = [r for r in sub if r['_lay'] == 'TRADEABLE']
        for nm, aa, bb in (('COND vs WATCH', c, w), ('TRADE vs WATCH', t3, w)):
            for k in (5, 10):
                va = [r[f'ex_{k}'] for r in aa if r.get(f'ex_{k}') is not None]
                vb = [r[f'ex_{k}'] for r in bb if r.get(f'ex_{k}') is not None]
                if len(va) < 10 or len(vb) < 10:
                    continue
                t, p = bt.welch_t(va, vb)
                W(f"      {nm:<14} T+{k:<3} 差{bt._mean(va)-bt._mean(vb):>+7.2f}pct  "
                  f"t={bt._fmt(t):>6} p={bt._fmt(p,4)}{'  ★' if (p == p and p < 0.05) else ''}")

    # ── G. 横截面分位定档（每日内按 r20 排名）—— 解决绝对阈值跨 regime 漂移 ──
    W("\n" + "=" * 104)
    W("【G】改用当日横截面分位定档（每日排名，不依赖 2026 标定出的绝对阈值）")
    W("=" * 104)
    XS_LO, XS_HI = 10.0, 30.0

    def xs_layers(sub):
        for r in sub:
            r['_lay'] = 'NA'
        bd = defaultdict(list)
        for r in sub:
            if r.get('r20') is not None:
                bd[r['trade_date']].append(r)
        for d, rs in bd.items():
            rs.sort(key=lambda x: x['r20'])
            n = len(rs)
            lo = max(1, int(round(XS_LO / 100.0 * n)))
            hi = max(lo + 1, int(round(XS_HI / 100.0 * n)))
            for i, r in enumerate(rs):
                r['_lay'] = 'TRADEABLE' if i < lo else ('CONDITIONAL' if i < hi else 'WATCH')

    def report_xs(tag, sub):
        if not sub:
            return
        xs_layers(sub)
        W(f"\n  ── {tag}（{len(sub)} 条 / {len({r['trade_date'] for r in sub})} 日；"
          f"每日 r20 最低 {XS_LO:.0f}% = TRADEABLE，{XS_LO:.0f}~{XS_HI:.0f}% = CONDITIONAL）──")
        groups = {}
        for r in sub:
            groups.setdefault(r.get('_lay'), []).append(r)
        for lp in ('TRADEABLE', 'CONDITIONAL', 'WATCH'):
            sel = groups.get(lp, [])
            va = [r['ex_5'] for r in sel if r.get('ex_5') is not None]
            v10 = [r['ex_10'] for r in sel if r.get('ex_10') is not None]
            ab = [r['fwd_5'] for r in sel if r.get('fwd_5') is not None]
            if len(va) < 10:
                W(f"    {lp:<12} n={len(va)}（不足）")
                continue
            W(f"    {lp:<12} n={len(va):<5} r20中位{bt._median([r['r20'] for r in sel]):>7.2f}%  "
              f"T+5超额{bt._mean(va):>7.2f}% (绝对{bt._mean(ab):>6.2f}%)  胜率{bt._winrate(va):>5.1f}%  "
              f"T+10超额{bt._mean(v10):>7.2f}%")
        c = groups.get('CONDITIONAL', [])
        w = groups.get('WATCH', [])
        t3 = groups.get('TRADEABLE', [])
        for nm, aa, bb in (('COND vs WATCH', c, w), ('TRADE vs WATCH', t3, w)):
            for k in (5, 10):
                va = [r[f'ex_{k}'] for r in aa if r.get(f'ex_{k}') is not None]
                vb = [r[f'ex_{k}'] for r in bb if r.get(f'ex_{k}') is not None]
                if len(va) < 10 or len(vb) < 10:
                    continue
                t, p = bt.welch_t(va, vb)
                W(f"      {nm:<14} T+{k:<3} 差{bt._mean(va)-bt._mean(vb):>+7.2f}pct  "
                  f"t={bt._fmt(t):>6} p={bt._fmt(p,4)}{'  ★' if (p == p and p < 0.05) else ''}")

    for tag, sub in (('A 长窗口静态池', long_sub), ('B 生产窗口', prod_sub)):
        report_xs(tag, sub)
    W("\n  ── 分 regime 段（同一横截面规则，观察跨期一致性）──")
    for tag, a, b in (('2025H1', '20250101', '20250630'), ('2025H2', '20250701', '20251231'),
                      ('2026H1', '20260101', '20260630'), ('2026H2', '20260701', '20260922')):
        report_xs(tag, [r for r in rows if a <= r['trade_date'] <= b])

    # ── H. regime 门检验：是否存在事前可观测变量，能决定超跌因子方向 ──
    W("\n" + "=" * 104)
    W("【H】regime 门：能否事前判断「超跌落后档有效」还是「强者恒强有效」")
    W("=" * 104)

    # 全市场等权指数（32 主题指数等权日收益复利）
    mret = {}
    for th in ts:
        idx = ts[th][0]
        s = seq[th]
        for i in range(1, len(s)):
            d, p = s[i], s[i - 1]
            if p in idx and idx[p]:
                mret.setdefault(d, []).append(idx[d] / idx[p] - 1.0)
    mkt = {d: sum(v) / len(v) for d, v in mret.items()}
    md = sorted(mkt)
    mpos = {d: i for i, d in enumerate(md)}
    cum = {}
    c = 1.0
    for d in md:
        c *= (1.0 + mkt[d])
        cum[d] = c

    def mkt_chg(d, k):
        i = mpos.get(d)
        if i is None or i - k < 0:
            return None
        return (cum[d] / cum[md[i - k]] - 1.0) * 100.0

    bydate_r20 = defaultdict(list)
    for r in rows:
        if r.get('r20') is not None:
            bydate_r20[r['trade_date']].append(r['r20'])

    mvol = {}
    for th in ts:
        for d, v in ts[th][1].items():
            mvol.setdefault(d, []).append(v)
    mv = {d: sum(v) / len(v) for d, v in mvol.items()}

    def vol_shrink(d):
        i = mpos.get(d)
        if i is None or i < 19:
            return None
        w = [mv[x] for x in md[i - 19:i + 1] if x in mv]
        if len(w) < 20:
            return None
        return (sum(w[-5:]) / 5.0) / (sum(w) / 20.0)

    reg = {}
    for d, vs in bydate_r20.items():
        if mkt_chg(d, 20) is None:
            continue
        i = mpos[d]
        ma20 = sum(cum[x] for x in md[i - 19:i + 1]) / 20.0 if i >= 19 else None
        reg[d] = {
            'MKT20     市场等权指数20日收益%': mkt_chg(d, 20),
            'MKTDEV    市场指数距MA20偏离%': (cum[d] / ma20 - 1.0) * 100.0 if ma20 else None,
            'XSDISP    主题r20横截面标准差': bt._std(vs),
            'POSSHARE  主题r20>0占比%': 100.0 * sum(1 for v in vs if v > 0) / len(vs),
            'VOLSHK    市场量能5/20日比': vol_shrink(d),
        }

    # 逐日因子价差：当日 r20 最低 20% vs 其余 80%（用横截面去均值后的 ex_k，已无市场 beta）
    bd = defaultdict(list)
    for r in rows:
        if r.get('r20') is not None:
            bd[r['trade_date']].append(r)
    sp5, sp10 = {}, {}
    for d, rs in bd.items():
        rs.sort(key=lambda x: x['r20'])
        k = max(1, int(round(0.2 * len(rs))))
        bot, rest = rs[:k], rs[k:]
        for kk, store in ((5, sp5), (10, sp10)):
            a = [r[f'ex_{kk}'] for r in bot if r.get(f'ex_{kk}') is not None]
            b = [r[f'ex_{kk}'] for r in rest if r.get(f'ex_{kk}') is not None]
            if a and b and len(b) >= 3:
                store[d] = sum(a) / len(a) - sum(b) / len(b)

    def t1(xs):
        if len(xs) < 3:
            return float('nan'), float('nan')
        m = sum(xs) / len(xs)
        se = bt._std(xs) / (len(xs) ** 0.5)
        if not se:
            return float('nan'), float('nan')
        t = m / se
        return t, 2.0 * (1.0 - bt._norm_cdf(abs(t)))

    def corr(a, b):
        n = len(a)
        if n < 3:
            return float('nan')
        ma, mb = sum(a) / n, sum(b) / n
        sa = sum((x - ma) ** 2 for x in a) ** 0.5
        sb = sum((x - mb) ** 2 for x in b) ** 0.5
        if not sa or not sb:
            return float('nan')
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)

    W("\n  ── 逐日因子价差（r20 最低20% − 其余80%，T+5 超额）按各 regime 变量中位分组 ──")
    for name in list(next(iter(reg.values())).keys()):
        days = [d for d in md if d in sp5 and reg.get(d, {}).get(name) is not None]
        if len(days) < 20:
            W(f"\n  ■ {name}  样本不足（{len(days)} 日）")
            continue
        days.sort(key=lambda d: reg[d][name])
        mid = len(days) // 2
        W(f"\n  ■ {name}")
        for tag, grp in (('低组(<中位)', days[:mid]), ('高组(≥中位)', days[mid:])):
            v5 = [sp5[d] for d in grp]
            v10 = [sp10[d] for d in grp if d in sp10]
            t5, p5 = t1(v5)
            t10, p10 = t1(v10)
            W(f"      {tag:<12} {len(v5):>4} 日   spread T+5 {sum(v5)/len(v5):>+7.3f}pct (t={bt._fmt(t5):>6} p={bt._fmt(p5,4)})"
              f"   T+10 {sum(v10)/len(v10):>+7.3f}pct (t={bt._fmt(t10):>6} p={bt._fmt(p10,4)})")
        W(f"      相关系数 corr(regime, spread_T+5) = {corr([reg[d][name] for d in days], [sp5[d] for d in days]):+.4f}")

    # ── H2. 门控策略：只在有利半区启用超跌档，看全样本累计效果 ──
    W("\n  ── 门控后整体效果（只在有利半区的交易日启用「当日 r20 最低 20%」）──")
    W(f"      {'方案':<34}{'选中n':>7}{'T+5超额':>10}{'T+10超额':>10}{'胜率':>8}")
    base5 = [r['ex_5'] for r in rows if r.get('ex_5') is not None]
    W(f"      {'【基准】全部主题':<34}{len(base5):>7}{bt._mean(base5):>+10.2f}{bt._mean([r['ex_10'] for r in rows if r.get('ex_10') is not None]):>+10.2f}{bt._winrate(base5):>8.1f}")

    def bottom20(d):
        rs = bd.get(d, [])
        if not rs:
            return []
        rs = sorted(rs, key=lambda x: x['r20'])
        return rs[:max(1, int(round(0.2 * len(rs))))]

    allb = [r for d in md for r in bottom20(d)]
    v5 = [r['ex_5'] for r in allb if r.get('ex_5') is not None]
    v10 = [r['ex_10'] for r in allb if r.get('ex_10') is not None]
    W(f"      {'【对照】全部交易日都用超跌档':<34}{len(v5):>7}{bt._mean(v5):>+10.2f}{bt._mean(v10):>+10.2f}{bt._winrate(v5):>8.1f}")
    for name in list(next(iter(reg.values())).keys()):
        days = [d for d in md if reg.get(d, {}).get(name) is not None]
        if len(days) < 20:
            continue
        days.sort(key=lambda d: reg[d][name])
        for tag, grp in (('低组', days[:len(days) // 2]), ('高组', days[len(days) // 2:])):
            sel = [r for d in grp for r in bottom20(d)]
            v5 = [r['ex_5'] for r in sel if r.get('ex_5') is not None]
            v10 = [r['ex_10'] for r in sel if r.get('ex_10') is not None]
            if len(v5) < 10:
                continue
            t, p = t1(v5)
            W(f"      {('超跌档 · ' + name.split()[0] + ' ' + tag):<34}{len(v5):>7}{bt._mean(v5):>+10.2f}"
              f"{bt._mean(v10):>+10.2f}{bt._winrate(v5):>8.1f}   t={bt._fmt(t)} p={bt._fmt(p,4)}"
              f"{'  ★' if (p == p and p < 0.05) else ''}")

    # ── I. 顺势轨道（强者恒强）检验：top 档在各 regime 是否有效 ──
    W("\n" + "=" * 104)
    W("【I】顺势轨道：横截面 top 档（强者恒强）跨 regime 检验")
    W("=" * 104)

    for r in rows:
        if r.get('r20') is not None and r.get('r60') is not None:
            r['mom2060'] = 0.5 * r['r20'] + 0.5 * r['r60']

    SEGS = [('长窗口(334日)', long_sub),
            ('2025H1', [r for r in rows if r['trade_date'] <= '20250630']),
            ('2025H2', [r for r in rows if '20250701' <= r['trade_date'] <= '20251231']),
            ('2026H1', [r for r in rows if '20260101' <= r['trade_date'] <= '20260630']),
            ('2026H2', [r for r in rows if r['trade_date'] >= '20260701'])]

    def trend_cell(feat, kk, sub, frac=0.2):
        bd2 = defaultdict(list)
        for r in sub:
            if r.get(feat) is not None:
                bd2[r['trade_date']].append(r)
        sp = []
        for d, rs in bd2.items():
            if len(rs) < 5:
                continue
            rs.sort(key=lambda x: -(x[feat]))
            k = max(1, int(round(frac * len(rs))))
            a = [r[f'ex_{kk}'] for r in rs[:k] if r.get(f'ex_{kk}') is not None]
            b = [r[f'ex_{kk}'] for r in rs[k:] if r.get(f'ex_{kk}') is not None]
            if a and b:
                sp.append(sum(a) / len(a) - sum(b) / len(b))
        if len(sp) < 10:
            return None
        m = sum(sp) / len(sp)
        t, p = t1(sp)
        return m, t, p, len(sp)

    TREND_FEATS = ('r5', 'r20', 'r60', 'mom2060', 'dev_ma20', 'ma60_slope',
                   'pos20', 'wave20', 'wave40', 'shrink5_20')
    for kk in (5, 10):
        W(f"\n  ── 顺势因子 top20% 逐日价差（T+{kk} 超额；正=强者恒强有效，负=反转有效）──")
        W(f"      {'特征':<14}" + "".join(f"{t:>21}" for t, _ in SEGS))
        for feat in TREND_FEATS:
            line = f"      {feat:<14}"
            for tag, sub in SEGS:
                c = trend_cell(feat, kk, sub)
                line += f"{'—':>21}" if not c else f"{c[0]:>+8.2f} p={bt._fmt(c[2],3)}{'★' if (c[2] == c[2] and c[2] < 0.05) else ' ':>2}"
            W(line)

    # ── I2. V2.1 四维 top 档（仅生产窗口可用，62 日）──
    W("\n  ── V2.1 四维 top20% 在生产窗口内的表现（仅 62 日，样本内，不构成验证）──")
    try:
        v21rows, _ = bt.load_rows(None)
    except SystemExit:
        v21rows = []
    vmap = {(r['trade_date'], r['theme']): r for r in v21rows}
    for r in rows:
        v = vmap.get((r['trade_date'], r['theme']))
        if v:
            r['v_conf'] = v.get('confirmation')
            r['v_breadth'] = v.get('breadth')
            r['v_lead'] = v.get('leadership')
            r['v_pers'] = v.get('persistence')
            r['v_comp'] = v.get('composite')
            vals = [v.get(x) for x in ('confirmation', 'breadth', 'leadership', 'persistence')]
            r['v_bar'] = min([x for x in vals if x is not None]) if any(x is not None for x in vals) else None
    hit = sum(1 for r in rows if r.get('v_conf') is not None)
    W(f"      四维匹配置信率 {hit}/{len(rows)} = {100.0 * hit / len(rows):.1f}%")
    W(f"      {'特征':<14}{'top20% T+5':>16}{'p':>9}{'top20% T+10':>16}{'p':>9}")
    for feat in ('v_bar', 'v_conf', 'v_breadth', 'v_lead', 'v_pers', 'v_comp'):
        cells = [trend_cell(feat, kk, prod_sub) for kk in (5, 10)]
        if not cells[0]:
            continue
        W(f"      {feat:<14}{cells[0][0]:>+16.3f}{bt._fmt(cells[0][2],4):>9}"
          f"{cells[1][0] if cells[1] else float('nan'):>+16.3f}{bt._fmt(cells[1][2],4) if cells[1] else '—':>9}")

    # ── J. 拥挤度（高位 + 放量）五分档：唯一跨 regime 符号一致的维度 ──
    W("\n" + "=" * 104)
    W("【J】拥挤度 = pos20 分位 + shrink5_20 分位（越高=越接近前高且越放量）：五分档跨 regime")
    W("=" * 104)

    for tag, sub in SEGS:
        bd3 = defaultdict(list)
        for r in sub:
            if r.get('pos20') is not None and r.get('shrink5_20') is not None:
                bd3[r['trade_date']].append(r)
        W(f"\n  ── {tag}（{len(sub)} 条 / {len(bd3)} 日）──")
        W(f"      {'五分档':<12}{'n':>7}{'T+1超额':>10}{'T+5超额':>10}{'T+10超额':>10}")
        agg = defaultdict(list)
        for d, rs in bd3.items():
            if len(rs) < 5:
                continue
            pos20rk = {id(r): i for i, r in enumerate(sorted(rs, key=lambda x: x['pos20']))}
            shrk = {id(r): i for i, r in enumerate(sorted(rs, key=lambda x: x['shrink5_20']))}
            order = sorted(rs, key=lambda r: pos20rk[id(r)] + shrk[id(r)])
            for pos, r in enumerate(order):
                q = min(4, int(pos * 5 / len(rs)))
                agg[q].append(r)
        for q in range(5):
            sel = agg.get(q, [])
            v1 = [r['ex_1'] for r in sel if r.get('ex_1') is not None]
            v5 = [r['ex_5'] for r in sel if r.get('ex_5') is not None]
            v10 = [r['ex_10'] for r in sel if r.get('ex_10') is not None]
            if len(v5) < 10:
                continue
            lbl = f"Q{q+1}" + ('（最低）' if q == 0 else ('（最高）' if q == 4 else ''))
            W(f"      {lbl:<12}{len(v5):>7}{bt._mean(v1):>+10.2f}{bt._mean(v5):>+10.2f}{bt._mean(v10):>+10.2f}")
        lo, hi = agg.get(0, []), agg.get(4, [])
        for kk in (1, 5, 10):
            a = [r[f'ex_{kk}'] for r in hi if r.get(f'ex_{kk}') is not None]
            b = [r[f'ex_{kk}'] for r in lo if r.get(f'ex_{kk}') is not None]
            if len(a) < 10 or len(b) < 10:
                continue
            t, p = bt.welch_t(a, b)
            W(f"      Q5−Q1 T+{kk:<3} 差 {bt._mean(a)-bt._mean(b):>+7.3f}pct  t={bt._fmt(t):>6} "
              f"p={bt._fmt(p,4)}{'  ★' if (p == p and p < 0.05) else ''}")

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[保存] {OUT}")


if __name__ == '__main__':
    main()
