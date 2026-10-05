# -*- coding: utf-8 -*-
"""
由 westock.db 生成 theme_purity.csv（个股×主题 产业链纯度分）。

数据源: D:/mystock/data/westock_cache/westock.db（快照级，见 D:/mystock/_ws_chain_cache.py）
产物:   report_daily/theme_purity.csv
依赖产物: report_daily/node_index.csv（同源，独立重算稀有度以保持脚本自包含）

背景与口径（实测结论）：
  - node 是「挂在公司身上的产品标签」，与主题无关：同一节点在各主题下成分 100% 一致。
    因此「某票在某主题里扮演什么角色」无法直接读取，只能靠「主题成员的节点共现」反推。

指标定义：
  1. 核心环节 core(T) = 满足两个条件的节点 N
       a) 主题内被 >= MIN_THEME_SHARE 只成员共享   -> 是这个主题的共同环节，不是个别票的私货
       b) 全库个股数 < GENERIC_THRESHOLD            -> 剔除行业级伪节点(工业机械/传感器/电子元件)
  2. 归属度 aff(N,T) = 主题内覆盖 / 全库覆盖       -> 该节点有多"专属于"这个主题
       减速机 in 人形机器人 = 30/30 = 100%（专属）; 工业机械 = 50/310 = 16%（通用）
  3. 纯度 purity(s,T) = 100 * Σ aff(N,T) / |labels(s)|
       -> 该票的全部业务标签里，平均有多少比例落在「这个主题的核心专属环节」上

  权重为何不用稀有度相乘（20260927 实测修正）：
      初版用 w = 稀有度(N) * aff(N,T)，结果 万科A 在「冰雪经济」得 61.4 分、
      反而高于在「房地产」的 35.2 分。根因是「体育场馆运营」这类单票独占标签稀有度=100，
      乘性权重下会压倒主题真实核心环节（核心环节往往被多只成员共有，稀有度只是中等）。
      改为 aff 主导 + 按标签总数稀释：既避免独占标签刷分，也让多业务集团(如汇川技术 28 个
      标签)天然低于纯正标的，符合「主题纯度」语义。

判读：万科A 在「房地产」应高分、在「冰雪经济/人工智能」应低分 —— 同一只票、不同主题、
      不同纯度，这正是本表相比 node_index 的增量。

用法:
  python -X utf8 gen_theme_purity.py
"""
import csv
import os
import sqlite3
from collections import defaultdict

DB = r"D:/mystock/data/westock_cache/westock.db"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "report_daily", "theme_purity.csv")

MIN_THEME_SHARE = 3      # 节点在主题内至少被几只成员共享才算核心环节
GENERIC_THRESHOLD = 100  # 全库个股数 >= 此值视为通用(行业级)节点，不参与核心环节


def to_ts(code: str) -> str:
    if not code:
        return ""
    p, n = code[:2].lower(), code[2:]
    return f"{n}.{p.upper()}" if p in ("sh", "sz", "bj") else code


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    stock_labels: dict[str, set] = defaultdict(set)
    stock_name: dict[str, str] = {}
    for node, code, nm in cur.execute(
            "SELECT node, stock_code, stock_name FROM chain_stock_node WHERE is_theme_node=0"):
        stock_labels[to_ts(code)].add(node)
        stock_name[to_ts(code)] = nm or stock_name.get(to_ts(code), "")

    theme_members: dict[str, set] = defaultdict(set)
    for tn, code, nm in cur.execute(
            "SELECT theme_name, stock_code, stock_name FROM chain_stock_node WHERE is_theme_node=1"):
        ts = to_ts(code)
        theme_members[tn].add(ts)
        stock_name.setdefault(ts, nm or "")
    con.close()

    node_stocks: dict[str, set] = defaultdict(set)
    for c, ls in stock_labels.items():
        for n in ls:
            node_stocks[n].add(c)

    # 稀有度: 按个股数分位反转(0~100)，与 node_index.csv 同口径
    sizes = sorted({len(s) for s in node_stocks.values()})
    total_nodes = len(node_stocks)
    rare, acc = {}, 0
    for s in sizes:
        rare[s] = 100.0 * (1 - acc / max(1, total_nodes - 1))
        acc += sum(1 for v in node_stocks.values() if len(v) == s)
    rare_of = {n: rare[len(s)] for n, s in node_stocks.items()}

    rows = []
    theme_stat = []
    for theme, members in theme_members.items():
        # 核心环节
        core = {}
        for n, s in node_stocks.items():
            cov = len(s & members)
            if cov >= MIN_THEME_SHARE and len(s) < GENERIC_THRESHOLD:
                core[n] = (cov, cov / len(s))
        if not core:
            continue

        local = []
        for ts in members:
            labels = stock_labels.get(ts)
            if not labels:
                local.append([ts, stock_name.get(ts, ""), theme, len(members), 0, 0.0, 0.0, ""])
                continue
            hits = [(n, core[n][1]) for n in labels if n in core]
            p = 100.0 * sum(aff for _, aff in hits) / len(labels)
            hit_txt = "|".join(f"{n}({aff:.0%})" for n, aff in
                               sorted(hits, key=lambda x: (-x[1], -rare_of[x[0]])))
            local.append([ts, stock_name.get(ts, ""), theme, len(members),
                          len(hits), p, 0.0, hit_txt])

        # 主题内分位：免疫主题规模偏差（成员多的主题 aff 系统性虚高，见文件头说明）
        n = len(local)
        for i, j in enumerate(sorted(range(n), key=lambda x: local[x][5])):
            local[j][6] = round(100.0 * i / (n - 1), 1) if n > 1 else 100.0
        for r in local:
            r[5] = round(r[5], 1)
        rows.extend(local)

        sp = sorted(r[5] for r in local)
        theme_stat.append((theme, len(members), round(sp[len(sp) // 2], 1),
                           round(sum(sp) / len(sp), 1), len(core)))

    rows.sort(key=lambda x: (x[2], -x[5], x[0]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["股票代码", "股票名称", "主题", "主题成员数", "命中环节数",
                    "纯度分", "主题内分位", "命中环节"])
        w.writerows(rows)

    print(f"输出: {OUT}")
    print(f"个股×主题 组合数 {len(rows)}   主题数 {len(theme_stat)}")

    # ---- 主题质量: 中位纯度越低说明泛概念票越多 ----
    print("\n-- 泛概念最严重的 12 个主题（成员多但中位纯度低） --")
    for t, m, med, avg, nc in sorted(theme_stat, key=lambda x: x[2])[:12]:
        print(f"   {t:<12} 成员{m:>4}  中位纯度{med:>5}  均值{avg:>5}  核心环节{nc:>3}")
    print("\n-- 最「实」的 12 个主题（中位纯度高） --")
    for t, m, med, avg, nc in sorted(theme_stat, key=lambda x: -x[2])[:12]:
        print(f"   {t:<12} 成员{m:>4}  中位纯度{med:>5}  均值{avg:>5}  核心环节{nc:>3}")

    # ---- 已知正反例抽查 ----
    print("\n-- 抽查：同一只票在不同主题下的纯度（应显著分化） --")
    for name in ("万科A", "英集芯", "汇川技术", "宁德时代", "帝奥微"):
        sub = [r for r in rows if r[1] == name]
        if not sub:
            print(f"   [未找到] {name}")
            continue
        print(f"\n   {name}:")
        for r in sorted(sub, key=lambda x: -x[6]):
            print(f"      {r[2]:<12} 纯度{r[5]:>5}  主题内分位{r[6]:>5}  命中{r[4]:>2}环节   {r[7][:100]}")


if __name__ == "__main__":
    main()
