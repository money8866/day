"""自动归类引擎 - 多维度股票主题分类."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from theme_kg_v3.core.confidence import ConfidenceScorer
from theme_kg_v3.core.keyword_engine import KeywordEngine, KeywordMatchResult
from theme_kg_v3.schema.dataclasses import ClassificationResult, ConfidenceBreakdown
from theme_kg_v3.config.settings import (
    THEME_CONFIG_PATH,
    CLASSIFIER_WORKERS,
    DEFAULT_CONFIDENCE_WEIGHTS,
)

logger = logging.getLogger(__name__)


class ThemeClassifier:
    """主题自动分类器.

    对给定股票数据，基于关键词匹配、行业映射、概念标签、营收构成、
    研报分析等多维度信息，计算各主题的置信度，自动确定 Primary Theme
    及次要主题，并关联产业链和概念标签.
    """

    def __init__(
        self,
        keyword_engine: KeywordEngine,
        confidence_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        """初始化分类器.

        Args:
            keyword_engine: 关键词引擎实例.
            confidence_weights: 置信度权重字典，覆盖默认值.
        """
        self.keyword_engine = keyword_engine
        self.theme_config = keyword_engine.theme_config

        # 初始化置信度评分器
        self.scorer = ConfidenceScorer(
            keyword_engine=keyword_engine,
            weights=confidence_weights,
        )

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def classify(self, stock_data: Dict[str, Any]) -> ClassificationResult:
        """对单只股票进行主题分类.

        Args:
            stock_data: 股票数据字典，包含以下字段:
                - stock_code: 股票代码
                - stock_name: 股票名称
                - sw_industry: 申万行业
                - cx_industry: 中信行业
                - eastmoney_concepts: 东方财富概念列表
                - ths_concepts: 同花顺概念列表
                - business_description: 主营业务描述
                - products: 产品列表
                - customers: 客户列表
                - supply_chain_position: 供应链位置描述
                - institution_reports: 机构研报文本列表
                - revenue_breakdown: 营收构成 {产品名: 营收占比}

        Returns:
            分类结果 ClassificationResult.
        """
        stock_code = stock_data.get("stock_code", "")
        stock_name = stock_data.get("stock_name", "")

        # ── Step 1: 运行所有匹配策略 ──────────────────
        all_match_scores = self._run_all_strategies(stock_data)

        if not all_match_scores:
            return ClassificationResult(
                stock_code=stock_code,
                stock_name=stock_name,
                primary_theme_code="",
                primary_theme_name="",
                confidence=0.0,
                confidence_breakdown=ConfidenceBreakdown(reason="无匹配主题"),
            )

        # ── Step 2: 计算每个候选主题的置信度 ──────────
        theme_confidences: Dict[str, Tuple[float, ConfidenceBreakdown]] = {}
        for theme_code in all_match_scores:
            confidence, breakdown = self.scorer.score(theme_code, stock_data)
            theme_confidences[theme_code] = (confidence, breakdown)

        # ── Step 3: 排除关键词过滤 ────────────────────
        self._apply_exclude_penalty(
            theme_confidences, stock_data, penalty_rate=0.30,
        )

        # ── Step 4: 选择 Primary Theme ────────────────
        sorted_themes = sorted(
            theme_confidences.items(),
            key=lambda x: x[1][0],
            reverse=True,
        )

        if not sorted_themes or sorted_themes[0][0] == "":
            return ClassificationResult(
                stock_code=stock_code,
                stock_name=stock_name,
                primary_theme_code="",
                primary_theme_name="",
                confidence=0.0,
                confidence_breakdown=ConfidenceBreakdown(reason="无匹配主题"),
            )

        primary_code, (primary_confidence, primary_breakdown) = sorted_themes[0]
        primary_name = self.theme_config.get(primary_code, {}).get("name_cn", primary_code)

        # ── Step 5: 提取次要主题 ──────────────────────
        all_scores = {code: conf for code, (conf, _) in theme_confidences.items()}
        secondary_codes = self._extract_secondary_themes(all_scores, primary_code)

        # ── Step 6: 确定产业链和概念标签 ──────────────
        industry_chain_codes = self._determine_industry_chain(stock_data, primary_code)
        concept_tag_codes = self._determine_concept_tags(stock_data)

        # ── Step 7: 龙头类型判定（简易） ──────────────
        leader_type = self._determine_leader_type(
            stock_data, primary_code, primary_confidence,
        )

        return ClassificationResult(
            stock_code=stock_code,
            stock_name=stock_name,
            primary_theme_code=primary_code,
            primary_theme_name=primary_name,
            confidence=round(primary_confidence, 2),
            confidence_breakdown=primary_breakdown,
            secondary_theme_codes=secondary_codes,
            industry_chain_codes=industry_chain_codes,
            concept_tag_codes=concept_tag_codes,
            leader_type=leader_type,
        )

    # ──────────────────────────────────────────────
    # 策略执行
    # ──────────────────────────────────────────────

    def _run_all_strategies(self, stock_data: Dict) -> Dict[str, Dict[str, float]]:
        """并行执行所有匹配策略，收集各主题的原始匹配分数.

        Returns:
            {theme_code: {strategy_name: score}} 格式的原始分数.
        """
        # 概念匹配
        concept_scores = self._match_by_concepts(stock_data)

        # 行业匹配
        industry_scores = self._match_by_industry(stock_data)

        # 关键词匹配
        keyword_scores = self._match_by_keywords(stock_data)

        # 业务描述匹配
        biz_scores = self._match_by_business_description(stock_data)

        # ETF 相关性
        etf_scores = self._compute_etf_correlation(stock_data)

        # 合并所有候选主题
        all_candidates: set[str] = set()
        for score_map in [concept_scores, industry_scores, keyword_scores, biz_scores, etf_scores]:
            all_candidates.update(score_map.keys())

        # 构建原始分数字典
        result: Dict[str, Dict[str, float]] = {}
        for theme_code in all_candidates:
            result[theme_code] = {
                "concept_match": concept_scores.get(theme_code, 0.0),
                "industry_match": industry_scores.get(theme_code, 0.0),
                "keyword_match": keyword_scores.get(theme_code, 0.0),
                "business_description": biz_scores.get(theme_code, 0.0),
                "etf_correlation": etf_scores.get(theme_code, 0.0),
            }

        return result

    def _match_by_concepts(self, stock_data: Dict) -> Dict[str, float]:
        """概念匹配策略."""
        eastmoney = stock_data.get("eastmoney_concepts", [])
        ths = stock_data.get("ths_concepts", [])
        all_concepts = list(dict.fromkeys(eastmoney + ths))
        if not all_concepts:
            return {}

        results = self.keyword_engine.match_by_concepts(all_concepts)
        return {r.theme_code: r.score for r in results}

    def _match_by_industry(self, stock_data: Dict) -> Dict[str, float]:
        """行业匹配策略."""
        sw_industry = stock_data.get("sw_industry")
        cx_industry = stock_data.get("cx_industry")
        if not sw_industry and not cx_industry:
            return {}

        results = self.keyword_engine.match_by_industry(
            sw_industry=sw_industry,
            cx_industry=cx_industry,
        )
        return {r.theme_code: r.score for r in results if r.score > 0}

    def _match_by_keywords(self, stock_data: Dict) -> Dict[str, float]:
        """关键词匹配策略."""
        texts: List[str] = []
        if stock_data.get("business_description"):
            texts.append(stock_data["business_description"])
        if stock_data.get("products"):
            texts.extend(str(p) for p in stock_data["products"])
        if stock_data.get("supply_chain_position"):
            texts.append(stock_data["supply_chain_position"])
        if stock_data.get("customers"):
            texts.extend(str(c) for c in stock_data["customers"])

        if not texts:
            return {}

        results = self.keyword_engine.match_by_keywords(texts)
        return {r.theme_code: r.score for r in results}

    def _match_by_business_description(self, stock_data: Dict) -> Dict[str, float]:
        """业务描述匹配策略."""
        description = stock_data.get("business_description", "")
        if not description:
            return {}

        results = self.keyword_engine.match_by_business_description(description)
        return {r.theme_code: r.score for r in results}

    def _compute_etf_correlation(self, stock_data: Dict) -> Dict[str, float]:
        """计算 ETF 相关性得分.

        简化版本：检查股票所属行业是否与主题的 ETF 行业标签匹配.

        Returns:
            {theme_code: correlation_score} 得分 0-100.
        """
        sw_industry = stock_data.get("sw_industry", "")
        cx_industry = stock_data.get("cx_industry", "")

        if not sw_industry and not cx_industry:
            return {}

        result: Dict[str, float] = {}
        etf_mapping = self.scorer._etf_mapping

        for theme_code, info in etf_mapping.items():
            if theme_code == "_meta":
                continue
            tags = info.get("etf_industry_tags", [])
            if not tags:
                continue

            # 直接匹配
            for tag in tags:
                if sw_industry and tag in sw_industry:
                    result[theme_code] = 90.0
                    break
                if cx_industry and tag in cx_industry:
                    result[theme_code] = 85.0
                    break
            else:
                # 子行业匹配
                for tag in tags:
                    if sw_industry and sw_industry in tag:
                        result[theme_code] = 60.0
                        break
                    if cx_industry and cx_industry in tag:
                        result[theme_code] = 55.0
                        break
                else:
                    # 关键词重叠
                    overlap_score = ConfidenceScorer._keyword_overlap_score(
                        [sw_industry, cx_industry], tags,
                    )
                    result[theme_code] = min(overlap_score, 30.0)

        return result

    # ──────────────────────────────────────────────
    # 置信度处理
    # ──────────────────────────────────────────────

    def _compute_confidence(
        self,
        theme_code: str,
        match_results: Dict[str, float],
        stock_data: Dict,
    ) -> Tuple[float, ConfidenceBreakdown]:
        """计算特定主题的置信度.

        Args:
            theme_code: 主题代码.
            match_results: 各策略匹配得分 {strategy: score}.
            stock_data: 股票数据.

        Returns:
            (置信度总分, 分解详情).
        """
        return self.scorer.score(theme_code, stock_data)

    def _apply_exclude_penalty(
        self,
        theme_confidences: Dict[str, Tuple[float, ConfidenceBreakdown]],
        stock_data: Dict,
        penalty_rate: float = 0.30,
    ) -> None:
        """对匹配到排除关键词的主题降低置信度.

        Args:
            theme_confidences: 主题置信度字典（原地修改）.
            stock_data: 股票数据.
            penalty_rate: 惩罚比例（0-1），默认 30%.
        """
        # 收集所有文本
        texts: List[str] = []
        if stock_data.get("business_description"):
            texts.append(stock_data["business_description"])
        if stock_data.get("products"):
            texts.extend(str(p) for p in stock_data["products"])
        if stock_data.get("supply_chain_position"):
            texts.append(stock_data["supply_chain_position"])
        if stock_data.get("customers"):
            texts.extend(str(c) for c in stock_data["customers"])
        if stock_data.get("institution_reports"):
            texts.extend(str(r) for r in stock_data["institution_reports"])

        if not texts:
            return

        combined = " ".join(texts)

        for theme_code, (conf, breakdown) in theme_confidences.items():
            exclude_set = self.keyword_engine.get_exclude_keywords(theme_code)
            if not exclude_set:
                continue

            # 检查排除关键词是否出现在文本中
            has_exclude = any(ekw in combined for ekw in exclude_set)
            if has_exclude:
                new_conf = conf * (1.0 - penalty_rate)
                theme_confidences[theme_code] = (new_conf, breakdown)

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _extract_secondary_themes(
        self,
        all_scores: Dict[str, float],
        primary_theme: str,
    ) -> List[str]:
        """提取次要主题.

        筛选规则：置信度 > 20 且非 Primary Theme，最多返回 3 个，
        按置信度降序排列.

        Args:
            all_scores: {theme_code: confidence} 所有主题置信度.
            primary_theme: 主属主题代码.

        Returns:
            次要主题代码列表.
        """
        candidates = [
            (code, conf)
            for code, conf in all_scores.items()
            if code != primary_theme and conf > 20.0
        ]
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [code for code, _ in candidates[:3]]

    def _determine_industry_chain(
        self,
        stock_data: Dict,
        primary_theme: str,
    ) -> List[str]:
        """确定股票在 Primary Theme 中的产业链归属.

        将股票的产品与业务描述和主题的产业链节点名进行关键词匹配.

        Args:
            stock_data: 股票数据.
            primary_theme: 主属主题代码.

        Returns:
            匹配的产业链代码/名称列表.
        """
        theme = self.theme_config.get(primary_theme, {})
        chain_nodes = theme.get("industry_chains", [])

        if not chain_nodes:
            return []

        # 收集股票文本
        texts: List[str] = []
        if stock_data.get("products"):
            texts.extend(str(p) for p in stock_data["products"])
        if stock_data.get("business_description"):
            texts.append(stock_data["business_description"])
        if stock_data.get("supply_chain_position"):
            texts.append(stock_data["supply_chain_position"])

        combined = " ".join(texts)

        matched: List[str] = []
        for node in chain_nodes:
            if node in combined:
                matched.append(node)

        return matched[:5]  # 最多返回 5 个

    def _determine_concept_tags(self, stock_data: Dict) -> List[str]:
        """确定股票关联的概念标签.

        匹配 stock 的东方财富/同花顺概念标签与 theme_config 中定义的
        概念标签.

        Returns:
            概念标签代码/名称列表.
        """
        eastmoney = stock_data.get("eastmoney_concepts", [])
        ths = stock_data.get("ths_concepts", [])
        all_concepts = list(dict.fromkeys(eastmoney + ths))

        if not all_concepts:
            return []

        # 从 theme_config 收集所有概念标签
        all_theme_concepts: Dict[str, str] = {}  # concept_name -> theme_code
        for code, theme in self.theme_config.items():
            em = theme.get("eastmoney_concepts", [])
            th = theme.get("ths_concepts", [])
            combined = list(dict.fromkeys(em + th))
            for c in combined:
                if c not in all_theme_concepts:
                    all_theme_concepts[c] = code

        matched: List[str] = []
        for concept in all_concepts:
            if concept in all_theme_concepts:
                matched.append(concept)

        return matched[:10]  # 最多返回 10 个

    @staticmethod
    def _determine_leader_type(
        stock_data: Dict,
        primary_theme: str,
        confidence: float,
    ) -> Optional[str]:
        """简易龙头类型判定.

        根据置信度和指定字段判断龙头类型.
        子类可以重写此方法实现更复杂的逻辑.

        Args:
            stock_data: 股票数据.
            primary_theme: 主属主题代码.
            confidence: 置信度得分.

        Returns:
            龙头类型或 None.
        """
        is_leader = stock_data.get("is_leader", False)
        leader_type = stock_data.get("leader_type", "")

        if leader_type:
            return leader_type
        if is_leader:
            return "leader"
        if confidence >= 80:
            return "core"
        if confidence >= 60:
            return "follower"
        return None

    # ──────────────────────────────────────────────
    # 批量分类
    # ──────────────────────────────────────────────

    def batch_classify(
        self,
        stocks_data: List[Dict[str, Any]],
        max_workers: Optional[int] = None,
    ) -> List[ClassificationResult]:
        """批量分类多只股票.

        使用线程池并行处理.

        Args:
            stocks_data: 股票数据字典列表.
            max_workers: 最大并行线程数，默认使用 CLASSIFIER_WORKERS 配置.

        Returns:
            分类结果列表，顺序与输入一致.
        """
        workers = max_workers or CLASSIFIER_WORKERS
        results: List[ClassificationResult] = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self.classify, stock_data): idx
                for idx, stock_data in enumerate(stocks_data)
            }

            # 按原始顺序收集结果
            ordered_results: Dict[int, ClassificationResult] = {}
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    result = future.result()
                    ordered_results[idx] = result
                except Exception as e:
                    stock_code = stocks_data[idx].get("stock_code", "unknown")
                    logger.error(
                        "分类失败 stock=%s idx=%d: %s",
                        stock_code, idx, e,
                    )
                    ordered_results[idx] = ClassificationResult(
                        stock_code=stock_code,
                        stock_name=stocks_data[idx].get("stock_name", ""),
                        primary_theme_code="",
                        primary_theme_name="",
                        confidence=0.0,
                        confidence_breakdown=ConfidenceBreakdown(
                            reason=f"分类异常: {e}",
                        ),
                    )

            results = [ordered_results[i] for i in range(len(stocks_data))]

        return results
