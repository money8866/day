"""每日收盘后自动更新流水线（完整版）.

收盘后执行流程:
  1. 判定当前交易日期
  2. ETF 相关性分析 → 更新 theme_config.json + etf_mapping.json
  3. 概念热度分析 → 更新 concept_keywords / eastmoney_concepts / ths_concepts
  4. 龙虎榜分析 → 更新 leaders / core_stocks
  5. 机构研报分析 → 更新 relations / core_stocks
  6. 公告分析 → 更新 keywords
  7. 主营分析 → 更新 industry_weight / purity
  8. 产业链分析 → 更新 industry_chains / chain_relations
  9. 资金流向分析 → 更新 rotation / flow_summary
  10. 自动 git commit 配置变更

用法:
  python -m theme_kg_v3.main --daily
  python -m theme_kg_v3.main --daily-dry-run
  python -m theme_kg_v3.core.daily_pipeline --dry-run
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from theme_kg_v3.config.settings import (
    CONFIG_DIR,
    THEME_CONFIG_PATH,
    ETF_MAPPING_PATH,
    SW_INDUSTRY_MAPPING_PATH,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Git 自动提交
# ────────────────────────────────────────────────────────────

def _git_commit(
    trade_date: str,
    files: List[Path],
    dry_run: bool = False,
) -> bool:
    """自动 commit 配置文件变更.

    检查指定文件是否有改动，若有则 stage 并 commit.

    Args:
        trade_date: 交易日期 YYYYMMDD.
        files: 需要检查和提交的文件路径列表.
        dry_run: True 时仅打印不执行 git 命令.

    Returns:
        True 表示成功提交或无需提交; False 表示出错.
    """
    repo_root = CONFIG_DIR.parent.parent  # theme_kg_v3 项目根

    try:
        for f in files:
            if not f.exists():
                continue
            rel_path = f.relative_to(repo_root) if f.is_relative_to(repo_root) else f.name

            # 检查 staging area
            result = subprocess.run(
                ["git", "diff", "--quiet", "--", str(rel_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                logger.info("发现变更: %s", rel_path)
            else:
                # 检查 unstaged changes
                result2 = subprocess.run(
                    ["git", "diff", "--quiet", "HEAD", "--", str(rel_path)],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result2.returncode == 0:
                    logger.debug("无变更跳过: %s", rel_path)
                    continue

            if dry_run:
                logger.info("[DRY RUN] git add %s", rel_path)
            else:
                subprocess.run(
                    ["git", "add", str(rel_path)],
                    cwd=repo_root,
                    capture_output=True,
                    timeout=15,
                )
                logger.info("已 stage: %s", rel_path)

        # 检查是否有 staged 变更
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_root,
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("无配置变更需要提交")
            return True

        msg = f"chore(theme): 每日自动更新主题配置 [{trade_date}]"
        if dry_run:
            logger.info("[DRY RUN] git commit -m '%s'", msg)
        else:
            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=repo_root,
                capture_output=True,
                timeout=30,
            )
            logger.info("已提交: %s", msg)

        return True

    except subprocess.TimeoutExpired:
        logger.warning("git 操作超时")
        return False
    except FileNotFoundError:
        logger.info("未检测到 git，跳过自动提交")
        return True
    except Exception as e:
        logger.warning("git commit 失败: %s", e)
        return False


# ────────────────────────────────────────────────────────────
# 每日更新编排
# ────────────────────────────────────────────────────────────

def run_daily_update(
    trade_date: Optional[str] = None,
    skip_etf: bool = False,
    skip_concept: bool = False,
    skip_dragon_tiger: bool = False,
    skip_research: bool = False,
    skip_announcement: bool = False,
    skip_business: bool = False,
    skip_chain: bool = False,
    skip_flow: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行每日收盘后自动更新流水线（完整 8 步骤 + 自动 Commit）.

    Args:
        trade_date: 交易日期 (YYYYMMDD)，自动判定若为 None.
        skip_etf: 跳过 ETF 分析步骤.
        skip_concept: 跳过概念分析步骤.
        skip_dragon_tiger: 跳过龙虎榜分析步骤.
        skip_research: 跳过机构研报分析步骤.
        skip_announcement: 跳过公告分析步骤.
        skip_business: 跳过主营分析步骤.
        skip_chain: 跳过产业链分析步骤.
        skip_flow: 跳过资金流向分析步骤.
        dry_run: 仅打印不写文件/不提交.

    Returns:
        执行摘要字典.
    """
    from theme_kg_v3.core.etf_analyzer import (
        get_trade_date,
        run_etf_analysis as _run_etf,
    )
    from theme_kg_v3.core.concept_analyzer import (
        run_concept_analysis as _run_concept,
    )
    from theme_kg_v3.core.dragon_tiger_analyzer import (
        run_dragon_tiger_analysis as _run_dragon_tiger,
    )
    from theme_kg_v3.core.research_analyzer import (
        run_research_analysis as _run_research,
    )
    from theme_kg_v3.core.announcement_analyzer import (
        run_announcement_analysis as _run_announcement,
    )
    from theme_kg_v3.core.business_analyzer import (
        run_business_analysis as _run_business,
    )
    from theme_kg_v3.core.chain_analyzer import (
        run_chain_analysis as _run_chain,
    )
    from theme_kg_v3.core.flow_analyzer import (
        run_flow_analysis as _run_flow,
    )

    if trade_date is None:
        # 如果跳过了所有步骤，直接用今天的日期（无需 Tushare）
        all_skipped = all([skip_etf, skip_concept, skip_dragon_tiger,
                           skip_research, skip_announcement, skip_business,
                           skip_chain, skip_flow])
        if all_skipped:
            trade_date = datetime.now().strftime("%Y%m%d")
        else:
            trade_date = get_trade_date()

    logger.info("")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  自动更新流水线（完整版）")
    logger.info("║  交易日期: %s", trade_date)
    logger.info("║  执行时间: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if dry_run:
        logger.info("║  DRY RUN 模式 - 不会写入实际文件和提交")
    logger.info("╚" + "═" * 68 + "╝")

    summary: Dict[str, Any] = {
        "trade_date": trade_date,
        "dry_run": dry_run,
        "steps": {},
        "config_written": False,
        "mapping_written": False,
        "git_committed": False,
        "errors": [],
    }

    # ── Step 1: ETF 相关性分析 ──────────────────────────
    if not skip_etf:
        logger.info("")
        logger.info("▸ [Step 1/8] ETF 相关性分析 & 自动更新 keywords/leaders/relations/purity")
        logger.info("─" * 50)
        if dry_run:
            if THEME_CONFIG_PATH.exists():
                with open(THEME_CONFIG_PATH, encoding="utf-8") as f:
                    theme_config = json.load(f)
                total_etfs = sum(
                    len(cfg.get("etf_codes", []))
                    for cfg in theme_config.values()
                    if isinstance(cfg, dict)
                )
                logger.info("[DRY RUN] 将分析 %d 个主题的 %d+ 只 ETF", len(theme_config), total_etfs)
                summary["steps"]["etf"] = {"themes": len(theme_config), "status": "dry_run"}
        else:
            try:
                etf_result = _run_etf(trade_date=trade_date)
                summary["steps"]["etf"] = etf_result
                if etf_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("ETF 分析失败: %s", e)
                summary["errors"].append(f"ETF: {e}")
                summary["steps"]["etf"] = {"status": "failed", "error": str(e)}
    else:
        logger.debug("[Step 1/8] 已跳过")

    # ── Step 2: 概念热度分析 ────────────────────────────
    if not skip_concept:
        logger.info("")
        logger.info("▸ [Step 2/8] 概念热度分析 & 自动更新 keywords")
        logger.info("─" * 50)
        if not dry_run:
            try:
                concept_result = _run_concept(trade_date=trade_date)
                summary["steps"]["concept"] = concept_result
                if concept_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("概念分析失败: %s", e)
                summary["errors"].append(f"概念: {e}")
                summary["steps"]["concept"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析各主题关联概念的热度变化")
            summary["steps"]["concept"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 2/8] 已跳过")

    # ── Step 3: 龙虎榜分析 ──────────────────────────────
    if not skip_dragon_tiger:
        logger.info("")
        logger.info("▸ [Step 3/8] 龙虎榜分析 & 自动更新 leaders")
        logger.info("─" * 50)
        if not dry_run:
            try:
                dt_result = _run_dragon_tiger(trade_date=trade_date)
                summary["steps"]["dragon_tiger"] = dt_result
                if dt_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("龙虎榜分析失败: %s", e)
                summary["errors"].append(f"龙虎榜: {e}")
                summary["steps"]["dragon_tiger"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析当日龙虎榜数据")
            summary["steps"]["dragon_tiger"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 3/8] 已跳过")

    # ── Step 4: 机构研报分析 ────────────────────────────
    if not skip_research:
        logger.info("")
        logger.info("▸ [Step 4/8] 机构研报分析 & 自动更新 relations")
        logger.info("─" * 50)
        if not dry_run:
            try:
                research_result = _run_research(trade_date=trade_date)
                summary["steps"]["research"] = research_result
                if research_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("研报分析失败: %s", e)
                summary["errors"].append(f"研报: {e}")
                summary["steps"]["research"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析机构研报覆盖")
            summary["steps"]["research"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 4/8] 已跳过")

    # ── Step 5: 公告分析 ────────────────────────────────
    if not skip_announcement:
        logger.info("")
        logger.info("▸ [Step 5/8] 公告分析 & 自动更新 keywords")
        logger.info("─" * 50)
        if not dry_run:
            try:
                ann_result = _run_announcement(trade_date=trade_date)
                summary["steps"]["announcement"] = ann_result
                if ann_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("公告分析失败: %s", e)
                summary["errors"].append(f"公告: {e}")
                summary["steps"]["announcement"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析公司公告")
            summary["steps"]["announcement"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 5/8] 已跳过")

    # ── Step 6: 主营分析 ────────────────────────────────
    if not skip_business:
        logger.info("")
        logger.info("▸ [Step 6/8] 主营业务分析 & 自动更新 industry_weight / purity")
        logger.info("─" * 50)
        if not dry_run:
            try:
                biz_result = _run_business(trade_date=trade_date)
                summary["steps"]["business"] = biz_result
                if biz_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("主营分析失败: %s", e)
                summary["errors"].append(f"主营: {e}")
                summary["steps"]["business"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析主营业务构成")
            summary["steps"]["business"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 6/8] 已跳过")

    # ── Step 7: 产业链分析 ──────────────────────────────
    if not skip_chain:
        logger.info("")
        logger.info("▸ [Step 7/8] 产业链分析 & 自动更新 industry_chains / relations")
        logger.info("─" * 50)
        if not dry_run:
            try:
                chain_result = _run_chain(trade_date=trade_date)
                summary["steps"]["chain"] = chain_result
                if chain_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("产业链分析失败: %s", e)
                summary["errors"].append(f"产业链: {e}")
                summary["steps"]["chain"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析产业链分布")
            summary["steps"]["chain"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 7/8] 已跳过")

    # ── Step 8: 资金流向分析 ────────────────────────────
    if not skip_flow:
        logger.info("")
        logger.info("▸ [Step 8/8] 资金流向分析 & 自动更新 rotation / 轮动指标")
        logger.info("─" * 50)
        if not dry_run:
            try:
                flow_result = _run_flow(trade_date=trade_date)
                summary["steps"]["flow"] = flow_result
                if flow_result.get("config_updated"):
                    summary["config_written"] = True
            except Exception as e:
                logger.error("资金分析失败: %s", e)
                summary["errors"].append(f"资金: {e}")
                summary["steps"]["flow"] = {"status": "failed", "error": str(e)}
        else:
            logger.info("[DRY RUN] 将分析资金流向")
            summary["steps"]["flow"] = {"status": "dry_run"}
    else:
        logger.debug("[Step 8/8] 已跳过")

    # ── Final Step: Git 自动提交 ──────────────────────────
    logger.info("")
    logger.info("▸ [Final] Git 自动提交")
    logger.info("─" * 50)

    config_files = [
        THEME_CONFIG_PATH,
        ETF_MAPPING_PATH,
        SW_INDUSTRY_MAPPING_PATH,
    ]

    # 仅在非 dry-run 且配置有变更时提交
    has_changes = summary.get("config_written", False) or any(
        step.get("config_updated", False)
        for step in summary.get("steps", {}).values()
        if isinstance(step, dict)
    )

    if not dry_run and has_changes:
        try:
            commit_ok = _git_commit(trade_date, config_files, dry_run=False)
            summary["git_committed"] = commit_ok
        except Exception as e:
            logger.warning("Git 提交失败: %s", e)
            summary["errors"].append(f"Git: {e}")
    elif dry_run:
        # dry run 时仍执行 git 检查但仅打印
        _git_commit(trade_date, config_files, dry_run=True)
        summary["git_committed"] = True  # dry run 视为成功
    else:
        logger.info("无配置变更，跳过 Git 提交")
        summary["git_committed"] = True  # 无变更视为成功

    # ── 完成 ──────────────────────────────────────────────
    logger.info("")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  每日更新完成!")
    logger.info("║  Steps: %d/8", 8 - sum(1 for k in ["etf","concept","dragon_tiger","research",
                                                       "announcement","business","chain","flow"]
                                           if skip_etf and k=="etf" or
                                              skip_concept and k=="concept" or
                                              skip_dragon_tiger and k=="dragon_tiger" or
                                              skip_research and k=="research" or
                                              skip_announcement and k=="announcement" or
                                              skip_business and k=="business" or
                                              skip_chain and k=="chain" or
                                              skip_flow and k=="flow"))
    logger.info("║  Config written: %s", "✓" if summary["config_written"] else "-")
    logger.info("║  Git committed:  %s", "✓" if summary["git_committed"] else "-")
    if summary["errors"]:
        logger.info("║  Errors: %d", len(summary["errors"]))
        for e in summary["errors"]:
            logger.info("║    • %s", e)
    logger.info("╚" + "═" * 68 + "╝")

    return summary


# ────────────────────────────────────────────────────────────
# CLI 入口
# ────────────────────────────────────────────────────────────

def main() -> None:
    """每日流水线 CLI 入口."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Theme KG V3 - 每日收盘后自动更新流水线（完整版）",
    )
    parser.add_argument("--trade-date", type=str, default=None, help="指定交易日期 YYYYMMDD")
    parser.add_argument("--skip-etf", action="store_true", help="跳过 ETF 分析")
    parser.add_argument("--skip-concept", action="store_true", help="跳过概念分析")
    parser.add_argument("--skip-dragon-tiger", action="store_true", help="跳过龙虎榜分析")
    parser.add_argument("--skip-research", action="store_true", help="跳过机构研报分析")
    parser.add_argument("--skip-announcement", action="store_true", help="跳过公告分析")
    parser.add_argument("--skip-business", action="store_true", help="跳过主营分析")
    parser.add_argument("--skip-chain", action="store_true", help="跳过产业链分析")
    parser.add_argument("--skip-flow", action="store_true", help="跳过资金流向分析")
    parser.add_argument("--dry-run", action="store_true", help="仅打印预览，不写入/不提交")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("tushare").setLevel(logging.WARNING)

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
        dry_run=args.dry_run,
    )

    if summary["errors"]:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
