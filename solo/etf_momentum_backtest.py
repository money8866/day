# -*- coding: utf-8 -*-
"""
ETF主线轮动策略(单一动量) 历史回测 + 参数对比
========================================================================
只按动量换仓, 保留风控; 支持参数化与多组合对比
  选品 : Top3中站上MA20者(ma20) | 直接取动量最高(top)
  换仓 : 固定周期(rebal) 或 排名跌出Top X%(exit) 或 跌破MA30
  保护 : 持仓满hold_min个交易日才允许动态退出
数据 : 复用 etf_mainline_strategy_tushare 的 Tushare 接口与缓存

用法:
    python etf_momentum_backtest.py            # 单组(当前参数)
    python etf_momentum_backtest.py --compare  # 多组合对比
"""
import os, sys, datetime, time, argparse
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_mainline_strategy_tushare as ems

HIST_CACHE = os.path.join(ems.CACHE_DIR, "etf_backtest_hist")
os.makedirs(HIST_CACHE, exist_ok=True)
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report_daily")


def _ts_code(code):
    return f"{code}.SH" if code.startswith(("5", "6")) else f"{code}.SZ"


def _clean_jumps(s):
    """消除除权/份额折算跳变: 单日|涨跌|>20%视为异常, 该日收益归零后重新累积(保持长度)"""
    s = s.ffill().bfill()
    r = s.pct_change().fillna(0.0)
    r[np.abs(r) > 0.20] = 0.0
    return (1 + r).cumprod() * s.iloc[0]


def load_hist(code, start, end):
    ts_code = _ts_code(code)
    f = os.path.join(HIST_CACHE, f"{ts_code}.csv")
    df = ems._read_cache(f)
    if df is None or df.empty or "close" not in df.columns:
        df = ems.pro.fund_daily(ts_code=ts_code,
                                start_date=start.strftime("%Y%m%d"),
                                end_date=end.strftime("%Y%m%d"),
                                fields="ts_code,trade_date,close")
        ems._save_cache(df, f)
        time.sleep(0.2)
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    return df.sort_values("trade_date").reset_index(drop=True)


def load_bench(start, end):
    f = os.path.join(HIST_CACHE, "idx_000300.csv")
    df = ems._read_cache(f)
    if df is None or df.empty or "close" not in df.columns:
        df = ems.pro.index_daily(ts_code="000300.SH",
                                 start_date=start.strftime("%Y%m%d"),
                                 end_date=end.strftime("%Y%m%d"),
                                 fields="trade_date,close")
        ems._save_cache(df, f)
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    return df.sort_values("trade_date").reset_index(drop=True)


# ======================= 回测核心(参数化) =======================
def backtest(close_m, name_m, all_dates, idx_list, p):
    start_i = max(idx_list[0], max(p["mom"], 30) + 1)
    comm = p["comm"]; n_etf = len(close_m)
    equity = 1.0; curve = []; holdings = None; trades = []

    def rank_at(i):
        rows = []
        for code, s in close_m.items():
            c0 = s.iloc[i]; c1 = s.iloc[i - p["mom"]]
            if pd.isna(c0) or pd.isna(c1) or c1 <= 0:
                continue
            ma20 = s.iloc[i - 19:i + 1].mean()
            ma30 = s.iloc[i - 29:i + 1].mean()
            rows.append({"code": code, "name": name_m[code], "close": c0,
                         "mom": c0 / c1 - 1,
                         "above_ma20": (not pd.isna(ma20)) and c0 > ma20,
                         "above_ma30": (not pd.isna(ma30)) and c0 > ma30})
        rows.sort(key=lambda x: x["mom"], reverse=True)
        for k, r in enumerate(rows):
            r["rank"] = k + 1
        return rows

    def pick(rows):
        if not rows:
            return None
        if p["pick"] == "top":
            return rows[0]
        for r in rows[:3]:
            if r["above_ma20"]:
                return r
        return rows[0]

    for i in idx_list:
        if i < start_i:
            continue
        d = all_dates[i]
        rows = rank_at(i)
        if not rows:
            continue
        if holdings is not None:
            s = close_m[holdings["code"]]
            pp, pn = s.iloc[i - 1], s.iloc[i]
            if pd.notna(pp) and pp > 0:
                equity *= (pn / pp)

        rebalance, reason = False, ""
        if holdings is None:
            rebalance, reason = True, "首次建仓"
        else:
            ds = i - holdings["buy_idx"]
            if ds >= p["rebal"]:
                rebalance, reason = True, f"周期({ds})"
            elif ds >= p["hold_min"]:
                hrow = next((r for r in rows if r["code"] == holdings["code"]), None)
                top_n = max(1, int(n_etf * p["exit"]))
                hrank = hrow["rank"] if hrow else n_etf
                if hrank > top_n:
                    rebalance, reason = True, f"跌出Top{top_n}(#{hrank})"
                elif (hrow is None) or (not hrow["above_ma30"]):
                    rebalance, reason = True, "破MA30"

        if rebalance:
            tgt = pick(rows)
            if tgt is None or (holdings is not None and tgt["code"] == holdings["code"]):
                curve.append((d, equity))
                continue
            if holdings is not None:
                sp = close_m[holdings["code"]].iloc[i]
                trades.append({"buy_date": holdings["buy_date"], "sell_date": d,
                               "name": holdings["name"], "code": holdings["code"],
                               "buy_price": holdings["buy_price"], "sell_price": sp,
                               "ret": sp / holdings["buy_price"] - 1, "reason": reason,
                               "hold": i - holdings["buy_idx"]})
                equity *= (1 - comm)
            equity *= (1 - comm)
            holdings = {"code": tgt["code"], "name": tgt["name"],
                        "buy_price": tgt["close"], "buy_idx": i, "buy_date": d}
        curve.append((d, equity))

    eq = pd.Series([e for _, e in curve], index=pd.DatetimeIndex([d for d, _ in curve]))
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    dr = eq.pct_change().dropna()
    dd = (eq - eq.cummax()) / eq.cummax()
    rets = [t["ret"] for t in trades]
    return {
        "eq": eq,
        "total": eq.iloc[-1] - 1,
        "cagr": eq.iloc[-1] ** (1 / years) - 1,
        "mdd": dd.min(),
        "sharpe": dr.mean() / (dr.std() + 1e-12) * np.sqrt(252),
        "calmar": (eq.iloc[-1] ** (1 / years) - 1) / abs(dd.min()) if dd.min() < 0 else float("inf"),
        "n": len(trades),
        "win": (sum(1 for r in rets if r > 0) / len(rets)) if rets else 0,
        "yearly": {y: eq[eq.index.year == y].iloc[-1] / eq[eq.index.year == y].iloc[0] - 1
                   for y in sorted(set(eq.index.year))},
        "trades": trades,
        "holdings": holdings,
    }


def build_data(start, end):
    hist_start = start - datetime.timedelta(days=130)
    print("  加载历史数据...")
    data = {}
    for name, code in ems.ETF_POOL.items():
        try:
            df = load_hist(code, hist_start, end)
            if len(df) > 50:
                data[code] = (name, df.set_index("trade_date")["close"])
        except Exception as e:
            print(f"  [WARN] {name}({code}): {e}")
    bench = load_bench(hist_start, end)
    all_dates = list(pd.DatetimeIndex(bench["trade_date"]))
    close_m, name_m = {}, {}
    for code, (name, s) in data.items():
        close_m[code] = _clean_jumps(s.reindex(all_dates).ffill())
        name_m[code] = name
    idx_list = [i for i, d in enumerate(all_dates) if d >= start]
    print(f"  已加载 {len(close_m)}/{len(ems.ETF_POOL)} 只ETF, 区间交易日 {len(idx_list)} 天")
    return close_m, name_m, all_dates, idx_list, bench


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20250101")
    ap.add_argument("--end", default="20260922")
    ap.add_argument("--commission", type=float, default=0.0005)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--grid", action="store_true")
    args = ap.parse_args()
    start = datetime.datetime.strptime(args.start, "%Y%m%d")
    end = datetime.datetime.strptime(args.end, "%Y%m%d")
    close_m, name_m, all_dates, idx_list, bench = build_data(start, end)
    b = bench.set_index("trade_date")["close"]

    if args.grid:
        print("\n" + "=" * 96)
        print(f"  周期 × 退出阈值 网格扫描 (动量20/选品top)  {start:%Y-%m-%d} ~ {end:%Y-%m-%d}")
        print("=" * 96)
        print(f"{'周期\\退出':<10}{'Top30%(10只)':>22}{'Top35%(12只)':>22}{'Top40%(14只)':>22}")
        print("-" * 96)
        for rebal in [15, 20, 25, 30, 35, 40]:
            cells = []
            for exit in [0.30, 0.35, 0.40]:
                pr = dict(mom=20, rebal=rebal, exit=exit, hold_min=5, pick="top", comm=args.commission)
                r = backtest(close_m, name_m, all_dates, idx_list, pr)
                y25 = r["yearly"].get(2025, 0); y26 = r["yearly"].get(2026, 0)
                cells.append(f"{y25:>6.0%}/{y26:>6.0%}/{r['total']:>6.0%}")
            print(f"{rebal:<10}" + "".join(f"{c:>22}" for c in cells))
        print("-" * 96)
        print("  单元格格式: 2025收益/2026收益/总收益")
        print("=" * 96)
        return

    if args.compare:
        combos = [
            ("当前基准    20/60/Top20%/ma20", dict(mom=20, rebal=60, exit=0.20, hold_min=5, pick="ma20")),
            ("推荐A       20/30/Top30%/top ", dict(mom=20, rebal=30, exit=0.30, hold_min=5, pick="top")),
            ("推荐B       20/25/Top30%/top ", dict(mom=20, rebal=25, exit=0.30, hold_min=5, pick="top")),
            ("推荐C       20/35/Top30%/top ", dict(mom=20, rebal=35, exit=0.30, hold_min=5, pick="top")),
            ("备选        20/30/Top35%/top ", dict(mom=20, rebal=30, exit=0.35, hold_min=5, pick="top")),
            ("选品对比    20/30/Top30%/ma20", dict(mom=20, rebal=30, exit=0.30, hold_min=5, pick="ma20")),
            ("动量25      25/30/Top30%/top ", dict(mom=25, rebal=30, exit=0.30, hold_min=5, pick="top")),
            ("动量15      15/30/Top30%/top ", dict(mom=15, rebal=30, exit=0.30, hold_min=5, pick="top")),
        ]
        print("\n" + "=" * 108)
        print(f"  参数对比回测  {start:%Y-%m-%d} ~ {end:%Y-%m-%d}  (单边费率{args.commission:.2%})")
        print("=" * 108)
        hdr = f"{'组合':<26}{'2025':>9}{'2026':>9}{'总收益':>10}{'年化':>9}{'回撤':>9}{'夏普':>7}{'卡玛':>7}{'换手':>6}{'胜率':>7}"
        print(hdr)
        print("-" * 108)
        results = []
        for label, pr in combos:
            pr = dict(pr); pr["comm"] = args.commission
            r = backtest(close_m, name_m, all_dates, idx_list, pr)
            results.append((label, r))
            y25 = r["yearly"].get(2025, 0); y26 = r["yearly"].get(2026, 0)
            print(f"{label:<26}{y25:>9.1%}{y26:>9.1%}{r['total']:>10.1%}{r['cagr']:>9.1%}"
                  f"{r['mdd']:>9.1%}{r['sharpe']:>7.2f}{r['calmar']:>7.2f}{r['n']:>6d}{r['win']:>7.1%}")
        by25 = b[b.index.year == 2025]; by26 = b[b.index.year == 2026]
        print("-" * 108)
        print(f"{'沪深300 基准':<26}{by25.iloc[-1]/by25.iloc[0]-1:>9.1%}{by26.iloc[-1]/by26.iloc[0]-1:>9.1%}"
              f"{b.iloc[-1]/b.iloc[0]-1:>10.1%}")
        print("=" * 108)

        # 最优组合的交易明细
        best = max(results, key=lambda x: x[1]["total"])
        print(f"\n  最优组合: {best[0]}")
        print(f"  {'买入日':<12}{'卖出日':<12}{'标的':<9}{'持有':>5}{'收益':>9}  原因")
        for t in best[1]["trades"]:
            print(f"  {t['buy_date']:%Y-%m-%d}  {t['sell_date']:%Y-%m-%d}  {t['name']:<8}"
                  f"{t['hold']:>4}天{t['ret']:>9.2%}  {t['reason']}")
        h = best[1]["holdings"]
        if h:
            lp = close_m[h["code"]].iloc[idx_list[-1]]
            print(f"  {h['buy_date']:%Y-%m-%d}  {'持仓中':<10}  {h['name']:<8}{idx_list[-1]-h['buy_idx']:>4}天"
                  f"{lp/h['buy_price']-1:>9.2%}  未平仓")
        return

    # 单组
    p = dict(mom=20, rebal=ems.REBAL_DAYS, exit=ems.DYNAMIC_EXIT_TOP_PCT,
             hold_min=ems.MIN_HOLD_DAYS, pick="ma20", comm=args.commission)
    r = backtest(close_m, name_m, all_dates, idx_list, p)
    print(f"\n  区间 {r['eq'].index[0]:%Y-%m-%d} ~ {r['eq'].index[-1]:%Y-%m-%d}")
    print(f"  累计 {r['total']:.2%} | 年化 {r['cagr']:.2%} | 回撤 {r['mdd']:.2%} | 夏普 {r['sharpe']:.2f} | 换手 {r['n']}")


if __name__ == "__main__":
    main()
