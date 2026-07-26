"""关键词引擎 - TF-IDF + 多策略关键词匹配."""

from __future__ import annotations

import json
import re
import math
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class KeywordMatchResult:
    """关键词匹配结果数据类."""

    theme_code: str
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    match_type: str = "exact"
    keyword_count: int = 0


class KeywordEngine:
    """关键词引擎，支持 TF-IDF + 多策略匹配.

    基于 theme_config.json 构建关键词倒排索引，提供关键词、概念、
    行业、业务描述等多种匹配策略，用于 A 股股票主题分类。
    """

    def __init__(self, config_path: Path | str) -> None:
        """初始化关键词引擎，加载主题配置和行业映射数据.

        Args:
            config_path: theme_config.json 的路径（文件或所在目录）.
        """
        config_path = Path(config_path)
        if config_path.is_dir():
            config_path = config_path / "theme_config.json"

        with open(config_path, encoding="utf-8") as f:
            self.theme_config: dict = json.load(f)

        # 尝试加载行业映射表
        self.sw_industry_mapping: dict = {}
        mapping_candidates = [
            config_path.parent / "sw_industry_mapping.json",
            config_path.parent.parent / "data" / "sw_industry_mapping.json",
            Path("theme_kg_v3/data/sw_industry_mapping.json"),
        ]
        for candidate in mapping_candidates:
            if candidate.exists():
                with open(candidate, encoding="utf-8") as f:
                    self.sw_industry_mapping = json.load(f)
                break

        # 主题代码列表
        self.theme_codes: list[str] = list(self.theme_config.keys())

        # 倒排索引: keyword -> set[theme_code]
        self.inverted_index: dict[str, set[str]] = {}
        self.build_inverted_index()

        # 预计算各主题的关键词集合，供快速访问
        self._theme_keywords_cache: dict[str, dict[str, list[str]]] = {}
        self._theme_concepts_cache: dict[str, list[str]] = {}
        self._theme_exclude_cache: dict[str, set[str]] = {}

    # ──────────────────────────────────────────────
    # 倒排索引
    # ──────────────────────────────────────────────

    def build_inverted_index(self) -> None:
        """构建 keyword -> theme_codes 倒排索引.

        遍历所有主题的 keywords / core_keywords / industry_keywords /
        product_keywords / concept_keywords 等字段，建立倒排映射。
        """
        self.inverted_index.clear()
        keyword_fields = [
            "keywords", "core_keywords", "industry_keywords",
            "product_keywords", "concept_keywords", "brand_keywords",
        ]
        for code, theme in self.theme_config.items():
            seen = set()
            for field in keyword_fields:
                kw_list = theme.get(field, [])
                if isinstance(kw_list, list):
                    for kw in kw_list:
                        kw = kw.strip()
                        if kw and kw not in seen:
                            seen.add(kw)
                            if kw not in self.inverted_index:
                                self.inverted_index[kw] = set()
                            self.inverted_index[kw].add(code)

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _get_theme_keywords(self, theme_code: str) -> dict[str, list[str]]:
        """获取主题所有关键词（按类型分组）."""
        if theme_code not in self._theme_keywords_cache:
            theme = self.theme_config.get(theme_code, {})
            self._theme_keywords_cache[theme_code] = {
                "keywords": theme.get("keywords", []),
                "core_keywords": theme.get("core_keywords", []),
                "industry_keywords": theme.get("industry_keywords", []),
                "product_keywords": theme.get("product_keywords", []),
                "concept_keywords": theme.get("concept_keywords", []),
                "brand_keywords": theme.get("brand_keywords", []),
            }
        return self._theme_keywords_cache[theme_code]

    def _get_theme_concepts(self, theme_code: str) -> list[str]:
        """获取主题的概念标签（东方财富 + 同花顺）."""
        if theme_code not in self._theme_concepts_cache:
            theme = self.theme_config.get(theme_code, {})
            em = theme.get("eastmoney_concepts", [])
            ths = theme.get("ths_concepts", [])
            # 去重合并
            combined = list(dict.fromkeys(em + ths))
            self._theme_concepts_cache[theme_code] = combined
        return self._theme_concepts_cache[theme_code]

    def get_exclude_keywords(self, theme_code: str) -> set[str]:
        """返回主题的排除关键词集合."""
        if theme_code not in self._theme_exclude_cache:
            theme = self.theme_config.get(theme_code, {})
            exc = theme.get("exclude_keywords", [])
            self._theme_exclude_cache[theme_code] = set(exc)
        return self._theme_exclude_cache[theme_code]

    # ──────────────────────────────────────────────
    # N-gram 分词
    # ──────────────────────────────────────────────

    @staticmethod
    def _chinese_ngrams(text: str, min_n: int = 2, max_n: int = 4) -> set[str]:
        """对中文文本提取字符级 n-gram.

        Args:
            text: 输入文本.
            min_n: 最小 gram 长度.
            max_n: 最大 gram 长度.

        Returns:
            n-gram 集合.
        """
        # 只保留中文字符
        chars = re.findall(r"[\u4e00-\u9fff]", text)
        ngrams: set[str] = set()
        for n in range(min_n, max_n + 1):
            for i in range(len(chars) - n + 1):
                ngrams.add("".join(chars[i : i + n]))
        return ngrams

    @staticmethod
    def _tokenize_text(text: str) -> list[str]:
        """对文本进行 token 化，保留中英文词和数字.

        Returns:
            中文字段按字符 n-gram 展开，英文/数字保留原样.
        """
        # 分离中英文和数字
        tokens: list[str] = []
        # 匹配英文单词、数字、中文字符序列
        parts = re.findall(r"[a-zA-Z0-9\.\+\-]+|[\u4e00-\u9fff]+", text)
        for part in parts:
            if re.match(r"^[\u4e00-\u9fff]+$", part):
                # 中文部分：提取 2-gram 和 3-gram
                ngrams = KeywordEngine._chinese_ngrams(part, 2, 3)
                tokens.extend(ngrams)
                # 也保留原始短语（若长度 >= 2）
                if len(part) >= 2:
                    tokens.append(part)
            else:
                tokens.append(part.lower())
        return tokens

    # ──────────────────────────────────────────────
    # TF-IDF 计算
    # ──────────────────────────────────────────────

    def compute_tfidf(self, texts: list[str]) -> dict[str, float]:
        """计算简单 TF-IDF 分值.

        TF = 词项在 texts 中的频次
        IDF = log(total_themes / (1 + themes_containing_term))

        Args:
            texts: 输入文本列表.

        Returns:
            词项 -> TF-IDF 分值 的字典.
        """
        total_themes = len(self.theme_codes) if self.theme_codes else 1

        # 合并所有文本并 tokenize
        all_tokens: list[str] = []
        for text in texts:
            all_tokens.extend(self._tokenize_text(text))

        # TF
        tf = Counter(all_tokens)
        max_tf = max(tf.values()) if tf else 1

        # IDF: 计算每个词出现在多少个主题中
        term_to_themes: dict[str, set[str]] = {}
        for term in tf:
            term_to_themes[term] = set()
            for code in self.theme_codes:
                kw_dict = self._get_theme_keywords(code)
                # 扁平化所有关键词
                all_kws: list[str] = []
                for kw_list in kw_dict.values():
                    all_kws.extend(kw_list)
                if any(term in kw or kw in term for kw in all_kws):
                    term_to_themes[term].add(code)

        # TF-IDF
        result: dict[str, float] = {}
        for term, count in tf.items():
            tf_score = count / max_tf
            themes_with_term = len(term_to_themes.get(term, set()))
            idf = math.log((total_themes + 1) / (1 + themes_with_term)) + 1
            result[term] = round(tf_score * idf, 4)

        return result

    # ──────────────────────────────────────────────
    # 关键词匹配
    # ──────────────────────────────────────────────

    def match_by_keywords(
        self,
        texts: list[str],
        theme_codes: Optional[list[str]] = None,
    ) -> list[KeywordMatchResult]:
        """通过关键词匹配文本，返回主题匹配结果.

        对每个输入文本进行 n-gram 分词和精确短语匹配，
        core_keywords 权重为 2x，命中 exclude_keywords 扣 50 分.

        Score = min(100, (matched_weighted_count / total_theme_keywords) * 100
                     - exclude_penalty)

        Args:
            texts: 输入文本列表（如股票主营业务、新闻标题等）.
            theme_codes: 限定匹配的主题代码列表，None 表示匹配所有.

        Returns:
            按 score 降序排列的匹配结果列表.
        """
        candidates = theme_codes or self.theme_codes
        results: list[KeywordMatchResult] = []

        # 合并所有文本并 tokenize
        combined_tokens: set[str] = set()
        combined_phrases: list[str] = []
        for text in texts:
            combined_tokens.update(self._tokenize_text(text))
            # 保留原始文本用于精确短语匹配
            combined_phrases.append(text)

        for code in candidates:
            kw_dict = self._get_theme_keywords(code)
            exclude_set = self.get_exclude_keywords(code)

            # 扁平化所有关键词并记录类型
            all_kw_info: list[tuple[str, bool]] = []  # (keyword, is_core)
            for kw in kw_dict.get("keywords", []):
                all_kw_info.append((kw, False))
            for kw in kw_dict.get("core_keywords", []):
                all_kw_info.append((kw, True))
            for kw in kw_dict.get("industry_keywords", []):
                all_kw_info.append((kw, False))
            for kw in kw_dict.get("product_keywords", []):
                all_kw_info.append((kw, False))
            for kw in kw_dict.get("concept_keywords", []):
                all_kw_info.append((kw, False))
            for kw in kw_dict.get("brand_keywords", []):
                all_kw_info.append((kw, False))

            if not all_kw_info:
                continue

            total_weight = 0
            matched_weight = 0
            matched_kws: list[str] = []
            exclude_hits = 0

            # 为每个关键词分配权重
            for kw, is_core in all_kw_info:
                weight = 2.0 if is_core else 1.0
                total_weight += weight

                # 检查是否在 exclude 中
                if kw in exclude_set:
                    continue

                # 精确短语匹配（直接出现在文本中）
                phrase_match = any(kw in phrase for phrase in combined_phrases)
                # token 匹配（n-gram 级别）
                token_match = kw in combined_tokens

                if phrase_match or token_match:
                    matched_weight += weight
                    matched_kws.append(kw)

                # 如果 keyword 不在 tokens 中，尝试逆向 n-gram 匹配
                # 即 keyword 中包含文本中的某些 n-gram
                if not phrase_match and not token_match:
                    kw_tokens = self._tokenize_text(kw)
                    if combined_tokens & set(kw_tokens):
                        matched_weight += weight * 0.5
                        matched_kws.append(kw)

            # 排除关键词扣分
            for text in texts:
                for ekw in exclude_set:
                    if ekw in text:
                        exclude_hits += 1

            # 计算分数
            score = (matched_weight / total_weight) * 100 if total_weight > 0 else 0
            score -= exclude_hits * 50
            score = max(0.0, min(100.0, score))

            if matched_kws or score > 0:
                results.append(
                    KeywordMatchResult(
                        theme_code=code,
                        score=round(score, 2),
                        matched_keywords=matched_kws,
                        match_type="exact",
                        keyword_count=len(matched_kws),
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    # ──────────────────────────────────────────────
    # 概念匹配
    # ──────────────────────────────────────────────

    def match_by_concepts(
        self,
        concepts: list[str],
        theme_codes: Optional[list[str]] = None,
    ) -> list[KeywordMatchResult]:
        """通过概念标签匹配主题.

        Score = (matched_concepts / total_theme_concepts) * 100

        Args:
            concepts: 概念标签列表（如东方财富概念、同花顺概念）.
            theme_codes: 限定匹配的主题代码列表.

        Returns:
            按 score 降序排列的匹配结果列表.
        """
        candidates = theme_codes or self.theme_codes
        results: list[KeywordMatchResult] = []

        concept_set = set(concepts)

        for code in candidates:
            theme_concepts = self._get_theme_concepts(code)
            if not theme_concepts:
                continue

            # 计算匹配的概念
            matched = [c for c in theme_concepts if c in concept_set]

            score = (len(matched) / len(theme_concepts)) * 100 if theme_concepts else 0
            score = max(0.0, min(100.0, score))

            results.append(
                KeywordMatchResult(
                    theme_code=code,
                    score=round(score, 2),
                    matched_keywords=matched,
                    match_type="concept",
                    keyword_count=len(matched),
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    # ──────────────────────────────────────────────
    # 行业匹配
    # ──────────────────────────────────────────────

    def match_by_industry(
        self,
        sw_industry: Optional[str] = None,
        cx_industry: Optional[str] = None,
    ) -> list[KeywordMatchResult]:
        """通过申万/中信行业分类匹配主题.

        行业匹配逻辑:
        - 主题的 sw_industry_match / cx_industry_match 列表包含输入行业 => 80 分
        - 在 sw_industry_mapping 的二级行业中匹配 => 50 分
        - 不匹配 => 0 分

        Args:
            sw_industry: 申万行业分类（如 "电子", "计算机"）.
            cx_industry: 中信行业分类（如 "电子", "计算机"）.

        Returns:
            按 score 降序排列的匹配结果列表.
        """
        results: list[KeywordMatchResult] = []

        for code in self.theme_codes:
            theme = self.theme_config.get(code, {})
            score = 0.0
            match_type = "industry"

            # 一级行业匹配（取最高分）
            sw_match_list = theme.get("sw_industry_match", [])
            cx_match_list = theme.get("cx_industry_match", [])

            if sw_industry and sw_industry in sw_match_list:
                score = 80.0
            elif cx_industry and cx_industry in cx_match_list:
                score = 80.0
            else:
                # 二级行业匹配：在 sw_industry_mapping 中查找
                if self.sw_industry_mapping:
                    secondary_map = self.sw_industry_mapping.get("二级行业", {})
                    # 检查 sw_industry 是否作为二级行业匹配
                    if sw_industry:
                        mapped_themes = secondary_map.get(sw_industry, [])
                        if code in mapped_themes:
                            score = 50.0
                    # 检查 cx_industry 是否作为二级行业匹配
                    if score == 0 and cx_industry:
                        mapped_themes = secondary_map.get(cx_industry, [])
                        if code in mapped_themes:
                            score = 50.0

            results.append(
                KeywordMatchResult(
                    theme_code=code,
                    score=score,
                    matched_keywords=(
                        [sw_industry] if sw_industry
                        else [cx_industry] if cx_industry
                        else []
                    ),
                    match_type=match_type,
                    keyword_count=1 if score > 0 else 0,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    # ──────────────────────────────────────────────
    # 业务描述匹配
    # ──────────────────────────────────────────────

    def match_by_business_description(
        self,
        description: str,
    ) -> list[KeywordMatchResult]:
        """通过业务描述匹配主题.

        对业务描述进行 n-gram 分词，与每个主题的关键词进行匹配.
        Score = (matched_chars / total_chars_in_description) * 100

        Args:
            description: 业务描述文本.

        Returns:
            按 score 降序排列的匹配结果列表.
        """
        if not description or not description.strip():
            return []

        results: list[KeywordMatchResult] = []

        # 提取描述中的中文字符
        desc_chars = re.findall(r"[\u4e00-\u9fff]", description)
        total_chars = len(desc_chars)
        if total_chars == 0:
            return []

        # 对描述提取 n-gram
        desc_ngrams = self._chinese_ngrams(description, 2, 4)

        for code in self.theme_codes:
            kw_dict = self._get_theme_keywords(code)
            exclude_set = self.get_exclude_keywords(code)

            # 扁平化所有关键词
            all_kws: list[str] = []
            for kw_list in kw_dict.values():
                all_kws.extend(kw_list)

            if not all_kws:
                continue

            matched_kws: list[str] = []
            matched_chars: set[int] = set()  # 记录匹配到 description 中的字符下标
            exclude_hits = 0

            for kw in all_kws:
                if kw in exclude_set:
                    # 检查排除关键词是否出现在描述中
                    if kw in description:
                        exclude_hits += 1
                    continue

                # 直接在描述中查找关键词
                idx = description.find(kw)
                if idx != -1:
                    matched_kws.append(kw)
                    # 记录匹配到的字符范围（近似）
                    for ci in range(idx, idx + len(kw)):
                        matched_chars.add(ci)
                else:
                    # 尝试 n-gram 匹配 -> 关键词的 n-gram 是否与描述的 n-gram 重叠
                    kw_ngrams = self._chinese_ngrams(kw, 2, 3)
                    overlap = desc_ngrams & kw_ngrams
                    if overlap:
                        matched_kws.append(kw)

            # 计算分数：匹配字符占比
            # 如果用了 n-gram 模糊匹配，按关键词占比计算
            total_kw_count = len(all_kws) - len(exclude_set & set(all_kws))
            if total_kw_count <= 0:
                continue

            # 综合评分 = 匹配关键词占比
            score = (len(matched_kws) / total_kw_count) * 100
            # 排除关键词扣分
            score -= exclude_hits * 50
            score = max(0.0, min(100.0, score))

            results.append(
                KeywordMatchResult(
                    theme_code=code,
                    score=round(score, 2),
                    matched_keywords=matched_kws,
                    match_type="fuzzy",
                    keyword_count=len(matched_kws),
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    # ──────────────────────────────────────────────
    # 快速匹配
    # ──────────────────────────────────────────────

    def quick_match(self, text: str) -> list[KeywordMatchResult]:
        """单文本快速匹配所有主题，用于实时分类.

        内部调用 match_by_keywords 并合并精确/模糊结果.

        Args:
            text: 输入文本.

        Returns:
            按 score 降序排列的匹配结果列表.
        """
        # 先用关键词精确匹配
        results = self.match_by_keywords([text])

        # 对 score 为零或很低的主题补充业务描述匹配
        high_score_codes = {r.theme_code for r in results if r.score > 10}
        desc_results = self.match_by_business_description(text)

        # 合并结果：对已有高分结果保留，对未覆盖的主题补充
        result_map: dict[str, KeywordMatchResult] = {r.theme_code: r for r in results}

        for dr in desc_results:
            if dr.theme_code in result_map:
                # 如果已有结果且分数更高则跳过
                existing = result_map[dr.theme_code]
                if dr.score > existing.score:
                    existing.score = dr.score
                    existing.matched_keywords = list(
                        dict.fromkeys(existing.matched_keywords + dr.matched_keywords)
                    )
                    existing.keyword_count = len(existing.matched_keywords)
                    existing.match_type = "fuzzy" if dr.score > existing.score else existing.match_type
            else:
                result_map[dr.theme_code] = dr

        final_results = sorted(result_map.values(), key=lambda r: r.score, reverse=True)
        return final_results
