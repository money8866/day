"""
择时信号评分器
综合 趋势/低吸/突破 三类信号，输出每日操作建议

用法:
    from timing_scorer import compute_timing_scores
    result = compute_timing_scores(ts_code, daily_df)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional


def _ma(series: pd.Series, n: int) -> float:
    """计算移动平均（最后一天的值）"""
    if len(series) < n:
        return 0.0
    return series.tail(n).mean()


def _volume_ratio(vol_series: pd.Series) -> float:
    """量比 = 今日量 / 5日均量"""
    if len(vol_series) < 2:
        return 1.0
    today_vol = vol_series.iloc[-1]
    avg_vol = vol_series.tail(min(6, len(vol_series))).iloc[:-1].mean()  # 前5日均量
    if avg_vol <= 0:
        return 1.0
    return today_vol / avg_vol


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
    """计算MACD三值（最后一天）"""
    if len(close) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    return float(dif.iloc[-1]), float(dea.iloc[-1]), float(macd_bar.iloc[-1])


def _rsi(close: pd.Series, n: int = 14) -> float:
    """计算RSI（最后一天）"""
    if len(close) < n + 1:
        return 50.0
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.tail(n + 1).mean()
    avg_loss = loss.tail(n + 1).mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> Tuple[float, float, float]:
    """计算KDJ三值（最后一天）"""
    if len(close) < n:
        return 50.0, 50.0, 50.0
    h_n = high.tail(n).max()
    l_n = low.tail(n).min()
    if h_n == l_n:
        return 50.0, 50.0, 50.0
    rsv = (close.iloc[-1] - l_n) / (h_n - l_n) * 100
    k = 2 / 3 * 50.0 + 1 / 3 * rsv
    d = 2 / 3 * 50.0 + 1 / 3 * k
    j = 3 * k - 2 * d
    return float(k), float(d), float(j)


def _lower_shadow_ratio(row: pd.Series) -> float:
    """下影线比例 = (min(open,close) - low) / (high - low)"""
    amplitude = row['high'] - row['low']
    if amplitude <= 0:
        return 0.0
    body_low = min(row['open'], row['close'])
    return (body_low - row['low']) / amplitude


def _drawdown_from_high(close: pd.Series, lookback: int = 60) -> float:
    """从近期高点的回撤幅度（%）"""
    if len(close) < 2:
        return 0.0
    period = close.tail(min(lookback, len(close)))
    peak = period.max()
    current = close.iloc[-1]
    if peak <= 0:
        return 0.0
    return (peak - current) / peak * 100


def compute_timing_scores(ts_code: str, daily_df: pd.DataFrame,
                          chip_info: dict = None) -> Dict:
    """
    计算综合择时信号分

    Args:
        ts_code: 股票代码
        daily_df: 该股票的日线数据（含 trade_date, open, high, low, close, vol）
        chip_info: 筹码信息（可选，含成本线等）

    Returns:
        {
            'ts_code': str,
            'trend_score': float,       # 趋势信号分 0~100
            'dip_score': float,         # 低吸信号分 0~100
            'breakout_score': float,    # 突破信号分 0~100
            'composite_score': float,   # 综合信号分 = max(三类)
            'signal_type': str,         # trend / dip / breakout
            'signal_level': str,        # 强烈(>=80) / 中等(>=60) / 一般(>=40) / 观望(<40)
            'signals': List[str],       # 触发的具体信号清单
            'suggestion': str,          # 操作建议
        }
    """
    if daily_df is None or len(daily_df) < 20:
        return _default_result(ts_code, "数据不足")

    df = daily_df.sort_values('trade_date').reset_index(drop=True)
    close = df['close']
    vol = df['vol']
    high = df['high']
    low = df['low']
    latest = df.iloc[-1]
    pct_chg = latest.get('pct_chg', 0)

    # ── 计算技术指标 ──
    ma5 = _ma(close, 5)
    ma10 = _ma(close, 10)
    ma20 = _ma(close, 20)
    ma60 = _ma(close, 60) if len(close) >= 60 else _ma(close, min(20, len(close)))
    vol_ratio = _volume_ratio(vol)
    dif, dea, macd_bar = _macd(close)
    rsi14 = _rsi(close, 14)
    k, d, j_val = _kdj(high, low, close)
    shadow_ratio = _lower_shadow_ratio(latest)
    drawdown = _drawdown_from_high(close, 60)
    close_price = latest['close']
    amplitude_20d = (high.tail(20).max() - low.tail(20).min()) / close_price * 100 if len(close) >= 20 else 0
    high_20d = high.tail(20).max()
    low_20d = low.tail(20).min()

    signals: List[str] = []
    pct_chg_20d = ((close.iloc[-1] / close.iloc[-min(21, len(close))]) - 1) * 100 if len(close) >= 21 else 0

    # ================================================================
    # ① 趋势信号分 (0~100)
    # ================================================================
    trend_score = 0.0
    trend_signals: List[str] = []

    # MA排列
    if ma5 > ma10 > ma20:
        trend_score += 30
        trend_signals.append("MA多头排列")
    elif ma5 > ma20:
        trend_score += 15
        trend_signals.append("MA偏多")
    else:
        trend_signals.append("MA空头")

    # MACD
    if dif > dea > 0 and len(close) >= 3 and dif > float((close.ewm(span=12).mean() - close.ewm(span=26).mean()).iloc[-3] if len(close) >= 26 else dif):
        trend_score += 20
        trend_signals.append("MACD金叉+柱扩大")
    elif dif > dea > 0:
        trend_score += 10
        trend_signals.append("MACD金叉")
    elif dif > dea:
        trend_score += 5
        trend_signals.append("MACD偏多")

    # 站上MA20 + 量比配合
    if close_price > ma20 and vol_ratio >= 1.3:
        trend_score += 20
        trend_signals.append("站上MA20+放量")
    elif close_price > ma20:
        trend_score += 10
        trend_signals.append("站上MA20")
    elif close_price > ma10:
        trend_score += 5

    # 20日涨幅评分
    if 5 <= pct_chg_20d <= 20:
        trend_score += 15
        trend_signals.append(f"20日涨幅适中({pct_chg_20d:.0f}%)")
    elif pct_chg_20d > 20:
        trend_score += 10
        trend_signals.append(f"20日涨幅偏高({pct_chg_20d:.0f}%)")
    elif pct_chg_20d > 0:
        trend_score += 5

    # KDJ多头
    if j_val > k > 50:
        trend_score += 15
        trend_signals.append("KDJ多头")
    elif j_val > k:
        trend_score += 8

    trend_score = min(trend_score, 100)

    # ================================================================
    # ② 低吸信号分 (0~100)
    # ================================================================
    dip_score = 0.0
    dip_signals: List[str] = []

    # 回踩MA10 / MA20
    dist_to_ma10 = abs(close_price - ma10) / ma10 * 100 if ma10 > 0 else 999
    dist_to_ma20 = abs(close_price - ma20) / ma20 * 100 if ma20 > 0 else 999
    if dist_to_ma10 <= 3:
        dip_score += 20
        dip_signals.append(f"回踩MA10(偏离{dist_to_ma10:.1f}%)")
    elif dist_to_ma20 <= 5:
        dip_score += 15
        dip_signals.append(f"回踩MA20(偏离{dist_to_ma20:.1f}%)")
    elif dist_to_ma20 <= 8:
        dip_score += 8
        dip_signals.append(f"接近MA20(偏离{dist_to_ma20:.1f}%)")

    # 缩量
    if vol_ratio < 0.8 and vol_ratio > 0:
        dip_score += 20
        dip_signals.append(f"缩量(量比{vol_ratio:.2f})")
    elif vol_ratio < 1.0:
        dip_score += 10
        dip_signals.append("量能偏低")

    # 下影线
    if shadow_ratio > 0.5:
        dip_score += 15
        dip_signals.append(f"下影线({shadow_ratio:.0%})")

    # 筹码支撑（如果提供筹码信息）
    if chip_info and 'cost_price' in chip_info:
        cost = chip_info['cost_price']
        if cost > 0:
            dist_to_cost = abs(close_price - cost) / cost * 100
            if dist_to_cost <= 5:
                dip_score += 20
                dip_signals.append(f"筹码支撑(偏离{dist_to_cost:.1f}%)")
            elif dist_to_cost <= 10:
                dip_score += 10
                dip_signals.append("接近筹码峰")

    # RSI超卖
    if rsi14 < 35:
        dip_score += 25
        dip_signals.append(f"RSI超卖({rsi14:.0f})")
    elif rsi14 < 45:
        dip_score += 15
        dip_signals.append(f"RSI偏低({rsi14:.0f})")
    elif rsi14 < 55:
        dip_score += 5

    # 回撤深度
    if drawdown >= 10:
        dip_score += 10
        dip_signals.append(f"回撤{drawdown:.0f}%")

    dip_score = min(dip_score, 100)

    # ================================================================
    # ③ 突破信号分 (0~100)
    # ================================================================
    breakout_score = 0.0
    breakout_signals: List[str] = []

    # 放量突破
    if abs(pct_chg) >= 3 and vol_ratio >= 1.5:
        breakout_score += 30
        breakout_signals.append(f"放量突破(涨幅{pct_chg:.1f}%+量比{vol_ratio:.2f})")
    elif abs(pct_chg) >= 2 and vol_ratio >= 1.3:
        breakout_score += 15
        breakout_signals.append("放量上涨")

    # 突破20日新高
    if close_price >= high_20d >= low_20d > 0:
        breakout_score += 20
        breakout_signals.append("突破20日新高")
    elif close_price >= high.tail(10).max():
        breakout_score += 10
        breakout_signals.append("突破10日新高")

    # 金叉
    if len(close) >= 11:
        ma5_prev = _ma(close.tail(11).iloc[:-1], 5) if len(close) >= 11 else 0
        ma10_prev = _ma(close.tail(11).iloc[:-1], 10) if len(close) >= 11 else 0
        if ma5_prev <= ma10_prev and ma5 > ma10:
            breakout_score += 20
            breakout_signals.append("MA5金叉MA10")
        elif len(close) >= 21:
            ma10_prev2 = _ma(close.tail(21).iloc[:-1], 10) if len(close) >= 21 else 0
            ma20_prev = _ma(close.tail(21).iloc[:-1], 20) if len(close) >= 21 else 0
            if ma10_prev2 <= ma20_prev and ma10 > ma20:
                breakout_score += 20
                breakout_signals.append("MA10金叉MA20")
            else:
                breakout_score += 5
                breakout_signals.append("均线偏多")
        else:
            breakout_score += 5

    # 箱体突破
    if amplitude_20d < 15 and amplitude_20d > 0:
        upper = low_20d + amplitude_20d / 100 * low_20d
        if close_price >= upper * 0.98:
            breakout_score += 20
            breakout_signals.append(f"箱体突破(20日振幅{amplitude_20d:.1f}%)")
        elif close_price >= low_20d + (upper - low_20d) * 0.8:
            breakout_score += 10
            breakout_signals.append("接近箱体上沿")

    # 涨停基因
    zt_count = len(df[df['pct_chg'] >= 9.5]) if 'pct_chg' in df.columns else 0
    zt_recent = len(df.tail(60)[df.tail(60)['pct_chg'] >= 9.5]) if len(df) >= 60 else zt_count
    if zt_recent >= 1:
        breakout_score += 10
        breakout_signals.append("近期有涨停")

    breakout_score = min(breakout_score, 100)

    # ================================================================
    # ④ 综合评分
    # ================================================================
    scores = [
        ('trend', trend_score),
        ('dip', dip_score),
        ('breakout', breakout_score),
    ]
    best = max(scores, key=lambda x: x[1])
    composite_score = best[1]
    signal_type = best[0]

    # 信号强度等级
    if composite_score >= 80:
        signal_level = "强烈"
    elif composite_score >= 60:
        signal_level = "中等"
    elif composite_score >= 40:
        signal_level = "一般"
    else:
        signal_level = "观望"

    # 操作建议
    all_signals = trend_signals + dip_signals + breakout_signals
    suggestion = _make_suggestion(signal_type, signal_level, all_signals, pct_chg)

    return {
        'ts_code': ts_code,
        'trend_score': round(trend_score, 1),
        'dip_score': round(dip_score, 1),
        'breakout_score': round(breakout_score, 1),
        'composite_score': round(composite_score, 1),
        'signal_type': signal_type,
        'signal_level': signal_level,
        'signals': all_signals[:6],  # 最多6个信号
        'suggestion': suggestion,
    }


def _make_suggestion(signal_type: str, level: str,
                     signals: List[str], pct_chg: float) -> str:
    """生成操作建议"""
    if level == "强烈":
        if signal_type == "dip":
            return "★ 强烈建议逢低介入"
        elif signal_type == "breakout":
            return "★ 强烈建议追涨参与"
        else:
            return "★ 强烈建议积极关注"
    elif level == "中等":
        if signal_type == "dip":
            return "可逢低分批建仓"
        elif signal_type == "breakout":
            return "可小仓参与突破"
        else:
            return "可适度关注"
    elif level == "一般":
        if pct_chg > 3:
            return "观察，不追高"
        else:
            return "保持观望，等待信号加强"
    else:
        return "暂不参与"


def _default_result(ts_code: str, reason: str = "") -> Dict:
    """数据不足时的默认返回"""
    return {
        'ts_code': ts_code,
        'trend_score': 0.0,
        'dip_score': 0.0,
        'breakout_score': 0.0,
        'composite_score': 0.0,
        'signal_type': 'none',
        'signal_level': '观望',
        'signals': [f"数据不足: {reason}"] if reason else [],
        'suggestion': '数据不足',
    }


def batch_timing_scan(results_list: List, daily_history: pd.DataFrame,
                      chip_map: dict = None) -> List[Dict]:
    """
    批量择时扫描

    Args:
        results_list: BullScoreV2Result 列表
        daily_history: 全市场日线DataFrame（含 ts_code 列）
        chip_map: {ts_code: chip_info} 可选

    Returns:
        按综合信号分排序的择时结果列表
    """
    timing_results = []
    for r in results_list:
        ts_code = r.ts_code
        stock_daily = daily_history[daily_history['ts_code'] == ts_code] if daily_history is not None else None
        chip_info = (chip_map or {}).get(ts_code)
        result = compute_timing_scores(ts_code, stock_daily, chip_info)
        # 附上股票基本信息
        result['name'] = getattr(r, 'name', '')
        result['industry'] = getattr(r, 'industry', '')
        result['theme'] = getattr(r, 'theme', '') or getattr(r, 'chain_tag', '')
        result['final_score'] = round(getattr(r, 'final_score', 0), 1)
        result['bull_level'] = getattr(r, 'bull_level', '')
        timing_results.append(result)

    timing_results.sort(key=lambda x: x['composite_score'], reverse=True)
    return timing_results


def print_timing_report(timing_results: List[Dict], top_n: int = 30):
    """打印择时信号报告"""
    print("\n" + "═" * 100)
    print("  择时信号矩阵 — 综合评分排名")
    print("═" * 100)
    print(f"  {'序号':>3} {'代码':>10} {'名称':>10} {'主题':>14} {'综合分':>6} {'趋势分':>6} {'低吸分':>6} {'突破分':>6} {'信号类型':>8} {'信号等级':>8}  {'操作建议'}")
    print("─" * 100)

    strong_count = 0
    medium_count = 0
    for i, tr in enumerate(timing_results[:top_n], 1):
        code = tr['ts_code'].replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
        print(f"  {i:>3} {code:>10} {tr.get('name',''):>10} "
              f"{tr.get('theme','')[:12]:>14} "
              f"{tr['composite_score']:>6.1f} {tr['trend_score']:>6.1f} "
              f"{tr['dip_score']:>6.1f} {tr['breakout_score']:>6.1f} "
              f"{tr['signal_type']:>8} {tr['signal_level']:>8}  {tr['suggestion']}")
        if tr['signal_level'] == '强烈':
            strong_count += 1
        elif tr['signal_level'] == '中等':
            medium_count += 1

    # 统计
    total = len(timing_results)
    print("─" * 100)
    print(f"  共{total}只 | 强烈{strong_count}只 | 中等{medium_count}只 | "
          f"观望{total - strong_count - medium_count}只")

    # 打印强烈信号详细
    strong_list = [tr for tr in timing_results if tr['signal_level'] == '强烈']
    if strong_list:
        print("\n" + "═" * 100)
        print("  ★ 强烈信号标的 — 详细信号")
        print("═" * 100)
        for tr in strong_list:
            code = tr['ts_code'].replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
            print(f"  {code} {tr.get('name','')} | {tr['suggestion']} | "
                  f"信号: {', '.join(tr['signals'][:4])}")
