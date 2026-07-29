# -*- coding: utf-8 -*-
"""用同花顺 MCP 拉取 2026-07-28 收盘数据（容错版）。"""
import urllib.request, json
TOKEN = open('D:/mystock/report_daily/_hithink_token.txt', encoding='ascii').read().strip()
H = {"X-Authorization": TOKEN, "X-Consumer-Id": "qclaw", "X-Client-Secret": "1",
     "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ASH = "https://fuyao.aicubes.cn/mcp/a-share"

def call(tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(ASH, data=body, headers=H, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode("utf-8", "replace")
        t = raw.strip()
        if "data:" in t:
            ls = [l for l in t.splitlines() if l.startswith("data:")]
            if ls: t = ls[-1][len("data:"):].strip()
        return json.loads(json.loads(t)["result"]["content"][0]["text"])
    except Exception as e:
        return {"error": str(e)}

print("=== 同花顺 MCP 实盘数据 (2026-07-28) ===\n")

# 1) 指数
idx = call("get_a_share_prices_snapshot",
           {"thscodes": "000001.SH,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH,399673.SZ"})
names = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指",
         "000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
         "399673.SZ": "科创50"}
print("【指数收盘】")
if idx.get("data") and idx["data"].get("item"):
    for it in idx["data"]["item"]:
        c = it["thscode"]; chg = it["price_change_ratio_pct"]
        color = "🔴" if chg < -3 else "🟡" if chg < 0 else "🟢"
        print("  %s %-8s last=%.2f  chg=%+.2f%%  turnover=%.0f亿" % (
            color, names.get(c,c), it["last_price"], chg, it["turnover"]/1e8))
else:
    print("  ERROR:", idx.get("error"))

# 2) 涨停池
lu = call("get_a_share_special_data_limit_up_pool", {"page":1,"size":100})
up_cnt = len(lu["data"]["item"]) if lu.get("data") else 0
print("\n【涨停池】 %d只" % up_cnt)

# 3) ETF（用a-share接口查）
etf_codes = ["159516.SZ", "512480.SH", "512760.SH", "515050.SH", "159611.SZ"]
etf_names = {"159516.SZ":"半导体设备ETF","512480.SH":"半导体ETF","512760.SH":"芯片ETF",
             "515050.SH":"通信ETF","159611.SZ":"电力ETF"}
etf_snap = call("get_a_share_prices_snapshot", {"thscodes": ",".join(etf_codes)})
print("\n【ETF 收盘】")
if etf_snap.get("data") and etf_snap["data"].get("item"):
    for it in etf_snap["data"]["item"]:
        chg = it["price_change_ratio_pct"]
        color = "🔴" if chg < -3 else "🟡" if chg < 0 else "🟢"
        name = etf_names.get(it["thscode"], it["thscode"])
        print("  %s %-12s last=%.3f  chg=%+.2f%%" % (color, name, it["last_price"], chg))
else:
    print("  ETF数据为空")

# 4) 热股 TOP20
hot = call("get_a_share_special_data_hot_stock_list", {"period":"day"})
print("\n【热股 TOP20】")
if hot.get("data") and hot["data"].get("item"):
    for s in hot["data"]["item"][:20]:
        print("  #%-2d %-8s  热度=%s" % (s["rank"], s["name"], s["heat"]))
else:
    print("  热股数据为空")

# 5) 涨停题材分布
if up_cnt > 0:
    from collections import Counter
    themes = Counter()
    for s in lu["data"]["item"]:
        for t in s["limit_up_reason"].split("+"):
            t = t.strip()
            if t and t not in ("央企","国企","中报预增","增持"):
                themes[t] += 1
    print("\n【涨停题材 TOP12】")
    for t, c in themes.most_common(12):
        print("  %-12s × %d" % (t, c))
