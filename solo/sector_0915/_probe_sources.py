"""临时探针2：翻页拉取申万 index_member_all 全量，量化补齐后的覆盖增益（只读）。"""
import json
import re
import sys
import time

sys.path.insert(0, r"d:\mystock\solo")

import pandas as pd
import tushare as ts
from sli.utils import load_token

pro = ts.pro_api(load_token())

frames = []
off = 0
while True:
    d = pro.index_member_all(is_new="Y", offset=off, limit=3000)
    if d is None or d.empty:
        break
    frames.append(d)
    off += len(d)
    time.sleep(0.15)
    if len(d) < 3000:
        break
sw = pd.concat(frames, ignore_index=True)
print("全量行数:", len(sw), "股票数:", sw.ts_code.nunique())
print("L1/L2/L3 取值数:", sw.l1_name.nunique(), sw.l2_name.nunique(), sw.l3_name.nunique())
sw.to_parquet(r"d:\mystock\solo\sector_0915\cache\_probe_sw_member_all.parquet")

l3 = pd.read_parquet(r"D:\mystock\solo\sli\cache\classify_SW2021_L3.parquet")
mb = pd.read_parquet(r"D:\mystock\solo\sli\cache\members_20260907.parquet")
old3 = set()
idx2name = dict(zip(mb.index_code, [None] * len(mb)))
cov_codes = set(mb.index_code.unique())
old_names = set(l3[l3.index_code.isin(cov_codes)].industry_name.astype(str))
new_names = set(sw.l3_name.dropna().astype(str))
print("本地快照 L3 名称数:", len(old_names), " index_member_all L3 名称数:", len(new_names))
print("新增可得 L3 名称数:", len(new_names - old_names))
miss_local = set(l3[~l3.index_code.isin(cov_codes)].industry_name.astype(str))
print("本地缺失 L3 被 index_member_all 补齐:", len(miss_local & new_names), "/", len(miss_local))
print("仍未获得:", sorted(miss_local - new_names))


def nk(x):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(x or "")).replace("Ⅲ", "").replace("Ⅱ", "")


doc = json.load(open(r"d:\mystock\solo\sector_0915\sector_master.json", encoding="utf-8"))
S = doc["sectors"] if "sectors" in doc else doc
l3_members = sw.groupby("l3_name").ts_code.apply(set).to_dict()
l2_members = sw.groupby("l2_name").ts_code.apply(set).to_dict()
l1_members = sw.groupby("l1_name").ts_code.apply(set).to_dict()

print("\n===== 补齐后各 Sector 可获得的行业直接归属股票数（申万口径，仅计新增 L3/L2/L1 板块）=====")
gain = []
for s in S:
    keys = [nk(x) for x in (s.get("aliases") or []) + (s.get("subsectors") or []) +
            (s.get("eastmoney_industry_keywords") or []) + (s.get("keywords") or [])]
    keys = [k for k in keys if len(k) >= 2]
    hit3, hit2, hit1 = set(), set(), set()
    for nm in new_names:
        n = nk(nm)
        if any(k in n or n in k for k in keys):
            hit3 |= l3_members.get(nm, set())
    for nm in set(sw.l2_name.dropna().astype(str)):
        n = nk(nm)
        if any(k in n or n in k for k in keys):
            hit2 |= l2_members.get(nm, set())
    for nm in set(sw.l1_name.dropna().astype(str)):
        n = nk(nm)
        if any(k in n or n in k for k in keys):
            hit1 |= l1_members.get(nm, set())
    gain.append((s["sector_id"], s["sector_name"], len(hit3), len(hit2), len(hit1),
                 len(hit3 | hit2 | hit1)))

q = pd.read_csv(r"d:\mystock\solo\sector_0915\output\sector_quality.csv")
cur = dict(zip(q.sector_id, q.fixed_member_count))
g = pd.DataFrame(gain, columns=["sid", "name", "L3", "L2", "L1", "union"])
g["当前固定层"] = g.sid.map(cur)
g["增益"] = g.union - g["当前固定层"]
print(g.sort_values("增益", ascending=False).head(25).to_string(index=False))
print("\nRAW_ONLY 补齐情况:")
print(g[g.sid.isin(["T06", "T11", "T38", "T50"])].to_string(index=False))
