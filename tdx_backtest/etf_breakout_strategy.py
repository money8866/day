# -*- coding: utf-8 -*-
"""
ETF 驱动趋势突破策略 (ETF-Driven Donchian Breakout)

三层结构:
  1) ETF 层: 趋势强度 + 动量 + 突破状态 → 只交易强势 ETF 成份股
  2) 个股层: Donchian 20日突破 + 量能放大 + EMA50 趋势过滤
  3) 出场层: ATR 止损 + EMA20 止盈 + 跌破10日最低 + ETF转弱

增强模块:
  - ETF 强度排序, 只交易 Top 30% ETF
  - 同时只持仓最强 3 个 ETF 的成份股
  - ST/低流动性/连续阴跌 去噪
"""
from __future__ import annotations
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# 项目路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "data_loader.py") and BASE_DIR)

from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code


# =========================================================
# ETF 池 (复用自 etf_mainline_strategy_tushare.py)
# =========================================================
ETF_POOL = {
    "沪深300": "510300.SH", "创业板": "159915.SZ", "上证50": "510050.SH",
    "双创ETF": "588300.SH", "科创ETF": "588050.SH",
    "半导体": "512480.SH", "芯片": "159995.SZ", "半导体设备": "159516.SZ",
    "人工智能": "159819.SZ", "软件": "515230.SH", "通信": "515880.SH",
    "消费电子": "159732.SZ", "金融科技": "159851.SZ", "游戏": "159869.SZ",
    "新能源": "516160.SH", "光伏": "515790.SH", "储能": "159566.SZ",
    "电池": "159755.SZ", "新能源车": "515030.SH",
    "创新药": "159992.SZ", "医疗器械": "159883.SZ", "医药": "512010.SH",
    "军工": "512660.SH", "航空航天": "159227.SZ", "机器人": "562500.SH",
    "有色金属": "516650.SH", "化工": "159870.SZ", "煤炭": "515220.SH",
    "钢铁": "515210.SH", "电力": "159611.SZ", "电网设备": "561380.SH",
    "消费": "159928.SZ", "食品饮料": "159736.SZ", "酒": "512690.SH",
    "家电": "159996.SZ", "证券": "512880.SH", "银行": "512800.SH",
    "红利": "515180.SH", "黄金": "518880.SH", "工业母机": "159667.SZ",
}


# =========================================================
# ETF 成份股获取 (调用 etf_mainline_strategy_tushare)
# =========================================================
def get_all_etf_constituents() -> Dict[str, List[str]]:
    """获取所有 ETF 的成份股列表

    Returns:
        {etf_code: [con_code1, con_code2, ...]}
    """
    cache_file = os.path.join(PROJECT_ROOT, "cache_daily",
                              "etf_constituents_all.json")
    # 尝试读取缓存
    if os.path.exists(cache_file):
        try:
            import json
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and len(data) > 5:
                    print(f"[ETF] 从缓存加载 {len(data)} 只 ETF 成份股")
                    return data
        except Exception:
            pass

    # 调用 etf_mainline_strategy_tushare 获取
    sys.path.insert(0, os.path.join(BASE_DIR, "..", "solo"))
    solo_dir = os.path.join(PROJECT_ROOT, "solo")
    if solo_dir not in sys.path:
        sys.path.insert(0, solo_dir)

    try:
        from etf_mainline_strategy_tushare import get_etf_constituents
    except ImportError:
        print("[ERROR] 无法导入 etf_mainline_strategy_tushare")
        return {}

    # 加载 Tushare token
    from dotenv import load_dotenv
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    if not os.path.exists(env_path):
        env_path = os.path.join(solo_dir, ".env")
    load_dotenv(env_path)

    import tushare as ts
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        # 尝试从 .env 读取
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
                        token = line.split("=", 1)[1].strip()
                        break
    if not token:
        print("[ERROR] 未找到 TUSHARE_TOKEN")
        return {}
    ts.set_token(token)

    result = {}
    for name, etf_code in ETF_POOL.items():
        try:
            cons = get_etf_constituents(etf_code)
            con_codes = [c["con_code"] for c in cons if c.get("con_code")]
            if con_codes:
                result[etf_code] = con_codes
                print(f"  {name}({etf_code}): {len(con_codes)} 只成份股")
        except Exception as e:
            print(f"  [WARN] {name}({etf_code}) 获取失败: {e}")
            continue
        time.sleep(0.3)

    # 缓存
    try:
        import json
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[ETF] 缓存 {len(result)} 只 ETF 成份股到 {cache_file}")
    except Exception:
        pass

    return result


# =========================================================
# 板块感知参数 (Board-Aware Config)
# =========================================================
@dataclass
class BoardConfig:
    """不同板块使用不同的突破/退出参数"""
    donchian_period: int       # 突破回看周期
    vol_ratio_min: float       # 量能放大倍数下限 (相对 20日均量)
    atr_stop_mult: float       # 止损 ATR 倍数
    atr_trail_mult: float      # Trailing ATR 倍数
    rsi_max: float             # RSI 上限 (避免过热)
    ema_period: int            # 趋势 EMA 周期
    max_hold: int              # 最大持有天数
    etf_weak_days: int         # ETF 弱势容忍天数


# 主板: 涨停10%、波动小 → 紧止损(减少大亏) + 宽 trailing(让盈利奔跑) + 量能站稳即可
# 双创板: 涨停20%、波动大 → 宽止损 + 让盈利奔跑
BOARD_CONFIGS = {
    "MB":  BoardConfig(donchian_period=15, vol_ratio_min=0.90,
                       atr_stop_mult=1.5, atr_trail_mult=3.5,
                       rsi_max=75, ema_period=50, max_hold=30,
                       etf_weak_days=4),
    "CYB": BoardConfig(donchian_period=20, vol_ratio_min=1.0,
                       atr_stop_mult=2.0, atr_trail_mult=3.0,
                       rsi_max=75, ema_period=50, max_hold=30,
                       etf_weak_days=3),
    "KCB": BoardConfig(donchian_period=20, vol_ratio_min=1.0,
                       atr_stop_mult=2.5, atr_trail_mult=4.0,
                       rsi_max=80, ema_period=50, max_hold=35,
                       etf_weak_days=3),
}


def get_board(ts_code: str) -> str:
    """根据 ts_code 返回板块标识: MB / CYB / KCB"""
    code6 = ts_code.split(".")[0]
    if code6.startswith("60") or code6.startswith("00"):
        return "MB"
    if code6.startswith("300") or code6.startswith("301"):
        return "CYB"
    if code6.startswith("688") or code6.startswith("689"):
        return "KCB"
    return "MB"  # 默认按主板处理


# =========================================================
# 指标引擎 (不用 TA-Lib)
# =========================================================
def ema(arr: np.ndarray, n: int) -> np.ndarray:
    """EMA 指标"""
    s = pd.Series(arr)
    return s.ewm(span=n, adjust=False).mean().values


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    """ATR 指标"""
    h = pd.Series(high)
    l = pd.Series(low)
    c = pd.Series(close)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean().values


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    """RSI 指标"""
    s = pd.Series(close)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).values


def rolling_max(arr: np.ndarray, n: int) -> np.ndarray:
    """滚动最大值 (shift 1, 不含当日)"""
    s = pd.Series(arr)
    return s.rolling(n).max().shift(1).values


def rolling_min(arr: np.ndarray, n: int) -> np.ndarray:
    """滚动最小值 (shift 1, 不含当日)"""
    s = pd.Series(arr)
    return s.rolling(n).min().shift(1).values


def rolling_mean(arr: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(arr)
    return s.rolling(n).mean().values


# =========================================================
# ETF 强势过滤
# =========================================================
@dataclass
class ETFState:
    """ETF 状态"""
    etf_code: str
    trade_date: str
    trend_score: float    # (EMA20-EMA60)/EMA60
    momentum: float       # 20日收益率
    breakout: bool        # close > 20日最高价
    is_strong: bool       # 强势状态
    etf_score: float      # 综合得分


def compute_etf_state(etf_df: pd.DataFrame, etf_code: str,
                      trade_date: str) -> Optional[ETFState]:
    """计算 ETF 在某交易日的状态"""
    df = etf_df[etf_df["trade_date"] <= trade_date]
    if len(df) < 60:
        return None
    last = df.iloc[-1]
    C = df["close"].values

    ema20 = ema(C, 20)
    ema60 = ema(C, 60)
    if np.isnan(ema20[-1]) or np.isnan(ema60[-1]) or ema60[-1] == 0:
        return None

    trend_score = (ema20[-1] - ema60[-1]) / ema60[-1]
    momentum = C[-1] / C[-21] - 1 if len(C) >= 21 else 0

    # 20日最高价 (不含当日)
    high_20_prev = rolling_max(df["high"].values, 20)
    if np.isnan(high_20_prev[-1]):
        return None
    breakout = C[-1] > high_20_prev[-1]
    breakout_strength = (C[-1] - high_20_prev[-1]) / high_20_prev[-1] if breakout else 0

    is_strong = (trend_score > 0) and (momentum > 0) and breakout
    etf_score = 0.4 * trend_score + 0.3 * momentum + 0.3 * breakout_strength

    return ETFState(
        etf_code=etf_code, trade_date=trade_date,
        trend_score=trend_score, momentum=momentum,
        breakout=breakout, is_strong=is_strong, etf_score=etf_score,
    )


def get_strong_etfs(etf_data: Dict[str, pd.DataFrame], trade_date: str,
                    top_pct: float = 0.3, max_etfs: int = 3
                    ) -> List[ETFState]:
    """获取某日强势 ETF 列表 (Top pct + 最多 max_etfs 个)"""
    states = []
    for etf_code, df in etf_data.items():
        st = compute_etf_state(df, etf_code, trade_date)
        if st and st.is_strong:
            states.append(st)
    # 按 etf_score 排序
    states.sort(key=lambda x: -x.etf_score)
    # Top pct
    n_top = max(1, int(len(ETF_POOL) * top_pct))
    top_states = states[:n_top]
    # 最多 max_etfs 个
    return top_states[:max_etfs]


# =========================================================
# 个股突破信号生成
# =========================================================
@dataclass
class Signal:
    """买入信号"""
    ts_code: str
    trade_date: str
    close: float
    atr: float
    etf_code: str
    etf_score: float
    reason: str


def check_stock_breakout(stock_df: pd.DataFrame, ts_code: str,
                         trade_date: str, etf_code: str,
                         etf_score: float,
                         cfg: Optional[BoardConfig] = None) -> Optional[Signal]:
    """检查个股突破信号 (板块感知参数)"""
    if cfg is None:
        cfg = BOARD_CONFIGS.get(get_board(ts_code), BOARD_CONFIGS["MB"])

    df = stock_df[stock_df["trade_date"] <= trade_date]
    if len(df) < max(50, cfg.donchian_period + 5):
        return None

    last = df.iloc[-1]
    C = df["close"].values
    H = df["high"].values
    VOL = df["vol"].values

    # Donchian N日突破 (不含当日, N 由板块决定)
    high_n_prev = rolling_max(H, cfg.donchian_period)
    if np.isnan(high_n_prev[-1]):
        return None
    if C[-1] <= high_n_prev[-1]:
        return None

    # 成交量 >= N日均量 × vol_ratio_min
    vol_ma20 = rolling_mean(VOL, 20)
    if np.isnan(vol_ma20[-1]) or VOL[-1] < vol_ma20[-1] * cfg.vol_ratio_min:
        return None

    # EMA 趋势过滤 (周期由板块决定)
    ema_trend = ema(C, cfg.ema_period)
    if np.isnan(ema_trend[-1]) or C[-1] <= ema_trend[-1]:
        return None

    # RSI < rsi_max (避免过热)
    rsi_val = rsi(C, 14)
    if np.isnan(rsi_val[-1]) or rsi_val[-1] >= cfg.rsi_max:
        return None

    # 去噪: 连续阴跌 (20日收益 < -10%)
    if len(C) >= 21 and C[-1] / C[-21] - 1 < -0.10:
        return None

    # 去噪: 低流动性 (成交额 < 1亿)
    amt = df["amount"].iloc[-1] if "amount" in df.columns else 0
    if amt > 0 and amt < 10000:  # tushare单位千元, 10000千=1亿
        return None

    # ATR
    atr_val = atr(H, df["low"].values, C, 14)
    if np.isnan(atr_val[-1]) or atr_val[-1] <= 0:
        return None

    return Signal(
        ts_code=ts_code, trade_date=trade_date,
        close=last["close"], atr=atr_val[-1],
        etf_code=etf_code, etf_score=etf_score,
        reason=f"Donchian{cfg.donchian_period}突破+量能{cfg.vol_ratio_min}x+EMA{cfg.ema_period}",
    )


# =========================================================
# 回测引擎
# =========================================================
@dataclass
class Position:
    """持仓"""
    ts_code: str
    etf_code: str
    entry_date: str
    entry_price: float
    atr_at_entry: float
    max_price: float
    board: str = "MB"          # 板块标识
    cfg: object = None         # BoardConfig
    hold_days: int = 0
    etf_weak_days: int = 0     # ETF连续弱势天数


@dataclass
class TradeRecord:
    """交易记录"""
    ts_code: str
    etf_code: str
    signal_date: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    hold_days: int
    return_pct: float
    exit_reason: str
    etf_score: float
    board: str = "MB"


class ETFBreakoutBacktester:
    """ETF 驱动突破策略回测"""

    def __init__(self, etf_constituents: Dict[str, List[str]],
                 start_date: str = "20250101",
                 end_date: str = None,
                 lookback_days: int = 400):
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.etf_constituents = etf_constituents

        # 收集所有需要加载的股票代码
        all_stocks: Set[str] = set()
        for con_list in etf_constituents.values():
            all_stocks.update(con_list)
        print(f"[Init] ETF 数: {len(etf_constituents)}, "
              f"成份股总数(去重): {len(all_stocks)}")

        # 加载 ETF K 线
        self.etf_data: Dict[str, pd.DataFrame] = {}
        self._load_etf_data()

        # 加载个股 K 线
        self.stock_data: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._load_stock_data(all_stocks, lookback_days)

        # 回测交易日 (用沪深300的交易日)
        ref_df = self.etf_data.get("510300.SH")
        if ref_df is None:
            for df in self.etf_data.values():
                ref_df = df
                break
        if ref_df is not None:
            self.trade_dates = sorted([d for d in ref_df["trade_date"]
                                        if self.start_date <= d <= self.end_date])
        else:
            self.trade_dates = []
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}")

    def _load_etf_data(self):
        """加载 ETF K 线 (TDX 本地数据)"""
        t0 = time.time()
        for name, etf_code in ETF_POOL.items():
            df = load_kline(etf_code, start_date="20240101",
                           end_date=self.end_date)
            if df.empty or len(df) < 60:
                continue
            self.etf_data[etf_code] = df
        print(f"[ETF] 加载 {len(self.etf_data)} 只 ETF, "
              f"耗时 {time.time()-t0:.1f}s")

    def _load_stock_data(self, all_stocks: Set[str], lookback_days: int):
        """加载个股 K 线"""
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=lookback_days)).strftime("%Y%m%d")

        t0 = time.time()
        n_ok, n_fail = 0, 0
        for i, ts_code in enumerate(sorted(all_stocks)):
            # 跳过格式无效的代码 (必须恰好1个点号)
            if ts_code.count(".") != 1:
                n_fail += 1
                continue
            df = load_kline(ts_code, start_date=load_start,
                           end_date=self.end_date)
            if df.empty or len(df) < 50:
                n_fail += 1
                continue
            self.stock_data[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            n_ok += 1
            if (i + 1) % 500 == 0:
                print(f"  [{i+1}/{len(all_stocks)}] 已加载 {n_ok} 只, "
                      f"耗时 {time.time()-t0:.1f}s")
        print(f"[Stock] 加载 {n_ok} 只, 失败 {n_fail}, "
              f"总耗时 {time.time()-t0:.1f}s")

    def _check_exit(self, pos: Position, df: pd.DataFrame, i: int,
                    etf_is_strong: bool) -> Tuple[bool, str]:
        """检查退出条件 (板块感知参数)"""
        cfg = pos.cfg or BOARD_CONFIGS[pos.board]
        if i >= len(df):
            return True, "数据结束"
        row = df.iloc[i]
        C = row["close"]
        H = df["high"].values
        L = df["low"].values
        Cl = df["close"].values

        # 更新最高价
        if C > pos.max_price:
            pos.max_price = C

        # 1. 止损: entry - atr_stop_mult * ATR (主板 1.5x, 双创 2-2.5x)
        stop_loss = pos.entry_price - cfg.atr_stop_mult * pos.atr_at_entry
        if C <= stop_loss:
            return True, f"止损({(C/pos.entry_price-1)*100:.1f}%)"

        # 2. Trailing stop: max_price - atr_trail_mult * ATR (主板 2.5x, 双创 3-4x)
        trailing = pos.max_price - cfg.atr_trail_mult * pos.atr_at_entry
        if C <= trailing:
            return True, f"Trailing止损({(C/pos.entry_price-1)*100:.1f}%)"

        # 3. 跌破 EMA20
        ema20 = ema(Cl, 20)
        if not np.isnan(ema20[i]) and C < ema20[i]:
            return True, "跌破EMA20"

        # 4. 跌破 10日最低价
        if i >= 10:
            low_10_prev = L[i-10:i].min()
            if C < low_10_prev:
                return True, "跌破10日最低"

        # 5. ETF 连续 cfg.etf_weak_days 天转弱才退出
        if not etf_is_strong:
            pos.etf_weak_days += 1
            if pos.etf_weak_days >= cfg.etf_weak_days:
                return True, f"ETF连续{cfg.etf_weak_days}天转弱"
        else:
            pos.etf_weak_days = 0

        # 6. 最大持有天数
        if pos.hold_days >= cfg.max_hold:
            return True, f"最大持有{cfg.max_hold}天"

        return False, ""

    def run_backtest(self, max_positions: int = 5,
                     verbose: bool = True) -> Dict:
        """完整回测"""
        holdings: Dict[str, Position] = {}
        trade_records: List[TradeRecord] = []
        daily_etf_strong_count = []
        daily_signal_count = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            # ===== 1. 检查退出 =====
            to_remove = []
            for ts_code, pos in holdings.items():
                df = self.stock_data.get(ts_code)
                if df is None:
                    to_remove.append(ts_code)
                    continue
                idx_map = self._date_idx_map[ts_code]
                cur_i = idx_map.get(td)
                if cur_i is None:
                    continue
                pos.hold_days += 1

                # 检查 ETF 是否仍强势
                etf_df = self.etf_data.get(pos.etf_code)
                etf_is_strong = False
                if etf_df is not None:
                    st = compute_etf_state(etf_df, pos.etf_code, td)
                    etf_is_strong = st.is_strong if st else False

                should_exit, reason = self._check_exit(pos, df, cur_i, etf_is_strong)
                if should_exit:
                    sell_price = df.iloc[cur_i]["close"]
                    ret = (sell_price / pos.entry_price - 1) * 100
                    trade_records.append(TradeRecord(
                        ts_code=ts_code, etf_code=pos.etf_code,
                        signal_date=pos.entry_date, buy_date=pos.entry_date,
                        buy_price=pos.entry_price,
                        sell_date=td, sell_price=round(sell_price, 2),
                        hold_days=pos.hold_days, return_pct=round(ret, 2),
                        exit_reason=reason, etf_score=0,
                        board=pos.board,
                    ))
                    to_remove.append(ts_code)
            for tc in to_remove:
                holdings.pop(tc, None)

            # ===== 2. 获取强势 ETF =====
            strong_etfs = get_strong_etfs(self.etf_data, td, top_pct=0.3,
                                          max_etfs=3)
            daily_etf_strong_count.append(len(strong_etfs))

            # 强势 ETF 的成份股集合
            strong_stocks: Set[str] = set()
            etf_score_map = {}
            for st in strong_etfs:
                con_list = self.etf_constituents.get(st.etf_code, [])
                for sc in con_list:
                    if sc not in holdings and sc in self.stock_data:
                        strong_stocks.add(sc)
                        etf_score_map[sc] = (st.etf_code, st.etf_score)

            # ===== 3. 生成买入信号 =====
            signals: List[Signal] = []
            for ts_code in strong_stocks:
                if ts_code in holdings:
                    continue
                if len(holdings) + len(signals) >= max_positions:
                    break
                etf_code, etf_score = etf_score_map.get(ts_code, ("", 0))
                df = self.stock_data[ts_code]
                sig = check_stock_breakout(df, ts_code, td, etf_code, etf_score)
                if sig:
                    signals.append(sig)
            daily_signal_count.append(len(signals))

            # ===== 4. 买入 (T+1 开盘) =====
            for sig in signals:
                if len(holdings) >= max_positions:
                    break
                df = self.stock_data[sig.ts_code]
                idx_map = self._date_idx_map[sig.ts_code]
                cur_i = idx_map.get(td)
                if cur_i is None or cur_i + 1 >= len(df):
                    continue
                buy_idx = cur_i + 1
                buy_row = df.iloc[buy_idx]
                buy_price = buy_row["open"]
                buy_date = buy_row["trade_date"]
                # 涨停跳过
                zt_up = 1.198 if sig.ts_code.startswith(("3", "688", "689")) else 1.098
                prev_close = df.iloc[cur_i]["close"]
                if buy_price >= prev_close * zt_up * 0.999:
                    continue
                board = get_board(sig.ts_code)
                holdings[sig.ts_code] = Position(
                    ts_code=sig.ts_code, etf_code=sig.etf_code,
                    entry_date=buy_date, entry_price=buy_price,
                    atr_at_entry=sig.atr, max_price=buy_price,
                    board=board, cfg=BOARD_CONFIGS[board],
                )

            if verbose and (i % 20 == 0 or i == len(self.trade_dates) - 1):
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(self.trade_dates) - i - 1)
                print(f"  [{i+1}/{len(self.trade_dates)}] {td}: "
                      f"强势ETF {len(strong_etfs)}, 信号 {len(signals)}, "
                      f"持仓 {len(holdings)}, 已平仓 {len(trade_records)}, "
                      f"耗时 {elapsed:.1f}s, ETA {eta:.0f}s")

        # ===== 强制平仓 =====
        last_td = self.trade_dates[-1] if self.trade_dates else None
        for ts_code, pos in holdings.items():
            df = self.stock_data.get(ts_code)
            if df is None or last_td is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            cur_i = idx_map.get(last_td, len(df) - 1)
            sell_price = df.iloc[cur_i]["close"]
            ret = (sell_price / pos.entry_price - 1) * 100
            trade_records.append(TradeRecord(
                ts_code=ts_code, etf_code=pos.etf_code,
                signal_date=pos.entry_date, buy_date=pos.entry_date,
                buy_price=pos.entry_price,
                sell_date=last_td, sell_price=round(sell_price, 2),
                hold_days=pos.hold_days, return_pct=round(ret, 2),
                exit_reason="末尾平仓", etf_score=0,
                board=pos.board,
            ))

        # ===== 统计 =====
        returns = [r.return_pct for r in trade_records]
        rets = np.array(returns) if returns else np.array([0])
        win_rate = (rets > 0).mean() * 100 if len(returns) > 0 else 0
        avg_ret = rets.mean() if len(returns) > 0 else 0
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
        pf = avg_win / avg_loss if avg_loss > 0 else float("inf")

        # 退出原因统计
        exit_reasons = {}
        for r in trade_records:
            reason = r.exit_reason
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # 持有天数
        hold_days = [r.hold_days for r in trade_records]

        # 盈利来源分布 (前20%交易贡献比例)
        if returns:
            sorted_rets = sorted(returns, reverse=True)
            top_20pct_n = max(1, len(sorted_rets) // 5)
            top_20pct_sum = sum(sorted_rets[:top_20pct_n])
            total_sum = sum(returns)
            profit_concentration = top_20pct_sum / total_sum if total_sum > 0 else 0
        else:
            profit_concentration = 0

        # ETF 过滤触发率
        etf_filter_rate = (sum(1 for c in daily_etf_strong_count if c > 0) /
                           max(len(daily_etf_strong_count), 1))

        return {
            "trade_records": trade_records,
            "all_returns": returns,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "profit_factor": round(pf, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "n_signals": len(trade_records),
            "avg_hold_days": round(np.mean(hold_days), 1) if hold_days else 0,
            "exit_reasons": exit_reasons,
            "profit_concentration": round(profit_concentration, 2),
            "etf_filter_rate": round(etf_filter_rate * 100, 1),
            "daily_signal_count": daily_signal_count,
            "n_total_days": len(self.trade_dates),
        }


# =========================================================
# 主入口
# =========================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="ETF驱动趋势突破策略")
    parser.add_argument("--start", type=str, default="20250101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--max-positions", type=int, default=5,
                        help="最大同时持仓数")
    args = parser.parse_args()

    # 获取 ETF 成份股
    etf_cons = get_all_etf_constituents()
    if not etf_cons:
        print("[ERROR] 未获取到 ETF 成份股")
        return

    total_stocks = sum(len(v) for v in etf_cons.values())
    print(f"\n[Summary] {len(etf_cons)} 只 ETF, 共 {total_stocks} 个成份股")

    # 回测
    bt = ETFBreakoutBacktester(etf_cons, start_date=args.start,
                               end_date=args.end)
    res = bt.run_backtest(max_positions=args.max_positions)

    # 输出结果
    print("\n" + "=" * 70)
    print("  ETF 驱动趋势突破策略 - 回测结果")
    print("=" * 70)
    print(f"  回测区间:       {args.start} ~ {args.end or '最新'}")
    print(f"  交易日数:       {res['n_total_days']}")
    print(f"  最大持仓数:     {args.max_positions}")
    print(f"  ----")
    print(f"  总信号数:       {res['n_signals']}")
    print(f"  胜率:           {res['win_rate']}%")
    print(f"  平均收益:       {res['avg_return']}%")
    print(f"  盈亏比:         {res['profit_factor']}")
    print(f"  平均盈利:       {res['avg_win']}%")
    print(f"  平均亏损:       -{res['avg_loss']}%")
    print(f"  平均持有天数:   {res['avg_hold_days']}")
    print(f"  ETF过滤触发率:  {res['etf_filter_rate']}%")

    if res["all_returns"]:
        rets = np.array(res["all_returns"])
        print(f"  最大盈利:       {rets.max():.2f}%")
        print(f"  最大亏损:       {rets.min():.2f}%")

    print(f"\n  策略统计分解:")
    print(f"    盈利来源集中度 (前20%交易贡献): {res['profit_concentration']*100:.1f}%")

    print(f"\n  退出原因统计:")
    for reason, cnt in sorted(res["exit_reasons"].items(),
                               key=lambda x: -x[1]):
        pct = cnt / res["n_signals"] * 100 if res["n_signals"] > 0 else 0
        print(f"    {reason:20s}: {cnt:4d} 笔 ({pct:.1f}%)")

    # 按 ETF 分析
    if res["trade_records"]:
        df_tr = pd.DataFrame([
            {"etf_code": r.etf_code, "return": r.return_pct,
             "hold_days": r.hold_days, "board": r.board,
             "ts_code": r.ts_code}
            for r in res["trade_records"]
        ])
        print(f"\n  按 ETF 分析:")
        for etf_code, grp in df_tr.groupby("etf_code"):
            wr = (grp["return"] > 0).mean() * 100
            avg = grp["return"].mean()
            print(f"    {etf_code:12s}: {len(grp):3d}笔, "
                  f"胜率{wr:5.1f}%, 均收益{avg:6.2f}%, "
                  f"均持有{grp['hold_days'].mean():.1f}天")

        # 按板块分析
        print(f"\n  按板块分析 (板块感知参数效果):")
        board_names = {"MB": "主板", "CYB": "创业板", "KCB": "科创板"}
        for board, grp in df_tr.groupby("board"):
            wr = (grp["return"] > 0).mean() * 100
            avg = grp["return"].mean()
            wins = grp[grp["return"] > 0]["return"]
            losses = grp[grp["return"] < 0]["return"]
            avg_win = wins.mean() if len(wins) else 0
            avg_loss = -losses.mean() if len(losses) else 0
            pf = avg_win / avg_loss if avg_loss > 0 else float("inf")
            print(f"    {board_names.get(board, board):6s} ({board}): "
                  f"{len(grp):3d}笔, 胜率{wr:5.1f}%, 均收益{avg:6.2f}%, "
                  f"盈亏比{pf:5.2f}, 均持有{grp['hold_days'].mean():.1f}天, "
                  f"均盈{avg_win:5.2f}%, 均亏-{avg_loss:5.2f}%")

    # 保存交易记录
    if res["trade_records"]:
        out_path = os.path.join(PROJECT_ROOT, "solo", "report_daily",
                                 "etf_breakout_trades.csv")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        pd.DataFrame([
            {"ts_code": r.ts_code, "etf_code": r.etf_code,
             "signal_date": r.signal_date, "buy_date": r.buy_date,
             "buy_price": r.buy_price, "sell_date": r.sell_date,
             "sell_price": r.sell_price, "hold_days": r.hold_days,
             "return": r.return_pct, "exit_reason": r.exit_reason,
             "board": r.board}
            for r in res["trade_records"]
        ]).to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  [交易记录已保存] {out_path}")


if __name__ == "__main__":
    main()
