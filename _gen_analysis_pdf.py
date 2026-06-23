# -*- coding: utf-8 -*-
"""生成BullScore TOP20个股分析PDF"""
import os, sys, datetime
sys.path.insert(0, r'D:\mystock')

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable, PageBreak)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# 字体
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
    pdfmetrics.registerFont(TTFont('SimSun', 'C:/Windows/Fonts/simsun.ttc'))
    pdfmetrics.registerFont(TTFont('Arial', 'C:/Windows/Fonts/arial.ttf'))
    FONT = 'SimHei'
    FONT_B = 'SimHei'
except:
    FONT = 'Helvetica'
    FONT_B = 'Helvetica'

# 颜色
RED = colors.HexColor('#e74c3c')
GREEN = colors.HexColor('#27ae60')
DARK_BLUE = colors.HexColor('#1a3c6e')
LIGHT_BLUE = colors.HexColor('#3498db')
ORANGE = colors.HexColor('#e67e22')
GRAY = colors.HexColor('#7f8c8d')
LIGHT_GRAY = colors.HexColor('#ecf0f1')
DARK_GRAY = colors.HexColor('#2c3e50')
GOLD = colors.HexColor('#f39c12')
UP_COLOR = colors.HexColor('#c0392b')
DOWN_COLOR = colors.HexColor('#27ae60')

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

# ── 数据 ─────────────────────────────────────────────
TODAY = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
LAST_TRADE = '2026-06-23'

# 个股数据（来自最新Tushare日线）
STOCKS = [
    {"rank": 1,  "code": "002602", "name": "世纪华通",   "close": 13.46, "pct_chg": +4.02, "ma5": 13.34, "ma20": 14.39, "ma60": 15.59, "rsi": 38, "boll_l": 13.00, "boll_m": 14.39, "boll_u": 15.78, "赛道": "数据要素/AI游戏",  "profit_yoy": 447.2, "roe": 25.4, "entry": 13.34, "stop": 13.00, "entry_logic": "MA5支撑", "target_con": 31.52, "target_reason": 40.55, "upside": +134.2, "rr": 0.0, "comment": "利润暴增447%超预期，AI+游戏双主线。RSI 38低位，MA5上方强势整理，回调MA5是最佳买点。赛道优质，止损设布林下轨13元。", "alert": "观望"},
    {"rank": 2,  "code": "002709", "name": "天赐材料",   "close": 55.92, "pct_chg": +8.35, "ma5": 52.34, "ma20": 50.86, "ma60": 51.48, "rsi": 60, "boll_l": 46.10, "boll_m": 50.86, "boll_u": 55.61, "赛道": "锂电电解液",        "profit_yoy": 180.9, "roe": 34.5, "entry": 52.34, "stop": 47.63, "entry_logic": "MA5回踩", "target_con": 86.27, "target_reason": 101.44, "upside": +54.3, "rr": 7.2, "comment": "锂电电解液龙头，ROE 34.5%顶级，利润增速181%。回踩MA5（52.3元）低吸，止损设MA10或-10%。", "alert": "推荐"},
    {"rank": 3,  "code": "688002", "name": "睿创微纳",   "close": 149.50,"pct_chg": +1.18, "ma5": 142.33,"ma20": 130.62,"ma60": 127.48,"rsi": 68, "boll_l": 111.76,"boll_m": 130.62,"boll_u": 149.49,"赛道": "红外/商业航天",    "profit_yoy": 124.1, "roe": 25.2, "entry": 142.33,"stop": 129.52,"entry_logic": "MA5回踩", "target_con": 205.16,"target_reason": 232.99,"upside": +37.2, "rr": 4.9, "comment": "红外+卫星互联网双轮驱动，RSI 68偏强。已突破布林上轨，等回踩MA5（142元）再买，止损设MA20（130元）。", "alert": "推荐"},
    {"rank": 4,  "code": "603659", "name": "璞泰来",     "close": 30.90, "pct_chg": +4.75, "ma5": 29.75, "ma20": 29.43, "ma60": 32.33, "rsi": 54, "boll_l": 26.55, "boll_m": 29.43, "boll_u": 32.30, "赛道": "负极/氟化工",      "profit_yoy": 88.2,  "roe": 14.7, "entry": 29.75, "stop": 27.07, "entry_logic": "MA5回踩", "target_con": 39.08, "target_reason": 43.16, "upside": +26.5, "rr": 3.5, "comment": "负极材料+氟化工双主业，利润增速88%。RSI 52健康，趋势完好，等回踩布林下轨，止损-10%。", "alert": "推荐"},
    {"rank": 5,  "code": "002985", "name": "北摩高科",   "close": 29.54, "pct_chg": -2.89, "ma5": 30.42, "ma20": 29.98, "ma60": 33.94, "rsi": 43, "boll_l": 26.96, "boll_m": 29.98, "boll_u": 32.99, "赛道": "军工航空航天",    "profit_yoy": 952.3, "roe": 10.3, "entry": 29.31, "stop": 26.67, "entry_logic": "MA10支撑", "target_con": 113.93,"target_reason": 156.13,"upside": +285.7, "rr": 32.1,"comment": "军工航空航天，利润暴增952%超高速。RSI 43低位低吸机会强，等反弹至MA5（30.4元）突破跟进，止损设布林下轨。", "alert": "推荐"},
    {"rank": 6,  "code": "688183", "name": "生益电子",   "close": 134.40,"pct_chg": -2.96, "ma5": 137.22,"ma20": 128.07,"ma60": 109.80,"rsi": 58, "boll_l": 108.83,"boll_m": 128.07,"boll_u": 147.31,"赛道": "PCB/AI服务器",   "profit_yoy": 343.8, "roe": 29.0, "entry": 127.93,"stop": 108.83,"entry_logic": "布林中轨", "target_con": 273.02,"target_reason": 342.33,"upside": +103.1, "rr": 0.0, "comment": "PCB龙头深度受益AI服务器，利润增速344%。回踩布林中轨（128元）是买点，止损布林下轨（109元）。", "alert": "观望"},
    {"rank": 7,  "code": "688525", "name": "佰维存储",   "close": 406.31,"pct_chg": +5.21, "ma5": 368.59,"ma20": 328.85,"ma60": 289.96,"rsi": 72, "boll_l": 270.47,"boll_m": 328.85,"boll_u": 387.22,"赛道": "存储芯片/AI算力", "profit_yoy": 520.2, "roe": 135.2,"entry": 368.59,"stop": 328.85,"entry_logic": "MA5回踩", "target_con": 1040.40,"target_reason": 1357.44,"upside": +156.1, "rr": 16.9,"comment": "存储芯片龙头，利润暴增520%，RSI 72偏热。短线追高需谨慎，等回踩MA5（369元），止损设MA20（329元）。", "alert": "推荐"},
    {"rank": 8,  "code": "603379", "name": "三美股份",   "close": 69.48, "pct_chg": +6.19, "ma5": 67.59, "ma20": 62.01, "ma60": 62.00, "rsi": 63, "boll_l": 54.54, "boll_m": 62.01, "boll_u": 69.47, "赛道": "氟化工制冷剂",    "profit_yoy": 163.8, "roe": 23.2, "entry": 67.59, "stop": 62.01, "entry_logic": "MA5回踩", "target_con": 103.62,"target_reason": 120.69,"upside": +49.1, "rr": 6.5, "comment": "氟化工制冷剂龙头，RSI 63偏强。等回踩MA5（67.6元）买入，止损布林中轨（62元）。", "alert": "推荐"},
    {"rank": 9,  "code": "603256", "name": "宏和科技",   "close": 263.01,"pct_chg": +2.02, "ma5": 251.99,"ma20": 209.05,"ma60": 140.66,"rsi": 79, "boll_l": 153.51,"boll_m": 209.05,"boll_u": 264.59,"赛道": "高端玻纤/PCB",   "profit_yoy": 785.5, "roe": 20.2, "entry": 251.99,"stop": 209.05,"entry_logic": "MA5回踩", "target_con": 882.79,"target_reason": 1192.68,"upside": +235.6, "rr": 14.7,"comment": "高端玻纤利润暴增786%爆发，ROE 20.2%。RSI 79超买，止损需设布林中轨（209元），幅度较大，谨慎。", "alert": "谨慎"},
    {"rank": 10, "code": "300476", "name": "胜宏科技",   "close": 365.40,"pct_chg": -0.98, "ma5": 359.22,"ma20": 354.32,"ma60": 327.14,"rsi": 56, "boll_l": 312.00,"boll_m": 354.32,"boll_u": 396.65,"赛道": "PCB/AI服务器",   "profit_yoy": 273.5, "roe": 29.6, "entry": 359.22,"stop": 326.89,"entry_logic": "MA5回踩", "target_con": 665.21,"target_reason": 815.12,"upside": +82.0, "rr": 9.5, "comment": "PCB+AI服务器受益标的，ROE 29.6%，RSI 56量价健康。回踩MA5（359元）是买点，止损MA20（327元）或-9%。", "alert": "推荐"},
    {"rank": 11, "code": "603893", "name": "瑞芯微",     "close": 179.91,"pct_chg": -0.42, "ma5": 175.87,"ma20": 176.82,"ma60": 173.07,"rsi": 54, "boll_l": 153.43,"boll_m": 176.82,"boll_u": 200.21,"赛道": "AI芯片/端侧",    "profit_yoy": 74.8,  "roe": 27.9, "entry": 175.87,"stop": 160.04,"entry_logic": "MA5回踩", "target_con": 220.28,"target_reason": 240.47,"upside": +22.4, "rr": 2.8, "comment": "AI芯片设计龙头，RSI 54震荡。等回踩10日线（176元），止损-10%，端侧AI需求持续。", "alert": "推荐"},
    {"rank": 12, "code": "688519", "name": "南亚新材",   "close": 375.65,"pct_chg": -5.38, "ma5": 372.48,"ma20": 277.93,"ma60": 200.08,"rsi": 74, "boll_l": 148.66,"boll_m": 277.93,"boll_u": 407.21,"赛道": "PCB覆铜板",      "profit_yoy": 377.6, "roe": 20.8, "entry": 372.48,"stop": 277.93,"entry_logic": "MA5回踩", "target_con": 801.19,"target_reason": 1013.95,"upside": +113.3, "rr": 4.5, "comment": "覆铜板材料受益算力需求，利润增速378%亮眼。RSI 74超买，止损需设MA20（278元），幅度25%，等更好价位。", "alert": "谨慎"},
    {"rank": 13, "code": "300548", "name": "长芯博创",   "close": 298.09,"pct_chg": +5.71, "ma5": 280.42,"ma20": 244.12,"ma60": 233.19,"rsi": 65, "boll_l": 196.08,"boll_m": 244.12,"boll_u": 292.16,"赛道": "光通信器件",      "profit_yoy": 175.1, "roe": 40.9, "entry": 280.42,"stop": 255.18,"entry_logic": "MA5回踩", "target_con": 454.68,"target_reason": 532.97,"upside": +52.5, "rr": 6.9, "comment": "光通信器件龙头，ROE 40.9%顶级，RSI 65。趋势完美，回踩5日线（280元）是理想买点，止损布林下轨（255元）。", "alert": "推荐"},
    {"rank": 14, "code": "300604", "name": "长川科技",   "close": 268.49,"pct_chg": +1.66, "ma5": 253.69,"ma20": 227.65,"ma60": 185.38,"rsi": 70, "boll_l": 186.59,"boll_m": 227.65,"boll_u": 268.70,"赛道": "半导体测试设备",  "profit_yoy": 187.7, "roe": 28.2, "entry": 253.69,"stop": 227.65,"entry_logic": "MA5回踩", "target_con": 419.68,"target_reason": 495.27,"upside": +56.3, "rr": 6.4, "comment": "半导体测试设备龙头，ROE 28.2%，RSI 70偏热。强势股等回调-8%以内可分批建仓，止损设MA20（228元）。", "alert": "推荐"},
    {"rank": 15, "code": "688127", "name": "蓝特光学",   "close": 87.23, "pct_chg": -2.51, "ma5": 86.46, "ma20": 84.81, "ma60": 77.86, "rsi": 55, "boll_l": 76.49, "boll_m": 84.81, "boll_u": 93.13, "赛道": "光学元组件",      "profit_yoy": 76.7,  "roe": 22.2, "entry": 76.49, "stop": 68.84, "entry_logic": "布林下轨", "target_con": 107.30,"target_reason": 117.34,"upside": +23.0, "rr": 2.7, "comment": "光学元组件，RSI 55估值合理。回踩布林下轨（76.5元）是买点，止损布林下轨（68.8元）或-10%。", "alert": "推荐"},
    {"rank": 16, "code": "688025", "name": "杰普特",     "close": 442.67,"pct_chg": -1.41, "ma5": 440.58,"ma20": 404.35,"ma60": 339.81,"rsi": 63, "boll_l": 333.72,"boll_m": 404.35,"boll_u": 474.98,"赛道": "激光/光通信",    "profit_yoy": 124.5, "roe": 16.1, "entry": 404.35,"stop": 333.72,"entry_logic": "布林中轨", "target_con": 608.01,"target_reason": 690.68,"upside": +37.4, "rr": 4.6, "comment": "激光设备受益光通信扩产，RSI 63偏强。回踩布林中轨（404元）或MA5（440元）买入，止损布林下轨（334元）。", "alert": "推荐"},
    {"rank": 17, "code": "688313", "name": "仕佳光子",   "close": 182.55,"pct_chg": +4.05, "ma5": 173.39,"ma20": 162.79,"ma60": 142.43,"rsi": 61, "boll_l": 140.97,"boll_m": 162.79,"boll_u": 184.62,"赛道": "光芯片/AI算力",   "profit_yoy": 473.2, "roe": 28.0, "entry": 173.39,"stop": 157.78,"entry_logic": "MA5回踩", "target_con": 441.70,"target_reason": 571.27,"upside": +142.0, "rr": 17.2,"comment": "光芯片龙头深度受益AI算力，利润暴增473%，RSI 61。高位强势，等回踩10日线（173元）再买，止损-10%。", "alert": "推荐"},
    {"rank": 18, "code": "300475", "name": "香农芯创",   "close": 287.80,"pct_chg": +12.92,"ma5": 239.33,"ma20": 195.67,"ma60": 174.11,"rsi": 80, "boll_l": 131.35,"boll_m": 195.67,"boll_u": 259.99,"赛道": "存储模组",        "profit_yoy": 169.5, "roe": 110.6,"entry": 239.33,"stop": 195.67,"entry_logic": "MA5回踩", "target_con": 434.15,"target_reason": 507.32,"upside": +50.9, "rr": 4.5, "comment": "存储模组需求旺盛，RSI 80超买！今日+12.9%短线极热，需等待回踩布林中轨或MA5（239元）附近，止损MA20（196元）。", "alert": "谨慎"},
    {"rank": 19, "code": "001389", "name": "广合科技",   "close": 192.10,"pct_chg": -4.65, "ma5": 197.35,"ma20": 186.70,"ma60": 163.06,"rsi": 55, "boll_l": 168.73,"boll_m": 186.70,"boll_u": 204.67,"赛道": "PCB/服务器",      "profit_yoy": 50.2,  "roe": 21.8, "entry": 186.70,"stop": 168.73,"entry_logic": "布林中轨", "target_con": 221.03,"target_reason": 235.50,"upside": +15.1, "rr": 15.3,"comment": "服务器PCB供应商，ROE 21.8%，RSI 55健康。今日-4.6%下跌，等回踩布林中轨（187元）是买点，止损布林下轨（169元）。", "alert": "观望"},
    {"rank": 20, "code": "002558", "name": "巨人网络",   "close": 25.74, "pct_chg": +7.70, "ma5": 24.46, "ma20": 25.21, "ma60": 29.63, "rsi": 48, "boll_l": 23.05, "boll_m": 25.21, "boll_u": 27.37, "赛道": "AI+游戏",         "profit_yoy": 23.6,  "roe": 28.6, "entry": 24.46, "stop": 22.26, "entry_logic": "MA5支撑", "target_con": 27.56, "target_reason": 28.47, "upside": +7.1, "rr": 1.4, "comment": "AI+游戏双概念，ROE 28.6%优质。RSI 48低位，等反弹至MA5（24.5元）突破跟进，止损布林下轨（22.3元）。空间有限。", "alert": "观望"},
]

# ── 样式 ─────────────────────────────────────────────
styles = getSampleStyleSheet()

def S(name='Normal', **kw):
    s = ParagraphStyle(name, parent=styles[name], **kw)
    return s

TITLE_S = S('Title', fontName=FONT_B, fontSize=20, textColor=DARK_BLUE,
            leading=24, alignment=TA_CENTER, spaceAfter=4)
SUBTITLE_S = S('Normal', fontName=FONT, fontSize=10, textColor=GRAY,
               alignment=TA_CENTER, spaceAfter=8)
SECTION_S = S('Heading2', fontName=FONT_B, fontSize=13, textColor=DARK_BLUE,
              leading=16, spaceBefore=12, spaceAfter=4)
BODY_S = S('Normal', fontName=FONT, fontSize=9, textColor=DARK_GRAY, leading=13)
SMALL_S = S('Normal', fontName=FONT, fontSize=8, textColor=GRAY, leading=11)
BOLD_S = S('Normal', fontName=FONT_B, fontSize=9, textColor=DARK_GRAY)
RED_S = S('Normal', fontName=FONT, fontSize=9, textColor=RED)
GREEN_S = S('Normal', fontName=FONT, fontSize=9, textColor=GREEN)
ORANGE_S = S('Normal', fontName=FONT_B, fontSize=9, textColor=ORANGE)

def red_p(t): return f'<font color="#e74c3c">{t}</font>'
def green_p(t): return f'<font color="#27ae60">{t}</font>'
def blue_p(t): return f'<font color="#1a3c6e"><b>{t}</b></font>'

def up_down_str(val):
    if val > 0:
        return Paragraph(f'<font color="#e74c3c">+{val:.2f}%</font>', SMALL_S)
    elif val < 0:
        return Paragraph(f'<font color="#27ae60">{val:.2f}%</font>', SMALL_S)
    return Paragraph('0.00%', SMALL_S)

def alert_style(alert):
    if alert == '推荐':
        return Paragraph('<font color="#27ae60"><b>推荐</b></font>', SMALL_S)
    elif alert == '谨慎':
        return Paragraph('<font color="#e67e22"><b>谨慎</b></font>', SMALL_S)
    else:
        return Paragraph('<font color="#7f8c8d">观望</font>', SMALL_S)

# ── PDF 生成 ─────────────────────────────────────────
out_path = os.path.join(r'D:\mystock\solo\multi_factor_picker\output',
                         f'BullScore_Top20分析报告_{datetime.datetime.now().strftime("%Y%m%d_%H%M")}.pdf')

doc = SimpleDocTemplate(out_path, pagesize=A4,
                        leftMargin=MARGIN, rightMargin=MARGIN,
                        topMargin=MARGIN, bottomMargin=MARGIN)

story = []

# ── 封面区 ───────────────────────────────────────────
story.append(Spacer(1, 8*mm))
story.append(Paragraph('BullScore TOP20 个股深度分析报告', TITLE_S))
story.append(Paragraph(f'数据日期: {LAST_TRADE} | 生成时间: {TODAY}', SUBTITLE_S))
story.append(HRFlowable(width='100%', thickness=2, color=DARK_BLUE, spaceAfter=6))

# ── 摘要 ────────────────────────────────────────────
story.append(Paragraph('分析说明', SECTION_S))
story.append(Paragraph(
    '本报告基于 BullScore 中长线牛股评分体系筛选 TOP20 个股，结合 Tushare 最新日线数据（MA5/MA20/MA60、RSI-14、布林带）计算技术面买点、止损点；'
    '结合利润增速（profit_yoy）、ROE 等基本面数据估算1年目标价空间。数据截止最新交易日。',
    BODY_S
))
story.append(Spacer(1, 4*mm))

# ── 汇总表（精简8列，适配A4）──────────────────────────
story.append(Paragraph('TOP20 技术面汇总', SECTION_S))

# 8列：代码/名称/现价/涨跌/RSI/买点/止损/上涨空间/评级
# 内容宽 = 210-36 = 174mm
header = ['代码', '名称', '现价', '涨跌', 'RSI14', '建议买点', '止损点', '1年空间', '评级']
col_w = [16, 22, 18, 18, 16, 18, 18, 20, 16]  # 共 162mm < 174mm OK

table_data = [header]
for s in STOCKS:
    pct_str = '+%.2f%%' % s['pct_chg'] if s['pct_chg'] >= 0 else '%.2f%%' % s['pct_chg']
    row = [
        s['code'], s['name'],
        '%.2f' % s['close'], pct_str,
        '%.0f' % s['rsi'],
        '%.2f' % s['entry'],
        '%.2f' % s['stop'],
        '+%.1f%%' % s['upside'],
        s['alert'],
    ]
    table_data.append(row)

t = Table(table_data, colWidths=[x * mm for x in col_w], repeatRows=1)
ts_style = [
    ('BACKGROUND', (0,0), (-1,0), DARK_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), FONT_B),
    ('FONTSIZE', (0,0), (-1,0), 8),
    ('FONTSIZE', (0,1), (-1,-1), 8),
    ('FONTNAME', (0,1), (-1,-1), FONT),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
]
# 涨跌色（红涨绿跌）
for i, s in enumerate(STOCKS):
    c = colors.HexColor('#e74c3c') if s['pct_chg'] > 0 else colors.HexColor('#27ae60')
    ts_style.append(('TEXTCOLOR', (3,i+1), (3,i+1), c))
# 评级色
for i, s in enumerate(STOCKS):
    if s['alert'] == '推荐':
        c = colors.HexColor('#27ae60')
    elif s['alert'] == '谨慎':
        c = colors.HexColor('#e67e22')
    else:
        c = colors.HexColor('#7f8c8d')
    ts_style.append(('TEXTCOLOR', (8,i+1), (8,i+1), c))
# 斑马纹
for i in range(len(STOCKS)):
    if i % 2 == 1:
        ts_style.append(('BACKGROUND', (0,i+1), (-1,i+1), colors.HexColor('#f2f4f7')))

t.setStyle(TableStyle(ts_style))
story.append(t)
story.append(Spacer(1, 5*mm))

# ── 重点推荐（精简7列）───────────────────────────────
story.append(Paragraph('★★★ 重点推荐（风险收益比 >= 4.5x）★★★', SECTION_S))

recommended = [s for s in STOCKS if s['rr'] >= 4.5][:10]
rec_cols = ['代码','名称','现价','今日涨跌','RSI','买点->止损','目标(保守)','1年空间','RR','评级']
# 7列：16+22+18+20+14+28+26+20+16+18 = 178mm >> 174mm → 压缩
rec_cols2 = ['代码','名称','现价','涨跌','RSI','买点','止损','1年空间','RR','评级']
cw_rec2 = [15, 22, 17, 18, 14, 17, 17, 18, 16, 18]  # 172mm OK
rec_data2 = [rec_cols2]
for s in recommended:
    pct_str = '+%.2f%%' % s['pct_chg'] if s['pct_chg'] >= 0 else '%.2f%%' % s['pct_chg']
    stop_loss_pct = (1 - s['stop'] / s['entry']) * 100
    rec_data2.append([
        s['code'], s['name'],
        '%.2f' % s['close'], pct_str,
        '%.0f' % s['rsi'],
        '%.2f' % s['entry'],
        '%.2f' % s['stop'],
        '+%.1f%%' % s['upside'],
        '%.1fx' % s['rr'],
        s['alert'],
    ])

t2 = Table(rec_data2, colWidths=[x*mm for x in cw_rec2], repeatRows=1)
ts2_style = [
    ('BACKGROUND', (0,0), (-1,0), GOLD),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), FONT_B),
    ('FONTSIZE', (0,0), (-1,0), 8),
    ('FONTSIZE', (0,1), (-1,-1), 8),
    ('FONTNAME', (0,1), (-1,-1), FONT),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
]
for i in range(len(recommended)):
    if i % 2 == 0:
        ts2_style.append(('BACKGROUND', (0,i+1), (-1,i+1), colors.HexColor('#fef9e7')))
for i, s in enumerate(recommended):
    c = colors.HexColor('#e74c3c') if s['pct_chg'] > 0 else colors.HexColor('#27ae60')
    ts2_style.append(('TEXTCOLOR', (3,i+1), (3,i+1), c))
    if s['alert'] == '推荐':
        c2 = colors.HexColor('#27ae60')
    elif s['alert'] == '谨慎':
        c2 = colors.HexColor('#e67e22')
    else:
        c2 = colors.HexColor('#7f8c8d')
    ts2_style.append(('TEXTCOLOR', (9,i+1), (9,i+1), c2))
t2.setStyle(TableStyle(ts2_style))
story.append(t2)
story.append(Spacer(1, 5*mm))

# ── 分页后：个股详情 ─────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('个股详细分析', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=DARK_BLUE, spaceAfter=6))

for idx, s in enumerate(STOCKS):
    stop_loss_pct = (1 - s['stop'] / s['entry']) * 100

    # 个股标题行
    rsi_color = '#e67e22' if s['rsi'] >= 70 else ('#27ae60' if s['rsi'] < 40 else '#7f8c8d')
    pct_str = '+%.2f%%' % s['pct_chg'] if s['pct_chg'] >= 0 else '%.2f%%' % s['pct_chg']
    title_str = '<font color="#1a3c6e"><b>\u7b2c' + str(s['rank']) + '\u540d</b></font>  '
    title_str += '<b>' + s['name'] + '</b>\uff08' + s['code'] + '\uff09  '
    title_str += '<font color="#e74c3c">\u73b0\u4ef7 %.2f</font>' % s['close']
    title_str += '  \u4eca\u65e5 ' + pct_str
    title_str += '  <font color="' + rsi_color + '">RSI=%.0f</font>' % s['rsi']
    story.append(Paragraph(title_str, BODY_S))
    story.append(Spacer(1, 1*mm))

    # 基本信息网格
    info_data = [
        ['赛道', s['赛道'], '利润增速', f'<font color="#e74c3c">{s["profit_yoy"]:.1f}%</font>', 'ROE', f'{s["roe"]:.1f}%'],
        ['MA5', f'{s["ma5"]:.2f}', 'MA20', f'{s["ma20"]:.2f}', 'MA60', f'{s["ma60"]:.2f}'],
        ['布林上轨', f'{s["boll_u"]:.2f}', '布林中轨', f'{s["boll_m"]:.2f}', '布林下轨', f'{s["boll_l"]:.2f}'],
    ]
    ti = Table([[Paragraph(c, SMALL_S) for c in row] for row in info_data],
               colWidths=[22*mm, 30*mm, 22*mm, 30*mm, 22*mm, 30*mm])
    ti.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), FONT_B),
        ('FONTNAME', (2,0), (2,-1), FONT_B),
        ('FONTNAME', (4,0), (4,-1), FONT_B),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_GRAY),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(ti)
    story.append(Spacer(1, 1*mm))

    # 核心建议
    level_color = '#27ae60' if s['alert']=='推荐' else '#e67e22' if s['alert']=='谨慎' else '#7f8c8d'
    idea_data = [
        [f'<b><font color="#27ae60">建议买点</font></b>',
         f'<b>{s["entry"]:.2f} 元</b>',
         f'（{s["entry_logic"]}）'],
        [f'<b><font color="#e74c3c">止损点</font></b>',
         f'<b>{s["stop"]:.2f} 元</b>',
         f'（-{(1-s["stop"]/s["entry"])*100:.1f}%）'],
        [f'<b><font color="#1a3c6e">目标价（保守）</font></b>',
         f'<b>{s["target_con"]:.2f} 元</b>',
         f'（+{s["upside"]:.1f}%）'],
        [f'<b><font color="#f39c12">风险收益比</font></b>',
         f'<b>{s["rr"]:.1f}x</b>',
         f'（盈亏比）'],
    ]
    ti2 = Table([[Paragraph(c, SMALL_S) for c in row] for row in idea_data],
                colWidths=[48*mm, 36*mm, 36*mm])
    ti2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f0f4f8')),
        ('FONTNAME', (0,0), (-1,-1), FONT),
        ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(ti2)
    story.append(Spacer(1, 1*mm))

    # 点评
    alert_badge = f'<font color="{level_color}"><b>[{s["alert"]}]</b></font>'
    story.append(Paragraph(
        f'{alert_badge} <b>点评：</b>{s["comment"]}',
        SMALL_S
    ))

    if s['rank'] < 20:
        story.append(Spacer(1, 3*mm))
        story.append(HRFlowable(width='100%', thickness=0.3, color=colors.HexColor('#dee2e6'), spaceAfter=3))

# ── 免责声明 ─────────────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('免责声明', SECTION_S))
story.append(HRFlowable(width='100%', thickness=1, color=GRAY, spaceAfter=6))
story.append(Paragraph(
    '本报告仅供技术分析参考，不构成任何投资建议。股市有风险，入市须谨慎。'
    'RSI指标说明：RSI>75 为超买区域（短期可能回调），RSI<35 为超卖区域（可能反弹）。'
    '布林带说明：价格触及布林上轨注意压力，触及下轨注意支撑。'
    '目标价基于利润增速 × 0.3 系数估算，为1年预期保守目标，实际涨跌受市场情绪、流动性等多因素影响。'
    '止损点根据个人风险承受能力可适当调整。',
    SMALL_S
))

# ── 图例 ─────────────────────────────────────────────
story.append(Spacer(1, 8*mm))
story.append(Paragraph('评级说明', SECTION_S))
legend_data = [
    [Paragraph('<font color="#27ae60"><b>推荐</b></font>', SMALL_S),
     Paragraph('买点明确 + RSI适中(40~70) + 风险收益比≥3x', SMALL_S)],
    [Paragraph('<font color="#e67e22"><b>谨慎</b></font>', SMALL_S),
     Paragraph('RSI≥75超买 或 止损幅度>15%，需等待更好价位', SMALL_S)],
    [Paragraph('<font color="#7f8c8d">观望</font>', SMALL_S),
     Paragraph('趋势不明 或 RSI偏低 或 目标空间<20%', SMALL_S)],
]
tl = Table(legend_data, colWidths=[22*mm, 120*mm])
tl.setStyle(TableStyle([
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#bdc3c7')),
    ('BACKGROUND', (0,0), (0,-1), LIGHT_GRAY),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('TOPPADDING', (0,0), (-1,-1), 4),
]))
story.append(tl)

# ── 生成 ─────────────────────────────────────────────
doc.build(story)
print(f'PDF已生成: {out_path}')
print(f'文件大小: {os.path.getsize(out_path)/1024:.0f} KB')
