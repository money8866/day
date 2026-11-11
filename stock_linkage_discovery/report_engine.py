"""每日输出报告（第十三层）+ CSV / JSON 落盘。

报告必须简明可执行；若无合格群体，明确输出 TODAY_NO_VALID_CLUSTER，
绝不为了每天有结果而降低标准。报告不产生 BUY 信号。

Markdown 采用「结论先行、细节后置」的结构：
今日速览 → 关键数字 → 重点群体 → 群体明细 → 观察池 → 二波 → 风险 → 附录（口径/原始数据）。
JSON / CSV 保持机器可读的完整字段，不因排版优化而删减。
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .cluster_engine import ClusterSet
from .common import get_logger, write_json
from .linkage_engine import NetworkResult

log = get_logger("report_engine")


# ---------------------------------------------------------------- 基础格式
def _s(x: Any, digits: int = 3) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _pct(x: Any, digits: int = 1, sign: bool = True) -> str:
    """比率转百分比：0.0472 -> +4.7% / 4.7%，比原始小数易读得多。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v * 100:+.{digits}f}%" if sign else f"{v * 100:.{digits}f}%"


def _date(x: Any) -> str:
    s = str(x)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _weekday(x: Any) -> str:
    try:
        return "周" + "一二三四五六日"[datetime.strptime(str(x), "%Y%m%d").weekday()]
    except (ValueError, TypeError):
        return ""


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_无_\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 标签中文对照
STATE_ZH = {
    "EMERGING_CLUSTER": "新联动形成", "LEADER_EXPANSION": "龙头扩散",
    "CLUSTER_STRENGTHENING": "联动强化", "EXISTING_CLUSTER": "存量维持",
    "DECAYING_CLUSTER": "联动衰减", "OVERHEATED_CLUSTER": "群体过热",
}
POS_ZH = {
    "DEEP_PULLBACK": "深度回踩", "PULLBACK": "回踩", "FLAT": "横盘",
    "EARLY_MOVE": "刚启动", "EXTENDED": "已透支", "UNKNOWN": "未知",
}
MEMBER_TAG_ZH = {"EXTENDED": "已透支", "MOVED": "已启动",
                 "PULLED_BACK": "回踩", "FLAT": "横盘"}
ROLE_ZH = {"LEADER": "领涨", "CORE": "核心", "FOLLOWER": "跟随",
           "LAGGARD": "滞后", "WEAK": "弱势"}
WAVE2_ZH = {
    "WAVE2_READY": "出现缩量点", "WAVE2_WAIT": "等待缩量",
    "WAVE2_BROKEN": "结构破坏", "WAVE2_EXPIRED": "已超时",
    "NOT_CONFIRMED": "非新确认", "NO_DATA": "数据不足",
}
CONCLUSION_ZH = {
    "LINKAGE_SETUP": "待策略确认", "LINKAGE_WATCH": "仅观察",
    "LINKAGE_ENTRY": "策略+联动共振", "LINKAGE_INVALID": "不进入观察池",
}


def _zh(d: dict[str, str], k: Any) -> str:
    return d.get(str(k), str(k) if k is not None else "—")


def _wave2_evidence(cfg: dict) -> dict | None:
    """读取事后验证结论，日报只引用真实回测产物，绝不把未验证的标签写成买点。"""
    p = Path(cfg["paths"]["output_dir"]) / "backtest" / "wave2_summary.json"
    if not p.exists():
        return None
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        allv = s.get("by_segment", {}).get("ALL", {}).get("WAVE2_SHRINK", {})
        pair = s.get("paired", {}).get("paired_vs_t0", {}).get("ALL", {})
        dg = s.get("diagnostics", {})
        return {"verdict": s.get("verdict"), "events": s.get("events"),
                "triggered": s.get("triggered_events"), "h10": allv.get("mean"),
                "win": allv.get("win_rate"), "paired": pair.get("mean"),
                "t0_trig": dg.get("t0_h10_triggered"),
                "t0_no": dg.get("t0_h10_no_trigger")}
    except (ValueError, OSError, TypeError):
        return None


def _risk_brief(c: dict) -> str:
    """风险按群体聚合成一行，避免同一条超长 move_note 在报告里重复出现。"""
    p: list[str] = []
    if c["sample_risk"] > 0.5 or c["n_members"] <= 4:
        p.append("样本偏小")
    if c["reliability"] < 0.5:
        p.append("关联不稳定")
    if c["driver_confidence"] in ("UNKNOWN", "LOW"):
        p.append("共同驱动不明")
    if c.get("move_state") == "EXTENDED":
        p.append(f"涨幅已透支（5日 {_pct(c.get('mean_move_5', 0))}），防追高")
    elif not c.get("move_confirmed", True):
        p.append("启动幅度不足")
    if c.get("overheated"):
        p.append("群体过热")
    if c.get("move_demoted"):
        p.append("严格幅度口径下降级")
    if c.get("wave2_status") == "WAVE2_BROKEN":
        p.append("二波结构破坏，不宜补票")
    if c.get("wave2_status") == "WAVE2_EXPIRED":
        p.append("二波本轮已超时")
    if c.get("wave2_status") == "WAVE2_READY" and c.get("move_state") == "EXTENDED":
        p.append("虽缩量但位置透支")
    if c["age_days"] > 15 and c["expansion_score"] > 0.3:
        p.append("扩散或已到后期")
    if c["density"] < 0.45 or c["decay_risk"] > 0.5:
        p.append("内部分化 / 联动衰减")
    return " · ".join(dict.fromkeys(p)) or "无特别风险"


def _wave2_section(cfg: dict, cs: ClusterSet, heading: str) -> list[str]:
    wcfg = cfg.get("_wave2") or {}
    ev = _wave2_evidence(cfg)
    counts = cs.stats.get("wave2_status_counts") or {}
    L = [heading, ""]
    if ev:
        L += [f"> ⚠️ **事后验证 {ev['verdict']}**（{ev['events']} 个真实事件 / "
              f"{ev['triggered']} 个合格缩量点）：二波入场 T+10 超额 "
              f"**{_pct(ev['h10'], 2)}**、胜率 {_pct(ev['win'], 1, sign=False)}。"
              f"「二波 − 第一波」配对差 {_pct(ev['paired'], 2)} 支持「不追第一波」，"
              f"但该差值是事后路径归因（T0 追涨 {_pct(ev['t0_trig'], 2)} vs 未回踩 "
              f"{_pct(ev['t0_no'], 2)}），**「等缩量就能赚」未被验证**。",
              "> 本表只作结构观察与风险分层，**不是买点确认，更不是 BUY 信号**；"
              "详见 `output/backtest/wave2_report.md`。", ""]
    else:
        L += ["> ⚠️ 尚未运行 `--wave2-validate`，本表仅为结构观察标签，不构成买点确认或买入信号。", ""]
    L += [f"今日：出现缩量 **{counts.get('WAVE2_READY', 0)}** · 等待 "
          f"{counts.get('WAVE2_WAIT', 0)} · 破坏 {counts.get('WAVE2_BROKEN', 0)} · "
          f"超时 {counts.get('WAVE2_EXPIRED', 0)} · 非新确认 {counts.get('NOT_CONFIRMED', 0)}", ""]
    rows = []
    for c in cs.clusters:
        if not c.get("wave2_status"):
            continue
        unmet = "、".join((c.get("wave2_unmet") or [])[:2]) or "—"
        rows.append([f"`{c['cluster_id']}`", _zh(WAVE2_ZH, c["wave2_status"]),
                     c.get("wave2_wait_days", "—"),
                     f"{_s(c.get('wave2_vr'), 2)} / {_s(c.get('wave2_first_wave_peak_vr'), 2)}",
                     _pct(c.get("wave2_range_3"), 1, sign=False),
                     _pct(c.get("wave2_retrace"), 1),
                     unmet])
    L.append(_table(["群体", "状态", "已等(日)", "量比 / 第一波峰值", "3日振幅",
                     "距确认日", "卡在哪"], rows))
    L += ["", "> `WAVE2_READY` 只表示出现缩量结构；是否介入仍必须由 HVT-BULL / W7 / E08 / VSW "
          "各自的入场与止损规则决定，本引擎不修改任何既有策略逻辑。"]
    return L


def risks_of(c: dict) -> list[str]:
    """完整风险清单，供 report.json 机器消费；Markdown 用 _risk_brief 压缩展示。"""
    r = []
    if c["sample_risk"] > 0.5 or c["n_members"] <= 4:
        r.append("群体样本不足 / 低样本风险")
    if c["reliability"] < 0.5:
        r.append("统计关联不稳定")
    if c["driver_confidence"] in ("UNKNOWN", "LOW"):
        r.append("共同驱动不明确（可能只是统计联动或偶然同步）")
    if c["overheated"]:
        r.append("领涨股或群体整体已经过热")
    if c.get("move_state") == "EXTENDED":
        r.append(f"涨幅已透支：{c.get('move_note', '')}；实证该位置 T+10 超额为负，防追高")
    elif not c.get("move_confirmed", True):
        r.append("个股涨幅幅度不足（仅统计联动位置，非已启动群体）")
    if c.get("move_demoted"):
        r.append("严格幅度口径下由 S/A 降级为 B")
    if c.get("wave2_status") == "WAVE2_BROKEN":
        r.append("二波等待期内已跌破回撤上限，结构破坏，不应再按『缩量回踩』补票")
    if c.get("wave2_status") == "WAVE2_EXPIRED":
        r.append("确认后超过最大等待日仍未出现合格缩量震荡，本轮二波逻辑失效")
    if (c.get("wave2_status") == "WAVE2_READY"
            and c.get("move_state") == "EXTENDED"):
        r.append("虽出现缩量，但群体位置已透支；交叉分层显示该组合 T+10 更差，防二次追高")
    if c["age_days"] > 15 and c["expansion_score"] > 0.3:
        r.append("群体扩散可能已进入后期")
    if c["density"] < 0.45 or c["decay_risk"] > 0.5:
        r.append("群体内部出现分化 / 联动衰减风险")
    return r


# ---------------------------------------------------------------- 主渲染
def render(cfg: dict[str, Any], asof: str, cs: ClusterSet, net: NetworkResult,
           obs: pd.DataFrame, quality: dict, timings: dict) -> dict[str, Path]:
    rcfg = cfg["report"]
    outdir = Path(cfg["paths"]["output_dir"]) / asof
    outdir.mkdir(parents=True, exist_ok=True)
    dig = int(rcfg.get("float_digits", 3))
    st = cs.stats
    top = [c for c in cs.clusters if c["rank"] > 0][:int(rcfg.get("max_clusters", 5))]
    no_valid = bool(cs.flags.get("TODAY_NO_VALID_CLUSTER"))
    wcounts = st.get("wave2_status_counts") or {}
    grades = st.get("grade_counts") or {}
    name_map = (dict(zip(cs.members["ts_code"], cs.members["stock_name"]))
                if len(cs.members) else {})

    def stock(code: Any) -> str:
        s = str(code)
        nm = name_map.get(s)
        return f"{s} {nm}" if isinstance(nm, str) and nm and nm != "nan" else s

    sec_i = [0]

    def sec(title: str) -> str:
        sec_i[0] += 1
        return f"## {sec_i[0]}. {title}"

    # ---------------- Markdown ----------------
    L: list[str] = []
    L += [f"# A股动态联动日报 · {_date(asof)}（{_weekday(asof)}）", ""]
    L += ["> **本报告只做「发现」，不做「买入」**：LinkageScore 代表关联强度，"
          "不等于预期收益；是否介入由 HVT-BULL / W7 / E08 / VSW 的入场与止损规则决定。", ""]
    if no_valid:
        L += ["> **⚠️ TODAY_NO_VALID_CLUSTER**", ">",
              "> 今日没有符合 S/A 级标准的联动群体，不降低标准凑数；"
              "下列 B 级仅为统计联动观察，需连续回放确认扩散/强化后才可升级。", ""]
    if cs.flags.get("BASELINE_SNAPSHOT"):
        L += ["> **基线快照**：本模块首个历史网络快照，缺少昨日/5日前/10日前对比，"
              "按规格不硬判「新联动形成」。", ""]
    if cs.flags.get("MOMENTUM_STRICT_FILTER"):
        L += ["> ⚠️ 本次启用了**严格幅度硬过滤**（非默认口径）：未通过幅度确认的 S/A 群体已降级并退出观察池，"
              "与历史样本外回放口径不一致，结论不可直接比较。", ""]

    # ---------- 今日速览 ----------
    headline = (f"今日无合格 S/A 级群体" if no_valid else
                f"{st.get('valid_clusters', 0)} 个有效群体"
                f"（S {grades.get('S', 0)} / A {grades.get('A', 0)} / B {grades.get('B', 0)}），"
                f"其中新联动 {st.get('emerging_clusters', 0)} 个；"
                f"全市场有效关联边 {len(net.edges):,} 条"
                f"（今日新增 {int(st.get('new_edges_today', 0)):,}）")
    focus = "、".join(
        f"{c.get('driver_label') or c['cluster_id']}（{c['grade']}级 · {c['n_members']}只 · "
        f"5日 {_pct(c.get('mean_move_5', 0))} · {_zh(POS_ZH, c.get('move_state'))}）"
        for c in top[:3]) or "无"
    n_ext = st.get("move_extended_clusters", 0)
    n_healthy = st.get("move_healthy_clusters", 0)
    n_ready = wcounts.get("WAVE2_READY", 0)
    n_fail = wcounts.get("WAVE2_BROKEN", 0) + wcounts.get("WAVE2_EXPIRED", 0)
    ev = _wave2_evidence(cfg)
    obs_setup = int((obs["conclusion"] == "LINKAGE_SETUP").sum()) if len(obs) else 0
    obs_watch = int((obs["conclusion"] == "LINKAGE_WATCH").sum()) if len(obs) else 0
    obs_entry = int((obs["conclusion"] == "LINKAGE_ENTRY").sum()) if len(obs) else 0
    has_signal = False
    if len(obs):
        for k in ("HVT-BULL", "W7", "E08", "VSW"):
            if k in obs.columns:
                vals = obs[k].fillna("").astype(str).str.strip()
                if bool((~vals.isin(("", "-", "nan", "None"))).any()):
                    has_signal = True
                    break
    L += ["## 今日速览", "",
          f"- **结论**：{headline}。",
          f"- **{'统计联动观察（未达 S/A 标准）' if no_valid else '重点群体'}**：{focus}。",
          f"- **位置风险**：已透支 {n_ext} 个 / 位置健康 {n_healthy} 个 / 幅度确认 "
          f"{st.get('move_confirmed_clusters', 0)} 个；"
          + ("历史分层显示透支位置后续超额偏弱，防追高。" if n_ext else
             "今日无涨幅透支群体，位置风险相对较低。"),
          f"- **二波**：今日出现缩量点 {n_ready} 个、等待 {wcounts.get('WAVE2_WAIT', 0)} 个、"
          f"失效 {n_fail} 个"
          + (f"；事后验证结论 **{ev['verdict']}**，缩量不等于买点。" if ev else "。"),
          f"- **观察池**：{len(obs)} 只（待策略确认 {obs_setup} / 仅观察 {obs_watch} / "
          f"策略共振 {obs_entry}）；四套既有策略今日"
          + ("有信号触发，见下表。" if has_signal else "**均无信号触发**，无 BUY。"), ""]

    # ---------- 1. 关键数字 ----------
    all_e = net.edges
    persisted = int((all_e["streak_days"] >= 2).sum()) if len(all_e) else 0
    L += [sec("关键数字"), ""]
    L.append(_table(["维度", "今日"], [
        ["股票池 / 关联边",
         f"{quality.get('universe', {}).get('final', '-')} 只 · {len(all_e):,} 条有效边"
         f"（新增 {int(st.get('new_edges_today', 0)):,}，连续≥2日 {persisted:,}）"],
        ["群体状态",
         f"{st.get('valid_clusters', 0)} 个有效 · 新联动 {st.get('emerging_clusters', 0)} · "
         f"龙头扩散 {st.get('expanding_clusters', 0)} · 联动强化 {st.get('strengthening_clusters', 0)} · "
         f"衰减 {st.get('decaying_clusters', 0)} · 板块前置 {st.get('pre_sector_clusters', 0)}"],
        ["涨幅位置",
         f"幅度确认 {st.get('move_confirmed_clusters', 0)} · "
         f"健康(-10%~+3%) {n_healthy} · 已透支 {n_ext} · "
         f"严格过滤{'开启（非默认）' if st.get('move_strict_filter') else '关闭（仅标签）'}"],
        ["二波状态",
         f"出现缩量 {n_ready} · 等待 {wcounts.get('WAVE2_WAIT', 0)} · "
         f"破坏 {wcounts.get('WAVE2_BROKEN', 0)} · 超时 {wcounts.get('WAVE2_EXPIRED', 0)}"],
        ["算法 / 口径",
         f"{st.get('algorithm', '-')} 社区发现 · 全A等权市场因子 · 严格 as-of（无未来数据）"],
    ]))
    L += ["> 群体主状态互斥；「新联动 / 扩散 / 强化」来自不互斥的信号标签，同一群体可同时计入多类。", ""]

    # ---------- 2. 重点群体 ----------
    L += [sec("统计联动观察（今日未达 S/A 标准）" if no_valid else "今日重点群体"), ""]
    L.append(_table(
        ["#", "群体 / 主线", "级别", "状态", "5日涨幅", "位置", "二波", "评分"],
        [[c["rank"], f"**{c.get('driver_label') or '未解释'}** · `{c['cluster_id']}`",
          f"{c['grade']} 级", _zh(STATE_ZH, c["state"]),
          _pct(c.get("mean_move_5", 0)), _zh(POS_ZH, c.get("move_state")),
          _zh(WAVE2_ZH, c.get("wave2_status")), _s(c["linkage_score"], 1)]
         for c in top]) if top else "_今日无 S/A 级群体_\n")
    L += ["", "> 「评分」= LinkageScore，仅表示关联强度与结构质量，不代表预期收益。"
          "共同驱动置信度、成员数、密度、新鲜度见下节与附录。", ""]

    # ---------- 3. 群体明细 ----------
    L += [sec("群体明细"), ""]
    if top and len(cs.members):
        for idx, c in enumerate(top, 1):
            cid = c["cluster_id"]
            L += [f"### {'①②③④⑤⑥⑦⑧⑨⑩'[idx - 1] if idx <= 10 else idx} "
                  f"{cid} · {c.get('driver_label') or '未解释'} · {c['grade']} 级 · "
                  f"{_zh(STATE_ZH, c['state'])}", ""]
            L += [f"`{c['n_members']} 只` · 领涨 **{stock(c['leader'])}** · "
                  f"密度 {_s(c['density'], 2)} · 评分 {_s(c['linkage_score'], 1)} · "
                  f"共同驱动 {c['driver_confidence']} · 新鲜度 {_s(c['freshness'], 2)} · "
                  f"确认日 {_date(c.get('birth_date'))}", ""]
            pos_line = (f"**位置**：5日 {_pct(c.get('mean_move_5', 0))} / "
                        f"10日 {_pct(c.get('mean_move_10', 0))} / "
                        f"20日 {_pct(c.get('mean_move_20', 0))} · "
                        f"**{_zh(POS_ZH, c.get('move_state'))}** · "
                        f"有幅度成员 {c.get('moved_stocks', 0)}/{c['n_members']} · "
                        f"领涨领先 {_pct(c.get('leader_follower_gap_5', 0))} · "
                        f"启动幅度确认 **{'是' if c.get('move_confirmed') else '否'}**")
            failed = c.get("move_failed_checks") or []
            if failed:
                pos_line += f"（缺：{'、'.join(failed[:2])}）"
            L += [pos_line, ""]
            w = c.get("wave2_status")
            if w:
                unmet = "、".join((c.get("wave2_unmet") or [])[:2]) or "—"
                entry = (f"，本轮缩量点 {_date(c['wave2_entry_date'])}"
                         f"（第 {c.get('wave2_entry_wait')} 日）"
                         if c.get("wave2_entry_date") else "")
                L += [f"**二波**：{_zh(WAVE2_ZH, w)}（确认日 {_date(c.get('wave2_t0'))} 起第 "
                      f"{c.get('wave2_wait_days', 0)} 日{entry}）· 量比 "
                      f"{_s(c.get('wave2_vr'), 2)} / 第一波峰值 "
                      f"{_s(c.get('wave2_first_wave_peak_vr'), 2)} · 3日振幅 "
                      f"{_pct(c.get('wave2_range_3'), 1, sign=False)} · 距确认日 "
                      f"{_pct(c.get('wave2_retrace'), 1)} · 卡在：{unmet}", ""]
            sub = cs.members[cs.members["cluster_id"] == cid].head(
                int(rcfg.get("max_members_per_table", 12)))
            L.append(_table(
                ["股票", "角色", "5日", "20日", "量比", "技术结构", "备注"],
                [[f"{r.ts_code} {r.stock_name}", _zh(ROLE_ZH, r.role),
                  _pct(getattr(r, "move_5", 0)), _pct(getattr(r, "move_20", 0)),
                  _s(r.amount_ratio, 2), r.trend,
                  "新加入" if r.is_new_member else ("弱势" if r.role == "WEAK" else "关联维持")]
                 for r in sub.itertuples(index=False)]))
            L.append("")
    else:
        L.append("_无_\n")

    # ---------- 4. 观察池 ----------
    L += [sec("观察池（不是买入清单）"), ""]
    if len(obs):
        sig_cols = ["HVT-BULL", "W7", "E08", "VSW"]
        def cross(row: pd.Series) -> str:
            hits = [f"{k}={row[k]}" for k in sig_cols
                    if str(row.get(k) or "").strip() not in ("", "-", "nan", "None")]
            return " / ".join(hits) if hits else "—"
        L += [f"> {len(obs)} 只进入观察池：待策略确认 {obs_setup} · 仅观察 {obs_watch} · "
              f"策略共振 {obs_entry}。四套既有策略今日"
              + ("有触发（见「策略交叉」列）。" if has_signal else "**均无触发**。")
              + (" LINKAGE_ENTRY 也必须遵守原策略止损与风控。" if obs_entry else ""), ""]
        # 四套策略都无信号时省掉整列，避免 17 行「—」的视觉噪音
        headers = ["股票", "角色", "5日", "位置", "所属群体", "结论"]
        rows = [[f"{row['ts_code']} {row['stock_name']}", _zh(ROLE_ZH, row["role"]),
                 _pct(row.get("move_5", 0)), _zh(MEMBER_TAG_ZH, row.get("move_tag")),
                 f"`{row['cluster_id']}`", _zh(CONCLUSION_ZH, row["conclusion"])]
                for _, row in obs.iterrows()]
        if has_signal:
            headers.insert(5, "策略交叉")
            for row, values in zip(obs.to_dict(orient="records"), rows):
                values.insert(5, cross(pd.Series(row)))
        L.append(_table(headers, rows))
        # 只保留有差异的提示，替代原来 17 条雷同的说明
        hot = [c["cluster_id"] for c in top if c.get("move_state") == "EXTENDED"]
        if hot:
            L += ["", f"- ⚠️ 来自涨幅已透支群体的 {int(obs['cluster_id'].isin(hot).sum())} 只"
                  f"（{'、'.join(hot[:3])}）：按历史分层该位置后续超额偏弱，防追高。"]
        if obs_watch:
            codes = [r.ts_code for r in obs[obs["conclusion"] == "LINKAGE_WATCH"].itertuples(index=False)]
            L += [f"- 仅观察 {'、'.join(codes[:6])}{'等' if len(codes) > 6 else ''}："
                  "等待回踩或原策略信号，不视为联动确认。"]
        if obs_entry:
            L += [f"- {obs_entry} 只与既有买点同时满足；仍须遵守原策略止损与风控。"]
    else:
        L.append("_今日无进入观察池的股票_\n")
    L.append("")

    # ---------- 5. 二波 ----------
    L += [""]
    L.extend(_wave2_section(cfg, cs, sec("二波介入观察（确认联动后等待震荡缩量）")))
    L.append("")

    # ---------- 6. 风险 ----------
    L += [sec("风险提示"), ""]
    if no_valid:
        L.append("- **⚠️ 今日无合格 S/A 级群体**：下列 B 级仅为统计联动观察，"
                 "不构成机会确认；不要把关联强度当作预期收益。")
    for c in top:
        L.append(f"- **{c.get('driver_label') or c['cluster_id']}** `{c['cluster_id']}`"
                 f"（{c['grade']} 级）：{_risk_brief(c)}")
    if not top:
        L.append("- 今日无 S/A 级群体；主要风险为「无有效联动机会」与误把统计同步当主线。")
    panel = quality.get("panel", {})
    L += ["", f"> 数据完整性：行情覆盖率 {_pct(panel.get('quote_coverage', 0), 1, sign=False)}、"
          f"PIT 历史简称覆盖率 {_pct(panel.get('pit_name_coverage', 0), 1, sign=False)}、"
          f"市场因子 {panel.get('market_source', '-')}。行业分类为当前快照（非 PIT），"
          "仅用于双调整残差与事后归因；涨跌停为板块阈值近似，仅作行为特征。", ""]

    # ---------- 附录 ----------
    L += ["## 附录 · 口径、图例与原始数据", "",
          "### A1 标签对照", ""]
    for label, d in (("群体状态", STATE_ZH), ("涨幅位置", POS_ZH),
                     ("成员幅度", MEMBER_TAG_ZH), ("成员角色", ROLE_ZH),
                     ("二波状态", WAVE2_ZH), ("观察结论", CONCLUSION_ZH)):
        L.append(f"- **{label}**：" + " · ".join(f"`{k}`→{v}" for k, v in d.items()))
    L.append("")
    mcfg = cfg.get("_momentum") or {}
    L += ["### A2 涨幅幅度口径", "",
          "每只成员与每个群体都给出 5/10/20 日**复权累计涨幅**与位置标签。"
          "`move_confirmed` 要求至少 3 只成员 5 日≥5% 或 10 日≥8%、群体 5 日均值≥3%、"
          "且领涨领先其余成员≥3pct。", "",
          f"> **实证提醒**：{mcfg.get('_empirical_note', '')}", ""]
    wcfg = cfg.get("_wave2") or {}
    L += ["### A3 二波判定口径", "",
          f"确认日 T0 后至少等 {wcfg.get('min_wait_days', 2)} 日、最多等 "
          f"{wcfg.get('max_wait_days', 10)} 日；群体量比连续 "
          f"{wcfg.get('shrink_confirm_days', 2)} 日 ≤ {wcfg.get('shrink_abs', 0.80)} 且 ≤ "
          f"第一波峰值的 {_pct(wcfg.get('shrink_vs_first_wave', 0.65), 0, sign=False)}，"
          f"3 日价格区间 ≤ {_pct(wcfg.get('max_range_3', 0.05), 0, sign=False)}，"
          f"相对 T0 回撤在 [-{_pct(wcfg.get('max_pullback', 0.10), 0, sign=False)}, "
          f"+{_pct(wcfg.get('max_rebound', 0.06), 0, sign=False)}]，"
          "收盘不破 10 日均价。判定只用当日及之前行情，按时间顺序取第一个合格日，"
          "绝不事后挑最优点。", ""]
    L += ["### A4 新联动关系", ""]
    ne = all_e[all_e["is_new"]] if len(all_e) else all_e
    if len(ne):
        shown = ne.sort_values("edge_score", ascending=False).head(10)
        L += [f"今日新增有效关联边 **{len(ne):,}** 条（新边按定义均为首次出现）；"
              f"全部 {len(all_e):,} 条有效边中，连续维持 ≥2 日 {persisted:,} 条、"
              f"单日现象 {len(all_e) - persisted:,} 条。以下为 EdgeScore 最高的 10 条：", ""]
        L.append(_table(
            ["股票A", "股票B", "20日残差相关", "10日残差相关", "EdgeScore", "可靠性"],
            [[stock(r.a), stock(r.b), _s(r.residual_corr_20, dig),
              _s(r.residual_corr_10, dig), _s(r.edge_score, dig),
              _s(r.edge_reliability, 2)]
             for r in shown.itertuples(index=False)]))
        new_by: dict[str, list[str]] = {}
        if len(cs.members):
            for r in cs.members.itertuples(index=False):
                if getattr(r, "is_new_member", False):
                    new_by.setdefault(str(r.cluster_id), []).append(str(r.ts_code))
        if new_by:
            L += ["", f"**今日新加入群体的股票**（{sum(len(v) for v in new_by.values())} 只，按群体分组）：", ""]
            for cid, codes in list(new_by.items())[:8]:
                L.append(f"- `{cid}`（{len(codes)}）：{'、'.join(codes)}")
            if len(new_by) > 8:
                L.append(f"- 其余 {len(new_by) - 8} 个群体见 `members.csv`")
    else:
        L += ["今日无新增有效关联边。", ""]
    L += ["", "### A5 免责与产物", "",
          "> 联动强度不等于买入信号；本模块不修改任何既有策略逻辑；严格 as-of（无未来数据）；"
          "全部数据来自真实行情库，未使用模拟数据冒充实盘结果。", "",
          f"> 完整字段见 `report.json`；明细见 `clusters.csv` / `members.csv` / "
          f"`edges.csv` / `observations.csv`；回测见 `output/backtest/`。", ""]
    L += ["---",
          f"计算耗时 {_s(timings.get('total', 0), 2)}s · 配置指纹 {cfg['_config_hash']} · "
          f"严格 as-of（无未来数据） · 数据源 cache_daily/stock_data.db", ""]

    md_path = outdir / "report.md"
    md_path.write_text("\n".join(L), encoding="utf-8")

    # ---------------- JSON（结构保持不变） ----------------
    payload = {
        "asof": asof,
        "flags": cs.flags,
        "overview": {
            **{k: v for k, v in st.items() if k not in ("rejected",)},
            "n_edges": len(net.edges),
            "network_stats": net.stats,
        },
        "clusters": [{k: v for k, v in c.items() if k != "metrics"} for c in cs.clusters],
        "members": cs.members.to_dict(orient="records") if len(cs.members) else [],
        "new_edges": ne.head(200).to_dict(orient="records") if len(ne) else [],
        "observations": obs.to_dict(orient="records") if len(obs) else [],
        "risks": [{"cluster_id": c["cluster_id"], "risks": risks_of(c)} for c in top],
        "data_quality": quality,
        "timings": timings,
        "config_hash": cfg["_config_hash"],
        "disclaimer": "联动强度不等于买入信号；不修改既有策略逻辑；无未来数据；无模拟数据冒充实盘结果",
    }
    json_path = write_json(outdir / "report.json", payload)

    # ---------------- CSV（结构保持不变） ----------------
    paths: dict[str, Path] = {"markdown": md_path, "json": json_path}
    for name, frame in (
        ("clusters", pd.DataFrame([{k: v for k, v in c.items()
                                    if not isinstance(v, (dict, list))} for c in cs.clusters])),
        ("members", cs.members),
        ("edges", net.edges),
        ("observations", obs),
    ):
        if "csv" in rcfg.get("formats", []):
            p = outdir / f"{name}.csv"
            frame.to_csv(p, index=False, encoding="utf-8-sig")
            paths[name] = p
    write_json(outdir / "data_quality.json", {"quality": quality, "timings": timings})
    log.info("报告已输出: %s", outdir)
    return paths
