# -*- coding: utf-8 -*-
"""
Report Generator - 市场报告生成器 V3

整合所有引擎的评估结果，生成结构化报告字典和格式化 Markdown 报告。
"""

import os
import sys
from typing import Dict, List, Tuple

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from market_regime_v3.engines.market_score import MarketScoreResult
from market_regime_v3.engines.state_machine import MarketRegime
from market_regime_v3.engines.heat_engine import HeatResult
from market_regime_v3.engines.style_engine import StyleResult
from market_regime_v3.engines.risk_engine import RiskAppetiteResult
from market_regime_v3.engines.breadth_engine import BreadthResult
from market_regime_v3.engines.sentiment_engine import SentimentResult
from market_regime_v3.engines.theme_resonance import ThemeResonanceResult
from market_regime_v3.engines.index_engine import IndexStrengthResult
from market_regime_v3.engines.exposure_model import ExposureResult
from market_regime_v3.explainer import MarketExplainer, ExplainBlock


class MarketReportGenerator:
    """市场报告生成器

    整合所有引擎的评估结果，生成结构化报告和格式化 Markdown 文本。
    """

    def __init__(self, output_dir: str = "./output"):
        self.output_dir = output_dir
        self.explainer = MarketExplainer()
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def generate_report(
        self,
        trade_date: str,
        market_score_result: MarketScoreResult,
        market_regime: MarketRegime,
        heat_result: HeatResult,
        style_result: StyleResult,
        risk_result: RiskAppetiteResult,
        breadth_result: BreadthResult,
        sentiment_result: SentimentResult,
        theme_resonance_result: ThemeResonanceResult,
        index_strength_result: IndexStrengthResult,
        exposure_result: ExposureResult,
    ) -> Tuple[Dict, str]:
        """生成完整市场报告

        Args:
            trade_date: 交易日 YYYYMMDD
            market_score_result: 市场评分结果
            market_regime: 市场状态
            heat_result: 市场热度结果
            style_result: 风格轮动结果
            risk_result: 风险偏好结果
            breadth_result: 市场宽度结果
            sentiment_result: 市场情绪结果
            theme_resonance_result: 主题共振结果
            index_strength_result: 指数强度结果
            exposure_result: 仓位暴露建议

        Returns:
            (report_dict, markdown_str)
        """
        # ---- 风险检测 ----
        risk_warnings = self._detect_risks(
            market_regime, breadth_result, sentiment_result, style_result
        )

        # ---- 使用 Explainer 生成解释块 ----
        explain_blocks = self._build_explain_blocks(
            market_score_result, index_strength_result, breadth_result,
            sentiment_result, style_result, heat_result
        )

        # ---- 构建结构化报告字典 ----
        report_dict = {
            "meta": {
                "trade_date": trade_date,
                "title": f"Market Regime Report — {trade_date}",
            },
            "overview": self._build_overview(
                market_score_result, market_regime, risk_result,
                heat_result, style_result, exposure_result,
                theme_resonance_result
            ),
            "market_score": self._build_market_score_section(
                market_score_result, explain_blocks
            ),
            "index_strength": self._build_index_strength_section(
                index_strength_result
            ),
            "breadth": self._build_breadth_section(breadth_result),
            "sentiment": self._build_sentiment_section(sentiment_result),
            "style": self._build_style_section(style_result),
            "heat": self._build_heat_section(heat_result),
            "exposure": self._build_exposure_section(exposure_result),
            "risk_warnings": risk_warnings,
            "suggestions": {
                "strategy": self._get_strategy(market_regime, heat_result),
                "trade_frequency": heat_result.max_trades_per_day,
                "risk_control": "严格执行止盈止损，单票上限15%",
            },
        }

        # ---- 渲染 Markdown ----
        markdown_str = self._render_markdown(report_dict)

        return report_dict, markdown_str

    # ------------------------------------------------------------------
    # V2: 6层 Pipeline 报告（含 Theme Beta / Leader / Trading Style / Risk Control）
    # ------------------------------------------------------------------

    def generate_report_v2(
        self,
        trade_date: str,
        market_score_result: MarketScoreResult,
        market_regime: MarketRegime,
        heat_result: HeatResult,
        style_result: StyleResult,
        risk_result: RiskAppetiteResult,
        breadth_result: BreadthResult,
        sentiment_result: SentimentResult,
        theme_resonance_result: ThemeResonanceResult,
        index_strength_result: IndexStrengthResult,
        exposure_result: ExposureResult,
        theme_beta_result=None,
        leader_result=None,
        trading_style_result=None,
        risk_control_result=None,
        v61_data=None,
    ) -> Dict:
        """生成6层Pipeline完整报告"""
        # 基础报告
        base_report, _ = self.generate_report(
            trade_date=trade_date,
            market_score_result=market_score_result,
            market_regime=market_regime,
            heat_result=heat_result,
            style_result=style_result,
            risk_result=risk_result,
            breadth_result=breadth_result,
            sentiment_result=sentiment_result,
            theme_resonance_result=theme_resonance_result,
            index_strength_result=index_strength_result,
            exposure_result=exposure_result,
        )

        # ── 第3层: Theme Beta ──
        theme_beta_section = {}
        if theme_beta_result is not None:
            theme_beta_section = {
                "allocations": theme_beta_result.allocations,
                "theme_scores": theme_beta_result.theme_scores,
                "theme_betas": theme_beta_result.theme_betas,
                "method": theme_beta_result.method,
            }

        # ── 第4层: Leader Quality ──
        leader_section = {"top_leaders": [], "theme_strength": {}}
        if leader_result is not None:
            leader_section = {
                "top_leaders": [
                    {"name": ld.get('name', ld.get('ts_code', '')),
                     "ts_code": ld.get('ts_code', ''),
                     "score": ld.get('total_score', 0),
                     "theme": ld.get('theme', ''),
                     "cross_section": ld.get('cross_section_score', 0),
                     "persistence": ld.get('persistence_score', 0)}
                    for ld in (leader_result.top_leaders or [])[:10]
                ],
                "theme_strength": leader_result.theme_leader_strength or {},
            }

        # ── 第5层: Trading Style ──
        trading_style_section = {"style": "swing_trade", "label": "波段操作", "description": ""}
        if trading_style_result is not None:
            trading_style_section = {
                "style": trading_style_result.style_name,
                "label": trading_style_result.style_label,
                "description": trading_style_result.style_description,
            }

        # ── 第6层: Risk Control ──
        risk_control_section = {"is_safe": True, "warnings": [], "actions": []}
        if risk_control_result is not None:
            risk_control_section = {
                "is_safe": risk_control_result.is_safe,
                "warnings": [w.get('detail', w.get('type', '')) for w in (risk_control_result.warnings or [])],
                "actions": risk_control_result.actions or [],
                "max_positions": risk_control_result.max_positions,
                "max_per_position_pct": risk_control_result.max_per_position_pct,
                "stop_loss_atr": risk_control_result.stop_loss_atr,
                "take_profit_atr": risk_control_result.take_profit_atr,
            }

        # 合并到报告
        report_dict = base_report
        report_dict["theme_beta"] = theme_beta_section
        report_dict["leader_quality"] = leader_section
        report_dict["trading_style"] = trading_style_section
        report_dict["risk_control"] = risk_control_section

        # 生成V2 Markdown
        markdown = self._render_markdown_v2(report_dict)
        report_dict["markdown"] = markdown

        # ── V6.1: 机构研报格式个股输出 ──
        if v61_data:
            report_dict.update(v61_data)
        v61_markdown = self._render_v61_institutional_report(report_dict)
        if v61_markdown:
            report_dict["markdown"] += "\n" + v61_markdown

        return report_dict

    # ------------------------------------------------------------------
    # V6.1 机构研报格式输出
    # ------------------------------------------------------------------

    def _judge_market_env(self, regime: str, score: float) -> str:
        """判断大盘环境类型"""
        if regime in ('Bear',) or score < 32:
            return "主跌期"
        elif regime in ('Recovery', 'Neutral'):
            return "震荡回暖期"
        else:  # Bull, Euphoria
            return "主升期"

    def _judge_stock_type(self, theme: str, subtheme: str, dom_theme: str) -> str:
        """判断个股类型：防御型 / 高弹性 / 中性"""
        # 合并所有可用的主题字段
        keywords = f"{theme} {subtheme} {dom_theme}".lower()

        # 防御性主题关键词
        defensive = ['电力', '银行', '煤炭', '钢铁', '公用事业', '高股息', '高速公路',
                     '白酒', '家电', '食品', '医药商业', '运营商', '水务']
        # 高弹性主题关键词
        high_beta = ['半导体', '机器人', 'ai算力', '人工智能', '信创', '消费电子',
                     '新能源车', '量子计算', '创新药', '创新化药', '整车',
                     '光伏', '储能', '军工', '芯片', '软件', '金融科技']

        for kw in defensive:
            if kw in keywords:
                return "防御型"
        for kw in high_beta:
            if kw in keywords:
                return "高弹性"
        return "中性"

    def _build_trade_suggestion(self, market_env: str, stock_type: str) -> str:
        """根据大盘环境和个股类型生成买卖提示"""
        mapping = {
            ("主跌期", "防御型"):   "防御配置优先（大盘主跌，防御性标的关注价值凸显）",
            ("主跌期", "高弹性"):   "注意风险（大盘主跌，高弹性标的承压较大，需严格控仓）",
            ("主跌期", "中性"):     "谨慎参与（大盘主跌，市场风险偏高，注意仓位管理）",
            ("震荡回暖期", "防御型"): "稳健配置（大盘震荡回暖，防御标的可作底仓配置）",
            ("震荡回暖期", "高弹性"): "弹性优先（大盘回暖，高弹性标的有望率先反弹）",
            ("震荡回暖期", "中性"):   "伺机而动（大盘震荡回暖，等待明确信号再入场）",
            ("主升期", "防御型"):    "顺势配置（大盘主升，防御标的亦可跟随）",
            ("主升期", "高弹性"):    "积极做多（大盘主升，高弹性标的弹性放大，持仓为主）",
            ("主升期", "中性"):      "顺势参与（大盘主升，可适当参与市场）",
        }
        return mapping.get((market_env, stock_type), "按计划执行")

    def _render_v61_institutional_report(self, data: Dict) -> str:
        """渲染V6.1机构研报格式的个股分析（V6.2增强：Pattern Type/Adjusted EV/System Mode）"""
        lines = []
        pullback_list = data.get("pullback_qualified", [])
        if not pullback_list:
            return ""

        pattern_data = data.get("v61_pattern")
        ev_data = data.get("v61_ev")
        sm_data = data.get("v61_smart_money")
        rb_data = data.get("v61_risk_budget")
        overview = data.get("overview", {})

        # V6.2: 系统模式
        system_mode = 'LIVE'
        if rb_data:
            system_mode = rb_data.system_mode

        # 大盘环境判断（用于买卖提示）
        regime = overview.get('regime', '')
        market_score = overview.get('market_score', 50)
        market_env = self._judge_market_env(regime, market_score)

        lines.append("")
        lines.append("## 十四、V6.2 候选标的深度分析")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"**系统模式**: {system_mode}")
        if rb_data and rb_data.learning_count > 0:
            lines.append(f" | **学习仓位**: {rb_data.learning_count}只")
        lines.append("")
        lines.append(f"> **大盘环境**: {market_env}（{regime} {market_score:.0f}分）")
        lines.append("")
        lines.append("")

        for pq in pullback_list:
            code = pq.get('ts_code', '')
            name = pq.get('name', '')
            theme = pq.get('theme', '')
            subtheme = pq.get('subtheme', '')
            dom_theme = pq.get('dominant_theme', '')

            # 买卖提示
            stock_type = self._judge_stock_type(theme, subtheme, dom_theme)
            suggestion = self._build_trade_suggestion(market_env, stock_type)
            # 写回 pullback_qualified 条目，供微信推送等模块使用
            pq["stock_type"] = stock_type
            pq["suggestion"] = suggestion

            pm = pattern_data.matches.get(code) if pattern_data and pattern_data.matches else None
            ev_r = ev_data.results.get(code) if ev_data and ev_data.results else None
            sm_r = sm_data.get(code) if sm_data else None
            pos_r = rb_data.positions.get(code) if rb_data and rb_data.positions else None

            lines.append(f"### {name}（{code}）")
            lines.append("")
            lines.append(f"| 维度 | 数据 |")
            lines.append(f"|------|------|")
            lines.append(f"| Market Regime | {overview.get('regime', 'N/A')}（{overview.get('regime_cn', '')}） |")
            lines.append(f"| Market Score | {overview.get('market_score', 0):.0f}/100 |")
            lines.append(f"| Theme | {theme} |")
            if subtheme:
                lines.append(f"| Subtheme | {subtheme} |")
            if dom_theme and dom_theme != theme:
                lines.append(f"| Dominant Theme | {dom_theme} |")
            # V7.0 拉升回调字段
            lines.append(f"| V7.0 总分 | {pq.get('total_score', 0):.0f}/100 |")
            lines.append(f"| 拉升幅度 | +{pq.get('rally_amplitude', 0)*100:.0f}% |")
            lines.append(f"| 涨停次数 | {pq.get('rally_limit_up_count', 0)}次（连板{pq.get('rally_max_consecutive_lu', 0)}） |")
            lines.append(f"| 区间放量 | {pq.get('rally_vol_expansion', 0):.1f}倍 |")
            lines.append(f"| 回撤 | {pq.get('drawdown', 0)*100:.1f}%（回调{pq.get('pullback_days', 0)}天） |")
            lines.append(f"| 低开阳线 | 低开{pq.get('candle_open_gap', 0)*100:.1f}% → 阳线{pq.get('candle_body_pct', 0)*100:.1f}% |")
            subs = pq.get('subs', {})
            lines.append(f"| 分项得分 | 放量{subs.get('vol_expansion',0):.0f} 涨停{subs.get('limit_up',0):.0f} 回调{subs.get('pullback',0):.0f} 阳线{subs.get('candle',0):.0f} 量能{subs.get('volume_confirm',0):.0f} |")
            lines.append(f"| 个股类型 | {stock_type} |")
            lines.append(f"| **买卖提示** | **{suggestion}** |")
            lines.append("")

            # Historical Pattern — V6.2 含Pattern Type
            if pm:
                lines.append("#### Pattern")
                lines.append("")
                lines.append(f"| 指标 | 数值 |")
                lines.append(f"|------|------|")
                lines.append(f"| 模式类型 | {pm.pattern_type} |")
                lines.append(f"| 相似案例 | {pm.n_samples}次 |")
                if pm.n_samples >= 5:
                    lines.append(f"| 上涨概率 | {pm.win_probability:.0%} |")
                    lines.append(f"| 平均收益(5日) | {pm.avg_return_5d:+.2%} |")
                    lines.append(f"| 平均收益(10日) | {pm.avg_return_10d:+.2%} |")
                    lines.append(f"| 平均收益(20日) | {pm.avg_return_20d:+.2%} |")
                    lines.append(f"| 中位收益(10日) | {pm.median_return_10d:+.2%} |")
                    lines.append(f"| 预期最大回撤 | {pm.avg_max_drawdown:.1%} |")
                    lines.append(f"| 平均持有天数 | {pm.avg_holding_days:.0f}天 |")
                else:
                    lines.append(f"| 上涨概率 | {pm.win_probability:.0%}（冷启动） |")
                lines.append("")
            else:
                lines.append("#### Pattern")
                lines.append("")
                lines.append("无匹配数据")
                lines.append("")

            # Smart Money
            if sm_r:
                lines.append("#### Smart Money")
                lines.append("")
                att = sm_r.attribution
                lines.append(f"| 因子 | 贡献 |")
                lines.append(f"|------|------|")
                lines.append(f"| Smart Money Score | {sm_r.composite_score:.0f}分 |")
                lines.append(f"| 主力资金 | +{att.main_force_score:.0f} |")
                lines.append(f"| 超大单 | +{att.super_large_score:.0f} |")
                lines.append(f"| 筹码 | +{att.chip_concentration:.0f} |")
                lines.append(f"| 换手 | +{att.turnover_health:.0f} |")
                lines.append(f"| 方向 | {sm_r.direction} |")
                lines.append("")

            # Expected Value — V6.2 含Adjusted EV + Confidence Level
            if ev_r:
                lines.append("#### Expected Value")
                lines.append("")
                lines.append(f"| 指标 | 数值 |")
                lines.append(f"|------|------|")
                lines.append(f"| Probability | {ev_r.win_probability:.0%} |")
                lines.append(f"| Expected 5D | {ev_r.expected_return_5d:+.2%} |")
                lines.append(f"| Expected 10D (EV) | {ev_r.expected_value_10d:+.2%} |")
                lines.append(f"| Expected 20D | {ev_r.expected_return_20d:+.2%} |")
                lines.append(f"| Expected Drawdown | {ev_r.expected_drawdown:.1%} |")
                lines.append(f"| Risk Reward Ratio | {ev_r.risk_reward_ratio:.2f} |")
                lines.append(f"| EV Score | {ev_r.ev_score:.0f}分 |")
                lines.append(f"| Confidence | {ev_r.confidence:.2f} |")
                lines.append(f"| Confidence Level | {ev_r.confidence_level}（n={ev_r.n_samples}） |")
                lines.append(f"| **Adjusted EV** | **{ev_r.adjusted_ev:+.2%}** |")
                lines.append(f"| 建议 | {ev_r.signal.value} |")
                lines.append("")

            # Risk Budget Position — V6.2 含System Mode + Learning
            if pos_r and pos_r.position_pct > 0:
                exp = pos_r.explanation
                lines.append("#### Position")
                lines.append("")
                lines.append(f"| 维度 | 说明 |")
                lines.append(f"|------|------|")
                lines.append(f"| 系统模式 | {exp.system_mode} |")
                if pos_r.is_learning:
                    lines.append(f"| 仓位类型 | **学习仓位**（仅用于积累样本） |")
                lines.append(f"| 最终仓位 | {exp.final_position_pct:.1f}% |")
                if not pos_r.is_learning:
                    lines.append(f"| 仓位计算 | {exp.base_position_pct:.0f}% × {exp.market_multiplier:.1f}(市) × {exp.ev_multiplier:.1f}(EV) × {exp.risk_multiplier:.1f}(风) |")
                lines.append(f"| 市场状态 | {exp.regime_label} → 乘数{exp.market_multiplier:.1f} |")
                if not pos_r.is_learning:
                    lines.append(f"| EV贡献 | {exp.ev_label} → 乘数{exp.ev_multiplier:.1f} |")
                lines.append(f"| 风险贡献 | {exp.risk_label} |")
                lines.append(f"| 单票上限 | {exp.max_per_position_pct:.0f}% |")
                lines.append("")
            elif pos_r:
                lines.append("#### Position")
                lines.append("")
                lines.append(f"不建仓（信号：{pos_r.signal} | 模式: {pos_r.explanation.system_mode}）")
                lines.append("")

            # 入场逻辑
            lines.append("#### Entry Logic")
            lines.append("")
            stop_pct = (pq.get('stop_loss', 0) / max(pq.get('ref_price', 1), 0.01) - 1) * 100
            profit_pct = (pq.get('take_profit', 0) / max(pq.get('ref_price', 1), 0.01) - 1) * 100
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            lines.append(f"| 入场参考 | {pq.get('ref_price', 0):.2f} |")
            lines.append(f"| 止损 | {pq.get('stop_loss', 0):.2f}（{stop_pct:+.1f}%） |")
            lines.append(f"| 止盈 | {pq.get('take_profit', 0):.2f}（{profit_pct:+.1f}%） |")
            lines.append(f"| ATR | {pq.get('atr', 0):.2f} |")
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # V2 Markdown 渲染
    # ------------------------------------------------------------------

    def _render_markdown_v2(self, data: Dict) -> str:
        """渲染包含6层Pipeline的完整Markdown报告"""
        lines = []
        meta = data.get("meta", {})
        overview = data.get("overview", {})

        lines.append(f"# 市场状态报告 Market Regime Report — {meta.get('trade_date', '')}")
        lines.append(f"{'═' * 56}")
        lines.append("")

        # ── 一、综合概览 ──
        lines.append("## 一、综合概览")
        lines.append("")
        lines.append(f"- Market Score: {overview.get('market_score', 0):.0f}/100")
        lines.append(f"- Market Regime: {overview.get('regime', '')}（{overview.get('regime_cn', '')}）")
        lines.append(f"- Risk Appetite: {overview.get('risk_appetite_level', '')}")
        lines.append(f"- Heat Score: {overview.get('heat_score', 0):.0f}/100 ({overview.get('heat_level', '')})")
        lines.append(f"- Dominant Style: {overview.get('dominant_style', '')}")
        lines.append(f"- Recommended Exposure: {overview.get('exposure', 0):.0f}%")
        lines.append(f"- Active Themes: {overview.get('theme_count', 0)}个")
        lines.append(f"- Trading Style: {data.get('trading_style', {}).get('label', '')}")
        lines.append(f"- Risk Status: {'✅ 安全' if data.get('risk_control', {}).get('is_safe', True) else '⚠️ 有风险'}")
        lines.append("")

        # ── 二、Market Score 分解 ──
        ms = data.get("market_score", {})
        lines.append("## 二、Market Score 分解")
        lines.append("")
        for item in ms.get("detail", ms.get("items", [])):
            lines.append(f"- {item}")
        lines.append("")

        # ── 三、指数强度 ──
        idx = data.get("index_strength", {})
        idx_rows = idx.get("table_rows", [])
        if idx_rows:
            lines.append("## 三、指数强度")
            lines.append("")
            lines.append("| 指数 | 得分 | 趋势 | 动量 | MA排列 | MACD |")
            lines.append("|------|------|------|------|--------|------|")
            for row in idx_rows:
                if isinstance(row, dict):
                    lines.append(f"| {row.get('name','')} | {row.get('score',0):.0f} | {row.get('trend',0):.0f} | {row.get('momentum',0):.0f} | {row.get('ma_alignment',0):.0f} | {row.get('macd',0):.0f} |")
                else:
                    lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} |")
            lines.append("")

        # ── 四、市场宽度 ──
        brd = data.get("breadth", {})
        lines.append("## 四、市场宽度")
        lines.append("")
        lines.append(f"- 上涨比例: {brd.get('up_ratio', 0)*100:.0f}%")
        lines.append(f"- 涨停: {brd.get('limit_up_count', brd.get('limit_up', 0))}家 | 跌停: {brd.get('limit_down_count', brd.get('limit_down', 0))}家")
        lines.append(f"- 20日新高: {brd.get('new_high_20_ratio', brd.get('new_high_20', 0))*100:.1f}%")
        lines.append(f"- 站上MA20: {brd.get('above_ma20_ratio', brd.get('above_ma20', 0))*100:.0f}%")
        lines.append(f"- 涨幅中位数: {brd.get('median_return', 0):.2f}%")
        lines.append("")

        # ── 五、市场情绪 ──
        st = data.get("sentiment", {})
        lines.append("## 五、市场情绪")
        lines.append("")
        lines.append(f"- 涨停率: {st.get('limit_up_ratio', 0)*100:.2f}%")
        lines.append(f"- 炸板率: {st.get('break_ratio', 0)*100:.0f}%")
        lines.append(f"- 最高连板: {st.get('max_continuous', 0)}板")
        lines.append(f"- 20cm涨停: {st.get('gem_count', 0)}家")
        lines.append(f"- 北向资金: {st.get('north_flow', 0):+.0f}亿")
        lines.append("")

        # ── 六、风格轮动 + 交易风格 ──
        style = data.get("style", {})
        lines.append("## 六、风格轮动 & 交易风格")
        lines.append("")
        ts = data.get("trading_style", {})
        lines.append(f"### 当前交易风格: {ts.get('label', '波段操作')}")
        lines.append(f"- 策略描述: {ts.get('description', '')}")
        lines.append("")
        lines.append("### 风格评分")
        lines.append("| 风格 | 得分 |")
        lines.append("|------|------|")
        for s_name, s_score in sorted(style.get("items", {}).items(),
                                       key=lambda x: x[1], reverse=True)[:7]:
            cn = MarketReportGenerator._style_to_cn(s_name)
            lines.append(f"| {cn} | {s_score:.0f} |")
        lines.append("")
        if style.get("suggestions"):
            lines.append(f"建议关注: {', '.join(style['suggestions'][:5])}")
            lines.append("")

        # ── 七、市场热度 ──
        heat = data.get("heat", {})
        lines.append("## 七、市场热度")
        lines.append("")
        lines.append(f"- Heat Score: {heat.get('score', 0):.0f}/100")
        lines.append(f"- 等级: {heat.get('level', '')} | 趋势: {heat.get('trend', '')} | 周期: {heat.get('cycle', '')}")
        lines.append(f"- 仓位修正: {heat.get('adjustment', 1.0):.2f}")
        lines.append("")

        # ── 八、Theme Beta（主题资金分配） ──
        tb = data.get("theme_beta", {})
        lines.append("## 八、Theme Beta（主题资金分配）")
        lines.append("")
        if tb.get("allocations"):
            lines.append(f"分配方法: {tb.get('method', 'score_weighted')}")
            lines.append("")
            lines.append("| 主题 | 分配比例 | 综合评分 | Beta |")
            lines.append("|------|----------|----------|------|")
            for tname, alloc in sorted(tb.get('allocations', {}).items(),
                                        key=lambda x: x[1], reverse=True):
                score = tb.get('theme_scores', {}).get(tname, 0)
                beta = tb.get('theme_betas', {}).get(tname, 0)
                lines.append(f"| {tname} | {alloc*100:.0f}% | {score:.0f} | {beta:.2f} |")
            lines.append("")
        else:
            lines.append("当前无有效主题分配")
            lines.append("")

        # ── 九、Leader Quality（龙头质量） ──
        lq = data.get("leader_quality", {})
        lines.append("## 九、Leader Quality（龙头质量）")
        lines.append("")
        top_leaders = lq.get("top_leaders", [])
        if top_leaders:
            lines.append("| 排名 | 股票 | 主题 | 总分 | 截面 | 持续 |")
            lines.append("|------|------|------|------|------|------|")
            for i, ld in enumerate(top_leaders[:10]):
                theme = ld.get('theme', '')
                cs = ld.get('cross_section_score', ld.get('score', 0))
                ps = ld.get('persistence_score', 0)
                ts = ld.get('total_score', ld.get('score', 0))
                lines.append(f"| {i+1} | {ld.get('name', '')} | {theme} | {ts:.0f} | {cs:.0f} | {ps:.0f} |")
            lines.append("")
        else:
            lines.append("无符合条件龙头")
            lines.append("")

        # ── 十、仓位建议 ──
        exp = data.get("exposure", {})
        lines.append("## 十、仓位建议")
        lines.append("")
        base_pct = exp.get("base_exposure", exp.get("raw_exposure", 0)) * 100
        lines.append(f"- 基础仓位: {base_pct:.0f}%（Market Score）")
        risk_mult = exp.get("risk_appetite_multiplier", 1.0)
        lines.append(f"- 风险偏好乘数: ×{risk_mult:.2f}")
        heat_mult = exp.get("heat_multiplier", 1.0)
        lines.append(f"- 热度乘数: ×{heat_mult:.2f}")
        raw_pct = exp.get("raw_exposure", 0) * 100
        lines.append(f"- 合成仓位: {raw_pct:.0f}%")
        floor = exp.get("regime_floor", 0) * 100
        cap = exp.get("regime_cap", 100) * 100
        lines.append(f"- Regime限幅: {floor:.0f}%~{cap:.0f}%")
        lines.append(f"- 最终仓位: {exp.get('portfolio_exposure_pct', 0):.0f}%")
        lines.append(f"- 主题数量: {exp.get('theme_count_min', 0)}~{exp.get('theme_count_max', 0)}个")
        lines.append(f"- ETF配置: {exp.get('etf_allocation', 0)*100:.0f}%")
        lines.append(f"- 龙头配置: {exp.get('leader_allocation', 0)*100:.0f}%")
        lines.append(f"- 跟风配置: {exp.get('follower_allocation', 0)*100:.0f}%")
        lines.append(f"- 现金: {exp.get('cash_allocation', 0)*100:.0f}%")
        lines.append("")

        # ── 十一、Risk Control（风控） ──
        rc = data.get("risk_control", {})
        lines.append("## 十一、Risk Control（风控执行）")
        lines.append("")
        lines.append(f"- 安全状态: {'✅' if rc.get('is_safe', True) else '⚠️ 有风险'}")
        if rc.get("warnings"):
            lines.append("- 风险警告:")
            for w in rc.get("warnings", []):
                lines.append(f"  - ⚠️ {w}")
        if rc.get("actions"):
            lines.append("- 建议操作:")
            for a in rc.get("actions", []):
                lines.append(f"  - {a}")
        lines.append(f"- 止损: {rc.get('stop_loss_atr', 2.0)}倍ATR")
        lines.append(f"- 止盈: {rc.get('take_profit_atr', 3.0)}倍ATR")
        lines.append(f"- 单票上限: {rc.get('max_per_position_pct', 0.15)*100:.0f}%")
        lines.append("")

        # ── 十二、风险提示 ──
        risks = data.get("risk_warnings", [])
        lines.append("## 十二、风险提示")
        lines.append("")
        if risks:
            for r in risks:
                severity = r.get("severity", "info")
                icon = "🔴" if severity == "danger" else ("⚠️" if severity == "warning" else "ℹ️")
                lines.append(f"- {icon} {r.get('type', '')}: {r.get('detail', '')}")
        else:
            lines.append("当前无显著风险信号")
        lines.append("")

        # ── 十三、操作建议 ──
        sug = data.get("suggestions", {})
        lines.append("## 十三、操作建议")
        lines.append("")
        lines.append(f"- 策略: {sug.get('strategy', '观望为主')}")
        lines.append(f"- 交易频率: {sug.get('trade_frequency', 0)}次/日")
        lines.append(f"- 风控: {sug.get('risk_control', '严格执行止盈止损')}")
        lines.append("")
        lines.append("---")
        lines.append("*Market Regime Engine V3 · 6层 Pipeline 自动生成*")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 风险检测
    # ------------------------------------------------------------------

    def _detect_risks(
        self,
        regime: MarketRegime,
        breadth: BreadthResult,
        sentiment: SentimentResult,
        style: StyleResult,
    ) -> List[Dict]:
        """检测市场风险信号"""
        warnings = []

        # 缩量上涨：上涨比例 > 50% 但上涨缺乏支撑（真实缩量上涨）
        is_up_trend = regime.primary in ("Bull", "Euphoria")
        if breadth.up_ratio > 0.5 and breadth.amount_spread_score < 30 and is_up_trend:
            warnings.append({
                "type": "缩量上涨",
                "detail": f"上涨比例 {breadth.up_ratio*100:.0f}% 但成交额偏集中，上涨缺乏量能支撑",
                "severity": "warning",
            })

        # 放量下跌
        if breadth.limit_down_count > 20:
            warnings.append({
                "type": "放量下跌",
                "detail": f"跌停 {breadth.limit_down_count} 家，市场抛压较重",
                "severity": "danger",
            })

        # 情绪退潮
        if sentiment.score < 35:
            warnings.append({
                "type": "情绪退潮",
                "detail": f"情绪评分 {sentiment.score:.1f}，市场情绪低迷",
                "severity": "danger",
            })

        # 风格背离：大盘与小盘走势分化
        large_cap = style.style_scores.get("LargeCap", None)
        small_cap = style.style_scores.get("SmallCap", None)
        if large_cap is not None and small_cap is not None:
            divergence = abs(large_cap - small_cap)
            if divergence > 25:
                warnings.append({
                    "type": "风格背离",
                    "detail": (
                        f"大盘({large_cap:.0f})与小盘({small_cap:.0f})风格分化显著"
                        f"（差值 {divergence:.0f}分），注意风格切换风险"
                    ),
                    "severity": "warning",
                })

        # 普跌调整：上涨比例 < 25%
        if breadth.up_ratio < 0.25:
            warnings.append({
                "type": "普跌调整",
                "detail": f"上涨比例仅 {breadth.up_ratio*100:.0f}%，市场中位数跌幅 {breadth.median_return:.2f}%",
                "severity": "warning",
            })

        return warnings

    # ------------------------------------------------------------------
    # 解释块构建
    # ------------------------------------------------------------------

    def _build_explain_blocks(
        self,
        market_score_result: MarketScoreResult,
        index_strength_result: IndexStrengthResult,
        breadth_result: BreadthResult,
        sentiment_result: SentimentResult,
        style_result: StyleResult,
        heat_result: HeatResult,
    ) -> Dict[str, ExplainBlock]:
        """使用 Explainer 生成各维度的解释块"""
        blocks = {}

        blocks["market_score"] = self.explainer.explain_market_score(
            market_score_result.score,
            market_score_result.contributions,
        )

        blocks["index_strength"] = self.explainer.explain_index_strength(
            index_strength_result.per_index,
            index_strength_result.weighted_score,
            index_strength_result.sub_scores,
        )

        blocks["breadth"] = self.explainer.explain_breadth(breadth_result)

        blocks["sentiment"] = self.explainer.explain_sentiment(sentiment_result)

        blocks["style"] = self.explainer.explain_style(style_result)

        blocks["heat"] = self.explainer.explain_heat(heat_result)

        return blocks

    # ------------------------------------------------------------------
    # 各章节数据构建
    # ------------------------------------------------------------------

    def _build_overview(
        self,
        market_score_result: MarketScoreResult,
        market_regime: MarketRegime,
        risk_result: RiskAppetiteResult,
        heat_result: HeatResult,
        style_result: StyleResult,
        exposure_result: ExposureResult,
        theme_resonance_result: ThemeResonanceResult,
    ) -> Dict:
        """构建综合概览数据"""
        return {
            "market_score": market_score_result.score,
            "market_score_display": f"{market_score_result.score:.0f}/100",
            "regime": market_regime.primary,
            "regime_cn": market_regime.description,
            "risk_appetite": risk_result.score,
            "risk_appetite_level": risk_result.level,
            "risk_appetite_display": f"{risk_result.level}（{'积极' if risk_result.score >= 65 else '中性' if risk_result.score >= 50 else '谨慎'}）",
            "heat_score": heat_result.score,
            "heat_score_display": f"{heat_result.score:.0f}/100",
            "heat_level": heat_result.level,
            "dominant_style": style_result.dominant_style,
            "style_cn": self._style_to_cn(style_result.dominant_style),
            "exposure": exposure_result.portfolio_exposure_pct,
            "exposure_display": f"{exposure_result.portfolio_exposure_pct:.0f}%",
            "theme_count": theme_resonance_result.theme_count,
            "theme_count_display": f"{theme_resonance_result.theme_count}个",
            "trade_frequency": heat_result.max_trades_per_day,
            "trade_frequency_display": f"{heat_result.max_trades_per_day}次/日",
        }

    def _build_market_score_section(
        self,
        market_score_result: MarketScoreResult,
        explain_blocks: Dict[str, ExplainBlock],
    ) -> Dict:
        """构建 Market Score 分解数据"""
        return {
            "score": market_score_result.score,
            "contributions": market_score_result.contributions,
            "explain_items": explain_blocks["market_score"].items,
        }

    def _build_index_strength_section(
        self, result: IndexStrengthResult
    ) -> Dict:
        """构建指数强度数据"""
        table_rows = []
        for code, total_score in result.per_index.items():
            subs = result.sub_scores.get(code, {})
            row = {
                "code": code,
                "name": self._index_name(code),
                "score": total_score,
                "trend": subs.get("trend", 0),
                "momentum": subs.get("momentum", 0),
                "ma_alignment": subs.get("ma_alignment", 0),
                "macd": subs.get("macd", 0),
            }
            table_rows.append(row)
        return {
            "weighted_score": result.weighted_score,
            "table_rows": table_rows,
        }

    def _build_breadth_section(self, result: BreadthResult) -> Dict:
        """构建市场宽度数据"""
        return {
            "score": result.score,
            "up_ratio": result.up_ratio,
            "limit_up_count": result.limit_up_count,
            "limit_down_count": result.limit_down_count,
            "new_high_20_ratio": result.new_high_20_ratio,
            "above_ma20_ratio": result.above_ma20_ratio,
            "median_return": result.median_return,
        }

    def _build_sentiment_section(self, result: SentimentResult) -> Dict:
        """构建市场情绪数据"""
        return {
            "score": result.score,
            "limit_up_ratio": result.limit_up_ratio,
            "break_ratio": result.break_ratio,
            "max_continuous": result.max_continuous,
            "gem_count": result.gem_count,
            "north_flow": result.north_flow,
            "amount_change": result.amount_change,
        }

    def _build_style_section(self, result: StyleResult) -> Dict:
        """构建风格轮动数据"""
        style_list = []
        for name, score in sorted(
            result.style_scores.items(), key=lambda x: x[1], reverse=True
        ):
            style_list.append({
                "name": name,
                "name_cn": self._style_to_cn(name),
                "score": score,
            })
        return {
            "style_list": style_list,
            "dominant_style": result.dominant_style,
            "dominant_style_cn": self._style_to_cn(result.dominant_style),
            "suggestions": result.suggestions,
        }

    def _build_heat_section(self, result: HeatResult) -> Dict:
        """构建市场热度数据"""
        sub_list = []
        for name, score in sorted(
            result.sub_scores.items(), key=lambda x: x[1], reverse=True
        ):
            sub_list.append({
                "name": self._heat_sub_name_cn(name),
                "score": score,
            })
        return {
            "score": result.score,
            "level": result.level,
            "trend": result.trend,
            "cycle": result.cycle,
            "adjustment_factor": result.adjustment_factor,
            "max_trades_per_day": result.max_trades_per_day,
            "trading_style": result.trading_style,
            "sub_scores": sub_list,
        }

    def _build_exposure_section(self, result: ExposureResult) -> Dict:
        """构建仓位建议数据"""
        return {
            "raw_exposure": result.raw_exposure,
            "base_exposure": result.base_exposure,
            "risk_appetite_multiplier": result.risk_appetite_multiplier,
            "heat_multiplier": result.heat_multiplier,
            "regime_floor": result.regime_floor,
            "regime_cap": result.regime_cap,
            "portfolio_exposure_pct": result.portfolio_exposure_pct,
            "theme_count_min": result.theme_count_min,
            "theme_count_max": result.theme_count_max,
            "etf_allocation": result.etf_allocation,
            "leader_allocation": result.leader_allocation,
            "follower_allocation": result.follower_allocation,
            "cash_allocation": result.cash_allocation,
        }

    # ------------------------------------------------------------------
    # Markdown 渲染
    # ------------------------------------------------------------------

    def _render_markdown(self, data: Dict) -> str:
        """将报告字典渲染为格式化 Markdown 字符串"""
        lines = []
        meta = data["meta"]
        ov = data["overview"]

        # ── 标题 ──
        lines.append(f"# Market Regime Report — {meta['trade_date']}")
        lines.append("═" * 45)
        lines.append("")

        # ── 一、综合概览 ──
        lines.append("## 一、综合概览")
        lines.append(f"- Market Score: {ov['market_score_display']}")
        lines.append(f"- Market Regime: {ov['regime']}（{ov['regime_cn']}）")
        lines.append(f"- Risk Appetite: {ov['risk_appetite_display']}")
        lines.append(f"- Heat Score: {ov['heat_score_display']} ({ov['heat_level']})")
        lines.append(f"- Dominant Style: {ov['dominant_style']}（{ov['style_cn']}）")
        lines.append(f"- Recommended Exposure: {ov['exposure_display']}")
        lines.append(f"- Theme Count: {ov['theme_count_display']}")
        lines.append(f"- Trading Frequency: {ov['trade_frequency_display']}")
        lines.append("")

        # ── 二、Market Score 分解 ──
        ms = data["market_score"]
        lines.append("## 二、Market Score 分解")
        contribs = ms["contributions"]
        for key, label in [
            ("index_strength", "指数强度"),
            ("breadth", "市场宽度"),
            ("sentiment", "情绪"),
            ("theme_resonance", "主题共振"),
            ("risk_appetite", "风险偏好"),
        ]:
            val = contribs.get(key, 0)
            lines.append(f"- {label}: {val:.1f}（贡献{val:+.1f}）")
        sep = "─" * 20
        lines.append(f"- {sep}")
        lines.append(f"- 总分: 修正后: {ms['score']:.0f}")
        lines.append("")
        lines.append("评分解释:")
        for item in ms["explain_items"]:
            lines.append(f"- {item}")
        lines.append("")

        # ── 三、指数强度 ──
        idx = data["index_strength"]
        lines.append("## 三、指数强度")
        lines.append(f"| 指数 | 得分 | 趋势 | 动量 | MA排列 | MACD |")
        lines.append(f"|------|------|------|------|--------|------|")
        for row in idx["table_rows"]:
            lines.append(
                f"| {row['name']} | {row['score']:.0f} | "
                f"{row['trend']:.0f} | {row['momentum']:.0f} | "
                f"{row['ma_alignment']:.0f} | {row['macd']:.0f} |"
            )
        lines.append("")

        # ── 四、市场宽度 ──
        br = data["breadth"]
        lines.append("## 四、市场宽度")
        lines.append(f"- 上涨比例: {br['up_ratio']*100:.0f}%")
        lines.append(f"- 涨停: {br['limit_up_count']}家")
        lines.append(f"- 跌停: {br['limit_down_count']}家")
        lines.append(f"- 20日新高: {br['new_high_20_ratio']*100:.1f}%")
        lines.append(f"- 站上MA20: {br['above_ma20_ratio']*100:.0f}%")
        lines.append(f"- 涨幅中位数: {br['median_return']:+.2f}%")
        lines.append("")

        # ── 五、市场情绪 ──
        se = data["sentiment"]
        lines.append("## 五、市场情绪")
        lines.append(f"- 涨停率: {se['limit_up_ratio']*100:.1f}%")
        lines.append(f"- 炸板率: {se['break_ratio']*100:.0f}%")
        lines.append(f"- 最高连板: {se['max_continuous']}板")
        lines.append(f"- 20cm涨停: {se['gem_count']}家")
        lines.append(f"- 北向资金: {se['north_flow']:+.0f}亿")
        lines.append(f"- 成交额变化: {se['amount_change']*100:+.1f}%")
        lines.append("")

        # ── 六、风格轮动 ──
        st = data["style"]
        lines.append("## 六、风格轮动")
        lines.append(f"| 风格 | 得分 |")
        lines.append(f"|------|------|")
        for s in st["style_list"]:
            lines.append(f"| {s['name_cn']} | {s['score']:.0f} |")
        lines.append("")
        lines.append(f"主导风格: {st['dominant_style']}（{st['dominant_style_cn']}）")
        if st["suggestions"]:
            lines.append(f"建议关注: {', '.join(st['suggestions'][:3])}")
        lines.append("")

        # ── 七、市场热度 ──
        ht = data["heat"]
        lines.append("## 七、市场热度")
        lines.append(f"- Heat Score: {ht['score']:.0f}/100")
        lines.append(f"- 等级: {ht['level']}")
        lines.append(f"- 趋势: {ht['trend']}")
        lines.append(f"- 周期: {ht['cycle']}")
        lines.append(f"- 仓位修正系数: {ht['adjustment_factor']:.2f}")
        lines.append(f"- 建议交易频率: {ht['max_trades_per_day']}次/日")
        lines.append(f"- 操作风格: {ht['trading_style']}")
        lines.append("")
        lines.append("热度分解:")
        for sub in ht["sub_scores"]:
            lines.append(f"- {sub['name']}: {sub['score']:.0f}分")
        lines.append("")

        # ── 八、仓位建议 ──
        ex = data["exposure"]
        lines.append("## 八、仓位建议")
        base_pct = ex.get("base_exposure", ex["raw_exposure"]) * 100
        lines.append(f"- 基础仓位: {base_pct:.0f}%（Market Score）")
        risk_mult = ex.get("risk_appetite_multiplier", 1.0)
        lines.append(f"- 风险偏好乘数: ×{risk_mult:.2f}")
        heat_mult = ex.get("heat_multiplier", 1.0)
        lines.append(f"- 热度乘数: ×{heat_mult:.2f}")
        raw_pct = ex["raw_exposure"] * 100
        lines.append(f"- 合成仓位: {raw_pct:.0f}%")
        floor = ex.get("regime_floor", 0) * 100
        cap = ex.get("regime_cap", 100) * 100
        lines.append(f"- Regime限幅: {floor:.0f}%~{cap:.0f}%")
        lines.append(f"- 最终仓位: {ex['portfolio_exposure_pct']:.0f}%")
        lines.append(f"- 主题数量: {ex['theme_count_min']}~{ex['theme_count_max']}个")
        lines.append(f"- ETF配置: {ex['etf_allocation']*100:.0f}%")
        lines.append(f"- 龙头配置: {ex['leader_allocation']*100:.0f}%")
        lines.append(f"- 跟风配置: {ex['follower_allocation']*100:.0f}%")
        lines.append(f"- 现金: {ex['cash_allocation']*100:.0f}%")
        lines.append("")

        # ── 九、风险提示 ──
        rw = data["risk_warnings"]
        lines.append("## 九、风险提示")
        if rw:
            for w in rw:
                icon = "⚠️" if w["severity"] == "warning" else "🔴"
                lines.append(f"- {icon} {w['type']}: {w['detail']}")
        else:
            lines.append("- 指数背离: 无异常")
            lines.append("- 缩量上涨: 无")
            lines.append("- 放量下跌: 无")
            lines.append("- 高位分歧: 无")
            lines.append("- 情绪退潮: 无")
        lines.append("")

        # ── 十、操作建议 ──
        sg = data["suggestions"]
        lines.append("## 十、操作建议")
        lines.append(f"- 策略: {sg['strategy']}")
        lines.append(f"- 交易频率: {sg['trade_frequency']}次/日")
        lines.append(f"- 风控: {sg['risk_control']}")
        lines.append("")

        # ── 底部 ──
        lines.append("---")
        lines.append("")
        lines.append("*Market Regime Engine V3 自动生成*")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 策略建议
    # ------------------------------------------------------------------

    @staticmethod
    def _get_strategy(regime: MarketRegime, heat: HeatResult) -> str:
        """根据市场状态和热度生成策略建议"""
        if regime.primary == "Euphoria":
            return "亢奋期注意控制仓位，适度止盈，避免追高"
        if regime.primary == "Bull":
            if heat.level in ("Hot", "Very Hot", "Extreme Hot"):
                return "积极做多，持股为主，注意过热回调风险"
            return "偏多操作，精选个股"
        if regime.primary == "Recovery":
            return "逢低布局，逐步加仓"
        if regime.primary == "Neutral":
            return "震荡市，高抛低吸，控制仓位"
        if regime.primary == "Bear":
            return "防御为主，降低仓位，多看少动"
        return "观望为主，等待明确信号"

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------

    def save_report(self, markdown: str, trade_date: str) -> str:
        """保存 Markdown 报告到文件

        Args:
            markdown: Markdown 文本
            trade_date: 交易日 YYYYMMDD

        Returns:
            文件路径
        """
        filename = f"market_report_{trade_date}.md"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"[Reporter] 报告已保存: {filepath}")
        return filepath

    @staticmethod
    def print_report(markdown: str):
        """打印报告到控制台"""
        print(markdown)

    # ------------------------------------------------------------------
    # 辅助工具
    # ------------------------------------------------------------------

    @staticmethod
    def _index_name(code: str) -> str:
        """指数代码 -> 中文名称"""
        names = {
            "000001.SH": "上证指数",
            "000300.SH": "沪深300",
            "000852.SH": "中证1000",
            "399006.SZ": "创业板",
            "000688.SH": "科创50",
        }
        return names.get(code, code)

    @staticmethod
    def _style_to_cn(name: str) -> str:
        """风格英文 -> 中文"""
        mapping = {
            "LargeCap": "大盘",
            "SmallCap": "小盘",
            "Growth": "成长",
            "Value": "价值",
            "Technology": "科技",
            "Dividend": "红利",
            "Consumption": "消费",
            "Medical": "医疗",
            "Military": "军工",
            "AI": "AI",
            "Defense": "国防",
            "Cyclical": "周期",
            "Financial": "金融",
            "NewEnergy": "新能源",
            "Semiconductor": "半导体",
        }
        return mapping.get(name, name)

    @staticmethod
    def _heat_sub_name_cn(name: str) -> str:
        """热度子因子英文 -> 中文"""
        mapping = {
            "volume_heat": "成交额热度",
            "profit_heat": "赚钱效应",
            "limit_up_heat": "涨停热度",
            "leader_heat": "龙头热度",
            "etf_heat": "ETF热度",
            "theme_heat": "主题热度",
            "capital_flow_heat": "资金流热度",
            "volatility_heat": "波动率热度",
        }
        return mapping.get(name, name)
