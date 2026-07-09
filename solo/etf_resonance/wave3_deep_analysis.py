"""Wave 3 深度技术分析 - 长周期形态浪解析

对今日选出的3只股票(兆易创新/北方华创/江化微)做长周期深度分析：
  1. 拉取3年+历史K线(约750交易日)
  2. 列出全部枢轴点(局部极值)时间序列
  3. 多层级波浪分解：大浪(PIVOT_WINDOW=20) / 中浪(10) / 小浪(5)
  4. 历史波浪完整演进：找出每一轮"L0→H1→L2→H3→L4→H5"五浪结构
  5. 技术指标：MA5/10/20/60/120/250、MACD、RSI、KDJ、布林带、量能趋势
  6. 关键支撑/压力位：枢轴点聚集区、黄金分割位、整数关、均线位
  7. 与上一轮主升浪的相似性比较
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from dotenv import load_dotenv
from multi_factor_picker.data_fetcher import DataFetcher
from etf_resonance.utils.indicators import ema, sma, atr, slope
from etf_resonance.wave3_detector import (
    Pivot, WaveCount, find_pivots, detect_waves, score_wave3_signal
)

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})


TARGETS = [
    ('603986.SH', '兆易创新', '半导体'),
    ('002371.SZ', '北方华创', '半导体'),
    ('603078.SH', '江化微', '半导体'),
]

HISTORY_DAYS = 1100  # ~4.5年
PIVOT_WINDOWS = [5, 10, 20]  # 小浪/中浪/大浪


def fmt_date(d: str) -> str:
    """20260307 -> 2026-03-07"""
    s = str(d)
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def load_history(ts_code: str, days: int = HISTORY_DAYS) -> Optional[pd.DataFrame]:
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    try:
        df = dfetcher.get_daily_by_code(ts_code=ts_code, start_date=start, end_date=end)
    except Exception as e:
        print(f"  [ERROR] {ts_code} 获取数据失败: {e}")
        return None
    if df is None or df.empty:
        return None
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """补充技术指标列。"""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['vol'].values if 'vol' in df.columns else df.get('volume', pd.Series([0]*len(df))).values

    df = df.copy()
    df['ma5'] = sma(close, 5)
    df['ma10'] = sma(close, 10)
    df['ma20'] = sma(close, 20)
    df['ma60'] = sma(close, 60)
    df['ma120'] = sma(close, 120)
    df['ma250'] = sma(close, 250)

    # MACD
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    dif = ema12 - ema26
    dea = ema(dif, 9)
    macd = (dif - dea) * 2
    df['dif'] = dif
    df['dea'] = dea
    df['macd'] = macd

    # RSI(14)
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=14).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=14).mean().values
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    rsi = 100 - 100 / (1 + rs)
    df['rsi14'] = rsi

    # KDJ
    low9 = pd.Series(low).rolling(9, min_periods=9).min().values
    high9 = pd.Series(high).rolling(9, min_periods=9).max().values
    rsv = (close - low9) / np.where(high9 - low9 > 0, high9 - low9, 1) * 100
    k = pd.Series(rsv).ewm(alpha=1/3, adjust=False).mean().values
    d = pd.Series(k).ewm(alpha=1/3, adjust=False).mean().values
    j = 3 * k - 2 * d
    df['k'] = k
    df['d'] = d
    df['j'] = j

    # BOLL(20, 2σ)
    mid = sma(close, 20)
    std = pd.Series(close).rolling(20, min_periods=20).std().values
    df['boll_mid'] = mid
    df['boll_up'] = mid + 2 * std
    df['boll_dn'] = mid - 2 * std

    # 量能均线
    df['vol_ma5'] = sma(vol, 5)
    df['vol_ma20'] = sma(vol, 20)
    df['vol_ma60'] = sma(vol, 60)
    return df


def pivot_summary(df: pd.DataFrame, window: int, label: str) -> List[Tuple[Pivot, float]]:
    """识别某窗口下的枢轴点,并计算其相对最大涨幅/跌幅。"""
    pivots = find_pivots(df, window=window)
    out = []
    for p in pivots:
        if p.kind == 'high':
            ref = df['low'].iloc[max(0, p.idx - 60):p.idx].min() if p.idx > 0 else p.price
            move = (p.price - ref) / max(ref, 1e-6) * 100
        else:
            ref = df['high'].iloc[max(0, p.idx - 60):p.idx].max() if p.idx > 0 else p.price
            move = (p.price - ref) / max(ref, 1e-6) * 100
        out.append((p, move))
    return out


def find_all_wave_structures(pivots: List[Pivot], df: pd.DataFrame,
                             w1_min: float = 0.30) -> List[WaveCount]:
    """找出所有满足波浪铁律的L0→H1→L2(→H3→L4→H5)结构,不止best一个。"""
    waves: List[WaveCount] = []
    if len(pivots) < 3:
        return waves

    for i in range(len(pivots) - 2):
        if pivots[i].kind != 'low':
            continue
        L0 = pivots[i]
        H1 = pivots[i + 1] if pivots[i + 1].kind == 'high' else None
        if H1 is None:
            continue
        L2 = pivots[i + 2] if pivots[i + 2].kind == 'low' else None
        if L2 is None:
            continue

        w1_gain = (H1.price - L0.price) / max(L0.price, 1e-6)
        if w1_gain < w1_min:
            continue
        w2_retrace = (H1.price - L2.price) / max(H1.price - L0.price, 1e-6)
        if not (0.10 <= w2_retrace <= 0.95):
            continue
        if L2.price <= L0.price:
            continue

        w3_target = L2.price + (H1.price - L0.price) * 1.618
        wave = WaveCount(
            L0=L0, H1=H1, L2=L2,
            w1_gain=w1_gain, w2_retrace=w2_retrace,
            w3_target_price=w3_target, is_valid=True,
        )

        for j in range(i + 3, len(pivots)):
            p = pivots[j]
            if p.kind == 'high' and wave.H3 is None:
                wave.H3 = p
                w3_len = p.price - L2.price
                w1_len = H1.price - L0.price
                wave.w3_ratio = w3_len / max(w1_len, 1e-6)
            elif p.kind == 'low' and wave.H3 is not None and wave.L4 is None:
                if p.price < H1.price:
                    wave.is_valid = False
                    wave.violation = f'第4浪低点({p.price:.2f})跌破第1浪顶({H1.price:.2f})'
                wave.L4 = p
            elif p.kind == 'high' and wave.L4 is not None and wave.H5 is None:
                wave.H5 = p
                break

        waves.append(wave)
    return waves


def find_nearest_support_resistance(df: pd.DataFrame, pivots: List[Pivot],
                                     current_price: float) -> Dict:
    """找出最近的支撑/压力位。"""
    recent_pivots = [p for p in pivots if abs(p.idx - len(df)) < 250]
    highs = sorted([p for p in recent_pivots if p.kind == 'high' and p.price > current_price],
                   key=lambda p: p.price)
    lows = sorted([p for p in recent_pivots if p.kind == 'low' and p.price < current_price],
                  key=lambda p: -p.price)

    last = df.iloc[-1]
    supports = []
    resistances = []

    # 均线支撑
    for name in ['ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma250']:
        v = last.get(name, np.nan)
        if not np.isnan(v) and v > 0:
            if v < current_price:
                supports.append((f'{name.upper()}', float(v), (current_price - v) / current_price * 100))
            else:
                resistances.append((f'{name.upper()}', float(v), (v - current_price) / current_price * 100))

    # 枢轴点
    for p in lows[:5]:
        supports.append((f'枢轴低{fmt_date(p.date)[-5:]}', p.price,
                         (current_price - p.price) / current_price * 100))
    for p in highs[:5]:
        resistances.append((f'枢轴高{fmt_date(p.date)[-5:]}', p.price,
                            (p.price - current_price) / current_price * 100))

    # 黄金分割位(以最近一轮L0→H1为基准)
    if len(pivots) >= 3:
        lows_seq = [p for p in pivots if p.kind == 'low']
        highs_seq = [p for p in pivots if p.kind == 'high']
        if lows_seq and highs_seq:
            L0 = lows_seq[-1]
            H1 = highs_seq[-1]
            swing = H1.price - L0.price
            for ratio, name in [(0.382, '回撤38.2%'), (0.5, '回撤50%'), (0.618, '回撤61.8%')]:
                lvl = H1.price - swing * ratio
                if lvl < current_price:
                    supports.append((f'黄金{name}', lvl, (current_price - lvl) / current_price * 100))
                else:
                    resistances.append((f'黄金{name}', lvl, (lvl - current_price) / current_price * 100))

    supports = sorted(supports, key=lambda x: -x[1])[:6]
    resistances = sorted(resistances, key=lambda x: x[1])[:6]
    return {'supports': supports, 'resistances': resistances}


def compare_with_last_wave(waves: List[WaveCount], current_wave: WaveCount) -> Optional[Dict]:
    """与上一轮完整波浪结构做相似性比较。"""
    completed = [w for w in waves if w.H5 is not None and w is not current_wave]
    if not completed:
        return None
    last = completed[-1]
    return {
        'last_L0_date': last.L0.date,
        'last_L0_price': last.L0.price,
        'last_H1_date': last.H1.date,
        'last_H1_price': last.H1.price,
        'last_w1_gain': last.w1_gain,
        'last_w2_retrace': last.w2_retrace,
        'last_w3_ratio': last.w3_ratio,
        'last_H5_price': last.H5.price if last.H5 else None,
        'w1_similarity': abs(last.w1_gain - current_wave.w1_gain) / max(current_wave.w1_gain, 1e-6),
        'w2_similarity': abs(last.w2_retrace - current_wave.w2_retrace),
    }


def compute_priority(r: Dict) -> Tuple[float, List[str]]:
    """综合8个维度计算操作优先级评分(0-100),分值越高越优先介入。

    维度权重(总计100):
      1. 波浪阶段 (20): W3刚启动最优,已延伸过多风险高
      2. 均线形态 (20): 完美多头排列=趋势确认
      3. 量价配合 (15): 放量上涨=主力收集
      4. 与上一轮相似度 (15): 历史会重演,相似度高则胜率高
      5. 距W3目标空间 (10): 剩余上涨空间
      6. 短期技术状态 (10): MACD/RSI/KDJ是否超买回调
      7. 距历史新高 (5): 接近新高=强势,但空间受限
      8. 历史回撤风险 (5): 近1年最大回撤越小越稳
    """
    score = 0.0
    reasons: List[str] = []

    cur = r.get('current_wave')
    ind = r.get('indicators', {})
    sr = r.get('support_resistance', {'supports': [], 'resistances': []})

    # 1. 波浪阶段 (20分)
    if cur:
        if cur.H3 is None:
            score += 20
            reasons.append(f'W3刚启动未到H3(阶段最优, +20)')
        else:
            ratio = cur.w3_ratio
            if ratio < 1.0:
                score += 18
                reasons.append(f'W3延伸{ratio:.2f}x<1.0(主升早期, +18)')
            elif ratio < 1.618:
                score += 10
                reasons.append(f'W3延伸{ratio:.2f}x接近1.618目标(中后段, +10)')
            else:
                score += 4
                reasons.append(f'W3延伸{ratio:.2f}x已超1.618(后期风险, +4)')
    else:
        score += 5
        reasons.append('波浪阶段不明(+5)')

    # 2. 均线形态 (20分)
    ma5, ma10, ma20, ma60, ma120, ma250 = (
        ind.get('ma5', 0), ind.get('ma10', 0), ind.get('ma20', 0),
        ind.get('ma60', 0), ind.get('ma120', 0), ind.get('ma250', 0)
    )
    cp = r['current_price']
    if all(v > 0 for v in [ma5, ma10, ma20, ma60, ma120, ma250]):
        if ma5 > ma10 > ma20 > ma60 > ma120 > ma250:
            score += 20
            reasons.append('完美多头排列MA5>MA10>MA20>MA60>MA120>MA250(+20)')
        elif ma5 > ma20 > ma60:
            score += 10
            reasons.append('均线多头(MA5>MA20>MA60)(+10)')
        else:
            score += 3
            reasons.append('均线紊乱(+3)')
    else:
        score += 3
        reasons.append('均线数据不足(+3)')

    # 3. 量价配合 (15分)
    vol_5_20 = r.get('vol_ratio_5_20', 0)
    up_vs_down = r.get('up_vol_vs_down_vol', 1.0)
    if vol_5_20 >= 1.2 and up_vs_down >= 1.1:
        score += 15
        reasons.append(f'放量上涨(量比{vol_5_20:.2f}+阳量/阴量{up_vs_down:.2f}, +15)')
    elif vol_5_20 >= 1.1 and up_vs_down >= 1.0:
        score += 10
        reasons.append(f'量价配合良好(量比{vol_5_20:.2f}, +10)')
    elif vol_5_20 >= 1.0:
        score += 5
        reasons.append(f'量能平稳(量比{vol_5_20:.2f}, +5)')
    else:
        score += 2
        reasons.append(f'缩量(量比{vol_5_20:.2f}, +2)')

    # 4. 与上一轮相似度 (15分)
    cmp = r.get('compare_with_last')
    if cmp:
        w1_sim = (1 - cmp['w1_similarity']) * 100
        w2_sim = (1 - cmp['w2_similarity']) * 100
        avg_sim = (w1_sim + w2_sim) / 2
        if avg_sim >= 80:
            score += 15
            reasons.append(f'与上一轮主升浪高度相似(W1 {w1_sim:.0f}%/W2 {w2_sim:.0f}%, +15)')
        elif avg_sim >= 60:
            score += 10
            reasons.append(f'与上一轮相似(W1 {w1_sim:.0f}%/W2 {w2_sim:.0f}%, +10)')
        else:
            score += 5
            reasons.append(f'与上一轮相似度低(W1 {w1_sim:.0f}%/W2 {w2_sim:.0f}%, +5)')
    else:
        score += 5
        reasons.append('无历史对比(+5)')

    # 5. 距W3目标空间 (10分)
    dist_to_target = r.get('dist_to_w3_target', 0)
    if dist_to_target >= 40:
        score += 10
        reasons.append(f'距W3目标{dist_to_target:.0f}%空间充足(+10)')
    elif dist_to_target >= 20:
        score += 7
        reasons.append(f'距W3目标{dist_to_target:.0f}%空间适中(+7)')
    elif dist_to_target >= 10:
        score += 4
        reasons.append(f'距W3目标{dist_to_target:.0f}%空间有限(+4)')
    else:
        score += 1
        reasons.append(f'距W3目标{dist_to_target:.0f}%空间不足(+1)')

    # 6. 短期技术状态 (10分)
    macd_val = ind.get('macd', 0)
    rsi = ind.get('rsi14', 50)
    j = ind.get('j', 50)
    tech_flags = []
    if macd_val > 0:
        tech_flags.append('MACD金叉')
    if 40 <= rsi <= 65:
        tech_flags.append(f'RSI{rsi:.0f}中性')
    if j < 0:
        tech_flags.append(f'KDJ-J{j:.0f}超卖')
    elif j > 100:
        tech_flags.append(f'KDJ-J{j:.0f}超买')
    if macd_val > 0 and 40 <= rsi <= 65:
        score += 10
        reasons.append(f'短期技术健康({"/".join(tech_flags)}, +10)')
    elif j < 0 or (macd_val > 0 and rsi < 70):
        score += 6
        reasons.append(f'短期技术尚可({"/".join(tech_flags)}, +6)')
    elif macd_val < 0 and rsi > 70:
        score += 1
        reasons.append(f'短期超买({"/".join(tech_flags)}, +1)')
    else:
        score += 3
        reasons.append(f'短期技术一般({"/".join(tech_flags)}, +3)')

    # 7. 距历史新高 (5分)
    dist_to_high = r.get('dist_to_history_high', 100)
    if 5 <= dist_to_high <= 15:
        score += 5
        reasons.append(f'距历史新高{dist_to_high:.0f}%(强势但未透支, +5)')
    elif dist_to_high <= 5:
        score += 4
        reasons.append(f'距历史新高{dist_to_high:.0f}%(突破即新高, +4)')
    elif dist_to_high <= 30:
        score += 3
        reasons.append(f'距历史新高{dist_to_high:.0f}%(+3)')
    else:
        score += 1
        reasons.append(f'距历史新高{dist_to_high:.0f}%(远离, +1)')

    # 8. 历史回撤风险 (5分)
    max_dd = r.get('max_drawdown_1y', -20)
    if max_dd >= -15:
        score += 5
        reasons.append(f'近1年最大回撤{max_dd:.0f}%(抗跌, +5)')
    elif max_dd >= -25:
        score += 3
        reasons.append(f'近1年最大回撤{max_dd:.0f}%(+3)')
    else:
        score += 1
        reasons.append(f'近1年最大回撤{max_dd:.0f}%(波动大, +1)')

    score = min(score, 100.0)
    return score, reasons


def build_advice(r: Dict, priority_score: float) -> Dict:
    """根据优先级评分生成操作建议(介入策略+关键价位+止损止盈)。"""
    cur = r.get('current_wave')
    cp = r['current_price']
    sr = r.get('support_resistance', {'supports': [], 'resistances': []})
    ind = r.get('indicators', {})

    supports = sr.get('supports', [])
    resistances = sr.get('resistances', [])

    # 判断优先级等级
    if priority_score >= 75:
        level = '⭐⭐⭐ 高优先'
        action = '回踩支撑位加仓'
    elif priority_score >= 60:
        level = '⭐⭐ 中优先'
        action = '突破确认后介入'
    else:
        level = '⭐ 低优先'
        action = '等待深度回调'

    # 选最近的有效支撑作为介入点
    entry_levels = []
    for name, price, dev in supports[:3]:
        if 0 < dev <= 25:
            entry_levels.append((name, price, dev))
    if not entry_levels and supports:
        entry_levels = [supports[0]]

    # 选最近的压力位作为目标
    target_levels = []
    for name, price, dev in resistances[:2]:
        if dev > 0:
            target_levels.append((name, price, dev))

    # 止损位: 第2浪低点下方 或 最近支撑下方5%
    stop_loss = None
    if cur:
        stop_loss = cur.L2.price * 0.95
    elif entry_levels:
        stop_loss = entry_levels[0][1] * 0.95

    # 止盈位: W3目标 或 最近压力位
    take_profit = None
    if cur:
        take_profit = cur.w3_target_price
    elif target_levels:
        take_profit = target_levels[0][1]

    # 介入理由
    reasons = []
    if cur:
        if cur.H3 is None:
            reasons.append(f'W3刚启动,现价{cp:.2f}突破H1({cur.H1.price:.2f})确认主升浪')
        elif cur.w3_ratio < 1.618:
            reasons.append(f'W3延伸{cur.w3_ratio:.2f}x,距1.618目标{cur.w3_target_price:.2f}仍有空间')
    if ind.get('ma5', 0) > ind.get('ma20', 0) > ind.get('ma60', 0):
        reasons.append('均线多头排列趋势确认')
    if r.get('vol_ratio_5_20', 0) >= 1.1:
        reasons.append(f"量比{r['vol_ratio_5_20']:.2f}量能配合")

    # 风险提示
    risks = []
    if ind.get('macd', 0) < 0:
        risks.append('MACD死叉,短期有调整压力')
    if ind.get('rsi14', 50) > 70:
        risks.append(f"RSI{ind['rsi14']:.0f}超买")
    if ind.get('j', 50) > 100:
        risks.append(f"KDJ-J{ind['j']:.0f}超买")
    recent_5d = r.get('recent_5d_return', 0)
    if recent_5d < -10:
        risks.append(f'近5日{recent_5d:.0f}%急跌,破位风险')
    if r.get('up_vol_vs_down_vol', 1.0) < 1.0:
        risks.append('阴线量>阳线量,主力疑似出货')

    return {
        'level': level,
        'action': action,
        'entry_levels': entry_levels,
        'target_levels': target_levels,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'reasons': reasons,
        'risks': risks,
    }


def analyze_one(ts_code: str, name: str, industry: str) -> Dict:
    """深度分析单只股票。"""
    print(f"\n{'='*78}")
    print(f"  {ts_code} {name} ({industry}) 深度技术分析")
    print(f"{'='*78}")

    df = load_history(ts_code)
    if df is None or len(df) < 120:
        print(f"  数据不足(<120根),跳过")
        return {}
    print(f"  历史数据: {fmt_date(df.iloc[0]['trade_date'])} ~ {fmt_date(df.iloc[-1]['trade_date'])}  共{len(df)}根K线")
    print(f"  历史最高: {df['high'].max():.2f}  历史最低: {df['low'].min():.2f}")

    df = enrich_indicators(df)
    current_price = float(df['close'].values[-1])
    last = df.iloc[-1]

    # ===== 多窗口枢轴点 =====
    print(f"\n  [1] 多层级枢轴点分析")
    pivot_data = {}
    for w, label in zip(PIVOT_WINDOWS, ['小浪(W5)', '中浪(W10)', '大浪(W20)']):
        ps = pivot_summary(df, w, label)
        pivot_data[label] = ps
        highs = [p for p, _ in ps if p.kind == 'high']
        lows = [p for p, _ in ps if p.kind == 'low']
        print(f"    {label}: 共{len(ps)}个枢轴({len(highs)}高+{len(lows)}低)")
        # 列出大浪最近5个
        if label == '大浪(W20)':
            for p, move in ps[-6:]:
                arrow = '▲' if p.kind == 'high' else '▼'
                print(f"      {arrow} {fmt_date(p.date)}  价{p.price:>9.2f}  前段幅度{move:+.1f}%")

    # ===== 历史波浪结构演进 =====
    print(f"\n  [2] 历史波浪结构演进(大浪级)")
    big_pivots = [p for p, _ in pivot_data['大浪(W20)']]
    all_waves = find_all_wave_structures(big_pivots, df, w1_min=0.30)
    print(f"    识别出 {len(all_waves)} 轮波浪结构(W1≥30%):")
    for i, w in enumerate(all_waves):
        status = []
        status.append(f"L0={fmt_date(w.L0.date)}({w.L0.price:.2f})")
        status.append(f"H1={fmt_date(w.H1.date)}({w.H1.price:.2f})")
        status.append(f"W1=+{w.w1_gain*100:.0f}%")
        status.append(f"L2={fmt_date(w.L2.date)}({w.L2.price:.2f})")
        status.append(f"W2=-{w.w2_retrace*100:.0f}%")
        if w.H3:
            status.append(f"H3={fmt_date(w.H3.date)}({w.H3.price:.2f})")
            status.append(f"W3={w.w3_ratio:.2f}x")
        if w.L4:
            status.append(f"L4={fmt_date(w.L4.date)}({w.L4.price:.2f})")
        if w.H5:
            status.append(f"H5={fmt_date(w.H5.date)}({w.H5.price:.2f})")
        flag = '✓' if w.is_valid else '✗'
        print(f"      [{i+1}] {flag} " + ' | '.join(status))
        if w.violation:
            print(f"          ⚠ {w.violation}")

    # ===== 当前波浪定位 =====
    print(f"\n  [3] 当前波浪定位(最新)")
    # 用小浪级定位当前所处阶段
    small_pivots = [p for p, _ in pivot_data['小浪(W5)']]
    current_waves = find_all_wave_structures(small_pivots, df, w1_min=0.40)
    if current_waves:
        cur = current_waves[-1]
        print(f"    最新波浪结构:")
        print(f"      第1浪: {fmt_date(cur.L0.date)}({cur.L0.price:.2f}) → {fmt_date(cur.H1.date)}({cur.H1.price:.2f})  涨{cur.w1_gain*100:.1f}%")
        print(f"      第2浪: → {fmt_date(cur.L2.date)}({cur.L2.price:.2f})  回调{cur.w2_retrace*100:.1f}%")
        print(f"      第3浪1.618目标: {cur.w3_target_price:.2f}  距今{cur.w3_target_price/current_price*100-100:+.1f}%")
        if cur.H3:
            print(f"      第3浪已到H3={cur.H3.price:.2f}(达成{cur.w3_ratio:.2f}x)")
        else:
            print(f"      第3浪: 进行中,当前{current_price:.2f}")
        if cur.L4:
            print(f"      第4浪: {fmt_date(cur.L4.date)}({cur.L4.price:.2f})")
        if cur.H5:
            print(f"      第5浪: {fmt_date(cur.H5.date)}({cur.H5.price:.2f})")

        # 与上一轮比较
        cmp = compare_with_last_wave(current_waves, cur)
        if cmp:
            print(f"\n      📊 与上一轮完整主升浪对比:")
            print(f"        上一轮: {fmt_date(cmp['last_L0_date'])}起 W1+{cmp['last_w1_gain']*100:.0f}% W2-{cmp['last_w2_retrace']*100:.0f}% W3={cmp['last_w3_ratio']:.2f}x")
            print(f"        本轮  : W1+{cur.w1_gain*100:.0f}% W2-{cur.w2_retrace*100:.0f}%")
            print(f"        W1相似度: {(1-cmp['w1_similarity'])*100:.0f}%  W2相似度: {(1-cmp['w2_similarity'])*100:.0f}%")
    else:
        print(f"    (未识别出当前满足条件的小浪级结构)")

    # ===== 技术指标全景 =====
    print(f"\n  [4] 技术指标全景")
    print(f"    现价: {current_price:.2f}  日期: {fmt_date(last['trade_date'])}")
    print(f"    均线位置:")
    for ma_name in ['ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma250']:
        v = last.get(ma_name, np.nan)
        if not np.isnan(v) and v > 0:
            pos = '上方' if current_price > v else '下方'
            dev = (current_price - v) / v * 100
            print(f"      {ma_name.upper():6s} {v:>9.2f}  价在{pos}(偏离{dev:+.1f}%)")
    print(f"    多头排列: ", end='')
    ma_seq = [last.get(n, np.nan) for n in ['ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma250']]
    if all(not np.isnan(v) for v in ma_seq):
        bull = all(ma_seq[i] >= ma_seq[i+1] for i in range(len(ma_seq)-1))
        print('✓ 完美多头(MA5>MA10>MA20>MA60>MA120>MA250)' if bull else '✗ 非完美多头')
    print(f"    MACD: DIF={last['dif']:.2f}  DEA={last['dea']:.2f}  MACD={last['macd']:.2f}"
          f"  {'金叉/红柱' if last['dif'] > last['dea'] else '死叉/绿柱'}")
    print(f"    RSI(14): {last['rsi14']:.1f}  {'超买>70' if last['rsi14'] > 70 else ('超卖<30' if last['rsi14'] < 30 else '中性')}")
    print(f"    KDJ:    K={last['k']:.1f}  D={last['d']:.1f}  J={last['j']:.1f}"
          f"  {'超买' if last['j'] > 100 else ('超卖' if last['j'] < 0 else '中性')}")
    print(f"    BOLL:   上轨{last['boll_up']:.2f}  中轨{last['boll_mid']:.2f}  下轨{last['boll_dn']:.2f}"
          f"  价位{'突破上轨' if current_price > last['boll_up'] else ('触下轨' if current_price < last['boll_dn'] else '中轨间')}")

    # 量能趋势
    vol = df['vol'].values
    vol_ma5 = last['vol_ma5']
    vol_ma20 = last['vol_ma20']
    vol_ma60 = last['vol_ma60']
    print(f"    量能:   5日均量{vol_ma5:.0f}  20日均量{vol_ma20:.0f}  60日均量{vol_ma60:.0f}"
          f"  量比5/20={vol_ma5/vol_ma20:.2f}")
    # 近60日量价关系
    recent = df.tail(60)
    up_days = (recent['close'] > recent['open']).sum()
    down_days = 60 - up_days
    up_vol = recent[recent['close'] > recent['open']]['vol'].mean()
    down_vol = recent[recent['close'] < recent['open']]['vol'].mean() if down_days > 0 else 0
    print(f"    近60日: {up_days}阳/{down_days}阴  阳线均量{up_vol:.0f}  阴线均量{down_vol:.0f}"
          f"  {'放量上涨(主力收集)' if up_vol > down_vol * 1.1 else '缩量上涨(需警惕)'}")

    # ===== 支撑压力位 =====
    print(f"\n  [5] 关键支撑/压力位")
    sr = find_nearest_support_resistance(df, small_pivots, current_price)
    print(f"    📉 支撑位(由近到远):")
    for sr_name, price, dev in sr['supports'][:4]:
        print(f"       {sr_name:<14s} {price:>9.2f}  距今{dev:+.1f}%")
    print(f"    📈 压力位(由近到远):")
    for sr_name, price, dev in sr['resistances'][:4]:
        print(f"       {sr_name:<14s} {price:>9.2f}  距今{dev:+.1f}%")

    # ===== 长周期回报统计 =====
    print(f"\n  [6] 长周期回报统计")
    for n in [5, 20, 60, 120, 250]:
        if len(df) > n:
            ret = (current_price / df['close'].iloc[-n-1] - 1) * 100
            print(f"    近{n:>3d}日: {ret:+.1f}%", end='  ')
        if n in [5, 60, 250]:
            print()
    print()

    # 最大回撤与最大涨幅
    max_drawdown_1y = 0.0
    history_high = float(df['high'].max())
    if len(df) > 60:
        roll_max = df['close'].rolling(252, min_periods=60).max()
        drawdown = (df['close'] / roll_max - 1) * 100
        max_drawdown_1y = float(drawdown.tail(252).min())
        print(f"    近1年最大回撤: {max_drawdown_1y:.1f}%")
        print(f"    近1年最大涨幅: {(df['close'].tail(252).max()/df['close'].tail(252).min()-1)*100:.1f}%")

    # 计算优先级评分所需的补充字段
    vol_ratio_5_20 = float(last['vol_ma5'] / last['vol_ma20']) if last['vol_ma20'] > 0 else 0.0
    recent_60 = df.tail(60)
    up_days_df = recent_60[recent_60['close'] > recent_60['open']]
    down_days_df = recent_60[recent_60['close'] < recent_60['open']]
    up_vol = float(up_days_df['vol'].mean()) if len(up_days_df) > 0 else 0.0
    down_vol = float(down_days_df['vol'].mean()) if len(down_days_df) > 0 else 0.0
    up_vol_vs_down_vol = up_vol / down_vol if down_vol > 0 else 1.0
    recent_5d_return = float((current_price / df['close'].iloc[-6] - 1) * 100) if len(df) > 6 else 0.0
    dist_to_w3_target = float((cur.w3_target_price - current_price) / current_price * 100) if cur else 0.0
    dist_to_history_high = float((history_high - current_price) / current_price * 100)
    cmp_data = compare_with_last_wave(current_waves, cur) if cur and current_waves else None

    result = {
        'ts_code': ts_code, 'name': name, 'industry': industry,
        'current_price': current_price,
        'history_start': fmt_date(df.iloc[0]['trade_date']),
        'history_end': fmt_date(df.iloc[-1]['trade_date']),
        'history_bars': len(df),
        'history_high': history_high,
        'all_waves': all_waves,
        'current_wave': current_waves[-1] if current_waves else None,
        'support_resistance': sr,
        'indicators': {
            'ma5': float(last['ma5']), 'ma10': float(last['ma10']), 'ma20': float(last['ma20']),
            'ma60': float(last['ma60']), 'ma120': float(last['ma120']), 'ma250': float(last['ma250']),
            'dif': float(last['dif']), 'dea': float(last['dea']), 'macd': float(last['macd']),
            'rsi14': float(last['rsi14']),
            'k': float(last['k']), 'd': float(last['d']), 'j': float(last['j']),
            'boll_up': float(last['boll_up']), 'boll_mid': float(last['boll_mid']), 'boll_dn': float(last['boll_dn']),
        },
        'vol_ratio_5_20': vol_ratio_5_20,
        'up_vol_vs_down_vol': up_vol_vs_down_vol,
        'recent_5d_return': recent_5d_return,
        'dist_to_w3_target': dist_to_w3_target,
        'dist_to_history_high': dist_to_history_high,
        'max_drawdown_1y': max_drawdown_1y,
        'compare_with_last': cmp_data,
    }

    # ===== 操作优先级与建议 =====
    print(f"\n  [7] 操作优先级与建议")
    priority_score, priority_reasons = compute_priority(result)
    advice = build_advice(result, priority_score)
    result['priority_score'] = priority_score
    result['advice'] = advice

    print(f"    🎯 优先级评分: {priority_score:.1f}/100  {advice['level']}")
    print(f"    📋 操作策略: {advice['action']}")
    print(f"\n    评分明细:")
    for reason in priority_reasons:
        print(f"      • {reason}")
    print(f"\n    ✅ 介入理由:")
    for reason in advice['reasons']:
        print(f"      • {reason}")
    if advice['entry_levels']:
        print(f"\n    📉 介入价位(支撑位):")
        for name, price, dev in advice['entry_levels']:
            print(f"       {name:<14s} {price:>9.2f}  距今{dev:+.1f}%")
    if advice['target_levels']:
        print(f"\n    📈 目标价位(压力位):")
        for name, price, dev in advice['target_levels']:
            print(f"       {name:<14s} {price:>9.2f}  距今{dev:+.1f}%")
    if advice['stop_loss']:
        sl_pct = (advice['stop_loss'] - current_price) / current_price * 100
        print(f"\n    🛑 止损位: {advice['stop_loss']:.2f} ({sl_pct:+.1f}%)")
    if advice['take_profit']:
        tp_pct = (advice['take_profit'] - current_price) / current_price * 100
        print(f"    🎯 止盈位: {advice['take_profit']:.2f} ({tp_pct:+.1f}%)")
    if advice['risks']:
        print(f"\n    ⚠ 风险提示:")
        for risk in advice['risks']:
            print(f"      • {risk}")

    return result


def main():
    import pandas as _pd
    wave3_csv = r'd:\mystock\solo\etf_resonance\output\wave3_signals.csv'
    targets = list(TARGETS)
    if os.path.exists(wave3_csv):
        df_sig = _pd.read_csv(wave3_csv, dtype={'code': str})
        if len(df_sig) > 0:
            targets = [(str(r['code']), str(r.get('name', '')), str(r.get('industry', '')))
                       for _, r in df_sig.iterrows()]
    print("=" * 78)
    print(f"  Wave 3 深度技术分析 - 长周期形态浪解析")
    print(f"  目标: {len(targets)} 只W3信号股")
    print("=" * 78)

    results = []
    for code, name, ind in targets:
        r = analyze_one(code, name, ind)
        if r:
            results.append(r)

    # 保存JSON供后续可视化
    out_dir = r'd:\mystock\solo\etf_resonance\output'
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'wave3_deep_analysis.json')

    # 序列化(把dataclass转dict)
    def wave_to_dict(w: WaveCount) -> Dict:
        if w is None:
            return None
        d = {
            'L0': {'date': w.L0.date, 'price': w.L0.price},
            'H1': {'date': w.H1.date, 'price': w.H1.price},
            'L2': {'date': w.L2.date, 'price': w.L2.price},
            'w1_gain': w.w1_gain, 'w2_retrace': w.w2_retrace,
            'w3_ratio': w.w3_ratio, 'w3_target_price': w.w3_target_price,
            'is_valid': w.is_valid, 'violation': w.violation,
        }
        if w.H3:
            d['H3'] = {'date': w.H3.date, 'price': w.H3.price}
        if w.L4:
            d['L4'] = {'date': w.L4.date, 'price': w.L4.price}
        if w.H5:
            d['H5'] = {'date': w.H5.date, 'price': w.H5.price}
        return d

    out = []
    for r in results:
        d = dict(r)
        d['all_waves'] = [wave_to_dict(w) for w in r['all_waves']]
        d['current_wave'] = wave_to_dict(r['current_wave'])
        d['support_resistance'] = {
            'supports': [(n, p, dev) for n, p, dev in r['support_resistance']['supports']],
            'resistances': [(n, p, dev) for n, p, dev in r['support_resistance']['resistances']],
        }
        # advice里的entry_levels/target_levels是tuple list,转成list of dict便于JSON
        if 'advice' in r:
            adv = r['advice']
            d['advice'] = {
                'level': adv['level'],
                'action': adv['action'],
                'entry_levels': [{'name': n, 'price': p, 'deviation_pct': dev} for n, p, dev in adv['entry_levels']],
                'target_levels': [{'name': n, 'price': p, 'deviation_pct': dev} for n, p, dev in adv['target_levels']],
                'stop_loss': adv['stop_loss'],
                'take_profit': adv['take_profit'],
                'reasons': adv['reasons'],
                'risks': adv['risks'],
            }
        out.append(d)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n{'='*78}")
    print(f"  深度分析结果已保存: {out_path}")
    print(f"{'='*78}")

    # ===== 优先级汇总排名 =====
    print(f"\n{'='*78}")
    print(f"  📊 操作优先级汇总排名")
    print(f"{'='*78}")
    ranked = sorted(results, key=lambda x: -x.get('priority_score', 0))
    name_map = {c: nm for c, nm, _ in TARGETS}
    print(f"  {'排名':<6}{'代码':<14}{'名称':<12}{'优先级分':<10}{'等级':<14}{'操作策略':<20}")
    print(f"  {'-'*76}")
    for i, r in enumerate(ranked):
        adv = r.get('advice', {})
        nm = name_map.get(r['ts_code']) or r.get('name') or '?'
        print(f"  {i+1:<6}{r['ts_code']:<14}{nm:<12}"
              f"{r.get('priority_score', 0):<10.1f}{adv.get('level', '-'):<14}{adv.get('action', '-'):<20}")

    print(f"\n  💡 综合建议:")
    for i, r in enumerate(ranked):
        adv = r.get('advice', {})
        nm = name_map.get(r['ts_code']) or r.get('name') or '?'
        print(f"\n  [{i+1}] {nm} ({r['ts_code']}) - {adv.get('level', '')}")
        print(f"      策略: {adv.get('action', '')}")
        if adv.get('entry_levels'):
            print(f"      介入: " + " / ".join(f"{n}@{p:.2f}" for n, p, _ in adv['entry_levels'][:2]))
        if adv.get('stop_loss'):
            sl_pct = (adv['stop_loss'] - r['current_price']) / r['current_price'] * 100
            print(f"      止损: {adv['stop_loss']:.2f} ({sl_pct:+.1f}%)")
        if adv.get('take_profit'):
            tp_pct = (adv['take_profit'] - r['current_price']) / r['current_price'] * 100
            print(f"      止盈: {adv['take_profit']:.2f} ({tp_pct:+.1f}%)")
        if adv.get('risks'):
            print(f"      风险: {'; '.join(adv['risks'])}")
    print(f"\n{'='*78}")


if __name__ == '__main__':
    main()
