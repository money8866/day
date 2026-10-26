# -*- coding: utf-8 -*-
"""极米科技 20260929 走完整 analyze() 链路，看最终是否进入可操作榜（临时脚本，用完删除）"""
import sys

sys.path.insert(0, r"d:\mystock\solo")
import w7_second_wave_engine as W

CODE, DATE = "688696.SH", "20260929"
reader = W.CacheReader()
codes = [CODE] + [c for c, _ in W.ANCHORS.values()]
reader.load_all(DATE, codes=codes, verbose=False)
anchors = {label: W.anchor_features(reader.bars_sql(c, DATE), d)
           for label, (c, d) in W.ANCHORS.items()}
mdates, mvals = reader.market_curve(DATE)
mkt = W.MarketCtx(mdates, mvals)
reader.load_fina()
ige_info, ige_snap = W.load_ige_adj(DATE)
ig = ige_info.get(CODE)
df = reader.bars(CODE, DATE)
res = W.analyze(CODE, "极米科技", "家用电器", df, anchors, reader=reader, mkt=mkt,
                sector_strength={}, sector_growth={}, ige_adj=ig["ige_adj"] if ig else None)
if not res:
    print("analyze -> None")
    sys.exit()
res["ige_adj"] = ig["ige_adj"] if ig else None
res["ige_snap"] = ige_snap
print(f"state={res['state']}  type={res['type']}  buy={res['buy']}  score={res['score']:.1f}")
print(f"close={res['close']:.2f} pressure={res['pressure']:.2f} ma20={res['ma20']:.2f} "
      f"volr={res['volr']:.3f} ige={res['ige_adj']}")
print(f"v5_dims={ {k: round(v, 1) for k, v in res['v5_dims'].items()} }")
print(f"w7_fail_line={W.w7_fail_line(res)}")
print(f"w7_exec_status={W.w7_exec_status(res)}")
print(f"w7_quality_gate={W.w7_quality_gate(res)}")
print(f"reason={res['reason']}")
print(f"horizons={res['horizons']}")
