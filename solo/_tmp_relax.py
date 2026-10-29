import sys, time
sys.path.insert(0, r"d:\mystock\solo")
import rising_bottom_select as rbs

date = "20261027"
base = dict(MAX_DIST_MA60_PCT=rbs.MAX_DIST_MA60_PCT,
            MAX_EXT_FROM_LOW_PCT=rbs.MAX_EXT_FROM_LOW_PCT,
            LAST_LOW_AGE_MAX=rbs.LAST_LOW_AGE_MAX)

def run(tag, dist, ext, age):
    rbs.MAX_DIST_MA60_PCT = dist
    rbs.MAX_EXT_FROM_LOW_PCT = ext
    rbs.LAST_LOW_AGE_MAX = age
    t = time.time()
    td, hits = rbs.scan(date, verbose=False)
    codes = {h["ts_code"] for h in hits}
    s = [h for h in hits if h["ts_code"] == "301550.SZ"]
    print("[%s] dist=%.0f ext=%.0f age=%.0f -> %d 只 (%.0fs) 301550=%s"
          % (tag, dist, ext, age, len(hits), time.time() - t,
             ("命中 技术分%.1f 低点%d 抬高%.1f%% dist_ma60=%.1f%% ext_low=%.1f%%"
              % (s[0]["score"], s[0]["low_cnt"], s[0]["elev_pct"],
                 s[0]["dist_ma60"], s[0]["ext_low"])) if s else "未命中"))
    return codes

c0 = run("基线", 15, 20, 40)
c1 = run("放宽A", 25, 35, 60)
c2 = run("放宽B", 30, 40, 60)
print("放宽A 新增:", len(c1 - c0), " 放宽B 新增:", len(c2 - c0))
print("B 独有（相对A）:", len(c2 - c1))

rbs.MAX_DIST_MA60_PCT = base["MAX_DIST_MA60_PCT"]
rbs.MAX_EXT_FROM_LOW_PCT = base["MAX_EXT_FROM_LOW_PCT"]
rbs.LAST_LOW_AGE_MAX = base["LAST_LOW_AGE_MAX"]
