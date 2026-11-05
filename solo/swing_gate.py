# -*- coding: utf-8 -*-
"""V10.7.3 · 波段闸门 swing_gate —— 纯收紧型仓位 overlay（增量层）。

设计约束
--------
1. 不修改 v10 / v101 / V10.7 既有决策模块；只在 P1/P2 给出目标仓位之后
   再算一层「有效目标仓位」。v10/v101 源码 SHA256 由测试锁定。
2. 纯收紧：``w_eff = min(w_base, w_base × Π f_layer, hard_cap)``，
   任何一层都不会放大仓位，也不会突破状态权限 / 确认门控 / P0 风险上限。
3. 无未来数据：指数序列严格只取 ``trade_date <= 当日``；MA / 回撤窗口
   均为「含当日的向后窗口」；历史链严格只取 ``trade_date < 当日``。
4. 缺失即 UNKNOWN：信号样本不足时该层不收紧、也不宣称通过，
   写入审计并关闭主动加仓（不默认通过）。
5. 降仓立即执行（风险优先），升仓沿用 P1 的 10pp/日限速；
   闸门有自己的独立历史表，不污染 P1 平滑器链（A/B 可完全关闭）。

与实验脚本 ``_bt_swing_optimizer.py`` 的口径完全一致
----------------------------------------------------
* ``state_s2s5``   : 状态 in (S2, S5) -> ×0.3        【默认唯一启用】
* ``ma20_0.25``    : 收盘 <= MA20     -> ×0.25       【三层档；单独用可设 0.0】
* ``dd_brake``     : 60日回撤 <= -5%  -> ×0.3        【三层档】
* ``combo_ma20_s2s5_dd`` = 上述三者连乘，再 min(hard_cap)

两年（2025-2026，443 日）实测：S2/S5 过滤是唯一两年波段 alpha 均为正
的层（2025 +1.36pp / 2026 +0.98pp）；MA20 与回撤刹车在 2025 牛市为负，
故默认关闭，只留作熊市可选档。详见 _bt_swing_optimizer_metrics.json。
"""
import os
import sys
import math
import sqlite3
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import market_regime_v10 as v10

OK = "OK"
TIGHTEN = "TIGHTEN"
OFF = "OFF"
UNKNOWN = "UNKNOWN"

PASS = "PASS"
FAIL = "FAIL"

DEFAULT_BENCH = "000300.SH"


# ============================================================
# 数据结构
# ============================================================
@dataclass
class SwingGateAudit:
    """一次波段闸门计算的可对比记录（附加字段，不改 12 字段契约）。"""
    enabled: bool = True
    profile: str = "default"
    benchmark: str = DEFAULT_BENCH
    base_target: Optional[int] = None
    effective_target: Optional[int] = None
    effective_target_exact: Optional[float] = None
    target_after: Optional[int] = None
    hard_cap: Optional[int] = None
    factors: Dict[str, float] = field(default_factory=dict)
    layers: Dict[str, str] = field(default_factory=dict)
    signals: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "profile": self.profile,
            "benchmark": self.benchmark,
            "base_target": self.base_target,
            "effective_target": self.effective_target,
            "effective_target_exact": self.effective_target_exact,
            "target_after": self.target_after,
            "hard_cap": self.hard_cap,
            "factors": self.factors,
            "layers": self.layers,
            "signals": self.signals,
            "execution": self.execution,
            "missing": self.missing,
            "reasons": self.reasons,
            "notes": self.notes,
        }


@dataclass
class GateAdjust:
    """闸门调整量；由 v107 侧施加，本模块不修改传入的结果对象。"""
    enabled: bool = True
    effective_target: Optional[int] = None
    deny_active_add: bool = False
    audit: SwingGateAudit = field(default_factory=SwingGateAudit)
    history_record: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 独立历史链（严格只取 trade_date < 当日）
# ============================================================
HISTORY_TABLE = "swing_gate_state"


def _db():
    db = v10.STATE_DB
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
        trade_date TEXT PRIMARY KEY,
        effective_exact REAL,
        effective_target INTEGER,
        base_target INTEGER,
        state TEXT,
        factor REAL
    )""")
    return conn


def load_history(trade_date: str) -> Optional[Dict[str, Any]]:
    try:
        conn = _db()
        try:
            row = conn.execute(
                f"SELECT trade_date, effective_exact, effective_target, "
                f"base_target, state, factor FROM {HISTORY_TABLE} "
                f"WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                (str(trade_date),)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return {"trade_date": row[0], "effective_exact": row[1],
                "effective_target": row[2], "base_target": row[3],
                "state": row[4], "factor": row[5]}
    except Exception:
        return None


def save_history(trade_date: str, rec: Dict[str, Any]) -> None:
    if not rec:
        return
    try:
        conn = _db()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {HISTORY_TABLE} "
                f"(trade_date, effective_exact, effective_target, base_target, state, factor) "
                f"VALUES (?,?,?,?,?,?)",
                (str(trade_date), rec.get("effective_exact"),
                 rec.get("effective_target"), rec.get("base_target"),
                 rec.get("state"), rec.get("factor")))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _valid_history(hist, trade_date: str, dates: List[str],
                   max_gap_days: int):
    """过期历史链视为冷启动，避免用很久以前的有效仓位做限速。"""
    if not hist:
        return None
    d = str(hist.get("trade_date") or "")
    if not d or d >= str(trade_date):
        return None
    if dates:
        past = [x for x in dates if str(x) <= str(trade_date)]
        if d in past:
            gap = len(past) - 1 - past.index(d)
            if gap > max_gap_days:
                return None
    return hist


# ============================================================
# 指数信号（严格只用 <= 当日 的收盘价）
# ============================================================
def load_index_series(trade_date: str, code: str = DEFAULT_BENCH,
                      days: int = 80) -> List[Dict[str, Any]]:
    """从本地 index_daily_cache 读取 <= trade_date 的收盘价序列。

    失败返回空列表（调用方据此把指数相关层判为 UNKNOWN，不默认通过）。
    """
    db = os.path.join(os.path.dirname(BASE_DIR), "cache_daily", "stock_data.db")
    if not os.path.exists(db):
        return []
    try:
        conn = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro",
                               uri=True, timeout=30)
        try:
            rows = conn.execute(
                "SELECT trade_date, close FROM index_daily_cache "
                "WHERE ts_code=? AND trade_date<=? AND close IS NOT NULL "
                "ORDER BY trade_date DESC LIMIT ?",
                (code, str(trade_date), int(days))).fetchall()
        finally:
            conn.close()
        out = [{"trade_date": str(d), "close": float(c)}
               for d, c in reversed(rows) if c is not None]
        return [x for x in out if str(x["trade_date"]) <= str(trade_date)]
    except Exception:
        return []


def build_signals(series: List[Dict[str, Any]], cfg: dict) -> Dict[str, Any]:
    """与 _bt_swing_optimizer.build_signals 同口径：MA / 60日回撤。"""
    gcfg = cfg.get("swing_gate") or {}
    layers = gcfg.get("layers") or {}
    ma_win = int((layers.get("ma_gate") or {}).get("window", 20))
    dd_win = int((layers.get("drawdown_brake") or {}).get("window", 60))
    rows = [x for x in (series or []) if x.get("close") is not None]
    c = [float(x["close"]) for x in rows]
    out = {"samples": len(c), "close": (c[-1] if c else None),
           "last_date": (str(rows[-1]["trade_date"]) if rows else None)}
    out["ma"] = (sum(c[-ma_win:]) / ma_win if len(c) >= ma_win else None)
    out["ma_window"] = ma_win
    if c:
        win = c[-dd_win:]
        out["dd"] = c[-1] / max(win) - 1.0
    else:
        out["dd"] = None
    out["dd_window"] = dd_win
    return out


# ============================================================
# 主计算（纯函数：不修改传入的 V107Result）
# ============================================================
def _layer_enabled(gcfg: dict, name: str, default: bool = False) -> bool:
    """profile 优先于单层 enabled；便于一键切换默认档 / 三层档。"""
    prof = (gcfg.get("profiles") or {}).get(gcfg.get("profile", "default")) or {}
    if name in prof:
        return bool(prof[name])
    return bool((gcfg.get("layers") or {}).get(name, {}).get("enabled", default))


def compute(r, cfg: dict, base_target: Optional[int] = None,
            hard_cap: Optional[int] = None, series=None,
            history=None) -> GateAdjust:
    """算出波段闸门后的有效目标仓位，由 v107 侧施加。

    ``base_target``：P1/P2 之后的最终目标仓位（闸门的作用基准）。
    ``series``：可注入内存指数序列用于测试/回放；不传则只读本地库。
    ``history``：可注入内存历史链；不传则读独立表。
    """
    gcfg = dict(cfg.get("swing_gate") or {})
    enabled = bool(gcfg.get("enabled", True))
    profile = str(gcfg.get("profile", "default"))
    bench = str(gcfg.get("benchmark", DEFAULT_BENCH))
    td = str(getattr(r, "trade_date", "") or "")
    state = str(getattr(r, "state", "") or "")

    audit = SwingGateAudit(enabled=enabled, profile=profile, benchmark=bench)
    adj = GateAdjust(enabled=enabled, audit=audit)
    base = int(base_target if base_target is not None
               else (getattr(getattr(r, "plan", None), "target_position", 0) or 0))
    cap = int(hard_cap if hard_cap is not None
              else (getattr(getattr(r, "plan", None), "hard_cap", 100) or 100))
    audit.base_target = base
    audit.hard_cap = cap

    if not enabled:
        adj.effective_target = base
        audit.effective_target = base
        audit.effective_target_exact = float(base)
        audit.notes.append("swing_gate.enabled=false：本次未施加波段闸门（A/B 对照）")
        return adj

    # ---------- 指数信号（只读 <= 当日） ----------
    rows = series
    if rows is None:
        rows = load_index_series(td, bench)
    rows = [x for x in (rows or []) if str(x.get("trade_date", "")) <= td]
    sig = build_signals(rows, cfg)
    dates = [str(x.get("trade_date", "")) for x in rows]
    audit.signals = {
        "samples": sig["samples"], "close": sig["close"],
        "ma": sig["ma"], "ma_window": sig["ma_window"],
        "dd_pct": (round(sig["dd"] * 100, 4) if sig["dd"] is not None else None),
        "dd_window": sig["dd_window"], "last_date": sig["last_date"],
        "state": state,
    }

    layers = gcfg.get("layers") or {}
    factors: Dict[str, float] = {}
    deny = False

    # ---------- ① S2/S5 状态过滤（默认唯一启用，不依赖外部数据） ----------
    lname = "state_filter"
    lcfg = layers.get(lname) or {}
    if not _layer_enabled(gcfg, lname, True):
        audit.layers[lname] = OFF
    elif not state:
        audit.layers[lname] = UNKNOWN
        audit.missing.append("市场状态（状态过滤无法判定，不默认通过）")
        deny = True
    else:
        states = [str(x) for x in (lcfg.get("states") or ["S2", "S5"])]
        f = float(lcfg.get("factor", 0.3))
        hit = state in states
        factors[lname] = f if hit else 1.0
        audit.layers[lname] = TIGHTEN if hit else OK
        audit.reasons.append(
            f"状态 {state} {'属于' if hit else '不属于'} {states} "
            f"→ 因子 {factors[lname]:.2f}"
            + ("（两年样本外唯一双正 alpha 层）" if hit else ""))

    # ---------- ② MA 趋势闸门（可选；三层档用 0.25） ----------
    lname = "ma_gate"
    lcfg = layers.get(lname) or {}
    if not _layer_enabled(gcfg, lname, False):
        audit.layers[lname] = OFF
    elif sig["ma"] is None or sig["close"] is None:
        audit.layers[lname] = UNKNOWN
        audit.missing.append(f"MA{sig['ma_window']} 样本不足（指数收盘序列）")
        deny = True
    else:
        f = float(lcfg.get("factor", 0.25))
        hit = sig["close"] <= sig["ma"]
        factors[lname] = f if hit else 1.0
        audit.layers[lname] = TIGHTEN if hit else OK
        audit.reasons.append(
            f"收盘 {sig['close']:.2f} {'≤' if hit else '>'} "
            f"MA{sig['ma_window']} {sig['ma']:.2f} → 因子 {factors[lname]:.2f}")

    # ---------- ③ 60 日回撤刹车（可选） ----------
    lname = "drawdown_brake"
    lcfg = layers.get(lname) or {}
    if not _layer_enabled(gcfg, lname, False):
        audit.layers[lname] = OFF
    elif sig["dd"] is None:
        audit.layers[lname] = UNKNOWN
        audit.missing.append(f"{sig['dd_window']}日回撤样本不足")
        deny = True
    else:
        f = float(lcfg.get("factor", 0.3))
        th = float(lcfg.get("threshold_pct", -5.0)) / 100.0
        hit = sig["dd"] <= th
        factors[lname] = f if hit else 1.0
        audit.layers[lname] = TIGHTEN if hit else OK
        audit.reasons.append(
            f"{sig['dd_window']}日回撤 {sig['dd']*100:.2f}% "
            f"{'≤' if hit else '>'} {th*100:.1f}% → 因子 {factors[lname]:.2f}")

    audit.factors = {k: round(v, 4) for k, v in factors.items()}
    prod = 1.0
    for v in factors.values():
        prod *= v

    eff = min(float(base) * prod, float(base), float(cap))
    eff_before_smooth = eff

    # ---------- 降仓立即 / 升仓沿用 P1 限速 ----------
    scfg = gcfg.get("execution") or {}
    up_step = float(scfg.get("up_step_pct", 10))
    max_gap = int(scfg.get("max_gap_days", 5))
    hist = _valid_history(
        history if history is not None else load_history(td),
        td, dates, max_gap)
    prev = (float(hist["effective_exact"]) if hist
            and hist.get("effective_exact") is not None else None)
    exec_info: Dict[str, Any] = {"prev_effective": prev,
                                 "up_step_pct": up_step,
                                 "before_smooth": round(eff_before_smooth, 4),
                                 "action": "COLD_START"}
    if prev is not None:
        if eff > prev:
            if up_step > 0 and eff > prev + up_step:
                eff = prev + up_step
                exec_info["action"] = "UP_LIMITED"
                audit.reasons.append(
                    f"闸门放开导致有效仓位跳升 → 升仓限速 {up_step:.0f}pp/日")
            else:
                exec_info["action"] = "UP_OK"
        elif eff < prev:
            exec_info["action"] = "DOWN_IMMEDIATE"
            audit.reasons.append("降仓立即执行（风险优先，不平滑）")
        else:
            exec_info["action"] = "HOLD"
    else:
        audit.notes.append("闸门历史链不存在或已过期 → 冷启动，首日不做升仓限速")
    eff = max(0.0, min(eff, float(base), float(cap)))
    exec_info["after_smooth"] = round(eff, 4)
    audit.execution = exec_info

    adj.effective_target = int(round(eff))
    adj.effective_target_exact = round(eff, 4)
    audit.effective_target = adj.effective_target
    audit.effective_target_exact = adj.effective_target_exact
    adj.deny_active_add = bool(deny and gcfg.get("unknown_deny_active_add", True))
    if adj.deny_active_add:
        audit.reasons.append("存在 UNKNOWN 信号 → 关闭主动加仓（不默认通过）")

    adj.history_record = {
        "trade_date": td, "state": state,
        "effective_exact": adj.effective_target_exact,
        "effective_target": adj.effective_target,
        "base_target": base, "factor": round(prod, 6),
    }
    return adj


# ============================================================
# 报告渲染
# ============================================================
def render_gate_section(a: Optional[SwingGateAudit]) -> List[str]:
    if a is None:
        return []
    L = ["", "【波段闸门 swing_gate】"]
    if not a.enabled:
        L.append("  已关闭（swing_gate.enabled=false），本次为 A/B 对照")
        return L
    L.append(f"  档位 {a.profile}　基准 {a.benchmark}　"
             f"有效目标：{a.base_target}% → {a.effective_target}%"
             f"（硬上限 {a.hard_cap}%，只收紧不放宽）")
    cn = {"state_filter": "S2/S5 状态过滤", "ma_gate": "MA 趋势闸门",
          "drawdown_brake": "60日回撤刹车"}
    vs = {OK: "未触发", TIGHTEN: "触发收紧", OFF: "关闭", UNKNOWN: "UNKNOWN(不默认通过)"}
    for k in ("state_filter", "ma_gate", "drawdown_brake"):
        L.append(f"    · {cn[k]}：{vs.get(a.layers.get(k), a.layers.get(k, '—'))}"
                 f"　因子 {a.factors.get(k, '—')}")
    s = a.signals or {}
    L.append(f"  信号（≤当日）：状态 {s.get('state', 'UNKNOWN')} | "
             f"样本 {s.get('samples', 0)} 日 | 收盘 "
             f"{('%.2f' % s['close']) if s.get('close') is not None else 'UNKNOWN'} / "
             f"MA{s.get('ma_window', 20)} "
             f"{('%.2f' % s['ma']) if s.get('ma') is not None else 'UNKNOWN'} | "
             f"{s.get('dd_window', 60)}日回撤 "
             f"{('%.2f%%' % s['dd_pct']) if s.get('dd_pct') is not None else 'UNKNOWN'}")
    e = a.execution or {}
    L.append(f"  执行：{e.get('action', '—')}　前一日有效目标 "
             f"{('%.2f%%' % e['prev_effective']) if e.get('prev_effective') is not None else '无历史链'}"
             f"　升仓限速 {e.get('up_step_pct', 10)}pp/日（降仓立即）")
    for x in (a.reasons or [])[:5]:
        L.append(f"      · {x}")
    if a.missing:
        L.append("  UNKNOWN（缺失即不默认通过，主动加仓关闭）："
                 + "；".join(dict.fromkeys(a.missing)))
    L.append("  口径说明：有效目标为叠加闸门后的可执行仓位；"
             "三层档需在配置切 profile=swing3，MA/回撤层在 2025 牛市为负 alpha，默认不开。")
    return L
