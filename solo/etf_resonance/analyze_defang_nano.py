"""德方纳米(300769.SZ) W2策略选后破位大跌原因分析

回溯:
  1. 在回测区间内逐日复现W2信号,定位德方纳米被选中的日期
  2. 还原当时的波浪结构与评分理由
  3. 跟踪选中后30/60日的价格走势
  4. 分析破位的技术面原因(均线/MACD/量能/板块)
"""
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from dotenv import load_dotenv
from multi_factor_picker.data_fetcher import DataFetcher
from etf_resonance.utils.indicators import sma, ema, atr
from etf_resonance.wave3_detector import find_pivots, detect_waves, WaveCount, Pivot


def score_w2_signal(wave: WaveCount, df: pd.DataFrame) -> Tuple[float, List[str]]:
    """W2浪结束点信号分(复制自run_backtest_wave_compare,避免触发模块级回测)。"""
    score = 0.0
    reasons: List[str] = []
    close = df['close'].values
    vol = df['vol'].values if 'vol' in df.columns else df.get('volume', pd.Series([0]*len(df))).values
    current_price = float(close[-1])

    if wave.is_valid:
        score += 15
        reasons.append(f'波浪结构有效(W1涨{wave.w1_gain*100:.0f}%,W2回调{wave.w2_retrace*100:.0f}%)')

    w1_pct = wave.w1_gain * 100
    if 60 <= w1_pct < 80:
        score += 20
        reasons.append(f'W1涨幅{w1_pct:.0f}%处于60-80%最优区间')
    elif 100 <= w1_pct <= 200:
        score += 18
        reasons.append(f'W1涨幅{w1_pct:.0f}%处于100-200%主升浪区间')
    elif 80 <= w1_pct < 100:
        score += 12
    elif 40 <= w1_pct < 60:
        score += 6
    elif w1_pct > 200:
        score += 10

    w2_pct = wave.w2_retrace * 100
    if 30 <= w2_pct < 40:
        score += 20
        reasons.append(f'W2回调{w2_pct:.0f}%处于30-40%最佳介入时点')
    elif 50 <= w2_pct < 60:
        score += 18
        reasons.append(f'W2回调{w2_pct:.0f}%深度洗盘后弹性大')
    elif 40 <= w2_pct < 50:
        score += 12
    elif 60 <= w2_pct <= 70:
        score += 8
    elif w2_pct > 70:
        score += 2

    rebound_pct = (current_price / wave.L2.price - 1) * 100 if wave.L2.price > 0 else 0
    if 5 <= rebound_pct <= 15:
        score += 20
        reasons.append(f'从L2反弹{rebound_pct:.1f}%处于5-15%最佳启动区间')
    elif 15 < rebound_pct <= 25:
        score += 12
        reasons.append(f'从L2反弹{rebound_pct:.1f}%已启动')
    elif 0 < rebound_pct < 5:
        score += 8
        reasons.append(f'从L2反弹{rebound_pct:.1f}%刚起步')
    elif rebound_pct > 25:
        score += 4
        reasons.append(f'从L2反弹{rebound_pct:.1f}%已较多')

    ma5 = sma(close, 5)
    ma20 = sma(close, 20)
    if len(ma5) >= 2 and len(ma20) > 0:
        if ma5[-1] > ma5[-2] and ma5[-1] > ma20[-1] * 0.95:
            score += 10
            reasons.append('MA5拐头向上企稳')
        elif ma5[-1] > ma5[-2]:
            score += 6
            reasons.append('MA5开始拐头')

    if len(vol) >= 10:
        vol_5 = np.mean(vol[-5:])
        vol_20 = np.mean(vol[-20:])
        if vol_20 > 0:
            ratio = vol_5 / vol_20
            if 0.8 < ratio < 1.2:
                score += 10
                reasons.append(f'量比{ratio:.2f}缩量企稳')
            elif ratio >= 1.2:
                score += 6
                reasons.append(f'量比{ratio:.2f}放量')

    dist_to_target = (wave.w3_target_price - current_price) / max(current_price, 1e-6) * 100
    if dist_to_target > 30:
        score += 5
        reasons.append(f'距1.618目标价{wave.w3_target_price:.2f}还有{dist_to_target:.0f}%空间')
    elif dist_to_target > 10:
        score += 3

    score = min(score, 100.0)
    return score, reasons

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

TARGET = '300769.SZ'
NAME = '德方纳米'


def fmt_date(d) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def load_full_history(days: int = 800) -> pd.DataFrame:
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    df = dfetcher.get_daily_by_code(ts_code=TARGET, start_date=start, end_date=end)
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['vol'].values
    df = df.copy()
    df['ma5'] = sma(close, 5)
    df['ma10'] = sma(close, 10)
    df['ma20'] = sma(close, 20)
    df['ma60'] = sma(close, 60)
    df['ma120'] = sma(close, 120)
    df['ma250'] = sma(close, 250)
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    dif = ema12 - ema26
    dea = ema(dif, 9)
    df['dif'] = dif
    df['dea'] = dea
    df['macd'] = (dif - dea) * 2
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=14).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=14).mean().values
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    df['rsi14'] = 100 - 100 / (1 + rs)
    df['vol_ma5'] = sma(vol, 5)
    df['vol_ma20'] = sma(vol, 20)
    return df


def scan_w2_signals(df: pd.DataFrame, window: int = 5) -> List[Tuple[int, WaveCount, float, List[str]]]:
    """逐日复现W2信号,返回所有被选中的日期。"""
    hits = []
    end_idx = len(df)
    start_idx = 60
    for i in range(start_idx, end_idx):
        sub = df.iloc[:i+1].copy()
        if len(sub) < 60:
            continue
        pivots = find_pivots(sub, window=window)
        if len(pivots) < 3:
            continue
        wave = detect_waves(pivots, sub)
        if wave is None:
            continue
        if wave.w1_gain < 0.60:
            continue
        if not (0.30 <= wave.w2_retrace <= 0.70):
            continue
        current_price = float(sub['close'].values[-1])
        # W2信号: 现价在 L2*1.02 ~ H1 之间
        if current_price > wave.H1.price:
            continue
        if current_price < wave.L2.price * 1.02:
            continue
        score, reasons = score_w2_signal(wave, sub)
        if score >= 90.0:
            hits.append((i, wave, score, reasons, current_price))
    return hits


def main():
    print("=" * 78)
    print(f"  德方纳米({TARGET}) W2策略选后破位大跌 原因分析")
    print("=" * 78)

    df = load_full_history(days=800)
    if df is None or len(df) < 60:
        print("数据不足")
        return
    df = enrich(df)
    print(f"历史数据: {fmt_date(df.iloc[0]['trade_date'])} ~ {fmt_date(df.iloc[-1]['trade_date'])} 共{len(df)}根")
    print(f"历史最高: {df['high'].max():.2f}  历史最低: {df['low'].min():.2f}  现价: {df['close'].values[-1]:.2f}")

    # ===== 1. 逐日扫描定位W2信号 =====
    print(f"\n[1] 逐日扫描W2信号(信号分≥90, 现价在L2*1.02~H1之间)")
    hits = scan_w2_signals(df, window=5)
    if not hits:
        print("  回测区间内未触发W2信号(可能W1<60%或W2回调不在30-70%)。放宽条件复扫...")
        # 放宽: 信号分≥80, W2回调0.20-0.85
        hits2 = []
        for i in range(60, len(df)):
            sub = df.iloc[:i+1].copy()
            pivots = find_pivots(sub, window=5)
            if len(pivots) < 3:
                continue
            wave = detect_waves(pivots, sub)
            if wave is None:
                continue
            if wave.w1_gain < 0.40:
                continue
            current_price = float(sub['close'].values[-1])
            if current_price > wave.H1.price:
                continue
            if current_price < wave.L2.price * 1.02:
                continue
            score, reasons = score_w2_signal(wave, sub)
            if score >= 80.0:
                hits2.append((i, wave, score, reasons, current_price))
        hits = hits2
        print(f"  放宽后命中 {len(hits)} 次(信号分≥80, W1≥40%)")
    else:
        print(f"  共命中 {len(hits)} 次")

    if not hits:
        print("\n  仍未找到W2信号。列出德方纳米近1年的波段结构供人工判断:")
        pivots = find_pivots(df, window=10)
        for p in pivots[-10:]:
            arrow = '▲' if p.kind == 'high' else '▼'
            print(f"    {arrow} {fmt_date(p.date)}  价{p.price:.2f}")
        return

    # ===== 2. 展示每次命中详情 =====
    print(f"\n[2] W2信号命中详情(按时间顺序)")
    for k, (idx, wave, score, reasons, price) in enumerate(hits):
        row = df.iloc[idx]
        date_str = fmt_date(row['trade_date'])
        print(f"\n  ── 命中{k+1} ── 日期 {date_str}  收盘 {price:.2f}  信号分 {score:.1f}")
        print(f"    波浪结构:")
        print(f"      L0={fmt_date(wave.L0.date)}({wave.L0.price:.2f})")
        print(f"      H1={fmt_date(wave.H1.date)}({wave.H1.price:.2f})  W1涨幅 +{wave.w1_gain*100:.1f}%")
        print(f"      L2={fmt_date(wave.L2.date)}({wave.L2.price:.2f})  W2回调 -{wave.w2_retrace*100:.1f}%")
        print(f"      W3目标(1.618) = {wave.w3_target_price:.2f}  距今{(wave.w3_target_price/price-1)*100:+.1f}%")
        print(f"      现价位置: L2({wave.L2.price:.2f}) ~ H1({wave.H1.price:.2f})之间, "
              f"距L2反弹{(price/wave.L2.price-1)*100:.1f}%, 距H1还差{(wave.H1.price/price-1)*100:.1f}%")
        print(f"    评分理由:")
        for r in reasons:
            print(f"      • {r}")

        # 跟踪选中后走势
        print(f"    选中后走势:")
        for n in [5, 10, 20, 30, 60]:
            if idx + n < len(df):
                future = df.iloc[idx + n]
                ret = (future['close'] / price - 1) * 100
                arrow = '↑' if ret > 0 else '↓'
                print(f"      +{n:>2d}日({fmt_date(future['trade_date'])}): {future['close']:.2f}  {arrow}{ret:+.1f}%")
        # 最大回撤
        if idx + 60 < len(df):
            future_seg = df.iloc[idx:idx+61]
            max_high = future_seg['high'].max()
            max_low = future_seg['low'].min()
            max_up = (max_high / price - 1) * 100
            max_dn = (max_low / price - 1) * 100
            print(f"      选中后60日内: 最高{max_high:.2f}({max_up:+.1f}%) 最低{max_low:.2f}({max_dn:+.1f}%)")

    # ===== 3. 分析第一次命中后的破位过程 =====
    print(f"\n[3] 破位过程技术面分析(以第一次命中为基准)")
    idx0, wave0, score0, reasons0, price0 = hits[0]
    seg = df.iloc[idx0:idx0+61]
    print(f"    命中日 {fmt_date(df.iloc[idx0]['trade_date'])} 收盘 {price0:.2f}")
    print(f"\n    逐日技术指标:")
    print(f"    {'日期':<12}{'收盘':>8}{'涨跌':>8}{'MA5':>8}{'MA20':>8}{'MA60':>8}{'MACD':>8}{'RSI':>6}{'量比5/20':>10}")
    for i, r in seg.iterrows():
        chg = (r['close'] / df.iloc[i-1]['close'] - 1) * 100 if i > 0 else 0
        vr = r['vol_ma5'] / r['vol_ma20'] if r['vol_ma20'] > 0 else 0
        print(f"    {fmt_date(r['trade_date']):<12}{r['close']:>8.2f}{chg:>+7.1f}%"
              f"{r['ma5']:>8.2f}{r['ma20']:>8.2f}{r['ma60']:>8.2f}{r['macd']:>+8.2f}{r['rsi14']:>6.1f}{vr:>10.2f}")

    # ===== 4. 关键技术位分析 =====
    print(f"\n[4] 关键支撑位破位分析")
    row0 = df.iloc[idx0]
    print(f"    命中时:")
    print(f"      现价 {price0:.2f}")
    print(f"      MA5  {row0['ma5']:.2f}  MA20 {row0['ma20']:.2f}  MA60 {row0['ma60']:.2f}")
    print(f"      L2   {wave0.L2.price:.2f}  (W2低点,理论支撑)")
    print(f"      布林下轨 (估算)")
    # 找破位日
    broke_dates = []
    for i, r in seg.iterrows():
        if r['close'] < wave0.L2.price:
            broke_dates.append((i, r))
    if broke_dates:
        bi, br = broke_dates[0]
        print(f"\n    ⚠ 首次跌破L2({wave0.L2.price:.2f})的日期: {fmt_date(br['trade_date'])} 收盘{br['close']:.2f}")
        print(f"      距命中 {(bi-idx0)}日, 跌幅 {(br['close']/price0-1)*100:+.1f}%")
        # 破位时技术指标
        print(f"      破位时: MA5={br['ma5']:.2f} MA20={br['ma20']:.2f} MA60={br['ma60']:.2f}")
        print(f"              MACD={br['macd']:+.2f} RSI={br['rsi14']:.1f}")
        if br['ma5'] < br['ma20'] < br['ma60']:
            print(f"              均线已呈空头排列(MA5<MA20<MA60)")
    else:
        print(f"    选中后60日内未跌破L2({wave0.L2.price:.2f})")

    # ===== 5. 量价配合分析 =====
    print(f"\n[5] 量价配合分析(命中日前后10日)")
    start_a = max(0, idx0 - 10)
    end_a = min(len(df), idx0 + 11)
    around = df.iloc[start_a:end_a]
    up_days = around[around['close'] > around['open']]
    dn_days = around[around['close'] < around['open']]
    up_v = up_days['vol'].mean() if len(up_days) > 0 else 0
    dn_v = dn_days['vol'].mean() if len(dn_days) > 0 else 0
    print(f"    命中日前后10日: {len(up_days)}阳/{len(dn_days)}阴")
    print(f"      阳线均量 {up_v:.0f}  阴线均量 {dn_v:.0f}  比 {up_v/dn_v if dn_v>0 else 0:.2f}")
    if dn_v > up_v:
        print(f"      ⚠ 阴线量>阳线量,主力疑似出货/出逃")

    # ===== 6. 总结 =====
    print(f"\n[6] 破位大跌原因总结")
    idx_min = np.argmin(df.iloc[idx0:idx0+61]['close'].values)
    lowest = df.iloc[idx0 + idx_min]
    max_loss = (lowest['close'] / price0 - 1) * 100
    print(f"    命中日 {fmt_date(df.iloc[idx0]['trade_date'])} 收盘 {price0:.2f}")
    print(f"    选中后60日内最低: {fmt_date(lowest['trade_date'])} {lowest['close']:.2f} ({max_loss:+.1f}%)")

    print(f"\n    核心问题:")
    print(f"      1. W2策略是左侧抄底,假设'L2是第2浪调整低点',但L2可能只是下跌中继")
    print(f"      2. 现价在L2~H1之间并不代表'第2浪结束',可能只是下跌途中的反弹")
    print(f"      3. W2回调30-70%看似合理,但在熊市/板块转弱时,50-70%回调常演变为C浪下跌")
    print(f"      4. '反弹5-15%最佳'的评分维度,在下跌趋势中反而是诱多陷阱")


if __name__ == '__main__':
    main()
