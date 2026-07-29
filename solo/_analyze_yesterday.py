import sys, os, warnings, json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
warnings.filterwarnings('ignore')

sys.path.insert(0, 'd:/mystock/solo')
sys.path.insert(0, 'd:/mystock/solo/multi_factor_picker')

from dotenv import load_dotenv
load_dotenv('d:/mystock/config/.env')
import tushare as ts
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

# 读取昨天的bull报告数据
bull = pd.read_csv('d:/mystock/solo/report_daily/enhanced_timing_bull_all_20260728.csv', encoding='utf-8-sig')

stocks = ['301165.SZ', '002440.SZ']
names = {'301165.SZ': '锐捷网络', '002440.SZ': '闰土股份'}

for code in stocks:
    df = pro.daily(ts_code=code, start_date='20260601', end_date='20260728')
    if df is None or len(df) == 0:
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 取最近30根K线
    recent = df.tail(30).copy()
    
    print(f"\n{'═'*60}")
    print(f"  {names[code]} ({code}) — 近30日K线")
    print(f"{'═'*60}")
    print(f"{'日期':10s} {'开盘':>7s} {'收盘':>7s} {'最高':>7s} {'最低':>7s} {'涨幅%':>6s} {'量比':>6s} {'成交额亿':>8s}")
    
    # 计算量比（相对于前20日均量）
    vol_ma20 = df['vol'].rolling(20).mean()
    df['vol_ratio'] = df['vol'] / vol_ma20
    
    for i, (_, r) in enumerate(recent.iterrows()):
        pct = r['pct_chg']
        vr = df.loc[r.name, 'vol_ratio'] if r.name in df.index else 1.0
        amount_yi = r['amount'] / 100000000
        marker = ''
        if abs(pct) >= 9.9:
            marker = ' ★涨停' if pct > 0 else ' ★跌停'
        elif abs(pct) >= 7:
            marker = ' ↑大阳' if pct > 0 else ' ↓大阴'
        elif abs(pct) >= 4:
            marker = ' ↑中阳' if pct > 0 else ' ↓中阴'
        print(f"{r['trade_date']:10s} {r['open']:7.2f} {r['close']:7.2f} {r['high']:7.2f} {r['low']:7.2f} {pct:6.2f} {vr:6.2f} {amount_yi:8.2f}{marker}")
    
    # 计算技术指标
    closes = recent['close'].values.astype(float)
    highs = recent['high'].values.astype(float)
    lows = recent['low'].values.astype(float)
    vols = recent['vol'].values.astype(float)
    
    ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else None
    ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else None
    ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else None
    
    # 最高点到现在的回撤
    peak = np.max(closes)
    peak_idx = np.argmax(closes)
    drawdown = (peak - closes[-1]) / peak * 100
    
    # 成交量萎缩程度
    vol_last5 = np.mean(vols[-5:])
    vol_prev = np.mean(vols[-20:-5]) if len(vols) >= 20 else vol_last5
    vol_shrink = (1 - vol_last5 / vol_prev) * 100 if vol_prev > 0 else 0
    
    # MACD
    ema12 = pd.Series(closes).ewm(span=12).mean().values
    ema26 = pd.Series(closes).ewm(span=26).mean().values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9).mean().values
    macd = 2 * (dif - dea)
    
    # 昨日收盘价在什么位置
    last_close = closes[-1]
    
    print(f"\n  ── 技术形态分析 ──")
    print(f"  最新收盘: {last_close:.2f}")
    print(f"  MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}" if ma5 else "")
    if ma5 and ma10 and ma20:
        print(f"  均线排列: {'多头' if ma5>ma10>ma20 else '空头' if ma5<ma10<ma20 else '交叉'}")
        print(f"  价格距MA20: {(last_close-ma20)/ma20*100:+.2f}%")
    print(f"  阶段最大回撤: {drawdown:.2f}% (从{closes[peak_idx]:.2f})")
    print(f"  近5日/前15日均量比: {vol_last5/vol_prev:.2f} (缩量{vol_shrink:.0f}%)" if vol_prev > 0 else "")
    print(f"  MACD: DIF={dif[-1]:.3f} DEA={dea[-1]:.3f} MACD={macd[-1]:.3f}")
    print(f"  MACD柱: {'红柱' if macd[-1]>0 else '绿柱'} (前日={'红' if macd[-2]>0 else '绿'})")
    print(f"  MACD形态: {'金叉' if dif[-1]>dea[-1] and dif[-2]<=dea[-2] else '死叉' if dif[-1]<dea[-1] and dif[-2]>=dea[-2] else '多头' if dif[-1]>dea[-1] else '空头'}")
    
    # 昨日bull报告中的信号
    row = bull[bull['代码'].str.strip() == code.replace('.SZ','').replace('.SH','')]
    if len(row) > 0:
        r = row.iloc[0]
        print(f"\n  ── 昨日bull报告信号 ──")
        for col in ['量化择时分','修正后评分','修正后胜率分级','兑现冲击过滤','冲击详情','VWAP','现价','MA20','筹码峰顶','筹码集中度%','交易决策']:
            if col in r and not pd.isna(r[col]):
                print(f"  {col}: {r[col]}")
    
    # 今日行情简述
    print(f"\n  ── 今日(20260729)表现 ──")
    try:
        today = pro.daily(ts_code=code, start_date='20260729', end_date='20260729')
        if today is not None and len(today) > 0:
            t = today.iloc[0]
            print(f"  开盘={t['open']:.2f} 收盘={t['close']:.2f} 最高={t['high']:.2f} 最低={t['low']:.2f} 涨幅={t['pct_chg']:.2f}%")
            if t['pct_chg'] >= 9.9:
                print(f"  >>> 涨停板!")
            elif t['pct_chg'] <= -9.9:
                print(f"  >>> 跌停板!")
        else:
            print(f"  无数据或未交易")
    except:
        print(f"  获取失败")
    
    print()
