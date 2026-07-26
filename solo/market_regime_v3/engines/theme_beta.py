# -*- coding: utf-8 -*-
"""
主题贝塔引擎 - Theme Beta Engine
在总仓位确定后，分配资金到各主题。
评估各主题的贝塔、动量、趋势、ETF相关性、波动率等因子，
综合评分后按指定方法分配资金。
"""

import os
import sys
import json
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import stock_cache as sc
from inst_pullback_v2.data.loader import DataLoader
from market_regime_v3.engines import resolve_theme_stock_map_path


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
CACHE_DIR = r"D:\mystock\cache_daily"


@dataclass
class ThemeBetaResult:
    """主题贝塔/资金分配结果"""
    allocations: Dict[str, float]  # theme_name -> allocation_pct (0-1)
    theme_scores: Dict[str, float]  # theme_name -> composite_score (0-100)
    theme_betas: Dict[str, float]  # theme_name -> beta vs benchmark
    theme_momentum: Dict[str, float]  # theme_name -> momentum score
    total_exposure_used: float  # sum of allocations
    top_themes: List[str]
    method: str  # allocation method used
    explain: Dict[str, str]


class ThemeBetaEngine:
    """主题贝塔/资金分配引擎

    在 Portfolio Exposure 之后调用，总仓位已确定。
    对传入的 top_themes 列表逐一评估，综合打分后按策略分配资金。
    """

    def __init__(self, config: dict):
        self.cfg = config['theme_beta']
        self.loader = DataLoader()
        self._theme_stock_map = None

    # ──────────────────────────────────────────────
    # 主题-股票映射加载
    # ──────────────────────────────────────────────

    def _load_theme_stock_map(self, trade_date: str = None) -> Optional[dict]:
        """从 JSON 文件加载主题-股票映射"""
        if self._theme_stock_map is not None:
            return self._theme_stock_map
        map_path = resolve_theme_stock_map_path(trade_date)
        if not os.path.exists(map_path):
            print(f"[ThemeBeta] 警告: 主题映射文件不存在 {map_path}")
            return None
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                if 'themes' in raw:
                    self._theme_stock_map = raw['themes']
                else:
                    self._theme_stock_map = raw
            return self._theme_stock_map
        except Exception as e:
            print(f"[ThemeBeta] 加载主题映射失败: {e}")
            return None

    @staticmethod
    def _get_theme_stocks(theme_stock_map: dict, theme_name: str) -> List[str]:
        """从主题映射中提取该主题的股票代码列表

        支持三种格式：
        - list of dict: [{'code': '300502.SZ', 'name': '...'}, ...]
        - list of str: ['300502.SZ', '300308.SZ']
        - dict: {ts_code: {...}, ...}
        """
        data = theme_stock_map.get(theme_name, [])
        codes = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    code = item.get('code', '')
                    if not code:
                        code = item.get('ts_code', '')
                    if code:
                        codes.append(code)
                elif isinstance(item, str):
                    codes.append(item)
            return codes
        if isinstance(data, dict):
            return list(data.keys())
        return []

    # ──────────────────────────────────────────────
    # 日期工具
    # ──────────────────────────────────────────────

    @staticmethod
    def _calc_start_date(trade_date: str, lookback_days: int) -> str:
        """根据目标交易日数估算起始日历日期"""
        dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
        cal_days = int(lookback_days * 1.4) + 20
        start = dt - datetime.timedelta(days=cal_days)
        return start.strftime('%Y%m%d')

    @staticmethod
    def _calc_start_date_exact(trade_date: str, lookback_days: int) -> str:
        """向前推 lookback_days 个自然日作为起始日期（宽松）"""
        dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
        start = dt - datetime.timedelta(days=lookback_days)
        return start.strftime('%Y%m%d')

    # ──────────────────────────────────────────────
    # 数据库查询
    # ──────────────────────────────────────────────

    def _batch_query_hist_prices(self, ts_codes: List[str], start_date: str,
                                  end_date: str) -> pd.DataFrame:
        """批量查询指定股票在日期范围内的历史收盘价和成交额"""
        if not ts_codes or not os.path.exists(sc.DB_PATH):
            return pd.DataFrame()
        try:
            import sqlite3
            conn = sqlite3.connect(sc.DB_PATH)
            placeholders = ','.join(['?'] * len(ts_codes))
            sql = f"""
                SELECT ts_code, trade_date, close_hfq, amount
                FROM stk_factor_pro
                WHERE trade_date BETWEEN ? AND ? AND ts_code IN ({placeholders})
                ORDER BY ts_code, trade_date
            """
            params = [start_date, end_date] + ts_codes
            df = pd.read_sql(sql, conn, params=params)
            conn.close()
            for col in ['close_hfq', 'amount']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def _batch_query_date_prices(self, ts_codes: List[str],
                                  trade_date: str) -> pd.DataFrame:
        """批量查询指定股票在 trade_date 的行情数据（含成交额）"""
        if not ts_codes or not os.path.exists(sc.DB_PATH):
            return pd.DataFrame()
        try:
            import sqlite3
            conn = sqlite3.connect(sc.DB_PATH)
            placeholders = ','.join(['?'] * len(ts_codes))
            sql = f"""
                SELECT ts_code, close_hfq, pct_chg, amount
                FROM stk_factor_pro
                WHERE trade_date = ? AND ts_code IN ({placeholders})
            """
            params = [trade_date] + ts_codes
            df = pd.read_sql(sql, conn, params=params)
            conn.close()
            for col in ['close_hfq', 'pct_chg', 'amount']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def _load_benchmark_data(self, benchmark: str, start_date: str,
                              end_date: str) -> Optional[pd.DataFrame]:
        """加载基准指数（如 HS300）的日线数据"""
        try:
            df = self.loader.load_index_data(benchmark, start_date, end_date, silent=True)
            if df is not None and not df.empty:
                df = df.sort_values('trade_date').reset_index(drop=True)
                if 'close' in df.columns:
                    df['close'] = pd.to_numeric(df['close'], errors='coerce')
                return df
            return None
        except Exception:
            return None

    def _load_etf_data(self, etf_code: str, start_date: str,
                        end_date: str) -> Optional[pd.DataFrame]:
        """加载 ETF 日线数据"""
        try:
            df = self.loader.load_index_data(etf_code, start_date, end_date, silent=True)
            if df is not None and not df.empty:
                df = df.sort_values('trade_date').reset_index(drop=True)
                for col in ['close', 'amount']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                return df
            return None
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 子因子计算
    # ──────────────────────────────────────────────

    def _calc_theme_returns(self, stocks: List[str], trade_date: str,
                             lookback: int) -> Optional[pd.Series]:
        """计算主题内股票的日收益率序列（按日期对齐）

        取所有股票的平均日收益率作为主题代理收益率。
        如果数据不足以计算日收益率，返回 None。
        """
        if not stocks:
            return None
        start_date = self._calc_start_date_exact(trade_date, lookback + 20)
        df = self._batch_query_hist_prices(stocks, start_date, trade_date)
        if df.empty:
            return None

        # 按 ts_code 和 trade_date 形成宽表（每列一只股票）
        pivot = df.pivot_table(index='trade_date', columns='ts_code',
                                values='close_hfq', aggfunc='first')
        pivot = pivot.dropna(axis=1, how='all').dropna(axis=0, how='all')
        if pivot.empty or pivot.shape[1] == 0:
            return None

        # 计算每只股票的日收益率
        pct = pivot.pct_change().dropna(how='all')
        if pct.empty:
            return None

        # 主题收益率 = 所有股票日收益率的等权平均
        theme_returns = pct.mean(axis=1)
        return theme_returns

    def _calc_theme_beta(self, theme_returns: pd.Series,
                          benchmark_returns: pd.Series,
                          rolling_window: int = 60,
                          min_periods: int = 20) -> float:
        """计算主题相对于基准的贝塔

        使用 rolling_window 天的滚动协方差 / 基准方差。
        如果数据不足，返回 1.0 作为默认值。
        """
        # 对齐两个序列
        combined = pd.concat([theme_returns, benchmark_returns], axis=1, join='inner')
        combined.columns = ['theme', 'benchmark']
        combined = combined.dropna()

        if len(combined) < min_periods:
            return 1.0

        # 取最近 rolling_window 天
        recent = combined.tail(rolling_window)
        if len(recent) < min_periods:
            recent = combined  # 用全部可用数据

        try:
            cov = recent['theme'].cov(recent['benchmark'])
            var = recent['benchmark'].var()
            if var <= 0 or np.isnan(cov) or np.isnan(var):
                return 1.0
            beta = cov / var
            # 限制极端值
            beta = max(-2.0, min(5.0, beta))
            return beta
        except Exception:
            return 1.0

    def _calc_theme_momentum(self, theme_returns: pd.Series,
                               trade_date: str) -> float:
        """计算主题动量得分

        计算 5d / 20d / 60d 累积收益率，按配置权重加权。
        返回 0-100 的得分。
        """
        periods = self.cfg['momentum']['periods']  # [5, 20, 60]
        weights = self.cfg['momentum']['weights']  # [0.2, 0.5, 0.3]

        # 计算累积收益率
        cum_rets = {}
        for period in periods:
            if len(theme_returns) >= period:
                # 取最近 period 天的对数累积收益率
                ret = theme_returns.tail(period).sum()
                cum_rets[period] = ret
            else:
                cum_rets[period] = theme_returns.sum() if len(theme_returns) > 0 else 0.0

        # 加权复合收益率
        weighted_ret = sum(cum_rets[p] * w for p, w in zip(periods, weights))

        # 将收益率映射到 0-100 分
        # 0% → 50分, +10% → 100分, -10% → 0分
        momentum_score = np.clip(50.0 + weighted_ret * 100.0 * 5.0, 0.0, 100.0)
        return float(momentum_score)

    def _calc_theme_trend(self, theme_returns: pd.Series) -> float:
        """计算主题趋势得分（均线排列评分）

        使用短/中/长期均线判断趋势：
        - MA5 > MA20 > MA60 → 完美多头排列 → 高分
        - 均线向上发散 → 加分
        """
        if len(theme_returns) < 60:
            return 50.0

        # 计算累计价格序列（从 1.0 开始）
        price = (1.0 + theme_returns).cumprod()

        # 计算 MA
        ma5 = price.rolling(5).mean().dropna()
        ma20 = price.rolling(20).mean().dropna()
        ma60 = price.rolling(60).mean().dropna()

        if len(ma60) < 1:
            return 50.0

        latest_ma5 = ma5.iloc[-1]
        latest_ma20 = ma20.iloc[-1]
        latest_ma60 = ma60.iloc[-1]
        latest_price = price.iloc[-1]

        # MA 排列评分
        alignment_score = 0.0
        if latest_ma5 > latest_ma20 > latest_ma60:
            alignment_score = 40.0  # 完美多头排列
        elif latest_ma5 > latest_ma20:
            alignment_score = 25.0  # 短中期多头
        elif latest_ma5 > latest_ma60:
            alignment_score = 15.0  # 短期强于长期
        else:
            alignment_score = 5.0  # 空头排列

        # 价格相对 MA60 的位置
        if latest_ma60 > 0:
            price_vs_ma60 = (latest_price / latest_ma60 - 1.0) * 100.0
            if price_vs_ma60 > 5.0:
                ma_score = 30.0
            elif price_vs_ma60 > 0:
                ma_score = 20.0
            elif price_vs_ma60 > -5.0:
                ma_score = 10.0
            else:
                ma_score = 0.0
        else:
            ma_score = 15.0

        # MA 趋势方向（MA20 斜率）
        if len(ma20) >= 5:
            ma20_slope = (ma20.iloc[-1] / ma20.iloc[-5] - 1.0) * 100.0
            if ma20_slope > 1.0:
                slope_score = 30.0
            elif ma20_slope > 0:
                slope_score = 20.0
            elif ma20_slope > -1.0:
                slope_score = 10.0
            else:
                slope_score = 0.0
        else:
            slope_score = 15.0

        total_trend = alignment_score + ma_score + slope_score
        return float(np.clip(total_trend, 0.0, 100.0))

    def _calc_theme_etf_corr(self, theme_name: str, theme_returns: pd.Series,
                               trade_date: str) -> float:
        """计算主题收益率与对应 ETF 收益率的相关系数

        从 theme_config 或常用 ETF 池中寻找对应 ETF，
        加载其日线数据并计算相关性。
        如果没有匹配的 ETF 或数据不足，返回 50（中性）。
        """
        # 尝试从 loader 的 ETF 池获取
        etf_pool = self.loader.get_etf_pool()
        # 尝试从主题名称匹配 ETF（简单的关键词匹配）
        matched_etfs = []
        for code, name in etf_pool.items():
            # 如果主题名称包含 ETF 名称中的关键词
            for kw in name.replace('ETF', '').strip().split():
                if len(kw) >= 2 and kw in theme_name:
                    matched_etfs.append(code)
                    break

        if not matched_etfs:
            return 50.0

        start_date = self._calc_start_date_exact(trade_date, 80)
        end_date = trade_date

        best_corr = 0.0
        for etf_code in matched_etfs:
            try:
                df = self._load_etf_data(etf_code, start_date, end_date)
                if df is None or df.empty or 'close' not in df.columns:
                    continue
                closes = df['close'].dropna()
                if len(closes) < 20:
                    continue
                etf_returns = closes.pct_change().dropna()

                # 对齐两个收益率序列
                combined = pd.concat([theme_returns, etf_returns], axis=1, join='inner')
                combined.columns = ['theme', 'etf']
                combined = combined.dropna()
                if len(combined) < 10:
                    continue
                corr = combined['theme'].corr(combined['etf'])
                if not np.isnan(corr):
                    best_corr = max(best_corr, abs(corr))
            except Exception:
                continue

        # 相关系数 0 → 0分, 0.5 → 50分, 1.0 → 100分
        corr_score = best_corr * 100.0
        return float(np.clip(corr_score, 0.0, 100.0))

    def _calc_theme_volatility(self, theme_returns: pd.Series) -> float:
        """计算主题波动率得分（低波动 = 高分）

        使用 20 日滚动波动率的近期均值。
        """
        if len(theme_returns) < 20:
            return 50.0

        # 计算日波动率（20 日滚动标准差）
        rolling_std = theme_returns.rolling(20).std().dropna()
        if rolling_std.empty:
            return 50.0

        # 取近期均值
        recent_vol = rolling_std.tail(min(60, len(rolling_std))).mean()

        # 年化波动率
        annual_vol = recent_vol * np.sqrt(252)

        # 波动率映射：年化波动率 10% → 100分（低波动高分），
        # 30% → 50分（中性），50% → 0分（高波动低分）
        vol_score = np.clip(100.0 - (annual_vol * 100.0 - 10.0) * 2.5, 0.0, 100.0)
        return float(vol_score)

    def _calc_theme_lifecycle(self, theme_name: str, theme_returns: pd.Series) -> float:
        """计算主题生命周期得分

        基于近期收益率趋势判断主题处于早期/中期/晚期阶段。
        - 近期加速上涨 → 中期（高分配）
        - 近期减速 → 晚期或消退（低分配）
        - 近期平稳 → 早期或成熟期
        """
        if len(theme_returns) < 20:
            return 50.0

        # 对比近 5 日与近 20 日的平均收益率
        ret_5d = theme_returns.tail(5).mean()
        ret_20d = theme_returns.tail(20).mean()

        if len(theme_returns) >= 60:
            ret_60d = theme_returns.tail(60).mean()
        else:
            ret_60d = ret_20d

        # 趋势加速：5日均 > 20日均 > 60日均 → 加速上涨阶段
        # 趋势减速：5日均 < 20日均 > 60日均 → 可能见顶
        # 趋势消退：5日均 < 20日均 < 60日均 → 消退阶段

        if ret_5d > ret_20d > ret_60d:
            # 加速上涨 → 中期阶段，评分最高
            lifecycle_score = 80.0
        elif ret_5d > ret_20d:
            # 短期强于中期 → 可能处于早期加速
            lifecycle_score = 65.0
        elif ret_20d > ret_60d:
            # 中期强于长期但短期开始走弱 → 晚期信号
            lifecycle_score = 40.0
        elif ret_5d > 0:
            # 整体仍正收益但趋势减弱
            lifecycle_score = 30.0
        else:
            # 全面走弱 → 消退阶段
            lifecycle_score = 15.0

        return float(lifecycle_score)

    # ──────────────────────────────────────────────
    # 资金分配方法
    # ──────────────────────────────────────────────

    def _allocate_risk_parity(self, scores: Dict[str, float],
                                theme_names: List[str]) -> Dict[str, float]:
        """风险平价分配：与波动率成反比

        使用 composite_score 中的波动率子因子。
        如果无法计算波动率，使用等权。
        """
        n = len(theme_names)
        if n == 0:
            return {}

        # 从 scores 获取各主题的波动率子因子
        alloc = {}
        total_inv_vol = 0.0
        vol_values = {}

        for name in theme_names:
            score = scores.get(name, 50.0)
            # 分数越低 → 波动率越高 → 分配越少
            # 将 score 转换为逆波动率权重
            inv_vol = max(0.01, 100.0 - score + 10.0)  # 确保非负
            vol_values[name] = inv_vol
            total_inv_vol += inv_vol

        if total_inv_vol > 0:
            for name in theme_names:
                alloc[name] = vol_values[name] / total_inv_vol
        else:
            equal_share = 1.0 / n
            for name in theme_names:
                alloc[name] = equal_share

        return alloc

    def _allocate_score_weighted(self, scores: Dict[str, float],
                                  theme_names: List[str]) -> Dict[str, float]:
        """按综合得分比例分配"""
        n = len(theme_names)
        if n == 0:
            return {}

        total_score = sum(scores.get(name, 0.0) for name in theme_names)
        alloc = {}
        if total_score > 0:
            for name in theme_names:
                alloc[name] = scores.get(name, 0.0) / total_score
        else:
            equal_share = 1.0 / n
            for name in theme_names:
                alloc[name] = equal_share

        return alloc

    def _allocate_equal(self, scores: Dict[str, float],
                         theme_names: List[str]) -> Dict[str, float]:
        """等权分配"""
        n = len(theme_names)
        if n == 0:
            return {}
        equal_share = 1.0 / n
        return {name: equal_share for name in theme_names}

    # ──────────────────────────────────────────────
    # 主评估接口
    # ──────────────────────────────────────────────

    def evaluate(self, trade_date: str, top_themes: List[str]) -> ThemeBetaResult:
        """评估主题贝塔并分配资金

        Args:
            trade_date: 交易日 YYYYMMDD
            top_themes: ThemeResonanceEngine 输出的 top themes 列表

        Returns:
            ThemeBetaResult 包含分配比例和各因子得分
        """
        if not top_themes:
            return ThemeBetaResult(
                allocations={},
                theme_scores={},
                theme_betas={},
                theme_momentum={},
                total_exposure_used=0.0,
                top_themes=[],
                method=self.cfg.get('allocation_method', 'equal'),
                explain={"error": "无主题输入"}
            )

        # ── 1. 加载主题-股票映射 ──
        theme_stock_map = self._load_theme_stock_map(trade_date)
        if not theme_stock_map:
            return ThemeBetaResult(
                allocations={},
                theme_scores={},
                theme_betas={},
                theme_momentum={},
                total_exposure_used=0.0,
                top_themes=top_themes,
                method=self.cfg.get('allocation_method', 'equal'),
                explain={"error": "无法加载主题映射"}
            )

        # ── 2. 加载基准（HS300）数据 ──
        benchmark_code = self.cfg.get('benchmark', '000300.SH')
        lookback = self.cfg.get('lookback', 60)
        start_date = self._calc_start_date(trade_date, lookback)

        bench_df = self._load_benchmark_data(benchmark_code, start_date, trade_date)
        if bench_df is not None and 'close' in bench_df.columns:
            bench_returns = bench_df['close'].pct_change().dropna()
        else:
            bench_returns = None

        # ── 3. 对各主题计算子因子 ──
        weights = self.cfg.get('sub_weights', {})
        total_weight = sum(weights.values())
        beta_cfg = self.cfg.get('beta', {})
        rolling_window = beta_cfg.get('rolling_window', 60)
        min_periods = beta_cfg.get('min_periods', 20)

        theme_scores = {}
        theme_betas = {}
        theme_momentum = {}
        sub_scores_all: Dict[str, List[float]] = {
            'theme_beta': [],
            'theme_momentum': [],
            'theme_trend': [],
            'theme_etf_corr': [],
            'theme_volatility': [],
            'theme_lifecycle': [],
        }
        explain_parts = {}

        for theme_name in top_themes:
            stocks = self._get_theme_stocks(theme_stock_map, theme_name)
            if not stocks:
                theme_scores[theme_name] = 50.0
                theme_betas[theme_name] = 1.0
                theme_momentum[theme_name] = 50.0
                for k in sub_scores_all:
                    sub_scores_all[k].append(50.0)
                explain_parts[theme_name] = "无成分股，默认中性"
                continue

            # ── 3a. 计算主题收益率序列 ──
            theme_ret = self._calc_theme_returns(stocks, trade_date, lookback)

            if theme_ret is None or theme_ret.empty:
                theme_scores[theme_name] = 50.0
                theme_betas[theme_name] = 1.0
                theme_momentum[theme_name] = 50.0
                for k in sub_scores_all:
                    sub_scores_all[k].append(50.0)
                explain_parts[theme_name] = "收益率数据不足，默认中性"
                continue

            # ── 3b. 主题 Beta ──
            if bench_returns is not None:
                beta_val = self._calc_theme_beta(
                    theme_ret, bench_returns,
                    rolling_window=rolling_window,
                    min_periods=min_periods
                )
            else:
                beta_val = 1.0
            theme_betas[theme_name] = beta_val

            # Beta 得分：beta=1 → 50分, beta=0.5 → 75分（防御性高）, beta=2 → 0分（风险高）
            if beta_val <= 0:
                beta_score = 80.0
            elif beta_val >= 2.0:
                beta_score = 0.0
            else:
                beta_score = 100.0 - (beta_val - 0.5) / 1.5 * 100.0
            beta_score = np.clip(beta_score, 0.0, 100.0)

            # ── 3c. 主题动量 ──
            mom_score = self._calc_theme_momentum(theme_ret, trade_date)
            theme_momentum[theme_name] = mom_score

            # ── 3d. 主题趋势 ──
            trend_score = self._calc_theme_trend(theme_ret)

            # ── 3e. 主题 ETF 相关性 ──
            etf_corr_score = self._calc_theme_etf_corr(theme_name, theme_ret, trade_date)

            # ── 3f. 主题波动率（逆指标） ──
            vol_score = self._calc_theme_volatility(theme_ret)

            # ── 3g. 主题生命周期 ──
            lifecycle_score = self._calc_theme_lifecycle(theme_name, theme_ret)

            # ── 记录子因子 ──
            sub_scores_all['theme_beta'].append(beta_score)
            sub_scores_all['theme_momentum'].append(mom_score)
            sub_scores_all['theme_trend'].append(trend_score)
            sub_scores_all['theme_etf_corr'].append(etf_corr_score)
            sub_scores_all['theme_volatility'].append(vol_score)
            sub_scores_all['theme_lifecycle'].append(lifecycle_score)

            # ── 4. 综合得分 ──
            if total_weight > 0:
                composite = (
                    beta_score * weights.get('theme_beta', 0) +
                    mom_score * weights.get('theme_momentum', 0) +
                    trend_score * weights.get('theme_trend', 0) +
                    etf_corr_score * weights.get('theme_etf_corr', 0) +
                    vol_score * weights.get('theme_volatility', 0) +
                    lifecycle_score * weights.get('theme_lifecycle', 0)
                ) / total_weight
            else:
                composite = 50.0

            composite = max(0.0, min(100.0, composite))
            theme_scores[theme_name] = round(composite, 2)

            explain_parts[theme_name] = (
                f"β={beta_val:.2f}({beta_score:.0f}分), "
                f"动量={mom_score:.0f}分, 趋势={trend_score:.0f}分, "
                f"ETF相关={etf_corr_score:.0f}分, 波动={vol_score:.0f}分, "
                f"生命周期={lifecycle_score:.0f}分 → 综合{composite:.1f}分"
            )

        # ── 5. 按综合得分排序，取 top_n ──
        top_n = self.cfg.get('top_n', 5)
        sorted_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
        top_theme_names = [t[0] for t in sorted_themes[:top_n]]

        # ── 6. 资金分配 ──
        method = self.cfg.get('allocation_method', 'risk_parity')
        if method == 'risk_parity':
            raw_alloc = self._allocate_risk_parity(theme_scores, top_theme_names)
        elif method == 'score_weighted':
            raw_alloc = self._allocate_score_weighted(theme_scores, top_theme_names)
        else:  # equal
            raw_alloc = self._allocate_equal(theme_scores, top_theme_names)
            method = 'equal'

        # ── 7. 应用上下限限制 ──
        max_alloc = self.cfg.get('max_allocation_per_theme', 0.40)
        min_alloc = self.cfg.get('min_allocation_per_theme', 0.05)

        # 先裁剪上限，再将多余按比例重新分配
        clipped = {}
        remaining = 0.0
        for name, alloc_pct in raw_alloc.items():
            if alloc_pct > max_alloc:
                clipped[name] = max_alloc
                remaining += alloc_pct - max_alloc
            else:
                clipped[name] = alloc_pct

        # 将剩余部分按比例分配给未超限的主题
        if remaining > 0:
            under_names = [n for n in clipped if clipped[n] < max_alloc]
            under_total = sum(clipped[n] for n in under_names)
            if under_total > 0:
                for n in under_names:
                    extra = remaining * (clipped[n] / under_total)
                    clipped[n] = min(max_alloc, clipped[n] + extra)

        # 应用下限
        final_alloc = {}
        for name in top_theme_names:
            final_alloc[name] = max(min_alloc, clipped.get(name, 0.0))

        # 归一化到总和为 1.0
        total_alloc = sum(final_alloc.values())
        if total_alloc > 0:
            final_alloc = {k: v / total_alloc for k, v in final_alloc.items()}

        # ── 8. 构造结果 ──
        # 计算总分配仓位（所有主题分配之和，应为 1.0）
        total_exposure = sum(final_alloc.values())

        explain = {
            "allocation_method": f"分配方法: {method}",
            "max_allocation_per_theme": f"单主题上限: {max_alloc*100:.0f}%",
            "min_allocation_per_theme": f"单主题下限: {min_alloc*100:.0f}%",
            "total_themes": f"输入主题数: {len(top_themes)}，选用前 {top_n} 个",
        }
        # 仅保留使用了 top_theme_names 的 explain 信息
        for name in top_theme_names:
            if name in explain_parts:
                explain[name] = explain_parts[name]

        return ThemeBetaResult(
            allocations=final_alloc,
            theme_scores={k: v for k, v in theme_scores.items() if k in top_theme_names},
            theme_betas={k: v for k, v in theme_betas.items() if k in top_theme_names},
            theme_momentum={k: v for k, v in theme_momentum.items() if k in top_theme_names},
            total_exposure_used=round(total_exposure, 6),
            top_themes=top_theme_names,
            method=method,
            explain=explain,
        )
