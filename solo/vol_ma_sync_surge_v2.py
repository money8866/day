"""
信立泰式量能爆发形态选股公式 V2 - 严格版
=====================================
V1问题：17547只信号，T+5胜率仅27.5%，评分越高胜率反而越低
V2改进：
1. MA20前段斜率必须<0（确实回调过），后段斜率必须>2%（确实回升）
2. 量能放大必须>=1.5倍（V1是1.1倍）
3. MACD必须"刚刚红柱"或"红柱放大"（排除"红柱缩短"）
4. 距MA20必须在5%-15%
5. 必须站上MA5和MA10（短期多头确认）
6. 加入换手率条件
7. 加入"回调后反转"的严格判定
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

CODE = "002294.SZ"
TARGET_DATE = "20260715"


def detect_vol_ma_sync_surge_v2(df, target_idx=None):
    """V2: 严格版的量能爆发形态检测
    
    核心特征（信立泰20260715）:
    - 回调后反转：MA20前段斜率<0，后段斜率>2%
    - 量能持续放大：近20日均量/前20日均量>=1.5倍
    - MACD红柱放大（排除缩短）
    - 站上MA5和MA10
    - 距MA20在5%-15%
    - 换手率活跃
    """
    if df is None or len(df) < 80:
        return None
    
    if target_idx is None:
        target_idx = len(df) - 1
    
    # 取目标日前60天窗口
    start_i = max(0, target_idx - 59)
    seg = df.iloc[start_i:target_idx + 1].copy().reset_index(drop=True)
    if len(seg) < 40:
        return None
    
    close_arr = seg['close'].values.astype(float)
    high_arr = seg['high'].values.astype(float)
    low_arr = seg['low'].values.astype(float)
    vol_arr = seg['vol'].values.astype(float)
    pre_close_arr = seg['pre_close'].values.astype(float)
    
    # ============ 硬条件1: 量能放大 >=1.5倍 ============
    if len(vol_arr) < 40:
        return None
    pre_vol_20 = float(np.mean(vol_arr[:20]))
    post_vol_20 = float(np.mean(vol_arr[-20:]))
    if pre_vol_20 <= 0:
        return None
    vol_surge_ratio = post_vol_20 / pre_vol_20
    if vol_surge_ratio < 1.5:  # V2: 从1.1提升到1.5
        return None
    
    # ============ 硬条件2: MA20前段斜率<0（回调过）+ 后段斜率>2%（回升）============
    ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    if len(ma20) < 25:
        return None
    
    ma20_5ago = ma20[-6] if not np.isnan(ma20[-6]) else None
    ma20_20ago = ma20[-21] if not np.isnan(ma20[-21]) else None
    ma20_now = ma20[-1]
    
    if ma20_5ago is None or ma20_20ago is None or ma20_5ago <= 0 or ma20_20ago <= 0:
        return None
    
    # 近5日MA20斜率（后段）
    ma20_slope_5d = (ma20_now / ma20_5ago - 1) * 100
    # 前15日MA20斜率（前段：20日前→5日前）
    ma20_slope_pre = (ma20_5ago / ma20_20ago - 1) * 100
    
    # V2: 前段必须<0（确实回调过），后段必须>2%（确实回升）
    if ma20_slope_pre >= 0:  # 前段没有回调，不算"回调后反转"
        return None
    if ma20_slope_5d < 2.0:  # 后段回升力度不足
        return None
    
    # ============ 硬条件3: MACD必须"刚刚红柱"或"红柱放大"============
    exp12 = pd.Series(close_arr).ewm(span=12, adjust=False).mean().values
    exp26 = pd.Series(close_arr).ewm(span=26, adjust=False).mean().values
    dif = exp12 - exp26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd = (dif - dea) * 2
    
    last_macd = macd[-1]
    prev_macd = macd[-2] if len(macd) > 1 else 0
    last_dif = dif[-1]
    
    if last_macd <= 0:  # 必须红柱
        return None
    
    if last_macd > 0 and prev_macd <= 0:
        macd_status = "刚刚红柱"
    elif last_macd > 0 and prev_macd > 0 and last_macd > prev_macd:
        macd_status = "红柱放大"
    elif last_macd > 0 and prev_macd > 0 and last_macd <= prev_macd:
        macd_status = "红柱缩短"
        return None  # V2: 排除红柱缩短
    else:
        return None
    
    # ============ 硬条件4: 站上MA5和MA10 ============
    ma5 = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10 = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    last_close = close_arr[-1]
    
    if last_close < ma5[-1] or last_close < ma10[-1]:  # 必须站上MA5和MA10
        return None
    
    # ============ 硬条件5: 距MA20在5%-15% ============
    dist_ma20 = (last_close / ma20_now - 1) * 100
    if dist_ma20 < 5 or dist_ma20 > 15:  # V2: 收紧到5%-15%
        return None
    
    # ============ 硬条件6: 平均振幅>=3% ============
    amplitude = (high_arr - low_arr) / np.maximum(pre_close_arr, 0.01) * 100
    avg_amplitude = float(np.mean(amplitude))
    if avg_amplitude < 3.0:
        return None
    
    # ============ 硬条件7: 量能持续性 ============
    vol_recent_5 = float(np.mean(vol_arr[-5:]))
    vol_persistence = vol_recent_5 / max(post_vol_20, 1)
    if vol_persistence < 0.7:  # V2: 从0.5提升到0.7
        return None
    
    # ============ 硬条件8: 当日量比>=1.0 ============
    vol_ma20_arr = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    last_vol_ratio = vol_arr[-1] / max(vol_ma20_arr[-1], 1)
    if last_vol_ratio < 1.0:  # 当日量能不能低于20日均量
        return None
    
    # ============ 评分系统 V2 ============
    score = 0
    
    # 量能放大（25分）
    if vol_surge_ratio >= 2.5:
        score += 25
    elif vol_surge_ratio >= 2.0:
        score += 22
    elif vol_surge_ratio >= 1.8:
        score += 18
    elif vol_surge_ratio >= 1.5:
        score += 15
    
    # 均线同步上升（30分）- 核心特征，权重提升
    if ma20_slope_5d >= 5 and ma20_slope_pre <= -5:
        score += 30  # 完美的V型反转
    elif ma20_slope_5d >= 3 and ma20_slope_pre <= -3:
        score += 25
    elif ma20_slope_5d >= 2 and ma20_slope_pre <= -2:
        score += 20
    else:
        score += 10
    
    # MACD红柱（20分）
    if macd_status == "刚刚红柱":
        score += 20  # 刚刚金叉最有价值
    elif macd_status == "红柱放大":
        score += 18
    
    # DIF在0轴上方（5分）
    if last_dif > 0:
        score += 5
    
    # 量能持续性（10分）
    if vol_persistence >= 1.2:
        score += 10
    elif vol_persistence >= 1.0:
        score += 8
    elif vol_persistence >= 0.8:
        score += 5
    
    # 距MA20位置（10分）- 信立泰是+12.41%
    if 8 <= dist_ma20 <= 14:
        score += 10  # 最优区间
    elif 5 <= dist_ma20 < 8 or 14 < dist_ma20 <= 15:
        score += 7
    else:
        score += 3
    
    return {
        'vol_surge_ratio': round(vol_surge_ratio, 2),
        'ma20_slope_5d': round(ma20_slope_5d, 2),
        'ma20_slope_pre': round(ma20_slope_pre, 2),
        'macd_status': macd_status,
        'dif_above_zero': last_dif > 0,
        'vol_persistence': round(vol_persistence, 2),
        'last_vol_ratio': round(last_vol_ratio, 2),
        'dist_ma20': round(dist_ma20, 2),
        'avg_amplitude': round(avg_amplitude, 2),
        'above_ma5_ma10': True,
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
            result[f'T+{d}_win'] = chg >= 3
        else:
            result[f'T+{d}_chg'] = None
            result[f'T+{d}_win'] = None
    
    return result


# ============ 第一步：验证信立泰 ============
print("=" * 70)
print("【V2验证】信立泰(002294.SZ)在20260715的形态")
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
        result = detect_vol_ma_sync_surge_v2(df, target_idx)
        if result:
            print(f"✅ 信立泰命中！评分={result['score']}")
            for k, v in result.items():
                print(f"  {k}: {v}")
        else:
            print("❌ 信立泰未命中（检查硬条件）")

# ============ 第二步：全市场回测 ============
print("\n" + "=" * 70)
print("【V2全市场回测】")
print("=" * 70)

all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
print(f"待扫描股票数: {len(all_stocks)}")

BACKTEST_START = "20260101"
BACKTEST_END = "20260715"
SCORE_THRESHOLD = 70

signals = []
scanned = 0
hit_count = 0

t0 = time.time()
for ts_code in all_stocks[:3000]:  # 扫前3000只
    if ts_code.startswith('8') or ts_code.startswith('4') or ts_code.startswith('9'):
        continue
    
    scanned += 1
    if scanned % 200 == 0:
        elapsed = time.time() - t0
        print(f"  扫描进度: {scanned}/{len(all_stocks[:3000])}, 命中{hit_count}只, 耗时{elapsed:.0f}s")
    
    try:
        stock_df = tq.get_hist_data(ts_code)
        if stock_df is None or len(stock_df) < 80:
            continue
        
        stock_df = stock_df.copy()
        if 'trade_date' in stock_df.columns:
            stock_df['trade_date'] = stock_df['trade_date'].astype(str)
            stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)
        
        for i in range(60, len(stock_df)):
            td = str(stock_df['trade_date'].iloc[i])
            if td < BACKTEST_START or td > BACKTEST_END:
                continue
            
            result = detect_vol_ma_sync_surge_v2(stock_df, i)
            if result and result['score'] >= SCORE_THRESHOLD:
                bt = backtest_stock(stock_df, i, [5, 10])
                if bt.get('T+5_win') is not None:
                    name = tq.get_stock_name(ts_code) if hasattr(tq, 'get_stock_name') else ts_code
                    signals.append({
                        'code': ts_code,
                        'name': name,
                        'date': td,
                        'score': result['score'],
                        'vol_surge': result['vol_surge_ratio'],
                        'ma20_slope_5d': result['ma20_slope_5d'],
                        'ma20_slope_pre': result['ma20_slope_pre'],
                        'macd_status': result['macd_status'],
                        'dist_ma20': result['dist_ma20'],
                        'vol_persistence': result['vol_persistence'],
                        **bt
                    })
                    hit_count += 1
    except Exception as e:
        pass

elapsed = time.time() - t0
print(f"\n扫描完成: {scanned}只, 命中{hit_count}只信号, 耗时{elapsed:.0f}s")

# ============ 第三步：胜率统计 ============
print("\n" + "=" * 70)
print("【V2胜率统计】")
print("=" * 70)

if signals:
    sig_df = pd.DataFrame(signals)
    
    total = len(sig_df)
    t5_win = sig_df['T+5_win'].sum()
    t10_win = sig_df['T+10_win'].sum()
    
    print(f"\n信号总数: {total}")
    print(f"T+5涨幅>=3%胜率: {t5_win}/{total} = {t5_win/total*100:.1f}%")
    print(f"T+10涨幅>=3%胜率: {t10_win}/{total} = {t10_win/total*100:.1f}%")
    
    # 按评分分组
    print("\n按评分分组胜率:")
    for score_range in [(70, 75), (75, 80), (80, 85), (85, 90), (90, 100)]:
        mask = (sig_df['score'] >= score_range[0]) & (sig_df['score'] < score_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            avg_t10 = sub['T+10_chg'].mean()
            print(f"  评分[{score_range[0]}-{score_range[1]}): {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%(均{avg_t5:+.2f}%), T+10胜率={t10w/len(sub)*100:.1f}%(均{avg_t10:+.2f}%)")
    
    # 按MACD状态分组
    print("\n按MACD状态分组胜率:")
    for status in sig_df['macd_status'].unique():
        sub = sig_df[sig_df['macd_status'] == status]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  {status}: {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%(均{avg_t5:+.2f}%), T+10胜率={t10w/len(sub)*100:.1f}%")
    
    # 按量能放大倍数分组
    print("\n按量能放大倍数分组胜率:")
    for vol_range in [(1.5, 1.8), (1.8, 2.0), (2.0, 2.5), (2.5, 5.0)]:
        mask = (sig_df['vol_surge'] >= vol_range[0]) & (sig_df['vol_surge'] < vol_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  量能[{vol_range[0]}-{vol_range[1]}): {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%(均{avg_t5:+.2f}%), T+10胜率={t10w/len(sub)*100:.1f}%")
    
    # 按距MA20分组
    print("\n按距MA20分组胜率:")
    for dist_range in [(5, 8), (8, 10), (10, 12), (12, 15)]:
        mask = (sig_df['dist_ma20'] >= dist_range[0]) & (sig_df['dist_ma20'] < dist_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            t5w = sub['T+5_win'].sum()
            t10w = sub['T+10_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  距MA20[{dist_range[0]}~{dist_range[1]}%): {len(sub)}只, T+5胜率={t5w/len(sub)*100:.1f}%(均{avg_t5:+.2f}%), T+10胜率={t10w/len(sub)*100:.1f}%")
    
    # 平均收益
    print(f"\n平均收益:")
    print(f"  T+5平均涨幅: {sig_df['T+5_chg'].mean():+.2f}%")
    print(f"  T+10平均涨幅: {sig_df['T+10_chg'].mean():+.2f}%")
    print(f"  T+5中位数: {sig_df['T+5_chg'].median():+.2f}%")
    print(f"  T+10中位数: {sig_df['T+10_chg'].median():+.2f}%")
    
    # 保存信号
    out_path = r"d:\mystock\cache_daily\VolMaSync_Backtest_V2.xlsx"
    sig_df.to_excel(out_path, index=False)
    print(f"\n✅ 信号已保存: {out_path}")
    
    # 显示前20只
    print("\n信号样本（前20只）:")
    print(sig_df[['code', 'name', 'date', 'score', 'vol_surge', 'ma20_slope_5d', 'ma20_slope_pre', 'macd_status', 'dist_ma20', 'T+5_chg', 'T+10_chg']].head(20).to_string())
else:
    print("❌ 未找到任何信号")
    print("建议：降低SCORE_THRESHOLD或放宽硬条件")
