"""测试V2回踩逻辑：临时关闭大盘过滤器，验证信号检测和回踩买点"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

# 清空观察池
WATCHLIST_FILE = r"d:\mystock\cache_daily\VolMaSync_WatchList.json"
if os.path.exists(WATCHLIST_FILE):
    os.remove(WATCHLIST_FILE)
    print("已清空旧观察池")

import importlib.util
import pandas as pd
import numpy as np
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

# 重新加载V2模块
spec2 = importlib.util.spec_from_file_location("vol_scan", r"d:\mystock\solo\vol_ma_sync_surge_scan.py")
vol_scan = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(vol_scan)

# 临时禁用大盘过滤器来测试信号逻辑
original_get_regime = vol_scan.get_market_regime
def mock_get_regime():
    return {'allow_trade': True, 'regime': 'bull', 'reason': '测试模式-禁用过滤器',
            'sh_close': 3900, 'sh_ma20': 3880, 'sh_chg_5d': 1.5}
vol_scan.get_market_regime = mock_get_regime

# 测试依依股份的已知案例（20260708信号日，20260710 T+2回踩）
test_cases = [
    ("001206.SZ", "依依股份", "20260708", "20260710"),
]

for ts_code, name, sig_date, buy_date in test_cases:
    print(f"\n{'='*70}")
    print(f"测试个股: {ts_code} {name}")
    print(f"信号日(T): {sig_date}, 预期买点(T+2): {buy_date}")
    print(f"{'='*70}\n")
    
    # 先跑信号日
    tq.TRADE_DATE = sig_date
    vol_scan.tq.TRADE_DATE = sig_date
    
    df = tq.get_hist_data(ts_code)
    if df is None:
        print(f"无法获取{ts_code}数据")
        continue
    
    print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
    print(f"总天数: {len(df)}")
    
    # 检测T日信号
    sig = vol_scan.detect_vol_ma_sync_surge(df)
    if sig:
        print(f"\n✅ T日({sig_date})信号检测成功:")
        print(f"   评分: {sig['score']}")
        print(f"   量能放大: {sig['vol_surge_ratio']}x")
        print(f"   MA20斜率: {sig['ma20_slope_5d']}%")
        print(f"   收盘价: {sig['close']}")
        print(f"   距MA20: {sig['dist_ma20']}%")
        print(f"   MACD: {sig['macd_status']}")
    else:
        print(f"\n❌ T日({sig_date})未检测到信号")
        # 看看是哪个条件不满足
        df_sorted = df.sort_values('trade_date').reset_index(drop=True)
        mask = df_sorted['trade_date'] == sig_date
        if mask.any():
            idx = df_sorted.index[mask][0]
            print(f"   信号日索引位置: {idx}")
            seg = df_sorted.iloc[max(0,idx-59):idx+1].copy().reset_index(drop=True)
            close_arr = seg['close'].values.astype(float)
            vol_arr = seg['vol'].values.astype(float)
            pre_vol_20 = float(np.mean(vol_arr[:20]))
            post_vol_20 = float(np.mean(vol_arr[-20:]))
            print(f"   前20日均量: {pre_vol_20:.0f}, 后20日均量: {post_vol_20:.0f}, 量比: {post_vol_20/pre_vol_20:.2f}")
    
    # 模拟把信号加入观察池，然后检测T+2日
    if sig:
        watchlist = {ts_code: {sig_date: sig}}
        vol_scan.save_watchlist(watchlist)
        print(f"\n已将{sig_date}信号加入观察池")
        
        # 跑买点日
        tq.TRADE_DATE = buy_date
        vol_scan.tq.TRADE_DATE = buy_date
        
        df2 = tq.get_hist_data(ts_code)
        pb = vol_scan.detect_pullback_buy(df2, sig, sig_date, buy_date)
        if pb:
            print(f"\n✅ T+{pb['buy_offset']}日({buy_date})回踩买点检测成功:")
            print(f"   买点评分: {pb['buy_score']}")
            print(f"   买入价: {pb['buy_price']}")
            print(f"   当日涨跌: {pb['pct_on_buy_day']}%")
            print(f"   量比(vs信号日): {pb['vol_ratio']}")
            print(f"   回踩均线: {pb['touch_ma']}")
            print(f"   距MA5: {pb['dist_ma5']}%")
            print(f"   距MA10: {pb['dist_ma10']}%")
        else:
            print(f"\n❌ T+2日({buy_date})未检测到回踩买点")
            # 调试看看条件
            df2 = df2.sort_values('trade_date').reset_index(drop=True)
            mask_sig = df2['trade_date'] == sig_date
            mask_today = df2['trade_date'] == buy_date
            if mask_sig.any() and mask_today.any():
                sig_idx = df2.index[mask_sig][0]
                today_idx = df2.index[mask_today][0]
                offset = today_idx - sig_idx
                print(f"   T+{offset}")
                close_arr = df2['close'].values.astype(float)
                low_arr = df2['low'].values.astype(float)
                vol_arr = df2['vol'].values.astype(float)
                pre_close_arr = df2['pre_close'].values.astype(float)
                import pandas as pd
                import numpy as np
                ma5_arr = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
                ma10_arr = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
                ma20_arr = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
                signal_vol = sig['signal_vol']
                c = close_arr[today_idx]
                l = low_arr[today_idx]
                v = vol_arr[today_idx]
                pct = (c / pre_close_arr[today_idx] - 1) * 100
                ma5 = ma5_arr[today_idx]
                ma10 = ma10_arr[today_idx]
                ma20 = ma20_arr[today_idx]
                print(f"   收盘价: {c:.2f}, 最低: {l:.2f}, 涨跌幅: {pct:.2f}%")
                print(f"   成交量: {v:.0f}, 信号日量: {signal_vol:.0f}, 比值: {v/signal_vol:.2f} (需<0.7)")
                print(f"   MA5: {ma5:.2f}, MA10: {ma10:.2f}, MA20: {ma20:.2f}")
                print(f"   最低/MA5: {l/ma5:.3f}, 最低/MA10: {l/ma10:.3f}, 最低/MA20: {l/ma20:.3f}")
