import os
import sys
import json
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DailyReporter:
    def __init__(self, output_dir=None):
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output')
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_report(self, market_state, themes, buy_signals, sell_signals, trade_date=None):
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')

        lines = []
        lines.append("# A股机构主线第一次回调日报")
        lines.append(f"日期: {trade_date}")
        lines.append("")

        lines.append("## 一、市场状态")
        ms = market_state
        if hasattr(ms, 'get_state_cn'):
            lines.append(f"- 市场状态: **{ms.get_state_cn()}**")
            lines.append(f"- 市场评分: **{ms.score:.0f}**")
            lines.append(f"- 趋势: {ms.trend_score:.2f} | 资金: {ms.money_score:.2f} | 宽度: {ms.breadth_score:.2f} | 新高: {ms.new_high_score:.2f} | 情绪: {ms.sentiment_score:.2f}")
        elif hasattr(ms, 'state'):
            lines.append(f"- 市场状态: **{ms.state}**")
            lines.append(f"- 市场评分: **{ms.score:.0f}**")
        else:
            lines.append(f"- 市场状态: **{ms.get('state', 'UNKNOWN')}**")
            lines.append(f"- 市场评分: **{ms.get('score', 0):.0f}**")
        lines.append("")

        lines.append("## 二、机构主线Top5")
        if themes:
            lines.append("| 排名 | 主题 | 综合评分 | 趋势 | 资金 | ETF趋势 | 龙头强度 |")
            lines.append("|------|------|----------|------|------|---------|----------|")
            for t in themes:
                if hasattr(t, 'rank'):
                    lines.append(f"| {t.rank} | {t.name} | {t.composite_score:.3f} | {t.trend_score:.3f} | {t.money_score:.3f} | {t.etf_trend_score:.3f} | {t.leader_strength_score:.3f} |")
                else:
                    lines.append(f"| {t.get('rank', '-')} | {t.get('name', '-')} | {t.get('composite_score', 0):.3f} | - | - | - | - |")
        else:
            lines.append("暂无机构主线数据")
        lines.append("")

        lines.append("## 三、龙头跟踪")
        if themes:
            lines.append("| 主题 | 龙头 | LeaderScore | 状态 |")
            lines.append("|------|------|-------------|------|")
            for t in themes:
                if hasattr(t, 'leader_stocks') and t.leader_stocks:
                    for ldr in t.leader_stocks[:3]:
                        if hasattr(ldr, 'name'):
                            lines.append(f"| {t.name} | {ldr.name} | {ldr.leader_score:.0f} | 跟踪中 |")
                        else:
                            lines.append(f"| {t.name} | {ldr} | - | 跟踪中 |")
                else:
                    lines.append(f"| {t.name if hasattr(t, 'name') else t.get('name', '-')} | - | - | 待识别 |")
        lines.append("")

        lines.append("## 四、第一次回调机会")
        if buy_signals:
            lines.append("| 股票 | 主题 | Alpha | 买点类型 | ETF | 建议 |")
            lines.append("|------|------|-------|----------|-----|------|")
            for s in buy_signals:
                if hasattr(s, 'alpha'):
                    lines.append(f"| {s.name} | {s.theme} | **{s.alpha}** | {s.buy_type} | {s.etf_code} | {s.suggestion} |")
                else:
                    lines.append(f"| {s.get('name', '-')} | {s.get('theme', '-')} | **{s.get('alpha', 0)}** | {s.get('buy_type', '-')} | {s.get('etf_code', '-')} | {s.get('suggestion', '-')} |")
        else:
            lines.append("今日暂无符合条件的回调机会")
        lines.append("")

        lines.append("## 五、风险预警")
        if sell_signals:
            for s in sell_signals:
                if hasattr(s, 'ts_code'):
                    lines.append(f"- ⚠️ {s.name} ({s.theme}): {s.suggestion}")
                else:
                    lines.append(f"- ⚠️ {s.get('name', '-')} ({s.get('theme', '-')}): {s.get('suggestion', '减仓')}")
        else:
            lines.append("暂无风险预警")
        lines.append("")

        lines.append("## 六、操作建议")
        buy_count = len(buy_signals) if buy_signals else 0
        sell_count = len(sell_signals) if sell_signals else 0
        ms_obj = market_state
        if hasattr(ms_obj, 'state'):
            if ms_obj.state in ['BULL_TREND', 'BULL_PULLBACK']:
                position_pct = 70
            elif ms_obj.state in ['ROTATION', 'SIDEWAY']:
                position_pct = 40
            else:
                position_pct = 20
        else:
            position_pct = 50

        lines.append(f"- 建议仓位: **{position_pct}%**")
        lines.append(f"- 新开仓: **{buy_count}**只")
        lines.append(f"- 持仓观察: 根据现有持仓调整")
        lines.append(f"- 清仓: **{sell_count}**只")
        lines.append("")

        lines.append("---")
        lines.append(f"*报告由 Institution First Pullback Alpha V2 自动生成*")

        report = "\n".join(lines)

        report_path = os.path.join(self.output_dir, f"daily_report_{trade_date}.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)

        json_path = os.path.join(self.output_dir, f"daily_report_{trade_date}.json")
        report_data = {
            'trade_date': trade_date,
            'market_state': {
                'state': ms.state if hasattr(ms, 'state') else ms.get('state', ''),
                'score': ms.score if hasattr(ms, 'score') else ms.get('score', 0),
            },
            'buy_signals': [s.to_dict() if hasattr(s, 'to_dict') else s for s in (buy_signals or [])],
            'sell_signals': sell_signals or [],
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        return report, report_path

    def print_console(self, report):
        print(report)