# -*- coding: utf-8 -*-
"""全市场截面排序引擎 — 20因子多维度评分

核心逻辑：
  - 对所有股票（3000+）计算20个因子，覆盖动量、资金、技术、基本面、微观结构
  - 截面动量排名（25%权重） + 多窗口动量共振（25%权重） +
    动量加速度（10%权重） + 量价协同（25%权重） + 趋势质量（10%权重）
    其余15个因子作为综合调整项
  - 输出全市场排名，扩展候选池至Top50，支持后续的Alpha筛选和概率预测
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import stock_cache as sc
except ImportError:
    sc = None


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class FactorResult:
    """单个因子计算结果"""
    name: str
    category: str          # momentum / capital / technical / fundamental / microstructure
    raw_value: float
    score: float           # 0~100 归一化得分
    weight: float          # 在综合评分中的权重
    contribution: float    # score * weight


@dataclass
class StockAlpha:
    """单只股票的Alpha评分"""
    ts_code: str
    name: str
    themes: List[str] = field(default_factory=list)
    total_score: float = 0.0
    factors: Dict[str, FactorResult] = field(default_factory=dict)
    cross_sectional_rank: int = 0
    cross_sectional_pct: float = 0.0  # 排名百分位 0~1


@dataclass
class CrossSectionalResult:
    """截面排序结果"""
    trade_date: str
    n_stocks_analyzed: int
    stock_alphas: List[StockAlpha]
    factor_contributions: Dict[str, float]  # 各因子对整体排名的贡献
    top_n: List[StockAlpha] = field(default_factory=list)


# ──────────────────────────────────────────────
# 主引擎
# ──────────────────────────────────────────────

class CrossSectionalRanking:
    """全市场截面排序引擎 — 20因子多维度评分"""

    def __init__(self, config: dict):
        self.config = config.get('cross_sectional', {})
        self.lookback_days = self.config.get('lookback_days', 120)
        self.top_n = self.config.get('top_n', 50)
        self.min_volume = self.config.get('min_volume', 0)  # 最低成交额过滤（万元）
        self.max_market_cap = self.config.get('max_market_cap', 1e12)  # 最高市值过滤
        self.min_market_cap = self.config.get('min_market_cap', 0)
        self.max_stocks = self.config.get('max_stocks', 300)  # 每日Pipeline最大分析数（防止超时）
        self.load_threads = self.config.get('load_threads', 8)  # 并行加载线程数

        # 因子权重配置（只读）
        self.factor_weights = self.config.get('factor_weights', {})
        self._load_default_weights()

    def _load_default_weights(self):
        """加载默认因子权重（当config中缺失时）"""
        defaults = {
            # 五大类权重
            '_group_momentum': 0.25,
            '_group_multi_window': 0.25,
            '_group_acceleration': 0.10,
            '_group_volume_price': 0.25,
            '_group_trend_quality': 0.10,
            # 大类内部保留0.05给其他辅助因子
            # 子因子权重 - 截面动量
            'cross_sectional_rank': 0.06,
            # 多窗口动量共振
            'momentum_5d': 0.075,
            'momentum_20d': 0.125,
            'momentum_60d': 0.05,
            # 动量加速度
            'momentum_accel': 0.10,
            # 量价协同
            'volume_price_synergy': 0.25,
            # 趋势质量
            'trend_quality': 0.10,
            # 辅助因子（资金、技术、基本面、微观结构）
            'northbound_flow_5d': 0.02,
            'institutional_flow_5d': 0.02,
            'large_order_intensity': 0.02,
            'rsi_14': 0.015,
            'macd_hist': 0.015,
            'volatility_ratio': 0.015,
            'market_cap_score': 0.015,
            'turnover_stability': 0.015,
            'profit_growth': 0.01,
            'dist_ma20': 0.01,
            'dist_ma60': 0.01,
            'volume_shrink': 0.01,
            'limit_up_history': 0.01,
            'relative_strength': 0.01,
        }
        for k, v in defaults.items():
            if k not in self.factor_weights:
                self.factor_weights[k] = v

    def evaluate(self, trade_date: str,
                 theme_stock_map: dict = None,
                 stock_meta: dict = None,
                 n_stocks_limit: int = None) -> CrossSectionalResult:
        """全市场截面排序主入口

        Args:
            trade_date: 交易日 YYYYMMDD
            theme_stock_map: 主题-股票映射 {theme: [{code, name, ...}]}
            stock_meta: 股票元数据 {ts_code: {name, industry, ...}}
            n_stocks_limit: 分析数量限制（None=全市场）

        Returns:
            CrossSectionalResult
        """
        # Step 1: 获取全部股票池
        all_stocks = self._build_universe(trade_date, theme_stock_map, stock_meta)
        if n_stocks_limit and len(all_stocks) > n_stocks_limit:
            all_stocks = all_stocks[:n_stocks_limit]

        # Step 2: 批量加载因子数据
        codes = [s['ts_code'] for s in all_stocks]
        factor_df = self._load_batch_factor_data(codes, trade_date)
        if factor_df is None or factor_df.empty:
            return CrossSectionalResult(
                trade_date=trade_date, n_stocks_analyzed=0,
                stock_alphas=[], factor_contributions={}
            )

        # Step 3: 为每只股票计算20个因子
        stock_alphas = []
        for stock in all_stocks:
            code = stock['ts_code']
            stock_row = factor_df[factor_df['ts_code'] == code] if 'ts_code' in factor_df.columns else None
            if stock_row is None or stock_row.empty:
                continue
            row = stock_row.iloc[-1]  # 取最新一行
            alpha = self._compute_single_stock(stock, row, factor_df)
            if alpha is not None:
                stock_alphas.append(alpha)

        if not stock_alphas:
            return CrossSectionalResult(
                trade_date=trade_date, n_stocks_analyzed=0,
                stock_alphas=[], factor_contributions={}
            )

        # Step 4: 截面排序
        stock_alphas.sort(key=lambda x: x.total_score, reverse=True)
        n_total = len(stock_alphas)
        for i, sa in enumerate(stock_alphas):
            sa.cross_sectional_rank = i + 1
            sa.cross_sectional_pct = (i + 1) / n_total

        # Step 5: 因子贡献分析
        factor_contribs = self._compute_factor_contributions(stock_alphas)

        top_n = stock_alphas[:min(self.top_n, len(stock_alphas))]

        return CrossSectionalResult(
            trade_date=trade_date,
            n_stocks_analyzed=len(stock_alphas),
            stock_alphas=stock_alphas,
            factor_contributions=factor_contribs,
            top_n=top_n,
        )

    # ──────────────────────────────────────────────
    # 股票池构建
    # ──────────────────────────────────────────────

    def _build_universe(self, trade_date: str,
                        theme_stock_map: dict,
                        stock_meta: dict) -> List[dict]:
        """构建全市场股票池"""
        seen = set()
        stocks = []
        if theme_stock_map:
            for theme, members in theme_stock_map.items():
                if isinstance(members, list):
                    for m in members:
                        if isinstance(m, dict):
                            code = m.get('code', m.get('ts_code', ''))
                        else:
                            continue
                        if code and code not in seen:
                            seen.add(code)
                            name = m.get('name', '') if isinstance(m, dict) else ''
                            stocks.append({
                                'ts_code': code,
                                'name': name,
                                'themes': [theme],
                            })

        # 如果 theme_stock_map 是空的，从股票元数据补充
        if stock_meta:
            for code, meta in stock_meta.items():
                if code not in seen:
                    seen.add(code)
                    stocks.append({
                        'ts_code': code,
                        'name': meta.get('name', '') if isinstance(meta, dict) else '',
                        'themes': meta.get('themes', []) if isinstance(meta, dict) else [],
                    })

        return stocks

    # ──────────────────────────────────────────────
    # 因子数据加载
    # ──────────────────────────────────────────────

    def _load_batch_factor_data(self, codes: List[str], trade_date: str) -> Optional[pd.DataFrame]:
        """批量加载因子数据（并行+限数）

        使用 stk_factor_pro 表获取技术因子。仅加载 max_stocks 只股票，
        并使用线程池并行加速。
        """
        # 限制数量
        codes = codes[:self.max_stocks]
        start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=self.lookback_days)).strftime('%Y%m%d')

        dfs = []
        loaded = 0
        skipped = 0

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _load_one(code):
            try:
                df = sc.cached_stk_factor_pro(code, start_date, trade_date, silent=True)
                if df is not None and not df.empty:
                    df['ts_code'] = code
                    return df
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=self.load_threads) as pool:
            futures = {pool.submit(_load_one, code): code for code in codes}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=30)
                    if result is not None:
                        dfs.append(result)
                        loaded += 1
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1

        if not dfs:
            return None
        result = pd.concat(dfs, ignore_index=True)
        result = result.drop_duplicates(subset=['ts_code', 'trade_date']).reset_index(drop=True)
        return result

    # ──────────────────────────────────────────────
    # 单只股票因子计算
    # ──────────────────────────────────────────────

    def _compute_single_stock(self, stock: dict, row: pd.Series,
                              full_df: pd.DataFrame) -> Optional[StockAlpha]:
        """计算单只股票的20个因子"""
        try:
            factors = {}
            code = stock['ts_code']

            # 获取该股票的时间序列
            stock_series = full_df[full_df['ts_code'] == code].sort_values('trade_date')
            if stock_series.empty:
                return None

            close_hfq = stock_series['close_hfq'].values if 'close_hfq' in stock_series.columns else stock_series['close'].values
            vol = stock_series['vol'].values if 'vol' in stock_series.columns else None
            amount = stock_series['amount'].values if 'amount' in stock_series.columns else None

            if len(close_hfq) < 5:
                return None

            latest_close = close_hfq[-1]

            # ════════════════════════════════════
            # 因子组1: 截面动量排名（25%）
            # ════════════════════════════════════
            mom_20d = self._calc_return(close_hfq, 20)
            # 截面排名需要在所有股票中计算，这里用原始值占位，后续在全集中归一化
            factors['cross_sectional_rank'] = FactorResult(
                name='截面动量排名', category='momentum',
                raw_value=mom_20d, score=50.0,  # 暂用50，后续全局归一化
                weight=self.factor_weights.get('cross_sectional_rank', 0.06),
                contribution=0
            )

            # ════════════════════════════════════
            # 因子组2: 多窗口动量共振（25%）
            # ════════════════════════════════════
            mom_5d = self._calc_return(close_hfq, 5)
            mom_60d = self._calc_return(close_hfq, 60)

            factors['momentum_5d'] = FactorResult(
                name='5日动量', category='momentum',
                raw_value=mom_5d, score=50.0,
                weight=self.factor_weights.get('momentum_5d', 0.075),
                contribution=0
            )
            factors['momentum_20d'] = FactorResult(
                name='20日动量', category='momentum',
                raw_value=mom_20d, score=50.0,
                weight=self.factor_weights.get('momentum_20d', 0.125),
                contribution=0
            )
            factors['momentum_60d'] = FactorResult(
                name='60日动量', category='momentum',
                raw_value=mom_60d, score=50.0,
                weight=self.factor_weights.get('momentum_60d', 0.05),
                contribution=0
            )

            # ════════════════════════════════════
            # 因子组3: 动量加速度（10%）
            # ════════════════════════════════════
            mom_accel = mom_5d - mom_20d if not (np.isnan(mom_5d) or np.isnan(mom_20d)) else 0
            factors['momentum_accel'] = FactorResult(
                name='动量加速度', category='momentum',
                raw_value=mom_accel, score=self._score_momentum_accel(mom_accel),
                weight=self.factor_weights.get('momentum_accel', 0.10),
                contribution=0
            )

            # ════════════════════════════════════
            # 因子组4: 量价协同（25%）
            # ════════════════════════════════════
            vp_score = self._calc_volume_price_synergy(stock_series)
            factors['volume_price_synergy'] = FactorResult(
                name='量价协同', category='momentum',
                raw_value=vp_score, score=vp_score,
                weight=self.factor_weights.get('volume_price_synergy', 0.25),
                contribution=0
            )

            # ════════════════════════════════════
            # 因子组5: 趋势质量（10%）
            # ════════════════════════════════════
            tq_score = self._calc_trend_quality(stock_series)
            factors['trend_quality'] = FactorResult(
                name='趋势质量', category='momentum',
                raw_value=tq_score, score=tq_score,
                weight=self.factor_weights.get('trend_quality', 0.10),
                contribution=0
            )

            # ════════════════════════════════════
            # 辅助因子: 资金行为（6%）
            # ════════════════════════════════════
            factors['northbound_flow_5d'] = FactorResult(
                name='北向资金5日', category='capital',
                raw_value=0, score=50.0,  # 需要外部数据源
                weight=self.factor_weights.get('northbound_flow_5d', 0.02),
                contribution=0
            )
            factors['institutional_flow_5d'] = FactorResult(
                name='机构资金5日', category='capital',
                raw_value=0, score=50.0,
                weight=self.factor_weights.get('institutional_flow_5d', 0.02),
                contribution=0
            )
            factors['large_order_intensity'] = FactorResult(
                name='大单强度', category='capital',
                raw_value=0, score=50.0,
                weight=self.factor_weights.get('large_order_intensity', 0.02),
                contribution=0
            )

            # ════════════════════════════════════
            # 辅助因子: 技术指标（4.5%）
            # ════════════════════════════════════
            rsi14 = self._safe_col(row, 'rsi_bfq_6')  # RSI6作为短线代理
            macd_hist = self._calc_macd_hist(stock_series)
            vol_ratio = self._calc_volatility_ratio(close_hfq)

            factors['rsi_14'] = FactorResult(
                name='RSI', category='technical',
                raw_value=rsi14, score=self._normalize_0_100(rsi14, 0, 100),
                weight=self.factor_weights.get('rsi_14', 0.015),
                contribution=0
            )
            factors['macd_hist'] = FactorResult(
                name='MACD柱值', category='technical',
                raw_value=macd_hist, score=self._score_macd(macd_hist),
                weight=self.factor_weights.get('macd_hist', 0.015),
                contribution=0
            )
            factors['volatility_ratio'] = FactorResult(
                name='波动率比', category='technical',
                raw_value=vol_ratio, score=self._score_vol_ratio(vol_ratio),
                weight=self.factor_weights.get('volatility_ratio', 0.015),
                contribution=0
            )

            # ════════════════════════════════════
            # 辅助因子: 基本面（4%）
            # ════════════════════════════════════
            mcap = self._safe_col(row, 'circ_mv')  # 自由流通市值（万元）
            mcap_score = self._score_market_cap(mcap)

            factors['market_cap_score'] = FactorResult(
                name='市值评分', category='fundamental',
                raw_value=mcap, score=mcap_score,
                weight=self.factor_weights.get('market_cap_score', 0.015),
                contribution=0
            )

            # 换手率稳定性（CV越低越稳定）
            turnover_cv = self._calc_turnover_cv(stock_series)
            factors['turnover_stability'] = FactorResult(
                name='换手率稳定性', category='fundamental',
                raw_value=turnover_cv, score=self._score_cv(turnover_cv),
                weight=self.factor_weights.get('turnover_stability', 0.015),
                contribution=0
            )

            # 净利润增速（降权）
            profit_g = self._safe_col(row, 'profit_g')
            factors['profit_growth'] = FactorResult(
                name='利润增速', category='fundamental',
                raw_value=profit_g, score=self._normalize_0_100(profit_g, -50, 200),
                weight=self.factor_weights.get('profit_growth', 0.01),
                contribution=0
            )

            # ════════════════════════════════════
            # 辅助因子: 微观结构（4%）
            # ════════════════════════════════════
            ma20 = self._safe_col(row, 'ma_bfq_20')
            ma60 = self._safe_col(row, 'ma_bfq_60')

            dist_ma20 = (latest_close / ma20 - 1) * 100 if ma20 > 0 else 0
            dist_ma60 = (latest_close / ma60 - 1) * 100 if ma60 > 0 else 0
            vol_shrink = self._calc_volume_shrink(stock_series)

            factors['dist_ma20'] = FactorResult(
                name='距MA20', category='microstructure',
                raw_value=dist_ma20, score=self._score_dist_ma(dist_ma20),
                weight=self.factor_weights.get('dist_ma20', 0.01),
                contribution=0
            )
            factors['dist_ma60'] = FactorResult(
                name='距MA60', category='microstructure',
                raw_value=dist_ma60, score=self._score_dist_ma60(dist_ma60),
                weight=self.factor_weights.get('dist_ma60', 0.01),
                contribution=0
            )
            factors['volume_shrink'] = FactorResult(
                name='缩量调整', category='microstructure',
                raw_value=vol_shrink, score=self._score_vol_shrink(vol_shrink),
                weight=self.factor_weights.get('volume_shrink', 0.01),
                contribution=0
            )
            factors['limit_up_history'] = FactorResult(
                name='历史连板', category='microstructure',
                raw_value=0, score=50.0,  # 需从额外数据源获取
                weight=self.factor_weights.get('limit_up_history', 0.01),
                contribution=0
            )
            factors['relative_strength'] = FactorResult(
                name='相对强度', category='microstructure',
                raw_value=0, score=50.0,  # 需全市场排名后归一化
                weight=self.factor_weights.get('relative_strength', 0.01),
                contribution=0
            )

            # ── 计算综合评分 ──
            total = sum(f.score * f.weight for f in factors.values())
            total += 3.0  # 补偿项（因部分辅助因子默认50分，总权重大于1时居中偏上）

            # 扣除非活跃因子（默认50分的资金因子）
            active_factor_count = sum(1 for f in factors.values() if f.raw_value != 0)
            if active_factor_count < 10:
                total *= 0.8  # 数据不足时降权

            return StockAlpha(
                ts_code=code,
                name=stock.get('name', ''),
                themes=stock.get('themes', []),
                total_score=round(total, 2),
                factors=factors,
            )

        except Exception as e:
            return None

    # ──────────────────────────────────────────────
    # 因子归一化（全市场截面）
    # ──────────────────────────────────────────────

    def _normalize_cross_sectional(self, stock_alphas: List[StockAlpha]):
        """全市场截面归一化：将需要截面排名的因子统一映射到0~100"""
        if not stock_alphas:
            return

        # 截面动量排名归一化
        mom_values = []
        for sa in stock_alphas:
            f = sa.factors.get('cross_sectional_rank')
            if f:
                mom_values.append(f.raw_value)
        if mom_values:
            sorted_vals = sorted(mom_values, reverse=True)
            rank_map = {v: i for i, v in enumerate(sorted_vals)}
            n = len(sorted_vals)
            for sa in stock_alphas:
                f = sa.factors.get('cross_sectional_rank')
                if f and f.raw_value in rank_map:
                    rank_pos = rank_map[f.raw_value]
                    # 第1名100分，最后1名约=100/n分
                    pct = (n - rank_pos) / n * 100
                    f.score = pct
                    f.contribution = pct * f.weight

        # 多窗口动量重新归一化
        for key in ['momentum_5d', 'momentum_20d', 'momentum_60d']:
            values = []
            for sa in stock_alphas:
                f = sa.factors.get(key)
                if f:
                    values.append(f.raw_value)
            if values:
                v_min, v_max = np.percentile(values, [1, 99])
                v_range = v_max - v_min if v_max != v_min else 1
                for sa in stock_alphas:
                    f = sa.factors.get(key)
                    if f:
                        f.score = max(0, min(100, (f.raw_value - v_min) / v_range * 100))
                        f.contribution = f.score * f.weight

        # 相对强度：截面排名
        rs_values = []
        for sa in stock_alphas:
            f = sa.factors.get('relative_strength')
            if f:
                rs_values.append(f.raw_value)
        if rs_values:
            rs_min, rs_max = np.percentile(rs_values, [1, 99])
            rs_range = rs_max - rs_min if rs_max != rs_min else 1
            for sa in stock_alphas:
                f = sa.factors.get('relative_strength')
                if f:
                    f.score = max(0, min(100, (f.raw_value - rs_min) / rs_range * 100))
                    f.contribution = f.score * f.weight

        # 重新计算总分
        for sa in stock_alphas:
            total = sum(f.contribution for f in sa.factors.values())
            total += 3.0  # 补偿项
            active_count = sum(1 for f in sa.factors.values() if f.raw_value != 0)
            if active_count < 10:
                total *= 0.8
            sa.total_score = round(total, 2)

    # ──────────────────────────────────────────────
    # 辅助计算函数
    # ──────────────────────────────────────────────

    def _calc_return(self, prices: np.ndarray, period: int) -> float:
        """计算N日收益率"""
        if len(prices) < period + 1:
            return 0.0
        ret = (prices[-1] / prices[-period - 1] - 1) * 100
        return ret if not np.isnan(ret) else 0.0

    def _calc_volume_price_synergy(self, df: pd.DataFrame) -> float:
        """量价协同因子（根据项目记忆中的规则）"""
        try:
            if df is None or len(df) < 3:
                return 50.0
            last = df.iloc[-1]
            prev = df.iloc[-2]

            ret = (last['close_hfq'] / prev['close_hfq'] - 1) * 100 if 'close_hfq' in df.columns else 0
            vol_ratio = last['vol'] / df['vol'].iloc[-min(21, len(df)):-1].mean() if 'vol' in df.columns and len(df) > 21 else 1.0

            if ret > 0 and vol_ratio > 1:
                return min(100, 50 + vol_ratio * 25)
            elif ret > 0 and vol_ratio <= 1:
                return 50.0
            elif ret <= 0 and vol_ratio > 1:
                return max(0, 50 - vol_ratio * 25)
            else:  # ret <= 0 and vol_ratio <= 1
                return min(100, 50 + (1 - vol_ratio) * 25)
        except Exception:
            return 50.0

    def _calc_trend_quality(self, df: pd.DataFrame) -> float:
        """趋势质量分：上涨天数占比 + 最大回撤 + MA20上方天数占比"""
        try:
            if df is None or len(df) < 20:
                return 50.0
            recent = df.tail(20)
            closes = recent['close_hfq'].values if 'close_hfq' in recent.columns else recent['close'].values

            # 上涨天数占比
            up_days = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
            up_ratio = up_days / (len(closes) - 1)

            # 最大回撤
            peak = np.maximum.accumulate(closes)
            drawdown = (closes - peak) / peak
            max_dd = abs(drawdown.min())

            # MA20上方天数
            ma20 = recent['ma_bfq_20'].values if 'ma_bfq_20' in recent.columns else None
            above_ma20 = 0
            if ma20 is not None and len(ma20) == len(closes):
                above_ma20 = sum(1 for i in range(len(closes)) if closes[i] > ma20[i]) / len(closes)

            score = up_ratio * 40 + max(0, 1 - max_dd / 0.15) * 30 + above_ma20 * 30
            return min(100, score * 100)
        except Exception:
            return 50.0

    def _calc_macd_hist(self, df: pd.DataFrame) -> float:
        """计算MACD柱值"""
        try:
            if 'macd_dif_bfq' not in df.columns or 'macd_dea_bfq' not in df.columns:
                return 0
            dif = float(df['macd_dif_bfq'].iloc[-1]) if pd.notna(df['macd_dif_bfq'].iloc[-1]) else 0
            dea = float(df['macd_dea_bfq'].iloc[-1]) if pd.notna(df['macd_dea_bfq'].iloc[-1]) else 0
            return dif - dea
        except Exception:
            return 0.0

    def _calc_volatility_ratio(self, prices: np.ndarray) -> float:
        """波动率比：20日std / 60日std"""
        if len(prices) < 60:
            return 1.0
        rets = np.diff(prices) / prices[:-1]
        vol_20 = np.std(rets[-20:]) if len(rets) >= 20 else np.std(rets)
        vol_60 = np.std(rets[-60:]) if len(rets) >= 60 else vol_20
        return vol_20 / vol_60 if vol_60 > 0 else 1.0

    def _calc_turnover_cv(self, df: pd.DataFrame) -> float:
        """换手率稳定性（CV = std/mean）"""
        try:
            if 'turnover' not in df.columns:
                return 1.0
            recent = df['turnover'].tail(20)
            if recent.empty or recent.mean() == 0:
                return 1.0
            return recent.std() / recent.mean()
        except Exception:
            return 1.0

    def _calc_volume_shrink(self, df: pd.DataFrame) -> float:
        """缩量比例：当前量 / 20日均量"""
        try:
            if 'vol' not in df.columns or len(df) < 21:
                return 1.0
            current_vol = float(df['vol'].iloc[-1])
            avg_vol = df['vol'].iloc[-21:-1].mean()
            return current_vol / avg_vol if avg_vol > 0 else 1.0
        except Exception:
            return 1.0

    # ──────────────────────────────────────────────
    # 评分函数
    # ──────────────────────────────────────────────

    def _score_momentum_accel(self, val: float) -> float:
        """动量加速度评分：正值较好"""
        if val > 10:
            return 100
        elif val > 5:
            return 80
        elif val > 0:
            return 60
        elif val > -5:
            return 40
        elif val > -10:
            return 20
        return 0

    def _score_macd(self, hist: float) -> float:
        """MACD柱值评分"""
        if hist > 1:
            return 80
        elif hist > 0.3:
            return 65
        elif hist > 0:
            return 55
        elif hist > -0.3:
            return 45
        elif hist > -1:
            return 35
        return 20

    def _score_vol_ratio(self, ratio: float) -> float:
        """波动率比评分：接近1为稳定"""
        if 0.8 <= ratio <= 1.2:
            return 80
        elif 0.6 <= ratio <= 1.4:
            return 60
        elif 0.4 <= ratio <= 1.6:
            return 40
        return 20

    def _score_market_cap(self, mcap: float) -> float:
        """市值评分：300~1500亿最优"""
        if mcap <= 0 or np.isnan(mcap):
            return 50
        mcap_yi = mcap  # 万元为单位
        if 300e4 <= mcap_yi <= 1500e4:
            return 100
        elif 150e4 <= mcap_yi < 300e4:
            return 60 + (mcap_yi - 150e4) / (150e4) * 40
        elif 1500e4 < mcap_yi <= 3000e4:
            return 60 + (3000e4 - mcap_yi) / (1500e4) * 40
        elif mcap_yi >= 5000e4:
            return 20
        return 40

    def _score_cv(self, cv: float) -> float:
        """CV评分：越低越稳定"""
        if cv < 0.3:
            return 90
        elif cv < 0.5:
            return 75
        elif cv < 0.8:
            return 55
        elif cv < 1.2:
            return 35
        return 15

    def _score_dist_ma(self, dist: float) -> float:
        """距MA20位置评分：5%~25%为理想区间"""
        ad = abs(dist)
        if 5 <= ad <= 25:
            return 80
        elif 2 <= ad < 5:
            return 60
        elif 25 < ad <= 40:
            return 40
        elif ad < 2:
            return 30
        return 10

    def _score_dist_ma60(self, dist: float) -> float:
        """距MA60位置评分：不超过30%"""
        ad = abs(dist)
        if ad < 10:
            return 80
        elif ad < 20:
            return 60
        elif ad < 30:
            return 40
        return 20

    def _score_vol_shrink(self, ratio: float) -> float:
        """缩量调整评分：0.4~0.8为理想缩量区间"""
        if 0.4 <= ratio <= 0.8:
            return 80
        elif 0.2 <= ratio < 0.4:
            return 60
        elif 0.8 < ratio <= 1.2:
            return 50
        elif ratio > 1.2:
            return 20
        return 30

    def _normalize_0_100(self, val: float, vmin: float, vmax: float) -> float:
        """将值映射到0~100（线性裁剪）"""
        if np.isnan(val):
            return 50
        v = (val - vmin) / (vmax - vmin) * 100 if vmax != vmin else 50
        return max(0, min(100, v))

    def _safe_col(self, row: pd.Series, col: str) -> float:
        """安全获取列值"""
        try:
            val = row.get(col, np.nan)
            return float(val) if pd.notna(val) else 0.0
        except Exception:
            return 0.0

    def _compute_factor_contributions(self, stock_alphas: List[StockAlpha]) -> Dict[str, float]:
        """计算各因子对整体排名的贡献度"""
        contributions = {}
        if not stock_alphas:
            return contributions
        for sa in stock_alphas:
            for fname, fresult in sa.factors.items():
                contributions[fname] = contributions.get(fname, 0) + fresult.contribution
        n = len(stock_alphas)
        return {k: round(v / n, 2) for k, v in contributions.items()}


# ──────────────────────────────────────────────
# CLI 测试入口
# ──────────────────────────────────────────────

if __name__ == '__main__':
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    engine = CrossSectionalRanking(cfg)
    td = sc.get_effective_date()
    # 加载 theme_stock_map
    from market_regime_v3.engines import resolve_theme_stock_map_path
    tmap_path = resolve_theme_stock_map_path(td)
    tmap = {}
    if os.path.exists(tmap_path):
        with open(tmap_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        tmap = raw.get('themes', raw) if isinstance(raw, dict) else {}
    result = engine.evaluate(td, tmap)
    print(f"\n全市场截面排序结果 ({td}):")
    print(f"  分析股票数: {result.n_stocks_analyzed}")
    if result.top_n:
        print(f"\n  Top{len(result.top_n)}名单:")
        for sa in result.top_n[:20]:
            themes_str = ','.join(sa.themes[:2]) if sa.themes else ''
            print(f"  #{sa.cross_sectional_rank:4d} {sa.name:8s}({sa.ts_code:12s}) {sa.total_score:5.1f}分 [{themes_str}]")
    print("\n  因子贡献度:")
    for fname, contrib in sorted(result.factor_contributions.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"    {fname}: {contrib:.2f}")
