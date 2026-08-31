import sys
sys.path.insert(0, r"D:\mystock\solo")
from w7_second_wave_engine import CacheReader, MarketCtx, analyze, ANCHORS, anchor_features

reader = CacheReader()
date = reader.latest_date()
print(f"[test] date={date}")
reader.load_all(date, codes=["601808.SH", "300308.SZ", "603186.SH"])
mdates, mvals = reader.market_curve(date)
mkt = MarketCtx(mdates, mvals)
nfina = reader.load_fina()
print(f"[test] fina覆盖={nfina} 市场曲线={len(mvals)}天")
anchors = {}
for label, (code, ad) in ANCHORS.items():
    anchors[label] = anchor_features(reader.bars_sql(code, date), ad)
df = reader.bars("601808.SH", date)
print(f"[test] 601808.SH bars={len(df)}", flush=True)
r = analyze("601808.SH", "中海油服", "采掘服务", df, anchors, reader=reader, mkt=mkt, sector_strength={}, sector_growth={})
if r is None:
    print("[test] FAIL: analyze返回None")
    sys.exit(1)
for k in ("code", "name", "state", "t120", "entry", "buy", "reason", "next"):
    print(f"{k}: {r[k]}")
print("dims:", {k: round(v, 1) for k, v in r["dims"].items()})
print("entry_dims:", {k: round(v, 1) for k, v in r["entry_dims"].items()})
print("cq/acc/sds/lock/pp:", [round(r[k], 1) for k in ("cq", "acceptance", "sds", "lock", "pp")])
print("explanation:", r["explanation"])
reader.close()
