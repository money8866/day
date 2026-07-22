#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.2 - 主程序

V6.2 核心升级：从 Current Heat -> Future Alpha
  - 新增 Forward Alpha 预测模块（动量加速 + 反转张力 + 聪明钱背离）
  - 综合分权重重构：Forward Alpha 35%（最大权重）
  - 交易信号双层触发：Future Alpha 预测层 + Current Heat 确认层
  - 目标：预测未来5个交易日最可能成为市场主线的主题

V6.0 改进：
  - 全主题百分位排名 (Relative Momentum)
  - DC热度数据集成 (Sentiment)
  - moneyflow资金流 (Capital)
  - limit_list_d涨停数据 (Sentiment/Leader)
  - top_list/top_inst龙虎榜 (Leader/Risk)
  - daily_basic换手率 (Risk)
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
from trend import compute_trend_score, compute_momentum
from capital import compute_all_capital_scores
from sentiment import compute_sentiment_score
from persistence import compute_persistence_score
from lifecycle import identify_stage, stage_bonus
from leader import identify_leader
from risk import compute_risk_score
from continuation import compute_continuation_score, continuation_signal
from composite import compute_composite, trade_signal, confidence
from forward_alpha import compute_forward_alpha
from score_v7 import calculate_theme_score_v7
from v7_theme_scorer import calculate_v7_theme_score as calculate_v7_standalone
from v8_theme_rhythm import calculate_v8_theme_score


def main(trade_date=None):
    print("=" * 70)
    print("  Theme Alpha Engine V6.2 (Future Alpha)")
    print("  目标：预测未来5~20日最可能产生超额收益的主题主线")
    print("=" * 70)

    if trade_date is None:
        trade_date = dl.get_last_trade_date()
    print(f"\n  交易日: {trade_date}")

    dt = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (dt - timedelta(days=config.LOOKBACK_DAYS)).strftime("%Y%m%d")

    # ===== 第一步：加载主题池 =====
    print(f"\n[1/7] 加载主题池...")
    universe = tb.build_theme_universe()
    if not universe:
        print("[Error] 主题池为空")
        return []

    # ===== 第二步：加载日线数据 =====
    print(f"[2/7] 加载日线数据 ({start_date} ~ {trade_date})...")
    all_codes = list(set(sum(universe.values(), [])))
    daily = dl.load_daily(all_codes, start_date, trade_date)
    if daily.empty:
        print("[Error] 日线数据为空")
        return []
    print(f"      {len(daily)} 条记录, {daily['ts_code'].nunique()} 只股票")

    latest_day = daily["trade_date"].max()
    latest_daily = daily[daily["trade_date"] == latest_day]
    market_turnover = latest_daily["amount"].sum() / 1e8
    print(f"      最新交易日: {latest_day}, 全市场成交额: {market_turnover:.1f}亿")

    # ===== 第三步：加载辅助数据 =====
    print(f"[3/7] 加载辅助数据...")
    hs300 = dl.load_index("000300.SH", start_date, trade_date)
    index_ret = hs300.iloc[-1]["pct_chg"] if not hs300.empty else 0
    print(f"      沪深300收益: {index_ret:.2f}%")

    dc_hot = dl.load_dc_hot(trade_date)
    print(f"      DC热度: {'有' if not dc_hot.empty else '无'} ({len(dc_hot)} 条)")

    limit_df = dl.load_limit_list(trade_date)
    print(f"      涨停数据: {'有' if not limit_df.empty else '无'} ({len(limit_df)} 条)")

    top_df = dl.load_top_list(trade_date)
    top_inst = dl.load_top_inst(trade_date)
    print(f"      龙虎榜: {len(top_df)} 条, 机构: {len(top_inst)} 条")

    daily_basic = dl.load_daily_basic(trade_date)
    print(f"      daily_basic: {'有' if not daily_basic.empty else '无'}")

    moneyflow = dl.load_moneyflow_by_date(trade_date)
    print(f"      moneyflow: {'有' if not moneyflow.empty else '无'} ({len(moneyflow)} 条)")

    # ===== 第四步：计算全主题动量（用于百分位排名）=====
    print(f"[4/7] 计算全主题动量排名...")
    all_momentums = []
    for tname, codes in universe.items():
        r5, r10, r20, r40 = compute_momentum(daily, codes)
        all_momentums.append(r5 * 0.25 + r10 * 0.30 + r20 * 0.25 + r40 * 0.20)
    print(f"      {len(all_momentums)} 个主题动量计算完成")

    # ===== 第四步半：批量计算资金评分（跨主题百分位 + 非线性放大）=====
    print(f"[4.5/7] 批量计算资金评分...")
    capital_result = compute_all_capital_scores(daily, moneyflow, universe, market_turnover)
    print(f"        {len(capital_result)} 个主题资金评分完成")

    # ===== 第五步：逐个主题评分 =====
    print(f"[5/7] 主题评分计算中...")
    results = []
    theme_names = list(universe.keys())
    for i, tname in enumerate(theme_names):
        codes = universe[tname]
        if len(codes) < config.MIN_THEME_STOCKS:
            continue

        ts = compute_trend_score(daily, codes, all_momentums)
        # 从批量结果取 capital score 和子指标
        if tname in capital_result:
            cs, cap_metrics = capital_result[tname]
        else:
            cs, cap_metrics = 50.0, {}
        ss = compute_sentiment_score(daily, limit_df, dc_hot, codes, index_ret)
        ps = compute_persistence_score(daily, codes)
        rs = compute_risk_score(daily, codes, daily_basic, top_df)

        # 先识别龙头（延续评分需要龙头代码）
        ldr, ldr_score = identify_leader(daily, codes, top_df, top_inst)

        # 趋势延续评分：识别"强势延续"和"分歧买点"
        cont = compute_continuation_score(daily, codes, ldr)

        # 短期动量（用于子阶段精细化：初期/末期）
        r5, r10, _, _ = compute_momentum(daily, codes)

        # 阶段判断（需 continuation 约束，避免"主升+趋势走弱"矛盾）
        stage = identify_stage(ts, ss, cs, continuation=cont,
                               momentum=(r5 * 0.25 + r10 * 0.30) * 100,
                               r5=r5 * 100, r10=r10 * 100)
        lb = stage_bonus(stage)

        # 计算当日涨跌幅（用于综合分跌幅惩罚）
        theme_sub = daily[daily["ts_code"].isin(codes)]
        if not theme_sub.empty:
            latest_td = theme_sub["trade_date"].max()
            latest_theme = theme_sub[theme_sub["trade_date"] == latest_td]
            theme_today_ret = latest_theme["pct_chg"].mean() if not latest_theme.empty else 0
        else:
            theme_today_ret = 0

        # ===== V6.2: 计算 Forward Alpha 预测分（六因子）=====
        fa_score, fa_signal, fa_reason, fa_subs = compute_forward_alpha(
            daily, codes, moneyflow,
            limit_df=limit_df, top_df=top_df, dc_hot=dc_hot,
            all_momentums=all_momentums,
            leader_code=ldr, leader_score=ldr_score,
            trend_score=ts
        )

        cscore = compute_composite(ts, cs, ss, ps, lb, ldr_score, rs, cont,
                                   today_return=theme_today_ret, forward_alpha=fa_score)
        sig = trade_signal(cscore, cs, ts, stage, cont,
                           forward_alpha=fa_score, forward_signal=fa_signal)
        conf = confidence(cscore, ts, cs, cont, forward_alpha=fa_score)
        # 延续标签（需要真实 composite）
        cont_sig = continuation_signal(cont, cscore, stage)

        # 分歧买点标记：综合分确实低 + 延续分很高 + 阶段匹配
        is_divergence_buy = (cont >= config.WATCH_CONTINUATION
                             and cscore < config.WATCH_DIV_COMPOSITE
                             and stage in config.SB_STAGES)

        results.append({
            "theme": tname, "trade_date": trade_date, "stage": stage, "leader": ldr or "",
            "today_return": round(theme_today_ret, 2),
            "forward_alpha": fa_score,
            "forward_signal": fa_signal,
            "forward_reason": fa_reason,
            "fa_rotation_timing": fa_subs["rotation_timing"],
            "fa_capital_persist": fa_subs["capital_persist"],
            "fa_trend_quality": fa_subs["trend_quality"],
            "fa_catalyst": fa_subs["catalyst"],
            "fa_relative_rotation": fa_subs["relative_rotation"],
            "fa_leader_ecology": fa_subs["leader_ecology"],
            "trend_score": round(ts, 1), "capital_score": round(cs, 1),
            "cap_share": round(cap_metrics.get("market_share", 0) * 100, 2),
            "cap_accel": round(cap_metrics.get("acceleration", 0) * 100, 2),
            "cap_mflow": round(cap_metrics.get("mf_quality", 0) * 100, 1),
            "cap_conc": round(cap_metrics.get("concentration", 0) * 100, 1),
            "cap_persist": round(cap_metrics.get("persistence", 0) * 100, 1),
            "cap_rotation": round(cap_metrics.get("rotation", 0) * 100, 2),
            "cap_net_inflow": round(cap_metrics.get("net_inflow", 0) * 100, 2),
            "cap_persist_pct": round(cap_metrics.get("persistence_pct", 0) * 100, 1),
            "cap_rotation_pct": round(cap_metrics.get("rotation_pct", 0) * 100, 1),
            "sentiment_score": round(ss, 1), "persistence_score": round(ps, 1),
            "continuation_score": round(cont, 1),
            "risk_score": round(rs, 1), "lifecycle_score": lb,
            "leader_score": round(ldr_score, 1), "composite_score": round(cscore, 1),
            "confidence": round(conf, 1), "trade_signal": sig,
            "continuation_tag": cont_sig,
            "divergence_buy": "★" if is_divergence_buy else "",
        })

        if (i + 1) % 10 == 0:
            print(f"      进度: {i+1}/{len(theme_names)}")

    # ===== 第六步：Alpha Gate 两步筛选 =====
    print(f"[6/7] Alpha Gate 筛选...")
    df = pd.DataFrame(results)

    # --- 第一步：Alpha Gate（资格赛）---
    # 淘汰趋势差/资金不持续/无轮动信号的"伪主线"
    gate_trend = df["trend_score"] >= config.ALPHA_GATE_TREND
    gate_persist = df["cap_persist_pct"] >= config.ALPHA_GATE_CAP_PERSIST
    gate_rotation = df["cap_rotation_pct"] >= config.ALPHA_GATE_ROTATION

    df["alpha_gate"] = "PASS"
    df.loc[~gate_trend, "alpha_gate"] = "FAIL:趋势"
    df.loc[gate_trend & ~gate_persist, "alpha_gate"] = "FAIL:持续"
    df.loc[gate_trend & gate_persist & ~gate_rotation, "alpha_gate"] = "FAIL:轮动"

    gate_passed = df[gate_trend & gate_persist & gate_rotation].copy()
    gate_failed = df[~(gate_trend & gate_persist & gate_rotation)].copy()

    print(f"      Alpha Gate 通过: {len(gate_passed)} / {len(df)}")
    print(f"      淘汰原因: 趋势<{config.ALPHA_GATE_TREND}={(~gate_trend).sum()}, "
          f"持续<{config.ALPHA_GATE_CAP_PERSIST}={(gate_trend & ~gate_persist).sum()}, "
          f"轮动<{config.ALPHA_GATE_ROTATION}={(gate_trend & gate_persist & ~gate_rotation).sum()}")

    # --- 第二步：通过Alpha Gate的主题按综合分排序 ---
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    gate_passed = gate_passed.sort_values("composite_score", ascending=False).reset_index(drop=True)

    df.to_json(config.OUTPUT_JSON, orient="records", force_ascii=False, indent=2)
    df.to_csv(config.OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # 带日期的备份副本（保留历史版本，不覆盖）
    dated_json = config.OUTPUT_JSON.replace(".json", f"_{trade_date}.json")
    dated_csv = config.OUTPUT_CSV.replace(".csv", f"_{trade_date}.csv")
    df.to_json(dated_json, orient="records", force_ascii=False, indent=2)
    df.to_csv(dated_csv, index=False, encoding="utf-8-sig")

    # ===== V7 并行分析：梯队爆发力评分 =====
    print(f"[V7] 梯队爆发力评分中...")
    v7_results = []
    for i, tname in enumerate(theme_names):
        codes = universe[tname]
        if len(codes) < config.MIN_THEME_STOCKS:
            continue

        v7 = calculate_theme_score_v7(daily, codes, daily_basic, top_df, limit_df)

        theme_sub = daily[daily["ts_code"].isin(codes)]
        if not theme_sub.empty:
            latest_td = theme_sub["trade_date"].max()
            latest_theme = theme_sub[theme_sub["trade_date"] == latest_td]
            theme_today_ret = latest_theme["pct_chg"].mean() if not latest_theme.empty else 0
        else:
            theme_today_ret = 0

        v7_results.append({
            "theme": tname,
            "trade_date": trade_date,
            "composite_score": v7["composite_score"],
            "capital_vitality": v7["capital_vitality"],
            "echelon_integrity": v7["echelon_integrity"],
            "trend_momentum": v7["trend_momentum"],
            "stage": v7["stage"],
            "signal": v7["signal"],
            "today_return": round(theme_today_ret, 2),
            "penalties": v7["penalties"],
            **v7["sub_metrics"],
        })

        if (i + 1) % 10 == 0:
            print(f"      V7进度: {i+1}/{len(theme_names)}")

    # 保存V7结果
    v7_df = pd.DataFrame(v7_results)
    v7_json = config.OUTPUT_JSON.replace(".json", f"_v7_{trade_date}.json")
    v7_csv = config.OUTPUT_CSV.replace(".csv", f"_v7_{trade_date}.csv")
    v7_df.to_json(v7_json, orient="records", force_ascii=False, indent=2)
    v7_df.to_csv(v7_csv, index=False, encoding="utf-8-sig")
    print(f"      V7结果已保存: {v7_json}")

    # ===== V7 报告打印 =====
    v7_top = v7_df.sort_values("composite_score", ascending=False).head(15)
    v7_common = len(set(df.head(10)["theme"].tolist()) & set(v7_top.head(10)["theme"].tolist()))
    print(f"      V7梯队分计算完成: TOP1={v7_top.iloc[0]['theme']}({v7_top.iloc[0]['composite_score']:.1f}) | "
          f"与V6共同上榜={v7_common}个")

    # ===== V7.2 独立版评分（完整算法实现）=====
    print(f"[V7.2] 梯队爆发力完整评分中...")
    try:
        # 准备数据：合并daily_basic（换手率/流通市值）
        v7_data = daily.copy()
        if not daily_basic.empty:
            v7_data = v7_data.merge(
                daily_basic[["ts_code", "turnover_rate", "circ_mv"]],
                on="ts_code", how="left"
            )
        else:
            v7_data["turnover_rate"] = np.nan
            v7_data["circ_mv"] = np.nan

        # 合并资金流
        if not moneyflow.empty:
            mf_today = moneyflow[moneyflow["trade_date"] == trade_date].copy()
            if not mf_today.empty:
                mf_today["net_money_flow"] = mf_today["net_mf_amount"] * 1e4
                mf_today["net_money_flow_main"] = (
                    (mf_today["buy_lg_amount"] + mf_today["buy_elg_amount"] -
                     mf_today["sell_lg_amount"] - mf_today["sell_elg_amount"])
                ) * 1e4
                v7_data = v7_data.merge(
                    mf_today[["ts_code", "net_money_flow", "net_money_flow_main"]],
                    on="ts_code", how="left"
                )

        # 构建theme数据
        theme_rows = []
        for tname, codes in universe.items():
            for code in codes:
                sub = v7_data[v7_data["ts_code"] == code].copy()
                if not sub.empty:
                    sub["theme"] = tname
                    theme_rows.append(sub)

        if theme_rows:
            v7_df = pd.concat(theme_rows, ignore_index=True)
            v7_result = calculate_v7_standalone(v7_df)

            v7_standalone_json = config.OUTPUT_JSON.replace(".json", f"_v72_{trade_date}.json")
            v7_standalone_csv = config.OUTPUT_CSV.replace(".csv", f"_v72_{trade_date}.csv")
            v7_result.to_json(v7_standalone_json, orient="records", force_ascii=False, indent=2)
            v7_result.to_csv(v7_standalone_csv, index=False, encoding="utf-8-sig")
            print(f"      V7.2结果已保存: {v7_standalone_json}")

            v7_top = v7_result.head(15)
            v7_common2 = len(set(df.head(10)["theme"].tolist()) & set(v7_top.head(10)["主题"].tolist()))
            print(f"      V7.2完整分计算完成: TOP1={v7_top.iloc[0]['主题']}({v7_top.iloc[0]['V7综合得分']:.1f}) | "
                  f"与V6共同上榜={v7_common2}个")
    except Exception as e:
        print(f"      [V7.2] 评分异常: {e}")
        import traceback
        traceback.print_exc()

    # ===== V8.0 主题生命周期节奏与高确定性中军交易指导系统 =====
    print(f"[V8.0] 主题生命周期节奏与中军交易指导系统...")
    try:
        # 构建V8数据（复用V7.2的数据准备逻辑）
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
            mf_today = moneyflow[moneyflow["trade_date"] == trade_date].copy()
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

        v8_theme_rows = []
        for tname, codes in universe.items():
            for code in codes:
                sub = v8_data[v8_data["ts_code"] == code].copy()
                if not sub.empty:
                    sub["theme"] = tname
                    v8_theme_rows.append(sub)

        if not v8_theme_rows:
            print("      [V8.0] 无主题数据")
        else:
            v8_df = pd.concat(v8_theme_rows, ignore_index=True)
            v8_result, v8_center_df, v8_trading_card = calculate_v8_theme_score(v8_df)

            v8_json = config.OUTPUT_JSON.replace(".json", f"_v8_{trade_date}.json")
            v8_csv = config.OUTPUT_CSV.replace(".csv", f"_v8_{trade_date}.csv")
            v8_result.to_json(v8_json, orient="records", force_ascii=False, indent=2)
            v8_result.to_csv(v8_csv, index=False, encoding="utf-8-sig")
            print(f"      V8.0结果已保存: {v8_json}")

            if not v8_center_df.empty:
                v8_center_csv = config.OUTPUT_CSV.replace(".csv", f"_v8_center_{trade_date}.csv")
                v8_center_df.to_csv(v8_center_csv, index=False, encoding="utf-8-sig")
                print(f"      中军标的已保存: {v8_center_csv}")

            v8_card_file = os.path.join(BASE_DIR, "cache", f"trading_card_{trade_date}.md")
            with open(v8_card_file, "w", encoding="utf-8") as f:
                f.write(v8_trading_card)
            print(f"      指导卡已保存: {v8_card_file}")

            # D阶段分布统计（精简输出）
            stage_counts = v8_result["D阶段"].value_counts()
            stage_summary = " | ".join([f"{s}={c}" for s, c in stage_counts.items()])
            print(f"      V8生命周期计算完成: TOP1={v8_result.iloc[0]['主题']}({v8_result.iloc[0]['V7综合得分']:.1f}) | "
                  f"D阶段: {stage_summary}")
            print(f"      中军标的: {len(v8_center_df)}只 | 指导卡: {v8_card_file}")

    except Exception as e:
        print(f"      [V8.0] 异常: {e}")
        import traceback
        traceback.print_exc()

    # ===== V9.0/V10.0 已废弃（tushare_quant.py不引用）=====
    pass

    # ===== FUSION: V6+V8 融合排名 =====
    print(f"\n  [FUSION] V6+V8 主题融合排名...")
    try:
        from fusion_rank import build_fusion_rank
        # 从大盘分析获取状态
        import re as _re_fusion
        _ma_txt_fusion = ""
        try:
            _ma_path = os.path.join(os.path.dirname(config.BASE_DIR),
                                    "cache_backbone_tushare",
                                    f"market_analysis_{trade_date}.txt")
            if os.path.exists(_ma_path):
                with open(_ma_path, encoding='utf-8') as _f:
                    _ma_txt_fusion = _f.read()
        except Exception:
            pass
        _m_ts = _re_fusion.search(r'总趋势分.*?:\s*(\d+\.?\d*)', _ma_txt_fusion)
        _ts_f = float(_m_ts.group(1)) if _m_ts else 50.0
        _m_ms = _re_fusion.search(r'市场状态:\s*(.+)', _ma_txt_fusion)
        _ms_f = _m_ms.group(1).strip() if _m_ms else "震荡"
        build_fusion_rank(trade_date, market_regime=_ms_f, trend_score=_ts_f, quiet=True)
        print(f"      FUSION融合排名已保存")
    except Exception as e:
        print(f"      [FUSION] 异常: {e}")
        import traceback
        traceback.print_exc()

    # ===== FUSION-based 中军筛选（按活跃主题信号）=====
    try:
        _fusion_json = os.path.join(
            os.path.dirname(config.BASE_DIR),
            "theme_alpha_v6", "cache", f"theme_fusion_rank_{trade_date}.json"
        )
        import json as _json
        if os.path.exists(_fusion_json):
            with open(_fusion_json, 'r', encoding='utf-8') as _f:
                _fusion_data = _json.load(_f)
            _fusion_list = _fusion_data.get("data", _fusion_data) if isinstance(_fusion_data, dict) else _fusion_data

            # v8_df是否可用
            try:
                _v8_df_ref = v8_df
            except NameError:
                _v8_df_ref = None

            if _v8_df_ref is not None and not _v8_df_ref.empty:
                from v8_theme_rhythm import generate_center_by_fusion
                _center_fusion = generate_center_by_fusion(
                    _v8_df_ref, _fusion_list,
                    min_action="轻仓参与", max_themes=15
                )
                if not _center_fusion.empty:
                    _center_csv = config.OUTPUT_CSV.replace(
                        ".csv", f"_v8_center_{trade_date}.csv"
                    )
                    _center_fusion.to_csv(_center_csv, index=False, encoding="utf-8-sig")
                    print(f"      [融合中军] 已保存: {_center_csv} ({len(_center_fusion)}只)")
                else:
                    print(f"      [融合中军] 无符合条件的标的")
                del _center_fusion
    except Exception as _e_center:
        print(f"      [融合中军] 异常: {_e_center}")

    # ===== 第七步：打印报告 =====
    print(f"[7/7] 打印报告...")

    # 资金分分布
    cap_scores = df['capital_score'].values
    print(f"      Alpha Gate通过={len(gate_passed)} | "
          f"综合分范围={df['composite_score'].min():.1f}~{df['composite_score'].max():.1f} | "
          f"资金分: p25={np.percentile(cap_scores,25):.1f} p50={np.percentile(cap_scores,50):.1f} p75={np.percentile(cap_scores,75):.1f} | "
          f"ForwardAlpha信号: 看多={df['forward_signal'].value_counts().get('看多',0)} 中性={df['forward_signal'].value_counts().get('中性',0)}")

    print(f"\n  结果已保存:")
    print(f"    {config.OUTPUT_JSON}")
    print(f"    {config.OUTPUT_CSV}")
    print(f"    {dated_json}")
    print(f"{'='*100}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Theme Alpha V6.2 Engine")
    parser.add_argument("--date", type=str, default=None, help="交易日(YYYYMMDD)")
    args = parser.parse_args()
    main(trade_date=args.date)
