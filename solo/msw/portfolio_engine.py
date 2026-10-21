# -*- coding: utf-8 -*-
"""MSW 组合引擎（任务书 §11 portfolio_engine.py）

仓位管理（§8.2）：
  - vol_weighted：ATR 风险预算 —— 仓位价值 = 权益 × atr_target_pct / atr_pct，
    使 1 倍 ATR 波动对应固定的权益风险比例；单股上限 max_single_frac
  - equal：等权（1/max_positions），同样受单股上限约束
集中度限制：单股 <= max_single_frac、单行业 <= max_sector_frac、总持仓 <= max_positions。

先验证个股策略、再研究多股组合（§8.2）：本引擎同时支持单股独立交易模式
（max_positions=1, equal）与组合模式，由配置切换。
"""
import numpy as np


def board_limit_pct(ts_code: str, name: str = '') -> float:
    """涨跌停幅度：主板 10%，创业板/科创板 20%，ST 5%，北交所 30%"""
    c = str(ts_code)
    if c.startswith(('8', '4')):
        return 0.30
    if c.startswith(('688', '689', '300', '301')):
        return 0.20
    nm = str(name).upper()
    if 'ST' in nm or '退' in nm:
        return 0.05
    return 0.10


class Portfolio:
    """组合状态与仓位计算（不负责成交，成交由 backtest 执行）"""

    def __init__(self, cfg: dict, initial_cash: float = 1_000_000.0):
        self.cfg = cfg
        p = cfg.get('portfolio', {})
        self.mode = p.get('mode', 'vol_weighted')
        self.max_positions = int(p.get('max_positions', 10))
        self.equal_weight_frac = float(p.get('equal_weight_frac', 0.1))
        self.atr_target_pct = float(p.get('atr_target_pct', 0.02))
        self.max_single_frac = float(p.get('max_single_frac', 0.15))
        self.max_sector_frac = float(p.get('max_sector_frac', 0.35))
        self.cash = float(initial_cash)
        self.initial_cash = float(initial_cash)
        self.positions = {}          # ts_code -> dict(shares, entry_price, entry_date, industry, atr_pct, value)

    # ---------------- 权益 ----------------

    def equity(self, close_map: dict) -> float:
        v = self.cash
        for code, pos in self.positions.items():
            px = close_map.get(code)
            if px is None or not np.isfinite(px):
                px = pos['entry_price']
            v += pos['shares'] * px
        return v

    def invested_frac(self, close_map: dict) -> float:
        eq = self.equity(close_map)
        if eq <= 0:
            return 0.0
        return 1.0 - self.cash / eq

    def sector_frac(self, industry: str, close_map: dict) -> float:
        eq = self.equity(close_map)
        if eq <= 0:
            return 0.0
        v = sum(pos['shares'] * (close_map.get(code) or pos['entry_price'])
                for code, pos in self.positions.items() if pos['industry'] == industry)
        return v / eq

    # ---------------- 仓位计算 ----------------

    def target_weight(self, atr_pct: float, industry: str, close_map: dict) -> float:
        """新开仓目标权重（0 表示不可开）"""
        eq = self.equity(close_map)
        if eq <= 0:
            return 0.0
        if len(self.positions) >= self.max_positions:
            return 0.0
        if self.sector_frac(industry, close_map) >= self.max_sector_frac:
            return 0.0
        if self.mode == 'equal':
            w = self.equal_weight_frac
        else:
            if not np.isfinite(atr_pct) or atr_pct <= 0:
                w = self.equal_weight_frac
            else:
                w = self.atr_target_pct / atr_pct
        w = min(w, self.max_single_frac)
        remain = 1.0 - self.invested_frac(close_map)
        w = min(w, max(0.0, remain))
        return w

    # ---------------- 成交 ----------------

    def buy(self, ts_code: str, price: float, value: float, industry: str,
            atr_pct: float, date: str) -> dict | None:
        """按目标价值买入（value 已由调用方按权重算好）；返回成交记录或 None"""
        if value <= 0 or price <= 0:
            return None
        shares = value / price
        cost = value * self._cost_rate()
        if value + cost > self.cash:
            value = self.cash / (1.0 + self._cost_rate())
            shares = value / price
            if value <= 0:
                return None
            cost = value * self._cost_rate()
        self.cash -= (value + cost)
        self.positions[ts_code] = {
            'shares': shares, 'entry_price': price, 'entry_date': date,
            'industry': industry, 'atr_pct': atr_pct, 'value': value}
        return {'ts_code': ts_code, 'date': date, 'price': price,
                'shares': shares, 'value': value, 'cost': cost}

    def sell(self, ts_code: str, price: float, frac: float = 1.0,
             date: str = '') -> dict | None:
        """卖出 frac 比例；返回成交记录或 None"""
        pos = self.positions.get(ts_code)
        if pos is None or price <= 0:
            return None
        frac = min(max(frac, 0.0), 1.0)
        shares = pos['shares'] * frac
        value = shares * price
        cost = value * self._cost_rate()
        self.cash += (value - cost)
        if frac >= 1.0 - 1e-9:
            self.positions.pop(ts_code, None)
        else:
            pos['shares'] -= shares
        return {'ts_code': ts_code, 'date': date, 'price': price,
                'shares': shares, 'value': value, 'cost': cost, 'frac': frac}

    def _cost_rate(self) -> float:
        """单边佣金率（往返成本的一半）。

        注意：滑点不在此列——滑点已由 backtest.simulate 在成交价上单独建模
        （买入 op×(1+slip) / 卖出 op×(1-slip)），若此处再加一次将双重计算。
        配置意图：往返佣金 30bp + 单边滑点 5bp → 全成本往返 40bp。
        """
        bt = self.cfg.get('backtest', {})
        return float(bt.get('cost_round_trip_bp', 30.0)) / 2.0 / 10000.0
