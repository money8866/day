# -*- coding: utf-8 -*-
"""
Strategy — 灵活的信号生成框架 + 示例策略

设计要点:
  1. 每个策略是一个继承 StrategyBase 的类, 实现 generate_signals(df) -> df
  2. df 必须含 trade_date/open/high/low/close/vol 列
  3. generate_signals 在 df 上添加 'buy_signal' / 'sell_signal' 布尔列
  4. 支持通过 dict 配置定义条件组合 (无需写代码)

示例:
    # 1. 用类
    stragegy = DoubleMAStrategy(fast=5, slow=10)
    df_sig = strategy.generate_signals(df)

    # 2. 用配置 (DSL)
    cfg = {
        'buy':  ['CROSS(MA5, MA10)', 'RSI6 < 70'],
        'sell': ['CROSS_DOWN(MA5, MA10)'],
    }
    strategy = ConfigStrategy(cfg)
    df_sig = strategy.generate_signals(df)
"""
from __future__ import annotations
from typing import List, Optional, Dict, Any

import pandas as pd
import numpy as np

from indicators import (
    MA, EMA, MACD, KDJ, RSI, BOLL, OBV,
    CROSS, CROSS_DOWN, add_indicators,
)


# =========================================================
# 策略基类
# =========================================================
class StrategyBase:
    """策略基类: 子类只需实现 generate_signals"""

    name: str = "Base"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成买卖信号

        Args:
            df: K线数据, 含 OHLCV

        Returns:
            df 添加 'buy_signal' / 'sell_signal' 布尔列 (True=触发)
        """
        raise NotImplementedError

    def __repr__(self):
        return f"<Strategy: {self.name}>"


# =========================================================
# 示例1: 双均线交叉策略 (最经典)
# =========================================================
class DoubleMAStrategy(StrategyBase):
    """双均线交叉策略

    买入: MA(fast) 上穿 MA(slow)
    卖出: MA(fast) 下穿 MA(slow)
    """

    def __init__(self, fast: int = 5, slow: int = 10):
        self.fast = fast
        self.slow = slow
        self.name = f"MA{fast}_MA{slow}_Cross"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        ma_f = MA(out["close"], self.fast)
        ma_s = MA(out["close"], self.slow)
        out[f"MA{self.fast}"] = ma_f
        out[f"MA{self.slow}"] = ma_s

        out["buy_signal"] = CROSS(ma_f, ma_s)
        out["sell_signal"] = CROSS_DOWN(ma_f, ma_s)
        return out


# =========================================================
# 示例2: MACD 金叉 + RSI 过滤
# =========================================================
class MACD_RSI_Strategy(StrategyBase):
    """MACD 金叉 + RSI<70 (避免顶部买入)

    买入: DIF 上穿 DEA 且 RSI < 70
    卖出: DIF 下穿 DEA
    """

    def __init__(self, rsi_max: float = 70.0):
        self.rsi_max = rsi_max
        self.name = "MACD_RSI"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        dif, dea, macd = MACD(out["close"])
        rsi = RSI(out["close"], 6)
        out["DIF"], out["DEA"], out["RSI6"] = dif, dea, rsi

        macd_golden = CROSS(dif, dea)
        macd_death  = CROSS_DOWN(dif, dea)

        out["buy_signal"]  = macd_golden & (rsi < self.rsi_max)
        out["sell_signal"] = macd_death
        return out


# =========================================================
# 示例3: 布林带突破策略
# =========================================================
class BollBreakStrategy(StrategyBase):
    """布林带突破策略

    买入: 收盘价上穿上轨
    卖出: 收盘价跌破中轨
    """

    def __init__(self, period: int = 20, nbdev: int = 2):
        self.period = period
        self.nbdev = nbdev
        self.name = "BollBreak"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        up, mid, dn = BOLL(out["close"], self.period, self.nbdev)
        out["BOLL_UP"], out["BOLL_MID"] = up, mid

        out["buy_signal"]  = CROSS(out["close"], up)
        out["sell_signal"] = CROSS_DOWN(out["close"], mid)
        return out


# =========================================================
# 配置驱动策略 (DSL)
# =========================================================
class ConfigStrategy(StrategyBase):
    """配置驱动策略 — 用 dict 描述买卖条件, 无需写代码

    支持的函数:
      CROSS(x, y)        — x 上穿 y
      CROSS_DOWN(x, y)   — x 下穿 y
      RSI<N              — RSI6 小于 N
      CLOSE > MA20       — 收盘价大于 MA20
      ... (可扩展)

    示例配置:
        cfg = {
            'buy':  ['CROSS(MA5, MA10)', 'RSI6 < 70'],
            'sell': ['CROSS_DOWN(MA5, MA10)'],
        }
    """

    def __init__(self, config: Dict[str, List[str]]):
        self.config = config
        self.name = "Config"

    def _eval_condition(self, expr: str, df: pd.DataFrame) -> pd.Series:
        """解析单个条件表达式 → bool Series"""
        expr = expr.strip()

        # CROSS(a, b) — 上穿
        if expr.startswith("CROSS(") and expr.endswith(")"):
            inner = expr[6:-1]
            a_name, b_name = [x.strip() for x in inner.split(",")]
            return CROSS(df[a_name], df[b_name])

        # CROSS_DOWN(a, b) — 下穿
        if expr.startswith("CROSS_DOWN(") and expr.endswith(")"):
            inner = expr[11:-1]
            a_name, b_name = [x.strip() for x in inner.split(",")]
            return CROSS_DOWN(df[a_name], df[b_name])

        # 比较: X < Y / X > Y / X <= Y / X >= Y
        for op in ["<=", ">=", "==", "!=", "<", ">"]:
            if op in expr:
                left, right = [x.strip() for x in expr.split(op, 1)]
                left_val = self._get_value(left, df)
                right_val = self._get_value(right, df)
                if op == "<":  return left_val < right_val
                if op == ">":  return left_val > right_val
                if op == "<=": return left_val <= right_val
                if op == ">=": return left_val >= right_val
                if op == "==": return left_val == right_val
                if op == "!=": return left_val != right_val

        # 默认: 全 False
        return pd.Series(False, index=df.index)

    def _get_value(self, name: str, df: pd.DataFrame):
        """获取列名或数值"""
        name = name.strip()
        # 数值
        try:
            return float(name)
        except ValueError:
            pass
        # 列名 (如 MA5, RSI6, CLOSE → close)
        alias = {"CLOSE": "close", "OPEN": "open", "HIGH": "high",
                 "LOW": "low", "VOL": "vol"}
        key = alias.get(name, name)
        if key in df.columns:
            return df[key]
        raise KeyError(f"未知字段: {name}")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_indicators(df)
        # 评估买入条件 (全部满足)
        buy_conds = [self._eval_condition(expr, out) for expr in self.config.get("buy", [])]
        sell_conds = [self._eval_condition(expr, out) for expr in self.config.get("sell", [])]
        out["buy_signal"]  = pd.concat(buy_conds, axis=1).all(axis=1) if buy_conds else False
        out["sell_signal"] = pd.concat(sell_conds, axis=1).all(axis=1) if sell_conds else False
        return out


# =========================================================
# 自检
# =========================================================
if __name__ == "__main__":
    from data_loader import load_kline

    df = load_kline("000001.SZ", start_date="20230101")
    print(f"数据: {len(df)} 条")

    # 测试双均线策略
    s1 = DoubleMAStrategy(5, 10)
    df1 = s1.generate_signals(df)
    n_buy  = df1["buy_signal"].sum()
    n_sell = df1["sell_signal"].sum()
    print(f"\n{s1.name}: 买入信号 {n_buy} 次, 卖出信号 {n_sell} 次")

    # 测试配置策略
    cfg = {
        "buy":  ["CROSS(MA5, MA10)", "RSI6 < 70"],
        "sell": ["CROSS_DOWN(MA5, MA10)"],
    }
    s2 = ConfigStrategy(cfg)
    df2 = s2.generate_signals(df)
    print(f"\n{s2.name}: 买入信号 {df2['buy_signal'].sum()} 次, 卖出信号 {df2['sell_signal'].sum()} 次")
