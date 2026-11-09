"""每日输出报告（第十三层）+ CSV / JSON 落盘。

报告必须简明可执行；若无合格群体，明确输出 TODAY_NO_VALID_CLUSTER，
绝不为了每天有结果而降低标准。报告不产生 BUY 信号。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .cluster_engine import ClusterSet
from .common import get_logger, write_json
from .linkage_engine import NetworkResult

log = get_logger("report_engine")


def _s(x: Any, digits: int = 3) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_无_\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def risks_of(c: dict) -> list[str]:
    r = []
    if c["sample_risk"] > 0.5 or c["n_members"] <= 4:
        r.append("群体样本不足 / 低样本风险")
    if c["reliability"] < 0.5:
        r.append("统计关联不稳定")
    if c["driver_confidence"] in ("UNKNOWN", "LOW"):
        r.append("共同驱动不明确（可能只是统计联动或偶然同步）")
    if c["overheated"]:
        r.append("领涨股或群体整体已经过热")
    if c["age_days"] > 15 and c["expansion_score"] > 0.3:
        r.append("群体扩散可能已进入后期")
    if c["density"] < 0.45 or c["decay_risk"] > 0.5:
        r.append("群体内部出现分化 / 联动衰减风险")
    return r


def render(cfg: dict[str, Any], asof: str, cs: ClusterSet, net: NetworkResult,
           obs: pd.DataFrame, quality: dict, timings: dict) -> dict[str, Path]:
    rcfg = cfg["report"]
    outdir = Path(cfg["paths"]["output_dir"]) / asof
    outdir.mkdir(parents=True, exist_ok=True)
    dig = int(rcfg.get("float_digits", 3))
    st = cs.stats
    top = [c for c in cs.clusters if c["rank"] > 0][:int(rcfg.get("max_clusters", 5))]
    no_valid = bool(cs.flags.get("TODAY_NO_VALID_CLUSTER"))

    # ---------------- Markdown ----------------
    L: list[str] = []
    L.append(f"# A股个股动态联动日报 · {asof}")
    L.append("")
    L.append("> 联动发现负责找机会，HVT-BULL / W7 / E08 / VSW 负责判断买点。"
             "**LinkageScore 只表示关联强度，不代表预期收益，也不是买入信号。**")
    L.append("")
    if no_valid:
        L.append("## ⚠️ TODAY_NO_VALID_CLUSTER")
        L.append("")
        L.append("今日没有符合 S/A 级标准的联动群体。不降低标准凑数。")
        L.append("下表若出现 B 级群体，仅为**统计联动观察**，需连续回放确认扩散/强化后才可升级。")
        L.append("")
    if cs.flags.get("BASELINE_SNAPSHOT"):
        L.append("> **基线快照**：这是本模块的首个历史网络快照，缺少昨日/5日前/10日前对比，"
                 "按规格不硬判「新联动形成」，仅建立基线。后续连续运行后才能识别 EMERGING_CLUSTER。")
        L.append("")
    L.append("## 1. 今日市场联动概览")
    L.append("")
    L.append(_table(["指标", "数值"], [
        ["有效群体数", st.get("valid_clusters", 0)],
        ["新联动群体 (EMERGING)", st.get("emerging_clusters", 0)],
        ["正在扩散 (LEADER_EXPANSION)", st.get("expanding_clusters", 0)],
        ["联动强化 (STRENGTHENING)", st.get("strengthening_clusters", 0)],
        ["衰减群体 (DECAYING)", st.get("decaying_clusters", 0)],
        ["板块前置联动 (PRE_SECTOR)", st.get("pre_sector_clusters", 0)],
        ["全市场有效关联边", len(net.edges)],
        ["今日新增关联边", st.get("new_edges_today", 0)],
        ["股票池规模", quality.get("universe", {}).get("final", "-")],
        ["社区发现算法", st.get("algorithm", "-")],
    ]))
    L.append("")
    L.append("## 2. 最值得关注的群体")
    L.append("")
    L.append(_table(
        ["排名", "群体", "股票数", "状态", "LinkageScore", "新鲜度", "共同驱动"],
        [[c["rank"], f"{c['cluster_id']} {c['driver_label']}".strip(), c["n_members"],
          c["state"], _s(c["linkage_score"], 1), _s(c["freshness"], 2),
          f"{c['driver_confidence']} / {c.get('driver_label') or '未解释'}"]
         for c in top]) if top else "_今日无 S/A 级群体_\n")
    L.append("")
    L.append("## 3. 群体内部股票")
    L.append("")
    if top and len(cs.members):
        for c in top:
            L.append(f"### {c['cluster_id']} · {c['state']} · {c['grade']}级 "
                     f"· 领涨 {c['leader']} · 密度 {_s(c['density'], 2)}")
            L.append("")
            sub = cs.members[cs.members["cluster_id"] == c["cluster_id"]].head(
                int(rcfg.get("max_members_per_table", 12)))
            L.append(_table(
                ["股票", "角色", "5日超额", "20日残差相关", "成交额倍数", "技术结构", "观察结论"],
                [[f"{r.ts_code} {r.stock_name}", r.role, _s(r.excess_mkt_5, dig),
                  _s(r.resid_corr_20, dig), _s(r.amount_ratio, 2), r.trend,
                  "新加入" if r.is_new_member else ("弱势" if r.role == "WEAK" else "关联维持")]
                 for r in sub.itertuples(index=False)]))
            L.append("")
    else:
        L.append("_无_\n")

    L.append("## 4. 新联动关系")
    L.append("")
    all_e = net.edges
    ne = all_e[all_e["is_new"]] if len(all_e) else all_e
    persisted = int((all_e["streak_days"] >= 2).sum()) if len(all_e) else 0
    single = int((all_e["streak_days"] < 2).sum()) if len(all_e) else 0
    if len(ne):
        shown = ne.sort_values("edge_score", ascending=False).head(20)
        L.append(f"今日**新增**有效关联边 **{len(ne)}** 条（新边按定义均为首次出现）。"
                 f"当前全部 {len(all_e)} 条有效边中，已连续维持 ≥2 日的 **{persisted}** 条，"
                 f"仍属单日现象的 **{single}** 条。")
        L.append("")
        L.append(_table(
            ["股票A", "股票B", "20日残差相关", "10日残差相关", "EdgeScore", "可靠性", "连续天数"],
            [[r.a, r.b, _s(r.residual_corr_20, dig), _s(r.residual_corr_10, dig),
              _s(r.edge_score, dig), _s(r.edge_reliability, 2), r.streak_days]
             for r in shown.itertuples(index=False)]))
        new_members = [f"{r.cluster_id}: {r.ts_code}" for r in cs.members.itertuples(index=False)
                       if getattr(r, "is_new_member", False)] if len(cs.members) else []
        L.append("")
        L.append(f"**今日新加入群体的股票**（{len(new_members)} 只）："
                 + ("、".join(new_members[:20]) if new_members else "无"))
    else:
        L.append("今日无新增有效关联边。")
    L.append("")

    L.append("## 5. 交易观察")
    L.append("")
    L.append("> 以下仅为观察池，不构成买入建议；LINKAGE_ENTRY 也必须遵守原策略止损与风控。")
    L.append("")
    if len(obs):
        L.append(_table(
            ["群体", "股票", "角色", "联动状态", "HVT-BULL", "W7", "E08", "VSW", "观察结论"],
            [[row["cluster_id"], f"{row['ts_code']} {row['stock_name']}", row["role"],
              row["linkage_state"], row.get("HVT-BULL") or "-", row.get("W7") or "-",
              row.get("E08") or "-", row.get("VSW") or "-", row["conclusion"]]
             for _, row in obs.iterrows()]))
        L.append("")
        for r in obs.itertuples(index=False):
            L.append(f"- **{r.ts_code} {r.stock_name}**（{r.role}）：{r.note}")
    else:
        L.append("_今日无进入观察池的股票_\n")
    L.append("")

    L.append("## 6. 风险提示")
    L.append("")
    risk_lines: list[str] = []
    for c in top:
        for r in risks_of(c):
            risk_lines.append(f"- **{c['cluster_id']}**：{r}")
    if not risk_lines:
        risk_lines.append("- 今日无 S/A 级群体，主要风险为「无有效联动机会」与误把统计同步当主线。")
    risk_lines.append(f"- 数据完整性：行情覆盖率 {_s(quality.get('panel', {}).get('quote_coverage', 0) * 100, 1)}%，"
                      f"PIT 历史简称覆盖率 {_s(quality.get('panel', {}).get('pit_name_coverage', 0) * 100, 1)}%，"
                      f"市场因子 {quality.get('panel', {}).get('market_source', '-')}。")
    risk_lines.append("- 行业分类为当前快照（非 PIT），仅用于双调整残差与事后归因，不参与主网络发现。")
    risk_lines.append("- 涨跌停为板块阈值近似（无 stk_limit 表），仅作行为特征。")
    L.extend(risk_lines)
    L.append("")
    L.append("---")
    L.append(f"计算耗时 {_s(timings.get('total', 0), 2)}s · 配置指纹 {cfg['_config_hash']} · "
             f"严格 as-of（无未来数据） · 数据源 cache_daily/stock_data.db")
    L.append("")

    md_path = outdir / "report.md"
    md_path.write_text("\n".join(L), encoding="utf-8")

    # ---------------- JSON ----------------
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

    # ---------------- CSV ----------------
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
