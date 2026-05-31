# -*- coding: utf-8 -*-
"""
block_decline_risk.py - Decline Risk Control for Sector Analysis

Import this module in etf_quant.py or block.py to add decline risk detection.
Provides: calc_decline_risk(), calc_sector_score_v2(), get_decline_warning()

Usage:
    import block_decline_risk as drc
    score, risk = drc.calc_sector_score_v2(df, name, state, df)
    print(risk)  # {'level': 0-3, 'signals': [...], 'discount': 0.4-1.0, 'detail': '...'}
"""

from collections import defaultdict

# =========================================================
# Signal definitions (Chinese)
# =========================================================
SIGNAL_LABELS = {
    'surge_crash': '高潮后骤降',
    'decay_3d': '连续3日衰减',
    'decay_2d': '动量+加速度双负',
    'leader_weak': '龙头涨幅低于中位数',
    'leader_green': '龙头翻绿板块仍涨',
    'downside_spread': '下跌扩散',
    'vol_price_diverge': '放量滞涨',
    'extreme_peak': '极端高潮(>2x均值)',
}

LEVEL_LABELS = {
    0: '安全',
    1: '警惕',
    2: '危险',
    3: '极度危险',
}


def calc_decline_risk(name, today_score, state, daily_df=None):
    """
    Calculate decline risk level for a sector.

    Args:
        name: sector name
        today_score: today's calc_sector_score result
        state: sector_state[name] dict with 'history' list
        daily_df: today's constituent stock data (optional)

    Returns:
        dict: {'level': 0-3, 'signals': [...], 'discount': float, 'detail': str}
    """
    h = state.get("history", [])

    if len(h) < 3:
        return {
            'level': 0, 'signals': [], 'discount': 1.0,
            'detail': LEVEL_LABELS[0]
        }

    signals = []

    # Signal 1: Surge then crash
    if len(h) >= 5:
        sorted_h = sorted(h[:-1])
        threshold = sorted_h[int(len(sorted_h) * 0.9)]
        recent_high = all(x >= threshold * 0.95 for x in h[-3:-1])
        today_drop = h[-1] < h[-2] * 0.90
        if recent_high and today_drop:
            signals.append('surge_crash')

    # Signal 2: Continuous decay
    if len(h) >= 3:
        momentum = h[-1] - h[-3]
        acc1 = h[-1] - h[-2]
        acc2 = h[-2] - h[-3]
        if momentum < 0 and acc1 < 0 and acc2 < 0:
            signals.append('decay_3d')
        elif momentum < 0 and acc1 < 0:
            signals.append('decay_2d')

    # Signal 3: Leader underperforming
    if daily_df is not None and len(daily_df) > 3:
        leader_pct = daily_df['pct_chg'].max()
        median_pct = daily_df['pct_chg'].median()
        avg_pct = daily_df['pct_chg'].mean()
        if median_pct > 0.5 and leader_pct < median_pct:
            signals.append('leader_weak')
        elif avg_pct > 0.5 and leader_pct < 0:
            signals.append('leader_green')

    # Signal 4: Downside spreading
    if daily_df is not None and len(daily_df) > 3:
        down_ratio = (daily_df['pct_chg'] < 0).mean()
        if down_ratio > 0.4 and today_score > 500:
            signals.append('downside_spread')

    # Signal 5: Volume-price divergence
    if daily_df is not None and len(daily_df) > 3:
        total_amount = daily_df['amount'].sum()
        avg_pct = daily_df['pct_chg'].mean()
        if total_amount > 50 and abs(avg_pct) < 0.3:
            signals.append('vol_price_diverge')

    # Signal 6: Extreme peak
    if len(h) >= 5:
        avg_5 = sum(h[-6:-1]) / 5
        if h[-1] > avg_5 * 2.0:
            signals.append('extreme_peak')

    n_signals = len(signals)
    level = min(n_signals, 3)
    discount = [1.0, 0.9, 0.7, 0.4][level]

    signal_labels = [SIGNAL_LABELS.get(s, s) for s in signals]
    detail = f"{LEVEL_LABELS[level]}: {', '.join(signal_labels)}" if signals else LEVEL_LABELS[0]

    return {
        'level': level,
        'signals': signals,
        'signal_labels': signal_labels,
        'discount': discount,
        'detail': detail,
    }


def calc_sector_score_v2(base_calc_fn, df, name, state, daily_df=None):
    """
    v2 score wrapper: applies decline risk discount to base score.

    Args:
        base_calc_fn: original calc_sector_score function
        df: constituent stock data
        name: sector name
        state: sector_state dict
        daily_df: same as df (for internal structure analysis)

    Returns:
        (final_score, risk_dict)
    """
    base_score = base_calc_fn(df)
    risk = calc_decline_risk(name, base_score, state, daily_df)
    final_score = round(base_score * risk['discount'], 2)
    return final_score, risk


def get_decline_warning(sector_df):
    """
    Scan sector dataframe for any sectors with decline level >= 1.

    Args:
        sector_df: output from analyze_hot_sectors() with decline columns

    Returns:
        list of warning dicts sorted by level desc
    """
    warnings = []

    decline_col = None
    for col in ['decline_level', 'tuichao_dengji']:
        if col in sector_df.columns:
            decline_col = col
            break

    if decline_col is None:
        return warnings

    high_risk = sector_df[sector_df[decline_col] >= 1].copy()

    for _, row in high_risk.iterrows():
        level = int(row[decline_col])
        name_col = 'name' if 'name' in row.index else 'zhuxian' if 'zhuxian' in row.index else None
        signals_col = 'decline_signals' if 'decline_signals' in row.index else 'tuichao_xinhao' if 'tuichao_xinhao' in row.index else None

        name = str(row[name_col]) if name_col else 'Unknown'
        signals = str(row[signals_col]) if signals_col else ''

        warnings.append({
            'sector': name,
            'level': level,
            'level_label': LEVEL_LABELS.get(level, str(level)),
            'signals': signals,
        })

    warnings.sort(key=lambda x: x['level'], reverse=True)
    return warnings


def format_decline_report(warnings):
    """
    Format warnings as a markdown table for AI report.

    Args:
        warnings: list from get_decline_warning()

    Returns:
        str: markdown table
    """
    if not warnings:
        return ""

    lines = ["## Decline Risk Report"]
    lines.append("")
    lines.append("| Sector | Level | Signals | Suggestion |")
    lines.append("|--------|-------|---------|------------|")

    suggestions = {
        1: "Monitor, no action",
        2: "Reduce 50%",
        3: "Exit immediately",
    }

    for w in warnings:
        signal_labels = [SIGNAL_LABELS.get(s.strip(), s.strip()) for s in w['signals'].split(',')]
        signals_str = ', '.join(signal_labels)
        lines.append(
            f"| {w['sector']} | {w['level_label']} | {signals_str} | {suggestions.get(w['level'], '')} |"
        )

    lines.append("")
    lines.append(f"Total: {len(warnings)} sectors with decline signals")
    return '\n'.join(lines)


# =========================================================
# Quick test
# =========================================================
if __name__ == "__main__":
    # Simulate a sector in decline
    state = {
        'history': [600, 700, 800, 900, 1000, 1200, 1300, 1250, 1150, 1050],
        'momentum': 0,
        'acc': 0,
        'leader': None
    }

    risk = calc_decline_risk("test_sector", 1050, state)
    print("Risk:", risk)

    # Test safe sector
    safe_state = {
        'history': [400, 450, 500, 550, 600, 650, 700],
        'momentum': 0,
        'acc': 0,
        'leader': None
    }
    safe_risk = calc_decline_risk("safe_sector", 700, safe_state)
    print("Safe:", safe_risk)
