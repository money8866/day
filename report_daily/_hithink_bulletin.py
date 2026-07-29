# -*- coding: utf-8 -*-
"""同花顺 MCP 公告与研报速递 2026-07-29（修复版）。"""
import urllib.request, json, datetime, re
TOKEN = open('D:/mystock/report_daily/_hithink_token.txt', encoding='ascii').read().strip()
H = {"X-Authorization": TOKEN, "X-Consumer-Id": "qclaw", "X-Client-Secret": "1",
     "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ASH = "https://fuyao.aicubes.cn/mcp/a-share"
OUT = "D:/mystock/report_daily"

def call(url, tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(url, data=body, headers=H, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
        t = raw.strip()
        if "data:" in t:
            ls = [l for l in t.splitlines() if l.startswith("data:")]
            if ls: t = ls[-1][len("data:"):].strip()
        return json.loads(json.loads(t)["result"]["content"][0]["text"])
    except Exception as e:
        return {"error": str(e)}

DATE = "2026-07-29"
print("=== 同花顺 MCP 公告与研报速递 %s ===\n" % DATE)

# 1) 热股TOP30
hot = call(ASH, "get_a_share_special_data_hot_stock_list", {"period":"day"})
stocks = hot["data"]["item"] if (hot.get("data") and hot["data"].get("item")) else []
print("【热股TOP30】%d只" % len(stocks))

# 2) 涨停池
lu = call(ASH, "get_a_share_special_data_limit_up_pool", {"page":1,"size":50})
up_items = lu["data"]["item"] if lu.get("data") else []
print("【涨停池】%d只" % len(up_items))

# 3) 连板梯队
ladder = call(ASH, "get_a_share_special_data_limit_up_ladder", {})
ladder_items = ladder["data"]["item"] if (ladder.get("data") and ladder["data"].get("item")) else []

# 4) 龙虎榜
dt = call(ASH, "get_a_share_special_data_dragon_tiger_list", {"board_type":"all"})
dt_items = dt["data"]["stock_items"] if (dt.get("data") and dt["data"].get("stock_items")) else []
print("【龙虎榜】%d只" % len(dt_items))

# 5) 异动原因
if stocks:
    codes = [s["thscode"] for s in stocks[:10]]
    anom = call(ASH, "get_a_share_special_data_anomaly_analysis_stock", {"thscodes": ",".join(codes)})
    if isinstance(anom.get("data"), list):
        anom_items = anom["data"][:10]
        print("【异动原因】%d条" % len(anom_items))
    else:
        anom_items = []
        print("【异动原因】数据格式异常")
else:
    anom_items = []

# 6) 生成报告
report = []
report.append("# 公告与研报速递 · %s\n" % DATE)
report.append("> 数据来源：同花顺金融数据 MCP\n")

# 涨停题材
from collections import Counter
themes = Counter()
for s in up_items:
    for t in s["limit_up_reason"].split("+"):
        t = t.strip()
        if t and t not in ("央企","国企","增持","中报预增","半年报预增"):
            themes[t] += 1

report.append("\n## 一、涨停题材分布（共%d只）\n" % len(up_items))
for t, c in themes.most_common(12):
    report.append("- **%s** × %d" % (t, c))

# 涨停明细
report.append("\n## 二、涨停明细（TOP20）\n")
for s in up_items[:20]:
    report.append("- **%s** %s  %s" % (s["name"], s["continue_day_text"], s["limit_up_reason"]))

# 连板梯队
if ladder_items:
    report.append("\n## 三、连板梯队\n")
    for it in ladder_items[:10]:
        if it.get("continue_days",1) > 1:
            report.append("- **%d连板** %s（累计%d天）" % (
                it.get("continue_days",1), it.get("stock_name",""), it.get("continuous_days",0)))

# 龙虎榜
if dt_items:
    report.append("\n## 四、龙虎榜机构动向\n")
    buy = sorted([s for s in dt_items if s.get("net_value",0) > 0], key=lambda x: -x["net_value"])[:5]
    sell = sorted([s for s in dt_items if s.get("net_value",0) < 0], key=lambda x: x["net_value"])[:5]
    report.append("### 净买入TOP5\n")
    for s in buy:
        report.append("- %s 净买入%+.2f亿  %s" % (s["name"], s["net_value"]/1e8, s.get("limit_reason","")))
    report.append("\n### 净卖出TOP5\n")
    for s in sell:
        report.append("- %s 净卖出%.2f亿  %s" % (s["name"], abs(s["net_value"])/1e8, s.get("limit_reason","")))

# 热股榜
if stocks:
    report.append("\n## 五、热股榜TOP20\n")
    for s in stocks[:20]:
        report.append("%d. **%s**  热度%s" % (s["rank"], s["name"], s["heat"]))

# 异动原因
if anom_items:
    report.append("\n## 六、异动原因（热门股）\n")
    for it in anom_items:
        name = it.get("stock_name","")
        reason = it.get("anomaly_reason","")[:60]
        report.append("- **%s**: %s" % (name, reason))

report.append("\n---\n⚠️ 本报告由AI基于同花顺金融数据自动生成，不构成投资建议。")

# 保存
md = "\n".join(report)
with open(OUT + "/bulletin_%s.md" % DATE, "w", encoding="utf-8") as f:
    f.write(md)
print("\n✅ 报告已保存: %s/bulletin_%s.md (%d字节)" % (OUT, DATE, len(md)))

# 打印摘要
print("\n" + "="*50)
print("【报告摘要】")
print("="*50)
print(md[:800])
