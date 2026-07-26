"""Factor Registry - 因子注册器

支持插件化注册、动态开关、回测和 Explain。
所有因子通过 registry 注册，引擎自动发现。
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any
from enum import Enum


class FactorCategory(Enum):
    TREND = "trend"
    MOMENTUM = "momentum"
    BREADTH = "breadth"
    SENTIMENT = "sentiment"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    FLOW = "flow"
    STYLE = "style"
    RISK = "risk"
    HEAT = "heat"


@dataclass
class FactorMeta:
    name: str
    category: FactorCategory
    description: str
    version: str = "1.0.0"
    enabled: bool = True
    weight: float = 1.0
    min_value: float = 0.0
    max_value: float = 100.0
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FactorResult:
    name: str
    value: float
    score: float  # 归一化 0~100
    weight: float
    contribution: float  # score * weight
    detail: str = ""


class FactorRegistry:
    """全局因子注册器

    所有因子在此注册。
    支持 enable/disable，支持添加自定义因子。
    """

    def __init__(self):
        self._factors: Dict[str, FactorMeta] = {}
        self._computers: Dict[str, Callable] = {}

    def register(self, meta: FactorMeta, computer: Callable):
        self._factors[meta.name] = meta
        self._computers[meta.name] = computer

    def unregister(self, name: str):
        self._factors.pop(name, None)
        self._computers.pop(name, None)

    def enable(self, name: str, enabled: bool = True):
        if name in self._factors:
            self._factors[name].enabled = enabled

    def is_enabled(self, name: str) -> bool:
        meta = self._factors.get(name)
        return meta is not None and meta.enabled

    def get_meta(self, name: str) -> Optional[FactorMeta]:
        return self._factors.get(name)

    def list_factors(self, category: Optional[FactorCategory] = None) -> List[FactorMeta]:
        if category:
            return [m for m in self._factors.values() if m.category == category and m.enabled]
        return [m for m in self._factors.values() if m.enabled]

    def compute(self, name: str, **kwargs) -> Optional[FactorResult]:
        meta = self._factors.get(name)
        computer = self._computers.get(name)
        if not meta or not computer or not meta.enabled:
            return None
        raw = computer(**kwargs)
        score = self._normalize(raw, meta)
        return FactorResult(
            name=name,
            value=raw,
            score=score,
            weight=meta.weight,
            contribution=score * meta.weight,
            detail=f"{name}: {raw:.4f} → {score:.1f}分 (w={meta.weight})",
        )

    def compute_batch(self, category: Optional[FactorCategory] = None, **kwargs) -> List[FactorResult]:
        results = []
        factors = self.list_factors(category)
        for meta in factors:
            result = self.compute(meta.name, **kwargs)
            if result is not None:
                results.append(result)
        return results

    @staticmethod
    def _normalize(value: float, meta: FactorMeta) -> float:
        # 简单的 min-max 裁剪
        if meta.max_value > meta.min_value:
            clipped = max(meta.min_value, min(meta.max_value, value))
            return (clipped - meta.min_value) / (meta.max_value - meta.min_value) * 100
        return min(100.0, max(0.0, value))


# 全局单例
GLOBAL_REGISTRY = FactorRegistry()
