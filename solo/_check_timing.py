# -*- coding: utf-8 -*-
"""寻宝策略选股 - 入场时机技术面分析"""
import os, time, threading
import pandas as pd
import tushare as ts
from dotenv import load_dotenv

load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

_lock = threading.Lock()
_last_ts = time.time()
def rate():
    global _last_ts
    with _lock:
        el = time.time() - _last_ts
        if el < 0.13: time.sleep(0.13 - el)
        _last_ts = time.time()

codes = ['688486.SH','688049.SH','300831.SZ','688325.SH','688721.SH','688591.SH','688419.SH']
names = ['龙迅股份','炬芯科技','ST派瑞','赛微微电','龙图光罩','泰凌微','耐科装备']

print(f"{'═'*70}")
print(f"  寻宝策略 — 入场时机技术面诊断")
print(f"  诊断日期: 2026-07-23")
print(f"{'═'*70}")

all_data = []
for code, name in zip(codes, names):
    rate()
    df = pro.daily(ts_code=code, start_date='20260601', end_date='20260723')
    if df is None or len(df) == 0:
        print(f"\n  {name} — 无数据")
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    latest = df.iloc[-1]
    close = float(latest['close'])
    pct = float(latest['pct_chg'])
    vol = float(latest['vol'])

    # 均线
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    ma5 = float(df['ma5'].dropna().iloc[-1]) if len(df['ma5'].dropna()) > 0 else 0
    ma10 = float(df['ma10'].dropna().iloc[-1]) if len(df['ma10'].dropna()) > 0 else 0
    ma20 = float(df['ma20'].dropna().iloc[-1]) if len(df['ma20'].dropna()) > 0 else 0
    ma60 = float(df['ma60'].dropna().iloc[-1]) if len(df['ma60'].dropna()) > 0 else 0

    # 距均线%
    pct_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0
    pct_ma10 = (close - ma10) / ma10 * 100 if ma10 > 0 else 0
    pct_ma60 = (close - ma60) / ma60 * 100 if ma60 > 0 else 0

    # 近20日高低
    recent_20 = df.tail(20)
    h20 = float(recent_20['high'].max())
    l20 = float(recent_20['low'].min())
    pct_from_h20 = (h20 - close) / h20 * 100
    pct_from_l20 = (close - l20) / l20 * 100

    # 量比
    vol_5 = float(df['vol'].tail(5).mean())
    vol_20 = float(df['vol'].tail(20).mean()) if len(df) >= 20 else vol_5
    vr = vol_5 / vol_20 if vol_20 > 0 else 1.0

    # 近60日涨跌幅
    if len(df) >= 60:
        close_60d = float(df.iloc[-60]['close'])
        ret_60d = (close - close_60d) / close_60d * 100
    else:
        ret_60d = 0

    # 信号判断
    signals = []

    # 大盘弱势，关注低吸
    signals.append('低吸')

    # 距MA20 5%~25% 是精准入场区间
    if 5 <= pct_ma20 <= 25:
        signals.append('精准入场区(MA20上方5~25%)')
    elif -5 <= pct_ma20 <= 5:
        signals.append('MA20附近密集区')
    elif pct_ma20 < -5:
        signals.append(f'MA20下方{pct_ma20:.1f}%，深度回调')

    # 量比
    if vr >= 1.3:
        signals.append(f'放量(vr={vr:.2f})')
    elif vr < 0.7:
        signals.append(f'缩量(vr={vr:.2f})')
    else:
        signals.append(f'量平(vr={vr:.2f})')

    # KDJ判断简版
    if len(df) >= 15:
        recent_9h = float(df['high'].tail(9).max())
        recent_9l = float(df['low'].tail(9).min())
        rsv = (close - recent_9l) / (recent_9h - recent_9l) * 100 if recent_9h > recent_9l else 50
        k = rsv * 2/3 + 50 * 1/3
        d = k * 2/3 + 50 * 1/3
        j = 3 * k - 2 * d
        if j < 20:
            signals.append('KDJ超卖(J<20)')
        elif j < 30:
            signals.append('KDJ低位(J<30)')
        elif j > 80:
            signals.append('KDJ超买(J>80)')
        else:
            signals.append(f'KDJ中性(J={j:.0f})')
    else:
        j = 50

    # 综合判断
    print(f"\n  {'─'*50}")
    print(f"  {name}({code})  收盘{close:.2f}  当日{pct:+.2f}%")
    print(f"  MA5:{ma5:.2f}  MA10:{ma10:.2f}  MA20:{ma20:.2f}  MA60:{ma60:.2f}")
    print(f"  距MA20:{pct_ma20:+.1f}%  距MA10:{pct_ma10:+.1f}%  距MA60:{pct_ma60:+.1f}%")
    print(f"  20日高:{h20:.2f}(距{pct_from_h20:.1f}%)  20日低:{l20:.2f}(弹{pct_from_l20:.1f}%)  60日涨幅:{ret_60d:.1f}%")
    print(f"  {' › '.join(signals)}")

    # 入场建议
    if 5 <= pct_ma20 <= 25 and vr >= 0.7:
        advice = '✅ 较佳入场区 — MA20上方温和区间，量能正常'
    elif pct_ma20 < -5 and vr < 0.7:
        advice = '⏳ 可关注低吸 — 深度回调+缩量，等待止跌信号'
    elif 25 < pct_ma20 <= 40:
        advice = '⚠️ 追高区 — 距MA20较远，等待回踩'
    elif pct_ma20 > 40:
        advice = '❌ 远离MA20，不宜追入'
    elif -5 <= pct_ma20 <= 5:
        advice = '👀 MA20附近 — 观察是否放量突破'
    else:
        advice = '⏳ 等待更明确信号'

    print(f"  → {advice}")
    print()

    all_data.append({
        'name': name, 'code': code, 'close': close,
        'ma20': ma20, 'pct_ma20': round(pct_ma20, 1),
        'pct_ma10': round(pct_ma10, 1),
        'pct_ma60': round(pct_ma60, 1),
        'pct_from_h20': round(pct_from_h20, 1),
        'pct_from_l20': round(pct_from_l20, 1),
        'vr': round(vr, 2),
        'ret_60d': round(ret_60d, 1),
        'j': round(j, 0),
        'signals': ' › '.join(signals),
        'advice': advice,
    })

print(f"\n{'═'*70}")
print(f"  综合分析")
print(f"{'═'*70}")
print(f"{'名称':>8} {'距MA20':>7} {'距MA10':>7} {'20日高距':>8} {'量比':>5} {'J值':>4} {'60日涨':>7} {'建议'}")
print(f"{'─'*70}")
for d in all_data:
    advice_short = d['advice'].split('—')[0].strip()
    print(f"{d['name']:>6} {d['pct_ma20']:>+6.1f}% {d['pct_ma10']:>+6.1f}% {d['pct_from_h20']:>7.1f}% {d['vr']:>5.2f} {d['j']:>4.0f} {d['ret_60d']:>+6.1f}% {advice_short}")
