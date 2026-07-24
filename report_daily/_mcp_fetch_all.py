# -*- coding: utf-8 -*-
"""直接 HTTP 调用同花顺 MCP，拉取 2026-07-24 收盘全量数据。"""
import urllib.request, json, os

BASE = {
    "ashare": "https://fuyao.aicubes.cn/mcp/a-share",
    "index":  "https://fuyao.aicubes.cn/mcp/a-share-index",
}
TOKEN = open("D:/mystock/report_daily/_hithink_token.txt", encoding="ascii").read().strip()
HEADERS = {
    "X-Authorization": TOKEN, "X-Consumer-Id": "qclaw", "X-Client-Secret": "1",
    "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
}
OUT = "D:/mystock/report_daily"

def call(service, tool, arguments, rid=1):
    url = BASE[service]
    body = json.dumps({"jsonrpc":"2.0","id":rid,"method":"tools/call",
                        "params":{"name":tool,"arguments":arguments}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8","replace")
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    text = raw.strip()
    if "data:" in text:
        lines = [l for l in text.splitlines() if l.startswith("data:")]
        if lines: text = lines[-1][len("data:"):].strip()
    try:
        data = json.loads(text)
    except Exception as e:
        return {"_error": f"parse: {e}"}
    if "result" in data and "content" in data["result"]:
        inner = data["result"]["content"][0]["text"]
        return json.loads(inner)
    return data

def save(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"  [OK] {name}  keys={list(obj.keys())[:5]}")

print("=== 同花顺 MCP 取数 2026-07-24 收盘 ===")
idx = call("ashare", "get_a_share_prices_snapshot",
           {"thscodes":"000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"})
save("q_indices_0724.json", idx)

all_lu = []
for p in [1,2,3]:
    lu = call("ashare", "get_a_share_special_data_limit_up_pool", {"page":p,"size":50})
    if lu and "data" in lu and lu["data"] and "item" in lu["data"]:
        all_lu.extend(lu["data"]["item"]); print(f"  limitup p{p}: {len(lu['data']['item'])}")
    else:
        err = (lu or {}).get('_error','empty')
        print(f"  limitup p{p}: 0 ({err[:60]})")
save("q_limitup_0724.json", {"total":len(all_lu),"items":all_lu})

ladder = call("ashare", "get_a_share_special_data_limit_up_ladder", {})
save("q_ladder_0724.json", ladder)

hot = call("ashare", "get_a_share_special_data_hot_stock_list", {"period":"day"})
save("q_hot_0724.json", hot)

drag = call("ashare", "get_a_share_special_data_dragon_tiger_list", {})
save("q_dragon_0724.json", drag)

pos = call("ashare", "get_a_share_prices_snapshot",
           {"thscodes":"159516.SZ,159611.SZ,512480.SH,512760.SH,159865.SZ,515050.SH"})
save("q_pos_0724.json", pos)
print("=== 完成 ===")
