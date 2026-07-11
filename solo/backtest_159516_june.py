"""回溯半导体设备ETF(159516.SZ) 6月份每个交易日的补涨信号。"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from etf_resonance.core.catchup import CatchupScorer, MomentumScorer
from etf_resonance.core.trend import TrendScorer
from etf_resonance.core.persistence import PersistenceScorer
from etf_resonance.core.diffusion import DiffusionScorer
from etf_resonance.utils.helpers import Config
from multi_factor_picker.data_fetcher import DataFetcher
from dotenv import load_dotenv

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 720},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

ETF_CODE = '159516.SZ'
ETF_NAME = '半导体设备'
CATCHUP_MIN = 70
MOMENTUM_MIN = 60

# 6月交易日
print("=" * 70)
print(f"  回溯 {ETF_NAME}ETF({ETF_CODE}) 2026年6月补涨信号")
print("=" * 70)

# 加载成份股
json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
all_etf_constituents = {}
if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        all_etf_constituents = json.load(f)

if ETF_CODE not in all_etf_constituents:
    print(f"  成份股未缓存，从tushare获取...")
    cons_df = dfetcher.get_etf_cons(ts_code=ETF_CODE)
    if cons_df is not None and not cons_df.empty:
        latest = cons_df['trade_date'].max()
        cons_df = cons_df[cons_df['trade_date'] == latest].sort_values('cpr', ascending=False)
        stocks = [c for c in cons_df['con_code'].tolist()
                  if not str(c).endswith('.BJ') and c != 'Au9999']
        all_etf_constituents[ETF_CODE] = stocks

stock_codes = [s for s in all_etf_constituents.get(ETF_CODE, [])
               if not s.endswith('.BJ') and s != 'Au9999'][:50]
print(f"[1] 成份股: {len(stock_codes)} 只")

# 获取股票名称
try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
except Exception:
    name_map = {}

# 下载ETF和股票日线 (从2025年10月到2026年7月，覆盖6月回测)
START_DATE = '20251001'
END_DATE = '20260710'

print("[2] 下载日线数据...")
etf_df = dfetcher.get_fund_daily(ts_code=ETF_CODE, start_date=START_DATE, end_date=END_DATE)
if etf_df is not None and not etf_df.empty:
    etf_df = etf_df.sort_values('trade_date').reset_index(drop=True)
    print(f"  ETF数据: {len(etf_df)} 天, {etf_df['trade_date'].iloc[0]} ~ {etf_df['trade_date'].iloc[-1]}")

stock_data = {}
for code in stock_codes:
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            if 'vol' not in df.columns and 'volume' in df.columns:
                df['vol'] = df['volume']
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
print(f"  股票数据: {len(stock_data)} 只")

# 获取6月交易日
cal_df = dfetcher.get_trade_cal(start_date='20260601', end_date='20260630', is_open='1')
if cal_df is not None and not cal_df.empty:
    june_dates = sorted(cal_df[cal_df['is_open'] == 1]['cal_date'].tolist())
else:
    june_dates = []
print(f"[3] 6月交易日: {len(june_dates)} 天 -> {june_dates}")

# 初始化评分器
config = Config(r'd:\mystock\solo\etf_resonance\config.yaml')
catchup_scorer = CatchupScorer(config)
momentum_scorer = MomentumScorer(config)

constituents = {ETF_CODE: stock_codes}

print(f"\n[4] 逐日回溯补涨信号 (补涨≥{CATCHUP_MIN})...")
print(f"{'日期':<12}{'补涨股票数':<10}{'强势股票数':<10}{'Top补涨股':<40}")
print("-" * 80)

all_results = []

for target_date in june_dates:
    # 截取到target_date的数据
    etf_cut = etf_df[etf_df['trade_date'] <= target_date].copy()
    if len(etf_cut) < 60:
        print(f"{target_date}  ETF数据不足({len(etf_cut)}天)，跳过")
        continue

    stock_cut = {}
    for code, df in stock_data.items():
        df_cut = df[df['trade_date'] <= target_date].copy()
        if len(df_cut) >= 60:
            stock_cut[code] = df_cut

    etf_data = {ETF_CODE: etf_cut}

    # 补涨评分
    catchup_results = catchup_scorer.score(stock_cut, etf_data, constituents, {ETF_CODE: 60.0})
    cu_list = catchup_results.get(ETF_CODE, [])
    cu_strong = [r for r in cu_list if r.catchup_score >= CATCHUP_MIN]

    # 强势前排评分
    mom_results = momentum_scorer.score(stock_cut, constituents)
    mom_list = mom_results.get(ETF_CODE, [])
    mom_strong = [r for r in mom_list if r.momentum_score >= MOMENTUM_MIN]

    # Top补涨股描述
    top_desc = ""
    if cu_strong:
        top3 = sorted(cu_strong, key=lambda x: -x.catchup_score)[:3]
        top_desc = ' | '.join(
            f"{name_map.get(r.ts_code, r.ts_code[:6])}({r.ts_code[-3:]} 补涨{r.catchup_score:.0f} "
            f"趋势{r.trend_setup:.0f} 量温{r.vol_gentle:.0f} 60d{r.ret_60d:+.1f}% "
            f"涨停{r.limit_up_5d}天 交叉{r.ma_cross_days}天)"
            for r in top3
        )

    print(f"{target_date}  {len(cu_strong):<10d}{len(mom_strong):<10d}{top_desc}")

    for r in cu_strong:
        all_results.append({
            'date': target_date,
            'code': r.ts_code,
            'name': name_map.get(r.ts_code, ''),
            'catchup_score': r.catchup_score,
            'trend_setup': r.trend_setup,
            'vol_gentle': r.vol_gentle,
            'gain_moderate': r.gain_moderate,
            'no_limit_up': r.no_limit_up,
            'catchup_space': r.catchup_space,
            'ret_60d': r.ret_60d,
            'etf_ret_60d': r.etf_ret_60d,
            'ret_gap': r.ret_gap,
            'vol_ratio_5d': r.vol_ratio_5d,
            'limit_up_5d': r.limit_up_5d,
            'ma_cross_days': r.ma_cross_days,
        })

# 汇总
print(f"\n{'='*80}")
print(f"  6月补涨信号汇总 (补涨≥{CATCHUP_MIN})")
print(f"{'='*80}")

if all_results:
    df_all = pd.DataFrame(all_results)

    # 按股票统计出现天数
    print(f"\n  共 {len(all_results)} 条信号, 涉及 {df_all['code'].nunique()} 只股票")

    print(f"\n  按股票出现天数排序:")
    stock_counts = df_all.groupby(['code', 'name']).agg(
        days=('date', 'count'),
        first_date=('date', 'min'),
        last_date=('date', 'max'),
        avg_score=('catchup_score', 'mean'),
        max_score=('catchup_score', 'max'),
    ).sort_values('days', ascending=False)

    print(f"  {'代码':<12}{'名称':<10}{'天数':<6}{'首次':<10}{'末次':<10}{'均分':<8}{'最高':<8}")
    print(f"  {'-'*64}")
    for (code, name), row in stock_counts.head(20).iterrows():
        print(f"  {code:<12}{name:<10}{int(row['days']):<6d}{row['first_date']:<10}{row['last_date']:<10}"
              f"{row['avg_score']:<8.1f}{row['max_score']:<8.1f}")

    # 按日期统计
    print(f"\n  按日期信号数:")
    date_counts = df_all.groupby('date').size()
    for d, cnt in date_counts.items():
        print(f"    {d}: {cnt} 只")

    # 保存CSV
    out_path = r'd:\mystock\solo\etf_resonance\output\backtest_159516_june.csv'
    df_all.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n  已保存: {out_path}")
else:
    print("  6月无补涨分≥70的信号")
