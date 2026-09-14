# -*- coding: utf-8 -*-
import subprocess, json, urllib.request, datetime, pytz

def get_token():
    r = subprocess.run(
        ["powershell", "-Command", "& 'C:\\Users\\kongx\\.qclaw\\skills\\hithink-mcp\\get-token.ps1'"],
        capture_output=True, text=True, timeout=30
    )
    return r.stdout.strip()

def call(service, tool, args):
    token = get_token()
    headers = {
        "X-Authorization": token, "X-Consumer-Id": "qclaw",
        "X-Client-Secret": "1", "Content-Type": "application/json"
    }
    base = "https://fuyao.aicubes.cn/mcp/a-share"
    if service == "index":
        base = "https://fuyao.aicubes.cn/mcp/a-share-index"
    body = {"jsonrpc":"2.0","method":"tools/call","params":{"name":tool,"arguments":args},"id":1}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=25) as resp:
        result = json.loads(resp.read())
        raw = result["result"]["content"][0]["text"]
        return json.loads(raw)

# 1. 指数
idx_data = call("index", "get_a_share_index_prices_snapshot", {
    "thscodes": "000001.SH,399001.SZ,399006.SZ,000688.SH,000016.SH"
})
d = idx_data.get("data", {})
items = d.get("item", [])
name_map = {
    "000001.SH": "上证指数", "399001.SZ": "深成指", "399006.SZ": "创业板指",
    "000688.SH": "科创50", "000016.SH": "上证50"
}
print("=== 指数 ===")
idx_vals = {}
for it in items:
    code = it["thscode"]
    pct = it["price_change_ratio_pct"]  # already in % (0.1587 means 0.1587%)
    chg = it["price_change"]
    amt = it["turnover"] / 1e8
    name = name_map.get(code, code)
    idx_vals[code] = {"name": name, "price": it["last_price"], "pct": pct, "chg": chg, "amt": amt}
    print(f"  {name} {it['last_price']:.2f} {pct:+.4f}%({chg:+.2f}) 额{amt:.0f}亿")

# 2. 涨停池 - recent trading day (9/11)
print("\n=== 涨停池 9/11 ===")
limitup = call("a-share", "get_a_share_special_data_limit_up_pool", {
    "date": "2026-09-11", "size": 10
})
ld = limitup.get("data", {})
pg = ld.get("pagination", {})
pool = ld.get("item", [])
total = pg.get("total", "?")
print(f"涨停共 {total} 家，前{min(10,len(pool))}家:")
for p in pool[:10]:
    reason = str(p.get('limit_up_reason', ''))[:50]
    print(f"  {p.get('thscode')} {p.get('name')} {p.get('continue_day_text')} 封单{p.get('seal_money',0)/1e8:.1f}亿 原因:{reason}")

# 3. 连板梯队 - try with date param
print("\n=== 连板梯队 9/11 ===")
ladder = call("a-share", "get_a_share_special_data_limit_up_ladder", {"date": "2026-09-11"})
lad = ladder.get("data", {})
litems = lad.get("list", lad.get("item", []))
print(f"连板 {len(litems)} 只:")
for l in litems[:10]:
    print(f"  {l.get('thscode')} {l.get('chsname')} {l.get('continue_day_text','')} {l.get('continue_day_cnt','')}连板")

# 4. 热股榜
print("\n=== 热股榜 ===")
hot = call("a-share", "get_a_share_special_data_hot_stock_list", {"period": "day", "date": "2026-09-11"})
ht = hot.get("data", {})
hots = ht.get("list", ht.get("item", []))
print(f"热股 {len(hots)} 只:")
for h in hots[:10]:
    print(f"  {h.get('thscode')} {h.get('name')} 关注:{h.get('focus_degree','')}")

# 5. 龙虎榜
print("\n=== 龙虎榜 9/11 ===")
dragon = call("a-share", "get_a_share_special_data_dragon_tiger_list", {"date": "2026-09-11", "board_type": "all"})
dl = dragon.get("data", {}).get("list", [])
print(f"龙虎 {len(dl)} 条:")
for d in dl[:5]:
    print(f"  {d.get('thscode')} {d.get('chsname')} 净买{d.get('net_buy',0)}万 机构{d.get('institution_net_buy',0)}万")

print("\nDone")
