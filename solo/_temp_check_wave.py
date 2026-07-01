import tushare as ts
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import os, sys
load_dotenv("d:/mystock/config/.env")
ts.set_token(os.getenv("TUSHARE_TOKEN"))
pro = ts.pro_api()

sys.path.insert(0, r"d:\mystock\solo")
from tushare_quant import get_hist_data

def analyze(code, name):
    df = get_hist_data(code)
    if df is None:
        return
    df = df.tail(90)
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    vol = df["vol"].values.astype(float)

    peak_idx = int(np.argmax(high))
    peak_price = float(high[peak_idx])
    current_price = float(close[-1])

    if peak_idx < len(df) - 1:
        post_peak_low = float(np.min(low[peak_idx:]))
        pullback_from_peak = (1 - post_peak_low / peak_price) * 100
    else:
        pullback_from_peak = 0

    pre_peak_low = float(np.min(low[:peak_idx+1])) if peak_idx > 0 else float(low[0])
    pre_peak_gain = (peak_price / pre_peak_low - 1) * 100

    dist_from_peak = (1 - current_price / peak_price) * 100

    if peak_idx < len(df) - 10:
        post_peak_prices = close[peak_idx:]
        post_peak_min = float(np.min(post_peak_prices))
        post_peak_min_idx = int(np.argmin(post_peak_prices)) + peak_idx
        if post_peak_min_idx < len(df) - 5:
            bounce = (close[-1] / post_peak_min - 1) * 100
        else:
            bounce = 0
    else:
        bounce = 0

    is_one_wave = pullback_from_peak > 30 and bounce < 10

    print("%s(%s):" % (name, code))
    print("  高峰前涨幅: %.1f%%  高峰后回撤: %.1f%%  距高: %.1f%%  反弹: %.1f%%  一波游: %s" % (
        pre_peak_gain, pullback_from_peak, dist_from_peak, bounce, "是" if is_one_wave else "否"))

    # 看看价格的走势形态：连续创新低还是震荡
    if peak_idx < len(df) - 15:
        last_30 = close[peak_idx:]
        min_trend = "震荡"
        if len(last_30) > 10:
            half = len(last_30) // 2
            if np.mean(last_30[:half]) > np.mean(last_30[half:]):
                min_trend = "持续下跌"
            elif np.max(last_30) > np.mean(last_30) * 1.15:
                min_trend = "宽幅震荡"
        print("  高峰后走势: %s" % min_trend)

analyze("688187.SH", "时代电气")
analyze("600578.SH", "京能电力")
analyze("000920.SZ", "沃顿科技")
analyze("300894.SZ", "火星人")
analyze("600863.SH", "华能蒙电")
