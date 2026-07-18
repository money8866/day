#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
三层信号融合引擎 + LightGBM动态权重学习
========================================
将大盘择时、主题择时、个股技术面入场信号三层融合，
并通过LightGBM模型动态学习最优权重组合。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from market.market_timing import MarketTimingEngine, get_market_state_features
from stock import entry_signals
from data import tdx_loader as tdx

from theme.theme_timing import ThemeTimingEngine, match_pool_to_themes

LOG = logging.getLogger("timing_trading.signal")

# ─────────────────────────────────────────────────────────────────
# TimingSignal dataclass
# ─────────────────────────────────────────────────────────────────


@dataclass
class TimingSignal:
    """三层信号融合结果"""
    ts_code: str
    stock_name: str
    trade_date: str
    # 三层信号
    market_score: float = 0.0       # 大盘择时分(0-100)
    theme_score: float = 0.0        # 主题择时分(0-100)
    entry_score: float = 0.0        # 个股技术面入场分(0-100)
    # 综合
    composite_score: float = 0.0    # 综合评分
    signal_type: str = "none"       # buy / sell / hold
    position_ratio: float = 0.0     # 建议仓位比例(0-1)
    # 细节
    primary_entry: str = ""         # 主要入场信号类型
    details: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────
# 辅助函数 - 特征提取
# ─────────────────────────────────────────────────────────────────


def _build_stock_technical_features(df: pd.DataFrame) -> Dict[str, float]:
    """从个股日线数据提取技术面特征向量(20维+turnover第21维)

    返回特征:
        dist_ma5, dist_ma10, dist_ma20, dist_ma60 (距离均线%)
        vol_ratio, vol_ma20 (量比)
        rsi_6, rsi_12, rsi_24, adx (动量)
        macd_diff, macd_bar, kdj_j, kdj_k (趋势)
        boll_width, boll_position (波动率)
        pct_chg_5d, pct_chg_10d, pct_chg_20d (收益)
        max_retrace_20d (最大回撤)
        turnover_5d_avg (5日平均换手率近似值)
    """
    # 默认零值特征模板
    _ZERO_FEATURES = {f"tech_{k}": 0.0 for k in [
        "dist_ma5", "dist_ma10", "dist_ma20", "dist_ma60",
        "vol_ratio", "vol_ma20",
        "rsi_6", "rsi_12", "rsi_24", "adx",
        "macd_diff", "macd_bar", "kdj_j", "kdj_k",
        "boll_width", "boll_position",
        "pct_chg_5d", "pct_chg_10d", "pct_chg_20d",
        "max_retrace_20d", "turnover_5d_avg",
    ]}

    if df is None or df.empty or len(df) < 60:
        return _ZERO_FEATURES

    last = df.iloc[-1]

    # 布林带位置
    boll_upper = last.get("boll_upper", 0)
    boll_lower = last.get("boll_lower", 0)
    boll_range = boll_upper - boll_lower
    boll_position = (last.get("close", 0) - boll_lower) / (boll_range + 1e-10)

    # 20日最大回撤
    close_20 = df["close"].tail(20)
    max_price_20 = close_20.max()
    min_price_20 = close_20.min()
    max_retrace_20d = (max_price_20 - min_price_20) / (max_price_20 + 1e-10) * 100

    # 5日平均换手率近似值 (用 vol 与20日均量比值)
    vol_ma20_val = last.get("vol_ma20", 0)
    turnover_5d_avg = df["vol"].tail(5).mean() / (vol_ma20_val + 1e-10) if vol_ma20_val > 0 else 0.0

    features = {
        "tech_dist_ma5": float(last.get("dist_ma5", 0)),
        "tech_dist_ma10": float(last.get("dist_ma10", 0)),
        "tech_dist_ma20": float(last.get("dist_ma20", 0)),
        "tech_dist_ma60": float(last.get("dist_ma60", 0)),
        "tech_vol_ratio": float(last.get("vol_ratio", 0)),
        "tech_vol_ma20": float(vol_ma20_val),
        "tech_rsi_6": float(last.get("rsi_6", 50)),
        "tech_rsi_12": float(last.get("rsi_12", 50)),
        "tech_rsi_24": float(last.get("rsi_24", 50)),
        "tech_adx": float(last.get("adx", 0)),
        "tech_macd_diff": float(last.get("macd_diff", 0)),
        "tech_macd_bar": float(last.get("macd_bar", 0)),
        "tech_kdj_j": float(last.get("kdj_j", 0)),
        "tech_kdj_k": float(last.get("kdj_k", 50)),
        "tech_boll_width": float(last.get("boll_width", 0)),
        "tech_boll_position": float(boll_position),
        "tech_pct_chg_5d": float(df["pct_chg"].tail(5).sum()),
        "tech_pct_chg_10d": float(df["pct_chg"].tail(10).sum()),
        "tech_pct_chg_20d": float(df["pct_chg"].tail(20).sum()),
        "tech_max_retrace_20d": float(max_retrace_20d),
        "tech_turnover_5d_avg": float(turnover_5d_avg),
    }

    return features


def _build_stock_fundamental_features(row: pd.Series) -> Dict[str, float]:
    """从股池DataFrame的行中提取基本面特征向量(5维)

    特征:
        bull_score (Bull_v2.1分)
        market_cap_log (市值log)
        limit_up_count (涨停次数)
        avg_daily_amount (日均成交额)
        leader_type_score (龙头类型分值)
    """
    # 龙头类型映射
    LEADER_TYPE_MAP = {
        "行业龙头": 100,
        "中军": 80,
        "龙二": 70,
        "补涨": 60,
        "普通": 50,
    }

    # 处理缺失值
    def _safe_float(val, default=0.0) -> float:
        try:
            v = float(val) if pd.notna(val) else default
            return v
        except (ValueError, TypeError):
            return default

    bull_score = _safe_float(row.get("Bull_v2.1分", 0))

    market_cap = _safe_float(row.get("市值(亿)", 0))
    market_cap_log = np.log(market_cap + 1) if market_cap > 0 else 0.0

    limit_up_count = _safe_float(row.get("涨停次数", 0))

    avg_daily_amount = _safe_float(row.get("日均成交额(亿)", 0))

    leader_type_str = str(row.get("龙头类型", ""))
    leader_type_score = LEADER_TYPE_MAP.get(leader_type_str, 30)

    return {
        "fund_bull_score": bull_score,
        "fund_market_cap_log": market_cap_log,
        "fund_limit_up_count": limit_up_count,
        "fund_avg_daily_amount": avg_daily_amount,
        "fund_leader_type_score": leader_type_score,
    }


def _build_theme_features(theme_info: dict) -> Dict[str, float]:
    """从主题评估结果提取主题特征向量(5维)"""
    return {
        "theme_score": float(theme_info.get("best_theme_score", theme_info.get("score", 0))),
        "theme_trend": float(theme_info.get("trend", 0)),
        "theme_breadth": float(theme_info.get("breadth", 0)),
        "theme_leader_health": float(theme_info.get("leader_health", 0)),
        "theme_capital_flow": float(theme_info.get("capital_flow", 0)),
    }


def _score_to_position(composite_score: float, config: dict) -> float:
    """根据综合评分映射仓位比例"""
    score_map = config.get("position", {}).get("score_to_position", {
        90: 1.0, 70: 0.7, 50: 0.4, 30: 0.2, 0: 0.05,
    })
    # 按score降序查找
    sorted_scores = sorted(score_map.items(), key=lambda x: -x[0])
    for threshold, pos in sorted_scores:
        if composite_score >= threshold:
            return pos
    return 0.05


# ─────────────────────────────────────────────────────────────────
# SignalFusionEngine
# ─────────────────────────────────────────────────────────────────


class SignalFusionEngine:
    """三层信号融合引擎

    融合大盘择时、主题择时、个股技术面入场信号，
    并通过LightGBM动态学习权重。
    """

    def __init__(self, config: dict):
        self.cfg = config

        # 大盘择时引擎
        self.market_engine = MarketTimingEngine(config)

        # 主题择时引擎（可选）
        self.theme_engine = None
        if ThemeTimingEngine is not None:
            try:
                self.theme_engine = ThemeTimingEngine(config)
            except Exception as e:
                LOG.warning("主题引擎初始化失败: %s", e)

        # LightGBM配置
        self.lgb_config = config.get("lgb", {})
        self.lgb_model = None
        self._feature_cols: List[str] = []

        # 模型文件路径
        model_dir = self.lgb_config.get("model_dir", "output/models")
        self.model_path = os.path.join(model_dir, "lgb_timing_model.txt")

        # 尝试加载已有模型
        if self.lgb_config.get("enabled", False):
            self.load_lgb_model()

    # ── 主评估接口 ──────────────────────────────────────────────

    def evaluate(
        self,
        pool_df: pd.DataFrame,
        trade_date: str = "",
        etf_data: Dict[str, pd.DataFrame] = None,
        force_retrain: bool = False,
        daily_cache: Dict[str, pd.DataFrame] = None,
    ) -> List[TimingSignal]:
        """对股池中每只股票生成综合信号

        Args:
            pool_df: 基本面股池DataFrame
            trade_date: 交易日期YYYYMMDD，空则使用最新
            etf_data: ETF日线数据（用于涨跌比计算）
            force_retrain: 是否强制重新训练LightGBM模型
            daily_cache: 可选，{ts_code: DataFrame} 缓存（用于回测加速）

        Returns:
            List[TimingSignal]
        """
        # 1. 大盘评估
        market_state = self.market_engine.evaluate(trade_date, etf_data)
        market_features = get_market_state_features(market_state)
        market_score = market_state.score

        # 2. 主题评估
        # 股池已在外部通过 match_pool_to_themes 打了主题标签，
        # 后续从每行记录的 best_theme_name/best_theme_score 获取

        # 3. 遍历股池生成信号
        signals: List[TimingSignal] = []
        tdx_root = self.cfg.get("general", {}).get("tdx_root", "C:\\new_tdx")

        total_stocks = 0
        for _, row in pool_df.iterrows():
            ts_code = row.get("ts_code", "")
            stock_name = row.get("name", ts_code)

            if not ts_code:
                continue
            total_stocks += 1

            try:
                signal = self._evaluate_stock(
                    ts_code=ts_code,
                    stock_name=stock_name,
                    trade_date=trade_date,
                    tdx_root=tdx_root,
                    row=row,
                    market_score=market_score,
                    market_features=market_features,
                    market_state=market_state,
                    theme_info={"best_theme_name": row.get("best_theme_name", ""), "best_theme_score": row.get("best_theme_score", 0)},
                    daily_cache=daily_cache,
                )
                signals.append(signal)
            except Exception as e:
                LOG.error("评估 %s 失败: %s", ts_code, e)
                continue

        LOG.info("evaluate: %d stocks processed, %d signals generated, %d buy",
                 total_stocks, len(signals),
                 sum(1 for s in signals if s.signal_type == "buy"))

        # 如果LightGBM模型已启用且需强制重训练
        lgb_enabled = self.lgb_config.get("enabled", False)
        if lgb_enabled and force_retrain and self.lgb_model is not None:
            LOG.info("强制重新训练LightGBM模型...")
            self.train_lgb_model(pool_df, trade_date)

        return signals

    def _evaluate_stock(
        self,
        ts_code: str,
        stock_name: str,
        trade_date: str,
        tdx_root: str,
        row: pd.Series,
        market_score: float,
        market_features: Dict[str, float],
        market_state,
        theme_info: dict,
        daily_cache: Dict[str, pd.DataFrame] = None,
    ) -> TimingSignal:
        """评估单只股票的综合信号"""
        # 3. 加载个股TDX日线 + 技术指标
        df = pd.DataFrame()
        if daily_cache and ts_code in daily_cache:
            cached = daily_cache[ts_code]
            if trade_date:
                cached = cached[cached["trade_date"] <= trade_date]
            if len(cached) >= 30:
                df = cached.tail(180).copy()
        if df.empty:
            df = tdx.load_daily(ts_code, tdx_root, min_records=30)
        if not df.empty:
            if "ma5" not in df.columns:
                df = tdx.calc_all_indicators(df)
            if trade_date:
                df = df[df["trade_date"] <= trade_date]

        # 4. 检测入场信号
        entry_result = {}
        entry_score = 0.0
        primary_entry = ""

        if not df.empty:
            try:
                entry_result = entry_signals.composite_entry_score(df, self.cfg)
                if isinstance(entry_result, dict):
                    entry_score = entry_result.get("score", 0)
                    signals_found = entry_result.get("signals", {})
                    if signals_found:
                        primary_entry = max(signals_found, key=lambda k: signals_found[k].get("score", 0))
            except Exception as e:
                LOG.debug("入场信号计算失败 %s: %s", ts_code, e)

        # 主题评分
        theme_score = float(theme_info.get("best_theme_score", 0)) if theme_info else 0.0

        # 5. 用LightGBM模型预测（如可用）
        lgb_enabled = self.lgb_config.get("enabled", False)
        lgb_prediction = None

        if lgb_enabled and self.lgb_model is not None and not df.empty:
            try:
                # 构建特征向量
                tech_features = _build_stock_technical_features(df)
                fund_features = _build_stock_fundamental_features(row)
                theme_features_vec = _build_theme_features(theme_info)

                # 合并特征（顺序需与训练一致）
                feature_vector = {}
                feature_vector.update(market_features)      # 10维
                feature_vector.update(theme_features_vec)   # 5维
                feature_vector.update(tech_features)        # 21维
                feature_vector.update(fund_features)        # 5维

                # 转为DataFrame
                feature_df = pd.DataFrame([feature_vector])
                # 确保列顺序与训练一致
                if self._feature_cols:
                    feature_df = feature_df.reindex(columns=self._feature_cols, fill_value=0)

                pred = self.lgb_model.predict(feature_df)
                lgb_prediction = float(pred[0])
            except Exception as e:
                LOG.debug("LightGBM预测失败 %s: %s", ts_code, e)

        # 6. 计算综合评分
        if lgb_prediction is not None:
            composite_score = lgb_prediction * 100
            composite_score = max(0.0, min(100.0, composite_score))
        else:
            # 权重设计：个股技术面占主导，大盘作为调节
            # 当 theme_score=0（数据缺失）时，用 max(market, theme) 兜底
            market_boost = max(market_score, theme_score)
            composite_score = (
                entry_score * 0.85
                + market_boost * 0.15
            )

        # 信号类型判定
        signal_type, position_ratio = self._determine_signal(
            composite_score, entry_score, market_score
        )

        # 构建详情
        details = {
            "market_state": {
                "name": getattr(market_state, "name", ""),
                "label": getattr(market_state, "label", ""),
                "score": market_score,
            },
            "theme_detail": theme_info,
            "entry_detail": entry_result if isinstance(entry_result, dict) else {},
            "lgb_prediction": lgb_prediction,
            "has_enough_data": not df.empty,
        }

        return TimingSignal(
            ts_code=ts_code,
            stock_name=stock_name,
            trade_date=trade_date or (df["trade_date"].iloc[-1] if not df.empty else ""),
            market_score=round(market_score, 1),
            theme_score=round(theme_score, 1),
            entry_score=round(entry_score, 1),
            composite_score=round(composite_score, 1),
            signal_type=signal_type,
            position_ratio=round(position_ratio, 4),
            primary_entry=primary_entry,
            details=details,
        )

    def _determine_signal(
        self,
        composite_score: float,
        entry_score: float,
        market_score: float,
    ) -> Tuple[str, float]:
        """判定信号类型和建议仓位

        信号判定逻辑（连续评分模式，entry占85%）：
        - composite_score >= 62 且 entry_score >= 50 → "buy"        (强信号)
        - composite_score >= 52 且 entry_score >= 45 → "buy" (weak) (弱信号)
        - composite_score < 32 或 entry_score < 28    → "sell"
        - 其他 → "hold"
        """
        if composite_score >= 62 and entry_score >= 50:
            signal_type = "buy"
        elif composite_score >= 52 and entry_score >= 45:
            signal_type = "buy"
        elif composite_score < 32 or entry_score < 28:
            signal_type = "sell"
        else:
            signal_type = "hold"

        # 仓位映射
        position_ratio = _score_to_position(composite_score, self.cfg)

        return signal_type, position_ratio

    # ── LightGBM 训练 ──────────────────────────────────────────

    def _prepare_training_data(
        self,
        pool_df: pd.DataFrame,
        start_date: str,
        end_date: str,
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """准备LightGBM训练数据

        遍历历史每一天，对股池中每只股票生成特征-标签对。
        特征: market_features(10维) + theme_features(5维) +
              stock_technical(21维) + stock_fundamental(5维)
        标签: 未来N日收益率（N由config.lgb.prediction_horizons决定，默认20）

        Returns:
            (X, y) 特征矩阵和标签向量
        """
        horizons = self.lgb_config.get("prediction_horizons", [5, 10, 20])
        pred_horizon = horizons[2] if len(horizons) > 2 else 20  # 默认使用20日

        tdx_root = self.cfg.get("general", {}).get("tdx_root", "C:\\new_tdx")

        # 预计算大盘历史状态和特征
        market_history = self.market_engine.evaluate_history(
            start_date=start_date,
            end_date=end_date,
        )
        if market_history.empty:
            LOG.warning("无法获取大盘历史数据")
            return pd.DataFrame(), pd.Series(dtype=float)

        # 建立日期索引的大盘特征映射
        market_feature_map: Dict[str, Dict[str, float]] = {}
        for _, m_row in market_history.iterrows():
            date_str = str(m_row["trade_date"])
            # 模拟MarketState对象用于特征提取
            class _MockState:
                pass
            mock_state = _MockState()
            mock_state.score = m_row.get("market_score", 35)
            mock_state.position_suggest = 0.0
            mock_state.adx = m_row.get("adx", 0)
            mock_state.advance_ratio = m_row.get("advance_ratio", 1.0)
            mock_state.name = m_row.get("market_state", "mid_adjust")
            mock_state.ma_arrangement = m_row.get("ma_arrangement", "mixed")
            market_feature_map[date_str] = get_market_state_features(mock_state)

        # 预计算主题历史特征（如果主题引擎可用）
        theme_feature_map: Dict[str, Dict[str, float]] = {}
        if self.theme_engine is not None:
            try:
                theme_history = self.theme_engine.evaluate_history(
                    start_date=start_date,
                    end_date=end_date,
                    pool_df=pool_df,
                )
                if isinstance(theme_history, dict):
                    theme_feature_map = theme_history
            except Exception as e:
                LOG.warning("主题历史特征计算失败: %s", e)

        # 遍历股池
        all_X: List[pd.DataFrame] = []
        all_y: List[float] = []

        for _, stock_row in pool_df.iterrows():
            ts_code = stock_row.get("ts_code", "")
            if not ts_code:
                continue

            # 加载个股全部日线
            df = tdx.load_daily(ts_code, tdx_root, min_records=60)
            if df.empty:
                continue
            df = tdx.calc_all_indicators(df)
            df = df.sort_values("trade_date").reset_index(drop=True)

            # 基本面特征（对于所有日期相同）
            fund_features = _build_stock_fundamental_features(stock_row)

            # 遍历每个有足够历史数据的日期
            for i in range(60, len(df) - pred_horizon):
                current_row = df.iloc[i]
                date_str = str(current_row["trade_date"])

                # 只处理目标日期范围内的数据
                if date_str < start_date or (end_date and date_str > end_date):
                    continue

                # 大盘特征
                m_features = market_feature_map.get(date_str, {})
                if not m_features:
                    continue

                # 主题特征
                theme_info = theme_feature_map.get(ts_code, {}).get(date_str, {})
                t_features = _build_theme_features(theme_info)

                # 技术特征（使用到当前日期为止的数据）
                tech_features = _build_stock_technical_features(df.iloc[:i + 1])

                # 合并特征向量
                feature_vec = {}
                feature_vec.update(m_features)       # 10维
                feature_vec.update(t_features)       # 5维
                feature_vec.update(tech_features)    # 21维
                feature_vec.update(fund_features)    # 5维

                # 标签：未来N日收益率
                future_return = (df["close"].iloc[i + pred_horizon] / current_row["close"] - 1) * 100

                all_X.append(pd.DataFrame([feature_vec]))
                all_y.append(future_return)

        if not all_X:
            LOG.warning("未生成训练数据")
            return pd.DataFrame(), pd.Series(dtype=float)

        X = pd.concat(all_X, ignore_index=True)
        y = pd.Series(all_y)

        # 保存特征列名
        self._feature_cols = list(X.columns)

        LOG.info("训练数据准备完成: X %s, y %d 样本",
                 X.shape, len(y))
        return X, y

    def train_lgb_model(
        self,
        pool_df: pd.DataFrame,
        start_date: str = "",
        end_date: str = "",
    ):
        """训练LightGBM模型

        特征工程后调用_prepare_training_data，8:2划分训练/验证集，
        保存模型并输出特征重要性。
        """
        import lightgbm as lgb

        # 确定日期范围
        if not end_date:
            from datetime import datetime
            end_date = datetime.now().strftime("%Y%m%d")
        if not start_date:
            # 默认使用训练窗口
            train_window = self.lgb_config.get("train_window", 120)
            # 粗略估计交易日数对应天数
            end_dt = pd.Timestamp(end_date) if end_date else pd.Timestamp.now()
            start_dt = end_dt - pd.Timedelta(days=int(train_window * 1.5))
            start_date = start_dt.strftime("%Y%m%d")

        LOG.info("开始训练LightGBM模型, 日期范围: %s ~ %s", start_date, end_date)

        # 准备数据
        X, y = self._prepare_training_data(pool_df, start_date, end_date)
        if X.empty or len(y) < 100:
            LOG.warning("训练数据不足 (%d 样本)，跳过训练", len(y))
            return

        # 8:2 划分
        split_idx = int(len(X) * 0.8)
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

        # 获取模型参数
        model_params = self.lgb_config.get("model_params", {})
        params = {
            "objective": "regression",
            "metric": "rmse",
            "num_leaves": model_params.get("num_leaves", 31),
            "learning_rate": model_params.get("learning_rate", 0.05),
            "feature_fraction": model_params.get("feature_fraction", 0.8),
            "bagging_fraction": model_params.get("bagging_fraction", 0.8),
            "bagging_freq": model_params.get("bagging_freq", 5),
            "verbosity": -1,
        }

        num_boost_round = model_params.get("num_boost_round", 200)
        early_stopping_rounds = model_params.get("early_stopping_rounds", 20)

        # 训练
        train_data = lgb.Dataset(X_train, label=y_train)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

        self.lgb_model = lgb.train(
            params,
            train_data,
            valid_sets=[val_data],
            num_boost_round=num_boost_round,
            callbacks=[lgb.early_stopping(early_stopping_rounds),
                       lgb.log_evaluation(50)],
        )

        # 保存特征列名
        self._feature_cols = list(X.columns)

        # 保存模型
        model_dir = os.path.dirname(self.model_path)
        os.makedirs(model_dir, exist_ok=True)
        self.lgb_model.save_model(self.model_path)
        LOG.info("LightGBM模型已保存至: %s", self.model_path)

        # 输出特征重要性
        importance = self.lgb_model.feature_importance(importance_type="gain")
        feat_imp = sorted(
            zip(self.lgb_model.feature_name(), importance),
            key=lambda x: -x[1],
        )
        LOG.info("=== 特征重要性 Top 20 ===")
        for name, imp in feat_imp[:20]:
            LOG.info("  %s: %.4f", name, imp)

    def load_lgb_model(self) -> bool:
        """加载已训练的LightGBM模型

        Returns:
            bool: 是否成功加载
        """
        if not os.path.exists(self.model_path):
            LOG.info("LightGBM模型文件不存在: %s", self.model_path)
            return False

        try:
            import lightgbm as lgb
            self.lgb_model = lgb.Booster(model_file=self.model_path)
            # 恢复特征列名
            self._feature_cols = self.lgb_model.feature_name()
            LOG.info("LightGBM模型已加载: %s (特征数: %d)",
                     self.model_path, len(self._feature_cols))
            return True
        except Exception as e:
            LOG.warning("LightGBM模型加载失败: %s", e)
            self.lgb_model = None
            return False
