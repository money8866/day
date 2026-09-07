import json, math

data = json.load(open(r"d:\mystock\solo\tdx_300364.json", encoding="utf-8"))
rows = [r for r in data["rows"] if 20260701 <= r["date"] <= 20260904]
n = len(rows)

# price plot: viewBox 640x260, area x 24..616, y 16..232
x0, x1, y0, y1 = 24, 616, 16, 232
cmin = min(r["close"] for r in rows)
cmax = max(r["close"] for r in rows)
px = [round(x0 + i * (x1 - x0) / (n - 1), 1) for i in range(n)]
py = [round(y1 - (r["close"] - cmin) / (cmax - cmin) * (y1 - y0), 1) for r in rows]

price_pts = " ".join(f"{px[i]},{py[i]}" for i in range(n))
area_path = f"M{px[0]},{y1}L" + "L".join(f"{px[i]},{py[i]}" for i in range(n)) + f"L{px[-1]},{y1}Z"

# volume plot: viewBox 640x200, area x 24..616, y 20..184
vx0, vx1, vy0, vy1 = 24, 616, 20, 184
vmax = max(r["volume"] for r in rows)
bw = 8
bars = []
for i, r in enumerate(rows):
    h = max(2, round(r["volume"] / vmax * (vy1 - vy0)))
    x = round(px[i] - bw / 2, 1)
    y = vy1 - h
    cls = "fbUp" if (r["pct"] or 0) > 0 else "fbDown"
    bars.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{bw}" height="{h}"/>')
bars_str = "\n          ".join(bars)

# volume MA5 line
vpy = [round(vy1 - (r["vol_ma5"] / vmax) * (vy1 - vy0), 1) for r in rows]
ma_pts = " ".join(f"{px[i]},{vpy[i]}" for i in range(n))

lim_idx = [i for i, r in enumerate(rows) if r["limit20"]]
print("LIMIT_IDX", lim_idx, [rows[i]["date"] for i in lim_idx])
print("PRICE_PTS")
print(price_pts)
print("AREA_PATH")
print(area_path)
print("MA_PTS")
print(ma_pts)
print("BARS")
print(bars_str)

# stats
def seg(a, b):
    ia = next(i for i, r in enumerate(rows) if r["date"] == a)
    ib = next(i for i, r in enumerate(rows) if r["date"] == b)
    return rows[ia:ib + 1]

w1 = seg(20260731, 20260807)
w2 = seg(20260810, 20260828)
w3 = seg(20260831, 20260904)
for name, s in [("wave1", w1), ("pullback", w2), ("wave2", w3)]:
    amt = sum(r["amount"] for r in s)
    vol = sum(r["volume"] for r in s)
    print(name, "days:", len(s), "range:", s[0]["date"], "-", s[-1]["date"],
          "close:", s[0]["close"], "->", s[-1]["close"],
          f"chg {(s[-1]['close']/s[0]['close']-1)*100:.1f}%",
          "avg_vol(M):", round(vol/len(s)/1e6, 2), "amt(yi):", round(amt/1e8, 1))
dyq = rows[[i for i, r in enumerate(rows) if r['date'] == 20260821][0]]
peak = max(w1, key=lambda r: r["volume"])
print("diliang:", dyq["volume"]/1e6, "vs peak:", peak["volume"]/1e6, "ratio:", round(dyq["volume"]/peak["volume"], 3))
print("831 vs 828 vol x:", round(143049002/52541712, 2))
print("low 7/24:", [r for r in data["rows"] if r["date"] == 20260724][0]["low"])
print("hi 9/1:", [r for r in rows if r["date"] == 20260901][0]["high"])
print("rise 7/24low->9/1hi:", round((29.90/18.45-1)*100, 1))
