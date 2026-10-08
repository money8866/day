"""跳空缩量 + 大盘弱势：最小可跑回测

策略口径（全部在收盘口径下判定，无未来函数）
  信号    : 当日最低价 > 前一交易日最高价（真实向上跳空，全天不回补）
  缺口幅度: 0.8% ~ 6%（上限分档回测后由 9% 收紧，6~9% 档表现不稳）
            gap% = (low_t*adj_t - high_{t-1}*adj_{t-1}) / (close_{t-1}*adj_{t-1}) * 100
  条件①   : 量比 < 1.5     量比 = vol_t / mean(vol_{t-5} … vol_{t-1})
  条件②   : 中证1000 20 日涨幅 < -5%   = close_t / close_{t-20} - 1（分档回测后由 < 0 收紧）
  universe: 总市值 > 80 亿、非北交所、上市满约 1 年
  入场    : T+1 开盘（信号当日收盘后才能确认；T+1 涨停一字开盘视为不可成交）
  评估    : T+1 开盘 → T+1/3/5/10 收盘；超额对照 = 同一交易日全市场等权同期收益

位置维度（--variant，已定为策略默认 = low20）
  plain     不叠位置（仅信号 + ① + ② + universe + 可成交）
  low20     位置维度①：缺口前 20 日累计涨幅 < 10%   ← 默认落地
  below60h  位置维度②：缺口前收盘距 60 日最高价 ≤ -5%（备选对照）
  all       三者并排对比

用法
  python gap_shrink_backtest.py --probe                  # 只看数据覆盖
  python gap_shrink_backtest.py                          # 默认区间 + all
  python gap_shrink_backtest.py --start 20240101 --end 20260930 --variant plain
  python gap_shrink_backtest.py --dump gap_signals.csv   # 导出命中明细
"""
import argparse
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
BASIC = os.path.join(CACHE, "stock_basic.csv")

IDX_CODE = "000852.SH"          # 中证1000（条件②）
GAP_MIN, GAP_MAX = 0.8, 6.0     # 缺口幅度档位（上限分档回测后由 9% 收到 6%）
VOLR_MAX = 1.5                  # 量比上限
IDX_MAX = -5.0                  # 条件②：中证1000 20 日涨幅上限（< -5%，原为 < 0）
POS_RET20_MAX = 10.0            # 位置维度（已定为默认硬条件）：缺口前 20 日累计涨幅 < 10%
DIST60H_MAX = -5.0              # 备选位置维度：缺口前收盘距 60 日最高价 ≤ -5%
MIN_TOTAL_MV = 800_000.0        # 万元 = 80 亿
MIN_BARS = 60                   # 组内最少历史 bar 数（近似「上市够久」）
FWD = (1, 3, 5, 10)
VARIANTS = ("plain", "low20", "below60h")

# 条件②分档（含上涨档）：按中证1000 20 日涨幅切
IDX_EDGES = [-1e9, -8.0, -5.0, -2.0, 0.0, 2.0, 5.0, 1e9]
IDX_LABELS = ["<-8%", "-8~-5%", "-5~-2%", "-2~0%", "0~+2%", "+2~+5%", ">+5%"]

# 缺口幅度分档（展示区间固定到 9%，与策略上限解耦，便于对照）
GAP_EDGES = [GAP_MIN, 3.0, 6.0, 9.0 + 1e-6]
GAP_LABELS = ["0.8~3%", "3~6%", "6~9%"]


# ────────────────────────────── 数据读取 ──────────────────────────────

def _read(conn, sql):
    return pd.read_sql_query(sql, conn)


def load_daily(conn, since):
    """顺序全表扫描 + pandas 排序（该库 22GB，ORDER BY 会走索引随机读致阻塞）"""
    df = _read(conn, "SELECT ts_code, trade_date, open, high, low, close, vol "
                     f"FROM daily_cache WHERE trade_date >= '{since}'")
    df = df.dropna(subset=["open", "high", "low", "close", "vol"])
    return df.sort_values(["ts_code", "trade_date"], kind="mergesort")


def load_adj(conn, since):
    return _read(conn, f"SELECT ts_code, trade_date, adj_factor FROM adj_factor_cache "
                       f"WHERE trade_date >= '{since}'")


def load_mv(conn, since):
    return _read(conn, f"SELECT ts_code, trade_date, total_mv FROM daily_basic_cache "
                       f"WHERE trade_date >= '{since}'")


def load_index(conn, since):
    df = _read(conn, f"SELECT trade_date, open, close FROM index_daily_cache "
                     f"WHERE ts_code = '{IDX_CODE}' AND trade_date >= '{since}' "
                     f"ORDER BY trade_date")
    return df


# ────────────────────────────── 特征计算 ──────────────────────────────

def build_features(df, idx):
    df = df.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)

    # 复权口径（缺口与收益都在复权价上算，免疫除权跳空）
    df["O"] = df["open"] * df["adj"]
    df["H"] = df["high"] * df["adj"]
    df["L"] = df["low"] * df["adj"]
    df["C"] = df["close"] * df["adj"]

    # 复权列建好后再 groupby，避免部分 pandas 版本取不到新列
    gb = df.groupby("ts_code", sort=False)
    H1, C1 = gb["H"].shift(1), gb["C"].shift(1)
    df["H1_raw"] = df.groupby("ts_code", sort=False)["high"].shift(1)  # 仅用于「缺口回补」描述

    # 信号：真缺口 + 档位
    df["gap"] = (df["L"] - H1) / C1 * 100.0
    # 条件①：量比
    v5 = df.groupby("ts_code", sort=False)["vol"].transform(
        lambda s: s.shift(1).rolling(5).mean())
    df["volr"] = df["vol"] / v5
    # 位置维度
    df["ret20"] = (C1 / gb["C"].shift(21) - 1.0) * 100.0
    hi60 = df.groupby("ts_code", sort=False)["H"].transform(
        lambda s: s.shift(1).rolling(MIN_BARS).max())
    df["dist60h"] = (C1 / hi60 - 1.0) * 100.0
    df["bar_no"] = df.groupby("ts_code", sort=False).cumcount()

    # 入场 T+1 开盘（复权），未来收益
    entry = df.groupby("ts_code", sort=False)["O"].shift(-1)
    entry_raw = df.groupby("ts_code", sort=False)["open"].shift(-1)
    lim = np.where(df["ts_code"].str.startswith(("300", "301", "688")), 0.195, 0.095)
    df["buyable"] = entry_raw < df["close"] * (1.0 + lim)
    for n in FWD:
        df[f"ret{n}"] = df.groupby("ts_code", sort=False)["C"].shift(-n) / entry - 1.0

    # 描述：T+1 收盘是否已回补缺口
    df["next_close"] = df.groupby("ts_code", sort=False)["C"].shift(-1)

    # 条件②：中证1000 20 日涨幅（按交易日 map）
    i = idx.sort_values("trade_date").reset_index(drop=True)
    i["ret20"] = (i["close"] / i["close"].shift(20) - 1.0) * 100.0
    i["entry"] = i["open"].shift(-1)
    for n in FWD:
        i[f"c{n}"] = i["close"].shift(-n)
    im = i.set_index("trade_date")
    df["idx_ret20"] = df["trade_date"].map(im["ret20"])
    return df, gb


def universe_mask(df, sb):
    """总市值 > 80 亿、非北交所、上市够久"""
    m = ~df["ts_code"].str.endswith(".BJ")
    m &= df["total_mv"].notna() & (df["total_mv"] > MIN_TOTAL_MV)
    m &= df["bar_no"] >= MIN_BARS
    if sb is not None:
        ld = sb.set_index("ts_code")["list_date"].astype(str)
        df["list_date"] = df["ts_code"].map(ld)
        cutoff = (pd.to_datetime(df["trade_date"]) - pd.Timedelta(days=365)).dt.strftime("%Y%m%d")
        m &= df["list_date"].isna() | (df["list_date"] <= cutoff)
    else:
        df["list_date"] = None
    return m


def index_baseline(df, uni):
    """同一交易日「全市场等权」同期收益（超额基准）"""
    sub = df[uni & df["ret10"].notna()]
    return sub.groupby("trade_date")[[f"ret{n}" for n in FWD]].mean()


# ────────────────────────────── 回测 ──────────────────────────────

def run_variant(df, base_mask, variant, baseline):
    m = base_mask.copy()
    if variant == "low20":
        m &= df["ret20"].notna() & (df["ret20"] < POS_RET20_MAX)
    elif variant == "below60h":
        m &= df["dist60h"].notna() & (df["dist60h"] <= DIST60H_MAX)
    s = df[m]
    row = {"variant": variant, "n": len(s)}
    if len(s) == 0:
        return row, s
    for n in FWD:
        r = s[f"ret{n}"]
        bl = s["trade_date"].map(baseline[f"ret{n}"])
        row[f"ret{n}"] = r.mean() * 100.0
        row[f"win{n}"] = (r > 0).mean() * 100.0
        row[f"exc{n}"] = (r - bl).mean() * 100.0
    row["fill"] = (s["next_close"] < s["H1_raw"]).mean() * 100.0  # T+1 收盘已回补缺口占比
    return row, s


def run_buckets(df, mask, col, edges, labels, baseline):
    """按 col 分档，输出各档收益/胜率/超额、样本数、档内均值、缺口回补率"""
    sub = df[mask & df[col].notna()].copy()
    sub["bucket"] = pd.cut(sub[col], bins=edges, labels=labels,
                           right=False, include_lowest=True)
    rows, frames = [], []
    for lab, g in sub.groupby("bucket", observed=True, sort=False):
        row = {"bucket": lab, "n": len(g), "x": g[col].mean()}
        for n in FWD:
            r = g[f"ret{n}"]
            bl = g["trade_date"].map(baseline[f"ret{n}"])
            row[f"ret{n}"] = r.mean() * 100.0
            row[f"win{n}"] = (r > 0).mean() * 100.0
            row[f"exc{n}"] = (r - bl).mean() * 100.0
        row["fill"] = (g["next_close"] < g["H1_raw"]).mean() * 100.0
        rows.append(row)
        frames.append(g.assign(bucket=str(lab)))
    out = pd.DataFrame(rows)
    order = {lab: i for i, lab in enumerate(labels)}
    out = out.sort_values("bucket", key=lambda s: s.map(order)).set_index("bucket")
    return out, (pd.concat(frames, ignore_index=True) if frames else sub.iloc[:0])


def show_buckets(out, xlabel):
    """打印分档表：绝对收益 / 胜率 / 超额 / 样本与档内均值"""
    print("\n绝对收益（%，T+1 开盘 → T+N 收盘）")
    print(out[[f"ret{n}" for n in FWD]].round(2).to_string())
    print("\n胜率（%）")
    print(out[[f"win{n}" for n in FWD]].round(1).to_string())
    print("\n超额 vs 全市场等权同期（%）")
    print(out[[f"exc{n}" for n in FWD]].round(2).to_string())
    print(f"\n样本数 / {xlabel}均值(%) / T+1 收盘已回补缺口(%)")
    print(out[["n", "x", "fill"]].round(1).to_string())


def run_idx_thresholds(df, sig_mask, baseline, ths=(0.0, -3.0, -5.0, -8.0)):
    """条件②阈值对照：idx 20日涨幅 < th 时的表现，用来确认收紧到什么位置"""
    rows = []
    for th in ths:
        s = df[sig_mask & df["idx_ret20"].notna() & (df["idx_ret20"] < th)]
        row = {"idx<": f"{th:.0f}%", "n": len(s)}
        if len(s) == 0:
            rows.append(row)
            continue
        for n in FWD:
            r = s[f"ret{n}"]
            bl = s["trade_date"].map(baseline[f"ret{n}"])
            row[f"ret{n}"] = r.mean() * 100.0
            row[f"win{n}"] = (r > 0).mean() * 100.0
            row[f"exc{n}"] = (r - bl).mean() * 100.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("idx<")


def run_scan(df, uni, sb, args, default_date):
    """盘后扫描：对指定交易日跑落地规则，输出次日候选清单（不依赖未来数据）"""
    sdate = args.date or default_date
    avail = df["trade_date"]
    if sdate not in set(avail.values):
        print(f"[警告] 缓存中没有 {sdate} 的数据，最新交易日为 {avail.max()}")
        sdate = avail.max()
        print(f"[改用] 信号日 {sdate}")

    # 逐级漏斗（当日口径，不含未来收益）
    day = df["trade_date"] == sdate
    g1 = uni & (df["gap"] >= GAP_MIN) & (df["gap"] <= args.gap_max)
    g2 = g1 & (df["volr"] < VOLR_MAX)
    g3 = g2 & (df["idx_ret20"] < args.idx_max)
    g4 = g3 & df["ret20"].notna() & (df["ret20"] < POS_RET20_MAX)

    idx20 = df.loc[day, "idx_ret20"].dropna()
    print(f"\n[盘后扫描] 信号日 {sdate}")
    print(f"  过滤：缺口 {GAP_MIN}~{args.gap_max}% + 量比<{VOLR_MAX} + 中证1000 20日涨幅<{args.idx_max:.0f}% "
          f"+ 缺口前20日涨幅<{POS_RET20_MAX:.0f}% + 市值>{MIN_TOTAL_MV / 10000:.0f}亿 + 非北交所")
    print(f"  中证1000 当日 20日涨幅：{idx20.iloc[0]:.2f}%" if len(idx20) else "  中证1000 20日涨幅：缺失")
    print(f"  漏斗：缺口 {int((g1 & day).sum())} → +量比 {int((g2 & day).sum())} "
          f"→ +条件② {int((g3 & day).sum())} → +位置low20 {int((g4 & day).sum())} 只")

    hits = df[day & g4].copy()
    if hits.empty:
        print("  当日无命中")
        return

    if sb is not None:
        hits = hits.merge(sb[["ts_code", "name", "industry"]], on="ts_code", how="left")
    hits["市值亿"] = (hits["total_mv"] / 10000.0).round(1)
    show = hits.sort_values("gap", ascending=False)[
        ["ts_code", "name", "industry", "close", "gap", "volr", "ret20", "dist60h", "idx_ret20", "市值亿"]
    ].rename(columns={"close": "收盘", "gap": "缺口%", "volr": "量比",
                      "ret20": "前20日%", "dist60h": "距60日高%", "idx_ret20": "指数20日%"})
    print(f"\n  次日候选（{len(show)} 只，按缺口降序）：")
    print(show.round(2).to_string(index=False))
    print("\n  口径：信号日收盘确认 → 次日开盘买入；次日一字涨停（双创19.5%/主板9.5%）视为不可成交。非投资建议。")
    if args.dump:
        show.to_csv(args.dump, index=False, encoding="utf-8-sig")
        print(f"  已导出 -> {args.dump}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="")
    ap.add_argument("--variant", default="low20", choices=("plain", "low20", "below60h", "all"),
                    help="位置维度：low20 已定为默认策略；all 可并排对比")
    ap.add_argument("--by-index", action="store_true", help="输出中证1000 20日涨幅分档表（含上涨档）")
    ap.add_argument("--by-gap", action="store_true", help="输出缺口幅度分档表（已落地策略池上切）")
    ap.add_argument("--idx-max", type=float, default=IDX_MAX, help=f"条件②阈值，单位百分点（默认 {IDX_MAX}）")
    ap.add_argument("--gap-max", type=float, default=GAP_MAX, help=f"缺口幅度上限，单位百分点（默认 {GAP_MAX}）")
    ap.add_argument("--scan", action="store_true", help="盘后扫描：输出指定交易日命中清单（不含未来收益）")
    ap.add_argument("--date", default="", help="扫描日 YYYYMMDD，默认取缓存最新交易日")
    ap.add_argument("--dump", default="")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)

    if args.probe:
        for t in ("daily_cache", "daily_basic_cache", "adj_factor_cache", "index_daily_cache"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                rng = conn.execute(f"SELECT MIN(trade_date),MAX(trade_date) FROM {t}").fetchone()
                print(f"{t:20s} rows={n:>10,}  {rng[0]} ~ {rng[1]}")
            except Exception as e:
                print(f"{t:20s} ERROR {e}")
        print("index 明细:")
        for r in conn.execute("SELECT ts_code,MIN(trade_date),MAX(trade_date),COUNT(*) "
                              "FROM index_daily_cache GROUP BY ts_code"):
            print("   ", r)
        return

    end = args.end or (conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()[0] or "20261231")
    warm = str((pd.to_datetime(args.start) - pd.Timedelta(days=200)).strftime("%Y%m%d"))
    mode = f"scan({args.date or end})" if args.scan else f"variant={args.variant}"
    print(f"区间 {args.start} ~ {end}（预热自 {warm}）| 基准 {IDX_CODE} | {mode}")

    df = load_daily(conn, warm)
    df = df.merge(load_adj(conn, warm), on=["ts_code", "trade_date"], how="left")
    df["adj"] = df["adj_factor"].groupby(df["ts_code"]).ffill().bfill().fillna(1.0)
    df = df.merge(load_mv(conn, warm), on=["ts_code", "trade_date"], how="left")
    idx = load_index(conn, warm)
    if idx.empty:
        raise SystemExit(f"[错误] index_daily_cache 缺 {IDX_CODE}，条件②无法计算")

    sb = pd.read_csv(BASIC) if os.path.exists(BASIC) else None
    df, _ = build_features(df, idx)

    uni = universe_mask(df, sb)

    if args.scan:
        run_scan(df, uni, sb, args, end)
        return

    baseline = index_baseline(df, uni)

    # 信号层：缺口 + ① + universe + 可成交 + 收益完整（不含条件②，留给分档）
    sig = (
        (df["gap"] >= GAP_MIN) & (df["gap"] <= args.gap_max)
        & (df["volr"] < VOLR_MAX)
        & uni & df["buyable"]
        & df["ret10"].notna()
        & (df["trade_date"] >= args.start) & (df["trade_date"] <= end)
    )
    # 基准策略：再叠条件②（中证1000 20 日涨幅 < idx_max，默认 -5%）
    base = sig & (df["idx_ret20"] < args.idx_max)

    landed = base & df["ret20"].notna() & (df["ret20"] < POS_RET20_MAX)
    print(f"[全样本] 原始缺口信号 {int(((df['gap'] >= GAP_MIN) & (df['gap'] <= args.gap_max)).sum()):,} 条 | "
          f"过 ①+universe+可成交 后 {int(sig.sum()):,} 条 | 叠条件②(<{args.idx_max:.0f}%) 后 {int(base.sum()):,} 条 | "
          f"再叠位置 low20(<{POS_RET20_MAX:.0f}%) 后 {int(landed.sum()):,} 条")

    cols = ["trade_date", "ts_code", "gap", "volr", "ret20", "dist60h", "idx_ret20",
            "ret1", "ret3", "ret5", "ret10"]

    if args.by_index or args.by_gap:
        allsig, cols_out = None, cols + ["bucket"]
        if args.by_index:
            out, allsig = run_buckets(df, sig, "idx_ret20", IDX_EDGES, IDX_LABELS, baseline)
            print("\n[指数分档] 中证1000 20日涨幅（信号层，未叠②，含上涨档）")
            show_buckets(out, "该档指数20日涨幅")
        if args.by_gap:
            out, allsig = run_buckets(df, landed, "gap", GAP_EDGES, GAP_LABELS, baseline)
            print(f"\n[缺口幅度分档] 已落地策略池（②<{args.idx_max:.0f}% + low20，共 {int(landed.sum())} 条）")
            show_buckets(out, "该档缺口幅度")
    else:
        variants = VARIANTS if args.variant == "all" else (args.variant,)
        rows, frames = [], []
        for v in variants:
            row, s = run_variant(df, base, v, baseline)
            rows.append(row)
            frames.append(s.assign(variant=v))
        out = pd.DataFrame(rows).set_index("variant")
        allsig = pd.concat(frames, ignore_index=True)
        cols_out = cols + ["variant"]
        print("\n绝对收益（%，T+1 开盘 → T+N 收盘）")
        print(out[[f"ret{n}" for n in FWD]].round(2).to_string())
        print("\n胜率（%）")
        print(out[[f"win{n}" for n in FWD]].round(1).to_string())
        print("\n超额 vs 全市场等权同期（%）")
        print(out[[f"exc{n}" for n in FWD]].round(2).to_string())
        print("\n样本数 / T+1 收盘已回补缺口占比（%）")
        print(out[["n", "fill"]].round(1).to_string())

    if args.dump:
        allsig.reindex(columns=cols_out).to_csv(
            args.dump, index=False, encoding="utf-8-sig")
        print(f"\n已导出命中明细 -> {args.dump}（{len(allsig)} 行）")

    # 条件②阈值对照（确认收紧位置）
    th = run_idx_thresholds(df, sig, baseline)
    print("\n[条件②阈值对照] 中证1000 20日涨幅 < 阈值 —— 收益（%）")
    print(th[[f"ret{n}" for n in FWD]].round(2).to_string())
    print("胜率（%）")
    print(th[[f"win{n}" for n in FWD]].round(1).to_string())
    print("超额 vs 全市场等权（%）")
    print(th[[f"exc{n}" for n in FWD]].round(2).to_string())
    print("样本数")
    print(th[["n"]].to_string())


if __name__ == "__main__":
    main()
