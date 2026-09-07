# -*- coding: utf-8 -*-
import json

data = json.load(open(r"d:\mystock\solo\tdx_300364.json", encoding="utf-8"))
rows = [r for r in data["rows"] if 20260701 <= r["date"] <= 20260904]

def mmdd(d):
    s = str(d)
    return s[4:6] + "-" + s[6:8]

labels  = [mmdd(r["date"]) for r in rows]
close   = [round(r["close"], 2) for r in rows]
pct     = [round(r["pct"], 2) for r in rows]
vol_wan = [round(r["volume"] / 1e4, 0) for r in rows]     # 万股
ma5_wan = [round(r["vol_ma5"] / 1e4, 0) for r in rows]    # 万股
amt_yi  = [round(r["amount"] / 1e8, 1) for r in rows]     # 亿元

out = "window.__ZWX = {\n"
out += " labels:" + json.dumps(labels, ensure_ascii=False) + ",\n"
out += " close:" + json.dumps(close) + ",\n"
out += " pct:" + json.dumps(pct) + ",\n"
out += " vol:" + json.dumps(vol_wan) + ",\n"
out += " ma5:" + json.dumps(ma5_wan) + ",\n"
out += " amt:" + json.dumps(amt_yi) + ",\n"
# 关键标注点: index of 涨停日 & 地量日
idxs = {r["date"]: i for i, r in enumerate(rows)}
out += " limitIdx:[" + str(idxs[20260731]) + "," + str(idxs[20260831]) + "],\n"
out += " diliangIdx:" + str(idxs[20260821]) + ",\n"
out += " n:" + str(len(rows)) + "\n};"
open(r"d:\mystock\solo\widget_data.js", "w", encoding="utf-8").write(out)
print("rows:", len(rows), "limitIdx", idxs[20260731], idxs[20260831], "diliangIdx", idxs[20260821])
print("done")
