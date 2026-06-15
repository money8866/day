"""
主题发现系统 (Topic Discovery)
===============================

从已采集的文本向量中，通过**时空聚类 + LLM 语义命名**，自动发现二级主题
并挂靠到预设一级主题下。

数据流:
  [1] MongoDB news_metadata  → 筛选最近 24h 的 dedup_key
  [2] Milvus news_vectors    → 根据 dedup_key 读取 embedding 向量
  [3] HDBSCAN (min_cluster_size=5, metric=cosine)  → 得到若干簇 + 噪声点
  [4] 每簇选代表性文本   → 调用 DeepSeek 生成 {secondary_topic, primary_topic}
  [5] 与 MongoDB topics 集合做余弦相似度比对 (>0.85 → 合并)
  [6] 落库 + 输出当日新增/活跃主题清单

依赖:
  pip install hdbscan umap-learn scikit-learn numpy httpx pymongo pymilvus
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import asyncio
import logging
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR: str = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import numpy as np  # noqa: E402

from modules.db_connector import (  # noqa: E402
    MongoConnector, MilvusConnector, CONFIG,  # noqa: E402
)
from modules.utils import setup_logger, today_str  # noqa: E402

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
logger: logging.Logger = setup_logger(
    name="topic_discovery",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="topic_discovery.log",
)

# Milvus / MongoDB 集合名（与 data_collector.py 保持一致）
MILVUS_NEWS_COLLECTION: str = "news_vectors"
MONGO_NEWS_META: str = "news_metadata"
MONGO_TOPICS: str = "topics"

# 一级主题白名单（从 config.yaml 的 thematic_system.primary_topic_list 读取）
# 回退默认: 与用户提示词中的 8 个主题一致
DEFAULT_PRIMARY_TOPICS: List[str] = [
    "新能源", "人工智能", "医药健康", "半导体",
    "消费", "金融", "地产", "其他",
]

# 聚类 / 去重参数
HDBSCAN_MIN_CLUSTER_SIZE: int = 5
HDBSCAN_MIN_SAMPLES: Optional[int] = None          # None → 使用 min_cluster_size
TOPIC_DEDUP_SIMILARITY_THRESHOLD: float = 0.85     # 余弦相似度高于此值则合并
TOP_N_REPRESENTATIVE_PER_CLUSTER: int = 8          # 每簇取多少代表文本送 LLM
RECENT_HOURS: int = 24                             # 回看窗口
TOP_K_ACTIVE: int = 20                             # 输出活跃主题数量
VECTOR_DIM: int = 384                              # 与 sentence-transformers 对齐


# ============================================================================ #
# 数据结构
# ============================================================================ #
@dataclass
class NewsDoc:
    """24 小时窗口内的单篇文本 + 向量"""
    dedup_key: str
    title: str
    content: str
    embedding: np.ndarray
    source: str = ""
    llm_primary_theme: str = ""     # 此前 LLM 标注的一级主题（仅作辅助信号）
    llm_secondary_theme: str = ""
    publish_time: str = ""
    related_stock_codes: List[str] = field(default_factory=list)


@dataclass
class TopicCluster:
    """HDBSCAN 输出的簇 + 后续命名、向量化结果"""
    cluster_id: int
    doc_count: int
    docs: List[NewsDoc]
    centroid: np.ndarray
    representative_texts: List[str]
    # LLM 填充
    secondary_topic: str = ""
    primary_topic: str = ""


@dataclass
class TopicRecord:
    """MongoDB topics 中的一条记录"""
    secondary_topic: str
    primary_topic: str
    centroid: List[float]
    first_seen: str
    last_seen: str
    hit_count: int
    sample_texts: List[str]
    recent_doc_ids: List[str]
    activity_score: float = 0.0


# ============================================================================ #
# 1. 数据加载层
# ============================================================================ #
class DataLoader:
    """从 MongoDB + Milvus 拉取最近 N 小时文本及其向量"""

    def __init__(self, hours: int = RECENT_HOURS) -> None:
        self.hours: int = hours

    # ------------------------------------------------------------------ #
    def load(self) -> List[NewsDoc]:
        """主入口。拉取并组装 NewsDoc list"""
        logger.info("[DataLoader] 拉取最近 %dh 文本 ...", self.hours)
        # Step 1: Mongo 中找到时间窗口内的 dedup_key 列表
        mongo_docs: List[Dict[str, Any]] = self._query_mongo_window()
        if not mongo_docs:
            logger.warning("[DataLoader] Mongo 中无最近 %dh 文本，跳过", self.hours)
            return []
        logger.info("[DataLoader] Mongo 命中 %d 条文本元数据", len(mongo_docs))

        # Step 2: Milvus 中按 dedup_key 读取 embedding
        key_to_meta: Dict[str, Dict[str, Any]] = {
            d["dedup_key"]: d for d in mongo_docs if "dedup_key" in d
        }
        vectors_map: Dict[str, np.ndarray] = self._query_milvus_vectors(
            list(key_to_meta.keys())
        )
        if not vectors_map:
            logger.warning("[DataLoader] Milvus 无任何向量返回")
            return []

        # Step 3: 组装
        docs: List[NewsDoc] = []
        for key, meta in key_to_meta.items():
            vec = vectors_map.get(key)
            if vec is None:
                continue
            docs.append(NewsDoc(
                dedup_key=key,
                title=str(meta.get("title", "")),
                content=str(meta.get("content", "")),
                embedding=vec,
                source=str(meta.get("source", "")),
                llm_primary_theme=str(meta.get("primary_theme", "")),
                llm_secondary_theme=str(meta.get("secondary_theme", "")),
                publish_time=str(meta.get("publish_time", "")),
                related_stock_codes=list(meta.get("related_stock_codes", []) or []),
            ))
        logger.info("[DataLoader] 组装完成，共 %d 条带向量文本", len(docs))
        return docs

    # ------------------------------------------------------------------ #
    def _query_mongo_window(self) -> List[Dict[str, Any]]:
        """按 processed_at / publish_time 在时间窗口内查询"""
        cutoff: datetime.datetime = (
            datetime.datetime.now() - datetime.timedelta(hours=self.hours)
        )
        cutoff_str: str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        try:
            with MongoConnector() as db:
                col = db[MONGO_NEWS_META]
                # 优先用 processed_at；若该字段缺失则退回到 publish_time
                docs = list(col.find({
                    "$or": [
                        {"processed_at": {"$gte": cutoff_str}},
                        {"publish_time": {"$gte": cutoff_str}},
                    ]
                }, {
                    "dedup_key": 1, "title": 1, "content": 1,
                    "source": 1, "primary_theme": 1, "secondary_theme": 1,
                    "publish_time": 1, "processed_at": 1,
                    "related_stock_codes": 1,
                }).limit(5000))  # 安全上限
            return docs
        except Exception as exc:
            logger.error("[DataLoader] Mongo 查询异常: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    def _query_milvus_vectors(
        self, dedup_keys: List[str]
    ) -> Dict[str, np.ndarray]:
        """
        按 dedup_key 列表从 Milvus 拉取向量。
        分批查询（每批 ≤ 1024 条），避免单次 query 超限。
        """
        if not dedup_keys:
            return {}

        from pymilvus import Collection

        vectors_map: Dict[str, np.ndarray] = {}
        mc = MilvusConnector()
        full_name = mc.get_collection_name(MILVUS_NEWS_COLLECTION)

        try:
            collection = Collection(full_name, using=mc.alias)
            collection.load()
        except Exception as exc:
            logger.error("[DataLoader] Milvus 集合 %s 无法加载: %s", full_name, exc)
            return vectors_map

        # pymilvus 的 query 支持 in [...]。分批执行
        batch_size: int = 512
        for i in range(0, len(dedup_keys), batch_size):
            batch: List[str] = dedup_keys[i:i + batch_size]
            # Milvus 要求字符串必须以双引号包裹
            expr_list = ",".join(f'"{k}"' for k in batch if k)
            expr: str = f"dedup_key in [{expr_list}]"
            try:
                result = collection.query(
                    expr,
                    output_fields=["dedup_key", "embedding"],
                    limit=len(batch) + 1,
                )
                for row in result:
                    key: str = row.get("dedup_key", "")
                    emb = row.get("embedding")
                    if not key or emb is None:
                        continue
                    arr = np.asarray(emb, dtype=np.float32)
                    if arr.ndim == 1 and arr.shape[0] == VECTOR_DIM:
                        vectors_map[key] = arr
            except Exception as exc:
                logger.warning(
                    "[DataLoader] Milvus query 批次 %d 异常: %s",
                    i // batch_size, exc,
                )
                continue

        logger.info("[DataLoader] Milvus 共读回 %d 条向量", len(vectors_map))
        return vectors_map


# ============================================================================ #
# 2. HDBSCAN 聚类层
# ============================================================================ #
class ClusterEngine:
    """基于余弦距离的 HDBSCAN 聚类，返回 TopicCluster list"""

    def __init__(
        self,
        min_cluster_size: int = HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples: Optional[int] = HDBSCAN_MIN_SAMPLES,
    ) -> None:
        self.min_cluster_size: int = min_cluster_size
        self.min_samples: Optional[int] = min_samples

    # ------------------------------------------------------------------ #
    def cluster(self, docs: List[NewsDoc]) -> List[TopicCluster]:
        if not docs or len(docs) < self.min_cluster_size:
            logger.warning(
                "[ClusterEngine] 文本数 %d < min_cluster_size=%d，跳过聚类",
                len(docs), self.min_cluster_size,
            )
            return []

        try:
            import hdbscan
        except ImportError:
            logger.error("[ClusterEngine] 未安装 hdbscan，执行: pip install hdbscan")
            return []

        # 构建 (N, dim) 矩阵并 L2 归一化 → 后续欧氏距离等价于余弦
        matrix: np.ndarray = np.vstack([d.embedding for d in docs]).astype(np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms < 1e-8] = 1.0
        matrix_normed: np.ndarray = matrix / norms

        logger.info(
            "[ClusterEngine] 开始 HDBSCAN 聚类: shape=%s, min_cluster_size=%d",
            matrix_normed.shape, self.min_cluster_size,
        )

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            min_samples=self.min_samples,
            metric="euclidean",  # 向量已归一化，欧氏等价余弦
            cluster_selection_epsilon=0.0,
            cluster_selection_method="eom",
            gen_min_span_tree=False,
        )
        labels: np.ndarray = clusterer.fit_predict(matrix_normed)

        # 统计簇
        unique_labels, counts = np.unique(labels, return_counts=True)
        logger.info(
            "[ClusterEngine] HDBSCAN 完成: %d 个簇(含噪声 -1)，分布: %s",
            len(unique_labels), dict(zip(unique_labels.tolist(), counts.tolist())),
        )

        clusters: List[TopicCluster] = []
        for label in unique_labels:
            if int(label) == -1:
                continue  # 跳过噪声点
            mask: np.ndarray = (labels == label)
            member_idx: np.ndarray = np.where(mask)[0]
            member_docs: List[NewsDoc] = [docs[i] for i in member_idx]
            centroid: np.ndarray = matrix_normed[member_idx].mean(axis=0)
            # 归一化 centroid，便于后续余弦
            centroid = centroid / (np.linalg.norm(centroid) + 1e-12)

            # 离 centroid 最近的 N 条 → 代表文本
            member_vecs = matrix_normed[member_idx]
            sims: np.ndarray = member_vecs @ centroid
            top_idx: np.ndarray = np.argsort(-sims)[:TOP_N_REPRESENTATIVE_PER_CLUSTER]
            repr_texts: List[str] = []
            for idx in top_idx:
                d: NewsDoc = member_docs[int(idx)]
                snippet: str = (d.title or "") + "\n" + (d.content[:200] or "")
                repr_texts.append(snippet.strip())

            clusters.append(TopicCluster(
                cluster_id=int(label),
                doc_count=len(member_docs),
                docs=member_docs,
                centroid=centroid,
                representative_texts=repr_texts,
            ))

        logger.info("[ClusterEngine] 得到 %d 个有效簇", len(clusters))
        return clusters


# ============================================================================ #
# 3. LLM 主题命名层
# ============================================================================ #
class TopicNamer:
    """并发调用 DeepSeek，把每个簇的代表文本 -> 标准二级主题名 + 挂靠一级主题"""

    _SYSTEM_PROMPT: str = (
        "你是专业的A股产业链研究员。只输出 JSON，不输出任何其他文字或解释。"
    )
    _USER_TEMPLATE: str = (
        "以下是多篇关于A股市场的片段，它们共同讨论了一个细分投资主题。"
        "请用一个简洁的专业术语命名该主题（例如：固态电池、AI智能体、HBM），"
        "并判断它属于哪个一级主题（从[{primary_list}]中选择）。"
        "输出严格 JSON 格式: {{\"secondary_topic\": \"...\", \"primary_topic\": \"...\"}}\n\n"
        "【市场片段】\n{texts}"
    )

    def __init__(self) -> None:
        self.api_key: str = str(CONFIG["api_keys"]["deepseek"].get("api_key", ""))
        self.base_url: str = str(
            CONFIG["api_keys"]["deepseek"].get("base_url", "https://api.deepseek.com")
        ).rstrip("/")
        self.model: str = str(
            CONFIG["api_keys"]["deepseek"].get("chat_model", "deepseek-chat")
        )

    # ------------------------------------------------------------------ #
    async def name_all(self, clusters: List[TopicCluster]) -> List[TopicCluster]:
        if not clusters:
            return clusters
        tasks = [asyncio.create_task(self._name_one(c)) for c in clusters]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid: List[TopicCluster] = []
        for cl, res in zip(clusters, results):
            if isinstance(res, TopicCluster):
                valid.append(res)
            else:
                logger.warning("[TopicNamer] 簇 %d 命名失败: %s", cl.cluster_id, res)
                # 降级: 用关键词/多数派 primary_theme 拼一个
                self._fallback_name(cl)
                valid.append(cl)
        return valid

    async def _name_one(self, cluster: TopicCluster) -> TopicCluster:
        primary_list = _load_primary_topic_list()
        texts_block: str = "\n---\n".join(cluster.representative_texts)[:3500]
        user_prompt: str = self._USER_TEMPLATE.format(
            primary_list=",".join(primary_list), texts=texts_block,
        )

        if not self.api_key or self.api_key.startswith("${"):
            # 无 API key → 走本地降级
            self._fallback_name(cluster)
            return cluster

        try:
            import httpx
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            content: str = data["choices"][0]["message"]["content"]
            parsed = _parse_json(content)
            secondary = str(parsed.get("secondary_topic", "")).strip()
            primary = str(parsed.get("primary_topic", "")).strip()
            cluster.secondary_topic = secondary or f"簇{cluster.cluster_id:03d}"
            cluster.primary_topic = primary or self._fallback_primary(cluster)
            logger.info(
                "[TopicNamer] 簇%d (%d篇) → [%s / %s]",
                cluster.cluster_id, cluster.doc_count,
                cluster.primary_topic, cluster.secondary_topic,
            )
            return cluster
        except Exception as exc:
            logger.warning("[TopicNamer] LLM 调用异常: %s", exc)
            self._fallback_name(cluster)
            return cluster

    # ------------------------------------------------------------------ #
    def _fallback_name(self, cluster: TopicCluster) -> None:
        """LLM 不可用时的降级命名:
        - 一级: 取成员文本中 LLM 标注的 primary_theme 的多数票
        - 二级: 取最长出现的高频名词短语（简单启发式，取前 3 个唯一二级主题中多数）
        """
        primary_votes: Dict[str, int] = {}
        secondary_votes: Dict[str, int] = {}
        for d in cluster.docs:
            if d.llm_primary_theme:
                primary_votes[d.llm_primary_theme] = (
                    primary_votes.get(d.llm_primary_theme, 0) + 1
                )
            if d.llm_secondary_theme:
                secondary_votes[d.llm_secondary_theme] = (
                    secondary_votes.get(d.llm_secondary_theme, 0) + 1
                )
        cluster.primary_topic = (
            max(primary_votes, key=primary_votes.get) if primary_votes else "其他"
        )
        cluster.secondary_topic = (
            max(secondary_votes, key=secondary_votes.get)
            if secondary_votes else f"簇{cluster.cluster_id:03d}"
        )
        logger.info(
            "[TopicNamer] 降级命名: 簇%d → [%s / %s]",
            cluster.cluster_id, cluster.primary_topic, cluster.secondary_topic,
        )

    def _fallback_primary(self, cluster: TopicCluster) -> str:
        return "其他"


# ============================================================================ #
# 4. 主题去重与落库层
# ============================================================================ #
class TopicStore:
    """MongoDB topics 集合的写入/去重/查询"""

    _COLLECTION: str = MONGO_TOPICS

    def __init__(self) -> None:
        self._ensure_indexes()

    # ------------------------------------------------------------------ #
    def _ensure_indexes(self) -> None:
        try:
            with MongoConnector() as db:
                col = db[self._COLLECTION]
                col.create_index([("secondary_topic", 1), ("primary_topic", 1)],
                                 unique=True)
                col.create_index([("last_seen", -1)])
                col.create_index([("hit_count", -1)])
                col.create_index([("primary_topic", 1)])
        except Exception as exc:
            logger.warning("[TopicStore] Mongo 索引创建异常: %s", exc)

    # ------------------------------------------------------------------ #
    def fetch_existing_centroids(self) -> List[TopicRecord]:
        """返回所有已存在主题的质心，供相似度比对"""
        try:
            with MongoConnector() as db:
                docs = list(db[self._COLLECTION].find({}, {
                    "secondary_topic": 1, "primary_topic": 1, "centroid_vector": 1,
                    "first_seen": 1, "last_seen": 1, "hit_count": 1,
                    "sample_texts": 1, "recent_doc_ids": 1, "activity_score": 1,
                }))
        except Exception as exc:
            logger.error("[TopicStore] 查询已有主题异常: %s", exc)
            return []

        records: List[TopicRecord] = []
        for d in docs:
            try:
                records.append(TopicRecord(
                    secondary_topic=d.get("secondary_topic", ""),
                    primary_topic=d.get("primary_topic", ""),
                    centroid=list(d.get("centroid_vector", []) or []),
                    first_seen=str(d.get("first_seen", today_str())),
                    last_seen=str(d.get("last_seen", today_str())),
                    hit_count=int(d.get("hit_count", 0)),
                    sample_texts=list(d.get("sample_texts", []) or []),
                    recent_doc_ids=list(d.get("recent_doc_ids", []) or []),
                    activity_score=float(d.get("activity_score", 0.0)),
                ))
            except Exception:
                continue
        logger.info("[TopicStore] 当前已有 %d 个主题", len(records))
        return records

    # ------------------------------------------------------------------ #
    def upsert_cluster(
        self,
        cluster: TopicCluster,
        existing: List[TopicRecord],
    ) -> Tuple[str, bool]:
        """
        返回 (secondary_topic, is_new)
        逻辑:
          - 若质心与某既有主题余弦 > 0.85 → 视为同一主题，合并
          - 否则新增
        """
        now_str: str = _now_iso()
        new_centroid_vec: np.ndarray = cluster.centroid

        # 4.1 先按名称精确匹配 (强约束: 同名 → 直接合并)
        name_hit: Optional[TopicRecord] = next(
            (t for t in existing if t.secondary_topic == cluster.secondary_topic), None,
        )

        # 4.2 余弦相似度比对
        sim_hit: Optional[TopicRecord] = None
        best_sim: float = 0.0
        if existing and new_centroid_vec.size:
            existing_matrix = np.array(
                [t.centroid for t in existing if len(t.centroid) == VECTOR_DIM],
                dtype=np.float32,
            )
            if existing_matrix.size:
                # 归一化既有质心（插入时已做，这里再保险一次）
                norms = np.linalg.norm(existing_matrix, axis=1, keepdims=True)
                norms[norms < 1e-8] = 1.0
                existing_norm = existing_matrix / norms
                sims = existing_norm @ new_centroid_vec.astype(np.float32)
                best_idx: int = int(np.argmax(sims))
                best_sim = float(sims[best_idx])
                if best_sim >= TOPIC_DEDUP_SIMILARITY_THRESHOLD:
                    # 同时满足名称优先 + 相似度优先：取两者中更好的
                    sim_hit = existing[best_idx]

        merged_record = name_hit or sim_hit
        is_new: bool = merged_record is None

        if merged_record is not None:
            # 合并: 更新质心（加权平均）+ 计数 + 最近文本
            new_count = cluster.doc_count
            old_count = max(merged_record.hit_count, 1)
            old_centroid = np.asarray(merged_record.centroid, dtype=np.float32)
            if old_centroid.size == VECTOR_DIM and new_centroid_vec.size:
                blended = (old_centroid * old_count
                           + new_centroid_vec * new_count) / (old_count + new_count)
                blended = blended / (np.linalg.norm(blended) + 1e-12)
                merged_centroid_list: List[float] = blended.tolist()
            else:
                merged_centroid_list = new_centroid_vec.tolist()

            new_doc_ids = [d.dedup_key for d in cluster.docs]
            merged_sample_texts = list(dict.fromkeys(
                cluster.representative_texts + merged_record.sample_texts
            ))[:10]
            merged_recent_ids = list(dict.fromkeys(
                new_doc_ids + merged_record.recent_doc_ids
            ))[:100]

            update_doc = {
                "$set": {
                    "centroid_vector": merged_centroid_list,
                    "last_seen": now_str,
                    "sample_texts": merged_sample_texts,
                    "recent_doc_ids": merged_recent_ids,
                },
                "$inc": {
                    "hit_count": new_count,
                    "activity_score": float(new_count),
                },
            }
            try:
                with MongoConnector() as db:
                    db[self._COLLECTION].update_one(
                        {"secondary_topic": merged_record.secondary_topic,
                         "primary_topic": merged_record.primary_topic},
                        update_doc,
                    )
                logger.info(
                    "[TopicStore] 合并主题 [%s / %s] (hit=%d, sim=%.3f, 最似=%s)",
                    merged_record.primary_topic, merged_record.secondary_topic,
                    new_count, best_sim,
                    "名称精确匹配" if name_hit else f"余弦={best_sim:.3f}",
                )
            except Exception as exc:
                logger.error("[TopicStore] 更新主题失败: %s", exc)
            return merged_record.secondary_topic, False

        # 新增主题
        doc = {
            "secondary_topic": cluster.secondary_topic or f"簇{cluster.cluster_id:03d}",
            "primary_topic": cluster.primary_topic or "其他",
            "centroid_vector": new_centroid_vec.tolist(),
            "first_seen": now_str,
            "last_seen": now_str,
            "hit_count": cluster.doc_count,
            "sample_texts": list(cluster.representative_texts)[:10],
            "recent_doc_ids": [d.dedup_key for d in cluster.docs][:100],
            "activity_score": float(cluster.doc_count),
            "created_from": "hdbscan_auto_discovery",
        }
        try:
            with MongoConnector() as db:
                db[self._COLLECTION].update_one(
                    {"secondary_topic": doc["secondary_topic"],
                     "primary_topic": doc["primary_topic"]},
                    {"$setOnInsert": doc,
                     "$set": {"last_seen": now_str}},
                    upsert=True,
                )
            logger.info(
                "[TopicStore] ✨ 新主题 [%s / %s] 入库 (成员=%d)",
                doc["primary_topic"], doc["secondary_topic"], cluster.doc_count,
            )
        except Exception as exc:
            logger.error("[TopicStore] 新主题落库失败: %s", exc)
        return doc["secondary_topic"], True

    # ------------------------------------------------------------------ #
    def fetch_today_topics(self) -> List[Dict[str, Any]]:
        """返回按活跃度排序的 TOP 主题（当日活跃 / 新增）"""
        today_prefix: str = today_str()
        try:
            with MongoConnector() as db:
                # 当日 last_seen 等于今天 的主题（最近活跃）
                cur = db[self._COLLECTION].find({
                    "$or": [
                        {"last_seen": {"$regex": f"^{today_prefix}"}},
                        {"first_seen": {"$regex": f"^{today_prefix}"}},
                    ],
                }).sort([("hit_count", -1), ("last_seen", -1)]).limit(TOP_K_ACTIVE)
                rows: List[Dict[str, Any]] = list(cur)
        except Exception as exc:
            logger.error("[TopicStore] 查询当日主题异常: %s", exc)
            return []

        # 清理向量字段，使输出可读性强
        for r in rows:
            r.pop("centroid_vector", None)
            r.pop("_id", None)
        return rows


# ============================================================================ #
# 5. 主流程编排
# ============================================================================ #
class TopicDiscoveryPipeline:
    """24h 文本 → 聚类 → LLM 命名 → 去重 → 入库 → 打印当日主题清单"""

    def __init__(self, hours: int = RECENT_HOURS) -> None:
        self.loader = DataLoader(hours=hours)
        self.clusterer = ClusterEngine()
        self.namer = TopicNamer()
        self.store = TopicStore()

    def run(self) -> Dict[str, Any]:
        logger.info("=" * 60)
        logger.info("[Pipeline] 主题发现管线启动 (回看 %dh)", self.loader.hours)
        logger.info("=" * 60)

        now_str = _now_iso()
        stats: Dict[str, Any] = {
            "run_time": now_str,
            "window_hours": self.loader.hours,
            "docs_fetched": 0,
            "clusters": 0,
            "new_topics": [],
            "merged_topics": [],
            "active_topics": [],
        }

        # Step 1
        docs: List[NewsDoc] = self.loader.load()
        stats["docs_fetched"] = len(docs)
        if not docs:
            logger.warning("[Pipeline] 无可用文本数据，结束")
            return stats

        # Step 2 聚类
        clusters: List[TopicCluster] = self.clusterer.cluster(docs)
        stats["clusters"] = len(clusters)
        if not clusters:
            logger.warning("[Pipeline] 无有效簇，结束")
            return stats

        # Step 3 LLM 命名（异步并发）
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            clusters = loop.run_until_complete(self.namer.name_all(clusters))
        finally:
            loop.close()

        # Step 4 去重 + 落库
        existing: List[TopicRecord] = self.store.fetch_existing_centroids()
        new_topics: List[str] = []
        merged_topics: List[str] = []
        for cl in clusters:
            if not cl.secondary_topic:
                continue
            secondary, is_new = self.store.upsert_cluster(cl, existing)
            (new_topics if is_new else merged_topics).append(
                f"[{cl.primary_topic}] {secondary} (成员={cl.doc_count})"
            )
        stats["new_topics"] = new_topics
        stats["merged_topics"] = merged_topics

        # Step 5 输出当日活跃主题清单
        active = self.store.fetch_today_topics()
        stats["active_topics"] = [
            {
                "primary_topic": r.get("primary_topic"),
                "secondary_topic": r.get("secondary_topic"),
                "hit_count": r.get("hit_count"),
                "activity_score": r.get("activity_score"),
                "first_seen": r.get("first_seen"),
                "last_seen": r.get("last_seen"),
            }
            for r in active
        ]

        # Step 6 控制台摘要
        _print_summary(stats)
        return stats


# ============================================================================ #
# 工具函数
# ============================================================================ #
def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_primary_topic_list() -> List[str]:
    """从 config.yaml 的 thematic_system.primary_topic_list 读取，
    否则用 DEFAULT_PRIMARY_TOPICS。"""
    try:
        user_list = (
            CONFIG.get("thematic_system", {})
                  .get("primary_topic_list")
        )
        if isinstance(user_list, list) and user_list:
            return [str(x).strip() for x in user_list if str(x).strip()]
    except Exception:
        pass
    return list(DEFAULT_PRIMARY_TOPICS)


def _parse_json(text: str) -> Dict[str, Any]:
    """健壮解析 LLM 输出的 JSON"""
    if not text:
        return {}
    txt = text.strip()
    if "```" in txt:
        start = txt.find("```")
        end = txt.find("```", start + 3)
        if end > start:
            txt = txt[start + 3:end].strip()
            if txt.lower().startswith("json"):
                txt = txt[4:].strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _print_summary(stats: Dict[str, Any]) -> None:
    print()
    print("=" * 64)
    print(f"  主题发现报告  {stats['run_time']}  (回看 {stats['window_hours']}h)")
    print("=" * 64)
    print(f"  - 已处理文本:        {stats['docs_fetched']} 篇")
    print(f"  - 识别有效簇:        {stats['clusters']} 个")
    print(f"  - 新主题:            {len(stats['new_topics'])} 个")
    for t in stats["new_topics"]:
        print(f"      ✨ {t}")
    print(f"  - 合并/活跃既有主题: {len(stats['merged_topics'])} 个")
    for t in stats["merged_topics"]:
        print(f"      ↔ {t}")
    print("-" * 64)
    print(f"  TOP 当日活跃主题 (按 hit_count 排序):")
    for i, r in enumerate(stats["active_topics"][:10], start=1):
        print(
            f"    {i:2d}. [{r['primary_topic']}] {r['secondary_topic']:<16s} "
            f"hit={r['hit_count']:<4d} score={r['activity_score']:.1f} "
            f"(first_seen={r['first_seen'][:10]})"
        )
    print("=" * 64)
    print()


# ============================================================================ #
# 模块级调用入口
# ============================================================================ #
def discover_topics(hours: int = RECENT_HOURS) -> Dict[str, Any]:
    """对外暴露的轻量级函数，供外部脚本/定时任务调用。"""
    pipeline = TopicDiscoveryPipeline(hours=hours)
    return pipeline.run()


if __name__ == "__main__":
    discover_topics()
