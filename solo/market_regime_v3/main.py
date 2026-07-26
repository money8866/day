"""Market Regime Engine V3 - 主编排器

6层 Pipeline:
1. Market Regime (指数强度 → 宽度 → 情绪 → 风格 → 风险偏好 → 主题共振 → 总评分 → 状态机 → 热度)
2. Exposure (仓位模型)
3. Theme Beta (主题资金分配)
4. Leader Quality (龙头质量评分)
5. Trading Style (交易风格)
6. Risk Control (风控执行)
"""

import os
import sys
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
from market_regime_v3.engines.theme_beta import ThemeBetaEngine
from market_regime_v3.engines.leader_quality import LeaderQualityEngine
from market_regime_v3.engines.trading_style import TradingStyleEngine
from market_regime_v3.engines.risk_control import RiskControlEngine
from market_regime_v3.reporter import MarketReportGenerator
from market_regime_v3.explainer import MarketExplainer
from market_regime_v3.wechat_push import send_pushplus, build_summary

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

        # 第5层: Trading Style
        self.trading_style_engine = TradingStyleEngine(self.config)

        # 第6层: Risk Control
        self.risk_control_engine = RiskControlEngine(self.config)

        self.reporter = MarketReportGenerator()
        self.trade_date = None
        self._push_enabled = False

    def run(self, trade_date: str = None) -> Dict:
        if trade_date is None:
            trade_date = sc.get_effective_date()
        self.trade_date = trade_date

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
        print(f"  宽度评分: {breadth_result.score:.1f}分")
        print(f"  上涨{breadth_result.up_ratio*100:.0f}% 涨停{breadth_result.limit_up_count}家 "
              f"跌停{breadth_result.limit_down_count}家 中位数{breadth_result.median_return:.2f}%")

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
        )
        print(f"  总仓位: {exposure_result.portfolio_exposure_pct:.0f}%")
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
        if os.path.exists(theme_map_path):
            with open(theme_map_path, 'r', encoding='utf-8') as f:
                raw = _json.load(f)
            theme_stock_map = raw.get('themes', raw) if isinstance(raw, dict) else {}
        else:
            print(f"  ⚠️ 主题映射文件不存在: {theme_map_path}")

        leader_result = self.leader_quality_engine.evaluate(trade_date, theme_stock_map, top_theme_names)
        if leader_result.top_leaders:
            print(f"  Top龙头:")
            for ld in leader_result.top_leaders[:5]:
                print(f"    {ld['name']}({ld['ts_code']}) {ld['total_score']:.0f}分")
        else:
            print("  无符合条件龙头")

        # ── 回调检测 + 入场逻辑 ──
        from inst_pullback_v2.engines.pullback_detector import PullbackDetector
        pb_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      'inst_pullback_v2', 'config.yaml')
        with open(pb_config_path, 'r', encoding='utf-8') as f:
            pb_config = yaml.safe_load(f)
        pd_engine = PullbackDetector(pb_config)

        pullback_qualified = []
        for ld in leader_result.top_leaders[:5]:
            code = ld['ts_code']
            name = ld['name']
            theme = ld.get('theme', '')
            pb_result = pd_engine.detect(code, trade_date)
            if pb_result and pb_result.is_qualified:
                # 计算入场逻辑
                df = self._load_stock_data(code, trade_date)
                if df is not None and not df.empty:
                    close_hfq = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values
                    ma5 = df['ma_bfq_5'].values if 'ma_bfq_5' in df.columns else None
                    ma10 = df['ma_bfq_10'].values if 'ma_bfq_10' in df.columns else None

                    latest_close = close_hfq[-1]
                    ref_price = min(ma5[-1], latest_close * 0.985) if ma5 is not None else latest_close * 0.985
                    stop_loss = ma10[-1] if ma10 is not None else ref_price * 0.93

                    atr_val = 0.0
                    if 'atr_bfq' in df.columns:
                        atr_val = float(df['atr_bfq'].iloc[-1]) if pd.notna(df['atr_bfq'].iloc[-1]) else 0.0
                    take_profit = ref_price + 3.0 * atr_val

                    pullback_qualified.append({
                        "ts_code": code,
                        "name": name,
                        "theme": theme,
                        "leader_score": ld['total_score'],
                        "ret_60d": pb_result.ret_60d,
                        "drawdown": pb_result.drawdown_from_high,
                        "quality_score": pb_result.quality_score,
                        "pullback_ma": pb_result.pullback_ma,
                        "is_first_pullback": pb_result.is_first_pullback,
                        "ref_price": round(ref_price, 2),
                        "stop_loss": round(stop_loss, 2),
                        "take_profit": round(take_profit, 2),
                        "atr": round(atr_val, 2),
                    })

        if pullback_qualified:
            print(f"\n  ✅ 符合回踩条件: {len(pullback_qualified)}只")
            for pq in pullback_qualified:
                profit_pct = (pq['take_profit'] / pq['ref_price'] - 1) * 100
                lines = f"    {pq['name']}({pq['ts_code']}) ←{pq['pullback_ma']} 入场{pq['ref_price']:.2f} 止损{pq['stop_loss']:.2f}({(pq['stop_loss']/pq['ref_price']-1)*100:.1f}%) 止盈+{profit_pct:.0f}%"
                print(lines)

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

        # ── 生成完整报告 ──
        print(f"\n{'═' * 60}")
        print("  生成最终报告...")

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
        )

        # 补充回调检测结果 + overview 字段
        report_dict["pullback_qualified"] = pullback_qualified
        report_dict["overview"]["index_score"] = round(index_result.weighted_score)
        report_dict["overview"]["breadth_score"] = round(breadth_result.score)
        report_dict["overview"]["sentiment_score"] = round(sentiment_result.score)

        report_path = self.reporter.save_report(report_dict['markdown'], trade_date)
        print(f"\n  ✅ 报告已保存: {report_path}")

        # 微信推送
        if self._push_enabled:
            print("\n  推送微信...")
            summary = build_summary(report_dict)
            send_pushplus(summary, title=f"市场状态报告 {trade_date}")

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
    args = parser.parse_args()

    engine = MarketRegimeV3(config_path=args.config)
    if args.push:
        engine._push_enabled = True
    engine.run(trade_date=args.date)


if __name__ == '__main__':
    main()
