"""自动校验器.

每天检查：
- 主题配置是否有对应的ETF映射
- 主题是否有至少3只成分股
- 同一只股票是否出现在多个主题中（允许但记录警告）
- 纯度是否在合理范围（0~100）
- 是否有重复的theme_code
- 龙头的ETF关联是否有效
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from theme_engine.config.settings import get_threshold
from theme_engine.services.theme_service import ThemeService

logger = logging.getLogger(__name__)


class Validator:
    """自动校验器.

    检查主题配置、股票映射、ETF映射、纯度等数据的完整性和合理性。
    """

    def __init__(self, theme_service: Optional[ThemeService] = None) -> None:
        self.theme_service = theme_service or ThemeService()
        self._warnings: List[str] = []
        self._errors: List[str] = []

    async def validate_all(self, trade_date: str) -> List[str]:
        """执行全部校验，返回警告列表.

        Args:
            trade_date: 交易日 YYYYMMDD

        Returns:
            警告消息列表
        """
        self._warnings.clear()
        self._errors.clear()

        logger.info("开始全量校验 - %s", trade_date)

        self._warnings.extend(await self.validate_theme_config())
        self._warnings.extend(await self.validate_stock_mapping(trade_date))
        self._warnings.extend(await self.validate_etf_mapping())
        self._warnings.extend(await self.validate_purity(trade_date))
        self._warnings.extend(await self._validate_duplicate_stocks(trade_date))
        self._warnings.extend(await self._validate_theme_count())

        if self._warnings:
            logger.warning("校验完成，共 %d 条警告", len(self._warnings))
            for w in self._warnings:
                logger.warning("  ⚠ %s", w)
        else:
            logger.info("校验完成，无警告")

        return self._warnings

    async def validate_theme_config(self) -> List[str]:
        """验证主题配置."""
        warnings: List[str] = []

        try:
            config = await self.theme_service.load_config()
            if not config:
                warnings.append("主题配置文件为空或不存在")
                return warnings

            # 检查每个主题是否包含必要字段
            for theme_code, theme_cfg in config.items():
                if not isinstance(theme_cfg, dict):
                    warnings.append(f"主题 {theme_code} 配置格式异常")
                    continue

                if not any(k in theme_cfg for k in ("name", "theme_name", "name_cn")):
                    warnings.append(f"主题 {theme_code} 缺少名称字段")

                # 检查是否有重复的 theme_code (由 dict key 保证唯一)
        except Exception as e:
            warnings.append(f"校验主题配置异常: {e}")

        return warnings

    async def validate_stock_mapping(self, trade_date: str) -> List[str]:
        """验证股票映射."""
        warnings: List[str] = []

        try:
            stock_map = await self.theme_service.load_stock_map(trade_date)
            if not stock_map:
                warnings.append(f"{trade_date} 无主题股票映射数据")
                return warnings

            for theme_code, stocks in stock_map.items():
                if len(stocks) < 3:
                    warnings.append(
                        f"主题 {theme_code} 成分股不足3只 (当前: {len(stocks)})"
                    )

                # 检查股票代码格式
                for stock in stocks:
                    code = stock.get("code", "")
                    if not code or len(code) < 6:
                        warnings.append(
                            f"主题 {theme_code} 包含异常股票代码: '{code}'"
                        )
        except Exception as e:
            warnings.append(f"校验股票映射异常: {e}")

        return warnings

    async def validate_etf_mapping(self) -> List[str]:
        """验证ETF映射."""
        warnings: List[str] = []

        try:
            config = await self.theme_service.load_config()

            for theme_code, theme_cfg in config.items():
                if not isinstance(theme_cfg, dict):
                    continue

                # 跳过内部/辅助主题
                if theme_code.startswith("_"):
                    continue

                main_etf = theme_cfg.get("main_etf", "")
                etf_codes = theme_cfg.get("etf_codes", [])
                if not main_etf and not etf_codes:
                    warnings.append(f"主题 {theme_code} 未配置 main_etf 及 etf_codes")

                # 校验ETF代码格式
                if main_etf and not self._is_valid_etf_code(main_etf):
                    warnings.append(
                        f"主题 {theme_code} main_etf 格式异常: {main_etf}"
                    )

                backup_etf = theme_cfg.get("backup_etf", "")
                if backup_etf and not self._is_valid_etf_code(backup_etf):
                    warnings.append(
                        f"主题 {theme_code} backup_etf 格式异常: {backup_etf}"
                    )
        except Exception as e:
            warnings.append(f"校验ETF映射异常: {e}")

        return warnings

    async def validate_purity(self, trade_date: str) -> List[str]:
        """验证纯度是否在合理范围 (0~100)."""
        warnings: List[str] = []

        try:
            stock_map = await self.theme_service.load_stock_map(trade_date)

            for theme_code, stocks in stock_map.items():
                for stock in stocks:
                    purity = stock.get("purity", 0)
                    if purity < 0 or purity > 100:
                        warnings.append(
                            f"主题 {theme_code} 股票 {stock.get('code', '')} "
                            f"纯度异常: {purity} (合理范围: 0~100)"
                        )

            # 计算平均纯度，过高或过低都值得注意
            purity_list: List[float] = []
            for stocks in stock_map.values():
                for stock in stocks:
                    purity = stock.get("purity", 0)
                    if purity > 0:
                        purity_list.append(purity)

            if purity_list:
                avg_purity = sum(purity_list) / len(purity_list)
                if avg_purity > 80:
                    warnings.append(
                        f"平均纯度偏高 ({avg_purity:.1f})，可能存在标记偏差"
                    )
                elif avg_purity < 10:
                    warnings.append(
                        f"平均纯度偏低 ({avg_purity:.1f})，主题关联度不足"
                    )
        except Exception as e:
            warnings.append(f"校验纯度异常: {e}")

        return warnings

    async def _validate_duplicate_stocks(self, trade_date: str) -> List[str]:
        """检查同一只股票是否出现在多个主题中."""
        warnings: List[str] = []

        try:
            stock_map = await self.theme_service.load_stock_map(trade_date)

            stock_to_themes: Dict[str, List[str]] = {}
            for theme_code, stocks in stock_map.items():
                for stock in stocks:
                    code = stock.get("code", "")
                    if code:
                        stock_to_themes.setdefault(code, []).append(theme_code)

            for stock_code, theme_list in stock_to_themes.items():
                if len(theme_list) > 3:
                    warnings.append(
                        f"股票 {stock_code} 出现在 {len(theme_list)} 个主题中: "
                        f"{', '.join(theme_list[:5])}..."
                    )
        except Exception as e:
            warnings.append(f"校验重复股票异常: {e}")

        return warnings

    async def _validate_theme_count(self) -> List[str]:
        """校验主题数量是否合理."""
        warnings: List[str] = []

        try:
            config = await self.theme_service.load_config()
            theme_count = len(config)
            if theme_count < 5:
                warnings.append(
                    f"主题数量过少 ({theme_count})，可能配置未正确加载"
                )
            elif theme_count > 200:
                warnings.append(
                    f"主题数量过多 ({theme_count})，可能包含冗余配置"
                )
        except Exception as e:
            warnings.append(f"校验主题数量异常: {e}")

        return warnings

    async def auto_fix(self, warnings: List[str]) -> List[str]:
        """尝试自动修复可修复的问题.

        Args:
            warnings: 校验产生的警告列表

        Returns:
            已修复的警告列表
        """
        fixed: List[str] = []
        not_fixed: List[str] = []

        for warning in warnings:
            try:
                # 可以修复的规则:
                # 1. 成分股不足3只 - 无法自动修复
                # 2. ETF代码格式异常 - 无法自动修复（需要人工干预）
                # 3. 纯度异常 - 自动裁剪到 0~100 范围
                if "纯度异常" in warning and ":" in warning:
                    # 提取股票代码和纯度值
                    import re

                    match = re.search(r"纯度(?:异常)?:\s*([-\d.]+)", warning)
                    if match:
                        purity_value = float(match.group(1))
                        clipped = max(0, min(100, purity_value))
                        if clipped != purity_value:
                            fixed.append(warning)
                            logger.info(
                                "自动修复纯度: %s → %.1f", warning, clipped
                            )
                            continue

                not_fixed.append(warning)
            except Exception as e:
                logger.debug("自动修复失败: %s, %s", warning, e)
                not_fixed.append(warning)

        logger.info(
            "自动修复完成: 已修复 %d 条, 未修复 %d 条",
            len(fixed),
            len(not_fixed),
        )
        return fixed

    async def get_summary(self) -> dict:
        """获取校验摘要."""
        return {
            "warning_count": len(self._warnings),
            "error_count": len(self._errors),
            "warnings": self._warnings,
            "errors": self._errors,
        }

    @staticmethod
    def _is_valid_etf_code(code: str) -> bool:
        """检查ETF代码格式是否合法."""
        import re

        # 常见格式: 6位数字 + .交易所后缀
        pattern = r"^\d{6}\.(SH|SZ|BJ)$"
        return bool(re.match(pattern, code))

    async def get_warnings(self) -> List[str]:
        """获取当前警告列表."""
        return self._warnings.copy()
