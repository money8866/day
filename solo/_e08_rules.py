# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv("e08_prev_t0.csv", dtype={"ts_code": str, "date_t0": str})
b = df[df["sim5_ret"].notna()].copy()

def r(cond, name):
    s = b[cond.reindex(b.index).fillna(False)]
    if s.empty:
        print("%-28s 无样本" % name); return
    print("%-28s n=%4d 均值%+6.2f%% 中位%+6.2f%% 超额%+6.2f%% 胜率%4.0f%% 最大盈%+5.1f%% MA10%+6.2f%%"
          % (name, len(s), s["sim5_ret"].mean(), s["sim5_ret"].median(), s["sim5_ex"].mean(),
             (s["sim5_ret"] > 0).mean() * 100, s["sim5_mx"].mean(), s["sim10_ret"].mean()))

print("== 组合规则（回踩 MA5 口径） ==")
r(pd.Series(True, index=b.index), "R0 全部")
r(b["prev_pct"] >= 0, "R1 前一日阳线(>=0)")
r(b["prev_pct"] >= 3, "R2 前一日中阳以上(>=3)")
r((b["prev_pct"] >= 3) & (b["prev_pct"] < 7), "R3 前一日中阳(3~7)")
r((b["prev_pct"] >= 7) & (b["prev_vol_mult"] >= 1.5), "R4 前一日长阳且放量>=1.5x")
r((b["prev_pct"] >= 3) & (b["gap"] < 4), "R5 中阳以上 且 缺口<4%")
r((b["prev_pct"] >= 0) & ~((b["prev_pct"] >= 7) & (b["prev_vol_mult"] < 1.5)), "R6 阳线 且 剔除缩量长阳")
r((b["prev_pct"] >= 3) & (b["gap"] < 4)
  & ~((b["prev_pct"] >= 7) & (b["prev_vol_mult"] < 1.5)), "R7 中阳以上+缺口<4%+去缩量长阳")
r(b["prev_pct"] < 0, "对照组 前一日阴线")
