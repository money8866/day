"""FactorRegistry — 因子注册与调度中心.

插件化架构：
  - registry.register(factor_instance) 自动注册
  - registry.calculate_all(theme_code, trade_date) 执行全部因子
  - 支持按层分组批量计算
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult

logger = logging.getLogger(__name__)


class FactorRegistry:
    """全局因子注册表."""

    def __init__(self) -> None:
        self._factors: Dict[str, BaseFactor] = {}
        self._layers: Dict[str, List[str]] = {}  # layer_name -> [factor_name, ...]

    def register(self, factor: BaseFactor, layer: str = "") -> None:
        """注册一个因子实例.

        Args:
            factor: BaseFactor 子类实例
            layer: 所属层级名称（如 "etf_strength", "breadth"）
        """
        if factor.name in self._factors:
            logger.warning("因子 %s 已存在，将被覆盖", factor.name)
        self._factors[factor.name] = factor
        if layer:
            self._layers.setdefault(layer, []).append(factor.name)
        logger.info("注册因子: %s v%s [层级: %s]", factor.name, factor.version, layer or "未分类")

    def register_class(self, factor_class: Type[BaseFactor], layer: str = "") -> None:
        """通过类注册（自动实例化）. """
        self.register(factor_class(), layer)

    def get(self, name: str) -> Optional[BaseFactor]:
        return self._factors.get(name)

    def get_all(self) -> List[BaseFactor]:
        return list(self._factors.values())

    def get_by_layer(self, layer: str) -> List[BaseFactor]:
        names = self._layers.get(layer, [])
        return [self._factors[n] for n in names if n in self._factors]

    def get_layer_names(self) -> List[str]:
        return list(self._layers.keys())

    async def calculate_all(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> Dict[str, FactorResult]:
        """执行全部已注册因子的计算.

        Returns:
            {factor_name: FactorResult}
        """
        results: Dict[str, FactorResult] = {}
        for name, factor in self._factors.items():
            try:
                result = await factor.calculate(theme_code, trade_date, **kwargs)
                results[name] = result
            except Exception as e:
                logger.error("因子 %s 计算失败: %s", name, e)
                results[name] = FactorResult(
                    factor_name=name,
                    version=factor.version,
                    score=0.0,
                    weight=0.0,
                    contribution=0.0,
                    error=str(e),
                )
        return results

    async def calculate_layer(
        self,
        layer: str,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> Dict[str, FactorResult]:
        """执行指定层级的所有因子."""
        results: Dict[str, FactorResult] = {}
        for factor in self.get_by_layer(layer):
            try:
                result = await factor.calculate(theme_code, trade_date, **kwargs)
                results[factor.name] = result
            except Exception as e:
                logger.error("因子 %s (层级 %s) 计算失败: %s", factor.name, layer, e)
                results[factor.name] = FactorResult(
                    factor_name=factor.name,
                    version=factor.version,
                    score=0.0,
                    weight=0.0,
                    contribution=0.0,
                    error=str(e),
                )
        return results

    @property
    def count(self) -> int:
        return len(self._factors)

    def __repr__(self) -> str:
        return f"<FactorRegistry: {self.count} factors, {len(self._layers)} layers>"


# ── 全局单例 ────────────────────────────────────────────────
_registry: Optional[FactorRegistry] = None


def get_registry() -> FactorRegistry:
    """获取全局 FactorRegistry 单例."""
    global _registry
    if _registry is None:
        _registry = FactorRegistry()
    return _registry


def reset_registry() -> None:
    """重置单例（仅测试用）. """
    global _registry
    _registry = None
