# -*- coding: utf-8 -*-
"""V10.7 · P1/P2 仓位优化增量层（纯增量，v10/v101 源码一行不动）

对应 2026-11-04 仓位链路诊断的 P1 / P2 清单：

P1 · 可执行性
  ③ 仓位跳变无迟滞：200 日回放 78 天目标变化，最大单次 85pp，49 天 ≥20pp，
     3 日状态往返 14 次。状态机有 7 日投票+迟滞，仓位层直接查表把迟滞绕过。
     → ``position_smoother``：升仓限速、降仓立即、状态升级复用 easing/rebound
       确认、降仓后升仓冷却；只作用于 suggested，hard_cap 仍由权限决定。
  ④ 三项确认没进入仓位约束：确认 FAIL 但状态未变时，仓位上限一字不动。
     → easing 非 PASS 关闭主动加仓；rebound 非 PASS 且 state=S5 时
       hard_cap 回落到 S4 权限（补上「条件不完整继续保持观察」的原意）。

P2 · 语义与边界
  · 现金地板与满仓授权矛盾：S6 "满仓" 实为 90% → 明确为结算备付金并写进报告；
    提供可选豁免开关（默认关闭，不改变历史结论）。
  · 阶梯 off-by-one：``steps=[20,40,...]`` 且 cur 初值 0，当前 10% 给出
    next_step=30% 却写「阶梯 20→40」→ 阶梯前补 0，重算 current_step/next_step。
  · ``plan_ladder_add`` 的 c5「主线未发生变化 = True」是恒真占位符 →
    改为主线模块真实判定 + 前一日未恶化；数据缺失一律 UNKNOWN，不默认通过。
  · 风险预算与权限同源（同一信号双重计入）→ 引入**独立**的指数已实现波动率
    因子，只在波动显著高于滚动基准时收紧预算（波动翻倍、预算/仓位减半），
    clamp 到 [0.5, 1.0]，只收紧不放宽。
  · 建议区间 lo_ratio=0.5 硬编码 → 与确认强度、波动率联动并 clamp。

严格约束
--------
· 无未来数据：历史链只取 ``trade_date < 当日``；波动率序列截断到当日；
  基准中位数只用当日之前的 vol20 值。
· 缺失即 UNKNOWN：确认数据缺失不默认通过；主线模块/历史缺失 → c5 UNKNOWN；
  波动率样本不足 → 因子 1.0（不额外收紧，也不放宽）并标注。
· V10.1 原决策记录 ``r.base`` 完全不改动（ladder 修正用 deepcopy 另存）。
· ``position_optimizer.enabled=false`` 时一切计算不施加，供 A/B 对照。

用法（由 market_regime_v107.analyze_trade_date_v107 第 7c 步调用）::

    adj = compute(r, cfg, history=..., series=series,
                  current_position=current_position)
    # 由 v107 侧应用 target_override / risk_cap / extra_cap / lo_ratio 后重建 plan
"""
from __future__ import annotations

import os
import copy
import json
import math
import sqlite3
import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

import numpy as np

import market_regime_v101 as v101
import market_regime_v10 as v10
import position_p0_fix as p0fix

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

STATE_S4 = v101.STATE_EASING
STATE_S5 = v101.STATE_REPAIR


# ============================================================
# 数据结构
# ============================================================
@dataclass
class OptimizerAudit:
    """一次 P1/P2 优化的完整可对比记录（附加字段，不改 12 字段契约）。"""
    enabled: bool = True
    target_before: Optional[int] = None
    target_after: Optional[int] = None
    hard_cap_before: Optional[int] = None
    hard_cap_after: Optional[int] = None
    smoother: Dict[str, Any] = field(default_factory=dict)
    confirm_gates: Dict[str, Any] = field(default_factory=dict)
    volatility: Dict[str, Any] = field(default_factory=dict)
    range_ratio: Dict[str, Any] = field(default_factory=dict)
    ladder: Dict[str, Any] = field(default_factory=dict)
    cash_floor: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "target_before": self.target_before,
            "target_after": self.target_after,
            "hard_cap_before": self.hard_cap_before,
            "hard_cap_after": self.hard_cap_after,
            "smoother": self.smoother,
            "confirm_gates": self.confirm_gates,
            "volatility": self.volatility,
            "range_ratio": self.range_ratio,
            "ladder": self.ladder,
            "cash_floor": self.cash_floor,
            "missing": self.missing,
            "notes": self.notes,
        }


@dataclass
class PlanAdjust:
    """计算出的调整量；由 v107 侧施加，本模块不修改传入的结果对象。"""
    enabled: bool = True
    risk_cap: Optional[int] = None          # P0 风险上限 ∩ 波动率驱动上限
    extra_cap: Optional[int] = None         # 确认门控回落的硬上限
    lo_ratio: Optional[float] = None        # 动态建议区间下限比例
    target_override: Optional[int] = None   # 平滑后的目标仓位
    deny_active_add: bool = False
    deny_reasons: List[str] = field(default_factory=list)
    ladder_patch: Any = None                # deepcopy 后修正的 LadderResult
    cash_floor_note: str = ""
    history_record: Dict[str, Any] = field(default_factory=dict)
    audit: OptimizerAudit = field(default_factory=OptimizerAudit)


# ============================================================
# 历史链（严格只取 trade_date < 当日）
# ============================================================
def _opt_state_db():
    db = v10.STATE_DB
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS position_optimizer_state (
        trade_date TEXT PRIMARY KEY, state TEXT, target INTEGER, hard_cap INTEGER,
        raw_target INTEGER, cooldown_left INTEGER, mainline_ok INTEGER,
        easing TEXT, rebound TEXT, updated_at TEXT)""")
    conn.commit()
    return conn


def load_history(trade_date: str) -> Optional[Dict[str, Any]]:
    """从独立表读取最近一个**早于**当日的历史记录（无未来数据）。"""
    if not os.path.exists(v10.STATE_DB):
        return None
    try:
        conn = _opt_state_db()
        cur = conn.execute(
            "SELECT trade_date, state, target, hard_cap, raw_target, "
            "cooldown_left, mainline_ok, easing, rebound "
            "FROM position_optimizer_state WHERE trade_date < ? "
            "ORDER BY trade_date DESC LIMIT 1", (str(trade_date),))
        row = cur.fetchone()
        if not row:
            return None
        return {"trade_date": row[0], "state": row[1], "target": row[2],
                "hard_cap": row[3], "raw_target": row[4],
                "cooldown_left": row[5] or 0,
                "mainline_ok": (None if row[6] is None else bool(row[6])),
                "easing": row[7], "rebound": row[8]}
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_history(trade_date: str, rec: Dict[str, Any]) -> None:
    if not rec:
        return
    try:
        conn = _opt_state_db()
        conn.execute(
            "INSERT OR REPLACE INTO position_optimizer_state "
            "(trade_date, state, target, hard_cap, raw_target, cooldown_left, "
            " mainline_ok, easing, rebound, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(trade_date), rec.get("state"), int(rec.get("target") or 0),
             int(rec.get("hard_cap") or 0), int(rec.get("raw_target") or 0),
             int(rec.get("cooldown_left") or 0),
             (None if rec.get("mainline_ok") is None
              else int(bool(rec["mainline_ok"]))),
             rec.get("easing"), rec.get("rebound"),
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _valid_history(hist, trade_date, series, max_gap_days: int):
    """历史链必须严格早于当日，且间隔不超过 max_gap_days 个交易日。"""
    if not isinstance(hist, dict) or not hist.get("trade_date"):
        return None
    if str(hist["trade_date"]) >= str(trade_date):
        return None
    if max_gap_days and series:
        dates = [str(x.get("trade_date")) for x in series
                 if str(x.get("trade_date")) <= str(trade_date)]
        if dates and str(hist["trade_date"]) < dates[0]:
            return None                      # 比可用交易日序列还早 → 视为过期
        if str(hist["trade_date"]) in dates:
            gap = len(dates) - 1 - dates.index(str(hist["trade_date"]))
            if gap > max_gap_days:
                return None
    return hist


# ============================================================
# P2 · 波动率驱动的独立风险预算
# ============================================================
def volatility_factor(trade_date: str, cfg: dict,
                      series: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """独立波动率因子：波动显著高于滚动基准时收紧预算，只收紧不放宽。

    数据源为 ``market_micro.micro_daily`` 的核心指数日涨跌幅（与 ETF 代理
    止损距离不同源），避免「风险预算与状态权限由同一信号双重计入」。
    样本不足 → ``status=UNKNOWN, factor=1.0``，并在报告中标注。
    """
    vcfg = ((cfg.get("position_optimizer") or {}).get("volatility_budget") or {})
    window = int(vcfg.get("window", 20))
    base_win = int(vcfg.get("baseline_window", 60))
    activate = float(vcfg.get("activate_ratio", 1.5))
    fmin = float(vcfg.get("factor_min", 0.5))
    fmax = float(vcfg.get("factor_max", 1.0))
    min_base = int(vcfg.get("min_baseline_samples", 20))
    out = {"status": UNKNOWN, "factor": 1.0, "vol_today": None,
           "baseline_vol": None, "samples": 0, "window": window,
           "note": ""}
    if not vcfg.get("enabled", True):
        out["status"] = "OFF"
        out["note"] = "volatility_budget.enabled=false（A/B 对照）"
        return out
    rows = series
    if rows is None:
        try:
            import market_micro as micro
            rows = micro.load_series(trade_date, window + base_win + 10)
        except Exception:
            rows = None
    rows = [x for x in (rows or [])
            if str(x.get("trade_date", "")) <= str(trade_date)
            and x.get("index_chg") is not None]
    if len(rows) < window + min_base:
        out["note"] = (f"指数涨跌样本 {len(rows)} 条 < {window + min_base} 条 "
                       "→ UNKNOWN，不额外收紧也不放宽")
        return out

    chg = np.asarray([float(x["index_chg"]) for x in rows], dtype=float)
    dates = [str(x["trade_date"]) for x in rows]
    vol_seq = [(dates[i], float(np.std(chg[i - window + 1:i + 1], ddof=1)))
               for i in range(window - 1, len(chg))]
    prior = [v for (d, v) in vol_seq[:-1]]       # 不含当日 → 无未来数据
    if len(prior) < min_base:
        out["note"] = f"历史 vol{window} 样本 {len(prior)} 条不足 → UNKNOWN"
        return out
    baseline = float(np.median(np.asarray(prior[-base_win:])))
    vol_today = vol_seq[-1][1]
    out.update({"samples": len(prior), "vol_today": round(vol_today, 4),
                "baseline_vol": round(baseline, 4), "status": "OK"})
    if baseline <= 0 or vol_today <= 0:
        out["note"] = "波动率非正值 → 因子 1.0（不做调整）"
        return out
    if vol_today <= baseline * activate:
        out["note"] = (f"当日 vol{window}={vol_today:.3f}% ≤ 基准 "
                       f"{baseline:.3f}%×{activate:.2f} → 未触发收紧")
        return out
    factor = min(fmax, max(fmin, baseline / vol_today))
    out["factor"] = round(factor, 4)
    out["status"] = "TIGHTEN"
    out["note"] = (f"当日 vol{window}={vol_today:.3f}% 高于滚动基准 "
                   f"{baseline:.3f}%×{activate:.2f} → 预算因子 {factor:.3f}"
                   "（波动翻倍、预算/仓位减半）")
    return out


def _base_budget(state: str, authority, cfg: dict) -> Optional[float]:
    pr = cfg.get("portfolio_risk") or {}
    budget = v101._risk_budget_for_state(state, authority, pr)
    if budget is None:
        budget = float(pr.get("max_total_risk_pct", 3.0) or 3.0)
    return float(budget)


# ============================================================
# P2 · 阶梯 off-by-one 与 c5 恒真占位符修正
# ============================================================
def _mainline_passed(r) -> Optional[bool]:
    try:
        mods = r.base.strong.modules if r.base and r.base.strong else None
        m = (mods or {}).get("主线持续性")
        if m is None:
            return None
        v = getattr(m, "passed", None)
        return bool(v) if isinstance(v, (bool, np.bool_)) else None
    except Exception:
        return None


def build_ladder_patch(r, cfg: dict, current_position: Optional[int],
                       hist: Optional[Dict[str, Any]],
                       final_target: Optional[int] = None,
                       enabled: bool = True) -> Tuple[Any, Dict[str, Any]]:
    """在 deepcopy 上修正 LadderResult，V10.1 原决策记录保持一字不改。"""
    info: Dict[str, Any] = {"enabled": enabled, "applied": False,
                            "next_step_before": None, "next_step_after": None,
                            "c5": UNKNOWN, "checks": {}, "reasons": []}
    src = getattr(r.base, "ladder", None) if r.base is not None else None
    if src is None:
        info["reasons"].append("V10.1 未生成阶梯结果，跳过修正")
        return None, info
    patched = copy.deepcopy(src)
    info["next_step_before"] = getattr(src, "next_step", None)
    if not enabled:
        info["reasons"].append("ladder_patch.enabled=false（A/B 对照）")
        return patched, info

    lcfg = ((cfg.get("position_optimizer") or {}).get("ladder_patch") or {})
    try:
        steps = list(cfg["ladder"]["steps"])
    except Exception:
        steps = [20, 40, 60, 80, 100]
    steps0 = ([0] + steps) if lcfg.get("prepend_zero_step", True) else list(steps)
    max_step = int(getattr(patched, "max_single_step_pct", 20) or 20)
    info["steps_before"] = steps
    info["steps_after"] = steps0

    # ---- ① off-by-one：cur 初值 0 时把「未达第一档」误当成「已在第一档」 ----
    if current_position is not None:
        cur = 0
        for i, s in enumerate(steps0):
            if current_position >= s:
                cur = i
        patched.current_step = cur
        if cur >= len(steps0) - 1:
            patched.next_step = None
            patched.can_add = False
            patched.reason = f"已在最高阶梯（{steps0[-1]}%），无加仓空间"
        else:
            nxt = steps0[cur + 1]
            if nxt - current_position > max_step:
                nxt = current_position + max_step
            patched.next_step = nxt
            patched.reason = (f"阶梯修正：{steps0[cur]}%→{steps0[cur+1]}%"
                              f"（单次上限 {max_step}pp，目标 {nxt}%）")
        info["current_step_after"] = cur
        info["next_step_after"] = patched.next_step
    else:
        patched.can_add = False
        patched.next_step = None
        patched.reason = "未提供当前实际仓位 → UNKNOWN，不规划加仓阶梯"
        info["reasons"].append("当前仓位缺失 → 阶梯 UNKNOWN，不默认通过")

    # ---- ② c5 恒真占位符 → 主线真实判定 + 前一日未恶化 ----
    checks = dict(getattr(patched, "checks", None) or {})
    today_ok = _mainline_passed(r)
    prev_ok = hist.get("mainline_ok") if isinstance(hist, dict) else None
    if not lcfg.get("c5_requires_mainline", True):
        c5 = True
        info["c5"] = "PASS(配置关闭校验)"
    elif today_ok is None or prev_ok is None:
        c5 = None                       # UNKNOWN：不得默认通过
        info["c5"] = UNKNOWN
        info["reasons"].append("主线持续性模块或前一日记录缺失 → c5 UNKNOWN")
    else:
        c5 = bool(today_ok)
        if lcfg.get("mainline_no_deterioration", True) and prev_ok and not today_ok:
            c5 = False
        info["c5"] = "PASS" if c5 else FAIL
    checks["主线未发生变化"] = c5
    if final_target is not None and patched.next_step is not None:
        checks["目标仓位支持本级"] = bool(final_target >= patched.next_step)
    patched.checks = checks

    unknown = [k for k, v in checks.items() if v is None]
    failed = [k for k, v in checks.items() if v is False]
    if unknown or failed:
        patched.can_add = False
        bits = []
        if failed:
            bits.append("未通过：" + "、".join(failed))
        if unknown:
            bits.append("UNKNOWN（不默认通过）：" + "、".join(unknown))
        patched.reason = "加仓检查" + "；".join(bits)
    else:
        patched.can_add = bool(patched.next_step is not None)
        if patched.can_add:
            patched.reason += "；六项检查全部通过（含主线未发生变化的真实判定）"
        else:
            patched.reason += "；检查通过但无下一阶梯"
    info["checks"] = {k: ("UNKNOWN" if v is None else bool(v))
                      for k, v in checks.items()}
    info["can_add"] = bool(patched.can_add)
    info["applied"] = True
    if info["next_step_before"] != patched.next_step:
        info["reasons"].append(
            f"next_step {info['next_step_before']} → {patched.next_step}（阶梯前补 0）")
    return patched, info


# ============================================================
# P1 · 平滑器
# ============================================================
def _verdict(obj) -> str:
    v = getattr(obj, "verdict", None) if obj is not None else None
    return v if v in (PASS, FAIL, UNKNOWN) else UNKNOWN


def _state_cap(state: str, cfg: dict) -> int:
    try:
        return int(v101.position_authority(state, cfg).max_position)
    except Exception:
        return 0


def smooth_target(raw: int, state: str, hist, cfg: dict,
                  easing: str, rebound: str, gate_blocked: List[str],
                  series=None, trade_date: str = "") -> Dict[str, Any]:
    """升仓限速 / 降仓立即 / 状态升级复用确认 / 降仓冷却。"""
    scfg = ((cfg.get("position_optimizer") or {}).get("smoother") or {})
    up = int(scfg.get("up_step_pct", 10))
    up_confirm = int(scfg.get("up_step_on_confirmed_upgrade_pct", 20))
    cooldown_days = int(scfg.get("cooldown_days_after_down", 2))
    out = {"raw": int(raw), "prev": None, "smoothed": int(raw),
           "action": "COLD_START", "step": None, "cooldown_left": 0,
           "state_upgrade": False, "confirm_ok": None,
           "reasons": ["无有效历史链 → 冷启动，不平滑首日目标"]}
    if hist is None:
        return out
    prev = hist.get("target")
    if prev is None:
        return out
    prev = int(prev)
    out["prev"] = prev
    prev_state = hist.get("state")
    cap = _state_cap(state, cfg)
    prev_cap = _state_cap(prev_state, cfg) if prev_state else None
    upgrade = bool(prev_cap is not None and cap > prev_cap)
    out["state_upgrade"] = upgrade
    cooldown = max(0, int(hist.get("cooldown_left") or 0) - 1)

    # 状态升级复用确认：门控已否决 / S4 需 easing / S5 需 rebound
    confirm_ok = True
    if upgrade and scfg.get("state_upgrade_requires_confirm", True):
        if gate_blocked:
            confirm_ok = False
            out["reasons"].append("状态门控已否决升级 → 不升仓")
        elif state == STATE_S4 and easing != PASS:
            confirm_ok = False
            out["reasons"].append(f"升级到 {state} 但卖压衰减确认 {easing} → 不升仓")
        elif state == STATE_S5 and (easing != PASS or rebound != PASS):
            confirm_ok = False
            out["reasons"].append(
                f"升级到 {state} 但卖压衰减 {easing}/反弹 {rebound} → 不升仓")
        else:
            out["reasons"].append("状态升级确认通过 → 允许较快升仓")
    out["confirm_ok"] = confirm_ok

    if raw <= prev:
        out["action"] = "DOWN" if raw < prev else "HOLD"
        out["smoothed"] = int(raw)
        out["step"] = 0
        if raw < prev:
            cooldown = cooldown_days
            out["reasons"].append(
                f"降仓 {prev}%→{raw}% 立即执行（风险优先），升仓冷却 {cooldown} 日")
        else:
            out["reasons"].append("目标持平，保持前一日仓位路径")
    else:
        if not confirm_ok:
            step = 0
            out["action"] = "UP_BLOCKED"
        elif cooldown > 0:
            step = 0
            out["action"] = "UP_COOLDOWN"
            out["reasons"].append(f"降仓冷却中（剩 {cooldown} 日）→ 暂不升仓")
        else:
            step = up_confirm if upgrade else up
            out["action"] = "UP_LIMITED"
            out["reasons"].append(
                f"升仓限速 {step}pp/日（{'确认通过的状态升级' if upgrade else '普通升仓'}）")
        out["step"] = step
        out["smoothed"] = int(min(raw, prev + step))
        if out["smoothed"] < raw:
            out["reasons"].append(
                f"目标由 {raw}% 平滑至 {out['smoothed']}%（前一日 {prev}%）")
    out["cooldown_left"] = cooldown
    return out


# ============================================================
# 主计算（纯函数：不修改传入的 V107Result）
# ============================================================
def compute(r, cfg: dict, history=None, series=None,
            current_position: Optional[int] = None,
            final_hard_cap: Optional[int] = None) -> PlanAdjust:
    """算出 P1/P2 的全部调整量，由 v107 侧施加。"""
    ocfg = dict(cfg.get("position_optimizer") or {})
    enabled = bool(ocfg.get("enabled", True))
    audit = OptimizerAudit(enabled=enabled)
    adj = PlanAdjust(enabled=enabled, audit=audit)
    if r.plan is not None:
        audit.target_before = int(r.plan.target_position)
        audit.hard_cap_before = int(r.plan.hard_cap)
    if not enabled:
        adj.risk_cap = (r.p0.risk_cap if getattr(r, "p0", None) is not None else None)
        audit.notes.append("position_optimizer.enabled=false：本次未施加 P1/P2（A/B 对照）")
        return adj

    td = str(getattr(r, "trade_date", "") or "")
    scfg = ocfg.get("smoother") or {}
    max_gap = int(scfg.get("max_gap_days", 5))
    gap_series = series
    if gap_series is None and history is not None:
        try:
            import market_micro as micro
            gap_series = micro.load_series(td, max_gap + 3)
        except Exception:
            gap_series = None
    hist = _valid_history(history, td, gap_series, max_gap)
    easing = _verdict(getattr(r, "easing", None))
    rebound = _verdict(getattr(r, "rebound", None))
    state = getattr(r, "state", "") or ""

    # ---------- P2 · 独立波动率驱动的预算 ----------
    vol = volatility_factor(td, cfg, series=series)
    audit.volatility = vol
    p0_cap = (r.p0.risk_cap if getattr(r, "p0", None) is not None else None)
    adj.risk_cap = p0_cap
    if vol["status"] == "TIGHTEN":
        sd = p0fix.resolve_stop_distance(getattr(r, "portfolio", None), cfg)
        extra = p0fix.cost_extra(cfg)
        budget = _base_budget(state, getattr(r, "authority", None), cfg)
        if sd is None or budget is None or budget <= 0:
            vol["status"] = UNKNOWN
            vol["factor"] = 1.0
            vol["note"] += "；止损距离/预算缺失 → UNKNOWN，不收紧"
            audit.missing.append("波动率预算所需的止损距离或风险预算")
        else:
            eff_budget = budget * float(vol["factor"])
            vol_cap = p0fix.risk_cap_from_budget(
                eff_budget, sd, extra,
                max_cap=int((cfg.get("p0_fixes") or {}).get("max_risk_cap", 100)))
            vol["budget_base"] = round(budget, 3)
            vol["budget_effective"] = round(eff_budget, 3)
            vol["risk_cap"] = vol_cap
            vol["risk_cap_p0"] = p0_cap
            adj.risk_cap = (vol_cap if p0_cap is None else min(int(p0_cap), vol_cap))
    elif vol["status"] == UNKNOWN:
        audit.missing.append("指数已实现波动率（样本不足）")

    # ---------- P1 · ④ 三项确认进入仓位约束 ----------
    gcfg = ocfg.get("confirm_gates") or {}
    unknown_blocks = bool(gcfg.get("unknown_does_not_pass", True))
    easing_bad = easing != PASS if unknown_blocks else easing == FAIL
    rebound_bad = rebound != PASS if unknown_blocks else rebound == FAIL
    if gcfg.get("easing_not_pass_deny_active_add", True) and easing_bad:
        adj.deny_active_add = True
        adj.deny_reasons.append(
            f"卖压衰减确认 {easing}（非 PASS）→ 关闭主动试探/加仓权限")
    if gcfg.get("rebound_not_pass_s5_cap_to_s4", True) \
            and state == STATE_S5 and rebound_bad:
        s4_cap = _state_cap(STATE_S4, cfg)
        adj.extra_cap = s4_cap
        adj.deny_reasons.append(
            f"反弹确认 {rebound}（非 PASS）且状态 {state} → 硬上限回落至 "
            f"S4 权限 {s4_cap}%（继续保持观察）")
    audit.confirm_gates = {
        "easing": easing, "rebound": rebound,
        "deny_active_add": adj.deny_active_add,
        "extra_cap": adj.extra_cap,
        "unknown_does_not_pass": unknown_blocks,
        "reasons": list(adj.deny_reasons),
    }

    # ---------- P1 · ③ 仓位平滑器 ----------
    raw = int(r.plan.target_position) if r.plan is not None else 0
    gate_blocked = list(getattr(getattr(r, "gate", None), "blocked", None) or [])
    sm = smooth_target(raw, state, hist, cfg, easing, rebound,
                       gate_blocked, series=series, trade_date=td)
    audit.smoother = sm
    smoothed = int(sm["smoothed"])
    # 硬上限预估：确认门控 + 风险上限 + 现金地板 + 授权（与实际重建同口径取 min）
    cap_est = final_hard_cap
    if cap_est is None and r.plan is not None:
        cap_est = int(r.plan.hard_cap)
        if adj.extra_cap is not None:
            cap_est = min(cap_est, int(adj.extra_cap))
        if adj.risk_cap is not None and (r.p0 is None or r.p0.risk_cap is None
                                         or adj.risk_cap < int(r.p0.risk_cap)):
            cap_est = min(cap_est, int(adj.risk_cap))
    if cap_est is not None and smoothed > int(cap_est):
        smoothed = int(cap_est)
        sm["reasons"].append(f"平滑目标被硬上限 {cap_est}% 压回（只收紧不放宽）")
    adj.target_override = smoothed
    sm["smoothed_final"] = smoothed

    # ---------- P2 · lo_ratio 与确认强度 / 波动率联动 ----------
    rcfg = ocfg.get("range_ratio") or {}
    base = float((cfg.get("position_semantics") or {}).get("range_low_with_signal", 0.5))
    lo = float(rcfg.get("base", base))
    bonus = float(rcfg.get("confirm_bonus", 0.05))
    penalty = float(rcfg.get("confirm_penalty", 0.05))
    det = {"base": lo, "adjustments": []}
    for name, v in (("卖压衰减", easing), ("反弹", rebound)):
        d = bonus if v == PASS else -penalty
        lo += d
        det["adjustments"].append(f"{name} {v}: {d:+.2f}")
    if vol.get("status") == "TIGHTEN":
        lo -= float(rcfg.get("high_vol_penalty", 0.10))
        det["adjustments"].append(f"高波动: -{rcfg.get('high_vol_penalty', 0.10)}")
    elif vol.get("status") == "OK" and vol.get("vol_today") \
            and vol.get("baseline_vol") \
            and float(vol["vol_today"]) < float(vol["baseline_vol"]) * 0.7:
        lo += float(rcfg.get("low_vol_bonus", 0.05))
        det["adjustments"].append("低波动: +0.05")
    elif vol.get("status") == UNKNOWN:
        det["adjustments"].append("波动率 UNKNOWN: 0.00（不放宽）")
        audit.missing.append("建议区间动态化所需的波动率判定")
    lo = max(float(rcfg.get("min", 0.3)), min(float(rcfg.get("max", 0.7)), lo))
    det["final"] = round(lo, 3)
    det["clamp"] = [rcfg.get("min", 0.3), rcfg.get("max", 0.7)]
    adj.lo_ratio = round(lo, 3)
    audit.range_ratio = det

    # ---------- P2 · 现金地板语义 ----------
    ccfg = ocfg.get("cash_floor") or {}
    hard_now = int(r.plan.hard_cap) if r.plan is not None else 0
    auth_res = getattr(getattr(r, "authorization", None), "result", "")
    pr = cfg.get("portfolio_risk") or {}
    min_cash = float(pr.get("min_cash_pct", 10.0) or 10.0)
    is_s6 = (state == v101.STATE_STRONG)
    is_full = (auth_res == v101.FULL_ALLOWED)
    cf = {"min_cash_pct": min_cash, "state": state,
          "full_authorization": auth_res, "hard_cap": hard_now,
          "waived": False, "note": ""}
    if is_s6 and is_full and hard_now < 100 and ccfg.get("enabled", True):
        cf["note"] = (f"「满仓」= 满仓授权通过且状态 {state} 允许 100% 可投资仓位；"
                      f"实际硬上限 {hard_now}% 是扣除 {min_cash:.0f}% "
                      "结算备付金/现金最低保留后的结果，并非授权被降级。"
                      "二者不矛盾：授权管「能不能」，现金地板管「留多少」")
        adj.cash_floor_note = cf["note"]
        audit.notes.append("现金地板与满仓授权文案已明确（默认值不变）")
    if (ccfg.get("enabled", True) and ccfg.get("waive_for_s6", False)
            and is_s6 and is_full
            and getattr(getattr(r, "portfolio", None), "ok", False)
            and easing == PASS and rebound == PASS):
        cf["waived"] = True
        cf["note"] += "；★ 已按 waive_for_s6=true 豁免现金地板（需三项确认全 PASS）"
        adj.cash_floor_note = cf["note"]
    audit.cash_floor = cf

    # ---------- P2 · 阶梯修正 ----------
    ladder_patch, ladder_info = build_ladder_patch(
        r, cfg, current_position, hist, final_target=smoothed,
        enabled=bool((ocfg.get("ladder_patch") or {}).get("enabled", True)))
    adj.ladder_patch = ladder_patch
    audit.ladder = ladder_info
    if ladder_info.get("c5") == UNKNOWN:
        audit.missing.append("主线持续性/前一日主线状态（c5 UNKNOWN）")

    # ---------- 历史链记录 ----------
    adj.history_record = {
        "trade_date": td, "state": state,
        "target": int(smoothed), "raw_target": int(raw),
        "hard_cap": int(final_hard_cap if final_hard_cap is not None else hard_now),
        "cooldown_left": int(sm.get("cooldown_left", 0)),
        "mainline_ok": _mainline_passed(r),
        "easing": easing, "rebound": rebound,
    }
    if sm["action"] == "COLD_START":
        audit.notes.append("历史链不存在或已过期 → 首日不平滑（不使用未来数据）")
    return adj


# ============================================================
# 报告渲染
# ============================================================
def render_optimizer_section(a: Optional[OptimizerAudit]) -> List[str]:
    if a is None:
        return []
    L = ["", "【P1/P2 仓位优化】"]
    if not a.enabled:
        L.append("  已关闭（position_optimizer.enabled=false），本次为 A/B 对照")
        return L
    L.append(f"  目标仓位：{a.target_before}% → {a.target_after}%"
             f"　硬上限：{a.hard_cap_before}% → {a.hard_cap_after}%")
    sm = a.smoother or {}
    if sm:
        L.append("  [P1-③ 平滑器] " + {
            "COLD_START": "冷启动（无历史链，不平滑）",
            "DOWN": "降仓立即执行", "HOLD": "目标持平",
            "UP_BLOCKED": "升仓被确认门控拦截",
            "UP_COOLDOWN": "升仓冷却中",
            "UP_LIMITED": "升仓限速",
        }.get(sm.get("action"), sm.get("action", "")))
        L.append(f"    原始目标 {sm.get('raw')}% / 前一日 {sm.get('prev')}%"
                 f" / 平滑后 {sm.get('smoothed_final', sm.get('smoothed'))}%"
                 f" / 单日限速 {sm.get('step')}pp / 冷却剩 {sm.get('cooldown_left')} 日")
        for x in (sm.get("reasons") or [])[:4]:
            L.append(f"      · {x}")
    cg = a.confirm_gates or {}
    if cg:
        L.append(f"  [P1-④ 确认门控] 卖压衰减 {cg.get('easing')} / 反弹 {cg.get('rebound')}"
                 f" → 主动加仓{'关闭' if cg.get('deny_active_add') else '按原规则'}"
                 f" / 硬上限回落 {cg.get('extra_cap') if cg.get('extra_cap') is not None else '无'}")
        for x in (cg.get("reasons") or []):
            L.append(f"      · {x}")
    v = a.volatility or {}
    if v:
        L.append(f"  [P2 波动率预算] {v.get('status')} 因子 {v.get('factor')}"
                 f" | vol{v.get('window')} {v.get('vol_today')}% / 基准 {v.get('baseline_vol')}%"
                 + (f" | 预算 {v.get('budget_base')}% → {v.get('budget_effective')}%"
                    f" | 风险上限 {v.get('risk_cap')}%" if v.get("risk_cap") is not None else ""))
        if v.get("note"):
            L.append(f"      · {v['note']}")
    rr = a.range_ratio or {}
    if rr:
        L.append(f"  [P2 建议区间] lo_ratio {rr.get('base')} → {rr.get('final')}"
                 f"（{'; '.join(rr.get('adjustments') or [])}）")
    ld = a.ladder or {}
    if ld.get("enabled"):
        L.append(f"  [P2 阶梯修正] next_step {ld.get('next_step_before')} → "
                 f"{ld.get('next_step_after')} | c5 主线未发生变化：{ld.get('c5')}"
                 f" | can_add={ld.get('can_add')}")
        for x in (ld.get("reasons") or [])[:3]:
            L.append(f"      · {x}")
    cf = a.cash_floor or {}
    if cf.get("note"):
        L.append(f"  [P2 现金地板] {cf['note']}")
    if a.missing:
        L.append("  UNKNOWN（缺失即不默认通过）：" + "；".join(dict.fromkeys(a.missing)))
    return L
