# -*- coding: utf-8 -*-
"""
由 westock.db 的「环节节点」层生成 node_index.csv（节点索引，一行一节点）。

数据源: D:/mystock/data/westock_cache/westock.db（由 D:/mystock/_ws_chain_cache.py 抓取）
产物:   report_daily/node_index.csv

口径说明（见实测结论）:
  - node 是「挂在公司身上的产品标签」，与主题无关；同一节点在各主题下成分完全一致(100%)。
    故本表按节点全局聚合，主题仅作为「该节点被哪些主题复用」的参考列。
  - 稀有度: 越窄的节点越锐利。按个股数做分位反转(0~100)，个股数为 1 时为 100。
  - 通用节点: 个股数 >= 100 的节点本质是行业级伪节点(如 工业机械/传感器)，下游加权时须剔除或降权。
  - positions 列未采用: 48% 为空且同一节点在不同个股上自相矛盾(上游/下游/空混挂)。

用法:
  python -X utf8 gen_node_index.py
"""
import csv
import os
import sqlite3

DB = r"D:/mystock/data/westock_cache/westock.db"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "report_daily", "node_index.csv")

GENERIC_THRESHOLD = 100  # 个股数 >= 此值视为通用(行业级)节点


def to_ts(code: str) -> str:
    """sh600006 -> 600006.SH ; sz000001 -> 000001.SZ ; bj920110 -> 920110.BJ"""
    if not code:
        return ""
    p, n = code[:2].lower(), code[2:]
    return f"{n}.{p.upper()}" if p in ("sh", "sz", "bj") else code


def tier(n: int) -> str:
    if n <= 3:
        return "锐利"
    if n <= 29:
        return "可用"
    if n <= 99:
        return "偏泛"
    return "通用"


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    nodes: dict[str, dict] = {}
    for node, tn, code, name in cur.execute(
        """SELECT node, theme_name, stock_code, stock_name
           FROM chain_stock_node WHERE is_theme_node=0"""
    ):
        rec = nodes.setdefault(node, {"stocks": {}, "themes": []})
        if code and code not in rec["stocks"]:
            rec["stocks"][code] = name or ""
        if tn and tn not in rec["themes"]:
            rec["themes"].append(tn)

    # 稀有度: 按个股数分位反转，个股数相同者同分
    sizes = sorted({len(r["stocks"]) for r in nodes.values()})
    m = len(nodes)
    below = {}
    acc = 0
    for s in sizes:
        below[s] = 100.0 * (1 - acc / max(1, m - 1))
        acc += sum(1 for r in nodes.values() if len(r["stocks"]) == s)

    rows = []
    for node, r in nodes.items():
        n = len(r["stocks"])
        items = "|".join(f"{to_ts(c)} {nm}" for c, nm in
                         sorted(r["stocks"].items(), key=lambda x: to_ts(x[0])))
        rows.append([node, n, len(r["themes"]), round(below[n], 1), tier(n),
                     1 if n >= GENERIC_THRESHOLD else 0,
                     "|".join(sorted(r["themes"])), items])
    rows.sort(key=lambda x: (-x[3], x[0]))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["节点", "个股数", "跨主题数", "稀有度", "层级", "是否通用节点",
                    "主题列表", "个股列表"])
        w.writerows(rows)

    # ---- 统计 ----
    from collections import Counter
    cnt = Counter(x[4] for x in rows)
    print(f"输出: {OUT}")
    print(f"节点总数 {len(rows)}   关系数 {sum(x[1] for x in rows)}")
    print("分档: " + "  ".join(f"{k} {cnt[k]}" for k in ("锐利", "可用", "偏泛", "通用")))
    print(f"通用节点门槛 >= {GENERIC_THRESHOLD} 只，共 {sum(x[5] for x in rows)} 个")
    print(f"最长「个股列表」{max(len(x[7]) for x in rows)} 字符 / "
          f"最长「主题列表」{max(len(x[6]) for x in rows)} 字符")
    print("\n-- 最锐利的 15 个节点 --")
    for x in rows[:15]:
        print(f"   {x[0]:<14} {x[1]}只  跨{x[2]}主题  稀有度{x[3]}  [{x[6]}]")
    print("\n-- 通用节点（应降权） --")
    for x in rows:
        if x[5]:
            print(f"   {x[0]:<14} {x[1]}只  跨{x[2]}主题")
    con.close()


if __name__ == "__main__":
    main()
