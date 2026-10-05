# -*- coding: utf-8 -*-
"""大节点景气度排序（节点级，不是个股级）。

把三块拼成一张表：
  1) 节点景气度 : report_daily/node_ige_<date>.csv 的 IGE_ADJ / MOM / PERSISTENCE
  2) 节点成分   : westock.db 的 chain_stock_node (is_theme_node=0)
  3) 成分股超预期: report_daily/q3_surprise_rank_2026.csv 的「强超预期概率」

只看大节点（node_ige 分层 = 偏泛 30~99 只 / 通用 >=100 只），按景气度 IGE_ADJ 降序。
超预期概率为等权均值（与 IGE 的横截面口径一致，不做市值加权）。

用法:
  python -X utf8 gen_node_rank.py            # 自动取最新 node_ige_*.csv
  python -X utf8 gen_node_rank.py 20260930   # 指定日期
  python -X utf8 gen_node_rank.py <file.csv> # 指定 node_ige 文件
产物:
  report_daily/node_prospect_rank_<date>.csv
"""
import glob
import os
import re
import sqlite3
import sys

import numpy as np
import pandas as pd

DB = r"D:/mystock/data/westock_cache/westock.db"
BASE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(BASE, "report_daily")
RANK_CSV = os.path.join(REPORT, "q3_surprise_rank_2026.csv")

HI_P = 0.50         # 「高概率」阈值


def to_ts(code: str) -> str:
    """sh600006 -> 600006.SH ; sz000001 -> 000001.SZ ; bj920110 -> 920110.BJ"""
    if not code:
        return ""
    p, n = code[:2].lower(), code[2:]
    return f"{n}.{p.upper()}" if p in ("sh", "sz", "bj") else code


def load_node_members() -> dict:
    con = sqlite3.connect(DB)
    rows = list(con.execute(
        "SELECT node, stock_code FROM chain_stock_node WHERE is_theme_node=0"))
    con.close()
    d: dict = {}
    for node, code in rows:
        d.setdefault(node, set()).add(to_ts(code))
    return d


def pick_node_ige(arg: str) -> str:
    if arg and arg.endswith(".csv"):
        return arg
    if arg:
        return os.path.join(REPORT, f"node_ige_{arg.replace('-', '')}.csv")
    fs = glob.glob(os.path.join(REPORT, "node_ige_*.csv"))
    if not fs:
        raise SystemExit("未找到 node_ige_*.csv")
    return max(fs, key=lambda p: re.search(r"(\d{8})", p).group(1))


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    ige_path = pick_node_ige(arg)
    date = re.search(r"(\d{8})", os.path.basename(ige_path)).group(1)

    ig = pd.read_csv(ige_path, encoding="utf-8-sig", dtype={"节点": str})
    mem = load_node_members()
    rk = pd.read_csv(RANK_CSV, encoding="utf-8-sig",
                     usecols=["代码", "强超预期概率"])
    rk["代码"] = rk["代码"].astype(str)
    pmap = dict(zip(rk["代码"], pd.to_numeric(rk["强超预期概率"],
                                              errors="coerce")))

    md = pd.DataFrame([(nd, ts) for nd, ss in mem.items() for ts in ss],
                      columns=["节点", "代码"]).drop_duplicates()
    md["p"] = md["代码"].map(pmap)
    md["列榜"] = md["p"].notna() & (md["p"] >= HI_P)
    comp = pd.Series({nd: len(ss) for nd, ss in mem.items()}, name="成分数")
    agg = md.groupby("节点").agg(榜单覆盖=("p", "count"),
                                 概率均值=("p", "mean"),
                                 概率中位=("p", "median"),
                                 高概率股数=("列榜", "sum"))

    out = ig.merge(agg, on="节点", how="left")
    out["成分数"] = out["节点"].map(comp)
    # 大节点 = node_ige 分层里的「偏泛(30~99) + 通用(>=100)」
    out = out[out["层级"].isin(["偏泛", "通用"])].copy()
    out["覆盖率%"] = (out["榜单覆盖"] / out["成分数"] * 100).round(1)
    out["高概率占比%"] = (out["高概率股数"] / out["榜单覆盖"] * 100).round(1)
    for c in ("概率均值", "概率中位"):
        out[c] = out[c].round(4)
    out = out.sort_values("IGE_ADJ", ascending=False).reset_index(drop=True)
    out["景气排名"] = np.arange(1, len(out) + 1)

    cols = ["景气排名", "节点", "层级", "成分数", "榜单覆盖", "覆盖率%",
            "IGE_ADJ", "IGE_MOM", "IGE_PERSISTENCE", "等级",
            "主导申万一级", "主导申万三级", "概率均值", "概率中位",
            "高概率股数", "高概率占比%"]
    out = out[cols].rename(columns={
        "IGE_ADJ": "景气度", "IGE_MOM": "动量", "IGE_PERSISTENCE": "持续性"})

    path = os.path.join(REPORT, f"node_prospect_rank_{date}.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"大节点(偏泛+通用) {len(out)}  层级 "
          f"{out['层级'].value_counts().to_dict()}")
    print(f"覆盖校验: 榜单覆盖 0 的节点 {int((out['榜单覆盖'] == 0).sum())}")
    print(f"输出: {path}")


if __name__ == "__main__":
    main()
