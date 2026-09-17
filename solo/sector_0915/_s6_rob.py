# -*- coding: utf-8 -*-
"""用少量样本预检 run_robustness 代码路径（构造最小 art，不跑全量构建）。"""
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import stock_structure_hvt_build as S

cfg = S.load_config()
d = pd.read_csv(os.path.join(BASE_DIR, "data", "stock_structure_daily.csv"), dtype=str,
                usecols=["trade_date", "ts_code"])
rows = [{"trade_date": a, "ts_code": b} for a, b in
        zip(d["trade_date"].astype(str), d["ts_code"].astype(str))]
art = {"rows": rows, "mb_status": "STATIC_ONLY",
       "trade_date": str(max(d["trade_date"])), "n_stock": d["ts_code"].nunique()}
print("fake art rows =", len(rows))

# 1) 扰动函数必须返回完整配置
c2 = S._apply_perturbation(cfg, cfg["robustness"]["perturbations"][0])
print("perturb root keys =", len(c2), "| orig keys =", len(cfg))
print("orig unchanged =", cfg["qualification"]["qualified_structure_quality_min"])
print("pert changed  =", c2["qualification"]["qualified_structure_quality_min"])

# 2) 小样本跑通 robustness 全流程
rob = S.run_robustness(cfg, art, sample_size=12)
print("\nrobustness n_flip =", rob["n_flip"], "| overfit =", rob["overfit_risk"])
print("base_all_mean =", rob["base_all_mean"], "| base_all_n =", rob["base_all_n"])
for r in rob["results"][:3]:
    print("  ", r["name"], r["n_base"], r["n_pert"], r["mean_base"], r["mean_pert"],
          r["sign_flip"], r["status"])

# 3) 交易指令词扫描
print("\nforbidden hits =", S.forbidden_term_scan(cfg))
print("ROBUSTNESS PATH OK")
