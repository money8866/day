# -*- coding: utf-8 -*-
"""V10.7.4 · 主升段锁存 bull_latch —— 默认关闭的升仓增量层（纯增量）。

来源与口径
----------
2026-11-05 激进策略研究（``_bt_bull_aggressive.py``）结论：两年 +17.54% 的
1.5 倍杠杆档在 2021–2026 长历史全期转负（-4.43%），``exit=none`` 属样本内
过拟合。**建议落地的 recommended 档**：满仓（100%，不碰杠杆）+ 4% 跟踪止损
+ 锁存 10 日 + 锁存期 50pp/日升仓，两年 +10.87%、长历史同形态 +10.18%。

与实验脚本逐行同口径
--------------------
* 入场 ``s_trend`` = ``close > MA20`` 且 ``MA20 > MA20(5日前)``
  且 ``5日均 up_ratio > 0.58``；``up_ratio = up_count / total``
  （``micro_daily`` 原始列，不是 ``up/(up+down)``）。
* 日期序列以指数交易日为准，micro 按日期左连接；缺失 → 该日 up_ratio 为空。
* 锁存状态机逐日复刻 ``aggressive_lock(..., exit_mode="trail", hold=10,
  cooldown=0, trail_pct=0.04)``：信号触发置 left=10，信号持续则每日重置；
  退出判断在进入当日 left>0 时执行；退出后当日信号仍在可立即重入。
* 期望仓位 ``w_des = max(w_gate, target_cap) if locked else w_gate``；
  升仓限速 ``min(w_des, prev + step/100)``，锁存期 step=50pp、其余 10pp；
  降仓立即执行。权重强制 clamp 到 [0, 100] —— **不提供杠杆**。

风控约束（本层与 swing_gate 的根本差别）
----------------------------------------
swing_gate 是纯收紧 overlay；bull_latch 会申请**高于状态权限**的仓位，
因此不是一层 overlay，而是显式授权链：

1. 整层 ``enabled`` 默认 false（A/B 对照，行为与 10.7.3 逐字节一致）；
2. 突破 ``hard_cap`` 需同时满足：配置 ``break_hard_cap=true``、
   V10.7 ``authorization.result == FULL_ALLOWED``、
   ``portfolio.ok is True``、P0 统一口径重算 ``risk_cap >= 目标``
   且目标仓位下总风险不超预算、信号非 UNKNOWN；
3. 授权通过也只**放宽状态权限这一项**，现金地板 / 风险预算 / 确认门控 /
   满仓授权否决等其余约束仍逐项取 min；
4. 任一环节 UNKNOWN → 不默认通过、不入场、关闭主动加仓。

无未来数据：指数 / 微结构序列严格只取 ``trade_date <= 当日``；MA20、
MA20(5日前)、5日广度均为「含当日的向后窗口」；历史链严格只取
``trade_date < 当日``，存独立表 ``bull_latch_state``，不污染 P1 平滑器链
与 swing_gate 链。
"""
import os
import sys
import copy
import sqlite3
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import market_regime_v101 as v101
import position_p0_fix as p0fix

OK = "OK"
OFF = "OFF"
UNKNOWN = "UNKNOWN"
LATCH_ON = "ON"
LATCH_OFF = "OFF"

DEFAULT_BENCH = "000300.SH"

# 授权结果
NOT_NEEDED = "NOT_NEEDED"
BREAK_ALLOWED = "BREAK_ALLOWED"
BLOCKED_BY_CONFIG = "BLOCKED_BY_CONFIG"
BLOCKED_UNKNOWN = "BLOCKED_UNKNOWN"
BLOCKED_AUTH = "BLOCKED_AUTH"
BLOCKED_RISK = "BLOCKED_RISK"

BLOCKED_CN = {
    NOT_NEEDED: "未申请突破（未锁存）",
    BREAK_ALLOWED: "授权通过（P0 风险预算重算 + 满仓授权 + 组合风险合格）",
    BLOCKED_BY_CONFIG: "配置未开启突破 hard_cap",
    BLOCKED_UNKNOWN: "信号 UNKNOWN，不默认通过",
    BLOCKED_AUTH: "满仓授权非 FULL_ALLOWED",
    BLOCKED_RISK: "P0 风险预算 / 组合风险不允许",
}


# ============================================================
# 配置解析（profile 只覆盖 signal / exit / position 的差异项）
# ============================================================
def resolve_cfg(cfg: dict) -> dict:
    """把 profile 的差异项合并进 bull_latch 配置副本（不改原配置）。"""
    lcfg = copy.deepcopy((cfg or {}).get("bull_latch") or {})
    prof = (lcfg.get("profiles") or {}).get(lcfg.get("profile")) or {}
    for k in ("signal", "exit", "position"):
        if isinstance(prof.get(k), dict):
            merged = dict(lcfg.get(k) or {})
            merged.update(prof[k])
            lcfg[k] = merged
    return lcfg


# ============================================================
# 数据结构
# ============================================================
@dataclass
class BullLatchAudit:
    """一次主升段锁存计算的可对比记录（附加字段，不改 12 字段契约）。"""
    enabled: bool = False
    profile: str = "recommended"
    benchmark: str = DEFAULT_BENCH
    base_target: Optional[int] = None
    effective_target: Optional[int] = None
    effective_target_exact: Optional[float] = None
    target_after: Optional[int] = None
    hard_cap: Optional[int] = None
    authorized_hard_cap: Optional[int] = None
    signals: Dict[str, Any] = field(default_factory=dict)
    latch: Dict[str, Any] = field(default_factory=dict)
    authorization: Dict[str, Any] = field(default_factory=dict)
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
            "authorized_hard_cap": self.authorized_hard_cap,
            "signals": self.signals,
            "latch": self.latch,
            "authorization": self.authorization,
            "execution": self.execution,
            "missing": self.missing,
            "reasons": self.reasons,
            "notes": self.notes,
        }


@dataclass
class LatchAdjust:
    """锁存调整量；由 v107 侧施加，本模块不修改传入的结果对象。"""
    enabled: bool = False
    effective_target: Optional[int] = None
    effective_target_exact: Optional[float] = None
    authorized_hard_cap: Optional[int] = None
    deny_active_add: bool = False
    audit: BullLatchAudit = field(default_factory=BullLatchAudit)
    history_record: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 独立历史链（严格只取 trade_date < 当日）
# ============================================================
HISTORY_TABLE = "bull_latch_state"


def _db():
    db = v101_state_db()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
        trade_date TEXT PRIMARY KEY,
        left_days INTEGER,
        peak REAL,
        cooldown INTEGER,
        effective_exact REAL,
        effective_target INTEGER,
        base_target INTEGER,
        locked INTEGER,
        signal TEXT
    )""")
    return conn


def v101_state_db() -> str:
    """V10.0/V10.1 状态库路径（只读路径，不写它们的表）。"""
    import market_regime_v10 as v10
    return v10.STATE_DB


def load_history(trade_date: str) -> Optional[Dict[str, Any]]:
    try:
        conn = _db()
        try:
            row = conn.execute(
                f"SELECT trade_date, left_days, peak, cooldown, effective_exact, "
                f"effective_target, base_target, locked, signal "
                f"FROM {HISTORY_TABLE} WHERE trade_date < ? "
                f"ORDER BY trade_date DESC LIMIT 1", (str(trade_date),)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return {"trade_date": row[0], "left": row[1], "peak": row[2],
                "cool": row[3], "effective_exact": row[4],
                "effective_target": row[5], "base_target": row[6],
                "locked": row[7], "signal": row[8]}
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
                f"(trade_date, left_days, peak, cooldown, effective_exact, "
                f" effective_target, base_target, locked, signal) "
                f"VALUES (?,?,?,?,?,?,?,?,?)",
                (str(trade_date), rec.get("left"), rec.get("peak"),
                 rec.get("cool"), rec.get("effective_exact"),
                 rec.get("effective_target"), rec.get("base_target"),
                 1 if rec.get("locked") else 0, rec.get("signal")))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _valid_history(hist, trade_date: str, dates: List[str],
                   max_gap_days: int):
    """过期历史链视为冷启动；与 swing_gate._valid_history 同口径。"""
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
# 特征（严格只用 <= 当日 的收盘 / 广度）
# ============================================================
def _read_index(trade_date: str, code: str, days: int) -> List[Dict[str, Any]]:
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
        return [{"trade_date": str(d), "close": float(c)}
                for d, c in reversed(rows) if c is not None]
    except Exception:
        return []


def _read_micro(trade_date: str, days: int) -> Dict[str, Optional[float]]:
    """up_ratio = up_count / total（与实验脚本完全同口径）。"""
    db = os.path.join(BASE_DIR, "cache_micro", "micro_daily.db")
    if not os.path.exists(db):
        return {}
    try:
        conn = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro",
                               uri=True, timeout=20)
        try:
            rows = conn.execute(
                "SELECT trade_date, up_count, total FROM micro_daily "
                "WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?",
                (str(trade_date), int(days))).fetchall()
        finally:
            conn.close()
        out = {}
        for d, up, total in rows:
            try:
                t = float(total)
                out[str(d)] = (float(up) / t) if t > 0 else None
            except (TypeError, ValueError):
                out[str(d)] = None
        return out
    except Exception:
        return {}


def load_features(trade_date: str, code: str = DEFAULT_BENCH,
                  days: int = 90, index_series=None,
                  micro_series=None) -> List[Dict[str, Any]]:
    """按指数交易日对齐的特征序列（升序），严格 <= trade_date。

    ``index_series`` / ``micro_series`` 可注入内存数据用于测试与回放。
    micro 缺当日行 → up_ratio=None → 广度 UNKNOWN，不默认通过。
    """
    idx = index_series if index_series is not None else _read_index(trade_date, code, days)
    mic = micro_series if micro_series is not None else _read_micro(trade_date, days)
    if isinstance(mic, list):
        mm = {}
        for x in mic:
            d = str(x.get("trade_date", ""))
            up, total = x.get("up_count"), x.get("total")
            try:
                t = float(total)
                mm[d] = (float(up) / t) if (up is not None and t > 0) else None
            except (TypeError, ValueError):
                mm[d] = None
        mic = mm
    out = []
    for x in (idx or []):
        d = str(x.get("trade_date", ""))
        if not d or d > str(trade_date):
            continue
        c = x.get("close")
        try:
            c = float(c) if c is not None else None
        except (TypeError, ValueError):
            c = None
        out.append({"trade_date": d, "close": c,
                    "up_ratio": (mic or {}).get(d)})
    return out


def build_signals(rows: List[Dict[str, Any]], cfg: dict) -> Dict[str, Any]:
    """与 _bt_bull_entry.build_features / _bt_bull_aggressive 同口径。"""
    lcfg = resolve_cfg(cfg)
    sc = lcfg.get("signal") or {}
    ma_win = int(sc.get("ma_window", 20))
    lookback = int(sc.get("ma_lookback", 5))
    bwin = int(sc.get("breadth_window", 5))
    th = float(sc.get("breadth_threshold", 0.58))
    rows = [x for x in (rows or []) if str(x.get("trade_date", ""))]
    c = [x.get("close") for x in rows]
    b = [x.get("up_ratio") for x in rows]
    n = len(c)
    out = {"samples": n, "ma_window": ma_win, "ma_lookback": lookback,
           "breadth_window": bwin, "breadth_threshold": th,
           "last_date": (rows[-1]["trade_date"] if rows else None),
           "close": (c[-1] if n and c[-1] is not None else None),
           "missing": []}
    valid_close = all(v is not None for v in c)

    def _ma(win, end=None):
        if not valid_close or n < win:
            return None
        seg = c[-win:] if end is None else c[end - win:end]
        if len(seg) < win or any(v is None for v in seg):
            return None
        return sum(seg) / win

    out["ma"] = _ma(ma_win)
    out["ma10"] = _ma(10)
    out["ma20"] = _ma(20)
    # MA(win).shift(lookback)：第 i-lookback 行的 MA = mean(c[i-w-lookback+1:i-lookback+1])
    out["ma_prev5"] = (_ma(ma_win, end=n - lookback)
                       if n >= ma_win + lookback else None)
    tail = b[-bwin:] if n >= bwin else []
    out["breadth5"] = (sum(tail) / bwin
                       if len(tail) == bwin and all(v is not None for v in tail)
                       else None)

    if not valid_close or n < ma_win + lookback:
        out["missing"].append(f"指数收盘序列不足 {ma_win + lookback} 日")
    if out["breadth5"] is None:
        out["missing"].append(f"近 {bwin} 日 up_ratio 不完整（广度不可判定）")
    if out["ma"] is None or out["ma_prev5"] is None or \
            out["breadth5"] is None or out["close"] is None:
        out["signal"] = None
    else:
        out["signal"] = bool(out["close"] > out["ma"]
                             and out["ma"] > out["ma_prev5"]
                             and out["breadth5"] > th)
    return out


# ============================================================
# 授权链：突破 hard_cap 必须 P0 重算 + 满仓授权 + 组合风险合格
# ============================================================
def authorize_break(r, cfg: dict, target_pct: float,
                    signal_ok: bool) -> Dict[str, Any]:
    """返回授权审计；调用方据此决定是否允许突破状态权限。"""
    lcfg = resolve_cfg(cfg)
    ac = lcfg.get("authorization") or {}
    info = {
        "mode": NOT_NEEDED,
        "mode_cn": BLOCKED_CN[NOT_NEEDED],
        "target_pct": round(float(target_pct), 2),
        "break_hard_cap_cfg": bool(lcfg.get("break_hard_cap", False)),
        "full_authorization": str(getattr(getattr(r, "authorization", None),
                                          "result", "") or ""),
        "portfolio_ok": getattr(getattr(r, "portfolio", None), "ok", None),
        "risk_cap": None,
        "target_risk_pct": None,
        "risk_budget_pct": None,
        "budget_usage": None,
        "allowed_cap_pct": None,
        "note": "",
    }
    if not signal_ok:
        info["mode"], info["mode_cn"] = BLOCKED_UNKNOWN, BLOCKED_CN[BLOCKED_UNKNOWN]
        info["note"] = "信号 UNKNOWN 不默认通过：不入场、不申请突破"
        return info
    if not info["break_hard_cap_cfg"]:
        info["mode"], info["mode_cn"] = BLOCKED_BY_CONFIG, BLOCKED_CN[BLOCKED_BY_CONFIG]
        info["note"] = "break_hard_cap=false：锁存期只在原 hard_cap 内补足"
        return info

    if ac.get("require_full_allowed", True) and \
            info["full_authorization"] != v101.FULL_ALLOWED:
        info["mode"], info["mode_cn"] = BLOCKED_AUTH, BLOCKED_CN[BLOCKED_AUTH]
        info["note"] = "满仓授权非 FULL_ALLOWED，不得突破状态权限"
        return info
    if ac.get("require_portfolio_ok", True) and info["portfolio_ok"] is not True:
        info["mode"], info["mode_cn"] = BLOCKED_RISK, BLOCKED_CN[BLOCKED_RISK]
        info["note"] = "组合风险不合格或 UNKNOWN，不得突破"
        return info

    risk_cap = None
    if ac.get("require_p0_recompute", True):
        try:
            cr = p0fix.compute_consistent_risk(
                position_pct=float(target_pct),
                state=str(getattr(r, "state", "") or ""),
                authority=getattr(r, "authority", None),
                cfg=cfg, portfolio=getattr(r, "portfolio", None))
        except Exception as e:
            cr = None
            info["note"] = f"P0 统一口径重算异常：{e}"
        if cr is None:
            info["mode"], info["mode_cn"] = BLOCKED_RISK, BLOCKED_CN[BLOCKED_RISK]
            info["note"] = (info["note"] or "止损距离缺失") + \
                           " → 风险口径 UNKNOWN，不默认通过"
            return info
        info.update({
            "risk_cap": cr.risk_cap,
            "target_risk_pct": cr.total_risk_pct,
            "risk_budget_pct": cr.risk_budget_pct,
            "budget_usage": cr.budget_usage,
        })
        risk_cap = cr.risk_cap
        if not cr.ok or risk_cap is None or int(risk_cap) < int(round(target_pct)):
            info["mode"], info["mode_cn"] = BLOCKED_RISK, BLOCKED_CN[BLOCKED_RISK]
            info["note"] = (f"目标 {target_pct:.0f}% 下总风险 {cr.total_risk_pct:.3f}%"
                            f" / 预算 {cr.risk_budget_pct:.2f}%，"
                            f"反解 risk_cap={risk_cap}% → 不允许突破")
            return info
    else:
        try:
            risk_cap = int(getattr(getattr(r, "p0", None), "risk_cap", None))
        except (TypeError, ValueError):
            risk_cap = None
        info["risk_cap"] = risk_cap

    allowed = min(float(target_pct), float(risk_cap if risk_cap is not None
                                           else target_pct), 100.0)
    info["allowed_cap_pct"] = int(round(allowed))
    info["mode"], info["mode_cn"] = BREAK_ALLOWED, BLOCKED_CN[BREAK_ALLOWED]
    info["note"] = (f"满仓授权 {info['full_authorization']} + P0 风险预算反解 "
                    f"risk_cap={risk_cap}% + 组合风险合格 → 允许申请 "
                    f"{info['allowed_cap_pct']}%")
    return info


# ============================================================
# 主计算（纯函数：不修改传入的 V107Result）
# ============================================================
def compute(r, cfg: dict, base_target: Optional[int] = None,
            hard_cap: Optional[int] = None, series=None,
            history=None) -> LatchAdjust:
    """算出主升段锁存后的有效目标仓位，由 v107 第 7e 步施加。"""
    lcfg = resolve_cfg(cfg)
    enabled = bool(lcfg.get("enabled", False))
    profile = str(lcfg.get("profile", "recommended"))
    bench = str(lcfg.get("benchmark", DEFAULT_BENCH))
    td = str(getattr(r, "trade_date", "") or "")

    audit = BullLatchAudit(enabled=enabled, profile=profile, benchmark=bench)
    adj = LatchAdjust(enabled=enabled, audit=audit)
    # 保留浮点精度：生产链路传入的是整数目标，回放/实验注入的可能是
    # 带小数的闸门有效目标，int() 截断会带来最多 0.99pp 的虚假误差。
    base = float(base_target if base_target is not None
                 else (getattr(getattr(r, "plan", None), "target_position", 0) or 0))
    cap = float(hard_cap if hard_cap is not None
                else (getattr(getattr(r, "plan", None), "hard_cap", 100) or 100))
    audit.base_target = int(round(base))
    audit.hard_cap = int(round(cap))

    if not enabled:
        adj.effective_target = base
        adj.effective_target_exact = float(base)
        audit.effective_target = base
        audit.effective_target_exact = float(base)
        audit.notes.append("bull_latch.enabled=false：本次未施加主升段锁存（A/B 对照）")
        return adj

    ec = lcfg.get("exit") or {}
    pc = lcfg.get("position") or {}
    hold = int(ec.get("hold_days", 10))
    cool_cfg = int(ec.get("cooldown_days", 0))
    trail = float(ec.get("trail_pct", 0.04))
    exit_mode = str(ec.get("mode", "trail"))
    target = min(100.0, max(0.0, float(pc.get("target_pct", 100))))  # 不碰杠杆
    if float(pc.get("max_pct", 100)) > 100:
        audit.notes.append("配置 max_pct>100 已被强制 clamp 到 100（本层不提供杠杆）")
    up_fast = float(pc.get("up_step_latch_pct", 50))
    up_slow = float(pc.get("up_step_normal_pct", 10))
    max_gap = int((lcfg.get("execution") or {}).get("max_gap_days", 5))

    # ---------- 特征（只读 <= 当日） ----------
    rows = series if series is not None else load_features(td, bench)
    rows = [x for x in (rows or []) if str(x.get("trade_date", "")) <= td]
    sig = build_signals(rows, cfg)
    dates = [str(x.get("trade_date", "")) for x in rows]
    audit.signals = {
        "samples": sig["samples"], "close": sig["close"],
        "ma": sig["ma"], "ma_prev5": sig["ma_prev5"],
        "ma10": sig["ma10"], "ma20": sig["ma20"], "breadth5": sig["breadth5"],
        "breadth_threshold": sig["breadth_threshold"],
        "ma_window": sig["ma_window"], "ma_lookback": sig["ma_lookback"],
        "breadth_window": sig["breadth_window"], "last_date": sig["last_date"],
        "signal": sig["signal"], "state": str(getattr(r, "state", "") or ""),
    }
    signal_ok = sig["signal"] is not None
    unknown = not signal_ok
    effective_on = False   # 真正允许升仓的锁存（UNKNOWN 时强制不入场、不升仓）
    for x in sig["missing"]:
        audit.missing.append(x)
    if unknown:
        audit.reasons.append("信号 UNKNOWN（数据不足）→ 不入场、不默认通过、关闭主动加仓")

    # ---------- 锁存状态机（逐行对齐实验 aggressive_lock） ----------
    hist = _valid_history(
        history if history is not None else load_history(td),
        td, dates, max_gap)
    left = int(hist["left"]) if hist and hist.get("left") is not None else 0
    peak = (float(hist["peak"]) if hist and hist.get("peak") is not None else None)
    cool = int(hist["cool"]) if hist and hist.get("cool") is not None else 0
    c = sig["close"]
    exit_now = False
    if left > 0:
        if exit_mode == "trail" and c is not None:
            peak = c if peak is None else max(peak, c)
            if peak > 0 and c / peak - 1.0 <= -trail:
                exit_now = True
        elif exit_mode in ("ma10", "ma20"):
            ma = sig.get("ma10") if exit_mode == "ma10" else sig.get("ma20")
            if c is not None and ma is not None and c < ma:
                exit_now = True
    if cool > 0:
        cool -= 1
    if exit_now:
        left, peak, cool = 0, None, cool_cfg
        audit.reasons.append(
            f"退出触发（{exit_mode}）→ 立即解除锁存，降仓立即执行")
    if sig["signal"] is True and cool == 0:
        left = hold
        if peak is None and c is not None:
            peak = c
    on = left > 0
    if on:
        left -= 1
    else:
        peak = None
    effective_on = bool(on and not unknown)
    if on and not effective_on:
        audit.reasons.append("信号 UNKNOWN 期间维持锁存状态但不升仓（不默认通过）")
    audit.latch = {
        "locked": on, "position_active": effective_on,
        "left_after": left, "peak": peak,
        "cooldown_after": cool, "hold_days": hold,
        "exit_mode": exit_mode, "trail_pct": trail,
        "exit_triggered": exit_now,
        "prev_effective": (float(hist["effective_exact"])
                           if hist and hist.get("effective_exact") is not None
                           else None),
    }

    # ---------- 授权链 ----------
    auth = authorize_break(r, cfg, target, signal_ok) if effective_on else \
        dict(mode=NOT_NEEDED, mode_cn=BLOCKED_CN[NOT_NEEDED],
             target_pct=target, allowed_cap_pct=None,
             risk_cap=None, note="未有效锁存，不申请突破 hard_cap")
    audit.authorization = auth
    allow_break = (effective_on and auth.get("mode") == BREAK_ALLOWED
                   and auth.get("allowed_cap_pct") is not None)
    allowed_cap = (int(auth["allowed_cap_pct"]) if allow_break
                   else min(int(round(target)), cap))
    if auth.get("risk_cap") is not None:
        allowed_cap = min(float(allowed_cap), float(auth["risk_cap"]))
    allowed_cap = int(round(max(0.0, min(100.0, float(allowed_cap)))))
    if allow_break:
        adj.authorized_hard_cap = allowed_cap
        audit.authorized_hard_cap = allowed_cap
        audit.reasons.append(
            f"授权通过 → 申请把硬上限的状态权限项放宽到 {allowed_cap}%"
            f"（其余约束仍逐项取 min）")
    elif effective_on:
        audit.reasons.append(
            f"{auth.get('mode_cn', '未获授权')} → 锁存期目标不超过 "
            f"min(目标 {target:.0f}%, 硬上限 {cap:.0f}%)")

    # ---------- 期望仓位 + 升仓限速（复刻 aggressive_weight/apply_limit_lev） ----------
    w_des = max(float(base), float(allowed_cap)) if effective_on else float(base)
    step = up_fast if effective_on else up_slow
    prev = audit.latch["prev_effective"]
    exec_info: Dict[str, Any] = {"prev_effective": prev,
                                 "step_pct": step,
                                 "before_smooth": round(w_des, 4),
                                 "action": "COLD_START"}
    w = w_des
    if prev is not None:
        if w_des > prev:
            if step > 0 and w_des > prev + step:
                w = prev + step
                exec_info["action"] = "UP_LIMITED"
                audit.reasons.append(
                    f"锁存期升仓限速 {step:.0f}pp/日（其余 {up_slow:.0f}pp）")
            else:
                exec_info["action"] = "UP_OK"
        elif w_des < prev:
            exec_info["action"] = "DOWN_IMMEDIATE"
            audit.reasons.append("降仓立即执行（风险优先，不平滑）")
        else:
            exec_info["action"] = "HOLD"
    else:
        audit.notes.append("锁存历史链不存在或已过期 → 冷启动，首日不做升仓限速")
    w = max(0.0, min(float(w), 100.0))   # ★ 强制不碰杠杆
    exec_info["after_smooth"] = round(w, 4)
    audit.execution = exec_info

    adj.effective_target = int(round(w))
    adj.effective_target_exact = round(w, 4)
    audit.effective_target = adj.effective_target
    audit.effective_target_exact = adj.effective_target_exact
    adj.deny_active_add = bool(unknown and lcfg.get("unknown_deny_active_add", True))
    if adj.deny_active_add:
        audit.reasons.append("存在 UNKNOWN 信号 → 关闭主动加仓（不默认通过）")

    adj.history_record = {
        "trade_date": td, "left": left, "peak": peak, "cool": cool,
        "effective_exact": adj.effective_target_exact,
        "effective_target": adj.effective_target,
        "base_target": base, "locked": on,
        "signal": ("PASS" if sig["signal"] is True else
                   ("FAIL" if sig["signal"] is False else UNKNOWN)),
    }
    return adj


# ============================================================
# 报告渲染
# ============================================================
def render_latch_section(a: Optional[BullLatchAudit]) -> List[str]:
    if a is None:
        return []
    L = ["", "【主升段锁存 bull_latch】"]
    if not a.enabled:
        L.append("  已关闭（bull_latch.enabled=false），本次为 A/B 对照；"
                 "行为与 V10.7.3 一致")
        return L
    L.append(f"  档位 {a.profile}　基准 {a.benchmark}　"
             f"有效目标：{a.base_target}% → {a.effective_target}%"
             f"（硬上限 {a.hard_cap}%"
             + (f"，授权后申请 {a.authorized_hard_cap}%"
                if a.authorized_hard_cap is not None else "") + "）")
    s = a.signals or {}
    sig = s.get("signal")
    L.append(f"  信号（≤当日）：状态 {s.get('state', 'UNKNOWN')} | 样本 "
             f"{s.get('samples', 0)} 日 | 收盘 "
             f"{('%.2f' % s['close']) if s.get('close') is not None else 'UNKNOWN'} / "
             f"MA{s.get('ma_window', 20)} "
             f"{('%.2f' % s['ma']) if s.get('ma') is not None else 'UNKNOWN'} / "
             f"MA{s.get('ma_window', 20)}({s.get('ma_lookback', 5)}日前) "
             f"{('%.2f' % s['ma_prev5']) if s.get('ma_prev5') is not None else 'UNKNOWN'}"
             f" | {s.get('breadth_window', 5)}日广度 "
             f"{('%.4f' % s['breadth5']) if s.get('breadth5') is not None else 'UNKNOWN'}"
             f" / 阈值 {s.get('breadth_threshold', 0.58)}"
             f" | 判定 {('触发' if sig is True else ('未触发' if sig is False else 'UNKNOWN'))}")
    k = a.latch or {}
    L.append(f"  锁存：{LATCH_ON if k.get('locked') else LATCH_OFF}　剩余 "
             f"{k.get('left_after', 0)} 日 | 退出 {k.get('exit_mode', 'trail')}"
             f"（跟踪止损 {float(k.get('trail_pct', 0.04)) * 100:.1f}%，"
             f"峰值 {('%.2f' % k['peak']) if k.get('peak') is not None else '无'}）"
             f" | 冷却 {k.get('cooldown_after', 0)} 日"
             + (" | 本日触发退出" if k.get("exit_triggered") else ""))
    au = a.authorization or {}
    L.append(f"  授权：{au.get('mode_cn', au.get('mode', '—'))}"
             f" | 满仓授权 {au.get('full_authorization', '—')}"
             f" | P0 risk_cap {au.get('risk_cap', '—')}"
             f" | 目标风险/预算 "
             f"{au.get('target_risk_pct', '—')}/{au.get('risk_budget_pct', '—')}%")
    e = a.execution or {}
    L.append(f"  执行：{e.get('action', '—')}　前一日有效目标 "
             f"{('%.2f%%' % e['prev_effective']) if e.get('prev_effective') is not None else '无历史链'}"
             f"　本次限速 {e.get('step_pct', '—')}pp/日（降仓立即）")
    for x in (a.reasons or [])[:5]:
        L.append(f"      · {x}")
    if a.missing:
        L.append("  UNKNOWN（缺失即不默认通过，主动加仓关闭）："
                 + "；".join(dict.fromkeys(a.missing)))
    L.append("  口径：默认关闭做 A/B；开启后仍不碰杠杆（上限 100%），"
             "突破硬上限须 P0 风险预算重算 + 满仓授权 FULL_ALLOWED + 组合风险合格；"
             "两年 +10.87% / 长历史同形态 +10.18%，长历史仅用于判断信号可外推性。")
    return L
