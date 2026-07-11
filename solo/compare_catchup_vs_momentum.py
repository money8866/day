"""对比补涨vs强势前排信号在半导体设备ETF 6月的涨幅和胜率。"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from etf_resonance.core.catchup import CatchupScorer, MomentumScorer
from etf_resonance.utils.helpers import Config
from dotenv import load_dotenv
from multi_factor_picker.data_fetcher import DataFetcher

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

print("=" * 70)
print(f"  补涨 vs 强势前排 对比回测 | {ETF_NAME}ETF({ETF_CODE}) 2026年6月")
print("=" * 70)

# 加载成份股
json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
all_etf_constituents = {}
if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        all_etf_constituents = json.load(f)
stock_codes = [s for s in all_etf_constituents.get(ETF_CODE, [])
               if not s.endswith('.BJ') and s != 'Au9999'][:50]

# 股票名称
try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
except Exception:
    name_map = {}

# 下载日线
START_DATE = '20251001'
END_DATE = '20260715'

print(f"[1] 下载数据...")
etf_df = dfetcher.get_fund_daily(ts_code=ETF_CODE, start_date=START_DATE, end_date=END_DATE)
if etf_df is not None and not etf_df.empty:
    etf_df = etf_df.sort_values('trade_date').reset_index(drop=True)

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
print(f"  ETF: {len(etf_df)}天, 股票: {len(stock_data)}只")

# 6月交易日
cal_df = dfetcher.get_trade_cal(start_date='20260601', end_date='20260630', is_open='1')
june_dates = sorted(cal_df[cal_df['is_open'] == 1]['cal_date'].tolist())
print(f"  6月交易日: {len(june_dates)}天")

# 初始化评分器
config = Config(r'd:\mystock\solo\etf_resonance\config.yaml')
catchup_scorer = CatchupScorer(config)
momentum_scorer = MomentumScorer(config)
constituents = {ETF_CODE: stock_codes}

# 逐日计算信号
print(f"\n[2] 逐日计算信号...")
all_signals = []

for target_date in june_dates:
    etf_cut = etf_df[etf_df['trade_date'] <= target_date].copy()
    if len(etf_cut) < 60:
        continue

    stock_cut = {}
    for code, df in stock_data.items():
        df_cut = df[df['trade_date'] <= target_date].copy()
        if len(df_cut) >= 60:
            stock_cut[code] = df_cut

    etf_data = {ETF_CODE: etf_cut}

    # 补涨信号
    cu_results = catchup_scorer.score(stock_cut, etf_data, constituents, {ETF_CODE: 60.0})
    cu_list = cu_results.get(ETF_CODE, [])
    cu_strong = [r for r in cu_list if r.catchup_score >= CATCHUP_MIN]

    # 强势前排信号
    mom_results = momentum_scorer.score(stock_cut, constituents)
    mom_list = mom_results.get(ETF_CODE, [])
    mom_strong = [r for r in mom_list if r.momentum_score >= MOMENTUM_MIN]

    for r in cu_strong:
        all_signals.append({
            'date': target_date,
            'type': '补涨',
            'code': r.ts_code,
            'name': name_map.get(r.ts_code, ''),
            'score': r.catchup_score,
            'ret_60d': r.ret_60d,
            'ma_cross_days': r.ma_cross_days,
        })
    for r in mom_strong:
        all_signals.append({
            'date': target_date,
            'type': '强势前排',
            'code': r.ts_code,
            'name': name_map.get(r.ts_code, ''),
            'score': r.momentum_score,
            'ret_60d': r.ret_60d,
            'ma_cross_days': 0,
        })

print(f"  共 {len(all_signals)} 条信号 (补涨+强势前排)")

# 计算未来收益
def get_future_returns(code, signal_date, n_days):
    if code not in stock_data:
        return None, None, None
    df = stock_data[code]
    idx = df.index[df['trade_date'] == signal_date].tolist()
    if not idx:
        return None, None, None
    pos = idx[0]
    if pos + n_days >= len(df):
        return None, None, None
    buy_close = df.iloc[pos]['close']
    sell_close = df.iloc[pos + n_days]['close']
    if buy_close <= 0:
        return None, None, None
    ret = (sell_close / buy_close - 1) * 100
    max_high = df.iloc[pos + 1:pos + 1 + n_days]['high'].max()
    min_low = df.iloc[pos + 1:pos + 1 + n_days]['low'].min()
    max_ret = (max_high / buy_close - 1) * 100
    min_ret = (min_low / buy_close - 1) * 100
    return ret, max_ret, min_ret

# 计算每条信号收益
for sig in all_signals:
    ret5, max5, min5 = get_future_returns(sig['code'], sig['date'], 5)
    ret10, max10, min10 = get_future_returns(sig['code'], sig['date'], 10)
    sig['ret5'] = ret5
    sig['max5'] = max5
    sig['min5'] = min5
    sig['ret10'] = ret10
    sig['max10'] = max10
    sig['min10'] = min10

df_all = pd.DataFrame(all_signals)

# ETF基准
def get_etf_ret(signal_date, n_days):
    idx = etf_df.index[etf_df['trade_date'] == signal_date].tolist()
    if not idx:
        return None
    pos = idx[0]
    if pos + n_days >= len(etf_df):
        return None
    return (etf_df.iloc[pos + n_days]['close'] / etf_df.iloc[pos]['close'] - 1) * 100

for sig in all_signals:
    sig['etf_ret5'] = get_etf_ret(sig['date'], 5)
    sig['etf_ret10'] = get_etf_ret(sig['date'], 10)
    sig['excess5'] = (sig['ret5'] - sig['etf_ret5']) if (sig['ret5'] is not None and sig['etf_ret5'] is not None) else None
    sig['excess10'] = (sig['ret10'] - sig['etf_ret10']) if (sig['ret10'] is not None and sig['etf_ret10'] is not None) else None

df_all = pd.DataFrame(all_signals)

# ============== 对比统计 ==============
print(f"\n{'='*90}")
print(f"  补涨 vs 强势前排 5日/10日涨幅对比")
print(f"{'='*90}")

for sig_type in ['补涨', '强势前排']:
    grp = df_all[df_all['type'] == sig_type]
    valid5 = grp[grp['ret5'].notna()]
    valid10 = grp[grp['ret10'].notna()]

    print(f"\n  【{sig_type}信号】 总{len(grp)}条, 5日有效{len(valid5)}, 10日有效{len(valid10)}")
    if len(valid5) > 0:
        win5 = (valid5['ret5'] > 0).sum()
        print(f"    5日:  胜率{win5}/{len(valid5)}={win5/len(valid5)*100:.1f}% | "
              f"平均{valid5['ret5'].mean():+.2f}% | 中位{valid5['ret5'].median():+.2f}% | "
              f"最大涨{valid5['max5'].mean():+.1f}% | 最大跌{valid5['min5'].mean():+.1f}% | "
              f"超额{valid5['excess5'].mean():+.2f}%")
    if len(valid10) > 0:
        win10 = (valid10['ret10'] > 0).sum()
        print(f"    10日: 胜率{win10}/{len(valid10)}={win10/len(valid10)*100:.1f}% | "
              f"平均{valid10['ret10'].mean():+.2f}% | 中位{valid10['ret10'].median():+.2f}% | "
              f"最大涨{valid10['max10'].mean():+.1f}% | 最大跌{valid10['min10'].mean():+.1f}% | "
              f"超额{valid10['excess10'].mean():+.2f}%")

# ============== 重叠分析 ==============
print(f"\n{'='*90}")
print(f"  信号重叠分析")
print(f"{'='*90}")

cu_sigs = df_all[df_all['type'] == '补涨'][['date', 'code', 'name']].copy()
mom_sigs = df_all[df_all['type'] == '强势前排'][['date', 'code', 'name']].copy()
merged = cu_sigs.merge(mom_sigs, on=['date', 'code', 'name'], how='inner')
print(f"  补涨信号: {len(cu_sigs)}条")
print(f"  强势前排: {len(mom_sigs)}条")
print(f"  同日同股重叠: {len(merged)}条")
if len(merged) > 0:
    print(f"  重叠股票:")
    for _, r in merged.iterrows():
        print(f"    {r['date']} {r['code']} {r['name']}")

# ============== 并集对比（去重） ==============
print(f"\n{'='*90}")
print(f"  去重后对比 (同一股同一天只取最高分信号)")
print(f"{'='*90}")

# 按date+code去重，保留高分
df_dedup = df_all.sort_values('score', ascending=False).drop_duplicates(subset=['date', 'code'], keep='first')
print(f"  去重前: {len(df_all)}条 -> 去重后: {len(df_dedup)}条")

for sig_type in ['补涨', '强势前排']:
    grp = df_dedup[df_dedup['type'] == sig_type]
    valid5 = grp[grp['ret5'].notna()]
    valid10 = grp[grp['ret10'].notna()]
    if len(valid5) > 0:
        win5 = (valid5['ret5'] > 0).sum()
        print(f"  【{sig_type}】 去重后{len(grp)}条 | 5日胜率{win5/len(valid5)*100:.1f}% 平均{valid5['ret5'].mean():+.2f}% 超额{valid5['excess5'].mean():+.2f}%")
    if len(valid10) > 0:
        win10 = (valid10['ret10'] > 0).sum()
        print(f"  【{sig_type}】 去重后{len(grp)}条 | 10日胜率{win10/len(valid10)*100:.1f}% 平均{valid10['ret10'].mean():+.2f}% 超额{valid10['excess10'].mean():+.2f}%")

# ============== 按涨幅档位分析 ==============
print(f"\n{'='*90}")
print(f"  按信号发出时60日涨幅档位分析 (5日收益)")
print(f"{'='*90}")

print(f"  {'类型':<8}{'60日涨幅':<12}{'样本':<6}{'胜率':<8}{'平均5日':<10}{'平均超额':<10}")
print(f"  {'-'*60}")
for sig_type in ['补涨', '强势前排']:
    grp = df_all[(df_all['type'] == sig_type) & (df_all['ret5'].notna())]
    for lo, hi, label in [(-100, 0, '<0%'), (0, 10, '0-10%'), (10, 30, '10-30%'), (30, 100, '30%+')]:
        sub = grp[(grp['ret_60d'] >= lo) & (grp['ret_60d'] < hi)]
        if len(sub) > 0:
            w = (sub['ret5'] > 0).sum()
            print(f"  {sig_type:<8}{label:<12}{len(sub):<6d}{w/len(sub)*100:<8.1f}"
                  f"{sub['ret5'].mean():<+10.2f}{sub['excess5'].mean():<+10.2f}")

# ============== 选股策略建议 ==============
print(f"\n{'='*90}")
print(f"  选股策略建议")
print(f"{'='*90}")

# 计算各策略组合的收益
strategies = {
    '只买补涨': df_all[df_all['type'] == '补涨'],
    '只买强势前排': df_all[df_all['type'] == '强势前排'],
    '补涨且60d<10%': df_all[(df_all['type'] == '补涨') & (df_all['ret_60d'] < 10)],
    '强势前排且60d>30%': df_all[(df_all['type'] == '强势前排') & (df_all['ret_60d'] > 30)],
}

print(f"  {'策略':<20}{'样本':<6}{'5日胜率':<10}{'5日均涨':<10}{'5日超额':<10}{'10日胜率':<10}{'10日均涨':<10}")
print(f"  {'-'*80}")
for name, grp in strategies.items():
    v5 = grp[grp['ret5'].notna()]
    v10 = grp[grp['ret10'].notna()]
    w5 = f"{(v5['ret5']>0).sum()}/{len(v5)}={((v5['ret5']>0).sum()/len(v5)*100):.0f}%" if len(v5) > 0 else "N/A"
    w10 = f"{(v10['ret10']>0).sum()}/{len(v10)}={((v10['ret10']>0).sum()/len(v10)*100):.0f}%" if len(v10) > 0 else "N/A"
    r5 = f"{v5['ret5'].mean():+.2f}%" if len(v5) > 0 else "N/A"
    r10 = f"{v10['ret10'].mean():+.2f}%" if len(v10) > 0 else "N/A"
    e5 = f"{v5['excess5'].mean():+.2f}%" if len(v5) > 0 else "N/A"
    print(f"  {name:<20}{len(grp):<6d}{w5:<10}{r5:<10}{e5:<10}{w10:<10}{r10:<10}")

# 保存
out_path = r'd:\mystock\solo\etf_resonance\output\compare_catchup_vs_momentum.csv'
df_all.to_csv(out_path, index=False, encoding='utf-8-sig')
print(f"\n  已保存: {out_path}")
