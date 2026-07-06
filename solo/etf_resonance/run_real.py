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
from etf_resonance.core.catchup import CatchupScorer
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
print("=" * 70)
print(f"ETF 补涨扩散策略 - 盘后复盘 | 输出补涨分 ≥ {CATCHUP_MIN} 的股票")
print("=" * 70)

# 16点前使用上个交易日数据（当日日线16点后才更新）
now = datetime.now()
if now.hour < 16:
    # 取上个交易日
    cal_end = now.strftime('%Y%m%d')
    cal_start = (now - timedelta(days=10)).strftime('%Y%m%d')
    try:
        cal_df = dfetcher.get_trade_cal(start_date=cal_start, end_date=cal_end, is_open='1')
        if cal_df is not None and not cal_df.empty:
            open_dates = sorted(cal_df[cal_df['is_open'] == 1]['cal_date'].tolist())
            # 今天的日历日期
            today_str = now.strftime('%Y%m%d')
            # 取小于今天的前一个交易日
            prev_dates = [d for d in open_dates if d < today_str]
            if prev_dates:
                ANALYSIS_END = prev_dates[-1]
            else:
                ANALYSIS_END = open_dates[-1]
        else:
            ANALYSIS_END = (now - timedelta(days=1)).strftime('%Y%m%d')
    except Exception as e:
        print(f"  ⚠️ 获取交易日历失败: {e}，使用昨日")
        ANALYSIS_END = (now - timedelta(days=1)).strftime('%Y%m%d')
    print(f"\n  ⏰ 当前 {now.strftime('%H:%M')} < 16:00，使用上个交易日数据: {ANALYSIS_END}")
else:
    ANALYSIS_END = now.strftime('%Y%m%d')
    print(f"\n  🕓 当前 {now.strftime('%H:%M')} >= 16:00，使用当日数据: {ANALYSIS_END}")

# 下载范围：固定按年对齐，让 cache_key 稳定（7天内命中缓存，避免每天重复下载）
year = now.year
START_DATE = f'{year-2}0101'   # 2年前1月1日（足够计算所有指标）
END_DATE = f'{year}1231'       # 今年12月31日（API只返回已有数据）
print(f"  下载范围: {START_DATE} ~ {END_DATE}（固定，复用缓存）")

print(f"\n[1] 加载成份股（前50只/ETF）...")

# 优先读取本地汇总JSON，缺失的ETF回退到 DataFetcher.get_etf_cons()
all_etf_constituents = {}
json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        all_etf_constituents = json.load(f)
print(f"  本地JSON覆盖: {len(all_etf_constituents)} 只ETF")

missing_etfs = [c for c in ETF_THEME_MAP if c not in all_etf_constituents]
if missing_etfs:
    print(f"  JSON缺失 {len(missing_etfs)} 只，回退到 DataFetcher 补充...")
    for etf_code in missing_etfs:
        try:
            cons_df = dfetcher.get_etf_cons(ts_code=etf_code)
            if cons_df is not None and not cons_df.empty:
                latest = cons_df['trade_date'].max()
                cons_df = cons_df[cons_df['trade_date'] == latest].sort_values('cpr', ascending=False)
                stocks = [c for c in cons_df['con_code'].tolist()
                          if not str(c).endswith('.BJ') and c != 'Au9999']
                all_etf_constituents[etf_code] = stocks
                print(f"    {etf_code} ({ETF_THEME_MAP[etf_code]}): {len(stocks)} 只")
        except Exception as e:
            print(f"    {etf_code} 获取失败: {e}")

# 取前50只
constituents = {}
all_stocks = set()
for etf_code in ETF_THEME_MAP:
    if etf_code in all_etf_constituents:
        stocks = [s for s in all_etf_constituents[etf_code]
                  if not s.endswith('.BJ') and s != 'Au9999'][:50]
        constituents[etf_code] = stocks
        all_stocks.update(stocks)
print(f"  成份股总数: {len(all_stocks)} 只 (跨 {len(constituents)} 个ETF)")

# 获取股票名称
print("\n  获取股票名称...")
try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
    industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
except Exception as e:
    print(f"    获取失败: {e}")
    name_map = {}
    industry_map = {}

# ============== 2. 下载日线数据 ==============
print(f"\n[2] 加载日线数据（优先缓存）{START_DATE} ~ {END_DATE}...")

print("  加载ETF日线...")
etf_data = {}
for etf_code in ETF_THEME_MAP:
    try:
        df = dfetcher.get_fund_daily(ts_code=etf_code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            # 切片到 ANALYSIS_END（避免16点前用到当日未完成数据）
            df = df[df['trade_date'] <= ANALYSIS_END]
            etf_data[etf_code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
print(f"  ETF: {len(etf_data)} 个")

print("  加载股票日线...")
stock_data = {}
for i, code in enumerate(sorted(all_stocks)):
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            # 切片到 ANALYSIS_END
            df = df[df['trade_date'] <= ANALYSIS_END]
            if 'vol' not in df.columns and 'volume' in df.columns:
                df['vol'] = df['volume']
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
    if (i + 1) % 200 == 0:
        print(f"    进度: {i+1}/{len(all_stocks)}")
print(f"  股票: {len(stock_data)}/{len(all_stocks)}")

# ============== 3. 计算ETF趋势分 ==============
print("\n[3] 计算ETF趋势分...")
config = Config(r'd:\mystock\solo\etf_resonance\config.yaml')
trend_scorer = TrendScorer(config)
persist_scorer = PersistenceScorer(config)
catchup_scorer = CatchupScorer(config)
diffusion_scorer = DiffusionScorer(config)

trend_results = trend_scorer.score(etf_data)
persist_results = persist_scorer.score(etf_data)

print("\n  ETF趋势评分：")
for code, tr in sorted(trend_results.items(), key=lambda x: -x[1].trend_score):
    pr = persist_results.get(code)
    ps = pr.persistence_score if pr else 0
    flag = '✓' if (tr.trend_score >= 55 and ps >= 40 and tr.ema20_above_ema60) else ' '
    print(f"  {flag} {code} ({ETF_THEME_MAP.get(code,'')}): Trend={tr.trend_score} "
          f"Persist={ps} ADX={tr.adx_val} ret60={tr.return_60d}% ema20>ema60={tr.ema20_above_ema60}")

# 过滤趋势ETF
qualifying_etfs = {}
for code, tr in trend_results.items():
    pr = persist_results.get(code)
    if (tr.trend_score >= 55 and pr and pr.persistence_score >= 40
        and tr.ema20_above_ema60):
        qualifying_etfs[code] = tr

if not qualifying_etfs:
    # 退而求其次：取趋势分Top 5
    print(f"\n  ⚠️ 无ETF满足严格条件（Trend≥55 & Persist≥40 & ema20>ema60），取Top 5")
    qualifying_etfs = dict(sorted(trend_results.items(), key=lambda x: -x[1].trend_score)[:5])

print(f"\n  趋势ETF: {len(qualifying_etfs)} 个")

# ============== 4. 计算扩散度 ==============
print("\n[4] 计算主题扩散度...")
qualifying_constituents = {c: constituents[c] for c in qualifying_etfs if c in constituents}
diffusion_results = diffusion_scorer.score(
    stock_data, etf_data, qualifying_constituents, ETF_THEME_MAP
)

# 扩散度 > 50 的ETF
diffused_etfs = {c: r for c, r in diffusion_results.items() if r.diffusion_score > 50}
if not diffused_etfs:
    print("  ⚠️ 无ETF扩散度>50，取Top 3")
    diffused_etfs = dict(sorted(diffusion_results.items(), key=lambda x: -x[1].diffusion_score)[:3])

print("\n  扩散ETF：")
for c, r in sorted(diffused_etfs.items(), key=lambda x: -x[1].diffusion_score):
    print(f"    {c} ({ETF_THEME_MAP.get(c,'')}): Diffusion={r.diffusion_score:.1f} "
          f"breadth={r.breadth_expansion:.1f} rotation={r.rotation_signal:.1f}")

# ============== 5. 计算补涨评分 ==============
print("\n[5] 计算补涨评分...")
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
print(f"  补涨候选总数: {total_catchup} 只 (跨 {len(catchup_results)} 个ETF)")

# ============== 6. 汇总并过滤 catchup_score >= CATCHUP_MIN ==============
print(f"\n[6] 筛选补涨分 ≥ {CATCHUP_MIN} 的股票...")

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

# 排序
all_candidates.sort(key=lambda x: -x['catchup_score'])

# 过滤补涨分 >= CATCHUP_MIN
strong_candidates = [c for c in all_candidates if c['catchup_score'] >= CATCHUP_MIN]

# ============== 7. 输出结果 ==============
print("\n" + "=" * 70)
print(f"  📊 补涨分 ≥ {CATCHUP_MIN} 的股票: {len(strong_candidates)} 只")
print("=" * 70)

if not strong_candidates:
    print("\n  今日无补涨分≥70的股票。")
    print(f"\n  参考：补涨分Top 10（未达阈值）:")
    for c in all_candidates[:10]:
        print(f"    {c['code']} {c['stock_name']} | {c['etf_code']} {c['etf_name']} | "
              f"补涨={c['catchup_score']:.1f} | 滞涨={c['lag_degree']:.1f} | "
              f"启动={c['startup_signal']:.1f} | 落后ETF {c['ret_gap']:.1f}% | "
              f"量比5d={c['vol_ratio_5d']:.2f}")
else:
    print(f"\n{'代码':<12}{'名称':<10}{'ETF':<18}{'补涨分':<8}{'滞涨':<6}{'启动':<6}"
          f"{'落后ETF':<10}{'量比5d':<8}{'距低点':<8}{'最终分':<8}")
    print("-" * 110)
    for c in strong_candidates:
        print(f"{c['code']:<12}{c['stock_name']:<10}{c['etf_code']+' '+c['etf_name']:<18}"
              f"{c['catchup_score']:<8.1f}{c['lag_degree']:<6.1f}{c['startup_signal']:<6.1f}"
              f"{c['ret_gap']:+.1f}%{'':>3}{c['vol_ratio_5d']:<8.2f}"
              f"{c['dist_to_low']:+.1f}%{'':>3}{c['final_score']:<8.1f}")

    # 保存CSV
    df_out = pd.DataFrame(strong_candidates)
    output_path = r'd:\mystock\solo\etf_resonance\output\catchup_signals.csv'
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df_out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n[已保存] {output_path}")

    # 按ETF分组统计
    print(f"\n📊 按ETF分组:")
    etf_grp = df_out.groupby(['etf_code', 'etf_name']).agg(
        count=('code', 'count'),
        avg_catchup=('catchup_score', 'mean'),
        max_catchup=('catchup_score', 'max'),
    ).round(1)
    print(etf_grp.to_string())

print("\n" + "=" * 70)
print(f"  全候选: {len(all_candidates)} 只 | 强信号(≥{CATCHUP_MIN}): {len(strong_candidates)} 只")
print("=" * 70)
