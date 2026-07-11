"""ETF 补涨扩散策略 - 每日盘后复盘脚本。

基于回测验证：补涨分 70+ 胜率显著高于其他区间，本脚本只输出补涨分 ≥ 70 的股票。

流程：
  1. 加载ETF池（35个行业ETF）+ 成份股（前50只）
  2. 下载ETF/股票日线数据（DataFetcher缓存+限流）
  3. 计算ETF趋势分 → 过滤趋势ETF
  4. 计算主题扩散度 → 过滤扩散ETF
  5. 计算补涨评分 → 输出 catchup_score >= 70 的股票
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from etf_resonance.core.trend import TrendScorer
from etf_resonance.core.persistence import PersistenceScorer
from etf_resonance.core.catchup import CatchupScorer, MomentumScorer
from etf_resonance.core.diffusion import DiffusionScorer
from etf_resonance.utils.helpers import Config
from multi_factor_picker.data_fetcher import DataFetcher
from dotenv import load_dotenv

# ============== 配置 ==============
load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

# 补涨分阈值（回测验证70+胜率最高）
CATCHUP_MIN = 70

# 强势前排分阈值
MOMENTUM_MIN = 60

# ETF 池（35个行业ETF，已去除指数型）
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

# ============== 1. 加载成份股 ==============
# 16点前使用上个交易日数据（当日日线16点后才更新）
now = datetime.now()
if now.hour < 16:
    cal_end = now.strftime('%Y%m%d')
    cal_start = (now - timedelta(days=10)).strftime('%Y%m%d')
    try:
        cal_df = dfetcher.get_trade_cal(start_date=cal_start, end_date=cal_end, is_open='1')
        if cal_df is not None and not cal_df.empty:
            open_dates = sorted(cal_df[cal_df['is_open'] == 1]['cal_date'].tolist())
            today_str = now.strftime('%Y%m%d')
            prev_dates = [d for d in open_dates if d < today_str]
            if prev_dates:
                ANALYSIS_END = prev_dates[-1]
            else:
                ANALYSIS_END = open_dates[-1]
        else:
            ANALYSIS_END = (now - timedelta(days=1)).strftime('%Y%m%d')
    except Exception:
        ANALYSIS_END = (now - timedelta(days=1)).strftime('%Y%m%d')
else:
    ANALYSIS_END = now.strftime('%Y%m%d')

START_DATE = f'{int(ANALYSIS_END[:4])-2}0101'
END_DATE = ANALYSIS_END

print("=" * 70)
print(f"  ETF 补涨扩散+强势前排策略 | {ANALYSIS_END} | 补涨≥{CATCHUP_MIN} 强势≥{MOMENTUM_MIN}")
print("=" * 70)

print("\n[1] 加载成份股...")
all_etf_constituents = {}
json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        all_etf_constituents = json.load(f)

missing_etfs = [c for c in ETF_THEME_MAP if c not in all_etf_constituents]
if missing_etfs:
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

constituents = {}
all_stocks = set()
for etf_code in ETF_THEME_MAP:
    if etf_code in all_etf_constituents:
        stocks = [s for s in all_etf_constituents[etf_code]
                  if not s.endswith('.BJ') and s != 'Au9999'][:50]
        constituents[etf_code] = stocks
        all_stocks.update(stocks)

try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
    industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
except Exception:
    name_map = {}
    industry_map = {}

# ============== 2. 下载日线数据 ==============
print("[2] 加载日线...")
etf_data = {}
for etf_code in ETF_THEME_MAP:
    try:
        df = dfetcher.get_fund_daily(ts_code=etf_code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            df = df[df['trade_date'] <= ANALYSIS_END]
            etf_data[etf_code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass

stock_data = {}
for code in sorted(all_stocks):
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            df = df[df['trade_date'] <= ANALYSIS_END]
            if 'vol' not in df.columns and 'volume' in df.columns:
                df['vol'] = df['volume']
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
print(f"  ETF: {len(etf_data)} 个 | 股票: {len(stock_data)} 只")

# ============== 3. 计算ETF趋势分 ==============
config = Config(r'd:\mystock\solo\etf_resonance\config.yaml')
trend_scorer = TrendScorer(config)
persist_scorer = PersistenceScorer(config)
catchup_scorer = CatchupScorer(config)
momentum_scorer = MomentumScorer(config)
diffusion_scorer = DiffusionScorer(config)

trend_results = trend_scorer.score(etf_data)
persist_results = persist_scorer.score(etf_data)

qualifying_etfs = {}
for code, tr in trend_results.items():
    pr = persist_results.get(code)
    if (tr.trend_score >= 55 and pr and pr.persistence_score >= 40
        and tr.ema20_above_ema60):
        qualifying_etfs[code] = tr

if not qualifying_etfs:
    qualifying_etfs = dict(sorted(trend_results.items(), key=lambda x: -x[1].trend_score)[:5])

print(f"[3] 趋势ETF: {len(qualifying_etfs)} 个")

# ============== 4. 计算扩散度 ==============
qualifying_constituents = {c: constituents[c] for c in qualifying_etfs if c in constituents}
diffusion_results = diffusion_scorer.score(
    stock_data, etf_data, qualifying_constituents, ETF_THEME_MAP
)

diffused_etfs = {c: r for c, r in diffusion_results.items() if r.diffusion_score > 50}
if not diffused_etfs:
    diffused_etfs = dict(sorted(diffusion_results.items(), key=lambda x: -x[1].diffusion_score)[:3])

# 趋势通过的ETF全部参与评分（扩散度仅作参考分，不作为硬过滤）
for c in qualifying_etfs:
    if c not in diffused_etfs:
        diffused_etfs[c] = diffusion_results.get(c,
            type('R', (), {'diffusion_score': 50})())

print(f"[4] 扩散ETF: {len(diffused_etfs)} 个")

# ============== 5. 计算补涨评分 ==============
filtered_constituents = {c: qualifying_constituents[c] for c in diffused_etfs
                         if c in qualifying_constituents}
if not filtered_constituents:
    print("  ❌ 无候选成份股")
    sys.exit(0)

catchup_results = catchup_scorer.score(
    stock_data, etf_data, filtered_constituents,
    {c: tr.trend_score for c, tr in qualifying_etfs.items()}
)
total_catchup = sum(len(v) for v in catchup_results.values())

# ============== 5b. 计算强势前排评分 ==============
momentum_results = momentum_scorer.score(stock_data, filtered_constituents)
total_momentum = sum(len(v) for v in momentum_results.values())

# ============== 6. 汇总并过滤 catchup_score >= CATCHUP_MIN ==============
all_candidates = []
for etf_code, results in catchup_results.items():
    diff_score = diffused_etfs.get(etf_code)
    diff_val = diff_score.diffusion_score if diff_score else 50
    for r in results:
        final_score = (
            r.catchup_score * 0.6 +
            diff_val * 0.3 +
            qualifying_etfs[etf_code].trend_score * 0.1
        )
        all_candidates.append({
            'code': r.ts_code,
            'stock_name': name_map.get(r.ts_code, ''),
            'industry': industry_map.get(r.ts_code, ''),
            'etf_code': etf_code,
            'etf_name': ETF_THEME_MAP.get(etf_code, ''),
            'catchup_score': r.catchup_score,
            'trend_setup': r.trend_setup,
            'vol_gentle': r.vol_gentle,
            'gain_moderate': r.gain_moderate,
            'no_limit_up': r.no_limit_up,
            'catchup_space': r.catchup_space,
            'ret_60d': r.ret_60d,
            'etf_ret_60d': r.etf_ret_60d,
            'ret_gap': r.ret_gap,
            'dist_to_low': r.dist_to_low,
            'dist_to_high': r.dist_to_high,
            'vol_ratio_5d': r.vol_ratio_5d,
            'limit_up_5d': r.limit_up_5d,
            'ma_cross_days': r.ma_cross_days,
            'diffusion_score': diff_val,
            'etf_trend': qualifying_etfs[etf_code].trend_score,
            'final_score': round(final_score, 2),
        })

all_candidates.sort(key=lambda x: -x['catchup_score'])
strong_candidates = [c for c in all_candidates if c['catchup_score'] >= CATCHUP_MIN]

# ============== 6b. 汇总强势前排 ==============
momentum_candidates = []
for etf_code, results in momentum_results.items():
    for r in results:
        final_mom = (
            r.momentum_score * 0.7 +
            diffused_etfs.get(etf_code, type('', (), {'diffusion_score': 50})()).diffusion_score * 0.2 +
            qualifying_etfs[etf_code].trend_score * 0.1
        )
        momentum_candidates.append({
            'code': r.ts_code,
            'stock_name': name_map.get(r.ts_code, ''),
            'industry': industry_map.get(r.ts_code, ''),
            'etf_code': etf_code,
            'etf_name': ETF_THEME_MAP.get(etf_code, ''),
            'momentum_score': r.momentum_score,
            'new_high_score': r.new_high_score,
            'trend_60d_score': r.trend_60d_score,
            'mom_20d_score': r.mom_20d_score,
            'today_surge_score': r.today_surge_score,
            'vol_surge_score': r.vol_surge_score,
            'ret_60d': r.ret_60d,
            'ret_20d': r.ret_20d,
            'today_pct': r.today_pct,
            'dist_to_high': r.dist_to_high,
            'vol_ratio_5d': r.vol_ratio_5d,
            'diffusion_score': diffused_etfs.get(etf_code, type('', (), {'diffusion_score': 50})()).diffusion_score,
            'etf_trend': qualifying_etfs[etf_code].trend_score,
            'final_score': round(final_mom, 2),
        })

momentum_candidates.sort(key=lambda x: -x['momentum_score'])
strong_momentum = [c for c in momentum_candidates if c['momentum_score'] >= MOMENTUM_MIN]

# ============== 7. 输出结果 ==============
df_out = pd.DataFrame(strong_candidates) if strong_candidates else pd.DataFrame()

if not strong_candidates:
    print(f"\n[结果] 无补涨分≥{CATCHUP_MIN}的股票。")
    print(f"  参考 Top 10:")
    for c in all_candidates[:10]:
        print(f"    {c['code']} {c['stock_name']} | {c['etf_code']} {c['etf_name']} | "
              f"补涨={c['catchup_score']:.1f} | 趋势={c['trend_setup']:.0f} | "
              f"量温={c['vol_gentle']:.0f} | 60d={c['ret_60d']:+.1f}% | "
              f"涨停{c['limit_up_5d']}天 | 交叉{c['ma_cross_days']}天")
else:
    print(f"\n[结果] 补涨分 ≥ {CATCHUP_MIN}: {len(strong_candidates)} 只")
    print(f"  {'代码':<12}{'名称':<10}{'ETF':<18}{'补涨分':<8}{'趋势':<6}{'量温':<6}"
          f"{'涨幅':<6}{'涨停':<6}{'60d':<8}{'量比':<6}{'交叉':<6}{'最终分':<8}")
    print(f"  {'-'*92}")
    for c in strong_candidates:
        print(f"  {c['code']:<12}{c['stock_name']:<10}{c['etf_code']+' '+c['etf_name']:<18}"
              f"{c['catchup_score']:<8.1f}{c['trend_setup']:<6.0f}{c['vol_gentle']:<6.0f}"
              f"{c['gain_moderate']:<6.0f}{c['limit_up_5d']:<6d}"
              f"{c['ret_60d']:+.1f}%{'':>2}{c['vol_ratio_5d']:<6.2f}"
              f"{c['ma_cross_days']:<6d}{c['final_score']:<8.1f}")

    if not df_out.empty:
        print(f"\n  ETF分布:")
        for (ec, en), grp in df_out.groupby(['etf_code', 'etf_name']):
            print(f"    {ec} {en}: {len(grp)} 只, 均分{grp['catchup_score'].mean():.1f}")

# 始终保存CSV（即使为空），供下游程序消费
output_path = r'd:\mystock\solo\etf_resonance\output\catchup_signals.csv'
os.makedirs(os.path.dirname(output_path), exist_ok=True)
df_out.to_csv(output_path, index=False, encoding='utf-8-sig')
print(f"\n  已保存: {output_path} ({len(strong_candidates)} 条)")

# ============== 8. 输出强势前排结果 ==============
df_mom = pd.DataFrame(strong_momentum) if strong_momentum else pd.DataFrame()

if not strong_momentum:
    print(f"\n[强势前排] 无分≥{MOMENTUM_MIN}的股票。")
    print(f"  参考 Top 10:")
    for c in momentum_candidates[:10]:
        print(f"    {c['code']} {c['stock_name']} | {c['etf_code']} {c['etf_name']} | "
              f"强势={c['momentum_score']:.1f} | 创新高={c['new_high_score']:.0f} | "
              f"60d={c['ret_60d']:+.1f}% | 今日={c['today_pct']:+.1f}%")
else:
    print(f"\n[强势前排] 分 ≥ {MOMENTUM_MIN}: {len(strong_momentum)} 只")
    print(f"  {'代码':<12}{'名称':<10}{'ETF':<18}{'强势分':<8}{'新高':<6}{'60d':<6}"
          f"{'20d':<6}{'今日':<8}{'距高':<8}{'最终分':<8}")
    print(f"  {'-'*92}")
    for c in strong_momentum:
        print(f"  {c['code']:<12}{c['stock_name']:<10}{c['etf_code']+' '+c['etf_name']:<18}"
              f"{c['momentum_score']:<8.1f}{c['new_high_score']:<6.0f}"
              f"{c['ret_60d']:+6.1f}{'':>1}{c['ret_20d']:+6.1f}{'':>1}"
              f"{c['today_pct']:+.1f}%{'':>3}{c['dist_to_high']:+.1f}%{'':>2}"
              f"{c['final_score']:<8.1f}")

    if not df_mom.empty:
        print(f"\n  ETF分布:")
        for (ec, en), grp in df_mom.groupby(['etf_code', 'etf_name']):
            print(f"    {ec} {en}: {len(grp)} 只, 均分{grp['momentum_score'].mean():.1f}")

mom_path = r'd:\mystock\solo\etf_resonance\output\trend_leaders.csv'
df_mom.to_csv(mom_path, index=False, encoding='utf-8-sig')
print(f"\n  已保存: {mom_path} ({len(strong_momentum)} 条)")
