"""优化版W2策略对比回测: 原版W2 vs 优化版W2 vs W3

目的: 回答"优化2wave,还是只保留3就好了?"

基于德方纳米破位分析得出的5个W2系统性缺陷,构建优化版W2评分函数:
  1. 大盘趋势过滤: 大盘在MA20下方时,W2信号大幅扣分(左侧抄底在熊市极危险)
  2. MACD死叉检测: MACD刚死叉/DIF<0/DIF<DEA时扣分(趋势转弱否决)
  3. MA60趋势过滤: 现价在MA60下方时扣分(中期趋势向下不抄底)
  4. 右侧确认: W2回调>50%时要求反弹突破H1的80%(防C浪下跌)
  5. 量能结构: W2深回调(>60%)时要求缩量(放量下跌=仍在下跌中)

ETF成份股池(经对比验证优于合格股池)
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass, field

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from multi_factor_picker.data_fetcher import DataFetcher
from etf_resonance.wave3_detector import (
    find_pivots, detect_waves, WaveCount, Pivot,
    W1_MIN_GAIN, W2_RETRACE_MIN, W2_RETRACE_MAX, W3_RATIO_TARGET,
    score_wave3_signal,
)
from etf_resonance.utils.indicators import sma, ema
from dotenv import load_dotenv

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

# ============== 回测参数 ==============
REBALANCE_DAYS = 5
HOLDING_TOPN = 5
BACKTEST_MONTHS = 6
BENCHMARK = '000300.SH'
STOP_LOSS_PCT = -8.0
TAKE_PROFIT_PCT = 20.0
MARKET_FILTER = True
MARKET_MA = 20

ETF_THEME_MAP = {
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

ETF_CONSTITUENTS_JSON = r'd:\mystock\cache_daily\etf_constituents_all.json'


def load_etf_pool() -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """加载ETF成份股池(与run_backtest_wave3一致)。"""
    all_etf_constituents = {}
    if os.path.exists(ETF_CONSTITUENTS_JSON):
        with open(ETF_CONSTITUENTS_JSON, 'r', encoding='utf-8') as f:
            all_etf_constituents = json.load(f)

    missing_etfs = [c for c in ETF_THEME_MAP if c not in all_etf_constituents]
    if missing_etfs:
        for etf_code in missing_etfs:
            try:
                cons_df = dfetcher.get_etf_cons(ts_code=etf_code)
                if cons_df is not None and not cons_df.empty:
                    latest = cons_df['trade_date'].max()
                    cons_df = cons_df[cons_df['trade_date'] == latest].sort_values('cpr', ascending=False)
                    stocks = [c for c in cons_df['con_code'].tolist()
                              if not str(c).endswith('.BJ') and c != 'Au9999']
                    all_etf_constituents[etf_code] = stocks
            except Exception:
                pass

    all_stocks = set()
    for etf_code in ETF_THEME_MAP:
        if etf_code in all_etf_constituents:
            stocks = [s for s in all_etf_constituents[etf_code]
                      if not s.endswith('.BJ') and s != 'Au9999'][:50]
            all_stocks.update(stocks)

    codes = sorted(all_stocks)

    try:
        stock_basic = dfetcher.get_stock_list(list_status='L')
        name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
        industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
    except Exception:
        name_map = {}
        industry_map = {}

    return codes, name_map, industry_map


MIN_SIGNAL_SCORE = 90.0
MIN_W1_GAIN = 0.60
W2_RETRACE_RANGE = (0.30, 0.70)


@dataclass
class WaveSignal:
    ts_code: str
    name: str
    industry: str
    wave: WaveCount
    current_price: float
    signal_score: float
    signal_reasons: List[str] = field(default_factory=list)
    dist_to_w3_target: float = 0.0
    w3_progress: float = 0.0
    wave_stage: str = 'W3'


# ============== 原版W2评分(基线) ==============
def score_w2_original(wave: WaveCount, df: pd.DataFrame) -> Tuple[float, List[str]]:
    """原版W2信号分(复制自run_backtest_wave_compare)。"""
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


# ============== 优化版W2评分(加5个过滤器) ==============
def score_w2_optimized(wave: WaveCount, df: pd.DataFrame,
                       bench_bullish: bool = True) -> Tuple[float, List[str]]:
    """优化版W2信号分 - 在原版基础上加5个缺陷过滤器。

    过滤器(扣分制,从基础分中扣除):
      1. 大盘趋势: 大盘在MA20下方时扣30分(左侧抄底在熊市极危险)
      2. MACD死叉: 刚死叉扣25分, DIF<0扣15分, DIF<DEA扣10分
      3. MA60趋势: 现价<MA60扣20分(中期趋势向下不抄底)
      4. 右侧确认: W2回调>50%但反弹未到H1的80%扣15分(防C浪下跌)
      5. 量能结构: W2深回调>60%但放量扣10分(放量下跌=仍在下跌中)
    """
    base_score, reasons = score_w2_original(wave, df)

    close = df['close'].values
    vol = df['vol'].values if 'vol' in df.columns else df.get('volume', pd.Series([0]*len(df))).values
    current_price = float(close[-1])

    penalties = []
    penalty = 0.0

    # 过滤器1: 大盘趋势
    if not bench_bullish:
        penalty += 30
        penalties.append('大盘在MA20下方,左侧抄底风险极高(-30)')

    # 过滤器2: MACD死叉检测
    if len(close) >= 35:
        ema12 = ema(close, 12)
        ema26 = ema(close, 26)
        dif = ema12 - ema26
        dea = ema(dif, 9)
        if len(dif) >= 2 and len(dea) >= 1:
            if dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
                penalty += 25
                penalties.append('MACD刚死叉,趋势转弱(-25)')
            elif dif[-1] < 0:
                penalty += 15
                penalties.append(f'DIF={dif[-1]:.3f}<0,中期趋势偏空(-15)')
            elif dif[-1] < dea[-1]:
                penalty += 10
                penalties.append('DIF<DEA,短期偏弱(-10)')

    # 过滤器3: MA60趋势
    ma60 = sma(close, 60)
    if len(ma60) > 0 and not np.isnan(ma60[-1]) and current_price < ma60[-1]:
        penalty += 20
        penalties.append(f'现价{current_price:.2f}<MA60({ma60[-1]:.2f}),中期趋势向下(-20)')

    # 过滤器4: 右侧确认(W2回调>50%时要求反弹突破H1的80%)
    w2_pct = wave.w2_retrace * 100
    h1_80 = wave.L2.price + (wave.H1.price - wave.L2.price) * 0.8
    if w2_pct >= 50 and current_price < h1_80:
        penalty += 15
        penalties.append(f'W2回调{w2_pct:.0f}%但反弹未到H1的80%({h1_80:.2f}),可能是C浪下跌(-15)')

    # 过滤器5: 量能结构(W2深回调>60%时要求缩量)
    if w2_pct >= 60 and len(vol) >= 20:
        vol_5 = np.mean(vol[-5:])
        vol_20 = np.mean(vol[-20:])
        if vol_20 > 0 and vol_5 > vol_20 * 1.2:
            penalty += 10
            penalties.append(f'W2深回调{w2_pct:.0f}%但放量(5/20={vol_5/vol_20:.2f}),仍在下跌中(-10)')

    final_score = max(0, base_score - penalty)
    all_reasons = reasons + penalties
    if penalty > 0:
        all_reasons.append(f'基础分{base_score:.0f}-扣分{penalty:.0f}={final_score:.0f}')
    return min(final_score, 100.0), all_reasons


def analyze_wave_signal(code: str, stock_df: pd.DataFrame, name: str, industry: str,
                        strategy: str = 'W3', bench_bullish: bool = True) -> Optional[WaveSignal]:
    """分析单只股票的波浪信号。strategy: W2_orig/W2_opt/W3"""
    if stock_df is None or len(stock_df) < 60:
        return None
    pivots = find_pivots(stock_df)
    if len(pivots) < 3:
        return None
    wave = detect_waves(pivots, stock_df)
    if wave is None or not wave.is_valid:
        return None

    current_price = float(stock_df['close'].values[-1])

    if strategy in ('W2_orig', 'W2_opt'):
        if current_price > wave.H1.price:
            return None
        if current_price < wave.L2.price * 1.02:
            return None
        if strategy == 'W2_orig':
            score, reasons = score_w2_original(wave, stock_df)
        else:
            score, reasons = score_w2_optimized(wave, stock_df, bench_bullish)
        wave_stage = 'W2'
    else:
        if current_price < wave.H1.price:
            return None
        score, reasons = score_wave3_signal(wave, stock_df)
        wave_stage = 'W3'

    w3_progress = 0.0
    if wave.H3 is not None:
        w3_progress = (current_price - wave.L2.price) / max(wave.H3.price - wave.L2.price, 1e-6) * 100
    elif current_price > wave.L2.price:
        w3_len_target = (wave.H1.price - wave.L0.price) * W3_RATIO_TARGET
        w3_progress = (current_price - wave.L2.price) / max(w3_len_target, 1e-6) * 100

    dist_to_target = (wave.w3_target_price - current_price) / max(current_price, 1e-6) * 100

    return WaveSignal(
        ts_code=code, name=name, industry=industry,
        wave=wave, current_price=current_price,
        signal_score=score, signal_reasons=reasons,
        dist_to_w3_target=dist_to_target,
        w3_progress=w3_progress,
        wave_stage=wave_stage,
    )


def run_backtest(strategy: str, stock_data: Dict, name_map: Dict, industry_map: Dict,
                 all_trade_dates: List[str], rebalance_dates: List[str],
                 bench_df: pd.DataFrame) -> Tuple[List[dict], List[dict]]:
    """运行单策略回测。"""
    all_trades = []
    portfolio_values = []
    current_holdings = {}
    INIT_CAPITAL = 1_000_000
    cash = INIT_CAPITAL

    def slice_stock_data(as_of_date):
        out = {}
        for code, df in stock_data.items():
            sliced = df[df['trade_date'] <= as_of_date].copy()
            if len(sliced) >= 60:
                out[code] = sliced
        return out

    def get_price(code, date):
        df = stock_data.get(code)
        if df is None:
            return None
        row = df[df['trade_date'] == date]
        return float(row['close'].iloc[0]) if not row.empty else None

    def get_daily_prices_between(code, start_date, end_date):
        df = stock_data.get(code)
        if df is None:
            return []
        mask = (df['trade_date'] > start_date) & (df['trade_date'] <= end_date)
        return df[mask]['close'].tolist()

    def is_market_bullish(as_of_date):
        if not MARKET_FILTER or bench_df is None:
            return True
        sliced = bench_df[bench_df['trade_date'] <= as_of_date].copy()
        if len(sliced) < MARKET_MA:
            return True
        ma = sliced['close'].rolling(MARKET_MA).mean().iloc[-1]
        cur = sliced['close'].iloc[-1]
        return cur > ma

    print(f"\n[3] 开始{strategy}策略回测...", flush=True)

    for rb_idx, rb_date in enumerate(rebalance_dates):
        if (rb_idx + 1) % 5 == 0:
            print(f"  {strategy}进度: {rb_idx+1}/{len(rebalance_dates)}", flush=True)

        bullish = is_market_bullish(rb_date)
        if not bullish:
            for code in list(current_holdings.keys()):
                pos = current_holdings[code]
                exit_price = get_price(code, rb_date) or pos['entry_price']
                ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
                cash += pos['shares'] * exit_price * (1 - 0.0003)
                hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
                all_trades.append({
                    'entry_date': pos['entry_date'], 'exit_date': rb_date, 'code': code,
                    'stock_name': pos.get('stock_name', ''), 'industry': pos.get('industry', ''),
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                    'signal_score': pos.get('signal_score', 0),
                    'w1_gain': pos.get('w1_gain', 0),
                    'w2_retrace': pos.get('w2_retrace', 0),
                    'exit_reason': '大盘空仓', 'strategy': strategy,
                })
                del current_holdings[code]
            portfolio_values.append({'date': rb_date, 'equity': cash})
            continue

        stock_slice = slice_stock_data(rb_date)
        candidates = []
        for code, df in stock_slice.items():
            try:
                sig = analyze_wave_signal(code, df, name_map.get(code, ''),
                                          industry_map.get(code, ''), strategy, bullish)
                if sig is None:
                    continue
                if sig.signal_score < MIN_SIGNAL_SCORE:
                    continue
                w1 = sig.wave.w1_gain
                w2 = sig.wave.w2_retrace
                if w1 < MIN_W1_GAIN:
                    continue
                if not (W2_RETRACE_RANGE[0] <= w2 <= W2_RETRACE_RANGE[1]):
                    continue
                candidates.append({
                    'code': code, 'signal_score': sig.signal_score,
                    'current_price': sig.current_price,
                    'w1_gain': w1, 'w2_retrace': w2,
                    'w3_target': sig.wave.w3_target_price,
                })
            except Exception:
                continue

        candidates.sort(key=lambda x: -x['signal_score'])
        top_picks = candidates[:HOLDING_TOPN]
        top_codes = {p['code'] for p in top_picks}

        if not top_picks:
            portfolio_values.append({'date': rb_date, 'equity': cash + sum(
                h['shares'] * (get_price(c, rb_date) or h['entry_price'])
                for c, h in current_holdings.items())})
            continue

        for code in list(current_holdings.keys()):
            pos = current_holdings[code]
            prices = get_daily_prices_between(code, pos['entry_date'], rb_date)
            triggered = False
            for i, p in enumerate(prices):
                ret = (p / pos['entry_price'] - 1) * 100
                if ret <= STOP_LOSS_PCT:
                    exit_price = p
                    ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
                    cash += pos['shares'] * exit_price * (1 - 0.0003)
                    trade_dates_between = [d for d in all_trade_dates
                                           if pos['entry_date'] < d <= rb_date]
                    exit_date = trade_dates_between[i] if i < len(trade_dates_between) else rb_date
                    hold_days = i + 1
                    all_trades.append({
                        'entry_date': pos['entry_date'], 'exit_date': exit_date, 'code': code,
                        'stock_name': pos.get('stock_name', ''), 'industry': pos.get('industry', ''),
                        'entry_price': pos['entry_price'], 'exit_price': exit_price,
                        'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                        'signal_score': pos.get('signal_score', 0),
                        'w1_gain': pos.get('w1_gain', 0),
                        'w2_retrace': pos.get('w2_retrace', 0),
                        'exit_reason': f'止损{STOP_LOSS_PCT}%', 'strategy': strategy,
                    })
                    del current_holdings[code]
                    triggered = True
                    break
                if ret >= TAKE_PROFIT_PCT:
                    exit_price = p
                    ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
                    cash += pos['shares'] * exit_price * (1 - 0.0003)
                    trade_dates_between = [d for d in all_trade_dates
                                           if pos['entry_date'] < d <= rb_date]
                    exit_date = trade_dates_between[i] if i < len(trade_dates_between) else rb_date
                    hold_days = i + 1
                    all_trades.append({
                        'entry_date': pos['entry_date'], 'exit_date': exit_date, 'code': code,
                        'stock_name': pos.get('stock_name', ''), 'industry': pos.get('industry', ''),
                        'entry_price': pos['entry_price'], 'exit_price': exit_price,
                        'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                        'signal_score': pos.get('signal_score', 0),
                        'w1_gain': pos.get('w1_gain', 0),
                        'w2_retrace': pos.get('w2_retrace', 0),
                        'exit_reason': f'止盈{TAKE_PROFIT_PCT}%', 'strategy': strategy,
                    })
                    del current_holdings[code]
                    triggered = True
                    break

            if not triggered and code not in top_codes:
                exit_price = get_price(code, rb_date) or pos['entry_price']
                ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
                cash += pos['shares'] * exit_price * (1 - 0.0003)
                hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
                all_trades.append({
                    'entry_date': pos['entry_date'], 'exit_date': rb_date, 'code': code,
                    'stock_name': pos.get('stock_name', ''), 'industry': pos.get('industry', ''),
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                    'signal_score': pos.get('signal_score', 0),
                    'w1_gain': pos.get('w1_gain', 0),
                    'w2_retrace': pos.get('w2_retrace', 0),
                    'exit_reason': '调仓换股', 'strategy': strategy,
                })
                del current_holdings[code]

        total_equity = cash
        for code, pos in current_holdings.items():
            cur_price = get_price(code, rb_date) or pos['entry_price']
            total_equity += pos['shares'] * cur_price
        target_per_stock = total_equity / HOLDING_TOPN if total_equity > 0 else 0

        for pick in top_picks:
            code = pick['code']
            if code in current_holdings:
                continue
            entry_price = get_price(code, rb_date)
            if entry_price is None or entry_price <= 0:
                continue
            if cash < target_per_stock:
                continue
            shares = int(target_per_stock / entry_price / 100) * 100
            if shares <= 0:
                shares = int(target_per_stock / entry_price)
            if shares <= 0:
                continue
            cash -= shares * entry_price * (1 + 0.0003)
            current_holdings[code] = {
                'entry_date': rb_date, 'entry_price': entry_price, 'shares': shares,
                'signal_score': pick['signal_score'],
                'w1_gain': pick['w1_gain'], 'w2_retrace': pick['w2_retrace'],
                'stock_name': name_map.get(code, ''), 'industry': industry_map.get(code, ''),
            }

        total_equity = cash
        for code, pos in current_holdings.items():
            cur_price = get_price(code, rb_date) or pos['entry_price']
            total_equity += pos['shares'] * cur_price
        portfolio_values.append({'date': rb_date, 'equity': total_equity})

    final_date = all_trade_dates[-1] if all_trade_dates else datetime.now().strftime('%Y%m%d')
    for code in list(current_holdings.keys()):
        pos = current_holdings[code]
        exit_price = get_price(code, final_date) or pos['entry_price']
        ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
        cash += pos['shares'] * exit_price * (1 - 0.0003)
        hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= final_date)
        all_trades.append({
            'entry_date': pos['entry_date'], 'exit_date': final_date, 'code': code,
            'stock_name': pos.get('stock_name', ''), 'industry': pos.get('industry', ''),
            'entry_price': pos['entry_price'], 'exit_price': exit_price,
            'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
            'signal_score': pos.get('signal_score', 0),
            'w1_gain': pos.get('w1_gain', 0),
            'w2_retrace': pos.get('w2_retrace', 0),
            'exit_reason': '回测结束', 'strategy': strategy,
        })
        del current_holdings[code]

    return all_trades, portfolio_values


def calc_metrics(trades: List[dict], portfolio_values: List[dict], bench_ret: float,
                 INIT_CAPITAL: float = 1_000_000) -> dict:
    if not trades:
        return {}
    trades_df = pd.DataFrame(trades)
    total_trades = len(trades_df)
    winning = trades_df[trades_df['return_pct'] > 0]
    win_rate = len(winning) / total_trades * 100
    avg_ret = trades_df['return_pct'].mean()
    avg_hold = trades_df['hold_days'].mean()

    cum_ret = 0; annual_ret = 0; sharpe = 0; max_dd = 0
    if portfolio_values:
        pv_df = pd.DataFrame(portfolio_values)
        pv_df['return'] = pv_df['equity'].pct_change().fillna(0)
        cum_ret = (pv_df['equity'].iloc[-1] / INIT_CAPITAL - 1) * 100
        days = (datetime.strptime(pv_df['date'].iloc[-1], '%Y%m%d') -
                datetime.strptime(pv_df['date'].iloc[0], '%Y%m%d')).days
        annual_ret = ((pv_df['equity'].iloc[-1] / INIT_CAPITAL) ** (365 / max(days, 1)) - 1) * 100
        sharpe = (pv_df['return'].mean() / pv_df['return'].std() * np.sqrt(252)
                  if pv_df['return'].std() > 0 else 0)
        cum = (1 + pv_df['return']).cumprod()
        peak = cum.expanding().max()
        dd = (cum / peak - 1)
        max_dd = dd.min() * 100

    stop_loss_trades = trades_df[trades_df['exit_reason'].str.startswith('止损', na=False)]
    take_profit_trades = trades_df[trades_df['exit_reason'].str.startswith('止盈', na=False)]
    rotate_trades = trades_df[trades_df['exit_reason'] == '调仓换股']

    win_avg = trades_df[trades_df['return_pct'] > 0]['return_pct'].mean() if len(winning) > 0 else 0
    lose_avg = trades_df[trades_df['return_pct'] <= 0]['return_pct'].mean() if len(trades_df[trades_df['return_pct']<=0])>0 else 0
    pf = abs(win_avg * len(winning) / (lose_avg * len(trades_df[trades_df['return_pct']<=0]) + 1e-6))

    return {
        'cum_ret': cum_ret, 'annual_ret': annual_ret, 'sharpe': sharpe, 'max_dd': max_dd,
        'win_rate': win_rate, 'avg_ret': avg_ret, 'avg_hold': avg_hold,
        'total_trades': total_trades, 'stop_loss': len(stop_loss_trades),
        'take_profit': len(take_profit_trades), 'rotate': len(rotate_trades),
        'win_avg': win_avg, 'lose_avg': lose_avg, 'profit_factor': pf,
        'bench_ret': bench_ret, 'excess_ret': cum_ret - bench_ret,
    }


def print_report(strategy: str, trades: List[dict], m: dict):
    print(f"\n{'='*70}")
    print(f"          📊 {strategy}策略回测报告")
    print(f"{'='*70}")
    print(f"  📈 累计收益:   {m['cum_ret']:+.2f}%")
    print(f"  📅 年化收益:   {m['annual_ret']:+.2f}%")
    print(f"  📊 夏普比率:   {m['sharpe']:.2f}")
    print(f"  📉 最大回撤:   {m['max_dd']:.2f}%")
    print(f"  🎯 胜率:       {m['win_rate']:.1f}% ({int(m['win_rate']*m['total_trades']/100)}/{m['total_trades']})")
    print(f"  💰 平均单笔:   {m['avg_ret']:+.2f}%")
    print(f"  📊 盈亏比:     {m['profit_factor']:.2f} (盈{m['win_avg']:+.1f}/亏{m['lose_avg']:+.1f})")
    print(f"  ⏱️  平均持仓:   {m['avg_hold']:.1f} 天")
    print(f"  🔄 交易次数:   {m['total_trades']} (止损{m['stop_loss']}|止盈{m['take_profit']}|调仓{m['rotate']})")
    print(f"  📌 基准:       {m['bench_ret']:+.2f}%")
    print(f"  🆚 超额收益:   {m['excess_ret']:+.2f}%")


# ============== 主流程 ==============
print("=" * 70)
print(f"优化版W2策略对比回测 | 股池: ETF成份股池")
print(f"回测期: 过去 {BACKTEST_MONTHS} 个月 | 调仓: {REBALANCE_DAYS}日 | 持仓: Top{HOLDING_TOPN}")
print(f"止损: {STOP_LOSS_PCT}% | 止盈: {TAKE_PROFIT_PCT}% | 大盘择时: {MARKET_FILTER}")
print(f"信号分≥{MIN_SIGNAL_SCORE} | W1≥{MIN_W1_GAIN*100:.0f}% | W2回调{W2_RETRACE_RANGE[0]*100:.0f}-{W2_RETRACE_RANGE[1]*100:.0f}%")
print("=" * 70, flush=True)

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 2)).strftime('%Y%m%d')
warmup_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 8)).strftime('%Y%m%d')

print(f"\n[1] 加载ETF成份股池...")
codes, name_map, industry_map = load_etf_pool()
print(f"  ETF股池: {len(codes)} 只")

print(f"\n[2] 下载日线数据 {warmup_date} ~ {end_date}")
stock_data = {}
for i, code in enumerate(codes):
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=warmup_date, end_date=end_date)
        if df is not None and not df.empty:
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    if (i + 1) % 200 == 0:
        print(f"  进度: {i+1}/{len(codes)}", flush=True)
print(f"  成功: {len(stock_data)}/{len(codes)}")

print("\n  下载基准指数...")
bench_df = dfetcher.get_index_daily(ts_code=BENCHMARK, start_date=start_date, end_date=end_date)
if bench_df is not None and not bench_df.empty:
    bench_df = bench_df.sort_values('trade_date').reset_index(drop=True)

all_trade_dates = sorted(bench_df['trade_date'].tolist()) if bench_df is not None else []
backtest_start = (datetime.now() - relativedelta(months=BACKTEST_MONTHS)).strftime('%Y%m%d')
trade_dates_bt = [d for d in all_trade_dates if d >= backtest_start]
rebalance_dates = trade_dates_bt[::REBALANCE_DAYS]

bench_bt = bench_df[bench_df['trade_date'] >= backtest_start] if bench_df is not None else pd.DataFrame()
bench_ret = (bench_bt['close'].iloc[-1] / bench_bt['close'].iloc[0] - 1) * 100 if not bench_bt.empty else 0

# ============== 运行3方对比回测 ==============
w2o_trades, w2o_pv = run_backtest('W2_orig', stock_data, name_map, industry_map,
                                   all_trade_dates, rebalance_dates, bench_df)
w2p_trades, w2p_pv = run_backtest('W2_opt', stock_data, name_map, industry_map,
                                   all_trade_dates, rebalance_dates, bench_df)
w3_trades, w3_pv = run_backtest('W3', stock_data, name_map, industry_map,
                                all_trade_dates, rebalance_dates, bench_df)

w2o_metrics = calc_metrics(w2o_trades, w2o_pv, bench_ret)
w2p_metrics = calc_metrics(w2p_trades, w2p_pv, bench_ret)
w3_metrics = calc_metrics(w3_trades, w3_pv, bench_ret)

print_report('原版W2', w2o_trades, w2o_metrics)
print_report('优化版W2', w2p_trades, w2p_metrics)
print_report('W3浪起点', w3_trades, w3_metrics)

# ============== 三方对比汇总 ==============
print(f"\n{'='*78}")
print(f"          ⚔️  原版W2 vs 优化版W2 vs W3  三方对比")
print(f"{'='*78}")
print(f"  {'指标':<14}{'原版W2':<16}{'优化版W2':<16}{'W3浪起点':<16}")
print(f"  {'-'*62}")
if w2o_metrics:
    print(f"  {'累计收益':<12}{w2o_metrics['cum_ret']:+.2f}%{'':>7}", end='')
else:
    print(f"  {'累计收益':<12}{'无信号':<16}", end='')
if w2p_metrics:
    print(f"{w2p_metrics['cum_ret']:+.2f}%{'':>7}", end='')
else:
    print(f"{'无信号':<16}", end='')
if w3_metrics:
    print(f"{w3_metrics['cum_ret']:+.2f}%")
else:
    print(f"{'无信号'}")

if w2o_metrics and w2p_metrics and w3_metrics:
    print(f"  {'年化收益':<12}{w2o_metrics['annual_ret']:+.2f}%{'':>7}{w2p_metrics['annual_ret']:+.2f}%{'':>7}{w3_metrics['annual_ret']:+.2f}%")
    print(f"  {'夏普比率':<12}{w2o_metrics['sharpe']:.2f}{'':>11}{w2p_metrics['sharpe']:.2f}{'':>11}{w3_metrics['sharpe']:.2f}")
    print(f"  {'最大回撤':<12}{w2o_metrics['max_dd']:.2f}%{'':>8}{w2p_metrics['max_dd']:.2f}%{'':>8}{w3_metrics['max_dd']:.2f}%")
    print(f"  {'胜率':<14}{w2o_metrics['win_rate']:.1f}%{'':>8}{w2p_metrics['win_rate']:.1f}%{'':>8}{w3_metrics['win_rate']:.1f}%")
    print(f"  {'平均单笔':<12}{w2o_metrics['avg_ret']:+.2f}%{'':>7}{w2p_metrics['avg_ret']:+.2f}%{'':>7}{w3_metrics['avg_ret']:+.2f}%")
    print(f"  {'盈亏比':<14}{w2o_metrics['profit_factor']:.2f}{'':>11}{w2p_metrics['profit_factor']:.2f}{'':>11}{w3_metrics['profit_factor']:.2f}")
    print(f"  {'交易次数':<12}{w2o_metrics['total_trades']:<16}{w2p_metrics['total_trades']:<16}{w3_metrics['total_trades']}")
    print(f"  {'止损次数':<12}{w2o_metrics['stop_loss']:<16}{w2p_metrics['stop_loss']:<16}{w3_metrics['stop_loss']}")
    print(f"  {'止盈次数':<12}{w2o_metrics['take_profit']:<16}{w2p_metrics['take_profit']:<16}{w3_metrics['take_profit']}")
    print(f"  {'超额收益':<12}{w2o_metrics['excess_ret']:+.2f}%{'':>7}{w2p_metrics['excess_ret']:+.2f}%{'':>7}{w3_metrics['excess_ret']:+.2f}%")
print(f"{'='*78}")

# ============== 决策结论 ==============
print(f"\n{'='*78}")
print(f"          📋 决策结论")
print(f"{'='*78}")
if not w2p_metrics or w2p_metrics['total_trades'] == 0:
    print("  ❌ 优化版W2在回测期内0信号 -> 5个过滤器过于严格,把所有W2信号都筛掉了")
    print("     说明: W2策略的固有缺陷无法通过过滤器修复,问题不在评分而在策略逻辑本身")
elif w3_metrics and w2p_metrics:
    if w2p_metrics['cum_ret'] >= w3_metrics['cum_ret'] and w2p_metrics['win_rate'] >= w3_metrics['win_rate']:
        print("  ✅ 优化版W2在收益和胜率上均达到或超过W3 -> 建议保留两者组合使用")
    elif w2p_metrics['cum_ret'] > 0 and w2p_metrics['win_rate'] > w2o_metrics.get('win_rate', 0):
        print("  ⚠️ 优化版W2相比原版有改善,但仍未追上W3")
        print("     说明: 过滤器能减少亏损,但无法改变W2左侧抄底的本质风险")
        print("     建议: 只保留W3,W2的左侧抄底逻辑在结构性问题无法修复")
    else:
        print("  ❌ 优化版W2仍无法盈利,且未追上W3")
        print("     结论: W2策略的左侧抄底本质决定了它不适合当前市场环境")
        print("     建议: 只保留W3浪起点策略,W2策略予以放弃")
print(f"{'='*78}")

# ============== 保存结果 ==============
output_dir = r'd:\mystock\solo\etf_resonance\output'
all_trades_df = pd.DataFrame(w2o_trades + w2p_trades + w3_trades)
all_trades_df.to_csv(os.path.join(output_dir, 'backtest_w2_optimize_trades.csv'),
                     index=False, encoding='utf-8-sig')
print(f"\n[已保存] 交易明细: backtest_w2_optimize_trades.csv")
print("=" * 70)
