"""ETFStrengthFactor — ETF 强度评分因子.

从 theme_config.json 读取主题对应的 main_etf / backup_etf，
计算趋势、动量、Alpha、成交量、资金流、波动率等子因子，
加权汇总得到 ETF 强度评分。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from theme_engine.config.settings import THEME_CONFIG_PATH
from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import ETFStrengthResult, FactorResult

logger = logging.getLogger(__name__)


def _load_theme_config() -> Dict[str, Any]:
    """加载 theme_config.json."""
    path = THEME_CONFIG_PATH
    if not path.exists():
        # 回退到项目根目录
        path = Path(__file__).resolve().parent.parent.parent / "theme_config.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    logger.warning("theme_config.json 未找到，尝试路径: %s", path)
    return {}


class ETFStrengthFactor(BaseFactor):
    """ETF 强度评分因子."""

    name: str = "etf_strength"
    version: str = "1.0.0"
    weight_key: str = "etf_strength"

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """计算 ETF 强度评分.

        kwargs 可传入：
            etf_data: Dict[str, Any]  — ETF 行情指标
                包含 trend, momentum, alpha, volume, money_flow,
                volatility, relative_strength, ma_trend, slope, atr, breakout
        """
        await asyncio.sleep(0)

        # 读取主题配置获取 ETF 信息
        config = _load_theme_config()
        theme_cfg = config.get(theme_code, {})
        main_etf: str = theme_cfg.get("main_etf", "")
        backup_etf: Optional[str] = theme_cfg.get("backup_etf", None)
        if not backup_etf:
            etf_codes: List[str] = theme_cfg.get("etf_codes", [])
            if len(etf_codes) > 1:
                backup_etf = etf_codes[1]

        # 获取 ETF 数据 — 优先从 etf_service 获取日线
        etf_data: Optional[Dict[str, Any]] = kwargs.get("etf_data")
        etf_service = kwargs.get("etf_service")

        if etf_data is None and etf_service is not None and main_etf:
            try:
                df = await etf_service.get_etf_daily(main_etf, trade_date, days=60)
                if df is not None and not df.empty and "close" in df.columns:
                    closes = df["close"].values
                    volumes = df.get("volume", df.get("vol", pd.Series([0] * len(df)))).values
                    highs = df["high"].values if "high" in df.columns else closes
                    lows = df["low"].values if "low" in df.columns else closes
                    n = len(closes)

                    # 计算基础指标
                    returns = np.diff(closes) / closes[:-1] if n > 1 else [0]

                    # trend: 20日涨跌幅
                    trend = (closes[-1] / closes[max(0, n - 20)] - 1) if n >= 20 else (closes[-1] / closes[0] - 1)

                    # momentum: 5日涨跌幅
                    momentum = (closes[-1] / closes[max(0, n - 5)] - 1) if n >= 5 else trend

                    # volume: 量比 (当日量 / 20日均量)
                    vol_mean = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes)
                    vol_ratio = volumes[-1] / vol_mean if vol_mean > 0 else 1.0

                    # volatility: 20日年化波动率
                    ret_std = np.std(returns[-20:]) if len(returns) >= 20 else np.std(returns) if len(returns) > 0 else 0
                    volatility = ret_std * np.sqrt(252) * 100  # 年化百分比

                    # ma_trend: 价格相对MA20位置
                    ma20 = np.mean(closes[-20:]) if n >= 20 else np.mean(closes)
                    ma_trend = (closes[-1] / ma20 - 1) * 100  # 百分比偏离

                    # slope: 20日线性回归斜率 (归一化到百分比)
                    if n >= 20:
                        x = np.arange(20)
                        y = closes[-20:]
                        slope_val = np.polyfit(x, y, 1)[0] / ma20 * 100  # 斜率/均价的百分比
                    else:
                        slope_val = 0.0

                    # atr: 14日ATR比率
                    if n >= 15:
                        tr_list = []
                        for i in range(1, n):
                            tr = max(highs[i] - lows[i],
                                     abs(highs[i] - closes[i - 1]),
                                     abs(lows[i] - closes[i - 1]))
                            tr_list.append(tr)
                        atr_val = np.mean(tr_list[-14:]) / closes[-1] * 100
                    else:
                        atr_val = 0.0

                    # breakout: 相对20日最高点位置
                    hh_20 = np.max(closes[-20:]) if n >= 20 else np.max(closes)
                    breakout = (closes[-1] / hh_20 - 1) * 100

                    etf_data = {
                        "trend": trend * 100,  # 转百分比
                        "momentum": momentum * 100,
                        "alpha": trend * 100,  # 简化: 使用ETF自身收益
                        "volume": vol_ratio,
                        "money_flow": 0.0,  # 资金流需额外接口
                        "volatility": volatility,
                        "relative_strength": trend * 100,  # 简化
                        "ma_trend": ma_trend,
                        "slope": slope_val,
                        "atr": atr_val,
                        "breakout": breakout,
                    }
            except Exception as e:
                logger.warning("从 etf_service 获取 %s 数据异常: %s", main_etf, e)

        weights = self.get_weights()
        if not weights:
            weights = {
                "trend": 0.20, "momentum": 0.15, "alpha": 0.10,
                "volume": 0.10, "money_flow": 0.10, "volatility": 0.05,
                "relative_strength": 0.10, "ma_trend": 0.05,
                "slope": 0.05, "atr": 0.05, "breakout": 0.05,
            }

        if not main_etf or not etf_data:
            logger.info("主题 %s 无 ETF 数据，返回默认分 50", theme_code)
            default_result = ETFStrengthResult(
                theme_code=theme_code,
                trade_date=trade_date,
                main_etf=main_etf or "",
                backup_etf=backup_etf,
                etf_strength=50.0,
            )
            return FactorResult(
                factor_name=self.name,
                version=self.version,
                score=50.0,
                weight=0.0,
                contribution=0.0,
                details={"etf_strength_result": default_result.__dict__},
                error="ETF 数据缺失，使用默认分",
            )

        # ── 计算各子因子分 ────────────────────────────────────
        trend_score = self.normalize(
            etf_data.get("trend", 0), -5, 5
        )
        momentum_score = self.normalize(
            etf_data.get("momentum", 0), -5, 5
        )
        alpha_score = self.normalize(
            etf_data.get("alpha", 0), -3, 3
        )
        volume_score = self.normalize(
            etf_data.get("volume", 0), 0, 3
        )
        money_flow_score = self.normalize(
            etf_data.get("money_flow", 0), -2, 2
        )
        volatility_score = self.normalize(
            etf_data.get("volatility", 0), 0, 3
        )
        relative_strength = self.normalize(
            etf_data.get("relative_strength", 0), -3, 3
        )
        ma_trend = self.normalize(
            etf_data.get("ma_trend", 0), -5, 5
        )
        slope = self.normalize(
            etf_data.get("slope", 0), -2, 2
        )
        atr_score = self.normalize(
            etf_data.get("atr", 0), 0, 5
        )
        breakout_score = self.normalize(
            etf_data.get("breakout", 0), 0, 3
        )

        # ── 加权总分 ──────────────────────────────────────────
        sub_scores = {
            "trend": trend_score,
            "momentum": momentum_score,
            "alpha": alpha_score,
            "volume": volume_score,
            "money_flow": money_flow_score,
            "volatility": volatility_score,
            "relative_strength": relative_strength,
            "ma_trend": ma_trend,
            "slope": slope,
            "atr": atr_score,
            "breakout": breakout_score,
        }

        etf_strength = 0.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key, w in weights.items():
                etf_strength += sub_scores.get(key, 50.0) * w

        etf_strength = max(0.0, min(100.0, etf_strength))

        # ── 构建结果 ──────────────────────────────────────────
        strength_result = ETFStrengthResult(
            theme_code=theme_code,
            trade_date=trade_date,
            main_etf=main_etf,
            backup_etf=backup_etf,
            trend_score=trend_score,
            momentum_score=momentum_score,
            alpha_score=alpha_score,
            volume_score=volume_score,
            money_flow_score=money_flow_score,
            volatility_score=volatility_score,
            relative_strength=relative_strength,
            ma_trend=ma_trend,
            slope=slope,
            atr_score=atr_score,
            breakout_score=breakout_score,
            etf_strength=etf_strength,
            details=etf_data,
        )

        total_w = weights.get("__total__", sum(weights.values()))
        contribution = etf_strength * total_w / 100.0 if total_w > 0 else 0.0

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=etf_strength,
            weight=total_w,
            contribution=contribution,
            details={"etf_strength_result": strength_result.__dict__},
        )
