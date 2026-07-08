"""波浪理论第3浪选股策略回测 (Elliott Wave 3 Strategy Backtest)

策略核心：
  在每个调仓日，扫描ETF成份股池，找出处于第3浪起点且波浪结构有效的股票。
  选择信号分最高的TopN只股票等权持仓，加入止损/止盈和大盘择时。

对比补涨扩散策略：
  - 补涨策略：ETF趋势形成 → 找成份股中相对滞涨的（追补涨）
  - 波浪策略：识别已完成1浪建仓+2浪洗盘 → 第3浪主升浪启动的股票
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
    find_pivots, detect_waves, score_wave3_signal, Wave3Signal,
    W1_MIN_GAIN, W2_RETRACE_MIN, W2_RETRACE_MAX, W3_RATIO_TARGET, PIVOT_WINDOW,
)
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

MIN_SIGNAL_SCORE = 90.0
MIN_W1_GAIN = 0.60
MAX_W1_GAIN = 9.99
W2_RETRACE_RANGE = (0.30, 0.70)

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


def score_stock_wave3(code: str, stock_df: pd.DataFrame, name: str = '', industry: str = '') -> Optional[Wave3Signal]:
    """对单只股票做波浪分析并返回第3浪信号(回测用,数据已切片好)。"""
    if stock_df is None or len(stock_df) < 60:
        return None
    pivots = find_pivots(stock_df)
    if len(pivots) < 3:
        return None
    wave = detect_waves(pivots, stock_df)
    if wave is None or not wave.is_valid:
        return None

    current_price = float(stock_df['close'].values[-1])
    w3_progress = 0.0
    if wave.H3 is not None:
        w3_progress = (current_price - wave.L2.price) / max(wave.H3.price - wave.L2.price, 1e-6) * 100
    elif current_price > wave.L2.price:
        w3_len_target = (wave.H1.price - wave.L0.price) * W3_RATIO_TARGET
        w3_progress = (current_price - wave.L2.price) / max(w3_len_target, 1e-6) * 100

    dist_to_target = (wave.w3_target_price - current_price) / max(current_price, 1e-6) * 100
    score, reasons = score_wave3_signal(wave, stock_df, name)

    return Wave3Signal(
        ts_code=code, name=name, industry=industry,
        wave=wave, current_price=current_price,
        dist_to_w3_target=dist_to_target,
        w3_progress=w3_progress,
        signal_score=score,
        signal_reasons=reasons,
    )


# ============== 1. 加载历史数据 ==============
print("=" * 70)
print(f"波浪理论第3浪选股策略回测 | 回测期: 过去 {BACKTEST_MONTHS} 个月")
print(f"调仓: {REBALANCE_DAYS}个交易日 | 持仓: Top {HOLDING_TOPN}")
print(f"止损: {STOP_LOSS_PCT}% | 止盈: {TAKE_PROFIT_PCT}% | 大盘择时: {MARKET_FILTER}")
print(f"硬过滤: 信号分≥{MIN_SIGNAL_SCORE} | W1涨幅{MIN_W1_GAIN*100:.0f}-{MAX_W1_GAIN*100:.0f}% | W2回调{W2_RETRACE_RANGE[0]*100:.0f}-{W2_RETRACE_RANGE[1]*100:.0f}%")
print("=" * 70, flush=True)

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 2)).strftime('%Y%m%d')
warmup_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 8)).strftime('%Y%m%d')

print(f"\n[1] 加载历史数据 {warmup_date} ~ {end_date}")

all_etf_constituents = {}
json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        all_etf_constituents = json.load(f)
print(f"  本地成份股JSON: 覆盖 {len(all_etf_constituents)} 只ETF")

missing_etfs = [c for c in ETF_THEME_MAP if c not in all_etf_constituents]
if missing_etfs:
    print(f"  JSON缺失 {len(missing_etfs)} 只，回退到 DataFetcher.get_etf_cons() 补充...")
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

print(f"  成份股: {len(all_stocks)} 只")

print("\n  下载股票日线...")
stock_data = {}
for i, code in enumerate(sorted(all_stocks)):
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=warmup_date, end_date=end_date)
        if df is not None and not df.empty:
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    if (i + 1) % 200 == 0:
        print(f"    进度: {i+1}/{len(all_stocks)}", flush=True)
print(f"  成功: {len(stock_data)}/{len(all_stocks)}")

print("\n  获取股票名称...")
try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
    industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
except Exception:
    name_map = {}
    industry_map = {}

print("\n  下载基准指数...")
bench_df = dfetcher.get_index_daily(ts_code=BENCHMARK, start_date=start_date, end_date=end_date)
if bench_df is not None and not bench_df.empty:
    bench_df = bench_df.sort_values('trade_date').reset_index(drop=True)
    print(f"  基准: {len(bench_df)} 条")

# ============== 2. 生成调仓日 ==============
print("\n[2] 生成调仓日...")
all_trade_dates = sorted(bench_df['trade_date'].tolist()) if bench_df is not None else []
backtest_start = (datetime.now() - relativedelta(months=BACKTEST_MONTHS)).strftime('%Y%m%d')
trade_dates_bt = [d for d in all_trade_dates if d >= backtest_start]
rebalance_dates = trade_dates_bt[::REBALANCE_DAYS]
print(f"  调仓日数: {len(rebalance_dates)}")

# ============== 3. 回测主循环 ==============
print("\n[3] 开始回测...", flush=True)

all_trades = []
portfolio_values = []
current_holdings = {}
INIT_CAPITAL = 1_000_000
cash = INIT_CAPITAL


def slice_stock_data(data_dict, as_of_date):
    out = {}
    for code, df in data_dict.items():
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


for rb_idx, rb_date in enumerate(rebalance_dates):
    print(f"\n  --- 调仓 {rb_idx+1}/{len(rebalance_dates)}: {rb_date} ---", flush=True)

    bullish = is_market_bullish(rb_date)
    if not bullish:
        print(f"    ⚠️ 大盘弱势（沪深300<{MARKET_MA}MA），空仓观望")
        for code in list(current_holdings.keys()):
            pos = current_holdings[code]
            exit_price = get_price(code, rb_date) or pos['entry_price']
            ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
            cash += pos['shares'] * exit_price * (1 - 0.0003)
            hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
            all_trades.append({
                'entry_date': pos['entry_date'], 'exit_date': rb_date, 'code': code,
                'stock_name': pos.get('stock_name', name_map.get(code, '')),
                'industry': pos.get('industry', industry_map.get(code, '')),
                'entry_price': pos['entry_price'], 'exit_price': exit_price,
                'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                'signal_score': pos.get('signal_score', 0),
                'w1_gain': pos.get('w1_gain', 0),
                'w2_retrace': pos.get('w2_retrace', 0),
                'exit_reason': '大盘空仓',
            })
            del current_holdings[code]
        portfolio_values.append({'date': rb_date, 'equity': cash})
        continue

    stock_slice = slice_stock_data(stock_data, rb_date)
    if len(stock_slice) < 30:
        portfolio_values.append({'date': rb_date, 'equity': cash + sum(
            h['shares'] * (get_price(c, rb_date) or h['entry_price'])
            for c, h in current_holdings.items())})
        continue

    # 波浪扫描：对所有成份股做第3浪信号分析
    candidates = []
    for code, df in stock_slice.items():
        try:
            sig = score_stock_wave3(code, df, name_map.get(code, ''), industry_map.get(code, ''))
            if sig is None:
                continue
            w1 = sig.wave.w1_gain
            w2 = sig.wave.w2_retrace
            if sig.signal_score < MIN_SIGNAL_SCORE:
                continue
            if w1 < MIN_W1_GAIN:
                continue
            if w1 > MAX_W1_GAIN:
                continue
            if not (W2_RETRACE_RANGE[0] <= w2 <= W2_RETRACE_RANGE[1]):
                continue
            candidates.append({
                'code': code,
                'signal_score': sig.signal_score,
                'current_price': sig.current_price,
                'w1_gain': w1,
                'w2_retrace': w2,
                'w3_target': sig.wave.w3_target_price,
                'dist_to_target': sig.dist_to_w3_target,
                'w3_progress': sig.w3_progress,
                'wave': sig.wave,
                'reasons': sig.signal_reasons,
            })
        except Exception:
            continue

    candidates.sort(key=lambda x: -x['signal_score'])
    top_picks = candidates[:HOLDING_TOPN]
    top_codes = {p['code'] for p in top_picks}

    print(f"    波浪候选: {len(candidates)} 只 | Top{HOLDING_TOPN}: "
          + ', '.join(f"{p['code']}({p['signal_score']:.0f})" for p in top_picks[:3])
          + ('...' if len(top_picks) > 3 else ''))

    if not top_picks:
        portfolio_values.append({'date': rb_date, 'equity': cash + sum(
            h['shares'] * (get_price(c, rb_date) or h['entry_price'])
            for c, h in current_holdings.items())})
        continue

    # 止损止盈检查
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
                    'stock_name': pos.get('stock_name', name_map.get(code, '')),
                    'industry': pos.get('industry', industry_map.get(code, '')),
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                    'signal_score': pos.get('signal_score', 0),
                    'w1_gain': pos.get('w1_gain', 0),
                    'w2_retrace': pos.get('w2_retrace', 0),
                    'exit_reason': f'止损{STOP_LOSS_PCT}%',
                })
                del current_holdings[code]
                triggered = True
                print(f"    🛑 {code} 止损: {ret_pct:.2f}%")
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
                    'stock_name': pos.get('stock_name', name_map.get(code, '')),
                    'industry': pos.get('industry', industry_map.get(code, '')),
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                    'signal_score': pos.get('signal_score', 0),
                    'w1_gain': pos.get('w1_gain', 0),
                    'w2_retrace': pos.get('w2_retrace', 0),
                    'exit_reason': f'止盈{TAKE_PROFIT_PCT}%',
                })
                del current_holdings[code]
                triggered = True
                print(f"    💰 {code} 止盈: +{ret_pct:.2f}%")
                break

        if not triggered and code not in top_codes:
            exit_price = get_price(code, rb_date) or pos['entry_price']
            ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
            cash += pos['shares'] * exit_price * (1 - 0.0003)
            hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
            all_trades.append({
                'entry_date': pos['entry_date'], 'exit_date': rb_date, 'code': code,
                'stock_name': pos.get('stock_name', name_map.get(code, '')),
                'industry': pos.get('industry', industry_map.get(code, '')),
                'entry_price': pos['entry_price'], 'exit_price': exit_price,
                'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                'signal_score': pos.get('signal_score', 0),
                'w1_gain': pos.get('w1_gain', 0),
                'w2_retrace': pos.get('w2_retrace', 0),
                'exit_reason': '调仓换股',
            })
            del current_holdings[code]

    # 开仓新增
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
            'entry_date': rb_date,
            'entry_price': entry_price,
            'shares': shares,
            'signal_score': pick['signal_score'],
            'w1_gain': pick['w1_gain'],
            'w2_retrace': pick['w2_retrace'],
            'w3_target': pick['w3_target'],
            'stock_name': name_map.get(code, ''),
            'industry': industry_map.get(code, ''),
        }

    total_equity = cash
    for code, pos in current_holdings.items():
        cur_price = get_price(code, rb_date) or pos['entry_price']
        total_equity += pos['shares'] * cur_price
    portfolio_values.append({'date': rb_date, 'equity': total_equity})
    print(f"    组合净值: {total_equity:,.0f} | 持仓: {len(current_holdings)} 只 | 现金: {cash:,.0f}")

# ============== 4. 平仓剩余 ==============
print("\n[4] 平仓剩余持仓...", flush=True)
final_date = all_trade_dates[-1] if all_trade_dates else end_date
for code in list(current_holdings.keys()):
    pos = current_holdings[code]
    exit_price = get_price(code, final_date) or pos['entry_price']
    ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
    cash += pos['shares'] * exit_price * (1 - 0.0003)
    hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= final_date)
    all_trades.append({
        'entry_date': pos['entry_date'], 'exit_date': final_date, 'code': code,
        'stock_name': pos.get('stock_name', name_map.get(code, '')),
        'industry': pos.get('industry', industry_map.get(code, '')),
        'entry_price': pos['entry_price'], 'exit_price': exit_price,
        'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
        'signal_score': pos.get('signal_score', 0),
        'w1_gain': pos.get('w1_gain', 0),
        'w2_retrace': pos.get('w2_retrace', 0),
        'exit_reason': '回测结束',
    })
    del current_holdings[code]

# ============== 5. 计算指标 ==============
print("\n" + "=" * 70)
print("          📊 波浪理论第3浪选股策略回测报告")
print("=" * 70)

if not all_trades:
    print("无交易记录！")
    sys.exit(1)

trades_df = pd.DataFrame(all_trades)
trades_df.to_csv(r'd:\mystock\solo\etf_resonance\output\backtest_wave3_trades.csv',
                 index=False, encoding='utf-8-sig')

total_trades = len(trades_df)
winning = trades_df[trades_df['return_pct'] > 0]
win_rate = len(winning) / total_trades * 100
avg_ret = trades_df['return_pct'].mean()
avg_hold = trades_df['hold_days'].mean()

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
else:
    cum_ret = 0; annual_ret = 0; sharpe = 0; max_dd = 0

if bench_df is not None and not bench_df.empty:
    bench_bt = bench_df[bench_df['trade_date'] >= backtest_start]
    bench_ret = (bench_bt['close'].iloc[-1] / bench_bt['close'].iloc[0] - 1) * 100 if not bench_bt.empty else 0
else:
    bench_ret = 0

stop_loss_trades = trades_df[trades_df['exit_reason'].str.startswith('止损', na=False)]
take_profit_trades = trades_df[trades_df['exit_reason'].str.startswith('止盈', na=False)]
rotate_trades = trades_df[trades_df['exit_reason'] == '调仓换股']

print(f"  回测区间:     {backtest_start} ~ {final_date}")
print(f"  调仓周期:     {REBALANCE_DAYS} 个交易日")
print(f"  持仓数量:     Top {HOLDING_TOPN}")
print(f"  初始资金:     {INIT_CAPITAL:,}")
print("-" * 70)
print(f"  📈 累计收益:   {cum_ret:+.2f}%")
print(f"  📅 年化收益:   {annual_ret:+.2f}%")
print(f"  📊 夏普比率:   {sharpe:.2f}")
print(f"  📉 最大回撤:   {max_dd:.2f}%")
print(f"  🎯 胜率:       {win_rate:.1f}% ({len(winning)}/{total_trades})")
print(f"  💰 平均单笔:   {avg_ret:+.2f}%")
print(f"  ⏱️  平均持仓:   {avg_hold:.1f} 天")
print(f"  🔄 交易次数:   {total_trades}")
print(f"    止损: {len(stop_loss_trades)} | 止盈: {len(take_profit_trades)} | 调仓: {len(rotate_trades)}")
print("-" * 70)
print(f"  📌 基准 ({BENCHMARK}):  {bench_ret:+.2f}%")
print(f"  🆚 超额收益:   {cum_ret - bench_ret:+.2f}%")
print("=" * 70)

_cols = ['code', 'stock_name', 'industry', 'entry_date', 'exit_date',
         'return_pct', 'hold_days', 'signal_score', 'w1_gain', 'w2_retrace', 'exit_reason']
_cols = [c for c in _cols if c in trades_df.columns]

print("\n📈 Top 5 盈利交易:")
top_win = trades_df.nlargest(5, 'return_pct')[_cols]
for _, r in top_win.iterrows():
    print(f"  {r['code']} {r.get('stock_name','')} | {r.get('industry','')} | "
          f"{r['entry_date']}→{r['exit_date']} | {r['return_pct']:+.2f}% | 持{r['hold_days']}天 | "
          f"信号分{r.get('signal_score',0):.1f} | W1涨{r.get('w1_gain',0)*100:.0f}% | {r['exit_reason']}")

print("\n📉 Top 5 亏损交易:")
top_lose = trades_df.nsmallest(5, 'return_pct')[_cols]
for _, r in top_lose.iterrows():
    print(f"  {r['code']} {r.get('stock_name','')} | {r.get('industry','')} | "
          f"{r['entry_date']}→{r['exit_date']} | {r['return_pct']:+.2f}% | 持{r['hold_days']}天 | "
          f"信号分{r.get('signal_score',0):.1f} | W1涨{r.get('w1_gain',0)*100:.0f}% | {r['exit_reason']}")

print("\n📊 按信号评分分组的胜率:")
trades_df['score_bin'] = pd.cut(trades_df['signal_score'], bins=[0, 60, 70, 80, 90, 101],
                                labels=['<60', '60-70', '70-80', '80-90', '90+'])
grp = trades_df.groupby('score_bin', observed=True).agg(
    trades=('return_pct', 'count'),
    win_rate=('return_pct', lambda x: (x > 0).sum() / len(x) * 100),
    avg_ret=('return_pct', 'mean')
).round(2)
print(grp.to_string())

print("\n📊 按退出原因分组:")
grp2 = trades_df.groupby('exit_reason').agg(
    trades=('return_pct', 'count'),
    win_rate=('return_pct', lambda x: (x > 0).sum() / len(x) * 100),
    avg_ret=('return_pct', 'mean')
).round(2)
print(grp2.to_string())

print("\n📊 按第1浪涨幅分组:")
trades_df['w1_bin'] = pd.cut(trades_df['w1_gain'], bins=[0, 0.5, 1.0, 1.5, 10],
                             labels=['<50%', '50-100%', '100-150%', '150%+'])
grp3 = trades_df.groupby('w1_bin', observed=True).agg(
    trades=('return_pct', 'count'),
    win_rate=('return_pct', lambda x: (x > 0).sum() / len(x) * 100),
    avg_ret=('return_pct', 'mean')
).round(2)
print(grp3.to_string())

if portfolio_values:
    pv_df.to_csv(r'd:\mystock\solo\etf_resonance\output\backtest_wave3_equity.csv',
                 index=False, encoding='utf-8-sig')
    print(f"\n[已保存] 净值曲线: backtest_wave3_equity.csv")
print(f"[已保存] 交易明细: backtest_wave3_trades.csv")
print("=" * 70)
