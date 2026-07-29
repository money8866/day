"""对比两只股票在 20260724 vs 20260728 的状态"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
pd.set_option('display.max_rows', 60)
pd.set_option('display.width', 250)
pd.set_option('display.max_columns', 20)
pd.set_option('display.float_format', lambda x: '%.2f' % x)

from mainline_pullback.data_loader import load_local_data
from mainline_pullback.config import get_config

stocks = [
    ("301310.SZ", "鑫宏业"),
    ("688057.SH", "金达莱"),
]
cfg = get_config()
cfg_m = cfg.momentum
cfg_p = cfg.pullback

for ts_code, name in stocks:
    print(f"\n{'='*70}")
    print(f"【{name}({ts_code})】")
    print(f"{'='*70}")
    
    df = load_local_data("daily", ts_code=ts_code, start_date="20260601", end_date="20260728")
    if df is None:
        continue
    for c in ["open","high","low","close","vol","amount","pct_chg"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    
    # 找 7/24 和 7/28 的位置
    for check_date in ["20260724", "20260728"]:
        mask = df["trade_date"] == check_date
        if not mask.any():
            print(f"\n  [{check_date}] 无数据")
            continue
        idx = df[mask].index[0]
        row = df.loc[idx]
        recent20 = df.loc[max(0,idx-19):idx]  # 包含自身的20行
        
        wave_high = recent20["high"].max()
        wave_low = recent20["low"].min()
        wave_gain = wave_high / wave_low
        limit_cnt = int(((recent20["pct_chg"] >= cfg_m.limit_up_threshold) | (recent20["pct_chg"] >= cfg_m.big_positive_threshold)).sum())
        
        pk_idx = recent20["high"].idxmax()
        pk_high = df.loc[pk_idx, "high"]
        pullback = (pk_high - row["close"]) / pk_high
        
        ma10_v = df.loc[idx, "ma10"]
        ma20_v = df.loc[idx, "ma20"]
        dist10 = (row["close"] - ma10_v) / ma10_v * 100
        dist20 = (row["close"] - ma20_v) / ma20_v * 100
        dist_ok = abs(dist10) <= cfg_p.support_tolerance*100 or abs(dist20) <= cfg_p.support_tolerance*100
        
        before = max(pk_idx - cfg_p.vol_peak_window, 0)
        after = min(pk_idx + cfg_p.vol_peak_window, len(df)-1)
        vol_peak = df.loc[before:after, "vol"].max()
        vol_shrink = row["vol"] / vol_peak if vol_peak > 0 else 1.0
        shrink_ok = vol_shrink < cfg_p.max_vol_ratio
        pullback_ok = cfg_p.min_pullback <= pullback <= cfg_p.max_pullback
        
        print(f"\n  [{check_date}] 收盘:{row['close']:.2f} 涨幅:{row['pct_chg']:+.2f}% 量:{row['vol']:.0f}")
        print(f"    B层: 涨幅{(wave_gain-1)*100:.1f}%{'✅' if wave_gain>=cfg_m.min_wave_gain else '❌'} | 大阳{limit_cnt}次{'✅' if limit_cnt>=cfg_m.min_limit_up_count else '❌'}")
        print(f"    C1回撤: {pullback*100:.1f}% (高{pk_high:.2f}) {'✅' if pullback_ok else '❌'}")
        print(f"    C2均线: MA10:{dist10:+.2f}% MA20:{dist20:+.2f}% {'✅' if dist_ok else '❌'}")
        print(f"    C3缩量: {vol_shrink:.2f} (峰{vol_peak:.0f}) {'✅' if shrink_ok else '❌'}")
        print(f"    → 全部通过: {'✅✅✅' if (pullback_ok and dist_ok and shrink_ok) else '❌'}")
