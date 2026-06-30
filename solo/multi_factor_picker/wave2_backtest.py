# -*- coding: utf-8 -*-
"""
二波行情调整形态量化回测
研究：一波20%+拉升后，什么样的调整形态最可能产生第二波？
数据源：Tushare stk_factor(技术因子) + daily_basic(量比/换手) + moneyflow(资金流)
回测区间：2024-01-01 ~ 2026-06-20
样本：沪深300成分股
"""
import os, sys, time, datetime, json, io
sys.path.insert(0, r'D:\mystock')

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

# 双写：stdout + 日志文件
_log_fpath = os.path.join(OUT_DIR, 'wave2_log.txt')
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

# ── 参数 ────────────────────────────────────────────
START_DATE = '20240101'
END_DATE   = '20260620'
SURGE_DAYS = 20        # 一波拉升的窗口
SURGE_MIN  = 0.20      # 最低涨幅20%
ADJUST_MAX = 60        # 调整期最长60天
WAVE2_WINDOW = 20      # 二波确认窗口
WAVE2_MIN = 0.10       # 二波最低涨幅10%
WAVE2_LONG = 60        # 长期观察窗口

print(f"{'='*80}")
print(f"二波行情调整形态量化回测")
print(f"回测区间: {START_DATE} ~ {END_DATE}")
print(f"{'='*80}\n")

# ── Step 1: 获取沪深300成分股 ────────────────────────
print("[Step 1] 获取沪深300成分股...")
# 用最新成分股权重获取
try:
    iw = pro.index_weight(index_code='399300.SZ', start_date='20250101', end_date=END_DATE)
    stocks = iw['con_code'].unique().tolist()
    print(f"  沪深300成分股: {len(stocks)} 只")
except Exception as e:
    print(f"  沪深300获取失败: {e}")
    stocks = []

if not stocks:
    # fallback: 直接用stock_basic获取全部
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    stocks = sb['ts_code'].tolist()
    print(f"  fallback: 全市场 {len(stocks)} 只")

time.sleep(0.1)

# ── Step 2: 获取大盘指数（上证）用于过滤 ──────────────
print("[Step 2] 获取上证指数...")
idx_df = pro.index_daily(ts_code='000001.SH', start_date=START_DATE, end_date=END_DATE)
idx_df = idx_df.sort_values('trade_date').reset_index(drop=True)
idx_df['idx_pct'] = idx_df['pct_chg']  # 当日涨跌
idx_dates = set(idx_df['trade_date'].tolist())
print(f"  交易日: {len(idx_df)} 天")
time.sleep(0.1)

# ── Step 3: 批量获取日线+技术因子 ────────────────────
print("[Step 3] 获取股票日线数据和技术因子...")

def load_stock_data(ts_code, start=START_DATE, end=END_DATE):
    """获取单只股票的日线+stk_factor+daily_basic"""
    try:
        # 日线
        daily = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if daily is None or len(daily) < 60:
            return None
        daily = daily.sort_values('trade_date').reset_index(drop=True)

        # 技术因子（MACD/KDJ/RSI/布林/CCI）- 使用 stk_factor_pro
        factor = pro.stk_factor_pro(ts_code=ts_code, start_date=start, end_date=end)
        time.sleep(0.06)

        # 日线基本（换手率/量比）
        basic = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end,
                                  fields='ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb')
        time.sleep(0.06)

        # 资金流向（大单净买入）
        mf = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
        time.sleep(0.06)

        # 合并（stk_factor_pro 字段：使用 _qfq 前复权版本，避免除权日指标失真）
        factor_rename = {
            'ma_qfq_5': 'ma5', 'ma_qfq_10': 'ma10', 'ma_qfq_20': 'ma20', 'ma_qfq_60': 'ma60',
            'macd_qfq': 'macd', 'macd_dif_qfq': 'macd_dif', 'macd_dea_qfq': 'macd_dea',
            'rsi_qfq_6': 'rsi_6', 'rsi_qfq_12': 'rsi_12', 'rsi_qfq_24': 'rsi_24',
            'kdj_k_qfq': 'kdj_k', 'kdj_d_qfq': 'kdj_d', 'kdj_qfq': 'kdj_j',
            'boll_upper_qfq': 'boll_upper', 'boll_mid_qfq': 'boll_mid', 'boll_lower_qfq': 'boll_lower',
            'cci_qfq': 'cci',
        }
        valid_cols = ['trade_date'] + [k for k in factor_rename if k in factor.columns]
        valid_rename = {k: v for k, v in factor_rename.items() if k in factor.columns}
        factor_subset = factor[valid_cols].rename(columns=valid_rename)
        df = daily.merge(factor_subset, on='trade_date', how='left')
        df = df.merge(basic[['trade_date','turnover_rate','volume_ratio','pe_ttm','pb']],
                      on='trade_date', how='left')
        df = df.merge(mf[['trade_date','net_mf_amount','buy_lg_amount','sell_lg_amount']],
                      on='trade_date', how='left')

        # 删除停牌日（成交量=0）
        df = df[df['vol'] > 0].reset_index(drop=True)

        # ⚠️ 关键修复：用 close_qfq（前复权）替代 close（未复权）
        # 未复权价在除权日产生虚假跳空，导致涨幅/回调幅度/RSI全部失真
        if 'close_qfq' in df.columns:
            df['close_bfq'] = df['close']
            df['close'] = df['close_qfq']
        if 'high_qfq' in df.columns:
            df['high'] = df['high_qfq']
        if 'low_qfq' in df.columns:
            df['low'] = df['low_qfq']

        # MA 已从 stk_factor_pro 获取，无需手动 rolling
        # 补充 MA120/MA250（手动 rolling）
        if 'close' in df.columns:
            df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
            df['ma250'] = df['close'].rolling(250, min_periods=120).mean()

        # 计算近N日涨跌幅
        df['pct_5d'] = df['close'].pct_change(5)
        df['pct_10d'] = df['close'].pct_change(10)
        df['pct_20d'] = df['close'].pct_change(20)

        return df
    except Exception as e:
        return None

# 并行获取（串行+限速）
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

# ── Step 4: 识别一波拉升+调整+形态分类 ──────────────
print("\n[Step 4] 识别一波拉升和调整形态...")

surge_cases = []  # 所有拉升案例

for code, df in all_data.items():
    n = len(df)
    closes = df['close'].values
    volumes = df['vol'].values
    dates = df['trade_date'].values

    # 滑动窗口找20%+拉升
    for i in range(SURGE_DAYS, n - ADJUST_MAX - WAVE2_LONG):
        # 窗口起点到当前：涨幅
        low_in_window = closes[i-SURGE_DAYS:i+1].min()
        high_in_window = closes[i-SURGE_DAYS:i+1].max()

        # 确认是"拉升"（低点在前，高点在后或接近）
        low_idx_in_window = np.argmin(closes[i-SURGE_DAYS:i+1]) + (i - SURGE_DAYS)
        high_idx_in_window = np.argmax(closes[i-SURGE_DAYS:i+1]) + (i - SURGE_DAYS)

        # 低点必须在高点之前（或相差不超过5天）
        if high_idx_in_window < low_idx_in_window:
            continue
        if (high_idx_in_window - low_idx_in_window) > SURGE_DAYS:
            continue

        # 涨幅
        surge_pct = (high_in_window - low_in_window) / low_in_window
        if surge_pct < SURGE_MIN:
            continue

        # 大盘过滤：拉升期间大盘不能大跌
        surge_start = dates[low_idx_in_window]
        surge_end = dates[high_idx_in_window]
        idx_in_surge = idx_df[(idx_df['trade_date'] >= surge_start) & (idx_df['trade_date'] <= surge_end)]
        if len(idx_in_surge) > 0:
            idx_chg = idx_in_surge['pct_chg'].sum()
            if idx_chg < -5:
                continue

        surge_peak_idx = high_idx_in_window  # 拉升高点位置
        surge_peak_price = closes[surge_peak_idx]
        surge_start_idx = low_idx_in_window
        surge_days = surge_peak_idx - surge_start_idx

        # ── 寻找调整低点（高点后最多60天）──
        adjust_window = closes[surge_peak_idx+1:surge_peak_idx+1+ADJUST_MAX]
        if len(adjust_window) == 0:
            continue
        adjust_low_price = adjust_window.min()
        adjust_low_idx = np.argmin(adjust_window) + surge_peak_idx + 1
        adjust_days = adjust_low_idx - surge_peak_idx

        # 回调幅度
        pullback_pct = (surge_peak_price - adjust_low_price) / surge_peak_price

        # 拉升期日均量
        surge_vol_avg = volumes[surge_start_idx:surge_peak_idx+1].mean()
        # 调整期日均量
        adjust_vol_avg = volumes[surge_peak_idx+1:adjust_low_idx+1].mean()
        vol_ratio = adjust_vol_avg / surge_vol_avg if surge_vol_avg > 0 else 1.0

        # 调整期振幅
        adjust_high = closes[surge_peak_idx+1:adjust_low_idx+1].max()
        adjust_low = closes[surge_peak_idx+1:adjust_low_idx+1].min()
        adjust_amplitude = (adjust_high - adjust_low) / surge_peak_price if surge_peak_price > 0 else 0

        # 三角收敛检测：前半段振幅 vs 后半段振幅
        adjust_len = adjust_low_idx - surge_peak_idx
        if adjust_len >= 10:
            mid = adjust_len // 2
            first_half_amp = (closes[surge_peak_idx+1:surge_peak_idx+1+mid].max() -
                              closes[surge_peak_idx+1:surge_peak_idx+1+mid].min())
            second_half_amp = (closes[surge_peak_idx+1+mid:adjust_low_idx+1].max() -
                               closes[surge_peak_idx+1+mid:adjust_low_idx+1].min())
            converge_ratio = first_half_amp / second_half_amp if second_half_amp > 0.1 else 99
        else:
            converge_ratio = 1.0

        # ── 调整低点的技术指标 ──
        row_at_low = df.iloc[adjust_low_idx] if adjust_low_idx < len(df) else None
        if row_at_low is None:
            continue

        ma20_at_low = row_at_low.get('ma20', 0)
        ma60_at_low = row_at_low.get('ma60', 0)
        ma120_at_low = row_at_low.get('ma120', 0)
        ma250_at_low = row_at_low.get('ma250', 0)
        rsi_at_low = row_at_low.get('rsi_6', 50) or 50
        rsi12_at_low = row_at_low.get('rsi_12', 50) or 50
        kdj_j_at_low = row_at_low.get('kdj_j', 50) or 50
        macd_dif = row_at_low.get('macd_dif', 0) or 0
        macd_dea = row_at_low.get('macd_dea', 0) or 0
        macd_val = row_at_low.get('macd', 0) or 0
        boll_lower = row_at_low.get('boll_lower', 0) or 0
        cci_val = row_at_low.get('cci', 0) or 0
        turnover = row_at_low.get('turnover_rate', 0) or 0
        vol_ratio_day = row_at_low.get('volume_ratio', 1) or 1
        low_price = closes[adjust_low_idx]

        above_ma20 = low_price > ma20_at_low
        above_ma60 = low_price > ma60_at_low
        above_ma120 = low_price > ma120_at_low if ma120_at_low and ma120_at_low > 0 else None
        above_ma250 = low_price > ma250_at_low if ma250_at_low and ma250_at_low > 0 else None
        near_boll = (boll_lower > 0) and (low_price <= boll_lower * 1.02)
        macd_golden = (macd_dif - macd_dea) > -0.02  # 接近金叉
        net_mf = row_at_low.get('net_mf_amount', 0) or 0

        # ── 形态分类 ──
        pattern = '其他'

        # 强势横盘：回调<10% + 量能维持 + 低点在MA20上方
        if pullback_pct < 0.10 and vol_ratio > 0.5 and above_ma20:
            pattern = '强势横盘'

        # 缩量回调：回调10-20% + 量能萎缩 + 持续>15天
        elif 0.10 <= pullback_pct <= 0.20 and vol_ratio < 0.7 and adjust_days > 15:
            pattern = '缩量回调'

        # 深度回调：回调>20% + 接近MA60
        elif pullback_pct > 0.20:
            if not above_ma60 or (ma60_at_low > 0 and abs(low_price - ma60_at_low) / ma60_at_low < 0.08):
                pattern = '深度回调'
            else:
                pattern = '深度回调'

        # 三角收敛：振幅递减明显
        elif converge_ratio > 1.5 and adjust_days >= 10:
            pattern = '三角收敛'

        # 放量回调：回调10-<20% + 量能放大
        elif 0.10 <= pullback_pct < 0.20 and vol_ratio > 1.0:
            pattern = '放量回调'

        # V型急跌急涨：短时间大幅回调
        elif adjust_days <= 8 and pullback_pct > 0.10:
            pattern = 'V型急跌'

        # ── 二波统计（调整低点后的表现）──
        # 检查低点后是否有足够的数据
        remaining = n - adjust_low_idx - 1
        if remaining < WAVE2_WINDOW:
            continue

        # 二波确认：低点后20日内涨幅>10%
        future_closes = closes[adjust_low_idx+1:adjust_low_idx+1+WAVE2_LONG]
        future_max = future_closes.max()
        future_end = future_closes[-1] if len(future_closes) >= WAVE2_WINDOW else future_closes[-1]

        wave2_gain = (future_max - low_price) / low_price
        wave2_20d_gain = (future_end - low_price) / low_price if future_end > 0 else 0

        # 60日涨幅
        if remaining >= WAVE2_LONG:
            future_60d = closes[adjust_low_idx+1:adjust_low_idx+1+WAVE2_LONG]
            wave2_60d_gain = (future_60d[-1] - low_price) / low_price
            wave2_60d_max = (future_60d.max() - low_price) / low_price
        else:
            wave2_60d_gain = wave2_20d_gain
            wave2_60d_max = wave2_gain

        # 二波确认
        wave2_confirmed = wave2_gain >= WAVE2_MIN

        # 盈亏比
        risk = pullback_pct  # 调整期间最大回撤
        reward = wave2_gain  # 二波最大涨幅
        rr = reward / risk if risk > 0 else 0

        # 记录
        case = {
            'code': code,
            'surge_start_date': str(dates[surge_start_idx]) if surge_start_idx < len(dates) else '',
            'surge_peak_date': str(dates[surge_peak_idx]) if surge_peak_idx < len(dates) else '',
            'adjust_low_date': str(dates[adjust_low_idx]) if adjust_low_idx < len(dates) else '',
            'surge_pct': round(surge_pct * 100, 2),
            'pullback_pct': round(pullback_pct * 100, 2),
            'adjust_days': adjust_days,
            'vol_ratio': round(vol_ratio, 3),
            'adjust_amplitude': round(adjust_amplitude * 100, 2),
            'converge_ratio': round(converge_ratio, 2),
            'above_ma20': above_ma20,
            'above_ma60': above_ma60,
            'above_ma120': above_ma120,
            'above_ma250': above_ma250,
            'near_boll_lower': near_boll,
            'macd_golden': macd_golden,
            'rsi_at_low': round(float(rsi_at_low), 1),
            'rsi12_at_low': round(float(rsi12_at_low), 1),
            'kdj_j_at_low': round(float(kdj_j_at_low), 1),
            'cci_at_low': round(float(cci_val), 1),
            'turnover_at_low': round(float(turnover), 3),
            'vol_ratio_day': round(float(vol_ratio_day), 3),
            'pattern': pattern,
            'wave2_confirmed': wave2_confirmed,
            'wave2_gain': round(wave2_gain * 100, 2),
            'wave2_20d_gain': round(wave2_20d_gain * 100, 2),
            'wave2_60d_gain': round(wave2_60d_gain * 100, 2),
            'wave2_60d_max': round(wave2_60d_max * 100, 2),
            'rr': round(rr, 2),
            'peak_price': round(surge_peak_price, 2),
            'low_price': round(low_price, 2),
        }
        surge_cases.append(case)

print(f"  识别拉升案例: {len(surge_cases)} 个")

# ── Step 5: 统计分析 ──────────────────────────────────
print("\n[Step 5] 形态统计分析...")

if len(surge_cases) == 0:
    print("  ERROR: 未找到任何拉升案例！")
    sys.exit(1)

cases_df = pd.DataFrame(surge_cases)

# 保存CSV
csv_path = os.path.join(OUT_DIR, 'wave2_results.csv')
cases_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f"  CSV已保存: {csv_path}")

# 按形态分组统计
pattern_stats = {}
for pattern in cases_df['pattern'].unique():
    mask = cases_df['pattern'] == pattern
    sub = cases_df[mask]
    total_n = len(sub)

    stats = {
        '样本数': total_n,
        '二波成功率%': round(sub['wave2_confirmed'].sum() / total_n * 100, 1),
        '二波平均涨幅%': round(sub['wave2_gain'].mean(), 2),
        '二波中位涨幅%': round(sub['wave2_gain'].median(), 2),
        '二波最大涨幅(均值)%': round(sub['wave2_60d_max'].mean(), 2),
        '20日平均涨幅%': round(sub['wave2_20d_gain'].mean(), 2),
        '60日平均涨幅%': round(sub['wave2_60d_gain'].mean(), 2),
        '平均回调幅度%': round(sub['pullback_pct'].mean(), 2),
        '平均调整天数': round(sub['adjust_days'].mean(), 1),
        '平均量能比': round(sub['vol_ratio'].mean(), 3),
        '平均盈亏比': round(sub['rr'].mean(), 2),
        '中位盈亏比': round(sub['rr'].median(), 2),
        'RSI低点均值': round(sub['rsi_at_low'].mean(), 1),
        'MA20上方比例%': round(sub['above_ma20'].sum() / total_n * 100, 1),
        'MA60上方比例%': round(sub['above_ma60'].sum() / total_n * 100, 1),
        '布林下轨附近比例%': round(sub['near_boll_lower'].sum() / total_n * 100, 1),
        'MACD金叉比例%': round(sub['macd_golden'].sum() / total_n * 100, 1),
    }
    pattern_stats[pattern] = stats

stats_df = pd.DataFrame(pattern_stats).T
stats_df = stats_df.sort_values('二波成功率%', ascending=False)

print(f"\n{'='*90}")
print("形态统计排名（按二波成功率降序）")
print(f"{'='*90}")

# 打印核心表格
print(f"\n{'形态':<10} {'样本':>5} {'成功率%':>8} {'二波均涨%':>10} {'20日涨%':>9} {'60日涨%':>9} {'回调%':>8} {'盈亏比':>7} {'MA20上%':>8}")
print('-'*90)
for pattern, row in stats_df.iterrows():
    print(f"{pattern:<10} {int(row['样本数']):>5} {row['二波成功率%']:>8.1f} "
          f"{row['二波平均涨幅%']:>10.2f} {row['20日平均涨幅%']:>9.2f} "
          f"{row['60日平均涨幅%']:>9.2f} {row['平均回调幅度%']:>8.2f} "
          f"{row['平均盈亏比']:>7.2f} {row['MA20上方比例%']:>8.1f}")

# ── Step 6: 多因子叠加分析 ──────────────────────────
print(f"\n{'='*90}")
print("多因子叠加分析（各形态内最优入场条件）")
print(f"{'='*90}")

# 对每种形态，分析哪些子条件提升二波概率
for pattern in ['强势横盘', '缩量回调', '深度回调', '三角收敛', '放量回调', 'V型急跌', '其他']:
    if pattern not in cases_df['pattern'].values:
        continue
    sub = cases_df[cases_df['pattern'] == pattern]
    n = len(sub)
    base_rate = sub['wave2_confirmed'].mean() * 100
    print(f"\n【{pattern}】 基础二波成功率: {base_rate:.1f}% (n={n})")

    # RSI分层
    for rsi_range, rsi_name in [(0, '<30'), (30, '30-50'), (50, '50-70'), (70, '70+')]:
        if rsi_range == 0:
            mask = sub['rsi_at_low'] < 30
        elif rsi_range == 70:
            mask = sub['rsi_at_low'] >= 70
        else:
            mask = (sub['rsi_at_low'] >= rsi_range) & (sub['rsi_at_low'] < rsi_range + 20)
        cnt = mask.sum()
        if cnt >= 5:
            rate = sub.loc[mask, 'wave2_confirmed'].mean() * 100
            gain = sub.loc[mask, 'wave2_gain'].mean()
            print(f"  RSI {rsi_name}: 成功率 {rate:.1f}% (n={cnt}), 平均涨幅 {gain:.2f}%")

    # 量能比分层
    for vr_name, vr_low, vr_high in [('缩量(<0.5)', 0, 0.5), ('温和(0.5-0.8)', 0.5, 0.8),
                                        ('正常(0.8-1.2)', 0.8, 1.2), ('放量(>1.2)', 1.2, 99)]:
        mask = (sub['vol_ratio'] >= vr_low) & (sub['vol_ratio'] < vr_high)
        cnt = mask.sum()
        if cnt >= 5:
            rate = sub.loc[mask, 'wave2_confirmed'].mean() * 100
            gain = sub.loc[mask, 'wave2_gain'].mean()
            print(f"  量能 {vr_name}: 成功率 {rate:.1f}% (n={cnt}), 平均涨幅 {gain:.2f}%")

    # MA20/MA60/MA120/MA250分层
    if (sub['above_ma20'] == True).sum() >= 5:
        rate = sub[sub['above_ma20'] == True]['wave2_confirmed'].mean() * 100
        print(f"  低点在MA20上方: 成功率 {rate:.1f}% (n={sub['above_ma20'].sum()})")
    if (sub['above_ma60'] == True).sum() >= 5:
        rate = sub[sub['above_ma60'] == True]['wave2_confirmed'].mean() * 100
        print(f"  低点在MA60上方: 成功率 {rate:.1f}% (n={sub['above_ma60'].sum()})")
    if sub['above_ma120'].notna().sum() >= 5:
        valid = sub[sub['above_ma120'].notna()]
        cnt = valid['above_ma120'].sum()
        if cnt >= 5:
            rate = valid[valid['above_ma120'] == True]['wave2_confirmed'].mean() * 100
            print(f"  低点在MA120上方: 成功率 {rate:.1f}% (n={cnt})")
    if sub['above_ma250'].notna().sum() >= 5:
        valid = sub[sub['above_ma250'].notna()]
        cnt = valid['above_ma250'].sum()
        if cnt >= 5:
            rate = valid[valid['above_ma250'] == True]['wave2_confirmed'].mean() * 100
            print(f"  低点在MA250上方: 成功率 {rate:.1f}% (n={cnt})")

# ── Step 7: 最优入场条件提炼 ──────────────────────────
print(f"\n{'='*90}")
print("最优入场条件提炼")
print(f"{'='*90}")

# 找出成功率最高的组合条件
best_combos = []
for pattern in cases_df['pattern'].unique():
    sub = cases_df[cases_df['pattern'] == pattern]
    if len(sub) < 10:
        continue

    # 组合条件筛选
    combos = [
        ('RSI<40 + MA20上方', sub[(sub['rsi_at_low'] < 40) & (sub['above_ma20'] == True)]),
        ('RSI<50 + 量能比<0.8', sub[(sub['rsi_at_low'] < 50) & (sub['vol_ratio'] < 0.8)]),
        ('RSI<40 + MACD金叉', sub[(sub['rsi_at_low'] < 40) & (sub['macd_golden'] == True)]),
        ('布林下轨 + RSI<50', sub[(sub['near_boll_lower'] == True) & (sub['rsi_at_low'] < 50)]),
        ('换手率<3% + 缩量', sub[(sub['turnover_at_low'] < 3) & (sub['vol_ratio'] < 0.8)]),
        ('KDJ_J<20 + RSI<40', sub[(sub['kdj_j_at_low'] < 20) & (sub['rsi_at_low'] < 40)]),
        ('CCI<-100 + MA20上方', sub[(sub['cci_at_low'] < -100) & (sub['above_ma20'] == True)]),
        ('MACD金叉 + MA20上方', sub[(sub['macd_golden'] == True) & (sub['above_ma20'] == True)]),
        ('RSI<35 + MA60上方', sub[(sub['rsi_at_low'] < 35) & (sub['above_ma60'] == True)]),
        ('MA120上方 + RSI<50', sub[(sub['above_ma120'] == True) & (sub['rsi_at_low'] < 50)]),
        ('MA250上方 + RSI<50', sub[(sub['above_ma250'] == True) & (sub['rsi_at_low'] < 50)]),
        ('MA120+MA250双均线支撑', sub[(sub['above_ma120'] == True) & (sub['above_ma250'] == True)]),
        ('MA60+MA120+MA250三均线支撑', sub[(sub['above_ma60'] == True) & (sub['above_ma120'] == True) & (sub['above_ma250'] == True)]),
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

# ── Step 8: 保存统计结果 ──────────────────────────────
stats_csv = os.path.join(OUT_DIR, 'wave2_pattern_stats.csv')
stats_df.to_csv(stats_csv, encoding='utf-8-sig')
print(f"\n统计结果已保存: {stats_csv}")

if len(best_df) > 0:
    best_csv = os.path.join(OUT_DIR, 'wave2_best_combos.csv')
    best_df.to_csv(best_csv, index=False, encoding='utf-8-sig')
    print(f"最优组合已保存: {best_csv}")

# 保存JSON供PDF生成用
result_json = {
    'total_cases': len(surge_cases),
    'pattern_stats': pattern_stats,
    'best_combos': best_df.to_dict('records') if len(best_df) > 0 else [],
    'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
}
json_path = os.path.join(OUT_DIR, 'wave2_result.json')
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(result_json, f, ensure_ascii=False, indent=2)
print(f"JSON已保存: {json_path}")

print(f"\n{'='*90}")
print("回测完成！")
print(f"{'='*90}")
_log_file.close()
