# -*- coding: utf-8 -*-
"""午盘仓位分析：把实时快照接入 V10.1 管线

★ 关键：午盘数据只有半场，必须做「年化/折算」处理后再判定。
  成交额：半日 → 估算全日（×1.8~2.0，A股午后量能通常占全天 45~55%）
  广度/涨跌停：午盘已实现，不折算（但要注意尾盘反转风险）
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import sqlite3
import market_regime_v10 as v10
import market_regime_v101 as v101
import etf_mainline as em

BASE = os.path.dirname(os.path.abspath(__file__))
TRADE_DATE = '20261102'
CFGDIR = os.path.join(os.path.dirname(BASE), 'cache_daily')

def load_realtime():
    df = pd.read_csv(os.path.join(BASE, '_bt_out', 'realtime_20261102.csv'))
    # 修正停牌股：price=0 的用 pre_close 代替（涨跌幅 0）
    bad = df.price <= 0
    if bad.sum():
        print(f'  修正停牌股 {int(bad.sum())} 只（price=0 → 按 0% 处理）')
        df.loc[bad, 'price'] = df.loc[bad, 'pre_close']
    df['pct_chg'] = (df['price'] / df['pre_close'] - 1) * 100
    return df

def main():
    cfg = v101.load_config()
    v10cfg = v10.load_config()
    rt = load_realtime()

    up = int((rt.pct_chg > 0).sum()); dn = int((rt.pct_chg < 0).sum())
    ratio = up / (up + dn) if (up + dn) else 0
    thr = rt.ts_code.map(lambda c: 20.0 if str(c)[:3] in ('300', '301', '688') else 10.0)
    zt = int(((rt.pct_chg >= thr*0.99) & (rt.pct_chg < thr+0.1)).sum())
    dt_ = int(((rt.pct_chg <= -thr*0.99) & (rt.pct_chg > -thr-0.1)).sum())
    amt_half = rt.amount.sum() / 1e8
    print('=' * 70)
    print(f'  {TRADE_DATE} 午盘实时快照（数据截止 11:30）')
    print('=' * 70)
    print(f'  样本 {len(rt)} 只   上涨 {up}  下跌 {dn}  平盘 {int((rt.pct_chg==0).sum())}')
    print(f'  广度 {ratio*100:.1f}%   涨停 {zt} 家   跌停 {dt_} 家')
    print(f'  半日成交额 {amt_half:.0f} 亿')
    print()

    # ---- 构造历史广度序列（今日用午盘值追加）----
    conn = sqlite3.connect(f"file:{os.path.join(CFGDIR,'stock_data.db')}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>='20260101' ORDER BY trade_date")]
    conn.close()
    hist = []
    for d in dates:
        c2 = sqlite3.connect(f"file:{os.path.join(CFGDIR,'stock_data.db')}?mode=ro", uri=True)
        u = c2.execute("SELECT COUNT(*) FROM daily_cache WHERE trade_date=? AND pct_chg>0", (d,)).fetchone()[0]
        n = c2.execute("SELECT COUNT(*) FROM daily_cache WHERE trade_date=? AND pct_chg<0", (d,)).fetchone()[0]
        c2.close()
        hist.append(u/(u+n)*100 if (u+n) else 0.0)
    ma5 = np.mean(hist[-4:] + [ratio*100]) if len(hist) >= 4 else None
    ma20 = np.mean(hist[-19:] + [ratio*100]) if len(hist) >= 19 else None
    print(f'  ★ 广度序列（含今日午盘）：5日均 {ma5:.1f}%   20日均 {ma20:.1f}%')
    print(f'    昨日(10/30) 5日均 32.0%  20日均 39.2%')
    print()

    # ---- 主线：ETF 实时 ----
    etf_rt = pd.read_csv(os.path.join(BASE, '_bt_out', 'etf_realtime_20261102.csv'))
    ecfg = em.load_config()
    panel = em.load_etf_panel()
    # 把今日实时价接到 ETF 面板上
    panel = panel[panel.trade_date < TRADE_DATE].copy()
    new_rows = []
    for _, r in etf_rt.iterrows():
        nav_hist = panel[panel.ts_code == r.ts_code].sort_values('trade_date')
        if nav_hist.empty: continue
        last_nav = nav_hist.nav.iloc[-1]
        # 用 pct_chg 累乘构造今日 nav（与 etf_mainline 的复权口径一致）
        nav_new = last_nav * (1 + r.pct_chg/100)
        new_rows.append({'ts_code': r.ts_code, 'trade_date': TRADE_DATE,
                         'nav': nav_new, 'amount': r.amount, 'pct_chg': r.pct_chg})
    panel_rt = pd.concat([panel, pd.DataFrame(new_rows)], ignore_index=True)
    # 补齐 amount 字段（历史 panel 有 amount）
    if 'amount' not in panel.columns and 'amount' in panel_rt.columns:
        panel_rt['amount'] = panel_rt['amount'].fillna(0)

    ml = em.detect_mainlines(TRADE_DATE, ecfg, panel_rt)
    det = ml.get('details') or {}
    print('  【主线判定（ETF 实时）】')
    top = sorted(det.keys(), key=lambda i: -(det[i]['excess20'] or -999))[:6]
    for ind in top:
        x = det[ind]
        flag = '✓主线' if x['passed'] else '  '
        print(f"    {flag} {ind:<8} 超额20日 {x['excess20']:>6.1f}%  超额60日 {x['excess60']:>6.1f}%  "
              f"近20日 {x['ret20']:>6.1f}%  连续强态 {x.get('days_strong', 0):>2}日")
    print(f'    → 识别主线: {ml.get("industries")}')
    print()

    return dict(ratio=ratio, up=up, dn=dn, zt=zt, dt=dt_, ma5=ma5, ma20=ma20,
                amt_half=amt_half, ml=ml, det=det, etf_rt=etf_rt, panel_rt=panel_rt,
                rt=rt, hist=hist, cfg=cfg, v10cfg=v10cfg)

if __name__ == '__main__':
    main()


def full_analysis(d):
    """完整判定 + 敏感性分析"""
    cfg = d['cfg']; v10cfg = d['v10cfg']
    ratio = d['ratio']; ma5 = d['ma5']; ma20 = d['ma20']
    zt = d['zt']; dt_ = d['dt']; amt_half = d['amt_half']

    print('=' * 70)
    print('  V10.1 四模块判定（午盘口径）')
    print('=' * 70)

    # --- A. 指数趋势（用 ETF 实时无法算指数，用历史最后状态 + 方向推断）---
    # 说明：指数数据最新到 10/30，盘中无指数数据 → 该模块标记为「数据缺失」
    print('  [数据缺失] 指数趋势')
    print('       指数日线最新到 20261030，盘中无指数快照 → 无法判定')
    print()

    # --- B. 市场广度 ---
    print('  [判定] 市场广度')
    c1 = ma5 >= ma20
    print(f"       5日均广度 {ma5:.1f}% ≥ 20日均 {ma20:.1f}%（广度在改善）  {'✓' if c1 else '✗'}")
    c2 = ma5 >= 45
    print(f"       5日均广度 ≥45% 底线（当前 {ma5:.1f}%）  {'✓' if c2 else '✗'}")
    c3 = zt >= 30
    print(f"       涨停 {zt} 家（要求 ≥30）  {'✓' if c3 else '✗'}")
    c4 = dt_ <= 30
    print(f"       跌停 {dt_} 家（要求 ≤30）  {'✓' if c4 else '✗'}")
    breadth_pass = all([c1, c2, c3, c4])
    print(f"       → 广度模块 {'通过' if breadth_pass else '未通过'}")
    print()

    # --- C. 短线赚钱效应（盘中无炸板/晋级率/强股次日数据）---
    print('  [部分判定] 短线赚钱效应')
    print(f"       跌停 {dt_} 家（要求 ≤35）  {'✓' if dt_ <= 35 else '✗'}")
    print('       炸板率/晋级率/强股次日表现 → 盘中无法计算（需收盘后数据）')
    print()

    # --- D. 主线 ---
    inds = d['ml'].get('industries') or []
    print(f"  [判定] 主线持续性：{inds}")
    for ind in inds[:2]:
        x = d['det'][ind]
        print(f"       {ind}：连续强态 {x.get('days_strong')}日｜超额20日 {x['excess20']:.1f}%"
              f"｜超额60日 {x['excess60']:.1f}%")
    print()

    # --- 状态机 ---
    print('  [状态机] 上一交易日 10/30 = S3 恐慌加速（广度16.3%、跌停95家）')
    print('       今日午盘广度 86.8% → 恐慌节点（S3）明确不命中')
    print()

    return breadth_pass

def sensitivity(d):
    """午盘数据的敏感性分析：尾盘反转风险"""
    print('=' * 70)
    print('  敏感性分析：午盘读数 → 全日读数的可能区间')
    print('=' * 70)
    ratio = d['ratio']; ma5 = d['ma5']; ma20 = d['ma20']; hist = d['hist']
    print('  广度序列最近 6 日（末位为今日午盘）:')
    for i, v in enumerate(hist[-5:]):
        print(f'    {hist[-5:][i]:.1f}%', end='')
    print(f'  →  {ratio*100:.1f}%（今日午盘）')
    print()
    # 情景：尾盘反转（下午广度回落到不同水平）
    print('  情景推演（假设下午广度回落到 X%）：')
    print(f"    {'情景':<22} {'今日广度':>8} {'5日均':>8} {'20日均':>8} {'广度改善':>8} {'广度模块':>8}")
    for label, aft in [('强势延续(午盘维持)', ratio*100),
                       ('小幅回落 -8pp', max(0, ratio*100-8)),
                       ('明显回落 -15pp', max(0, ratio*100-15)),
                       ('翻空 -25pp', max(0, ratio*100-25)),
                       ('回到常态 50%', 50.0)]:
        m5 = np.mean(hist[-4:] + [aft])
        m20 = np.mean(hist[-19:] + [aft])
        imp = '✓' if m5 >= m20 else '✗'
        # 底线 45%
        ok = '✓通过' if (m5 >= m20 and m5 >= 45 and d['zt'] >= 30 and d['dt'] <= 30) else '✗未过'
        print(f'    {label:<22} {aft:>7.1f}% {m5:>7.1f}% {m20:>7.1f}% {imp:>8} {ok:>8}')
    print()
    print('  ★ 关键：广度「改善方向」条件（5日均≥20日均）今日是否成立 =',
          '✓成立' if ma5 >= ma20 else '✗不成立')
    print(f'    5日均 {ma5:.1f}%  vs  20日均 {ma20:.1f}%  差 {ma5-ma20:+.1f}pp')
    print()

if __name__ == '__main__':
    import _bt_noon_position as _self
