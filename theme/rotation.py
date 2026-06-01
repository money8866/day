from collections import Counter, defaultdict
from db import load_recent_scores, load_rotation_history, save_rotation


def build_rotation(trade_date, scored_themes, top_n=20):
    top = sorted(scored_themes, key=lambda x: x["score"], reverse=True)[:top_n]
    ranks = []
    for i, item in enumerate(top):
        ranks.append({"rank": i + 1, "theme_name": item["theme_name"], "score": round(item["score"], 2)})
    save_rotation(trade_date, ranks)
    return ranks


def compute_rotation_matrix(lookback_days=30):
    rows = load_rotation_history()
    if not rows:
        return Counter()

    date_groups = defaultdict(list)
    for r in rows:
        trade_date, rank, theme_name, score = r
        date_groups[trade_date].append((rank, theme_name, score))

    sorted_dates = sorted(date_groups.keys())[-lookback_days:]
    if len(sorted_dates) < 2:
        return Counter()

    transitions = Counter()
    for i in range(len(sorted_dates) - 1):
        d1, d2 = sorted_dates[i], sorted_dates[i + 1]
        top1 = {t[1] for t in date_groups[d1] if t[0] <= 10}
        top2 = {t[1] for t in date_groups[d2] if t[0] <= 10}
        left = top1 - top2
        entered = top2 - top1
        for out_t in left:
            for in_t in entered:
                transitions[(out_t, in_t)] += 1

    return transitions


def select_tomorrow_watch(scored_themes, top_n=10):
    candidates = []
    for item in scored_themes:
        recent = load_recent_scores(item["theme_name"], lookback=7)
        if len(recent) >= 3:
            recent_scores = [r[1] for r in sorted(recent, key=lambda x: x[0])]
            avg_5d = sum(recent_scores[-5:]) / len(recent_scores[-5:]) if len(recent_scores) >= 5 else sum(recent_scores) / len(recent_scores)
            accelerate = item["score"] - avg_5d
        else:
            accelerate = 0
        candidates.append({"theme_name": item["theme_name"], "score": item["score"], "accelerate": round(accelerate, 2)})

    candidates.sort(key=lambda x: x["accelerate"], reverse=True)
    return [c for c in candidates if c["accelerate"] > 0][:top_n]
