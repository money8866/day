# -*- coding: utf-8 -*-
"""MarketNode V1 命令行入口。

用法：
    python marketnode/run.py                 # 对最新交易日生成日报 + 回填节点历史
    python marketnode/run.py --date 20261023 # 指定交易日
    python marketnode/run.py --backfill      # 仅回填节点历史
    python marketnode/run.py --history       # 打印最近节点历史
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketnode import load_config
from marketnode.features import build_features
from marketnode.engine import MarketNodeEngine
from marketnode.report import write_report, save_node_history, send_report_mail

NODE_NAME = {
    "N1": "情绪冰点", "N2": "冰点修复", "N3": "增量启动",
    "N4": "主线扩张/主升确认", "N5": "极度高潮", "N6": "退潮确认",
}


def main():
    ap = argparse.ArgumentParser(description="A股大盘炒作节点识别引擎 V1")
    ap.add_argument("--date", type=str, default=None, help="交易日 YYYYMMDD，默认最新")
    ap.add_argument("--backfill", action="store_true", help="强制回填节点历史")
    ap.add_argument("--history", action="store_true", help="只打印最近节点历史")
    ap.add_argument("--mail", action="store_true", help="生成日报后发送邮件（HTML 22px 大字）")
    ap.add_argument("--mail-to", type=str, default=None, help="邮件收件人，默认取 config.mail.to")
    ap.add_argument("--config", type=str, default=None, help="配置文件路径")
    args = ap.parse_args()

    cfg = load_config(args.config)

    # 特征（缓存优先）
    feat = build_features(cfg)
    engine = MarketNodeEngine(cfg, feat)

    # 节点历史（缓存优先）
    out_dir = cfg["data"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "node_history.csv")
    meta_path = os.path.join(out_dir, "node_meta.json")
    if args.backfill or not os.path.exists(csv_path):
        print("[engine] 回填节点历史 ...")
        hist, meta = engine.backfill()
        save_node_history(hist, meta, cfg)
        print(f"[engine] 节点历史: {len(hist)} 条 -> {csv_path}")
    else:
        import pandas as pd
        hist = pd.read_csv(csv_path, dtype={"trade_date": str})

    if args.history:
        print("\n最近节点历史：")
        if len(hist):
            tail = hist.tail(15)
            for _, row in tail.iterrows():
                nm = NODE_NAME.get(row["node_type"], row["node_type"])
                print(f"  {row['trade_date']}  {row['node_type']:<3s}{nm}  "
                      f"{row['market_state']}/{row['sentiment_state']}  强度{row['node_score']}  "
                      f"{row['reason_codes']}")
        else:
            print("  （暂无）")
        return

    # 目标交易日
    trade_date = args.date if args.date else str(feat.index[-1])
    detect = engine.detect_date(trade_date)

    # 是否为新节点日（对比前一交易日节点）
    prev_node = engine.detect_date(feat.index[feat.index.get_loc(trade_date) - 1]).get("node_type") \
        if feat.index.get_loc(trade_date) > 0 else None
    is_new = detect["node_type"] is not None and detect["node_type"] != prev_node

    path, md = write_report(detect, feat, cfg)

    # 控制台摘要
    print(f"\n===== A股大盘节点日报 {trade_date} =====")
    if detect["node_type"]:
        nm = NODE_NAME[detect["node_type"]]
        tag = "新节点日" if is_new else "延续中"
        print(f"当前节点: {detect['node_type']} {nm}（{tag}）")
    else:
        print("当前节点: 无新节点")
    print(f"当前状态: {detect['market_state']}   情绪: {detect['sentiment_state']}   "
          f"强度: {detect['node_score']:.0f}   置信度: {detect['confidence']}")
    print(f"触发原因: {'、'.join(detect['reason_codes']) if detect['reason_codes'] else '—'}")
    if detect.get("missing_fields"):
        print(f"缺失字段(已降置信度): {', '.join(detect['missing_fields'])}")
    print(f"\n报告已生成: {path}")

    if args.mail:
        send_report_mail(path, cfg, to_addr=args.mail_to)


if __name__ == "__main__":
    main()