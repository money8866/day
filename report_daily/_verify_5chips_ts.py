# -*- coding: utf-8 -*-
"""用Tushare查历史K线，计算5只半导体设备股的技术指标（二波诊断）。"""
import tushare as ts
import json, os
OUT = "D:/mystock/report_daily"

pro = ts.pro_api("1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34")

stocks = [
    ("002371", "SZ", "北方华创"),
    ("688072", "SH", "拓荆科技"),
    ("688012", "SH", "中微公司"),
    ("688361", "SH", "中科飞测"),
    ("688037", "SH", "芯源微"),
]

def calc_rsi(closes, n=6):
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    if len(gains) < n: return 50
    ag = sum(gains[-n:]) / n; al = sum(losses[-n:]) / n
    if al == 0: return 100
    return 100 - 100 / (1 + ag/al)

print("=== 半导体设备5只股票技术指标诊断 ===")
results = {}
for code, mkt, name in stocks:
    ts_code = f"{code}.{mkt}"
    try:
        df = pro.daily(ts_code=ts_code, start_date="20260101", end_date="20260724",
                       fields="trade_date,open,high,low,close,vol,amount,pct_chg")
        if df is None or df.empty:
            print(f"  {name} 无数据"); continue
        df = df.sort_values("trade_date").reset_index(drop=True)
        closes = df["close"].tolist()
        vols   = df["vol"].tolist()
        if len(closes) < 20:
            print(f"  {name} 数据不足 {len(closes)} 天"); continue
        cur   = closes[-1]
        ma5   = sum(closes[-5:])   / 5
        ma10  = sum(closes[-10:])  / 10
        ma20  = sum(closes[-20:])  / 20
        ma60  = sum(closes[-60:])  / min(60, len(closes))
        ma120 = sum(closes[-120:]) / min(120, len(closes)) if len(closes) >= 60 else ma60
        ma250 = ma120  # 数据不足250天用ma120近似

        # 60日最高
        peak60_val = max(closes[-60:]) if len(closes) >= 60 else max(closes)
        pullback   = (peak60_val - cur) / peak60_val * 100
        gain60     = (cur - closes[0]) / closes[0] * 100 if closes[0] else 0
        gain20     = (cur - closes[-20]) / closes[-20] * 100

        rsi6  = calc_rsi(closes, 6)
        rsi14 = calc_rsi(closes, 14)
        vol5  = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else sum(vols) / max(1, len(vols))
        vol_ratio = vols[-1] / vol5 if vol5 > 0 else 1

        # 均线支撑
        above_ma60  = cur > ma60
        above_ma120 = cur > ma120
        near_ma10   = abs(cur - ma10) / ma10 < 0.02
        near_ma20   = abs(cur - ma20) / ma20 < 0.02
        three_above = above_ma60 and above_ma120
        # 不创新低检查（近20日最低 vs 前低）
        lows20 = [min(closes[max(0,i-20):i+1]) for i in range(20, len(closes))]
        recent_low  = min(closes[-20:])
        prev_lows   = closes[:-20]
        prev_min    = min(prev_lows) if prev_lows else recent_low
        not_new_low = recent_low > prev_min * 0.98  # 允许2%误差

        # 二波评分
        score = 0; notes = []
        # 回撤评分
        if pullback < 10:   score += 20; notes.append("回撤<10%强势+20")
        elif pullback < 15: score += 15; notes.append("回撤10-15%健康+15")
        elif pullback < 20: score += 10; notes.append("回撤15-20%合理+10")
        elif pullback < 25: score += 5;  notes.append("回撤20-25%偏深+5")
        else: score -= 5; notes.append("回撤>25%偏弱-5")
        # RSI
        if rsi6 < 35:    score += 15; notes.append("RSI6超卖+15")
        elif rsi6 < 45:  score += 8;  notes.append("RSI6偏低+8")
        elif rsi6 < 55:  score += 3;  notes.append("RSI6中性+3")
        elif rsi6 > 70:  score -= 5;  notes.append("RSI6偏高-5")
        # 量比
        if vol_ratio > 1.5:   score += 8; notes.append(f"量比{v:.1f}放量+8")
        elif vol_ratio > 1.0: score += 4; notes.append(f"量比{v:.1f}健康+4")
        elif vol_ratio < 0.7: score -= 5; notes.append(f"量比{v:.1f}缩量-5")
        # 均线支撑
        if three_above:  score += 15; notes.append("MA60+MA120上方+15")
        elif above_ma60: score += 8;  notes.append("MA60上方+8")
        # 不创新低
        if not_new_low:  score += 10; notes.append("不创新低+10")
        else:             score -= 5;  notes.append("创新低-5")
        # 20日涨幅
        if gain20 > 5:   score += 5;  notes.append("20日涨幅+{:.1f}%+5".format(gain20))
        elif gain20 < -10: score -= 5; notes.append("20日涨幅{:.1f}%-5".format(gain20))
        # 科创加分
        is_gem = code.startswith(("688", "300"))
        if is_gem: score += 5; notes.append("科创加分+5")

        results[code] = {
            "name": name, "ts_code": ts_code,
            "close": round(cur, 2), "gain60": round(gain60, 1),
            "pullback": round(pullback, 1),
            "ma5": round(ma5,2), "ma10": round(ma10,2), "ma20": round(ma20,2),
            "ma60": round(ma60,2), "ma120": round(ma120,2),
            "rsi6": round(rsi6,1), "rsi14": round(rsi14,1),
            "vol_ratio": round(vol_ratio, 2),
            "above_ma60": above_ma60, "above_ma120": above_ma120,
            "three_above": three_above, "not_new_low": not_new_low,
            "score": score, "notes": notes,
            "last_date": df["trade_date"].iloc[-1],
        }
        print(f"\n  【{name}】{ts_code}  (数据截止{df['trade_date'].iloc[-1]})")
        print(f"    现价={cur:.2f}  60日涨幅={gain60:+.1f}%  回撤={pullback:.1f}%")
        print(f"    MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}  MA120={ma120:.2f}")
        print(f"    RSI6={rsi6:.1f}  RSI14={rsi14:.1f}  量比={vol_ratio:.2f}")
        print(f"    MA60上方:{'✓' if above_ma60 else '✗'}  MA120上方:{'✓' if above_ma120 else '✗'}  不创新低:{'✓' if not_new_low else '✗'}")
        print(f"    二波评分={score}  {' | '.join(notes)}")
    except Exception as e:
        print(f"  {name} ERR: {e}")
    import time; time.sleep(0.12)

# 保存
with open(OUT + "/_5chips_analysis.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\n=== 汇总 ===")
print("评分排序：")
for code, d in sorted(results.items(), key=lambda x: -x[1]["score"]):
    print("  %-8s 评分=%3d  回撤=%5.1f%%  RSI6=%5.1f  量比=%.2f  MA60%s  不创新低=%s" % (
        d["name"], d["score"], d["pullback"], d["rsi6"], d["vol_ratio"],
        "✓" if d["above_ma60"] else "✗",
        "✓" if d["not_new_low"] else "✗"))
