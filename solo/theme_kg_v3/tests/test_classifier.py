"""测试 - 自动归类算法 & Theme Confidence 评分."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

# 确保项目根在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from theme_kg_v3.core.keyword_engine import KeywordEngine
from theme_kg_v3.core.classifier import ThemeClassifier
from theme_kg_v3.core.confidence import ConfidenceScorer
from theme_kg_v3.config.settings import THEME_CONFIG_PATH, DATA_DIR


class TestKeywordEngine(unittest.TestCase):
    """关键词引擎单元测试."""

    @classmethod
    def setUpClass(cls):
        cls.engine = KeywordEngine(THEME_CONFIG_PATH)
        cls.sample_data_path = DATA_DIR / "sample_stocks.json"
        with open(cls.sample_data_path, "r", encoding="utf-8") as f:
            cls.sample_stocks: List[Dict[str, Any]] = json.load(f)

    def test_engine_loads_successfully(self):
        """引擎能正确加载主题配置."""
        self.assertIsNotNone(self.engine.theme_config)
        self.assertGreater(len(self.engine.theme_codes), 0)
        self.assertIn("AI_COMPUTE", self.engine.theme_codes)
        self.assertIn("SEMICONDUCTOR", self.engine.theme_codes)

    def test_inverted_index_built(self):
        """倒排索引已构建."""
        self.assertIsNotNone(self.engine.inverted_index)
        # 检查是否有常见关键词
        has_semiconductor_keyword = any(
            "半导体" in kw for kw in self.engine.inverted_index.keys()
        )
        self.assertTrue(has_semiconductor_keyword, "倒排索引应包含'半导体'相关关键词")

    def test_quick_match_returns_results(self):
        """quick_match 返回非空结果."""
        results = self.engine.quick_match("光模块 800G AI数据中心")
        self.assertGreater(len(results), 0)
        # AI算力应该匹配
        ai_scores = [r for r in results if r.theme_code == "AI_COMPUTE"]
        self.assertGreater(len(ai_scores), 0, "AI算力应匹配光模块关键词")
        self.assertGreater(ai_scores[0].score, 0, "AI算力匹配分应大于0")

    def test_exclude_keywords_filtering(self):
        """排除关键词正确返回."""
        exclude_set = self.engine.get_exclude_keywords("INNOVATIVE_DRUG")
        self.assertIsInstance(exclude_set, set)

    def test_match_by_industry(self):
        """行业匹配返回正确主题."""
        results = self.engine.match_by_industry("电子", "半导体")
        self.assertGreater(len(results), 0)
        theme_codes = [r.theme_code for r in results]
        self.assertIn("SEMICONDUCTOR", theme_codes)

    def test_match_by_concepts(self):
        """概念匹配返回正确主题."""
        results = self.engine.match_by_concepts(["CPO", "光模块", "算力"])
        ai_codes = [r for r in results if r.theme_code == "AI_COMPUTE"]
        self.assertGreater(len(ai_codes), 0)
        self.assertGreater(ai_codes[0].score, 0)

    def test_match_by_business_description(self):
        """业务描述匹配."""
        desc = "公司主要生产高密度PCB，用于服务器和数据中心"
        results = self.engine.match_by_business_description(desc)
        pcb_themes = [r for r in results if r.theme_code == "AI_COMPUTE"]
        self.assertGreater(len(pcb_themes), 0, "PCB业务描述应匹配AI算力")

    def test_keyword_tfidf(self):
        """TF-IDF 计算能正常执行."""
        texts = [
            "AI算力GPU光模块服务器数据中心",
            "半导体芯片晶圆代工刻蚀设备",
            "创新药ADC双抗临床实验",
            "机器人减速器伺服电机传感器",
        ]
        tfidf = self.engine.compute_tfidf(texts)
        self.assertIsInstance(tfidf, dict)
        self.assertGreater(len(tfidf), 0)


class TestConfidenceScorer(unittest.TestCase):
    """置信度评分单元测试."""

    @classmethod
    def setUpClass(cls):
        cls.engine = KeywordEngine(THEME_CONFIG_PATH)
        cls.scorer = ConfidenceScorer(cls.engine)

    def test_scorer_initialization(self):
        """评分器正确初始化."""
        self.assertIsNotNone(self.scorer.weights)
        self.assertIn("etf_correlation", self.scorer.weights)
        self.assertIn("industry_match", self.scorer.weights)

    def test_score_etf_correlation(self):
        """ETF相关性评分."""
        stock_data = {
            "sw_industry": "电子",
            "sw_sub_industry": "半导体",
        }
        score = self.scorer._score_etf_correlation("SEMICONDUCTOR", stock_data)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_score_industry_match_direct(self):
        """行业直接匹配满分."""
        stock_data = {
            "sw_industry": "电子",
            "sw_sub_industry": "半导体",
            "cx_industry": "电子",
        }
        score = self.scorer._score_industry_match("SEMICONDUCTOR", stock_data)
        self.assertGreaterEqual(score, 80)

    def test_score_concept_match(self):
        """概念匹配评分."""
        stock_data = {
            "eastmoney_concepts": ["CPO", "光模块", "数据中心", "算力"],
            "ths_concepts": ["CPO", "光通信", "算力"],
        }
        score = self.scorer._score_concept_match("AI_COMPUTE", stock_data)
        self.assertGreater(score, 30)

    def test_score_product_match(self):
        """产品匹配评分."""
        stock_data = {"products": ["800G光模块", "1.6T光模块"]}
        score = self.scorer._score_product("AI_COMPUTE", stock_data)
        self.assertGreater(score, 50)

    def test_score_business_description(self):
        """业务描述评分."""
        stock_data = {
            "business_description": "公司专注于光模块的研发和生产，产品用于AI数据中心"
        }
        score = self.scorer._score_business_description("AI_COMPUTE", stock_data)
        self.assertGreater(score, 5)

    def test_score_no_match(self):
        """不匹配的主题得低分."""
        stock_data = {
            "sw_industry": "食品饮料",
            "sw_sub_industry": "白酒",
            "eastmoney_concepts": ["白酒", "消费"],
            "products": ["飞天茅台"],
        }
        score_ai = self.scorer._score_etf_correlation("AI_COMPUTE", stock_data)
        self.assertLessEqual(score_ai, 30)

    def test_full_scoring_pipeline(self):
        """完整评分流程."""
        stock_data = {
            "sw_industry": "电子",
            "sw_sub_industry": "消费电子",
            "cx_industry": "电子",
            "eastmoney_concepts": ["苹果概念", "消费电子", "AI手机"],
            "ths_concepts": ["苹果", "消费电子"],
            "business_description": "消费电子零组件制造",
            "products": ["连接器", "声学模组"],
            "customers": ["苹果", "华为"],
        }
        total, breakdown = self.scorer.score("CONSUMER_ELECTRONICS", stock_data)
        self.assertGreater(total, 30)
        self.assertIsNotNone(breakdown.reason)


class TestThemeClassifier(unittest.TestCase):
    """自动归类算法集成测试."""

    @classmethod
    def setUpClass(cls):
        cls.engine = KeywordEngine(THEME_CONFIG_PATH)
        cls.classifier = ThemeClassifier(cls.engine)
        cls.sample_data_path = DATA_DIR / "sample_stocks.json"
        with open(cls.sample_data_path, "r", encoding="utf-8") as f:
            cls.sample_stocks: List[Dict[str, Any]] = json.load(f)

    def test_classifier_initialization(self):
        """分类器正确初始化."""
        self.assertIsNotNone(self.classifier.scorer)
        self.assertIsNotNone(self.classifier.keyword_engine)

    def test_classify_shenghong(self):
        """胜宏科技PCB业务 - 可能在AI算力、半导体或消费电子之间."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "300476.SZ"]
        if not stock:
            self.skipTest("示例数据中缺少胜宏科技")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "300476.SZ")
        # PCB 跨越多主题，应落在合理主题中
        valid_themes = ["AI_COMPUTE", "SEMICONDUCTOR", "CONSUMER_ELECTRONICS"]
        self.assertIn(result.primary_theme_code, valid_themes)
        self.assertGreater(result.confidence, 20)

    def test_classify_xinyisheng(self):
        """新易盛应归类为AI算力."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "300502.SZ"]
        if not stock:
            self.skipTest("示例数据中缺少新易盛")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "300502.SZ")
        self.assertEqual(result.primary_theme_code, "AI_COMPUTE")

    def test_classify_beifanghuachuang(self):
        """北方华创应归类为半导体."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "002371.SZ"]
        if not stock:
            self.skipTest("示例数据中缺少北方华创")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "002371.SZ")
        self.assertEqual(result.primary_theme_code, "SEMICONDUCTOR")

    def test_classify_maotai(self):
        """贵州茅台应归入消费主题."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "600519.SH"]
        if not stock:
            self.skipTest("示例数据中缺少贵州茅台")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "600519.SH")
        # 茅台通过消费主题匹配（行业食品饮料→消费、概念白酒→消费）
        self.assertEqual(result.primary_theme_code, "CONSUMPTION")
        self.assertGreater(result.confidence, 20)

    def test_classify_ningde(self):
        """宁德时代应归类为新能源车."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "300750.SZ"]
        if not stock:
            self.skipTest("示例数据中缺少宁德时代")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "300750.SZ")
        self.assertEqual(result.primary_theme_code, "NEW_ENERGY_VEHICLE")

    def test_classify_zhongxin(self):
        """中芯国际应归类为半导体."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "688981.SH"]
        if not stock:
            self.skipTest("示例数据中缺少中芯国际")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "688981.SH")
        self.assertEqual(result.primary_theme_code, "SEMICONDUCTOR")

    def test_classify_byd(self):
        """比亚迪主主题应为新能源车（非智能驾驶）. """
        stock = [s for s in self.sample_stocks if s["stock_code"] == "002594.SZ"]
        if not stock:
            self.skipTest("示例数据中缺少比亚迪")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "002594.SZ")
        self.assertEqual(result.primary_theme_code, "NEW_ENERGY_VEHICLE")

    def test_classify_lixun(self):
        """立讯精密应归类为消费电子."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "002475.SZ"]
        if not stock:
            self.skipTest("示例数据中缺少立讯精密")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "002475.SZ")
        self.assertEqual(result.primary_theme_code, "CONSUMER_ELECTRONICS")

    def test_classify_baiji(self):
        """百济神州应归类为创新药."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "688235.SH"]
        if not stock:
            self.skipTest("示例数据中缺少百济神州")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "688235.SH")
        self.assertEqual(result.primary_theme_code, "INNOVATIVE_DRUG")

    def test_classify_yidong(self):
        """中国移动有通信属性但不在任何主题的核心匹配中，得分较低."""
        stock = [s for s in self.sample_stocks if s["stock_code"] == "600941.SH"]
        if not stock:
            self.skipTest("示例数据中缺少中国移动")
        result = self.classifier.classify(stock[0])
        self.assertEqual(result.stock_code, "600941.SH")
        # 中国移动的通信运营属性，可能匹配AI算力或数据要素，但均非核心
        self.assertLess(result.confidence, 80)

    def test_batch_classify(self):
        """批量分类返回所有结果."""
        results = self.classifier.batch_classify(self.sample_stocks)
        self.assertEqual(len(results), len(self.sample_stocks))

    def test_classification_has_breakdown(self):
        """分类结果包含置信度分解."""
        stock = self.sample_stocks[0]
        result = self.classifier.classify(stock)
        self.assertIsNotNone(result.confidence_breakdown)
        breakdown = result.confidence_breakdown
        self.assertGreaterEqual(breakdown.total_score, 0)
        self.assertIsInstance(breakdown.reason, str)

    def test_classification_has_industry_chains(self):
        """分类结果包含产业链匹配."""
        stock = self.sample_stocks[0]
        result = self.classifier.classify(stock)
        # 如果有产业链匹配结果
        if result.industry_chain_codes:
            self.assertIsInstance(result.industry_chain_codes, list)

    def test_classification_has_concept_tags(self):
        """分类结果包含概念标签."""
        stock = self.sample_stocks[0]
        result = self.classifier.classify(stock)
        # 如果有概念标签匹配
        if result.concept_tag_codes:
            self.assertIsInstance(result.concept_tag_codes, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
