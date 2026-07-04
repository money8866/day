# -*- coding: utf-8 -*-
"""
二波行情专项回测：双创板（创业板+科创板）
对比分析双创板与主板（沪深300）的二波形态差异
"""
import os, sys, time, datetime, json, io
sys.path.insert(0, r'D:\mystock')

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

_log_fpath = os.path.join(OUT_DIR, 'wave2_sc_log.txt')
_log_file = open(_log_fpath, 'w', encoding='utf-8', buffering=1)
_orig_stdout = sys.stdout
class _DualWriter:
    def write(self, s):
        try:
            if isinstance(s, bytes):
                _orig_stdout.buffer.write(s)
            else:
                _orig_stdout.write(s)
        except Exception:
            pass
        _log_file.write(s)
        _log_file.flush()
    def flush(self):
        try: _orig_stdout.flush()
        except Exception: pass
        _log_file.flush()
sys.stdout = _DualWriter()

if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
import pandas as pd
import numpy as np
import tushare as ts
from collections import defaultdict

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# ── DataFetcher 单例（统一缓存+限频，降级回退到 pro 直调）──
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from data_fetcher import DataFetcher

_df_singleton = None
def _get_df():
    global _df_singleton
    if _df_singleton is not None:
        return _df_singleton
    try:
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('TUSHARE_TOKEN=') and not line.startswith('#'):
                            token = line.split('=', 1)[1].strip()
                            break
        if not token:
            return None
        config = {'cache': {'enabled': True, 'dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache'), 'expire_hours': 168}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
        _df_singleton = DataFetcher(token, config)
    except Exception:
        return None
    return _df_singleton

_dfetch = _get_df()

def _get_stk_factor_pro_range(ts_code, start, end):
    """按股票+日期范围获取stk_factor_pro（DataFetcher仅有按trade_date的接口，这里用通用缓存+限频包裹范围查询）"""
    if _dfetch is not None:
        cache_key = f"stk_factor_pro_range_{_dfetch._safe_name(ts_code)}_{start}_{end}"
        return _dfetch._get_df_cached(cache_key, _dfetch.pro.stk_factor_pro,
                                       ts_code=ts_code, start_date=start, end_date=end)
    return pro.stk_factor_pro(ts_code=ts_code, start_date=start, end_date=end)

# ═══════════════════════════════════════════════════════
# 参数（与主回测一致）
# ═══════════════════════════════════════════════════════
START_DATE = '20240101'
END_DATE   = '20260620'
SURGE_DAYS = 20
SURGE_MIN  = 0.20
ADJUST_MAX = 60
WAVE2_WINDOW = 20
WAVE2_MIN = 0.10
WAVE2_LONG = 60

print(f"{'='*80}")
print(f"二波行情双创板（创业板+科创板）专项回测")
print(f"回测区间: {START_DATE} ~ {END_DATE}")
print(f"{'='*80}\n")

# ── Step 1: 获取双创板成分股 ────────────────────────
print("[Step 1] 获取双创板成分股（创业板+科创板）...")
if _dfetch is not None:
    sb = _dfetch.get_stock_list('L')
else:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
# 创业板(300) + 科创板(688)
cy = sb[sb['ts_code'].str.startswith(('300', '688'))]
stocks = cy['ts_code'].tolist()
print(f"  创业板+科创板: {len(stocks)} 只（创业板={sum(1 for s in stocks if s.startswith('300'))}，科创板={sum(1 for s in stocks if s.startswith('688'))}）")
time.sleep(0.1)

# ── Step 2: 获取上证指数 ──────────────────────────────
print("[Step 2] 获取上证指数...")
if _dfetch is not None:
    idx_df = _dfetch.get_index_daily(ts_code='000001.SH', start_date=START_DATE, end_date=END_DATE)
else:
    idx_df = pro.index_daily(ts_code='000001.SH', start_date=START_DATE, end_date=END_DATE)
idx_df = idx_df.sort_values('trade_date').reset_index(drop=True)
idx_dates = set(idx_df['trade_date'].tolist())
print(f"  交易日: {len(idx_df)} 天")
time.sleep(0.1)

# ── Step 3: 批量获取日线+技术因子 ────────────────────
print("[Step 3] 获取股票日线数据和技术因子...")

def load_stock_data(ts_code, start=START_DATE, end=END_DATE):
    try:
        if _dfetch is not None:
            daily = _dfetch.get_daily_by_code(ts_code=ts_code, start_date=start, end_date=end)
        else:
            daily = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if daily is None or len(daily) < 60:
            return None
        daily = daily.sort_values('trade_date').reset_index(drop=True)
        factor = _get_stk_factor_pro_range(ts_code, start, end)
        time.sleep(0.06)
        if _dfetch is not None:
            basic = _dfetch.get_daily_basic_by_code(ts_code=ts_code, start_date=start, end_date=end)
        else:
            basic = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end,
                                    fields='ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb')
        time.sleep(0.06)
        if _dfetch is not None:
            mf = _dfetch.get_moneyflow_by_code(ts_code=ts_code, start_date=start, end_date=end)
        else:
            mf = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
        time.sleep(0.06)
        factor_rename = {
            'ma_bfq_5': 'ma5', 'ma_bfq_10': 'ma10', 'ma_bfq_20': 'ma20', 'ma_bfq_60': 'ma60',
            'macd_bfq': 'macd', 'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea',
            'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
            'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d', 'kdj_bfq': 'kdj_j',
            'boll_upper_bfq': 'boll_upper', 'boll_mid_bfq': 'boll_mid', 'boll_lower_bfq': 'boll_lower',
            'cci_bfq': 'cci',
        }
        valid_cols = ['trade_date'] + [k for k in factor_rename if k in factor.columns]
        valid_rename = {k: v for k, v in factor_rename.items() if k in factor.columns}
        factor_subset = factor[valid_cols].rename(columns=valid_rename)
        df = daily.merge(factor_subset, on='trade_date', how='left')
        df = df.merge(basic[['trade_date','turnover_rate','volume_ratio','pe_ttm','pb']],
                      on='trade_date', how='left')
        df = df.merge(mf[['trade_date','net_mf_amount','buy_lg_amount','sell_lg_amount']],
                      on='trade_date', how='left')
        df = df[df['vol'] > 0].reset_index(drop=True)
        # MA 已从 stk_factor_pro 获取，无需手动 rolling
        df['pct_5d'] = df['close'].pct_change(5)
        df['pct_10d'] = df['close'].pct_change(10)
        df['pct_20d'] = df['close'].pct_change(20)
        return df
    except Exception as e:
        return None

all_data = {}
total = len(stocks)
for i, code in enumerate(stocks):
    if (i+1) % 50 == 0 or i == 0:
        print(f"  进度: {i+1}/{total} ({code})...")
    df = load_stock_data(code)
    if df is not None and len(df) >= 60:
        all_data[code] = df
    time.sleep(0.06)

print(f"\n  成功获取: {len(all_data)} 只股票数据")

# ── Step 4: 识别形态 ────────────────────────────────
print("\n[Step 4] 识别一波拉升和调整形态...")

surge_cases = []

for code, df in all_data.items():
    n = len(df)
    closes = df['close'].values
    volumes = df['vol'].values
    dates = df['trade_date'].values

    for i in range(SURGE_DAYS, n - ADJUST_MAX - WAVE2_LONG):
        window_closes = closes[i-SURGE_DAYS:i+1]
        low_idx_win = np.argmin(window_closes)
        high_idx_win = np.argmax(window_closes)
        if high_idx_win <= low_idx_win:
            continue
        if (high_idx_win - low_idx_win) > SURGE_DAYS - 2:
            continue
        surge_gain = (window_closes[high_idx_win] - window_closes[low_idx_win]) / window_closes[low_idx_win]
        if surge_gain < SURGE_MIN:
            continue

        wave1_high_idx = i - SURGE_DAYS + high_idx_win
        wave1_low_idx = i - SURGE_DAYS + low_idx_win
        wave1_high_price = closes[wave1_high_idx]

        post_high = closes[wave1_high_idx:]
        if len(post_high) < 5:
            continue

        low_after_high = post_high.min()
        pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price

        if pullback_pct < 0.02:
            continue

        # 排除ST/退市（检查名称）
        name = code  # 简化处理

        # 调整期天数
        low_pos = np.argmin(post_high)
        adjust_days = int(low_pos)
        if adjust_days > ADJUST_MAX:
            continue

        # 量能比
        if wave1_high_idx >= 20:
            base_vol = volumes[wave1_high_idx-20:wave1_high_idx].mean()
        else:
            base_vol = volumes[:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
        vol_ratio = post_high[:adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0

        # 技术指标（调整低点位置）
        low_idx_in_df = wave1_high_idx + low_pos
        rsi_at_low = df.iloc[low_idx_in_df]['rsi_6'] if not np.isnan(df.iloc[low_idx_in_df]['rsi_6']) else 50.0

        macd_dif_at_low = df.iloc[low_idx_in_df]['macd_dif']
        macd_dea_at_low = df.iloc[low_idx_in_df]['macd_dea']
        macd_golden = (macd_dif_at_low > macd_dea_at_low) if (not np.isnan(macd_dif_at_low) and not np.isnan(macd_dea_at_low)) else False

        kdj_j_at_low = df.iloc[low_idx_in_df]['kdj_j'] if not np.isnan(df.iloc[low_idx_in_df]['kdj_j']) else 50.0
        cci_at_low = df.iloc[low_idx_in_df]['cci'] if not np.isnan(df.iloc[low_idx_in_df]['cci']) else 0.0

        # MA位置
        if low_idx_in_df < len(df):
            ma20_at_low = df.iloc[low_idx_in_df]['ma20'] if not np.isnan(df.iloc[low_idx_in_df]['ma20']) else 0.0
            ma60_at_low = df.iloc[low_idx_in_df]['ma60'] if not np.isnan(df.iloc[low_idx_in_df]['ma60']) else 0.0
            above_ma20 = closes[low_idx_in_df] > ma20_at_low if ma20_at_low > 0 else False
            above_ma60 = closes[low_idx_in_df] > ma60_at_low if ma60_at_low > 0 else False
        else:
            above_ma20 = False
            above_ma60 = False

        # 二波判断（调整低点后WAVE2_WINDOW日涨幅）
        if low_idx_in_df + WAVE2_WINDOW < n:
            post_low = closes[low_idx_in_df:]
            wave2_gain = (post_low[WAVE2_WINDOW] - closes[low_idx_in_df]) / closes[low_idx_in_df] if closes[low_idx_in_df] > 0 else 0.0
            wave2_20d_gain = (post_low[min(20, len(post_low)-1)] - closes[low_idx_in_df]) / closes[low_idx_in_df] if closes[low_idx_in_df] > 0 else 0.0
            wave2_60d_gain = (post_low[min(60, len(post_low)-1)] - closes[low_idx_in_df]) / closes[low_idx_in_df] if closes[low_idx_in_df] > 0 else 0.0
            wave2_60d_max = (post_low[:min(60, len(post_low))].max() - closes[low_idx_in_df]) / closes[low_idx_in_df] if closes[low_idx_in_df] > 0 else 0.0
        else:
            wave2_gain = wave2_20d_gain = wave2_60d_gain = wave2_60d_max = 0.0

        wave2_confirmed = 1 if wave2_gain >= WAVE2_MIN else 0

        # 盈亏比（60日最大涨幅/止损5%）
        stop_loss = 0.05
        rr = wave2_60d_max / stop_loss if stop_loss > 0 else 0.0

        # 形态分类
        v_crash = (adjust_days <= 10 and pullback_pct > 0.10)
        vol_surge = (vol_ratio > 0.80)
        deep_pullback = (pullback_pct >= 0.20)
        narrow_pullback = (pullback_pct < 0.10 and adjust_days <= 15)

        if v_crash:
            pattern = 'V型急跌'
        elif narrow_pullback:
            pattern = '强势横盘'
        elif not deep_pullback and vol_surge:
            pattern = '放量回调'
        elif not deep_pullback and not vol_surge:
            pattern = '缩量回调'
        elif deep_pullback and adjust_days >= 10:
            # 三角收敛检测
            triangle = False
            if adjust_days >= 10:
                ranges = []
                step = max(5, adjust_days // 4)
                for k in range(0, adjust_days - 2, step):
                    chunk = post_high[k:min(k+step, adjust_days+1)]
                    if len(chunk) >= 3:
                        ranges.append(chunk.max() - chunk.min())
                if len(ranges) >= 2 and ranges[-1] < ranges[0] * 0.80:
                    triangle = True
            pattern = '三角收敛' if triangle else '深度回调'
        else:
            pattern = '深度回调'

        surge_cases.append({
            'ts_code': code,
            'board': 'CY' if code.startswith('300') else 'KC',
            'pattern': pattern,
            'surge_gain': surge_gain,
            'pullback_pct': pullback_pct,
            'adjust_days': adjust_days,
            'vol_ratio': vol_ratio,
            'wave2_confirmed': wave2_confirmed,
            'wave2_gain': wave2_gain,
            'wave2_20d_gain': wave2_20d_gain,
            'wave2_60d_gain': wave2_60d_gain,
            'wave2_60d_max': wave2_60d_max,
            'rsi_at_low': rsi_at_low,
            'macd_golden': macd_golden,
            'kdj_j_at_low': kdj_j_at_low,
            'cci_at_low': cci_at_low,
            'above_ma20': above_ma20,
            'above_ma60': above_ma60,
            'rr': rr,
        })

cases_df = pd.DataFrame(surge_cases)
print(f"  识别拉升案例: {len(cases_df)} 个")

# ── Step 5: 形态统计 ────────────────────────────────
print("\n[Step 5] 形态统计分析...")

pattern_stats = {}
for pattern in cases_df['pattern'].unique():
    sub = cases_df[cases_df['pattern'] == pattern]
    total_n = len(sub)
    stats = {
        '样本数': total_n,
        '二波成功率%': round(sub['wave2_confirmed'].sum() / total_n * 100, 1),
        '二波平均涨幅%': round(sub['wave2_gain'].mean(), 2),
        '二波中位涨幅%': round(sub['wave2_gain'].median(), 2),
        '60日最大涨幅均值%': round(sub['wave2_60d_max'].mean(), 2),
        '20日平均涨幅%': round(sub['wave2_20d_gain'].mean(), 2),
        '60日平均涨幅%': round(sub['wave2_60d_gain'].mean(), 2),
        '平均回调幅度%': round(sub['pullback_pct'].mean(), 2),
        '平均调整天数': round(sub['adjust_days'].mean(), 1),
        '平均量能比': round(sub['vol_ratio'].mean(), 3),
        '平均盈亏比': round(sub['rr'].mean(), 2),
        'MA20上方占比%': round(sub['above_ma20'].mean() * 100, 1),
        'MA60上方占比%': round(sub['above_ma60'].mean() * 100, 1),
    }
    pattern_stats[pattern] = stats

stats_df = pd.DataFrame(pattern_stats).T
stats_df = stats_df.sort_values('二波成功率%', ascending=False)
print(f"\n{'='*80}")
print(f"双创板形态统计（按成功率排序）")
print(f"总样本: {len(cases_df)} 个 | 创业板: {len(cases_df[cases_df['board']=='CY'])} | 科创板: {len(cases_df[cases_df['board']=='KC'])}")
print(f"{'='*80}")
print(f"{'形态':<10} {'样本':>6} {'成功率%':>8} {'二波均涨%':>10} {'20日涨%':>8} {'60日涨%':>8} {'回调%':>7} {'盈亏比':>7} {'MA20上%':>8}")
print('-'*80)
for pattern, s in stats_df.iterrows():
    print(f"{pattern:<10} {int(s['样本数']):>6} {s['二波成功率%']:>8.1f} {s['二波平均涨幅%']:>10.2f} "
          f"{s['20日平均涨幅%']:>8.2f} {s['60日平均涨幅%']:>8.2f} {s['平均回调幅度%']:>7.2f} "
          f"{s['平均盈亏比']:>7.2f} {s['MA20上方占比%']:>8.1f}")

# ── Step 6: 创业板 vs 科创板对比 ────────────────────
print(f"\n{'='*80}")
print("创业板 vs 科创板 形态对比")
print('='*80)
for board in ['CY', 'KC']:
    board_df = cases_df[cases_df['board'] == board]
    print(f"\n【{'创业板' if board=='CY' else '科创板'}】共 {len(board_df)} 个案例")
    if len(board_df) > 0:
        for pattern in stats_df.index:
            sub = board_df[board_df['pattern'] == pattern]
            if len(sub) >= 5:
                rate = sub['wave2_confirmed'].mean() * 100
                gain = sub['wave2_gain'].mean()
                print(f"  {pattern:<10} n={len(sub):<5} 成功率={rate:.1f}%  均涨={gain:.1f}%")

# ── Step 7: 最优组合 ────────────────────────────────
print(f"\n{'='*80}")
print("双创板 最优入场条件组合（各形态内最优）")
print('='*80)

best_combos = []
for pattern in cases_df['pattern'].unique():
    sub = cases_df[cases_df['pattern'] == pattern]
    combos = [
        ('RSI<40 + MA20上方',       sub[(sub['rsi_at_low'] < 40) & (sub['above_ma20'] == True)]),
        ('RSI<35 + MA60上方',       sub[(sub['rsi_at_low'] < 35) & (sub['above_ma60'] == True)]),
        ('RSI<30',                  sub[sub['rsi_at_low'] < 30]),
        ('MACD金叉 + MA20上方',     sub[(sub['macd_golden'] == True) & (sub['above_ma20'] == True)]),
        ('MACD金叉',                sub[sub['macd_golden'] == True]),
        ('KDJ_J<20 + RSI<40',       sub[(sub['kdj_j_at_low'] < 20) & (sub['rsi_at_low'] < 40)]),
        ('CCI<-100 + MA20上方',     sub[(sub['cci_at_low'] < -100) & (sub['above_ma20'] == True)]),
        ('CCI<-100 + MA60上方',     sub[(sub['cci_at_low'] < -100) & (sub['above_ma60'] == True)]),
        ('MA20上方 + RSI<50',       sub[(sub['above_ma20'] == True) & (sub['rsi_at_low'] < 50)]),
        ('MA60上方 + RSI<50',       sub[(sub['above_ma60'] == True) & (sub['rsi_at_low'] < 50)]),
        ('量能比<0.8 + RSI<50',     sub[(sub['vol_ratio'] < 0.8) & (sub['rsi_at_low'] < 50)]),
        ('量能比>1.2',              sub[sub['vol_ratio'] > 1.2]),
    ]
    for combo_name, combo_df in combos:
        n = len(combo_df)
        if n >= 5:
            rate = combo_df['wave2_confirmed'].mean() * 100
            gain = combo_df['wave2_gain'].mean()
            rr = combo_df['rr'].mean()
            best_combos.append({
                'pattern': pattern,
                'combo': combo_name,
                'n': n,
                'rate': round(rate, 1),
                'gain': round(gain, 2),
                'rr': round(rr, 2),
            })

best_df = pd.DataFrame(best_combos)
if len(best_df) > 0:
    best_df = best_df.sort_values('rate', ascending=False)
    print(f"\n{'形态':<10} {'组合条件':<25} {'样本':>5} {'成功率%':>8} {'平均涨%':>9} {'盈亏比':>7}")
    print('-'*80)
    for _, row in best_df.head(15).iterrows():
        print(f"{row['pattern']:<10} {row['combo']:<25} {int(row['n']):>5} "
              f"{row['rate']:>8.1f} {row['gain']:>9.2f} {row['rr']:>7.2f}")

# ── Step 8: 保存 ────────────────────────────────
stats_csv = os.path.join(OUT_DIR, 'wave2_shuangchuang_stats.csv')
stats_df.to_csv(stats_csv, encoding='utf-8-sig')
print(f"\n统计结果已保存: {stats_csv}")

if len(best_df) > 0:
    best_csv = os.path.join(OUT_DIR, 'wave2_shuangchuang_best_combos.csv')
    best_df.to_csv(best_csv, index=False, encoding='utf-8-sig')
    print(f"最优组合已保存: {best_csv}")

result_json = {
    'total_cases': len(cases_df),
    'board_breakdown': {
        '创业板': int(len(cases_df[cases_df['board']=='CY'])),
        '科创板': int(len(cases_df[cases_df['board']=='KC'])),
    },
    'pattern_stats': pattern_stats,
    'best_combos': best_df.to_dict('records') if len(best_df) > 0 else [],
    'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
}
json_path = os.path.join(OUT_DIR, 'wave2_shuangchuang_result.json')
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(result_json, f, ensure_ascii=False, indent=2)
print(f"JSON已保存: {json_path}")

print(f"\n{'='*80}")
print("回测完成！")
print(f"{'='*80}")
_log_file.close()
