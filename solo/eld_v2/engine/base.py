"""
ELD V3 引擎基类

所有评分引擎继承自 BaseEngine，统一：
- 配置注入
- 日志接口
- 评分结果验证
- 异常处理
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

from ..config import Config, get_config

T = TypeVar("T")


class BaseEngine(ABC, Generic[T]):
    """引擎基类"""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.cfg = config or get_config()
        self.logger = logging.getLogger(f"eld_v3.engine.{self.__class__.__name__}")

    @abstractmethod
    def score(self, ts_code: str, data_source: Any, **kwargs) -> T:
        """计算评分

        Args:
            ts_code: 股票代码
            data_source: 数据源实例
            **kwargs: 各引擎特定参数

        Returns:
            评分结果对象
        """
        ...

    def _validate_score(self, score: float) -> float:
        """验证评分在0-100范围内"""
        return max(0.0, min(100.0, score))

    def _log_result(self, ts_code: str, result: T) -> None:
        """记录评分摘要"""
        score = getattr(result, "score", -1)
        self.logger.debug("[%s] score=%.2f", ts_code, score)
