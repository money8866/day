"""
ELD V2 报告生成模块

支持 Markdown / CSV / SQLite / JSON 四种输出格式。
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Optional

from .config import get_config
from .constants import CSV_COLUMNS, VERSION, RELEASE_DATE
from .models import EldReport, FinalScoreResult

logger = logging.getLogger("eld.report")


class ReportGenerator:
    """报告生成器"""

    def __init__(self, config=None):
        self.cfg = config or get_config()
        self.rc = self.cfg.report
        self.output_dir = self.cfg.global_.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_date_str(self) -> str:
        """获取运行日期字符串"""
        return self.cfg.global_.target_date or datetime.now().strftime("%Y%m%d")

    def _file_path(self, suffix: str) -> str:
        """生成输出文件路径"""
        date_str = self._get_date_str()
        return os.path.join(self.output_dir, f"eld_report_{date_str}.{suffix}")

    # ──────────────────────────────────────────────
    # Markdown 报告
    # ──────────────────────────────────────────────
    def generate_markdown(self, report: EldReport) -> str:
        """生成 Markdown 格式报告"""
        lines = []
        date_str = self._get_date_str()

        lines.append(f"# ELD V2  Earnings Leader Report — {date_str}")
        lines.append("")
        lines.append(f"> 系统版本: {VERSION} | 发布日期: {RELEASE_DATE}")
        lines.append("")
        lines.append("## 运行概览")
        lines.append("")
        lines.append(f"- 扫描股票数: {report.total_stocks}")
        lines.append(f"- 通过过滤数: {report.filtered_stocks}")
        lines.append(f"- 市场状态: {report.market_regime}")
        lines.append("")

        if not report.results:
            lines.append("**⚠ 本日无符合条件标的**")
            lines.append("")
            return "\n".join(lines)

        lines.append("## TOP 排行榜")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 行业 | 预告增幅 | ELS | 最终分 | 事件 | 基本面 | 资金 | 筹码 | 趋势 | 行业 | 时效 | 预期差 | 相似度 | 买点 | 建议 |")
        lines.append("|------|------|------|------|----------|------|--------|------|--------|------|------|------|------|------|--------|--------|------|------|")

        top_n = min(self.rc.top_n, len(report.results))
        for r in report.results[:top_n]:
            d = r.to_dict()
            if self.rc.include_detail:
                lines.append(
                    f"| {d['rank']} | {d['ts_code']} | {d['name']} | {d['industry']} "
                    f"| {d['forecast_pct']:.0f}% | {d['els']:.1f} | {d['final_score']:.1f} "
                    f"| {d['event_quality']:.0f} | {d['earnings']:.0f} | {d['institution']:.0f} "
                    f"| {d['chip']:.0f} | {d['trend']:.0f} | {d['industry']:.0f} "
                    f"| {d['freshness']:.0f} | {d['expectation_gap']:.0f} | {d['similarity']:.0f} "
                    f"| {d['buy_point']} | {d['recommendation']} |"
                )

        lines.append("")

        # 详细分析（TOP 10）
        if self.rc.include_detail:
            lines.append("## 详细分析 (TOP 10)")
            lines.append("")

            for r in report.results[:10]:
                lines.append(f"### {r.rank}. {r.name} ({r.ts_code})")
                lines.append("")
                lines.append(f"- **行业**: {r.industry}")
                lines.append(f"- **公告日期**: {r.announce_date}")
                lines.append(f"- **预告增幅**: {r.forecast_pct:.1f}%")
                lines.append(f"- **ELS**: {r.els:.1f} → **最终分**: {r.final_score:.1f}")
                lines.append(f"- **建议**: {r.recommendation}")
                lines.append("")

                # 各维度评分
                lines.append("| 维度 | 评分 | 逻辑 |")
                lines.append("|------|------|------|")
                details = [
                    ("事件质量", r.event_detail),
                    ("基本面", r.earnings_detail),
                    ("机构资金", r.institution_detail),
                    ("筹码", r.chip_detail),
                    ("趋势", r.trend_detail),
                    ("行业", r.industry_detail),
                    ("公告时效", r.freshness_detail),
                    ("预期差", r.expectation_gap_detail),
                    ("历史相似度", r.similarity_detail),
                ]
                for name, detail in details:
                    if detail and detail.logic:
                        brief = detail.logic[0] if len(detail.logic) > 0 else ""
                        lines.append(f"| {name} | {detail.score:.0f} | {brief} |")
                    elif detail:
                        lines.append(f"| {name} | {detail.score:.0f} | - |")

                lines.append("")

                # 买点
                if r.buy_point_detail:
                    lines.append(f"**买点信号**: {r.buy_point_detail.state.value} (星级: {'★' * r.buy_point_detail.stars_int})")
                    if r.buy_point_detail.logic:
                        lines.append("- " + "\n- ".join(r.buy_point_detail.logic))
                    lines.append("")

                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # CSV 报告
    # ──────────────────────────────────────────────
    def generate_csv(self, report: EldReport) -> str:
        """生成 CSV 格式报告"""
        filepath = self._file_path("csv")

        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
            for r in report.results:
                d = r.to_dict()
                writer.writerow([d.get(col, "") for col in CSV_COLUMNS])

        logger.info("CSV report saved: %s", filepath)
        return filepath

    # ──────────────────────────────────────────────
    # SQLite 报告
    # ──────────────────────────────────────────────
    def generate_sqlite(self, report: EldReport) -> str:
        """生成 SQLite 数据库报告"""
        filepath = self._file_path("db")

        conn = sqlite3.connect(filepath)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eld_results (
                rank INTEGER,
                ts_code TEXT,
                name TEXT,
                industry TEXT,
                theme TEXT,
                announce_date TEXT,
                forecast_pct REAL,
                els REAL,
                final_score REAL,
                event_quality REAL,
                earnings REAL,
                institution REAL,
                chip REAL,
                trend REAL,
                industry_score REAL,
                freshness REAL,
                expectation_gap REAL,
                similarity REAL,
                buy_point TEXT,
                recommendation TEXT,
                run_date TEXT
            )
        """)

        cursor.execute("DELETE FROM eld_results WHERE run_date = ?", (self._get_date_str(),))

        for r in report.results:
            d = r.to_dict()
            cursor.execute(
                """INSERT INTO eld_results VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (
                    d["rank"], d["ts_code"], d["name"], d["industry"], d.get("theme", ""),
                    d["announce_date"], d["forecast_pct"], d["els"], d["final_score"],
                    d["event_quality"], d["earnings"], d["institution"],
                    d["chip"], d["trend"], d["industry"],
                    d["freshness"], d["expectation_gap"], d["similarity"],
                    d["buy_point"], d["recommendation"], self._get_date_str(),
                ),
            )

        conn.commit()
        conn.close()

        logger.info("SQLite report saved: %s", filepath)
        return filepath

    # ──────────────────────────────────────────────
    # JSON 报告
    # ──────────────────────────────────────────────
    def generate_json(self, report: EldReport) -> str:
        """生成 JSON 格式报告"""
        filepath = self._file_path("json")

        data = report.to_dict()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info("JSON report saved: %s", filepath)
        return filepath

    # ──────────────────────────────────────────────
    # 统一生成
    # ──────────────────────────────────────────────
    def generate_all(self, report: EldReport) -> dict[str, str]:
        """
        生成所有格式的报告

        Returns:
            {format: filepath} 字典
        """
        outputs: dict[str, str] = {}

        if self.rc.output_markdown:
            md_content = self.generate_markdown(report)
            md_path = self._file_path("md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            outputs["markdown"] = md_path
            logger.info("Markdown report saved: %s", md_path)

        if self.rc.output_csv:
            csv_path = self.generate_csv(report)
            outputs["csv"] = csv_path

        if self.rc.output_sqlite:
            db_path = self.generate_sqlite(report)
            outputs["sqlite"] = db_path

        if self.rc.output_json:
            json_path = self.generate_json(report)
            outputs["json"] = json_path

        return outputs
