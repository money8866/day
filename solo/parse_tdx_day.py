import struct, json

path = r"C:\new_tdx\vipdoc\sz\lday\sz300364.day"
out = r"d:\mystock\solo\tdx_300364.json"

buf = open(path, "rb").read()
n = len(buf) // 32
records = []
for i in range(n):
    d, o, h, l, c, amt, vol, _res = struct.unpack_from("<IIIIIfII", buf, i * 32)
    records.append({
        "date": d,
        "open": o / 100.0,
        "high": h / 100.0,
        "low": l / 100.0,
        "close": c / 100.0,
        "amount": round(amt, 0),
        "volume": vol,
    })
records.sort(key=lambda r: r["date"])

for i, r in enumerate(records):
    if i > 0:
        prev = records[i - 1]["close"]
        r["pct"] = round((r["close"] / prev - 1) * 100, 2)
        r["limit20"] = abs(r["close"] - round(prev * 1.2, 2)) < 0.011
    else:
        r["pct"] = None
        r["limit20"] = False

for i, r in enumerate(records):
    if i >= 4:
        ma5 = sum(records[j]["volume"] for j in range(i - 4, i + 1)) / 5
        r["vol_ma5"] = round(ma5)
        r["vol_ratio"] = round(r["volume"] / ma5, 2)
    else:
        r["vol_ma5"] = None
        r["vol_ratio"] = None

sel = [r for r in records if 20260615 <= r["date"] <= 20260904]
json.dump({"total": n, "first": records[0]["date"], "last": records[-1]["date"], "rows": sel},
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
print("total_records:", n, "| range:", records[0]["date"], "->", records[-1]["date"], "| out_rows:", len(sel))
