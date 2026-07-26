"""BaseFactor 抽象基类 — 所有评分器的统一接口.

插件化架构：
  1. 继承 BaseFactor
  2. 实现 name / version / calculate()
  3. 系统通过 FactorRegistry 自动注册
  4. 新增因子无需修改主程序
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from theme_engine.models.dataclasses import FactorResult
from theme_engine.config.settings import get_factor_weights


class BaseFactor(ABC):
    """因子基类.

    Subclass 必须实现：
      - name: str          (类属性，唯一标识)
      - version: str       (类属性，语义版本)
      - weight_key: str    (对应 weights.yaml 中的键名)
      - calculate()        (核心计算逻辑)
    """

    name: str = ""
    version: str = "1.0.0"
    weight_key: str = ""

    @abstractmethod
    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """执行因子计算.

        Args:
            theme_code: 主题代码 (如 AI_COMPUTE)
            trade_date: 交易日 YYYYMMDD
            **kwargs:  额外参数（由调用方按需传入）

        Returns:
            FactorResult 包含评分、权重、贡献度、明细
        """
        ...

    def get_weights(self) -> Dict[str, float]:
        """获取当前子因子权重配置."""
        return get_factor_weights(self.weight_key)

    def normalize(self, value: float, min_val: float = 0, max_val: float = 1) -> float:
        """线性归一化到 0~100."""
        if max_val == min_val:
            return 50.0
        clipped = max(min_val, min(max_val, value))
        return (clipped - min_val) / (max_val - min_val) * 100.0

    def sigmoid_normalize(self, value: float, midpoint: float = 0, steepness: float = 1) -> float:
        """Sigmoid 归一化到 0~100."""
        import math as _math
        return 100.0 / (1.0 + _math.exp(-steepness * (value - midpoint)))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.name} v{self.version}>"
