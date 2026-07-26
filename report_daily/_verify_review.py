# -*- coding: utf-8 -*-
"""验证 review_20260724.md 关键结论 vs 同花顺 MCP 实盘数据。"""
import urllib.request, json
TOKEN = open('D:/mystock/report_daily/_hithink_token.txt', encoding='ascii').read().strip()
H = {"X-Authorization": TOKEN, "X-Consumer-Id": "qclaw", "X-Client-Secret": "1",
     "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ASH = "https://fuyao.aicubes.cn/mcp/a-share"
FUND = "https://fuyao.aicubes.cn/mcp/fund"

def call(url, tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(url, data=body, headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read().decode("utf-8", "replace")
    t = raw.strip()
    if "data:" in t:
        ls = [l for l in t.splitlines() if l.startswith("data:")]
        if ls: t = ls[-1][len("data:"):].strip()
    return json.loads(json.loads(t)["result"]["content"][0]["text"])

# 1) 指数 (含上证)
idx = call(ASH, "get_a_share_prices_snapshot",
           {"thscodes": "000001.SH,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"})["data"]["item"]
print("=== 指数 07-24 收盘 ===")
for it in idx:
    print("  %s last=%.2f chg%%=%+.2f%% turnover(亿)=%.0f" % (
        it["thscode"], it["last_price"], it["price_change_ratio_pct"], it["turnover"]/1e8))

# 2) 电力ETF 实际表现 (验证"趋势强势≥0.75可逢低做多")
pw = call(FUND, "get_fund_market_snapshot", {"thscode": "159611.SZ"})["data"]["item"][0]
print("\n=== 电力ETF 159611 07-24 ===")
print("  last=%.3f chg%%=%+.2f%%" % (pw["last_price"], pw["price_change_ratio_pct"]))

# 3) 涨停题材 (验证机构主线)
lu = call(ASH, "get_a_share_special_data_limit_up_pool", {"page": 1, "size": 50})
items = lu["data"]["item"] if (lu.get("data") and lu["data"].get("item")) else []
print("\n=== 涨停 %d 只 (07-24) ===" % len(items))
from collections import Counter
th = Counter()
for s in items:
    print("  %-8s %s %s" % (s["name"], s["continue_day_text"], s["limit_up_reason"]))
    for x in s["limit_up_reason"].split("+"):
        th[x.strip()] += 1
print("  题材TOP:", [t for t, _ in th.most_common(10)])

# 4) 热股 (验证主线人气)
hot = call(ASH, "get_a_share_special_data_hot_stock_list", {"period": "day"})["data"]["item"][:15]
print("\n=== 热股TOP15 ===")
for s in hot:
    print("  #%-2d %s" % (s["rank"], s["name"]))
