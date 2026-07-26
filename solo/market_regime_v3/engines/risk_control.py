# -*- coding: utf-8 -*-
"""
Risk Control Engine - 风控执行引擎
在仓位/主题/龙头确定后，执行风险控制规则
"""

import sys
sys.path.insert(0, r'd:\mystock\solo')

from dataclasses import dataclass, field
from typing import List, Dict
import yaml


@dataclass
class RiskControlResult:
    """风控评估结果"""
    is_safe: bool                       # 整体安全标志
    max_positions: int                  # 最大持仓数
    max_per_position_pct: float         # 单个仓位最大占比
    max_per_theme_pct: float            # 单个主题最大占比
    stop_loss_atr: float                # 止损 ATR 倍数
    take_profit_atr: float              # 止盈 ATR 倍数
    max_daily_drawdown: float           # 最大日回撤
    trailing_stop_activation: float     # 移动止盈激活阈值
    trailing_stop_distance: float       # 移动止盈距离
    dynamic_stop_enabled: bool          # 动态止损是否启用
    warnings: List[Dict[str, str]]      # 风险警告列表
    actions: List[str]                  # 建议操作列表
    explain: Dict[str, str]             # 解释说明


class RiskControlEngine:
    """风险控制引擎"""

    def __init__(self, config: dict):
        """
        初始化风控引擎

        :param config: 完整配置字典，从中读取 risk_control 段
        """
        self._cfg = config.get('risk_control', {})

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def evaluate(self, regime_name: str, heat_level: str,
                 exposure_pct: float, theme_count: int) -> RiskControlResult:
        """
        执行风控评估

        :param regime_name:   市场状态名称 (Bear / Recovery / Neutral / Bull / Euphoria)
        :param heat_level:    热度等级 (Ice / Cold / Cool / Normal / Warm / Hot / Very Hot / Extreme Hot)
        :param exposure_pct:  当前仓位比例 (0~1)
        :param theme_count:   当前持有主题数量
        :return:              RiskControlResult
        """
        # ---- 加载阈值 ----
        max_positions = self._cfg.get('max_positions', 5)
        max_per_position_pct = self._cfg.get('max_per_position_pct', 0.15)
        max_per_theme_pct = self._cfg.get('max_per_theme_pct', 0.30)
        sl_multiple = self._cfg.get('stop_loss_atr_multiple', 2.0)
        tp_multiple = self._cfg.get('take_profit_atr_multiple', 3.0)
        max_daily_dd = self._cfg.get('max_daily_drawdown', 0.03)
        trailing_act = self._cfg.get('trailing_stop_activation', 0.08)
        trailing_dist = self._cfg.get('trailing_stop_distance', 0.05)
        dynamic_cfg = self._cfg.get('dynamic_stop', {})
        dynamic_enabled = dynamic_cfg.get('enabled', True)

        # ---- 生成警告 ----
        warnings: List[Dict[str, str]] = []

        if exposure_pct > 0.8:
            warnings.append({'type': 'high_exposure', 'msg': '高仓位运行，注意风险'})

        if regime_name == 'Bear' and exposure_pct > 0.2:
            warnings.append({'type': 'bear_overweight', 'msg': '熊市仓位过重'})

        if regime_name == 'Euphoria':
            warnings.append({'type': 'euphoria', 'msg': '市场亢奋，注意止盈'})

        if heat_level in ('Extreme Hot', 'Very Hot'):
            warnings.append({'type': 'market_overheat', 'msg': '市场过热'})

        if theme_count > self._cfg.get('max_positions', 5):
            warnings.append({'type': 'theme_over_dispersion', 'msg': '主题过于分散'})

        # ---- 生成操作建议 ----
        actions: List[str] = []

        # 动态止损计算
        vol_mult = dynamic_cfg.get('volatility_multiplier', 1.5)
        base_sl = sl_multiple
        if dynamic_enabled:
            adj_sl = base_sl * vol_mult
            max_sl = dynamic_cfg.get('max_stop_loss', 0.12)
            min_sl = dynamic_cfg.get('min_stop_loss', 0.03)
            # 这里 adj_sl 是 ATR 倍数，max/min 是相对于入场价的百分比，统一逻辑在外部
            effective_sl = max(min_sl, min(max_sl, adj_sl * 0.02))  # 示意换算
            actions.append(f'动态止损建议: {effective_sl:.1%} 止损')

        actions.append(f'建议止损: {sl_multiple:.0f}×ATR')
        actions.append(f'建议止盈: {tp_multiple:.0f}×ATR')

        # 再平衡建议
        rebalance_threshold = self._cfg.get('rebalance_threshold', 0.05)
        if exposure_pct > 0.8 or (regime_name == 'Bear' and exposure_pct > 0.2):
            actions.append('建议降低仓位，进行再平衡')

        # ---- 安全标志 ----
        is_safe = True
        if regime_name == 'Bear' and exposure_pct > 0.3:
            is_safe = False
        if heat_level in ('Extreme Hot',) and exposure_pct > 0.9:
            is_safe = False

        # ---- 解释 ----
        explain = {
            'regime': regime_name,
            'heat': heat_level,
            'exposure': f'{exposure_pct:.0%}',
            'theme_count': str(theme_count),
            'dynamic_stop': 'enabled' if dynamic_enabled else 'disabled',
        }

        return RiskControlResult(
            is_safe=is_safe,
            max_positions=max_positions,
            max_per_position_pct=max_per_position_pct,
            max_per_theme_pct=max_per_theme_pct,
            stop_loss_atr=sl_multiple,
            take_profit_atr=tp_multiple,
            max_daily_drawdown=max_daily_dd,
            trailing_stop_activation=trailing_act,
            trailing_stop_distance=trailing_dist,
            dynamic_stop_enabled=dynamic_enabled,
            warnings=warnings,
            actions=actions,
            explain=explain,
        )

    # ------------------------------------------------------------------
    # 静态工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_stop_loss(entry_price: float, atr: float,
                            config: dict) -> Dict:
        """
        根据入场价、ATR 和配置计算动态止损/止盈

        :param entry_price:  入场价格
        :param atr:          ATR 值
        :param config:       完整配置字典
        :return:             {'stop_loss': float, 'take_profit': float}
        """
        cfg = config.get('risk_control', {})
        base_sl = cfg.get('stop_loss_atr_multiple', 2.0) * atr

        if cfg.get('dynamic_stop', {}).get('enabled', True):
            vol_mult = cfg['dynamic_stop']['volatility_multiplier']
            adj_sl = base_sl * vol_mult
            max_sl = cfg['dynamic_stop']['max_stop_loss'] * entry_price
            min_sl = cfg['dynamic_stop']['min_stop_loss'] * entry_price
            stop_loss = max(min_sl, min(max_sl, adj_sl))
        else:
            stop_loss = base_sl

        take_profit = cfg.get('take_profit_atr_multiple', 3.0) * atr
        return {'stop_loss': stop_loss, 'take_profit': take_profit}
