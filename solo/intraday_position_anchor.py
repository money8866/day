# -*- coding: utf-8 -*-
"""V10.7.5 · 日内盘中 ↔ 盘后仓位口径桥接（纯收紧 / 只读 / fail-soft）

背景
----
盘后 ``market_regime_v107.py`` 已演进到 10.7.4::

    V10.1 决策 → P0 风险口径 → P1/P2 仓位优化 → 波段闸门 → 主升段锁存(默认关)

而盘中 ``_intraday_monitor.py`` 的仓位仍是 V10.5 / V10.7 初版链路::

    V10.0 状态机 → V10.1 权限表 → V10.7 状态门控 → 时点置信折扣

两者互不知晓，盘中可能给出**高于盘后最终有效目标**的建议（尤其波段闸门
收紧后）。本模块不重算任何盘后逻辑，只把「上一交易日盘后最终口径」作为
盘中仓位的**上限锚点**做一次 min()。

设计约束（务必保持）
--------------------
1. **严格只用 ``trade_date < 当日`` 的摘要**。当日盘后报告收盘后才产出，
   盘中读它等于使用未来数据（与 V10.7 全链路的无未来数据口径一致，
   亦与 ``_prev_snapshot.build(before_date=...)`` 的约定一致）。
2. **只收紧、绝不放大**：``cap_after <= cap_before`` 且不超过盘后有效目标。
3. **不跨日继承满仓授权**：``authorization.result`` / P0 风险预算 /
   ``authorized_hard_cap`` 每日盘后重算，盘中无权沿用昨日的 hard_cap 突破。
4. **盘中不重新触发主升段锁存**：不重算 MA20 / 广度信号、不申请升仓、
   不写 ``bull_latch_state``。锁存期目标仅作为「已被盘后确认的上限」出现，
   仍需受盘中状态门控与状态权限表约束。
5. **fail-soft**：摘要缺失 / 过期 / 损坏 / 字段不全时退回原盘中口径，
   并输出审计行，绝不抛异常阻断盘中监控。
6. 独立开关 ``intraday_bridge.enabled=false`` 可一键回退到 10.7.4 盘中口径，
   便于 A/B 对照。
"""
from __future__ import annotations

import datetime
import glob
import json
import math
import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

BASE = os.path.dirname(os.path.abspath(__file__))
# 盘后产物在仓库根目录 report_daily/（不是 solo/report_daily/）
DEFAULT_REPORT_DIR = os.path.normpath(os.path.join(BASE, os.pardir, "report_daily"))
SUMMARY_GLOB = "market_regime_v107_summary_*.json"
DATE_RE = re.compile(r"(\d{8})")

STATUS_CN = {
    "OK": "已加载",
    "DISABLED": "已关闭（A/B 对照）",
    "NO_ANCHOR": "无严格早于当日的盘后摘要",
    "STALE": "盘后摘要过期",
    "BAD_JSON": "盘后摘要不可读/解析失败",
    "NO_CAP": "盘后摘要缺少有效目标与硬上限",
    "NOT_LOADED": "未加载",
}

LAYER_CN = {
    "p0": "P0风险",
    "optimizer": "P1P2优化",
    "swing_gate": "波段闸门",
    "bull_latch": "主升段锁存",
}


# ============================================================
# 数据结构
# ============================================================
@dataclass
class PositionAnchor:
    trade_date: str
    enabled: bool = True
    status: str = "NOT_LOADED"
    basis_date: Optional[str] = None
    source: Optional[str] = None
    effective_target: Optional[float] = None
    hard_cap: Optional[float] = None
    anchor_cap: Optional[float] = None
    state: Optional[str] = None
    layers: Dict[str, bool] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.status == "OK" and self.anchor_cap is not None

    @property
    def status_cn(self) -> str:
        return STATUS_CN.get(self.status, self.status)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "status_cn": self.status_cn,
            "usable": self.usable,
            "basis_date": self.basis_date,
            "trade_date": self.trade_date,
            "source": self.source,
            "effective_target": self.effective_target,
            "hard_cap": self.hard_cap,
            "anchor_cap": self.anchor_cap,
            "state": self.state,
            "layers": dict(self.layers),
            "notes": list(self.notes),
            "error": self.error,
        }


# ============================================================
# 配置 / 工具
# ============================================================
def _view(anchor: Any):
    """允许把 ``as_dict()`` 的结果直接传给施加/渲染函数（报告链路用）。"""
    if anchor is None or isinstance(anchor, PositionAnchor):
        return anchor
    if isinstance(anchor, dict):
        return SimpleNamespace(
            enabled=bool(anchor.get("enabled")),
            status=anchor.get("status"),
            status_cn=(anchor.get("status_cn")
                       or STATUS_CN.get(anchor.get("status"), anchor.get("status"))),
            usable=bool(anchor.get("usable")),
            basis_date=anchor.get("basis_date"),
            trade_date=anchor.get("trade_date"),
            state=anchor.get("state"),
            effective_target=anchor.get("effective_target"),
            hard_cap=anchor.get("hard_cap"),
            anchor_cap=anchor.get("anchor_cap"),
            source=anchor.get("source"),
            layers=dict(anchor.get("layers") or {}),
            notes=list(anchor.get("notes") or []),
            error=anchor.get("error"),
        )
    return None


def resolve_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """读取 ``intraday_bridge`` 段；缺省值保证旧配置也能安全运行。"""
    c = dict((cfg or {}).get("intraday_bridge") or {})
    out = {
        "enabled": bool(c.get("enabled", True)),
        "max_age_days": int(c.get("max_age_days", 7) or 7),
        "report_dir": c.get("report_dir") or DEFAULT_REPORT_DIR,
    }
    return out


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _parse_range_upper(v: Any) -> Optional[float]:
    """``"8%~20%"`` → 20.0（取上界）；纯数字原样返回。"""
    if v is None:
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(v))]
    return max(nums) if nums else None


def _date_from_path(p: str) -> Optional[str]:
    m = DATE_RE.search(os.path.basename(p))
    return m.group(1) if m else None


def _days_between(d0: str, d1: str) -> Optional[int]:
    try:
        a = datetime.datetime.strptime(str(d0), "%Y%m%d").date()
        b = datetime.datetime.strptime(str(d1), "%Y%m%d").date()
        return (b - a).days
    except (TypeError, ValueError):
        return None


# ============================================================
# 载入盘后锚点
# ============================================================
def load_anchor(trade_date: str,
                cfg: Optional[Dict[str, Any]] = None,
                report_dir: Optional[str] = None) -> PositionAnchor:
    """读取**严格早于当日**的最近一份 V10.7 盘后摘要，解析最终仓位口径。

    绝不抛异常；任何异常都转成 ``status != OK`` 的不可用锚点。
    ``report_dir`` 参数仅供测试/部署覆盖。
    """
    td = str(trade_date)
    a = PositionAnchor(trade_date=td)
    opt = resolve_cfg(cfg)
    a.enabled = bool(opt["enabled"])
    if not a.enabled:
        a.status = "DISABLED"
        a.notes.append("intraday_bridge.enabled=false：盘中沿用 V10.7.4 原口径")
        return a

    d = report_dir or opt["report_dir"]
    try:
        cands: List[Tuple[str, str]] = []
        for p in glob.glob(os.path.join(d, SUMMARY_GLOB)):
            d0 = _date_from_path(p)
            # ★ 严格小于当日：当日盘后摘要在盘中尚不存在；若存在（回补/复跑）
            #   也属于收盘后信息，禁止用于盘中决策。
            if d0 and d0 < td:
                cands.append((d0, p))
        if not cands:
            a.status = "NO_ANCHOR"
            a.notes.append("目录内无 trade_date < 当日 的 V10.7 摘要 → 不额外收紧")
            return a
        basis, path = max(cands, key=lambda x: (x[0], x[1]))

        age = _days_between(basis, td)
        if age is None or age > opt["max_age_days"]:
            a.status = "STALE"
            a.basis_date = basis
            a.notes.append(
                f"基准日 {basis} 距今 {age} 自然日 > max_age_days "
                f"{opt['max_age_days']} → 拒绝使用陈旧仓位锚点")
            return a
        a.basis_date = basis

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("摘要顶层不是对象")
    except Exception as e:
        a.status = "BAD_JSON"
        a.error = str(e)
        a.notes.append("盘后摘要不可读 → 不额外收紧")
        return a

    # ---------- 层级开关（只作审计展示，不参与重算） ----------
    p0 = raw.get("P0风险口径修复") or {}
    optm = raw.get("P1P2仓位优化") or {}
    swing = raw.get("波段闸门") or {}
    bull = raw.get("主升段锁存") or {}
    a.layers = {
        "p0": bool(p0.get("enabled")),
        "optimizer": bool(optm.get("enabled")),
        "swing_gate": bool(swing.get("enabled")),
        "bull_latch": bool(bull.get("enabled")),
    }
    a.state = raw.get("市场状态")

    # ---------- 盘后最终有效目标：按链路末层优先 ----------
    # 盘后第 7e 步的 effective_target 已是 P0 / P1P2 / 闸门叠加后的结果。
    eff, src = None, None
    if a.layers["bull_latch"]:
        eff = _num(bull.get("effective_target_exact"))
        if eff is None:
            eff = _num(bull.get("effective_target"))
        src = "bull_latch.effective_target"
    elif a.layers["swing_gate"]:
        eff = _num(swing.get("effective_target_exact"))
        if eff is None:
            eff = _num(swing.get("effective_target"))
        src = "swing_gate.effective_target"
    elif a.layers["optimizer"]:
        eff = _num(optm.get("target_after"))
        src = "P1P2.target_after"
    if eff is None:
        eff = _parse_range_upper(raw.get("建议仓位"))
        src = "建议仓位上界（回退解析）"

    hard = _num(raw.get("仓位硬上限"))
    a.effective_target = eff
    a.hard_cap = hard
    a.source = src
    a.notes.append("口径来源：" + str(src))
    # ★ 明确不继承 authorized_hard_cap / 满仓授权：authority 与 P0 风险预算
    #   由盘后每日重算，盘中只读不放宽。
    a.notes.append("不继承跨日满仓授权与 authorized_hard_cap（盘后每日重算）")
    a.notes.append("盘中不重新触发主升段锁存 / 不重算 MA20 与广度信号")

    cand = [x for x in (eff, hard) if x is not None]
    if not cand:
        a.status = "NO_CAP"
        a.notes.append("有效目标与硬上限均缺失 → 不额外收紧")
        return a
    a.anchor_cap = max(0.0, min(100.0, min(cand)))
    a.status = "OK"
    return a


# ============================================================
# 施加：纯收紧
# ============================================================
def apply_anchor_cap(cap: Any, anchor: Optional[PositionAnchor],
                     cfg: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
    """把盘后锚点作为上限施加到盘中仓位，返回 ``(new_cap, note)``。

    ``new_cap <= cap`` 恒成立；档位取整到 5% 且保证不越过锚点。
    """
    try:
        before = int(cap)
    except (TypeError, ValueError):
        return 5, "盘中 cap 非法 → 回落到 5%"
    anchor = _view(anchor)
    if anchor is None or not anchor.usable:
        return before, (f"盘后锚点不可用（{anchor.status_cn if anchor else '未加载'}）"
                        f" → 沿用原口径")
    raw = min(float(before), float(anchor.anchor_cap))
    q = int(round(raw / 5.0) * 5)
    if q > raw + 1e-9:          # round 不允许越过锚点
        q -= 5
    q = max(5, min(100, q))
    new = min(before, q)         # ★ 只收紧，绝不放大
    if new < before:
        note = (f"锚点 {anchor.basis_date} 上限 {anchor.anchor_cap:g}% "
                f"→ 由 {before}% 收紧到 {new}%")
    else:
        note = (f"盘中 {before}% 未超过锚点 {anchor.anchor_cap:g}% "
                f"（{anchor.basis_date}）→ 不变")
    return new, note


# ============================================================
# 报告审计行
# ============================================================
def render_anchor_lines(anchor: Optional[PositionAnchor],
                        cap_before: int, cap_after: int,
                        apply_note: str = "") -> List[str]:
    L = ["", "【盘后仓位基准（V10.7.5 桥接 · 只收紧）】"]
    anchor = _view(anchor)
    if anchor is None:
        L.append("未加载 → 盘中沿用原口径")
        return L
    if not anchor.enabled:
        L.append("已关闭（A/B 对照）：盘中仓位沿用 V10.7.4 原口径")
        return L
    if not anchor.usable:
        L.append(f"不可用（{anchor.status_cn}）→ 本时点沿用原口径，不额外收紧")
        for n in anchor.notes:
            L.append(f"  · {n}")
        if anchor.error:
            L.append(f"  · 详情：{anchor.error}")
        return L
    L.append(f"基准日 {anchor.basis_date}（严格早于 {anchor.trade_date}）"
             f"｜盘后状态 {anchor.state or 'UNKNOWN'}")
    eff = ("%.1f" % anchor.effective_target
           if anchor.effective_target is not None else "UNKNOWN")
    hc = ("%.0f" % anchor.hard_cap if anchor.hard_cap is not None else "UNKNOWN")
    L.append(f"盘后有效目标 {eff}%｜状态硬上限 {hc}%｜锚点上限 "
             f"{anchor.anchor_cap:g}%（{anchor.source}）")
    layer_str = " / ".join(
        f"{LAYER_CN.get(k, k)} {'ON' if v else 'OFF'}"
        for k, v in anchor.layers.items())
    L.append("盘后层级：" + (layer_str or "无"))
    L.append(f"盘中推断 {cap_before}% → 桥接后 {cap_after}%"
             + ("（已收紧）" if cap_after < cap_before else "（未收紧）"))
    if apply_note:
        L.append(f"  · {apply_note}")
    for n in anchor.notes:
        L.append(f"  · {n}")
    return L
