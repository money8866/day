"""
信立泰式量能爆发形态选股公式 - 构建与回测
=====================================
形态特征（基于002294.SZ在20260715的表现）:
1. 量能明显放大：近期均量 vs 起涨前均量放大≥1.3倍
2. 股价和均线同步上升：MA20斜率由负转正（前段下行→后段上行）
3. MACD红柱放大（确认多头）
4. 量能持续性：近5日均量≥近20日均量×0.8
5. 量比活跃：近60天内量比>1.5的天数≥5天
6. 位置安全：距MA20在-5%~+15%之间（不过度高估）
7. 振幅活跃：平均日振幅≥3%

回测目标：T+5 和 T+10 涨幅>=3%的胜率
"""
import sys, os, time
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

import pandas as pd
import numpy as np
from datetime import datetime

CODE = "002294.SZ"
TARGET_DATE = "20260715"


def detect_vol_ma_sync_surge(df, target_idx=None):
    """信立泰式量能爆发形态检测
    核心逻辑：量能放大 + 均线同步上升 + MACD红柱
    
    Args:
        df: 历史数据DataFrame（含close, high, low, vol, pre_close）
        target_idx: 目标日的索引，None则取最后一天
    Returns:
        dict or None: 命中返回特征字典，未命中返回None
    """
    if df is None or len(df) < 80:
        return None
    
    if target_idx is None:
        target_idx = len(df) - 1
    
    # 取目标日前60天窗口
    start_i = max(0, target_idx - 59)
    seg = df.iloc[start_i:target_idx + 1].copy().reset_index(drop=True)
    if len(seg) < 30:
        return None
    
    close_arr = seg['close'].values.astype(float)
    high_arr = seg['high'].values.astype(float)
    low_arr = seg['low'].values.astype(float)
    vol_arr = seg['vol'].values.astype(float)
    pre_close_arr = seg['pre_close'].values.astype(float)
    
    # ============ 1. 量能分析 ============
    vol_ma5 = pd.Series(vol_arr).rolling(5, min_periods=1).mean().values
    vol_ma20 = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    
    # 起涨前20日均量 vs 近20日均量
    if len(vol_arr) < 40:
        return None
    pre_vol_20 = float(np.mean(vol_arr[:20]))  # 前20日均量
    post_vol_20 = float(np.mean(vol_arr[-20:]))  # 近20日均量
    if pre_vol_20 <= 0:
        return None
    vol_surge_ratio = post_vol_20 / pre_vol_20  # 量能放大倍数
    
    # 量比（当日量/20日均量）
    vol_ratio = vol_arr / np.maximum(vol_ma20, 1)
    vol_ratio_gt15 = int(np.sum(vol_ratio > 1.5))
    max_vol_ratio = float(np.max(vol_ratio))
    
    # 量能持续性：近5日均量 / 近20日均量
    vol_recent_5 = float(np.mean(vol_arr[-5:]))
    vol_persistence = vol_recent_5 / max(post_vol_20, 1)
    
    # ============ 2. 均线分析 ============
    ma5 = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10 = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    
    # MA20斜率：前段（5~20日前）vs 后段（近5日）
    if len(ma20) < 25:
        return None
    
    # 关键：前段斜率为负（回调），后段斜率为正（回升）→ 均线同步上升
    ma20_5ago = ma20[-6] if not np.isnan(ma20[-6]) else ma20[0]
    ma20_20ago = ma20[-21] if not np.isnan(ma20[-21]) else ma20[0]
    ma20_now = ma20[-1]
    
    if ma20_5ago <= 0 or ma20_20ago <= 0:
        return None
    
    # 近5日MA20斜率（后段）
    ma20_slope_5d = (ma20_now / ma20_5ago - 1) * 100
    # 前15日MA20斜率（前段：20日前→5日前）
    ma20_slope_pre = (ma20_5ago / ma20_20ago - 1) * 100
    
    # 均线同步上升：后段斜率>0（上升）且前段斜率<后段斜率（转折向上）
    ma_sync_up = ma20_slope_5d > 0 and ma20_slope_5d > ma20_slope_pre
    
    # MA5 > MA10 > MA20（短期多头排列）
    short_multi = ma5[-1] > ma10[-1] > ma20[-1]
    
    # 距MA20
    last_close = close_arr[-1]
    dist_ma20 = (last_close / ma20_now - 1) * 100
    
    # ============ 3. MACD分析 ============
    exp12 = pd.Series(close_arr).ewm(span=12, adjust=False).mean().values
    exp26 = pd.Series(close_arr).ewm(span=26, adjust=False).mean().values
    dif = exp12 - exp26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd = (dif - dea) * 2
    
    last_macd = macd[-1]
    prev_macd = macd[-2] if len(macd) > 1 else 0
    last_dif = dif[-1]
    last_dea = dea[-1]
    
    # MACD状态
    if last_macd > 0 and prev_macd <= 0:
        macd_status = "刚刚红柱"
    elif last_macd > 0 and prev_macd > 0 and last_macd > prev_macd:
        macd_status = "红柱放大"
    elif last_macd > 0 and prev_macd > 0 and last_macd <= prev_macd:
        macd_status = "红柱缩短"
    elif last_macd < 0 and prev_macd < 0 and last_macd > prev_macd:
        macd_status = "绿柱缩短"
    else:
        macd_status = "其他"
    
    # DIF在0轴上方（强势）
    dif_above_zero = last_dif > 0
    
    # ============ 4. 价格形态 ============
    range_high = float(np.max(high_arr))
    range_low = float(np.min(low_arr))
    range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0
    
    amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
    avg_amplitude = float(np.mean(amplitude))
    
    # 区间涨幅
    price_change = (close_arr[-1] / close_arr[0] - 1) * 100 if close_arr[0] > 0 else 0
    
    # ============ 5. 评分系统 ============
    score = 0
    
    # 量能放大（25分）
    if vol_surge_ratio >= 2.0:
        score += 25
    elif vol_surge_ratio >= 1.5:
        score += 20
    elif vol_surge_ratio >= 1.3:
        score += 15
    elif vol_surge_ratio >= 1.1:
        score += 8
    
    # 均线同步上升（25分）- 核心特征
    if ma_sync_up and ma20_slope_5d >= 3:
        score += 25
    elif ma_sync_up and ma20_slope_5d >= 1:
        score += 20
    elif ma_sync_up:
        score += 15
    elif ma20_slope_5d > 0:
        score += 8
    
    # 短期多头排列（10分）
    if short_multi:
        score += 10
    elif ma5[-1] > ma10[-1]:
        score += 5
    
    # MACD红柱（20分）
    if macd_status == "刚刚红柱":
        score += 20
    elif macd_status == "红柱放大":
        score += 18
    elif macd_status == "红柱缩短":
        score += 10
    elif macd_status == "绿柱缩短":
        score += 8
    
    # DIF在0轴上方（5分）
    if dif_above_zero:
        score += 5
    
    # 量能持续性（10分）
    if vol_persistence >= 1.0:
        score += 10
    elif vol_persistence >= 0.8:
        score += 8
    elif vol_persistence >= 0.6:
        score += 4
    
    # 量比活跃（5分）
    if vol_ratio_gt15 >= 10:
        score += 5
    elif vol_ratio_gt15 >= 5:
        score += 4
    elif vol_ratio_gt15 >= 3:
        score += 2
    
    # ============ 6. 硬条件过滤 ============
    # 量能必须放大
    if vol_surge_ratio < 1.1:
        return None
    # MA20近5日必须上升
    if ma20_slope_5d < 0:
        return None
    # MACD必须是红柱
    if last_macd <= 0:
        return None
    # 距MA20不能过远（>20%说明已严重超买）
    if dist_ma20 > 20 or dist_ma20 < -10:
        return None
    # 平均振幅>=2.5%（有一定的活跃度）
    if avg_amplitude < 2.5:
        return None
    # 量能持续性不能太差
    if vol_persistence < 0.5:
        return None
    
    return {
        'vol_surge_ratio': round(vol_surge_ratio, 2),
        'ma20_slope_5d': round(ma20_slope_5d, 2),
        'ma20_slope_pre': round(ma20_slope_pre, 2),
        'ma_sync_up': ma_sync_up,
        'short_multi': short_multi,
        'macd_status': macd_status,
        'dif_above_zero': dif_above_zero,
        'vol_persistence': round(vol_persistence, 2),
        'vol_ratio_gt15': vol_ratio_gt15,
        'max_vol_ratio': round(max_vol_ratio, 2),
        'dist_ma20': round(dist_ma20, 2),
        'range_swing': round(range_swing, 2),
        'avg_amplitude': round(avg_amplitude, 2),
        'price_change': round(price_change, 2),
        'score': score,
        'close': round(last_close, 2),
    }


def backtest_stock(df, target_idx, forward_days=[5, 10]):
    """回测单只股票目标日后的涨跌幅"""
    if df is None or target_idx is None:
        return {}
    
    close_arr = df['close'].values.astype(float)
    target_close = close_arr[target_idx]
    
    result = {}
    for d in forward_days:
        end_idx = target_idx + d
        if end_idx < len(close_arr):
            future_close = close_arr[end_idx]
            chg = (future_close / target_close - 1) * 100
            result[f'T+{d}_chg'] = round(chg, 2)
            result[f'T+{d}_win'] = chg >= 3  # 胜率阈值3%
        else:
            result[f'T+{d}_chg'] = None
            result[f'T+{d}_win'] = None
    
    return result


# ============ 第一步：验证信立泰 ============
print("=" * 70)
print("【第一步】验证信立泰(002294.SZ)在20260715的形态")
print("=" * 70)

df = tq.get_hist_data(CODE)
if df is not None:
    df = df.copy()
    if 'trade_date' in df.columns:
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)
    
    target_idx = None
    for i, d in enumerate(df['trade_date'].values):
        if str(d) == TARGET_DATE:
            target_idx = i
            break
    
    if target_idx is not None:
        result = detect_vol_ma_sync_surge(df, target_idx)
        if result:
            print(f"✅ 信立泰命中！评分={result['score']}")
            print(f"  量能放大: {result['vol_surge_ratio']}倍")
            print(f"  MA20近5日斜率: {result['ma20_slope_5d']}%")
            print(f"  MA20前段斜率: {result['ma20_slope_pre']}%")
            print(f"  均线同步上升: {result['ma_sync_up']}")
            print(f"  短期多头: {result['short_multi']}")
            print(f"  MACD状态: {result['macd_status']}")
            print(f"  量能持续性: {result['vol_persistence']}")
            print(f"  距MA20: {result['dist_ma20']}%")
            
            # 回测
            bt = backtest_stock(df, target_idx)
            print(f"\n  回测结果:")
            for k, v in bt.items():
                print(f"    {k}: {v}")
        else:
            print("❌ 信立泰未命中（检查硬条件）")
    else:
        print(f"❌ 未找到目标日{TARGET_DATE}")

# ============ 第二步：全市场回测 ============
print("\n" + "=" * 70)
print("【第二步】全市场历史回测")
print("=" * 70)

# 加载所有股票代码
all_stocks = tq.load_stock_pool() if hasattr(tq, 'load_stock_pool') else None
if all_stocks is None:
    # 从换手率缓存获取
    ts_codes = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
    all_stocks = ts_codes

print(f"待扫描股票数: {len(all_stocks)}")

# 回测参数
BACKTEST_START = "20260101"  # 回测起始日
BACKTEST_END = "20260715"    # 回测结束日
SCORE_THRESHOLD = 70         # 评分阈值

# 收集所有信号
signals = []
scanned = 0
hit_count = 0

t0 = time.time()
for ts_code in all_stocks[:2000]:  # 先扫前2000只测试
    # 跳过北交所
    if ts_code.startswith('8') or ts_code.startswith('4') or ts_code.startswith('9'):
        continue
    
    scanned += 1
    if scanned % 200 == 0:
        elapsed = time.time() - t0
        print(f"  扫描进度: {scanned}/{len(all_stocks[:2000])}, 命中{hit_count}只, 耗时{elapsed:.0f}s")
    
    try:
        stock_df = tq.get_hist_data(ts_code)
        if stock_df is None or len(stock_df) < 80:
            continue
        
        stock_df = stock_df.copy()
        if 'trade_date' in stock_df.columns:
            stock_df['trade_date'] = stock_df['trade_date'].astype(str)
            stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)
        
        # 在回测期间逐日检测
        for i in range(60, len(stock_df)):
            td = str(stock_df['trade_date'].iloc[i])
            if td < BACKTEST_START or td > BACKTEST_END:
                continue
            
            result = detect_vol_ma_sync_surge(stock_df, i)
            if result and result['score'] >= SCORE_THRESHOLD:
                # 回测
                bt = backtest_stock(stock_df, i, [5, 10])
                if bt.get('T+5_win') is not None:
                    name = tq.get_stock_name(ts_code) if hasattr(tq, 'get_stock_name') else ts_code
                    signals.append({
                        'code': ts_code,
                        'name': name,
                        'date': td,
                        'score': result['score'],
                        'vol_surge': result['vol_surge_ratio'],
                        'ma20_slope': result['ma20_slope_5d'],
                        'macd_status': result['macd_status'],
                        'dist_ma20': result['dist_ma20'],
                        **bt
                    })
                    hit_count += 1
    except Exception as e:
        pass

elapsed = time.time() - t0
print(f"\n扫描完成: {scanned}只, 命中{hit_count}只信号, 耗时{elapsed:.0f}s")

# ============ 第三步：胜率统计 ============
print("\n" + "=" * 70)
print("【第三步】胜率统计")
print("=" * 70)

if signals:
    sig_df = pd.DataFrame(signals)
    
    # 总体胜率
    total = len(sig_df)
    t5_win = sig_df['T+5_win'].sum()
    t10_win = sig_df['T+10_win'].sum()
    
    print(f"\n信号总数: {total}")
    print(f"T+5涨幅>=3%胜率: {t5_win}/{total} = {t5_win/total*100:.1f}%")
    print(f"T+10涨幅>=3%胜率: {t10_win}/{total} = {t10_win/total*100:.1f}%")
    
    # 按评分分组
    print("\n按评分分组胜率:")
    for score_range in [(70, 75), (75, 80), (80, 85), (85, 100)]:
        mask = (sig_df['score'] >= score_range[0]) & (sig_df['score'] < score_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            print(f"  评分[{score_range[0]}-{score_range[1]}): {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%, T+10胜率={t10w/len(sub)*100:.1f}%")
    
    # 按MACD状态分组
    print("\n按MACD状态分组胜率:")
    for status in sig_df['macd_status'].unique():
        sub = sig_df[sig_df['macd_status'] == status]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            print(f"  {status}: {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%, T+10胜率={t10w/len(sub)*100:.1f}%")
    
    # 按距MA20分组
    print("\n按距MA20分组胜率:")
    for dist_range in [(-10, 0), (0, 5), (5, 10), (10, 20)]:
        mask = (sig_df['dist_ma20'] >= dist_range[0]) & (sig_df['dist_ma20'] < dist_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            print(f"  距MA20[{dist_range[0]}~{dist_range[1]}%): {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%, T+10胜率={t10w/len(sub)*100:.1f}%")
    
    # 平均收益
    print(f"\n平均收益:")
    print(f"  T+5平均涨幅: {sig_df['T+5_chg'].mean():.2f}%")
    print(f"  T+10平均涨幅: {sig_df['T+10_chg'].mean():.2f}%")
    
    # 保存信号
    out_path = r"d:\mystock\cache_daily\VolMaSync_Backtest.xlsx"
    sig_df.to_excel(out_path, index=False)
    print(f"\n✅ 信号已保存: {out_path}")
    
    # 显示部分信号样本
    print("\n信号样本（前20只）:")
    print(sig_df[['code', 'name', 'date', 'score', 'vol_surge', 'ma20_slope', 'macd_status', 'dist_ma20', 'T+5_chg', 'T+10_chg']].head(20).to_string())
else:
    print("❌ 未找到任何信号")
    print("建议：降低SCORE_THRESHOLD或放宽硬条件")
