# -*- coding: utf-8 -*-
"""每日盘后选股 · 缺口突破 + 量比 + 大盘（正式落地版）

策略定稿（经 5541 只 × 343 交易日回测验证）
    信号    当日最低价 > 前一交易日最高价（真实向上跳空缺口）
            缺口幅度 0.8% ~ 9%
    条件①  量比 < 2.0        （缩量，抛压小；放量是负向信号）
    条件②  中证1000 20日涨幅 < 0%  （大盘回调期）
    买入    信号日 T 收盘买入（回测验证：当日直接追 > 等回踩）
    止损    缺口下沿 −1.5%
    持有    8 个交易日
    预期    15112 笔样本，均收益 +2.98%，胜率 52.7%

回测出处见 回测报告.md。「行业共振」维度经检验无增量，已弃用。

数据源
    实时快照   新浪 Market_Center（东财 push2 在本机被拦）
    指数       新浪 / 东财指数快照，回退腾讯
    历史K线    腾讯日线（仅用于计算量比所需的 20 日均量）

用法
    python daily_picker.py                # 盘后跑，默认最近交易日
    python daily_picker.py -d 20260930    # 指定交易日
    python daily_picker.py --json out.json
    python daily_picker.py --no-trade     # 只做大盘体检，不选股
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

# ============================================================
# 参数区（全部来自回测，勿随意改动）
# ============================================================
P = {
    # 缺口
    "gap_min": 0.8,              # 最小缺口幅度(%)
    "gap_max": 9.0,              # 最大缺口幅度(%)
    # 条件①量比（回测最强者，方向是「缩量」）
    # 阈值经细分档位回测校准：<1.5 档 +3.58%/胜率56.6%，1.5~2.0 档仅 +1.03%/胜率40.1%。
    # 原定 2.0 过宽，纳入大量弱档，现收紧到 1.5。
    "vol_lookback": 20,          # 均量回溯交易日
    "vol_ratio_max": 1.5,        # 量比上限（收紧：1.5~2.0 档胜率仅40%）
    "vol_ratio_strong": 1.0,     # 强确认档（<1.0 为极缩量，历史最优）
    # 条件②大盘
    "mkt_code_sina": "sh000852",     # 中证1000（风格匹配）
    "mkt_code_tencent": "sh000852",
    "mkt_period": 20,
    "mkt_ret_max": 0.0,          # 20日涨幅上限：负值或零才开仓
    # 硬过滤
    "min_amount_yi": 1.0,        # 最小成交额(亿)
    "max_amount_yi": 300.0,      # 最大成交额(亿)
    "limit_pct": 9.0,            # |涨幅| 超过视为涨跌停，买不到
    "exclude_st": True,
    "exclude_fund": True,        # 排除 ETF/LOF/基金：其跳空来自套利折价，不是资金博弈
    "fund_name_words": ("ETF", "LOF", "指数", "基金", "债", "收益", "货币"),
    # 交易参数（回测结论，仅供下单参考）
    "stop_buffer": 1.5,          # 止损 = 缺口下沿 ×(1−1.5%)
    "max_hold": 8,               # 最长持有交易日
    "max_picks": 10,             # 最多输出标的数
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_SINA = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "Market_Center.getHQNodeData")
_TENCENT_K = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_TENCENT_Q = "https://qt.gtimg.cn/q="
_last: dict[str, float] = {}


def _throttle(key: str, gap: float) -> None:
    w = gap - (time.time() - _last.get(key, 0.0))
    if w > 0:
        time.sleep(w)
    _last[key] = time.time()


def _f(v: Any) -> float:
    try:
        f = float(v)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


# ============================================================
# 数据源
# ============================================================
def iter_market(total: int = 6000) -> list[dict[str, Any]]:
    """新浪全市场 A 股快照。"""
    out: list[dict[str, Any]] = []
    num, pages = 80, (min(total, 6000) + 79) // 80
    for page in range(1, pages + 1):
        _throttle("sina", 0.35)
        try:
            r = requests.get(
                _SINA, params={"page": page, "num": num, "sort": "changepercent",
                              "asc": 0, "node": "hs_a", "_s_r_a": "page"},
                headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
                timeout=15)
            rows = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 新浪第{page}页失败: {type(exc).__name__}")
            break
        if not rows:
            break
        for x in rows:
            name = x.get("name") or ""
            if any(t in name for t in ("ST", "退", "*")):
                continue
            # 排除 ETF/LOF/基金：跳空来自套利与折价，不是资金博弈信号
            if P["exclude_fund"] and any(w in name.upper()
                                         for w in P["fund_name_words"]):
                continue
            close, high, low = _f(x.get("trade")), _f(x.get("high")), _f(x.get("low"))
            prev = _f(x.get("settlement"))
            if not (close and high and low and prev):
                continue
            out.append({
                "code": x.get("code"), "symbol": x.get("symbol"), "name": name,
                "close": close, "open": _f(x.get("open")), "high": high,
                "low": low, "prev_close": prev,
                "pct": _f(x.get("changepercent")),
                "volume": _f(x.get("volume")),        # 股
                "amount": _f(x.get("amount")),        # 元
                "float_cap": _f(x.get("nmc")) * 1e4,  # nmc 单位是万元
            })
    return out


def tencent_kline(symbol: str, count: int = 40,
                  end: str | None = None) -> list[dict]:
    """腾讯日线。symbol 形如 sh600519。返回升序列表。"""
    param = f"{symbol},day,,,{count},qfq" if not end else \
            f"{symbol},day,,{end},{count},qfq"
    _throttle("tk", 0.25)
    try:
        r = requests.get(_TENCENT_K, params={"param": param},
                         headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
                         timeout=(8, 20))
        payload = r.json()
    except Exception:  # noqa: BLE001
        return []
    data = payload.get("data") or {}
    node = data.get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    out = []
    for row in rows:
        if len(row) < 6:
            continue
        out.append({"date": row[0], "open": _f(row[1]), "close": _f(row[2]),
                    "high": _f(row[3]), "low": _f(row[4]), "volume": _f(row[5])})
    return out


def market_gate() -> dict[str, Any]:
    """条件②：大盘闸门。用中证1000的 20 日涨幅判定。"""
    sym = P["mkt_code_sina"]
    _throttle("tq", 0.3)
    info: dict[str, Any] = {"code": sym, "name": "中证1000"}
    try:
        r = requests.get(_TENCENT_Q + sym, headers={"User-Agent": UA}, timeout=12)
        r.encoding = "gbk"
        body = r.text.split("=", 1)[1].strip().strip('"')
        f = body.split("~")
        info["close"] = _f(f[3])
        info["pct"] = _f(f[32]) if len(f) > 32 else 0.0
    except Exception:  # noqa: BLE001
        pass
    bars = tencent_kline(sym, count=P["mkt_period"] + 5)
    if len(bars) >= 2:
        info["ma20"] = round(sum(b["close"] for b in bars[-P["mkt_period"]:])
                             / min(len(bars), P["mkt_period"]), 2)
        if len(bars) > P["mkt_period"]:
            info["ret20"] = round(
                (bars[-1]["close"] / bars[-(P["mkt_period"] + 1)]["close"] - 1) * 100, 2)
        info["above_ma20"] = bars[-1]["close"] > info.get("ma20", 0)
        info["date"] = bars[-1]["date"]
    info["threshold"] = P["mkt_ret_max"]
    info["pass"] = info.get("ret20", 99) < P["mkt_ret_max"]
    return info


# ============================================================
# 选股
# ============================================================
@dataclass
class Pick:
    code: str
    name: str
    close: float
    pct: float
    gap_pct: float
    vol_ratio: float
    amount_yi: float
    float_cap_yi: float
    prev_high: float          # 缺口下沿
    stop: float
    target_1r: float
    gap_type: str
    vol_level: str
    score: float
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def pick(market: list[dict], mkt: dict[str, Any],
         verbose: bool = True) -> dict[str, Any]:
    print("=" * 72)
    print("  每日盘后选股 · 缺口突破 + 缩量 + 大盘回调")
    print("=" * 72)

    # ---- 条件② 大盘闸门
    print("\n【条件②】大盘状态")
    print(f"  {mkt.get('name', '中证1000')}  收盘 {mkt.get('close', '—')}  "
          f"当日 {mkt.get('pct', 0):+.2f}%")
    print(f"  MA{P['mkt_period']} = {mkt.get('ma20', '—')}  "
          f"20日涨幅 {mkt.get('ret20', 0):+.2f}%（阈值 < {P['mkt_ret_max']}%）")
    if mkt["pass"]:
        print("  ▶ 大盘处于回调期，符合开仓条件")
    else:
        print(f"  ▶ 大盘未处于回调期，条件②不满足")

    # ---- 条件① + 缺口
    print(f"\n【筛选】全市场 {len(market)} 只 → 逐只验证缺口与量比")
    t0 = time.time()
    stats = {"no_gap": 0, "gap_small": 0, "gap_big": 0, "vol_fail": 0,
             "amount": 0, "limit": 0, "fund": 0, "kline_err": 0, "pass": 0}
    cands: list[Pick] = []

    for q in market:
        # 代码级兜底：排除 ETF/LOF 常见代码段
        c6 = q["code"]
        if P["exclude_fund"] and (
            c6.startswith(("51", "56", "58", "15", "16", "50", "17"))
        ):
            stats["fund"] += 1
            continue
        # 成交额门槛
        if not (P["min_amount_yi"] * 1e8 <= q["amount"] <= P["max_amount_yi"] * 1e8):
            stats["amount"] += 1
            continue
        # 涨停买不到
        if abs(q["pct"]) > P["limit_pct"]:
            stats["limit"] += 1
            continue
        # 缺口粗筛：当日最低 > 昨收（快照无昨高，先用昨收缩小范围）
        if q["low"] <= q["prev_close"]:
            stats["no_gap"] += 1
            continue
        gap = (q["low"] - q["prev_close"]) / q["prev_close"] * 100
        if gap < P["gap_min"]:
            stats["gap_small"] += 1
            continue
        if gap > P["gap_max"]:
            stats["gap_big"] += 1
            continue

        # 条件① 量比：需真实 20 日均量（多取 1 根用于排除当日）
        bars = tencent_kline(q["symbol"], count=P["vol_lookback"] + 5)
        if len(bars) < P["vol_lookback"] + 2:
            stats["kline_err"] += 1
            continue

        # 缺口精算：以上一交易日真实最高价为基准重算。
        # 注意：盘后腾讯日线最后一根【就是当日】（实测 2026-09-30 与实时快照一致），
        # 因此上一交易日是倒数第二根。用 bars[-1] 会把今日最高价当成昨高，
        # 使缺口恒为负、全部标的被误杀。
        prev_high = bars[-2]["high"] if len(bars) >= 2 else 0.0
        if prev_high <= 0:
            stats["kline_err"] += 1
            continue
        if q["low"] <= prev_high:
            stats["no_gap"] += 1          # 昨收口径高估了缺口，用真实昨高复核后淘汰
            continue
        gap = (q["low"] - prev_high) / prev_high * 100
        if not (P["gap_min"] <= gap <= P["gap_max"]):
            stats["gap_small" if gap < P["gap_min"] else "gap_big"] += 1
            continue

        # 量比：均量须取「今日之前」的 20 根，否则含当日会低估量比
        today_vol = q["volume"] / 100.0        # 股 → 手
        hist = [b["volume"] for b in bars[-(P["vol_lookback"] + 1):-1]]
        if len(hist) < P["vol_lookback"]:
            stats["kline_err"] += 1
            continue
        avg = sum(hist) / len(hist)
        vol_ratio = today_vol / avg if avg else 0.0
        if vol_ratio >= P["vol_ratio_max"]:
            stats["vol_fail"] += 1
            continue

        stats["pass"] += 1
        # prev_high 已在缺口精算段用真实昨高赋值
        stop = round(prev_high * (1 - P["stop_buffer"] / 100), 2)
        r1 = q["close"] - prev_high
        gap_type = "突破缺口" if gap >= 2.0 else "持续缺口"
        if vol_ratio < 0.6:
            vol_level = "极缩量(最优)"
        elif vol_ratio < 1.0:
            vol_level = "很缩(优)"
        elif vol_ratio < P["vol_ratio_strong"]:
            vol_level = "缩量(良)"
        else:
            vol_level = "偏缩(弱)"

        # 打分：量比越低越好（回测单调递减），缺口适中最好
        vol_s = max(0, (P["vol_ratio_max"] - vol_ratio) / P["vol_ratio_max"] * 45)
        gap_s = 25 if 1.5 <= gap <= 4 else max(0, 25 - abs(gap - 2.5) * 8)
        cap_s = 20 if 30 <= q["float_cap"] / 1e8 <= 500 else 10
        hard_s = 10 if not any(t in q["name"] for t in ("ST", "退")) else 0
        score = round(vol_s + gap_s + cap_s + hard_s, 1)

        cands.append(Pick(
            code=q["code"], name=q["name"], close=q["close"], pct=q["pct"],
            gap_pct=round(gap, 2), vol_ratio=round(vol_ratio, 2),
            amount_yi=round(q["amount"] / 1e8, 2),
            float_cap_yi=round(q["float_cap"] / 1e8, 1),
            prev_high=round(prev_high, 2), stop=stop,
            target_1r=round(q["close"] + r1, 2),
            gap_type=gap_type, vol_level=vol_level, score=score,
            detail={"量比档": vol_level, "缺口类型": gap_type},
        ))
        if verbose and len(cands) % 5 == 0:
            print(f"    ... 已通过 {len(cands)} 只（{time.time()-t0:.0f}s）")

    cands.sort(key=lambda x: -x.score)      # Pick 是 dataclass，须用属性访问
    top = cands[:P["max_picks"]]

    print(f"\n  淘汰明细：无缺口 {stats['no_gap']}，缺口过小 {stats['gap_small']}，"
          f"缺口过大 {stats['gap_big']}")
    print(f"            放量剔除 {stats['vol_fail']}，"
          f"成交额不足 {stats['amount']}，涨停剔除 {stats['limit']}，"
          f"基金剔除 {stats['fund']}，K线异常 {stats['kline_err']}")
    print(f"  耗时 {time.time() - t0:.0f}s")

    # ---- 输出
    can_trade = mkt["pass"] and bool(top)
    print(f"\n{'='*72}")
    if not mkt["pass"]:
        print("  ⛔ 大盘条件不满足 → 今日空仓")
    elif not top:
        print("  无符合条件的标的 → 今日空仓")
    else:
        state = "✅ 可开仓" if can_trade else "⛔ 今日空仓"
        print(f"  候选池（通过缺口+缩量：{len(cands)} 只，展示前 {len(top)}）  {state}")
        print("=" * 72)
        for i, c in enumerate(top, 1):
            print(f"\n  【{i}】{c.name}({c.code})  {c.score} 分   {c.gap_type} · {c.vol_level}")
            print(f"       现价 {c.close}（{c.pct:+.2f}%）  缺口 {c.gap_pct}%  "
                  f"量比 {c.vol_ratio}")
            print(f"       成交额 {c.amount_yi}亿  流通市值 {c.float_cap_yi}亿")
            print(f"       缺口下沿 {c.prev_high}  止损 {c.stop}  "
                  f"1R目标 {c.target_1r}")

    if can_trade and top:
        first = top[0]
        print(f"\n{'='*72}")
        print("  执行纪律")
        print("=" * 72)
        print("  1. 次日开盘买入（回测验证：当日直接追优于等回踩）")
        print(f"  2. 各自止损见上（缺口下沿 −{P['stop_buffer']}%），跌破即离场，不摊平")
        print(f"     首选 {first.name}({first.code}) 止损 {first.stop}")
        print(f"  3. 持有 {P['max_hold']} 个交易日，或达 1R 减半仓后转移动止损")
        print("  4. 单笔风险敞口 ≤ 总资金 1%，单日最多 3 只")
        print("  5. 若次日大盘继续大幅低开（>1%），全部取消")

    return {"date": mkt.get("date"), "market": mkt, "can_trade": can_trade,
            "picks": [c.to_dict() for c in top],
            "stats": stats}


def main() -> None:
    ap = argparse.ArgumentParser(description="每日盘后选股（缺口+缩量+大盘回调）")
    ap.add_argument("-d", "--date", help="交易日（仅记录用，实时接口取最新）")
    ap.add_argument("--json", help="导出结果 JSON")
    ap.add_argument("--no-trade", action="store_true", help="只做大盘体检")
    a = ap.parse_args()
    try:
        mkt = market_gate()
        if a.no_trade:
            print("=" * 72)
            print("  大盘体检")
            print("=" * 72)
            print(f"  {mkt.get('name')}  收盘 {mkt.get('close')}  "
                  f"20日涨幅 {mkt.get('ret20'):+.2f}%  "
                  f"MA{P['mkt_period']} {mkt.get('ma20')}")
            print(f"  ▶ 条件②{'满足' if mkt['pass'] else '不满足'} → "
                  f"{'可开仓' if mkt['pass'] else '今日空仓'}")
            return
        market = iter_market()
        if not market:
            print("行情获取失败，退出。", file=sys.stderr)
            sys.exit(1)
        res = pick(market, mkt)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"\n结果已写入 {a.json}")
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
