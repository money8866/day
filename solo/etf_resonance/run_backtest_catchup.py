"""ETF Catchup-Diffusion Strategy Backtest - 补涨扩散策略回测。

新策略核心：
  1. 找趋势已形成的ETF（TrendScore > 60）
  2. 找扩散预期强的主题（DiffusionScore > 50）
  3. 在扩散主题中找相对滞涨但有补涨弹性的股票（CatchupScore Top N）
  4. 加入止损（-8% 或 ATR 2倍）和择时（大盘弱势空仓）

对比原策略：
  - 原：追龙头（Composite Top 20）→ 6个月 +0.10%，胜率39.1%
  - 新：找补涨（CatchupScore Top 10）+ 止损 + 择时
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from etf_resonance.core.trend import TrendScorer
from etf_resonance.core.persistence import PersistenceScorer
from etf_resonance.core.catchup import CatchupScorer
from etf_resonance.core.diffusion import DiffusionScorer
from etf_resonance.utils.helpers import Config
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
REBALANCE_DAYS = 5           # 调仓周期（更频繁以快速止损）
HOLDING_TOPN = 5             # 持仓数量
BACKTEST_MONTHS = 6          # 回测时长
BENCHMARK = '000300.SH'
STOP_LOSS_PCT = -8.0         # 个股止损线
TAKE_PROFIT_PCT = 20.0       # 个股止盈线
MARKET_FILTER = True         # 是否启用大盘择时
MARKET_MA = 20               # 大盘均线周期

# ETF 池
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

# ============== 1. 加载历史数据 ==============
print("=" * 70)
print(f"ETF 补涨扩散策略回测 | 回测期: 过去 {BACKTEST_MONTHS} 个月")
print(f"调仓: {REBALANCE_DAYS}个交易日 | 持仓: Top {HOLDING_TOPN}")
print(f"止损: {STOP_LOSS_PCT}% | 止盈: {TAKE_PROFIT_PCT}% | 大盘择时: {MARKET_FILTER}")
print("=" * 70)

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 2)).strftime('%Y%m%d')
warmup_date = (datetime.now() - relativedelta(months=BACKTEST_MONTHS + 8)).strftime('%Y%m%d')

print(f"\n[1] 加载历史数据 {warmup_date} ~ {end_date}")

# 成份股：优先读取本地汇总JSON，缺失的ETF回退到 DataFetcher.get_etf_cons()
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
                # 取最新一期，按权重降序
                latest = cons_df['trade_date'].max()
                cons_df = cons_df[cons_df['trade_date'] == latest].sort_values('cpr', ascending=False)
                stocks = [c for c in cons_df['con_code'].tolist()
                          if not str(c).endswith('.BJ') and c != 'Au9999']
                all_etf_constituents[etf_code] = stocks
                print(f"    {etf_code} ({ETF_THEME_MAP[etf_code]}): {len(stocks)} 只")
        except Exception as e:
            print(f"    {etf_code} 获取失败: {e}")

print("\n  下载ETF日线...")
etf_data = {}
for etf_code in ETF_THEME_MAP:
    try:
        df = dfetcher.get_fund_daily(ts_code=etf_code, start_date=warmup_date, end_date=end_date)
        if df is not None and not df.empty:
            etf_data[etf_code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
print(f"  ETF: {len(etf_data)} 个")

all_stocks = set()
constituents = {}
for etf_code in ETF_THEME_MAP:
    if etf_code in all_etf_constituents:
        stocks = [s for s in all_etf_constituents[etf_code]
                  if not s.endswith('.BJ') and s != 'Au9999'][:50]
        constituents[etf_code] = stocks
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
    if (i + 1) % 100 == 0:
        print(f"    进度: {i+1}/{len(all_stocks)}")
print(f"  成功: {len(stock_data)}/{len(all_stocks)}")

# 获取股票名称映射
print("\n  获取股票名称...")
try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
    industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
except Exception as e:
    print(f"    获取股票名称失败: {e}")
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

# ============== 3. 初始化评分器 ==============
print("\n[3] 初始化评分器...")
config = Config(r'd:\mystock\solo\etf_resonance\config.yaml')
trend_scorer = TrendScorer(config)
persist_scorer = PersistenceScorer(config)
catchup_scorer = CatchupScorer(config)
diffusion_scorer = DiffusionScorer(config)

# ============== 4. 回测主循环 ==============
print("\n[4] 开始回测...")

all_trades = []
portfolio_values = []
current_holdings = {}
INIT_CAPITAL = 1_000_000
cash = INIT_CAPITAL

def slice_data_as_of(data_dict, as_of_date):
    out = {}
    for code, df in data_dict.items():
        sliced = df[df['trade_date'] <= as_of_date].copy()
        if len(sliced) >= 60:
            out[code] = sliced
    return out

def get_price(code, date):
    df = stock_data.get(code)
    if df is None: return None
    row = df[df['trade_date'] == date]
    return float(row['close'].iloc[0]) if not row.empty else None

def get_daily_prices_between(code, start_date, end_date):
    """获取两日间的所有收盘价（用于止损判断）"""
    df = stock_data.get(code)
    if df is None: return []
    mask = (df['trade_date'] > start_date) & (df['trade_date'] <= end_date)
    return df[mask]['close'].tolist()

def is_market_bullish(as_of_date):
    """大盘择时：沪深300在MA20上方"""
    if not MARKET_FILTER or bench_df is None:
        return True
    sliced = bench_df[bench_df['trade_date'] <= as_of_date].copy()
    if len(sliced) < MARKET_MA:
        return True
    ma = sliced['close'].rolling(MARKET_MA).mean().iloc[-1]
    cur = sliced['close'].iloc[-1]
    return cur > ma

for rb_idx, rb_date in enumerate(rebalance_dates):
    print(f"\n  --- 调仓 {rb_idx+1}/{len(rebalance_dates)}: {rb_date} ---")

    # 大盘择时
    bullish = is_market_bullish(rb_date)
    if not bullish:
        print(f"    ⚠️ 大盘弱势（沪深300<{MARKET_MA}MA），空仓观望")
        # 平仓所有持仓
        for code in list(current_holdings.keys()):
            pos = current_holdings[code]
            exit_price = get_price(code, rb_date) or pos['entry_price']
            ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
            cash += pos['shares'] * exit_price * (1 - 0.0003)
            hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
            all_trades.append({
                'entry_date': pos['entry_date'], 'exit_date': rb_date, 'code': code,
                'stock_name': pos.get('stock_name', name_map.get(code, '')),
                'etf_code': pos.get('etf_code', ''), 'etf_name': pos.get('etf_name', ''),
                'industry': pos.get('industry', industry_map.get(code, '')),
                'entry_price': pos['entry_price'], 'exit_price': exit_price,
                'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                'score': pos.get('catchup_score', 0), 'exit_reason': '大盘空仓',
            })
            del current_holdings[code]
        portfolio_values.append({'date': rb_date, 'equity': cash})
        continue

    # 1. 切片数据
    etf_slice = slice_data_as_of(etf_data, rb_date)
    stock_slice = slice_data_as_of(stock_data, rb_date)
    if len(etf_slice) < 3 or len(stock_slice) < 30:
        continue

    # 2. 计算ETF趋势分
    trend_results = trend_scorer.score(etf_slice)
    persist_results = persist_scorer.score(etf_slice)

    # 3. 过滤ETF：TrendScore > 55 且 Persistence > 40
    qualifying_etfs = {}
    for code, tr in trend_results.items():
        pr = persist_results.get(code)
        if (tr.trend_score >= 55 and pr and pr.persistence_score >= 40
            and tr.ema20_above_ema60):
            qualifying_etfs[code] = tr

    if not qualifying_etfs:
        print(f"    无趋势ETF，空仓")
        portfolio_values.append({'date': rb_date, 'equity': cash + sum(
            h['shares'] * (get_price(c, rb_date) or h['entry_price'])
            for c, h in current_holdings.items())})
        continue

    # 4. 计算扩散度
    qualifying_constituents = {c: constituents[c] for c in qualifying_etfs if c in constituents}
    diffusion_results = diffusion_scorer.score(
        stock_slice, etf_slice, qualifying_constituents, ETF_THEME_MAP
    )

    # 只保留扩散度 > 50 的ETF
    diffused_etfs = {c: r for c, r in diffusion_results.items() if r.diffusion_score > 50}

    if not diffused_etfs:
        # 退而求其次：扩散度最高的3个
        sorted_diff = sorted(diffusion_results.items(), key=lambda x: -x[1].diffusion_score)
        diffused_etfs = dict(sorted_diff[:3])

    print(f"    趋势ETF: {len(qualifying_etfs)} | 扩散ETF: {len(diffused_etfs)}")
    for c, r in sorted(diffused_etfs.items(), key=lambda x: -x[1].diffusion_score)[:3]:
        print(f"      {c} ({ETF_THEME_MAP.get(c,'')}): Diffusion={r.diffusion_score} "
              f"breadth={r.breadth_expansion} rotation={r.rotation_signal}")

    # 5. 计算补涨评分
    filtered_constituents = {c: qualifying_constituents[c] for c in diffused_etfs
                             if c in qualifying_constituents}
    if not filtered_constituents:
        continue

    catchup_results = catchup_scorer.score(
        stock_slice, etf_slice, filtered_constituents,
        {c: tr.trend_score for c, tr in qualifying_etfs.items()}
    )
    total_catchup = sum(len(v) for v in catchup_results.values())
    print(f"    补涨候选: {total_catchup} 只 (跨 {len(catchup_results)} 个ETF)")
    if total_catchup == 0:
        # 诊断：看下第一个ETF的第一只股票为啥失败
        for etf_code, stocks in filtered_constituents.items():
            etf_df = etf_slice.get(etf_code)
            if etf_df is None: continue
            for code in stocks[:3]:
                df = stock_slice.get(code)
                if df is None:
                    print(f"      诊断: {code} 不在stock_slice")
                    continue
                print(f"      诊断: {code} len={len(df)} close[-1]={df['close'].iloc[-1]:.2f}")
            break

    # 6. 汇总所有候选股票并排序
    all_candidates = []
    for etf_code, results in catchup_results.items():
        diff_score = diffused_etfs.get(etf_code)
        diff_val = diff_score.diffusion_score if diff_score else 50
        for r in results:
            # 最终评分 = 补涨分 × 0.6 + 扩散分 × 0.3 + ETF趋势分 × 0.1
            final_score = (
                r.catchup_score * 0.6 +
                diff_val * 0.3 +
                qualifying_etfs[etf_code].trend_score * 0.1
            )
            all_candidates.append({
                'code': r.ts_code,
                'etf_code': etf_code,
                'catchup_score': r.catchup_score,
                'lag_degree': r.lag_degree,
                'startup_signal': r.startup_signal,
                'elasticity': r.elasticity,
                'ret_60d': r.ret_60d,
                'etf_ret_60d': r.etf_ret_60d,
                'ret_gap': r.ret_gap,
                'dist_to_low': r.dist_to_low,
                'dist_to_high': r.dist_to_high,
                'vol_ratio_5d': r.vol_ratio_5d,
                'beta': r.beta,
                'diffusion_score': diff_val,
                'etf_trend': qualifying_etfs[etf_code].trend_score,
                'final_score': round(final_score, 2),
            })

    if not all_candidates:
        print(f"    无候选股票")
        continue

    # 按最终评分排序
    all_candidates.sort(key=lambda x: -x['final_score'])
    top_picks = all_candidates[:HOLDING_TOPN]
    top_codes = {p['code'] for p in top_picks}

    print(f"    Top {HOLDING_TOPN}:")
    for p in top_picks[:5]:
        print(f"      {p['code']} | 补涨={p['catchup_score']:.1f} | 滞涨度={p['lag_degree']:.1f} | "
              f"启动={p['startup_signal']:.1f} | 落后ETF {p['ret_gap']:.1f}% | 最终={p['final_score']:.1f}")

    # 7. 止损检查：每个交易日检查持仓是否触发止损
    for code in list(current_holdings.keys()):
        pos = current_holdings[code]
        # 检查自上次调仓以来的每日价格
        prices = get_daily_prices_between(code, pos['entry_date'], rb_date)
        triggered = False
        for i, p in enumerate(prices):
            ret = (p / pos['entry_price'] - 1) * 100
            if ret <= STOP_LOSS_PCT:
                # 触发止损
                exit_price = p
                ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
                cash += pos['shares'] * exit_price * (1 - 0.0003)
                # 找触发日期
                trade_dates_between = [d for d in all_trade_dates
                                       if pos['entry_date'] < d <= rb_date]
                exit_date = trade_dates_between[i] if i < len(trade_dates_between) else rb_date
                hold_days = i + 1
                all_trades.append({
                    'entry_date': pos['entry_date'], 'exit_date': exit_date, 'code': code,
                    'stock_name': pos.get('stock_name', name_map.get(code, '')),
                    'etf_code': pos.get('etf_code', ''), 'etf_name': pos.get('etf_name', ''),
                    'industry': pos.get('industry', industry_map.get(code, '')),
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                    'score': pos.get('catchup_score', 0), 'exit_reason': f'止损{STOP_LOSS_PCT}%',
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
                    'etf_code': pos.get('etf_code', ''), 'etf_name': pos.get('etf_name', ''),
                    'industry': pos.get('industry', industry_map.get(code, '')),
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                    'score': pos.get('catchup_score', 0), 'exit_reason': f'止盈{TAKE_PROFIT_PCT}%',
                })
                del current_holdings[code]
                triggered = True
                print(f"    💰 {code} 止盈: +{ret_pct:.2f}%")
                break

        # 未触发止损止盈，检查是否仍在TopN
        if not triggered and code not in top_codes:
            exit_price = get_price(code, rb_date) or pos['entry_price']
            ret_pct = (exit_price / pos['entry_price'] - 1) * 100 - 0.06
            cash += pos['shares'] * exit_price * (1 - 0.0003)
            hold_days = sum(1 for d in all_trade_dates if pos['entry_date'] <= d <= rb_date)
            all_trades.append({
                'entry_date': pos['entry_date'], 'exit_date': rb_date, 'code': code,
                'stock_name': pos.get('stock_name', name_map.get(code, '')),
                'etf_code': pos.get('etf_code', ''), 'etf_name': pos.get('etf_name', ''),
                'industry': pos.get('industry', industry_map.get(code, '')),
                'entry_price': pos['entry_price'], 'exit_price': exit_price,
                'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
                'score': pos.get('catchup_score', 0), 'exit_reason': '调仓换股',
            })
            del current_holdings[code]

    # 8. 开仓新增的TopN
    total_equity = cash
    for code, pos in current_holdings.items():
        cur_price = get_price(code, rb_date) or pos['entry_price']
        total_equity += pos['shares'] * cur_price

    target_per_stock = total_equity / HOLDING_TOPN
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
            'catchup_score': pick['catchup_score'],
            'final_score': pick['final_score'],
            'etf_code': pick['etf_code'],
            'etf_name': ETF_THEME_MAP.get(pick['etf_code'], ''),
            'stock_name': name_map.get(code, ''),
            'industry': industry_map.get(code, ''),
        }

    total_equity = cash
    for code, pos in current_holdings.items():
        cur_price = get_price(code, rb_date) or pos['entry_price']
        total_equity += pos['shares'] * cur_price
    portfolio_values.append({'date': rb_date, 'equity': total_equity})
    print(f"    组合净值: {total_equity:,.0f} | 持仓: {len(current_holdings)} 只 | 现金: {cash:,.0f}")

# ============== 5. 平仓剩余 ==============
print("\n[5] 平仓剩余持仓...")
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
        'etf_code': pos.get('etf_code', ''), 'etf_name': pos.get('etf_name', ''),
        'industry': pos.get('industry', industry_map.get(code, '')),
        'entry_price': pos['entry_price'], 'exit_price': exit_price,
        'return_pct': round(ret_pct, 2), 'hold_days': hold_days,
        'score': pos.get('catchup_score', 0), 'exit_reason': '回测结束',
    })
    del current_holdings[code]

# ============== 6. 计算指标 ==============
print("\n" + "=" * 70)
print("            📊 ETF 补涨扩散策略回测报告")
print("=" * 70)

if not all_trades:
    print("无交易记录！")
    sys.exit(1)

trades_df = pd.DataFrame(all_trades)
trades_df.to_csv(r'd:\mystock\solo\etf_resonance\output\backtest_catchup_trades.csv',
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

# 止损统计
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

# Top 5 盈亏
_top_cols = ['code', 'stock_name', 'etf_code', 'etf_name', 'entry_date', 'exit_date',
             'return_pct', 'hold_days', 'score', 'exit_reason']
_top_cols = [c for c in _top_cols if c in trades_df.columns]

print("\n📈 Top 5 盈利交易:")
top_win = trades_df.nlargest(5, 'return_pct')[_top_cols]
for _, r in top_win.iterrows():
    print(f"  {r['code']} {r.get('stock_name','')} | {r.get('etf_code','')} {r.get('etf_name','')} | "
          f"{r['entry_date']}→{r['exit_date']} | {r['return_pct']:+.2f}% | 持{r['hold_days']}天 | "
          f"补涨分{r['score']:.1f} | {r['exit_reason']}")

print("\n📉 Top 5 亏损交易:")
top_lose = trades_df.nsmallest(5, 'return_pct')[_top_cols]
for _, r in top_lose.iterrows():
    print(f"  {r['code']} {r.get('stock_name','')} | {r.get('etf_code','')} {r.get('etf_name','')} | "
          f"{r['entry_date']}→{r['exit_date']} | {r['return_pct']:+.2f}% | 持{r['hold_days']}天 | "
          f"补涨分{r['score']:.1f} | {r['exit_reason']}")

# 按补涨分组统计
print("\n📊 按补涨评分分组的胜率:")
trades_df['score_bin'] = pd.cut(trades_df['score'], bins=[0, 40, 50, 60, 70, 100],
                                 labels=['<40', '40-50', '50-60', '60-70', '70+'])
grp = trades_df.groupby('score_bin').agg(
    trades=('return_pct', 'count'),
    win_rate=('return_pct', lambda x: (x > 0).sum() / len(x) * 100),
    avg_ret=('return_pct', 'mean')
).round(2)
print(grp.to_string())

# 按退出原因分组
print("\n📊 按退出原因分组:")
grp2 = trades_df.groupby('exit_reason').agg(
    trades=('return_pct', 'count'),
    win_rate=('return_pct', lambda x: (x > 0).sum() / len(x) * 100),
    avg_ret=('return_pct', 'mean')
).round(2)
print(grp2.to_string())

# 保存
if portfolio_values:
    pv_df.to_csv(r'd:\mystock\solo\etf_resonance\output\backtest_catchup_equity.csv',
                 index=False, encoding='utf-8-sig')
    print(f"\n[已保存] 净值曲线: backtest_catchup_equity.csv")
print(f"[已保存] 交易明细: backtest_catchup_trades.csv")
print("=" * 70)
