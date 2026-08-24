# -*- coding: utf-8 -*-
"""
报告生成模块 - 输出 RIB 完整分析结果

输出格式：Markdown
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from .config import RIB_CONFIG
from .engine import RIBResult


def generate_report(result: RIBResult) -> str:
    """生成单只股票的完整分析报告。"""
    r = result
    dt = r.downtrend
    imp = r.impulse
    base = r.base
    bo = r.breakout
    pb = r.pullback
    ra = r.reacc
    fs = r.final_score
    tp = r.trade_plan

    lines = []
    lines.append(f"# RIB 分析报告 - {r.ts_code} {r.name}")
    lines.append(f"分析日期: {r.date}")
    lines.append("")

    # ── 基础 ──
    lines.append("## 【基础】")
    lines.append(f"- **代码**: {r.ts_code}")
    lines.append(f"- **名称**: {r.name}")
    lines.append(f"- **现价**: {r.close:.2f}")
    lines.append(f"- **行业**: {r.industry}")
    lines.append(f"- **状态**: {r.state}")
    lines.append("")

    # ── 长期下跌 ──
    lines.append("## 【长期下跌】")
    if dt:
        lines.append(f"- **下跌开始**: 第{dt.start_index}日")
        lines.append(f"- **阶段最低**: {dt.lowest_price:.2f}")
        lines.append(f"- **60日跌幅**: {dt.decline_60d*100:.1f}%")
        lines.append(f"- **120日跌幅**: {dt.decline_120d*100:.1f}%")
        lines.append(f"- **MA20趋势**: {'向下' if dt.ma20_slope < 0 else '向上'} ({dt.ma20_slope:.2%})")
        lines.append(f"- **MA60趋势**: {'向下' if dt.ma60_slope < 0 else '向上'} ({dt.ma60_slope:.2%})")
        lines.append(f"- **高点降低数**: {dt.higher_highs}")
        lines.append(f"- **低点降低数**: {dt.lower_lows}")
        lines.append(f"- **DOWNTREND_SCORE**: {dt.score:.0f}/100")
    else:
        lines.append("- 未检测到长期下跌")
    lines.append("")

    # ── 第一波反转 ──
    lines.append("## 【第一波反转】")
    if imp:
        lines.append(f"- **ImpulseStart**: 第{imp.impulse_start_idx}日 (最低价 {imp.impulse_low:.2f})")
        lines.append(f"- **ImpulseHigh**: {imp.impulse_high:.2f} (第{imp.impulse_high_idx}日)")
        lines.append(f"- **ImpulseDays**: {imp.impulse_days}日")
        lines.append(f"- **ImpulseReturn**: {imp.impulse_return*100:.1f}%")
        lines.append(f"- **ImpulseATR**: {imp.impulse_atr:.1f}")
        lines.append(f"- **ImpulseVolumeRatio**: {imp.volume_ratio:.2f}")
        lines.append(f"- **是否突破下降趋势**: {'是' if imp.broke_trend_line else '否'}")
        lines.append(f"- **是否突破MA20**: {'是' if imp.broke_ma20 else '否'}")
        lines.append(f"- **是否突破MA60**: {'是' if imp.broke_ma60 else '否'}")
        lines.append(f"- **是否突破前高**: {'是' if imp.broke_previous_high else '否'}")
        lines.append(f"- **REVERSAL_CONFIRMED**: {'是' if imp.is_reversal_confirmed else '否'}")
        lines.append(f"- **EXTREME_ACCELERATION**: {'是' if imp.is_extreme_acceleration else '否'}")
        lines.append(f"- **IMPULSE_SCORE**: {imp.score:.0f}/100")
    else:
        lines.append("- 未检测到第一波反转")
    lines.append("")

    # ── POST_IMPULSE_BASE ──
    lines.append("## 【第一波后的平台】")
    if base:
        lines.append(f"- **PlatformStart**: 第{base.platform_start_idx}日")
        lines.append(f"- **PlatformEnd**: 第{base.platform_end_idx}日")
        lines.append(f"- **PlatformDays**: {base.platform_days}日")
        lines.append(f"- **BaseHigh**: {base.base_high:.2f}")
        lines.append(f"- **BaseLow**: {base.base_low:.2f}")
        lines.append(f"- **BaseRange**: {base.base_range*100:.1f}%")
        lines.append(f"- **PullbackDepth**: {base.pullback_depth*100:.1f}%")
        lines.append(f"- **ImpulseRetainRatio**: {base.retain_ratio*100:.1f}%")
        lines.append(f"- **平台成交量/第一波**: {base.volume_shrink_ratio:.2f}")
        lines.append(f"- **高点结构**: {base.high_structure}")
        lines.append(f"- **低点结构**: {base.low_structure}")
        lines.append(f"- **MA20Slope**: {base.ma20_slope:.2%}")
        lines.append(f"- **平台类型**: {base.base_type}")
        lines.append(f"- **POST_IMPULSE_BASE_SCORE**: {base.score:.0f}/100")
        if base.is_volume_plunge:
            lines.append("- ⚠️ **警告**: 平台放量下跌！")
        if base.is_back_to_origin:
            lines.append("- ⚠️ **警告**: 跌回第一波启动区！")
    else:
        lines.append("- 未检测到 POST_IMPULSE_BASE")
    lines.append("")

    # ── 第二波突破 ──
    lines.append("## 【第二波突破】")
    if bo:
        lines.append(f"- **突破日期**: {bo.breakout_date}")
        lines.append(f"- **突破价格**: {bo.breakout_price:.2f}")
        lines.append(f"- **ImpulseHigh**: {bo.impulse_high:.2f}")
        lines.append(f"- **突破距离**: {bo.breakout_distance_atr:.1f}ATR")
        lines.append(f"- **突破量比**: {bo.volume_ratio:.2f}")
        lines.append(f"- **收盘位置**: {bo.close_location:.2f}")
        lines.append(f"- **K线质量**: 上影{bo.upper_shadow:.2f}")
        lines.append(f"- **MA5>MA10**: {'是' if bo.ma5_above_ma10 else '否'}")
        lines.append(f"- **MA20Slope≥0**: {'是' if bo.ma20_slope_ok else '否'}")
        lines.append(f"- **SECOND_LEG_BREAKOUT_SCORE**: {bo.score:.0f}/100")
        if bo.is_fake_breakout:
            lines.append("- ⚠️ **警告**: 假突破！")
    else:
        lines.append("- 未检测到第二波突破")
    lines.append("")

    # ── 第一次回踩 ──
    lines.append("## 【第一次回踩】")
    if pb:
        lines.append(f"- **回踩日期**: 第{pb.pullback_low_idx}日")
        lines.append(f"- **回踩最低**: {pb.pullback_low:.2f}")
        lines.append(f"- **回踩天数**: {pb.pullback_days}日")
        lines.append(f"- **回踩深度**: {pb.pullback_depth*100:.1f}%")
        lines.append(f"- **回踩量/突破量**: {pb.pullback_volume_ratio:.2f}")
        lines.append(f"- **是否跌破第一波高点**: {'是' if pb.broke_impulse_high else '否'}")
        lines.append(f"- **是否TEST_AND_RECLAIM**: {'是' if pb.is_test_and_reclaim else '否'}")
        lines.append(f"- **是否跌回平台**: {'是' if pb.fell_back_to_base else '否'}")
        lines.append(f"- **关键位承接**: {'是' if pb.support_found else '否'}")
        lines.append(f"- **PULLBACK_SCORE**: {pb.score:.0f}/100")
    else:
        lines.append("- 未检测到第一次回踩")
    lines.append("")

    # ── 二次启动 ──
    lines.append("## 【二次启动】")
    if ra:
        lines.append(f"- **启动日期**: {ra.reacc_date}")
        lines.append(f"- **启动价格**: {ra.reacc_price:.2f}")
        lines.append(f"- **MA5**: {ra.ma5:.2f}")
        lines.append(f"- **MA10**: {ra.ma10:.2f}")
        if ra.vwap:
            lines.append(f"- **VWAP**: {ra.vwap:.2f}")
        lines.append(f"- **量比**: {ra.volume_ratio:.2f}")
        lines.append(f"- **收盘位置**: {ra.close_location:.2f}")
        lines.append(f"- **MA5SlopeUp**: {'是' if ra.ma5_slope_up else '否'}")
        lines.append(f"- **突破回踩高点**: {'是' if ra.break_pullback_high else '否'}")
        lines.append(f"- **RE_ACCELERATION_SCORE**: {ra.score:.0f}/100")
    else:
        lines.append("- 未检测到二次启动")
    lines.append("")

    # ── 交易 ──
    lines.append("## 【交易】")
    if fs and tp:
        lines.append(f"- **FINAL_SCORE**: {fs.total:.0f}/100")
        lines.append(f"- **等级**: {fs.grade}")
        lines.append(f"- **状态**: {r.state}")
        lines.append(f"- **PRIMARY_BUY**: {'★ 是' if fs.is_primary_buy else '否'}")
        lines.append(f"- **建议买入区**: {tp.zone_low:.2f} ~ {tp.zone_high:.2f}")
        lines.append(f"- **止损**: {tp.stop_loss:.2f}")
        lines.append(f"- **目标1**: {tp.target1:.2f}")
        lines.append(f"- **目标2**: {tp.target2:.2f}")
        lines.append(f"- **RR**: {r.risk_reward:.1f}")
        lines.append(f"- **建议仓位**: {tp.position_pct*100:.0f}%")
        lines.append(f"- **预计持有**: {tp.holding_days}日")
        lines.append("")

        lines.append("### 分项评分")
        lines.append(f"- ① 长期下跌背景: {fs.s_downtrend_bg:.1f}/10")
        lines.append(f"- ② 第一波反转: {fs.s_impulse:.1f}/25")
        lines.append(f"- ③ POST_IMPULSE_BASE: {fs.s_post_impulse_base:.1f}/30")
        lines.append(f"- ④ 第二波突破: {fs.s_second_breakout:.1f}/15")
        lines.append(f"- ⑤ 第一次回踩: {fs.s_first_pullback:.1f}/10")
        lines.append(f"- ⑥ 再启动: {fs.s_re_acceleration:.1f}/10")
        lines.append(f"- 主题增强: +{r.theme_bonus:.1f}")
    else:
        lines.append("- 尚未形成完整交易计划")
    lines.append("")

    # ── Q1~Q13 检查 ──
    if r.q_checks:
        lines.append("## 【Q1~Q13 关键检查】")
        q_map = {
            "Q1_downtrend": "Q1: 长期下降趋势",
            "Q2_impulse_strong": "Q2: 第一波足够强",
            "Q3_trend_changed": "Q3: 改变下降结构",
            "Q4_base_holds_most": "Q4: 保留大部分涨幅",
            "Q5_base_volume_shrink": "Q5: 平台缩量",
            "Q6_low_stable": "Q6: 低点稳定",
            "Q7_ma20_up": "Q7: MA20拐头",
            "Q8_near_impulse_high": "Q8: 接近第一波高点",
            "Q9_true_breakout": "Q9: 真正突破",
            "Q10_healthy_pullback": "Q10: 健康回踩",
            "Q11_support_held": "Q11: 守住关键位",
            "Q12_re_acceleration": "Q12: 重新转强",
            "Q13_rr_ge_2": "Q13: 盈亏比≥2",
        }
        for k, v in r.q_checks.items():
            label = q_map.get(k, k)
            status = "✅" if v else "❌"
            lines.append(f"- {status} {label}")
        lines.append("")

    # ── 否决项 ──
    if r.veto_triggered:
        lines.append("## ⚠️ 【强制否决】")
        for v in r.veto_triggered:
            lines.append(f"- {v}")
        lines.append("")

    # ── 结论 ──
    lines.append("## 【结论】")
    lines.append(r.conclusion)
    lines.append("")

    return "\n".join(lines)


def generate_summary(results: List[RIBResult]) -> str:
    """生成多只股票的摘要报告。"""
    lines = []
    lines.append("# RIB 选股引擎运行摘要")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"扫描股票数: {len(results)}")
    lines.append("")

    # 按状态分组
    by_state: dict = {}
    for r in results:
        by_state.setdefault(r.state, []).append(r)

    lines.append("## 状态分布")
    for state, group in sorted(by_state.items()):
        lines.append(f"- **{state}**: {len(group)}只")
    lines.append("")

    # 最终候选
    primary = [r for r in results if r.state == "PRIMARY_BUY"]
    lines.append(f"## ★ PRIMARY BUY 候选 ({len(primary)}只)")
    for r in sorted(primary, key=lambda x: x.final_score.total if x.final_score else 0, reverse=True):
        fs = r.final_score
        lines.append(
            f"- **{r.ts_code} {r.name}** | "
            f"SCORE={fs.total:.0f} ({fs.grade}) | "
            f"RR={r.risk_reward:.1f} | "
            f"现价={r.close:.2f}"
        )
    lines.append("")

    # 高分候选
    high_score = [r for r in results
                  if r.final_score and r.final_score.total >= 75
                  and r.state != "PRIMARY_BUY"]
    if high_score:
        lines.append(f"## 高分观察候选 ({len(high_score)}只, SCORE≥75)")
        for r in sorted(high_score, key=lambda x: x.final_score.total, reverse=True)[:20]:
            fs = r.final_score
            lines.append(
                f"- **{r.ts_code} {r.name}** | "
                f"SCORE={fs.total:.0f} ({fs.grade}) | "
                f"状态={r.state}"
            )
        lines.append("")

    # 市场环境
    regimes = set(r.market_regime for r in results)
    if regimes:
        lines.append("## 市场环境")
        for reg in sorted(regimes):
            lines.append(f"- {reg}: {sum(1 for r in results if r.market_regime == reg)}只")
        lines.append("")

    return "\n".join(lines)


def save_report(report: str, filepath: str):
    """保存报告到文件。"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report)
