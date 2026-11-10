"""编排与 CLI —— 每日收盘后运行 / 指定历史日期回放 / 增量计算。

用法：
    python -m stock_linkage_discovery.pipeline --date 20261106
    python -m stock_linkage_discovery.pipeline --backfill 20260901 20261106
    python -m stock_linkage_discovery.pipeline --backfill 20261001 20261106 --force
    python -m stock_linkage_discovery.pipeline --self-test

增量：已存在 asof 快照默认跳过；--force 强制重算（先清理当日快照）。
回放：一次性载入面板，逐日严格 as-of 计算，快照逐日入库存档。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any

from . import attribution_engine, cluster_engine, data_layer, feature_engine
from . import linkage_engine, observation_engine, report_engine, residual_engine, snapshot_store
from .common import Timings, get_logger, setup_logging
from .config import apply_overrides, ensure_dirs, load_config


def run_day(cfg: dict[str, Any], panel: data_layer.Panel, asof: str,
            dates: list[str], resolver: data_layer.PITNameResolver,
            tag_maps: dict, force: bool = False, fast: bool = False) -> dict[str, Any]:
    log = get_logger("pipeline")
    if snapshot_store.has_snapshot(cfg, asof) and not force:
        log.info("%s 快照已存在，跳过（增量模式）", asof)
        return {"asof": asof, "status": "SKIPPED"}
    if force and snapshot_store.has_snapshot(cfg, asof):
        snapshot_store.delete_snapshot(cfg, asof)

    tm = Timings()
    snapshot_store.start_run(cfg, asof)
    quality: dict[str, Any] = {}
    try:
        with tm.measure("universe"):
            universe, uq = data_layer.build_universe(panel, asof, cfg, resolver)
        if len(universe) < cfg["cluster"]["min_size"]:
            raise RuntimeError(f"股票池仅 {len(universe)} 只，无法进行群体发现")

        with tm.measure("slice"):
            s = panel.slice(asof, universe, min(len(panel.dates), 200))
        with tm.measure("residual"):
            rr = residual_engine.compute_residuals(s, cfg)
        s.resid_mkt, s.resid_mkt_ind = rr.resid_mkt, rr.resid_mkt_ind
        with tm.measure("features"):
            fs = feature_engine.compute_features(s, cfg)
        with tm.measure("history"):
            hist = snapshot_store.load_history(cfg, dates, dates.index(asof))
        with tm.measure("network"):
            net = linkage_engine.build_network(
                s, fs, rr, cfg,
                history_edges=hist.prev_edges,
                history_edges_5d=hist.edges_5d,
                smooth_history=hist.smooth_edges)
        with tm.measure("clusters"):
            cs = cluster_engine.discover_clusters(net, s, fs, cfg, hist)
        with tm.measure("attribution"):
            driver_map = attribution_engine.analyze_clusters(cs, s, cfg, tag_maps)
            cs = cluster_engine.apply_driver_scores(cs, driver_map, cfg)
        with tm.measure("observation"):
            obs = observation_engine.cross_validate(cs, cfg, asof, dates)

        quality = {
            "panel": panel.quality,
            "universe": uq,
            "residual": rr.quality,
            "features": fs.meta,
            "network": net.stats,
            "clusters": cs.stats,
            "no_future_data": True,
            "synthetic_data": False,
        }
        def _total() -> float:
            return round(sum(v for k, v in tm.items.items() if k != "total"), 3)
        tm.items["total"] = _total()          # 报告页脚展示计算阶段耗时
        if fast:
            # 长历史回放只落快照，日报在需要时单独用 --date 重算
            paths = {"markdown": ""}
        else:
            with tm.measure("report"):
                paths = report_engine.render(cfg, asof, cs, net, obs, quality, tm.as_dict())
        with tm.measure("store"):
            snapshot_store.save_snapshot(cfg, asof, s, fs, net, cs, obs,
                                         tm.as_dict(), quality)
        tm.items["total"] = _total()          # 落库/日志里的完整总耗时
        # 日报目录里的质量报告在渲染时写出，当时还没有 report/store 耗时；
        # 落库完成后同步一次，保证与 run_log 的完整耗时一致。
        qpath = Path(cfg["paths"]["output_dir"]) / asof / "data_quality.json"
        if qpath.exists():
            import json
            from .common import write_json
            try:
                qdoc = json.loads(qpath.read_text(encoding="utf-8"))
                qdoc["timings"] = tm.as_dict()
                write_json(qpath, qdoc)
            except (ValueError, OSError) as qe:
                log.warning("质量报告耗时同步失败: %s", qe)
        snapshot_store.finish_run(cfg, asof, "OK", tm.as_dict(), quality, net.stats)
        log.info("[%s] 完成 %.1fs | 边 %d | 群体 %d | S/A=%d | %s",
                 asof, tm.total, len(net.edges), len(cs.clusters),
                 sum(1 for c in cs.clusters if c["grade"] in ("S", "A")),
                 "TODAY_NO_VALID_CLUSTER" if cs.flags["TODAY_NO_VALID_CLUSTER"] else "有候选")
        return {
            "asof": asof, "status": "OK", "edges": len(net.edges),
            "clusters": len(cs.clusters),
            "grades": cs.stats.get("grade_counts"),
            "no_valid": cs.flags["TODAY_NO_VALID_CLUSTER"],
            "baseline": cs.flags.get("BASELINE_SNAPSHOT"),
            "seconds": tm.total,
            "report": str(paths["markdown"]),
        }
    except Exception as e:
        tb = traceback.format_exc()
        log.error("[%s] 失败: %s\n%s", asof, e, tb)
        snapshot_store.finish_run(cfg, asof, "ERROR", tm.as_dict(), quality, {}, error=f"{e}\n{tb}")
        return {"asof": asof, "status": "ERROR", "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A股全市场个股动态联动发现引擎 V2.0")
    ap.add_argument("--date", help="运行单个历史/当日交易日，如 20261106")
    ap.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="回放区间")
    ap.add_argument("--force", action="store_true", help="强制重算已存在的快照")
    ap.add_argument("--reset", action="store_true",
                    help="清空全部历史快照后重放（保留 PIT 简称库；口径变更后使用）")
    ap.add_argument("--config", help="指定 linkage_config.json")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="覆盖任意配置，可多次；如 --set linkage.corr_threshold_20=0.60")
    ap.add_argument("--fast", action="store_true",
                    help="长历史回放时跳过日报渲染，只落网络快照（日报用 --date 单独重算）")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--self-test", action="store_true", help="跑单元测试（合成数据，不写入日报）")
    ap.add_argument("--backtest", nargs=2, metavar=("START", "END"),
                    help="对已存档群体事件做事后回测（T+1/3/5/10/20 + 对照组）")
    args = ap.parse_args(argv)

    if args.self_test:
        from .tests.test_core import run_tests
        return run_tests()

    cfg = apply_overrides(load_config(args.config), args.set)
    ensure_dirs(cfg)
    level = args.log_level or cfg["runtime"].get("log_level", "INFO")
    setup_logging(cfg["paths"]["log_dir"], level)
    log = get_logger("pipeline")
    log.info("配置 %s 指纹 %s", cfg["_config_path"], cfg["_config_hash"])

    if args.backtest:
        from . import backtest_engine
        import json
        res = backtest_engine.run_backtest(cfg, args.backtest[0], args.backtest[1])
        print(json.dumps({k: v for k, v in res.items() if k != "summary"},
                         ensure_ascii=False, indent=2, default=str))
        return 0 if res.get("status") == "OK" else 2

    snapshot_store.init_db(cfg)
    if args.reset:
        snapshot_store.reset_snapshots(cfg)
        log.warning("已清空历史快照，开始完整重放（保留 PIT 简称库）")

    if args.backfill:
        start, end = args.backfill
    elif args.date:
        start = end = args.date
    else:
        end = data_layer.trade_dates(cfg, "99991231", 1)[-1]
        start = end
        log.info("未指定日期，使用最近交易日 %s", end)

    all_dates = data_layer.trade_dates(cfg, end, min(200, int(cfg["data"].get("max_load_days", 420))))
    replay = [d for d in all_dates if str(start) <= d <= str(end)]
    if not replay:
        log.error("回放区间内无交易日: %s ~ %s", start, end)
        return 2
    log.info("回放 %d 个交易日: %s ~ %s", len(replay), replay[0], replay[-1])

    resolver = data_layer.PITNameResolver(cfg, data_layer.load_stock_basic(cfg))
    tag_maps = data_layer.load_tag_maps(cfg) if cfg["attribution"].get("enabled", True) else {}
    panel = data_layer.load_panel(cfg, all_dates[0], end, resolver)

    results = []
    for d in replay:
        results.append(run_day(cfg, panel, d, all_dates, resolver, tag_maps,
                               args.force, args.fast))
    ok = [r for r in results if r["status"] == "OK"]
    skipped = [r for r in results if r["status"] == "SKIPPED"]
    failed = [r for r in results if r["status"] == "ERROR"]
    log.info("回放汇总: OK=%d SKIP=%d ERROR=%d", len(ok), len(skipped), len(failed))
    for r in ok:
        log.info("  %s | 边=%s 群体=%s S/A=%s %.1fs | %s", r["asof"], r["edges"],
                 r["clusters"], (r["grades"] or {}).get("S", 0) + (r["grades"] or {}).get("A", 0),
                 r["seconds"], r["report"])
    for r in failed:
        log.error("  %s | %s", r["asof"], r["error"])
    print("\n=== SUMMARY ===")
    print(f"dates={len(replay)} ok={len(ok)} skipped={len(skipped)} failed={len(failed)}")
    if ok:
        last = ok[-1]
        print(f"last={last['asof']} edges={last['edges']} clusters={last['clusters']} "
              f"no_valid={last['no_valid']} report={last['report']}")
    if failed:
        print("failed_dates=" + ",".join(r["asof"] for r in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
