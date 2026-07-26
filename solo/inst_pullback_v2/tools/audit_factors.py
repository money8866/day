"""
逐因子审计脚本——追踪每个因子从原始数据到最终分数的完整链路
用法: cd d:\mystock\solo\inst_pullback_v2 && python tools/audit_factors.py --date 20260724
"""
import os, sys, json, sqlite3, argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import stock_cache as sc
from data.indicators import ema, sma, slope, rsi, macd, adx, atr, new_high_count, price_position, volume_ratio
from data.loader import DataLoader, load_config

OK = "✅"
WARN = "⚠️"
ERR = "❌"
INFO = "🔍"

class FactorAuditor:
    def __init__(self, trade_date="20260724"):
        self.td = trade_date
        self.loader = DataLoader(trade_date)
        self.config = load_config()
        self.issues = []
        self.oks = []
        self.db_path = r"D:\mystock\cache_daily\stock_data.db"

    def log(self, level, engine, factor, msg):
        entry = f"  {level} [{engine}] {factor}: {msg}"
        if level == ERR:
            self.issues.append(entry)
        else:
            self.oks.append(entry)
        print(entry)

    def check_not_nan(self, engine, factor, value):
        if value is None:
            self.log(ERR, engine, factor, "值为 None")
            return False
        try:
            if isinstance(value, (float, np.floating)) and np.isnan(value):
                self.log(ERR, engine, factor, "值为 NaN")
                return False
        except:
            pass
        return True

    def check_range(self, engine, factor, value, lo, hi, warn_only=False):
        if not self.check_not_nan(engine, factor, value):
            return False
        try:
            v = float(value)
            if v < lo or v > hi:
                lvl = WARN if warn_only else ERR
                self.log(lvl, engine, factor, f"值 {v:.4f} 超出范围 [{lo}, {hi}]")
                return warn_only
        except:
            self.log(ERR, engine, factor, f"无法转为数值: {type(value)}")
            return False
        return True

    def check_positive(self, engine, factor, value, warn_only=False):
        return self.check_range(engine, factor, value, 0, 1e12, warn_only)

    def run(self):
        print("=" * 70)
        print(f"  Institution First Pullback Alpha V2 — 逐因子审计")
        print(f"  交易日期: {self.td}")
        print("=" * 70)

        self.audit_data_availability()
        self.audit_market_state()
        self.audit_theme_engine()
        self.audit_leader_engine()
        self.audit_pullback_detector()
        self.audit_chip_analyzer()
        self.audit_etf_resonance()
        self.audit_fund_flow()
        self.audit_trend_health()
        self.audit_lifecycle()
        self.audit_risk_filter()
        self.audit_alpha_scorer()

        print("\n" + "=" * 70)
        print(f"  审计完成: {len(self.oks)} OK, {len(self.issues)} 问题")
        print("=" * 70)
        if self.issues:
            print("\n⚠️ 发现问题:")
            for i in self.issues:
                print(i)

    # ──────────────────────────────────────────────
    # 0. 数据可用性
    # ──────────────────────────────────────────────
    def audit_data_availability(self):
        print("\n── 0. 数据可用性 ──")

        sb = self.loader.load_stock_basic()
        self.log(OK if sb is not None else ERR, "DATA", "stock_basic", f"加载: {len(sb) if sb is not None else 0} 条")

        with sqlite3.connect(self.db_path) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date = ?",
                               (self.td,)).fetchone()[0]
            self.log(OK if cnt > 1000 else WARN, "DATA", "stk_factor_pro",
                     f"{self.td} 日数据: {cnt} 行")
            total = conn.execute("SELECT COUNT(DISTINCT ts_code) FROM stk_factor_pro").fetchone()[0]
            self.log(INFO, "DATA", "stk_factor_pro", f"累计股票数: {total}")

            dates = conn.execute("SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC LIMIT 5").fetchall()
            self.log(INFO, "DATA", "recent_dates", f"最近5天: {[d[0] for d in dates]}")

        mf = self.loader.load_moneyflow(self.td)
        self.log(OK if mf is not None else WARN, "DATA", "moneyflow",
                 f"{self.td}: {'有数据' if mf is not None else '无数据'} ({len(mf) if mf is not None else 0}行)")

        theme_map = self.loader.load_theme_stock_map()
        n_themes = len(theme_map) if theme_map else 0
        self.log(OK if n_themes > 0 else ERR, "DATA", "theme_stock_map", f"主题数: {n_themes}")

    # ──────────────────────────────────────────────
    # 1. Market State Engine
    # ──────────────────────────────────────────────
    def audit_market_state(self):
        print("\n── 1. Market State Engine ──")
        from engines.market_state import MarketStateEngine
        engine = MarketStateEngine(self.config)
        engine.loader = self.loader
        ms = engine.evaluate(self.td)

        self.log(OK, "MarketState", "state", ms.state)
        self.check_range("MarketState", "score", ms.score, 0, 100)
        self.check_range("MarketState", "trend_score", ms.trend_score, 0, 1)
        self.check_range("MarketState", "money_score", ms.money_score, 0, 1)
        self.check_range("MarketState", "breadth_score", ms.breadth_score, 0, 1)
        self.check_range("MarketState", "new_high_score", ms.new_high_score, 0, 1)
        self.check_range("MarketState", "sentiment_score", ms.sentiment_score, 0, 1)

        sd = (pd.to_datetime(self.td) - timedelta(days=250)).strftime('%Y%m%d')
        for idx in ["000300.SH", "000852.SH", "399006.SZ", "000688.SH"]:
            df = self.loader.load_index_data(idx, sd, self.td, silent=True)
            if df is not None and not df.empty:
                self.log(OK, "MarketState", f"index_{idx}", f"数据: {len(df)}行 close={df['close'].iloc[-1]:.2f}")
            else:
                self.log(ERR, "MarketState", f"index_{idx}", "无数据")

        bd = (pd.to_datetime(self.td) - timedelta(days=60)).strftime('%Y%m%d')
        self.log(INFO, "MarketState", "breadth_db_query", f"日期范围: {bd}~{self.td}")

    # ──────────────────────────────────────────────
    # 2. Theme Engine
    # ──────────────────────────────────────────────
    def audit_theme_engine(self):
        print("\n── 2. Institution Theme Engine ──")
        from engines.theme_engine import InstitutionThemeEngine
        engine = InstitutionThemeEngine(self.config)
        engine.loader = self.loader
        themes = engine.evaluate(self.td)

        if not themes:
            self.log(ERR, "ThemeEngine", "evaluate", "无主题结果")
            return

        self.log(OK, "ThemeEngine", "count", f"输出 {len(themes)} 个主题")

        for t in themes:
            self.check_range("ThemeEngine", f"{t.name}.trend", t.trend_score, 0, 1)
            self.check_range("ThemeEngine", f"{t.name}.money", t.money_score, 0, 1)
            self.check_range("ThemeEngine", f"{t.name}.duration", t.duration_score, 0, 1)
            self.check_range("ThemeEngine", f"{t.name}.leader_strength", t.leader_strength_score, 0, 1)
            self.check_range("ThemeEngine", f"{t.name}.composite", t.composite_score, 0, 1)

            self.log(INFO, "ThemeEngine", t.name,
                     f"trend={t.trend_score:.3f} money={t.money_score:.3f} dur={t.duration_score:.3f} "
                     f"leader={t.leader_strength_score:.3f} composite={t.composite_score:.3f} stocks={t.stock_count}")

        # 检查 bulk 预加载
        self.log(INFO, "ThemeEngine", "bulk_cache", f"预加载了 {len(engine._bulk_cache)} 只股票")
        if engine._bulk_cache:
            sample_code = list(engine._bulk_cache.keys())[0]
            sample_df = engine._bulk_cache[sample_code]
            self.log(INFO, "ThemeEngine", "bulk_sample",
                     f"{sample_code}: {len(sample_df)}行, columns={list(sample_df.columns)[:8]}")

    # ──────────────────────────────────────────────
    # 3. Leader Engine V3
    # ──────────────────────────────────────────────
    def audit_leader_engine(self):
        print("\n── 3. Leader Engine V3 ──")
        from engines.leader_engine import LeaderEngineV3, LeaderHistoryDB

        loader = self.loader
        theme_map = loader.load_theme_stock_map()
        if not theme_map:
            self.log(ERR, "LeaderEngine", "theme_map", "无法加载主题映射")
            return

        test_theme = "创新药" if "创新药" in theme_map else list(theme_map.keys())[0]
        raw_stocks = theme_map[test_theme]
        codes = []
        for s in raw_stocks:
            if isinstance(s, dict) and 'code' in s:
                codes.append(s['code'])
            elif isinstance(s, str):
                codes.append(s)

        self.log(INFO, "LeaderEngine", "test_theme", f"{test_theme} 成分股: {len(codes)}")

        engine = LeaderEngineV3(self.config)
        engine.loader = self.loader
        results = engine.evaluate(codes, theme_name=test_theme, etf_code="", trade_date=self.td)

        if not results:
            self.log(ERR, "LeaderEngine", "results", "无龙头结果")
            return

        self.log(OK, "LeaderEngine", "count", f"输出 {len(results)} 个龙头")

        for r in results:
            self.log(INFO, "LeaderEngine", r.name,
                     f"总分={r.leader_score:.1f} 截面={r.cross_section_score:.3f} 持续={r.persistence_score:.3f} "
                     f"任期={r.tenure_days}天 Top3_20d={r.top3_ratio_20d:.2f} Top3_60d={r.top3_ratio_60d:.2f} "
                     f"稳定={r.rank_stability:.2f} 动量={r.rank_momentum:.2f} 历史={r.history_weighted_score:.3f} "
                     f"已确立={r.is_established}")

            # 截面因子
            self.check_range("LeaderEngine", f"{r.name}.ret_60d", r.ret_60d, -1, 10)
            self.check_range("LeaderEngine", f"{r.name}.ret_20d", r.ret_20d, -1, 5)
            self.check_range("LeaderEngine", f"{r.name}.amount", r.amount_score, 0, 1)
            self.check_range("LeaderEngine", f"{r.name}.new_high", r.new_high_score, 0, 1)
            self.check_range("LeaderEngine", f"{r.name}.etf_corr", r.etf_corr_score, 0, 1)

            # 持续性因子
            self.check_range("LeaderEngine", f"{r.name}.cross_section", r.cross_section_score, 0, 1)
            self.check_range("LeaderEngine", f"{r.name}.persistence", r.persistence_score, 0, 1)
            self.check_positive("LeaderEngine", f"{r.name}.tenure", r.tenure_days)
            self.check_range("LeaderEngine", f"{r.name}.top3_20d", r.top3_ratio_20d, 0, 1)
            self.check_range("LeaderEngine", f"{r.name}.top3_60d", r.top3_ratio_60d, 0, 1)
            self.check_range("LeaderEngine", f"{r.name}.rank_stability", r.rank_stability, 0, 1)
            self.check_range("LeaderEngine", f"{r.name}.rank_momentum", r.rank_momentum, -1, 1)
            self.check_range("LeaderEngine", f"{r.name}.history_weighted", r.history_weighted_score, 0, 1)

        # 检查 bulk 预加载
        self.log(INFO, "LeaderEngine", "bulk_cache", f"预加载了 {len(engine._bulk_cache)} 只股票")
        if engine._bulk_cache:
            sample_code = list(engine._bulk_cache.keys())[0]
            sample_df = engine._bulk_cache[sample_code]
            self.log(INFO, "LeaderEngine", "bulk_sample",
                     f"{sample_code}: {len(sample_df)}行")

        # 检查历史 DB
        db = LeaderHistoryDB()
        hist = db.get_theme_history(test_theme, self.td, 120)
        self.log(OK if len(hist) > 0 else WARN, "LeaderEngine", "history_db",
                 f"{test_theme} 历史记录: {len(hist)}行")
        if len(hist) > 0:
            self.log(INFO, "LeaderEngine", "history_sample",
                     f"最近日期: {hist['trade_date'].max()} | 股票数: {hist['ts_code'].nunique()}")

    # ──────────────────────────────────────────────
    # 4. Pullback Detector
    # ──────────────────────────────────────────────
    def audit_pullback_detector(self):
        print("\n── 4. First Pullback Detector ──")
        from engines.pullback_detector import PullbackDetector

        test_codes = ["600519.SH", "002317.SZ", "300750.SZ", "000858.SZ", "688266.SH"]
        engine = PullbackDetector(self.config)
        engine.loader = self.loader

        found = 0
        for code in test_codes:
            name = self.loader.get_stock_name(code)
            result = engine.detect(code, self.td)
            if result is None:
                self.log(INFO, "Pullback", name, f"{code}: 数据不足")
                continue

            # 检查各步骤
            self.log(INFO, "Pullback", name,
                     f"MA60↑={result.ma_60_up} MA120↑={result.ma_120_up} "
                     f"ret_60d={result.ret_60d:.2%} drawdown={result.drawdown_from_high:.2%} "
                     f"ma_type={result.pullback_ma} first={result.is_first_pullback} "
                     f"no_panic={result.no_volume_panic} qualified={result.is_qualified}")

            self.check_not_nan("Pullback", f"{name}.ret_60d", result.ret_60d)
            self.check_not_nan("Pullback", f"{name}.drawdown", result.drawdown_from_high)
            self.check_not_nan("Pullback", f"{name}.quality", result.quality_score)

            if result.is_qualified:
                found += 1
                self.check_range("Pullback", f"{name}.quality", result.quality_score, 0, 1)

        self.log(OK, "Pullback", "summary", f"测试{len(test_codes)}只, 符合条件{found}只")

    # ──────────────────────────────────────────────
    # 5. Chip Analyzer
    # ──────────────────────────────────────────────
    def audit_chip_analyzer(self):
        print("\n── 5. Chip Analyzer ──")
        from engines.chip_analyzer import ChipAnalyzer

        test_codes = ["002317.SZ", "688266.SH", "600519.SH"]
        engine = ChipAnalyzer(self.config)
        engine.loader = self.loader

        for code in test_codes:
            name = self.loader.get_stock_name(code)
            result = engine.analyze(code, self.td)
            if result is None:
                self.log(INFO, "Chip", name, f"{code}: 数据不足")
                continue

            self.log(INFO, "Chip", name,
                     f"稳定={result.is_stable} 分值={result.stability_score:.2f} "
                     f"质心偏移={result.centroid_shift:.4f} 获利盘={result.profit_ratio:.2%} "
                     f"集中度={result.concentration:.3f} 均值={result.avg_cost:.2f} 峰值={result.chip_peak:.2f}")

            self.check_range("Chip", f"{name}.stability", result.stability_score, 0, 1)
            self.check_range("Chip", f"{name}.centroid", result.centroid_shift, -1, 1)
            self.check_range("Chip", f"{name}.profit_ratio", result.profit_ratio, 0, 1)
            self.check_range("Chip", f"{name}.concentration", result.concentration, 0, 1)

    # ──────────────────────────────────────────────
    # 6. ETF Resonance
    # ──────────────────────────────────────────────
    def audit_etf_resonance(self):
        print("\n── 6. ETF Resonance ──")
        from engines.chip_analyzer import ETFResonance

        engine = ETFResonance(self.config)
        engine.loader = self.loader
        test_pairs = [("002317.SZ", "159828"), ("300750.SZ", "561910")]

        for code, etf in test_pairs:
            name = self.loader.get_stock_name(code)
            result = engine.evaluate(code, etf, self.td)

            if result is None:
                self.log(INFO, "ETFResonance", name, f"{code}: 无结果")
                continue

            self.log(INFO, "ETFResonance", name,
                     f"共振={result.get('is_resonant')} 得分={result.get('score')} "
                     f"MA20↑={result.get('etf_ma20_up')} MA60↑={result.get('etf_ma60_up')} "
                     f"ret_20d={result.get('etf_ret_20d')} 新高={result.get('new_high_recent')}")

            self.check_range("ETFResonance", f"{name}.score", result.get('score', 0), 0, 100)

    # ──────────────────────────────────────────────
    # 7. Fund Flow
    # ──────────────────────────────────────────────
    def audit_fund_flow(self):
        print("\n── 7. Fund Flow ──")
        from engines.chip_analyzer import FundFlow

        engine = FundFlow(self.config)
        engine.loader = self.loader
        test_codes = ["002317.SZ", "688266.SH", "300750.SZ"]

        for code in test_codes:
            name = self.loader.get_stock_name(code)
            result = engine.evaluate(code, self.td)

            if result is None:
                self.log(INFO, "FundFlow", name, f"{code}: 无结果")
                continue

            flows = result.get('net_flows', [])
            self.log(INFO, "FundFlow", name,
                     f"恢复={result.get('is_recovering')} 得分={result.get('score')} "
                     f"净流入={flows}")

            self.check_range("FundFlow", f"{name}.score", result.get('score', 0), 0, 1)

    # ──────────────────────────────────────────────
    # 8. Trend Health
    # ──────────────────────────────────────────────
    def audit_trend_health(self):
        print("\n── 8. Trend Health ──")
        from engines.chip_analyzer import TrendHealth

        engine = TrendHealth(self.config)
        engine.loader = self.loader
        test_codes = ["002317.SZ", "688266.SH", "600519.SH"]

        for code in test_codes:
            name = self.loader.get_stock_name(code)
            result = engine.evaluate(code, self.td)

            if result is None:
                self.log(INFO, "TrendHealth", name, f"{code}: 无结果")
                continue

            self.log(INFO, "TrendHealth", name,
                     f"健康={result.get('is_healthy')} 得分={result.get('score')} "
                     f"EMA对齐={result.get('ema_aligned')} "
                     f"EMA20={result.get('ema20')} EMA60={result.get('ema60')} EMA120={result.get('ema120')} "
                     f"ADX={result.get('adx')} MACD={result.get('macd_score')}")

            self.check_range("TrendHealth", f"{name}.score", result.get('score', 0), 0, 1)

    # ──────────────────────────────────────────────
    # 9. Lifecycle
    # ──────────────────────────────────────────────
    def audit_lifecycle(self):
        print("\n── 9. Theme Lifecycle ──")
        from engines.chip_analyzer import ThemeLifecycleFilter

        engine = ThemeLifecycleFilter(self.config)
        engine.loader = self.loader
        theme_map = self.loader.load_theme_stock_map()
        test_themes = ["创新药", "AI芯片", "军工"]

        for theme in test_themes:
            theme_stocks = []
            if theme_map and theme in theme_map:
                raw_stocks = theme_map[theme]
                for s in raw_stocks:
                    if isinstance(s, dict) and 'code' in s:
                        theme_stocks.append(s['code'])
                    elif isinstance(s, str):
                        theme_stocks.append(s)
            result = engine.evaluate(theme, theme_stocks, self.td)
            self.log(INFO, "Lifecycle", theme,
                     f"阶段={result.get('stage')} 允许={result.get('is_allowed')} 动量={result.get('momentum')}")

    # ──────────────────────────────────────────────
    # 10. Risk Filter
    # ──────────────────────────────────────────────
    def audit_risk_filter(self):
        print("\n── 10. Risk Filter ──")
        from engines.chip_analyzer import RiskFilter

        engine = RiskFilter(self.config)
        engine.loader = self.loader
        test_codes = ["002317.SZ", "000001.SZ", "300750.SZ", "600519.SH"]

        for code in test_codes:
            name = self.loader.get_stock_name(code)
            result = engine.evaluate(code, self.td)
            self.log(INFO, "RiskFilter", name,
                     f"干净={result.get('is_clean')} 得分={result.get('score')} 问题={result.get('issues')}")

    # ──────────────────────────────────────────────
    # 11. Alpha Scorer
    # ──────────────────────────────────────────────
    def audit_alpha_scorer(self):
        print("\n── 11. Alpha Scorer ──")
        from engines.alpha_scorer import AlphaScorer

        engine = AlphaScorer(self.config)
        components = {
            'ts_code': 'TEST.SZ',
            'name': '测试股票',
            'theme': '创新药',
            'market_state': 0.51,
            'theme_strength': 0.337,
            'leader_score': 0.672,
            'pullback_quality': 0.85,
            'etf_resonance': 0.75,
            'chip_stability': 0.70,
            'fund_flow_recovery': 0.60,
            'trend_health': 0.55,
            'buy_type': 'MA20回踩',
            'etf_code': '159828',
            'suggestion': '分批买入',
        }

        result = engine.score(components)
        self.log(INFO, "AlphaScorer", result.name,
                 f"Alpha={result.alpha} 评级={result.rating} "
                 f"MS={result.market_state_score:.3f} TH={result.theme_strength:.3f} "
                 f"LD={result.leader_score:.3f} PB={result.pullback_quality:.3f} "
                 f"ETF={result.etf_resonance_score:.3f} CH={result.chip_stability:.3f} "
                 f"FL={result.fund_flow_recovery:.3f} TR={result.trend_health_score:.3f}")

        self.check_range("AlphaScorer", "alpha", result.alpha, 0, 100)
        self.check_range("AlphaScorer", "market_state", result.market_state_score, 0, 1)
        self.check_range("AlphaScorer", "theme_strength", result.theme_strength, 0, 1)
        self.check_range("AlphaScorer", "leader_score", result.leader_score, 0, 1)
        self.check_range("AlphaScorer", "pullback_quality", result.pullback_quality, 0, 1)
        self.check_range("AlphaScorer", "etf_resonance", result.etf_resonance_score, 0, 1)
        self.check_range("AlphaScorer", "chip_stability", result.chip_stability, 0, 1)
        self.check_range("AlphaScorer", "fund_flow", result.fund_flow_recovery, 0, 1)
        self.check_range("AlphaScorer", "trend_health", result.trend_health_score, 0, 1)

        self.log(INFO, "AlphaScorer", "weight_check",
                 f"公式验证: {20*0.51 + 15*0.337 + 15*0.672 + 20*0.85 + 10*0.75 + 10*0.70 + 5*0.60 + 5*0.55:.1f} "
                 f"≈ {result.alpha}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='20260724')
    args = parser.parse_args()
    auditor = FactorAuditor(args.date)
    auditor.run()


if __name__ == '__main__':
    main()