"""回测：ELD V2 榜单信号的前向收益验证（零前视，基于历史榜单存档）。

假设（待验证）：
  H1 榜单 TOP N（T 日收盘后产出）→ T+1 开盘买入 → T+N 收盘卖出，
     净收益（含摩擦）> 池内等权基准（选股 alpha）且 > 上证指数同窗口
  H2 next_day_buyable=True 子集表现优于 False（系统自带次日可买过滤有效）
  H3 final_score_v2 与 T+5 收益的 RankIC > 0（评分有排序能力）

零裁量规则（无任何人工判断）：
  入场  T+1 开盘价成交；T+1 一字板（high==low）视为无法成交，剔除该笔
  出场  T+N 收盘卖出（N=1/3/5 三窗口）；止损口径：持有期内首日 close<=stop_loss_price
        → 次段按 min(open, stop) 成交
  摩擦  佣金 0.025% x2 + 印花税 0.05%（卖出）+ 滑点 0.10% x2（默认）
        压力口径滑点 0.20% / 0.30% 单边
  除权  全程使用官方 close/pre_close 比值链式累乘（除权安全，不用价格差）

样本：D:/mystock/report_daily/eld_report_2026MMDD.json 存档榜单
  主样本 20260803~20260902（final_score_v2 与买点字段完备期）
  T+5 完整窗口的榜单日最多到 20260827（其后 forward 交易日不足）
  注意：榜单为各时期版本的真实产出（0901 起为精简版），衡量系统整体历史表现。

数据：d:/mystock/cache_daily/daily_YYYYMMDD.csv（全市场日线快照）
  + eld_csv/index_000001.SH_*.csv（上证指数基准）

输出：控制台统计 + backtest_eld_rank_result.csv（逐笔明细）
"""
import csv
import glob
import json
import os
import sys
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT_DIR = r"D:/mystock/report_daily"
CSV_DIR = r"d:/mystock/cache_daily"
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_eld_rank_result.csv")

SAMPLE_DATES = [
    "20260803", "20260804", "20260805", "20260806", "20260807",
    "20260810", "20260811", "20260812", "20260814",
    "20260817", "20260818", "20260819", "20260820", "20260821",
    "20260824", "20260825", "20260826", "20260827", "20260828",
    "20260831", "20260901", "20260902",
]
WINDOWS = (1, 3, 5)
TOPS = (5, 10, 20, 50)

# 摩擦参数（单边小数）
COMMISSION = 0.00025
STAMP_TAX = 0.0005
SLIPPAGE = 0.001


def load_daily():
    """→ {trade_date: {ts_code: dict(open,high,low,close,pre_close,pct_chg)}}"""
    days = {}
    for p in glob.glob(os.path.join(CSV_DIR, "daily_2*.csv")):
        d = os.path.basename(p)[6:14]
        if not ("20260720" <= d <= "20260903"):
            continue
        m = {}
        with open(p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    m[row["ts_code"]] = {
                        "open": float(row["open"]), "high": float(row["high"]),
                        "low": float(row["low"]), "close": float(row["close"]),
                        "pre_close": float(row["pre_close"]),
                        "pct_chg": float(row["pct_chg"]),
                    }
                except (TypeError, ValueError, KeyError):
                    continue
        days[d] = m
    return days


def load_index():
    """→ {trade_date: dict(open, close)}"""
    ps = glob.glob(os.path.join(CSV_DIR, "eld_csv", "index_000001.SH_*.csv"))
    if not ps:
        return {}
    idx = {}
    with open(ps[0], encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                idx[row["trade_date"]] = {"open": float(row["open"]), "close": float(row["close"])}
            except (TypeError, ValueError, KeyError):
                continue
    return idx


def load_reports():
    """→ [(date, results)] 按日期升序，results 按 rank 升序。"""
    out = []
    for d in SAMPLE_DATES:
        p = os.path.join(REPORT_DIR, f"eld_report_{d}.json")
        if not os.path.exists(p):
            print(f"[warn] 榜单缺失: {p}")
            continue
        with open(p, encoding="utf-8") as f:
            txt = f.read()
        try:
            j = json.loads(txt)
        except json.JSONDecodeError:
            j, _ = json.JSONDecoder().raw_decode(txt)
            print(f"[warn] {d} 榜单尾部有多余数据，取首个完整 JSON")
        rows = [r for r in j.get("results", []) if r.get("rank") is not None]
        rows.sort(key=lambda r: r["rank"])
        out.append((d, rows))
    return out


def cost_ratio(slippage=SLIPPAGE):
    return COMMISSION * 2 + STAMP_TAX + slippage * 2


def simulate(bars, tds, i_t, n, stop=None):
    """T=tds[i_t] 收盘出信号。返回 (net_ret, exit_tag) 或 (None, reason)。
    net_ret 为含摩擦净收益（相对 entry 的净额），未成交返回 (None, 'limit_up_lock')。
    """
    if i_t + 1 >= len(tds):
        return None, "no_forward"
    fwd = tds[i_t + 1: i_t + 1 + n]
    if len(fwd) < n:
        return None, "no_forward"
    d0 = bars.get(fwd[0])
    if d0 is None:
        return None, "no_data"
    if d0["high"] == d0["low"]:
        return None, "limit_up_lock"      # 一字板无法成交
    entry = d0["open"]
    if entry <= 0:
        return None, "no_data"
    gross = d0["pre_close"] / entry        # T+1 开盘段
    exit_tag = "time"
    for k_i, k in enumerate(fwd):
        bk = bars.get(k)
        if bk is None:
            return None, "no_data"
        if stop is not None and bk["close"] <= stop and k_i >= 0:
            sell = min(bk["open"], stop)
            if k_i == 0:
                gross = bk["pre_close"] / entry * (sell / bk["pre_close"])
            else:
                gross *= (sell / bk["pre_close"])
            exit_tag = f"stop_d{k_i + 1}"
            return _net(gross), exit_tag
        if k_i == 0:
            gross *= bk["close"] / bk["pre_close"]
        else:
            gross *= bk["close"] / bk["pre_close"]
    return _net(gross), exit_tag


def _net(gross):
    return gross * (1.0 - cost_ratio()) - 1.0


def simulate_gross(bars, tds, i_t, n):
    """不含摩擦毛收益（仅用于滑点敏感性对照）。None=未成交/数据缺。"""
    r, tag = _raw_gross(bars, tds, i_t, n)
    return r


def _raw_gross(bars, tds, i_t, n):
    if i_t + 1 >= len(tds):
        return None
    fwd = tds[i_t + 1: i_t + 1 + n]
    if len(fwd) < n:
        return None
    d0 = bars.get(fwd[0])
    if d0 is None or d0["high"] == d0["low"] or d0["open"] <= 0:
        return None
    gross = d0["pre_close"] / d0["open"]
    for k in fwd:
        bk = bars.get(k)
        if bk is None:
            return None
        gross *= bk["close"] / bk["pre_close"]
    return gross


def stats_block(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "win": sum(1 for v in vals if v > 0) / len(vals) * 100,
        "mean": mean(vals) * 100,
        "median": median(vals) * 100,
    }


def fmt(st):
    if st is None:
        return "  n=0"
    return (f"n={st['n']:<4} 胜率{st['win']:5.1f}% 均值{st['mean']:+6.2f}% "
            f"中位{st['median']:+6.2f}%")


def rank_ic(pairs):
    """Spearman RankIC（手算，避免 scipy 依赖）。pairs=[(score, ret)]"""
    pairs = [(s, r) for s, r in pairs if s is not None and r is not None]
    n = len(pairs)
    if n < 10:
        return None

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for t in range(i, j + 1):
                rk[order[t]] = avg
            i = j + 1
        return rk

    rs = rank([p[0] for p in pairs])
    rr = rank([p[1] for p in pairs])
    ms, mr = mean(rs), mean(rr)
    cov = sum((a - ms) * (b - mr) for a, b in zip(rs, rr))
    vs = (sum((a - ms) ** 2 for a in rs)) ** 0.5
    vr = (sum((b - mr) ** 2 for b in rr)) ** 0.5
    if vs == 0 or vr == 0:
        return None
    return cov / (vs * vr)


def main():
    bars_by_day = load_daily()
    idx = load_index()
    tds = sorted(bars_by_day.keys())
    print(f"日线交易日: {len(tds)} 天 ({tds[0]} ~ {tds[-1]})")
    reports = load_reports()
    print(f"榜单样本: {len(reports)} 期 ({reports[0][0]} ~ {reports[-1][0]})")

    sbar_cache = {}

    def sbar(ts):
        """单票按日序列 {trade_date: bar}（带缓存）"""
        if ts not in sbar_cache:
            sbar_cache[ts] = {d: m[ts] for d, m in bars_by_day.items() if ts in m}
        return sbar_cache[ts]

    tag_count = {}
    print(f"摩擦: 佣金{COMMISSION * 200:.3f}% + 印花{STAMP_TAX * 100:.2f}% + 滑点{SLIPPAGE * 200:.2f}% = "
          f"{cost_ratio() * 100:.3f}% (双边合计)\n")

    detail = []       # 逐笔明细
    ics = []          # RankIC per 期
    top_by_pool = {}  # (top, n) -> dict(top=…, pool=…, idx=…)

    for date, rows in reports:
        if date not in bars_by_day:
            continue
        i_t = tds.index(date)
        all_rows = rows
        # ── 每只票的 T+5 毛收益（用于 IC 与基准），T+N 各窗口净收益 ──
        rets = {}
        for r in all_rows:
            ts = r.get("ts_code")
            if not ts:
                continue
            for n in WINDOWS:
                stop = r.get("stop_loss_price")
                net, tag = simulate(sbar(ts), tds, i_t, n, stop=None)
                rets.setdefault(n, {})[ts] = (net, tag)
                tag_count[tag] = tag_count.get(tag, 0) + 1
        # 池等权基准（同口径）
        pool_rets = {n: [v[0] for v in rets.get(n, {}).values() if v[0] is not None]
                     for n in WINDOWS}
        # 指数基准（open[T+1] 买入, close[T+N] 卖出）
        idx_rets = {}
        for n in WINDOWS:
            if i_t + n < len(tds):
                d_in, d_out = idx.get(tds[i_t + 1]), idx.get(tds[i_t + n])
                if d_in and d_out:
                    idx_rets[n] = d_out["close"] / d_in["open"] - 1.0
        # RankIC（全池, N=5, 毛收益口径）
        g5 = {r.get("ts_code"): _raw_gross(sbar(r.get("ts_code")), tds, i_t, 5)
              for r in all_rows if r.get("ts_code")}
        ic = rank_ic([(r.get("final_score_v2"), g5.get(r.get("ts_code"))) for r in all_rows])
        if ic is not None:
            ics.append((date, ic))

        # TOP 分层
        for top in TOPS:
            for n in WINDOWS:
                sel = [rets.get(n, {}).get(r.get("ts_code"), (None, ""))[0] for r in rows[:top]]
                st = stats_block(sel)
                ps = stats_block(pool_rets[n])
                ir = idx_rets.get(n)
                agg = top_by_pool.setdefault((top, n), {"top": [], "pool": [], "idx": []})
                if st:
                    agg["top"] += [v for v in sel if v is not None]
                agg["pool"] += [v for v in pool_rets[n]]
                if ir is not None:
                    agg["idx"].append(ir)

        # 逐笔明细（TOP50, N=3/5）
        for r in rows[:50]:
            ts = r.get("ts_code")
            for n in (3, 5):
                net, tag = rets.get(n, {}).get(ts, (None, ""))
                if net is None:
                    continue
                pr = pool_rets[n]
                pool_m = mean(pr) if pr else 0.0
                detail.append({
                    "date": date, "rank": r.get("rank"), "ts_code": ts,
                    "name": r.get("name", ""), "v2": r.get("final_score_v2"),
                    "buy_point": r.get("buy_point", ""),
                    "buyable": r.get("next_day_buyable", ""),
                    "inst_state": r.get("institution_state", ""),
                    "n": n, "ret_net_pct": round(net * 100, 3),
                    "pool_mean_pct": round(pool_m * 100, 3),
                    "excess_pct": round((net - pool_m) * 100, 3),
                    "exit": tag,
                })

    print("模拟标签分布:", {k: v for k, v in sorted(tag_count.items(), key=lambda x: -x[1])})
    print()
    # ── 1. 主表：分层 × 窗口 ──
    print("=" * 72)
    print("H1 主表  分层(TOP N) × 持有窗口(T+N)  净收益 vs 池等权 vs 上证指数")
    print("=" * 72)
    print(f"{'分层':<8}{'窗口':<6}{'榜单组合':<44}{'池等权':<38}{'超额':>8}")
    for top in TOPS:
        for n in WINDOWS:
            agg = top_by_pool.get((top, n))
            if not agg:
                continue
            st = stats_block(agg["top"])
            ps = stats_block(agg["pool"])
            ex = (st["mean"] - ps["mean"]) if (st and ps) else None
            print(f"TOP{top:<5} T+{n:<4} {fmt(st):<44}{fmt(ps):<38}"
                  f"{(f'{ex:+.2f}%' if ex is not None else '-'):>8}")
        print()

    # 指数对照
    print("上证指数同窗口（开盘买/收盘卖）:")
    for n in WINDOWS:
        agg = top_by_pool.get((10, n))
        if agg and agg["idx"]:
            print(f"  T+{n}: 均值{mean(agg['idx']) * 100:+.2f}%")

    # ── 2. H2: next_day_buyable 分组（TOP50, T+3） ──
    print()
    print("=" * 72)
    print("H2  next_day_buyable 分组 (TOP50, T+3, 净)")
    print("=" * 72)
    grp = {True: [], False: [], None: []}
    for d in detail:
        if d["n"] != 3:
            continue
        b = d["buyable"]
        key = True if b in (True, "True", 1, "1") else (False if b in (False, "False", 0, "0") else None)
        grp[key].append(d["ret_net_pct"] / 100)
    for k in (True, False, None):
        lab = {True: "buyable=True ", False: "buyable=False", None: "buyable=其他"}[k]
        print(f"  {lab}: {fmt(stats_block(grp[k]))}")

    # ── 3. 买点类型分组（TOP50, T+3） ──
    print()
    print("=" * 72)
    print("买点类型分组 (TOP50, T+3, 净)")
    print("=" * 72)
    bybp = {}
    for d in detail:
        if d["n"] != 3:
            continue
        bybp.setdefault(d["buy_point"] or "NONE", []).append(d["ret_net_pct"] / 100)
    for k in sorted(bybp, key=lambda k: -len(bybp[k])):
        print(f"  {k:<16}: {fmt(stats_block(bybp[k]))}")

    # ── 4. H3: RankIC ──
    print()
    print("=" * 72)
    print(f"H3  RankIC (final_score_v2 vs T+5 毛收益, 全池, {len(ics)} 期)")
    print("=" * 72)
    if ics:
        vals = [v for _, v in ics]
        print(f"  IC均值 {mean(vals):+.4f}  IC中位 {median(vals):+.4f}  "
              f"IC>0占比 {sum(1 for v in vals if v > 0) / len(vals) * 100:.0f}%  "
              f"ICIR {mean(vals) / (sum((v - mean(vals)) ** 2 for v in vals) / len(vals)) ** 0.5:+.2f}")
        print("  分期: " + " ".join(f"{d[4:]}:{v:+.2f}" for d, v in ics))

    # ── 5. 分旬稳健性（TOP10, T+3 超额） ──
    print()
    print("=" * 72)
    print("分旬稳健性  TOP10 T+3 超额(vs 池等权)")
    print("=" * 72)
    buckets = {"0803-0812": [], "0814-0821": [], "0824-0902": []}
    for d in detail:
        if d["n"] != 3 or d["rank"] > 10:
            continue
        dd = d["date"][4:]
        if "0803" <= dd <= "0812":
            buckets["0803-0812"].append(d["excess_pct"])
        elif "0814" <= dd <= "0821":
            buckets["0814-0821"].append(d["excess_pct"])
        elif "0824" <= dd <= "0902":
            buckets["0824-0902"].append(d["excess_pct"])
    for k, v in buckets.items():
        st = stats_block([x / 100 for x in v])
        print(f"  {k}: {fmt(st)}")

    # ── 6. 滑点敏感性（TOP10/TOP20, T+3/T+5） ──
    print()
    print("=" * 72)
    print("滑点敏感性  TOP10/TOP20  净收益均值（同一批毛收益，仅改摩擦）")
    print("=" * 72)
    c0 = cost_ratio()
    for top in (10, 20):
        for n in (3, 5):
            gs = [(d["ret_net_pct"] / 100 + 1) / (1 - c0)
                  for d in detail if d["n"] == n and d["rank"] <= top]
            if not gs:
                continue
            parts = []
            for s in (0.001, 0.002, 0.003):
                cs = COMMISSION * 2 + STAMP_TAX + s * 2
                parts.append(f"滑点{s * 100:.1f}%→{mean([g * (1 - cs) - 1 for g in gs]) * 100:+.2f}%")
            print(f"  TOP{top:<4} T+{n}: " + "  ".join(parts))

    # ── 保存明细 ──
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["date", "rank", "ts_code", "name", "v2", "buy_point", "buyable",
                  "inst_state", "n", "ret_net_pct", "pool_mean_pct", "excess_pct", "exit"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(detail)
    print(f"\n明细已保存: {OUT_CSV} ({len(detail)} 笔)")


if __name__ == "__main__":
    main()
