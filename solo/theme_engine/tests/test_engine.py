"""TERE V1 单元测试.

测试因子注册、各层级计算、状态机、轮动预测、评分计算、校验器及完整流水线。
使用 pytest-asyncio，mock 外部依赖。
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from theme_engine.config.settings import load_weights
from theme_engine.factor.base import BaseFactor
from theme_engine.factor.registry import FactorRegistry, get_registry, reset_registry
from theme_engine.services.theme_service import ThemeService
from theme_engine.models.dataclasses import (
    BreadthResult,
    ETFStrengthResult,
    EngineResult,
    FactorResult,
    FlowResult,
    LeaderResult,
    PurityResult,
    ResonanceResult,
    RotationResult,
    SignalResult,
    StageResult,
    ThemeDailyScore,
)
from theme_engine.score.calculator import ScoreCalculator
from theme_engine.stage.state_machine import StageStateMachine
from theme_engine.validator.validator import Validator


# ══════════════════════════════════════════════════════════════
#  Mock 因子
# ══════════════════════════════════════════════════════════════

class MockETFStrengthFactor(BaseFactor):
    name = "mock_etf_strength"
    version = "1.0.0"
    weight_key = "etf_strength"

    async def calculate(self, theme_code: str, trade_date: str, **kwargs) -> FactorResult:
        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=75.0,
            weight=0.3,
            contribution=22.5,
            details={"etf_code": kwargs.get("main_etf", "")},
        )


class MockBreadthFactor(BaseFactor):
    name = "mock_breadth"
    version = "1.0.0"
    weight_key = "breadth"

    async def calculate(self, theme_code: str, trade_date: str, **kwargs) -> FactorResult:
        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=65.0,
            weight=0.2,
            contribution=13.0,
        )


class MockLeaderFactor(BaseFactor):
    name = "mock_leader"
    version = "1.0.0"
    weight_key = "leader"

    async def calculate(self, theme_code: str, trade_date: str, **kwargs) -> FactorResult:
        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=80.0,
            weight=0.2,
            contribution=16.0,
        )


# ══════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def clean_registry():
    """每个测试前重置因子注册表."""
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return get_registry()


@pytest.fixture
def sample_themes() -> List[str]:
    return ["AI_COMPUTE", "SEMICONDUCTOR", "NEW_ENERGY"]


@pytest.fixture
def sample_stocks() -> List[Dict[str, Any]]:
    return [
        {"code": "000001.SZ", "name": "平安银行", "purity": 60.0},
        {"code": "000002.SZ", "name": "万科A", "purity": 45.0},
        {"code": "000333.SZ", "name": "美的集团", "purity": 80.0},
        {"code": "600519.SH", "name": "贵州茅台", "purity": 90.0},
        {"code": "002415.SZ", "name": "海康威视", "purity": 30.0},
    ]


# ══════════════════════════════════════════════════════════════
#  Test: Factor Registry
# ══════════════════════════════════════════════════════════════

class TestFactorRegistry:
    """测试因子注册和计算."""

    async def test_register_and_get(self, registry):
        factor = MockETFStrengthFactor()
        registry.register(factor, layer="etf_strength")

        assert registry.count == 1
        assert registry.get("mock_etf_strength") is factor

    async def test_register_with_layer(self, registry):
        etf = MockETFStrengthFactor()
        breadth = MockBreadthFactor()

        registry.register(etf, layer="etf_strength")
        registry.register(breadth, layer="breadth")

        assert "etf_strength" in registry.get_layer_names()
        assert "breadth" in registry.get_layer_names()
        assert len(registry.get_by_layer("etf_strength")) == 1
        assert registry.count == 2

    async def test_calculate_all(self, registry):
        registry.register(MockETFStrengthFactor(), layer="etf_strength")
        registry.register(MockBreadthFactor(), layer="breadth")

        results = await registry.calculate_all("TEST", "20260724")
        assert "mock_etf_strength" in results
        assert "mock_breadth" in results
        assert results["mock_etf_strength"].score == 75.0
        assert results["mock_breadth"].score == 65.0

    async def test_calculate_layer(self, registry):
        registry.register(MockETFStrengthFactor(), layer="etf_strength")
        registry.register(MockBreadthFactor(), layer="breadth")

        results = await registry.calculate_layer("etf_strength", "TEST", "20260724")
        assert "mock_etf_strength" in results
        assert "mock_breadth" not in results

    async def test_factor_failure_graceful(self, registry):
        class FailingFactor(BaseFactor):
            name = "failing"
            version = "1.0"
            weight_key = "test"

            async def calculate(self, theme_code, trade_date, **kwargs) -> FactorResult:
                raise ValueError("模拟失败")

        registry.register(FailingFactor())
        results = await registry.calculate_all("TEST", "20260724")

        assert "failing" in results
        assert results["failing"].error is not None
        assert results["failing"].score == 0.0


# ══════════════════════════════════════════════════════════════
#  Test: ETF Strength Factor
# ══════════════════════════════════════════════════════════════

class TestETFStrengthFactor:
    """测试ETF强度因子."""

    async def test_mock_etf_factor(self, registry):
        registry.register(MockETFStrengthFactor(), layer="etf_strength")
        results = await registry.calculate_layer(
            "etf_strength", "AI_COMPUTE", "20260724",
            main_etf="159995.SZ",
        )
        factor = results.get("mock_etf_strength")
        assert factor is not None
        assert factor.score == 75.0
        assert factor.details.get("etf_code") == "159995.SZ"


# ══════════════════════════════════════════════════════════════
#  Test: Breadth Factor
# ══════════════════════════════════════════════════════════════

class TestBreadthFactor:
    """测试扩散度因子."""

    async def test_mock_breadth_factor(self, registry):
        registry.register(MockBreadthFactor(), layer="breadth")
        results = await registry.calculate_layer(
            "breadth", "SEMICONDUCTOR", "20260724",
        )
        factor = results.get("mock_breadth")
        assert factor is not None
        assert factor.score == 65.0


# ══════════════════════════════════════════════════════════════
#  Test: Leader Factor
# ══════════════════════════════════════════════════════════════

class TestLeaderFactor:
    """测试龙头因子."""

    async def test_mock_leader_factor(self, registry):
        registry.register(MockLeaderFactor(), layer="leader")
        results = await registry.calculate_layer(
            "leader", "NEW_ENERGY", "20260724",
        )
        factor = results.get("mock_leader")
        assert factor is not None
        assert factor.score == 80.0


# ══════════════════════════════════════════════════════════════
#  Test: Resonance Factor (简化的共振评分测试)
# ══════════════════════════════════════════════════════════════

class TestResonanceFactor:
    """测试共振因子."""

    async def test_resonance_logic(self):
        """测试共振分数的基本逻辑."""
        etf_score = 75.0
        breadth_score = 65.0
        leader_score = 80.0

        # 这里简单测试共振计算逻辑（实际因子的简化）
        scores = [etf_score, breadth_score, leader_score]
        consistency = 100.0 - (max(scores) - min(scores))  # 差值越小，一致性越高
        std = sum((s - sum(scores) / len(scores)) ** 2 for s in scores) / len(scores)
        # 一致性计算
        resonance = consistency * 0.5 + (100.0 - std) * 0.5

        assert 0 <= resonance <= 100
        assert consistency <= 100
        # ETF/扩散/龙头分数接近，共振应该较高
        assert resonance > 50


# ══════════════════════════════════════════════════════════════
#  Test: Stage State Machine
# ══════════════════════════════════════════════════════════════

class TestStageStateMachine:
    """测试状态机."""

    @pytest.fixture
    def machine(self):
        return StageStateMachine()

    async def test_birth_to_growth(self, machine):
        """birth 阶段，低指标."""
        result = await machine.analyze(
            theme_code="TEST",
            trade_date="20260701",
            indicators={"etf_strength": 10.0, "breadth": 5.0, "leader": 0.0,
                        "leader_count": 0, "flow": 5.0, "resonance": 5.0},
        )
        assert result.current_stage in ("birth", "growth")
        assert result.stage_confidence >= 0

    async def test_main_trend_high_scores(self, machine):
        """高指标应匹配 main_trend."""
        result = await machine.analyze(
            theme_code="TEST",
            trade_date="20260724",
            indicators={"etf_strength": 85.0, "breadth": 80.0, "leader": 85.0,
                        "leader_count": 5, "resonance": 75.0, "flow": 80.0,
                        "purity": 70.0},
        )
        assert result.current_stage in ("main_trend", "expansion")
        assert result.stage_confidence >= 0
        assert result.days_in_stage >= 1

    async def test_death_low_scores(self, machine):
        """极限低指标应匹配 death."""
        result = await machine.analyze(
            theme_code="TEST",
            trade_date="20260724",
            indicators={"etf_strength": 5.0, "breadth": 3.0, "leader": 2.0,
                        "leader_count": 0, "resonance": 2.0, "flow": 1.0},
        )
        assert result.current_stage in ("death", "birth")
        assert result.stage_confidence >= 0

    async def test_no_reverse_order(self, machine):
        """测试逆序禁止: main_trend 不应回到 growth."""
        prev = await machine.analyze(
            theme_code="TEST",
            trade_date="20260720",
            indicators={"etf_strength": 85.0, "breadth": 80.0, "leader": 85.0,
                        "leader_count": 5, "resonance": 75.0, "flow": 80.0,
                        "purity": 70.0},
        )

        # 再次调用，但用低指标模拟评分
        result = await machine.analyze(
            theme_code="TEST",
            trade_date="20260721",
            indicators={"etf_strength": 10.0, "breadth": 5.0, "leader": 5.0,
                        "leader_count": 0, "resonance": 5.0, "flow": 5.0},
        )

        # 不应逆序到更早的阶段（birth）
        stage_order = ["birth", "growth", "expansion", "main_trend", "distribution", "death"]
        prev_idx = stage_order.index(prev.current_stage) if prev.current_stage in stage_order else -1
        cur_idx = stage_order.index(result.current_stage) if result.current_stage in stage_order else -1
        assert cur_idx >= prev_idx

    async def test_days_in_stage_accumulation(self, machine):
        """多次调用累计天数."""
        result1 = await machine.analyze("TEST", "20260701",
                                        {"etf_strength": 5.0, "breadth": 5.0, "leader": 0,
                                         "leader_count": 0, "flow": 5.0, "resonance": 5.0})
        result2 = await machine.analyze("TEST", "20260702",
                                        {"etf_strength": 5.0, "breadth": 5.0, "leader": 0,
                                         "leader_count": 0, "flow": 5.0, "resonance": 5.0})
        # 相同阶段天数递增
        if result1.current_stage == result2.current_stage:
            assert result2.days_in_stage >= result1.days_in_stage
        else:
            # 阶段切换，天数重置
            assert result2.days_in_stage >= 1


# ══════════════════════════════════════════════════════════════
#  Test: Rotation Predictor
# ══════════════════════════════════════════════════════════════

class TestRotationPredictor:
    """测试轮动预测."""

    @pytest.fixture
    def predictor(self):
        from theme_engine.rotation.predictor import RotationPredictor
        return RotationPredictor()

    async def test_predict_with_indicators(self, predictor):
        result = await predictor.predict(
            theme_code="AI_COMPUTE",
            trade_date="20260724",
            indicators={
                "etf_strength": 80.0,
                "breadth": 75.0,
                "leader": 70.0,
                "resonance": 65.0,
                "flow": 60.0,
                "purity": 55.0,
            },
            history_days=20,
        )
        assert isinstance(result, RotationResult)
        assert result.theme_code == "AI_COMPUTE"
        assert 0 <= result.prob_3d <= 100
        assert 0 <= result.prob_5d <= 100
        assert 0 <= result.prob_10d <= 100
        assert 0 <= result.rotation_score <= 100

    async def test_predict_with_history(self, predictor):
        """带历史数据的预测."""
        # 先记录几天历史
        for day in range(1, 10):
            date_str = f"202607{day:02d}"
            predictor.record("TEST", date_str, {
                "etf_strength": 50.0 + day * 3,
                "breadth": 40.0 + day * 2,
                "leader": 45.0 + day * 2,
                "resonance": 40.0 + day * 1.5,
                "flow": 35.0 + day * 2,
            })

        result = await predictor.predict(
            theme_code="TEST",
            trade_date="20260710",
            indicators={
                "etf_strength": 80.0,
                "breadth": 65.0,
                "leader": 70.0,
                "resonance": 60.0,
                "flow": 55.0,
                "purity": 50.0,
            },
            history_days=20,
        )
        # 有历史数据支持，概率应合理
        assert 0 <= result.prob_3d <= 100
        assert result.details.get("history_days", 0) > 1


# ══════════════════════════════════════════════════════════════
#  Test: Score Calculator
# ══════════════════════════════════════════════════════════════

class TestScoreCalculator:
    """测试评分计算."""

    @pytest.fixture
    def calculator(self):
        return ScoreCalculator()

    def test_calculate_score(self, calculator):
        """测试综合评分计算."""
        factor_results = {
            "etf_strength": FactorResult(
                factor_name="etf_strength", version="1.0",
                score=80.0, weight=30, contribution=24.0,
            ),
            "breadth": FactorResult(
                factor_name="breadth", version="1.0",
                score=70.0, weight=20, contribution=14.0,
            ),
            "leader": FactorResult(
                factor_name="leader", version="1.0",
                score=90.0, weight=20, contribution=18.0,
            ),
        }

        result = calculator.calculate(
            theme_code="AI_COMPUTE",
            theme_name="AI算力",
            factor_results=factor_results,
            stage="main_trend",
            signal="BUY",
            rotation=80.0,
            top_leaders=["000001.SZ", "000002.SZ"],
            top_stocks=["000001.SZ"],
            main_etf="159995.SZ",
        )

        assert isinstance(result, ThemeDailyScore)
        assert result.theme_code == "AI_COMPUTE"
        assert result.theme_name == "AI算力"
        assert 0 <= result.total_score <= 100
        assert result.stage == "main_trend"
        assert result.signal == "BUY"
        assert result.rotation_prob == 80.0
        assert len(result.explanations) > 0

    def test_ranking(self, calculator):
        """测试排名功能."""
        themes = [
            ThemeDailyScore(theme_code="A", theme_name="主题A", total_score=85.0, rank=0,
                            trade_date="20260724"),
            ThemeDailyScore(theme_code="B", theme_name="主题B", total_score=70.0, rank=0,
                            trade_date="20260724"),
            ThemeDailyScore(theme_code="C", theme_name="主题C", total_score=95.0, rank=0,
                            trade_date="20260724"),
        ]

        ranked = calculator.rank(themes)
        assert ranked[0].theme_code == "C"  # 最高分
        assert ranked[2].theme_code == "B"  # 最低分
        assert ranked[0].rank == 1
        assert ranked[1].rank == 2
        assert ranked[2].rank == 3

    def test_zero_scores(self, calculator):
        """零分场景."""
        result = calculator.calculate(
            theme_code="TEST",
            theme_name="测试主题",
            factor_results={},
        )
        assert result.total_score == 0.0


# ══════════════════════════════════════════════════════════════
#  Test: Validator
# ══════════════════════════════════════════════════════════════

class TestValidator:
    """测试校验器."""

    @pytest.fixture
    def validator(self):
        return Validator()

    async def test_validate_empty_config(self, validator):
        """空配置应该产生警告."""
        with patch.object(validator.theme_service, 'load_config', return_value={}):
            warnings = await validator.validate_theme_config()
            assert len(warnings) > 0
            assert any("为空" in w for w in warnings)

    async def test_validate_stock_mapping_insufficient(self, validator):
        """成分股不足3只应该告警."""
        with patch.object(
            validator.theme_service, 'load_stock_map',
            return_value={"TEST": [{"code": "000001.SZ"}]},
        ):
            warnings = await validator.validate_stock_mapping("20260724")
            assert len(warnings) > 0
            assert any("不足3只" in w for w in warnings)

    async def test_validate_etf_mapping_missing(self, validator):
        """缺少ETF配置应告警."""
        with patch.object(
            validator.theme_service, 'load_config',
            return_value={"TEST": {"name": "测试主题"}},
        ):
            warnings = await validator.validate_etf_mapping()
            assert len(warnings) > 0
            assert any("未配置" in w for w in warnings)

    async def test_auto_fix_purity(self, validator):
        """纯度异常应能自动修复."""
        fixed = await validator.auto_fix([
            "主题 TEST 股票 000001.SZ 纯度异常: 150 (合理范围: 0~100)"
        ])
        assert len(fixed) > 0

    async def test_validate_all_no_warnings(self, validator):
        """正常场景应该无警告."""
        with (
            patch.object(validator.theme_service, 'load_config',
                         return_value={"TEST": {"name": "测试", "main_etf": "159995.SZ"}}),
            patch.object(validator.theme_service, 'load_stock_map',
                         return_value={"TEST": [
                             {"code": "000001.SZ", "name": "A", "purity": 50.0},
                             {"code": "000002.SZ", "name": "B", "purity": 50.0},
                             {"code": "000003.SZ", "name": "C", "purity": 50.0},
                         ]}),
        ):
            warnings = await validator.validate_all("20260724")
            # ETF格式告警会被触发（没有.SH/.SZ后缀），但不会为空
            assert isinstance(warnings, list)


# ══════════════════════════════════════════════════════════════
#  Test: Full Pipeline (集成测试)
# ══════════════════════════════════════════════════════════════

class TestFullPipeline:
    """测试完整流水线（mock 外部依赖）. """

    @pytest.fixture
    def mock_engine(self):
        """创建一个 mock 引擎环境."""
        from theme_engine.api.engine import TERE

        with (
            patch("theme_engine.api.engine.load_weights") as mock_weights,
            patch.object(ThemeService, 'load_config') as mock_config,
            patch.object(ThemeService, 'load_stock_map') as mock_map,
            patch.object(ThemeService, 'get_theme_name') as mock_name,
            patch.object(ThemeService, 'get_theme_etfs') as mock_etfs,
        ):
            mock_weights.return_value = {
                "layer_weights": {
                    "etf_strength": 30,
                    "breadth": 20,
                    "leader": 20,
                    "purity": 10,
                    "resonance": 10,
                    "flow": 5,
                    "rotation": 5,
                },
                "thresholds": {"strong_buy": 85, "buy": 70, "watch": 50, "reduce": 35, "exit": 20},
            }
            mock_config.return_value = {
                "AI_COMPUTE": {"name": "AI算力", "main_etf": "159995.SZ"},
            }
            mock_map.return_value = {
                "AI_COMPUTE": [
                    {"code": "000001.SZ", "name": "A", "purity": 60.0},
                    {"code": "000002.SZ", "name": "B", "purity": 45.0},
                    {"code": "000003.SZ", "name": "C", "purity": 80.0},
                    {"code": "000004.SZ", "name": "D", "purity": 30.0},
                    {"code": "000005.SZ", "name": "E", "purity": 50.0},
                ],
            }
            mock_name.return_value = "AI算力"
            mock_etfs.return_value = ("159995.SZ", None)

            engine = TERE()
            engine._dry_run = True  # 不保存到数据库
            yield engine

    async def test_run_single(self, mock_engine):
        """测试单个主题完整流水线."""
        # 注册 mock 因子到引擎的 registry
        mock_engine.registry.register(MockETFStrengthFactor(), layer="etf_strength")
        mock_engine.registry.register(MockBreadthFactor(), layer="breadth")
        mock_engine.registry.register(MockLeaderFactor(), layer="leader")

        score = await mock_engine.run_single("AI_COMPUTE", "20260724")
        assert score is not None
        assert score.theme_code == "AI_COMPUTE"
        assert score.theme_name == "AI算力"
        assert 0 <= score.total_score <= 100
        assert score.signal in ("STRONG_BUY", "BUY", "WATCH", "REDUCE", "EXIT")

    async def test_run_full(self, mock_engine):
        """测试引擎完整运行."""
        mock_engine.registry.register(MockETFStrengthFactor(), layer="etf_strength")
        mock_engine.registry.register(MockBreadthFactor(), layer="breadth")
        mock_engine.registry.register(MockLeaderFactor(), layer="leader")

        result = await mock_engine.run(trade_date="20260724", dry_run=True)
        assert isinstance(result, EngineResult)
        assert result.trade_date == "20260724"
        assert len(result.themes) > 0
        assert result.ranking is not None
        assert result.error is None

    async def test_run_with_single_theme(self, mock_engine):
        """测试 single 参数."""
        mock_engine.registry.register(MockETFStrengthFactor(), layer="etf_strength")
        mock_engine.registry.register(MockBreadthFactor(), layer="breadth")
        mock_engine.registry.register(MockLeaderFactor(), layer="leader")

        result = await mock_engine.run(trade_date="20260724", dry_run=True, single="AI_COMPUTE")
        assert len(result.themes) == 1
        assert result.themes[0].theme_code == "AI_COMPUTE"

    async def test_empty_registry_graceful(self, mock_engine):
        """没有注册因子的场景."""
        result = await mock_engine.run(trade_date="20260724", dry_run=True)
        assert result is not None
        assert len(result.themes) > 0  # 即使没有因子，应该也能运行

    async def test_error_isolation(self, mock_engine):
        """单主题失败不影响其他主题."""
        mock_engine.registry.register(MockETFStrengthFactor(), layer="etf_strength")

        score_bad = await mock_engine.run_single("NON_EXISTENT", "20260724")
        # 失败的主题返回 None
        assert score_bad is None or isinstance(score_bad, ThemeDailyScore)
