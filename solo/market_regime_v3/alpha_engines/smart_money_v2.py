# -*- coding: utf-8 -*-
"""
Smart Money Score V2 (V6.1 Module 3)

核心升级：简化资金行为模型，基于现有Tushare数据。

因子权重：
  1. 主力资金净流入  35%
  2. 超大单净额      25%
  3. 换手率健康度    20%
  4. 筹码集中度变化  20%

输出：
  Smart Money Score + Factor Attribution
"""

import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import tushare as ts
try:
    import stock_cache as sc
except ImportError:
    sc = None


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class SmartMoneyFactorAttribution:
    """因子归因"""
    main_force_score: float = 0.0      # 主力资金净流入得分
    super_large_score: float = 0.0     # 超大单净额得分
    turnover_health: float = 0.0       # 换手率健康度得分
    chip_concentration: float = 0.0    # 筹码集中度得分


@dataclass
class SmartMoneyResult:
    """Smart Money Score 结果"""
    ts_code: str
    name: str = ''
    composite_score: float = 50.0      # 综合评分 0-100

    # 因子得分
    main_force_net: float = 0.0        # 主力净流入（万元）
    super_large_net: float = 0.0       # 超大单净额（万元）
    turnover_ratio: float = 0.0        # 换手率
    chip_change: float = 0.0           # 筹码集中度变化

    # 因子归因
    attribution: SmartMoneyFactorAttribution = field(default_factory=SmartMoneyFactorAttribution)

    # 资金方向判断
    direction: str = 'neutral'         # bullish / bearish / neutral


# ──────────────────────────────────────────────
# 主引擎
# ──────────────────────────────────────────────

class SmartMoneyScoreV2:
    """Smart Money Score V2 — 简化资金行为评分"""

    def __init__(self, config: dict):
        cfg = config.get('smart_money_v2', {})
        self.lookback_days = cfg.get('lookback_days', 10)
        self._pro = None

        # 因子权重
        w = cfg.get('weights', {})
        self.w_main_force = w.get('main_force', 0.35)
        self.w_super_large = w.get('super_large', 0.25)
        self.w_turnover = w.get('turnover_health', 0.20)
        self.w_chip = w.get('chip_concentration', 0.20)
        self.enabled = cfg.get('enabled', True)

    @property
    def pro(self):
        if self._pro is None:
            try:
                self._pro = ts.pro_api()
            except Exception:
                self._pro = None
        return self._pro

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def evaluate(self, trade_date: str, codes: List[str] = None) -> Dict[str, SmartMoneyResult]:
        """评估Smart Money Score

        Args:
            trade_date: 交易日
            codes: 待评估股票列表 (None=全市场)

        Returns:
            {ts_code: SmartMoneyResult}
        """
        results = {}
        start_date = (pd.to_datetime(trade_date) - timedelta(days=self.lookback_days + 5)).strftime('%Y%m%d')

        try:
            # 获取全市场资金流数据
            if codes:
                df_all = self._get_moneyflow_batch(codes, start_date, trade_date)
            else:
                df_all = self._get_market_moneyflow(trade_date)

            if df_all is None or df_all.empty:
                return results

            # 逐股计算
            if codes:
                for code in codes:
                    df_stock = df_all[df_all['ts_code'] == code] if 'ts_code' in df_all.columns else None
                    if df_stock is not None and not df_stock.empty:
                        result = self._calc_single_stock(code, df_stock, trade_date)
                        if result is not None:
                            results[code] = result
            else:
                # 全市场模式：按ts_code分组
                grouped = df_all.groupby('ts_code')
                for code, group in grouped:
                    result = self._calc_single_stock(code, group, trade_date)
                    if result is not None:
                        results[code] = result

        except Exception:
            pass

        return results

    def evaluate_single(self, ts_code: str, trade_date: str) -> Optional[SmartMoneyResult]:
        """评估单只股票的Smart Money Score"""
        results = self.evaluate(trade_date, codes=[ts_code])
        return results.get(ts_code)

    # ──────────────────────────────────────────────
    # 单股计算
    # ──────────────────────────────────────────────

    def _calc_single_stock(self, ts_code: str, df: pd.DataFrame, trade_date: str) -> Optional[SmartMoneyResult]:
        """计算单只股票"""
        try:
            df = df.sort_values('trade_date').reset_index(drop=True)
            if df.empty:
                return None

            latest = df.iloc[-1]
            result = SmartMoneyResult(ts_code=ts_code)

            # ── 1) 主力资金净流入 ──
            main_force = self._get_main_force(df)
            result.main_force_net = main_force
            main_force_score = self._score_main_force(main_force)

            # ── 2) 超大单净额 ──
            super_large = self._get_super_large_net(df)
            result.super_large_net = super_large
            super_large_score = self._score_super_large(super_large)

            # ── 3) 换手率健康度 ──
            turnover = self._get_turnover(df, latest)
            result.turnover_ratio = turnover
            turnover_score = self._score_turnover(turnover, df)

            # ── 4) 筹码集中度变化 ──
            chip = self._get_chip_concentration(df)
            result.chip_change = chip
            chip_score = self._score_chip_change(chip)

            # ── 综合评分 ──
            composite = (
                self.w_main_force * main_force_score +
                self.w_super_large * super_large_score +
                self.w_turnover * turnover_score +
                self.w_chip * chip_score
            )
            result.composite_score = max(0, min(100, composite))

            # ── 因子归因 ──
            result.attribution = SmartMoneyFactorAttribution(
                main_force_score=round(main_force_score * self.w_main_force, 1),
                super_large_score=round(super_large_score * self.w_super_large, 1),
                turnover_health=round(turnover_score * self.w_turnover, 1),
                chip_concentration=round(chip_score * self.w_chip, 1),
            )

            # ── 方向判断 ──
            result.direction = 'bullish' if composite >= 65 else ('bearish' if composite <= 35 else 'neutral')

            return result
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 因子计算
    # ──────────────────────────────────────────────

    def _get_main_force(self, df: pd.DataFrame) -> float:
        """获取主力净流入（最新日）"""
        for col in ['net_mf_amount', 'net_amount', 'net_inflow']:
            if col in df.columns:
                val = float(df[col].iloc[-1]) if pd.notna(df[col].iloc[-1]) else 0.0
                return val
        return 0.0

    def _score_main_force(self, net: float) -> float:
        """主力资金得分 (0-100)"""
        # 净流入 >5000万 → 高分; 净流出 < -5000万 → 低分
        if net > 5000:
            return 80 + min(20, (net - 5000) / 500)
        elif net > 0:
            return 50 + net / 5000 * 30
        elif net > -5000:
            return 50 + net / 5000 * 30  # net is negative
        else:
            return max(0, 20 + (net + 5000) / 500)

    def _get_super_large_net(self, df: pd.DataFrame) -> float:
        """获取超大单净额"""
        buy_elder = 0.0
        sell_elder = 0.0
        if 'buy_elder_amount' in df.columns:
            buy_elder = float(df['buy_elder_amount'].iloc[-1]) if pd.notna(df['buy_elder_amount'].iloc[-1]) else 0.0
        if 'sell_elder_amount' in df.columns:
            sell_elder = float(df['sell_elder_amount'].iloc[-1]) if pd.notna(df['sell_elder_amount'].iloc[-1]) else 0.0
        return buy_elder - sell_elder

    def _score_super_large(self, net: float) -> float:
        """超大单得分 (0-100)"""
        if net > 2000:
            return 80 + min(20, (net - 2000) / 200)
        elif net > 0:
            return 50 + net / 2000 * 30
        elif net > -2000:
            return 50 + net / 2000 * 30
        else:
            return max(0, 20 + (net + 2000) / 200)

    def _get_turnover(self, df: pd.DataFrame, latest: pd.Series) -> float:
        """获取换手率"""
        for col in ['turnover_rate', 'turnover', 'turn']:
            if col in df.index or col in df.columns:
                val = float(latest[col]) if pd.notna(latest[col]) else 0.0
                return val
        return 0.0

    def _score_turnover(self, current_turnover: float, df: pd.DataFrame) -> float:
        """换手率健康度 (0-100)

        换手率过低 → 不活跃
        换手率适中 → 健康
        换手率过高 → 过热风险
        """
        # 获取20日均换手
        turnover_col = None
        for col in ['turnover_rate', 'turnover', 'turn']:
            if col in df.columns:
                turnover_col = col
                break

        if turnover_col and len(df) >= 5:
            avg_turnover = float(df[turnover_col].tail(min(20, len(df))).mean())
        else:
            avg_turnover = current_turnover

        if avg_turnover <= 0:
            return 50.0

        ratio = current_turnover / max(avg_turnover, 1e-6)

        # 量比 0.8~1.5 → 健康
        if 0.8 <= ratio <= 1.5:
            return 80.0
        # 量比 0.5~0.8 → 略缩量
        elif 0.5 <= ratio < 0.8:
            return 60.0
        # 量比 1.5~2.5 → 放量
        elif 1.5 < ratio <= 2.5:
            return 60.0
        # 量比 > 2.5 → 过热
        elif ratio > 2.5:
            return 30.0
        # 量比 < 0.5 → 极度缩量
        else:
            return 20.0

    def _get_chip_concentration(self, df: pd.DataFrame) -> float:
        """获取筹码集中度变化

        使用日线数据简化估计：
        通过价格波动范围估计筹码分散/集中
        正值=集中，负值=分散
        """
        if len(df) < 5:
            return 0.0

        recent = df.tail(5)
        # 用ATR/价格 的变化表示筹码变化
        close_col = 'close_hfq' if 'close_hfq' in df.columns else 'close'
        prices = recent[close_col].values if close_col in recent.columns else recent['close'].values

        if len(prices) < 2 or prices[0] <= 0:
            return 0.0

        price_change = (prices[-1] - prices[0]) / prices[0]
        # 缩量+价格稳定 → 筹码集中（正分）
        # 放量+价格波动 → 筹码分散（负分）

        vol_col = None
        for col in ['vol', 'volume']:
            if col in recent.columns:
                vol_col = col
                break

        if vol_col is not None:
            vol_ratio = float(recent[vol_col].iloc[-1]) / max(float(recent[vol_col].mean()), 1)
        else:
            vol_ratio = 1.0

        if vol_ratio < 0.8 and abs(price_change) < 0.03:
            return 0.05  # 集中 +5%
        elif vol_ratio > 1.5 and abs(price_change) > 0.05:
            return -0.03  # 分散 -3%
        else:
            return 0.01  # 略集中

    def _score_chip_change(self, chip_change: float) -> float:
        """筹码集中度得分 (0-100)"""
        if chip_change > 0.03:
            return 80.0
        elif chip_change > 0:
            return 65.0
        elif chip_change > -0.03:
            return 50.0
        else:
            return 30.0

    # ──────────────────────────────────────────────
    # 数据获取
    # ──────────────────────────────────────────────

    def _get_moneyflow_batch(self, codes: List[str], start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """批量获取个股资金流"""
        all_dfs = []
        for code in codes[:10]:  # 限制API调用
            try:
                if self.pro is None:
                    continue
                df = self.pro.moneyflow(ts_code=code, start_date=start_date, end_date=end_date)
                if df is not None and not df.empty:
                    df['ts_code'] = code
                    all_dfs.append(df)
            except Exception:
                continue
        return pd.concat(all_dfs, ignore_index=True) if all_dfs else None

    def _get_market_moneyflow(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取全市场资金流快照"""
        try:
            if self.pro is None:
                return None
            return self.pro.moneyflow(trade_date=trade_date)
        except Exception:
            return None
