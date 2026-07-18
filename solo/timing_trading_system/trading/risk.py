#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
风控模块
========
止损止盈检查、回撤控制、黑名单过滤
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

LOG = logging.getLogger("timing_trading.risk")


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    passed: bool = True
    reason: str = ""
    details: dict = field(default_factory=dict)


class RiskManager:
    """风控管理器"""

    def __init__(self, config: dict):
        self.cfg = config.get("risk", {})
        self.max_drawdown = self.cfg.get("max_drawdown", -15.0)
        self.daily_loss_limit = self.cfg.get("daily_loss_limit", -3.0)
        self.max_correlated = self.cfg.get("max_correlated", 0.6)
        self.blacklist = set(self.cfg.get("blacklist", []))
        # 运行时状态
        self.peak_capital = 0.0
        self.current_capital = 0.0
        self.daily_pnl_pct = 0.0
        self.position_df = pd.DataFrame()

    def update_state(self, capital: float, daily_pnl_pct: float,
                     position_df: pd.DataFrame):
        """更新运行时状态"""
        self.current_capital = capital
        self.peak_capital = max(self.peak_capital, capital)
        self.daily_pnl_pct = daily_pnl_pct
        self.position_df = position_df

    def check_max_drawdown(self) -> RiskCheckResult:
        """检查最大回撤"""
        if self.peak_capital <= 0:
            return RiskCheckResult(True)
        dd = (self.current_capital - self.peak_capital) / self.peak_capital * 100
        if dd <= self.max_drawdown:
            return RiskCheckResult(
                False,
                f"最大回撤触发: {dd:.1f}% <= {self.max_drawdown}%",
                {"drawdown_pct": round(dd, 1)}
            )
        return RiskCheckResult(True, details={"drawdown_pct": round(dd, 1)})

    def check_daily_loss(self) -> RiskCheckResult:
        """检查单日亏损限额"""
        if self.daily_pnl_pct <= self.daily_loss_limit:
            return RiskCheckResult(
                False,
                f"单日亏损超限: {self.daily_pnl_pct:.1f}% <= {self.daily_loss_limit}%",
                {"daily_loss_pct": round(self.daily_pnl_pct, 1)}
            )
        return RiskCheckResult(True, details={"daily_loss_pct": round(self.daily_pnl_pct, 1)})

    def check_ts_code(self, ts_code: str) -> RiskCheckResult:
        """检查个股黑名单"""
        if ts_code in self.blacklist:
            return RiskCheckResult(False, f"黑名单: {ts_code}")
        return RiskCheckResult(True)

    def check_correlation(self, ts_code: str, theme_name: str) -> RiskCheckResult:
        """检查主题集中度"""
        if self.position_df.empty or not theme_name:
            return RiskCheckResult(True)
        theme_total = self.position_df[self.position_df.get("theme", "") == theme_name]["position"].sum()
        total = self.position_df["position"].sum()
        if total > 0 and (theme_total / total) > self.max_correlated:
            return RiskCheckResult(
                False,
                f"主题集中度超限: {theme_name} {theme_total/total*100:.0f}% > {self.max_correlated*100:.0f}%",
                {"theme_ratio": round(theme_total / total * 100, 1)}
            )
        return RiskCheckResult(True)

    def check_all(self, capital: float, daily_pnl_pct: float,
                  position_df: pd.DataFrame, new_ts_code: str = "",
                  new_theme: str = "") -> List[RiskCheckResult]:
        """执行全部风控检查"""
        self.update_state(capital, daily_pnl_pct, position_df)

        results = [
            self.check_max_drawdown(),
            self.check_daily_loss(),
        ]

        if new_ts_code:
            results.append(self.check_ts_code(new_ts_code))
        if new_theme:
            results.append(self.check_correlation(new_ts_code, new_theme))

        # 汇总
        failed = [r for r in results if not r.passed]
        if failed:
            LOG.warning("风控未通过: %s", "; ".join(r.reason for r in failed))

        return results

    def get_current_drawdown(self) -> float:
        """获取当前回撤幅度"""
        if self.peak_capital <= 0:
            return 0.0
        return (self.current_capital - self.peak_capital) / self.peak_capital * 100
