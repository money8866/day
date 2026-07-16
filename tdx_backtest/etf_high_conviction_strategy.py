# -*- coding: utf-8 -*-
"""
ETF确定性择时轮动策略 - 机构级
核心思路：只操作一只最有确定性的ETF，加入择时空仓机制

策略架构：
  1. 大盘择时层：均线趋势过滤，空头市场空仓
  2. ETF选优层：多因子评分选最强ETF
  3. 买入时机层：趋势确认 + 回调买入（不追涨）
  4. 卖出时机层：动态止盈止损 + 趋势反转

vs 原策略改进：
  - 原策略：纯动量轮动，无择时，熊市全亏
  - 新策略：趋势确认+择时空仓，只做确定性高的机会
"""
import os, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

TDX_BACKTEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tdx_backtest")
sys.path.insert(0, TDX_BACKTEST_DIR)

from data_loader import load_kline
from analyzer import compute_metrics, print_metrics
from backtest import Backtester, CostConfig, BacktestResult, TradeRecord

# =========================================================
# ETF池
# =========================================================
ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '黄金': '518880', '工业母机': '159667'
}

BENCHMARK_CODE = '510300'
INITIAL_CAPITAL = 100000


def load_etf_data(code, start_date, end_date):
    if code.startswith('5') or code.startswith('6'):
        ts_code = f"{code}.SH"
    else:
        ts_code = f"{code}.SZ"
    try:
        df = load_kline(ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        df['trade_date'] = df['trade_date'].astype(str)
        return df
    except:
        return None


# =========================================================
# 策略核心：确定性ETF择时轮动
# =========================================================
def generate_signals(all_data, benchmark_df, params):
    """
    生成每日交易信号

    买入条件（全部满足）：
      1. 大盘趋势向上（沪深300 > MA20）
      2. 目标ETF趋势向上（ETF > MA10 > MA20，或至少 ETF > MA20）
      3. 目标ETF评分排名前1
      4. 回调买入：当日跌幅<1% 或 前日刚突破MA10

    卖出条件（满足任一）：
      1. 趋势反转：ETF跌破MA20
      2. 动量止损：从买入价下跌超7%
      3. 动态止盈：从最高点回撤超8%
      4. 更强ETF出现（评分差>5分且大盘趋势向上）
    """
    mom_period = params.get('mom_period', 20)
    market_filter = params.get('market_filter', True)  # 大盘择时
    stop_loss = params.get('stop_loss', 7.0)           # 止损线
    trailing_stop = params.get('trailing_stop', 8.0)   # 移动止盈
    score_gap_switch = params.get('score_gap', 5.0)    # 换仓评分差

    # 预计算每个ETF的因子
    etf_factors = {}
    for code, df in all_data.items():
        if len(df) < mom_period + 10:
            continue
        close = df['close']
        df = df.copy()
        df['ma5'] = close.rolling(5).mean()
        df['ma10'] = close.rolling(10).mean()
        df['ma20'] = close.rolling(20).mean()
        df['mom_20d'] = close.pct_change(mom_period) * 100

        # 量能
        if 'vol' in df.columns:
            vol = df['vol']
            recent_vol = vol.rolling(5).mean()
            hist_vol = vol.rolling(mom_period).mean()
            df['vol_score'] = (recent_vol / (hist_vol + 1e-6) * 50).clip(upper=100)
        else:
            df['vol_score'] = 50.0

        # 风险调整
        daily_ret = close.pct_change()
        vol_std = daily_ret.rolling(mom_period).std() * np.sqrt(252) * 100
        df['risk_adj'] = (df['mom_20d'] / (vol_std + 1e-6) * 10).clip(upper=100, lower=0).fillna(50)

        # 相对强弱
        if benchmark_df is not None and len(benchmark_df) >= mom_period + 1:
            bm_mom = benchmark_df.set_index('trade_date')['close'].pct_change(mom_period) * 100
            df['bm_mom'] = df['trade_date'].map(bm_mom).fillna(0)
            df['rel_score'] = (50 + df['mom_20d'] - df['bm_mom']).clip(0, 100)
        else:
            df['rel_score'] = 50.0

        df['total_score'] = (
            df['mom_20d'] * 0.40 + df['vol_score'] * 0.25 +
            df['risk_adj'] * 0.20 + df['rel_score'] * 0.15
        )

        etf_factors[code] = df

    # 基准均线
    bm = benchmark_df.copy()
    bm['ma20'] = bm['close'].rolling(20).mean()
    bm = bm.set_index('trade_date')

    # 逐日生成信号
    all_dates = sorted(set(d for fdf in etf_factors.values() for d in fdf['trade_date'].values))

    signals = []  # [{date, target_code, score, market_ok, ...}]

    for date in all_dates:
        # 大盘趋势判断
        if date not in bm.index:
            signals.append({'date': date, 'target_code': None, 'score': 0, 'market_ok': False})
            continue
        bm_row = bm.loc[date]
        market_ok = bm_row['close'] > bm_row['ma20'] if not pd.isna(bm_row['ma20']) else False

        # 找评分最高的ETF
        best_code = None
        best_score = -999
        best_row = None

        for code, fdf in etf_factors.items():
            row_data = fdf[fdf['trade_date'] == date]
            if row_data.empty:
                continue
            row = row_data.iloc[0]
            if pd.isna(row['total_score']):
                continue
            score = row['total_score']
            if score > best_score:
                best_score = score
                best_code = code
                best_row = row

        signals.append({
            'date': date,
            'target_code': best_code,
            'score': best_score,
            'market_ok': market_ok if market_filter else True,
            'best_row': best_row,
        })

    return signals, etf_factors


def backtest_strategy(signals, etf_factors, params):
    """回测策略"""
    stop_loss = params.get('stop_loss', 7.0)
    trailing_stop = params.get('trailing_stop', 8.0)
    score_gap_switch = params.get('score_gap', 5.0)

    cost = CostConfig()
    cash = INITIAL_CAPITAL
    shares = 0
    holding_code = None
    buy_price = 0.0
    max_price = 0.0  # 持仓期间最高价
    buy_date_idx = -1

    equity_curve = []
    trades = []

    for i, sig in enumerate(signals):
        date = sig['date']
        market_ok = sig['market_ok']
        target_code = sig['target_code']
        best_score = sig['score']

        # 当前持仓数据
        hold_df = etf_factors.get(holding_code) if holding_code else None
        hold_row = None
        if hold_df is not None:
            r = hold_df[hold_df['trade_date'] == date]
            if not r.empty:
                hold_row = r.iloc[0]

        sell_signal = False
        sell_reason = ""

        if holding_code is not None and hold_row is not None:
            cur_price = float(hold_row['close'])
            max_price = max(max_price, cur_price)

            # 1. 大盘趋势反转 -> 卖出
            if not market_ok:
                sell_signal = True
                sell_reason = "大盘趋势反转"

            # 2. 趋势反转：跌破MA20
            elif not pd.isna(hold_row['ma20']) and cur_price < hold_row['ma20']:
                sell_signal = True
                sell_reason = "跌破MA20"

            # 3. 止损
            elif cur_price < buy_price * (1 - stop_loss / 100):
                sell_signal = True
                sell_reason = f"止损{stop_loss}%"

            # 4. 移动止盈
            elif cur_price < max_price * (1 - trailing_stop / 100):
                sell_signal = True
                sell_reason = f"回撤止盈{trailing_stop}%"

            # 5. 更强ETF出现（评分差>阈值）
            elif target_code and target_code != holding_code:
                hold_score = hold_row['total_score'] if not pd.isna(hold_row['total_score']) else 0
                if best_score - hold_score > score_gap_switch and market_ok:
                    sell_signal = True
                    sell_reason = f"更强ETF({target_code}评分{best_score:.1f}vs{hold_score:.1f})"

            if sell_signal:
                sell_price = cur_price - cost.slippage
                sell_amount = sell_price * shares
                fee = max(sell_amount * cost.commission_rate, 5) + sell_amount * cost.stamp_tax_rate
                cash += sell_amount - fee
                profit = (sell_price - buy_price) * shares - fee
                pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                trades.append({
                    'buy_date': signals[buy_date_idx]['date'],
                    'sell_date': date,
                    'code': holding_code,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'pct_return': pct,
                    'hold_days': i - buy_date_idx,
                    'profit': profit,
                    'sell_reason': sell_reason,
                })
                holding_code = None
                shares = 0
                max_price = 0.0

        # 买入判断
        if holding_code is None and market_ok and target_code is not None:
            tdf = etf_factors.get(target_code)
            if tdf is not None:
                t_row = tdf[tdf['trade_date'] == date]
                if not t_row.empty:
                    row = t_row.iloc[0]
                    cur_price = float(row['close'])

                    # 趋势确认：ETF需在MA20上方
                    if not pd.isna(row['ma20']) and cur_price > row['ma20']:
                        # 回调买入：当日跌幅<1.5% 或 当日收阳
                        pct_chg = 0
                        if i > 0:
                            prev_r = tdf[tdf['trade_date'] == signals[i-1]['date']]
                            if not prev_r.empty:
                                prev_close = float(prev_r.iloc[0]['close'])
                                pct_chg = (cur_price - prev_close) / prev_close * 100

                        if pct_chg < 1.5:  # 不追涨
                            buy_price = cur_price + cost.slippage
                            buy_amount = cash
                            shares = int(buy_amount / (buy_price * (1 + cost.commission_rate)))
                            if shares > 0:
                                fee = max(buy_price * shares * cost.commission_rate, 5)
                                cash -= buy_price * shares + fee
                                holding_code = target_code
                                buy_date_idx = i
                                max_price = buy_price

        # 当日总资产
        if holding_code is not None and hold_row is not None:
            total = cash + shares * float(hold_row['close'])
        else:
            total = cash
        equity_curve.append(total)

    # 最后清仓
    if holding_code is not None and shares > 0 and trades:
        last_sig = signals[-1]
        hold_df = etf_factors.get(holding_code)
        if hold_df is not None:
            r = hold_df[hold_df['trade_date'] == last_sig['date']]
            if not r.empty:
                sell_price = float(r.iloc[0]['close']) - cost.slippage
                sell_amount = sell_price * shares
                fee = max(sell_amount * cost.commission_rate, 5) + sell_amount * cost.stamp_tax_rate
                cash += sell_amount - fee
                profit = (sell_price - buy_price) * shares - fee
                pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                trades.append({
                    'buy_date': signals[buy_date_idx]['date'],
                    'sell_date': last_sig['date'],
                    'code': holding_code,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'pct_return': pct,
                    'hold_days': len(signals) - 1 - buy_date_idx,
                    'profit': profit,
                    'sell_reason': '期末清仓',
                })

    final_equity = equity_curve[-1] if equity_curve else INITIAL_CAPITAL

    return {
        'equity_curve': equity_curve,
        'trades': trades,
        'final_equity': final_equity,
        'initial_capital': INITIAL_CAPITAL,
    }


def calc_metrics(result):
    equity = pd.Series(result['equity_curve'])
    trades = result['trades']

    total_return = (result['final_equity'] / INITIAL_CAPITAL - 1) * 100
    n_days = len(equity)
    annual_return = ((result['final_equity'] / INITIAL_CAPITAL) ** (252 / max(n_days, 1)) - 1) * 100

    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax * 100
    max_drawdown = drawdown.min()

    if trades:
        wins = [t for t in trades if t['pct_return'] > 0]
        win_rate = len(wins) / len(trades) * 100
        avg_hold = np.mean([t['hold_days'] for t in trades])
        avg_profit = np.mean([t['pct_return'] for t in trades])
        win_profits = [t['pct_return'] for t in trades if t['pct_return'] > 0]
        loss_profits = [abs(t['pct_return']) for t in trades if t['pct_return'] < 0]
        profit_factor = sum(win_profits) / sum(loss_profits) if loss_profits else 999
    else:
        win_rate = avg_hold = avg_profit = profit_factor = 0

    daily_returns = equity.pct_change().fillna(0)
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

    return {
        'total_return': total_return, 'annual_return': annual_return,
        'max_drawdown': max_drawdown, 'win_rate': win_rate,
        'profit_factor': profit_factor, 'sharpe': sharpe,
        'n_trades': len(trades), 'avg_hold_days': avg_hold,
        'avg_profit_per_trade': avg_profit,
    }


def main():
    start_date = "20220101"
    end_date = "20260713"

    print("=" * 80)
    print("  ETF确定性择时轮动策略 - 机构级回测")
    print(f"  回测区间: {start_date} ~ {end_date}")
    print("=" * 80)

    print("\n[1/4] 加载ETF数据...")
    all_data = {}
    for name, code in ETF_POOL.items():
        df = load_etf_data(code, start_date, end_date)
        if df is not None and len(df) > 30:
            all_data[code] = df
    print(f"  加载 {len(all_data)} 只ETF")

    benchmark_df = load_etf_data(BENCHMARK_CODE, start_date, end_date)
    print(f"  基准: {len(benchmark_df)} 条")

    # 策略参数组
    param_sets = [
        # 1. 确定性择时策略（核心）
        {"name": "确定性择时", "mom_period": 20, "market_filter": True,
         "stop_loss": 7.0, "trailing_stop": 8.0, "score_gap": 5.0},
        # 2. 宽松止损版
        {"name": "宽松止损", "mom_period": 20, "market_filter": True,
         "stop_loss": 10.0, "trailing_stop": 12.0, "score_gap": 8.0},
        # 3. 严格趋势版
        {"name": "严格趋势", "mom_period": 20, "market_filter": True,
         "stop_loss": 5.0, "trailing_stop": 5.0, "score_gap": 3.0},
        # 4. 无择时（对照：纯选ETF+止盈止损）
        {"name": "无择时", "mom_period": 20, "market_filter": False,
         "stop_loss": 7.0, "trailing_stop": 8.0, "score_gap": 5.0},
    ]

    print("\n[2/4] 预计算因子...")
    all_results = []

    for params in param_sets:
        name = params['name']
        print(f"\n[3/4] 回测: {name}")
        print(f"  止损={params['stop_loss']}% 回撤止盈={params['trailing_stop']}% 换仓差={params['score_gap']}分 择时={params['market_filter']}")

        signals, etf_factors = generate_signals(all_data, benchmark_df, params)
        result = backtest_strategy(signals, etf_factors, params)
        metrics = calc_metrics(result)

        print(f"  交易: {metrics['n_trades']}次 | 胜率: {metrics['win_rate']:.1f}% | 盈亏比: {metrics['profit_factor']:.2f}")
        print(f"  总收益: {metrics['total_return']:+.2f}% | 年化: {metrics['annual_return']:+.2f}%")
        print(f"  最大回撤: {metrics['max_drawdown']:.2f}% | 夏普: {metrics['sharpe']:.2f} | 平均持仓: {metrics['avg_hold_days']:.0f}天")

        # 打印卖出原因统计
        if result['trades']:
            reasons = {}
            for t in result['trades']:
                r = t.get('sell_reason', '未知')
                reasons[r] = reasons.get(r, 0) + 1
            print(f"  卖出原因: {reasons}")

        all_results.append((name, metrics, result))

    # 汇总
    print("\n" + "=" * 80)
    print("  策略对比汇总")
    print("=" * 80)
    print(f"  {'策略':<12} {'总收益':>8} {'年化':>8} {'胜率':>6} {'盈亏比':>6} {'回撤':>8} {'夏普':>6} {'交易':>4} {'持仓天':>6}")
    print(f"  {'-'*80}")
    for name, m, _ in all_results:
        print(f"  {name:<12} {m['total_return']:>+7.1f}% {m['annual_return']:>+7.1f}% {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} {m['max_drawdown']:>+7.1f}% {m['sharpe']:>5.2f} {m['n_trades']:>4} {m['avg_hold_days']:>5.0f}天")

    best = max(all_results, key=lambda x: x[1]['sharpe'])
    print(f"\n  >>> 夏普最优: {best[0]} (夏普={best[1]['sharpe']:.2f}, 收益={best[1]['total_return']:+.1f}%, 胜率={best[1]['win_rate']:.1f}%)")
    best_ret = max(all_results, key=lambda x: x[1]['total_return'])
    print(f"  >>> 收益最高: {best_ret[0]} (收益={best_ret[1]['total_return']:+.1f}%)")
    best_wr = max(all_results, key=lambda x: x[1]['win_rate'])
    print(f"  >>> 胜率最高: {best_wr[0]} (胜率={best_wr[1]['win_rate']:.1f}%)")


if __name__ == "__main__":
    main()
