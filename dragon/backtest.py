# -*- coding: utf-8 -*-
"""回测系统"""
import os, json, pickle
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config import BACKTEST, CACHE_DIR
from data_engine import (
    fetch_stock_daily, fetch_index_daily, fetch_daily_basic,
    calc_ma, calc_rsi, get_trade_dates
)
from market_state import classify_market_state
from sector_strength import analyze_sector_strength, get_main_theme_stocks
from dragon_score import score_dragon_candidates


class BacktestEngine:
    def __init__(self, start_date=None, end_date=None, init_cash=None):
        self.start_date = start_date or BACKTEST["start_date"]
        self.end_date = end_date or BACKTEST["end_date"]
        self.init_cash = init_cash or BACKTEST["init_cash"]
        self.commission = BACKTEST["commission"]
        self.stamp_tax = BACKTEST["stamp_tax"]
        self.slippage = BACKTEST["slippage"]

        self.cash = self.init_cash
        self.holdings = {}  # {code: {shares, buy_price, buy_date, high_price}}
        self.trades = []
        self.daily_values = []
        self.state_log = []

    def run(self):
        """运行回测"""
        print(f"回测区间: {self.start_date} ~ {self.end_date}")
        print(f"初始资金: {self.init_cash:,.0f}")
        print("-" * 60)

        dates = get_trade_dates(self.start_date, self.end_date)
        print(f"共 {len(dates)} 个交易日")

        for i, date_str in enumerate(dates):
            if i % 50 == 0:
                print(f"  进度: {i}/{len(dates)} ({date_str})")

            # 市场状态 (简化: 用指数均线判断, 不每天调接口)
            self._simulate_step(date_str, dates, i)

        # 期末平仓
        for code, h in list(self.holdings.items()):
            price = h.get('last_price', h['buy_price'])
            self._sell(code, price, date_str, "期末平仓")

        self._calc_metrics()
        return self._get_results()

    def _simulate_step(self, date_str, all_dates, idx):
        """单日模拟"""
        pd_date = pd.Timestamp(date_str)

        # 市场状态判断 (每5天更新一次减少计算量)
        if idx % 5 == 0:
            self._current_state = self._quick_state(date_str)

        state = getattr(self, '_current_state', 'recover')

        # 仓位控制
        base_pos = {"ice": 0.2, "recover": 0.4, "bull": 0.8, "ebb": 0.3}.get(state, 0.4)
        max_hold = {"ice": 1, "recover": 2, "bull": 3, "ebb": 1}.get(state, 2)
        max_hold = min(max_hold, 3)

        # 检查止损
        self._check_stops(date_str)

        # 获取今日涨跌数据
        # 简化: 使用缓存的方式, 逐步构建价格数据
        # 这里用模拟方式处理 - 实际回测应预加载所有数据
        if idx % 20 == 0 and len(self.holdings) < max_hold:
            # 定期重新评估选股 (模拟定期调仓)
            self._rebalance(date_str, base_pos, max_hold)

        # 计算每日净值
        total_value = self.cash
        for code, h in self.holdings.items():
            price = h.get('last_price', h['buy_price'])
            total_value += price * h['shares']
            if price > h['high_price']:
                h['high_price'] = price

        self.daily_values.append({
            'date': date_str,
            'cash': self.cash,
            'holdings_value': total_value - self.cash,
            'total_value': total_value,
            'state': state,
            'num_holdings': len(self.holdings),
        })

    def _quick_state(self, date_str):
        """快速判断市场状态(回测用, 避免频繁API调用)"""
        try:
            idx = fetch_index_daily("000001.SH", start_date="20250101")
            if idx is None or len(idx) < 20:
                return "recover"
            idx = idx[idx['trade_date'] <= pd.Timestamp(date_str)]
            if len(idx) < 20:
                return "recover"
            idx = calc_ma(idx, [5, 20])
            latest = idx.iloc[-1]
            if latest['close'] > latest.get('ma5', 0) and latest['close'] > latest.get('ma20', 0):
                recent_vol = idx['vol'].tail(5).mean()
                prev_vol = idx['vol'].iloc[-25:-5].mean() if len(idx) > 25 else recent_vol
                if prev_vol > 0 and recent_vol / prev_vol > 1.1:
                    return "bull"
            if latest.get('ma5', 0) < latest.get('ma20', 0):
                return "ice" if idx['close'].iloc[-1] < idx['ma20'].iloc[-1] else "ebb"
            return "recover"
        except:
            return "recover"

    def _check_stops(self, date_str):
        """检查持仓止损"""
        to_sell = []
        for code, h in self.holdings.items():
            price = h.get('last_price', h['buy_price'])
            pnl = (price - h['buy_price']) / h['buy_price']
            drawdown = (price - h['high_price']) / max(h['high_price'], 0.01)

            if pnl <= -0.05:
                to_sell.append((code, "止损"))
            elif drawdown <= -0.08 and pnl > 0.05:
                to_sell.append((code, "移动止损"))
            elif pnl >= 0.20:
                to_sell.append((code, "止盈"))

        for code, reason in to_sell:
            price = self.holdings[code].get('last_price', self.holdings[code]['buy_price'])
            self._sell(code, price, date_str, reason)

    def _rebalance(self, date_str, target_pos, max_hold):
        """调仓逻辑(简化版回测)"""
        if len(self.holdings) >= max_hold:
            return

        try:
            sector_result = analyze_sector_strength()
            main_themes = sector_result.get("main_themes", [])[:3]
            if not main_themes:
                return

            candidates = get_main_theme_stocks(main_themes, top_n_boards=3)
            if candidates is None or len(candidates) == 0:
                return

            scored = score_dragon_candidates(candidates)
            if scored is None or len(scored) == 0:
                return

            dragons = scored.head(5)
            total_value = self.cash + sum(
                self.holdings[c].get('last_price', self.holdings[c]['buy_price']) * self.holdings[c]['shares']
                for c in self.holdings
            )

            available = total_value * target_pos - (
                total_value - self.cash
            )
            if available <= 0:
                return

            for _, row in dragons.iterrows():
                if len(self.holdings) >= max_hold or available <= 0:
                    break
                code = row['code']
                if code in self.holdings:
                    continue
                price = row.get('price', 0)
                if price <= 0:
                    continue

                # 获取实际价格
                ts_code = code + ".SH" if code.startswith(('6', '5', '9')) else code + ".SZ"
                daily = fetch_stock_daily(ts_code, start_date=date_str)
                if daily is not None and len(daily) > 0:
                    price = daily['close'].iloc[-1]
                    self.holdings[code]['last_price'] = price

                invest = min(available * 0.8, total_value * 0.35)
                shares = int(invest / (price * (1 + self.slippage)) / 100) * 100
                if shares <= 0:
                    continue

                cost = shares * price * (1 + self.slippage + self.commission)
                self.cash -= cost
                self.holdings[code] = {
                    'shares': shares,
                    'buy_price': price,
                    'buy_date': date_str,
                    'high_price': price,
                    'last_price': price,
                    'name': row.get('name', code),
                    'score': row.get('total_score', 0),
                }
                available -= cost
                self.trades.append({
                    'date': date_str,
                    'code': code,
                    'name': row.get('name', code),
                    'action': 'buy',
                    'price': price,
                    'shares': shares,
                    'cost': cost,
                    'reason': '龙头买入',
                    'state': getattr(self, '_current_state', 'recover'),
                })
        except Exception as e:
            pass  # 回测中跳过错误

    def _sell(self, code, price, date_str, reason):
        """卖出"""
        if code not in self.holdings:
            return
        h = self.holdings[code]
        shares = h['shares']
        revenue = shares * price * (1 - self.slippage - self.commission - self.stamp_tax)
        self.cash += revenue

        self.trades.append({
            'date': date_str,
            'code': code,
            'name': h.get('name', code),
            'action': 'sell',
            'price': price,
            'shares': shares,
            'revenue': revenue,
            'buy_price': h['buy_price'],
            'pnl': (price - h['buy_price']) / h['buy_price'],
            'reason': reason,
            'state': getattr(self, '_current_state', 'recover'),
        })
        del self.holdings[code]

    def _calc_metrics(self):
        """计算回测指标"""
        if not self.daily_values:
            self.metrics = {}
            return

        df = pd.DataFrame(self.daily_values)
        values = df['total_value']

        total_return = (values.iloc[-1] / values.iloc[0] - 1) * 100

        # 最大回撤
        cummax = values.cummax()
        drawdown = (values - cummax) / cummax
        max_dd = drawdown.min() * 100

        # 年化收益
        n_days = len(values)
        annual_return = (values.iloc[-1] / values.iloc[0]) ** (252 / max(n_days, 1)) - 1

        # 夏普比率
        daily_returns = values.pct_change().dropna()
        sharpe = (daily_returns.mean() / daily_returns.std() * (252 ** 0.5)
                  if daily_returns.std() > 0 else 0)

        # 胜率
        trade_df = pd.DataFrame(self.trades)
        sell_trades = trade_df[trade_df['action'] == 'sell']
        win_rate = (sell_trades['pnl'] > 0).mean() * 100 if len(sell_trades) > 0 else 0
        avg_win = sell_trades[sell_trades['pnl'] > 0]['pnl'].mean() * 100 if len(sell_trades[sell_trades['pnl'] > 0]) > 0 else 0
        avg_loss = sell_trades[sell_trades['pnl'] <= 0]['pnl'].mean() * 100 if len(sell_trades[sell_trades['pnl'] <= 0]) > 0 else 0

        # 按市场状态归因
        state_returns = {}
        for state in ['ice', 'recover', 'bull', 'ebb']:
            state_trades = sell_trades[sell_trades['state'] == state]
            if len(state_trades) > 0:
                state_returns[state] = {
                    'count': len(state_trades),
                    'avg_pnl': state_trades['pnl'].mean() * 100,
                    'win_rate': (state_trades['pnl'] > 0).mean() * 100,
                    'total_pnl': state_trades['pnl'].sum() * 100,
                }

        self.metrics = {
            "total_return": total_return,
            "annual_return": annual_return * 100,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "total_trades": len(self.trades),
            "sell_trades": len(sell_trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_loss_ratio": abs(avg_win / avg_loss) if avg_loss != 0 else 999,
            "state_attribution": state_returns,
            "final_value": values.iloc[-1],
        }

    def _get_results(self):
        return {
            "metrics": self.metrics,
            "trades": self.trades,
            "daily_values": self.daily_values,
        }


def run_backtest(start_date=None, end_date=None):
    """便捷回测入口"""
    engine = BacktestEngine(start_date=start_date, end_date=end_date)
    results = engine.run()
    return results
