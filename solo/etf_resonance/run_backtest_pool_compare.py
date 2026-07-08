"""W3浪起点策略 - 双股池A/B对比回测

在同一回测框架下，对比两个股池的W3浪策略表现：
  A. 合格股池: D:\\mystock\\solo\\report_daily\\bull_stocks_qualified.csv (1145只)
  B. ETF成份股池: 35个行业ETF的前50只成份股 (1049只)

对比维度：
  1. 收益/夏普/回撤/胜率
  2. 候选股数量（信号丰富度）
  3. 仓位利用率
  4. 按W1涨幅分组的胜率
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
from dateutil.relativedelta import relativedelta

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from multi_factor_picker.data_fetcher import DataFetcher
from etf_resonance.wave3_detector import (
    find_pivots, detect_waves, score_wave3_signal,
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

REBALANCE_DAYS = 5
HOLDING_TOPN = 5
BACKTEST_MONTHS = 6
BENCHMARK = '000300.SH'
STOP_LOSS_PCT = -8.0
TAKE_PROFIT_PCT = 20.0
MARKET_FILTER = True
MARKET_MA = 20

MIN_SIGNAL_SCORE = 90.0
MIN_W1_GAIN = 0.60
W2_RETRACE_RANGE = (0.30, 0.70)

QUALIFIED_CSV = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'

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


def load_qualified_pool():
    df = pd.read_csv(QUALIFIED_CSV, dtype={'code': str})
    df['code'] = df['code'].str.zfill(6)
    df['ts_code'] = df['code'].apply(lambda x: x + '.SH' if x.startswith('6') else x + '.SZ')
    codes = df['ts_code'].tolist()
    name_map = dict(zip(df['ts_code'], df['name']))
    industry_map = dict(zip(df['ts_code'], df['industry']))
    return codes, name_map, industry_map


def load_etf_pool():
    all_etf_constituents = {}
    json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            all_etf_constituents = json.load(f)

    missing = [c for c in ETF_THEME_MAP if c not in all_etf_constituents]
    for etf_code in missing:
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

    try:
        stock_basic = dfetcher.get_stock_list(list_status='L')
        name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
        industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
    except Exception:
        name_map = {}
        industry_map = {}
    return sorted(all_stocks), name_map, industry_map


def analyze_w3_signal(code, stock_df, name, industry):
    if stock_df is None or len(stock_df) < 60:
        return None
    pivots = find_pivots(stock_df)
    if len(pivots) < 3:
        return None
    wave = detect_waves(pivots, stock_df)
    if wave is None or not wave.is_valid:
        return None
    current_price = float(stock_df['close'].values[-1])
    if current_price < wave.H1.price:
        return None
    if wave.w1_gain < MIN_W1_GAIN:
        return None
    if not (W2_RETRACE_RANGE[0] <= wave.w2_retrace <= W2_RETRACE_RANGE[1]):
        return None
    score, reasons = score_wave3_signal(wave, stock_df, name)
    if score < MIN_SIGNAL_SCORE:
        return None
    return {
        'code': code, 'signal_score': score, 'current_price': current_price,
        'w1_gain': wave.w1_gain, 'w2_retrace': wave.w2_retrace,
        'w3_target': wave.w3_target_price, 'name': name, 'industry': industry,
    }


def run_w3_backtest(pool_name, codes, name_map, industry_map, stock_data,
                    all_trade_dates, rebalance_dates, bench_df, bench_ret):
    all_trades = []
    portfolio_values = []
    current_holdings = {}
    INIT_CAPITAL = 1_000_000
    cash = INIT_CAPITAL
    candidate_counts = []
    position_utilizations = []

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

    print(f"\n[3] 开始{pool_name}回测...", flush=True)

    for rb_idx, rb_date in enumerate(rebalance_dates):
        if (rb_idx + 1) % 5 == 0:
            print(f"  {pool_name}进度: {rb_idx+1}/{len(rebalance_dates)}", flush=True)

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
                    'exit_reason': '大盘空仓', 'pool': pool_name,
                })
                del current_holdings[code]
            portfolio_values.append({'date': rb_date, 'equity': cash})
            continue

        stock_slice = slice_stock_data(rb_date)
        candidates = []
        for code, df in stock_slice.items():
            try:
                sig = analyze_w3_signal(code, df, name_map.get(code, ''), industry_map.get(code, ''))
                if sig is not None:
                    candidates.append(sig)
            except Exception:
                continue

        candidates.sort(key=lambda x: -x['signal_score'])
        top_picks = candidates[:HOLDING_TOPN]
        top_codes = {p['code'] for p in top_picks}
        candidate_counts.append({'date': rb_date, 'candidates': len(candidates),
                                 'picked': len(top_picks)})

        if not top_picks:
            portfolio_values.append({'date': rb_date, 'equity': cash + sum(
                h['shares'] * (get_price(c, rb_date) or h['entry_price'])
                for c, h in current_holdings.items())})
            position_utilizations.append({'date': rb_date, 'utilization': 0})
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
                        'exit_reason': f'止损{STOP_LOSS_PCT}%', 'pool': pool_name,
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
                        'exit_reason': f'止盈{TAKE_PROFIT_PCT}%', 'pool': pool_name,
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
                    'exit_reason': '调仓换股', 'pool': pool_name,
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
                'stock_name': pick.get('name', ''), 'industry': pick.get('industry', ''),
            }

        total_equity = cash
        for code, pos in current_holdings.items():
            cur_price = get_price(code, rb_date) or pos['entry_price']
            total_equity += pos['shares'] * cur_price
        portfolio_values.append({'date': rb_date, 'equity': total_equity})
        position_utilizations.append({'date': rb_date,
                                      'utilization': len(current_holdings) / HOLDING_TOPN * 100})

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
            'exit_reason': '回测结束', 'pool': pool_name,
        })
        del current_holdings[code]

    return all_trades, portfolio_values, candidate_counts, position_utilizations


def calc_metrics(trades, portfolio_values, bench_ret, INIT_CAPITAL=1_000_000):
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

    stop_loss = len(trades_df[trades_df['exit_reason'].str.startswith('止损', na=False)])
    take_profit = len(trades_df[trades_df['exit_reason'].str.startswith('止盈', na=False)])
    rotate = len(trades_df[trades_df['exit_reason'] == '调仓换股'])
    win_avg = trades_df[trades_df['return_pct'] > 0]['return_pct'].mean() if len(winning) > 0 else 0
    lose_avg = trades_df[trades_df['return_pct'] <= 0]['return_pct'].mean() if len(trades_df[trades_df['return_pct']<=0])>0 else 0
    pf = abs(win_avg * len(winning) / (lose_avg * len(trades_df[trades_df['return_pct']<=0]) + 1e-6))

    return {
        'cum_ret': cum_ret, 'annual_ret': annual_ret, 'sharpe': sharpe, 'max_dd': max_dd,
        'win_rate': win_rate, 'avg_ret': avg_ret, 'avg_hold': avg_hold,
        'total_trades': total_trades, 'stop_loss': stop_loss,
        'take_profit': take_profit, 'rotate': rotate,
        'win_avg': win_avg, 'lose_avg': lose_avg, 'profit_factor': pf,
        'bench_ret': bench_ret, 'excess_ret': cum_ret - bench_ret,
    }


# ============== 主流程 ==============
print("=" * 70)
print(f"W3浪起点策略 - 双股池A/B对比回测")
print(f"回测期: 过去 {BACKTEST_MONTHS} 个月 | 调仓: {REBALANCE_DAYS}日 | 持仓: Top{HOLDING_TOPN}")
print(f"硬过滤: 信号分≥{MIN_SIGNAL_SCORE} | W1≥{MIN_W1_GAIN*100:.0f}% | W2回调{W2_RETRACE_RANGE[0]*100:.0f}-{W2_RETRACE_RANGE[1]*100:.0f}%")
print("=" * 70, flush=True)

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 2)).strftime('%Y%m%d')
warmup_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 8)).strftime('%Y%m%d')

print(f"\n[1] 加载两个股池...")
q_codes, q_names, q_industries = load_qualified_pool()
e_codes, e_names, e_industries = load_etf_pool()
print(f"  A. 合格股池: {len(q_codes)} 只")
print(f"  B. ETF成份股池: {len(e_codes)} 只")

all_codes = sorted(set(q_codes + e_codes))
print(f"  合并去重: {len(all_codes)} 只")

print(f"\n[2] 下载日线数据 {warmup_date} ~ {end_date}")
stock_data = {}
for i, code in enumerate(all_codes):
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=warmup_date, end_date=end_date)
        if df is not None and not df.empty:
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    if (i + 1) % 200 == 0:
        print(f"  进度: {i+1}/{len(all_codes)}", flush=True)
print(f"  成功: {len(stock_data)}/{len(all_codes)}")

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

q_data = {c: stock_data[c] for c in q_codes if c in stock_data}
e_data = {c: stock_data[c] for c in e_codes if c in stock_data}

q_trades, q_pv, q_cand, q_util = run_w3_backtest(
    '合格股池', q_codes, q_names, q_industries, q_data,
    all_trade_dates, rebalance_dates, bench_df, bench_ret)

e_trades, e_pv, e_cand, e_util = run_w3_backtest(
    'ETF股池', e_codes, e_names, e_industries, e_data,
    all_trade_dates, rebalance_dates, bench_df, bench_ret)

q_m = calc_metrics(q_trades, q_pv, bench_ret)
e_m = calc_metrics(e_trades, e_pv, bench_ret)

# ============== 对比报告 ==============
print(f"\n{'='*70}")
print(f"          ⚔️  双股池 A/B 对比报告")
print(f"{'='*70}")
print(f"  {'指标':<14}{'A.合格股池':<16}{'B.ETF股池':<16}{'差异(A-B)':<14}")
print(f"  {'-'*60}")
print(f"  {'累计收益':<12}{q_m['cum_ret']:+.2f}%{'':>5}{e_m['cum_ret']:+.2f}%{'':>5}{q_m['cum_ret']-e_m['cum_ret']:+.2f}%")
print(f"  {'年化收益':<12}{q_m['annual_ret']:+.2f}%{'':>5}{e_m['annual_ret']:+.2f}%{'':>5}{q_m['annual_ret']-e_m['annual_ret']:+.2f}%")
print(f"  {'夏普比率':<12}{q_m['sharpe']:.2f}{'':>9}{e_m['sharpe']:.2f}{'':>9}{q_m['sharpe']-e_m['sharpe']:+.2f}")
print(f"  {'最大回撤':<12}{q_m['max_dd']:.2f}%{'':>6}{e_m['max_dd']:.2f}%{'':>6}{q_m['max_dd']-e_m['max_dd']:+.2f}%")
print(f"  {'胜率':<14}{q_m['win_rate']:.1f}%{'':>6}{e_m['win_rate']:.1f}%{'':>6}{q_m['win_rate']-e_m['win_rate']:+.1f}%")
print(f"  {'平均单笔':<12}{q_m['avg_ret']:+.2f}%{'':>5}{e_m['avg_ret']:+.2f}%{'':>5}{q_m['avg_ret']-e_m['avg_ret']:+.2f}%")
print(f"  {'盈亏比':<14}{q_m['profit_factor']:.2f}{'':>9}{e_m['profit_factor']:.2f}{'':>9}{q_m['profit_factor']-e_m['profit_factor']:+.2f}")
print(f"  {'交易次数':<12}{q_m['total_trades']:<16}{e_m['total_trades']:<16}{q_m['total_trades']-e_m['total_trades']:+}")
print(f"  {'止损次数':<12}{q_m['stop_loss']:<16}{e_m['stop_loss']:<16}")
print(f"  {'止盈次数':<12}{q_m['take_profit']:<16}{e_m['take_profit']:<16}")
print(f"  {'超额收益':<12}{q_m['excess_ret']:+.2f}%{'':>5}{e_m['excess_ret']:+.2f}%")
print(f"{'='*70}")

# ============== 候选股数量与仓位利用率对比 ==============
print(f"\n📊 候选股数量与仓位利用率对比:")
q_cand_df = pd.DataFrame(q_cand)
e_cand_df = pd.DataFrame(e_cand)
q_util_df = pd.DataFrame(q_util)
e_util_df = pd.DataFrame(e_util)
print(f"  {'指标':<16}{'A.合格股池':<16}{'B.ETF股池':<16}")
print(f"  {'-'*48}")
print(f"  {'平均候选股数':<14}{q_cand_df['candidates'].mean():.1f}{'':>8}{e_cand_df['candidates'].mean():.1f}")
print(f"  {'最大候选股数':<14}{q_cand_df['candidates'].max()}{'':>11}{e_cand_df['candidates'].max()}")
print(f"  {'0候选调仓日':<14}{(q_cand_df['candidates']==0).sum()}{'':>11}{(e_cand_df['candidates']==0).sum()}")
print(f"  {'平均仓位利用率':<14}{q_util_df['utilization'].mean():.1f}%{'':>7}{e_util_df['utilization'].mean():.1f}%")

# ============== 按W1涨幅分组对比胜率 ==============
print(f"\n📊 按W1涨幅分组对比胜率:")
print(f"  {'W1区间':<14}{'A.合格股池':<16}{'B.ETF股池':<16}{'A均收益':<12}{'B均收益':<12}")
print(f"  {'-'*70}")
w1_bins = [(0.6, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 3.0)]
for lo, hi in w1_bins:
    q_df = pd.DataFrame(q_trades)
    e_df = pd.DataFrame(e_trades)
    q_s = q_df[(q_df['w1_gain'] >= lo) & (q_df['w1_gain'] < hi)]
    e_s = e_df[(e_df['w1_gain'] >= lo) & (e_df['w1_gain'] < hi)]
    q_wr = f"{(q_s['return_pct']>0).mean()*100:.1f}%({len(q_s)})" if len(q_s)>0 else '-'
    e_wr = f"{(e_s['return_pct']>0).mean()*100:.1f}%({len(e_s)})" if len(e_s)>0 else '-'
    q_avg = f"{q_s['return_pct'].mean():+.2f}%" if len(q_s)>0 else '-'
    e_avg = f"{e_s['return_pct'].mean():+.2f}%" if len(e_s)>0 else '-'
    print(f"  {lo*100:.0f}-{hi*100:.0f}%{'':>6}{q_wr:<16}{e_wr:<16}{q_avg:<12}{e_avg:<12}")

# ============== 保存结果 ==============
output_dir = r'd:\mystock\solo\etf_resonance\output'
all_trades_df = pd.DataFrame(q_trades + e_trades)
all_trades_df.to_csv(os.path.join(output_dir, 'backtest_pool_compare_trades.csv'),
                     index=False, encoding='utf-8-sig')
print(f"\n[已保存] 交易明细: backtest_pool_compare_trades.csv")
print("=" * 70)
