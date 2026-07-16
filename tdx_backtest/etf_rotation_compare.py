# -*- coding: utf-8 -*-
"""
ETF轮动调仓策略对比回测
对比两种调仓规则：
  A. 固定60日调仓（当前策略）
  B. 动量切换：有新的最强ETF就立即换仓

使用通达信本地数据回测2023-2025年
"""
import os, sys, datetime, json, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# 项目路径
TDX_BACKTEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tdx_backtest")
sys.path.insert(0, TDX_BACKTEST_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "solo"))

from data_loader import load_kline, ts_code_to_tdx_path

# =========================================================
# ETF池（与 etf_mainline_strategy_tushare.py 一致）
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

BENCHMARK_CODE = '510300'  # 沪深300ETF作为基准

# =========================================================
# 交易成本（与 backtest.py 一致）
# =========================================================
COMMISSION_RATE = 0.00025   # 佣金 万2.5
STAMP_TAX_RATE = 0.001      # 印花税 千1（仅卖出）
SLIPPAGE = 0.001            # 滑点 0.1%

INITIAL_CAPITAL = 100000  # 初始资金10万

# =========================================================
# 加载ETF日线数据（通达信本地）
# =========================================================
def load_etf_data(code, start_date, end_date):
    """从通达信本地文件加载ETF日线"""
    # 确定市场后缀
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
    except Exception as e:
        print(f"  [WARN] 加载 {code} 失败: {e}")
        return None


def load_all_etf_data(start_date, end_date):
    """加载ETF池中所有ETF数据"""
    all_data = {}
    for name, code in ETF_POOL.items():
        df = load_etf_data(code, start_date, end_date)
        if df is not None and len(df) > 30:
            all_data[code] = df
            # print(f"  {name}({code}): {len(df)} 条")
        else:
            print(f"  [SKIP] {name}({code}): 数据不足")
    return all_data


# =========================================================
# 多因子评分（与 etf_mainline_strategy_tushare.py 一致）
# =========================================================
def calc_factor_score(df, benchmark_df, mom_period=20):
    """计算多因子综合评分（截面上）"""
    if len(df) < mom_period + 5:
        return None

    close = df['close']

    # 动量
    mom_20d = close.pct_change(mom_period).iloc[-1] * 100

    # 量能
    if 'vol' in df.columns:
        vol = df['vol']
        recent_vol_avg = vol.tail(5).mean()
        hist_vol_avg = vol.tail(mom_period).mean()
        vol_ratio = recent_vol_avg / (hist_vol_avg + 1e-6)
        vol_score = min(vol_ratio * 50, 100)
    else:
        vol_score = 50

    # 风险调整
    daily_returns = close.pct_change().dropna()
    if len(daily_returns) >= mom_period:
        volatility = daily_returns.tail(mom_period).std() * np.sqrt(252) * 100
        risk_adj_score = min(mom_20d / volatility * 10, 100) if volatility > 0 else 50
    else:
        risk_adj_score = 50

    # 相对强弱
    if benchmark_df is not None and len(benchmark_df) >= mom_period + 1:
        bm_return = benchmark_df['close'].pct_change(mom_period).iloc[-1] * 100
        rel_score = max(0, min(100, 50 + (mom_20d - bm_return)))
    else:
        rel_score = 50

    total_score = mom_20d * 0.40 + vol_score * 0.25 + risk_adj_score * 0.20 + rel_score * 0.15

    return {
        'total_score': total_score,
        'momentum': mom_20d,
        'vol_score': vol_score,
        'risk_adj': risk_adj_score,
        'rel_strength': rel_score,
    }


def calc_daily_scores(all_data, benchmark_df, mom_period=20):
    """计算每日每只ETF的多因子评分（预计算滚动因子，高效版）"""
    from itertools import chain

    # 1. 收集所有交易日
    all_dates_set = set()
    for code, df in all_data.items():
        all_dates_set.update(df['trade_date'].tolist())
    if benchmark_df is not None:
        all_dates_set.update(benchmark_df['trade_date'].tolist())
    all_dates = sorted(all_dates_set)

    # 2. 对每个ETF预计算滚动因子
    etf_factors = {}  # code -> DataFrame indexed by trade_date
    for code, df in all_data.items():
        if len(df) < mom_period + 5:
            continue
        close = df['close']
        # 动量
        mom = close.pct_change(mom_period) * 100
        # 量能
        if 'vol' in df.columns:
            vol = df['vol']
            recent_vol = vol.rolling(5).mean()
            hist_vol = vol.rolling(mom_period).mean()
            vol_ratio = recent_vol / (hist_vol + 1e-6)
            vol_score = (vol_ratio * 50).clip(upper=100)
        else:
            vol_score = pd.Series(50, index=df.index)
        # 风险调整
        daily_ret = close.pct_change()
        volatility = daily_ret.rolling(mom_period).std() * np.sqrt(252) * 100
        risk_adj = (mom / volatility * 10).clip(upper=100, lower=0)
        risk_adj = risk_adj.fillna(50)
        # 基准动量
        if benchmark_df is not None and len(benchmark_df) >= mom_period + 1:
            bm_mom = benchmark_df['close'].pct_change(mom_period) * 100
            # 对齐日期
            bm_aligned = benchmark_df.set_index('trade_date')['close'].pct_change(mom_period) * 100
            # 按 trade_date 映射
            df_with_factors = df[['trade_date', 'close']].copy()
            df_with_factors['bm_mom'] = df_with_factors['trade_date'].map(
                benchmark_df.set_index('trade_date')['close'].pct_change(mom_period) * 100
            ).fillna(0)
            rel_score = (50 + df_with_factors['bm_mom'].rsub(0) * 0 + (mom.values - df_with_factors['bm_mom'].values) * 1).clip(0, 100)
            rel_score = pd.Series(rel_score, index=df.index)
        else:
            rel_score = pd.Series(50, index=df.index)

        total = mom * 0.40 + vol_score * 0.25 + risk_adj * 0.20 + rel_score * 0.15

        fdf = pd.DataFrame({
            'trade_date': df['trade_date'].values,
            'total_score': total.values,
            'momentum': mom.values,
        }, index=df['trade_date'].values)

        etf_factors[code] = fdf

    # 3. 构建每日截面评分
    daily_scores = {}
    for date in all_dates:
        scores = {}
        for code, fdf in etf_factors.items():
            if date in fdf.index:
                row = fdf.loc[date]
                if pd.notna(row['total_score']):
                    scores[code] = {
                        'total_score': row['total_score'],
                        'momentum': row['momentum'],
                    }
        if scores:
            daily_scores[date] = scores

    return daily_scores, all_dates


# =========================================================
# 回测引擎：两种调仓规则对比
# =========================================================
def backtest_strategy(daily_scores, all_dates, all_data, rebal_days=60, strategy_name="固定60日"):
    """
    回测ETF轮动策略

    Args:
        daily_scores: 每日评分 {date: {code: factors}}
        all_dates: 所有交易日列表
        all_data: {code: df}
        rebal_days: 调仓间隔（仅 strategy_name="固定60日" 时生效）
        strategy_name: "固定60日" 或 "动量切换"
    """
    cash = INITIAL_CAPITAL
    shares = 0
    holding_code = None
    buy_price = 0.0
    buy_date_idx = -1

    equity_curve = []
    trades = []

    for i, date in enumerate(all_dates):
        if date not in daily_scores:
            equity_curve.append(cash + (shares * 0 if holding_code is None else 0))
            continue

        scores = daily_scores[date]
        if not scores:
            equity_curve.append(cash)
            continue

        # 当前持仓的评分排名
        holding_score = scores.get(holding_code, {}).get('total_score', -999) if holding_code else -999

        # 最强ETF
        best_code = max(scores, key=lambda c: scores[c]['total_score'])
        best_score = scores[best_code]['total_score']

        # ========== 判断是否调仓 ==========
        need_rebal = False

        if holding_code is None:
            # 空仓 -> 买入最强
            need_rebal = True
        elif strategy_name == "固定60日":
            # 固定60日调仓
            days_since = i - buy_date_idx
            if days_since >= rebal_days:
                need_rebal = True
        elif strategy_name == "动量切换":
            # 有新的最强ETF就立即换仓（但需分数差距超过阈值，防止微小差异频繁换仓）
            if best_code != holding_code:
                score_gap = best_score - holding_score
                if score_gap > 2.0:  # 分数差距超过2分才切换
                    need_rebal = True

        # ========== 执行调仓 ==========
        if need_rebal and holding_code is not None:
            # 卖出当前持仓
            sell_df = all_data.get(holding_code)
            if sell_df is not None:
                sell_row = sell_df[sell_df['trade_date'] == date]
                if len(sell_row) > 0:
                    sell_price = float(sell_row.iloc[0]['close']) - SLIPPAGE
                    sell_amount = sell_price * shares
                    fee = max(sell_amount * COMMISSION_RATE, 5) + sell_amount * STAMP_TAX_RATE
                    cash += sell_amount - fee
                    profit = (sell_price - buy_price) * shares - fee
                    pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                    trades.append({
                        'buy_date': trades[-1]['sell_date'] if trades else all_dates[0],
                        'sell_date': date,
                        'holding_code': holding_code,
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'pct_return': pct,
                        'hold_days': i - buy_date_idx,
                        'profit': profit,
                    })
                    holding_code = None
                    shares = 0

        if need_rebal and holding_code is None:
            # 买入最强ETF
            target_code = best_code
            buy_df = all_data.get(target_code)
            if buy_df is not None:
                buy_row = buy_df[buy_df['trade_date'] == date]
                if len(buy_row) > 0:
                    buy_price = float(buy_row.iloc[0]['close']) + SLIPPAGE
                    buy_amount = cash
                    shares = int(buy_amount / (buy_price * (1 + COMMISSION_RATE)))
                    if shares > 0:
                        fee = max(buy_price * shares * COMMISSION_RATE, 5)
                        cash -= buy_price * shares + fee
                        holding_code = target_code
                        buy_date_idx = i
                        # 记录买入
                        if trades and 'buy_date' not in trades[-1]:
                            trades[-1]['buy_date'] = date

        # 计算当日总资产
        if holding_code is not None:
            cur_df = all_data.get(holding_code)
            if cur_df is not None:
                cur_row = cur_df[cur_df['trade_date'] == date]
                if len(cur_row) > 0:
                    total = cash + shares * float(cur_row.iloc[0]['close'])
                else:
                    total = cash
            else:
                total = cash
        else:
            total = cash

        equity_curve.append(total)

    # 最后清仓
    if holding_code is not None and shares > 0:
        last_date = all_dates[-1]
        sell_df = all_data.get(holding_code)
        if sell_df is not None:
            sell_row = sell_df[sell_df['trade_date'] == last_date]
            if len(sell_row) > 0:
                sell_price = float(sell_row.iloc[0]['close']) - SLIPPAGE
                sell_amount = sell_price * shares
                fee = max(sell_amount * COMMISSION_RATE, 5) + sell_amount * STAMP_TAX_RATE
                cash += sell_amount - fee
                profit = (sell_price - buy_price) * shares - fee
                pct = profit / (buy_price * shares) * 100 if buy_price > 0 else 0
                trades.append({
                    'sell_date': last_date,
                    'holding_code': holding_code,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'pct_return': pct,
                    'hold_days': len(all_dates) - 1 - buy_date_idx,
                    'profit': profit,
                })

    final_equity = cash + (shares * float(all_data[holding_code].iloc[-1]['close']) if holding_code and holding_code in all_data else 0)
    final_equity = cash  # 已清仓

    return {
        'equity_curve': equity_curve,
        'trades': trades,
        'final_equity': equity_curve[-1] if equity_curve else INITIAL_CAPITAL,
        'initial_capital': INITIAL_CAPITAL,
        'n_trades': len(trades),
    }


def calc_metrics(result):
    """计算绩效指标"""
    equity = pd.Series(result['equity_curve'])
    trades = result['trades']

    # 总收益率
    total_return = (result['final_equity'] / INITIAL_CAPITAL - 1) * 100

    # 年化收益率
    n_days = len(equity)
    annual_return = ((result['final_equity'] / INITIAL_CAPITAL) ** (252 / max(n_days, 1)) - 1) * 100

    # 最大回撤
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax * 100
    max_drawdown = drawdown.min()

    # 胜率
    if trades:
        wins = [t for t in trades if t['pct_return'] > 0]
        win_rate = len(wins) / len(trades) * 100
        avg_hold = np.mean([t['hold_days'] for t in trades])
        avg_profit = np.mean([t['pct_return'] for t in trades])
        # 盈亏比
        win_profits = [t['pct_return'] for t in trades if t['pct_return'] > 0]
        loss_profits = [abs(t['pct_return']) for t in trades if t['pct_return'] < 0]
        profit_factor = sum(win_profits) / sum(loss_profits) if loss_profits else 999
    else:
        win_rate = 0
        avg_hold = 0
        avg_profit = 0
        profit_factor = 0

    # 日收益率
    daily_returns = equity.pct_change().fillna(0)
    if daily_returns.std() > 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = 0

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'sharpe': sharpe,
        'n_trades': len(trades),
        'avg_hold_days': avg_hold,
        'avg_profit_per_trade': avg_profit,
    }


# =========================================================
# 主函数
# =========================================================
def main():
    # 回测区间
    start_date = "20220101"
    end_date = "20260713"

    print("=" * 80)
    print("  ETF轮动调仓策略对比回测")
    print(f"  回测区间: {start_date} ~ {end_date}")
    print(f"  初始资金: {INITIAL_CAPITAL:,.0f}")
    print("=" * 80)

    # 加载数据
    print("\n[1/3] 加载ETF日线数据（通达信本地）...")
    all_data = load_all_etf_data(start_date, end_date)
    print(f"  成功加载 {len(all_data)} 只ETF")

    # 基准
    benchmark_df = load_etf_data(BENCHMARK_CODE, start_date, end_date)
    if benchmark_df is not None:
        print(f"  基准(沪深300ETF): {len(benchmark_df)} 条")
    else:
        print("  [WARN] 基准数据加载失败")

    if len(all_data) < 10:
        print(f"\n[ERROR] ETF数据不足({len(all_data)}只)，请确认通达信本地数据完整")
        return

    # 计算每日评分
    print("\n[2/3] 计算每日多因子评分...")
    daily_scores, all_dates = calc_daily_scores(all_data, benchmark_df, mom_period=20)
    print(f"  评分覆盖 {len(all_dates)} 个交易日")

    # 回测两种策略
    print("\n[3/3] 回测对比...")

    strategies = [
        {"name": "固定60日", "rebal_days": 60},
        {"name": "动量切换", "rebal_days": 0},
        # 额外测试几种固定周期
        {"name": "固定30日", "rebal_days": 30},
        {"name": "固定20日", "rebal_days": 20},
    ]

    results = []
    for s in strategies:
        print(f"\n  --- {s['name']} ---")
        result = backtest_strategy(daily_scores, all_dates, all_data,
                                    rebal_days=s['rebal_days'], strategy_name=s['name'])
        metrics = calc_metrics(result)
        results.append((s['name'], metrics))
        print(f"  交易次数: {metrics['n_trades']}")
        print(f"  总收益: {metrics['total_return']:+.2f}%")
        print(f"  年化: {metrics['annual_return']:+.2f}%")
        print(f"  胜率: {metrics['win_rate']:.1f}%")
        print(f"  盈亏比: {metrics['profit_factor']:.2f}")
        print(f"  最大回撤: {metrics['max_drawdown']:.2f}%")
        print(f"  夏普: {metrics['sharpe']:.2f}")
        print(f"  平均持仓: {metrics['avg_hold_days']:.0f}天")
        print(f"  单笔均收益: {metrics['avg_profit_per_trade']:+.2f}%")

    # 汇总对比
    print("\n" + "=" * 80)
    print("  策略对比汇总")
    print("=" * 80)
    print(f"  {'策略':<12} {'总收益':>8} {'年化':>8} {'胜率':>6} {'盈亏比':>6} {'最大回撤':>8} {'夏普':>6} {'交易':>4} {'持仓天':>6}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*8} {'-'*6} {'-'*4} {'-'*6}")
    for name, m in results:
        print(f"  {name:<12} {m['total_return']:>+7.1f}% {m['annual_return']:>+7.1f}% {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} {m['max_drawdown']:>+7.1f}% {m['sharpe']:>5.2f} {m['n_trades']:>4} {m['avg_hold_days']:>5.0f}天")

    # 结论
    best = max(results, key=lambda x: x[1]['sharpe'])
    print(f"\n  >>> 综合夏普最优: {best[0]} (夏普={best[1]['sharpe']:.2f})")
    best_ret = max(results, key=lambda x: x[1]['total_return'])
    print(f"  >>> 总收益最高: {best_ret[0]} (收益={best_ret[1]['total_return']:+.1f}%)")
    best_wr = max(results, key=lambda x: x[1]['win_rate'])
    print(f"  >>> 胜率最高: {best_wr[0]} (胜率={best_wr[1]['win_rate']:.1f}%)")


if __name__ == "__main__":
    main()
