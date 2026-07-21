#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V8.0 实测验证脚本 — 用20260720真实数据跑完整V8.0管线
"""
import os, sys, json, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")

import config
import theme_builder as tb
import data_loader as dl
from v8_theme_rhythm import calculate_v8_theme_score

TRADE_DATE = "20260720"

def main():
    print("=" * 70)
    print("  V8.0 主题生命周期节奏与高确定性中军交易指导系统")
    print("  ----- 真实数据验证 (20260720) -----")
    print("=" * 70)

    # 1. 加载主题池
    print(f"\n[1/5] 加载主题池...")
    universe = tb.build_theme_universe()
    print(f"      主题数量: {len(universe)}")

    dt = datetime.strptime(TRADE_DATE, "%Y%m%d")
    start_date = (dt - timedelta(days=config.LOOKBACK_DAYS)).strftime("%Y%m%d")

    # 2. 加载日线数据
    print(f"[2/5] 加载日线数据 ({start_date} ~ {TRADE_DATE})...")
    all_codes = list(set(sum(universe.values(), [])))
    daily = dl.load_daily(all_codes, start_date, TRADE_DATE)
    print(f"      日线数据: {len(daily)} 条, {daily['ts_code'].nunique()} 只股票")

    # 3. 加载辅助数据
    print(f"[3/5] 加载辅助数据...")
    daily_basic = dl.load_daily_basic(TRADE_DATE)
    print(f"      daily_basic: {len(daily_basic)} 条")
    moneyflow = dl.load_moneyflow_by_date(TRADE_DATE)
    print(f"      moneyflow: {len(moneyflow)} 条")

    # 4. 构建V8数据
    print(f"[4/5] 构建V8.0输入数据...")
    v8_data = daily.copy()
    if not daily_basic.empty:
        v8_data = v8_data.merge(
            daily_basic[["ts_code", "turnover_rate", "circ_mv"]],
            on="ts_code", how="left"
        )
    else:
        v8_data["turnover_rate"] = np.nan
        v8_data["circ_mv"] = np.nan

    if not moneyflow.empty:
        mf_today = moneyflow[moneyflow["trade_date"] == TRADE_DATE].copy()
        if not mf_today.empty:
            mf_today["net_money_flow"] = mf_today["net_mf_amount"] * 1e4
            mf_today["net_money_flow_main"] = (
                (mf_today["buy_lg_amount"] + mf_today["buy_elg_amount"] -
                 mf_today["sell_lg_amount"] - mf_today["sell_elg_amount"])
            ) * 1e4
            v8_data = v8_data.merge(
                mf_today[["ts_code", "net_money_flow", "net_money_flow_main"]],
                on="ts_code", how="left"
            )

    theme_rows = []
    for tname, codes in universe.items():
        for code in codes:
            sub = v8_data[v8_data["ts_code"] == code].copy()
            if not sub.empty:
                sub["theme"] = tname
                theme_rows.append(sub)

    if not theme_rows:
        print("[Error] 无主题数据")
        return

    v8_df = pd.concat(theme_rows, ignore_index=True)
    print(f"      V8输入数据: {len(v8_df)} 行, {v8_df['theme'].nunique()} 个主题, "
          f"{v8_df['ts_code'].nunique()} 只股票")

    # 5. 运行V8.0
    print(f"[5/5] 运行V8.0评分引擎...")
    v8_result, center_df, trading_card = calculate_v8_theme_score(v8_df)

    # 6. 保存结果
    v8_json = config.OUTPUT_JSON.replace(".json", f"_v8_{TRADE_DATE}.json")
    v8_csv = config.OUTPUT_CSV.replace(".csv", f"_v8_{TRADE_DATE}.csv")
    v8_result.to_json(v8_json, orient="records", force_ascii=False, indent=2)
    v8_result.to_csv(v8_csv, index=False, encoding="utf-8-sig")
    print(f"      V8结果已保存: {v8_json}")

    if not center_df.empty:
        center_csv = config.OUTPUT_CSV.replace(".csv", f"_v8_center_{TRADE_DATE}.csv")
        center_df.to_csv(center_csv, index=False, encoding="utf-8-sig")
        print(f"      中军标的已保存: {center_csv}")

    card_file = os.path.join(BASE_DIR, "cache", f"trading_card_{TRADE_DATE}.md")
    with open(card_file, "w", encoding="utf-8") as f:
        f.write(trading_card)
    print(f"      指导卡已保存: {card_file}")

    # 7. 打印TOP 20结果
    print(f"\n{'='*100}")
    print(f"  V8.0 评分结果 (TOP 20) - {TRADE_DATE}")
    print(f"{'='*100}")
    display_cols = ["排名", "主题", "V7综合得分", "D阶段", "策略动作",
                    "T_start", "T_MA", "R_volume", "资金分", "梯队分", "趋势分", "基础分", "惩罚项说明"]
    display_cols = [c for c in display_cols if c in v8_result.columns]
    top20 = v8_result.head(20)
    print(f"  {'#':<3} {'主题':<18} {'V8分':<6} {'D阶段':<8} {'动作':<12} {'T_s':<4} {'T_M':<4} {'R_v':<6} {'资金':<5} {'梯队':<5} {'趋势':<5} {'基础':<5} {'惩罚'}")
    print(f"  {'-'*120}")
    for _, row in top20.iterrows():
        penalty_str = str(row.get("惩罚项说明", ""))[:20] if row.get("惩罚项说明") else ""
        print(f"  {row['排名']:<3} {row['主题']:<18} {row['V7综合得分']:<6.1f} "
              f"{row.get('D阶段',''):<8} {row.get('策略动作',''):<12} "
              f"{row.get('T_start',0):<4} {row.get('T_MA',0):<4} "
              f"{row.get('R_volume',0):<6.2f} {row['资金分']:<5.1f} {row['梯队分']:<5.1f} "
              f"{row['趋势分']:<5.1f} {row['基础分']:<5.1f} {penalty_str}")

    # 8. 打印中军标的
    if not center_df.empty:
        print(f"\n{'='*100}")
        print(f"  高确定性中军标的")
        print(f"{'='*100}")
        center_cols = ["主题", "主题排名", "D阶段", "ts_code", "自由流通市值(亿)",
                       "确定性得分", "均线多头天数", "Beta_theme", "近10日最大回撤%",
                       "低吸参考价", "防守止损位"]
        center_cols = [c for c in center_cols if c in center_df.columns]
        print(center_df[center_cols].to_string(index=False))

    # 9. 打印交易指导卡摘要
    print(f"\n{'='*100}")
    print(f"  ★ 次日实盘交易指导卡 (TOP 1)")
    print(f"{'='*100}")
    print(trading_card[:2000] + "\n...（完整版已保存至文件）")

    # 10. 阶段分布统计
    stage_counts = v8_result["D阶段"].value_counts()
    print(f"\n  D阶段分布:")
    for stage, cnt in stage_counts.items():
        pct = cnt / len(v8_result) * 100
        print(f"    {stage}: {cnt} 个 ({pct:.1f}%)")

    print(f"\n  {'='*100}")
    print(f"  V8.0 验证完成")
    print(f"  {'='*100}")

if __name__ == "__main__":
    main()