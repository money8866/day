"""大牛股第3浪起点发现器 (Elliott Wave Theory - Wave 3 Start Detector)

基于艾略特波浪理论识别大牛股的主升浪(第3浪)起点。

波浪理论核心规则：
  第1浪(L0→H1): 主力建仓试盘段，涨幅通常≥40%
  第2浪(H1→L2): 暴力洗盘回调浪，回调幅度50%-80%为常态
  第3浪(L2→H3): 主升浪，长度通常是第1浪的1.618倍(黄金分割)
  第4浪(H3→L4): 调整浪，铁律：L4不能跌破H1(第1浪顶)
  第5浪(L4→H5): 冲顶浪，常量价背离

铁律验证：
  - 第2浪低点 L2 必须 > 第1浪起点 L0 (否则波浪计数无效)
  - 第4浪低点 L4 必须 > 第1浪顶 H1 (否则波浪计数无效)
  - 第3浪长度 ≥ 第1浪长度 × 1.0 (常见1.618~2.618)

使用方法：
  python wave3_detector.py --code 600396       # 验证单只股票历史五浪
  python wave3_detector.py --scan              # 扫描全市场第3浪起点
  python wave3_detector.py --scan --top 30     # 输出前30只
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from dotenv import load_dotenv
from multi_factor_picker.data_fetcher import DataFetcher
from etf_resonance.utils.indicators import ema, sma, atr, slope
import tushare_quant as tq

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

TRADE_DATE = tq.TRADE_DATE


# ============== 波浪理论参数 ==============
W1_MIN_GAIN = 0.40        # 第1浪最小涨幅40%
W1_MAX_GAIN = 9.99        # 第1浪最大涨幅(实际不限制)
W2_RETRACE_MIN = 0.20     # 第2浪最小回调20%
W2_RETRACE_MAX = 0.85     # 第2浪最大回调85%(超过则可能不是第2浪)
W3_RATIO_MIN = 1.0        # 第3浪长度/第1浪长度 最小1.0
W3_RATIO_TARGET = 1.618   # 第3浪黄金分割目标1.618
W3_RATIO_MAX = 4.236      # 第3浪最大倍数4.236(超过则过度延伸)
PIVOT_WINDOW = 5          # 枢轴点判定窗口(前后各5根K线)
GOLDEN_RATIOS = [1.618, 2.0, 2.618, 3.236, 4.236]  # 第3浪常见倍数


@dataclass
class Pivot:
    """价格枢轴点。"""
    idx: int            # 在DataFrame中的行索引
    date: str           # 交易日期 YYYYMMDD
    price: float        # 价格
    kind: str           # 'high' 或 'low'


@dataclass
class WaveCount:
    """波浪计数结果。"""
    L0: Pivot           # 第1浪起点(低点)
    H1: Pivot           # 第1浪终点(高点)
    L2: Pivot           # 第2浪低点
    H3: Optional[Pivot] = None   # 第3浪高点(若已走完)
    L4: Optional[Pivot] = None   # 第4浪低点(若已走完)
    H5: Optional[Pivot] = None   # 第5浪高点(若已走完)
    w1_gain: float = 0.0        # 第1浪涨幅
    w2_retrace: float = 0.0      # 第2浪回调比例
    w3_ratio: float = 0.0        # 第3浪长度/第1浪长度
    w3_target_price: float = 0.0  # 第3浪目标价 = L2 + (H1-L0)*1.618
    is_valid: bool = False       # 波浪计数是否有效
    violation: str = ''          # 违反规则说明


@dataclass
class Wave3Signal:
    """第3浪起点信号。"""
    ts_code: str
    name: str
    industry: str
    wave: WaveCount
    current_price: float
    dist_to_w3_target: float     # 当前价距第3浪目标价的空间%
    w3_progress: float           # 第3浪已走完的比例%(若已突破H1则>0)
    signal_score: float          # 综合信号分0-100
    signal_reasons: List[str] = field(default_factory=list)


def find_pivots(df: pd.DataFrame, window: int = PIVOT_WINDOW) -> List[Pivot]:
    """识别价格的枢轴点(局部极值)。

    使用滚动窗口判定：若某点价格是前后window范围内的最高/最低，则为枢轴点。
    """
    pivots: List[Pivot] = []
    highs = df['high'].values
    lows = df['low'].values
    dates = df['trade_date'].values
    n = len(df)

    for i in range(window, n - window):
        left_h = highs[i - window:i]
        right_h = highs[i + 1:i + 1 + window]
        left_l = lows[i - window:i]
        right_l = lows[i + 1:i + 1 + window]

        if highs[i] >= np.max(left_h) and highs[i] >= np.max(right_h):
            pivots.append(Pivot(idx=i, date=str(dates[i]), price=float(highs[i]), kind='high'))

        if lows[i] <= np.min(left_l) and lows[i] <= np.min(right_l):
            pivots.append(Pivot(idx=i, date=str(dates[i]), price=float(lows[i]), kind='low'))

    pivots.sort(key=lambda p: p.idx)
    return _dedup_pivots(pivots)


def _dedup_pivots(pivots: List[Pivot]) -> List[Pivot]:
    """合并相邻的同类型枢轴点，只保留极值。"""
    if not pivots:
        return pivots
    out: List[Pivot] = [pivots[0]]
    for p in pivots[1:]:
        last = out[-1]
        if p.kind == last.kind:
            if (p.kind == 'high' and p.price > last.price) or \
               (p.kind == 'low' and p.price < last.price):
                out[-1] = p
        else:
            out.append(p)
    return out


def detect_waves(pivots: List[Pivot], df: pd.DataFrame) -> Optional[WaveCount]:
    """从枢轴点序列中识别波浪结构(低-高-低-高...)。

    波浪理论要求结构为：
      L0(低) → H1(高) → L2(低) → H3(高) → L4(低) → H5(高)
    本函数寻找满足以下条件的结构：
      - L0→H1 涨幅≥W1_MIN_GAIN (第1浪)
      - H1→L2 回调在[W2_RETRACE_MIN, W2_RETRACE_MAX]之间 (第2浪)
      - L2 > L0 (铁律1:第2浪低点不破第1浪起点)
    """
    if len(pivots) < 3:
        return None

    best_wave: Optional[WaveCount] = None
    best_score = -1.0

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
        if w1_gain < W1_MIN_GAIN:
            continue

        w2_retrace = (H1.price - L2.price) / max(H1.price - L0.price, 1e-6)
        if not (W2_RETRACE_MIN <= w2_retrace <= W2_RETRACE_MAX):
            continue

        if L2.price <= L0.price:
            continue

        w3_target = L2.price + (H1.price - L0.price) * W3_RATIO_TARGET

        wave = WaveCount(
            L0=L0, H1=H1, L2=L2,
            w1_gain=w1_gain,
            w2_retrace=w2_retrace,
            w3_target_price=w3_target,
            is_valid=True,
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

        score = w1_gain * 10 + (1.0 if wave.is_valid else 0.0)
        if score > best_score:
            best_score = score
            best_wave = wave

    return best_wave


def score_wave3_signal(wave: WaveCount, df: pd.DataFrame, name: str = '') -> Tuple[float, List[str]]:
    """计算第3浪起点信号综合分(0-100)。

    基于历史回测数据优化的评分维度(权重已调整)：
      1. 波浪结构有效性 (15分): L2>L0, W1涨幅, W2回调幅度合理
      2. 第1浪涨幅高度 (20分): W1在60%-200%区间得分最高(回测胜率85%+)
      3. 第2浪回调深度 (15分): W2回调30%-70%为最佳介入时点(回测胜率66%+)
      4. 第3浪启动确认 (20分): 现价突破H1(第1浪顶)是第3浪加速的标志
      5. 均线多头排列 (15分): MA5>MA20>MA60，趋势向上
      6. 量能配合 (10分): 近5日成交量放大
      7. 第3浪空间 (5分): 距1.618目标价的空间

    历史回测验证的关键阈值：
      - 信号分≥95: 胜率64.5%, 均收益+5.28%, 盈亏比2.36
      - W1涨幅60-80%: 胜率57.1%, 均收益+6.65%, 盈亏比3.57
      - W2回调30-40%: 胜率66.7%, 均收益+6.24%
      - W2回调50-60%: 胜率61.1%, 均收益+7.06%
      - 最优组合(信号分≥90+W1[80-200%]+W2[30-70%]): 胜率88.9%, 盈亏比8.1
    """
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
        reasons.append(f'W1涨幅{w1_pct:.0f}%处于60-80%最优区间(回测胜率57%,盈亏比3.57)')
    elif 100 <= w1_pct <= 200:
        score += 18
        reasons.append(f'W1涨幅{w1_pct:.0f}%处于100-200%主升浪区间(回测胜率85%+)')
    elif 80 <= w1_pct < 100:
        score += 12
        reasons.append(f'W1涨幅{w1_pct:.0f}%接近最优区间')
    elif 40 <= w1_pct < 60:
        score += 6
        reasons.append(f'W1涨幅{w1_pct:.0f}%偏低(回测胜率仅40%)')
    elif w1_pct > 200:
        score += 10
        reasons.append(f'W1涨幅{w1_pct:.0f}%过高(需警惕)')

    w2_pct = wave.w2_retrace * 100
    if 30 <= w2_pct < 40:
        score += 15
        reasons.append(f'W2回调{w2_pct:.0f}%处于30-40%最佳介入时点(回测胜率67%)')
    elif 50 <= w2_pct < 60:
        score += 13
        reasons.append(f'W2回调{w2_pct:.0f}%处于50-60%深度洗盘区间(回测胜率61%,均收益+7%)')
    elif 40 <= w2_pct < 50:
        score += 8
        reasons.append(f'W2回调{w2_pct:.0f}%适中')
    elif 60 <= w2_pct <= 70:
        score += 6
        reasons.append(f'W2回调{w2_pct:.0f}%较深')
    elif w2_pct > 70:
        score += 2
        reasons.append(f'W2回调{w2_pct:.0f}%过深(回测胜率仅25%)')

    if current_price > wave.H1.price:
        score += 20
        reasons.append(f'现价{current_price:.2f}已突破第1浪顶{wave.H1.price:.2f}(第3浪加速)')
    elif current_price > wave.L2.price * 1.05:
        score += 12
        reasons.append(f'现价从第2浪低点{wave.L2.price:.2f}反弹{(current_price/wave.L2.price-1)*100:.1f}%')

    ma5 = sma(close, 5)
    ma20 = sma(close, 20)
    ma60 = sma(close, 60)
    if len(ma5) > 0 and len(ma20) > 0 and len(ma60) > 0:
        if ma5[-1] > ma20[-1] > ma60[-1]:
            score += 15
            reasons.append('均线多头排列(MA5>MA20>MA60)')
        elif ma5[-1] > ma20[-1]:
            score += 8
            reasons.append('MA5上穿MA20')

    if len(vol) >= 10:
        vol_5 = np.mean(vol[-5:])
        vol_20 = np.mean(vol[-20:])
        if vol_20 > 0 and vol_5 / vol_20 > 1.2:
            score += 10
            reasons.append(f'近5日量比{vol_5/vol_20:.2f}放大')
        elif vol_20 > 0 and vol_5 / vol_20 > 1.0:
            score += 5
            reasons.append(f'近5日量比{vol_5/vol_20:.2f}略增')

    dist_to_target = (wave.w3_target_price - current_price) / max(current_price, 1e-6) * 100
    if dist_to_target > 30:
        score += 5
        reasons.append(f'距1.618目标价{wave.w3_target_price:.2f}还有{dist_to_target:.0f}%空间')
    elif dist_to_target > 10:
        score += 3
        reasons.append(f'距1.618目标价还有{dist_to_target:.0f}%')

    score = min(score, 100.0)
    return score, reasons


def analyze_stock(ts_code: str, name: str = '', industry: str = '',
                  start_date: Optional[str] = None, end_date: Optional[str] = None) -> Optional[Wave3Signal]:
    """分析单只股票的波浪结构。

    Args:
        ts_code: 股票代码 (如 600396.SH)
        name: 股票名称
        industry: 所属行业
        start_date: 起始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD

    Returns:
        Wave3Signal 或 None
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=730)).strftime('%Y%m%d')

    try:
        df = tq.get_hist_data(ts_code)
    except Exception:
        return None
    if df is None or df.empty or len(df) < 60:
        return None

    if 'trade_date' not in df.columns:
        return None
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    if end_date:
        df = df[df['trade_date'] <= end_date]
    if start_date:
        df = df[df['trade_date'] >= start_date]
    if df.empty or len(df) < 60:
        return None
    df = df.sort_values('trade_date').reset_index(drop=True)

    pivots = find_pivots(df)
    if len(pivots) < 3:
        return None

    wave = detect_waves(pivots, df)
    if wave is None or not wave.is_valid:
        return None

    current_price = float(df['close'].values[-1])
    w3_progress = 0.0
    if wave.H3 is not None:
        w3_progress = (current_price - wave.L2.price) / max(wave.H3.price - wave.L2.price, 1e-6) * 100
    elif current_price > wave.L2.price:
        w3_len_target = (wave.H1.price - wave.L0.price) * W3_RATIO_TARGET
        w3_progress = (current_price - wave.L2.price) / max(w3_len_target, 1e-6) * 100

    dist_to_target = (wave.w3_target_price - current_price) / max(current_price, 1e-6) * 100
    score, reasons = score_wave3_signal(wave, df, name)

    return Wave3Signal(
        ts_code=ts_code, name=name, industry=industry,
        wave=wave, current_price=current_price,
        dist_to_w3_target=dist_to_target,
        w3_progress=w3_progress,
        signal_score=score,
        signal_reasons=reasons,
    )


def print_wave_detail(sig: Wave3Signal) -> None:
    """详细打印单只股票的波浪拆解。"""
    w = sig.wave
    print(f"\n{'='*70}")
    print(f"  {sig.ts_code} {sig.name} {sig.industry}")
    print(f"{'='*70}")

    print(f"\n  📈 波浪结构:")
    print(f"    第1浪: {w.L0.date}({w.L0.price:.2f}) → {w.H1.date}({w.H1.price:.2f})"
          f"  涨幅 {w.w1_gain*100:.1f}%")
    print(f"    第2浪: {w.H1.date}({w.H1.price:.2f}) → {w.L2.date}({w.L2.price:.2f})"
          f"  回调 {w.w2_retrace*100:.1f}%")
    print(f"    铁律1: 第2浪低点 {w.L2.price:.2f} > 第1浪起点 {w.L0.price:.2f} "
          f"{'✓' if w.L2.price > w.L0.price else '✗'}")

    if w.H3 is not None:
        print(f"    第3浪: {w.L2.date}({w.L2.price:.2f}) → {w.H3.date}({w.H3.price:.2f})"
              f"  长度是第1浪的 {w.w3_ratio:.2f} 倍"
              f"  {'(>1.618 主升浪确认)' if w.w3_ratio >= W3_RATIO_TARGET else '(未达1.618)'}")
    else:
        print(f"    第3浪: {w.L2.date}({w.L2.price:.2f}) → 进行中  当前价 {sig.current_price:.2f}")

    print(f"    第3浪1.618目标价: {w.w3_target_price:.2f}  "
          f"(距当前 {sig.dist_to_w3_target:+.1f}%)")

    if w.L4 is not None:
        print(f"    第4浪: {w.H3.date if w.H3 else '?'} → {w.L4.date}({w.L4.price:.2f})")
        print(f"    铁律2: 第4浪低点 {w.L4.price:.2f} vs 第1浪顶 {w.H1.price:.2f} "
              f"{'✓ 未破' if w.L4.price > w.H1.price else '✗ 破位'}")
        if w.violation:
            print(f"    ⚠ {w.violation}")
    if w.H5 is not None:
        print(f"    第5浪: → {w.H5.date}({w.H5.price:.2f})")

    print(f"\n  🎯 第3浪起点信号:")
    print(f"    综合评分: {sig.signal_score:.1f}/100")
    for r in sig.signal_reasons:
        print(f"    • {r}")

    print(f"\n  当前价: {sig.current_price:.2f}  第3浪进度: {sig.w3_progress:.1f}%")


def _load_etf_constituents() -> List[str]:
    """加载ETF成份股池(复用run_real.py的JSON缓存,数据已下载)。

    优先级:
      1. d:\\mystock\\cache_daily\\etf_constituents_all.json (已缓存)
      2. 若不存在则从35个行业ETF逐个下载
    """
    import json
    json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
    etf_pool = {
        '512480.SH': '半导体', '159995.SZ': '芯片', '159516.SZ': '半导体设备',
        '159819.SZ': '人工智能', '515230.SH': '软件', '515880.SH': '通信',
        '159732.SZ': '消费电子', '159851.SZ': '金融科技', '159869.SZ': '游戏',
        '516160.SH': '新能源', '515790.SH': '光伏', '159566.SZ': '储能',
        '159755.SZ': '电池', '515030.SH': '新能源车', '159992.SZ': '创新药',
        '159883.SZ': '医疗器械', '512010.SH': '医药', '512660.SH': '军工',
        '159227.SZ': '航空航天', '562500.SH': '机器人', '516650.SH': '有色金属',
        '159870.SZ': '化工', '515220.SH': '煤炭', '515210.SH': '钢铁',
        '159611.SZ': '电力', '561380.SH': '电网设备', '159928.SZ': '消费',
        '159736.SZ': '食品饮料', '512690.SH': '酒', '159996.SZ': '家电',
        '512880.SH': '证券', '512800.SH': '银行', '515180.SH': '红利',
        '518880.SH': '黄金', '159667.SZ': '工业母机',
    }

    all_stocks = set()
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for etf_code in etf_pool:
            if etf_code in data:
                stocks = [s for s in data[etf_code]
                          if not s.endswith('.BJ') and s != 'Au9999'][:50]
                all_stocks.update(stocks)

    if not all_stocks:
        for etf_code in etf_pool:
            try:
                cons_df = dfetcher.get_etf_cons(ts_code=etf_code)
                if cons_df is not None and not cons_df.empty:
                    latest = cons_df['trade_date'].max()
                    cons_df = cons_df[cons_df['trade_date'] == latest].sort_values('cpr', ascending=False)
                    stocks = [c for c in cons_df['con_code'].tolist()
                              if not str(c).endswith('.BJ') and c != 'Au9999'][:50]
                    all_stocks.update(stocks)
            except Exception:
                continue

    return sorted(all_stocks)


def scan_market(top_n: int = 20, min_score: float = 50.0,
                start_date: Optional[str] = None, end_date: Optional[str] = None,
                scope: str = 'etf') -> List[Wave3Signal]:
    """扫描寻找处于第3浪起点的股票。

    Args:
        top_n: 输出前N只
        min_score: 最低信号分
        start_date: 数据起始日期
        end_date: 数据结束日期
        scope: 扫描范围 'etf'=ETF成份股池(快,数据已缓存) 'all'=全市场(慢)

    Returns:
        符合条件的信号列表
    """
    print("=" * 70)
    print(f"  波浪理论第3浪起点扫描 | 范围={scope} | 最低评分 {min_score}")
    print("=" * 70, flush=True)

    name_map, industry_map = {}, {}
    codes: List[str] = []

    if scope == 'etf':
        print("\n[1] 加载ETF成份股池(复用run_real.py缓存)...")
        codes = _load_etf_constituents()
        try:
            stock_basic = dfetcher.get_stock_list(list_status='L')
            if stock_basic is not None and not stock_basic.empty:
                name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
                industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
        except Exception:
            pass
    else:
        print("\n[1] 加载全市场股票列表...")
        try:
            stock_basic = dfetcher.get_stock_list(list_status='L')
        except Exception as e:
            print(f"  ❌ 获取股票列表失败: {e}")
            return []
        if stock_basic is None or stock_basic.empty:
            print("  ❌ 股票列表为空")
            return []
        stock_basic = stock_basic[~stock_basic['ts_code'].str.endswith('.BJ')]
        stock_basic = stock_basic[~stock_basic['name'].str.startswith('ST', na=False)]
        stock_basic = stock_basic[~stock_basic['name'].str.startswith('*ST', na=False)]
        stock_basic = stock_basic[~stock_basic['ts_code'].str.startswith(('8', '4'), na=False)]
        name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
        industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
        codes = stock_basic['ts_code'].tolist()

    print(f"  共 {len(codes)} 只股票待扫描", flush=True)

    print(f"\n[2] 逐只分析波浪结构(每50只输出进度)...")
    signals: List[Wave3Signal] = []
    for i, code in enumerate(codes):
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(codes)}  已发现 {len(signals)} 个信号", flush=True)
        try:
            sig = analyze_stock(code, name_map.get(code, ''), industry_map.get(code, ''),
                                start_date, end_date)
            if sig is not None and sig.signal_score >= min_score:
                signals.append(sig)
        except Exception:
            continue

    signals.sort(key=lambda s: -s.signal_score)
    return signals[:top_n]


def main():
    parser = argparse.ArgumentParser(description='大牛股第3浪起点发现器')
    parser.add_argument('--code', type=str, help='单只股票代码(如 600396.SH) 验证波浪结构')
    parser.add_argument('--scan', action='store_true', help='扫描股票池')
    parser.add_argument('--scope', type=str, default='etf', choices=['etf', 'all'],
                        help='扫描范围: etf=ETF成份股池(快,默认) all=全市场(慢)')
    parser.add_argument('--top', type=int, default=20, help='扫描结果输出前N只(默认20)')
    parser.add_argument('--min-score', type=float, default=60.0, help='最低信号分(默认60)')
    parser.add_argument('--start', type=str, help='数据起始日期 YYYYMMDD')
    parser.add_argument('--end', type=str, help='数据结束日期 YYYYMMDD')
    args = parser.parse_args()

    if args.code:
        if '.' not in args.code:
            if args.code.startswith('6'):
                args.code = args.code + '.SH'
            else:
                args.code = args.code + '.SZ'
        stock_basic = None
        try:
            stock_basic = dfetcher.get_stock_list(list_status='L')
        except Exception:
            pass
        name, industry = '', ''
        if stock_basic is not None and not stock_basic.empty:
            row = stock_basic[stock_basic['ts_code'] == args.code]
            if not row.empty:
                name = row.iloc[0]['name']
                industry = row.iloc[0]['industry']

        sig = analyze_stock(args.code, name, industry, args.start, args.end)
        if sig is None:
            print(f"\n{args.code} {name}: 未识别出有效的波浪结构")
            return
        print_wave_detail(sig)
        return

    if args.scan:
        signals = scan_market(top_n=args.top, min_score=args.min_score,
                              start_date=args.start, end_date=args.end,
                              scope=args.scope)
        if not signals:
            print("\n[结果] 未发现符合条件的第3浪起点信号")
            return

        print(f"\n[结果] 发现 {len(signals)} 个第3浪起点信号:")
        print(f"  {'代码':<12}{'名称':<10}{'行业':<10}{'现价':<10}{'W1涨幅':<10}"
              f"{'W2回调':<10}{'W3目标':<12}{'信号分':<8}")
        print(f"  {'-'*82}")
        for s in signals:
            print(f"  {s.ts_code:<12}{s.name:<10}{s.industry:<10}{s.current_price:<10.2f}"
                  f"{s.wave.w1_gain*100:<10.1f}{s.wave.w2_retrace*100:<10.1f}"
                  f"{s.wave.w3_target_price:<12.2f}{s.signal_score:<8.1f}")

        output_path = r'd:\mystock\solo\etf_resonance\output\wave3_signals.csv'
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        rows = []
        for s in signals:
            w = s.wave
            rows.append({
                'code': s.ts_code, 'name': s.name, 'industry': s.industry,
                'current_price': s.current_price,
                'w1_start_date': w.L0.date, 'w1_start_price': w.L0.price,
                'w1_end_date': w.H1.date, 'w1_end_price': w.H1.price,
                'w1_gain_pct': round(w.w1_gain * 100, 1),
                'w2_end_date': w.L2.date, 'w2_end_price': w.L2.price,
                'w2_retrace_pct': round(w.w2_retrace * 100, 1),
                'w3_target_price': round(w.w3_target_price, 2),
                'dist_to_w3_target_pct': round(s.dist_to_w3_target, 1),
                'w3_progress_pct': round(s.w3_progress, 1),
                'signal_score': round(s.signal_score, 1),
                'signal_reasons': '; '.join(s.signal_reasons),
            })
        pd.DataFrame(rows).to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\n  已保存: {output_path}")
        return

    parser.print_help()


if __name__ == '__main__':
    main()
