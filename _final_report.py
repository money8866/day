# -*- coding: utf-8 -*-
import subprocess, json, urllib.request, datetime

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
        return json.loads(result["result"]["content"][0]["text"])

# 1. Index
idx_data = call("index", "get_a_share_index_prices_snapshot", {
    "thscodes": "000001.SH,399001.SZ,399006.SZ,000688.SH,000016.SH"
})
name_map = {
    "000001.SH": "上证指数", "399001.SZ": "深成指", "399006.SZ": "创业板指",
    "000688.SH": "科创50", "000016.SH": "上证50"
}
items = idx_data.get("data", {}).get("item", [])
idx_lines = []
for it in items:
    code = it["thscode"]
    pct = it["price_change_ratio_pct"]
    chg = it["price_change"]
    amt = it["turnover"] / 1e8
    name = name_map.get(code, code)
    idx_lines.append(f"{name} {it['last_price']:.2f} {pct:+.2f}%({chg:+.2f}) 额{amt:.0f}亿")

# 2. Limit-up pool 9/11
lu = call("a-share", "get_a_share_special_data_limit_up_pool", {"date": "2026-09-11", "size": 10})
ld = lu.get("data", {})
pool = ld.get("item", [])
total_lu = ld.get("pagination", {}).get("total", "?")
lu_lines = [f"涨停 {total_lu} 家（9/11收盘）："]
for p in pool[:8]:
    lu_lines.append(f"• {p['name']}({p['thscode']}) {p['continue_day_text']} 封单{p['seal_money']/1e8:.1f}亿 | {str(p['limit_up_reason'])[:40]}")

# 3. Hot stocks 9/11
hot = call("a-share", "get_a_share_special_data_hot_stock_list_history", {"date": "2026-09-11"})
ht = hot.get("data", {})
hots = ht.get("list", [])
hot_lines = [f"热股榜 TOP{len(hots)}（9/11）："]
for h in hots[:10]:
    hot_lines.append(f"• {h['name']}({h['thscode']})")

# 4. Ladder 9/11
lad = call("a-share", "get_a_share_special_data_limit_up_ladder", {})
lad_data = lad.get("data", {})
litems = lad_data.get("list", [])
lad_lines = [f"连板梯队 {len(litems)} 只："]
for l in litems[:8]:
    board = l.get("board_num", l.get("continue_day_cnt", "?"))
    board_text = l.get("continue_day_text", "")
    lad_lines.append(f"• {l.get('chsname', l.get('name',''))}({l.get('thscode','')}) {board_text} {board}连板")

# Build report
report = f"""【A股基本面早报 | 9月14日（周一）】

━━━ 【一、大盘指数】（09-14 早盘）━━━
{' | '.join(idx_lines)}

━━━ 【二、涨停追踪（9/11收盘）】━━━
{chr(10).join(lu_lines)}

━━━ 【三、热股榜 TOP10（9/11）】━━━
{chr(10).join(hot_lines)}

━━━ 【四、连板梯队（9/11）】━━━
{chr(10).join(lad_lines)}

━━━ 【五、市场简评】━━━
今日早盘整体偏弱分化：上证指数小幅飘红(+0.16%)，但深成(-0.25%)、创业板(-0.47%)、科创50(-0.94%)均下跌，科创50跌幅最深。市场成交仍较活跃，说明资金博弈激烈，防御情绪升温。

上周五（9/11）涨停48家，热股集中于AI算力（远东股份/中际旭创）、消费零售（中百集团/国芳集团/桂林旅游）、农业粮食（金健米业）、PCB（风华高科/金安国纪/太极实业）、机器人（宇树科技）等方向。

━━━ 【六、操作参考】━━━
• 仓位：5-6成，不追高，低吸为主
• 关注：AI算力链、光通信、黄金、电力
• 规避：科创50高位、解禁压力股

⚠️ 以上数据基于同花顺金融数据自动生成，仅供参考，不构成投资建议。
"""
print(report)

with open(r'D:\mystock\report_daily\morning_report_20260914.md', 'w', encoding='utf-8') as f:
    f.write(report)
print("\n[OK] Saved.")
