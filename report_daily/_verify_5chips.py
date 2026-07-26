# -*- coding: utf-8 -*-
"""验证半导体设备5只股票的实时行情 + 技术指标（二波诊断）。"""
import urllib.request, json
TOKEN = open('D:/mystock/report_daily/_hithink_token.txt', encoding='ascii').read().strip()
H = {"X-Authorization": TOKEN, "X-Consumer-Id": "qclaw", "X-Client-Secret": "1",
     "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ASH = "https://fuyao.aicubes.cn/mcp/a-share"

def call(tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(ASH, data=body, headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read().decode("utf-8", "replace")
    t = raw.strip()
    if "data:" in t:
        ls = [l for l in t.splitlines() if l.startswith("data:")]
        if ls: t = ls[-1][len("data:"):].strip()
    return json.loads(json.loads(t)["result"]["content"][0]["text"])

stocks = {
    "002371.SZ": "北方华创",  # 主板
    "688072.SH": "拓荆科技",  # 科创
    "688012.SH": "中微公司",  # 科创
    "688361.SH": "中科飞测",  # 科创
    "688037.SH": "芯源微",    # 科创
}

# 1) 实时快照
snap = call("get_a_share_prices_snapshot",
             {"thscodes": ",".join(stocks.keys())})
items = snap["data"]["item"] if snap.get("data") else []
price_map = {it["thscode"]: it for it in items}
print("=== 实时行情 (07-25 收盘后) ===")
for code, name in stocks.items():
    it = price_map.get(code, {})
    last = it.get("last_price", "—")
    chg = it.get("price_change_ratio_pct", 0)
    print("  %-8s last=%s chg%%=%+.2f%%" % (name, last, chg))

# 2) 历史K线计算均线 + 二波参数 (最近60个交易日)
print("\n=== 技术指标分析 ===")
# 计算60日均线和支撑位
now_ms = 1753488000000  # 2026-07-24
start_ms = 1741536000000  # 2026-01-01
for code, name in stocks.items():
    is_gem = code.startswith("688") or code.startswith("300")
    # 获取日线历史
    hist = call("get_a_share_prices_historical",
                {"thscode": code, "interval": "1d",
                 "start": str(now_ms - 90 * 86400 * 1000),
                 "end": str(now_ms),
                 "adjust": "forward"})
    data = hist.get("data", {}).get("item", []) if hist.get("data") else []
    if not data:
        print("  %-8s K线数据为空" % name)
        continue
    # 倒序：data[0]最老，data[-1]最新
    closes = [float(x["close"]) for x in data if x.get("close")]
    highs  = [float(x["high"])  for x in data if x.get("high")]
    lows   = [float(x["low"])   for x in data if x.get("low")]
    vols   = [float(x["volume"]) for x in data if x.get("volume")]
    if len(closes) < 10:
        print("  %-8s 数据不足 %d 天" % (name, len(closes)))
        continue
    cur = closes[-1]
    ma5  = sum(closes[-5:])  / min(5, len(closes))
    ma10 = sum(closes[-10:]) / min(10, len(closes))
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    ma60 = sum(closes[-60:]) / min(60, len(closes))
    ma120, ma250 = ma60, ma60
    if len(closes) >= 120: ma120 = sum(closes[-120:]) / 120
    if len(closes) >= 250: ma250 = sum(closes[-250:]) / 250

    # 最近60日最高点
    peak60_idx = highs.index(max(highs)) if highs else -1
    peak60_val = highs[peak60_idx] if highs else cur
    pullback_pct = (peak60_val - cur) / peak60_val * 100 if peak60_val else 0
    gain60_pct = (cur - closes[0]) / closes[0] * 100 if closes[0] else 0

    # RSI(6)
    def calc_rsi(series, n=6):
        if len(series) < n+1: return 50
        gains = [max(series[i]-series[i-1], 0) for i in range(1, len(series))]
        losses = [max(series[i-1]-series[i], 0) for i in range(1, len(series))]
        avg_gain = sum(gains[-n:]) / n
        avg_loss = sum(losses[-n:]) / n
        if avg_loss == 0: return 100
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    rsi6 = calc_rsi(closes, 6)
    rsi14 = calc_rsi(closes, 14)

    # 量比
    vol5_avg = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else sum(vols) / len(vols) if vols else 1
    vol_ratio = vols[-1] / vol5_avg if vol5_avg > 0 else 1

    # 均线支撑判断
    above_ma60  = cur > ma60
    above_ma120 = cur > ma120
    above_ma250 = cur > ma250
    near_ma10   = abs(cur / ma10 - 1) < 0.02
    near_ma20   = abs(cur / ma20 - 1) < 0.02
    three_above = above_ma60 and above_ma120 and above_ma250

    # 二波形态评分
    score = 0; notes = []
    if pullback_pct < 10:  score += 20; notes.append("回撤<10%强势")
    elif pullback_pct < 20: score += 12; notes.append("回撤10-20%健康")
    elif pullback_pct < 30: score += 5; notes.append("回撤20-30%偏深")
    else: notes.append("回撤>30%偏弱")

    if rsi6 < 35: score += 15; notes.append("RSI6超卖+")
    elif rsi6 < 50: score += 8; notes.append("RSI6偏低+")
    elif rsi6 > 70: score -= 5; notes.append("RSI6偏高-")

    if vol_ratio > 1.2: score += 8; notes.append("量比>1.2放量+")
    elif vol_ratio < 0.8: score -= 3; notes.append("量比<0.8缩量-")

    if three_above: score += 15; notes.append("三均线支撑+")
    elif above_ma60: score += 8; notes.append("MA60上方+")

    if is_gem:
        score += 5; notes.append("双创加分+")
    else:
        score += 3; notes.append("主板加分+")

    status = "回踩中" if near_ma10 and not near_ma20 else "靠近MA10" if near_ma20 else "偏弱"

    print("\n  【%s】%s" % (name, code))
    print("    现价=%.2f  60日涨幅=%+.1f%%  回撤=%.1f%%" % (cur, gain60_pct, pullback_pct))
    print("    MA10=%.2f  MA20=%.2f  MA60=%.2f  MA120=%.2f  MA250=%.2f" % (ma10,ma20,ma60,ma120,ma250))
    print("    均线状态: MA10%s MA20%s MA60%s 三均线%s"
          % ("↑" if cur>ma10 else "↓", "↑" if cur>ma20 else "↓",
             "↑" if above_ma60 else "↓", "✓" if three_above else "✗"))
    print("    RSI6=%.1f  RSI14=%.1f  量比=%.2f  %s" % (rsi6, rsi14, vol_ratio, status))
    print("    二波评分=%d  %s" % (score, " | ".join(notes)))
