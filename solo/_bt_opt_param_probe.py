# -*- coding: utf-8 -*-
"""P1 平滑器参数探针：在「降仓立即执行」前提下寻找升仓限速与高仓位天数的平衡。

用法： python _bt_opt_param_probe.py 20260101 20261103
输出： _bt_opt_probe_metrics.json（含 off 基线与各组实验）
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import pandas as pd

import _bt_position_optimizer_replay as bt


def detail(df):
    t = df['target'].astype(float)
    d = t.diff().fillna(0)
    diff = d.abs()
    return {
        "mean_target": round(float(t.mean()), 2),
        "ge20pp_days": int((diff >= 20).sum()),
        "ge20pp_up": int(((diff >= 20) & (d > 0)).sum()),
        "ge20pp_down": int(((diff >= 20) & (d < 0)).sum()),
        "gt20pp_up": int(((diff > 20) & (d > 0)).sum()),
        "max_up_pp": round(float(d[d > 0].max() or 0), 1),
        "max_down_pp": round(float(-d[d < 0].min() or 0), 1),
        "three_day_roundtrip": int(((d.shift(1) > 0) & (d < 0)).sum()
                                   + ((d.shift(1) < 0) & (d > 0)).sum()),
        "cap_eq_target_days": int((df['target'] == df['hard_cap']).sum()),
        "ge60_days": int((t >= 60).sum()),
        "mean_hard_cap": round(float(df['hard_cap'].mean()), 2),
    }


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else '20260101'
    end = sys.argv[2] if len(sys.argv) > 2 else '20261103'
    dates = bt.get_dates(start, end)
    bt.micro.build(start, end)
    pack = bt.hist_pack(dates)

    # 已跑过的组直接复用明细，避免重复回放
    out = {}
    for tag, fname in (
            ("off", "_bt_opt_replay_off.csv"),
            ("on_default", "_bt_opt_replay_on.csv"),
            ("A_up40_cd1", "_bt_opt_replay_probe_A_up40_cd1.csv"),
            ("B_up60_step15_cd1", "_bt_opt_replay_probe_B_up60_step15_cd1.csv")):
        p = os.path.join(BASE_DIR, fname)
        if os.path.exists(p):
            out[tag] = detail(pd.read_csv(p))

    cases = [
        ("C_step15_cd1", {"smoother": {
            "up_step_pct": 15,
            "cooldown_days_after_down": 1}}),
        ("D_step15_up30_cd1", {"smoother": {
            "up_step_pct": 15,
            "up_step_on_confirmed_upgrade_pct": 30,
            "cooldown_days_after_down": 1}}),
    ]
    for tag, ov in cases:
        print(f"\n=== 探针 {tag} ===", flush=True)
        df = bt.run(dates, pack, True, "probe_" + tag, overrides=ov)
        out[tag] = detail(df)

    print("\n" + "=" * 92)
    print("参数探针（off=10.7.1 / on_default=10.7.2 默认 / A、B=候选）")
    print("=" * 92)
    keys = list(next(iter(out.values())).keys())
    print(f"{'指标':<24}" + "".join(f"{k:>17}" for k in out))
    for key in keys:
        print(f"{key:<24}" + "".join(f"{str(v[key]):>17}" for v in out.values()))
    with open(os.path.join(BASE_DIR, "_bt_opt_probe_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已保存 _bt_opt_probe_metrics.json")


if __name__ == '__main__':
    main()
