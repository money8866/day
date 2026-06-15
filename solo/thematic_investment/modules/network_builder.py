"""
文本-股票 二部网络构建 (network_builder.py)
============================================

从 MongoDB `news_metadata` 中抽取 (文档, 提及股票) 二部图:
  - 节点类型: 'doc' / 'stock'
  - 边: 文档提及股票 → 带权重 (TF / 重要度信号)
  - 股-股共现边: 同一文档同时提及两只股票则在它们之间加一条共现边
  - 输出: networkx Graph + 单只股票的中介中心度(betweenness centrality)

同时支持:
  - 加载产业链图谱 CSV(upstream/downstream), 将行业/公司关系合并进图
  - 输出每个主题下的"子图中心度排名", 供 stock_picker.py 使用

依赖: pip install networkx pandas pymongo
"""

from __future__ import annotations

import os
import re
import sys
import time
import logging
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# 路径
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR: str = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import pandas as pd
from modules.db_connector import MongoConnector
from modules.utils import setup_logger, today_str

logger: logging.Logger = setup_logger(
    name="network_builder",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="network_builder.log",
)

# A股代码 (6位数字)
STOCK_CODE_RE: re.Pattern = re.compile(r"\b(\d{6})\b")


# ============================================================================ #
# 数据结构
# ============================================================================ #
@dataclass
class DocStockEntry:
    """文档 → 提及的股票及权重"""
    doc_id: str
    title: str
    publish_time: str
    stock_codes: List[str] = field(default_factory=list)
    # 每只股票在本文档中的局部权重 (出现次数 or 标题权重)
    stock_weights: Dict[str, float] = field(default_factory=dict)


# ============================================================================ #
# 1. 文档-股票 抽取
# ============================================================================ #
class DocStockExtractor:
    """从 MongoDB 文本 + 标题中抽取 (doc, stock) 对"""

    def __init__(self, lookback_days: int = 30) -> None:
        self.lookback_days: int = lookback_days

    def extract(self, topic_primary: Optional[str] = None,
                topic_secondary: Optional[str] = None) -> List[DocStockEntry]:
        """
        抽取最近 lookback_days 天的文本 → 为每篇文档解析提及股票。
        可通过主题标签筛选。
        """
        cutoff: str = (datetime.datetime.now()
                        - datetime.timedelta(days=self.lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with MongoConnector() as db:
                # 构建查询条件
                query: Dict[str, Any] = {
                    "$or": [
                        {"processed_at": {"$gte": cutoff}},
                        {"publish_time": {"$gte": cutoff}},
                    ]
                }
                # 若传入主题过滤，则按主题标签筛选
                if topic_primary:
                    query["primary_theme"] = topic_primary
                if topic_secondary:
                    query["secondary_theme"] = topic_secondary

                docs = list(db["news_metadata"].find(query, {
                    "_id": 0,
                    "dedup_key": 1,
                    "title": 1,
                    "content": 1,
                    "publish_time": 1,
                    "related_stock_codes": 1,
                }).limit(5000))
        except Exception as exc:
            logger.error("[DocStockExtractor] Mongo 查询异常: %s", exc)
            return []

        entries: List[DocStockEntry] = []
        for d in docs:
            dedup_key: str = str(d.get("dedup_key", ""))
            title: str = str(d.get("title", ""))
            content: str = str(d.get("content", ""))
            publish_time: str = str(d.get("publish_time", ""))

            # 优先使用 LLM 已经打标出来的 related_stock_codes
            explicit_codes: List[str] = list(d.get("related_stock_codes") or [])
            # 再从 title+content 中抽取补充
            text_codes: List[str] = list(dict.fromkeys(
                STOCK_CODE_RE.findall(title + "\n" + content)
            ))

            # 去重合并
            codes: List[str] = list(dict.fromkeys(explicit_codes + text_codes))

            # 过滤明显不是 A 股代码（全同数字）
            codes = [c for c in codes
                     if len(c) == 6 and c.isdigit() and not (c == c[0] * 6)]
            if not codes:
                continue

            # 计算局部权重:
            #   - 标题出现 × 3
            #   - 正文前 200 字出现 × 2
            #   - 正文其余部分 × 1
            weights: Dict[str, float] = {}
            for c in codes:
                w: float = 0.0
                if c in title:
                    w += 3.0
                if c in content[:200]:
                    w += 2.0
                if c in content[200:]:
                    w += 1.0
                if w < 0.5:
                    w = 1.0
                weights[c] = w

            entries.append(DocStockEntry(
                doc_id=dedup_key or f"doc_{len(entries):05d}",
                title=title,
                publish_time=publish_time,
                stock_codes=codes,
                stock_weights=weights,
            ))

        logger.info(
            "[DocStockExtractor] 提取到 %d 篇有效文档, 总提及股票 %d 次",
            len(entries), sum(len(e.stock_codes) for e in entries),
        )
        return entries


# ============================================================================ #
# 2. 二部图构建 (networkx)
# ============================================================================ #
class TextStockNetwork:
    """
    二部图 + 股票间共现图合并:
      doc 节点 → stock 节点, 权重 = 局部 TF 权重
      stock 节点之间若出现在同一文档 → 共现边
    """

    def __init__(self) -> None:
        self.G = None  # type: ignore

    # ----------------------------------------------------------- 构建

    def build(self, entries: List[DocStockEntry]) -> None:
        try:
            import networkx as nx
        except ImportError:
            logger.error("[TextStockNetwork] 未安装 networkx, 请: pip install networkx")
            return

        self.G = nx.Graph()

        # 统计股-股共现
        cooccurrence: Dict[Tuple[str, str], int] = {}

        for e in entries:
            doc_node: str = f"doc::{e.doc_id}"
            self.G.add_node(doc_node, type="doc", title=e.title[:50],
                             publish_time=e.publish_time)

            for c in e.stock_codes:
                stock_node: str = f"stock::{c}"
                if not self.G.has_node(stock_node):
                    self.G.add_node(stock_node, type="stock", code=c)

                w: float = e.stock_weights.get(c, 1.0)
                # 若已有多条边，则累加权重
                if self.G.has_edge(doc_node, stock_node):
                    self.G[doc_node][stock_node]["weight"] += w
                else:
                    self.G.add_edge(doc_node, stock_node, weight=w)

            # 股-股共现
            for i, c1 in enumerate(e.stock_codes):
                for c2 in e.stock_codes[i + 1:]:
                    key: Tuple[str, str] = tuple(sorted([c1, c2]))  # type: ignore
                    cooccurrence[key] = cooccurrence.get(key, 0) + 1

        # 加入共现边 (权重为共现次数, 上限裁剪以避免极端节点)
        for (c1, c2), cnt in cooccurrence.items():
            n1: str = f"stock::{c1}"
            n2: str = f"stock::{c2}"
            if self.G.has_edge(n1, n2):
                self.G[n1][n2]["cooccurrence"] = (
                    self.G[n1][n2].get("cooccurrence", 0) + cnt
                )
            else:
                self.G.add_edge(n1, n2, cooccurrence=min(cnt, 50),
                                weight=float(cnt) * 0.5)

        logger.info("[TextStockNetwork] 图构建完成: %d 节点 / %d 边",
                    self.G.number_of_nodes(), self.G.number_of_edges())

    # ----------------------------------------------------------- 加产业链关系

    def merge_industry_graph(self, csv_path: str) -> None:
        """
        CSV 列: source_code,source_name,relation,target_code,target_name,strength
        例如: 002594,比亚迪,上游,300750,宁德时代,5
        """
        if self.G is None or not os.path.exists(csv_path):
            logger.warning("[TextStockNetwork] 图未初始化或 CSV 不存在: %s", csv_path)
            return
        try:
            df = pd.read_csv(csv_path)
            cols = [c for c in ["source_code", "target_code", "strength"] if c in df.columns]
            if len(cols) < 2:
                logger.warning("[TextStockNetwork] CSV 缺少关键列")
                return
            added: int = 0
            for _, row in df.iterrows():
                src = str(int(row["source_code"])) if "source_code" in row else ""
                tgt = str(int(row["target_code"])) if "target_code" in row else ""
                if not src or not tgt:
                    continue
                # 规范化为 6 位
                src = src.zfill(6)
                tgt = tgt.zfill(6)
                strength: float = float(row.get("strength", 1.0))
                n1: str = f"stock::{src}"
                n2: str = f"stock::{tgt}"
                if not self.G.has_node(n1):
                    self.G.add_node(n1, type="stock", code=src)
                if not self.G.has_node(n2):
                    self.G.add_node(n2, type="stock", code=tgt)
                if self.G.has_edge(n1, n2):
                    self.G[n1][n2]["weight"] = (
                        self.G[n1][n2].get("weight", 0.0) + strength
                    )
                else:
                    self.G.add_edge(n1, n2, weight=strength,
                                    relation=str(row.get("relation", "indirect")))
                added += 1
            logger.info("[TextStockNetwork] 合并产业链边 %d 条", added)
        except Exception as exc:
            logger.error("[TextStockNetwork] 合并产业链 CSV 失败: %s", exc)

    # ----------------------------------------------------------- 中心度计算

    def stock_betweenness(self,
                          topic_stock_codes: Optional[List[str]] = None) -> Dict[str, float]:
        """
        返回 {stock_code: betweenness_centrality_01}。
        - topic_stock_codes 指定后, 仅在子图上计算, 速度更快且更聚焦
        - 归一化到 0~1 (除以最大值)
        """
        if self.G is None or self.G.number_of_nodes() == 0:
            return {}
        try:
            import networkx as nx
        except ImportError:
            return {}

        if topic_stock_codes:
            # 子图: 主题相关股票 + 与之相连的 doc
            seeds: Set[str] = set(f"stock::{c.zfill(6)}" for c in topic_stock_codes if c)
            neighbors: Set[str] = set()
            for s in seeds:
                if s in self.G:
                    neighbors.add(s)
                    for nbr in list(self.G.neighbors(s)):
                        neighbors.add(nbr)
            subgraph = self.G.subgraph(neighbors) if neighbors else self.G
            logger.info("[TextStockNetwork] 在子图(%d节点) 上计算中心度", subgraph.number_of_nodes())
        else:
            subgraph = self.G

        # 计算 betweenness (无权重较快; 若图太大, 取 k 近似)
        n_nodes = subgraph.number_of_nodes()
        k_val = min(n_nodes, 100) if n_nodes > 100 else None
        try:
            centrality = nx.betweenness_centrality(
                subgraph,
                k=k_val,
                normalized=True,
                weight="weight",
                seed=42,
            )
        except Exception:
            centrality = nx.betweenness_centrality(
                subgraph, normalized=True, seed=42
            )

        # 仅输出 stock 节点
        stock_centrality: Dict[str, float] = {}
        for node, val in centrality.items():
            if str(node).startswith("stock::"):
                code = str(node)[len("stock::"):]
                stock_centrality[code] = float(val)

        # 归一化到 0~1 (除以最大值)
        if stock_centrality:
            max_v = max(stock_centrality.values())
            if max_v > 1e-10:
                stock_centrality = {k: v / max_v for k, v in stock_centrality.items()}

        logger.info("[TextStockNetwork] 中介中心度计算完成, 涉及 %d 只股票",
                    len(stock_centrality))
        return stock_centrality

    def stock_degree(self) -> Dict[str, float]:
        """简单度中心度"""
        if self.G is None:
            return {}
        deg = dict(self.G.degree(weight="weight"))
        result: Dict[str, float] = {}
        for node, val in deg.items():
            if str(node).startswith("stock::"):
                result[str(node)[len("stock::"):]] = float(val)
        if result:
            max_v = max(result.values())
            if max_v > 1e-10:
                result = {k: v / max_v for k, v in result.items()}
        return result


# ============================================================================ #
# 便捷函数
# ============================================================================ #
def build_network_for_topic(
    topic_primary: str,
    topic_secondary: str,
    lookback_days: int = 30,
    industry_csv: Optional[str] = None,
) -> Tuple[TextStockNetwork, Dict[str, float]]:
    """
    对指定主题构建网络并计算其中介中心度。
    返回 (network, {stock_code: centrality_01})。
    """
    extractor = DocStockExtractor(lookback_days=lookback_days)
    entries = extractor.extract(topic_primary=topic_primary,
                                 topic_secondary=topic_secondary)
    net = TextStockNetwork()
    net.build(entries)
    if industry_csv and os.path.exists(industry_csv):
        net.merge_industry_graph(industry_csv)
    # 从 entries 收集涉及的股票作为子图种子
    topic_codes: Set[str] = set()
    for e in entries:
        topic_codes.update(e.stock_codes)
    centrality = net.stock_betweenness(topic_stock_codes=list(topic_codes))
    return net, centrality


if __name__ == "__main__":
    # 简单自测: 构建全量文本网络并打印 Top10 中介中心度股票
    net, centr = build_network_for_topic(
        topic_primary="", topic_secondary="", lookback_days=30,
    )
    top = sorted(centr.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info("Top 10 中介中心度股票:")
    for code, v in top:
        logger.info(f"  {code}: {v:.4f}")
