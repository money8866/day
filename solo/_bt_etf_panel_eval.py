# -*- coding: utf-8 -*-
"""构建行业面板（按位置索引）+ 用事后收益评估主线判定口径。

★ 关键：所有前瞻收益必须用**完整序列按位置索引**计算，
  不能用被截断到 td 的子序列（否则 fwd20 全为 NaN）。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import etf_mainline as em

cfg = em.load_config()
tr = cfg["trend"]; mf = cfg["money_flow"]; bd = cfg["breadth"]; rs = cfg["relative_strength"]
panel = em.load_etf_panel()
series = em.compute_industry_series(panel, cfg)

dates = sorted(panel["trade_date"].unique())
dates = [d for d in dates if str(d) >= '20260101']
idx_all = sorted(panel["trade_date"].unique())
pos_of = {d: i for i, d in enumerate(idx_all)}

# ---- 基准：所有行业 nav 等权（按同一 index 对齐）----
nav_mat = {}
for ind, s in series.items():
    nav_mat[ind] = s.set_index("trade_date")["nav"].reindex(idx_all).values
usable = {k: v for k, v in nav_mat.items() if not np.isnan(v).any()}
bench_nav = np.mean(list(usable.values()), axis=0)

MA20W = tr["ma20_window"]; MA60W = tr["ma60_window"]; AMTW = mf["amt_window"]

rows = []
for ind, s in series.items():
    sub = s.set_index("trade_date").reindex(idx_all)
    nav = sub["nav"].values
    amt = sub["amount"].values
    up = sub["up_ratio"].values
    ab20 = sub["above20_ratio"].values
    n_etf = sub["etf_count"].values
    b20 = sub["nav"].rolling(MA20W, min_periods=5).mean().values
    b60 = sub["nav"].rolling(MA60W, min_periods=5).mean().values
    bamt = sub["amount"].rolling(AMTW, min_periods=5).mean().values
    for d in dates:
        i = pos_of[d]
        if i < MA60W or np.isnan(nav[i]) or np.isnan(nav[i-MA60W]):
            continue
        r5 = (nav[i]/nav[i-5]-1)*100 if i >= 5 else None
        r20 = (nav[i]/nav[i-20]-1)*100 if i >= 20 else None
        r60 = (nav[i]/nav[i-60]-1)*100 if i >= 60 else None
        br20 = (bench_nav[i]/bench_nav[i-20]-1)*100 if i >= 20 else None
        br60 = (bench_nav[i]/bench_nav[i-60]-1)*100 if i >= 60 else None
        ex20 = (r20-br20) if (r20 is not None and br20 is not None) else None
        ex60 = (r60-br60) if (r60 is not None and br60 is not None) else None
        hi20 = float(np.max(nav[i-19:i+1]))
        fwd20 = (nav[i+20]/nav[i]-1)*100 if i+20 < len(nav) and not np.isnan(nav[i+20]) else None
        # ★ 基准的未来 20 日收益（用于算真实的「未来相对超额」）
        bfwd20 = ((bench_nav[i+20]/bench_nav[i]-1)*100
                  if i+20 < len(bench_nav) and not np.isnan(bench_nav[i+20]) else None)
        rows.append({
            "d": str(d), "ind": ind, "nav": float(nav[i]),
            "above20": bool(nav[i] > b20[i]) if not np.isnan(b20[i]) else None,
            "above60": bool(nav[i] > b60[i]) if not np.isnan(b60[i]) else None,
            "dd20": (nav[i]/hi20-1)*100,
            "ret5": r5, "ret20": r20, "ret60": r60,
            "ex20": ex20, "ex60": ex60,
            "amt_ratio": (amt[i]/bamt[i]) if (bamt[i] and bamt[i] > 0 and not np.isnan(bamt[i])) else None,
            "above20_ratio": float(ab20[i]) if not np.isnan(ab20[i]) else None,
            "up_ratio": float(up[i]) if not np.isnan(up[i]) else None,
            "etf_count": int(n_etf[i]) if not np.isnan(n_etf[i]) else 0,
            "fwd20": fwd20,
            "bfwd20": bfwd20,
            "fwd_ex": None,
        })

df = pd.DataFrame(rows)
df["fwd_ex"] = df["fwd20"] - df["bfwd20"]
df.to_csv("_bt_out/etf_industry_panel.csv", index=False)
print(f"面板 {len(df)} 行 / {df.d.nunique()} 天 / {df.ind.nunique()} 行业")
print(f"fwd20 非空 {df.fwd20.notna().sum()} 行\n")

ev_df = df[df["fwd20"].notna()].copy()
ev_df["label"] = (ev_df["fwd_ex"] > 5).astype(int)
pos = int(ev_df["label"].sum())
print(f"事实标签（未来20日相对基准超额>5%）：正样本 {pos} / {len(ev_df)} "
      f"({100*pos/max(1,len(ev_df)):.0f}%)")

D = ev_df
def ev(name, mask):
    tp = int((mask & (D.label == 1)).sum()); n = int(mask.sum())
    prec = 100*tp/max(1, n); rec = 100*tp/max(1, pos)
    f1 = 2*prec*rec/max(1e-9, prec+rec)
    print(f"  {name:48s} 命中{n:>5}  准确{prec:>3.0f}%  召回{rec:>3.0f}%  F1{f1:>5.1f}")

print("\n=== 候选口径评估 ===")
ev("当前: ex20>=2 & above20 & ex60>=3",
   (D.ex20>=2)&(D.above20==True)&(D.ex60>=3))
ev("当前+量能: & amt>=1.15",
   (D.ex20>=2)&(D.above20==True)&(D.ex60>=3)&(D.amt_ratio>=1.15))
ev("当前+宽度: & above20_ratio>=0.5",
   (D.ex20>=2)&(D.above20==True)&(D.ex60>=3)&(D.above20_ratio>=0.5))
print()
for nm, m in [
  ("A: ex20>=2 & ex60>=3 & (above20|dd20>=-12)", (D.ex20>=2)&(D.ex60>=3)&((D.above20==True)|(D.dd20>=-12))),
  ("B: ex20>=2 & ex60>=3 & dd20>=-12",           (D.ex20>=2)&(D.ex60>=3)&(D.dd20>=-12)),
  ("C: ex60>=10 & dd20>=-12",                    (D.ex60>=10)&(D.dd20>=-12)),
  ("D: ex60>=10 & (above20|dd20>=-12)",          (D.ex60>=10)&((D.above20==True)|(D.dd20>=-12))),
  ("E: ex20>=2 & ex60>=3 & (above20|dd20>=-10)", (D.ex20>=2)&(D.ex60>=3)&((D.above20==True)|(D.dd20>=-10))),
  ("F: ex20>=2 & ex60>=3 & (above20|dd20>=-15)", (D.ex20>=2)&(D.ex60>=3)&((D.above20==True)|(D.dd20>=-15))),
  ("G: ex20>=2 & ex60>=3 & dd20>=-10",           (D.ex20>=2)&(D.ex60>=3)&(D.dd20>=-10)),
]:
    ev(nm, m)

print("\n=== dd20 阈值敏感性（ex20>=2 & ex60>=3）===")
for thr in (-6,-8,-10,-12,-15,-20,-25,-30):
    ev(f"   dd20>={thr}", (D.ex20>=2)&(D.ex60>=3)&((D.above20==True)|(D.dd20>=thr)))
print("\n=== ex60 阈值敏感性（ex20>=2 & dd20>=-12）===")
for thr in (3,5,8,10,15,20):
    ev(f"   ex60>={thr}", (D.ex20>=2)&(D.ex60>=thr)&((D.above20==True)|(D.dd20>=-12)))
print("\n=== ex20 阈值敏感性（ex60>=3 & dd20>=-12）===")
for thr in (0,1,2,3,5,8):
    ev(f"   ex20>={thr}", (D.ex20>=thr)&(D.ex60>=3)&((D.above20==True)|(D.dd20>=-12)))
