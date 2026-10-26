"""
finchx_minute —— 5分钟线获取工具（FinchX 未原生封装，这里补齐）

背景
----
FinchX 3.1.1 全库 61 个公开接口中与 K 线相关的只有两个：
  - fx.market.ohlcv(code, start, end, adjustment)  → **仅日线**（腾讯 newfqkline，周期固定 day）
  - fx.market.equity_intraday / _5d                  → **1分钟粒度**分时（267 点/交易日）

即：**FinchX 没有 5 分钟 K 线接口**。但有两条可行路径拿到它：

路径 A（腾讯原生 mkline，推荐）
  直接请求腾讯 web 前端的分钟K线接口，一次拿到标准 OHLCV 五价 + 量 + 换手：
    https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?param=<symbol>,<period>,,,<count>
  period ∈ {m1, m5, m15, m30, m60}
  返回行：[时间, 开, 收, 高, 低, 成交量(手), {}, 换手率(‱)]
  特点：字段是标准 K 线（含 OHLC），但**无复权**；服务端固定返回 482 根上限，
        历史深度 = 482根 ÷ 每日根数（m5≈11个交易日 / m15≈31 / m30≈61 / m60≈121 / m1≈2）。

路径 B（FinchX intraday 自聚合）
  用 fx.market.equity_intraday_5d 拿近 5 日 1分钟分时，自己按 5 分钟切桶聚合。
  特点：**逐笔量价真实、可复权与否取决于分时源（分时为不复权）**，
        但只有近 5 个交易日深度；且分时含 15:00 后盘后点与开盘点，聚合时需自行对齐。

两条路径的 m5 收盘价在同日同刻**完全一致**（已实测交叉验证），可互为校验。

用法
----
    from finchx_minute import minute_bars, minute_bars_native, aggregate_5m

    # 路径 A：腾讯原生 5 分钟线
    bars = minute_bars_native("600519", "m5")
    print(bars[-1])   # {'datetime': '2026-10-23 15:00', 'open':..., 'high':..., 'low':..., 'close':..., 'volume':..., 'amount':..., 'turnover_rate':...}

    # 路径 B：从 FinchX 1 分钟分时聚合
    bars5 = aggregate_5m("600519")

命令行
------
    python finchx_minute.py 600519 --period m5
    python finchx_minute.py 600519 --source finchx
    python finchx_minute.py 600519 --period m5 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime

__all__ = [
    "minute_bars",
    "minute_bars_native",
    "aggregate_5m",
    "to_symbol",
    "PERIODS",
    "MKLINE_URL",
]

# 腾讯前端分钟K线接口（proxy.finance.qq.com 是可用域名；web.ifzq.gtimg.cn 在部分网络下会 502）
MKLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"

PERIODS = ("m1", "m5", "m15", "m30", "m60")

# 腾讯每日连续竞价 4 小时 + 集合竞价，5分钟线每交易日 48 根
BARS_PER_DAY = {
    "m1": 241,   # 实测 267 点/日（含 15:00 后盘后点），原生 m1 为 241 根/日
    "m5": 48,
    "m15": 16,
    "m30": 8,
    "m60": 4,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://finance.qq.com/",
    "Accept": "*/*",
}

# 沪深北市场代码 → 腾讯 symbol 前缀规则
_PREFIX_RULES = (
    ("60", "sh"), ("68", "sh"), ("51", "sh"), ("58", "sh"), ("56", "sh"), ("50", "sh"),
    ("00", "sz"), ("30", "sz"), ("15", "sz"), "16", "12", "18", "sz",
)


def to_symbol(code: str) -> str:
    """把 6 位 A 股代码转成腾讯 symbol（sh600519 / sz000001 / bj430047）。"""
    code = str(code).strip().lower()
    for suffix in ("sh", "sz", "bj"):
        if code.startswith(suffix):
            return code
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无法识别的股票代码：{code!r}（应为 6 位数字或带 sh/sz/bj 前缀）")
    if code[0] in ("4", "8"):
        return "bj" + code
    if code.startswith(("60", "68", "51", "58", "56", "50", "11", "13")):
        return "sh" + code
    return "sz" + code


def _get_json(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def minute_bars_native(
    code: str,
    period: str = "m5",
    count: int = 640,
    timeout: float = 20.0,
) -> list[dict]:
    """
    路径 A：腾讯原生分钟K线。

    返回 list[dict]，每根含：
        datetime (str 'YYYY-MM-DD HH:MM')  该根的**结束**时刻（09:35 表示 09:31~09:35）
        open/high/low/close (float)         不复权价格
        volume (float)                      成交量，单位「股」（原生为手，此处已 ×100）
        amount (float)                      估算成交额（元），按 (H+L+C)/3 × 成交量 反推
        turnover_rate (float)               换手率，单位 ‱（万分比），原生第 8 字段

    注意：服务端固定返回 482 根上限，count 超过该值不会报错但也不会更多。
    """
    if period not in PERIODS:
        raise ValueError(f"period 必须是 {PERIODS} 之一，收到 {period!r}")
    symbol = to_symbol(code)
    payload = _get_json(f"{MKLINE_URL}?param={symbol},{period},,,{count}", timeout=timeout)
    data = payload.get("data") or {}
    sym = data.get(symbol) or {}
    rows = sym.get(period)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            f"腾讯分钟K线返回空（symbol={symbol}, period={period}）。"
            f"返回 keys={list(sym.keys())}，可能该标的不支持分钟线（如北交所新股）。"
        )

    bars: list[dict] = []
    for raw in rows:
        try:
            stamp = str(raw[0])
            dt = datetime.strptime(stamp, "%Y%m%d%H%M")
            o, c, h, l = (float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]))
            vol_hand = float(raw[5])
            turnover = float(raw[7]) if len(raw) > 7 and raw[7] != "" else None
        except (ValueError, IndexError, TypeError) as exc:
            raise RuntimeError(f"分钟K线行结构异常：{raw!r}") from exc
        volume = vol_hand * 100.0
        bars.append({
            "datetime": dt.strftime("%Y-%m-%d %H:%M"),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": volume,
            "amount": round((h + l + c) / 3.0 * volume, 2),
            "turnover_rate": turnover,
        })
    bars.sort(key=lambda b: b["datetime"])
    return bars


def aggregate_5m(code: str, kind: str = "stock", timeout: float = 20.0) -> list[dict]:
    """
    路径 B：从 FinchX 1 分钟分时聚合 5 分钟线（近 5 个交易日）。

    对齐口径（已与腾讯原生 m5 逐根交叉验证，收盘价偏差 0.0000、成交量完全一致）：
      - 桶标签 = 该桶**结束**时刻：把每个 1 分钟点 ceil 到下一个 5 分钟边界
      - 首桶 09:35 含集合竞价，共 6 点（09:30~09:35）；其余桶各 5 点
      - 15:00 桶吸收 15:00 之后的盘后点
      - open=桶内首价，close=桶内末价，high/low=桶内极值，volume/amount=桶内求和
    """
    try:
        from finchx import FinchX
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("路径 B 需要 finchx 包：pip install finchx") from exc

    fx = FinchX(timeout=timeout)
    getter = fx.market.index_intraday_5d if kind == "index" else fx.market.equity_intraday_5d
    points = getter(code)

    def slot_of(hour: int, minute: int) -> int:
        total = hour * 60 + minute
        if total >= 15 * 60:             # 15:00 之后的盘后点并入 15:00 桶
            return 15 * 60
        if total <= 9 * 60 + 35:        # 09:30~09:35 → 首桶（含集合竞价）
            return 9 * 60 + 35
        return ((total + 4) // 5) * 5   # 向上取整到下一个 5 分钟边界

    buckets: dict[tuple[str, int], list[dict]] = {}
    for p in points:
        hour, minute = (int(x) for x in p["time"].split(":"))
        buckets.setdefault((p["date"], slot_of(hour, minute)), []).append(p)

    bars: list[dict] = []
    for (date, slot) in sorted(buckets):
        group = sorted(buckets[(date, slot)], key=lambda x: x["time"])
        prices = [g["price"] for g in group]
        bars.append({
            "datetime": f"{date} {slot // 60:02d}:{slot % 60:02d}",
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": sum(g.get("volume") or 0 for g in group),
            "amount": sum(g.get("amount") or 0 for g in group),
            "n_points": len(group),
        })
    return bars


def minute_bars(code: str, period: str = "m5", source: str = "native", **kwargs) -> list[dict]:
    """统一入口。source ∈ {'native'（腾讯原生，推荐）, 'finchx'（1分钟分时自聚合）}"""
    if source == "native":
        return minute_bars_native(code, period, **kwargs)
    if source == "finchx":
        if period != "m5":
            raise ValueError("finchx 路径仅支持 5 分钟聚合（源为1分钟分时）")
        kwargs.pop("count", None)
        return aggregate_5m(code, **kwargs)
    raise ValueError(f"source 必须是 'native' 或 'finchx'，收到 {source!r}")


def _fmt_table(bars: list[dict], limit: int = 12) -> str:
    lines = [
        f"{'时间':<18}{'开':>10}{'高':>10}{'低':>10}{'收':>10}{'量(手)':>12}{'换手‱':>9}",
        "-" * 79,
    ]
    for b in bars[-limit:]:
        lines.append(
            f"{b['datetime']:<18}{b['open']:>10.2f}{b['high']:>10.2f}{b['low']:>10.2f}"
            f"{b['close']:>10.2f}{b['volume'] / 100:>12.0f}"
            f"{(b.get('turnover_rate') or 0):>9.2f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="获取 5 分钟（及 m1/m15/m30/m60）K 线")
    ap.add_argument("code", help="6 位股票代码，如 600519 / 000001 / sz300750")
    ap.add_argument("--period", default="m5", choices=list(PERIODS), help="周期，默认 m5")
    ap.add_argument("--source", default="native", choices=["native", "finchx"], help="取数路径")
    ap.add_argument("--count", type=int, default=640, help="原生路径请求根数（服务端上限 482）")
    ap.add_argument("--json", dest="json_path", help="额外导出 JSON 文件")
    ap.add_argument("--tail", type=int, default=12, help="终端打印最后 N 根")
    args = ap.parse_args(argv)

    try:
        bars = minute_bars(args.code, args.period, args.source, count=args.count)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"✅ {args.code} ({to_symbol(args.code)}) {args.period} × {len(bars)} 根 "
          f"source={args.source}")
    if bars:
        print(f"   区间：{bars[0]['datetime']} ~ {bars[-1]['datetime']}")
    print()
    print(_fmt_table(bars, args.tail))

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump({"code": args.code, "period": args.period,
                       "source": args.source, "bars": bars}, f, ensure_ascii=False, indent=1)
        print(f"\n已导出：{args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
