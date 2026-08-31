# -*- coding: utf-8 -*-
"""验证修复突破：用完整数据到今天跑 analyze，看华正新材/中际旭创当前状态"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
from w7_second_wave_engine import CacheReader, MarketCtx, anchor_features, analyze

reader = CacheReader()
reader.load_all("20260828", codes=["300308.SZ", "603186.SH", "688710.SH"], min_date="20230101", verbose=False)
nfina = reader.load_fina()
mdates, mvals = reader.market_curve("20260828")
mkt = MarketCtx(mdates, mvals)

anchors = {}
for label, (code, ad) in {"中际旭创": ("300308.SZ", "20250508"), "华正新材": ("603186.SH", "20250812")}.items():
    anchors[label] = anchor_features(reader.bars_sql(code, "20260828"), ad)

for code, name in (("300308.SZ", "中际旭创"), ("603186.SH", "华正新材"), ("688710.SH", "益诺思")):
    df = reader.bars(code, "20260828")
    r = analyze(code, name, "", df, anchors, reader=reader, mkt=mkt, sector_strength={}, sector_growth={})
    if r is None:
        print(f"{name}: 已剔除(无候选或runup过滤)")
        continue
    print(f"{name}: state={r['state']} T120={r['t120']:.1f} ENTRY={r['entry']:.1f} buy={r['buy']}")
    print(f"   dims: {r['dims']}")
    print(f"   reason: {r['reason']}")
reader.close()
