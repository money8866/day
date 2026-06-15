"""
主题轮动策略回测引擎 (backtest_engine.py)
==========================================

基于 Backtrader 框架实现主题配置信号的回测：
  1. 数据源: PostgreSQL 读取复权行情 / akshare 回退
  2. 策略类: 每日 9:25 读取信号 → 按权重调仓
  3. A股特殊处理: T+1 / 涨停无法买入 / 跌停无法卖出
  4. 风控: 单主题 8% 止损 / 总回撤 15% 清仓
  5. 分析: 收益率/年化/夏普/最大回撤/换手率/月度热力图
  6. 输出: HTML 报告

依赖:
  pip install backtrader pandas numpy sqlalchemy psycopg2-binary matplotlib seaborn
"""

from __future__ import annotations

import os
import sys
import json
import time
import math
import datetime
from typing import Any, Dict, List, Optional, Tuple

# 路径处理
_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR: str = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import backtrader as bt
import numpy as np
import pandas as pd

from modules.db_connector import CONFIG, PgConnector
from modules.utils import setup_logger

logger = setup_logger(
    name="backtest_engine",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="backtest_engine.log",
)

# A股常量
COMMISSION_RATE: float = 0.0025   # 万2.5
STAMP_TAX_RATE: float = 0.001     # 千1 印花税(卖出时)
SLIPPAGE: float = 0.01            # 滑点(每股)
LIMIT_UP_PCT: float = 9.80
LIMIT_DOWN_PCT: float = -9.80
MAX_TOPIC_LOSS: float = 0.08      # 单主题最大亏损 8%
MAX_DRAWDOWN: float = 0.15        # 总回撤 15%


# ============================================================================ #
# 1. 自定义数据源 (PostgreSQL / akshare)
# ============================================================================ #
class PostgresFeed(bt.feeds.GenericCSVData):
    """
    从 PostgreSQL 读取复权行情数据的 Backtrader 数据源。
    数据表结构 (参考):
        CREATE TABLE stock_daily (
            code VARCHAR(10),
            date DATE,
            open FLOAT,
            high FLOAT,
            low FLOAT,
            close FLOAT,
            volume BIGINT,
            amount BIGINT,
            adj_factor FLOAT,
            PRIMARY KEY (code, date)
        );
    """

    params = (
        ("fromdate", datetime.datetime(2020, 1, 1)),
        ("todate", datetime.datetime.now()),
        ("code", ""),
        ("adjust", "qfq"),  # "qfq" / "hfq" / None
    )

    def __init__(self):
        super().__init__()
        self._data_df: Optional[pd.DataFrame] = None

    def start(self):
        # 在 start 阶段从 PostgreSQL 加载数据
        try:
            with PgConnector() as conn:
                query = f"""
                    SELECT date, open, high, low, close, volume, amount
                    FROM stock_daily
                    WHERE code = %s
                      AND date >= %s
                      AND date <= %s
                    ORDER BY date ASC
                """
                params = (
                    self.p.code,
                    self.p.fromdate.strftime("%Y-%m-%d"),
                    self.p.todate.strftime("%Y-%m-%d"),
                )
                df = pd.read_sql_query(query, conn, params=params)
                if df.empty:
                    logger.warning("[PostgresFeed] 股票 %s 无数据", self.p.code)
                    self._data_df = None
                    return

                # 复权处理 (假设数据库已存储复权因子)
                if self.p.adjust in ["qfq", "hfq"]:
                    # 简化: 假设数据库已有 adj_factor 列, 或直接使用复权后数据
                    pass
                df["date"] = pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)
                self._data_df = df
                logger.info("[PostgresFeed] 加载 %s: %d 条数据",
                          self.p.code, len(df))
        except Exception as exc:
            logger.error("[PostgresFeed] PostgreSQL 查询失败: %s", exc)
            # 回退到 tushare
            self._data_df = self._fallback_tushare()

        if self._data_df is not None:
            self._idx = 0
            self._last_date = None

    def _fallback_tushare(self) -> Optional[pd.DataFrame]:
        """PostgreSQL 不可用时回退到 tushare"""
        try:
            import tushare as ts
            from modules.db_connector import CONFIG
            
            token = CONFIG.get("api_keys", {}).get("tushare", {}).get("token", "")
            if not token or token.startswith("${"):
                token = os.environ.get("TUSHARE_TOKEN", "")
            
            pro = ts.pro_api(token) if token else ts.pro_api()
            
            # 补充市场后缀
            code = self.p.code
            if len(code) == 6:
                code = f"{code}.SH" if code.startswith(("6", "5")) else f"{code}.SZ"
            
            df = pro.daily(
                ts_code=code,
                start_date=self.p.fromdate.strftime("%Y%m%d"),
                end_date=self.p.todate.strftime("%Y%m%d"),
                adj=self.p.adjust,
            )
            if df is None or df.empty:
                return None
            df["date"] = pd.to_datetime(df["trade_date"])
            df = df.rename(columns={
                "open": "open", "high": "high", "low": "low",
                "close": "close", "vol": "volume", "amount": "amount",
            })
            df = df[["open", "high", "low", "close", "volume", "amount"]]
            df.set_index("date", inplace=True)
            df = df.sort_index()
            return df
        except Exception as exc:
            logger.warning("[PostgresFeed] tushare 回退失败: %s", exc)
            return None

    def _load(self):
        if self._data_df is None:
            return False
        if self._idx >= len(self._data_df):
            return False

        row = self._data_df.iloc[self._idx]
        self.lines.datetime[0] = bt.date2num(row.name.to_pydatetime())
        self.lines.open[0] = float(row["open"])
        self.lines.high[0] = float(row["high"])
        self.lines.low[0] = float(row["low"])
        self.lines.close[0] = float(row["close"])
        self.lines.volume[0] = int(row["volume"])
        self.lines.openinterest[0] = 0

        self._last_date = row.name
        self._idx += 1
        return True


# ============================================================================ #
# 2. 主题轮动策略类
# ============================================================================ #
class TopicRotationStrategy(bt.Strategy):
    """
    主题轮动策略核心逻辑:
      - 每日开盘前(9:25)读取信号 → 计算目标权重
      - 按目标权重调仓, 考虑 T+1 / 涨停无法买入 / 跌停无法卖出
      - 风控: 单主题亏损 8% 止损, 总回撤 15% 清仓
    """

    params = (
        ("signal_dir", os.path.join(_PARENT_DIR, "signals")),
        ("rebalance_days", 1),
        ("max_single_weight", 0.30),
    )

    def __init__(self):
        self.dataclose = {d._name: d.close for d in self.datas}
        self.order_targets: Dict[str, float] = {}  # 目标权重 {code: weight}
        self.entry_prices: Dict[str, float] = {}   # 持仓成本 {code: price}
        self.topic_stocks: Dict[str, List[str]] = {}  # {topic: [codes]}
        self.rebalance_counter = 0
        self.highest_nav = 1.0

    def next(self):
        # 每日开盘前处理
        if self.rebalance_counter == 0:
            self._load_daily_signal()
            self._execute_rebalance()
        self.rebalance_counter = (self.rebalance_counter + 1) % self.p.rebalance_days

        # 风控检查
        self._risk_control()

    def _load_daily_signal(self):
        """加载当日信号文件或调用模块生成信号"""
        today = self.datas[0].datetime.date(0)
        date_str = today.strftime("%Y-%m-%d")

        # 尝试从信号文件读取
        signal_file = os.path.join(self.p.signal_dir, f"signal_{date_str}.json")
        if os.path.exists(signal_file):
            try:
                with open(signal_file, "r", encoding="utf-8") as f:
                    signal_data = json.load(f)
                self._parse_signal(signal_data)
                return
            except Exception as exc:
                logger.warning("[Strategy] 读取信号文件失败: %s", exc)

        # 回退到调用模块生成信号
        try:
            from modules.topic_configurator import generate_topic_signals_json
            from modules.stock_picker import StockPickerPipeline

            # 获取主题信号
            signal_json = generate_topic_signals_json(date_str)
            signal_data = json.loads(signal_json)
            self._parse_signal(signal_data)

            # 获取成分股
            topics = [(s["primary_topic"], s["secondary_topic"])
                    for s in signal_data.get("signals", [])]
            picker = StockPickerPipeline()
            compositions = picker.run(topics)
            for comp in compositions:
                key = f"{comp.primary_topic}/{comp.secondary_topic}"
                self.topic_stocks[key] = [sp.code for sp in comp.stocks]
        except Exception as exc:
            logger.warning("[Strategy] 调用信号模块失败: %s", exc)

    def _parse_signal(self, signal_data: Dict[str, Any]):
        """解析信号数据为目标权重"""
        self.order_targets.clear()
        signals = signal_data.get("signals", [])
        for sig in signals:
            topic_key = f"{sig['primary_topic']}/{sig['secondary_topic']}"
            weight = float(sig["target_weight"])
            codes = sig.get("stock_codes", [])
            if codes and weight > 0:
                # 按成分股数量平分主题权重
                per_stock_weight = min(
                    weight / len(codes),
                    self.p.max_single_weight / len(codes),
                )
                for code in codes[:5]:  # 每主题最多5只股票
                    self.order_targets[code] = (
                        self.order_targets.get(code, 0) + per_stock_weight
                    )

        # 归一化权重
        total_weight = sum(self.order_targets.values())
        if total_weight > 0:
            self.order_targets = {
                k: v / total_weight for k, v in self.order_targets.items()
            }

    def _execute_rebalance(self):
        """执行调仓"""
        # 卖出不在目标中的持仓
        for data in self.datas:
            code = data._name.replace("stock_", "")
            pos = self.getposition(data).size
            if pos != 0 and code not in self.order_targets:
                # 检查是否跌停
                if self._can_sell(data):
                    self.close(data)
                    logger.info("[Strategy] 卖出 %s (不在目标中)", code)

        # 按目标权重买入
        total_value = self.broker.getvalue()
        for code, target_weight in self.order_targets.items():
            data = self._get_data_by_code(code)
            if data is None:
                continue
            pos = self.getposition(data).size
            target_value = total_value * target_weight
            current_value = pos * self.dataclose[code][0]
            diff_value = target_value - current_value

            if diff_value > 0:
                # 买入
                if self._can_buy(data):
                    size = int(diff_value / self.dataclose[code][0] / 100) * 100
                    if size > 0:
                        self.buy(data, size=size)
                        self.entry_prices[code] = self.dataclose[code][0]
                        logger.info("[Strategy] 买入 %s: %d 股 @ %.2f",
                                  code, size, self.dataclose[code][0])
            elif diff_value < -100:  # 卖出阈值
                # 卖出
                if self._can_sell(data):
                    size = int(-diff_value / self.dataclose[code][0] / 100) * 100
                    if size > 0:
                        self.sell(data, size=size)
                        logger.info("[Strategy] 卖出 %s: %d 股 @ %.2f",
                                  code, size, self.dataclose[code][0])

    def _can_buy(self, data) -> bool:
        """检查是否可以买入（非涨停）"""
        if len(data) < 2:
            return True
        prev_close = data.close[-1]
        current_open = data.open[0] if data.open[0] > 0 else data.close[0]
        change = (current_open - prev_close) / prev_close * 100
        return change < LIMIT_UP_PCT - 0.5  # 留出误差

    def _can_sell(self, data) -> bool:
        """检查是否可以卖出（非跌停）"""
        if len(data) < 2:
            return True
        prev_close = data.close[-1]
        current_open = data.open[0] if data.open[0] > 0 else data.close[0]
        change = (current_open - prev_close) / prev_close * 100
        return change > LIMIT_DOWN_PCT + 0.5  # 留出误差

    def _get_data_by_code(self, code: str):
        """根据代码获取数据 feed"""
        for data in self.datas:
            if data._name == f"stock_{code}":
                return data
        return None

    def _risk_control(self):
        """风控检查"""
        # 更新最高净值
        current_nav = self.broker.getvalue() / self.broker.startingcash
        self.highest_nav = max(self.highest_nav, current_nav)

        # 总回撤检查
        drawdown = (self.highest_nav - current_nav) / self.highest_nav
        if drawdown > MAX_DRAWDOWN:
            logger.warning("[Strategy] 总回撤 %.2f%% > 15%%, 清仓", drawdown * 100)
            for data in self.datas:
                pos = self.getposition(data).size
                if pos != 0 and self._can_sell(data):
                    self.close(data)
            self.order_targets.clear()
            return

        # 单主题止损检查
        for code, entry_price in self.entry_prices.items():
            data = self._get_data_by_code(code)
            if data is None:
                continue
            current_price = self.dataclose[code][0]
            loss = (entry_price - current_price) / entry_price
            if loss > MAX_TOPIC_LOSS:
                logger.warning("[Strategy] %s 亏损 %.2f%% > 8%%, 止损",
                            code, loss * 100)
                if self._can_sell(data):
                    self.close(data)
                    self.entry_prices.pop(code, None)

    def notify_order(self, order):
        if order.status in [order.Completed]:
            code = order.data._name.replace("stock_", "")
            if order.isbuy():
                logger.info("[Order] 买入完成: %s %d 股 @ %.2f",
                          code, order.executed.size, order.executed.price)
            else:
                logger.info("[Order] 卖出完成: %s %d 股 @ %.2f",
                          code, order.executed.size, order.executed.price)
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            logger.warning("[Order] 订单失败: %s", order.data._name)


# ============================================================================ #
# 3. 自定义分析器
# ============================================================================ #
class SharpeRatioAnalyzer(bt.Analyzer):
    """夏普比率分析器"""

    def __init__(self):
        self.returns = []

    def next(self):
        if len(self.data) > 1:
            ret = (self.data.close[0] - self.data.close[-1]) / self.data.close[-1]
            self.returns.append(ret)

    def get_analysis(self):
        if not self.returns:
            return {"sharpe_ratio": 0.0}
        returns = np.array(self.returns)
        mean_return = returns.mean() * 252
        std_return = returns.std() * np.sqrt(252)
        sharpe_ratio = mean_return / std_return if std_return > 0 else 0.0
        return {"sharpe_ratio": sharpe_ratio}


class MonthlyReturnAnalyzer(bt.Analyzer):
    """月度收益分析器"""

    def __init__(self):
        self.monthly_returns = {}
        self.last_month = None
        self.last_nav = 1.0

    def next(self):
        current_date = self.strategy.datas[0].datetime.date(0)
        current_month = (current_date.year, current_date.month)
        current_nav = self.strategy.broker.getvalue() / self.strategy.broker.startingcash

        if current_month != self.last_month:
            if self.last_month is not None:
                ret = (current_nav - self.last_nav) / self.last_nav
                self.monthly_returns[self.last_month] = ret
            self.last_month = current_month
            self.last_nav = current_nav

    def get_analysis(self):
        return {"monthly_returns": self.monthly_returns}


class TurnoverAnalyzer(bt.Analyzer):
    """换手率分析器"""

    def __init__(self):
        self.total_traded = 0.0
        self.total_value = 0.0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.total_traded += abs(order.executed.size * order.executed.price)

    def next(self):
        self.total_value += self.strategy.broker.getvalue()

    def get_analysis(self):
        if self.total_value > 0:
            turnover = self.total_traded / (self.total_value / len(self.strategy.datas))
            return {"turnover_ratio": turnover}
        return {"turnover_ratio": 0.0}


# ============================================================================ #
# 4. 回测引擎主类
# ============================================================================ #
class BacktestEngine:
    """回测引擎主类"""

    def __init__(self, start_date: str, end_date: str,
                initial_cash: float = 1_000_000.0):
        self.start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_cash = initial_cash
        self.cerebro = bt.Cerebro()
        self.results = None

    def add_stock_data(self, codes: List[str]):
        """添加股票数据"""
        for code in codes:
            data = PostgresFeed(
                code=code,
                fromdate=self.start_date,
                todate=self.end_date,
                adjust="qfq",
            )
            self.cerebro.adddata(data, name=f"stock_{code}")

    def add_strategy(self, **kwargs):
        """添加策略"""
        self.cerebro.addstrategy(TopicRotationStrategy, **kwargs)

    def add_analyzers(self):
        """添加分析器"""
        self.cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
        self.cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        self.cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
        self.cerebro.addanalyzer(MonthlyReturnAnalyzer, _name="monthly")
        self.cerebro.addanalyzer(TurnoverAnalyzer, _name="turnover")

    def run(self):
        """运行回测"""
        self.cerebro.broker.setcash(self.initial_cash)
        self.cerebro.broker.setcommission(commission=COMMISSION_RATE,
                                        margin=1.0,
                                        mult=1.0,
                                        commtype=bt.CommInfoBase.COMM_FIXED)
        self.cerebro.broker.addcommissioninfo(
            ChineseStockCommInfo(commission=COMMISSION_RATE, stamp_duty=STAMP_TAX_RATE)
        )

        logger.info("[BacktestEngine] 开始回测: %s -> %s",
                   self.start_date.strftime("%Y-%m-%d"),
                   self.end_date.strftime("%Y-%m-%d"))
        self.results = self.cerebro.run()
        logger.info("[BacktestEngine] 回测完成")
        return self.results

    def get_results(self) -> Dict[str, Any]:
        """获取分析结果"""
        if self.results is None:
            return {}
        strat = self.results[0]
        returns = strat.analyzers.returns.get_analysis()
        drawdown = strat.analyzers.drawdown.get_analysis()
        sharpe = strat.analyzers.sharpe.get_analysis()
        monthly = strat.analyzers.monthly.get_analysis()
        turnover = strat.analyzers.turnover.get_analysis()

        return {
            "total_return": returns.get("rtot", 0.0),
            "annual_return": returns.get("rnorm", 0.0),
            "max_drawdown": drawdown.get("max", {}).get("drawdown", 0.0),
            "sharpe_ratio": sharpe.get("sharperatio", 0.0),
            "turnover_ratio": turnover.get("turnover_ratio", 0.0),
            "monthly_returns": monthly.get("monthly_returns", {}),
            "final_value": self.cerebro.broker.getvalue(),
        }


# ============================================================================ #
# 5. A股佣金和印花税处理
# ============================================================================ #
class ChineseStockCommInfo(bt.CommInfoBase):
    """A股佣金和印花税计算"""

    params = (
        ("commission", 0.0025),
        ("stamp_duty", 0.001),
        ("margin", 1.0),
        ("mult", 1.0),
    )

    def _getcommission(self, size, price, pseudoexec):
        if size > 0:
            # 买入: 只收佣金
            return abs(size) * price * self.p.commission
        else:
            # 卖出: 佣金 + 印花税
            return abs(size) * price * (self.p.commission + self.p.stamp_duty)


# ============================================================================ #
# 6. 参数优化 (多线程)
# ============================================================================ #
class ParameterOptimizer:
    """参数优化器"""

    def __init__(self, engine: BacktestEngine):
        self.engine = engine

    def optimize(self, param_grid: Dict[str, List], n_jobs: int = 4):
        """多线程参数优化"""
        try:
            from itertools import product
            import concurrent.futures

            params_list = list(product(*param_grid.values()))
            param_names = list(param_grid.keys())

            def run_backtest(params):
                param_dict = dict(zip(param_names, params))
                eng = BacktestEngine(
                    start_date=self.engine.start_date.strftime("%Y-%m-%d"),
                    end_date=self.engine.end_date.strftime("%Y-%m-%d"),
                    initial_cash=self.engine.initial_cash,
                )
                # 复用股票数据
                eng.add_stock_data([d._name.replace("stock_", "") for d in self.engine.cerebro.datas])
                eng.add_strategy(**param_dict)
                eng.add_analyzers()
                eng.run()
                results = eng.get_results()
                return (param_dict, results)

            with concurrent.futures.ThreadPoolExecutor(max_workers=n_jobs) as executor:
                futures = [executor.submit(run_backtest, p) for p in params_list]
                results = [f.result() for f in futures]

            # 按夏普比率排序
            results.sort(key=lambda x: x[1]["sharpe_ratio"], reverse=True)
            return results
        except Exception as exc:
            logger.error("[ParameterOptimizer] 优化失败: %s", exc)
            return []


if __name__ == "__main__":
    # 简单测试
    engine = BacktestEngine("2024-01-01", "2024-12-31")
    engine.add_stock_data(["002594", "300750", "600519"])
    engine.add_strategy()
    engine.add_analyzers()
    engine.run()
    results = engine.get_results()
    print("回测结果:", json.dumps(results, indent=2, ensure_ascii=False))
