"""Market Regime Engine V3 - 主编排器 + Alpha引擎

7层 Pipeline:
1. Market Regime (指数强度 → 宽度 → 情绪 → 风格 → 风险偏好 → 主题共振 → 总评分 → 状态机 → 热度)
2. Exposure (仓位模型)
3. Theme Beta (主题资金分配)
4. Full Ranking (龙头质量 + 全市场截面排序 + 资金行为)
5. Probability (概率预测模型)
6. Trading Style (交易风格)
7. Portfolio (组合优化 + 风控执行)

Alpha引擎扩展:
  - Cross-Sectional Ranking: 20因子全市场排序，扩展候选池至Top50
  - Capital Flow Engine: 北向资金 + 机构资金流 + 大单强度
  - Probability Model: Logistic Regression 回调成功率预测
  - Portfolio Optimizer: Kelly准则 + Risk Parity + 均值方差
"""

import os
import sys
import numpy as np
import yaml
import argparse
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_regime_v3.engines.index_engine import IndexStrengthEngine
from market_regime_v3.engines.breadth_engine import BreadthEngine
from market_regime_v3.engines.sentiment_engine import SentimentEngine
from market_regime_v3.engines.style_engine import StyleEngine
from market_regime_v3.engines.risk_engine import RiskAppetiteEngine
from market_regime_v3.engines.theme_resonance import ThemeResonanceEngine
from market_regime_v3.engines.market_score import MarketScoreEngine
from market_regime_v3.engines.state_machine import StateMachine
from market_regime_v3.engines.exposure_model import ExposureModel
from market_regime_v3.engines.heat_engine import HeatEngine
from market_regime_v3.engines.rally_pullback_engine import RallyPullbackEngine
from market_regime_v3.engines.theme_beta import ThemeBetaEngine
from market_regime_v3.engines.leader_quality import LeaderQualityEngine
from market_regime_v3.engines.trading_style import TradingStyleEngine
from market_regime_v3.engines.risk_control import RiskControlEngine
from market_regime_v3.reporter import MarketReportGenerator
from market_regime_v3.explainer import MarketExplainer
from market_regime_v3.wechat_push import send_pushplus, build_summary

# Alpha Engines
from market_regime_v3.alpha_engines.cross_sectional import CrossSectionalRanking
from market_regime_v3.alpha_engines.capital_flow import CapitalFlowEngine
from market_regime_v3.alpha_engines.probability import ProbabilityModel
from market_regime_v3.alpha_engines.portfolio import PortfolioOptimizer, OptimizationMethod
from market_regime_v3.alpha_engines.pattern_engine import HistoricalPatternEngine
from market_regime_v3.alpha_engines.ev_engine import EVEngine
from market_regime_v3.alpha_engines.smart_money_v2 import SmartMoneyScoreV2
from market_regime_v3.alpha_engines.risk_budget_position import RiskBudgetPositionEngine

import stock_cache as sc


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


class MarketRegimeV3:
    """Market Regime Engine V3 主编排器 (6层Pipeline)"""

    def __init__(self, config_path: str = None):
        self.config = load_config(config_path)

        # 第1层: Market Regime
        self.index_engine = IndexStrengthEngine(self.config)
        self.breadth_engine = BreadthEngine(self.config)
        self.sentiment_engine = SentimentEngine(self.config)
        self.style_engine = StyleEngine(self.config)
        self.risk_engine = RiskAppetiteEngine(self.config)
        self.theme_engine = ThemeResonanceEngine(self.config)
        self.market_score_engine = MarketScoreEngine(self.config)
        self.state_machine = StateMachine(self.config)
        self.heat_engine = HeatEngine(self.config)

        # 第2层: Exposure
        self.exposure_model = ExposureModel(self.config)

        # 第3层: Theme Beta
        self.theme_beta_engine = ThemeBetaEngine(self.config)

        # 第4层: Leader Quality
        self.leader_quality_engine = LeaderQualityEngine(self.config)

        # Alpha 引擎扩展
        alpha_cfg = self.config
        self.cross_sectional_engine = CrossSectionalRanking(alpha_cfg) if alpha_cfg.get('cross_sectional', {}).get('enabled', True) else None
        self.capital_flow_engine = CapitalFlowEngine(alpha_cfg) if alpha_cfg.get('capital_flow', {}).get('enabled', True) else None
        self.probability_model = ProbabilityModel(alpha_cfg) if alpha_cfg.get('probability_model', {}).get('enabled', True) else None
        self.portfolio_optimizer = PortfolioOptimizer(alpha_cfg) if alpha_cfg.get('portfolio_optimizer', {}).get('enabled', True) else None

        # 第5层: Trading Style
        self.trading_style_engine = TradingStyleEngine(self.config)

        # 第6层: Risk Control (简化)
        self.risk_control_engine = RiskControlEngine(self.config)

        # 第7层: Portfolio (集成组合优化)
        # 运行在 risk_control 之后

        # ── V6.1 引擎扩展 ──
        self.pattern_engine = HistoricalPatternEngine(self.config) if self.config.get('pattern_engine', {}).get('enabled', True) else None
        self.ev_engine = EVEngine(self.config) if self.config.get('ev_engine', {}).get('enabled', True) else None
        self.smart_money_v2 = SmartMoneyScoreV2(self.config) if self.config.get('smart_money_v2', {}).get('enabled', True) else None
        self.risk_budget_engine = RiskBudgetPositionEngine(self.config) if self.config.get('risk_budget_position', {}).get('enabled', True) else None

        self.reporter = MarketReportGenerator()
        self.trade_date = None
        self._push_enabled = False

    def run(self, trade_date: str = None, mode: str = None) -> Dict:
        """运行完整Pipeline

        Args:
            trade_date: 交易日 YYYYMMDD
            mode: 系统模式（V6.2）LIVE/LEARNING/VALIDATION，None=自动判断
        """
        if trade_date is None:
            trade_date = sc.get_effective_date()
        self.trade_date = trade_date
        self._forced_mode = mode

        print(f"\n{'═' * 60}")
        print(f"  Market Regime Engine V3")
        print(f"  机构级市场状态评估系统 v3.1")
        print(f"  交易日期: {trade_date}")
        print(f"{'═' * 60}")

        start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=250)).strftime('%Y%m%d')

        # ── 第1层: Market Regime ──
        # ═══════════════════════════

        print("\n【第1层】Market Regime")
        print("─" * 40)

        # Step 1: 指数强度
        print("\n[1/6] 计算指数强度...")
        index_result = self.index_engine.evaluate(start_date, trade_date)
        print(f"  加权指数强度: {index_result.weighted_score:.1f}分")

        # Step 2: 市场宽度
        print("\n[2/6] 计算市场宽度...")
        breadth_result = self.breadth_engine.evaluate(trade_date)
        if breadth_result is None:
            print("  ⚠️ 数据库无当日数据，自动调用Tushare缓存...")
            try:
                sc.batch_cache_stk_factor_pro(trade_date)
            except Exception as e:
                print(f"  ❌ 缓存失败: {e}")
                return None
            # 重试
            breadth_result = self.breadth_engine.evaluate(trade_date)
            if breadth_result is None:
                print("  ❌ 缓存后仍无数据，跳过")
                return None

        # 数据完整性检查：若当日数据量不足，按个股补全
        if breadth_result is not None:
            import sqlite3 as _sc
            _conn = _sc.connect(sc.DB_PATH)
            _cur = _conn.cursor()
            _cur.execute('SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date=?', (trade_date,))
            _row_count = _cur.fetchone()[0]
            _conn.close()
            if _row_count < 5000:
                _supplemented = sc.supplement_missing_stocks(trade_date, target_count=5000)
                if _supplemented > 0:
                    print(f"  ✅ 补全完成，重新计算宽度...")
                    breadth_result = self.breadth_engine.evaluate(trade_date)

        # Step 3: 情绪
        print("\n[3/6] 计算市场情绪...")
        sentiment_result = self.sentiment_engine.evaluate(trade_date)
        print(f"  情绪评分: {sentiment_result.score:.1f}分")
        print(f"  涨停率{sentiment_result.limit_up_ratio*100:.2f}% "
              f"炸板率{sentiment_result.break_ratio*100:.0f}% "
              f"最高连板{sentiment_result.max_continuous}板 "
              f"北向{sentiment_result.north_flow:+.0f}亿")

        # Step 4: 风格轮动
        print("\n[4/6] 计算风格轮动...")
        style_result = self.style_engine.evaluate(start_date, trade_date)
        print(f"  主导风格: {style_result.dominant_style}")
        if style_result.suggestions:
            print(f"  建议关注: {', '.join(style_result.suggestions[:5])}")

        # Step 5: 风险偏好
        print("\n[5/6] 计算风险偏好...")
        risk_result = self.risk_engine.evaluate(
            breadth_result.score, sentiment_result.score,
            trade_date, start_date
        )
        print(f"  风险偏好: {risk_result.score:.1f}分 ({risk_result.level})")

        # Step 6: 主题共振
        print("\n[6/6] 计算主题共振...")
        theme_result = self.theme_engine.evaluate(trade_date)
        print(f"  主题共振评分: {theme_result.score:.1f}分")
        print(f"  活跃主题数量: {theme_result.theme_count}个")
        top_theme_names = [t['name'] for t in theme_result.top_themes[:5]] if theme_result.top_themes else []
        if top_theme_names:
            print(f"  Top主题: {', '.join(top_theme_names)}")

        # 市场总分
        market_score_result = self.market_score_engine.evaluate(
            index_strength=index_result.weighted_score,
            breadth=breadth_result.score,
            sentiment=sentiment_result.score,
            theme_resonance=theme_result.score,
            risk_appetite=risk_result.score,
        )
        print(f"\n  Market Score: {market_score_result.score:.1f}分")

        # 市场状态
        regime = self.state_machine.classify(
            market_score=market_score_result.score,
            sentiment_score=sentiment_result.score / 100,
            style_dominant=style_result.dominant_style,
            style_scores=style_result.style_scores,
        )
        print(f"  Market Regime: {regime.primary} ({regime.description}) [{regime.confidence:.2f}]")

        # 市场热度
        heat_result = self.heat_engine.evaluate(trade_date)
        print(f"  热度: {heat_result.score:.1f}分 ({heat_result.level}) {heat_result.trend}")

        # ── 第2层: Exposure ──
        # ═════════════════════
        print(f"\n{'─' * 40}")
        print("【第2层】Exposure")

        exposure_result = self.exposure_model.calculate(
            market_score=market_score_result.score,
            risk_appetite_score=risk_result.score,
            regime_name=regime.primary,
            heat_score=heat_result.score,
            heat_level=heat_result.level,
        )
        print(f"  总仓位: {exposure_result.portfolio_exposure_pct:.0f}%")
        print(f"  计算链: Base {exposure_result.base_exposure:.0%} × RA×{exposure_result.risk_appetite_multiplier:.2f} × Heat×{exposure_result.heat_multiplier:.2f}"
              f" → Raw {exposure_result.raw_exposure:.0%}"
              f" | Regime[{regime.primary}] {exposure_result.regime_floor:.0%}~{exposure_result.regime_cap:.0%}")
        print(f"  配置: ETF {exposure_result.etf_allocation*100:.0f}% "
              f"龙头 {exposure_result.leader_allocation*100:.0f}% "
              f"跟风 {exposure_result.follower_allocation*100:.0f}% "
              f"现金 {exposure_result.cash_allocation*100:.0f}%")

        # ── 第3层: Theme Beta ──
        # ═══════════════════════
        print(f"\n{'─' * 40}")
        print("【第3层】Theme Beta")

        theme_beta_result = self.theme_beta_engine.evaluate(trade_date, top_theme_names)
        if theme_beta_result.allocations:
            print(f"  分配方法: {theme_beta_result.method}")
            for tname, alloc in sorted(theme_beta_result.allocations.items(),
                                       key=lambda x: x[1], reverse=True):
                score = theme_beta_result.theme_scores.get(tname, 0)
                beta = theme_beta_result.theme_betas.get(tname, 0)
                if alloc > 0:
                    print(f"    {tname}: {alloc*100:.0f}% (评分{score:.0f} β{beta:.2f})")
        else:
            print("  无有效主题分配")

        # ── 第4层: Leader Quality ──
        # ═══════════════════════════
        print(f"\n{'─' * 40}")
        print("【第4层】Leader Quality")

        # 载入 theme_stock_map（按交易日动态解析）
        import json as _json
        from market_regime_v3.engines import resolve_theme_stock_map_path
        theme_map_path = resolve_theme_stock_map_path(trade_date)
        theme_stock_map = {}
        stock_meta = {}  # {ts_code: {subtheme, dominant_theme}}
        if os.path.exists(theme_map_path):
            with open(theme_map_path, 'r', encoding='utf-8') as f:
                raw = _json.load(f)
            theme_stock_map = raw.get('themes', raw) if isinstance(raw, dict) else {}
            # 加载 stocks 中的子主题和主导叙事字段
            raw_stocks = raw.get('stocks', {}) if isinstance(raw, dict) else {}
            for code, sinfo in raw_stocks.items():
                if isinstance(sinfo, dict):
                    sub = sinfo.get('subtheme', '')
                    dom = sinfo.get('dominant_theme', '')
                    if sub or dom:
                        stock_meta[code] = {
                            'subtheme': sub,
                            'dominant_theme': dom,
                        }
        else:
            print(f"  ⚠️ 主题映射文件不存在: {theme_map_path}")

        leader_result = self.leader_quality_engine.evaluate(trade_date, theme_stock_map, top_theme_names)
        if leader_result.top_leaders:
            print(f"  Top龙头:")
            for ld in leader_result.top_leaders[:5]:
                print(f"    {ld['name']}({ld['ts_code']}) {ld['total_score']:.0f}分")
        else:
            print("  无符合条件龙头")

        # ── V7.0 主线第一次回调升级：区间放量多涨停拉升后回调 + 低开阳线承接 ──
        rp_engine = RallyPullbackEngine(self.config)
        print("\n  [全市场扫描] 区间放量多涨停回调检测...")

        # 构建全市场候选池（龙头前10 + 总市值>80亿）
        sb_cache = sc.load_stock_basic()
        candidate_pool = []
        leader_codes = set()

        for ld in leader_result.top_leaders[:10]:
            code = ld['ts_code']
            candidate_pool.append(ld)
            leader_codes.add(code)

        try:
            pro = sc._get_pro()
            db = pro.daily_basic(trade_date=trade_date,
                                 fields='ts_code,total_mv,circ_mv,close,turnover_rate')
            if db is not None and not db.empty:
                db = db[db['ts_code'].isin(sb_cache[sb_cache['ts_code'].str.endswith(('.SH','.SZ'))]['ts_code'])]
                db = db[~db['ts_code'].str.startswith(('8','4','9'))]
                db = db[~db['ts_code'].isin(leader_codes)]
                db = db[db['total_mv'] > 800_000].sort_values('total_mv', ascending=False)
                for _, row in db.iterrows():
                    code = row['ts_code']
                    name_row = sb_cache[sb_cache['ts_code'] == code]
                    name = name_row['name'].values[0] if not name_row.empty else code
                    candidate_pool.append({
                        'ts_code': code, 'name': name, 'theme': '',
                        'total_score': 0, 'from_market': True
                    })
                print(f"    龙头候选: {len(leader_codes)}只 + 全市场(总市值>80亿): {len(db)}只 = {len(candidate_pool)}只")
        except Exception as e:
            print(f"    全市场补充失败(仅用龙头): {e}")

        # 运行 RallyPullbackEngine
        pullback_qualified = []
        total = len(candidate_pool)
        for idx, cand in enumerate(candidate_pool, 1):
            code = cand['ts_code']
            name = cand.get('name', code)
            theme = cand.get('theme', '')
            rp_result = rp_engine.detect(code, trade_date)
            if idx % 200 == 0 or idx == total:
                print(f"    检测进度: {idx}/{total}")

            if rp_result and rp_result.is_qualified:
                meta = stock_meta.get(code, {})
                pullback_qualified.append({
                    "ts_code": code,
                    "name": name,
                    "theme": theme or meta.get('subtheme', '') or meta.get('dominant_theme', ''),
                    "subtheme": meta.get('subtheme', ''),
                    "dominant_theme": meta.get('dominant_theme', ''),
                    "leader_score": cand.get('total_score', 0),
                    # V7.0 新字段
                    "total_score": rp_result.total_score,
                    "rally_amplitude": rp_result.rally_amplitude,
                    "rally_vol_expansion": rp_result.rally_vol_expansion,
                    "rally_limit_up_count": rp_result.rally_limit_up_count,
                    "rally_max_consecutive_lu": rp_result.rally_max_consecutive_limit_up,
                    "rally_high_date": rp_result.rally_high_date,
                    "drawdown": rp_result.drawdown_from_high,
                    "pullback_days": rp_result.pullback_days,
                    "is_low_open_positive": rp_result.is_low_open_positive,
                    "candle_open_gap": rp_result.candle_open_gap,
                    "candle_body_pct": rp_result.candle_body_pct,
                    "subs": rp_result.subs,
                    # 入场逻辑
                    "ref_price": rp_result.ref_price,
                    "stop_loss": rp_result.stop_loss,
                    "take_profit": rp_result.take_profit,
                    "atr": rp_result.atr,
                })

        if pullback_qualified:
            pullback_qualified.sort(key=lambda x: x['total_score'], reverse=True)
            print(f"\n  ✅ 符合区间放量多涨停回调条件: {len(pullback_qualified)}只")
            for pq in pullback_qualified:
                amps = pq['rally_amplitude'] * 100
                subs = pq.get('subs', {})
                vol_s = subs.get('vol_expansion', 0)
                lu_s = subs.get('limit_up', 0)
                pb_s = subs.get('pullback', 0)
                cdl_s = subs.get('candle', 0)
                profit_pct = (pq['take_profit'] / pq['ref_price'] - 1) * 100
                print(f"    {pq['name']}({pq['ts_code']}) "
                      f"总分{pq['total_score']:.0f} "
                      f"涨停×{pq['rally_limit_up_count']} 拉升+{amps:.0f}% "
                      f"放量{pq['rally_vol_expansion']:.1f}倍 "
                      f"回撤{pq['drawdown']*100:.1f}% "
                      f"低开{pq['candle_open_gap']*100:.1f}%阳线{pq['candle_body_pct']*100:.1f}% "
                      f"放{vol_s:.0f}涨{lu_s:.0f}回{pb_s:.0f}阳{cdl_s:.0f} "
                      f"入场{pq['ref_price']:.2f} 止损{pq['stop_loss']:.2f} +{profit_pct:.0f}%")
        else:
            print("\n  全市场扫描完成，无符合区间放量多涨停回调条件的标的。")

        # ── V6.1 Layer: Pattern → Smart Money → EV → Risk Budget ──
        # ════════════════════════════════════════════════════════════
        pattern_result = None
        ev_result = None
        sm_result = None
        rb_result = None

        if pullback_qualified:
            print(f"\n{'─' * 40}")
            print("【V6.1 Layer】Historical Pattern · EV · Smart Money · Risk Budget")

        # V6.1-a) Historical Pattern Engine
        if self.pattern_engine and pullback_qualified:
            print("\n[V6.1/1] 历史模式匹配...")
            try:
                pattern_result = self.pattern_engine.evaluate(
                    trade_date=trade_date,
                    pullback_candidates=pullback_qualified,
                    market_regime=regime.primary if regime else 'Unknown',
                    market_score=market_score_result.score if market_score_result else 50,
                    risk_appetite=risk_result.score if risk_result else 50,
                    heat_score=heat_result.score if heat_result else 50,
                )
                for code, pm in pattern_result.matches.items():
                    phase_mark = {'cold': '❄️', 'warm': '🔥', 'data_driven': '✅'}.get(pm.cold_start_phase, '')
                    if pm.n_samples >= 5:
                        print(f"    {pm.name:8s}({code}) [{pm.pattern_type[:10]:10s}] "
                              f"样本{pm.n_samples:3d}次 P={pm.win_probability:.0%} "
                              f"10日EV={pm.avg_return_10d:+.2%} DD={pm.avg_max_drawdown:.1%} "
                              f"Conf={pm.confidence:.2f} {phase_mark}")
                    else:
                        print(f"    {pm.name:8s}({code}) [{pm.pattern_type[:10]:10s}] "
                              f"冷启P={pm.win_probability:.0%} "
                              f"样本{pm.n_samples}次<{self.pattern_engine.min_samples} {phase_mark}")
            except Exception as e:
                print(f"  ⚠️ 历史模式匹配异常: {e}")

        # V6.1-b) Smart Money Score V2
        sm_scores = {}
        if self.smart_money_v2 and pullback_qualified:
            print("\n[V6.1/2] Smart Money Score V2...")
            try:
                pb_codes = [pq['ts_code'] for pq in pullback_qualified]
                sm_result = self.smart_money_v2.evaluate(trade_date, codes=pb_codes)
                for code, smr in sm_result.items():
                    sm_scores[code] = smr.composite_score
                    att = smr.attribution
                    print(f"    {smr.ts_code} S={smr.composite_score:.0f} 主力{att.main_force_score:+.0f} "
                          f"超大单{att.super_large_score:+.0f} 换手{att.turnover_health:.0f} 筹码{att.chip_concentration:.0f}")
            except Exception as e:
                print(f"  ⚠️ Smart Money Score异常: {e}")

        # V6.1-c) 写入Pattern DB（含Smart Money Score + 龙头 + 截面）
        cs_result = None  # 提前初始化，供后续Alpha Layer和PatternDB使用
        if self.pattern_engine and pullback_qualified and regime:
            try:
                # 准备龙头列表和截面列表（转为dict以兼容）
                leading_list = leader_result.top_leaders if leader_result and leader_result.top_leaders else None
                cs_list = None  # 截面数据在Alpha Layer获取，此处尚不可用

                n_saved = self.pattern_engine.save_pattern_records(
                    trade_date=trade_date,
                    pullback_candidates=pullback_qualified,
                    market_regime=regime.primary if regime else 'Unknown',
                    market_score=market_score_result.score if market_score_result else 50,
                    risk_appetite=risk_result.score if risk_result else 50,
                    heat_score=heat_result.score if heat_result else 50,
                    smart_money_scores=sm_scores,
                    leading_stocks=leading_list,           # ← V6.1 龙头样本
                    cross_sectional_stocks=cs_list,         # ← V6.1 截面样本
                )
                if n_saved > 0:
                    print(f"\n  [PatternDB] 已保存 {n_saved} 条模式记录 (回撤+龙头+截面)")
            except Exception as e:
                print(f"  ⚠️ PatternDB写入异常: {e}")

        # V6.2: 确定系统模式
        # ─────────────────────────────────────
        if self._forced_mode:
            system_mode = self._forced_mode
        else:
            system_mode = 'LIVE'
            if pattern_result and pattern_result.matches:
                has_learning_candidate = any(
                    pm.n_samples < 30 for pm in pattern_result.matches.values()
                )
                regime_ok = regime and regime.primary in ['Recovery', 'Neutral', 'Bull', 'Euphoria']
                if has_learning_candidate and regime_ok:
                    system_mode = 'LEARNING'
        print(f"\n  [V6.2] System Mode: {system_mode}")

        # V6.1-d) EV Engine
        if self.ev_engine and pattern_result and pattern_result.matches:
            print("\n[V6.1/3] Expected Value 计算...")
            try:
                ev_result = self.ev_engine.evaluate(
                    trade_date=trade_date,
                    pattern_matches=pattern_result.matches,
                )
                for ev_r in ev_result.ranked_list:
                    phase_mark = {'cold': '❄️', 'warm': '🔥', 'data_driven': '✅'}.get(ev_r.cold_start_phase, '')
                    print(f"    #{ev_r.rank:2d} {ev_r.name:8s}({ev_r.ts_code}) "
                          f"P={ev_r.win_probability:.0%} EV={ev_r.expected_value_10d:+.2%} "
                          f"AdjEV={ev_r.adjusted_ev:+.2%} Conf={ev_r.confidence:.2f}({ev_r.confidence_level}) "
                          f"n={ev_r.n_samples} {ev_r.pattern_type[:8]} "
                          f"{phase_mark}{ev_r.cold_start_phase[:4]} → {ev_r.signal.value}")
            except Exception as e:
                print(f"  ⚠️ EV计算异常: {e}")

        # V6.1-e) Risk Budget Position
        if self.risk_budget_engine and ev_result and pullback_qualified:
            print("\n[V6.1/4] Risk Budget 仓位分配...")
            try:
                base_pct = exposure_result.portfolio_exposure_pct if exposure_result else 0
                rb_result = self.risk_budget_engine.allocate(
                    trade_date=trade_date,
                    candidates=pullback_qualified,
                    base_exposure_pct=base_pct,
                    regime_name=regime.primary if regime else 'Unknown',
                    ev_results=ev_result.results,
                    market_score=market_score_result.score if market_score_result else 50,
                    system_mode=system_mode,
                    smart_money_scores=sm_scores,
                )
                for code, pr in rb_result.positions.items():
                    if pr.position_pct > 0:
                        exp = pr.explanation
                        mode_tag = " [学习]" if pr.is_learning else ""
                        print(f"    {pr.name:8s}({code}) 仓位={pr.position_pct:.1f}%{mode_tag} "
                              f"({exp.base_position_pct:.0f}%×{exp.market_multiplier:.1f}×{exp.ev_multiplier:.1f}×{exp.risk_multiplier:.1f})")
                print(f"    总仓位: {rb_result.total_exposure:.1f}% | 标的数: {rb_result.asset_count} | "
                      f"剩余现金: {rb_result.remaining_cash:.1f}% | 模式: {rb_result.system_mode}", end='')
                if rb_result.learning_count > 0:
                    print(f" | 学习仓位: {rb_result.learning_count}只", end='')
                print()
            except Exception as e:
                print(f"  ⚠️ 仓位分配异常: {e}")

        # ── Alpha Layer: 全市场截面排序 + 资金行为 + 概率预测 ──
        # ═══════════════════════════════════════════════════════
        print(f"\n{'─' * 40}")
        print("【Alpha Layer】截面排序 · 资金流 · 概率")

        # 4a) 全市场截面排序
        if self.cross_sectional_engine:
            print("\n[Alpha/1] 全市场截面排序...")
            try:
                cs_result = self.cross_sectional_engine.evaluate(
                    trade_date, theme_stock_map, stock_meta,
                    n_stocks_limit=None
                )
                if cs_result and cs_result.top_n:
                    print(f"  分析 {cs_result.n_stocks_analyzed} 只股票")
                    print(f"  Top10:")
                    for sa in cs_result.top_n[:10]:
                        themes_str = ','.join(sa.themes[:2]) if sa.themes else ''
                        print(f"    #{sa.cross_sectional_rank:4d} {sa.name:8s}({sa.ts_code:12s}) {sa.total_score:5.1f}分 [{themes_str}]")
            except Exception as e:
                print(f"  ⚠️ 截面排序异常: {e}")

        # 4b) 资金流分析
        cf_result = None
        if self.capital_flow_engine:
            print("\n[Alpha/2] 资金行为分析...")
            try:
                cf_result = self.capital_flow_engine.evaluate(trade_date)
                if cf_result:
                    nb = cf_result.north_bound
                    if nb:
                        print(f"  北向资金: 当日{nb.total_inflow_today:+.1f}亿 | 5日{nb.total_inflow_5d:+.1f}亿 | {nb.trend}")
                    print(f"  市场净流入: {cf_result.market_net_inflow:.0f}万")
                    print(f"  资金综合评分: {cf_result.composite_score:.1f}分")
            except Exception as e:
                print(f"  ⚠️ 资金流分析异常: {e}")

        # 4c) 概率预测
        prob_results = []
        if self.probability_model and pullback_qualified:
            print("\n[Alpha/3] 回调成功率预测...")
            try:
                # 加载因子数据用于预测
                pb_codes = [pq['ts_code'] for pq in pullback_qualified]
                df_cache = {}
                for code in pb_codes:
                    df = self._load_stock_data(code, trade_date)
                    if df is not None:
                        df_cache[code] = df

                theme_qualities = {}
                if theme_beta_result and hasattr(theme_beta_result, 'theme_scores'):
                    theme_qualities = theme_beta_result.theme_scores

                leader_scores = {}
                for ld in leader_result.top_leaders[:10] if leader_result.top_leaders else []:
                    leader_scores[ld['ts_code']] = ld.get('total_score', 0)

                candidates_for_prob = [
                    {'ts_code': pq['ts_code'], 'name': pq['name'],
                     'theme': pq.get('theme', ''), 'pb_result': pq}
                    for pq in pullback_qualified
                ]

                prob_results = self.probability_model.predict_batch(
                    candidates=candidates_for_prob,
                    df_cache=df_cache,
                    market_score=market_score_result.score,
                    theme_qualities=theme_qualities,
                    leader_scores=leader_scores,
                    capital_flow_scores={},
                )
                print(f"  预测 {len(prob_results)} 只标的:")
                for pr in sorted(prob_results, key=lambda x: x.probability, reverse=True)[:5]:
                    print(f"    {pr.name:8s}({pr.ts_code}) P={pr.probability:.1%} → {pr.signal}")
            except Exception as e:
                print(f"  ⚠️ 概率预测异常: {e}")

        # ── 第5层: Trading Style ──
        # ═════════════════════════
        print(f"\n{'─' * 40}")
        print("【第5层】Trading Style")

        style_result_v5 = self.trading_style_engine.evaluate(regime.primary, heat_result.level)
        print(f"  交易风格: {style_result_v5.style_label} ({style_result_v5.style_description})")

        # ── 第6层: Risk Control ──
        # ════════════════════════
        print(f"\n{'─' * 40}")
        print("【第6层】Risk Control")

        risk_control_result = self.risk_control_engine.evaluate(
            regime_name=regime.primary,
            heat_level=heat_result.level,
            exposure_pct=exposure_result.portfolio_exposure_pct / 100,
            theme_count=len(top_theme_names),
        )
        print(f"  安全状态: {'✅' if risk_control_result.is_safe else '⚠️ 有风险'}")
        if risk_control_result.warnings:
            for w in risk_control_result.warnings[:3]:
                print(f"    ⚠️ {w.get('detail', w.get('type', ''))}")
        if risk_control_result.actions:
            print(f"  建议操作: {'; '.join(risk_control_result.actions[:3])}")

        # ── 第7层: Portfolio Optimizer ──
        # ═════════════════════════════
        print(f"\n{'─' * 40}")
        print("【第7层】Portfolio Optimizer")

        portfolio_result = None
        if self.portfolio_optimizer and pullback_qualified:
            try:
                # 构建候选标的（含概率预测结果）
                prob_map = {pr.ts_code: pr.probability for pr in prob_results} if prob_results else {}
                port_candidates = []
                for pq in pullback_qualified:
                    code = pq['ts_code']
                    prob = prob_map.get(code, 0.5)
                    vol = (pq.get('atr', 0) / max(pq.get('ref_price', 1), 0.1)) * np.sqrt(252)
                    vol = max(0.15, min(0.60, vol))
                    exp_ret = (pq.get('take_profit', 0) / max(pq.get('ref_price', 1), 0.1) - 1) * 0.5
                    port_candidates.append({
                        'ts_code': code,
                        'name': pq.get('name', ''),
                        'probability': prob,
                        'volatility': vol,
                        'expected_return': max(exp_ret, 0.02),
                    })

                if port_candidates:
                    total_exp = exposure_result.leader_allocation if exposure_result else 0.5
                    portfolio_result = self.portfolio_optimizer.optimize(
                        candidates=port_candidates,
                        total_exposure=total_exp,
                        probabilities=prob_map,
                    )
                    print(f"  方法: {portfolio_result.method.value}")
                    print(f"  配置 {portfolio_result.n_assets} 标的 | 预期收益: {portfolio_result.expected_return:.1%}"
                          f" 波动: {portfolio_result.expected_volatility:.1%} 夏普: {portfolio_result.sharpe_ratio:.2f}")
                    print(f"  HHI集中度: {portfolio_result.concentration:.3f} | 再平衡: {portfolio_result.rebalance_signal}")
                    print(f"  Top配置:")
                    for a in portfolio_result.allocations[:5]:
                        if a.weight > 0.01:
                            print(f"    {a.name:8s}({a.ts_code:12s}) {a.weight:.1%} "
                                  f"[P={a.probability:.0%} Kelly={a.kelly_fraction:.1%}]")
            except Exception as e:
                print(f"  ⚠️ 组合优化异常: {e}")
        else:
            print("  无符合条件的候选标的或优化器未启用")

        # ── 生成最终报告 ──
        print(f"\n{'═' * 60}")
        print("  生成最终报告...")

        # 先填充V6.1数据（让report_dict就绪）
        v61_data = {
            "v61_pattern": pattern_result,
            "v61_ev": ev_result,
            "v61_smart_money": sm_result,
            "v61_risk_budget": rb_result,
            "pullback_qualified": pullback_qualified,
        }

        report_dict = self.reporter.generate_report_v2(
            trade_date=trade_date,
            market_score_result=market_score_result,
            market_regime=regime,
            heat_result=heat_result,
            style_result=style_result,
            risk_result=risk_result,
            breadth_result=breadth_result,
            sentiment_result=sentiment_result,
            theme_resonance_result=theme_result,
            index_strength_result=index_result,
            exposure_result=exposure_result,
            theme_beta_result=theme_beta_result,
            leader_result=leader_result,
            trading_style_result=style_result_v5,
            risk_control_result=risk_control_result,
            v61_data=v61_data,
        )

        # 补充回调检测结果 + Alpha引擎结果 + overview 字段
        report_dict["pullback_qualified"] = pullback_qualified
        if cs_result:
            report_dict["cross_sectional"] = {
                "n_stocks": cs_result.n_stocks_analyzed,
                "top_stocks": [
                    {"ts_code": sa.ts_code, "name": sa.name,
                     "score": sa.total_score, "rank": sa.cross_sectional_rank}
                    for sa in cs_result.top_n[:20]
                ]
            }
        if cf_result:
            report_dict["capital_flow"] = {
                "composite_score": cf_result.composite_score,
                "north_bound_today": cf_result.north_bound.total_inflow_today if cf_result.north_bound else None,
                "north_bound_5d": cf_result.north_bound.total_inflow_5d if cf_result.north_bound else None,
                "north_trend": cf_result.north_bound.trend if cf_result.north_bound else None,
            }
        if prob_results:
            report_dict["probabilities"] = [
                {"ts_code": pr.ts_code, "name": pr.name,
                 "probability": pr.probability, "signal": pr.signal}
                for pr in sorted(prob_results, key=lambda x: x.probability, reverse=True)
            ]
        if portfolio_result:
            report_dict["portfolio"] = {
                "method": portfolio_result.method.value,
                "expected_return": portfolio_result.expected_return,
                "expected_vol": portfolio_result.expected_volatility,
                "sharpe": portfolio_result.sharpe_ratio,
                "hhi": portfolio_result.concentration,
                "allocations": [
                    {"ts_code": a.ts_code, "name": a.name,
                     "weight": a.weight, "kelly": a.kelly_fraction,
                     "prob": a.probability}
                    for a in portfolio_result.allocations if a.weight > 0.01
                ]
            }
        report_dict["overview"]["index_score"] = round(index_result.weighted_score)
        report_dict["overview"]["breadth_score"] = round(breadth_result.score)
        report_dict["overview"]["sentiment_score"] = round(sentiment_result.score)

        report_path = self.reporter.save_report(report_dict['markdown'], trade_date)
        print(f"\n  ✅ 报告已保存: {report_path}")

        # 微信推送
        if self._push_enabled:
            print("\n  推送微信...")
            summary = build_summary(report_dict)
            send_pushplus(summary, title=f"主线第一次回调模式 {trade_date}")

        print()

        self.reporter.print_report(report_dict['markdown'])

        return report_dict

    def _load_stock_data(self, ts_code: str, trade_date: str):
        """加载个股数据用于入场逻辑计算"""
        try:
            import stock_cache as sc
            start = (pd.to_datetime(trade_date) - pd.Timedelta(days=120)).strftime('%Y%m%d')
            return sc.cached_stk_factor_pro(ts_code, start, trade_date, silent=True)
        except Exception:
            return None


def main():
    parser = argparse.ArgumentParser(description='Market Regime Engine V3')
    parser.add_argument('--date', type=str, default=None, help='交易日期 YYYYMMDD')
    parser.add_argument('--config', type=str, default=None, help='配置文件路径')
    parser.add_argument('--push', action='store_true', help='推送微信')
    parser.add_argument('--mode', type=str, default='LIVE', choices=['LIVE', 'LEARNING', 'VALIDATION'],
                        help='系统模式（V6.2）')
    args = parser.parse_args()

    engine = MarketRegimeV3(config_path=args.config)
    if args.push:
        engine._push_enabled = True
    engine.run(trade_date=args.date, mode=args.mode)


if __name__ == '__main__':
    main()
