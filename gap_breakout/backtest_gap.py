# -*- coding: utf-8 -*-
"""跳空缺口回踩买点 · 回测引擎（基于 solo/cache_daily 本地缓存）

数据：5541 只 × 343 交易日（2025-01-02 ~ 2026-06-05），全离线，无需网络。

回测标的
    信号：当日最低价 > 前一交易日最高价（真实向上缺口）
          缺口幅度 0.8% ~ 9%（剔除过小噪声与过大衰竭）
          缺口日量比 ≥ 1.3（资金主动介入）
    买点：缺口后回踩至下沿上方 0~2% 区间（买在下沿 +0.5%）
    止损：缺口下沿下方 1.5%
    持有：最多 N 个交易日（含隔夜，跳空低开按开盘价计）

关键设计
    1. 信号日 T 收盘后确认，**最早 T+1 开盘买入** —— 不存在未来函数
    2. 回踩买点要求 T+1..T+k 内任一日触及买点区间，触及即成交（限价单语义）
    3. 若先触及止损则判定失败，不计收益（保守处理跳空低开）
    4. 同日既触买点又触止损时，按更保守的「先止损」处理

用法
    python backtest_gap.py                 # 默认参数全样本回测
    python backtest_gap.py --hold 5        # 最长持有5日
    python backtest_gap.py --band 1 3     # 回踩带 1%~3%
    python backtest_gap.py --json bt.json  # 导出明细
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from ts_cache import (_I_AMT, _I_CLOSE, _I_DATE, _I_HIGH, _I_LOW, _I_OPEN,
                      _I_PCT, _I_PREV, _I_VOL, list_codes, load_bars)

# ============================================================
# 参数区
# ============================================================
BP = {
    # 信号过滤
    "gap_min": 0.8,              # 最小缺口幅度(%)
    "gap_max": 9.0,              # 最大缺口幅度(%)，超过视为衰竭/异常
    "vol_ratio_min": 1.3,        # 缺口日量比下限（放量突破）
    "vol_lookback": 20,          # 均量回溯
    "breakaway_min": 2.0,        # 突破缺口阈值
    "exhaust_max": 1.2,          # 衰竭缺口阈值（≤此剔除）

    # 买点
    "band_low": 0.0,             # 回踩带下沿（相对缺口下沿 %）
    "band_high": 2.0,            # 回踩带上沿
    "entry_offset": 0.5,         # 成交价 = 下沿 ×(1+offset%)，带内挂限价单

    # 风控
    "stop_buffer": 1.5,          # 止损 = 下沿 ×(1-stop_buffer%)
    "max_hold": 5,               # 最长持有交易日
    "min_amount": 0.5,           # 最小成交额（亿），过滤流动性极差
    "exclude_st_name": True,

    # 去重
    "one_signal_per_stock": True,   # 同一只票在冷却期内只发一次信号
    "cooldown": 10,                 # 冷却交易日

    # 可交易性约束（重要：缺了会严重高估收益）
    "skip_signal_day_limit": True,  # 剔除信号日已涨停的票（买不到）
    "limit_pct": 9.0,               # |pct_chg| 超过此视为涨跌停
    "skip_stop_on_limit_down": True,# 止损当日跌停则顺延（卖不掉）
    "ma_filter": False,             # 20日均线过滤：收盘价须高于MA20
    "ma_period": 20,
}


# ============================================================
# 单只股票信号扫描
# ============================================================
def scan_stock(bars: list[list], name: str = "") -> list[dict[str, Any]]:
    """在单只股票的历史上扫描所有「回踩买点」信号。"""
    sigs: list[dict[str, Any]] = []
    n = len(bars)
    if n < BP["vol_lookback"] + 5:
        return sigs

    last_signal_idx = -999

    for i in range(BP["vol_lookback"], n - 1):
        cur, prev = bars[i], bars[i - 1]

        # --- 缺口判定：当日最低 > 前日最高
        if cur[_I_LOW] <= prev[_I_HIGH]:
            continue
        gap_pct = (cur[_I_LOW] - prev[_I_HIGH]) / prev[_I_HIGH] * 100
        if not (BP["gap_min"] <= gap_pct <= BP["gap_max"]):
            continue

        # --- 缺口类型（衰竭剔除）
        if gap_pct <= BP["exhaust_max"]:
            continue

        # --- 量能
        w = bars[i - BP["vol_lookback"]:i]
        avg_vol = sum(b[_I_VOL] for b in w) / len(w) if w else 0.0
        if avg_vol <= 0 or cur[_I_VOL] / avg_vol < BP["vol_ratio_min"]:
            continue

        # --- 流动性
        if cur[_I_AMT] < BP["min_amount"] * 1e4:   # tushare amount 单位千元
            continue

        # --- 可交易性：信号日涨停买不到
        if BP["skip_signal_day_limit"] and abs(cur[_I_PCT]) > BP["limit_pct"]:
            continue

        # --- 可交易性：止损当日跌停卖不掉则顺延（_simulate 内处理）

        # --- 均线过滤
        if BP["ma_filter"]:
            w = bars[i - BP["ma_period"] + 1:i + 1]
            if w and cur[_I_CLOSE] <= sum(x[_I_CLOSE] for x in w) / len(w) * 0.98:
                continue

        # --- 冷却去重
        if BP["one_signal_per_stock"] and i - last_signal_idx < BP["cooldown"]:
            continue

        gap_low = prev[_I_HIGH]
        entry = gap_low * (1 + BP["entry_offset"] / 100)
        band_hi = gap_low * (1 + BP["band_high"] / 100)
        stop = gap_low * (1 - BP["stop_buffer"] / 100)

        # --- 在后续 max_hold 日内寻找买点触发
        trade = _simulate(bars, i, entry, band_hi, stop)
        if trade is None:
            continue

        trade.update(
            code=cur[0], name=name, gap_date=cur[_I_DATE],
            gap_pct=round(gap_pct, 2), gap_low=round(gap_low, 3),
            vol_ratio=round(cur[_I_VOL] / avg_vol, 2),
            gap_type="突破缺口" if gap_pct >= BP["breakaway_min"] else "持续缺口",
            signal_close=round(cur[_I_CLOSE], 3),
        )
        sigs.append(trade)
        last_signal_idx = i

    return sigs


def _simulate(bars: list[list], sig_i: int, entry: float,
              band_hi: float, stop: float) -> dict[str, Any] | None:
    """从信号日次一交易日起模拟持仓。

    返回 None 表示 max_hold 内既未触发买点也未触发止损（无交易）。
    """
    n = len(bars)
    start = sig_i + 1                     # 最早 T+1 开盘（无未来函数）
    end = min(n, sig_i + 1 + BP["max_hold"])

    entry_i = None
    for j in range(start, end):
        b = bars[j]
        # 保守：同日既可能触买点又可能触止损时，判定为止损（先止损）
        if b[_I_LOW] <= stop:
            return {
                "entry_date": None, "exit_date": b[_I_DATE],
                "entry_price": None, "exit_price": round(stop, 3),
                "ret_pct": round((stop - bars[sig_i][_I_CLOSE]) / bars[sig_i][_I_CLOSE] * 100, 2),
                "status": "止损", "hold_days": j - sig_i,
            }
        # 触及买点区间（用 low 判触发，符合限价单语义）
        if b[_I_LOW] <= band_hi and b[_I_HIGH] >= entry:
            entry_i = j
            break

    if entry_i is None:
        return None

    ent_price = entry
    sig_close = bars[sig_i][_I_CLOSE]

    # --- 持有至 max_hold 或触发止损
    for k in range(entry_i, min(n, entry_i + BP["max_hold"] + 1)):
        b = bars[k]
        if b[_I_LOW] <= stop:
            # 跌停当日卖不掉 → 顺延到下一个可交易日
            if BP["skip_stop_on_limit_down"] and abs(b[_I_PCT]) > BP["limit_pct"]:
                continue
            exit_p, status = stop, "止损"
        elif k == entry_i + BP["max_hold"] or k == n - 1:
            exit_p, status = b[_I_CLOSE], "到期平仓"
        else:
            continue
        return {
            "entry_date": bars[entry_i][_I_DATE],
            "exit_date": b[_I_DATE],
            "entry_price": round(ent_price, 3),
            "exit_price": round(exit_p, 3),
            "ret_pct": round((exit_p - ent_price) / ent_price * 100, 2),
            "ret_vs_signal": round((exit_p - sig_close) / sig_close * 100, 2),
            "status": status,
            "hold_days": k - sig_i,
        }
    return None


# ============================================================
# 全市场回测
# ============================================================
def run_backtest(max_stocks: int = 0, verbose: bool = True) -> dict[str, Any]:
    codes = list_codes()
    if max_stocks:
        codes = codes[:max_stocks]
    if verbose:
        print("=" * 72)
        print("  跳空缺口回踩买点 · 回测")
        print("=" * 72)
        print(f"  样本：{len(codes)} 只")
        print(f"  参数：缺口 {BP['gap_min']}~{BP['gap_max']}% | "
              f"量比≥{BP['vol_ratio_min']} | 回踩带 {BP['band_low']}~{BP['band_high']}%")
        print(f"        买点=下沿+{BP['entry_offset']}% 止损=下沿-{BP['stop_buffer']}% "
              f"最长持有{BP['max_hold']}日 冷却{BP['cooldown']}日")
        print("-" * 72)

    all_trades: list[dict[str, Any]] = []
    for idx, code in enumerate(codes, 1):
        bars = load_bars(code)
        if not bars:
            continue
        name = code
        if BP["exclude_st_name"]:
            # 从 tushare 缓存拿不到名称，此处按代码标识
            name = code
        try:
            all_trades += scan_stock(bars, name)
        except Exception:  # noqa: BLE001
            continue
        if verbose and idx % 500 == 0:
            print(f"    ... {idx}/{len(codes)}  已发现 {len(all_trades)} 笔")

    if verbose:
        print(f"\n  完成：{len(all_trades)} 笔交易")

    if not all_trades:
        return {"trades": [], "stats": {}}

    rets = [t["ret_pct"] for t in all_trades if t.get("ret_pct") is not None]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    stopped = [t for t in all_trades if t["status"] == "止损"]

    n = len(rets)
    win_rate = len(wins) / n * 100
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = sum(rets) / n
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

    # 按交易序列的累计收益曲线（等权组合视角，每笔等额）
    # 注意：逐笔复利会夸大回撤，这里用累计收益 / 总笔数 近似组合净值
    n_tr = len(rets)
    equity_pct = 0.0
    peak_pct = 0.0
    mdd = 0.0
    for r in rets:
        equity_pct += r
        peak_pct = max(peak_pct, equity_pct)
        mdd = max(mdd, peak_pct - equity_pct)
    # 组合层面最大回撤（%）
    combo_mdd = mdd / n_tr * 100 if n_tr else 0.0
    # 等权组合总收益
    total_ret = equity_pct / n_tr

    # 按缺口类型分组
    by_type: dict[str, list[float]] = {}
    for t in all_trades:
        by_type.setdefault(t["gap_type"], []).append(t["ret_pct"])

    stats = {
        "trades": n,
        "win_rate": round(win_rate, 1),
        "avg_ret": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2),
        "max_ret": round(max(rets), 1),
        "min_ret": round(min(rets), 1),
        "stop_ratio": round(len(stopped) / n * 100, 1),
        "combo_max_drawdown": round(combo_mdd, 2),
        "combo_total_ret": round(total_ret, 2),
        "by_type": {
            k: {
                "n": len(v),
                "win_rate": round(len([x for x in v if x > 0]) / len(v) * 100, 1),
                "avg_ret": round(sum(v) / len(v), 2),
            }
            for k, v in by_type.items()
        },
    }

    if verbose:
        print(f"\n{'='*72}")
        print("  回测结果")
        print("=" * 72)
        print(f"  交易笔数     {n}")
        print(f"  胜率         {win_rate:.1f}%")
        print(f"  平均收益     {expectancy:+.2f}%")
        print(f"  平均盈利     {avg_win:+.2f}%   平均亏损 {avg_loss:+.2f}%")
        print(f"  盈亏比       {abs(avg_win / avg_loss) if avg_loss else 0:.2f}")
        print(f"  盈利因子     {pf:.2f}")
        print(f"  最大单笔     {max(rets):+.1f}% / 最差 {min(rets):+.1f}%")
        print(f"  止损占比     {stats['stop_ratio']}%")
        print(f"  组合总收益   {total_ret:+.2f}%（等权逐笔累计）")
        print(f"  组合最大回撤 {combo_mdd:.2f}%")
        print(f"\n  分类型：")
        for k, v in stats["by_type"].items():
            print(f"    {k}: {v['n']} 笔  胜率 {v['win_rate']}%  均收益 {v['avg_ret']:+.2f}%")

    return {"trades": all_trades, "stats": stats}


def main() -> None:
    ap = argparse.ArgumentParser(description="跳空缺口回踩买点回测")
    ap.add_argument("--hold", type=int, help="最长持有交易日")
    ap.add_argument("--band", type=float, nargs=2, help="回踩带下沿 上沿(%)")
    ap.add_argument("--min-gap", type=float, help="最小缺口幅度%")
    ap.add_argument("--max-stocks", type=int, default=0, help="只跑前 N 只（调试用）")
    ap.add_argument("--json", help="导出结果 JSON")
    a = ap.parse_args()
    if a.hold:
        BP["max_hold"] = a.hold
    if a.band:
        BP["band_low"], BP["band_high"] = a.band
    if a.min_gap:
        BP["gap_min"] = a.min_gap
    res = run_backtest(a.max_stocks)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n已写入 {a.json}")


if __name__ == "__main__":
    main()
