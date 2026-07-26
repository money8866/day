"""Theme Confidence 评分算法 - 多维度置信度评分."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from theme_kg_v3.core.keyword_engine import KeywordEngine
from theme_kg_v3.schema.dataclasses import ConfidenceBreakdown
from theme_kg_v3.config.settings import (
    THEME_CONFIG_PATH,
    ETF_MAPPING_PATH,
    DEFAULT_CONFIDENCE_WEIGHTS,
)

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """多维度置信度评分器.

    对给定股票与主题的匹配程度，从 ETF 相关性、行业匹配、营收匹配、
    概念匹配、研报匹配、业务描述、供应链、客户、产品、TF-IDF 等
    多个维度分别评分，最后按权重聚合为综合置信度。
    """

    def __init__(
        self,
        keyword_engine: KeywordEngine,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        """初始化评分器.

        Args:
            keyword_engine: 关键词引擎实例，提供匹配能力.
            weights: 各维度权重字典，未提供时使用 DEFAULT_CONFIDENCE_WEIGHTS.
        """
        self.keyword_engine = keyword_engine
        self.weights = dict(DEFAULT_CONFIDENCE_WEIGHTS)
        if weights:
            self.weights.update(weights)
        # 确保 keyword_tfidf 有权重（默认配置中可能没有该字段）
        self.weights.setdefault("keyword_tfidf", 0.05)

        # 加载 ETF 映射表
        self._etf_mapping: Dict[str, Any] = {}
        etf_path = ETF_MAPPING_PATH
        if etf_path.exists():
            with open(etf_path, encoding="utf-8") as f:
                self._etf_mapping = json.load(f)
        else:
            logger.warning("ETF mapping file not found: %s", etf_path)

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def score(
        self,
        theme_code: str,
        stock_data: Dict[str, Any],
    ) -> Tuple[float, ConfidenceBreakdown]:
        """计算某主题下股票的综合置信度得分.

        Args:
            theme_code: 主题代码，如 "AI_COMPUTE".
            stock_data: 股票数据字典.

        Returns:
            (总分, 置信度分解详情).
        """
        break_kwargs: Dict[str, float] = {
            "etf_correlation": self._score_etf_correlation(theme_code, stock_data),
            "industry_match": self._score_industry_match(theme_code, stock_data),
            "revenue_match": self._score_revenue_match(theme_code, stock_data),
            "concept_match": self._score_concept_match(theme_code, stock_data),
            "institution_report": self._score_institution_report(theme_code, stock_data),
            "business_description": self._score_business_description(theme_code, stock_data),
            "supply_chain": self._score_supply_chain(theme_code, stock_data),
            "customer": self._score_customer(theme_code, stock_data),
            "product": self._score_product(theme_code, stock_data),
            "keyword_tfidf": self._score_keyword_tfidf(theme_code, stock_data),
        }

        total_score, reason = self._aggregate(break_kwargs)
        break_kwargs["total_score"] = round(total_score, 2)
        break_kwargs["reason"] = reason

        breakdown = ConfidenceBreakdown(**break_kwargs)
        return round(total_score, 2), breakdown

    # ──────────────────────────────────────────────
    # 各维度评分方法
    # ──────────────────────────────────────────────

    def _score_etf_correlation(self, theme_code: str, stock_data: Dict) -> float:
        """ETF 持仓相关性评分 (0-100).

        检查股票所属的申万/中信行业是否与主题的 ETF 行业标签匹配.
        """
        sw_industry = stock_data.get("sw_industry", "")
        cx_industry = stock_data.get("cx_industry", "")

        theme_etf_info = self._etf_mapping.get(theme_code, {})
        etf_industry_tags = theme_etf_info.get("etf_industry_tags", [])

        if not etf_industry_tags:
            return 30.0

        # 直接匹配
        for tag in etf_industry_tags:
            if sw_industry and tag in sw_industry:
                return 90.0
            if cx_industry and tag in cx_industry:
                return 85.0

        # 子行业匹配（sw_industry 或 cx_industry 作为 etf_industry_tags 的子集）
        for tag in etf_industry_tags:
            if sw_industry and sw_industry in tag:
                return 60.0
            if cx_industry and cx_industry in tag:
                return 55.0

        # 关键词重叠估算
        overlap_score = self._keyword_overlap_score(
            [sw_industry, cx_industry],
            etf_industry_tags,
        )
        return min(overlap_score, 30.0)

    def _score_industry_match(self, theme_code: str, stock_data: Dict) -> float:
        """行业匹配评分 (0-100).

        直接匹配申万一级/二级、中信行业.
        """
        sw_industry = stock_data.get("sw_industry", "")
        cx_industry = stock_data.get("cx_industry", "")
        theme = self.keyword_engine.theme_config.get(theme_code, {})

        sw_match_list = theme.get("sw_industry_match", [])
        cx_match_list = theme.get("cx_industry_match", [])

        # 申万一级行业直接匹配
        if sw_industry and sw_industry in sw_match_list:
            return 100.0

        # 中信行业匹配
        if cx_industry and cx_industry in cx_match_list:
            return 70.0

        # 二级行业匹配（通过 sw_industry_mapping）
        if self.keyword_engine.sw_industry_mapping:
            secondary_map = self.keyword_engine.sw_industry_mapping.get("二级行业", {})
            if sw_industry:
                mapped_themes = secondary_map.get(sw_industry, [])
                if theme_code in mapped_themes:
                    return 80.0
            if not sw_industry and cx_industry:
                mapped_themes = secondary_map.get(cx_industry, [])
                if theme_code in mapped_themes:
                    return 80.0

        return 0.0

    def _score_revenue_match(self, theme_code: str, stock_data: Dict) -> float:
        """营收匹配评分 (0-100).

        从 revenue_breakdown 中找出匹配主题关键词的产品营收占比.
        """
        revenue_breakdown = stock_data.get("revenue_breakdown", {})
        if not revenue_breakdown:
            return 50.0

        theme = self.keyword_engine.theme_config.get(theme_code, {})
        all_keywords = self._get_flat_theme_keywords(theme_code)

        if not all_keywords:
            return 0.0

        relevant_revenue_pct = 0.0
        for product_name, pct in revenue_breakdown.items():
            pct_val = float(pct) if pct is not None else 0.0
            for kw in all_keywords:
                if kw in product_name:
                    relevant_revenue_pct += pct_val
                    break

        # Score = min(relevant_revenue_pct * 100 / 0.5, 100)
        score = min(relevant_revenue_pct * 100 / 0.5, 100.0)
        return max(0.0, score)

    def _score_concept_match(self, theme_code: str, stock_data: Dict) -> float:
        """概念匹配评分 (0-100).

        通过 keyword_engine 的概念匹配结果归一化.
        """
        eastmoney_concepts = stock_data.get("eastmoney_concepts", [])
        ths_concepts = stock_data.get("ths_concepts", [])

        all_concepts = list(dict.fromkeys(eastmoney_concepts + ths_concepts))
        if not all_concepts:
            return 0.0

        results = self.keyword_engine.match_by_concepts(
            all_concepts,
            theme_codes=[theme_code],
        )
        if results:
            return results[0].score
        return 0.0

    def _score_institution_report(self, theme_code: str, stock_data: Dict) -> float:
        """机构研报匹配评分 (0-100).

        统计研报文本中主题关键词的出现次数.
        """
        reports = stock_data.get("institution_reports", [])
        if not reports:
            return 0.0

        all_keywords = self._get_flat_theme_keywords(theme_code)
        if not all_keywords:
            return 0.0

        # 合并所有研报文本
        combined_text = " ".join(str(r) for r in reports if r)

        hit_count = 0
        for kw in all_keywords:
            if kw in combined_text:
                hit_count += 1

        score = min(hit_count * 10.0, 100.0)
        return score

    def _score_business_description(self, theme_code: str, stock_data: Dict) -> float:
        """业务描述匹配评分 (0-100).

        直接使用 keyword_engine 的业务描述匹配结果.
        """
        description = stock_data.get("business_description", "")
        if not description:
            return 0.0

        results = self.keyword_engine.match_by_business_description(description)
        for r in results:
            if r.theme_code == theme_code:
                return r.score
        return 0.0

    def _score_supply_chain(self, theme_code: str, stock_data: Dict) -> float:
        """供应链关联评分 (0-100).

        匹配 supply_chain_position 中的关键词.
        """
        position = stock_data.get("supply_chain_position", "")
        if not position:
            return 20.0

        theme = self.keyword_engine.theme_config.get(theme_code, {})
        chain_nodes = theme.get("industry_chains", [])
        all_keywords = self._get_flat_theme_keywords(theme_code)

        # 上游/下游/核心匹配
        core_indicators = ["上游", "下游", "中游", "核心", "龙头", "垂直"]
        for indicator in core_indicators:
            if indicator in position:
                # 再检查是否与主题相关
                for kw in all_keywords:
                    if kw in position:
                        return 90.0

        # 产业链节点匹配
        for node in chain_nodes:
            if node in position:
                return 85.0

        # 外围匹配
        for kw in all_keywords:
            if kw in position:
                return 50.0

        return 20.0

    def _score_customer(self, theme_code: str, stock_data: Dict) -> float:
        """客户关联评分 (0-100).

        检查客户名称是否包含主题关键词.
        """
        customers = stock_data.get("customers", [])
        if not customers:
            return 0.0

        all_keywords = self._get_flat_theme_keywords(theme_code)
        if not all_keywords:
            return 0.0

        customer_text = " ".join(str(c) for c in customers if c)

        # 直接关键词匹配
        direct_hits = 0
        for kw in all_keywords:
            if kw in customer_text:
                direct_hits += 1

        if direct_hits >= 2:
            return 90.0
        elif direct_hits == 1:
            return 75.0

        # 行业相关匹配
        sw_industry = stock_data.get("sw_industry", "")
        if sw_industry and sw_industry in customer_text:
            return 50.0

        return 0.0

    def _score_product(self, theme_code: str, stock_data: Dict) -> float:
        """产品关联评分 (0-100).

        检查产品名称是否匹配主题关键词或产业链节点.
        """
        products = stock_data.get("products", [])
        if not products:
            return 0.0

        theme = self.keyword_engine.theme_config.get(theme_code, {})
        all_keywords = self._get_flat_theme_keywords(theme_code)
        chain_nodes = theme.get("industry_chains", [])
        all_terms = list(dict.fromkeys(all_keywords + chain_nodes))

        if not all_terms:
            return 0.0

        product_text = " ".join(str(p) for p in products if p)

        # 直接产品匹配
        for term in all_keywords:
            if term in product_text:
                return 95.0

        # 产业链节点匹配
        for node in chain_nodes:
            if node in product_text:
                return 85.0

        # 模糊匹配
        product_tokens = set()
        for p in products:
            if isinstance(p, str):
                product_tokens.update(self.keyword_engine._tokenize_text(p))

        for term in all_terms:
            term_tokens = set(self.keyword_engine._tokenize_text(term))
            if product_tokens & term_tokens:
                return 60.0

        return 0.0

    def _score_keyword_tfidf(self, theme_code: str, stock_data: Dict) -> float:
        """关键词 TF-IDF 匹配评分 (0-100).

        将股票各字段文本合并后计算 TF-IDF，取主题关键词对应的最高分.
        """
        # 收集所有文本
        texts: List[str] = []
        if stock_data.get("business_description"):
            texts.append(stock_data["business_description"])
        if stock_data.get("products"):
            texts.extend(str(p) for p in stock_data["products"])
        if stock_data.get("customers"):
            texts.extend(str(c) for c in stock_data["customers"])
        if stock_data.get("supply_chain_position"):
            texts.append(stock_data["supply_chain_position"])
        if stock_data.get("institution_reports"):
            texts.extend(str(r) for r in stock_data["institution_reports"])

        if not texts:
            return 0.0

        tfidf_scores = self.keyword_engine.compute_tfidf(texts)
        all_keywords = self._get_flat_theme_keywords(theme_code)

        if not all_keywords or not tfidf_scores:
            return 0.0

        # 取主题关键词对应的最高 TF-IDF 分
        max_score = 0.0
        for kw in all_keywords:
            kw_score = tfidf_scores.get(kw, 0.0)
            if kw_score > max_score:
                max_score = kw_score

        # 归一化到 0-100
        normalized = min(max_score * 50.0, 100.0)
        return normalized

    # ──────────────────────────────────────────────
    # 聚合与归一化
    # ──────────────────────────────────────────────

    def _aggregate(
        self,
        scores: Dict[str, float],
    ) -> Tuple[float, str]:
        """加权聚合各维度得分，生成原因说明.

        Args:
            scores: 各维度得分字典.

        Returns:
            (加权总分, 原因说明字符串).
        """
        total = 0.0
        contributions: List[Tuple[str, str, float]] = []  # (key, label, weighted_score)

        label_map = {
            "etf_correlation": "ETF相关性",
            "industry_match": "行业匹配",
            "revenue_match": "营收匹配",
            "concept_match": "概念匹配",
            "institution_report": "机构研报",
            "business_description": "业务描述",
            "supply_chain": "供应链",
            "customer": "客户",
            "product": "产品",
            "keyword_tfidf": "关键词TF-IDF",
        }

        for key, raw_score in scores.items():
            weight = self.weights.get(key, 0.0)
            weighted = raw_score * weight
            total += weighted
            label = label_map.get(key, key)
            contributions.append((key, label, raw_score))

        # 按加权得分降序排列，取 Top 3
        contributions.sort(key=lambda x: x[2] * self.weights.get(x[0], 0.0), reverse=True)
        top3 = contributions[:3]

        reason_parts = [f"{label}({raw_score:.0f})" for _, label, raw_score in top3]
        reason = ", ".join(reason_parts) if reason_parts else "无显著匹配"

        return round(total, 2), reason

    def _normalize_scores(
        self,
        raw_scores: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """min-max 归一化所有主题得分至 0-100.

        Args:
            raw_scores: {theme_code: {dimension: score}} 格式的原始分数字典.

        Returns:
            {theme_code: normalized_total} 归一化后总分.
        """
        if not raw_scores:
            return {}

        # 先计算每个主题的加权总分
        totals: Dict[str, float] = {}
        for code, dims in raw_scores.items():
            total = 0.0
            for dim, score in dims.items():
                weight = self.weights.get(dim, 0.0)
                total += score * weight
            totals[code] = total

        values = list(totals.values())
        min_val = min(values)
        max_val = max(values)

        if max_val == min_val:
            return {code: 50.0 for code in totals}

        normalized = {}
        for code, val in totals.items():
            normalized[code] = round((val - min_val) / (max_val - min_val) * 100.0, 2)

        return normalized

    # ──────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────

    def _get_flat_theme_keywords(self, theme_code: str) -> List[str]:
        """获取主题的扁平化关键词列表（去重）."""
        kw_dict = self.keyword_engine._get_theme_keywords(theme_code)
        all_kws: List[str] = []
        for kw_list in kw_dict.values():
            all_kws.extend(kw_list)
        # 去重保持顺序
        return list(dict.fromkeys(all_kws))

    @staticmethod
    def _keyword_overlap_score(
        texts: List[str],
        keywords: List[str],
    ) -> float:
        """计算文本与关键词列表的简单重叠得分."""
        combined = " ".join(t for t in texts if t)
        if not combined or not keywords:
            return 0.0

        hits = sum(1 for kw in keywords if kw in combined)
        return min(hits / len(keywords) * 100.0, 100.0) if keywords else 0.0
