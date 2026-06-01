from db import load_recent_scores


def detect_stage(theme_name, current_score):
    """
    识别题材生命周期阶段。
    阈值根据实际评分分布校准（top1约70-80分，top10约25-50分）：

    启动: 30+ 且持续加速 (如30→38→45)
    主升: 50+ 持续3天或单日60+
    高潮: 70+ 领涨全市场
    退潮: 50+ 连续2天下降或40+连续3天下降
    """
    recent = load_recent_scores(theme_name, lookback=7)

    # ── 无历史 → 按当前分数估算 ──
    if not recent:
        if current_score >= 60:
            return "主升"
        elif current_score >= 35:
            return "启动"
        else:
            return "震荡"

    scores = [r[1] for r in sorted(recent, key=lambda x: x[0])]
    scores.append(current_score)

    if len(scores) < 3:
        if current_score >= 60:
            return "主升"
        elif current_score >= 35:
            return "启动"
        else:
            return "震荡"

    s1, s2, s3 = scores[-3], scores[-2], scores[-1]
    look3 = scores[-3:]

    # ── 高潮：70+ 领涨 ──
    if s3 >= 70:
        return "高潮"

    # ── 退潮：从高位连续下降 ──
    if s1 >= 50 and s2 < s1 and s3 < s2:
        return "退潮"
    if len(scores) >= 4:
        s0 = scores[-4]
        if s0 >= 50 and s1 < s0 and s2 < s1 and s3 < s2:
            return "退潮"

    # ── 主升：60+ 单日强势，或50+ 持续 ──
    if s3 >= 60:
        return "主升"
    if all(s >= 50 for s in look3):
        return "主升"

    # ── 启动：从低位加速上行 ──
    if s1 >= 25 and s2 > s1 and s3 > s2:
        return "启动"
    if s3 >= 35 and s2 > s1 and s3 > s2:
        return "启动"

    return "震荡"


def detect_stages_for_all(scored_themes):
    results = []
    for item in scored_themes:
        stage = detect_stage(item["theme_name"], item["score"])
        results.append({"theme_name": item["theme_name"], "score": item["score"], "stage": stage})
    return results
