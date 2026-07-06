# -*- coding: utf-8 -*-
"""
量能爆发+宽幅震荡池回测（独立版）
直接从缓存读取日线，按测试日截取，检测量能爆发+宽幅震荡形态，
模拟买入并计算T+1/T+3/T+5/T+10收益，分析不同形态/买点的胜率。
"""
import os
import sys
import time
import glob
import re
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = os.path.dirname(BASE_DIR)
load_dotenv(os.path.join(STOCK_DATA_DIR, "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")


def get_trade_dates(start, end):
    cal = pro.trade_cal(exchange='', start_date=start, end_date=end)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date').reset_index(drop=True)
    return cal['cal_date'].tolist()


def load_stock_df(ts_code):
    """加载缓存的日线数据"""
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            return df.sort_values('trade_date').reset_index(drop=True)
        except:
            pass
    return None


def detect_vol_surge_swing(df_full, test_date):
    """
    量能爆发+宽幅震荡检测（独立版，基于df截取到test_date）
    逻辑与tushare_quant.detect_volume_surge_swing一致
    """
    df = df_full[df_full['trade_date'] <= test_date].copy()
    if len(df) < 180:
        return None

    recent = df.tail(60)
    if len(recent) < 20:
        return None

    vol_arr = recent['vol'].values.astype(float)
    high_arr = recent['high'].values.astype(float)
    low_arr = recent['low'].values.astype(float)
    close_arr = recent['close'].values.astype(float)
    pre_close_arr = recent['pre_close'].values.astype(float)

    vol_ma20 = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    vol_ratio = vol_arr / np.maximum(vol_ma20, 1)
    max_vol_ratio = float(np.max(vol_ratio))
    vol_ratio_gt2 = int(np.sum(vol_ratio > 2.0))
    vol_ratio_gt3 = int(np.sum(vol_ratio > 3.0))

    hist_vol_max = float(np.max(df['vol'].values.astype(float)))
    recent_vol_max = float(np.max(vol_arr))
    vol_vs_hist_pct = (recent_vol_max / hist_vol_max * 100) if hist_vol_max > 0 else 0

    amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
    avg_amplitude = float(np.mean(amplitude))
    amp_gt8_count = int(np.sum(amplitude > 8))

    range_high = float(np.max(high_arr))
    range_low = float(np.min(low_arr))
    range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0

    price_change = (close_arr[-1] / close_arr[0] - 1) * 100 if close_arr[0] > 0 else 0

    # 硬条件
    if max_vol_ratio < 2.6: return None
    if vol_ratio_gt2 < 3: return None
    if avg_amplitude < 4.5: return None
    if range_swing < 35: return None
    if price_change < -10: return None
    if price_change > 100: return None
    if len(df) < 180: return None
    if vol_vs_hist_pct < 50: return None

    # =========================
    # MA20趋势检查：20日均线必须走平或上行
    # 排除中线均线压制逐波走低的股票（如金田股份）
    # 判定：近10天MA20变化率>=-0.3%（走平）且近20天MA20变化率>=-1%（中线未走低）
    # =========================
    ma20_full = pd.Series(df['close'].values.astype(float)).rolling(20, min_periods=20).mean().values
    if len(ma20_full) >= 41:
        ma20_now = float(ma20_full[-1])
        ma20_10ago = float(ma20_full[-11]) if not np.isnan(ma20_full[-11]) else ma20_now
        ma20_20ago = float(ma20_full[-21]) if not np.isnan(ma20_full[-21]) else ma20_now

        if (not np.isnan(ma20_now) and not np.isnan(ma20_10ago) and ma20_10ago > 0
                and not np.isnan(ma20_20ago) and ma20_20ago > 0):
            ma20_chg_10d = (ma20_now / ma20_10ago - 1) * 100
            ma20_chg_20d = (ma20_now / ma20_20ago - 1) * 100
            # 走平或上行：近10天变化率>=-0.3% AND 近20天变化率>=-1%
            # 明显下行（逐波走低）则排除
            if ma20_chg_10d < -0.3 or ma20_chg_20d < -1.0:
                return None

    # 200天窗口检查
    _df200 = df.tail(200) if len(df) >= 200 else df
    _vol200 = _df200['vol'].values.astype(float)
    _high200 = _df200['high'].values.astype(float)
    _low200 = _df200['low'].values.astype(float)

    _peak_vol_idx = int(np.argmax(_vol200))
    _peak_vol_price = float(_high200[_peak_vol_idx])

    _pre_peak_start = max(0, _peak_vol_idx - 20)
    _pre_peak_end = max(0, _peak_vol_idx - 3)
    _base_vol = float(np.mean(_vol200[_pre_peak_start:_pre_peak_end])) if _pre_peak_end > _pre_peak_start else float(np.mean(_vol200[:_peak_vol_idx]))
    _base_vol = max(_base_vol, 1)

    _recent_vol = float(np.mean(_vol200[-20:])) if len(_vol200) >= 20 else float(np.mean(_vol200))
    _vol_vs_base = _recent_vol / _base_vol
    if _vol_vs_base < 1.3: return None

    _peak_vol_start = max(0, _peak_vol_idx - 5)
    _peak_vol_end = min(len(_vol200), _peak_vol_idx + 6)
    _peak_5d_vol = float(np.mean(_vol200[_peak_vol_start:_peak_vol_end])) if _peak_vol_end > _peak_vol_start else _recent_vol
    _peak_5d_vol = max(_peak_5d_vol, 1)
    _vol_vs_peak = _recent_vol / _peak_5d_vol
    if _vol_vs_peak < 0.5: return None

    _a_low = float(np.min(_low200[:_peak_vol_idx+1]))
    _a_gain = (_peak_vol_price / _a_low - 1) * 100 if _a_low > 0 else 0
    if _a_gain < 15: return None

    if _peak_vol_idx < len(_low200) - 3:
        _b_low = float(np.min(_low200[_peak_vol_idx:]))
        _b_drop = (1 - _b_low / _peak_vol_price) * 100
        _retrace_ratio = _b_drop / _a_gain * 100 if _a_gain > 0 else 0
    else:
        _b_low = close_arr[-1]
        _b_drop = 0
        _retrace_ratio = 0

    _fib_786 = _peak_vol_price - (_peak_vol_price - _a_low) * 0.786
    if _b_low < _fib_786 * 0.92: return None
    # 回测验证：深回调(_retrace_ratio>=50) T+5胜率仅50% 平均-3.88%，应排除
    if _retrace_ratio > 50: return None

    _peak_idx = int(np.argmax(high_arr))
    _peak_price = float(high_arr[_peak_idx])
    _pre_peak_low = float(np.min(low_arr[:_peak_idx+1])) if _peak_idx > 0 else float(low_arr[0])
    _pre_peak_gain = (_peak_price / _pre_peak_low - 1) * 100 if _pre_peak_low > 0 else 0
    _dist_from_peak = (1 - close_arr[-1] / _peak_price) * 100

    if _peak_idx < len(high_arr) - 10:
        _post_peak_low = float(np.min(low_arr[_peak_idx:]))
        _bounce = (close_arr[-1] / _post_peak_low - 1) * 100 if _post_peak_low > 0 else 0
    else:
        _bounce = 0

    if _pre_peak_gain > 70 and _dist_from_peak > 15 and _bounce < 10:
        return None

    # 评分
    vol_score = min(max_vol_ratio / 5.0, 1) * 30
    freq_score = min(vol_ratio_gt2 / 7, 1) * 20
    amp_score = min(avg_amplitude / 7, 1) * 20
    big_amp_score = min(amp_gt8_count / 15, 1) * 15
    swing_score = min(range_swing / 60, 1) * 15
    total_score = vol_score + freq_score + amp_score + big_amp_score + swing_score

    # 回测验证：评分55-65 T+5胜率仅17% 平均-8.32%，需提升阈值至65
    if total_score < 65:
        return None

    # MACD
    close_full = df['close'].values.astype(float)
    ema12 = pd.Series(close_full).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close_full).ewm(span=26, adjust=False).mean().values
    macd_dif = ema12 - ema26
    macd_dea = pd.Series(macd_dif).ewm(span=9, adjust=False).mean().values
    macd_bar = 2 * (macd_dif - macd_dea)

    cur_bar = float(macd_bar[-1])
    prev_bar = float(macd_bar[-2]) if len(macd_bar) >= 2 else cur_bar
    prev2_bar = float(macd_bar[-3]) if len(macd_bar) >= 3 else prev_bar

    macd_status = ''
    macd_pass = False
    if prev_bar < 0 < cur_bar:
        macd_status = '刚刚红柱'
        macd_pass = True
    elif cur_bar < 0 and cur_bar > prev_bar > prev2_bar:
        macd_status = '即将红柱'
        macd_pass = True

    if not macd_pass:
        return None

    # === 附加形态分析 ===
    # 回调幅度
    pullback_pct = _dist_from_peak

    # 回撤深度分类
    if _retrace_ratio < 30:
        retrace_type = '浅回调(强势横盘)'
    elif _retrace_ratio < 50:
        retrace_type = '中回调'
    else:
        retrace_type = '深回调'

    # 位置分类
    close_latest = float(close_arr[-1])
    ma5 = pd.Series(close_arr).rolling(5).mean().values[-1]
    ma10 = pd.Series(close_arr).rolling(10).mean().values[-1]
    ma20 = pd.Series(close_arr).rolling(20).mean().values[-1]
    ma60 = pd.Series(close_full).rolling(60).mean().values[-1]

    pos_ma20 = (close_latest / ma20 - 1) * 100 if not np.isnan(ma20) and ma20 > 0 else 0
    pos_ma60 = (close_latest / ma60 - 1) * 100 if not np.isnan(ma60) and ma60 > 0 else 0

    # 量比
    today_vol_ratio = float(vol_ratio[-1]) if len(vol_ratio) > 0 else 0

    return {
        'score': round(total_score, 1),
        'max_vol_ratio': round(max_vol_ratio, 2),
        'vol_gt2_days': vol_ratio_gt2,
        'avg_amplitude': round(avg_amplitude, 2),
        'big_amp_days': amp_gt8_count,
        'range_swing': round(range_swing, 1),
        'range_change': round(price_change, 1),
        'vol_vs_hist_pct': round(vol_vs_hist_pct, 0),
        'macd_status': macd_status,
        'today_vol_ratio': round(today_vol_ratio, 2),
        'pullback_pct': round(pullback_pct, 1),
        'retrace_ratio': round(_retrace_ratio, 1),
        'retrace_type': retrace_type,
        'a_gain': round(_a_gain, 1),
        'bounce': round(_bounce, 1),
        'pos_ma20': round(pos_ma20, 1),
        'pos_ma60': round(pos_ma60, 1),
        'vol_vs_base': round(_vol_vs_base, 2),
        'vol_vs_peak': round(_vol_vs_peak, 2),
    }


def main():
    print("\n" + "=" * 70)
    print("量能爆发+宽幅震荡池 - 历史回测（独立版）")
    print("=" * 70)

    start_date = '20260101'
    end_date = '20260703'
    trade_dates_all = get_trade_dates(start_date, '20260715')
    test_dates = [d for d in trade_dates_all if start_date <= d <= end_date]

    print(f"回测日期: {test_dates[0]} ~ {test_dates[-1]} ({len(test_dates)}个)")

    # 加载股票池：扫描全市场A股缓存CSV（排除北交所、指数、ETF、可转债等）
    # 通过文件名正则严格匹配A股代码：6xxxxx.SH / 0xxxxx.SZ / 3xxxxx.SZ / 688xxx.SH
    pool_codes = set()
    csv_files = glob.glob(os.path.join(CACHE_DIR, "*.csv"))
    a_share_pattern = re.compile(r'^(6\d{5}\.SH|(0|3)\d{5}\.SZ|688\d{3}\.SH)$')
    for csv_file in csv_files:
        fname = os.path.basename(csv_file).replace('.csv', '')
        if a_share_pattern.match(fname):
            pool_codes.add(fname)

    print(f"股票池总股票数（沪深A股）: {len(pool_codes)}", flush=True)

    # 预加载所有股票数据（一次性加载，避免重复IO）
    print("预加载股票数据...", flush=True)
    stock_data = {}  # code -> df_full
    load_start = time.time()
    for j, code in enumerate(pool_codes):
        df_full = load_stock_df(code)
        if df_full is not None and len(df_full) >= 180:
            stock_data[code] = df_full
        if (j + 1) % 1000 == 0:
            print(f"  已加载 {j+1}/{len(pool_codes)} 只...", flush=True)
    print(f"预加载完成: {len(stock_data)}只有效数据, 耗时{time.time()-load_start:.1f}秒", flush=True)

    all_results = []

    for i, test_date in enumerate(test_dates):
        print(f"\n[{i+1}/{len(test_dates)}] 回测 {test_date}...", end=' ', flush=True)
        hit_count = 0

        if test_date not in trade_dates_all:
            print("非交易日", flush=True)
            continue

        test_idx = trade_dates_all.index(test_date)
        future_dates = trade_dates_all[test_idx + 1: test_idx + 1 + 12]

        for code, df_full in stock_data.items():
            # 确保有测试日数据
            if not (df_full['trade_date'] <= test_date).any():
                continue

            result = detect_vol_surge_swing(df_full, test_date)
            if result is None:
                continue

            hit_count += 1

            # 买入价
            buy_row = df_full[df_full['trade_date'] == test_date]
            if buy_row.empty:
                continue
            buy_price = float(buy_row.iloc[0]['close'])

            entry = {
                'test_date': test_date,
                'code': code,
                'buy_price': buy_price,
                **result,
            }

            for offset, label in [(1, 'T+1'), (3, 'T+3'), (5, 'T+5'), (10, 'T+10')]:
                if offset < len(future_dates):
                    target_date = future_dates[offset]
                    future_row = df_full[df_full['trade_date'] == target_date]
                    if not future_row.empty:
                        future_close = float(future_row.iloc[0]['close'])
                        entry[f'{label}_pct'] = (future_close / buy_price - 1) * 100
                    else:
                        entry[f'{label}_pct'] = None
                else:
                    entry[f'{label}_pct'] = None

            all_results.append(entry)

        print(f"命中: {hit_count}只", flush=True)

    if not all_results:
        print("无回测数据！")
        return

    df_all = pd.DataFrame(all_results)

    # === 分析 ===
    print("\n" + "=" * 70)
    print("量能爆发+宽幅震荡池 - 回测分析")
    print("=" * 70)
    print(f"总样本: {len(df_all)}")

    # 整体
    print("\n--- 整体收益 ---")
    for label in ['T+1', 'T+3', 'T+5', 'T+10']:
        col = f'{label}_pct'
        valid = df_all[col].dropna()
        if len(valid) == 0: continue
        wr = (valid > 0).sum() / len(valid) * 100
        print(f"  {label}: {len(valid)}只 | 胜率{wr:.1f}% | 平均{valid.mean():+.2f}% | 中位{valid.median():+.2f}%")

    # 按评分
    print("\n--- 按评分分组 ---")
    for lo, hi, label in [(80, 200, '>=80'), (65, 80, '65-80'), (55, 65, '55-65')]:
        g = df_all[(df_all['score'] >= lo) & (df_all['score'] < hi)]
        for t in ['T+3', 'T+5']:
            v = g[f'{t}_pct'].dropna()
            if len(v) >= 2:
                wr = (v > 0).sum() / len(v) * 100
                print(f"  评分{label} [{t}]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 按MACD
    print("\n--- 按MACD状态 ---")
    for macd in df_all['macd_status'].unique():
        g = df_all[df_all['macd_status'] == macd]
        for t in ['T+3', 'T+5']:
            v = g[f'{t}_pct'].dropna()
            if len(v) >= 2:
                wr = (v > 0).sum() / len(v) * 100
                print(f"  {macd} [{t}]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 按回撤类型
    print("\n--- 按回撤类型 ---")
    for rt in df_all['retrace_type'].unique():
        g = df_all[df_all['retrace_type'] == rt]
        for t in ['T+3', 'T+5']:
            v = g[f'{t}_pct'].dropna()
            if len(v) >= 2:
                wr = (v > 0).sum() / len(v) * 100
                print(f"  {rt} [{t}]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 按区间涨幅
    print("\n--- 按区间涨幅 ---")
    for lo, hi, label in [(-20, 0, '下跌'), (0, 20, '0-20%'), (20, 40, '20-40%'), (40, 60, '40-60%'), (60, 101, '60%+')]:
        g = df_all[(df_all['range_change'] >= lo) & (df_all['range_change'] < hi)]
        v = g['T+5_pct'].dropna()
        if len(v) >= 2:
            wr = (v > 0).sum() / len(v) * 100
            print(f"  区间涨幅{label} [T+5]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 按区间振幅
    print("\n--- 按区间振幅 ---")
    for lo, hi, label in [(35, 50, '35-50%'), (50, 70, '50-70%'), (70, 100, '70-100%'), (100, 999, '100%+')]:
        g = df_all[(df_all['range_swing'] >= lo) & (df_all['range_swing'] < hi)]
        v = g['T+5_pct'].dropna()
        if len(v) >= 2:
            wr = (v > 0).sum() / len(v) * 100
            print(f"  区间振幅{label} [T+5]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 按今日量比
    print("\n--- 按今日量比 ---")
    for lo, hi, label in [(0, 1.0, '<1.0'), (1.0, 1.5, '1.0-1.5'), (1.5, 2.0, '1.5-2.0'), (2.0, 99, '2.0+')]:
        g = df_all[(df_all['today_vol_ratio'] >= lo) & (df_all['today_vol_ratio'] < hi)]
        for t in ['T+3', 'T+5']:
            v = g[f'{t}_pct'].dropna()
            if len(v) >= 2:
                wr = (v > 0).sum() / len(v) * 100
                print(f"  量比{label} [{t}]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 按距MA20位置
    print("\n--- 按距MA20位置 ---")
    for lo, hi, label in [(-99, -3, '<-3%'), (-3, 0, '-3~0%'), (0, 3, '0~3%'), (3, 10, '3~10%'), (10, 99, '>10%')]:
        g = df_all[(df_all['pos_ma20'] >= lo) & (df_all['pos_ma20'] < hi)]
        v = g['T+5_pct'].dropna()
        if len(v) >= 2:
            wr = (v > 0).sum() / len(v) * 100
            print(f"  距MA20 {label} [T+5]: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 最佳组合
    print("\n" + "=" * 70)
    print("最佳形态组合（T+5胜率）")
    print("=" * 70)

    combos = [
        ("评分>=70+刚红柱", (df_all['score'] >= 70) & (df_all['macd_status'] == '刚刚红柱')),
        ("评分>=65+涨幅<40%", (df_all['score'] >= 65) & (df_all['range_change'] < 40)),
        ("刚红柱+量比<1.5", (df_all['macd_status'] == '刚刚红柱') & (df_all['today_vol_ratio'] < 1.5)),
        ("评分>=60+振幅<70%+红柱", (df_all['score'] >= 60) & (df_all['range_swing'] < 70) & (df_all['macd_status'].str.contains('红柱'))),
        ("涨幅0-40%+振幅<7%+红柱", (df_all['range_change'].between(0, 40)) & (df_all['avg_amplitude'] < 7) & (df_all['macd_status'].str.contains('红柱'))),
        ("浅回调+刚红柱", (df_all['retrace_type'] == '浅回调(强势横盘)') & (df_all['macd_status'] == '刚刚红柱')),
        ("涨幅0-40%+距MA20<3%", (df_all['range_change'].between(0, 40)) & (df_all['pos_ma20'] < 3)),
        ("距MA20<0%+刚红柱", (df_all['pos_ma20'] < 0) & (df_all['macd_status'] == '刚刚红柱')),
        ("涨幅<30%+量比<1.5+红柱", (df_all['range_change'] < 30) & (df_all['today_vol_ratio'] < 1.5) & (df_all['macd_status'].str.contains('红柱'))),
    ]

    for name, mask in combos:
        g = df_all[mask]
        v = g['T+5_pct'].dropna()
        v3 = g['T+3_pct'].dropna()
        if len(v) >= 3:
            wr = (v > 0).sum() / len(v) * 100
            wr3 = (v3 > 0).sum() / len(v3) * 100 if len(v3) >= 1 else 0
            print(f"  {name}: T+3={len(v3)}只胜率{wr3:.0f}%, T+5={len(v)}只胜率{wr:.0f}% 平均{v.mean():+.2f}%")

    # 按测试日
    print("\n--- 按测试日 ---")
    for d in sorted(df_all['test_date'].unique()):
        g = df_all[df_all['test_date'] == d]
        v = g['T+5_pct'].dropna()
        if len(v) >= 1:
            wr = (v > 0).sum() / len(v) * 100
            print(f"  {d}: {len(v)}只 | 胜率{wr:.0f}% | 平均{v.mean():+.2f}%")

    # 保存
    output_file = os.path.join(REPORT_DIR, "vol_surge_swing_backtest.csv")
    df_all.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n详细结果已保存: {output_file}")


if __name__ == '__main__':
    main()
