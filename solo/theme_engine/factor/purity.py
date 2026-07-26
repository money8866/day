"""PurityFactor — 主题纯度评分因子.

通过 theme_config.json 读取主题的 industry_chains、keywords 等信息，
结合外部传入的成分股数据，计算股票与主题的相关纯度，
进而得到加权扩散度和加权 Alpha 纯度评分。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from theme_engine.config.settings import THEME_CONFIG_PATH
from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult, PurityResult

logger = logging.getLogger(__name__)


def _load_theme_config() -> Dict[str, Any]:
    """加载 theme_config.json."""
    path = THEME_CONFIG_PATH
    if not path.exists():
        path = Path(__file__).resolve().parent.parent.parent / "theme_config.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    logger.warning("theme_config.json 未找到，尝试路径: %s", path)
    return {}


class PurityFactor(BaseFactor):
    """主题纯度评分因子."""

    name: str = "purity"
    version: str = "1.0.0"
    weight_key: str = "purity"

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """计算主题纯度评分.

        kwargs 需要传入:
            theme_stocks: List[Dict[str, Any]] — 主题成分股列表
                每只股票含以下字段：
                - code: str                  股票代码
                - name: str                  股票名称
                - purity: float              该股与主题的纯度值 0~1
                - pct_chg: float             涨幅%
                - alpha: float               Alpha 值
                - industry_weight: float     行业权重
        """
        await asyncio.sleep(0)

        theme_stocks: List[Dict[str, Any]] = kwargs.get("theme_stocks", [])
        weights = self.get_weights()

        if not theme_stocks:
            logger.info("主题 %s 无成分股数据，返回默认分 50", theme_code)
            default_result = PurityResult(
                theme_code=theme_code,
                trade_date=trade_date,
                purity_score=50.0,
            )
            return FactorResult(
                factor_name=self.name,
                version=self.version,
                score=50.0,
                weight=0.0,
                contribution=0.0,
                details={"purity_result": default_result.__dict__},
                error="成分股数据缺失，使用默认分",
            )

        # ── 计算各股票的纯度 ──────────────────────────────────
        stock_purities: List[Dict[str, Any]] = []
        purities: List[float] = []
        weighted_breadth_sum = 0.0
        weighted_alpha_sum = 0.0
        total_weight_factor = 0.0

        for stock in theme_stocks:
            purity = stock.get("purity", 0) or 0
            alpha = stock.get("alpha", 0) or 0
            pct_chg = stock.get("pct_chg", 0) or 0
            industry_weight = stock.get("industry_weight", 1) or 1

            purities.append(purity)
            weighted_breadth_sum += purity * pct_chg * industry_weight
            weighted_alpha_sum += purity * alpha * industry_weight
            total_weight_factor += industry_weight

            stock_purities.append({
                "code": stock.get("code", ""),
                "name": stock.get("name", ""),
                "purity": purity,
                "alpha": alpha,
                "pct_chg": pct_chg,
                "industry_weight": industry_weight,
            })

        # ── 主题纯度：所有股票纯度的均值 ──────────────────────
        theme_purity = (
            sum(purities) / len(purities) if purities else 0.0
        )

        # ── 加权扩散度：纯度加权涨幅 ──────────────────────────
        weighted_breadth = (
            weighted_breadth_sum / total_weight_factor
            if total_weight_factor > 0
            else 0.0
        )

        # ── 加权 Alpha ────────────────────────────────────────
        weighted_alpha = (
            weighted_alpha_sum / total_weight_factor
            if total_weight_factor > 0
            else 0.0
        )

        # ── 归一化子因子到 0~100 ──────────────────────────────
        theme_purity_score = self.normalize(theme_purity, 0, 1)
        weighted_breadth_score = self.normalize(weighted_breadth, -5, 5)
        weighted_alpha_score = self.normalize(weighted_alpha, -3, 3)

        # ── 加权总分 ──────────────────────────────────────────
        sub_scores = {
            "theme_purity": theme_purity_score,
            "weighted_breadth": weighted_breadth_score,
            "weighted_alpha": weighted_alpha_score,
        }

        purity_score = 0.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key, w in weights.items():
                purity_score += sub_scores.get(key, 50.0) * w

        purity_score = max(0.0, min(100.0, purity_score))

        # ── 构建结果 ──────────────────────────────────────────
        purity_result = PurityResult(
            theme_code=theme_code,
            trade_date=trade_date,
            theme_purity=theme_purity,
            weighted_breadth=weighted_breadth,
            weighted_alpha=weighted_alpha,
            purity_score=purity_score,
            stock_purities=stock_purities,
            details={"total_stocks": len(theme_stocks)},
        )

        contribution = purity_score * total_weight / 100.0 if total_weight > 0 else 0.0

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=purity_score,
            weight=total_weight,
            contribution=contribution,
            details={"purity_result": purity_result.__dict__},
        )
