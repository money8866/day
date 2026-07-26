"""主题数据服务.

从 theme_config.json 加载主题配置，
从 theme_stock_map_v2 CSV 加载主题-股票映射。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from theme_engine.config.settings import THEME_CONFIG_PATH, THEME_STOCK_MAP_DIR

logger = logging.getLogger(__name__)


class ThemeService:
    """主题数据服务.

    从 theme_config.json 加载主题配置，
    从 theme_stock_map_v2 CSV 加载主题-股票映射。
    """

    def __init__(self) -> None:
        self._config: Optional[dict] = None
        self._stock_map_cache: Dict[str, List[Dict[str, Any]]] = {}

    async def load_config(self) -> dict:
        """加载 theme_config.json.

        Returns:
            dict: 主题配置，key 为 theme_code，value 为配置项
        """
        if self._config is not None:
            return self._config

        try:
            config_path = Path(THEME_CONFIG_PATH)
            if not config_path.exists():
                logger.warning("主题配置文件不存在: %s", config_path)
                self._config = {}
                return self._config

            with open(config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            logger.info("已加载 %d 个主题配置", len(self._config))
            return self._config
        except json.JSONDecodeError as e:
            logger.error("主题配置文件解析失败: %s", e)
            self._config = {}
            return self._config
        except Exception as e:
            logger.error("加载主题配置失败: %s", e)
            self._config = {}
            return self._config

    async def load_stock_map(
        self, trade_date: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """从CSV加载主题股票映射.

        Args:
            trade_date: 交易日 YYYYMMDD

        Returns:
            {theme_code: [{"code": str, "name": str, "purity": float}, ...]}
        """
        if trade_date in self._stock_map_cache:
            return self._stock_map_cache[trade_date]

        result: Dict[str, List[Dict[str, Any]]] = {}

        try:
            csv_path = self._find_stock_map_file(trade_date)
            if csv_path is None:
                logger.warning("未找到 %s 的主题股票映射文件", trade_date)
                self._stock_map_cache[trade_date] = result
                return result

            df = pd.read_csv(csv_path, dtype=str)
            logger.info("加载主题映射 CSV: %s, 行数: %d", csv_path.name, len(df))

            # 标准化列名
            df.columns = [c.strip().lower() for c in df.columns]

            # 确定各个字段的列名映射（含中英文名）
            theme_code_col = self._find_column(
                df, ["theme_code", "theme", "code",
                     "主题英文key", "主题英文KEY", "主题key", "主题代码"]
            )
            stock_code_col = self._find_column(
                df, ["stock_code", "code", "con_code", "ts_code",
                     "股票代码", "代码", "ts_code"]
            )
            stock_name_col = self._find_column(
                df, ["stock_name", "name", "con_name",
                     "股票名称", "名称"]
            )
            purity_col = self._find_column(
                df, ["purity", "theme_purity", "weight", "评分", "score"]
            )
            etf_col = self._find_column(df, ["main_etf", "etf"])
            backup_etf_col = self._find_column(df, ["backup_etf", "sub_etf"])

            if theme_code_col is None or stock_code_col is None:
                logger.error(
                    "CSV缺少必要列: theme_code=%s, stock_code=%s, 可用列=%s",
                    theme_code_col,
                    stock_code_col,
                    list(df.columns),
                )
                self._stock_map_cache[trade_date] = result
                return result

            for _, row in df.iterrows():
                tc = str(row[theme_code_col]).strip()
                if not tc:
                    continue

                stock_item: Dict[str, Any] = {
                    "code": str(row[stock_code_col]).strip(),
                }

                if stock_name_col and stock_name_col in row:
                    stock_item["name"] = str(row[stock_name_col]).strip()

                if purity_col and purity_col in row:
                    try:
                        stock_item["purity"] = float(row[purity_col])
                    except (ValueError, TypeError):
                        stock_item["purity"] = 0.0

                if etf_col and etf_col in row:
                    stock_item["main_etf"] = str(row[etf_col]).strip()
                if backup_etf_col and backup_etf_col in row:
                    stock_item["backup_etf"] = str(row[backup_etf_col]).strip()

                result.setdefault(tc, []).append(stock_item)

            self._stock_map_cache[trade_date] = result
            logger.info(
                "主题映射加载完成: %d 个主题, %d 只股票",
                len(result),
                sum(len(v) for v in result.values()),
            )
        except Exception as e:
            logger.error("加载主题股票映射失败: %s", e)
            self._stock_map_cache[trade_date] = result

        return result

    async def get_theme_etfs(
        self, theme_code: str
    ) -> tuple[str, Optional[str]]:
        """获取主题的 main_etf 和 backup_etf.

        Returns:
            (main_etf, backup_etf) 元组
        """
        config = await self.load_config()
        theme_cfg = config.get(theme_code, {})
        main_etf = theme_cfg.get("main_etf", "") or ""
        backup_etf = theme_cfg.get("backup_etf", None)
        return main_etf, backup_etf

    async def get_theme_stocks(
        self, theme_code: str, trade_date: str
    ) -> List[Dict[str, Any]]:
        """获取主题成分股列表.

        Args:
            theme_code: 主题代码
            trade_date: 交易日

        Returns:
            成分股列表 [{"code": ..., "name": ..., "purity": ...}, ...]
        """
        stock_map = await self.load_stock_map(trade_date)
        return stock_map.get(theme_code, [])

    async def get_all_theme_codes(self) -> List[str]:
        """获取所有主题代码列表."""
        config = await self.load_config()
        return list(config.keys())

    async def get_theme_name(self, theme_code: str) -> str:
        """获取主题名称."""
        config = await self.load_config()
        theme_cfg = config.get(theme_code, {})
        if isinstance(theme_cfg, dict):
            return theme_cfg.get("name", theme_cfg.get("name_cn",
                         theme_cfg.get("theme_name", theme_code)))
        return str(theme_cfg)

    @staticmethod
    def _find_stock_map_file(trade_date: str) -> Optional[Path]:
        """查找主题股票映射 CSV 文件."""
        map_dir = Path(THEME_STOCK_MAP_DIR)
        if not map_dir.exists():
            return None

        # 可能的文件命名模式
        candidates = [
            map_dir / f"theme_stock_map_v2_{trade_date}.csv",
            map_dir / f"theme_stock_map_{trade_date}.csv",
            map_dir / f"stock_map_{trade_date}.csv",
            map_dir / f"theme_map_{trade_date}.csv",
        ]

        # 查找最近的日期文件
        for c in candidates:
            if c.exists():
                return c

        # 如果精确匹配找不到，找小于 trade_date 的最新文件
        try:
            from datetime import datetime

            target_dt = datetime.strptime(trade_date, "%Y%m%d")
            best_file: Optional[Path] = None
            best_diff = float("inf")

            for f in sorted(map_dir.glob("theme_stock_map_v2_*.csv")):
                try:
                    date_str = f.stem.split("_")[-1]
                    file_dt = datetime.strptime(date_str, "%Y%m%d")
                    diff = (target_dt - file_dt).days
                    if 0 <= diff < best_diff:
                        best_diff = diff
                        best_file = f
                except (ValueError, IndexError):
                    continue

            return best_file
        except Exception as e:
            logger.debug("查找映射文件异常: %s", e)
            return None

    @staticmethod
    def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
        """在 DataFrame 中查找可能的列名."""
        for col in candidates:
            if col in df.columns:
                return col

        # 尝试模糊匹配
        for col in df.columns:
            col_lower = col.lower().strip()
            for candidate in candidates:
                if candidate.lower() in col_lower or col_lower in candidate.lower():
                    return col
        return None

    async def clear_cache(self) -> None:
        """清空缓存."""
        self._config = None
        self._stock_map_cache.clear()
