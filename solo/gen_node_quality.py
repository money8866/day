# -*- coding: utf-8 -*-
"""
由 westock.db 生成两张「节点质量」表，用于清洗 个股 × 环节节点 的对照误差。

数据源: D:/mystock/data/westock_cache/westock.db
产物:
  report_daily/node_blacklist.csv   通用节点剔除 / 中颗粒串味降权 + 算法检出的离群(误挂)成员
  report_daily/node_merge_map.csv   碎片节点 -> 归并族

背景（实测结论，与主题无关）:
  node 是挂在公司身上的全局产品标签。对照在「大颗粒」层面准确，但有 3 类系统性误差：
    ① 通用节点稀释 —— 39 个行业级伪节点(个股数>=100)，如 工业机械310 / AI模块263 / 西药259
    ② 中颗粒串味   —— 30~99 只的节点混入异质成员，如「操作系统软件」混入中信重工
    ③ 粒度碎片化   —— 同一域拆成多个节点，如 白酒5个 / 光伏24个 / 锂离子电池4个

两张表的用途:
  node_blacklist.csv  下游按「层级」把通用节点剔除、把降权节点打折后再算环节分
  node_merge_map.csv  下游把碎片节点先归并到「归并族」再统计覆盖率，避免同一件事被拆散

归并族的确定: 用 KEYWORDS 里的领域词对节点名做「最长匹配」，命中即归入该族，未命中则自成一族。
  刻意不含 服务/设备/系统/产品/材料/工程/软件/器件/制品/用品/管理/信息/生产/集成/应用/控制/零售
  这类「中心词」——它们是中心词不是领域，作族名会把不相干的链合在一起。

用法: python -X utf8 gen_node_quality.py
"""
import csv
import os
import sqlite3
from collections import defaultdict

DB = r"D:/mystock/data/westock_cache/westock.db"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")

GENERIC_THRESHOLD = 100   # 个股数 >= 100 -> 通用(行业级伪节点) 剔除
MEDIUM_LOW = 30           # 30 <= 个股数 < 100 -> 中颗粒，降权并检离群

# 领域族名候选：按「长度优先、同长按此序」匹配，越靠前优先级越高
KEYWORDS = [
    # 新能源 / 电力设备
    "新能源汽车", "锂离子电池", "钠离子电池", "燃料电池", "光伏", "风电", "氢能", "氢燃料",
    "储能", "锂电池", "充电桩", "特高压", "电网", "核电", "水电", "火电", "燃气", "电力",
    # 半导体 / 电子
    "半导体", "集成电路", "晶圆", "光芯片", "光模块", "光器件", "印制电路板", "连接器",
    "传感器", "显示", "消费电子", "芯片", "被动元件",
    # 智能装备
    "工业机器人", "机器人", "减速器", "伺服", "数控机床", "机床", "工程机械",
    # 汽车
    "汽车零部", "汽车", "轮胎", "两轮车",
    # 消费 / 农业
    "白酒", "啤酒", "黄酒", "乳制品", "调味品", "保健品", "烟草", "饮料", "食品",
    "家电", "家居", "纺织", "服装", "珠宝", "造纸", "包装",
    "养殖", "种业", "化肥", "农药", "宠物", "农业",
    # 医药
    "医疗器械", "医药", "中药", "疫苗", "原料药", "生物制品", "医院", "药店", "药品",
    # 金融 / 地产
    "银行", "保险", "证券", "信托", "期货", "基金", "金融", "地产", "物业",
    "建筑", "水泥", "玻璃", "装修",
    # 周期
    "钢铁", "煤炭", "有色", "黄金", "白银", "稀土", "铜", "铝", "金属",
    "石油", "天然气", "油气", "化工", "化纤", "塑料", "橡胶",
    "环保", "水务",
    # 军工 / 交运
    "航空", "航天", "国防", "军工", "船舶", "卫星",
    "港口", "航运", "机场", "铁路", "公路", "物流", "快递",
    # TMT
    "通信", "光通信", "网络设备", "软件", "云计算", "人工智能", "大数据",
    "网络安全", "互联网", "游戏", "传媒", "教育", "电商", "零售",
    # 服务
    "旅游", "酒店", "餐饮", "免税",
]


def to_ts(code: str) -> str:
    """sh600006 -> 600006.SH ; sz000001 -> 000001.SZ ; bj920110 -> 920110.BJ"""
    if not code:
        return ""
    pre, num = code[:2].lower(), code[2:]
    return f"{num}.{pre.upper()}" if pre in ("sh", "sz", "bj") else code


def tier(n: int) -> str:
    if n <= 3:
        return "锐利"
    if n <= 29:
        return "可用"
    if n <= 99:
        return "偏泛"
    return "通用"


def load():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    labels: dict[str, set] = defaultdict(set)   # 个股 -> {节点}
    name: dict[str, str] = {}
    node_themes: dict[str, set] = defaultdict(set)
    node_stocks: dict[str, set] = defaultdict(set)
    for node, code, nm, tname in cur.execute(
            """SELECT node, stock_code, stock_name, theme_name
               FROM chain_stock_node WHERE is_theme_node=0"""):
        ts = to_ts(code)
        labels[ts].add(node)
        name[ts] = nm or name.get(ts, "")
        node_stocks[node].add(ts)
        if tname:
            node_themes[node].add(tname)
    con.close()
    return labels, name, node_stocks, node_themes


def outliers_of(node: str, members: list, labels: dict) -> list:
    """离群成员：该票的「其它标签」在本节点其他成员身上一个都不出现 -> 疑似误挂。"""
    others = {t: (labels[t] - {node}) for t in members}
    res = []
    for t in members:
        o = others[t]
        if not o:
            continue                      # 只有这一个标签，无从判断
        for u in members:
            if u != t and (o & others[u]):
                break
        else:
            res.append(t)
    return res


def build_blacklist(labels, name, node_stocks, node_themes):
    rows = []
    for nd, S in node_stocks.items():
        c = len(S)
        if c < MEDIUM_LOW:
            continue
        n_theme = len(node_themes.get(nd, ()))
        if c >= GENERIC_THRESHOLD:
            rows.append([nd, c, n_theme, tier(c), "剔除",
                         "行业级伪节点(个股数>=100)，对个股无区分度", ""])
        else:
            out = outliers_of(nd, sorted(S), labels)
            rows.append([nd, c, n_theme, tier(c), "降权",
                         "中颗粒节点，易混入异质成员" +
                         ("（算法检出离群成员）" if out else ""),
                         "|".join(name.get(t, "") or t for t in out[:6])])
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def build_merge_map(node_stocks):
    kw = list(dict.fromkeys(KEYWORDS))
    order = sorted(kw, key=lambda k: (-len(k), kw.index(k)))
    fam_of, fam_nodes = {}, defaultdict(list)
    for nd in node_stocks:
        hit = next((k for k in order if k in nd), None)
        f = hit or nd
        fam_of[nd] = f
        fam_nodes[f].append(nd)
    rows = []
    for nd in sorted(node_stocks):
        f = fam_of[nd]
        sibs = fam_nodes[f]
        union = set()
        for x in sibs:
            union |= node_stocks[x]
        rows.append([nd, f, len(sibs), len(union), len(node_stocks[nd]),
                     "归并" if f != nd else "自成一族",
                     "|".join(sorted(sibs))])
    rows.sort(key=lambda r: (-r[2], r[1], r[0]))
    return rows


def main():
    labels, name, node_stocks, node_themes = load()
    os.makedirs(OUT_DIR, exist_ok=True)

    bl = build_blacklist(labels, name, node_stocks, node_themes)
    out1 = os.path.join(OUT_DIR, "node_blacklist.csv")
    with open(out1, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["节点", "个股数", "跨主题数", "层级", "建议动作", "理由", "离群成员示例"])
        w.writerows(bl)

    mm = build_merge_map(node_stocks)
    out2 = os.path.join(OUT_DIR, "node_merge_map.csv")
    with open(out2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["原节点", "归并族", "族内节点数", "族内个股数", "本节点个股数", "处理", "族内节点"])
        w.writerows(mm)

    print(f"输出: {out1}  ({len(bl)} 行)")
    print(f"输出: {out2}  ({len(mm)} 行)")

    n_del = sum(1 for r in bl if r[4] == "剔除")
    n_down = sum(1 for r in bl if r[4] == "降权")
    n_out = sum(1 for r in bl if r[6])
    print(f"\n-- 黑名单 --  剔除(通用节点) {n_del} 个 | 降权(中颗粒) {n_down} 个 |"
          f" 其中含算法检出离群成员的 {n_out} 个")
    print("降权节点中，检出离群(疑似误挂)成员的：")
    for r in bl:
        if r[4] == "降权" and r[6]:
            print(f"   {r[0]:<14} {r[1]:>3}只  {r[2]:>2}主题  离群: {r[6]}")

    fam = defaultdict(list)
    for r in mm:
        fam[r[1]].append(r[0])
    multi = {k: v for k, v in fam.items() if len(v) >= 2}
    print(f"\n-- 归并表 --  共 {len(multi)} 个族覆盖 {sum(len(v) for v in multi.values())} 个节点"
          f"（/ 节点总数 {len(node_stocks)}）；{len(node_stocks)-sum(len(v) for v in multi.values())} 个节点自成一族")
    print("最大的 15 个族：")
    for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"   {k:<8} {len(v):>2}节点: " + " | ".join(sorted(v)[:8]) + (" …" if len(v) > 8 else ""))


if __name__ == "__main__":
    main()
