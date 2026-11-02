# -*- coding: utf-8 -*-
"""S6 四模块全条件诊断（口径与 V10.0 对齐，一次性 SQL 采集）

事实标签：未来 20 日「等权行业 ETF 基准」收益 > 阈值。
方法论：每个条件单独算「通过率 + 通过样本的事后强市率」，
        用相对基准的提升（pp）衡量该条件的边际价值。
        提升 ≤0 的条件 = 纯噪声或反向，应移除或改为加分项。
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import etf_mainline as em
import market_regime_v101 as v101

cfg = v101.load_config()
db = r'D:\mystock\cache_daily\stock_data.db'
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.execute("PRAGMA busy_timeout=60000")

dates = [r[0] for r in conn.execute(
    "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>='20260101' ORDER BY trade_date")]

def thr_of(codes):
    return codes.map(lambda c: 20.0 if str(c)[:3] in ('300', '301', '688') else 10.0)

def zt_dt(df):
    """与 V10.0 _count_limit 同口径"""
    thr = thr_of(df['ts_code'])
    pct = df['pct_chg'].astype(float)
    zt = int(((pct >= thr*0.99) & (pct < thr+0.1)).sum())
    dt = int(((pct <= -thr*0.99) & (pct > -thr-0.1)).sum())
    return zt, dt

print(f"采集 {len(dates)} 天…", flush=True)
frames = {}
for i, d in enumerate(dates):
    df = pd.read_sql_query(
        "SELECT ts_code, pct_chg, amount FROM daily_cache WHERE trade_date=?",
        conn, params=(d,))
    if df.empty: continue
    frames[d] = df
    if (i+1) % 60 == 0: print(f"  {i+1}/{len(dates)}", flush=True)
conn.close()

# ---- 事实标签：未来 20 日等权行业基准收益 ----
panel = em.load_etf_panel()
series = em.compute_industry_series(panel, em.load_config())
idx_all = sorted(panel["trade_date"].unique())
nav_by_ind = {ind: s.set_index("trade_date")["nav"].reindex(idx_all).values
              for ind, s in series.items()}
bench = np.mean(list(nav_by_ind.values()), axis=0)
pos_of = {d: i for i, d in enumerate(idx_all)}

recs = []
for d in dates:
    df = frames[d]
    up = int((df.pct_chg > 0).sum()); dn = int((df.pct_chg < 0).sum())
    zt, dt_ = zt_dt(df)
    # 昨日涨停股今日表现 + 连板晋级率
    di = dates.index(d)
    smean = sup = promo = None
    if di >= 1:
        ydf = frames[dates[di-1]]
        y_thr = thr_of(ydf['ts_code'])
        ypct = ydf['pct_chg'].astype(float)
        y_zt = set(ydf.loc[(ypct >= y_thr*0.99) & (ypct < y_thr+0.1), 'ts_code'])
        if y_zt:
            tdf = df[df['ts_code'].isin(y_zt)]
            if len(tdf):
                pcts = tdf['pct_chg'].astype(float).values
                smean = float(pcts.mean())
                sup = float((pcts > 0).mean())
                n_thr = thr_of(tdf['ts_code'])
                cont = int(((pcts >= n_thr.values*0.99) & (pcts < n_thr.values+0.1)).sum())
                promo = cont/len(y_zt)*100
    i = pos_of.get(d)
    fb = (bench[i+20]/bench[i]-1)*100 if (i is not None and i+20 < len(bench)) else None
    recs.append({'d': d, 'ratio': up/(up+dn)*100 if (up+dn) else np.nan,
                 'zt': zt, 'dt': dt_,
                 'amt': float(df.amount.sum())/1e5,
                 'smean': smean, 'sup': sup, 'promo': promo,
                 'fwd_bench': fb})

mkt = pd.DataFrame(recs)
mkt['ma5'] = mkt.ratio.rolling(5).mean()
mkt['ma20'] = mkt.ratio.rolling(20).mean()
mkt['prev_ratio'] = mkt.ratio.shift(1)
mkt['ma5_prev'] = mkt.ma5.shift(1)
mkt['amt_ma20'] = mkt.amt.rolling(20).mean()
mkt['amt_ratio'] = mkt.amt/mkt.amt_ma20
mkt.to_csv("_bt_out/six_diag_panel.csv", index=False)
print(f"面板 {len(mkt)} 行\n")

b = cfg["strong_uptrend"]["breadth"]; m = cfg["strong_uptrend"]["money_effect"]
P = 100.0   # 配置用比例，面板用百分数

def report(title, thresh=3.0):
    lab = mkt.fwd_bench > thresh
    base = 100*lab.mean()
    print(f"=== {title}（标签：未来20日基准收益>{thresh}%，基准 {base:.1f}%）===")
    def ev(nm, mask):
        ok = mask.fillna(False) if hasattr(mask, 'fillna') else mask
        n = int(ok.sum())
        if n == 0:
            print(f"  {nm:<32} 通过  0.0%  -")
            return
        tp = int((ok & lab).sum())
        prec = 100*tp/n
        print(f"  {nm:<32} 通过{100*ok.mean():>5.1f}%  事后强市{prec:>5.1f}%  ({prec-base:+.1f}pp)")
    return lab, base, ev

lab, base, ev = report("各条件边际价值")

print("\n--- 广度模块 ---")
ev("C1 当日占比>=55%",      mkt.ratio >= b['breadth_ratio_min']*P)
ev("C2 5日均>=50%",         mkt.ma5 >= b['breadth_ma5_min']*P)
ev("C3 20日均>=45%",        mkt.ma20 >= b['breadth_ma20_min']*P)
ev("C4 连续2日>=55%",       (mkt.prev_ratio>=b['breadth_ratio_min']*P)&(mkt.ratio>=b['breadth_ratio_min']*P))
ev("C5 涨停>=30",           mkt.zt >= b['zt_count_min'])
ev("C6 跌停<=30",           mkt.dt <= b['dt_count_max'])

print("\n--- 短线赚钱效应模块 ---")
ev("M1 涨停>=40",           mkt.zt >= m['zt_count_min'])
ev("M2 跌停<=25",           mkt.dt <= m['dt_count_max'])
ev("M3 晋级率>=25%",        mkt.promo >= m['promotion_rate_min'])
ev("M4 强股均值>=0%",       mkt.smean >= m['strong_stock_mean_pct_min'])
ev("M5 强股上涨比>=50%",    mkt.sup >= m['strong_stock_up_ratio_min'])

print("\n--- 广度口径对比（同一标签）---")
for thr in (30,35,40,45,50,55):
    ev(f"5日均>={thr}%", mkt.ma5 >= thr)
print()
for thr in (25,30,35,40,45):
    ev(f"20日均>={thr}%", mkt.ma20 >= thr)
print()
for thr in (45,50,55,60,65):
    ev(f"当日占比>={thr}%", mkt.ratio >= thr)
print("\n--- 相对口径 ---")
ev("5日均>=20日均(改善)", mkt.ma5 >= mkt.ma20)
ev("5日均>=前日5日均(连续改善)", mkt.ma5 >= mkt.ma5_prev)
ev("当日>=5日均", mkt.ratio >= mkt.ma5)
ev("涨停>=40 且 强股均值>=0", (mkt.zt>=40)&(mkt.smean>=0))
ev("涨停>=40 且 强股上涨比>=50%", (mkt.zt>=40)&(mkt.sup>=0.5))
ev("组合: 5日均>=40 且 涨停>=40", (mkt.ma5>=40)&(mkt.zt>=40))
ev("组合: 5日均>=40 且 5日均>=20日均", (mkt.ma5>=40)&(mkt.ma5>=mkt.ma20))
