# -*- coding: utf-8 -*-
"""Step 6 smoke：只验证导入链、config、legacy 引擎可用性与依赖产物存在性（不跑全量）。"""
import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import stock_structure_hvt_build as S

print("[1] import OK:", S.__file__)
try:
    cfg = S.load_config()
    print("[2] load_config OK, sections =", len(cfg))
    print("    legacy_config_used =", cfg["meta"]["legacy_config_used"])
    print("    adapter mode       =", S.legacy_adapter["mode"] if hasattr(S, "legacy_adapter") else cfg["legacy_adapter"]["mode"])
except Exception:
    traceback.print_exc()
    raise SystemExit(1)

try:
    voc = S.legacy_hvt_vocab()
    print("[3] legacy HVT vocab OK:", len(voc), voc[:5], "...")
except Exception:
    traceback.print_exc()

try:
    legacy = S.load_legacy_hvt_config()
    print("[4] legacy config OK, keys =", sorted(legacy.keys())[:12])
except Exception:
    traceback.print_exc()

try:
    cand = S.load_candidates(cfg)
    print("[5] candidates:", cand.shape, "| cols sample:", list(cand.columns[:6]))
    print("    dates:", cand["trade_date"].min(), "->", cand["trade_date"].max())
    print("    stocks:", cand["ts_code"].nunique())
except Exception:
    traceback.print_exc()

try:
    mb = S.load_membership()
    print("[6] membership:", mb.shape, "| version_status =", S.membership_version_status(mb))
except Exception:
    traceback.print_exc()

try:
    basic = S.load_stock_basic()
    print("[7] stock_basic:", basic.shape)
except Exception:
    traceback.print_exc()

# 单只股票端到端
try:
    code = sorted(set(cand["ts_code"].astype(str)))[0]
    anchor = str(cfg["input"]["hist_start_date"])
    m = S.load_stock_market(anchor, str(cand["trade_date"].max()), {code})
    f = S.compute_stock_features(m.reset_index(drop=True), cfg)
    dates = f["dates"]
    det = S.hvt_detect_all(f, cfg, legacy, anchor)
    evo = S.hvt_evolution(f, cfg, det, {}, legacy)
    want = [len(dates) - 1]
    res = S.analyse_stock(f, cfg, legacy, anchor, [dates[-1]], "", legacy_on=True)
    print("[8] single-stock end-to-end OK:", code, "rows =", len(res["rows"]))
    print("    sample row keys:", sorted(res["rows"][0].keys())[:12], "...")
    print("    qualification =", res["rows"][0]["structure_qualification"],
          "| state =", res["rows"][0]["structure_state"],
          "| hvt =", res["rows"][0]["hvt_state"])
except Exception:
    traceback.print_exc()

print("SMOKE DONE")
