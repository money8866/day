# -*- coding: utf-8 -*-
"""
RIB V2.1 报告生成（规范§38/§39）

输出结构：
  头部：REVERSAL-IMPULSE-BASE V2.1 / 交易日期 / 市场环境 / NOW/NEXT/WATCH
  # 一、NOW    - PRIMARY_BUY 或极高质量 RE_ACCELERATION（含买点/止损/目标/RR/为什么可以买）
  # 二、NEXT   - 未来1~3日最可能进入交易窗口（按PriorityScore排序，§39四问）
  # 三、WATCH  - 结构形成中（只显示 IMPULSE_PEAK/早期POST_IMPULSE_BASE/REVERSAL_SETUP，
                并说明"距离进入NEXT还缺什么"）
  # 四、REVERSAL FUNNEL - 全池状态分布漏斗
  # 五、FAILED LIST - FAILED_REVERSAL / FAILED_BREAKOUT / FAILED_PULLBACK 及失败原因
  # 六、最终市场结论 - NOW/NEXT/WATCH 数量与明确结论

原则：
  - 状态优先级高于分数（高分但未到交易阶段不得进入 NOW）
  - NOW 宁缺毋滥，NOW=0 时明确输出"当前没有符合最高胜率买点的股票"
  - NEXT>0 时输出"存在X只距离关键状态转换较近的NEXT候选"
  - 不使用缩进（移动端友好），分隔线用全角 ═/─
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .config import (
    STATE_DOWNTREND, STATE_REVERSAL_SETUP,
    STATE_IMPULSE_START, STATE_IMPULSE_ACTIVE, STATE_IMPULSE_PEAK,
    STATE_POST_IMPULSE_BASE, STATE_PRE_BREAKOUT, STATE_SECOND_LEG_BREAKOUT,
    STATE_FIRST_PULLBACK, STATE_PULLBACK_SUPPORT, STATE_RE_ACCELERATION,
    STATE_PRIMARY_BUY, STATE_HOLD, STATE_EXIT, STATE_INVALIDATED,
    STATE_FAILED_REVERSAL, STATE_FAILED_BREAKOUT, STATE_FAILED_PULLBACK,
)

SEP = "═" * 76
SEP_THIN = "─" * 76

# 市场状态标签
_REGIME_LABEL = {
    "bull": "BULL（进攻）",
    "normal": "NORMAL（正常）",
    "recovery": "RECOVERY（修复）",
    "weak": "WEAK（弱势）",
    "bear": "BEAR（防守）",
}

# 结构漏斗固定顺序（§29）
FUNNEL_ORDER = [
    STATE_DOWNTREND, STATE_REVERSAL_SETUP, STATE_IMPULSE_ACTIVE,
    STATE_IMPULSE_PEAK, STATE_POST_IMPULSE_BASE, STATE_PRE_BREAKOUT,
    STATE_SECOND_LEG_BREAKOUT, STATE_FIRST_PULLBACK, STATE_PULLBACK_SUPPORT,
    STATE_RE_ACCELERATION, STATE_PRIMARY_BUY,
    STATE_FAILED_REVERSAL, STATE_FAILED_BREAKOUT, STATE_FAILED_PULLBACK,
    STATE_INVALIDATED, STATE_HOLD, STATE_EXIT,
]

# NEXT规则标签（§24 A/B/C/D）
_RULE_LABEL = {
    "A": "规则A·成熟平台待突破",
    "B": "规则B·回踩获承接",
    "C": "规则C·缩量回踩支撑",
    "D": "规则D·二次启动未完备",
}


def _key_price(r) -> str:
    """根据状态给出关键价格描述。"""
    mem = r.memory or {}
    if r.state in (STATE_POST_IMPULSE_BASE, STATE_PRE_BREAKOUT, STATE_IMPULSE_PEAK):
        return f"第一波高点{mem.get('impulse_high', 0):.2f}"
    if r.state in (STATE_SECOND_LEG_BREAKOUT,):
        return f"突破价{mem.get('breakout_price', 0):.2f}"
    if r.state in (STATE_FIRST_PULLBACK, STATE_PULLBACK_SUPPORT, STATE_RE_ACCELERATION):
        return f"回踩高点{mem.get('pullback_high', 0):.2f}"
    return "-"


def _next_state_line(r) -> List[str]:
    """NEXT_STATE + 触发价 + 距离行（§21）。"""
    lines = []
    parts = [f"下一状态: {r.next_state}"]
    if r.next_state_level and r.next_state_level != "NOT_READY":
        parts.append(f"NEXT_SCORE={r.next_state_score:.0f}({r.next_state_level})")
    else:
        parts.append(f"NEXT_SCORE={r.next_state_score:.0f}")
    lines.append("   " + "  ".join(parts))
    if r.next_state_gap_price and r.next_state_gap_price > 0:
        dist = r.next_state_gap_price - r.close
        lines.append(
            f"   关键触发价: {r.next_state_gap_price:.2f}  当前价: {r.close:.2f}"
            f"  距离: {dist:+.2f}元({r.next_state_gap_atr:.2f}ATR)"
        )
    if r.distance_to_breakout:
        lines.append(
            f"   距突破位: {r.distance_to_breakout}"
            f"({r.distance_to_breakout_atr:.2f}ATR)"
        )
    if r.prob_level:
        lines.append(f"   结构概率: {r.prob_level}")
    return lines


def _structure_line(r) -> str:
    """结构风险 + 平台质量摘要行。"""
    parts = []
    if r.structure_risk > 0:
        items = f"({'、'.join(r.structure_risk_items)})" if r.structure_risk_items else ""
        parts.append(f"结构风险{r.structure_risk:.0f}{items}")
    if r.base and r.base.is_base:
        parts.append(
            f"平台{r.base.platform_days}日 质{r.base_quality:.0f}"
            f" 保留{r.base.retain_ratio*100:.0f}%"
        )
    if r.pre_breakout and r.pre_breakout.is_pre_breakout:
        pre = r.pre_breakout
        aged = " AGED(超5日)" if pre.aged else (" EXPIRED(超10日)" if pre.expired else "")
        parts.append(f"PRE_BO={pre.grade}({pre.score:.0f}){aged}")
    return "   " + "  ".join(parts) if parts else ""


# ═══════════════════════════════════════════════════════════
# NOW 块（§25/§38）
# ═══════════════════════════════════════════════════════════

def _now_block(idx: int, r) -> List[str]:
    """NOW 单股块：买点/止损/目标1/目标2/RR + 为什么现在可以买。"""
    lines = []
    tag = "  ★PRIMARY BUY" if r.state == STATE_PRIMARY_BUY else "  ◆RE_ACCEL"
    lines.append(
        f"{idx}. {r.name}({r.ts_code})  状态={r.state}{tag}"
        f"  当前价={r.close:.2f}"
    )
    lines.append(
        f"   BUY_READINESS={r.buy_readiness:.0f}  结构风险={r.structure_risk:.0f}"
        f"  PriorityScore={r.priority_score:.0f}"
    )
    tp = r.trade_plan
    if tp and tp.buy_price > 0:
        lines.append(
            f"   买入价: {tp.buy_price:.2f}  止损: {tp.stop_loss:.2f}"
            f"  目标1: {tp.target1:.2f}  目标2: {tp.target2:.2f}"
            f"  RR={tp.risk_reward:.2f}"
        )
        if tp.zone_high > tp.zone_low > 0:
            lines.append(f"   建议买入区: {tp.zone_low:.2f} ~ {tp.zone_high:.2f}")
        lines.append(f"   持有周期: {tp.holding_days}日  仓位: {tp.position_pct*100:.0f}%")
    else:
        lines.append(f"   盈亏比: {r.risk_reward:.2f}（止损/目标详见结构位）")
    if r.conclusion:
        why = r.conclusion.replace("\n", " | ")
        lines.append(f"   为什么现在可以买: {why}")
    return lines


# ═══════════════════════════════════════════════════════════
# NEXT 块（§26/§38/§39 四问）
# ═══════════════════════════════════════════════════════════

def _next_block(idx: int, r) -> List[str]:
    """NEXT 单股块：§39 四问（为什么不能买/下一步/触发价/触发后操作）。"""
    lines = []
    rule_tag = _RULE_LABEL.get(r.next_rule, "")
    lines.append(
        f"{idx}. {r.name}({r.ts_code})  状态={r.state}"
        f"  READINESS={r.buy_readiness:.0f}"
        f"  PriorityScore={r.priority_score:.0f}"
        + (f"  [{rule_tag}]" if rule_tag else "")
    )
    lines.extend(_next_state_line(r))
    s = _structure_line(r)
    if s:
        lines.append(s)
    # ── §39 四问 ──
    q1 = r.cannot_buy_reason or "尚未到达最高胜率买点阶段。"
    lines.append(f"   ① 为什么现在还不能买: {q1}")
    q2 = r.next_state_trigger or f"进入{r.next_state}。"
    lines.append(f"   ② 下一步需要发生什么: {q2}")
    if r.next_state_gap_price and r.next_state_gap_price > 0:
        q3 = (f"{r.next_state_gap_price:.2f}（当前{r.close:.2f}，"
              f"差{r.next_state_gap_price - r.close:+.2f}元/"
              f"{r.next_state_gap_atr:.2f}ATR）")
    else:
        q3 = _key_price(r)
    lines.append(f"   ③ 触发价格: {q3}")
    q4 = _after_trigger_action(r)
    lines.append(f"   ④ 触发以后怎么操作: {q4}")
    return lines


def _after_trigger_action(r) -> str:
    """④ 触发后操作指引（§39：不追涨，等第一次缩量回踩）。"""
    if r.state in (STATE_PRE_BREAKOUT, STATE_POST_IMPULSE_BASE, STATE_IMPULSE_PEAK):
        trig = f"{r.next_state_gap_price:.2f}" if r.next_state_gap_price else "第一波高点"
        return (
            f"突破{trig}当日不追涨；等待第一次缩量回踩{trig}附近不破，"
            "且回踩缩量+收盘站稳后再评估买点。"
        )
    if r.state == STATE_SECOND_LEG_BREAKOUT:
        return "等待第一次缩量回踩关键支撑（突破价/MA5/MA10）确认承接后再介入。"
    if r.state in (STATE_FIRST_PULLBACK, STATE_PULLBACK_SUPPORT):
        return (
            "等待收盘重新站回关键位+缩量企稳（RE_ACCELERATION信号），"
            "出现放量收复回踩高点时按买点介入。"
        )
    if r.state == STATE_RE_ACCELERATION:
        return "若满足PRIMARY_BUY全部条件（RR≥2、结构风险<35）则按计划介入，否则继续等。"
    return "突破确认后等待第一次健康回踩，不直接追涨。"


# ═══════════════════════════════════════════════════════════
# WATCH 块（§27/§38）
# ═══════════════════════════════════════════════════════════

# WATCH 只显示这三种（§27）
_WATCH_STATES = {STATE_IMPULSE_PEAK, STATE_POST_IMPULSE_BASE, STATE_REVERSAL_SETUP}

_MISS_TEXT = {
    STATE_REVERSAL_SETUP: "距离进入NEXT还缺: 第一波强势反转（涨幅≥15%、放量、突破MA20），当前仅是反弹观察。",
    STATE_IMPULSE_PEAK: "距离进入NEXT还缺: 第一波见顶后形成5日以上高位平台+缩量+涨幅保留≥50%。",
    STATE_POST_IMPULSE_BASE: "距离进入NEXT还缺: 平台成熟度不足（需≥7日+BASE_QUALITY≥75+缩量+距离第一波高点≤1ATR）。",
}


def _watch_block(idx: int, r) -> List[str]:
    """WATCH 单股块：状态+缺什么。"""
    lines = []
    lines.append(
        f"{idx}. {r.name}({r.ts_code})  状态={r.state}"
        f"  READINESS={r.buy_readiness:.0f}  平台质量={r.base_quality:.0f}"
    )
    miss = _MISS_TEXT.get(r.state, "结构尚未成熟。")
    lines.append(f"   {miss}")
    if r.state == STATE_POST_IMPULSE_BASE and r.base and r.base.is_base:
        pre = r.pre_breakout
        if pre:
            gap = pre.distance_atr
            lines.append(
                f"   当前距第一波高点{gap:.2f}ATR（需≤1.0ATR），"
                f"平台{r.base.platform_days}日（需≥7日），PRE_BO分={pre.score:.0f}（需≥75）。"
            )
    lines.append(f"   监控要点: {_key_price(r)}")
    return lines


# ═══════════════════════════════════════════════════════════
# 主报告
# ═══════════════════════════════════════════════════════════

def generate_v2_report(results: List,
                       end_date: str = "",
                       market_regime: str = "normal",
                       forward_text: str = "") -> str:
    """生成 V2.1 报告文本。

    Args:
        results: RIBResult 列表
        end_date: 交易日期
        market_regime: bull/normal/recovery/weak/bear
        forward_text: Forward Test 统计文本（可选，§31/§32）
    """
    out: List[str] = []
    out.append(SEP)
    out.append("REVERSAL-IMPULSE-BASE V2.1")
    out.append(f"交易日期: {end_date}")
    out.append(f"市场环境: {_REGIME_LABEL.get(market_regime, market_regime)}")
    out.append(f"候选总数: {len(results)}")

    # ── 分层 ──
    now, nxt, watch = [], [], []
    for r in results:
        if r.pool_tier == "NOW":
            now.append(r)
        elif r.pool_tier == "NEXT":
            nxt.append(r)
        elif r.pool_tier == "WATCH":
            watch.append(r)

    # 层内排序：状态优先级 > PriorityScore（§37）
    def now_sort_key(r):
        st_order = {STATE_PRIMARY_BUY: 0, STATE_RE_ACCELERATION: 1}
        return (st_order.get(r.state, 9), -r.priority_score)

    now.sort(key=now_sort_key)
    nxt.sort(key=lambda r: -r.priority_score)      # §38: 按PriorityScore排序
    watch.sort(key=lambda r: -r.priority_score)

    out.append(f"NOW: {len(now)}   NEXT: {len(nxt)}   WATCH: {len(watch)}")
    out.append(SEP)

    # ═══════════════════ 一、NOW ═══════════════════
    out.append("# 一、NOW ── 当前可交易（PRIMARY_BUY 或 极高质量RE_ACCELERATION）")
    out.append(SEP_THIN)
    if not now:
        out.append("当前没有符合最高胜率买点的股票。")
        out.append("（要求: BUY_READINESS≥85 且 结构风险<35 且 RR≥2 且 非BEAR）")
    else:
        for i, r in enumerate(now, 1):
            out.extend(_now_block(i, r))
            out.append(SEP_THIN)

    # ═══════════════════ 二、NEXT ═══════════════════
    out.append("# 二、NEXT ── 未来1~3日最可能进入交易窗口（按PriorityScore排序）")
    out.append(SEP_THIN)
    if not nxt:
        out.append("暂无接近买点的NEXT候选。")
    else:
        for i, r in enumerate(nxt, 1):
            out.extend(_next_block(i, r))
            out.append(SEP_THIN)

    # ═══════════════════ 三、WATCH ═══════════════════
    out.append("# 三、WATCH ── 结构形成中（只列 IMPULSE_PEAK / 早期POST_IMPULSE_BASE / REVERSAL_SETUP）")
    out.append(SEP_THIN)
    watch_show = [r for r in watch if r.state in _WATCH_STATES]
    if not watch_show:
        out.append("暂无正在形成关键结构的候选。")
    else:
        for i, r in enumerate(watch_show, 1):
            out.extend(_watch_block(i, r))
            out.append(SEP_THIN)

    # ═══════════════════ 四、REVERSAL FUNNEL ═══════════════════
    out.append("# 四、REVERSAL FUNNEL ── 全池结构漏斗（§29）")
    out.append(SEP_THIN)
    dist: Dict[str, int] = {}
    for r in results:
        dist[r.state] = dist.get(r.state, 0) + 1
    for st in FUNNEL_ORDER:
        n = dist.get(st, 0)
        if st in (STATE_HOLD, STATE_EXIT, STATE_INVALIDATED):
            continue
        bar = "█" * min(50, n) if n else ""
        out.append(f"{st:<24} {n:>3}  {bar}")

    # ═══════════════════ 五、FAILED LIST ═══════════════════
    out.append("# 五、FAILED LIST ── 失败结构及原因")
    out.append(SEP_THIN)
    failed = [r for r in results if r.state in (
        STATE_FAILED_REVERSAL, STATE_FAILED_BREAKOUT, STATE_FAILED_PULLBACK)]
    if not failed:
        out.append("无失败结构。")
    else:
        for i, r in enumerate(failed, 1):
            reason = r.cannot_buy_reason or r.conclusion or "结构失效"
            out.append(f"{i}. {r.name}({r.ts_code})  状态={r.state}  结构风险={r.structure_risk:.0f}")
            out.append(f"   原因: {reason}")

    # ═══════════════════ 六、最终市场结论 ═══════════════════
    out.append("")
    out.append("# 六、最终市场结论")
    out.append(SEP_THIN)
    out.append(f"NOW = {len(now)}")
    out.append(f"NEXT = {len(nxt)}")
    out.append(f"WATCH = {len(watch)}")
    out.append("")
    if len(now) == 0:
        out.append("当前没有符合最高胜率买点的股票。")
        if len(nxt) > 0:
            out.append(
                f"虽然当前没有PRIMARY_BUY，但存在{len(nxt)}只距离关键状态转换较近的NEXT候选，"
                "未来1~3个交易日存在进入交易窗口的可能。"
            )
    else:
        out.append(f"当前存在{len(now)}只符合最高胜率买点的股票。")
        if len(nxt) > 0:
            out.append(f"另有{len(nxt)}只NEXT候选可在未来1~3日跟踪。")
    out.append("")

    # ═══════════════════ Forward Test 附段（§31/§32）═══════════════════
    if forward_text:
        out.append("")
        out.append(forward_text)

    out.append(SEP)
    out.append("注: 状态优先级高于分数。未到交易阶段的股票即使评分高也不得进入NOW。")
    out.append("注: PRE_BREAKOUT不是买点，代表平台成熟、未来1~3日存在突破可能。")
    out.append(SEP)
    return "\n".join(out)


def v2_report_to_file(text: str, end_date: str = "", out_dir: Optional[str] = None) -> str:
    """保存 V2 报告到文件，返回文件路径。"""
    import os
    if out_dir is None:
        from .config import OUTPUT_DIR
        out_dir = OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"rib_v2_{end_date}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
