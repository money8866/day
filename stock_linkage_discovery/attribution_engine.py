"""Driver Attribution Engine（第八、九层）—— 共同驱动因素识别。

严格在统计群体发现之后运行，结果不得反向影响关联网络、社区划分或群体成员。

分级：
  HIGH    产业链节点 / 明确共同产品环节，>=3 只股票，超代表 >=3 倍
  MEDIUM  主题或概念存在合理产业联系，但证据尚不充分
  LOW     仅传统行业分类重叠（可能只是共同资金偏好/统计联动）
  UNKNOWN 暂时无法解释共同驱动

不为了给群体命名而强行寻找概念；没有证据就输出空标签 + UNKNOWN。
同时区分统计联动 / 产业链联动 / 传统行业 / 事件驱动（事件源 Phase 3 未接入）。
"""
from __future__ import annotations

from typing import Any

from .common import get_logger
from .cluster_engine import ClusterSet
from .data_layer import Slice, load_tag_maps

log = get_logger("attribution_engine")


def analyze_clusters(cs: ClusterSet, s: Slice, cfg: dict[str, Any],
                     tag_maps: dict[str, dict[str, set[str]]] | None = None) -> dict[str, dict]:
    acfg = cfg["attribution"]
    if not acfg.get("enabled", True):
        return {c["cluster_id"]: {"confidence": "UNKNOWN", "label": "", "evidence": []}
                for c in cs.clusters}
    if tag_maps is None:
        tag_maps = load_tag_maps(cfg)
    universe = set(s.codes)
    n_uni = max(1, len(universe))
    out: dict[str, dict] = {}
    max_tags = int(acfg.get("max_tags_per_cluster", 5))

    for c in cs.clusters:
        members = set(c["members"])
        n = max(1, len(members))
        hits: list[dict[str, Any]] = []
        for kind, tags in tag_maps.items():
            for tag, stocks in tags.items():
                base = len(stocks & universe) / n_uni
                if base <= 0 or base > 0.35:   # 过大的标签无解释力
                    continue
                hit = members & stocks
                if not hit:
                    continue
                cov = len(hit) / n
                if cov < float(acfg.get("min_tag_coverage", 0.60)) * 0.5:
                    continue
                over = cov / base
                hits.append({
                    "tag_type": kind, "tag": tag, "stocks": sorted(hit),
                    "coverage": round(cov, 4), "baseline": round(base, 4),
                    "overrep": round(over, 4),
                })
        hits.sort(key=lambda h: -(h["overrep"] * (1 + h["coverage"])))
        hits = hits[:max_tags]
        if not hits:
            out[c["cluster_id"]] = {"confidence": "UNKNOWN", "label": "", "evidence": [],
                                    "kind": "STATISTICAL_ONLY",
                                    "note": "仅观察到统计联动，无可用产业/概念证据"}
            continue

        chain = [h for h in hits if h["tag_type"] in ("westock_node", "westock_theme")
                 and h["coverage"] >= float(acfg.get("min_tag_coverage", 0.60))
                 and h["overrep"] >= float(acfg.get("overrep_factor_high", 3.0))
                 and len(h["stocks"]) >= int(acfg.get("min_evidence_stocks_high", 3))]
        theme = [h for h in hits if h["tag_type"] in ("westock_theme", "kpl_concept", "westock_node")
                 and h["coverage"] >= float(acfg.get("min_tag_coverage", 0.60))
                 and h["overrep"] >= float(acfg.get("overrep_factor_medium", 1.8))]
        industry_only = [h for h in hits if h["tag_type"] == "industry"
                         and h["coverage"] >= float(acfg.get("min_tag_coverage", 0.60))]

        if chain:
            conf, kind = "HIGH", "INDUSTRY_CHAIN"
            label = chain[0]["tag"]
        elif theme:
            conf, kind = "MEDIUM", "THEME_OR_CONCEPT"
            label = theme[0]["tag"]
        elif industry_only:
            conf, kind = "LOW", "TRADITIONAL_INDUSTRY"
            label = industry_only[0]["tag"] + "（传统行业映射）"
        else:
            conf, kind = "LOW", "STATISTICAL_ONLY"
            label = hits[0]["tag"] + "（弱证据）"

        out[c["cluster_id"]] = {
            "confidence": conf, "label": label, "kind": kind,
            "evidence": hits,
            "cross_sector": c.get("cross_sector", False),
            "event_source": "NOT_CONNECTED_PHASE3",
            "_note": "归因不影响统计发现；事件驱动与共同资金偏好尚未接入独立证据源",
        }
    log.info("归因完成: %s", {k: sum(1 for v in out.values() if v["confidence"] == k)
                              for k in ("HIGH", "MEDIUM", "LOW", "UNKNOWN")})
    return out
