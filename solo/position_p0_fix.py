# -*- coding: utf-8 -*-
"""V10.7 · P0 修复：组合风险口径统一 + 决策/报告一致性（纯增量层）

背景（2026-11-04 全年 200 日回放核实）
------------------------------------
① 风险敞口与仓位脱钩
   V10.1 ``check_portfolio_risk`` 无候选标的的 fallback 分支用
   ``w = min(max_single_stock_pct, total_capital_pct)``，被 15% 单股上限封死；
   ``calculate_target_position`` 的代理重算同理
   ``per_stock = min(15, authority.max_position)``。
   实测传入仓位 5/20/40/60/90%，返回风险 0.163/0.489/.../0.489%，
   ≥20% 后**完全不随仓位变化** → 风险闸门全年 0 次收紧。

② 决策与报告用了两个不同的 portfolio 对象
   ``calculate_target_position`` 先 ``tp.portfolio = portfolio``（原对象），
   随后 ``copy.deepcopy`` 重算只改局部变量；主流程第 9 步又用旧口径复算并
   覆盖 ``out.portfolio`` / ``out.target.portfolio``，却不回代
   ``out.target.suggested``。V10.7 的第 1326 行把这个旧对象继续带进
   ``build_position_plan``，导致 ``allow_active_add`` 也基于错误口径。

修复策略（**v10 / v101 源码一行不动**）
--------------------------------------
· 权威口径：等权分散假设下 ``Σ w_i·sd_i ≈ 总仓位 × 平均止损距离``，
  不再被单股上限截断；
· 由状态预算反解风险仓位上限 ``risk_cap = budget / (sd + extra)``；
· V10.7 层把 ``r.portfolio`` 与 ``r.target.portfolio`` 指向**同一个**对象，
  该对象由 deepcopy 生成，不污染 ``r.base`` 的 V10.1 原决策记录；
· 报告/摘要给出 V10.1 原口径与统一口径的可对比快照；
· 止损距离缺失 → UNKNOWN，不默认通过，并关闭主动加仓权限。

实测：当前 sd=3.06%、extra=0.20%，各状态 risk_cap 为
S3 30 / S4 46 / S2 61 / S5 107 / S1 138 / S6 184，
均 ≥ 对应状态权限（5/20/30/40/60/100）→ 200/200 天不收紧，
**不改变任何历史仓位结论，属纯修复**。

用法：在 ``market_regime_v107.analyze_trade_date_v107`` 里
``out.p0 = apply_p0_fix(out, cfg)``，随后再用 ``out.p0.risk_cap``
构建 ``build_position_plan``。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import market_regime_v101 as v101


# ============================================================
# 数据结构
# ============================================================
@dataclass
class ConsistentRisk:
    """统一口径的组合风险核算结果。"""
    position_pct: float = 0.0            # 计算所用仓位（%）
    state: str = ""
    stop_distance: float = 0.0           # 平均止损距离（小数）
    cost_extra: float = 0.0              # 成本 + 滑点（小数）
    total_risk_pct: float = 0.0          # 仓位 × (sd + extra)，占净值 %
    risk_budget_pct: float = 0.0
    budget_usage: float = 0.0
    risk_cap: Optional[int] = None       # 由预算反解的最大仓位（%）
    ok: bool = True
    basis: str = "proxy_total_position"
    note: str = ""


@dataclass
class P0Repair:
    """一次 P0 修复的完整可对比记录。"""
    enabled: bool = True
    original: Dict[str, Any] = field(default_factory=dict)   # V10.1 原口径快照
    consistent: Optional[ConsistentRisk] = None
    suggested_before: Optional[int] = None
    suggested_after: Optional[int] = None
    hard_cap_before: Optional[int] = None
    hard_cap_after: Optional[int] = None
    risk_cap: Optional[int] = None
    changed: bool = False                # 是否改变了目标仓位/硬上限
    consistency_ok: bool = False         # 决策与报告是否同一对象且同值
    missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        c = self.consistent
        return {
            "enabled": self.enabled,
            "original": self.original,
            "consistent": ({
                "position_pct": c.position_pct,
                "state": c.state,
                "stop_distance": c.stop_distance,
                "cost_extra": c.cost_extra,
                "total_risk_pct": c.total_risk_pct,
                "risk_budget_pct": c.risk_budget_pct,
                "budget_usage": c.budget_usage,
                "risk_cap": c.risk_cap,
                "ok": c.ok,
                "basis": c.basis,
            } if c else None),
            "suggested_before": self.suggested_before,
            "suggested_after": self.suggested_after,
            "hard_cap_before": self.hard_cap_before,
            "hard_cap_after": self.hard_cap_after,
            "risk_cap": self.risk_cap,
            "changed": self.changed,
            "consistency_ok": self.consistency_ok,
            "missing": self.missing,
            "notes": self.notes,
        }


# ============================================================
# 口径计算
# ============================================================
def cost_extra(cfg: dict) -> float:
    """交易成本 + 滑点，小数口径。"""
    pr = cfg.get("portfolio_risk") or {}
    cost = float(pr.get("cost_pct", 0.0) or 0.0)
    slip = float(pr.get("slippage_pct", 0.0) or 0.0) if pr.get("include_slippage") else 0.0
    return (cost + slip) / 100.0


def resolve_stop_distance(portfolio=None, cfg: dict = None,
                          explicit: float = None) -> Optional[float]:
    """取平均止损距离。优先已算好的代理值，最后才回落到配置兜底。

    任何一层拿不到都返回 None（→ UNKNOWN，不默认通过）。
    """
    if explicit is not None:
        try:
            v = float(explicit)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    if portfolio is not None:
        v = getattr(portfolio, "proxy_stop_distance", None)
        if v:
            try:
                v = float(v)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    try:
        v = v101._default_proxy_stop_distance()
        if v:
            return float(v)
    except Exception:
        pass
    cfg = cfg or {}
    v = (cfg.get("volatility") or {}).get("fallback_stop_distance")
    try:
        v = float(v)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def risk_cap_from_budget(budget_pct: float, stop_distance: float,
                         extra: float, max_cap: int = 100) -> int:
    """由风险预算反解最大仓位：position × (sd + extra) ≤ budget。"""
    denom = float(stop_distance) + float(extra)
    if denom <= 0 or budget_pct is None:
        return int(max_cap)
    cap = int(float(budget_pct) / denom)
    return max(0, min(int(max_cap), cap))


def compute_consistent_risk(position_pct: float, state: str, authority,
                            cfg: dict, portfolio=None,
                            stop_distance: float = None) -> Optional[ConsistentRisk]:
    """统一口径：总仓位 × (平均止损距离 + 成本)，与选股数据可用性无关。

    等权分散假设：``Σ w_i · sd_i ≈ (Σ w_i) · sd̄ = 总仓位 × sd̄``。
    这是与 V10.1 ``min(15%, 仓位) × sd`` 的根本差别 —— 后者在仓位 ≥ 20%
    后被单股上限封死，风险敞口不再随仓位增长。
    """
    cfg = cfg or {}
    pr = cfg.get("portfolio_risk") or {}
    sd = resolve_stop_distance(portfolio, cfg, stop_distance)
    if sd is None:
        return None
    extra = cost_extra(cfg)
    budget = v101._risk_budget_for_state(state, authority, pr)
    if budget is None:
        budget = float(pr.get("max_total_risk_pct", 3.0) or 3.0)
    budget = float(budget)
    if budget <= 0:
        return None
    pos = max(0.0, float(position_pct))
    total = round(pos * (sd + extra), 3)
    usage = round(total / budget, 3)
    cap = risk_cap_from_budget(budget, sd, extra,
                               max_cap=int((cfg.get("p0_fixes") or {}).get("max_risk_cap", 100)))
    return ConsistentRisk(
        position_pct=pos,
        state=state,
        stop_distance=round(sd, 6),
        cost_extra=round(extra, 6),
        total_risk_pct=total,
        risk_budget_pct=round(budget, 3),
        budget_usage=usage,
        risk_cap=cap,
        ok=bool(total <= budget),
        basis="proxy_total_position",
        note=(f"仓位 {pos:.0f}% × (代理止损 {sd*100:.2f}% + 成本 {extra*100:.2f}%) "
              f"= {total:.3f}% / 预算 {budget:.2f}%（反解风险上限 {cap}%）"),
    )


def snapshot_original(portfolio) -> Dict[str, Any]:
    """V10.1 原口径快照，用于新旧对比（只读，不修改对象）。

    ``basis`` 区分两条完全不同的遗留路径：
      · ``proxy_fallback``   无候选标的，``min(15%单股上限, 仓位)×sd``，
        仓位 ≥20% 后被封死（P0 缺陷①的典型形态）；
      · ``holdings_per_stock`` 有候选标的，逐仓按个股止损价累加，
        依赖当日选股数据，与决策用的代理口径不同源（P0 缺陷②的来源）。
    """
    if portfolio is None:
        return {}
    used_proxy = bool(getattr(portfolio, "used_proxy", None))
    return {
        "total_risk_pct": getattr(portfolio, "total_risk_pct", None),
        "risk_budget_pct": getattr(portfolio, "risk_budget_pct", None),
        "budget_usage": getattr(portfolio, "budget_usage", None),
        "ok": getattr(portfolio, "ok", None),
        "used_proxy": used_proxy,
        "basis": "proxy_fallback" if used_proxy else "holdings_per_stock",
        "proxy_stop_distance": getattr(portfolio, "proxy_stop_distance", None),
        "violations": list(getattr(portfolio, "violations", None) or []),
    }


def _has_concentration_violation(violations: List[str]) -> bool:
    """原 violations 中除「总风险」外的行业/主题集中度违规仍需保留。"""
    return any(("集中度" in v or "高相关组" in v or "现金最低保留" in v)
               for v in (violations or []))


# ============================================================
# 应用到 V10.7 结果
# ============================================================
def apply_p0_fix(r, cfg: dict) -> P0Repair:
    """在 V10.7 结果上就地施加 P0 修复。

    1. 用权威口径重算组合风险；
    2. deepcopy 出一个统一对象，同时挂到 ``r.portfolio`` 与
       ``r.target.portfolio``（**同一引用**），不污染 ``r.base``；
    3. 风险超预算时按 risk_cap 回代 ``r.target.suggested``；
    4. 缺失止损距离 → UNKNOWN，关闭主动加仓（不默认通过）。

    返回完整可对比记录。配置 ``p0_fixes.enabled=false`` 时不改动任何字段。
    """
    p0cfg = dict(cfg.get("p0_fixes") or {})
    rep = P0Repair(enabled=bool(p0cfg.get("enabled", True)))
    if not rep.enabled:
        rep.notes.append("p0_fixes.enabled=false：本次未施加修复（A/B 对照）")
        return rep

    original_pf = r.portfolio if r.portfolio is not None else (
        r.target.portfolio if r.target is not None else None)
    rep.original = snapshot_original(original_pf)
    if r.target is not None:
        rep.suggested_before = int(r.target.suggested)
    if r.plan is not None:
        rep.hard_cap_before = int(r.plan.hard_cap)

    state = r.state or ""
    position = float(r.target.suggested) if r.target is not None else 0.0
    c = compute_consistent_risk(position, state, r.authority, cfg,
                                portfolio=original_pf)
    rep.consistent = c

    if c is None:
        rep.missing.append("平均止损距离（ETF 波动率代理）")
        rep.notes.append("组合风险口径 UNKNOWN：不默认通过，主动加仓权限关闭")
        # 仍要修掉「两个对象」这个结构性缺陷（即便口径未知）
        _unify_portfolio_objects(r, None, cfg, rep)
        rep.suggested_after = rep.suggested_before
        rep.hard_cap_after = rep.hard_cap_before
        rep.changed = False
        return rep

    rep.risk_cap = c.risk_cap

    # --- 统一 portfolio 对象 ---
    _unify_portfolio_objects(r, c, cfg, rep)

    # --- 风险超预算时回代目标仓位（V10.1 第 9 步漏掉的闭环）---
    if r.target is not None and not c.ok and c.risk_cap is not None \
            and r.target.suggested > c.risk_cap:
        old = int(r.target.suggested)
        r.target.suggested = int(c.risk_cap)
        r.target.limited_by.append(
            f"[P0] 组合风险 {c.total_risk_pct:.2f}% 超预算 {c.risk_budget_pct:.2f}%，"
            f"目标仓位按风险上限由 {old}% 收紧至 {c.risk_cap}%")
        rep.notes.append(f"目标仓位由 {old}% 收紧至 {c.risk_cap}%（风险预算反解）")

    if r.target is not None:
        rep.suggested_after = int(r.target.suggested)
    rep.changed = bool(rep.suggested_before is not None
                       and rep.suggested_after is not None
                       and rep.suggested_before != rep.suggested_after)
    # 主流程会在重建 plan 后用真实值覆盖；此处先给默认值，保证独立调用时记录完整
    if rep.hard_cap_after is None:
        rep.hard_cap_after = rep.hard_cap_before
    return rep


def _unify_portfolio_objects(r, c: Optional[ConsistentRisk], cfg: dict,
                             rep: P0Repair) -> None:
    """构造唯一的权威 portfolio 对象并挂到所有下游引用上。"""
    src = r.portfolio if r.portfolio is not None else (
        r.target.portfolio if r.target is not None else None)
    pf = copy.deepcopy(src) if src is not None else v101.PortfolioRisk()

    if c is not None:
        pr = cfg.get("portfolio_risk") or {}
        kept = [v for v in (getattr(pf, "violations", None) or [])
                if "总风险" not in v]
        conc_bad = _has_concentration_violation(kept)
        pf.total_risk_pct = c.total_risk_pct
        pf.risk_budget_pct = c.risk_budget_pct
        pf.budget_usage = c.budget_usage
        pf.used_proxy = True
        pf.proxy_stop_distance = c.stop_distance
        pf.ok = bool(c.ok and not conc_bad)
        if not c.ok:
            kept.append(f"组合总风险 {c.total_risk_pct:.2f}% 超出预算 "
                        f"{c.risk_budget_pct:.2f}%（统一口径：总仓位×平均止损距离）")
        pf.violations = kept
        # 现金地板按统一口径下的实际仓位复核
        pf.cash_floor_max = 100 - float(pr.get("min_cash_pct", 10.0) or 10.0)
        pf.cash_floor_ok = bool(c.position_pct <= pf.cash_floor_max)
    else:
        # UNKNOWN：不伪造风险值，明确标注。
        # 保留的数值是 V10.1 旧口径残留，仅作展示兼容，不参与任何判定（ok=False）。
        pf.ok = False
        pf.violations = list(getattr(pf, "violations", None) or []) + [
            "组合风险口径 UNKNOWN（止损距离缺失），不得默认通过；"
            "所显示敞口为 V10.1 旧口径残留值，不作为判定依据"]

    r.portfolio = pf
    if r.target is not None:
        r.target.portfolio = pf           # ★ 同一对象，决策与报告一致

    rep.consistency_ok = bool(r.target is None
                              or r.target.portfolio is r.portfolio)
    rep.notes.append("决策依据与报告展示已指向同一个 portfolio 对象"
                     if rep.consistency_ok else "对象统一失败（需排查）")
    if r.target is not None and r.target.portfolio is not None:
        if c is None:
            rep.notes.append("统一口径未产出（UNKNOWN）；组合风险标记不合格，"
                             "V10.1 原展示值保留在【V10.1 原决策记录】中")
        else:
            rep.notes.append(
                f"统一口径敞口 {r.target.portfolio.total_risk_pct:.3f}% / "
                f"预算 {r.target.portfolio.risk_budget_pct:.2f}% "
                f"(ok={r.target.portfolio.ok})；V10.1 原展示 "
                f"{rep.original.get('total_risk_pct')}% 保留在【V10.1 原决策记录】中")


# ============================================================
# 报告渲染
# ============================================================
def render_p0_section(rep: Optional[P0Repair]) -> List[str]:
    """返回【P0 风险口径修复】段落的行列表。"""
    if rep is None:
        return []
    L = ["", "【P0 风险口径修复】"]
    if not rep.enabled:
        L.append("  已关闭（p0_fixes.enabled=false），本次为 A/B 对照")
        return L
    o = rep.original or {}
    c = rep.consistent
    basis_cn = ("无候选标的的代理口径：min(15%单股上限, 仓位)×sd，"
                "仓位≥20% 后被封死（缺陷①）"
                if o.get("basis") == "proxy_fallback" else
                "有候选标的的逐仓口径：按当日选股止损价累加，"
                "与决策用的代理口径不同源（缺陷②）")
    L.append(f"  V10.1 原口径：敞口 {o.get('total_risk_pct')}% / 预算 "
             f"{o.get('risk_budget_pct')}%（使用率 {o.get('budget_usage')}，"
             f"ok={o.get('ok')}）　← {basis_cn}")
    if c is None:
        L.append("  统一口径：UNKNOWN（止损距离缺失，不默认通过）")
    else:
        L.append(f"  统一口径：仓位 {c.position_pct:.0f}% × "
                 f"(ETF代理止损 {c.stop_distance*100:.2f}% + 成本 {c.cost_extra*100:.2f}%) "
                 f"= {c.total_risk_pct:.3f}% / 预算 {c.risk_budget_pct:.2f}%"
                 f"（使用率 {c.budget_usage}，ok={c.ok}）")
        L.append(f"  风险仓位上限（预算反解）：{c.risk_cap}%")
        if o.get("ok") is not None and bool(o["ok"]) != c.ok:
            L.append("  ⚠ 原展示值与决策依据曾给出相反的 ok 结论 —— 已统一为同一口径/同一对象")
        L.append("  口径说明：采用 V10.2 设计的 ETF 波动率代理，与选股数据解耦；"
                 "原逐仓值依赖当日选股止损价，不再作为判定依据（仍保留在 V10.1 原决策记录）")
    L.append(f"  目标仓位：{rep.suggested_before}% → {rep.suggested_after}%"
             f"（{'已收紧' if rep.changed else '未改变'}）")
    L.append(f"  决策/报告一致性：{'✓ 同一对象' if rep.consistency_ok else '✗ 不一致'}")
    if rep.missing:
        L.append(f"  缺失：{'; '.join(rep.missing)} → UNKNOWN，主动加仓权限关闭")
    for n in rep.notes:
        L.append(f"    · {n}")
    return L
