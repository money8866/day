# -*- coding: utf-8 -*-
"""缺口信号的正交确认层 —— 在缺口之上叠加独立证据源。

问题背景
    前一轮回测结论：裸缺口信号全样本均收益仅 +1.91%，扣成本后接近盈亏平衡。
    本模块检验「正交确认」能否把它变成可用信号。

什么是正交
    正交确认 = 与原信号（缺口形态）统计独立、但能提供增量信息的证据。
    判据不是相关系数，而是「在缺口信号内部做分组，能否拉开收益差距」。
    若某维度与缺口高度共线（如缺口幅度本身），它就不是正交的，只是同一信息换个说法。

检验结果（15045 个信号，全市场 5541 只 × 343 交易日）
    有效维度（按区分度跨度排序）
        量比       3.19pp  递减 ← 最强，且反直觉
        大盘20日    2.34pp  递减
        缺口幅度    2.07pp  递增（但这是原信号自身，非正交）
        ATR%       1.16pp  递增
    无效维度（分组无序）
        20日相对强度 / 偏离MA20 / 收盘位置 / 大盘5日

核心发现：放量是负向信号
    缺口日量比越高，后续收益越差。三个缺口分层内一致成立：
        小缺口 0.8-2%：缩量 +2.03%  vs  放量 +0.04%
        中缺口 2-4%：  缩量 +2.58%  vs  放量 +0.47%
        大缺口 4-9%：  缩量 +6.72%  vs  放量 -0.28%
    解释：放量意味着抢跑资金已经透支了未来涨幅；缩量缺口说明抛压小、
    筹码锁定度高，趋势有持续性。

最有效的组合
    量比 < 2  且  中证1000 20日涨幅 < 0%
    → 5567 笔，均收益 +3.17%，胜率 62.9%
    → 六个独立时段全部优于同期基准；去极值后 +2.85% / 胜率 63.2%
    → 跨年独立：2025 年 +3.67%，2026 年 +2.27%

用法
    python orth_confirm.py              # 全样本检验
    python orth_confirm.py --scan       # 对最新交易日标的打分
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from ts_cache import (_I_CLOSE, _I_DATE, _I_HIGH, _I_LOW, _I_PCT, _I_VOL,
                      list_codes, load_bars)

CACHE_DIR = r"D:\mystock\solo\cache_daily"

# ============================================================
# 正交确认参数
# ============================================================
OC = {
    # 量能（最强正交维度，注意方向是「缩量好」）
    "vol_ratio_max": 2.0,        # 缺口日量比上限：超过视为抢跑透支
    "vol_ratio_strong": 1.5,     # 强确认档

    # 大盘状态（中证1000 = 000852，与个股风格匹配）
    "mkt_code": "000852",
    "mkt_period": 20,
    "mkt_ret_max": 0.0,          # 大盘20日涨幅上限：负值或零才开仓

    # 缺口本体
    "gap_min": 0.8,
    "gap_max": 9.0,

    # 硬约束（去掉会高估收益）
    "skip_signal_day_limit": True,   # 剔除信号日涨停（买不到）
    "limit_pct": 9.0,

    # 前瞻
    "fwd_days": 8,
    "stop_buffer": 1.5,
    "skip_stop_on_limit_down": True,
}


# ============================================================
# 特征计算
# ============================================================
def mkt_series(mkt_code: str = "000852") -> dict[int, float]:
    b = load_bars(mkt_code, CACHE_DIR)
    return {r[_I_DATE]: r[_I_CLOSE] for r in b}


def feat_vol_ratio(bars: list, i: int, lookback: int = 20) -> float | None:
    """缺口日量比 = 当日量 / 前 N 日均量。"""
    if i < lookback:
        return None
    avg = sum(x[_I_VOL] for x in bars[i - lookback:i]) / lookback
    return bars[i][_I_VOL] / avg if avg else None


def feat_mkt_ret(mkt: dict[int, float], d0: int, d1: int) -> float | None:
    """中证1000 在 [d1, d0] 区间的涨幅(%)。"""
    if d0 not in mkt or d1 not in mkt:
        return None
    return (mkt[d0] / mkt[d1] - 1) * 100


def forward_return(bars: list, i: int, fwd_days: int = 8,
                   stop_buffer: float = 1.5) -> float | None:
    """信号日收盘买入的前瞻收益，带止损与涨跌停约束。"""
    n = len(bars)
    e = bars[i][_I_CLOSE]
    stop = bars[i - 1][_I_HIGH] * (1 - stop_buffer / 100)
    if e <= 0:
        return None
    for k in range(i + 1, min(n, i + fwd_days + 1)):
        if bars[k][_I_LOW] <= stop:
            if abs(bars[k][_I_PCT]) > 9.0:      # 跌停卖不掉
                continue
            return (stop - e) / e * 100
        if k == i + fwd_days:
            return (bars[k][_I_CLOSE] - e) / e * 100
    return None


# ============================================================
# 采集信号 + 特征
# ============================================================
def collect(limit: int = 0, verbose: bool = True) -> list[dict[str, Any]]:
    mkt = mkt_series(OC["mkt_code"])
    codes = list_codes()
    if limit:
        codes = codes[:limit]
    rows: list[dict[str, Any]] = []
    for idx, code in enumerate(codes, 1):
        bars = load_bars(code, CACHE_DIR)
        n = len(bars)
        if n < 40:
            continue
        for i in range(20, n - 2):
            if bars[i][_I_LOW] <= bars[i - 1][_I_HIGH]:
                continue
            gp = (bars[i][_I_LOW] - bars[i - 1][_I_HIGH]) / bars[i - 1][_I_HIGH] * 100
            if not (OC["gap_min"] <= gp <= OC["gap_max"]):
                continue
            if OC["skip_signal_day_limit"] and abs(bars[i][_I_PCT]) > OC["limit_pct"]:
                continue
            vr = feat_vol_ratio(bars, i)
            if vr is None:
                continue
            d20 = bars[i - 20][_I_DATE] if i >= 20 else None
            mk = feat_mkt_ret(mkt, bars[i][_I_DATE], d20) if d20 else None
            fwd = forward_return(bars, i, OC["fwd_days"], OC["stop_buffer"])
            if fwd is None:
                continue
            rows.append({
                "code": code, "date": bars[i][_I_DATE], "gap_pct": round(gp, 2),
                "vol_ratio": round(vr, 2), "mkt20": round(mk, 2) if mk is not None else None,
                "fwd": round(fwd, 2),
            })
        if verbose and idx % 1000 == 0:
            print(f"    ... {idx}/{len(codes)}  信号 {len(rows)}")
    return rows


# ============================================================
# 检验
# ============================================================
def _stats(rets: list[float]) -> tuple[int, float, float]:
    if not rets:
        return 0, 0.0, 0.0
    return (len(rets), sum(rets) / len(rets),
            len([r for r in rets if r > 0]) / len(rets) * 100)


def quadrant_test(rows: list[dict]) -> None:
    """量比 × 大盘状态 的四象限检验 —— 正交确认的核心方法。"""
    print("=" * 68)
    print("  四象限检验：量比 × 中证1000 20日涨幅")
    print("=" * 68)
    print(f"  {'象限':<28}{'笔数':>7}{'均收益%':>10}{'胜率%':>8}")
    print("  " + "-" * 64)
    quads = [
        ("缩量 + 大盘回调 (最优)", lambda r: r["vol_ratio"] < 2 and r["mkt20"] is not None and r["mkt20"] < 0),
        ("缩量 + 大盘上涨",        lambda r: r["vol_ratio"] < 2 and r["mkt20"] is not None and r["mkt20"] >= 0),
        ("放量 + 大盘回调",        lambda r: r["vol_ratio"] >= 2 and r["mkt20"] is not None and r["mkt20"] < 0),
        ("放量 + 大盘上涨 (最差)", lambda r: r["vol_ratio"] >= 2 and r["mkt20"] is not None and r["mkt20"] >= 0),
    ]
    for name, f in quads:
        a, b, c = _stats([r["fwd"] for r in rows if f(r)])
        print(f"  {name:<28}{a:>7}{b:>+10.2f}{c:>8.1f}")
    a, b, c = _stats([r["fwd"] for r in rows])
    print(f"  {'—— 全样本基准':<28}{a:>7}{b:>+10.2f}{c:>8.1f}")


def walkforward(rows: list[dict]) -> None:
    """分时段样本外检验。"""
    print("\n" + "=" * 68)
    print("  样本外检验：六时段（条件 = 量比<2 且 大盘20日<0%）")
    print("=" * 68)
    print(f"  {'时段':<14}{'基准均收益':>12}{'条件均收益':>12}{'条件胜率':>10}")
    print("  " + "-" * 56)
    segs = [("2025 Q1", 20250101, 20250331), ("2025 Q2", 20250401, 20250630),
            ("2025 Q3", 20250701, 20250930), ("2025 Q4", 20251001, 20251231),
            ("2026 Q1", 20260101, 20260331), ("2026 Q2", 20260401, 20260605)]
    cond = lambda r: r["vol_ratio"] < 2 and r["mkt20"] is not None and r["mkt20"] < 0
    wins = 0
    for name, lo, hi in segs:
        sub = [r for r in rows if lo <= r["date"] <= hi]
        _, ab, _ = _stats([r["fwd"] for r in sub])
        _, cb, cc = _stats([r["fwd"] for r in sub if cond(r)])
        if cb > ab:
            wins += 1
        print(f"  {name:<14}{ab:>+11.2f}%{cb:>+11.2f}%{cc:>9.1f}%")
    print(f"  条件占优时段：{wins}/6")


# ============================================================
# 对最新标的打分
# ============================================================
def scan_latest(verbose: bool = True) -> list[dict]:
    """对缓存中最后交易日出现缺口的标的给出正交确认评分。"""
    rows = collect(limit=0, verbose=False)
    if not rows:
        return []
    last_date = max(r["date"] for r in rows)
    latest = [r for r in rows if r["date"] == last_date]
    if verbose:
        print(f"\n最新信号日：{last_date}（{len(latest)} 个缺口信号）\n")
    for r in latest:
        vol_ok = r["vol_ratio"] < OC["vol_ratio_max"]
        mkt_ok = r["mkt20"] is not None and r["mkt20"] < OC["mkt_ret_max"]
        r["vol_pass"] = vol_ok
        r["mkt_pass"] = mkt_ok
        r["score"] = (2 if vol_ok else 0) + (1 if mkt_ok else 0)
        r["verdict"] = ("✓ 双确认" if r["score"] == 3
                        else "○ 部分确认" if r["score"] > 0 else "✗ 不通过")
    latest.sort(key=lambda x: (-x["score"], x["vol_ratio"]))
    if verbose:
        print(f"  {'代码':<9}{'缺口%':>7}{'量比':>7}{'大盘20日':>10}{'评分':>6}  判定")
        print("  " + "-" * 52)
        for r in latest[:25]:
            mk = f"{r['mkt20']:+.2f}%" if r["mkt20"] is not None else "—"
            print(f"  {r['code']:<9}{r['gap_pct']:>7.2f}{r['vol_ratio']:>7.2f}"
                  f"{mk:>10}{r['score']:>6}  {r['verdict']}")
    return latest


def main() -> None:
    ap = argparse.ArgumentParser(description="缺口信号正交确认")
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 只（调试）")
    ap.add_argument("--scan", action="store_true", help="对最新信号日打分")
    ap.add_argument("--json", help="导出信号明细")
    a = ap.parse_args()

    print("=" * 68)
    print("  缺口信号 · 正交确认层")
    print("=" * 68)
    rows = collect(limit=a.limit)
    print(f"\n  采集缺口信号 {len(rows)} 个")
    if rows:
        _, b, c = _stats([r["fwd"] for r in rows])
        print(f"  全样本基准：均收益 {b:+.2f}%  胜率 {c:.1f}%")
        quadrant_test(rows)
        walkforward(rows)
    if a.scan:
        scan_latest()
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"\n已写入 {a.json}")


if __name__ == "__main__":
    main()
