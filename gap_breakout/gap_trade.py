# -*- coding: utf-8 -*-
"""跳空缺口跟踪 —— 找回踩买点（非涨停股）。

与 gap_strategy.py 的区别：
    gap_strategy  涨停池驱动，只在涨停股里选，判「收盘突破箱体」
    本模块       全市场驱动，与涨停无关，判「真实向上缺口 + 回踩买点」

核心流程
    第 1 步  粗筛：新浪全市场快照，筛出「最低价 > 昨收」或「开盘跳空 >1%」的候选
    第 2 步  精算：拉日K线，取真实的 T-1 最高价作为缺口基准（昨收会低估缺口）
    第 3 步  分级：按缺口幅度与量能，把缺口分为「突破 / 持续 / 衰竭」三类
    第 4 步  跟踪：判断是否已进入「回踩缺口下沿」的买点区间，给出触发价与止损

买点逻辑（回踩确认）
    缺口不被回补 = 强势（直接追，但风险高）
    回踩到缺口下沿附近（0~+2% 区间）且缩量止跌 = 买点（风险收益比最优）
    跌破缺口下沿且不能收回 = 缺口失效，离场

用法
    python gap_trade.py                    # 今日扫描 + 买点跟踪
    python gap_trade.py --min-gap 2.0      # 提高缺口门槛
    python gap_trade.py --top 15
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from kline import KlineError, tencent_kline
from market_scan import scan_up_gaps

# ============================================================
# 参数区
# ============================================================
P = {
    # 缺口分类阈值（相对 T-1 最高价）
    "breakaway_min": 2.0,     # ≥2% 视为突破缺口（强，趋势启动）
    "exhaust_max": 1.2,       # ≤1.2% 视为衰竭/噪声缺口（弱，易回补）
    # 买点：回踩区间
    "pullback_band": (0.0, 2.0),   # 现价相对缺口下沿的溢价 %，落在该区间=买点
    "pullback_vol_ok": 0.85,       # 回踩日量能 ≤ 突破日量能的 85% → 缩量止跌
    # 硬过滤
    "min_amount_yi": 1.0,
    "min_float_cap_yi": 20.0,
    "max_float_cap_yi": 800.0,
    "exclude_st": True,
    # 量能
    "breakout_vol_ratio": 1.3,     # 缺口日量比下限
    "box_lookback": 20,
    # 止损缓冲
    "stop_buffer": 0.015,
    # 状态
    "max_gap_age": 5,              # 缺口超过 N 个交易日未回补 → 视为趋势已走远
}


@dataclass
class GapTrade:
    code: str
    name: str
    gap_date: str = ""
    gap_type: str = ""             # 突破 / 持续 / 衰竭
    gap_pct: float = 0.0
    prev_high: float = 0.0         # T-1 最高价 = 缺口下沿 = 结构支撑
    close: float = 0.0
    pct: float = 0.0
    amount_yi: float = 0.0
    float_cap_yi: float = 0.0
    turnover: float = 0.0
    vol_ratio: float = 0.0
    age: int = 0                   # 缺口至今几个交易日
    state: str = ""                # 未回踩 / 回踩买点 / 已跌破失效 / 远离
    action: str = ""
    entry: float = 0.0
    stop: float = 0.0
    box_high: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 第 2 步：日K线精算缺口
# ============================================================
def verify_gap(q: dict[str, Any], date: str | None = None) -> dict[str, Any] | None:
    """用日K取真实 T-1 最高价，精算缺口幅度并完成缺口分类。

    缺口定义（严格）：当日最低价 > 前一交易日最高价。
    这是唯一无歧义的跳空定义 —— 两日价格区间完全不重叠。
    """
    code = q["code"]
    try:
        bars = tencent_kline(code, count=P["box_lookback"] + 12, end=date)
    except KlineError as exc:
        return {"ok": False, "why": f"K线失败 {exc}"}

    if len(bars) < 5:
        return {"ok": False, "why": "K线不足"}

    cur, prev = bars[-1], bars[-2]
    if date:
        want = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
        if cur["date"] != want:
            return {"ok": False, "why": f"日期不符({cur['date']})"}

    if cur["low"] <= prev["high"]:
        return {"ok": False, "why": "无缺口（最低未超前高）"}

    gap_pct = (cur["low"] - prev["high"]) / prev["high"] * 100

    # 量能
    win = bars[-(P["box_lookback"] + 1):-1]
    avg_vol = sum(b["volume"] for b in win) / len(win) if win else 0.0
    vol_ratio = cur["volume"] / avg_vol if avg_vol else 0.0

    # 箱体上沿（20 日）
    box_high = max(b["high"] for b in win) if win else prev["high"]

    # 缺口分级 —— 三分法
    if gap_pct >= P["breakaway_min"]:
        gap_type = "突破缺口"
    elif gap_pct <= P["exhaust_max"]:
        gap_type = "衰竭缺口"
    else:
        gap_type = "持续缺口"

    return {
        "ok": True,
        "gap_pct": round(gap_pct, 2),
        "prev_high": round(prev["high"], 2),
        "gap_low": round(prev["high"], 2),
        "close": round(cur["close"], 2),
        "gap_date": cur["date"],
        "vol_ratio": round(vol_ratio, 2),
        "box_high": round(box_high, 2),
        "gap_type": gap_type,
        "avg_vol": round(avg_vol, 1),
    }


# ============================================================
# 第 4 步：跟踪状态 —— 是否进入买点
# ============================================================
def track_state(g: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """判断缺口当前状态，输出买点/止损。

    状态机：
        未回踩   现价显著高于下沿 → 追高风险大，等回踩
        回踩买点 现价落在缺口下沿上方 0~2% 区间 → 买点
        已失效   现价跌破下沿且连续 2 日未收回 → 离场
        远离     缺口已 N 日且现价远离 → 机会成本过高
    """
    low = g["gap_low"]
    close = g["close"]
    age = g.get("age", 0)

    # 跌破失效：近 2 日收盘均在下沿之下
    recent = bars[-2:]
    broke = sum(1 for b in recent if b["close"] < low)
    premium = (close - low) / low * 100

    d: dict[str, Any] = {"premium": round(premium, 2)}

    if broke >= 2:
        d.update(state="已跌破失效", action="缺口失效，离场，不做回补博弈")
    elif close < low:
        d.update(state="盘中跌破", action="跌破缺口下沿，观望是否快速收回")
    elif P["pullback_band"][0] <= premium <= P["pullback_band"][1]:
        # 缩量检查：回踩日量能是否萎缩
        cur_vol = bars[-1]["volume"]
        prev_vol = bars[-2]["volume"] if len(bars) > 1 else cur_vol
        shrinking = cur_vol <= prev_vol * P["pullback_vol_ok"]
        d["shrinking"] = shrinking
        if shrinking:
            d.update(
                state="回踩买点",
                action=f"缩量回踩缺口下沿（{low:.2f}），买点成立，分批建仓",
            )
        else:
            d.update(
                state="回踩但未缩量",
                action="回踩到下沿但量能未萎缩，警惕继续下探，等缩量再动",
            )
    elif premium < P["pullback_band"][0]:
        d.update(state="未回踩", action="仍在缺口上方运行，等回踩确认再介入")
    else:
        d.update(
            state="远离",
            action=f"溢价{premium:.1f}%过高，追高赔率差，等回踩或放弃",
        )

    if age > P["max_gap_age"] and d["state"] in ("未回踩", "远离"):
        d["state"] = "机会成本过高"
        d["action"] = f"缺口已{age}个交易日未回补，等回调不如换标的"
    return d


# ============================================================
# 主流程
# ============================================================

def load_hist_candidates(date: str, hist_days: int = 10) -> list[dict]:
    """历史回溯模式的候选池。

    数据源限制（实测）：东财涨停池只保留近期约 1 个月数据，更早日期返回空。
    因此历史回溯仅支持近期日期；返回空时如实告知，不做静默降级。

    注意：实时涨幅榜反映的是「今天」，不能用于历史日期 —— 实测用实时池
    配 date=20260828 会得到 0 个缺口（标的与日期不匹配）。
    """
    from em_ztpool import em_zt_pool, em_zb_pool

    cands: list[dict] = []
    for pool_fn in (em_zt_pool, em_zb_pool):
        try:
            for z in pool_fn(date):
                cands.append({
                    "code": z["code"], "name": z["name"],
                    "pct": z.get("pct", 0), "turnover": z.get("turnover", 0),
                    "amount_yi": round(z.get("amount", 0) / 1e8, 2),
                    "float_cap": 0, "float_cap_yi": 0,
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {pool_fn.__name__} 失败: {type(exc).__name__}")

    if not cands:
        print(f"  [提示] 东财涨停池无 {date} 数据（仅保留近期约一个月），"
              f"历史回溯仅支持近期日期。")
    return cands


def scan_historical_gaps(q: dict, lookback: int = 10, date: str = None) -> list[dict]:
    """在候选股上回溯扫描近 N 日内的所有向上缺口。

    这是「跟踪」的核心：当天新形成的缺口不可能已进入回踩，
    真正可交易的买点是此前若干日形成的缺口、今天正好回踩到下沿。
    """
    try:
        bars = tencent_kline(q["code"], count=lookback + P["box_lookback"] + 12, end=date)
    except KlineError:
        return []
    out = []
    n = len(bars)
    # 只回溯最近 lookback 个交易日内的缺口：i 从 (n-1-lookback) 起
    start = max(1, n - 1 - lookback)
    for i in range(start, n - 1):
        cur, prev = bars[i], bars[i - 1]
        if cur["low"] <= prev["high"]:
            continue
        gap_pct = (cur["low"] - prev["high"]) / prev["high"] * 100
        if gap_pct < 0.8:
            continue
        w = bars[max(0, i - P["box_lookback"]):i]
        avg_vol = sum(b["volume"] for b in w) / len(w) if w else 0.0
        vol_ratio = cur["volume"] / avg_vol if avg_vol else 0.0
        if gap_pct >= P["breakaway_min"]:
            gap_type = "突破缺口"
        elif gap_pct <= P["exhaust_max"]:
            gap_type = "衰竭缺口"
        else:
            gap_type = "持续缺口"
        after = bars[i + 1:]
        invalid = any(b["close"] < prev["high"] for b in after)

        # 连续一字板剔除：缺口形成后若持续一字涨停，买点根本无法成交。
        # 判定：缺口日当日一字（开=收=高=低），或缺口后连续 2 日以上一字。
        yizi = cur["open"] == cur["high"] == cur["low"] == cur["close"]
        yizi_run = sum(
            1 for b in after if b["open"] == b["high"] == b["low"] == b["close"]
        )
        if yizi or yizi_run >= 2:
            invalid = True
        out.append({
            "gap_date": cur["date"], "gap_pct": round(gap_pct, 2),
            "prev_high": round(prev["high"], 2), "gap_type": gap_type,
            "vol_ratio": round(vol_ratio, 2), "age": n - 1 - i,
            "invalid": invalid, "last_close": round(bars[-1]["close"], 2),
        })
    return out


def run(date: str = None, min_gap: float = 0.8, top_n: int = 15, hist_days: int = 10):
    print("=" * 70)
    print("  跳空缺口跟踪 · 回踩买点扫描（全市场，非涨停池）")
    print("=" * 70)

    # 候选池：历史回溯时必须用「东财涨停池 + 腾讯快照」定位当期活跃标的，
    # 因为实时涨幅榜反映的是今天，与历史日期不匹配（实测 date=20260828 时
    # 用实时池会得到 0 个缺口）。
    use_hist = bool(date) and date < time.strftime("%Y%m%d")
    if use_hist:
        cands = load_hist_candidates(date, hist_days)
        print(f"\n[候选] 历史模式 {date} → 活跃标的 {len(cands)} 只")
    else:
        t0 = time.time()
        cands = scan_up_gaps(min_amount_yi=P["min_amount_yi"])
        print(f"\n[粗筛] 实时全市场快照 → 候选 {len(cands)} 只（{time.time()-t0:.1f}s）")

    print(f"[回溯] 逐只扫描近 {hist_days} 日内的向上缺口…")
    live = []
    stat = {"gaps": 0, "invalid": 0}
    for i, q in enumerate(cands, 1):
        for g in scan_historical_gaps(q, lookback=hist_days, date=date):
            stat["gaps"] += 1
            if not g["invalid"]:
                live.append((q, g))
        if i % 40 == 0:
            print(f"    ... {i}/{len(cands)}")

    print(f"  共发现缺口 {stat['gaps']} 个，已跌破失效 {stat['invalid']} 个，"
          f"有效跟踪中 {len(live)} 个")

    results = []
    for q, g in live:
        if g["gap_type"] == "衰竭缺口":
            continue
        if g["vol_ratio"] < P["breakout_vol_ratio"]:
            continue
        fc = q.get("float_cap", 0)
        if fc and not (P["min_float_cap_yi"] * 1e8 <= fc <= P["max_float_cap_yi"] * 1e8):
            continue

        low = g["prev_high"]
        close = g["last_close"]
        premium = (close - low) / low * 100

        if premium < 0:
            state, action = "已跌破", "已跌破缺口下沿，放弃"
        elif P["pullback_band"][0] <= premium <= P["pullback_band"][1]:
            state = "回踩买点"
            action = f"回踩缺口下沿（{low:.2f}），买点区间，分批建仓"
        elif premium <= 5:
            state, action = "未回踩", "等回踩下沿再介入"
        else:
            state, action = "远离", f"溢价{premium:.1f}%，追高赔率差"
        if g["age"] > P["max_gap_age"] and state in ("未回踩", "远离"):
            state, action = "机会成本过高", f"缺口已{g['age']}日未回补，换标的"

        results.append(GapTrade(
            code=q["code"], name=q["name"], gap_date=g["gap_date"],
            gap_type=g["gap_type"], gap_pct=g["gap_pct"], prev_high=low,
            close=close, pct=q["pct"], amount_yi=q.get("amount_yi", 0),
            float_cap_yi=round(q.get("float_cap_yi", 0), 1),
            turnover=round(q.get("turnover", 0), 2),
            vol_ratio=g["vol_ratio"], age=g["age"], state=state, action=action,
            entry=round(low * (1 + P["pullback_band"][1] / 100), 2),
            stop=round(low * (1 - P["stop_buffer"]), 2), box_high=low,
            detail={"premium": round(premium, 2)},
        ))

    dedup = {}
    for r in results:
        if r.code not in dedup or r.age < dedup[r.code].age:
            dedup[r.code] = r
    results = list(dedup.values())

    by_type = {}
    for r in results:
        by_type[r.gap_type] = by_type.get(r.gap_type, 0) + 1
    print("  缺口分级：" + "  ".join(f"{k} {v}" for k, v in by_type.items()))

    pri = {"回踩买点": 0, "未回踩": 2, "远离": 3, "机会成本过高": 4, "已跌破": 9}
    results.sort(key=lambda x: (pri.get(x.state, 5), -x.gap_pct))

    buy = [r for r in results if r.state == "回踩买点"]
    print(f"\n{'='*70}")
    print(f"  买点信号 {len(buy)} 个 / 有效缺口标的 {len(results)} 只")
    print("=" * 70)

    show = results[:top_n]
    if not show:
        print("  无符合条件的缺口标的。")
    for i, r in enumerate(show, 1):
        flag = "★★买点" if r.state == "回踩买点" else "    "
        print(f"\n{flag}【{i}】{r.name}({r.code})  {r.gap_type} {r.gap_pct}%")
        print(f"       现价 {r.close}（{r.pct}%）| 缺口日 {r.gap_date}（{r.age}日前）")
        print(f"       缺口下沿 {r.prev_high} | 现价溢价 {r.detail['premium']}%")
        print(f"       量比(缺口日) {r.vol_ratio}  成交额 {r.amount_yi}亿  "
              f"流通 {r.float_cap_yi}亿  换手 {r.turnover}%")
        print(f"       状态：{r.state} → {r.action}")
        if r.state == "回踩买点":
            print(f"       ▶ 买点 ≤ {r.entry}   止损 {r.stop}")

    print(f"\n{'='*70}")
    print("  纪律")
    print("=" * 70)
    print("  1. 只买「回踩买点」状态；未回踩的不追高。")
    print("  2. 止损恒定在缺口下沿下方 1.5%，跌破即离场，不摊平。")
    print("  3. 缺口被跌破即判定失效，放弃该标的，不做回补博弈。")
    print("  4. 买点当日若高开超过买点价 3%，当日取消计划。")
    print("  5. 单笔风险敞口 ≤ 总资金 1%。")

    return {"date": date, "candidates": [r.to_dict() for r in show],
            "buy_signals": [r.to_dict() for r in buy]}


def main() -> None:
    ap = argparse.ArgumentParser(description="跳空缺口跟踪 · 回踩买点")
    ap.add_argument("-d", "--date", help="交易日 YYYYMMDD")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--hist", type=int, default=10, help="回溯扫描天数")
    a = ap.parse_args()
    try:
        run(a.date, top_n=a.top, hist_days=a.hist)
    except Exception as exc:
        print(f"失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
