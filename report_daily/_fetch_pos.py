# -*- coding: utf-8 -*-
import urllib.request, json
TOKEN = open('D:/mystock/report_daily/_hithink_token.txt', encoding='ascii').read().strip()
H = {"X-Authorization": TOKEN, "X-Consumer-Id": "qclaw", "X-Client-Secret": "1",
     "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
FUND_URL = "https://fuyao.aicubes.cn/mcp/fund"

def call_fund(thscode):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_fund_market_snapshot",
                                  "arguments": {"thscode": thscode}}}).encode()
    req = urllib.request.Request(FUND_URL, data=body, headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    t = raw.strip()
    if "data:" in t:
        ls = [l for l in t.splitlines() if l.startswith("data:")]
        if ls: t = ls[-1][len("data:"):].strip()
    d = json.loads(t)
    return json.loads(d["result"]["content"][0]["text"])

pos = {
    "159516.SZ": "半导体设备ETF", "159611.SZ": "电力ETF", "512480.SH": "半导体ETF",
    "512760.SH": "芯片ETF", "159865.SZ": "养殖ETF", "515050.SH": "通信ETF",
}
print("=== 持仓ETF 07-24 收盘 ===")
results = {}
for code, name in pos.items():
    try:
        r = call_fund(code)
        it = r.get("data", {}).get("item", [{}])[0] if r.get("data") else {}
        last = it.get("last_price")
        chg = it.get("price_change_ratio_pct")
        print("%-8s %-8s last=%s chg%%=%s" % (code, name, last, chg))
        results[code] = {"name": name, "last": last, "chg": chg}
    except Exception as e:
        print("%-8s %-8s ERR %s" % (code, name, str(e)[:60]))
json.dump(results, open("D:/mystock/report_daily/q_pos_0724.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("saved q_pos_0724.json")
