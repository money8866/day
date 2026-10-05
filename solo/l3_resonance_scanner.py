# -*- coding: utf-8 -*-
"""三级行业共振选股（L3 Resonance Scanner）
挑选当日「同一申万三级行业出现 ≥N 家收盘涨停」的行业，并列出行业内涨停股明细。

判定口径（数值唯一来源见下方常量区）
  宇宙：申万三级行业（SW2021）成分股，剔除 ST / 北交所（复用 ige.data.build_universe）
  涨停：收盘价 == 按板块涨跌幅上限四舍五入得到的涨停价
        主板 10%、创业板(300/301)/科创板(688/689) 20%
        等式判定天然排除上市首日/双创前 5 日等无涨跌幅限制的新股
  共振：同一 l3_code 当日收盘涨停家数 >= MIN_ZT（默认 2）

输出（report_daily/）
  l3_resonance_{date}.md                    行业榜 + 行业内涨停股明细
  l3_resonance_{date}.json                  机器可读（行业榜 + 涨停股 + 参数 + 全三级行业均涨幅）
  l3_resonance_{date}_industries.csv        行业榜
  l3_resonance_{date}_stocks.csv            涨停股明细（含所属三级行业）

数据源：cache_daily/stock_data.db 的 daily_cache（日更，run_all 内 tushare_quant 写入）
         + sli/cache 申万 L3 分类与成分快照（低频）。零 API 调用。

用法：
    python -X utf8 l3_resonance_scanner.py                     # 最新交易日
    python -X utf8 l3_resonance_scanner.py --date 20260930
    python -X utf8 l3_resonance_scanner.py --min-zt 3          # 门槛提到 3 家
    python -X utf8 l3_resonance_scanner.py --list-all          # 行业榜含未达标行业
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
OUTPUT_DIR = os.path.join(BASE, "report_daily")

# ── 参数（唯一口径来源，改这里即可）──────────────────────
MIN_ZT = 2                 # 共振门槛：同一三级行业当日收盘涨停家数下限
STREAK_WINDOW = 12         # 连板回溯交易日数（含当日）
LAST_ZT_LOOKBACK = 250     # 「上次涨停日期」回溯交易日数（不含当日）
MV_WAN_TO_YI = 1e4         # daily_basic_cache.total_mv 单位为万元，转亿元
LIMIT_MAIN = 0.10          # 主板涨跌幅上限
LIMIT_GEM = 0.20           # 创业板/科创板涨跌幅上限
ZT_TOL = 1e-4              # 涨停价比较容差（元）
DROP_NAME_KEYS = ("退",)   # 名称含「退」= 退市整理，剔除
TOP_DETAIL_LIMIT = 0       # 明细展示行业数上限，0 = 全部

SEP_H = "═" * 44
SEP_S = "─" * 44


# ─────────────────────────────────────────────
# 数据读取
# ─────────────────────────────────────────────
def _limit_ratio(ts_code: str) -> float:
    """涨跌幅上限：双创 20%，主板 10%（北交所/ST 已在宇宙层剔除）。"""
    if ts_code.startswith("688") or ts_code.startswith("689"):
        return LIMIT_GEM
    if ts_code.startswith("300") or ts_code.startswith("301"):
        return LIMIT_GEM
    return LIMIT_MAIN


def _latest_trade_date(conn) -> str:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()
    return str(row[0]) if row and row[0] else ""


def _recent_dates(conn, end_date: str, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
        "ORDER BY trade_date DESC LIMIT ?", (end_date, int(n))).fetchall()
    return sorted(str(r[0]) for r in rows)


def _load_quotes(conn, dates: list[str], codes: list[str] | None = None) -> pd.DataFrame:
    if not dates:
        return pd.DataFrame()
    ph = ",".join("?" * len(dates))
    sql = ("SELECT ts_code, trade_date, close, pre_close, pct_chg, amount "
           f"FROM daily_cache WHERE trade_date IN ({ph})")
    params = list(dates)
    if codes:
        ph2 = ",".join("?" * len(codes))
        sql += f" AND ts_code IN ({ph2})"
        params += list(codes)
    df = pd.read_sql_query(sql, conn, params=tuple(params))
    if df.empty:
        return df
    df["ts_code"] = df["ts_code"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str)
    for c in ("close", "pre_close", "pct_chg", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close", "pre_close"]).reset_index(drop=True)


def _load_universe(trade_date: str) -> pd.DataFrame:
    """申万三级成分宇宙（剔除 ST / 北交所），返回 ts_code/name/l3_code/l3_name/l2_name/l1_name。"""
    if BASE not in sys.path:
        sys.path.insert(0, BASE)
    from ige.data import build_universe  # 与 IGE 同源，保证行业口径一致
    uni = build_universe(trade_date)
    keep = ["ts_code", "name", "l3_code", "l3_name", "l2_name", "l1_name"]
    uni = uni[[c for c in keep if c in uni.columns]].copy()
    uni["ts_code"] = uni["ts_code"].astype(str)
    uni["name"] = uni["name"].fillna("").astype(str)
    uni = uni[~uni["name"].str.contains("|".join(DROP_NAME_KEYS), na=False)]
    return uni.drop_duplicates(subset=["ts_code"]).reset_index(drop=True)


# ─────────────────────────────────────────────
# 涨停与连板
# ─────────────────────────────────────────────
def _mark_zt(df: pd.DataFrame) -> pd.DataFrame:
    """标注当日是否收盘涨停：close == round(pre_close × (1+上限), 2)。"""
    out = df.copy()
    out = out[out["pre_close"] > 0].copy()
    ratio = out["ts_code"].map(_limit_ratio)
    out["limit_pct"] = (ratio * 100).round().astype(int)
    out["limit_price"] = (out["pre_close"] * (1.0 + ratio)).round(2)
    out["is_zt"] = (out["close"] - out["limit_price"]).abs() <= ZT_TOL
    return out


def _add_streak(df: pd.DataFrame) -> pd.DataFrame:
    """连板数：截至当日连续收盘涨停的家数（不足窗口左侧截断）。"""
    out = df.sort_values(["ts_code", "trade_date"], kind="mergesort").copy()
    out["_blk"] = (~out["is_zt"]).groupby(out["ts_code"]).cumsum()
    out["streak"] = out.groupby(["ts_code", "_blk"])["is_zt"].cumsum().astype(int)
    return out.drop(columns=["_blk"])


# ─────────────────────────────────────────────
# 聚合
# ─────────────────────────────────────────────
def _aggregate(cur: pd.DataFrame, uni: pd.DataFrame, min_zt: int, list_all: bool):
    """返回 (行业榜 DataFrame, 涨停股明细 DataFrame, 全部三级行业均涨幅 DataFrame)。"""
    m = cur.merge(uni, on="ts_code", how="inner")
    if m.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    zt = m[m["is_zt"]].copy()
    g = m.groupby(["l3_code", "l3_name", "l2_name"], as_index=False).agg(
        n_members=("ts_code", "count"),
        avg_pct=("pct_chg", "mean"))
    all_ind = g.sort_values("l3_code").reset_index(drop=True)
    z = (zt.groupby(["l3_code", "l3_name", "l2_name"], as_index=False)
         .agg(n_zt=("ts_code", "count"), max_streak=("streak", "max")))
    ind = g.merge(z, on=["l3_code", "l3_name", "l2_name"], how="left")
    ind["n_zt"] = ind["n_zt"].fillna(0).astype(int)
    ind["max_streak"] = ind["max_streak"].fillna(0).astype(int)
    ind["zt_ratio"] = ind["n_zt"] / ind["n_members"].replace(0, pd.NA)

    if not list_all:
        ind = ind[ind["n_zt"] >= int(min_zt)]
    # 排序：同日行业按「行业均涨幅」从大到小；n_zt / max_streak 仅作同分次的稳定次序
    ind = ind.sort_values(["avg_pct", "n_zt", "max_streak"],
                          ascending=[False, False, False]).reset_index(drop=True)
    ind.insert(0, "rank", range(1, len(ind) + 1))

    if not zt.empty:
        detail = zt[["l3_code", "l3_name", "l2_name", "ts_code", "name",
                     "close", "pct_chg", "streak", "amount"]].copy()
        detail = detail.merge(ind[["l3_code", "rank"]], on="l3_code", how="inner")
        detail = detail.sort_values(["rank", "streak", "pct_chg"],
                                    ascending=[True, False, False]).reset_index(drop=True)
        detail["amount_yi"] = (detail["amount"] / 1e5).round(3)
    else:
        detail = pd.DataFrame()
    return ind, detail, all_ind


# ─────────────────────────────────────────────
# 明细补充字段：上次涨停日期 / 总市值 / PE
# ─────────────────────────────────────────────
def _enrich(conn, trade_date: str, detail: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """给涨停股明细补 3 列：
      last_zt_date  上一个收盘涨停日（不含当日；lookback 内没有则为空串）
      total_mv_yi   总市值（亿元，daily_basic_cache.total_mv 万元 ÷ 1e4）
      pe            PE(TTM) 优先，缺失回退 PE
    """
    if detail.empty:
        return detail
    codes = detail["ts_code"].tolist()

    # 总市值 / PE（指定交易日的日频指标）
    ph = ",".join("?" * len(codes))
    b = pd.read_sql_query(
        "SELECT ts_code, total_mv, pe, pe_ttm FROM daily_basic_cache "
        f"WHERE trade_date = ? AND ts_code IN ({ph})",
        conn, params=(trade_date,) + tuple(codes))
    if b.empty:
        basics = pd.DataFrame(columns=["ts_code", "total_mv_yi", "pe_use"])
    else:
        b["ts_code"] = b["ts_code"].astype(str)
        for c in ("total_mv", "pe", "pe_ttm"):
            b[c] = pd.to_numeric(b[c], errors="coerce")
        b["total_mv_yi"] = (b["total_mv"] / MV_WAN_TO_YI).round(2)
        b["pe_use"] = b["pe_ttm"].where(b["pe_ttm"].notna(), b["pe"])
        basics = b[["ts_code", "total_mv_yi", "pe_use"]].drop_duplicates("ts_code")

    # 上次涨停日：回溯 lookback 个交易日，取 < trade_date 的最近一个收盘涨停
    dates = _recent_dates(conn, trade_date, lookback)
    hq = _load_quotes(conn, dates, codes)
    last: dict[str, str] = {}
    if not hq.empty:
        h = _mark_zt(hq)
        prev = h[(h["trade_date"] < trade_date) & h["is_zt"]]
        if not prev.empty:
            last = prev.groupby("ts_code")["trade_date"].max().to_dict()

    out = detail.merge(basics, on="ts_code", how="left")
    out["last_zt_date"] = out["ts_code"].map(lambda c: last.get(c, ""))
    return out


# ─────────────────────────────────────────────
# 输出
# ─────────────────────────────────────────────
def _fmt_pct(x) -> str:
    return "—" if pd.isna(x) else f"{x * 100:.1f}%"


def _fmt_zt_date(d) -> str:
    """20260929 -> 09-29；空/缺失 -> —。"""
    d = str(d or "").strip()
    return f"{d[4:6]}-{d[6:8]}" if len(d) == 8 else "—"


def _fmt_num(x, nd: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{float(x):.{nd}f}"


def _write_md(path: str, trade_date: str, ind: pd.DataFrame, detail: pd.DataFrame,
              min_zt: int, n_zt_total: int, list_all: bool) -> None:
    L: list[str] = []
    L.append(f"# 三级行业共振选股｜{trade_date}")
    L.append("")
    L.append(f"口径：申万三级行业（SW2021，剔除 ST / 北交所）；涨停 = 收盘价封住涨停价"
             f"（主板 {int(LIMIT_MAIN * 100)}%、创业板/科创板 {int(LIMIT_GEM * 100)}%）。")
    L.append(f"共振门槛：同一三级行业当日收盘涨停 ≥{min_zt} 家。"
             f"当日全市场涨停 {n_zt_total} 家。行业榜按行业均涨幅从大到小排序。")
    L.append("")
    L.append(SEP_H)
    scope = "全部三级行业" if list_all else f"达标行业（涨停 ≥{min_zt} 家）"
    n_hit = int((ind["n_zt"] >= min_zt).sum()) if len(ind) else 0
    L.append(f"一、共振行业榜｜{scope} {len(ind)} 个，达标 {n_hit} 个")
    L.append(SEP_H)
    L.append("")
    if ind.empty:
        L.append(f"当日无三级行业满足「涨停 ≥{min_zt} 家」。")
    else:
        L.append("| 排名 | 三级行业 | 二级行业 | 涨停数 | 成分数 | 涨停占比 | 最高连板 | 行业均涨幅 |")
        L.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |")
        for _, r in ind.iterrows():
            L.append(f"| {r['rank']} | {r['l3_name']} | {r['l2_name']} | {r['n_zt']} | "
                     f"{r['n_members']} | {_fmt_pct(r['zt_ratio'])} | {r['max_streak']} | "
                     f"{r['avg_pct']:+.2f}% |")
    L.append("")
    if len(detail):
        L.append(SEP_S)
        L.append("二、行业内涨停股明细")
        L.append(SEP_S)
        L.append("")
        n_show = len(ind) if TOP_DETAIL_LIMIT <= 0 else min(TOP_DETAIL_LIMIT, len(ind))
        for _, irow in ind.head(n_show).iterrows():
            sub = detail[detail["l3_code"] == irow["l3_code"]]
            if sub.empty:
                continue
            L.append(f"【{irow['rank']}】{irow['l3_name']}（涨停 {irow['n_zt']} 家，"
                     f"最高 {irow['max_streak']} 连板，行业均涨幅 {irow['avg_pct']:+.2f}%）")
            L.append("")
            L.append("| 代码 | 名称 | 收盘 | 涨幅 | 连板 | 成交额(亿) | 上次涨停 | 总市值(亿) | PE |")
            L.append("| --- | --- | ---: | ---: | ---: | ---: | :---: | ---: | ---: |")
            for _, s in sub.iterrows():
                L.append(f"| {s['ts_code']} | {s['name']} | {s['close']:.2f} | "
                         f"{s['pct_chg']:+.2f}% | {s['streak']} | {s['amount_yi']:.2f} | "
                         f"{_fmt_zt_date(s.get('last_zt_date'))} | "
                         f"{_fmt_num(s.get('total_mv_yi'))} | {_fmt_num(s.get('pe_use'))} |")
            L.append("")
    L.append(SEP_S)
    L.append("口径与数据源")
    L.append(SEP_S)
    L.append("")
    L.append(f"- 涨停判定：收盘价 == round(前收盘价 × (1+涨跌幅上限), 2)，误差 ≤{ZT_TOL} 元；"
             "等式判定天然排除上市首日 / 双创前 5 日等无涨跌幅限制的新股。")
    L.append(f"- 连板数：回溯最近 {STREAK_WINDOW} 个交易日（含当日）的连续收盘涨停天数，"
             "窗口左端截断可能低估。")
    L.append("- 行业均涨幅：行业内当日有行情成分股的等权平均涨幅。")
    L.append(f"- 上次涨停：回溯最近 {LAST_ZT_LOOKBACK} 个交易日内，上一个收盘涨停日"
             "（不含当日，供判断首板/断板节奏；窗口内无则显示 —）。")
    L.append(f"- 总市值 / PE：daily_basic_cache（{trade_date}）；总市值 = total_mv（万元）÷1e4（亿元）；"
             "PE 取 PE(TTM)，缺失时回退 PE，仍缺失显示 —。")
    L.append(f"- 数据源：stock_data.db daily_cache（{trade_date}）＋ sli/cache 申万 L3 分类与成分快照。")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def _write_json(path: str, trade_date: str, ind: pd.DataFrame, detail: pd.DataFrame,
                all_ind: pd.DataFrame, min_zt: int, n_zt_total: int, list_all: bool) -> None:
    payload = {
        "trade_date": trade_date,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "min_zt": int(min_zt),
            "streak_window": STREAK_WINDOW,
            "limit_main": LIMIT_MAIN,
            "limit_gem": LIMIT_GEM,
            "industry_standard": "SW2021_L3",
            "list_all": bool(list_all),
        },
        "summary": {
            "n_zt_total": int(n_zt_total),
            "n_industries": int(len(ind)),
            "n_industries_hit": int((ind["n_zt"] >= min_zt).sum()) if len(ind) else 0,
            "n_industry_zt_stocks": int(len(detail)),
        },
        "industries": [
            {
                "rank": int(r["rank"]),
                "l3_code": r["l3_code"],
                "l3_name": r["l3_name"],
                "l2_name": r["l2_name"],
                "n_zt": int(r["n_zt"]),
                "n_members": int(r["n_members"]),
                "zt_ratio": None if pd.isna(r["zt_ratio"]) else round(float(r["zt_ratio"]), 4),
                "max_streak": int(r["max_streak"]),
                "avg_pct": round(float(r["avg_pct"]), 3),
            }
            for _, r in ind.iterrows()
        ],
        "industry_avg": [
            {
                "l3_code": r["l3_code"],
                "l3_name": r["l3_name"],
                "avg_pct": None if pd.isna(r["avg_pct"]) else round(float(r["avg_pct"]), 3),
            }
            for _, r in all_ind.iterrows()
        ],
        "stocks": [
            {
                "ts_code": s["ts_code"],
                "name": s["name"],
                "l3_name": s["l3_name"],
                "l2_name": s["l2_name"],
                "close": float(s["close"]),
                "pct_chg": round(float(s["pct_chg"]), 3),
                "streak": int(s["streak"]),
                "amount_yi": float(s["amount_yi"]),
                "last_zt_date": str(s.get("last_zt_date") or ""),
                "total_mv_yi": None if pd.isna(s.get("total_mv_yi")) else round(float(s["total_mv_yi"]), 2),
                "pe": None if pd.isna(s.get("pe_use")) else round(float(s["pe_use"]), 2),
                "industry_rank": int(s["rank"]),
            }
            for _, s in detail.iterrows()
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_csv(ind_path: str, det_path: str, ind: pd.DataFrame, detail: pd.DataFrame) -> None:
    if len(ind):
        cols = ["rank", "l3_code", "l3_name", "l2_name", "n_zt", "n_members",
                "zt_ratio", "max_streak", "avg_pct"]
        ind[cols].to_csv(ind_path, index=False, encoding="utf-8-sig")
    if len(detail):
        cols = ["rank", "l3_code", "l3_name", "l2_name", "ts_code", "name",
                "close", "pct_chg", "streak", "amount_yi",
                "last_zt_date", "total_mv_yi", "pe_use"]
        detail[cols].to_csv(det_path, index=False, encoding="utf-8-sig")


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三级行业共振选股（当日 ≥N 家涨停的三级行业）")
    ap.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省取 daily_cache 最新")
    ap.add_argument("--min-zt", type=int, default=MIN_ZT, help=f"共振门槛，默认 {MIN_ZT}")
    ap.add_argument("--streak-window", type=int, default=STREAK_WINDOW,
                    help=f"连板回溯交易日数，默认 {STREAK_WINDOW}")
    ap.add_argument("--list-all", action="store_true", help="行业榜输出全部三级行业（不过滤门槛）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(DB_PATH):
        print(f"[ERROR] 缺少行情库: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        trade_date = args.date.strip() or _latest_trade_date(conn)
        if not trade_date:
            print("[ERROR] daily_cache 无数据", file=sys.stderr)
            return 1
        dates = _recent_dates(conn, trade_date, max(args.streak_window, 2))
        if not dates or dates[-1] != trade_date:
            print(f"[ERROR] {trade_date} 无行情数据（可用最近: {dates[-1] if dates else '无'}）",
                  file=sys.stderr)
            return 1
        quotes = _load_quotes(conn, dates)
    finally:
        conn.close()

    uni = _load_universe(trade_date)
    if uni.empty:
        print(f"[ERROR] {trade_date} 申万三级成分宇宙为空", file=sys.stderr)
        return 1

    q = _mark_zt(quotes)
    q = _add_streak(q)
    cur = q[q["trade_date"] == trade_date].copy()
    n_zt_total = int(cur["is_zt"].sum())

    ind, detail, all_ind = _aggregate(cur, uni, args.min_zt, args.list_all)

    # 明细补充字段（上次涨停 / 总市值 / PE）——需要重新连库读 daily_basic 与历史行情
    if len(detail):
        conn2 = sqlite3.connect(DB_PATH)
        try:
            detail = _enrich(conn2, trade_date, detail, LAST_ZT_LOOKBACK)
        finally:
            conn2.close()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    md = os.path.join(OUTPUT_DIR, f"l3_resonance_{trade_date}.md")
    js = os.path.join(OUTPUT_DIR, f"l3_resonance_{trade_date}.json")
    ci = os.path.join(OUTPUT_DIR, f"l3_resonance_{trade_date}_industries.csv")
    cs = os.path.join(OUTPUT_DIR, f"l3_resonance_{trade_date}_stocks.csv")
    _write_md(md, trade_date, ind, detail, args.min_zt, n_zt_total, args.list_all)
    _write_json(js, trade_date, ind, detail, all_ind, args.min_zt, n_zt_total, args.list_all)
    _write_csv(ci, cs, ind, detail)

    n_hit = int((ind["n_zt"] >= args.min_zt).sum()) if len(ind) else 0
    print(f"交易日 {trade_date}｜宇宙 {len(uni)} 只（申万三级，剔 ST/北交所）"
          f"｜全市场收盘涨停 {n_zt_total} 家")
    print(f"共振门槛 ≥{args.min_zt} 家：达标三级行业 {n_hit} 个，涉及涨停股 {len(detail)} 家")
    if len(ind) and n_hit:
        for _, r in ind.head(15).iterrows():
            if r["n_zt"] < args.min_zt:
                continue
            print(f"  {r['l3_name']:<10}（{r['l2_name']}）涨停 {r['n_zt']} 家 / 成分 {r['n_members']}"
                  f"｜占比 {_fmt_pct(r['zt_ratio'])}｜最高 {r['max_streak']} 连板"
                  f"｜均涨 {r['avg_pct']:+.2f}%")
    if args.verbose:
        print(f"[out] {md}")
        print(f"[out] {js}")
        print(f"[out] {ci}")
        print(f"[out] {cs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
