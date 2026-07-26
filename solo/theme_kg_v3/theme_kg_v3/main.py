"""Theme Knowledge Graph V3 - 系统主入口."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from theme_kg_v3.config.settings import (
    THEME_CONFIG_PATH,
    ETF_MAPPING_PATH,
    SW_INDUSTRY_MAPPING_PATH,
    SQLALCHEMY_DATABASE_URL,
)
from theme_kg_v3.core.keyword_engine import KeywordEngine
from theme_kg_v3.core.classifier import ThemeClassifier
from theme_kg_v3.core.confidence import ConfidenceScorer
from theme_kg_v3.core.lifecycle import LifecycleAnalyzer
from theme_kg_v3.core.leader import LeaderIdentifier
from theme_kg_v3.core.daily_pipeline import run_daily_update
from theme_kg_v3.db.connection import DatabaseManager, get_default_manager
from theme_kg_v3.db.repository import ThemeRepository
from theme_kg_v3.schema.dataclasses import (
    ClassificationResult,
    ConfidenceBreakdown,
    LifecycleResult,
    LeaderAnalysisResult,
    ThemeCreate,
    IndustryChainCreate,
)

logger = logging.getLogger(__name__)

# ── 生命周期阶段中文映射 ─────────────────────────────────────
_STAGE_CN: dict[str, str] = {
    "birth": "萌芽期",
    "growth": "成长期",
    "main_trend": "主升期",
    "distribution": "分歧/出货期",
    "death": "退潮期",
}

# ── 龙头类型中文映射 ─────────────────────────────────────────
_LEADER_TYPE_CN: dict[str, str] = {
    "leader": "龙头",
    "core": "核心/中军",
    "follower": "跟风",
    "catch_up": "补涨",
    "eliminated": "淘汰",
}


# ════════════════════════════════════════════════════════════
# 日志配置
# ════════════════════════════════════════════════════════════

def setup_logging() -> None:
    """配置日志输出格式到控制台."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # 清除已有 handler 避免重复添加
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # 关闭第三方库的噪音日志
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ════════════════════════════════════════════════════════════
# 主编排器
# ════════════════════════════════════════════════════════════

class ThemeKnowledgeGraph:
    """Theme Knowledge Graph V3 主编排器.

    串联关键词引擎、分类器、置信度评分、生命周期分析、龙-头识别
    以及数据库持久化，提供完整的主题知识图谱构建与分析流水线。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        """初始化各核心组件.

        Args:
            db_manager: 数据库管理器实例，未提供时使用全局默认单例.
        """
        # 核心引擎
        self.keyword_engine = KeywordEngine(THEME_CONFIG_PATH)
        self.confidence_scorer = ConfidenceScorer(
            keyword_engine=self.keyword_engine,
        )
        self.classifier = ThemeClassifier(
            keyword_engine=self.keyword_engine,
        )
        self.lifecycle_analyzer = LifecycleAnalyzer()
        self.leader_identifier = LeaderIdentifier()

        # 数据库
        self.db_manager = db_manager or get_default_manager()

        logger.info(
            "ThemeKnowledgeGraph initialized: %d themes loaded",
            len(self.keyword_engine.theme_codes),
        )

    # ──────────────────────────────────────────────
    # 数据库初始化
    # ──────────────────────────────────────────────

    def init_database(self, drop_first: bool = False) -> None:
        """初始化数据库表.

        Args:
            drop_first: 是否先删除已有表再创建.
        """
        if drop_first:
            logger.warning("正在删除所有数据库表...")
            self.db_manager.drop_db()
        logger.info("正在创建数据库表...")
        self.db_manager.init_db()
        logger.info("数据库表创建完成.")

    # ──────────────────────────────────────────────
    # 配置数据装载
    # ──────────────────────────────────────────────

    def load_config_to_db(self) -> int:
        """将 theme_config.json 中的主题配置加载到数据库.

        依次写入 theme、industry_chain、theme_etf、theme_keywords 表。

        Returns:
            加载的主题数量.
        """
        theme_config = self.keyword_engine.theme_config
        if not theme_config:
            logger.warning("theme_config.json 为空，跳过加载.")
            return 0

        # 加载 ETF 映射信息
        etf_mapping: dict[str, Any] = {}
        if ETF_MAPPING_PATH.exists():
            with open(ETF_MAPPING_PATH, encoding="utf-8") as f:
                etf_mapping = json.load(f)

        loaded_count = 0

        with self.db_manager.get_session() as session:
            for code, cfg in theme_config.items():
                if not isinstance(cfg, dict):
                    continue

                # 跳过已存在的主题
                existing = ThemeRepository.get_theme_by_code(session, code)
                if existing is not None:
                    logger.debug("主题 %s(%s) 已存在，跳过.", code, cfg.get("name_cn", ""))
                    continue

                # ── 1. 创建 Theme ─────────────────────
                theme_create = ThemeCreate(
                    code=code,
                    name_cn=cfg.get("name_cn", ""),
                    description=cfg.get("description", ""),
                    level=cfg.get("level", 2),
                    status="active" if cfg.get("is_active", True) else "inactive",
                    lifecycle_stage=None,
                    main_etf_code=cfg.get("main_etf", ""),
                )
                theme_resp = ThemeRepository.create_theme(session, theme_create)
                theme_id = theme_resp.id
                logger.info("创建主题: %s(%s) - id=%s", code, cfg.get("name_cn", ""), theme_id)

                # ── 2. 创建 IndustryChain ────────────
                chains = cfg.get("industry_chains", [])
                for idx, chain_name in enumerate(chains):
                    chain_code = f"{code}_{idx + 1:02d}"
                    chain_create = IndustryChainCreate(
                        theme_id=theme_id,
                        code=chain_code,
                        name_cn=chain_name,
                        description=f"{cfg.get('name_cn', '')} - {chain_name}",
                        sort_order=idx,
                    )
                    try:
                        ThemeRepository.create_industry_chain(session, chain_create)
                    except Exception as e:
                        logger.warning("创建产业链失败 %s: %s", chain_name, e)

                # ── 3. 创建 ThemeETF ─────────────────
                etf_info = etf_mapping.get(code, {})
                etf_codes_raw = cfg.get("etf_codes", [])
                main_etf = cfg.get("main_etf", "")

                # 优先从 etf_mapping.json 获取
                mapping_etf_list = etf_info.get("etf_codes", []) if isinstance(etf_info, dict) else []
                all_etf_codes = list(dict.fromkeys(etf_codes_raw + mapping_etf_list))

                for etf_code in all_etf_codes:
                    etf_name = etf_code  # 兜底
                    if isinstance(etf_info, dict):
                        # 尝试从 mapping 中获取名称
                        etf_name = etf_info.get("name", etf_code)
                    is_main = etf_code == main_etf

                    stmt = (
                        "INSERT INTO theme_etf (id, theme_id, etf_code, etf_name, is_main, weight, created_at) "
                        "VALUES (gen_random_uuid(), :theme_id, :etf_code, :etf_name, :is_main, :weight, NOW()) "
                        "ON CONFLICT (theme_id, etf_code) DO NOTHING"
                    )
                    session.execute(
                        stmt,
                        {
                            "theme_id": theme_id,
                            "etf_code": etf_code,
                            "etf_name": etf_name,
                            "is_main": is_main,
                            "weight": 1.0,
                        },
                    )

                # ── 4. 创建 ThemeKeyword ──────────────
                keyword_field_map: dict[str, str] = {
                    "keywords": "core",
                    "core_keywords": "core",
                    "industry_keywords": "industry",
                    "product_keywords": "product",
                    "concept_keywords": "concept",
                    "brand_keywords": "brand",
                }

                for field_name, kw_type in keyword_field_map.items():
                    kw_list = cfg.get(field_name, [])
                    for kw in kw_list:
                        if not kw or not kw.strip():
                            continue
                        stmt = (
                            "INSERT INTO theme_keywords "
                            "(id, theme_id, keyword, weight, keyword_type, is_exclude, created_at) "
                            "VALUES (gen_random_uuid(), :theme_id, :keyword, :weight, :keyword_type, FALSE, NOW()) "
                            "ON CONFLICT (theme_id, keyword) DO NOTHING"
                        )
                        session.execute(
                            stmt,
                            {
                                "theme_id": theme_id,
                                "keyword": kw.strip(),
                                "weight": 2.0 if kw_type == "core" else 1.0,
                                "keyword_type": kw_type,
                            },
                        )

                # ── 5. 创建排除关键词 ────────────────
                exclude_list = cfg.get("exclude_keywords", [])
                for kw in exclude_list:
                    if not kw or not kw.strip():
                        continue
                    stmt = (
                        "INSERT INTO theme_keywords "
                        "(id, theme_id, keyword, weight, keyword_type, is_exclude, created_at) "
                        "VALUES (gen_random_uuid(), :theme_id, :keyword, 1.0, 'exclude', TRUE, NOW()) "
                        "ON CONFLICT (theme_id, keyword) DO NOTHING"
                    )
                    session.execute(
                        stmt,
                        {"theme_id": theme_id, "keyword": kw.strip()},
                    )

                loaded_count += 1

        logger.info("配置加载完成，共加载 %d 个主题.", loaded_count)
        return loaded_count

    # ──────────────────────────────────────────────
    # 个股分类
    # ──────────────────────────────────────────────

    def classify_stock(self, stock_data: Dict[str, Any]) -> ClassificationResult:
        """对单只股票执行主题分类.

        Args:
            stock_data: 股票数据字典.

        Returns:
            分类结果.
        """
        return self.classifier.classify(stock_data)

    def classify_stocks_batch(
        self, stocks_data: List[Dict[str, Any]]
    ) -> List[ClassificationResult]:
        """批量分类多只股票，并将结果保存到数据库.

        Args:
            stocks_data: 股票数据字典列表.

        Returns:
            分类结果列表.
        """
        results = self.classifier.batch_classify(stocks_data)

        # 保存到数据库
        if results:
            with self.db_manager.get_session() as session:
                count = ThemeRepository.bulk_upsert_stock_themes(session, results)
                logger.info("批量保存 %d 条分类结果到 stock_theme 表.", count)

        return results

    # ──────────────────────────────────────────────
    # 生命周期分析
    # ──────────────────────────────────────────────

    def analyze_theme_lifecycle(
        self,
        theme_code: str,
        history_data: List[Dict[str, Any]],
    ) -> LifecycleResult:
        """分析主题的生命周期阶段.

        Args:
            theme_code: 主题代码.
            history_data: 历史日频数据列表.

        Returns:
            生命周期分析结果.
        """
        theme_name = self.keyword_engine.theme_config.get(theme_code, {}).get(
            "name_cn", theme_code
        )
        result = self.lifecycle_analyzer.analyze(theme_code, theme_name, history_data)

        # 保存到数据库
        try:
            with self.db_manager.get_session() as session:
                ThemeRepository.save_lifecycle_stage(session, result)
                # 同步更新 theme 表的 lifecycle_stage 字段
                theme = ThemeRepository.get_theme_by_code(session, theme_code)
                if theme is not None:
                    import uuid
                    ThemeRepository.update_theme(
                        session,
                        uuid.UUID(theme.id) if isinstance(theme.id, str) else theme.id,
                        {"lifecycle_stage": result.current_stage},
                    )
                logger.info(
                    "生命周期结果已保存: %s -> %s (%.1f%%)",
                    theme_code, result.current_stage, result.stage_confidence,
                )
        except Exception as e:
            logger.warning("保存生命周期结果失败: %s", e)

        return result

    # ──────────────────────────────────────────────
    # 龙头识别
    # ──────────────────────────────────────────────

    def analyze_theme_leaders(
        self,
        theme_code: str,
        stocks_data: List[Dict[str, Any]],
    ) -> LeaderAnalysisResult:
        """识别主题内的龙头/核心/跟风/补涨/淘汰股票.

        Args:
            theme_code: 主题代码.
            stocks_data: 主题成分股数据列表.

        Returns:
            龙头分析结果.
        """
        theme_name = self.keyword_engine.theme_config.get(theme_code, {}).get(
            "name_cn", theme_code
        )
        result = self.leader_identifier.identify(theme_code, theme_name, stocks_data)

        # 保存到数据库
        try:
            with self.db_manager.get_session() as session:
                ThemeRepository.save_leader_analysis(session, result)
                logger.info(
                    "龙头分析结果已保存: %s -> %d leaders, %d cores",
                    theme_code, len(result.leaders), len(result.cores),
                )
        except Exception as e:
            logger.warning("保存龙头分析结果失败: %s", e)

        return result

    # ──────────────────────────────────────────────
    # 主题报告生成
    # ──────────────────────────────────────────────

    def generate_theme_report(self, theme_code: str) -> str:
        """生成主题的综合分析文本报告.

        Args:
            theme_code: 主题代码.

        Returns:
            格式化文本报告.
        """
        lines: list[str] = []
        sep = "=" * 68

        # ── 1. 基础信息 ─────────────────────────────
        cfg = self.keyword_engine.theme_config.get(theme_code, {})
        theme_name = cfg.get("name_cn", theme_code)
        description = cfg.get("description", "")
        etf_codes = cfg.get("etf_codes", [])
        chains = cfg.get("industry_chains", [])

        lines.append(sep)
        lines.append(f"  主题报告: {theme_name} ({theme_code})")
        lines.append(sep)
        lines.append(f"  描述: {description}")
        lines.append("")

        # ETF 信息
        if etf_codes:
            etf_str = ", ".join(etf_codes)
            lines.append(f"  ETF: {etf_str}")
        lines.append("")

        # 产业链
        if chains:
            lines.append(f"  产业链 ({len(chains)} 个节点):")
            for i, ch in enumerate(chains, 1):
                lines.append(f"    {i:2d}. {ch}")
            lines.append("")

        # ── 2. 数据库实时数据 ───────────────────────
        lines.append("  ── 数据库状态 ──")
        try:
            with self.db_manager.get_session() as session:
                theme_db = ThemeRepository.get_theme_by_code(session, theme_code)
                if theme_db is None:
                    lines.append("  主题尚未入库，请先执行 load_config_to_db().")
                else:
                    # 生命周期
                    lifecycle = theme_db.lifecycle_stage or "未知"
                    stage_cn = _STAGE_CN.get(lifecycle, lifecycle)
                    lines.append(f"  生命周期阶段: {stage_cn} ({lifecycle})")

                    # 统计信息
                    import uuid
                    tid = uuid.UUID(theme_db.id) if isinstance(theme_db.id, str) else theme_db.id
                    stats = ThemeRepository.get_theme_statistics(session, tid)
                    lines.append(
                        f"  成分股: {stats['total_stocks']} 只"
                        f"  |  龙头: {stats['leader_count']}"
                        f"  |  核心: {stats['core_count']}"
                        f"  |  平均置信度: {stats['avg_confidence']:.1f}"
                    )

                    # 各类型股票列表
                    leader_type_map = {
                        "leader": "龙头股",
                        "core": "核心/中军",
                        "follower": "跟风股",
                        "catch_up": "补涨候选",
                    }
                    for ltype, label in leader_type_map.items():
                        stocks = ThemeRepository.get_stocks_by_leader_type(
                            session, tid, ltype,
                        )
                        if stocks:
                            names = [f"{s.stock_name}({s.stock_code})" for s in stocks[:5]]
                            lines.append(f"  {label}: {', '.join(names)}")
                            if len(stocks) > 5:
                                lines.append(f"    ... 及其他 {len(stocks) - 5} 只")

                    # 最近更新
                    if stats.get("last_updated"):
                        lines.append(f"  最近更新: {stats['last_updated']}")
        except Exception as e:
            lines.append(f"  (查询数据库失败: {e})")

        lines.append("")
        lines.append(sep)
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # 全流水线
    # ──────────────────────────────────────────────

    def run_pipeline(
        self, stocks_data_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """运行完整的主题知识图谱构建流水线.

        流程: init DB -> load config -> classify stocks -> lifecycle -> leaders.

        Args:
            stocks_data_path: 股票数据 JSON 文件路径，None 时使用内置示例数据.

        Returns:
            流水线执行统计摘要.
        """
        summary: Dict[str, Any] = {
            "themes_loaded": 0,
            "stocks_classified": 0,
            "lifecycles_analyzed": 0,
            "leaders_identified": 0,
            "errors": [],
        }

        # Step 1: 初始化数据库
        logger.info("[Pipeline] Step 1/4: 初始化数据库...")
        try:
            self.init_database(drop_first=False)
        except Exception as e:
            logger.error("数据库初始化失败: %s", e)
            summary["errors"].append(f"DB init: {e}")
            return summary

        # Step 2: 加载配置
        logger.info("[Pipeline] Step 2/4: 加载主题配置到数据库...")
        try:
            summary["themes_loaded"] = self.load_config_to_db()
        except Exception as e:
            logger.error("配置加载失败: %s", e)
            summary["errors"].append(f"Config load: {e}")

        # Step 3: 批量分类
        logger.info("[Pipeline] Step 3/4: 批量分类个股...")
        try:
            # 确定数据文件路径
            if stocks_data_path is None:
                data_dir = Path(__file__).resolve().parent / "data"
                stocks_data_path = str(data_dir / "sample_stocks.json")

            stocks_path = Path(stocks_data_path)
            if not stocks_path.exists():
                logger.warning("股票数据文件不存在: %s", stocks_path)
                summary["errors"].append(f"File not found: {stocks_data_path}")
            else:
                with open(stocks_path, encoding="utf-8") as f:
                    stocks_data = json.load(f)

                if not isinstance(stocks_data, list):
                    logger.warning("股票数据格式错误，期望 JSON 数组.")
                    summary["errors"].append("Invalid stock data format")
                else:
                    results = self.classify_stocks_batch(stocks_data)
                    summary["stocks_classified"] = len(results)

                    # 打印分类结果摘要
                    matched = sum(1 for r in results if r.primary_theme_code)
                    logger.info(
                        "分类完成: %d/%d 只股票匹配到主题",
                        matched, len(results),
                    )

                    # 按主题统计
                    theme_counts: dict[str, int] = {}
                    for r in results:
                        if r.primary_theme_code:
                            theme_counts[r.primary_theme_code] = \
                                theme_counts.get(r.primary_theme_code, 0) + 1
                    for tc, cnt in sorted(theme_counts.items(), key=lambda x: -x[1]):
                        tn = self.keyword_engine.theme_config.get(tc, {}).get("name_cn", tc)
                        logger.info("  %s(%s): %d 只", tn, tc, cnt)
        except Exception as e:
            logger.error("批量分类失败: %s", e)
            summary["errors"].append(f"Classify: {e}")

        # Step 4: 生命周期 & 龙头分析（简版，使用分类结果的统计）
        logger.info("[Pipeline] Step 4/4: 分析生命周期（需历史数据，跳过默认执行）...")
        logger.info("[Pipeline] 流水线执行完成.")

        return summary


# ════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════

def main() -> None:
    """命令行入口."""
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Theme Knowledge Graph V3 - 主题知识图谱系统",
    )
    parser.add_argument(
        "--init-db", action="store_true",
        help="初始化数据库表",
    )
    parser.add_argument(
        "--drop-first", action="store_true",
        help="与 --init-db 配合使用，先删除已有表再创建",
    )
    parser.add_argument(
        "--classify", type=str, metavar="PATH",
        help="对指定 JSON 文件中的股票数据进行分类",
    )
    parser.add_argument(
        "--report", type=str, metavar="THEME_CODE",
        help="生成指定主题的分析报告",
    )
    parser.add_argument(
        "--pipeline", action="store_true",
        help="运行完整流水线（init DB + load config + classify）",
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="使用内置示例数据运行分类",
    )
    parser.add_argument(
        "--daily", action="store_true",
        help="运行每日收盘后自动更新流水线（完整8步骤 → 配置更新 → git commit）",
    )
    parser.add_argument(
        "--daily-dry-run", action="store_true",
        help="每日更新流水线预览模式（不实际写入/提交）",
    )
    parser.add_argument(
        "--trade-date", type=str, default=None,
        help="指定交易日期 YYYYMMDD（用于 --daily）",
    )
    parser.add_argument("--skip-etf", action="store_true", help="跳过 ETF 分析")
    parser.add_argument("--skip-concept", action="store_true", help="跳过概念分析")
    parser.add_argument("--skip-dragon-tiger", action="store_true", help="跳过龙虎榜分析")
    parser.add_argument("--skip-research", action="store_true", help="跳过机构研报分析")
    parser.add_argument("--skip-announcement", action="store_true", help="跳过公告分析")
    parser.add_argument("--skip-business", action="store_true", help="跳过主营分析")
    parser.add_argument("--skip-chain", action="store_true", help="跳过产业链分析")
    parser.add_argument("--skip-flow", action="store_true", help="跳过资金流向分析")

    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        return

    kg = ThemeKnowledgeGraph()

    # ── init-db ─────────────────────────────────
    if args.init_db:
        kg.init_database(drop_first=args.drop_first)
        print("数据库初始化完成.")

    # ── pipeline ─────────────────────────────────
    if args.pipeline:
        print("开始执行完整流水线...")
        summary = kg.run_pipeline()
        print(f"\n流水线执行摘要:")
        print(f"  加载主题: {summary['themes_loaded']}")
        print(f"  分类个股: {summary['stocks_classified']}")
        if summary["errors"]:
            print(f"  错误: {summary['errors']}")

    # ── classify ─────────────────────────────────
    classify_path = args.classify
    if args.sample:
        data_dir = Path(__file__).resolve().parent / "data"
        classify_path = str(data_dir / "sample_stocks.json")

    if classify_path:
        path = Path(classify_path)
        if not path.exists():
            print(f"错误: 文件不存在 {path}", file=sys.stderr)
            sys.exit(1)

        with open(path, encoding="utf-8") as f:
            stocks_data = json.load(f)

        results = kg.classify_stocks_batch(stocks_data)

        print(f"\n分类结果 ({len(results)} 只股票):")
        print("-" * 60)
        for r in results:
            theme_info = (
                f"{r.primary_theme_name}({r.primary_theme_code})"
                if r.primary_theme_code else "未匹配"
            )
            print(
                f"  {r.stock_code} {r.stock_name:<8s}"
                f"  -> {theme_info}"
                f"  [置信度: {r.confidence:.1f}]"
            )

    # ── daily ────────────────────────────────────
    if args.daily or args.daily_dry_run:
        print("\n开始执行每日收盘后自动更新流水线（完整8步骤）...\n")
        summary = run_daily_update(
            trade_date=args.trade_date,
            skip_etf=args.skip_etf,
            skip_concept=args.skip_concept,
            skip_dragon_tiger=args.skip_dragon_tiger,
            skip_research=args.skip_research,
            skip_announcement=args.skip_announcement,
            skip_business=args.skip_business,
            skip_chain=args.skip_chain,
            skip_flow=args.skip_flow,
            dry_run=args.daily_dry_run,
        )
        print(f"\n每日更新摘要:")
        steps = summary.get("steps", {})
        for step_name, step_data in steps.items():
            if isinstance(step_data, dict):
                status = step_data.get("status", step_data.get("config_updated", False))
                print(f"  {step_name}: {'✓' if status else '-'}")
        print(f"  配置写入:   {'✓' if summary.get('config_written') else '-'}")
        print(f"  Git提交:    {'✓' if summary.get('git_committed') else '-'}")
        if summary.get("errors"):
            print(f"  错误:       {len(summary['errors'])} 个")
            for e in summary["errors"]:
                print(f"    - {e}")

    # ── report ───────────────────────────────────
    if args.report:
        report = kg.generate_theme_report(args.report)
        print(report)


if __name__ == "__main__":
    main()
