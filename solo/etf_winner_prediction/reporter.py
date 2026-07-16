#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Reporter 输出报告模块
=======================
生成最终输出:
  - DataFrame (按 Predicted Rank 排序)
  - JSON (保存到 output/)
  - Markdown 报告 (保存到 report/)
  - 可解释性说明
"""
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Reporter:
    """输出报告生成器"""

    def __init__(self, config: dict):
        self.config = config
        general = config.get("general", {})
        self.output_dir = os.path.join(BASE_DIR, general.get("output_dir", "./output"))
        self.report_dir = os.path.join(BASE_DIR, general.get("report_dir", "./report"))
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.report_dir, exist_ok=True)

    def to_dataframe(self, results: list) -> pd.DataFrame:
        """将结果列表转为DataFrame"""
        if not results:
            return pd.DataFrame()

        rows = []
        for r in results:
            rows.append({
                "Rank": r.predicted_rank,
                "ETF Code": r.etf_code,
                "ETF Name": r.etf_name,
                "Theme": r.theme,
                "Market Regime": r.market_state,
                "Market Score": r.market_score,
                "Future Theme Rank": r.theme_forecast_rank,
                "Theme Forecast Score": r.theme_forecast_score,
                "Lifecycle": r.lifecycle_stage,
                "Remaining Trend Days": r.remaining_trend_days,
                "Rotation Probability": f"{r.rotation_probability:.0f}%",
                "Leader": r.core_leader,
                "Leader Score": r.leader_score,
                "ETF Trend Score": r.etf_trend_score,
                "Expected20D": f"{r.expected_20d*100:.1f}%",
                "Expected40D": f"{r.expected_40d*100:.1f}%",
                "Expected60D": f"{r.expected_60d*100:.1f}%",
                "Expected Return": f"{r.expected_return*100:.1f}%",
                "Probability Top1": f"{r.probability_top1:.0%}",
                "Probability Top3": f"{r.probability_top3:.0%}",
                "Expected Holding Days": r.expected_holding_days,
                "Expected Drawdown": f"{r.expected_max_drawdown*100:.1f}%",
                "Risk Score": r.risk_score,
                "Suggested Position": f"{r.suggested_position*100:.0f}%",
                "Decision": r.decision,
                "Confidence": f"{r.confidence:.0f}%",
                "Reasons": "; ".join(r.reasons[:5]),
            })

        df = pd.DataFrame(rows)
        df = df.sort_values("Rank").reset_index(drop=True)
        return df

    def to_json(self, results: list, trade_date: str) -> str:
        """保存为JSON"""
        df = self.to_dataframe(results)
        fp = os.path.join(self.output_dir, f"etf_winner_{trade_date}.json")
        df.to_json(fp, orient="records", force_ascii=False, indent=2)
        return fp

    def to_csv(self, df: pd.DataFrame, trade_date: str) -> str:
        """保存为CSV"""
        fp = os.path.join(self.output_dir, f"etf_winner_{trade_date}.csv")
        df.to_csv(fp, index=False, encoding="utf-8-sig")
        return fp

    def to_markdown(self, results: list, trade_date: str) -> str:
        """生成Markdown报告"""
        df = self.to_dataframe(results)
        if df.empty:
            return ""

        dt = datetime.strptime(trade_date, "%Y%m%d")
        date_str = dt.strftime("%Y-%m-%d")

        lines = []
        lines.append(f"# ETF Winner Prediction Report")
        lines.append(f"**Date**: {date_str}")
        lines.append(f"**Total ETFs Evaluated**: {len(df)}")
        lines.append(f"**Accepted**: {len(df[df['Decision'] == 'ACCEPT'])}")
        lines.append(f"**Rejected**: {len(df[df['Decision'] == 'REJECT'])}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Accepted ETFs
        accepted = df[df["Decision"] == "ACCEPT"]
        if not accepted.empty:
            lines.append("## Accepted ETFs (Hard Filters Passed)")
            lines.append("")
            for _, row in accepted.iterrows():
                lines.append(f"### #{int(row['Rank'])} {row['ETF Code']} - {row['Theme']}")
                lines.append("")
                lines.append(f"- **Market**: {row['Market Regime']} (Score: {row['Market Score']})")
                lines.append(f"- **Theme Forecast**: Rank #{row['Future Theme Rank']}, Score {row['Theme Forecast Score']}")
                lines.append(f"- **Lifecycle**: {row['Lifecycle']}, Remaining {row['Remaining Trend Days']} days")
                lines.append(f"- **Leader**: {row['Leader']} (Score: {row['Leader Score']})")
                lines.append(f"- **ETF Trend**: {row['ETF Trend Score']}")
                lines.append(f"- **Expected Return**: 20D={row['Expected20D']}, 40D={row['Expected40D']}, 60D={row['Expected60D']}")
                lines.append(f"- **Top1 Prob**: {row['Probability Top1']}, Top3: {row['Probability Top3']}")
                lines.append(f"- **Risk**: {row['Risk Score']}, Position: {row['Suggested Position']}")
                lines.append(f"- **Confidence**: {row['Confidence']}")
                lines.append("")
                lines.append(f"**Why**: {row['Reasons']}")
                lines.append("")
            lines.append("---")
            lines.append("")

        # Rejected ETFs
        rejected = df[df["Decision"] == "REJECT"]
        if not rejected.empty:
            lines.append("## Rejected ETFs")
            lines.append("")
            for _, row in rejected.head(20).iterrows():
                lines.append(f"- **{row['ETF Code']}** ({row['Theme']}): {row['Reasons']}")

        lines.append("")
        lines.append("---")
        lines.append(f"*Report generated by ETF Winner Prediction Engine v1.0.0*")

        content = "\n".join(lines)
        fp = os.path.join(self.report_dir, f"ETF_Winner_Report_{trade_date}.md")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        return fp

    def print_summary(self, df: pd.DataFrame):
        """打印摘要"""
        if df.empty:
            print("No ETFs to display.")
            return

        accepted = df[df["Decision"] == "ACCEPT"]
        rejected = df[df["Decision"] == "REJECT"]

        print("=" * 90)
        print("  ETF Winner Prediction - Final Output")
        print("=" * 90)
        print(f"  Total: {len(df)} | Accepted: {len(accepted)} | Rejected: {len(rejected)}")
        print("=" * 90)

        if not accepted.empty:
            print("\n  >>> ACCEPTED (Hard Filters Passed) <<<\n")
            cols = ["Rank", "ETF Code", "Theme", "Lifecycle", "Expected Return",
                    "Probability Top3", "Risk Score", "Confidence"]
            print(accepted[cols].to_string(index=False))
        else:
            print("\n  >>> NO ETF PASSED ALL HARD FILTERS <<<\n")
            print("  Top candidates that came closest:")
            top_rejects = df.head(5)
            print(top_rejects[["Rank", "ETF Code", "Theme", "Decision", "Reasons"]].to_string(index=False))


def build_explainability(result) -> List[str]:
    """为每只ETF构建可解释性说明"""
    lines = []
    r = result
    if r.theme_forecast_rank <= 3:
        lines.append(f"Theme expected to remain Top3 for {r.remaining_trend_days} trading days.")
    if r.etf_trend_score >= 70:
        lines.append(f"ETF trend stable with score {r.etf_trend_score:.1f}.")
    if r.leader_score >= 75:
        lines.append(f"Leader continuously outperformed industry (score {r.leader_score:.1f}).")
    if r.expected_return >= 0.10:
        lines.append(f"Expected return {r.expected_return*100:.1f}% over holding period.")
    lines.append(f"Market regime supportive at score {r.market_score:.1f}.")
    return lines