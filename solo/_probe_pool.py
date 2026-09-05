# -*- coding: utf-8 -*-
# 一次性探针：SLI 细分龙头池 + 天量事件数据可用性
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sli.reader import latest_date, get_subsector_top5
from w7_second_wave_engine import CacheReader, extreme_event

meta = latest_date()
print("SLI 最近快照:", meta)

p = get_subsector_top5()
codes = sorted(set(p["ts_code"].astype(str).str.strip()))
print("Top5 池行数=%d 去重股票=%d 赛道数=%d snapshot=%s age=%s"
      % (len(p), len(codes), p["subsector"].nunique(),
         p.attrs.get("snapshot_date"), p.attrs.get("age_days")))

# 历史 asof 可用性
for d in ("20240628", "20241231", "20250630", "20251231", "20260630"):
    try:
        q = get_subsector_top5(asof=d)
        print("  asof=%s -> snapshot=%s 股票数=%d" % (d, q.attrs.get("snapshot_date"), q["ts_code"].nunique()))
    except Exception as e:
        print("  asof=%s -> 异常 %s" % (d, e))

reader = CacheReader()
print("DB 最后交易日:", reader.latest_date())
n_codes = len(codes)
reader.load_all(reader.latest_date(), codes=codes[:3], min_date="20230101")
for c in codes[:3]:
    df = reader.frames.get(c)
    if df is None:
        continue
    print("  样例 %s bars=%d 首日=%s 末日=%s" % (c, len(df), df.trade_date.iloc[0], df.trade_date.iloc[-1]))
    t0 = time.time()
    ev = 0
    dates = df.trade_date.astype(str).to_numpy()
    tr = df.turnover_rate_f.to_numpy(dtype=float)
    vol = df.vol.to_numpy(dtype=float)
    n = len(df)
    import numpy as np
    for i in range(120, n - 5):
        if extreme_event(df, i)[0]:
            ev += 1
    print("  样例 %s 全期天量事件数=%d 扫描耗时=%.2fs" % (c, ev, time.time() - t0))
reader.close()
