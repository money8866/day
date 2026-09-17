import pandas as pd
from collections import Counter

df = pd.read_csv("data/sector_phase_history.csv", dtype={"trade_date": str})
pat = Counter()
lag_cnt = Counter()
for sid, sub in df.sort_values(["sector_id", "trade_date"]).groupby("sector_id"):
    ph = sub["current_phase"].tolist()
    last_exit = {}
    for i, p in enumerate(ph):
        if i > 0 and p != ph[i - 1]:
            last_exit[ph[i - 1]] = i
            j = last_exit.get(p)
            if j is not None and (i - j) <= 3:
                pat[(ph[i - 1], p)] += 1
                lag_cnt[i - j] += 1
tot = sum(pat.values())
print("total osc:", tot)
print("lag dist:", dict(lag_cnt))
for k, v in pat.most_common(20):
    print(k, v)

# 转换全景
chg = df[df["phase_changed"]]
print("\ntop transitions:")
print(chg["phase_transition"].value_counts().head(20).to_string())
