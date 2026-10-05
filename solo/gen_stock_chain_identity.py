# -*- coding: utf-8 -*-
"""
由 westock.db 生成 stock_chain_identity.csv（个股产业链身份画像，一行一票）。

数据源: D:/mystock/data/westock_cache/westock.db（由 D:/mystock/_ws_chain_cache.py 抓取）
产物:   report_daily/stock_chain_identity.csv

用法:
  python -X utf8 gen_stock_chain_identity.py
"""
import csv
import os
import sqlite3

DB = r"D:/mystock/data/westock_cache/westock.db"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "report_daily", "stock_chain_identity.csv")


def to_ts(code: str) -> str:
    """sh600006 -> 600006.SH ; sz000001 -> 000001.SZ ; bj920110 -> 920110.BJ"""
    if not code:
        return ""
    p, n = code[:2].lower(), code[2:]
    return f"{n}.{p.upper()}" if p in ("sh", "sz", "bj") else code


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # ---- 产业链层：主题成员行(is_theme_node=1) + 环节节点行(is_theme_node=0) ----
    themes: dict[str, dict] = {}
    for code, name, tname, node, pos, is_tn in cur.execute(
        """SELECT stock_code, stock_name, theme_name, node, positions, is_theme_node
           FROM chain_stock_node"""
    ):
        ts = to_ts(code)
        rec = themes.setdefault(ts, {"name": "", "themes": [], "theme_pos": [], "nodes": []})
        if not rec["name"] and name:
            rec["name"] = name
        if is_tn == 1:
            if tname not in rec["themes"]:
                rec["themes"].append(tname)
                rec["theme_pos"].append(f"{tname}={pos or '-'}")
        else:
            if node and node not in rec["nodes"]:
                rec["nodes"].append(node)

    # ---- 概念层 ----
    concepts: dict[str, list] = {}
    for code, sname in cur.execute("SELECT stock_code, sector_name FROM concept_stocks"):
        ts = to_ts(code)
        lst = concepts.setdefault(ts, [])
        if sname and sname not in lst:
            lst.append(sname)

    rows = []
    for ts in sorted(set(themes) | set(concepts)):
        r = themes.get(ts, {})
        cs = concepts.get(ts, [])
        rows.append([
            ts, r.get("name", ""), ts[-2:] if ts else "",
            len(r.get("themes", [])), "|".join(r.get("themes", [])),
            "|".join(r.get("theme_pos", [])),
            len(r.get("nodes", [])), "|".join(r.get("nodes", [])),
            len(cs), "|".join(cs),
        ])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["股票代码", "股票名称", "市场", "主题数", "产业链主题", "主题位置",
                    "环节数", "环节节点", "概念数", "概念板块"])
        w.writerows(rows)

    # ---- 统计 ----
    n_th = sorted(r[3] for r in rows)
    n_nd = sorted(r[6] for r in rows)
    n_cp = sorted(r[8] for r in rows)

    def pct(a, p):
        return a[min(len(a) - 1, int(len(a) * p))]

    print(f"输出: {OUT}")
    print(f"总票数 {len(rows)}   北交所 {sum(1 for r in rows if r[2] == 'BJ')}   "
          f"名称含ST {sum(1 for r in rows if 'ST' in r[1].upper())}")
    print(f"产业链主题数 中位 {pct(n_th, .5)} / p90 {pct(n_th, .9)} / max {n_th[-1]}")
    print(f"环节节点数   中位 {pct(n_nd, .5)} / p90 {pct(n_nd, .9)} / max {n_nd[-1]}")
    print(f"概念板块数   中位 {pct(n_cp, .5)} / p90 {pct(n_cp, .9)} / max {n_cp[-1]}")
    print(f"仅有产业链主题无概念 {sum(1 for r in rows if r[3] and not r[8])}   "
          f"仅有概念无产业链主题 {sum(1 for r in rows if not r[3] and r[8])}")
    print(f"最长「环节节点」{max((len(r[7]) for r in rows), default=0)} 字符 / "
          f"最长「主题位置」{max((len(r[5]) for r in rows), default=0)} 字符")
    con.close()


if __name__ == "__main__":
    main()
