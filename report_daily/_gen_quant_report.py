# -*- coding: utf-8 -*-
"""生成 2026-07-24 幻方级量化复盘 HTML + PDF。"""
import json, datetime

OUT = "D:/mystock/report_daily"
DATE = "2026-07-24"

# ---------- 数据加载 ----------
idx = json.load(open(OUT + "/q_indices_0724.json", encoding="utf-8"))["data"]["item"]
# 修正: 000001.SZ 实为平安银行, 上证指数用 000001.SH
idx_map = {it["thscode"]: it for it in idx}
# 补上证指数 (已单独取)
sh = {"thscode": "000001.SH", "last_price": 3814.2, "price_change_ratio_pct": -1.61,
      "turnover": 0, "volume": 0}
idx_fixed = [sh] + [idx_map[c] for c in ["399001.SZ", "399006.SZ", "000300.SH", "000905.SH", "000852.SH"]]
idx_names = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指",
             "000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000"}

lu = json.load(open(OUT + "/q_limitup_0724.json", encoding="utf-8"))["items"]
hot = json.load(open(OUT + "/q_hot_0724.json", encoding="utf-8"))["data"]["item"]
dt = json.load(open(OUT + "/q_dragon_0724.json", encoding="utf-8"))["data"]
pos = json.load(open(OUT + "/q_pos_0724.json", encoding="utf-8"))

# 连板结构
from collections import Counter
ladder_cnt = Counter()
for s in lu:
    d = s.get("continue_day_cnt", 1)
    ladder_cnt[d] += 1
max_board = max([d for d in ladder_cnt if d > 1], default=0)
board_stocks = {}
for s in lu:
    d = s.get("continue_day_cnt", 1)
    if d > 1:
        board_stocks.setdefault(d, []).append(s["name"])

# 题材聚合
themes = Counter()
for s in lu:
    for t in s["limit_up_reason"].split("+"):
        themes[t.strip()] += 1

# 龙虎榜净买卖 (07-23)
dt_items = dt.get("stock_items", []) if isinstance(dt, dict) else []
dt_sorted = sorted([x for x in dt_items if x.get("net_value") is not None],
                   key=lambda x: x["net_value"])
dt_buy = [x for x in dt_sorted if x["net_value"] > 0][-8:]
dt_sell = [x for x in dt_sorted if x["net_value"] < 0][:8]

# ---------- HTML 生成 ----------
def fmt_pct(x):
    return "%.2f" % x

idx_rows = ""
for it in idx_fixed:
    c = it["thscode"]
    chg = it["price_change_ratio_pct"]
    color = "#c0392b" if chg < 0 else "#27ae60"
    turn = it.get("turnover", 0)
    turn_s = ("%.0f亿" % (turn / 1e8)) if turn else "—"
    idx_rows += ('<tr><td>%s</td><td>%.2f</td>'
                 '<td style="color:%s;font-weight:bold;">%s%%</td>'
                 '<td>%s</td></tr>') % (idx_names[c], it["last_price"], color, fmt_pct(chg), turn_s)

# 连板明细
board_detail = ""
for d in sorted(board_stocks.keys(), reverse=True):
    names = "、".join(board_stocks[d])
    board_detail += "<p><b>%d连板 (%d只)</b>：%s</p>" % (d, len(board_stocks[d]), names)

# 题材 TOP
theme_rows = ""
for t, c in themes.most_common(12):
    if t in ("央企", "国企", "中报预增", "半年报预增", "一季报增长", "增持", "回购"):
        continue
    theme_rows += "<tr><td>%s</td><td>%d</td></tr>" % (t, c)

# 热股 TOP15
hot_rows = ""
for s in hot[:15]:
    hot_rows += "<tr><td>#%d</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        s["rank"], s["name"], s["heat"], s.get("rank_trend", ""))

# 龙虎榜
dt_buy_rows = "".join("<tr><td>%s</td><td>%s</td><td style='color:#27ae60'>+%.2f亿</td></tr>"
                      % (x["name"], x.get("limit_reason", ""), x["net_value"] / 1e8) for x in dt_buy)
dt_sell_rows = "".join("<tr><td>%s</td><td>%s</td><td style='color:#c0392b'>%.2f亿</td></tr>"
                       % (x["name"], x.get("limit_reason", ""), x["net_value"] / 1e8) for x in dt_sell)

# 持仓
pos_rows = ""
pos_note = {
    "159516.SZ": "逆势最强·设备涨停潮驱动",
    "512480.SH": "微涨·跑赢大盘",
    "512760.SH": "微涨·跑赢大盘",
    "159611.SZ": "跟随电力回调",
    "159865.SZ": "跟随大盘",
    "515050.SH": "跟随大盘",
}
for code, v in pos.items():
    chg = v["chg"]
    color = "#27ae60" if chg >= 0 else "#c0392b"
    pos_rows += ("<tr><td>%s</td><td>%s</td><td>%.3f</td>"
                 "<td style='color:%s;font-weight:bold'>%s%%</td><td>%s</td></tr>") % (
        code, v["name"], v["last"], color, fmt_pct(chg), pos_note.get(code, ""))

html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
body{font-family:'Microsoft YaHei','微软雅黑',sans-serif;margin:0;padding:24px;background:#f5f6f8;color:#1a1a1a;}
h1{font-size:22px;color:#1a3a8a;border-bottom:3px solid #1a3a8a;padding-bottom:8px;}
h2{font-size:16px;color:#fff;background:#1a3a8a;padding:6px 12px;border-radius:4px;margin-top:22px;}
.box{background:#fff;padding:16px 18px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin:10px 0;}
table{width:100%%;border-collapse:collapse;font-size:13px;margin:6px 0;}
th{background:#1a3a8a;color:#fff;padding:6px 8px;text-align:left;}
td{padding:5px 8px;border-bottom:1px solid #eee;}
tr:nth-child(even){background:#f8f9fa;}
.hl{color:#e74c3c;font-weight:bold;}
.ok{color:#27ae60;font-weight:bold;}
.warn{background:#fff4e6;border-left:4px solid #e67e22;padding:10px 14px;border-radius:4px;margin:10px 0;}
.kpi{display:flex;gap:12px;flex-wrap:wrap;}
.kpi div{background:#1a3a8a;color:#fff;padding:10px 16px;border-radius:6px;text-align:center;flex:1;min-width:120px;}
.kpi b{font-size:20px;display:block;}
.small{font-size:12px;color:#666;}
</style></head><body>
<h1>幻方级量化复盘 · %s（收盘）</h1>
<p class="small">数据来源：同花顺金融数据 MCP（指数/涨跌停/热股/龙虎榜/ETF）· 生成时间 %s</p>

<h2>1、大盘全景 — 全市场共振下跌，小票通杀</h2>
<div class="box"><table>
<tr><th>指数</th><th>收盘</th><th>涨跌幅</th><th>成交额</th></tr>
%s</table>
<p class="hl">结构：中证1000 %s%% &gt; 创业板 %s%% &gt; 中证500 %s%% &gt; 沪深300 %s%% &gt; 上证 %s%%。小票跌幅是大盘的1.7倍，权重（银行/石油）护盘，个股普跌。</p>
</div>

<h2>2、市场情绪 — 风险偏好断崖，但连板抱团仍在</h2>
<div class="kpi">
<div><b>40</b>涨停（昨115，环比-65%%）</div>
<div><b>17</b>连板（占涨停42.5%%）</div>
<div><b>%d板</b>最高连板高度</div>
<div><b>冰点</b>情绪定位</div>
</div>
<div class="box">%s
<p class="small">注：同花顺无跌停池工具，跌停数未直接获取；中证1000 -2.78%% 暗示跌停扩散。涨停数从115骤降至40，打板生态急剧恶化，但高位连板未全面坍塌（4板×2、3板×2、2板×13）。</p>
</div>

<h2>3、资金与龙虎榜（最近交易日）</h2>
<div class="box"><p class="small">龙虎榜数据日期：%s（同花顺返回最新可用日）</p>
<table><tr><th>净买入TOP</th><th>题材</th><th>净额</th></tr>%s</table>
<table style="margin-top:10px;"><tr><th>净卖出TOP</th><th>题材</th><th>净额</th></tr>%s</table>
</div>

<h2>4、主线题材 — 军工重组 + 长鑫概念 + 半导体设备 + 电网液冷</h2>
<div class="box"><table><tr><th>涨停题材</th><th>家数</th></tr>%s</table>
<p class="hl">热股验证（人气榜TOP30）：半导体封测/存储霸榜 —— 通富微电#1、长电科技#5、华天科技#7、兆易创新#9、深科技#11、德明利#3、有研新材#20、澜起科技#29；电力/电网（立新能源#2、华电辽能#8、华银电力#12、中国西电#14）双主线并行。</p>
</div>

<h2>5、持仓诊断 — 半导体集群逆势，是非半导体的唯一亮点</h2>
<div class="box"><table>
<tr><th>代码</th><th>名称</th><th>现价</th><th>今日</th><th>诊断</th></tr>
%s</table>
<p class="ok">半导体设备ETF +3.06%% 逆势领涨（托伦斯/至纯科技/光力科技等设备股20%%涨停驱动）；半导体ETF +0.63%%、芯片ETF +0.92%% 微涨跑赢大盘。</p>
<p class="hl">非半导体（电力-4.09%%、养殖-3.20%%、通信-2.84%%）跟随市场下跌。半导体集群累计仍深套，今日为超跌反弹而非反转信号。</p>
</div>

<h2>6、规避与策略</h2>
<div class="warn"><p class="hl">规避方向：</p>
<p>① <b>高位连板题材股</b>：涨停数115→40断崖，退潮风险骤升，追高连板胜率下降；</p>
<p>② <b>无业绩支撑的小票</b>：中证1000 -2.78%%、创业板 -2.65%%，微盘/题材通杀；</p>
<p>③ <b>半导体设备不追高</b>：今日逆势大涨已反映短线情绪，大盘未企稳前反弹减仓优于加仓；</p>
<p>④ <b>整体防御</b>：C浪下杀未止，建议仓位维持 ≤15%%，以ETF网格/定投替代个股博弈。</p>
<p class="ok">可关注：电力ETF回调至支撑位低吸（防御主线）；半导体设备ETF反弹持有观察但择机减仓降敞口。</p>
</div>

<h2>7、明日观察信号</h2>
<div class="box"><p>① 半导体设备涨停潮能否延续（今日托伦斯/至纯科技/光力科技20%%涨停的持续性）；</p>
<p>② 连板高度能否突破4板（长缆科技-液冷、爱丽家居-存储测试设备）；</p>
<p>③ 创业板/中证1000是否止跌企稳（当前处C浪下杀，未出现底部信号）；</p>
<p>④ 外部催化：英伟达财报、台积电涨价链、长鑫科技IPO进展。</p></div>

<p class="small">⚠️ AI风险提示：本报告由AI基于同花顺金融数据自动生成，不构成任何投资建议。股市有风险，投资需谨慎。</p>
</body></html>""" % (
    DATE, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    idx_rows,
    fmt_pct(idx_fixed[5]["price_change_ratio_pct"]), fmt_pct(idx_fixed[2]["price_change_ratio_pct"]),
    fmt_pct(idx_fixed[4]["price_change_ratio_pct"]), fmt_pct(idx_fixed[3]["price_change_ratio_pct"]),
    fmt_pct(idx_fixed[0]["price_change_ratio_pct"]),
    max_board, board_detail,
    dt.get("trade_date", "—"), dt_buy_rows, dt_sell_rows,
    theme_rows, pos_rows,
)

with open(OUT + "/Final_Quant_%s.html" % DATE, "w", encoding="utf-8") as f:
    f.write(html)
print("HTML saved:", OUT + "/Final_Quant_%s.html" % DATE)
print("涨停:%d 连板:%d 最高板:%d板" % (len(lu), sum(ladder_cnt[d] for d in ladder_cnt if d > 1), max_board))
