"""
历史相似度引擎 — Historical Similarity Engine

通过比较当前股票与历史中报翻倍股的多维特征相似度，
评估该股复制历史行情的概率。
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Optional

import numpy as np

from .constants import SIMILARITY_FEATURES, SIMILARITY_TOP_N
from .models import SimilarityResult

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 默认历史翻倍股缓存路径
# ──────────────────────────────────────────────
_DEFAULT_WINNERS_CACHE: str = "cache/eld/historical_winners.json"

# 当无历史数据时使用的默认评分
_DEFAULT_NO_DATA_SCORE: float = 50.0

# XGB 代理模型的简化权重（模拟特征重要性）
_XGB_PROXY_WEIGHTS: dict[str, float] = {
    "market_cap": 0.05,
    "alpha_20d": 0.25,
    "turnover_rate": 0.10,
    "forecast_pct": 0.20,
    "roe": 0.10,
    "ocf_ratio": 0.05,
    "chip_concentration": 0.10,
    "institution_flow_20d": 0.10,
    "industry_code": 0.05,
}


class SimilarityEngine:
    """历史相似度引擎

    特征对比逻辑：
    - load_historical_winners(): 加载近10年中报翻倍股数据集
    - extract_features(): 将个股数据转为特征向量
    - cosine_similarity() / euclidean_similarity(): 相似度度量
    - xgb_probability(): 用简化线性模型代理 XGBoost 概率
    - compute_similarity(): 综合计算最终相似度评分
    """

    def __init__(self, data_source: Any) -> None:
        self._data_source = data_source
        self._historical_winners: list[dict[str, Any]] = []
        self._loaded: bool = False

    # ──────────────────────────────────────────
    # 数据加载
    # ──────────────────────────────────────────

    def load_historical_winners(
        self,
        cache_path: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """加载历史翻倍股数据集

        优先从缓存文件加载；若缓存不存在，尝试从数据源构建。

        Args:
            cache_path: 缓存 JSON 文件路径，默认 cache/eld/historical_winners.json

        Returns:
            list[dict]: 历史翻倍股特征字典列表
        """
        if self._loaded and self._historical_winners:
            return self._historical_winners

        path = cache_path or _DEFAULT_WINNERS_CACHE

        # 尝试从缓存加载
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._historical_winners = json.load(f)
                self._loaded = True
                logger.info(
                    "从缓存加载 %d 条历史翻倍股数据", len(self._historical_winners)
                )
                return self._historical_winners
            except Exception as exc:
                logger.warning("读取历史翻倍股缓存失败: %s", exc)

        # 尝试从数据源构建
        try:
            builder = getattr(self._data_source, "build_historical_winners", None)
            if builder is not None:
                self._historical_winners = builder(years=10)
            else:
                # 用预设清单作为兜底
                self._historical_winners = self._fallback_winners()

            if self._historical_winners:
                self._loaded = True
                # 异步写入缓存（不阻塞）
                self._save_cache(path)
                logger.info(
                    "从数据源构建 %d 条历史翻倍股数据", len(self._historical_winners)
                )
            else:
                logger.warning("未找到历史翻倍股数据")
        except Exception as exc:
            logger.warning("构建历史翻倍股数据失败: %s", exc)
            self._historical_winners = []

        return self._historical_winners

    def _fallback_winners(self) -> list[dict[str, Any]]:
        """兜底：返回空列表（数据源应注入真实数据）"""
        logger.info("使用空列表作为历史翻倍股兜底数据")
        return []

    def _save_cache(self, path: str) -> None:
        """将历史数据写入缓存"""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._historical_winners, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug("写入历史翻倍股缓存失败: %s", exc)

    # ──────────────────────────────────────────
    # 特征工程
    # ──────────────────────────────────────────

    def extract_features(self, stock_data: dict[str, Any]) -> np.ndarray:
        """从股票数据字典提取特征向量

        Args:
            stock_data: 包含 SIMILARITY_FEATURES 中各项字段的字典

        Returns:
            np.ndarray: 归一化后的特征向量
        """
        vec: list[float] = []
        for feature in SIMILARITY_FEATURES:
            val = stock_data.get(feature, 0.0)
            if val is None:
                val = 0.0
            # 简单归一化（sigmoid-like 到 [0,1] 区间）
            normalized = self._normalize_feature(feature, float(val))
            vec.append(normalized)
        return np.array(vec, dtype=np.float64)

    @staticmethod
    def _normalize_feature(name: str, value: float) -> float:
        """对单个特征做归一化（经验映射到 0~1）"""
        mapping: dict[str, tuple[float, float]] = {
            "market_cap": (0.0, 1e12),
            "alpha_20d": (-50.0, 50.0),
            "turnover_rate": (0.0, 30.0),
            "forecast_pct": (-100.0, 500.0),
            "roe": (-50.0, 50.0),
            "ocf_ratio": (-200.0, 200.0),
            "chip_concentration": (0.0, 100.0),
            "institution_flow_20d": (-1e10, 1e10),
            "industry_code": (0.0, 100.0),
        }
        lo, hi = mapping.get(name, (0.0, 100.0))
        if hi - lo < 1e-10:
            return 0.5
        clipped = max(lo, min(hi, value))
        return (clipped - lo) / (hi - lo)

    # ──────────────────────────────────────────
    # 相似度计算
    # ──────────────────────────────────────────

    @staticmethod
    def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度 [-1, 1]"""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 < 1e-10 or norm2 < 1e-10:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    @staticmethod
    def euclidean_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算欧氏距离并转为相似度 [0, 1]

        使用 d = ||v1 - v2||, similarity = 1 / (1 + d)
        """
        dist = float(np.linalg.norm(vec1 - vec2))
        return 1.0 / (1.0 + dist)

    # ──────────────────────────────────────────
    # XGB 概率代理
    # ──────────────────────────────────────────

    def xgb_probability(self, stock_data: dict[str, Any]) -> float:
        """用简化线性模型代理 XGBoost 翻倍概率

        当无真实 XGBoost 模型可用时，使用加权线性打分 + sigmoid 映射。

        Args:
            stock_data: 股票特征字典

        Returns:
            float: 翻倍概率 [0, 1]
        """
        score = 0.0
        for feature, weight in _XGB_PROXY_WEIGHTS.items():
            val = stock_data.get(feature, 0.0) or 0.0
            normalized = self._normalize_feature(feature, float(val))
            score += weight * normalized

        # 用 sigmoid 映射到 [0, 1]
        return 1.0 / (1.0 + math.exp(-6.0 * (score - 0.5)))

    # ──────────────────────────────────────────
    # 综合计算
    # ──────────────────────────────────────────

    def compute_similarity(
        self,
        ts_code: str,
        stock_data: dict[str, Any],
    ) -> SimilarityResult:
        """计算当前股票与历史翻倍股的相似度

        流程：
        1. 加载历史翻倍股数据
        2. 提取当前股票特征向量
        3. 逐一计算余弦+欧氏相似度，取综合排名
        4. 取 TOP_SIMILAR 最相似个股
        5. 输出综合评分 0-100

        Args:
            ts_code: 股票代码（仅用于日志）
            stock_data: 当前股票特征数据

        Returns:
            SimilarityResult: 相似度评分结果
        """
        logic: list[str] = []

        # 加载历史数据
        winners = self.load_historical_winners()
        if not winners:
            logic.append("无历史翻倍股数据，采用中性评分")
            return SimilarityResult(
                score=_DEFAULT_NO_DATA_SCORE,
                similar_stocks=[],
                cosine_sim=0.0,
                euclidean_dist=0.0,
                xgb_probability=0.5,
                logic=logic,
            )

        # 提取当前股票特征
        current_vec = self.extract_features(stock_data)
        if np.all(current_vec == 0):
            logic.append("当前股票特征向量全零，无法计算相似度")
            return SimilarityResult(
                score=_DEFAULT_NO_DATA_SCORE,
                similar_stocks=[],
                cosine_sim=0.0,
                euclidean_dist=0.0,
                xgb_probability=0.5,
                logic=logic,
            )

        # 计算与每个历史翻倍股的相似度
        scored: list[tuple[float, dict[str, Any]]] = []
        for winner in winners:
            winner_vec = self.extract_features(winner)
            cos_sim = self.cosine_similarity(current_vec, winner_vec)
            euc_sim = self.euclidean_similarity(current_vec, winner_vec)
            # 综合相似度（余弦+欧氏加权平均）
            combined = 0.6 * max(0.0, cos_sim) + 0.4 * euc_sim
            scored.append((combined, winner))

        # 按综合相似度降序排列，取 TOP_N
        scored.sort(key=lambda x: x[0], reverse=True)
        top_n = scored[:SIMILARITY_TOP_N]

        # 计算综合评分
        if top_n:
            avg_sim = sum(s for s, _ in top_n) / len(top_n)
            # 映射到 0-100
            score = min(100.0, max(0.0, avg_sim * 100.0))

            # 计算平均余弦与欧氏距离
            avg_cos = sum(
                max(0.0, self.cosine_similarity(current_vec, self.extract_features(w)))
                for _, w in top_n
            ) / len(top_n)
            avg_euc = sum(
                self.euclidean_similarity(current_vec, self.extract_features(w))
                for _, w in top_n
            ) / len(top_n)

            # XGB 概率
            xgb_prob = self.xgb_probability(stock_data)

            similar_stocks = [
                {
                    "ts_code": w.get("ts_code", ""),
                    "name": w.get("name", ""),
                    "similarity": round(s * 100.0, 2),
                    "year": w.get("year", ""),
                }
                for s, w in top_n
            ]

            logic.append(f"与 {len(winners)} 条历史翻倍股对比")
            logic.append(
                f"TOP{SIMILARITY_TOP_N} 平均相似度: {avg_sim * 100:.2f}%"
            )
            logic.append(f"综合评分: {score:.1f}分")
        else:
            score = _DEFAULT_NO_DATA_SCORE
            avg_cos = 0.0
            avg_euc = 0.0
            xgb_prob = 0.5
            similar_stocks = []
            logic.append("无有效历史对比数据，采用中性评分")

        return SimilarityResult(
            score=round(score, 1),
            similar_stocks=similar_stocks,
            cosine_sim=round(avg_cos, 4),
            euclidean_dist=round(avg_euc, 4),
            xgb_probability=round(xgb_prob, 4),
            logic=logic,
        )
