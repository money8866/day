# -*- coding: utf-8 -*-
"""申万三级行业共振 —— 作为缺口信号的第三重正交确认。

检验问题
    前面已确认两个正交维度：量比（缩量好）、大盘20日（回调好），
    组合后 15112 笔信号从 +1.89% 提升到 +3.11%。
    这里检验第三个维度：**所属行业当日是否共振**，能否再挤出增量。

共振的定义（避免常见错误）
    ❌ 错误做法：数同行业当天有几个涨停/大涨的成分股。
       这是「事后统计」，信号发出时才知道，隐含未来信息。
    ✅ 正确做法：用行业指数（或成分股当日涨幅均值/中位数）作为
       「T 日该行业整体表现」的代理，衡量个股信号与行业是否同向。
       T 日收盘后即可得，无未来信息。

三级 vs 二级
    申万三级行业成分股中位数仅约 5 只，样本太小，单只个股主导板块涨跌，
    共振信号噪声过大。二级（131 个，中位数约 40 只）更稳健。
    因此两个层级都测，用数据决定用哪个。

用法
    python sector_resonance.py            # 全样本检验
    python sector_resonance.py --scan     # 对最新信号日打分
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any

from sw_industry import SWIndustry
from ts_cache import (_I_CLOSE, _I_DATE, _I_HIGH, _I_LOW, _I_PCT, _I_VOL,
                      list_codes, load_bars)

CACHE_DIR = r"D:\mystock\solo\cache_daily"

# ============================================================
# 参数
# ============================================================
SR = {
    "mkt_code": "000852",
    "mkt_period": 20,
    "gap_min": 0.8,
    "gap_max": 9.0,
    "vol_ratio_max": 2.0,      # 正交确认①：缩量
    "mkt_ret_max": 0.0,        # 正交确认②：大盘回调
    "fwd_days": 8,
    "stop_buffer": 1.5,
    "limit_pct": 9.0,
    "skip_signal_day_limit": True,
    "skip_stop_on_limit_down": True,
    # 行业最小成分股数：太少则行业涨跌由单只票决定，噪声大
    "min_members": 8,
}


# ============================================================
# 1. 预计算每日行业表现（避免重复遍历）
# ============================================================
def build_industry_daily(sw: SWIndustry, codes: list[str],
                         verbose: bool = True) -> dict[int, dict[str, dict]]:
    """构建 {日期: {行业名: {ret, n, up_ratio}}}。

    行业表现 = 该行业当日成分股涨幅中位数（抗极值，比均值更稳）
    中位数为 0 时视作行业平盘（ret=0）。
    """
    l2_groups: dict[str, list[str]] = defaultdict(list)
    l3_groups: dict[str, list[str]] = defaultdict(list)
    for c in codes:
        v = sw.get(c)
        if not v:
            continue
        if len(l2_groups.setdefault(v["l2"], [])) or True:
            l2_groups[v["l2"]].append(c)
            l3_groups[v["l3"]].append(c)

    # 剔除成分股过少的行业
    l2_groups = {k: v for k, v in l2_groups.items() if len(v) >= SR["min_members"]}
    l3_groups = {k: v for k, v in l3_groups.items() if len(v) >= SR["min_members"]}
    if verbose:
        print(f"  行业数量：二级 {len(l2_groups)} 个 / 三级 {len(l3_groups)} 个"
              f"（成分股 ≥ {SR['min_members']}）")

    by_date: dict[int, dict[str, dict]] = defaultdict(lambda: {"L2": {}, "L3": {}})
    pct_by: dict[str, dict[int, float]] = {}
    for c in codes:
        b = load_bars(c, CACHE_DIR)
        if not b:
            continue
        pct_by[c] = {r[_I_DATE]: r[_I_PCT] for r in b}

    for d in {d for m in pct_by.values() for d in m}:
        for lv, groups in (("L2", l2_groups), ("L3", l3_groups)):
            for ind, members in groups.items():
                rets = [pct_by[c][d] for c in members if d in pct_by.get(c, {})]
                if len(rets) < SR["min_members"]:
                    continue
                rets.sort()
                mid = len(rets) // 2
                med = rets[mid] if len(rets) % 2 else (rets[mid - 1] + rets[mid]) / 2
                by_date[d][lv][ind] = {
                    "ret": round(med, 2),
                    "n": len(rets),
                    "up_ratio": round(len([x for x in rets if x > 0]) / len(rets), 3),
                }
    if verbose:
        print(f"  行业日频数据：{len(by_date)} 个交易日")
    return by_date


# ============================================================
# 2. 采集带共振特征的缺口信号
# ============================================================
def collect(sw: SWIndustry, ind_daily: dict, verbose: bool = True) -> list[dict]:
    mkt_bars = load_bars(SR["mkt_code"], CACHE_DIR)
    mkt = {r[_I_DATE]: r[_I_CLOSE] for r in mkt_bars}

    codes = list_codes()
    rows: list[dict[str, Any]] = []
    for idx, code in enumerate(codes, 1):
        bars = load_bars(code, CACHE_DIR)
        n = len(bars)
        if n < 40:
            continue
        meta = sw.get(code)
        if not meta:
            continue
        l2, l3 = meta["l2"], meta["l3"]
        for i in range(20, n - 2):
            if bars[i][_I_LOW] <= bars[i - 1][_I_HIGH]:
                continue
            gp = (bars[i][_I_LOW] - bars[i - 1][_I_HIGH]) / bars[i - 1][_I_HIGH] * 100
            if not (SR["gap_min"] <= gp <= SR["gap_max"]):
                continue
            if SR["skip_signal_day_limit"] and abs(bars[i][_I_PCT]) > SR["limit_pct"]:
                continue
            avg = sum(x[_I_VOL] for x in bars[i - 20:i]) / 20
            if not avg or bars[i][_I_VOL] / avg >= SR["vol_ratio_max"]:
                continue

            d = bars[i][_I_DATE]
            ind = ind_daily.get(d, {})
            l2d = ind.get("L2", {}).get(l2)
            l3d = ind.get("L3", {}).get(l3)
            mkt_ret = None
            if i >= 20:
                d0 = bars[i - 20][_I_DATE]
                if d in mkt and d0 in mkt:
                    mkt_ret = (mkt[d] / mkt[d0] - 1) * 100

            e = bars[i][_I_CLOSE]
            stop = bars[i - 1][_I_HIGH] * (1 - SR["stop_buffer"] / 100)
            fwd = None
            for k in range(i + 1, min(n, i + SR["fwd_days"] + 1)):
                if bars[k][_I_LOW] <= stop:
                    if SR["skip_stop_on_limit_down"] and abs(bars[k][_I_PCT]) > SR["limit_pct"]:
                        continue
                    fwd = (stop - e) / e * 100
                    break
                if k == i + SR["fwd_days"]:
                    fwd = (bars[k][_I_CLOSE] - e) / e * 100
            if fwd is None:
                continue

            rows.append({
                "code": code, "date": d, "l1": meta["l1"], "l2": l2, "l3": l3,
                "gap_pct": round(gp, 2), "fwd": round(fwd, 2),
                "vol_ratio": round(bars[i][_I_VOL] / avg, 2),
                "mkt20": round(mkt_ret, 2) if mkt_ret is not None else None,
                "l2_ret": l2d["ret"] if l2d else None,
                "l2_up": l2d["up_ratio"] if l2d else None,
                "l3_ret": l3d["ret"] if l3d else None,
                "l3_up": l3d["up_ratio"] if l3d else None,
            })
        if verbose and idx % 1500 == 0:
            print(f"    ... {idx}/{len(codes)}  信号 {len(rows)}")
    return rows


# ============================================================
# 3. 检验
# ============================================================
def _st(rets):
    if not rets:
        return 0, 0.0, 0.0
    return (len(rets), sum(rets) / len(rets),
            len([r for r in rets if r > 0]) / len(rets) * 100)


def _quartiles(rows, key):
    vals = sorted(r[key] for r in rows if r.get(key) is not None)
    if len(vals) < 500:
        return None
    qs = [vals[int(len(vals) * k / 4)] for k in (1, 2, 3)]
    groups = [[] for _ in range(4)]
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        g = 0
        for j, qv in enumerate(qs):
            if v > qv:
                g = j + 1
        groups[g].append(r["fwd"])
    if any(len(g) < 100 for g in groups):
        return None
    return groups


def test_dimension(rows, key, label):
    groups = _quartiles(rows, key)
    if not groups:
        print(f"  {label:<22} 样本不足")
        return
    avgs = [sum(g) / len(g) for g in groups]
    wrs = [len([x for x in g if x > 0]) / len(g) * 100 for g in groups]
    inc = all(avgs[k] <= avgs[k + 1] for k in range(3))
    dec = all(avgs[k] >= avgs[k + 1] for k in range(3))
    mono = "递增" if inc else ("递减" if dec else "无序")
    print(f"  {label:<22}Q1{avgs[0]:+6.2f} Q2{avgs[1]:+6.2f} "
          f"Q3{avgs[2]:+6.2f} Q4{avgs[3]:+6.2f} | 跨度{max(avgs)-min(avgs):5.2f}pp "
          f"{mono} | 胜率{wrs[0]:.0f}→{wrs[-1]:.0f}%")


def run_report(rows, verbose=True):
    if verbose:
        print("\n" + "=" * 72)
        print("  第三重正交：申万行业共振（已叠加量比+大盘过滤）")
        print("=" * 72)
        n, b, c = _st([r["fwd"] for r in rows])
        print(f"  基准（量比<2 且 大盘20日<0%）：{n} 笔  {b:+.2f}%  胜率 {c:.1f}%\n")
        print("  行业共振维度增量检验：")
        test_dimension(rows, "l2_ret", "二级行业中位涨幅")
        test_dimension(rows, "l2_up", "二级行业上涨占比")
        test_dimension(rows, "l3_ret", "三级行业中位涨幅")
        test_dimension(rows, "l3_up", "三级行业上涨占比")


def combo_test(rows, verbose=True):
    """在双重确认基础上，检验行业共振能否再分层。"""
    if verbose:
        print("\n  三重确认叠加检验：")
        print(f"  {'条件':<34}{'笔数':>7}{'均收益%':>10}{'胜率%':>8}")
        print("  " + "-" * 60)
    tiers = [
        ("仅量比+大盘（基准）", lambda r: True),
        ("+ 行业上涨(≥60%)", lambda r: r["l3_up"] is not None and r["l3_up"] >= 0.6),
        ("+ 行业上涨(≥60%) 且 行业涨幅>1%", lambda r: r["l3_up"] is not None and r["l3_up"] >= 0.6 and r["l3_ret"] > 1),
        ("+ 行业下跌(<40%) 反向", lambda r: r["l3_up"] is not None and r["l3_up"] < 0.4),
    ]
    out = []
    for name, f in tiers:
        a, b, c = _st([r["fwd"] for r in rows if f(r)])
        out.append((name, a, b, c))
        if verbose:
            print(f"  {name:<34}{a:>7}{b:>+10.2f}{c:>8.1f}")
    return out


def scan_latest(rows, sw, verbose=True):
    if not rows:
        return
    last = max(r["date"] for r in rows)
    cur = [r for r in rows if r["date"] == last]
    if verbose:
        print(f"\n  最新信号日 {last}（{len(cur)} 个已通过量比+大盘过滤的缺口信号）\n")
        print(f"  {'代码':<9}{'三级行业':<14}{'缺口%':>7}{'量比':>7}{'行业涨幅':>9}{'行业上涨':>9}")
        print("  " + "-" * 62)
    for r in cur:
        r["res_score"] = (
            (1 if r["l3_ret"] is not None and r["l3_ret"] > 0.5 else 0)
            + (1 if r["l3_up"] is not None and r["l3_up"] >= 0.6 else 0)
        )
    cur.sort(key=lambda x: (-x["res_score"], -(x["l3_ret"] or 0)))
    if verbose:
        for r in cur[:20]:
            ind = (r["l3"] or "—")[:12]
            ir = f"{r['l3_ret']:+.2f}%" if r["l3_ret"] is not None else "—"
            iu = f"{r['l3_up']*100:.0f}%" if r["l3_up"] is not None else "—"
            print(f"  {r['code']:<9}{ind:<14}{r['gap_pct']:>7.2f}{r['vol_ratio']:>7.2f}"
                  f"{ir:>9}{iu:>9}  {'★' * r['res_score']}")
    return cur


def main():
    ap = argparse.ArgumentParser(description="申万三级行业共振检验")
    ap.add_argument("--scan", action="store_true", help="对最新信号日打分")
    ap.add_argument("--json", help="导出信号明细")
    a = ap.parse_args()

    print("=" * 72)
    print("  申万行业共振 · 第三重正交确认")
    print("=" * 72)
    sw = SWIndustry()
    print(f"  行业映射：{sw.coverage()['members']} 只 / "
          f"三级 {sw.coverage()['l3_count']} 个")

    print("\n  构建行业日频表现（中位数，抗极值）…")
    ind_daily = build_industry_daily(sw, list_codes())

    print("\n  采集缺口信号（已过滤涨停/缩量/大盘）…")
    rows = collect(sw, ind_daily)
    print(f"  信号数：{len(rows)}")

    run_report(rows)
    combo_test(rows)
    if a.scan:
        scan_latest(rows, sw)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"\n  已写入 {a.json}")


if __name__ == "__main__":
    main()
