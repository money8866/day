"""ETF Resonance Strategy Backtest with Real Historical Data.

策略：
  - 每个调仓日运行 ETF 共振系统，选出 Composite Top 20
  - 等权持有，调仓周期可配置（5/10/20 个交易日）
  - 对比基准：沪深300指数同期收益
  - 输出：累计收益、年化、夏普、最大回撤、胜率、每笔交易明细
"""
import os
import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from etf_resonance.main import ETFResonanceSystem
from etf_resonance.core.trend import TrendScorer
from etf_resonance.core.persistence import PersistenceScorer
from etf_resonance.core.leader import LeaderScorer
from etf_resonance.core.resonance import ResonanceScorer
from etf_resonance.core.risk import RiskScorer
from etf_resonance.core.ranking import RankingEngine
from multi_factor_picker.data_fetcher import DataFetcher
from dotenv import load_dotenv
from dateutil.relativedelta import relativedelta

# ============== 配置 ==============
load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

# 回测参数
REBALANCE_DAYS = 10          # 调仓周期（交易日）
HOLDING_TOPN = 10            # 持仓数量
BACKTEST_MONTHS = 6          # 回测时长（月）
BENCHMARK = '000300.SH'      # 沪深300

# ETF 池
ETF_THEME_MAP = {
    '512480.SH': '半导体', '159995.SZ': '半导体设备',
    '515030.SH': '新能源汽车', '515790.SH': '光伏',
    '515880.SH': '通信设备', '515230.SH': '人工智能',
    '512880.SH': '证券', '512800.SH': '银行',
    '515220.SH': '煤炭', '515210.SH': '钢铁',
    '516160.SH': '新能源', '562500.SH': '机器人',
    '159732.SZ': '消费电子',
}

# ============== 1. 加载历史数据 ==============
print("=" * 70)
print(f"ETF 共振策略回测 | 回测期: 过去 {BACKTEST_MONTHS} 个月 | 调仓: {REBALANCE_DAYS}个交易日")
print(f"持仓: Top {HOLDING_TOPN} | 基准: {BENCHMARK}")
print("=" * 70)

# 回测起止日期
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 2)).strftime('%Y%m%d')
# 多取6个月用于指标计算
warmup_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 8)).strftime('%Y%m%d')

print(f"\n[1] 加载历史数据 {warmup_date} ~ {end_date}")

# 加载ETF成份股
with open(r'd:\mystock\cache_daily\etf_constituents_all.json', 'r', encoding='utf-8') as f:
    all_etf_constituents = json.load(f)

# 下载 ETF 日线
print("\n  下载ETF日线...")
etf_data = {}
for etf_code in ETF_THEME_MAP:
    try:
        df = dfetcher.get_fund_daily(ts_code=etf_code, start_date=warmup_date, end_date=end_date)
        if df is not None and not df.empty:
            etf_data[etf_code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f"    {etf_code} 失败: {e}")

print(f"  ETF: {len(etf_data)} 个")

# 收集所有成份股（每个ETF前30只）
all_stocks = set()
constituents = {}
for etf_code in ETF_THEME_MAP:
    if etf_code in all_etf_constituents:
        stocks = [s for s in all_etf_constituents[etf_code]
                  if not s.endswith('.BJ') and s != 'Au9999'][:30]
        constituents[etf_code] = stocks
        all_stocks.update(stocks)

print(f"  成份股: {len(all_stocks)} 只")

# 下载股票日线
print("\n  下载股票日线...")
stock_data = {}
for i, code in enumerate(sorted(all_stocks)):
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=warmup_date, end_date=end_date)
        if df is not None and not df.empty:
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    if (i + 1) % 100 == 0:
        print(f"    进度: {i+1}/{len(all_stocks)}")

print(f"  成功: {len(stock_data)}/{len(all_stocks)}")

# 下载基准
print("\n  下载基准指数...")
bench_df = dfetcher.get_index_daily(ts_code=BENCHMARK, start_date=start_date, end_date=end_date)
if bench_df is not None and not bench_df.empty:
    bench_df = bench_df.sort_values('trade_date').reset_index(drop=True)
    print(f"  基准 {BENCHMARK}: {len(bench_df)} 条")

# ============== 2. 生成调仓日 ==============
print("\n[2] 生成调仓日...")
# 用基准的交易日历
all_trade_dates = sorted(bench_df['trade_date'].tolist()) if bench_df is not None else []
# 只取回测期内的
backtest_start = (datetime.now() - relativedelta(months=BACKTEST_MONTHS)).strftime('%Y%m%d')
trade_dates_bt = [d for d in all_trade_dates if d >= backtest_start]
# 按 REBALANCE_DAYS 间隔取调仓日
rebalance_dates = trade_dates_bt[::REBALANCE_DAYS]
print(f"  回测期: {backtest_start} ~ {end_date}")
print(f"  调仓日数: {len(rebalance_dates)} 个")
print(f"  首次调仓: {rebalance_dates[0] if rebalance_dates else 'N/A'}")
print(f"  末次调仓: {rebalance_dates[-1] if rebalance_dates else 'N/A'}")

# ============== 3. 回测主循环 ==============
print("\n[3] 开始回测...")

system = ETFResonanceSystem(r'd:\mystock\solo\etf_resonance\config.yaml')
# 动态调整阈值适配真实市场
if system.config:
    system.config._data['etf_filter']['trend_score_min'] = 55
    system.config._data['etf_filter']['persistence_min'] = 40
    system.config._data['etf_filter']['adx_min'] = 15

# 初始化各scorer
trend_scorer = TrendScorer(system.config)
persist_scorer = PersistenceScorer(system.config)
leader_scorer = LeaderScorer(system.config)
resonance_scorer = ResonanceScorer(system.config)
risk_scorer = RiskScorer(system.config)
ranking_engine = RankingEngine(system.config)

# 回测记录
all_trades = []         # 每笔交易
portfolio_values = []   # 每日组合净值
current_holdings = {}   # code -> {entry_date, entry_price, shares}

# 初始资金
INIT_CAPITAL = 1_000_000
cash = INIT_CAPITAL

def slice_data_as_of(data_dict, as_of_date):
    """切片数据到指定日期（含）"""
    out = {}
    for code, df in data_dict.items():
        sliced = df[df['trade_date'] <= as_of_date].copy()
        if len(sliced) >= 60:
            out[code] = sliced
    return out

def get_price(code, date):
    """获取某日收盘价"""
    df = stock_data.get(code)
    if df is None: return None
    row = df[df['trade_date'] == date]
    return float(row['close'].iloc[0]) if not row.empty else None

for rb_idx, rb_date in enumerate(rebalance_dates):
    print(f"\n  --- 调仓 {rb_idx+1}/{len(rebalance_dates)}: {rb_date} ---")

    # 1. 切片数据到调仓日
    etf_slice = slice_data_as_of(etf_data, rb_date)
    stock_slice = slice_data_as_of(stock_data, rb_date)
    if len(etf_slice) < 3 or len(stock_slice) < 30:
        print(f"    数据不足，跳过")
        continue

    # 2. 运行共振系统
    try:
        system.load_data(etf_slice, stock_slice, constituents, ETF_THEME_MAP)
        results = system.run_pipeline()
    except Exception as e:
        print(f"    系统运行失败: {e}")
        continue

    if not results:
        print(f"    无候选股票")
        continue

    # 3. 选 Top N
    top_picks = results[:HOLDING_TOPN]
    top_codes = {p.ts_code for p in top_picks}
    print(f"    Top {HOLDING_TOPN}: {[(p.ts_code, round(p.composite_score,1)) for p in top_picks[:5]]}...")

    # 4. 平仓不在TopN的持仓
    for code in list(current_holdings.keys()):
        if code not in top_codes:
            pos = current_holdings[code]
            exit_price = get_price(code, rb_date)
            if exit_price is None:
                exit_price = pos['entry_price']
            ret_pct = (exit_price / pos['entry_price'] - 1) * 100
            # 扣手续费
            ret_pct -= 0.06  # 买卖各0.03%
            cash += pos['shares'] * exit_price * (1 - 0.0003)
            hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
            all_trades.append({
                'entry_date': pos['entry_date'],
                'exit_date': rb_date,
                'code': code,
                'entry_price': pos['entry_price'],
                'exit_price': exit_price,
                'return_pct': round(ret_pct, 2),
                'hold_days': hold_days,
                'composite_score': pos.get('composite_score', 0),
                'exit_reason': '调仓换股',
            })
            del current_holdings[code]

    # 5. 开仓新增的TopN
    # 先计算当前组合总值
    total_equity = cash
    for code, pos in current_holdings.items():
        cur_price = get_price(code, rb_date) or pos['entry_price']
        total_equity += pos['shares'] * cur_price

    # 等权配置
    target_per_stock = total_equity / HOLDING_TOPN
    for pick in top_picks:
        code = pick.ts_code
        if code in current_holdings:
            continue
        entry_price = get_price(code, rb_date)
        if entry_price is None or entry_price <= 0:
            continue
        # 资金不足时跳过
        if cash < target_per_stock:
            continue
        shares = int(target_per_stock / entry_price / 100) * 100  # 整百
        if shares <= 0:
            shares = int(target_per_stock / entry_price)
        if shares <= 0:
            continue
        cash -= shares * entry_price * (1 + 0.0003)
        current_holdings[code] = {
            'entry_date': rb_date,
            'entry_price': entry_price,
            'shares': shares,
            'composite_score': pick.composite_score,
        }

    # 6. 记录当日组合净值
    total_equity = cash
    for code, pos in current_holdings.items():
        cur_price = get_price(code, rb_date) or pos['entry_price']
        total_equity += pos['shares'] * cur_price
    portfolio_values.append({'date': rb_date, 'equity': total_equity})
    print(f"    组合净值: {total_equity:,.0f} | 持仓: {len(current_holdings)} 只 | 现金: {cash:,.0f}")

# ============== 4. 平仓所有剩余持仓 ==============
print("\n[4] 平仓所有剩余持仓...")
final_date = all_trade_dates[-1] if all_trade_dates else end_date
for code in list(current_holdings.keys()):
    pos = current_holdings[code]
    exit_price = get_price(code, final_date)
    if exit_price is None:
        exit_price = pos['entry_price']
    ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
    cash += pos['shares'] * exit_price * (1 - 0.0003)
    hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= final_date)
    all_trades.append({
        'entry_date': pos['entry_date'],
        'exit_date': final_date,
        'code': code,
        'entry_price': pos['entry_price'],
        'exit_price': exit_price,
        'return_pct': round(ret_pct, 2),
        'hold_days': hold_days,
        'composite_score': pos.get('composite_score', 0),
        'exit_reason': '回测结束',
    })
    del current_holdings[code]

# ============== 5. 计算回测指标 ==============
print("\n[5] 计算回测指标...")
print("=" * 70)

if not all_trades:
    print("无交易记录！")
    sys.exit(1)

trades_df = pd.DataFrame(all_trades)
trades_df.to_csv(r'd:\mystock\solo\etf_resonance\output\backtest_trades.csv',
                 index=False, encoding='utf-8-sig')

# 基本指标
total_trades = len(trades_df)
winning = trades_df[trades_df['return_pct'] > 0]
win_rate = len(winning) / total_trades * 100
avg_ret = trades_df['return_pct'].mean()
avg_hold = trades_df['hold_days'].mean()

# 累计收益（基于组合净值）
if portfolio_values:
    pv_df = pd.DataFrame(portfolio_values)
    pv_df['return'] = pv_df['equity'].pct_change().fillna(0)
    cum_ret = (pv_df['equity'].iloc[-1] / INIT_CAPITAL - 1) * 100
    # 年化
    days = (datetime.strptime(pv_df['date'].iloc[-1], '%Y%m%d') -
            datetime.strptime(pv_df['date'].iloc[0], '%Y%m%d')).days
    annual_ret = ((pv_df['equity'].iloc[-1] / INIT_CAPITAL) ** (365 / max(days, 1)) - 1) * 100
    # 夏普
    if pv_df['return'].std() > 0:
        sharpe = pv_df['return'].mean() / pv_df['return'].std() * np.sqrt(252)
    else:
        sharpe = 0
    # 最大回撤
    cum = (1 + pv_df['return']).cumprod()
    peak = cum.expanding().max()
    dd = (cum / peak - 1)
    max_dd = dd.min() * 100
else:
    cum_ret = avg_ret * total_trades
    annual_ret = 0
    sharpe = 0
    max_dd = 0

# 基准收益
if bench_df is not None and not bench_df.empty:
    bench_bt = bench_df[bench_df['trade_date'] >= backtest_start].copy()
    if not bench_bt.empty:
        bench_ret = (bench_bt['close'].iloc[-1] / bench_bt['close'].iloc[0] - 1) * 100
    else:
        bench_ret = 0
else:
    bench_ret = 0

# ============== 6. 输出报告 ==============
print()
print("=" * 70)
print("            📊 ETF 共振策略回测报告")
print("=" * 70)
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
print("-" * 70)
print(f"  📌 基准 ({BENCHMARK}):  {bench_ret:+.2f}%")
print(f"  🆚 超额收益:   {cum_ret - bench_ret:+.2f}%")
print("=" * 70)

# Top 5 盈利/亏损
print("\n📈 Top 5 盈利交易:")
top_win = trades_df.nlargest(5, 'return_pct')[['code', 'entry_date', 'exit_date', 'return_pct', 'hold_days', 'composite_score']]
for _, r in top_win.iterrows():
    print(f"  {r['code']} | {r['entry_date']}→{r['exit_date']} | {r['return_pct']:+.2f}% | 持{r['hold_days']}天 | 分{r['composite_score']:.1f}")

print("\n📉 Top 5 亏损交易:")
top_lose = trades_df.nsmallest(5, 'return_pct')[['code', 'entry_date', 'exit_date', 'return_pct', 'hold_days', 'composite_score']]
for _, r in top_lose.iterrows():
    print(f"  {r['code']} | {r['entry_date']}→{r['exit_date']} | {r['return_pct']:+.2f}% | 持{r['hold_days']}天 | 分{r['composite_score']:.1f}")

# 按Composite分组统计胜率
print("\n📊 按 Composite 评分分组的胜率:")
trades_df['score_bin'] = pd.cut(trades_df['composite_score'], bins=[0, 55, 60, 65, 70, 100],
                                 labels=['<55', '55-60', '60-65', '65-70', '70+'])
grp = trades_df.groupby('score_bin').agg(
    trades=('return_pct', 'count'),
    win_rate=('return_pct', lambda x: (x > 0).sum() / len(x) * 100),
    avg_ret=('return_pct', 'mean')
).round(2)
print(grp.to_string())

# 保存组合净值曲线
if portfolio_values:
    pv_df.to_csv(r'd:\mystock\solo\etf_resonance\output\backtest_equity_curve.csv',
                 index=False, encoding='utf-8-sig')
    print(f"\n[已保存] 净值曲线: backtest_equity_curve.csv")
print(f"[已保存] 交易明细: backtest_trades.csv")
print("=" * 70)
