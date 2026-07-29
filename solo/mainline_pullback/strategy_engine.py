"""
策略计算引擎 StrategyEngine

实现三层选股逻辑（A → B → C 逐层过滤）：
  A. 基础硬过滤（ST剔除、上市天数、流动性）
  B. 主升浪动量筛选（涨幅30%+、涨停/大阳线）
  C. 首次回踩缩量止跌（均线支撑、缩量、回撤位置）

通过后对所有标的进行综合评分 + 盈亏比估算。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

import stock_cache as sc
from .config import get_config
from .data_loader import load_local_data, get_last_trade_date

logger = logging.getLogger("mainline_pullback.strategy_engine")


# ──────────────────────────────────────────────
# 输出数据结构
# ──────────────────────────────────────────────

@dataclass
class StockResult:
    """单只股票的分析结果"""
    ts_code: str
    name: str                                  # 股票名称
    trade_date: str                            # 最新交易日
    close: float                               # 最新收盘价
    pct_chg: float                             # 最新涨跌幅
    amount: float                              # 最新成交额（亿元）
    total_mv: float                            # 总市值（亿元）

    # A层：基础过滤（结果）
    pass_a: bool = False

    # B层：主升浪动量
    pass_b: bool = False
    wave_high: float = 0.0                     # 第一波最高价
    wave_low: float = 0.0                      # 第一波最低价
    wave_gain_pct: float = 0.0                 # 第一波涨幅 %
    limit_up_count: int = 0                    # 涨停/大阳计数

    # C层：首次回踩
    pass_c: bool = False
    support_ma: int = 0                        # 支撑均线（10或20）
    support_price: float = 0.0                 # 支撑均线价格
    dist_to_ma_pct: float = 0.0                # 距均线幅度 %
    pullback_from_high: float = 0.0            # 距高点回撤 %
    vol_peak: float = 0.0                      # 峰值量
    vol_shrink_ratio: float = 0.0              # 缩量比例

    # 评分
    score: float = 0.0                         # 综合评分 0-100
    wave_score: float = 0.0                    # 波动强度分
    pullback_score: float = 0.0                # 回踩质量分
    shrink_score: float = 0.0                  # 缩量分
    support_score: float = 0.0                 # 支撑吻合度分
    volume_ratio_score: float = 0.0            # 量比合理性分

    # 盈亏比估算
    risk_reward_ratio: float = 0.0             # 盈亏比
    entry_price: float = 0.0                   # 建议入场价
    stop_loss: float = 0.0                     # 止损价
    take_profit: float = 0.0                   # 止盈价（第一预期）

    # 精准买点信号（来自 _refine_buy_point）
    buy_signal: str = ""                       # READY / WATCH / WAIT
    buy_readiness: float = 0.0                 # 买入 readiness 分 0-100
    buy_price_low: float = 0.0                 # 买入区间下限
    buy_price_high: float = 0.0                # 买入区间上限
    kdj_j: float = 0.0                        # KDJ  J值
    kdj_turn: str = ""                         # KDJ拐头方向 up/down/-
    rsi_6: float = 0.0                        # RSI 6
    macd_dif: float = 0.0                     # MACD DIF
    macd_dea: float = 0.0                     # MACD DEA
    bb_lower: float = 0.0                     # 布林下轨
    candle_pattern: str = ""                   # 蜡烛形态 small/large/doji
    consecutive_down: int = 0                  # 连跌天数


# ──────────────────────────────────────────────
# 策略引擎
# ──────────────────────────────────────────────

class StrategyEngine:
    """主线龙头首次回踩选股引擎

    使用方式：
        engine = StrategyEngine()
        results = engine.run(trade_date="20260724")
    """

    def __init__(self) -> None:
        self.cfg = get_config()
        self._stock_basic: Optional[pd.DataFrame] = None
        self._latest_daily: Optional[pd.DataFrame] = None
        self._name_map: dict[str, str] = {}       # ts_code → name

    # ══════════════════════════════════════════════
    # 入口
    # ══════════════════════════════════════════════

    def run(self, trade_date: str = "") -> list[StockResult]:
        """执行全市场扫描。

        Args:
            trade_date: 交易日 YYYYMMDD，留空则自动识别。

        Returns:
            按评分降序排列的 StockResult 列表（仅 pass_c 的股票）。
        """
        td = trade_date or get_last_trade_date()
        logger.info("=" * 60)
        logger.info("【主线龙头+首次回踩】选股引擎启动")
        logger.info("交易日: %s", td)
        logger.info("=" * 60)

        # ── 0. 预加载基础数据 ──
        self._load_basic_data(td)

        # ── 1. A层：基础硬过滤 ──
        candidates_a = self._hard_filter(td)
        logger.info("[A层] 基础过滤通过: %d 只", len(candidates_a))

        # ── 2. C层：首次回踩检测（含主升浪动量校验，逐只并发分析）──
        # 使用 stock_cache.cached_stk_factor_pro 加载个股历史数据，
        # 确保 MA、涨幅、涨停计数等计算有足够的历史窗口
        results = self._pullback_scan(candidates_a, td)
        results.sort(key=lambda r: r.score, reverse=True)

        logger.info("[C层] 回踩信号通过: %d 只", sum(1 for r in results if r.pass_c))
        logger.info("选股完成: 共 %d 只入选", len(results))
        return results

    # ══════════════════════════════════════════════
    # 0. 预加载
    # ══════════════════════════════════════════════

    def _load_basic_data(self, trade_date: str) -> None:
        """加载 stock_basic 和当日全市场日线快照"""
        # stock_basic
        sb = load_local_data("stock_basic")
        if sb is not None and not sb.empty:
            self._stock_basic = sb
            self._name_map = dict(zip(sb["ts_code"], sb["name"]))

        # 当日快照
        daily = load_local_data("daily", trade_date=trade_date)
        if daily is not None and not daily.empty:
            self._latest_daily = daily

    # ══════════════════════════════════════════════
    # A层：基础硬过滤（向量化）
    # ══════════════════════════════════════════════

    def _hard_filter(self, trade_date: str) -> list[str]:
        """A层过滤 — 返回通过基础过滤的 ts_code 列表"""
        if self._stock_basic is None:
            logger.error("stock_basic 未加载")
            return []
        if self._latest_daily is None:
            logger.error("日线快照未加载: %s", trade_date)
            return []

        cfg = self.cfg.hard_filter
        sb = self._stock_basic
        daily = self._latest_daily

        # ── 1.1 剔除 ST / *ST / 退市 ──
        import re
        name_col = sb["name"].astype(str)
        st_pattern = "|".join(re.escape(kw) for kw in cfg.st_keywords)
        st_mask = name_col.str.contains(st_pattern, na=False, regex=True)
        sb_valid = sb[~st_mask].copy()
        logger.debug("  A1 ST剔除: %d → %d", len(sb), len(sb_valid))

        # ── 1.1b 剔除北交所（代码 8/4/92 开头）──
        bj_mask = sb_valid["ts_code"].str.match(r'^[489]\d|^92\d')
        sb_valid = sb_valid[~bj_mask].copy()
        sb_valid = sb_valid[~sb_valid["ts_code"].str.endswith('.BJ')].copy()
        logger.debug("  A1b 北交所剔除: %d", len(sb_valid))

        # ── 1.2 上市天数 ≥ 60 ──
        sb_valid["list_date"] = sb_valid["list_date"].astype(str)
        sb_valid["list_days"] = (
            pd.to_datetime(trade_date, format="%Y%m%d")
            - pd.to_datetime(sb_valid["list_date"], format="%Y%m%d", errors="coerce")
        ).dt.days
        sb_valid = sb_valid[sb_valid["list_days"] >= cfg.min_listing_days].copy()
        logger.debug("  A2 上市天数: %d", len(sb_valid))

        # ── 1.3 前日成交额 ≥ 3000万 ──
        daily["amount"] = pd.to_numeric(daily["amount"], errors="coerce").fillna(0)
        daily_high_amount = daily[daily["amount"] >= cfg.min_amount]
        valid_codes = set(sb_valid["ts_code"]) & set(daily_high_amount["ts_code"])
        logger.debug("  A3 成交额过滤: %d", len(valid_codes))

        return sorted(valid_codes)

    # ══════════════════════════════════════════════
    # C层（含B层）：首次回踩检测 + 主升浪动量校验
    # ══════════════════════════════════════════════

    def _pullback_scan(self, codes: list[str], trade_date: str) -> list[StockResult]:
        """C层 — 对候选股逐只计算回踩信号（并发执行）"""
        cfg = self.cfg.pullback
        lookback = self.cfg.momentum.lookback_days
        # 需要额外的历史数据算 MA10/MA20
        history_days = lookback + 30
        start_date = self._calc_start_date(trade_date, history_days)

        results: list[StockResult] = []
        max_workers = self.cfg.max_workers

        # 批量预加载所有个股的日线缓存（利用内存缓存）
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for code in codes:
                fut = executor.submit(
                    self._analyze_single_stock, code, trade_date, start_date
                )
                future_map[fut] = code

            for fut in as_completed(future_map):
                code = future_map[fut]
                try:
                    result = fut.result()
                    if result is not None:
                        results.append(result)
                except Exception as exc:
                    logger.warning("[C层] %s 分析异常: %s", code, exc)
                # 简单限流
                time.sleep(0.01)

        return results

    def _analyze_single_stock(
        self, ts_code: str, trade_date: str, start_date: str
    ) -> Optional[StockResult]:
        """分析单只股票的回踩信号"""
        cfg_pullback = self.cfg.pullback
        cfg_momentum = self.cfg.momentum
        cfg_scoring = self.cfg.scoring

        # ── 1. 加载个股日线数据（通过 stock_cache 缓存，SQLite+API 自动补全）──
        df = sc.cached_stk_factor_pro(ts_code, start_date, trade_date, silent=True)
        if df is None or len(df) < 30:
            return None

        # 数值类型转换
        for col in ["open", "high", "low", "close", "vol", "amount", "pct_chg"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        df = df.sort_values("trade_date").reset_index(drop=True)

        # ── 2. 取最新日（今日）数据 ──
        today = df.iloc[-1]
        today_close = today["close"]
        today_low = today["low"]
        today_vol = today["vol"]
        today_amount = today["amount"]
        today_pct = today["pct_chg"]

        # ── 3. 计算 MA10 / MA20 ──
        ma10 = df["close"].rolling(window=10, min_periods=10).mean().iloc[-1]
        ma20 = df["close"].rolling(window=20, min_periods=20).mean().iloc[-1]

        # ── 4. 回看 lookback 天找第一波高点 ──
        recent = df.tail(cfg_momentum.lookback_days)
        peak_idx = recent["high"].idxmax()
        peak_row = df.loc[peak_idx]
        wave_high = peak_row["high"]

        # 波段的起点：取最高点之前20天的最低价
        look_start = max(0, peak_idx - 20)
        segment = df.iloc[look_start : peak_idx + 1]
        wave_low = segment["low"].min()
        wave_gain = wave_high / wave_low if wave_low > 0 else 1.0

        # ── 5. 计数涨停/大阳线 ──
        recent_pct = recent["pct_chg"]
        limit_count = int(
            ((recent_pct >= cfg_momentum.limit_up_threshold) |
             (recent_pct >= cfg_momentum.big_positive_threshold)).sum()
        )

        # ── 6. 回测计算 ──
        pullback_pct = (wave_high - today_close) / wave_high

        # 距 MA10 / MA20 距离
        dist_ma10 = (today_close - ma10) / ma10 if ma10 > 0 else 999
        dist_ma20 = (today_close - ma20) / ma20 if ma20 > 0 else 999

        # 判断是否回踩到支撑位
        on_support = False
        support_ma = 0
        support_price = 0.0
        dist_to_ma = 999.0

        for ma_period in cfg_pullback.ma_periods:
            ma_val = ma10 if ma_period == 10 else ma20
            dist = dist_ma10 if ma_period == 10 else dist_ma20
            if abs(dist) <= cfg_pullback.support_tolerance:
                on_support = True
                support_ma = ma_period
                support_price = ma_val
                dist_to_ma = dist
                break

        # 如果没有精确 ±2% 内，取距离最近的均线
        if not on_support:
            dists = [(10, dist_ma10, ma10), (20, dist_ma20, ma20)]
            best = min(dists, key=lambda x: abs(x[1]))
            support_ma, dist_to_ma, support_price = best

        # ── 7. 缩量计算 ──
        # 第一波主升浪前后5日峰值量
        before_peak = max(peak_idx - cfg_pullback.vol_peak_window, 0)
        after_peak = min(peak_idx + cfg_pullback.vol_peak_window, len(df) - 1)
        vol_peak = df.iloc[before_peak : after_peak + 1]["vol"].max()
        vol_shrink = today_vol / vol_peak if vol_peak > 0 else 1.0

        # ── 8. 构建输出 ──
        result = StockResult(
            ts_code=ts_code,
            name=self._name_map.get(ts_code, ""),
            trade_date=trade_date,
            close=round(today_close, 2),
            pct_chg=round(today_pct, 2),
            amount=round(today_amount / 1e5, 2),   # 千元→亿元
            total_mv=0.0,
            # A层（已通过）
            pass_a=True,
            # B层
            pass_b=True,
            wave_high=round(wave_high, 2),
            wave_low=round(wave_low, 2),
            wave_gain_pct=round((wave_gain - 1) * 100, 2),
            limit_up_count=limit_count,
            # C层
            support_ma=support_ma,
            support_price=round(support_price, 2),
            dist_to_ma_pct=round(dist_to_ma * 100, 2),
            pullback_from_high=round(pullback_pct * 100, 2),
            vol_peak=round(vol_peak, 0),
            vol_shrink_ratio=round(vol_shrink, 4),
        )

        # ── B层重新校验 ──
        result.pass_b = (wave_gain >= cfg_momentum.min_wave_gain
                         and limit_count >= cfg_momentum.min_limit_up_count)

        if not result.pass_b:
            return None

        # ── C层判断 ──
        pullback_ok = (
            cfg_pullback.min_pullback <= pullback_pct <= cfg_pullback.max_pullback
        )
        shrink_ok = vol_shrink < cfg_pullback.max_vol_ratio
        support_ok = abs(dist_to_ma) <= cfg_pullback.support_tolerance

        result.pass_c = pullback_ok and shrink_ok and support_ok

        if not result.pass_c:
            return None

        # ── 8b. 精准买点细化 ──
        self._refine_buy_point(
            df, result,
            support_ma=support_ma,
            support_price=support_price,
            dist_to_ma=dist_to_ma,
            vol_shrink=vol_shrink,
        )

        # ── 9. 评分计算 ──
        self._calc_score(result, cfg_scoring)

        # ── 10. 盈亏比估算 ──
        self._calc_risk_reward(result)

        return result

    # ══════════════════════════════════════════════
    # 评分系统
    # ══════════════════════════════════════════════

    def _calc_score(self, r: StockResult, cfg: Any) -> None:
        """计算综合评分（0-100）"""
        # ── 1. 波动力度分（wave_score）──
        # 涨幅越高越好 30%→60分, 50%→80分, 80%+→100分
        w = r.wave_gain_pct
        if w >= 80:
            r.wave_score = 100.0
        elif w >= 50:
            r.wave_score = 80.0 + (w - 50) / 30 * 20
        elif w >= 30:
            r.wave_score = 60.0 + (w - 30) / 20 * 20
        else:
            r.wave_score = max(0, w / 30 * 60)

        # 涨停/大阳频次加分
        r.wave_score = min(100, r.wave_score + min(r.limit_up_count * 5, 15))

        # ── 2. 回踩质量分（pullback_score）──
        # 回撤越接近中间值15%越好
        pb = r.pullback_from_high
        ideal_pb = 15.0  # 理想回撤15%
        pb_diff = abs(pb - ideal_pb)
        if pb_diff <= 3:
            r.pullback_score = 100.0
        elif pb_diff <= 5:
            r.pullback_score = 85.0
        elif pb_diff <= 8:
            r.pullback_score = 60.0
        else:
            r.pullback_score = 40.0

        # ── 3. 缩量分（shrink_score）──
        sr = r.vol_shrink_ratio
        if sr <= 0.3:
            r.shrink_score = 100.0
        elif sr <= 0.4:
            r.shrink_score = 90.0
        elif sr <= 0.5:
            r.shrink_score = 75.0
        elif sr <= 0.6:
            r.shrink_score = 60.0
        else:
            r.shrink_score = max(0, 60 - (sr - 0.6) / 0.4 * 40)

        # ── 4. 支撑吻合度（support_score）──
        dist = abs(r.dist_to_ma_pct)
        if dist <= 0.5:
            r.support_score = 100.0
        elif dist <= 1.0:
            r.support_score = 85.0
        elif dist <= 1.5:
            r.support_score = 65.0
        elif dist <= 2.0:
            r.support_score = 50.0
        else:
            r.support_score = max(0, 50 - (dist - 2.0) * 15)

        # ── 5. 量比合理性（volume_ratio_score）──
        # 量比 = 今日量 / 近20日均量
        # 这里用 vol_shrink 近似 — 极度缩量才合理
        vs = r.vol_shrink_ratio
        if vs <= 0.35:
            r.volume_ratio_score = 100.0
        elif vs <= 0.5:
            r.volume_ratio_score = 80.0
        elif vs <= 0.6:
            r.volume_ratio_score = 60.0
        else:
            r.volume_ratio_score = max(0, 60 - (vs - 0.6) / 0.4 * 40)

        # ── 综合 ──
        r.score = (
            r.wave_score * cfg.wave_momentum_weight
            + r.pullback_score * cfg.pullback_quality_weight
            + r.shrink_score * cfg.volume_shrink_weight
            + r.support_score * cfg.support_alignment_weight
            + r.volume_ratio_score * cfg.volume_ratio_weight
        )
        r.score = round(r.score, 2)

    # ══════════════════════════════════════════════
    # 盈亏比估算
    # ══════════════════════════════════════════════

    def _calc_risk_reward(self, r: StockResult) -> None:
        """估算盈亏比"""
        # 入场价 = 当前收盘价
        r.entry_price = r.close

        # 止损价 = 支撑均线下方 3% (或 支撑价 * 0.97)
        r.stop_loss = round(r.support_price * 0.97, 2)

        # 止盈价 = 第一波高点（历史前高）的 90%
        # 前高由于回撤已超过10%，向上空间可看前高附近
        first_target = r.wave_high * 0.95  # 保守看前高95%

        # 也看支撑均线向上 15~20%
        ma_target = r.support_price * 1.15

        r.take_profit = round(max(first_target, ma_target), 2)

        # 盈亏比
        profit_potential = r.take_profit - r.entry_price
        loss_risk = r.entry_price - r.stop_loss

        if loss_risk > 0 and profit_potential > 0:
            r.risk_reward_ratio = round(profit_potential / loss_risk, 2)
        else:
            r.risk_reward_ratio = 0.0

    # ══════════════════════════════════════════════
    # 精准买点细化
    # ══════════════════════════════════════════════

    def _refine_buy_point(
        self,
        df: pd.DataFrame,
        result: StockResult,
        support_ma: int,
        support_price: float,
        dist_to_ma: float,
        vol_shrink: float,
    ) -> None:
        """利用因子数据（KDJ/RSI/MACD/布林带/蜡烛形态）判定精确买点

        输出写入 result 的 buy_signal / buy_readiness / buy_price_low~high 等字段。
        """
        latest = df.iloc[-1]

        # ── 1. 读取因子值 ──
        kdj_j = float(latest.get("kdj_bfq", latest.get("kdj_j", 50)))
        kdj_k = float(latest.get("kdj_k_bfq", latest.get("kdj_k", 50)))
        rsi_6 = float(latest.get("rsi_bfq_6", latest.get("rsi_6", 50)))
        macd_dif = float(latest.get("macd_dif_bfq", latest.get("macd_dif", 0)))
        macd_dea = float(latest.get("macd_dea_bfq", latest.get("macd_dea", 0)))
        bb_lower = float(latest.get("boll_lower_bfq", latest.get("boll_lower", 0)))
        bb_mid = float(latest.get("boll_mid_bfq", latest.get("boll_mid", 0)))
        open_p = float(latest.get("open", 0))
        close_p = float(latest.get("close", 0))
        high_p = float(latest.get("high", 0))
        low_p = float(latest.get("low", 0))

        # ── 2. KDJ拐头方向 ──
        kdj_turn = "-"
        if len(df) >= 2:
            prev_j = float(df.iloc[-2].get("kdj_bfq", df.iloc[-2].get("kdj_j", 50)))
            if kdj_j > prev_j:
                kdj_turn = "up"
            elif kdj_j < prev_j:
                kdj_turn = "down"

        # ── 3. 蜡烛形态判断 ──
        body = abs(close_p - open_p)
        candle_range = high_p - low_p if high_p > low_p else body * 2
        body_ratio = body / candle_range if candle_range > 0 else 1.0

        if body_ratio < 0.15:
            candle_pattern = "doji"
        elif body_ratio < 0.4:
            candle_pattern = "small"
        elif body_ratio < 0.7:
            candle_pattern = "moderate"
        else:
            candle_pattern = "large"

        # ── 4. 连跌天数 ──
        consecutive_down = 0
        for i in range(len(df) - 1, 0, -1):
            pct = float(df.iloc[i].get("pct_chg", 0))
            if pct < 0:
                consecutive_down += 1
            else:
                break

        # ── 5. 买入 readiness 评分 ──
        # 5a) 支撑距离分 (30%)
        abs_dist = abs(dist_to_ma) * 100  # 转百分比
        if abs_dist <= 0.5:
            dist_score = 100.0
        elif abs_dist <= 1.0:
            dist_score = 80.0
        elif abs_dist <= 2.0:
            dist_score = 60.0
        elif abs_dist <= 3.0:
            dist_score = 40.0
        else:
            dist_score = max(0, 40 - (abs_dist - 3.0) * 10)

        # 5b) 缩量分 (20%)
        if vol_shrink <= 0.3:
            vol_score = 100.0
        elif vol_shrink <= 0.4:
            vol_score = 90.0
        elif vol_shrink <= 0.5:
            vol_score = 75.0
        elif vol_shrink <= 0.6:
            vol_score = 60.0
        else:
            vol_score = max(0, 60 - (vol_shrink - 0.6) * 100)

        # 5c) KDJ超卖分 (25%)
        if kdj_j < 10:
            kdj_score = 100.0
        elif kdj_j < 20:
            kdj_score = 85.0
        elif kdj_j < 35:
            kdj_score = 65.0
        else:
            kdj_score = 40.0
        # KDJ拐头向上加分
        if kdj_turn == "up":
            kdj_score = min(100, kdj_score + 15)

        # 5d) RSI分 (15%)
        if rsi_6 < 25:
            rsi_score = 100.0
        elif rsi_6 < 35:
            rsi_score = 80.0
        elif rsi_6 < 45:
            rsi_score = 60.0
        else:
            rsi_score = 40.0

        # 5e) 蜡烛形态分 (10%)
        if candle_pattern in ("doji", "small"):
            candle_score = 100.0
        elif candle_pattern == "moderate":
            candle_score = 70.0
        else:
            candle_score = 40.0
        # 阳线在支撑位加分
        if close_p >= open_p and abs_dist <= 1.0:
            candle_score = min(100, candle_score + 10)

        # 综合分
        buy_readiness = (
            dist_score * 0.30
            + vol_score * 0.20
            + kdj_score * 0.25
            + rsi_score * 0.15
            + candle_score * 0.10
        )

        # ── 6. 买点信号判定 ──
        if buy_readiness >= 80:
            buy_signal = "READY"
        elif buy_readiness >= 60:
            buy_signal = "WATCH"
        else:
            buy_signal = "WAIT"

        # ── 7. 精确买入区间 ──
        buy_low = min(support_price * 0.99, close_p * 0.99)
        buy_high = max(support_price * 1.02, close_p)

        # ── 写入 result ──
        result.buy_signal = buy_signal
        result.buy_readiness = round(buy_readiness, 1)
        result.buy_price_low = round(buy_low, 2)
        result.buy_price_high = round(buy_high, 2)
        result.kdj_j = round(kdj_j, 1)
        result.kdj_turn = kdj_turn
        result.rsi_6 = round(rsi_6, 1)
        result.macd_dif = round(macd_dif, 2)
        result.macd_dea = round(macd_dea, 2)
        result.bb_lower = round(bb_lower, 2)
        result.candle_pattern = candle_pattern
        result.consecutive_down = consecutive_down

    # ══════════════════════════════════════════════
    # 工具
    # ══════════════════════════════════════════════

    @staticmethod
    def _calc_start_date(end_date: str, days_back: int) -> str:
        """计算起始日期（YYYYMMDD，粗略按自然日算）"""
        import datetime
        d = datetime.datetime.strptime(end_date, "%Y%m%d")
        start = d - datetime.timedelta(days=days_back + 10)  # 多缓冲几天
        return start.strftime("%Y%m%d")
