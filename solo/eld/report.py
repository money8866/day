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

        lines.append("## TOP 排行榜 (ELD V2)")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 行业 | 预告增幅 | V2分 | V1分 | 事件 | 预期差V2 | 趋势 | 机构吸筹 | 主题 | 买点信号 | 机构状态 | 建议V2 |")
        lines.append("|------|------|------|------|----------|------|------|------|----------|------|----------|------|----------|----------|--------|")

        top_n = min(self.rc.top_n, len(report.results))
        for r in report.results[:top_n]:
            d = r.to_dict()
            if self.rc.include_detail:
                lines.append(
                    f"| {d['rank']} | {d['ts_code']} | {d['name']} | {d['industry']} "
                    f"| {d['forecast_pct']:.0f}% | {d['final_score_v2']:.1f} | {d['final_score']:.1f} "
                    f"| {d['event_quality']:.0f} | {d['expectation_gap_v2']:.0f} "
                    f"| {d['trend']:.0f} | {d['institution_accumulation']:.0f} "
                    f"| {d['industry_score']:.0f} | {d['earnings_buy_signal']} "
                    f"| {d['institution_state']} | {d['recommendation_v2']} |"
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
                lines.append(f"- **ELS V2**: {r.els_v2:.1f} → **最终分V2**: {r.final_score_v2:.1f}")
                lines.append(f"- **ELS V1**: {r.els:.1f} → **最终分V1**: {r.final_score:.1f}")
                lines.append(f"- **建议(V2)**: {r.recommendation_v2}")
                lines.append("")

                # ELD V2 新增维度概要
                lines.append("#### ELD V2 新增维度")
                lines.append("")
                lines.append(f"- **预期差V2**: {r.expectation_gap_v2_score:.0f}分")
                if r.expectation_gap_v2_detail:
                    eg = r.expectation_gap_v2_detail
                    lines.append(f"  - 行业增速: {eg.industry_growth:+.1f}% | 公司增速: {eg.company_growth:+.1f}% | 超额: {eg.gap:+.1f}%")
                    lines.append(f"  - 加速度: {eg.acceleration:+.1f}% | 可比公司: {eg.peer_count}")
                lines.append(f"- **机构吸筹**: {r.institution_accumulation_score:.0f}分 | 状态: {r.institution_state}")
                if r.institution_accumulation_detail:
                    ia = r.institution_accumulation_detail
                    lines.append(f"  - 资金趋势: {ia.fund_flow_score:.0f} | 量价结构: {ia.volume_price_score:.0f} | 筹码变化: {ia.chip_change_score:.0f}")
                lines.append(f"- **业绩回踩买点**: {r.earnings_buy_signal} (评分: {r.earnings_buy_score:.0f})")
                if r.earnings_buy_point_detail:
                    ebp = r.earnings_buy_point_detail
                    lines.append(f"  - 距公告: {ebp.days_since_announce}天 | 回撤: {ebp.pullback_from_high_pct:.1f}% | 量比: {ebp.volume_ratio:.2f}")
                lines.append("")

                # V2 各维度评分逻辑
                lines.append("#### V2 评分详情")
                lines.append("")
                lines.append("| V2维度 | 评分 | 逻辑 |")
                lines.append("|--------|------|------|")
                v2_details = [
                    ("事件质量", r.event_detail),
                    ("预期差V2", r.expectation_gap_v2_detail),
                    ("趋势Alpha", r.trend_detail),
                    ("机构吸筹", r.institution_accumulation_detail),
                    ("行业主题", r.industry_detail),
                    ("ETF", None),
                ]
                for name, detail in v2_details:
                    if detail and detail.logic:
                        brief = detail.logic[0] if len(detail.logic) > 0 else ""
                        score = detail.score
                        lines.append(f"| {name} | {score:.0f} | {brief} |")
                    elif detail:
                        lines.append(f"| {name} | {detail.score:.0f} | - |")
                    else:
                        lines.append(f"| {name} | 50 | ETF评分默认中性 |")

                lines.append("")

                # 买点详情
                lines.append("#### 买点信号")
                lines.append("")
                if r.buy_point_detail:
                    lines.append(f"- **传统买点**: {r.buy_point_detail.state.value} (星级: {'★' * r.buy_point_detail.stars_int})")
                lines.append(f"- **业绩回踩买点**: {r.earnings_buy_signal}")
                if r.earnings_buy_point_detail and r.earnings_buy_point_detail.logic:
                    lines.append("  - 回踩逻辑:")
                    for log_line in r.earnings_buy_point_detail.logic[:5]:  # 只显示前5条
                        lines.append(f"    - {log_line}")
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

        cursor.execute("DROP TABLE IF EXISTS eld_results")
        cursor.execute("""
            CREATE TABLE eld_results (
                rank INTEGER,
                ts_code TEXT,
                name TEXT,
                industry TEXT,
                theme TEXT,
                announce_date TEXT,
                forecast_pct REAL,
                els REAL,
                els_v2 REAL,
                final_score REAL,
                final_score_v2 REAL,
                event_quality REAL,
                earnings REAL,
                institution REAL,
                chip REAL,
                trend REAL,
                industry_score REAL,
                freshness REAL,
                expectation_gap REAL,
                similarity REAL,
                expectation_gap_v2 REAL,
                institution_accumulation REAL,
                institution_state TEXT,
                earnings_buy_signal TEXT,
                earnings_buy_score REAL,
                buy_point TEXT,
                recommendation TEXT,
                recommendation_v2 TEXT,
                run_date TEXT
            )
        """)

        cursor.execute("DELETE FROM eld_results WHERE run_date = ?", (self._get_date_str(),))

        for r in report.results:
            d = r.to_dict()
            cursor.execute(
                """INSERT INTO eld_results VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (
                    d["rank"], d["ts_code"], d["name"], d["industry"], d.get("theme", ""),
                    d["announce_date"], d["forecast_pct"],
                    d["els"], d["els_v2"], d["final_score"], d["final_score_v2"],
                    d["event_quality"], d["earnings"], d["institution"],
                    d["chip"], d["trend"], d["industry_score"],
                    d["freshness"], d["expectation_gap"], d["similarity"],
                    d["expectation_gap_v2"], d["institution_accumulation"],
                    d["institution_state"], d["earnings_buy_signal"],
                    d["earnings_buy_score"],
                    d["buy_point"], d["recommendation"], d["recommendation_v2"],
                    self._get_date_str(),
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
