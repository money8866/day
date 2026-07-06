"""Final Output Reporter — 生成格式化报告与导出数据。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger


class OutputReporter:
    """Generates final reports for the Institutional Mainline Engine."""

    def __init__(self, config: dict):
        self.cfg = config
        self.output_dir = config.get("general", {}).get("output_dir", "./output")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_dataframe(self, results: List) -> pd.DataFrame:
        """Convert CompositeResult list to a clean DataFrame.

        Columns: rank, ts_code, stock_name, etf_code, etf_name, theme,
                 lifecycle_stage, etf_trend, capital, heat, lifecycle,
                 leader, leader_persistence, resonance, risk_inverted,
                 composite_score, confidence, buy_signal, sell_signal
        """
        if not results:
            logger.warning("to_dataframe received empty results, returning empty DataFrame")
            return pd.DataFrame(columns=[
                "rank", "ts_code", "stock_name", "etf_code", "etf_name",
                "theme", "lifecycle_stage", "etf_trend", "capital", "heat",
                "lifecycle", "leader", "leader_persistence", "resonance",
                "risk_inverted", "composite_score", "confidence",
                "buy_signal", "sell_signal",
            ])

        rows = []
        for r in results:
            rows.append({
                "rank": getattr(r, "rank", 0),
                "ts_code": getattr(r, "ts_code", ""),
                "stock_name": getattr(r, "stock_name", ""),
                "etf_code": getattr(r, "etf_code", ""),
                "etf_name": getattr(r, "etf_name", ""),
                "theme": getattr(r, "theme", ""),
                "lifecycle_stage": getattr(r, "lifecycle_stage", "Unknown"),
                "etf_trend": getattr(r, "etf_trend", 0.0),
                "capital": getattr(r, "capital", 0.0),
                "heat": getattr(r, "heat", 0.0),
                "lifecycle": getattr(r, "lifecycle", 0.0),
                "leader": getattr(r, "leader", 0.0),
                "leader_persistence": getattr(r, "leader_persistence", 0.0),
                "resonance": getattr(r, "resonance", 0.0),
                "risk_inverted": getattr(r, "risk_inverted", 0.0),
                "composite_score": getattr(r, "composite_score", 0.0),
                "confidence": getattr(r, "confidence", ""),
                "buy_signal": getattr(r, "buy_signal", ""),
                "sell_signal": getattr(r, "sell_signal", ""),
            })

        df = pd.DataFrame(rows)
        return df

    def generate_html_report(
        self, results: List, title: str = "\u673a\u6784\u4e3b\u7ebf\u8bc6\u522b\u62a5\u544a"
    ) -> str:
        """Generate a complete HTML report with tables and formatting.

        - Summary table with top N results
        - Color coding by confidence (green=high, yellow=medium, red=low)
        - Buy/sell signal icons
        - Theme grouping
        - Statistics summary (count by confidence, avg scores by theme)

        Returns file path.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mainline_report_{timestamp}.html"
        filepath = os.path.join(self.output_dir, filename)
        os.makedirs(self.output_dir, exist_ok=True)

        if not results:
            logger.warning("generate_html_report received empty results")
            html = self._empty_html(title)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"Empty HTML report saved to {filepath}")
            return filepath

        df = self.to_dataframe(results)
        top_n = self.cfg.get("general", {}).get("top_n", 20)
        display = df.head(top_n)

        # Confidence distribution
        conf_counts = df["confidence"].value_counts()
        high_count = int(conf_counts.get("high", 0))
        med_count = int(conf_counts.get("medium", 0))
        low_count = int(conf_counts.get("low", 0))

        # Average composite by theme
        theme_avg = (
            df.groupby("theme")["composite_score"]
            .agg(["mean", "count"])
            .round(2)
            .sort_values("mean", ascending=False)
            .head(10)
        )

        table_rows = self._build_html_table_rows(display)
        stat_rows = self._build_html_theme_stats(theme_avg)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f7fa; color: #333; padding: 24px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
.header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: #fff; padding: 28px 32px; border-radius: 12px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 6px; }}
.header .meta {{ font-size: 13px; color: #a0aec0; }}
.summary-cards {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
.card {{ flex: 1; min-width: 140px; background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); text-align: center; }}
.card .value {{ font-size: 28px; font-weight: 700; }}
.card .label {{ font-size: 12px; color: #718096; margin-top: 4px; }}
.card.high .value {{ color: #38a169; }}
.card.medium .value {{ color: #d69e2e; }}
.card.low .value {{ color: #e53e3e; }}
.card.total .value {{ color: #2b6cb0; }}
.section {{ background: #fff; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.section h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #2d3748; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #edf2f7; color: #4a5568; font-weight: 600; padding: 10px 8px; text-align: left; border-bottom: 2px solid #e2e8f0; white-space: nowrap; }}
td {{ padding: 8px; border-bottom: 1px solid #edf2f7; }}
tr:hover {{ background: #f7fafc; }}
.conf-high {{ color: #38a169; font-weight: 600; }}
.conf-medium {{ color: #d69e2e; font-weight: 600; }}
.conf-low {{ color: #e53e3e; font-weight: 600; }}
.signal-buy {{ display: inline-block; background: #c6f6d5; color: #22543d; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.signal-sell {{ display: inline-block; background: #fed7d7; color: #742a2a; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.signal-none {{ display: inline-block; background: #f7fafc; color: #a0aec0; padding: 2px 8px; border-radius: 4px; font-size: 11px; }}
.text-right {{ text-align: right; }}
.text-center {{ text-align: center; }}
.theme-badge {{ display: inline-block; background: #ebf4ff; color: #2b6cb0; padding: 2px 8px; border-radius: 4px; font-size: 11px; }}
.stats-table {{ font-size: 12px; }}
.stats-table th {{ font-size: 11px; }}
.footer {{ text-align: center; color: #a0aec0; font-size: 12px; padding: 20px 0; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
<h1>{title}</h1>
<div class="meta">\u751f\u6210\u65f6\u95f4: {generated_at} | \u5171 {len(results)} \u6761\u7ed3\u679c</div>
</div>

<div class="summary-cards">
<div class="card total"><div class="value">{len(results)}</div><div class="label">\u7ebf\u7d22\u603b\u6570</div></div>
<div class="card high"><div class="value">{high_count}</div><div class="label">\u9ad8\u4fe1\u5fc3\u5ea6</div></div>
<div class="card medium"><div class="value">{med_count}</div><div class="label">\u4e2d\u4fe1\u5fc3\u5ea6</div></div>
<div class="card low"><div class="value">{low_count}</div><div class="label">\u4f4e\u4fe1\u5fc3\u5ea6</div></div>
</div>

<div class="section">
<h2>\u7ebf\u7d22\u8be6\u60c5\u8868 (Top {top_n})</h2>
<table>
<thead>
<tr>
<th>\u6392\u540d</th>
<th>\u4ee3\u7801</th>
<th>\u540d\u79f0</th>
<th>ETF</th>
<th>\u4e3b\u9898</th>
<th>\u751f\u547d\u5468\u671f</th>
<th>\u7efc\u5408\u5f97\u5206</th>
<th>\u4fe1\u5fc3\u5ea6</th>
<th>\u4e70\u5165</th>
<th>\u5356\u51fa</th>
</tr>
</thead>
<tbody>
{table_rows}
</tbody>
</table>
</div>

<div class="section">
<h2>\u4e3b\u9898\u7edf\u8ba1 (Top 10)</h2>
<table class="stats-table">
<thead>
<tr>
<th>\u4e3b\u9898</th>
<th class="text-right">\u5e73\u5747\u7efc\u5408\u5f97\u5206</th>
<th class="text-right">\u7ebf\u7d22\u6570\u91cf</th>
</tr>
</thead>
<tbody>
{stat_rows}
</tbody>
</table>
</div>

<div class="footer">
\u673a\u6784\u4e3b\u7ebf\u8bc6\u522b\u5f15\u64ce \u00b7 {generated_at}
</div>

</div>
</body>
</html>"""

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"HTML report saved to {filepath}")
        return filepath

    def generate_markdown_summary(self, results: List, top_n: int = 20) -> str:
        """Generate markdown summary for quick review."""
        if not results:
            return "# \u673a\u6784\u4e3b\u7ebf\u8bc6\u522b\u62a5\u544a\n\n*暂无数据*\n"

        df = self.to_dataframe(results)
        display = df.head(top_n)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conf_counts = df["confidence"].value_counts()
        high_count = int(conf_counts.get("high", 0))
        med_count = int(conf_counts.get("medium", 0))
        low_count = int(conf_counts.get("low", 0))

        # Top themes
        theme_ranking = (
            df.groupby("theme")["composite_score"]
            .mean()
            .round(2)
            .sort_values(ascending=False)
            .head(5)
        )

        buy_count = int((df["buy_signal"] != "").sum())
        sell_count = int((df["sell_signal"] != "").sum())

        lines = []
        lines.append(f"# \u673a\u6784\u4e3b\u7ebf\u8bc6\u522b\u62a5\u544a")
        lines.append(f"")
        lines.append(f"- \u751f\u6210\u65f6\u95f4: {timestamp}")
        lines.append(f"- \u7ebf\u7d22\u603b\u6570: {len(results)}")
        lines.append(f"")
        lines.append(f"## \u6982\u89c8")
        lines.append(f"")
        lines.append(f"| \u6307\u6807 | \u503c |")
        lines.append(f"|---|---|")
        lines.append(f"| \u9ad8\u4fe1\u5fc3\u5ea6 | {high_count} |")
        lines.append(f"| \u4e2d\u4fe1\u5fc3\u5ea6 | {med_count} |")
        lines.append(f"| \u4f4e\u4fe1\u5fc3\u5ea6 | {low_count} |")
        lines.append(f"| \u4e70\u5165\u4fe1\u53f7 | {buy_count} |")
        lines.append(f"| \u5356\u51fa\u4fe1\u53f7 | {sell_count} |")
        lines.append(f"")
        lines.append(f"## \u4e3b\u9898\u6392\u540d (Top 5)")
        lines.append(f"")
        lines.append(f"| \u4e3b\u9898 | \u5e73\u5747\u5f97\u5206 |")
        lines.append(f"|---|---|")
        for theme_name, avg_score in theme_ranking.items():
            theme_display = theme_name if theme_name else "(\u672a\u5206\u914d)"
            lines.append(f"| {theme_display} | {avg_score} |")
        lines.append(f"")
        lines.append(f"## \u7ebf\u7d22\u8be6\u60c5 (Top {top_n})")
        lines.append(f"")
        lines.append(f"| \u6392\u540d | \u4ee3\u7801 | \u540d\u79f0 | ETF | \u4e3b\u9898 | \u751f\u547d\u5468\u671f | \u7efc\u5408\u5f97\u5206 | \u4fe1\u5fc3\u5ea6 | \u4e70\u5165 | \u5356\u51fa |")
        lines.append(f"|---|---|---|---|---|---|---|---|---|---|")

        for _, row in display.iterrows():
            rank = int(row["rank"])
            ts_code = str(row["ts_code"])
            stock_name = str(row["stock_name"]) if row["stock_name"] else "-"
            etf_code = str(row["etf_code"]) if row["etf_code"] else "-"
            theme = str(row["theme"]) if row["theme"] else "-"
            lifecycle = str(row["lifecycle_stage"])
            score = f"{row['composite_score']:.2f}"
            conf = str(row["confidence"])
            buy = str(row["buy_signal"]) if row["buy_signal"] else "-"
            sell = str(row["sell_signal"]) if row["sell_signal"] else "-"
            lines.append(
                f"| {rank} | {ts_code} | {stock_name} | {etf_code} "
                f"| {theme} | {lifecycle} | {score} | {conf} | {buy} | {sell} |"
            )

        lines.append("")
        return "\n".join(lines)

    def generate_json_export(self, results: List, file_path: str = None) -> str:
        """Export results as JSON file. Returns file path."""
        if file_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = os.path.join(self.output_dir, f"mainline_export_{timestamp}.json")

        os.makedirs(os.path.dirname(file_path) or self.output_dir, exist_ok=True)

        if not results:
            logger.warning("generate_json_export received empty results")
            data = {
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_count": 0,
                "results": [],
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Empty JSON export saved to {file_path}")
            return file_path

        records = []
        for r in results:
            d = asdict(r)
            # Ensure numeric types are native Python types for JSON serialization
            record = {}
            for k, v in d.items():
                if isinstance(v, (np.integer,)):
                    record[k] = int(v)
                elif isinstance(v, (np.floating,)):
                    record[k] = float(v)
                elif isinstance(v, (np.bool_,)):
                    record[k] = bool(v)
                else:
                    record[k] = v
            records.append(record)

        # Compute summary
        df = self.to_dataframe(results)
        conf_counts = df["confidence"].value_counts()

        data = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": len(results),
            "confidence_distribution": {
                "high": int(conf_counts.get("high", 0)),
                "medium": int(conf_counts.get("medium", 0)),
                "low": int(conf_counts.get("low", 0)),
            },
            "results": records,
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON export saved to {file_path}")
        return file_path

    def generate_theme_summary(
        self, results: List, theme_data: Dict
    ) -> pd.DataFrame:
        """Summary by theme: avg composite, count, best stock, lifecycle stage."""
        if not results:
            logger.warning("generate_theme_summary received empty results")
            return pd.DataFrame(columns=[
                "theme", "avg_composite", "count", "best_stock",
                "best_composite", "lifecycle_stage",
            ])

        df = self.to_dataframe(results)
        if df.empty:
            return pd.DataFrame(columns=[
                "theme", "avg_composite", "count", "best_stock",
                "best_composite", "lifecycle_stage",
            ])

        grouped = df.groupby("theme", sort=False)
        rows = []
        for theme_name, group in grouped:
            best_idx = group["composite_score"].idxmax()
            best_row = group.loc[best_idx]
            best_stock = f"{best_row['ts_code']} ({best_row['stock_name']})" if best_row['stock_name'] else best_row['ts_code']

            # Determine lifecycle stage (most common)
            stages = group["lifecycle_stage"].value_counts()
            dominant_stage = stages.index[0] if not stages.empty else "Unknown"

            rows.append({
                "theme": theme_name if theme_name else "(unassigned)",
                "avg_composite": round(float(group["composite_score"].mean()), 2),
                "count": int(len(group)),
                "best_stock": best_stock,
                "best_composite": round(float(best_row["composite_score"]), 2),
                "lifecycle_stage": dominant_stage,
            })

        result = pd.DataFrame(rows)
        if not result.empty:
            result = result.sort_values("avg_composite", ascending=False).reset_index(drop=True)
        return result

    def generate_etf_summary(
        self, results: List, etf_scores: Dict
    ) -> pd.DataFrame:
        """Summary by ETF: avg composite, count, trend score, top stock."""
        if not results:
            logger.warning("generate_etf_summary received empty results")
            return pd.DataFrame(columns=[
                "etf_code", "etf_name", "avg_composite", "count",
                "etf_trend_score", "top_stock",
            ])

        df = self.to_dataframe(results)
        if df.empty:
            return pd.DataFrame(columns=[
                "etf_code", "etf_name", "avg_composite", "count",
                "etf_trend_score", "top_stock",
            ])

        grouped = df.groupby("etf_code", sort=False)
        rows = []
        for etf_code, group in grouped:
            etf_name = group["etf_name"].iloc[0] if group["etf_name"].iloc[0] else ""

            best_idx = group["composite_score"].idxmax()
            best_row = group.loc[best_idx]
            top_stock = f"{best_row['ts_code']} ({best_row['stock_name']})" if best_row['stock_name'] else best_row['ts_code']

            # Average etf_trend from the group
            avg_etf_trend = round(float(group["etf_trend"].mean()), 2)

            # Look up from external etf_scores if available
            etf_trend_score = avg_etf_trend
            if etf_scores and etf_code in etf_scores:
                score_obj = etf_scores[etf_code]
                external_trend = getattr(
                    score_obj, "score",
                    getattr(score_obj, "etf_trend_score", None),
                )
                if external_trend is not None:
                    etf_trend_score = round(float(external_trend), 2)

            rows.append({
                "etf_code": etf_code,
                "etf_name": etf_name,
                "avg_composite": round(float(group["composite_score"].mean()), 2),
                "count": int(len(group)),
                "etf_trend_score": etf_trend_score,
                "top_stock": top_stock,
            })

        result = pd.DataFrame(rows)
        if not result.empty:
            result = result.sort_values("avg_composite", ascending=False).reset_index(drop=True)
        return result

    @staticmethod
    def format_confidence(composite_score: float) -> str:
        """Return confidence level and color."""
        if composite_score >= 80.0:
            return "high (#38a169)"
        elif composite_score >= 60.0:
            return "medium (#d69e2e)"
        else:
            return "low (#e53e3e)"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _empty_html(self, title: str) -> str:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f7fa; color: #333; padding: 24px; text-align: center; }}
.container {{ max-width: 600px; margin: 80px auto; }}
.card {{ background: #fff; border-radius: 10px; padding: 40px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
h1 {{ font-size: 20px; color: #2d3748; margin-bottom: 12px; }}
p {{ color: #718096; }}
</style>
</head>
<body>
<div class="container">
<div class="card">
<h1>{title}</h1>
<p>\u6682\u65e0\u6570\u636e</p>
<p style="font-size: 12px; color: #a0aec0; margin-top: 16px;">{generated_at}</p>
</div>
</div>
</body>
</html>"""

    def _build_html_table_rows(self, display: pd.DataFrame) -> str:
        rows = []
        for _, row in display.iterrows():
            rank = int(row["rank"])
            ts_code = str(row["ts_code"])
            stock_name = str(row["stock_name"]) if row["stock_name"] else "-"
            etf_code = str(row["etf_code"]) if row["etf_code"] else "-"
            theme = str(row["theme"]) if row["theme"] else "-"
            lifecycle = str(row["lifecycle_stage"])

            score = row["composite_score"]
            conf = str(row["confidence"])
            if conf == "high":
                conf_html = f'<span class="conf-high">\u9ad8</span>'
            elif conf == "medium":
                conf_html = f'<span class="conf-medium">\u4e2d</span>'
            else:
                conf_html = f'<span class="conf-low">\u4f4e</span>'

            buy = str(row["buy_signal"]) if row["buy_signal"] else ""
            sell = str(row["sell_signal"]) if row["sell_signal"] else ""
            buy_html = (
                f'<span class="signal-buy">\u2b06 {buy}</span>'
                if buy else '<span class="signal-none">-</span>'
            )
            sell_html = (
                f'<span class="signal-sell">\u2b07 {sell}</span>'
                if sell else '<span class="signal-none">-</span>'
            )

            rows.append(
                f"<tr>"
                f"<td>{rank}</td>"
                f"<td>{ts_code}</td>"
                f"<td>{stock_name}</td>"
                f"<td>{etf_code}</td>"
                f"<td><span class=\"theme-badge\">{theme}</span></td>"
                f"<td>{lifecycle}</td>"
                f"<td class=\"text-right\">{score:.2f}</td>"
                f"<td class=\"text-center\">{conf_html}</td>"
                f"<td class=\"text-center\">{buy_html}</td>"
                f"<td class=\"text-center\">{sell_html}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    @staticmethod
    def _build_html_theme_stats(theme_avg: pd.DataFrame) -> str:
        if theme_avg.empty:
            return "<tr><td colspan=\"3\" style=\"text-align:center;color:#a0aec0;\">\u6682\u65e0\u6570\u636e</td></tr>"
        rows = []
        for theme_name, row in theme_avg.iterrows():
            theme_display = theme_name if theme_name else "(unassigned)"
            rows.append(
                f"<tr>"
                f"<td><span class=\"theme-badge\">{theme_display}</span></td>"
                f"<td class=\"text-right\">{row['mean']:.2f}</td>"
                f"<td class=\"text-right\">{int(row['count'])}</td>"
                f"</tr>"
            )
        return "\n".join(rows)


def print_pipeline_summary(results: List, elapsed_time: float):
    """Print a concise pipeline summary to console.

    Shows: date, total candidates, top themes, top stocks,
           buy/sell counts, confidence distribution.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator = "=" * 64

    print()
    print(separator)
    print("  \u673a\u6784\u4e3b\u7ebf\u8bc6\u522b\u5f15\u64ce \u2014 \u7ba1\u7ebf\u6267\u884c\u6458\u8981")
    print(separator)
    print(f"  \u6267\u884c\u65f6\u95f4 : {timestamp}")
    print(f"  \u8017\u65f6       : {elapsed_time:.2f}s")
    print(f"  \u7ebf\u7d22\u603b\u6570 : {len(results)}")
    print(separator)

    if not results:
        print("  [\u65e0\u7ed3\u679c] \u6ca1\u6709\u7b26\u5408\u6761\u4ef6\u7684\u7ebf\u7d22\u3002")
        print(separator)
        print()
        return

    # Confidence distribution
    df = pd.DataFrame([asdict(r) for r in results])
    conf_counts = df["confidence"].value_counts() if "confidence" in df.columns else pd.Series(dtype=int)
    high = int(conf_counts.get("high", 0))
    medium = int(conf_counts.get("medium", 0))
    low = int(conf_counts.get("low", 0))

    total_confidence = high + medium + low
    high_pct = high / total_confidence * 100 if total_confidence > 0 else 0.0
    medium_pct = medium / total_confidence * 100 if total_confidence > 0 else 0.0
    low_pct = low / total_confidence * 100 if total_confidence > 0 else 0.0

    # Confidence distribution bar
    bar_width = 40
    high_bar = int(high_pct / 100 * bar_width) if high_pct > 0 else 1
    medium_bar = int(medium_pct / 100 * bar_width) if medium_pct > 0 else 1
    low_bar = bar_width - high_bar - medium_bar
    if low_bar < 0:
        low_bar = 0

    print(f"  \u4fe1\u5fc3\u5ea6\u5206\u5e03 :")
    print(f"    \u9ad8   : {high:4d} ({high_pct:5.1f}%) {'#' * high_bar}")
    print(f"    \u4e2d   : {medium:4d} ({medium_pct:5.1f}%) {'#' * medium_bar}")
    print(f"    \u4f4e   : {low:4d} ({low_pct:5.1f}%) {'#' * low_bar}")

    # Buy / sell signals
    buy_count = int((df["buy_signal"] != "").sum()) if "buy_signal" in df.columns else 0
    sell_count = int((df["sell_signal"] != "").sum()) if "sell_signal" in df.columns else 0
    print(f"  \u4e70\u5165\u4fe1\u53f7 : {buy_count}")
    print(f"  \u5356\u51fa\u4fe1\u53f7 : {sell_count}")

    # Top themes by average composite score
    if "theme" in df.columns and "composite_score" in df.columns:
        theme_ranking = (
            df.groupby("theme")["composite_score"]
            .agg(["mean", "count"])
            .round(2)
            .sort_values("mean", ascending=False)
            .head(5)
        )
        if not theme_ranking.empty:
            print(f"  \u4e3b\u9898\u6392\u540d :")
            for theme_name, row_data in theme_ranking.iterrows():
                theme_display = theme_name if theme_name else "(unassigned)"
                print(f"    {theme_display:20s}  avg={row_data['mean']:6.2f}  n={int(row_data['count'])}")

    # Top 5 stocks
    if "ts_code" in df.columns and "composite_score" in df.columns:
        top5 = df.nlargest(5, "composite_score")
        print(f"  Top-5 \u80a1\u7968  :")
        for _, row in top5.iterrows():
            name = str(row.get("stock_name", "")) or "-"
            score = row["composite_score"]
            print(f"    #{int(row['rank']):3d}  {row['ts_code']:10s}  {name:10s}  score={score:.2f}")

    print(separator)
    print()
