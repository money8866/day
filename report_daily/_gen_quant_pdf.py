# -*- coding: utf-8 -*-
"""2026-07-24 量化复盘 PDF 生成（reportlab Platypus + 微软雅黑）。修复：所有带颜色的单元格需包成 Paragraph。"""
import json
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable)

FONT = "Chinese"
pdfmetrics.registerFont(TTFont(FONT, "C:/Windows/Fonts/msyh.ttc"))
OUT = "D:/mystock/report_daily"
DATE = "2026-07-24"

def P(text, fs=10, color="#1a1a1a", bold=False):
    txt = ("<b>%s</b>" % text) if bold else text
    return Paragraph(txt, ParagraphStyle("p", fontName=FONT, fontSize=fs,
                                         textColor=colors.HexColor(color), leading=fs + 4))

def cell(text, fs=8.5, color="#1a1a1a"):
    return Paragraph(str(text), ParagraphStyle("c", fontName=FONT, fontSize=fs,
                                               textColor=colors.HexColor(color), leading=fs + 3,
                                               alignment=1))

def trow(data, col_w, hc, fs=8.5):
    """data: 第一行表头(白字)，其余行单元格可为字符串(含<font>标记)或Paragraph。"""
    wrapped = []
    for ri, row in enumerate(data):
        wr = []
        for c in row:
            if isinstance(c, Paragraph):
                wr.append(c)
            elif ri == 0:
                wr.append(Paragraph(c, ParagraphStyle("h", fontName=FONT, fontSize=fs,
                             textColor=colors.whitesmoke, alignment=1, leading=fs + 3)))
            else:
                wr.append(cell(c, fs))
        wrapped.append(wr)
    t = Table(wrapped, colWidths=col_w)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), hc),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t

# 数据
idx = json.load(open(OUT + "/q_indices_0724.json", encoding="utf-8"))["data"]["item"]
im = {it["thscode"]: it for it in idx}
sh = {"thscode": "000001.SH", "last_price": 3814.2, "price_change_ratio_pct": -1.61}
idx_fixed = [sh] + [im[c] for c in ["399001.SZ", "399006.SZ", "000300.SH", "000905.SH", "000852.SH"]]
names = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指",
         "000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000"}
lu = json.load(open(OUT + "/q_limitup_0724.json", encoding="utf-8"))["items"]
hot = json.load(open(OUT + "/q_hot_0724.json", encoding="utf-8"))["data"]["item"]
dt = json.load(open(OUT + "/q_dragon_0724.json", encoding="utf-8"))["data"]
pos = json.load(open(OUT + "/q_pos_0724.json", encoding="utf-8"))
from collections import Counter
lc = Counter(s.get("continue_day_cnt", 1) for s in lu)
maxb = max([d for d in lc if d > 1], default=0)
bs = {}
for s in lu:
    if s.get("continue_day_cnt", 1) > 1:
        bs.setdefault(s["continue_day_cnt"], []).append(s["name"])
th = Counter()
for s in lu:
    for t in s["limit_up_reason"].split("+"):
        th[t.strip()] += 1
dt_items = dt.get("stock_items", []) if isinstance(dt, dict) else []
ds = sorted([x for x in dt_items if x.get("net_value") is not None], key=lambda x: x["net_value"])
dbuy = [x for x in ds if x["net_value"] > 0][-6:]
dsell = [x for x in ds if x["net_value"] < 0][:6]

BLUE, RED, GREEN, PURPLE, ORANGE, MBLUE = (colors.HexColor("#1a3a8a"), colors.HexColor("#e74c3c"),
    colors.HexColor("#27ae60"), colors.HexColor("#8e44ad"), colors.HexColor("#e67e22"),
    colors.HexColor("#2c5f9e"))

story = []
story.append(P("幻方级量化复盘 · %s（收盘）" % DATE, 17, "#1a3a8a", True))
story.append(P("数据来源：同花顺金融数据 MCP（指数/涨跌停/热股/龙虎榜/ETF）", 8, "#888"))
story.append(Spacer(1, 4))

# 1 大盘
story.append(P("1、大盘全景 — 全市场共振下跌，小票通杀", 12.5, "#fff", False))
hd = [["指数", "收盘", "涨跌幅", "成交额"]]
for it in idx_fixed:
    c = it["thscode"]; chg = it["price_change_ratio_pct"]
    col = "#c0392b" if chg < 0 else "#27ae60"
    hd.append([names[c], "%.2f" % it["last_price"],
               '<font color="%s">%.2f%%</font>' % (col, chg),
               ("%.0f亿" % (it.get("turnover", 0) / 1e8)) if it.get("turnover") else "—"])
story.append(trow(hd, [70, 70, 70, 90], BLUE))
story.append(P("结构：中证1000 %.2f%% > 创业板 %.2f%% > 中证500 %.2f%% > 沪深300 %.2f%% > 上证 %.2f%%。小票跌幅为大票1.7倍，权重护盘、个股普跌。"
                % (idx_fixed[5]["price_change_ratio_pct"], idx_fixed[2]["price_change_ratio_pct"],
                   idx_fixed[4]["price_change_ratio_pct"], idx_fixed[3]["price_change_ratio_pct"],
                   idx_fixed[0]["price_change_ratio_pct"]), 9, "#c0392b"))
story.append(Spacer(1, 6))

# 2 情绪
story.append(P("2、市场情绪 — 风险偏好断崖，连板抱团仍在", 12.5, "#fff", False))
kpi = Table([[P("40", 16, "#fff", True), P("%d" % maxb, 16, "#fff", True), P("17", 16, "#fff", True), P("冰点", 16, "#fff", True)],
             [P("涨停(昨115,-65%)", 8, "#fff"), P("最高连板", 8, "#fff"), P("连板(占42.5%)", 8, "#fff"), P("情绪定位", 8, "#fff")]],
            colWidths=[85, 85, 85, 85])
kpi.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BLUE), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                         ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                         ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("GRID", (0, 0), (-1, -1), 1, BLUE)]))
story.append(kpi)
bd = ""
for d in sorted(bs.keys(), reverse=True):
    bd += "  <b>%d连板(%d只)</b>：%s<br/>" % (d, len(bs[d]), "、".join(bs[d]))
story.append(P("连板明细：" + bd, 9, "#1a1a1a"))
story.append(P("涨停数115→40断崖，打板生态急剧恶化；但高位连板未全面坍塌。同花顺无跌停池工具，跌停数未直取。", 8.5, "#888"))
story.append(Spacer(1, 6))

# 3 龙虎榜
story.append(P("3、资金与龙虎榜（%s）" % dt.get("trade_date", "—"), 12.5, "#fff", False))
hb = [["净买入TOP", "题材", "净额"]] + [[x["name"], x.get("limit_reason", "")[:14],
      '<font color="#27ae60">+%.2f亿</font>' % (x["net_value"] / 1e8)] for x in dbuy]
hs = [["净卖出TOP", "题材", "净额"]] + [[x["name"], x.get("limit_reason", "")[:14],
      '<font color="#c0392b">%.2f亿</font>' % (x["net_value"] / 1e8)] for x in dsell]
story.append(trow(hb, [175, 130, 55], GREEN))
story.append(Spacer(1, 3))
story.append(trow(hs, [175, 130, 55], RED))
story.append(Spacer(1, 6))

# 4 主线
story.append(P("4、主线题材 — 军工重组+长鑫概念+半导体设备+电网液冷", 12.5, "#fff", False))
tr = [["涨停题材", "家数"]]
for t, c in th.most_common(14):
    if t in ("央企", "国企", "中报预增", "半年报预增", "一季报增长", "增持", "回购"):
        continue
    tr.append([t, str(c)])
story.append(trow(tr, [200, 60], MBLUE))
story.append(P("热股验证(人气TOP30)：半导体封测/存储霸榜——通富微电#1、长电#5、华天#7、兆易#9、深科技#11、德明利#3；电力/电网(立新能源#2、华电辽能#8、华银电力#12、中国西电#14)双主线。", 9, "#c0392b"))
story.append(Spacer(1, 6))

# 5 持仓
story.append(P("5、持仓诊断 — 半导体集群逆势，是非半导体的唯一亮点", 12.5, "#fff", False))
note = {"159516.SZ": "逆势最强·设备涨停潮", "512480.SH": "微涨·跑赢", "512760.SH": "微涨·跑赢",
        "159611.SZ": "跟随电力回调", "159865.SZ": "跟随大盘", "515050.SH": "跟随大盘"}
pr = [["代码", "名称", "现价", "今日", "诊断"]]
for code, v in pos.items():
    chg = v["chg"]
    col = "#27ae60" if chg >= 0 else "#c0392b"
    pr.append([code, v["name"], "%.3f" % v["last"],
               '<font color="%s">%.2f%%</font>' % (col, chg), note.get(code, "")])
story.append(trow(pr, [62, 78, 50, 55, 110], RED))
story.append(P("半导体设备ETF +3.06%逆势领涨（托伦斯/至纯科技/光力科技等设备股20%涨停驱动）；半导体ETF+0.63%、芯片ETF+0.92%微涨跑赢。非半导体（电力-4.09%、养殖-3.20%、通信-2.84%）跟跌。半导体集群累计仍深套，今日为超跌反弹非反转。", 9, "#1a1a1a"))
story.append(Spacer(1, 6))

# 6 规避
story.append(P("6、规避与策略", 12.5, "#fff", False))
avoid = ("<b>规避：</b><br/>① 高位连板题材股（涨停115→40断崖，退潮风险骤升）；<br/>"
         "② 无业绩小票（中证1000 -2.78%、创业板 -2.65%通杀）；<br/>"
         "③ 半导体设备不追高（今日逆势已反映情绪，大盘未企稳前反弹减仓优于加仓）；<br/>"
         "④ 整体防御：C浪下杀未止，仓位≤15%，以ETF网格/定投替代个股博弈。<br/>"
         "<b>可关注：</b>电力ETF回调至支撑低吸（防御主线）；半导体设备ETF反弹持有观察但择机减仓降敞口。")
story.append(P(avoid, 9.5, "#1a1a1a"))
story.append(Spacer(1, 6))

# 7 观察
story.append(P("7、明日观察信号", 12.5, "#fff", False))
obs = ("① 半导体设备涨停潮持续性（托伦斯/至纯科技/光力科技20%涨停能否延续）；<br/>"
       "② 连板高度能否突破4板（长缆科技-液冷、爱丽家居-存储测试设备）；<br/>"
       "③ 创业板/中证1000是否止跌企稳（处C浪下杀，未现底部信号）；<br/>"
       "④ 外部催化：英伟达财报、台积电涨价链、长鑫科技IPO进展。")
story.append(P(obs, 9.5, "#1a1a1a"))
story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", color=colors.HexColor("#ccc")))
story.append(P("⚠️ AI风险提示：本报告由AI基于同花顺金融数据自动生成，不构成任何投资建议。股市有风险，投资需谨慎。", 8, "#888"))

doc = SimpleDocTemplate(OUT + "/Final_Quant_%s.pdf" % DATE, pagesize=A4,
                        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                        title="量化复盘%s" % DATE)
doc.build(story)
print("PDF saved:", OUT + "/Final_Quant_%s.pdf" % DATE)
