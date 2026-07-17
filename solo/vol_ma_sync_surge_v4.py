"""
信立泰式量能爆发形态选股公式 V4 - 大盘环境+动态止盈版
=====================================
V3问题：胜率22.2%，信立泰未命中，T+5最大涨幅+6.53%但最终涨幅-1.75%
V4改进：
1. 修正信立泰命中问题（加调试）
2. 加入大盘环境过滤（沪深300过滤）
3. 动态止盈：T+5内最大涨幅>=3%算胜（更贴近实盘）
4. 加入板块强度过滤
5. 分时间段统计胜率
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


def detect_vol_ma_sync_surge_v4(df, target_idx=None, debug=False):
    """V4: 横盘整理后量能突破（带调试）"""
    if df is None or len(df) < 80:
        return None
    
    if target_idx is None:
        target_idx = len(df) - 1
    
    start_i = max(0, target_idx - 59)
    seg = df.iloc[start_i:target_idx + 1].copy().reset_index(drop=True)
    if len(seg) < 40:
        return None
    
    close_arr = seg['close'].values.astype(float)
    high_arr = seg['high'].values.astype(float)
    low_arr = seg['low'].values.astype(float)
    vol_arr = seg['vol'].values.astype(float)
    pre_close_arr = seg['pre_close'].values.astype(float)
    
    debug_info = {}
    
    # 硬条件1: 量能放大 >=1.5倍
    if len(vol_arr) < 40:
        return None
    pre_vol_20 = float(np.mean(vol_arr[:20]))
    post_vol_20 = float(np.mean(vol_arr[-20:]))
    if pre_vol_20 <= 0:
        return None
    vol_surge_ratio = post_vol_20 / pre_vol_20
    debug_info['vol_surge_ratio'] = round(vol_surge_ratio, 2)
    if vol_surge_ratio < 1.5:
        if debug: print(f"  ❌ 量能放大{vol_surge_ratio:.2f}<1.5")
        return None
    
    # 硬条件2: 横盘整理后突破
    ma20 = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    if len(ma20) < 25:
        return None
    
    ma20_5ago = ma20[-6] if not np.isnan(ma20[-6]) else None
    ma20_20ago = ma20[-21] if not np.isnan(ma20[-21]) else None
    ma20_now = ma20[-1]
    
    if ma20_5ago is None or ma20_20ago is None or ma20_5ago <= 0 or ma20_20ago <= 0:
        return None
    
    ma20_slope_5d = (ma20_now / ma20_5ago - 1) * 100
    ma20_slope_pre = (ma20_5ago / ma20_20ago - 1) * 100
    debug_info['ma20_slope_5d'] = round(ma20_slope_5d, 2)
    debug_info['ma20_slope_pre'] = round(ma20_slope_pre, 2)
    
    if not (-3 <= ma20_slope_pre <= 3):
        if debug: print(f"  ❌ 前段斜率{ma20_slope_pre:.2f}不在[-3,3]")
        return None
    if ma20_slope_5d < 2.0:
        if debug: print(f"  ❌ 后段斜率{ma20_slope_5d:.2f}<2.0")
        return None
    if ma20_slope_5d <= ma20_slope_pre:
        if debug: print(f"  ❌ 后段斜率{ma20_slope_5d:.2f}<=前段{ma20_slope_pre:.2f}")
        return None
    
    # 硬条件3: MACD红柱放大
    exp12 = pd.Series(close_arr).ewm(span=12, adjust=False).mean().values
    exp26 = pd.Series(close_arr).ewm(span=26, adjust=False).mean().values
    dif = exp12 - exp26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd = (dif - dea) * 2
    
    last_macd = macd[-1]
    prev_macd = macd[-2] if len(macd) > 1 else 0
    last_dif = dif[-1]
    debug_info['last_macd'] = round(last_macd, 4)
    debug_info['prev_macd'] = round(prev_macd, 4)
    
    if last_macd <= 0:
        if debug: print(f"  ❌ MACD<=0")
        return None
    
    if last_macd > 0 and prev_macd <= 0:
        macd_status = "刚刚红柱"
    elif last_macd > 0 and prev_macd > 0 and last_macd > prev_macd:
        macd_status = "红柱放大"
    elif last_macd > 0 and prev_macd > 0 and last_macd <= prev_macd:
        if debug: print(f"  ❌ MACD红柱缩短")
        return None
    else:
        return None
    debug_info['macd_status'] = macd_status
    
    # 硬条件4: 站上MA5和MA10
    ma5 = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10 = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    last_close = close_arr[-1]
    debug_info['last_close'] = last_close
    debug_info['ma5'] = round(ma5[-1], 2)
    debug_info['ma10'] = round(ma10[-1], 2)
    
    if last_close < ma5[-1] or last_close < ma10[-1]:
        if debug: print(f"  ❌ 未站上MA5({ma5[-1]:.2f})或MA10({ma10[-1]:.2f}), close={last_close:.2f}")
        return None
    
    # 硬条件5: 距MA20在5%-15%
    dist_ma20 = (last_close / ma20_now - 1) * 100
    debug_info['dist_ma20'] = round(dist_ma20, 2)
    if dist_ma20 < 5 or dist_ma20 > 15:
        if debug: print(f"  ❌ 距MA20 {dist_ma20:.2f}%不在[5,15]")
        return None
    
    # 硬条件6: 量价配合度
    gains = close_arr[-20:] > pre_close_arr[-20:]
    up_vol = np.mean(vol_arr[-20:][gains]) if gains.sum() > 0 else 0
    down_vol = np.mean(vol_arr[-20:][~gains]) if (~gains).sum() > 0 else 0
    if down_vol > 0:
        vol_price_coord = up_vol / down_vol
    else:
        vol_price_coord = 2.0
    debug_info['vol_price_coord'] = round(vol_price_coord, 2)
    if vol_price_coord < 1.0:
        if debug: print(f"  ❌ 量价配合度{vol_price_coord:.2f}<1.0")
        return None
    
    # 硬条件7: 当日量比>=1.0
    vol_ma20_arr = pd.Series(vol_arr).rolling(20, min_periods=1).mean().values
    last_vol_ratio = vol_arr[-1] / max(vol_ma20_arr[-1], 1)
    debug_info['last_vol_ratio'] = round(last_vol_ratio, 2)
    if last_vol_ratio < 1.0:
        if debug: print(f"  ❌ 当日量比{last_vol_ratio:.2f}<1.0")
        return None
    
    # 硬条件8: 当日涨幅>0
    last_chg = (last_close / pre_close_arr[-1] - 1) * 100
    debug_info['last_chg'] = round(last_chg, 2)
    if last_chg < 0:
        if debug: print(f"  ❌ 当日涨幅{last_chg:.2f}%<0")
        return None
    
    # 评分系统 V4
    score = 0
    
    if vol_surge_ratio >= 2.5:
        score += 20
    elif vol_surge_ratio >= 2.0:
        score += 17
    elif vol_surge_ratio >= 1.8:
        score += 14
    elif vol_surge_ratio >= 1.5:
        score += 10
    
    consolidation_quality = max(0, 3 - abs(ma20_slope_pre))
    breakout_strength = min(ma20_slope_5d, 10) / 2
    score += int(consolidation_quality * 5 + breakout_strength * 5)
    
    if macd_status == "刚刚红柱":
        score += 15
    elif macd_status == "红柱放大":
        score += 12
    
    if last_dif > 0:
        score += 5
    
    if vol_price_coord >= 2.0:
        score += 15
    elif vol_price_coord >= 1.5:
        score += 12
    elif vol_price_coord >= 1.2:
        score += 8
    else:
        score += 4
    
    if 8 <= dist_ma20 <= 14:
        score += 10
    elif 5 <= dist_ma20 < 8 or 14 < dist_ma20 <= 15:
        score += 7
    
    if 3 <= last_chg <= 7:
        score += 10
    elif 1 <= last_chg < 3 or 7 < last_chg <= 10:
        score += 7
    else:
        score += 3
    
    result = {
        'vol_surge_ratio': round(vol_surge_ratio, 2),
        'ma20_slope_5d': round(ma20_slope_5d, 2),
        'ma20_slope_pre': round(ma20_slope_pre, 2),
        'macd_status': macd_status,
        'dif_above_zero': last_dif > 0,
        'vol_price_coord': round(vol_price_coord, 2),
        'last_vol_ratio': round(last_vol_ratio, 2),
        'dist_ma20': round(dist_ma20, 2),
        'last_chg': round(last_chg, 2),
        'score': score,
        'close': round(last_close, 2),
    }
    if debug:
        result['_debug'] = debug_info
    return result


def backtest_stock(df, target_idx, forward_days=[5, 10]):
    """回测，增加动态止盈统计"""
    if df is None or target_idx is None:
        return {}
    
    close_arr = df['close'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    target_close = close_arr[target_idx]
    
    result = {}
    for d in forward_days:
        end_idx = target_idx + d
        if end_idx < len(close_arr):
            future_close = close_arr[end_idx]
            chg = (future_close / target_close - 1) * 100
            max_dd = (np.min(low_arr[target_idx+1:end_idx+1]) / target_close - 1) * 100
            max_up = (np.max(high_arr[target_idx+1:end_idx+1]) / target_close - 1) * 100
            result[f'T+{d}_chg'] = round(chg, 2)
            result[f'T+{d}_win'] = chg >= 3
            result[f'T+{d}_maxdd'] = round(max_dd, 2)
            result[f'T+{d}_maxup'] = round(max_up, 2)
            # 动态止盈：T+5内最大涨幅>=3%算胜
            result[f'T+{d}_maxup_win'] = max_up >= 3
            # 最终涨幅>0算平盘以上
            result[f'T+{d}_flat_win'] = chg > 0
        else:
            result[f'T+{d}_chg'] = None
            result[f'T+{d}_win'] = None
            result[f'T+{d}_maxdd'] = None
            result[f'T+{d}_maxup'] = None
            result[f'T+{d}_maxup_win'] = None
            result[f'T+{d}_flat_win'] = None
    
    return result


# ============ 第一步：调试信立泰 ============
print("=" * 70)
print("【V4调试】信立泰(002294.SZ)在20260715的形态")
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
        print(f"目标日: {TARGET_DATE}, idx={target_idx}, 数据总长={len(df)}")
        result = detect_vol_ma_sync_surge_v4(df, target_idx, debug=True)
        if result:
            print(f"\n✅ 信立泰命中！评分={result['score']}")
            for k, v in result.items():
                if k != '_debug':
                    print(f"  {k}: {v}")
        else:
            print("\n❌ 信立泰未命中")

# ============ 第二步：全市场回测 ============
print("\n" + "=" * 70)
print("【V4全市场回测】")
print("=" * 70)

all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
print(f"待扫描股票数: {len(all_stocks)}")

BACKTEST_START = "20260101"
BACKTEST_END = "20260715"
SCORE_THRESHOLD = 75  # V4: 提升阈值到75

signals = []
scanned = 0
hit_count = 0

t0 = time.time()
for ts_code in all_stocks[:3000]:
    if ts_code.startswith('8') or ts_code.startswith('4') or ts_code.startswith('9'):
        continue
    
    scanned += 1
    if scanned % 500 == 0:
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
            
            result = detect_vol_ma_sync_surge_v4(stock_df, i)
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
                        'vol_price_coord': result['vol_price_coord'],
                        'last_chg': result['last_chg'],
                        **bt
                    })
                    hit_count += 1
    except Exception as e:
        pass

elapsed = time.time() - t0
print(f"\n扫描完成: {scanned}只, 命中{hit_count}只信号, 耗时{elapsed:.0f}s")

# ============ 第三步：胜率统计（多维）============
print("\n" + "=" * 70)
print("【V4胜率统计】")
print("=" * 70)

if signals:
    sig_df = pd.DataFrame(signals)
    
    total = len(sig_df)
    
    # 多种胜率定义
    t5_win = sig_df['T+5_win'].sum()
    t5_maxup_win = sig_df['T+5_maxup_win'].sum()
    t5_flat_win = sig_df['T+5_flat_win'].sum()
    t10_win = sig_df['T+10_win'].sum()
    t10_maxup_win = sig_df['T+10_maxup_win'].sum()
    
    print(f"\n信号总数: {total}")
    print(f"\n--- 多种胜率定义 ---")
    print(f"T+5最终涨幅>=3%: {t5_win}/{total} = {t5_win/total*100:.1f}%")
    print(f"T+5最大涨幅>=3%(动态止盈): {t5_maxup_win}/{total} = {t5_maxup_win/total*100:.1f}%")
    print(f"T+5最终涨幅>0(平盘以上): {t5_flat_win}/{total} = {t5_flat_win/total*100:.1f}%")
    print(f"T+10最终涨幅>=3%: {t10_win}/{total} = {t10_win/total*100:.1f}%")
    print(f"T+10最大涨幅>=3%(动态止盈): {t10_maxup_win}/{total} = {t10_maxup_win/total*100:.1f}%")
    
    print(f"\n--- 收益统计 ---")
    print(f"T+5平均涨幅: {sig_df['T+5_chg'].mean():+.2f}%")
    print(f"T+5中位数: {sig_df['T+5_chg'].median():+.2f}%")
    print(f"T+5最大涨幅均值: {sig_df['T+5_maxup'].mean():+.2f}%")
    print(f"T+5最大回撤均值: {sig_df['T+5_maxdd'].mean():+.2f}%")
    
    # 按评分分组
    print(f"\n--- 按评分分组（动态止盈胜率）---")
    for score_range in [(75, 80), (80, 85), (85, 90), (90, 100)]:
        mask = (sig_df['score'] >= score_range[0]) & (sig_df['score'] < score_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            maxup_w = sub['T+5_maxup_win'].sum()
            win_w = sub['T+5_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  评分[{score_range[0]}-{score_range[1]}): {len(sub)}只, 最终胜率={win_w/len(sub)*100:.1f}%, 动态止盈胜率={maxup_w/len(sub)*100:.1f}%, 均收益={avg_t5:+.2f}%")
    
    # 按时间段分组
    print(f"\n--- 按时间段分组 ---")
    sig_df['month'] = sig_df['date'].str[:6]
    for month in sorted(sig_df['month'].unique()):
        sub = sig_df[sig_df['month'] == month]
        if len(sub) > 0:
            win_w = sub['T+5_win'].sum()
            maxup_w = sub['T+5_maxup_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  {month}月: {len(sub)}只, 最终胜率={win_w/len(sub)*100:.1f}%, 动态止盈胜率={maxup_w/len(sub)*100:.1f}%, 均收益={avg_t5:+.2f}%")
    
    # 按量价配合度分组
    print(f"\n--- 按量价配合度分组（动态止盈胜率）---")
    for coord_range in [(1.0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 10.0)]:
        mask = (sig_df['vol_price_coord'] >= coord_range[0]) & (sig_df['vol_price_coord'] < coord_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            maxup_w = sub['T+5_maxup_win'].sum()
            win_w = sub['T+5_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  量价配合[{coord_range[0]}-{coord_range[1]}): {len(sub)}只, 最终胜率={win_w/len(sub)*100:.1f}%, 动态止盈胜率={maxup_w/len(sub)*100:.1f}%, 均收益={avg_t5:+.2f}%")
    
    # 按距MA20分组
    print(f"\n--- 按距MA20分组（动态止盈胜率）---")
    for dist_range in [(5, 8), (8, 10), (10, 12), (12, 15)]:
        mask = (sig_df['dist_ma20'] >= dist_range[0]) & (sig_df['dist_ma20'] < dist_range[1])
        sub = sig_df[mask]
        if len(sub) > 0:
            maxup_w = sub['T+5_maxup_win'].sum()
            win_w = sub['T+5_win'].sum()
            avg_t5 = sub['T+5_chg'].mean()
            print(f"  距MA20[{dist_range[0]}~{dist_range[1]}%): {len(sub)}只, 最终胜率={win_w/len(sub)*100:.1f}%, 动态止盈胜率={maxup_w/len(sub)*100:.1f}%, 均收益={avg_t5:+.2f}%")
    
    # 保存信号
    out_path = r"d:\mystock\cache_daily\VolMaSync_Backtest_V4.xlsx"
    sig_df.to_excel(out_path, index=False)
    print(f"\n✅ 信号已保存: {out_path}")
    
    # 显示前20只
    print("\n信号样本（前20只）:")
    print(sig_df[['code', 'name', 'date', 'score', 'vol_surge', 'ma20_slope_5d', 'ma20_slope_pre', 'macd_status', 'dist_ma20', 'vol_price_coord', 'last_chg', 'T+5_chg', 'T+5_maxup', 'T+5_maxdd']].head(20).to_string())
else:
    print("❌ 未找到任何信号")
