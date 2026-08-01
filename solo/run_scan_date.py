"""指定日期运行量能爆发选股 - 强制扫描（绕过大盘过滤器用于回测分析）"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
import pandas as pd
import numpy as np

TARGET_DATE = "20260730"

# 加载选股模块
spec2 = importlib.util.spec_from_file_location("vms_scan", r"d:\mystock\solo\vol_ma_sync_surge_scan.py")
vms = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(vms)

# 覆盖TRADE_DATE
vms.tq.TRADE_DATE = TARGET_DATE
vms.tq.load_turnover_cache()

print(f"目标交易日: {TARGET_DATE}")
print("模式: 强制扫描（绕过大盘过滤器，用于回测分析）\n")

# 先检测大盘环境
regime = vms.get_market_regime()
print(f"【大盘环境】{regime['regime']}")
print(f"  上证: {regime.get('sh_close','-')} | MA20: {regime.get('sh_ma20','-')} | 近5日: {regime['sh_chg_5d']:+.2f}%")
print(f"  判定: {regime['reason']}")
print(f"  风控: {'停止选股' if not regime['allow_trade'] else '允许选股'}\n")

# 手动执行选股逻辑（绕过过滤器）
all_stocks = list(vms.tq.TURNOVER_CACHE.keys()) if hasattr(vms.tq, 'TURNOVER_CACHE') else []
print(f"待扫描股票数: {len(all_stocks)}")
print()

import time
t0 = time.time()
signals = []
total = len(all_stocks)

for i, code in enumerate(all_stocks):
    if (i+1) % 1000 == 0:
        print(f"  扫描进度: {i+1}/{total}...")
    try:
        # 跳过北交所
        if code.startswith('8') or code.startswith('4') or code.startswith('9'):
            continue
        df = vms.tq.get_hist_data(code)
        if df is None or len(df) < 60:
            continue
        result = vms.detect_vol_ma_sync_surge(df)
        if result and result['score'] >= 70:
            result['code'] = code
            try:
                result['name'] = vms.tq.get_stock_name(code)
            except Exception:
                result['name'] = ''
            signals.append(result)
    except Exception:
        continue

elapsed = time.time() - t0
print(f"扫描完成，耗时{elapsed:.1f}秒，共{len(signals)}只信号\n")

signals.sort(key=lambda x: -x['score'])

print("=" * 130)
print(f"20260730 量能爆发形态信号（共{len(signals)}只，评分>=70）")
print("=" * 130)
print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'评分':<6}{'量能放大':<8}{'MA20斜率':<12}{'MACD':<10}{'距MA20':<10}{'量价配合':<8}{'当日涨幅':<10}")
print("-" * 130)

for i, s in enumerate(signals[:20], 1):
    print(f"{i:<4}{s['code']:<12}{str(s.get('name',''))[:8]:<10}{s['score']:<6}{s['vol_surge_ratio']:<8.2f}"
          f"{s['ma20_slope_5d']:<+6.2f}/{s['ma20_slope_pre']:<+5.2f}  "
          f"{s['macd_status']:<10}{s['dist_ma20']:<+7.2f}%  "
          f"{s['vol_price_coord']:<8.2f}{s['last_chg']:<+7.2f}%")

# 按评分区间统计
print("\n" + "=" * 60)
print("评分分布:")
for lo, hi in [(70,75),(75,80),(80,85),(85,90),(90,100)]:
    cnt = sum(1 for s in signals if lo <= s['score'] < hi)
    print(f"  [{lo}-{hi}): {cnt}只")

# 高胜率标的
high_conf = [s for s in signals if s['score'] >= 80]
print(f"\n新评分>=80高胜率标的（回测止盈胜率87.5%+）: {len(high_conf)}只")
for s in high_conf[:10]:
    print(f"  {s['code']} {str(s.get('name',''))[:8]:<8} 评分:{s['score']}  距MA20:{s['dist_ma20']:+.2f}%  量价:{s['vol_price_coord']:.2f}  MA20斜率:{s['ma20_slope_5d']:+.2f}%")
