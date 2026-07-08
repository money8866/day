"""波浪策略对比回测: W2浪结束点 vs W3浪起点

策略对比：
  W2策略：第1浪建仓 → 第2浪回调结束点买入（更早介入，抄底性质）
          介入时点：现价从L2反弹，但未突破H1（仍在第2浪中）
  W3策略：第1浪建仓 → 第2浪洗盘 → 第3浪启动买入（右侧确认，追涨性质）
          介入时点：现价已突破H1（第3浪加速）

合格股池：D:\\mystock\\solo\\report_daily\\bull_stocks_qualified.csv
"""
import os
import sys
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
)
from etf_resonance.utils.indicators import sma
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

QUALIFIED_CSV = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'

W3_MIN_SIGNAL_SCORE = 90.0
W3_MIN_W1_GAIN = 0.60
W3_W2_RETRACE_RANGE = (0.30, 0.70)

W2_MIN_SIGNAL_SCORE = 90.0
W2_MIN_W1_GAIN = 0.60
W2_W2_RETRACE_RANGE = (0.30, 0.70)


@dataclass
class WaveSignal:
    """统一的波浪信号(兼容W2/W3)。"""
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


def score_w2_signal(wave: WaveCount, df: pd.DataFrame) -> Tuple[float, List[str]]:
    """计算W2浪结束点信号分(0-100)。

    W2策略核心：现价从L2反弹但未突破H1，即仍在第2浪调整末尾。
    最佳介入时点：W2回调30-70% + 现价反弹5-15% + 量能企稳。

    评分维度：
      1. 波浪结构有效性 (15分)
      2. W1涨幅高度 (20分): 60-200%最优
      3. W2回调深度 (20分): 30-70%为最佳介入时点(W2策略权重更高)
      4. 反弹确认 (20分): 现价从L2反弹5-15%为最佳(刚启动)
      5. 均线企稳 (10分): MA5开始拐头
      6. 量能底部 (10分): 缩量企稳后放量
      7. 空间 (5分): 距1.618目标价的空间
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


def score_w3_signal_v2(wave: WaveCount, df: pd.DataFrame) -> Tuple[float, List[str]]:
    """W3浪起点信号分(复用优化后的评分逻辑)。"""
    from etf_resonance.wave3_detector import score_wave3_signal
    return score_wave3_signal(wave, df)


def analyze_wave_signal(code: str, stock_df: pd.DataFrame, name: str, industry: str,
                        strategy: str = 'W3') -> Optional[WaveSignal]:
    """分析单只股票的波浪信号。"""
    if stock_df is None or len(stock_df) < 60:
        return None
    pivots = find_pivots(stock_df)
    if len(pivots) < 3:
        return None
    wave = detect_waves(pivots, stock_df)
    if wave is None or not wave.is_valid:
        return None

    current_price = float(stock_df['close'].values[-1])

    if strategy == 'W2':
        if current_price > wave.H1.price:
            return None
        if current_price < wave.L2.price * 1.02:
            return None
        score, reasons = score_w2_signal(wave, stock_df)
        wave_stage = 'W2'
    else:
        if current_price < wave.H1.price:
            return None
        score, reasons = score_w3_signal_v2(wave, stock_df)
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


def load_qualified_pool() -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """加载合格股池。"""
    df = pd.read_csv(QUALIFIED_CSV, dtype={'code': str})
    df['code'] = df['code'].str.zfill(6)
    df['ts_code'] = df['code'].apply(lambda x: x + '.SH' if x.startswith('6') else x + '.SZ')
    codes = df['ts_code'].tolist()
    name_map = dict(zip(df['ts_code'], df['name']))
    industry_map = dict(zip(df['ts_code'], df['industry']))
    return codes, name_map, industry_map


def run_backtest(strategy: str, stock_data: Dict, name_map: Dict, industry_map: Dict,
                 all_trade_dates: List[str], rebalance_dates: List[str],
                 bench_df: pd.DataFrame) -> Tuple[List[dict], List[dict]]:
    """运行单策略回测。返回(交易记录, 净值序列)。"""
    all_trades = []
    portfolio_values = []
    current_holdings = {}
    INIT_CAPITAL = 1_000_000
    cash = INIT_CAPITAL

    min_score = W2_MIN_SIGNAL_SCORE if strategy == 'W2' else W3_MIN_SIGNAL_SCORE

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
                                          industry_map.get(code, ''), strategy)
                if sig is None:
                    continue
                if sig.signal_score < min_score:
                    continue
                w1 = sig.wave.w1_gain
                w2 = sig.wave.w2_retrace
                if w1 < W3_MIN_W1_GAIN:
                    continue
                if not (W3_W2_RETRACE_RANGE[0] <= w2 <= W3_W2_RETRACE_RANGE[1]):
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
    """计算回测指标。"""
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
    """打印单策略报告。"""
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
print(f"波浪策略对比回测 | 合格股池: {QUALIFIED_CSV}")
print(f"回测期: 过去 {BACKTEST_MONTHS} 个月 | 调仓: {REBALANCE_DAYS}日 | 持仓: Top{HOLDING_TOPN}")
print(f"止损: {STOP_LOSS_PCT}% | 止盈: {TAKE_PROFIT_PCT}% | 大盘择时: {MARKET_FILTER}")
print("=" * 70, flush=True)

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 2)).strftime('%Y%m%d')
warmup_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 8)).strftime('%Y%m%d')

print(f"\n[1] 加载合格股池...")
codes, name_map, industry_map = load_qualified_pool()
print(f"  合格股池: {len(codes)} 只")

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

# ============== 运行W2策略回测 ==============
w2_trades, w2_pv = run_backtest('W2', stock_data, name_map, industry_map,
                                all_trade_dates, rebalance_dates, bench_df)

# ============== 运行W3策略回测 ==============
w3_trades, w3_pv = run_backtest('W3', stock_data, name_map, industry_map,
                                all_trade_dates, rebalance_dates, bench_df)

# ============== 计算指标 ==============
w2_metrics = calc_metrics(w2_trades, w2_pv, bench_ret)
w3_metrics = calc_metrics(w3_trades, w3_pv, bench_ret)

# ============== 打印报告 ==============
print_report('W2浪结束点', w2_trades, w2_metrics)
print_report('W3浪起点', w3_trades, w3_metrics)

# ============== 对比汇总 ==============
print(f"\n{'='*70}")
print(f"          ⚔️  W2浪 vs W3浪 策略对比")
print(f"{'='*70}")
print(f"  {'指标':<14}{'W2浪结束点':<16}{'W3浪起点':<16}{'差异':<14}")
print(f"  {'-'*60}")
print(f"  {'累计收益':<12}{w2_metrics['cum_ret']:+.2f}%{'':>5}{w3_metrics['cum_ret']:+.2f}%{'':>5}{w2_metrics['cum_ret']-w3_metrics['cum_ret']:+.2f}%")
print(f"  {'年化收益':<12}{w2_metrics['annual_ret']:+.2f}%{'':>5}{w3_metrics['annual_ret']:+.2f}%{'':>5}{w2_metrics['annual_ret']-w3_metrics['annual_ret']:+.2f}%")
print(f"  {'夏普比率':<12}{w2_metrics['sharpe']:.2f}{'':>9}{w3_metrics['sharpe']:.2f}{'':>9}{w2_metrics['sharpe']-w3_metrics['sharpe']:+.2f}")
print(f"  {'最大回撤':<12}{w2_metrics['max_dd']:.2f}%{'':>6}{w3_metrics['max_dd']:.2f}%{'':>6}{w2_metrics['max_dd']-w3_metrics['max_dd']:+.2f}%")
print(f"  {'胜率':<14}{w2_metrics['win_rate']:.1f}%{'':>6}{w3_metrics['win_rate']:.1f}%{'':>6}{w2_metrics['win_rate']-w3_metrics['win_rate']:+.1f}%")
print(f"  {'平均单笔':<12}{w2_metrics['avg_ret']:+.2f}%{'':>5}{w3_metrics['avg_ret']:+.2f}%{'':>5}{w2_metrics['avg_ret']-w3_metrics['avg_ret']:+.2f}%")
print(f"  {'盈亏比':<14}{w2_metrics['profit_factor']:.2f}{'':>9}{w3_metrics['profit_factor']:.2f}{'':>9}{w2_metrics['profit_factor']-w3_metrics['profit_factor']:+.2f}")
print(f"  {'交易次数':<12}{w2_metrics['total_trades']:<16}{w3_metrics['total_trades']:<16}{w2_metrics['total_trades']-w3_metrics['total_trades']:+}")
print(f"  {'止损次数':<12}{w2_metrics['stop_loss']:<16}{w3_metrics['stop_loss']:<16}")
print(f"  {'止盈次数':<12}{w2_metrics['take_profit']:<16}{w3_metrics['take_profit']:<16}")
print(f"  {'超额收益':<12}{w2_metrics['excess_ret']:+.2f}%{'':>5}{w3_metrics['excess_ret']:+.2f}%")
print(f"{'='*70}")

# ============== 按退出原因对比胜率 ==============
print(f"\n📊 按退出原因对比:")
for stg_name, trades in [('W2浪', w2_trades), ('W3浪', w3_trades)]:
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        continue
    print(f"\n  [{stg_name}]")
    for reason in ['调仓换股', f'止损{STOP_LOSS_PCT}%', f'止盈{TAKE_PROFIT_PCT}%', '大盘空仓']:
        sub = tdf[tdf['exit_reason'] == reason]
        if len(sub) == 0:
            continue
        wr = (sub['return_pct'] > 0).mean() * 100
        avg = sub['return_pct'].mean()
        print(f"    {reason}: {len(sub)}笔 | 胜率{wr:.1f}% | 均收益{avg:+.2f}%")

# ============== 按W1涨幅分组对比 ==============
print(f"\n📊 按W1涨幅分组对比胜率:")
print(f"  {'W1区间':<14}{'W2胜率':<12}{'W3胜率':<12}{'W2均收益':<12}{'W3均收益':<12}")
print(f"  {'-'*62}")
w1_bins = [(0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 3.0)]
for lo, hi in w1_bins:
    w2_sub = pd.DataFrame(w2_trades)
    w3_sub = pd.DataFrame(w3_trades)
    w2_s = w2_sub[(w2_sub['w1_gain'] >= lo) & (w2_sub['w1_gain'] < hi)]
    w3_s = w3_sub[(w3_sub['w1_gain'] >= lo) & (w3_sub['w1_gain'] < hi)]
    w2_wr = f"{(w2_s['return_pct']>0).mean()*100:.1f}%({len(w2_s)})" if len(w2_s)>0 else '-'
    w3_wr = f"{(w3_s['return_pct']>0).mean()*100:.1f}%({len(w3_s)})" if len(w3_s)>0 else '-'
    w2_avg = f"{w2_s['return_pct'].mean():+.2f}%" if len(w2_s)>0 else '-'
    w3_avg = f"{w3_s['return_pct'].mean():+.2f}%" if len(w3_s)>0 else '-'
    print(f"  {lo*100:.0f}-{hi*100:.0f}%{'':>6}{w2_wr:<12}{w3_wr:<12}{w2_avg:<12}{w3_avg:<12}")

# ============== 保存结果 ==============
output_dir = r'd:\mystock\solo\etf_resonance\output'
all_trades_df = pd.DataFrame(w2_trades + w3_trades)
all_trades_df.to_csv(os.path.join(output_dir, 'backtest_wave_compare_trades.csv'),
                     index=False, encoding='utf-8-sig')
print(f"\n[已保存] 交易明细: backtest_wave_compare_trades.csv")
print("=" * 70)
