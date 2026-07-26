import os
import sys
import argparse
import datetime
import pandas as pd
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import DataLoader, load_config
from data.indicators import slope
from engines.market_state import MarketStateEngine, MarketState
from engines.theme_engine import InstitutionThemeEngine, ThemeResult
from engines.leader_engine import LeaderEngineV3, LeaderResult
from engines.pullback_detector import PullbackDetector, PullbackResult
from engines.chip_analyzer import ChipAnalyzer, ChipResult, ETFResonance, FundFlow, TrendHealth, ThemeLifecycleFilter, RiskFilter
from engines.alpha_scorer import AlphaScorer, AlphaResult
from engines.verification import VerificationEngine, VerificationRecord
from output.reporter import DailyReporter


class InstitutionPullbackAlphaV2:
    def __init__(self, config_path=None):
        self.config = load_config()
        self.loader = DataLoader()

        self.market_engine = MarketStateEngine(self.config)
        self.theme_engine = InstitutionThemeEngine(self.config)
        self.leader_engine = LeaderEngineV3(self.config)
        self.pullback_detector = PullbackDetector(self.config)
        self.chip_analyzer = ChipAnalyzer(self.config)
        self.etf_resonance = ETFResonance(self.config)
        self.fund_flow = FundFlow(self.config)
        self.trend_health = TrendHealth(self.config)
        self.lifecycle_filter = ThemeLifecycleFilter(self.config)
        self.risk_filter = RiskFilter(self.config)
        self.alpha_scorer = AlphaScorer(self.config)
        self.verification = VerificationEngine(self.config)
        self.reporter = DailyReporter()
        self.all_stocks = []
        self._etf_pool = self.loader.get_etf_pool()

    def run(self, trade_date=None):
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date

        print(f"=" * 60)
        print(f"  Institution First Pullback Alpha V2")
        print(f"  机构主线第一次回调系统")
        print(f"  交易日期: {td}")
        print(f"=" * 60)

        print("\n[Step 1] 判断市场状态...")
        market_state = self.market_engine.evaluate(td)
        self._print_market_state(market_state)

        can_trade = self.market_engine.is_trade_allowed(market_state)
        if not can_trade:
            print(f"\n⚠️ 当前市场状态 {market_state.state} 不允许开仓，仅输出风险评估")
        else:
            print(f"\n✅ 市场状态 {market_state.state} 允许开仓，进行全流程评估")

        print("\n[Step 2] 识别机构主线...")
        themes = self.theme_engine.evaluate(td)
        self._print_themes(themes)

        if not themes:
            print("⚠️ 未识别到机构主线，结束运行")
            return

        print("\n[Step 3] 识别龙头...")
        theme_leaders = {}
        for theme in themes:
            theme_stocks = []
            theme_map = self.loader.load_theme_stock_map()
            if theme_map and theme.name in theme_map:
                raw_stocks = theme_map[theme.name]
                if isinstance(raw_stocks, list):
                    for s in raw_stocks:
                        if isinstance(s, dict) and 'code' in s:
                            theme_stocks.append(s['code'])
                        elif isinstance(s, str):
                            theme_stocks.append(s)
            else:
                sb = self.loader.load_stock_basic()
                if sb is not None:
                    theme_stocks = list(sb['ts_code'].sample(min(300, len(sb))))

            if not theme_stocks:
                continue

            print(f"\n  {theme.name} (成分股: {len(theme_stocks)})")
            leaders = self.leader_engine.evaluate(theme_stocks, theme_name=theme.name, etf_code=theme.etf_code, trade_date=td)
            theme_leaders[theme.name] = leaders
            theme.leader_stocks = leaders
            for ldr in leaders:
                print(f"    {ldr.name} ({ldr.ts_code}) LeaderScore: {ldr.leader_score} | 截面:{ldr.cross_section_score:.2f} 持续:{ldr.persistence_score:.2f} 任期:{ldr.tenure_days}天 {'★' if ldr.is_established else ''}")

        print("\n[Step 4] 筛选第一次回调...")
        all_candidates = []
        for theme in themes:
            leaders = theme_leaders.get(theme.name, [])
            if not leaders:
                continue

            leader_codes = [l.ts_code for l in leaders]
            print(f"\n  {theme.name}: 扫描 {len(leader_codes)} 只龙头...")
            pullbacks = self.pullback_detector.detect_batch(leader_codes, td)
            for pb in pullbacks:
                pb_result = {'pullback': pb, 'theme': theme}
                all_candidates.append(pb_result)
                print(f"    ✅ {pb.name} ({pb.ts_code}) 回踩{pb.pullback_ma} 质量:{pb.quality_score:.2f}")

        # 扫描各主题个股观察池（接近回调买点的标的）
        observation_pool = self._scan_observation_pool(themes, td)
        if observation_pool:
            print(f"\n  [观察池] 各主题中接近回调买点的标的:")
            for theme_name, stocks in observation_pool.items():
                print(f"    {theme_name}:")
                for s in stocks[:3]:
                    print(f"      {s['name']}({s['code']}) 涨幅{s['ret_60d']*100:.0f}% 回撤{s['drawdown']*100:.1f}% {'靠近'+s['near_ma'] if s['near_ma'] else ''}")

        if not can_trade:
            print(f"\n⚠️ 市场状态 {market_state.get_state_cn()} 不允许开仓，跳过后续评估")
            print(f"共发现 {len(all_candidates)} 个回调候选（仅观察）")
            self._generate_report(market_state, themes, all_candidates, observation_pool, td)
            return

        if not all_candidates:
            print("\n⚠️ 未发现符合条件的回调机会")
            return

        print(f"\n 共发现 {len(all_candidates)} 个符合条件的回调候选")

        print("\n[Step 5-10] 深度评估...")
        qualified = []
        for candidate in all_candidates:
            pb = candidate['pullback']
            theme = candidate['theme']
            code = pb.ts_code

            chip = self.chip_analyzer.analyze(code, td)
            if chip and not chip.is_stable:
                print(f"  ❌ {pb.name}: 筹码不稳定")
                continue

            lc = self.lifecycle_filter.evaluate(theme.name, [], td)
            if not lc.get('is_allowed', True):
                print(f"  ❌ {pb.name}: 主题生命周期 {lc.get('stage')} 不允许")
                continue

            risk = self.risk_filter.evaluate(code, td)
            if not risk.get('is_clean', True):
                print(f"  ❌ {pb.name}: 风险过滤 {risk.get('issues')}")
                continue

            etf_res = self.etf_resonance.evaluate(code, theme.etf_code, td)
            if etf_res and not etf_res.get('is_resonant', True):
                print(f"  ❌ {pb.name}: ETF共振不满足")
                continue

            flow = self.fund_flow.evaluate(code, td)
            if flow and not flow.get('is_recovering', True):
                print(f"  ❌ {pb.name}: 资金未恢复")
                continue

            th = self.trend_health.evaluate(code, td)
            if th and not th.get('is_healthy', True):
                print(f"  ❌ {pb.name}: 趋势不健康")
                continue

            components = {
                'ts_code': code,
                'name': pb.name,
                'theme': theme.name,
                'market_state': market_state.score / 100,
                'theme_strength': theme.composite_score,
                'leader_score': next((l.leader_score / 100 for l in theme.leader_stocks if l.ts_code == code), 0.5),
                'pullback_quality': pb.quality_score,
                'etf_resonance': etf_res.get('score', 75) / 100 if etf_res else 0.5,
                'chip_stability': chip.stability_score if chip else 0.5,
                'fund_flow_recovery': flow.get('score', 0.5) if flow else 0.5,
                'trend_health': th.get('score', 0.5) if th else 0.5,
                'buy_type': f"{pb.pullback_ma}回踩",
                'etf_code': theme.etf_code,
                'suggestion': '分批买入',
            }

            alpha_result = self.alpha_scorer.score(components)
            qualified.append(alpha_result)
            print(f"  ✅ {pb.name} Alpha: {alpha_result.alpha} {alpha_result.rating}")

        qualified.sort(key=lambda x: x.alpha, reverse=True)
        top_n = self.config['general'].get('top_n_output', 10)
        top_results = qualified[:top_n]

        print(f"\n[Step 11] Alpha评分完成，共 {len(qualified)} 只通过，Top{top_n}:")

        for i, r in enumerate(top_results):
            signal = self.alpha_scorer.generate_buy_signal(r, {
                'pullback_ma': r.buy_type,
                'etf_status': 'ETF新高' if r.etf_resonance_score > 0.75 else 'ETF稳定',
                'chip_status': '筹码稳定' if r.chip_stability > 0.7 else '筹码一般',
                'flow_status': '资金连续流入' if r.fund_flow_recovery > 0.7 else '资金恢复',
            })
            print(f"\n  #{i+1} {signal}")

        print("\n[Step 12] 保存验证记录...")
        records = []
        for r in top_results:
            ldr_score = next((l.leader_score for l in theme_leaders.get(r.theme, []) if l.ts_code == r.ts_code), 0)
            record = VerificationRecord(
                ts_code=r.ts_code,
                name=r.name,
                theme=r.theme,
                entry_date=td,
                alpha=r.alpha,
                market_state=market_state.state,
                market_score=market_state.score,
                theme_strength=r.theme_strength,
                leader_score=ldr_score,
                pullback_quality=r.pullback_quality,
                etf_resonance_score=r.etf_resonance_score,
                chip_stability=r.chip_stability,
                fund_flow_recovery=r.fund_flow_recovery,
                trend_health_score=r.trend_health_score,
            )
            records.append(record)
        self.verification.save_batch(records)
        print(f"  已保存 {len(records)} 条验证记录")

        print("\n[Step 13] 生成复盘报告...")
        report, report_path = self.reporter.generate_report(
            market_state=market_state,
            themes=themes,
            buy_signals=top_results,
            sell_signals=[],
            trade_date=td,
        )
        print(f"  报告已保存至: {report_path}")

        self.reporter.print_console(report)

        return top_results

    def _print_market_state(self, ms: MarketState):
        print(f"  Market State: {ms.state}")
        print(f"  Market Score: {ms.score:.0f}")
        print(f"  趋势: {ms.trend_score:.2f} | 资金: {ms.money_score:.2f} | 宽度: {ms.breadth_score:.2f} | 新高: {ms.new_high_score:.2f} | 情绪: {ms.sentiment_score:.2f}")

    def _generate_report(self, market_state, themes, all_candidates, observation_pool, td):
        """生成复盘报告（含不可交易时的简化版）"""
        lines = []
        lines.append("# 机构主线第一次回调系统 · 复盘报告")
        lines.append(f"## 交易日期: {td}")
        lines.append("")

        lines.append("## 一、市场状态")
        lines.append(f"- 市场状态: **{market_state.get_state_cn()}**")
        lines.append(f"- 市场评分: **{market_state.score:.0f}**")
        lines.append(f"- 趋势: {market_state.trend_score:.2f} | 资金: {market_state.money_score:.2f} | 宽度: {market_state.breadth_score:.2f} | 新高: {market_state.new_high_score:.2f} | 情绪: {market_state.sentiment_score:.2f}")
        if not self.market_engine.is_trade_allowed(market_state):
            lines.append("")
            lines.append("> ⚠️ 当前市场状态不允许开仓，以下为观察分析，不构成交易建议")
        lines.append("")

        lines.append("## 二、机构主线 Top5")
        lines.append("| 排名 | 主题 | 综合分 | 趋势 | 资金 | 动量爆发 | ETF | ETF趋势 |")
        lines.append("|------|------|--------|------|------|----------|-----|---------|")
        for t in themes:
            etf_name = self._etf_pool.get(t.etf_code, t.etf_code) if t.etf_code else "-"
            etf_trend_label = f"{t.etf_trend_score:.2f}"
            lines.append(f"| {t.rank} | {t.name} | {t.composite_score:.3f} | {t.trend_score:.3f} | {t.money_score:.3f} | {t.momentum_intensity:.3f} | {etf_name} | {etf_trend_label} |")
        lines.append("")

        lines.append("## 三、ETF操作参考")
        for t in themes:
            ref = self._etf_operation_ref(t)
            if ref:
                lines.append(f"- **{t.name}**: {ref}")
        lines.append("")

        lines.append("## 四、回调候选")
        if all_candidates:
            lines.append("| 股票 | 主题 | 回踩均线 | 质量分 |")
            lines.append("|------|------|----------|--------|")
            for c in all_candidates:
                pb = c['pullback']
                th = c['theme']
                lines.append(f"| {pb.name} | {th.name} | {pb.pullback_ma} | {pb.quality_score:.2f} |")
        else:
            lines.append("今日无符合条件的回调候选")
        lines.append("")

        lines.append("## 五、潜在观察池（放宽条件）")
        if observation_pool:
            lines.append("> 以下标的已满足趋势向上+60日涨幅≥30%+回撤3-25%，接近均线支撑位，可重点关注：")
            lines.append("")
            for theme_name, stocks in observation_pool.items():
                lines.append(f"### {theme_name}")
                lines.append("| 股票 | 60日涨幅 | 回撤幅度 | 均线靠近 |")
                lines.append("|------|----------|----------|----------|")
                for s in stocks[:5]:
                    near_str = s['near_ma'] if s['near_ma'] else "回踩中"
                    lines.append(f"| {s['name']}({s['code']}) | {s['ret_60d']*100:.0f}% | {s['drawdown']*100:.1f}% | {near_str} |")
                if len(stocks) > 5:
                    lines.append(f"| ... | 还有{len(stocks)-5}只 | | |")
                lines.append("")
        else:
            lines.append("当前无接近买点的标的")
        lines.append("")

        lines.append("## 六、操作建议")
        can_trade = self.market_engine.is_trade_allowed(market_state)
        if can_trade:
            lines.append(f"- **建议仓位**: 70%（强势市场）")
            lines.append(f"- **策略**: 聚焦强势主题回调低吸机会")
        else:
            lines.append(f"- **建议仓位**: 0%（观望）")
            lines.append(f"- **策略**: 当前市场震荡偏弱，耐心等待大盘企稳信号")
            lines.append(f"- **关注方向**: {themes[0].name if themes else '-'}、{themes[1].name if len(themes)>1 else '-'} 等主线主题回调后的右侧机会")
        lines.append("")

        lines.append("---")
        lines.append("*报告由 Institution First Pullback Alpha V2 自动生成*")

        report = "\n".join(lines)
        print("\n" + "=" * 60)
        print("  复盘报告")
        print("=" * 60)
        print(report)

        report_path = os.path.join(self.config['general'].get('output_dir', './output'), f"review_{td}.md")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n  📄 报告已保存至: {report_path}")

    def _print_themes(self, themes: List[ThemeResult]):
        print(f"  Top{len(themes)} 机构主线:")
        for t in themes:
            print(f"    {t.rank}. {t.name} (综合: {t.composite_score:.3f}, 趋势: {t.trend_score:.3f}, 资金: {t.money_score:.3f}, 动量爆发: {t.momentum_intensity:.3f})")

        print(f"\n  ETF操作参考:")
        for t in themes:
            etf_op = self._etf_operation_ref(t)
            if etf_op:
                print(f"    {t.rank}. {t.name} → {etf_op}")

    def _etf_operation_ref(self, theme: ThemeResult) -> str:
        """生成ETF操作参考"""
        if not theme.etf_code:
            return ""
        etf_name = self._etf_pool.get(theme.etf_code, "")
        etf_label = f"{etf_name}({theme.etf_code})" if etf_name else f"ETF({theme.etf_code})"
        score = theme.etf_trend_score
        if score >= 0.75:
            ref = "✅ 多头强势，可逢低做多"
        elif score >= 0.60:
            ref = "📈 趋势偏多，等待回踩确认"
        elif score >= 0.45:
            ref = "➡️ 震荡格局，观望为主"
        else:
            ref = "⚠️ 趋势偏弱，暂时回避"
        return f"{etf_label} ETF趋势{score:.2f} {ref}"

    def _scan_observation_pool(self, themes, td):
        """扫描各主题中接近回调买点的标的（放宽条件）"""
        import stock_cache as sc
        import json as _json

        observation_pool = {}
        # 从缓存读取主题成分股映射
        theme_map = self.loader.load_theme_stock_map()
        if not theme_map:
            return observation_pool

        start = (pd.to_datetime(td) - pd.Timedelta(days=250)).strftime('%Y%m%d')
        for theme in themes:
            raw_stocks = theme_map.get(theme.name, [])
            codes = []
            for s in raw_stocks:
                if isinstance(s, dict) and 'code' in s:
                    codes.append(s['code'])
                elif isinstance(s, str):
                    codes.append(s)
            codes = codes[:50]  # 扫描前50只

            candidates = []
            for code in codes:
                df = sc.cached_stk_factor_pro(code, start, td, silent=True)
                if df is None or df.empty or len(df) < 120:
                    continue
                close = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values
                if len(close) < 60:
                    continue
                # MA60斜率 > 0
                ma60 = pd.Series(close).rolling(60).mean().dropna()
                if len(ma60) < 10:
                    continue
                sl = slope(ma60.reset_index(drop=True), min(5, len(ma60)))
                if sl is None or len(sl) == 0 or sl.iloc[-1] <= 0:
                    continue
                # 60日涨幅 >= 30%
                ret_60d = close[-1] / close[-min(60, len(close))] - 1
                if ret_60d < 0.30:
                    continue
                # 回撤 3-25%
                high_60 = max(close[-60:])
                drawdown = (high_60 - close[-1]) / high_60
                if drawdown < 0.03 or drawdown > 0.25:
                    continue
                # 靠近均线?
                cv = close[-1]
                near_ma = ""
                for mp, mn in [(10, "MA10"), (20, "MA20"), (30, "MA30")]:
                    mv = pd.Series(close).rolling(mp).mean().iloc[-1]
                    if pd.isna(mv):
                        continue
                    if abs(cv - mv) / mv < 0.03:
                        near_ma = mn
                        break
                name = self.loader.get_stock_name(code)
                candidates.append({'code': code, 'name': name, 'ret_60d': ret_60d, 'drawdown': drawdown, 'near_ma': near_ma})

            if candidates:
                candidates.sort(key=lambda x: x['drawdown'])
                observation_pool[theme.name] = candidates

        return observation_pool

    def run_verification(self, trade_date=None):
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date

        print(f"\n[Verification] 回填历史验证记录...")
        self.verification.backfill_all_pending()

        print(f"\n[Verification] 生成周报...")
        report = self.verification.generate_weekly_report(td)
        print(report)

        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, f"verification_weekly_{td}.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n  周报已保存至: {report_path}")

        return report


def main():
    parser = argparse.ArgumentParser(description='Institution First Pullback Alpha V2')
    parser.add_argument('--date', type=str, default=None, help='交易日期 YYYYMMDD')
    parser.add_argument('--verify', action='store_true', help='仅运行验证回填')
    parser.add_argument('--weekly', action='store_true', help='生成周报')
    args = parser.parse_args()

    engine = InstitutionPullbackAlphaV2()

    if args.verify:
        engine.run_verification(args.date)
        return

    if args.weekly:
        engine.verification.backfill_all_pending()
        report = engine.verification.generate_weekly_report(args.date)
        print(report)
        return

    engine.run(args.date)


if __name__ == '__main__':
    main()