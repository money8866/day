# -*- coding: utf-8 -*-
"""
CRR V1.0 命令行入口

用法示例：
  python -m crr --date 20260722            # 扫描指定日期并输出报告
  python -m crr --date 20260722 --no-report
  python -m crr --audit                     # 数据源审计（实测本地字段与缺口）
  python -m crr --date 20260722 --top 50   # 控制台明细行数

约定：
  * 报告默认写到 output/crr/（config.REPORT_DIR）；
  * 退出码 0 = 正常完成；1 = 参数/数据错误；
  * 所有结论只有 BUY / WATCH / NO TRADE 三种。
"""
import argparse
import sys
import traceback

import numpy as np
import pandas as pd

from . import data as D
from . import engine as E
from . import report as RP
from .config import REPORT_DIR


def _print_audit(audit):
    """打印数据源审计结果。"""
    print("=" * 78)
    print(f"[审计] 扫描基准日: {audit.get('scan_date')}")
    print("=" * 78)
    print("\n[路径]")
    for k, v in (audit.get("paths") or {}).items():
        print(f"  {k:14s} {v}")

    print("\n[SQLite 表实测]")
    for name, info in (audit.get("tables") or {}).items():
        if not info.get("exists"):
            print(f"  {name:44s} 不存在")
            continue
        rng = ""
        for dc in ("trade_date", "ann_date", "end_date", "eval_date"):
            if dc in info:
                rng += f" {dc}={info.get(dc + '_min')}~{info.get(dc + '_max')}"
        print(f"  {name:44s} rows={info.get('rows'):>10,} cols={info.get('n_cols')}{rng}")

    print("\n[parquet 家族文件数]")
    for pat, n in (audit.get("parquet_files") or {}).items():
        print(f"  {pat:34s} {n}")

    print("\n[已知 UNKNOWN（无对应字段）]")
    for s in audit.get("unknowns") or []:
        print(f"  - {s}")

    print("\n[代理口径]")
    for s in audit.get("proxies") or []:
        print(f"  - {s}")

    print(f"\n[评分上限] {audit.get('score_cap')}")
    print("[未实现模块]")
    for s in audit.get("pending_modules") or []:
        print(f"  - {s}")


def _print_results(R, top):
    """控制台打印结论分组。"""
    s = R.get("summary", {}) or {}
    mkt = R.get("market", {}) or {}
    print("=" * 78)
    print(f"[扫描] {R.get('scan_date')}  市场状态: {mkt.get('state')}  "
          f"门控: {(R.get('meta') or {}).get('gate')}")
    print("=" * 78)
    print(f"候选 {s.get('n_candidates')} | 有效评分 {s.get('n_scored')} | "
          f"BUY {s.get('n_buy')} | WATCH {s.get('n_watch')} | NO TRADE {s.get('n_no_trade')}")
    print(f"最高总分 {s.get('max_total_score')} / 55 | 平均置信度 "
          f"{np.round((R.get('meta') or {}).get('mean_confidence', 0.0), 3)}")
    print()

    for label, key in (("BUY", "buy"), ("WATCH", "watch")):
        df = R.get(key)
        print(f"--- {label} ---")
        if df is None or df.empty:
            print("  无")
            print()
            continue
        show = df.head(top)
        cols = ["total_score", "confidence", "q_score", "v_score",
                "ret20", "specific_share", "decision_reason"]
        cols = [c for c in cols if c in show.columns]
        print(show[cols].to_string())
        print()

    nt = R.get("no_trade")
    if nt is not None and not nt.empty:
        print("--- NO TRADE（前 10，仅示例）---")
        cols = [c for c in ("total_score", "confidence", "decision_reason")
                if c in nt.columns]
        print(nt.head(10)[cols].to_string())
        print()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m crr",
        description="CRR V1.0：A股暴跌后优质错杀股识别（BUY/WATCH/NO TRADE）")
    ap.add_argument("--date", default=None,
                    help="扫描基准日 YYYYMMDD；缺省取本地最新交易日")
    ap.add_argument("--audit", action="store_true",
                    help="只做数据源审计并退出")
    ap.add_argument("--no-report", action="store_true",
                    help="不写 HTML/Markdown 报告文件")
    ap.add_argument("--top", type=int, default=30,
                    help="控制台打印 BUY/WATCH 明细行数（默认 30）")
    ap.add_argument("--out-dir", default=None,
                    help=f"报告输出目录（缺省 {REPORT_DIR}）")
    args = ap.parse_args(argv)

    try:
        if args.audit:
            audit = E.run_audit(args.date)
            _print_audit(audit)
            return 0

        sd = args.date or D.last_trade_date_on_or_before("20991231")
        if not sd:
            print("[错误] 本地 daily_cache 无可用交易日", file=sys.stderr)
            return 1
        print(f"[扫描] 开始，基准日 {sd} ...")
        R = E.run_scan(sd)
        _print_results(R, args.top)

        if not args.no_report:
            paths = RP.write_report(R, out_dir=args.out_dir)
            print(f"[报告] HTML : {paths['html']}")
            print(f"[报告] MD   : {paths['markdown']}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 顶层需要完整错误回显
        print(f"[错误] {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
