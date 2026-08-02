#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题高辨识度个股识别模块

用于未来选股重点识别，融合机构+游资+主题+连板四维度，
输出 S/A/B/C 分级标签。

评分框架：
    高辨识度评分 = 0.30*机构抱团分 + 0.25*游资活跃分
                  + 0.25*主题地位分 + 0.20*连板基因分

分级：
    S级 (≥85) : 龙头+机构抱团，重点跟踪
    A级 (75-84): 龙二/中军，关注回调机会
    B级 (65-74): 补涨股，低吸候选
    C级 (<65) : 跟风股，谨慎参与
"""
import os
import sys
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List
import pandas as pd

# Windows GBK 控制台输出修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 添加项目路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

CACHE_DIR = r"d:\mystock\cache_daily"
os.makedirs(CACHE_DIR, exist_ok=True)


class ThemeRecognitionScorer:
    """主题高辨识度个股评分器"""

    # 评分权重
    W_INST = 0.30   # 机构抱团分
    W_HOT = 0.25    # 游资活跃分
    W_THEME = 0.25  # 主题地位分
    W_LIMIT = 0.20  # 连板基因分

    def __init__(self, pro=None, df=None):
        """
        Args:
            pro: tushare pro_api 实例（保留兼容，优先使用 df）
            df: DataFetcher 实例（含 get_fund_portfolio / get_hk_hold_by_code 等方法）
        """
        self._pro = pro
        self._df = df
        self._cache: Dict[str, Dict] = {}
        self._north_hold_cache: Dict[str, float] = {}  # ts_code -> hold_ratio
        self._north_hold_loaded = False
        self._billboard_counts_cache: Dict[str, int] = {}  # ts_code -> 近60天上榜次数
        self._billboard_loaded = False

    def _get_df(self):
        """懒加载 DataFetcher 实例（统一缓存入口）"""
        if self._df is None:
            try:
                # 动态导入，避免硬依赖
                import sys as _sys
                mfp_dir = os.path.join(BASE_DIR, 'multi_factor_picker')
                if mfp_dir not in _sys.path:
                    _sys.path.insert(0, mfp_dir)
                from data_fetcher import DataFetcher  # type: ignore
                # 读取 token
                token = os.getenv("TUSHARE_TOKEN")
                if not token:
                    env_path = os.path.join(BASE_DIR, '.env')
                    if os.path.exists(env_path):
                        with open(env_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith('TUSHARE_TOKEN=') and not line.startswith('#'):
                                    token = line.split('=', 1)[1].strip()
                                    break
                if not token:
                    return None
                # 最小化 config：缓存目录 + TTL
                config = {
                    'cache': {
                        'enabled': True,
                        'dir': os.path.join(BASE_DIR, 'multi_factor_picker', 'cache'),
                        'expire_hours': 168,  # 7 天
                    },
                    'tushare': {'max_retry': 3, 'retry_delay': 5},
                }
                self._df = DataFetcher(token, config)
            except Exception:
                return None
        return self._df

    def _get_pro(self):
        """保留兼容：仅在 DataFetcher 不可用时降级直连 tushare"""
        if self._pro is None:
            try:
                import tushare as ts
                token = os.getenv("TUSHARE_TOKEN")
                if not token:
                    env_path = os.path.join(BASE_DIR, '.env')
                    if os.path.exists(env_path):
                        with open(env_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith('TUSHARE_TOKEN=') and not line.startswith('#'):
                                    token = line.split('=', 1)[1].strip()
                                    break
                if token:
                    ts.set_token(token)
                    self._pro = ts.pro_api()
            except Exception:
                pass
        return self._pro

    def _load_north_hold_batch(self, trade_date: str):
        """批量加载某日北向持股比例（按股票代码查询，避免每只股票调用API）

        注：tushare的 hk_hold(trade_date=...) 返回港股通南向数据，
        北向资金持股（沪深股通）需用 hk_hold(ts_code=...) 按股票查询。
        为节省API调用，本方法仅标记已加载，实际查询在 _score_institution 中按需进行。
        """
        self._north_hold_loaded = True  # 标记已尝试加载（按需查询模式）

    def _load_billboard_counts_batch(self):
        """批量加载近60天龙虎榜上榜次数（一次性查询所有交易日，避免逐股查询）

        Tushare 的 top_list 接口必须传 trade_date（单日查询），
        因此批量预加载近60天所有交易日的龙虎榜，统计每只股票的上榜次数。
        结果缓存在 self._billboard_counts_cache，供所有股票共享。
        """
        if self._billboard_loaded:
            return
        self._billboard_loaded = True  # 标记已尝试加载（即使失败也不重试）

        df = self._get_df()
        if df is None:
            return
        try:
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
            counts = df.get_billboard_counts_batch(start_date=start, end_date=end)
            if isinstance(counts, dict):
                self._billboard_counts_cache = counts
                if counts:
                    print(f"  [龙虎榜] 预加载近60天 {len(counts)} 只上榜股票")
        except Exception:
            pass

    # ─────────────────────────────────────────────
    # 维度1：机构抱团分 (0~100)
    # ─────────────────────────────────────────────
    def _score_institution(self, ts_code: str, market_cap_b: float) -> Tuple[float, Dict]:
        """机构抱团评分：北向持股 + 公募基金持有数"""
        df = self._get_df()
        details = {}
        nb_score = 0.0
        fund_score = 0.0

        # 北向持股比例评分（外资优先，最大权重）
        # 优先使用 DataFetcher 统一缓存（hk_hold_code_{ts_code}.json）
        nb_ratio = self._north_hold_cache.get(ts_code, 0.0)
        if nb_ratio <= 0 and df is not None:
            try:
                hold_info = df.get_hk_hold_by_code(ts_code)
                nb_ratio = float(hold_info.get('ratio', 0.0)) if hold_info else 0.0
                if nb_ratio > 0:
                    self._north_hold_cache[ts_code] = nb_ratio
            except Exception:
                pass
        # 降级：DataFetcher 不可用时直接查 tushare
        if nb_ratio <= 0 and df is None:
            pro = self._get_pro()
            if pro is not None:
                try:
                    hold_df = pro.hk_hold(ts_code=ts_code, fields='ts_code,trade_date,vol,ratio')
                    if hold_df is not None and len(hold_df) > 0:
                        hold_df = hold_df.sort_values('trade_date', ascending=False)
                        latest = hold_df.iloc[0]
                        if 'ratio' in hold_df.columns and pd.notna(latest.get('ratio', None)):
                            nb_ratio = float(latest['ratio'])
                            self._north_hold_cache[ts_code] = nb_ratio
                except Exception:
                    pass

        # 北向持股评分：外资优先最大权重（调整后门槛更合理）
        # ≥3%=100分；1-3%=85分；0.5-1%=70分；0.1-0.5%=55分；>0=40分；0=0分
        if nb_ratio >= 3:
            nb_score = 100
        elif nb_ratio >= 1:
            nb_score = 85
        elif nb_ratio >= 0.5:
            nb_score = 70
        elif nb_ratio >= 0.1:
            nb_score = 55
        elif nb_ratio > 0:
            nb_score = 40
        else:
            nb_score = 0  # 无北向持股
        details['north_hold_ratio'] = round(nb_ratio, 3)
        details['north_score'] = nb_score

        # 公募基金持仓评分（调整后门槛更合理）
        if df is not None:
            try:
                fund = df.get_fund_portfolio(ts_code)
                fund_count = fund.get('fund_count', 0)
                fund_ratio_change = fund.get('fund_ratio_change', 0.0)
                # 机构覆盖数（调整后）
                if fund_count >= 20:
                    fund_score = 100
                elif fund_count >= 10:
                    fund_score = 90
                elif fund_count >= 5:
                    fund_score = 75
                elif fund_count >= 1:
                    fund_score = 55
                else:
                    fund_score = 30
                # 加仓加分
                if fund_ratio_change > 0.5:
                    fund_score = min(100, fund_score + 10)
                elif fund_ratio_change < -0.5:
                    fund_score = max(0, fund_score - 10)
                details['fund_count'] = fund_count
                details['fund_ratio_change'] = round(fund_ratio_change, 3)
            except Exception:
                fund_score = 50  # 数据缺失给中位值
                details['fund_error'] = True
        else:
            fund_score = 50

        details['fund_score'] = fund_score

        # 综合：北向60% + 公募40%（外资优先）
        total = 0.6 * nb_score + 0.4 * fund_score
        details['institution_score'] = round(total, 1)
        return float(total), details

    # ─────────────────────────────────────────────
    # 维度2：游资活跃分 (0~100)
    # ─────────────────────────────────────────────
    def _score_hot_money(self, ts_code: str) -> Tuple[float, Dict]:
        """游资活跃评分：龙虎榜 + 成交额"""
        df = self._get_df()
        details = {}
        lb_score = 0.0
        amt_score = 0.0

        end = datetime.now().strftime('%Y%m%d')
        start_lb = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
        start_daily = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

        # 龙虎榜上榜次数（近60天）— 优先用批量预加载缓存
        lb_count = 0
        if self._billboard_counts_cache:
            lb_count = self._billboard_counts_cache.get(ts_code, 0)
        elif df is not None:
            # 降级：批量缓存未加载时，查单日（仅 end_date 当天）
            try:
                bb_df = df.get_billboard_list(ts_code, start_date=start_lb, end_date=end)
                lb_count = len(bb_df) if bb_df is not None else 0
            except Exception:
                details['billboard_error'] = True
        else:
            pro = self._get_pro()
            if pro is not None:
                try:
                    bb_df = pro.top_list(trade_date=end, ts_code=ts_code)
                    lb_count = len(bb_df) if bb_df is not None else 0
                except Exception:
                    details['billboard_error'] = True
        # 近60天上榜3次=100分；2次=80分；1次=60分；0次=0分
        if lb_count >= 3:
            lb_score = 100
        elif lb_count == 2:
            lb_score = 80
        elif lb_count == 1:
            lb_score = 60
        else:
            lb_score = 0
        if details.get('billboard_error'):
            lb_score = 50
        details['billboard_count_60d'] = lb_count

        # 成交额评分（近20日均额）— 优先 DataFetcher 统一缓存
        if df is not None:
            try:
                daily_df = df.get_daily_by_code(
                    ts_code, start_date=start_daily, end_date=end,
                    fields='ts_code,trade_date,amount',
                )
                if daily_df is not None and len(daily_df) > 0:
                    daily_df = daily_df.sort_values('trade_date').tail(20)
                    avg_amt = float(daily_df['amount'].mean()) / 100000  # 千元→亿元
                    # 20日均额：>20亿=100分；10-20亿=80分；5-10亿=60分；2-5亿=40分；<2亿=20分
                    if avg_amt >= 20:
                        amt_score = 100
                    elif avg_amt >= 10:
                        amt_score = 80
                    elif avg_amt >= 5:
                        amt_score = 60
                    elif avg_amt >= 2:
                        amt_score = 40
                    else:
                        amt_score = 20
                    details['avg_amount_20d_yi'] = round(avg_amt, 2)
                else:
                    amt_score = 50
            except Exception:
                amt_score = 50
                details['amount_error'] = True
        else:
            if self._get_pro() is not None:
                try:
                    pro = self._get_pro()
                    # V2: 优先 daily_cache 表
                    daily_df = None
                    try:
                        from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                        _, _max_date = get_daily_cache_range(ts_code)
                        if _max_date is not None and str(_max_date) >= str(end):
                            daily_df = get_daily_cache(ts_code, start_daily, end)
                            if daily_df is not None and not daily_df.empty:
                                daily_df['trade_date'] = daily_df['trade_date'].astype(str)
                    except Exception:
                        pass
                    if daily_df is None or daily_df.empty:
                        daily_df = pro.daily(ts_code=ts_code, start_date=start_daily, end_date=end)
                        if daily_df is not None and not daily_df.empty:
                            try:
                                from stock_cache import batch_insert_daily_cache
                                batch_insert_daily_cache(daily_df)
                            except Exception:
                                pass
                    if daily_df is not None and len(daily_df) > 0:
                        daily_df = daily_df.sort_values('trade_date').tail(20)
                        avg_amt = float(daily_df['amount'].mean()) / 100000
                        if avg_amt >= 20:
                            amt_score = 100
                        elif avg_amt >= 10:
                            amt_score = 80
                        elif avg_amt >= 5:
                            amt_score = 60
                        elif avg_amt >= 2:
                            amt_score = 40
                        else:
                            amt_score = 20
                        details['avg_amount_20d_yi'] = round(avg_amt, 2)
                    else:
                        amt_score = 50
                except Exception:
                    amt_score = 50
                    details['amount_error'] = True
            else:
                amt_score = 50

        details['billboard_score'] = lb_score
        details['amount_score'] = amt_score

        # 综合：龙虎榜60% + 成交额40%
        total = 0.6 * lb_score + 0.4 * amt_score
        details['hot_money_score'] = round(total, 1)
        return float(total), details

    # ─────────────────────────────────────────────
    # 维度3：主题地位分 (0~100)
    # ─────────────────────────────────────────────
    def _score_theme_position(self, ts_code: str, theme_stocks: List[Dict]) -> Tuple[float, Dict]:
        """主题内地位评分：主题内涨幅排名 + 成交额排名 + 主题纯度"""
        details = {}
        if not theme_stocks:
            return 50.0, {"error": "no theme stocks"}

        # 找到当前股票在主题中的排名
        sorted_by_score = sorted(theme_stocks, key=lambda x: -x.get('score', 0))
        rank_by_score = next((i + 1 for i, s in enumerate(sorted_by_score) if s.get('code') == ts_code), 99)
        total = len(theme_stocks)

        # 主题内排名评分
        if rank_by_score <= 3:
            rank_score = 100  # TOP3
        elif rank_by_score <= 10:
            rank_score = 80
        elif rank_by_score <= 20:
            rank_score = 60
        elif rank_by_score <= 50:
            rank_score = 40
        else:
            rank_score = 20
        details['theme_rank'] = rank_by_score
        details['theme_total'] = total
        details['rank_score'] = rank_score

        # via 来源评分（leader > core > dc > fallback）
        stock_info = next((s for s in theme_stocks if s.get('code') == ts_code), None)
        via = stock_info.get('via', '') if stock_info else ''
        if via == 'leader_company':
            via_score = 100
        elif via == 'core_company':
            via_score = 85
        elif via in ('dc_industry_board', 'stock_basic_industry'):
            via_score = 65
        elif via == 'concept_as_industry':
            via_score = 50
        elif via == 'concept_fallback':
            via_score = 30
        else:
            via_score = 50
        details['via'] = via
        details['via_score'] = via_score

        # 主题股数评分（小主题更稀缺）
        if total <= 10:
            size_score = 100
        elif total <= 30:
            size_score = 80
        elif total <= 60:
            size_score = 60
        elif total <= 100:
            size_score = 40
        else:
            size_score = 20
        details['size_score'] = size_score

        # 综合：排名50% + via30% + 主题规模20%
        total_score = 0.5 * rank_score + 0.3 * via_score + 0.2 * size_score
        details['theme_position_score'] = round(total_score, 1)
        return float(total_score), details

    # ─────────────────────────────────────────────
    # 维度4：连板基因分 (0~100)
    # ─────────────────────────────────────────────
    def _score_limit_up_gene(self, ts_code: str) -> Tuple[float, Dict]:
        """连板基因评分：最大连板高度 + 涨停频率"""
        df = self._get_df()
        details = {}

        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        # 优先 DataFetcher 统一缓存（与游资活跃分共用缓存命中）
        daily_df = None
        if df is not None:
            try:
                daily_df = df.get_daily_by_code(
                    ts_code, start_date=start_str, end_date=end_str,
                    fields='ts_code,trade_date,pct_chg',
                )
            except Exception:
                pass
        if (daily_df is None or len(daily_df) == 0):
            pro = self._get_pro()
            if pro is None:
                return 50.0, {"error": "no data source"}
            try:
                # V2: 优先 daily_cache 表
                try:
                    from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                    _, _max_date = get_daily_cache_range(ts_code)
                    if _max_date is not None and str(_max_date) >= str(end_str):
                        daily_df = get_daily_cache(ts_code, start_str, end_str)
                        if daily_df is not None and not daily_df.empty:
                            daily_df['trade_date'] = daily_df['trade_date'].astype(str)
                except Exception:
                    pass
                if daily_df is None or daily_df.empty:
                    daily_df = pro.daily(
                        ts_code=ts_code,
                        start_date=start_str,
                        end_date=end_str,
                        fields='ts_code,trade_date,pct_chg',
                    )
                    if daily_df is not None and not daily_df.empty:
                        try:
                            from stock_cache import batch_insert_daily_cache
                            batch_insert_daily_cache(daily_df)
                        except Exception:
                            pass
            except Exception as e:
                return 50.0, {"error": str(e)[:40]}

        if daily_df is None or len(daily_df) == 0:
            return 50.0, {"data_count": 0}

        daily_df['pct_chg'] = daily_df['pct_chg'].fillna(0)
        limit_up_count = int((daily_df['pct_chg'] >= 9.9).sum())

        # 计算最大连板高度
        max_consecutive = 0
        current_streak = 0
        for pct in daily_df['pct_chg'].values:
            if pct >= 9.9:
                current_streak += 1
                max_consecutive = max(max_consecutive, current_streak)
            else:
                current_streak = 0

        trading_days = len(daily_df)

        # 连板高度评分（调整后更符合实际市场分布）
        # ≥5板=100分；4板=92分；3板=80分；2板=65分；1板=50分；0板=25分
        if max_consecutive >= 5:
            streak_score = 100
        elif max_consecutive >= 4:
            streak_score = 92
        elif max_consecutive >= 3:
            streak_score = 80
        elif max_consecutive >= 2:
            streak_score = 65
        elif max_consecutive >= 1:
            streak_score = 50
        else:
            streak_score = 25

        # 涨停频率评分
        freq_pct = limit_up_count / trading_days * 100 if trading_days > 0 else 0
        freq_score = min(100, freq_pct * 5)  # 20%涨停率=100分

        # 综合：连板高度60% + 涨停频率40%
        total = 0.6 * streak_score + 0.4 * freq_score

        details['max_consecutive_zt'] = max_consecutive
        details['limit_up_count'] = limit_up_count
        details['freq_pct'] = round(freq_pct, 2)
        details['streak_score'] = streak_score
        details['freq_score'] = freq_score
        details['limit_up_gene_score'] = round(total, 1)
        return float(total), details

    # ─────────────────────────────────────────────
    # 综合评分
    # ─────────────────────────────────────────────
    def compute(self, ts_code: str, market_cap_b: float,
                theme_stocks: Optional[List[Dict]] = None) -> Dict:
        """
        综合辨识度评分

        Args:
            ts_code: 股票代码
            market_cap_b: 市值（亿元）
            theme_stocks: 该股票所属主题的全部成份股列表（用于主题地位评分）

        Returns:
            Dict with: total_score, grade, dimensions, details
        """
        cache_key = ts_code
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 批量加载北向数据（首次调用时）
        if not self._north_hold_loaded:
            self._load_north_hold_batch(datetime.now().strftime('%Y%m%d'))

        # 批量加载龙虎榜次数（首次调用时）
        if not self._billboard_loaded:
            self._load_billboard_counts_batch()

        # 四维度评分
        s_inst, d_inst = self._score_institution(ts_code, market_cap_b)
        s_hot, d_hot = self._score_hot_money(ts_code)
        s_theme, d_theme = self._score_theme_position(ts_code, theme_stocks or [])
        s_limit, d_limit = self._score_limit_up_gene(ts_code)

        # 综合评分
        total = (
            self.W_INST * s_inst +
            self.W_HOT * s_hot +
            self.W_THEME * s_theme +
            self.W_LIMIT * s_limit
        )

        # 分级（调整后阈值更符合实际市场分布）
        if total >= 80:
            grade = "S"
        elif total >= 70:
            grade = "A"
        elif total >= 60:
            grade = "B"
        else:
            grade = "C"

        # 标签描述
        if grade == "S":
            label = "龙头+机构抱团，重点跟踪"
        elif grade == "A":
            label = "龙二/中军，关注回调机会"
        elif grade == "B":
            label = "补涨股，低吸候选"
        else:
            label = "跟风股，谨慎参与"

        result = {
            "ts_code": ts_code,
            "total_score": round(total, 1),
            "grade": grade,
            "label": label,
            "dimensions": {
                "institution": {"score": round(s_inst, 1), **d_inst},
                "hot_money": {"score": round(s_hot, 1), **d_hot},
                "theme_position": {"score": round(s_theme, 1), **d_theme},
                "limit_up_gene": {"score": round(s_limit, 1), **d_limit},
            },
        }

        self._cache[cache_key] = result
        return result

    # ─────────────────────────────────────────────
    # 批量评分并输出CSV
    # ─────────────────────────────────────────────
    def score_theme_stocks(self, theme_name: str, theme_stocks: List[Dict],
                           output_dir: Optional[str] = None) -> List[Dict]:
        """
        批量评分主题内所有股票

        Args:
            theme_name: 主题名称
            theme_stocks: 主题成份股列表
            output_dir: 输出目录，None=不输出

        Returns:
            排序后的评分结果列表
        """
        print(f"\n[辨识度评分] {theme_name} ({len(theme_stocks)}只)")
        results = []
        for i, stock in enumerate(theme_stocks):
            if (i + 1) % 20 == 0:
                print(f"  进度 {i+1}/{len(theme_stocks)}...")
            try:
                # 获取市值（从 cache 或默认值）
                code = stock.get('code', '')
                market_cap_b = stock.get('market_cap_b', 100)  # 默认100亿
                result = self.compute(code, market_cap_b, theme_stocks)
                result['name'] = stock.get('name', '')
                result['theme'] = theme_name
                results.append(result)
            except Exception as e:
                print(f"  [Skip] {code}: {e}")
                continue

        # 按总分排序
        results.sort(key=lambda x: -x['total_score'])

        # 输出CSV
        if output_dir and results:
            os.makedirs(output_dir, exist_ok=True)
            today = datetime.now().strftime('%Y%m%d')
            csv_path = os.path.join(output_dir, f"recognition_{theme_name}_{today}.csv")
            try:
                import pandas as pd
                df_data = []
                for r in results:
                    row = {
                        'code': r['ts_code'],
                        'name': r.get('name', ''),
                        'theme': r['theme'],
                        'total_score': r['total_score'],
                        'grade': r['grade'],
                        'label': r['label'],
                        'institution_score': r['dimensions']['institution']['score'],
                        'north_hold_ratio': r['dimensions']['institution'].get('north_hold_ratio', 0),
                        'fund_count': r['dimensions']['institution'].get('fund_count', 0),
                        'hot_money_score': r['dimensions']['hot_money']['score'],
                        'billboard_count': r['dimensions']['hot_money'].get('billboard_count_60d', 0),
                        'theme_position_score': r['dimensions']['theme_position']['score'],
                        'theme_rank': r['dimensions']['theme_position'].get('theme_rank', 99),
                        'limit_up_gene_score': r['dimensions']['limit_up_gene']['score'],
                        'max_consecutive_zt': r['dimensions']['limit_up_gene'].get('max_consecutive_zt', 0),
                        'limit_up_count': r['dimensions']['limit_up_gene'].get('limit_up_count', 0),
                    }
                    df_data.append(row)
                pd.DataFrame(df_data).to_csv(csv_path, index=False, encoding='utf-8-sig')
                print(f"  [Output] {csv_path}")
            except Exception as e:
                print(f"  [CSV Error] {e}")

        # 打印TOP10
        if results:
            print(f"\n  === {theme_name} TOP10 高辨识度个股 ===")
            for r in results[:10]:
                print(f"    [{r['grade']}] {r.get('name', '')}({r['ts_code']}) "
                      f"总分={r['total_score']:.1f} "
                      f"机构={r['dimensions']['institution']['score']:.0f} "
                      f"游资={r['dimensions']['hot_money']['score']:.0f} "
                      f"主题={r['dimensions']['theme_position']['score']:.0f} "
                      f"连板={r['dimensions']['limit_up_gene']['score']:.0f}")

        return results


# ─────────────────────────────────────────────
# CLI 测试入口
# ─────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='主题高辨识度个股识别')
    parser.add_argument('-t', '--theme', type=str, default='人形机器人',
                        help='主题名称（默认人形机器人）')
    parser.add_argument('-n', '--top', type=int, default=20,
                        help='显示前N只（默认20）')
    parser.add_argument('--output', type=str, default=r'd:\mystock\solo\multi_factor_picker\output',
                        help='CSV输出目录')
    args = parser.parse_args()

    # 加载主题映射
    map_path = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
    if not os.path.exists(map_path):
        print(f"[Error] 主题映射文件不存在: {map_path}")
        sys.exit(1)

    with open(map_path, 'r', encoding='utf-8') as f:
        theme_map = json.load(f)

    if args.theme not in theme_map.get('themes', {}):
        print(f"[Error] 主题 '{args.theme}' 不存在")
        print(f"可用主题: {', '.join(list(theme_map.get('themes', {}).keys())[:20])}...")
        sys.exit(1)

    theme_stocks = theme_map['themes'][args.theme]
    print(f"[Load] 主题 '{args.theme}' 共 {len(theme_stocks)} 只成份股")

    # 初始化评分器
    scorer = ThemeRecognitionScorer()

    # 批量评分
    results = scorer.score_theme_stocks(args.theme, theme_stocks, args.output)

    # 输出TOP N
    print(f"\n{'='*70}")
    print(f"=== {args.theme} TOP{args.top} 高辨识度个股 ===")
    print(f"{'='*70}")
    print(f"{'排名':<4}{'等级':<4}{'股票名称':<10}{'代码':<12}{'总分':>6}{'机构':>6}{'游资':>6}{'主题':>6}{'连板':>6}{'北向%':>7}")
    print("-" * 70)
    for i, r in enumerate(results[:args.top]):
        d = r['dimensions']
        nb = d['institution'].get('north_hold_ratio', 0)
        print(f"{i+1:<4}{r['grade']:<4}{r.get('name',''):<10}{r['ts_code']:<12}"
              f"{r['total_score']:>6.1f}"
              f"{d['institution']['score']:>6.0f}"
              f"{d['hot_money']['score']:>6.0f}"
              f"{d['theme_position']['score']:>6.0f}"
              f"{d['limit_up_gene']['score']:>6.0f}"
              f"{nb:>7.2f}")

    # 分级统计
    grade_count = {'S': 0, 'A': 0, 'B': 0, 'C': 0}
    for r in results:
        grade_count[r['grade']] = grade_count.get(r['grade'], 0) + 1
    print(f"\n分级统计: S={grade_count['S']} A={grade_count['A']} B={grade_count['B']} C={grade_count['C']}")
