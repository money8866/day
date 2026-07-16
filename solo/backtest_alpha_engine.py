# -*- coding: utf-8 -*-
"""
ETF Alpha Engine 回测脚本 - 绝对趋势过滤
核心：只有ETF在MA20之上才允许买入
"""
import os, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

TDX_BACKTEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tdx_backtest")
sys.path.insert(0, TDX_BACKTEST_DIR)

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)

from data_loader import load_kline
from etf_alpha_engine.etf_ranking import ETFRankingEngine

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


class CostConfig:
    commission_rate = 0.0003
    stamp_tax_rate = 0.001
    slippage = 0.001


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
        df['ma20'] = df['close'].rolling(20).mean()
        return df
    except:
        return None


def run_backtest(all_data, all_dates, bm, daily_scores, params):
    stop_loss = params['stop_loss']
    trailing_stop = params['trailing_stop']
    score_gap = params['score_gap']
    market_filter = params['market_filter']
    etf_ma_filter = params.get('etf_ma_filter', False)

    cost = CostConfig()
    cash = INITIAL_CAPITAL
    shares = 0
    holding_code = None
    buy_price = 0.0
    max_price = 0.0
    buy_date_idx = -1

    equity_curve = []
    trades = []

    for i, date in enumerate(all_dates):
        market_ok = True
        if market_filter and date in bm.index:
            bm_row = bm.loc[date]
            market_ok = bm_row['close'] > bm_row['ma20'] if not pd.isna(bm_row['ma20']) else False

        scores = daily_scores.get(date, {})

        best_code = None
        best_score = -999
        for code, s in scores.items():
            score = s['score']
            if etf_ma_filter:
                df = all_data.get(code)
                if df is not None:
                    r = df[df['trade_date'] == date]
                    if not r.empty:
                        row = r.iloc[0]
                        if pd.isna(row['ma20']) or row['close'] <= row['ma20']:
                            continue
            if score > best_score:
                best_score = score
                best_code = code

        hold_df = all_data.get(holding_code) if holding_code else None
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

            if not market_ok:
                sell_signal = True
                sell_reason = "大盘趋势反转"

            elif cur_price < buy_price * (1 - stop_loss / 100):
                sell_signal = True
                sell_reason = f"止损{stop_loss}%"

            elif cur_price < max_price * (1 - trailing_stop / 100):
                sell_signal = True
                sell_reason = f"回撤止盈{trailing_stop}%"

            elif best_code and best_code != holding_code:
                hold_s = scores.get(holding_code, {})
                hold_s_score = hold_s.get('score', 0)
                if best_score - hold_s_score > score_gap and market_ok:
                    sell_signal = True
                    sell_reason = f"更强ETF({best_code})"

            if sell_signal:
                sell_price = cur_price - cost.slippage
                sell_amount = sell_price * shares
                fee = max(sell_amount * cost.commission_rate, 5) + sell_amount * cost.stamp_tax_rate
                cash += sell_amount - fee
                profit = (sell_price - buy_price) * shares - fee
                pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                trades.append({
                    'buy_date': all_dates[buy_date_idx],
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

        if holding_code is None and market_ok and best_code is not None:
            tdf = all_data.get(best_code)
            if tdf is not None:
                t_row = tdf[tdf['trade_date'] == date]
                if not t_row.empty:
                    row = t_row.iloc[0]
                    cur_price = float(row['close'])

                    if i > 0:
                        prev_date = all_dates[i-1]
                        prev_r = tdf[tdf['trade_date'] == prev_date]
                        if not prev_r.empty:
                            pct_chg = (cur_price - float(prev_r.iloc[0]['close'])) / float(prev_r.iloc[0]['close']) * 100
                        else:
                            pct_chg = 0
                    else:
                        pct_chg = 0

                    if pct_chg < 1.5:
                        buy_price = cur_price + cost.slippage
                        buy_amount = cash
                        shares = int(buy_amount / (buy_price * (1 + cost.commission_rate)))
                        if shares > 0:
                            fee = max(buy_price * shares * cost.commission_rate, 5)
                            cash -= buy_price * shares + fee
                            holding_code = best_code
                            buy_date_idx = i
                            max_price = buy_price

        if holding_code is not None and hold_row is not None:
            total = cash + shares * float(hold_row['close'])
        else:
            total = cash
        equity_curve.append(total)

    if holding_code is not None and shares > 0:
        last_date = all_dates[-1]
        tdf = all_data.get(holding_code)
        if tdf is not None:
            r = tdf[tdf['trade_date'] == last_date]
            if not r.empty:
                sell_price = float(r.iloc[0]['close']) - cost.slippage
                sell_amount = sell_price * shares
                fee = max(sell_amount * cost.commission_rate, 5) + sell_amount * cost.stamp_tax_rate
                cash += sell_amount - fee
                profit = (sell_price - buy_price) * shares - fee
                pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                trades.append({
                    'buy_date': all_dates[buy_date_idx],
                    'sell_date': last_date,
                    'code': holding_code,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'pct_return': pct,
                    'hold_days': len(all_dates) - 1 - buy_date_idx,
                    'profit': profit,
                    'sell_reason': '期末清仓',
                })

    if not equity_curve:
        return None

    equity = pd.Series(equity_curve)
    total_return = (equity.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    n_days = len(equity)
    if n_days > 0 and equity.iloc[-1] > 0:
        annual_return = ((equity.iloc[-1] / INITIAL_CAPITAL) ** (252 / n_days) - 1) * 100
    else:
        annual_return = -100

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
    if daily_returns.std() > 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = 0

    return {
        'total_return': total_return, 'annual_return': annual_return,
        'max_drawdown': max_drawdown, 'win_rate': win_rate,
        'profit_factor': profit_factor, 'sharpe': sharpe,
        'n_trades': len(trades), 'avg_hold_days': avg_hold,
        'avg_profit_per_trade': avg_profit,
        'trades': trades,
    }


def main():
    start_date = "20220101"
    end_date = "20260713"

    print("=" * 80)
    print("  ETF Alpha Engine - 绝对趋势过滤回测")
    print(f"  回测区间: {start_date} ~ {end_date}")
    print("=" * 80)

    print("\n[1/4] 加载ETF数据...")
    all_data = {}
    for name, code in ETF_POOL.items():
        df = load_etf_data(code, start_date, end_date)
        if df is not None and len(df) > 60:
            all_data[code] = df
    print(f"  加载 {len(all_data)} 只ETF")

    benchmark_df = load_etf_data(BENCHMARK_CODE, start_date, end_date)
    print(f"  基准: {len(benchmark_df)} 条")

    if not all_data or benchmark_df is None or benchmark_df.empty:
        print("  数据不足，退出")
        return

    bm = benchmark_df.copy()
    bm['ma20'] = bm['close'].rolling(20).mean()
    bm = bm.set_index('trade_date')

    all_dates = []
    for df in all_data.values():
        all_dates.extend(df['trade_date'].tolist())
    all_dates = sorted(set(all_dates))

    print("\n[2/4] 预计算每日评分...")
    daily_scores = {}

    for date in all_dates:
        etf_data_dict = {}
        for code, df in all_data.items():
            sub_df = df[df['trade_date'] <= date]
            if len(sub_df) >= 130:
                etf_data_dict[code] = sub_df

        if not etf_data_dict:
            continue

        bm_data = benchmark_df[benchmark_df['trade_date'] <= date]
        benchmark_arr = bm_data['close'].values.astype(float) if not bm_data.empty else None

        engine = ETFRankingEngine({})
        results = engine.score(etf_data_dict, benchmark_arr)

        date_scores = {}
        for code, df in etf_data_dict.items():
            if code in results:
                result = results[code]
                date_scores[code] = {
                    'score': result.etf_alpha_score,
                    'trend_quality': result.trend_quality,
                    'momentum': result.momentum_quality,
                    'rs': result.relative_strength,
                }
        daily_scores[date] = date_scores

    print(f"  完成 {len(daily_scores)} 天评分")

    param_sets = [
        {"name": "Alpha Engine(无过滤)", "stop_loss": 10.0, "trailing_stop": 12.0, "score_gap": 8.0, "market_filter": True, "etf_ma_filter": False},
        {"name": "Alpha Engine(ETF>MA20)", "stop_loss": 10.0, "trailing_stop": 12.0, "score_gap": 8.0, "market_filter": True, "etf_ma_filter": True},
        {"name": "旧策略(多因子)", "stop_loss": 10.0, "trailing_stop": 12.0, "score_gap": 8.0, "market_filter": True, "etf_ma_filter": False, "use_old": True},
    ]

    print("\n[3/4] 回测开始...")
    all_results = []

    for params in param_sets:
        name = params['name']
        print(f"\n[3/4] 回测: {name}")
        print(f"  止损={params['stop_loss']}% 回撤止盈={params['trailing_stop']}% 换仓差={params['score_gap']}分 择时={params['market_filter']} ETF>MA20={params['etf_ma_filter']}")

        if params.get('use_old', False):
            bm_mom_20 = benchmark_df['close'].pct_change(20).iloc[-1] * 100 if len(benchmark_df) >= 21 else 0
            old_scores = {}
            for date in all_dates:
                date_old = {}
                for code, df in all_data.items():
                    sub_df = df[df['trade_date'] <= date]
                    if len(sub_df) >= 21:
                        close = sub_df['close'].values
                        mom_20d = (close[-1] / close[-21] - 1) * 100
                        vol = sub_df['vol'].values
                        recent_vol = np.mean(vol[-5:]) if len(vol) >= 5 else 0
                        hist_vol = np.mean(vol[-20:]) if len(vol) >= 20 else 0
                        vol_score = (recent_vol / (hist_vol + 1e-6) * 50) if hist_vol > 0 else 50
                        daily_ret = np.diff(close) / close[:-1]
                        vol_std = np.std(daily_ret[-20:]) * np.sqrt(252) * 100 if len(daily_ret) >= 20 else 10
                        risk_adj = (mom_20d / (vol_std + 1e-6) * 10) if vol_std > 0 else 50
                        rel_score = 50 + mom_20d - bm_mom_20
                        old_score = mom_20d * 0.40 + vol_score * 0.25 + risk_adj * 0.20 + rel_score * 0.15
                    else:
                        old_score = 0
                    date_old[code] = {'score': old_score}
                old_scores[date] = date_old
            result = run_backtest(all_data, all_dates, bm, old_scores, params)
        else:
            result = run_backtest(all_data, all_dates, bm, daily_scores, params)

        if result is None:
            print("  无交易")
            continue

        print(f"  交易: {result['n_trades']}次 | 胜率: {result['win_rate']:.1f}% | 盈亏比: {result['profit_factor']:.2f}")
        print(f"  总收益: {result['total_return']:+.2f}% | 年化: {result['annual_return']:+.2f}%")
        print(f"  最大回撤: {result['max_drawdown']:.2f}% | 夏普: {result['sharpe']:.2f} | 平均持仓: {result['avg_hold_days']:.0f}天")

        all_results.append((name, result))

    print("\n" + "=" * 80)
    print("  策略对比汇总")
    print("=" * 80)
    print(f"  {'策略':<24} {'总收益':>8} {'年化':>8} {'胜率':>6} {'盈亏比':>6} {'回撤':>8} {'夏普':>6} {'交易':>4} {'持仓天':>6}")
    print(f"  {'-'*90}")
    for name, m in all_results:
        print(f"  {name:<24} {m['total_return']:>+7.1f}% {m['annual_return']:>+7.1f}% {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} {m['max_drawdown']:>+7.1f}% {m['sharpe']:>5.2f} {m['n_trades']:>4} {m['avg_hold_days']:>5.0f}天")

    if all_results:
        best = max(all_results, key=lambda x: x[1]['sharpe'])
        print(f"\n  >>> 夏普最优: {best[0]} (夏普={best[1]['sharpe']:.2f}, 收益={best[1]['total_return']:+.1f}%)")
        best_ret = max(all_results, key=lambda x: x[1]['total_return'])
        print(f"  >>> 收益最高: {best_ret[0]} (收益={best_ret[1]['total_return']:+.1f}%)")


if __name__ == "__main__":
    main()
