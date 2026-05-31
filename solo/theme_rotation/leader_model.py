# -*- coding: utf-8 -*-
"""龙头概率模型 + 启动股识别"""
from typing import Dict, List, Optional

from .config import LEADER_WEIGHTS


LAYER_SCORE = {"leader": 1.0, "core": 0.7, "follower": 0.4}


def calc_theme_sector_score(daily_rows: List[Dict]) -> Dict:
    """计算主题板块评分（简化版 block.py calc_sector_score）"""
    if not daily_rows:
        return {"score": 0, "zt_count": 0, "zt_ratio": 0, "max_lb": 0}

    pcts = [r["pct_chg"] for r in daily_rows if r.get("pct_chg") is not None]
    n = len(pcts)
    if n == 0:
        return {"score": 0, "zt_count": 0, "zt_ratio": 0, "max_lb": 0}

    pcts_sorted = sorted(pcts)
    left, right = int(n * 0.1), int(n * 0.9)
    trimmed = pcts_sorted[left:right] if right > left else pcts_sorted
    momentum = sum(trimmed) / len(trimmed)

    top1 = max(pcts)
    top3 = sum(sorted(pcts, reverse=True)[: min(3, n)]) / min(3, n)
    leader_strength = top1 * 2 + top3 * 1.5

    strong_cnt = sum(1 for p in pcts if p >= 5)
    limit_up = sum(1 for p in pcts if p >= 9.5)
    spread = (strong_cnt / n) * 20 + (limit_up / n) * 30

    amounts = [r.get("amount", 0) or 0 for r in daily_rows]
    avg_amount = sum(amounts) / max(len(amounts), 1)
    amount_score = min(avg_amount / 1e6, 15)

    zt_count = limit_up
    zt_ratio = limit_up / n * 100
    max_lb = max((r.get("lb_height", 0) or 0 for r in daily_rows), default=0)

    score = momentum * 3 + leader_strength + spread + amount_score
    return {
        "score": round(score, 2),
        "zt_count": zt_count,
        "zt_ratio": round(zt_ratio, 1),
        "max_lb": max_lb,
    }


def calc_leader_prob(
    stock: Dict,
    theme_avg_pct: float,
    max_lb_in_theme: int,
) -> float:
    """龙头概率 0~100"""
    w = LEADER_WEIGHTS

    layer_s = LAYER_SCORE.get(stock.get("layer", "follower"), 0.3)
    trend_s = min(max(stock.get("trend", 0) / 20, 0), 1)

    lb = stock.get("lb_height", 0) or 0
    limit_s = min(lb / max(max_lb_in_theme, 1), 1) if max_lb_in_theme else (1 if lb else 0)

    turnover_s = min(stock.get("turnover", 0) / 15, 1)
    purity_s = min(stock.get("purity", 0) / 5, 1)

    pct = stock.get("pct_chg", 0) or 0
    rel_s = min(max((pct - theme_avg_pct) / 10 + 0.5, 0), 1)

    prob = (
        w["layer"] * layer_s
        + w["trend"] * trend_s
        + w["limit_up"] * limit_s
        + w["turnover"] * turnover_s
        + w["purity"] * purity_s
        + w["relative_strength"] * rel_s
    ) * 100
    return round(prob, 2)


def calc_starter_prob(stock: Dict, theme_avg_pct: float, is_first_mover: bool) -> float:
    """启动股概率：谁最先带动主题"""
    base = calc_leader_prob(stock, theme_avg_pct, stock.get("lb_height", 0))

    pct = stock.get("pct_chg", 0) or 0
    early_bonus = 0
    if pct >= 9.5:
        early_bonus += 25
    elif pct >= 7:
        early_bonus += 15
    elif pct >= 5:
        early_bonus += 8

    if is_first_mover:
        early_bonus += 20

    turnover = stock.get("turnover", 0) or 0
    if turnover >= 8:
        early_bonus += 5

    return round(min(base + early_bonus, 100), 2)


def identify_starter(stocks: List[Dict]) -> Optional[Dict]:
    """识别主题内第1只启动股"""
    if not stocks:
        return None

    # 涨停优先，其次涨幅+换手
    limit_ups = [s for s in stocks if (s.get("pct_chg") or 0) >= 9.5]
    if limit_ups:
        return max(limit_ups, key=lambda x: (x.get("turnover", 0), x.get("pct_chg", 0)))

    strong = [s for s in stocks if (s.get("pct_chg") or 0) >= 5]
    if strong:
        return max(strong, key=lambda x: (x.get("pct_chg", 0), x.get("turnover", 0)))

    return max(stocks, key=lambda x: x.get("starter_prob", 0))


def score_theme_stocks(
    portfolio_stocks: List[Dict],
    daily_map: Dict[str, Dict],
    lb_map: Dict[str, int],
) -> List[Dict]:
    """为某主题全部成份股打分"""
    rows = []
    for s in portfolio_stocks:
        ts_code = s["ts_code"]
        d = daily_map.get(ts_code, {})
        row = {
            **s,
            "pct_chg": d.get("pct_chg", 0),
            "amount": d.get("amount", s.get("amount", 0)),
            "turnover": d.get("turnover_rate", s.get("turnover", 0)),
            "lb_height": lb_map.get(ts_code, 0),
            "is_limit_up": 1 if (d.get("pct_chg") or 0) >= 9.5 else 0,
        }
        rows.append(row)

    if not rows:
        return []

    theme_avg = sum(r["pct_chg"] for r in rows) / len(rows)
    max_lb = max((r["lb_height"] for r in rows), default=0)

    for r in rows:
        r["leader_prob"] = calc_leader_prob(r, theme_avg, max_lb)

    # 找第一启动：最高涨幅中换手最大的
    best_pct = max(r["pct_chg"] for r in rows)
    first_movers = [r for r in rows if r["pct_chg"] >= best_pct - 0.5]

    for r in rows:
        is_first = r in first_movers and r["pct_chg"] == best_pct
        r["starter_prob"] = calc_starter_prob(r, theme_avg, is_first)

    starter = identify_starter(rows)
    if starter:
        for r in rows:
            r["is_starter"] = 1 if r["ts_code"] == starter["ts_code"] else 0

    return rows
