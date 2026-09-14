# -*- coding: utf-8 -*-
"""DLG 高股息·低估值·高成长选股 20260911 MD → PDF"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable, KeepTogether, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import os, re

FONT = 'CN'
BOLD = 'CN-Bold'
pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))
pdfmetrics.registerFont(TTFont(BOLD, r'C:\Windows\Fonts\msyhbd.ttc'))

def S(name, **kw):
    base = dict(fontName=FONT, fontSize=9, leading=13, textColor=colors.black)
    base.update(kw)
    return ParagraphStyle(name, **base)

TITLE  = S('title',  fontSize=16, leading=20, alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a'))
H1    = S('h1',     fontSize=12, leading=16, textColor=colors.white,  backColor=colors.HexColor('#1a3a8a'))
H2    = S('h2',     fontSize=10, leading=14, textColor=colors.HexColor('#1a3a8a'))
BODY  = S('body',   fontSize=9,  leading=13)
BOLD  = S('bold',  fontSize=9,  leading=13, fontName='CN-Bold')
SMALL = S('small', fontSize=7.5, leading=11)
TINY  = S('tiny',  fontSize=6.5, leading=9)
RED   = S('red',   fontSize=9,  leading=13, textColor=colors.HexColor('#c0392b'))
GRN   = S('grn',   fontSize=9,  leading=13, textColor=colors.HexColor('#27ae60'))
BLU   = S('blu',   fontSize=9,  leading=13, textColor=colors.HexColor('#1a3a8a'))
FOOT  = S('foot',  fontSize=7,  leading=10, textColor=colors.grey)

HDR_COL  = colors.HexColor('#1a3a8a')
ALT1     = colors.HexColor('#f0f4fb')
ALT2     = colors.HexColor('#ffffff')
GRADE_A  = colors.HexColor('#fff3cd')
GRADE_B  = colors.HexColor('#d4edda')
GRADE_C  = colors.HexColor('#f8f9fa')

def th(text, color=HDR_COL, fs=8):
    return Paragraph(f'<font color="white"><b>{text}</b></font>', S('th', fontSize=fs, leading=fs*1.4,
        fontName='CN-Bold', textColor=colors.white, backColor=color, alignment=TA_CENTER))

def td(text, align=TA_CENTER, fs=8, bold=False):
    fn = 'CN-Bold' if bold else FONT
    return Paragraph(str(text), S('td', fontSize=fs, leading=fs*1.4, fontName=fn, alignment=align))

def tbl_style(header_color=HDR_COL, row_colors=None):
    rs = [
        ('BACKGROUND', (0,0), (-1,0), header_color),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME',  (0,0), (-1,0), 'CN-Bold'),
        ('FONTSIZE',  (0,0), (-1,-1), 8),
        ('ALIGN',     (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',    (0,0), (-1,-1), 'MIDDLE'),
        ('GRID',      (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), row_colors or [ALT2, ALT1]),
        ('LEFTPADDING',  (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING',  (0,0), (-1,-1), 2),
        ('BOTTOMPADDING',(0,0),(-1,-1), 2),
    ]
    return TableStyle(rs)

def section_header(text):
    data = [[Paragraph(f'<b>{text}</b>', S('sh', fontSize=10, fontName='CN-Bold',
        textColor=colors.white, alignment=TA_LEFT))]]
    t = Table(data, colWidths=[19*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HDR_COL),
        ('LEFTPADDING', (0,0),(-1,-1), 8),
        ('TOPPADDING', (0,0),(-1,-1), 5),
        ('BOTTOMPADDING',(0,-1),(-1,-1), 5),
    ]))
    return t

# ── Main ──────────────────────────────────────────────────────────────────────
OUT = r'D:\mystock\solo\report_daily\dlg_picker_20260911.pdf'
doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                         topMargin=1.5*cm, bottomMargin=1.5*cm)
story = []

# Title
story.append(Spacer(1, 0.3*cm))
story.append(Paragraph('DLG 高股息·低估值·高成长选股 20260911', TITLE))
story.append(Paragraph('数据日期：2026-09-11', S('sub', fontSize=9, alignment=TA_CENTER, textColor=colors.grey)))
story.append(Spacer(1, 0.3*cm))
story.append(HRFlowable(width='100%', thickness=2, color=HDR_COL))
story.append(Spacer(1, 0.3*cm))

# ── Section 1: 策略 ──────────────────────────────────────────────────────────
story.append(section_header('一、策略与数据'))
story.append(Spacer(1, 0.2*cm))

story.append(Paragraph('<b>选股逻辑</b>：三条主线取交集——能分红（股息率与现金流真实）× 便宜（PE/PB/PS 相对同行业不贵）× 还在成长（最新报告期净利/营收/扣非同比为正且加速）。', BODY))
story.append(Spacer(1, 0.1*cm))
story.append(Paragraph('<b>评分模型</b>：DLG = 0.30×股息 + 0.25×估值 + 0.30×成长 + 0.15×质量（均归一化到 0~100）', BODY))
story.append(Paragraph('<b>等级划分</b>：A ≥ 80｜B ≥ 70｜C ≥ 60', BOLD))
story.append(Spacer(1, 0.2*cm))

# Hard filters table
hf_data = [
    [th('硬门槛参数'), th('')],
    [td('股息率 3.0%~15.0%', TA_LEFT), td('净利同比 ≥ 10.0%', TA_LEFT)],
    [td('近3年分红未中断占比 ≥ 50%', TA_LEFT), td('扣非同比 ≥ 0.0%', TA_LEFT)],
    [td('PE(TTM) ≤ 30.0', TA_LEFT), td('ROE ≥ 6.0%', TA_LEFT)],
    [td('PB ≤ 8.0', TA_LEFT), td('毛利率 ≥ 10.0%', TA_LEFT)],
    [td('总市值 ≥ 80.0亿', TA_LEFT), td('资产负债率 ≤ 80.0%', TA_LEFT)],
    [td('成交额 ≥ 0.3亿', TA_LEFT), td('剔除 ST/退市/北交所/金融', TA_LEFT)],
]
hf_tbl = Table(hf_data, colWidths=[9.5*cm, 9.5*cm])
hf_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), HDR_COL),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('SPAN', (0,0), (-1,0)),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [ALT2, ALT1]),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ALIGN', (0,0), (-1,-1), 'LEFT'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('LEFTPADDING', (0,0), (-1,-1), 5),
    ('TOPPADDING', (0,0), (-1,-1), 2),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2),
]))
story.append(hf_tbl)
story.append(Spacer(1, 0.3*cm))

# ── Section 2: 漏斗 ─────────────────────────────────────────────────────────
story.append(section_header('二、筛选漏斗'))
story.append(Spacer(1, 0.2*cm))

funnel_cols = ['过滤条件', '剩余只数', '本步淘汰']
funnel_data = [
    [th(c) for c in funnel_cols],
    [td('基础池(当日有估值快照)', TA_LEFT), td('5,550', TA_CENTER), td('—', TA_CENTER)],
    [td('股息率 dv_ttm ≥ 3.0%', TA_LEFT), td('581', TA_CENTER), td('4,969', TA_CENTER)],
    [td('股息率 ≤ 15.0%', TA_LEFT), td('579', TA_CENTER), td('2', TA_CENTER)],
    [td('近3年分红未中断(占比≥50%)', TA_LEFT), td('579', TA_CENTER), td('0', TA_CENTER)],
    [td('0.0 < PE(TTM) ≤ 30.0', TA_LEFT), td('501', TA_CENTER), td('78', TA_CENTER)],
    [td('PB ≤ 8.0 且 > 0', TA_LEFT), td('500', TA_CENTER), td('1', TA_CENTER)],
    [td('总市值 ≥ 80.0亿', TA_LEFT), td('321', TA_CENTER), td('179', TA_CENTER)],
    [td('成交额 ≥ 0.3亿', TA_LEFT), td('316', TA_CENTER), td('5', TA_CENTER)],
    [td('剔除 ST/*ST/退市', TA_LEFT), td('316', TA_CENTER), td('0', TA_CENTER)],
    [td('剔除北交所', TA_LEFT), td('315', TA_CENTER), td('1', TA_CENTER)],
    [td('剔除金融行业', TA_LEFT), td('267', TA_CENTER), td('48', TA_CENTER)],
    [td('上市满 250 天', TA_LEFT), td('267', TA_CENTER), td('0', TA_CENTER)],
    [td('净利同比 ≥ 10.0%', TA_LEFT), td('73', TA_CENTER), td('194', TA_CENTER)],
    [td('扣非同比 ≥ 0.0%', TA_LEFT), td('67', TA_CENTER), td('6', TA_CENTER)],
    [td('ROE ≥ 6.0%', TA_LEFT), td('43', TA_CENTER), td('24', TA_CENTER)],
    [td('毛利率 ≥ 10.0%', TA_LEFT), td('42', TA_CENTER), td('1', TA_CENTER)],
    [td('资产负债率 ≤ 80.0%', TA_LEFT), td('42', TA_CENTER), td('0', TA_CENTER)],
    [td('已披露最新报告期(2026年最新)', TA_LEFT), td('42', TA_CENTER), td('0', TA_CENTER)],
    [td('<b>通过全部门槛 → 33 只</b>', TA_LEFT, 8, True),
     td('<b>A级2 / B级13 / C级18</b>', TA_CENTER, 8, True),
     td('', TA_CENTER, 8, True)],
]
ft = Table(funnel_data, colWidths=[10*cm, 4.5*cm, 4.5*cm])
ft.setStyle(tbl_style())
story.append(ft)
story.append(Spacer(1, 0.3*cm))

# ── Section 3: 入选结果 A/B/C 表 ─────────────────────────────────────────────
story.append(section_header('三、入选结果'))
story.append(Spacer(1, 0.15*cm))

# A级
def make_result_table(grade_label, grade_color, rows):
    cols = ['代码', '名称', '行业', 'DLG', '股息', '持久', '估值', '成长', '质量',
            '股息率%', 'PE', 'PB', '净利同比%', '扣非同比%', 'ROE%']
    cw = [2.3*cm, 1.8*cm, 1.5*cm, 1.0*cm, 1.0*cm, 1.0*cm, 1.0*cm, 1.0*cm, 1.0*cm,
          1.3*cm, 1.0*cm, 1.0*cm, 1.3*cm, 1.3*cm, 1.3*cm]
    header = [th(c, grade_color) for c in cols]
    data = [header]
    for r in rows:
        data.append([td(str(x), TA_CENTER, 7) for x in r])
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), grade_color),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [ALT2, ALT1]),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 2),
        ('RIGHTPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    return t

# A级数据 (代码, 名称, 行业, DLG, 股息, 持久, 估值, 成长, 质量, 股息率, PE, PB, 净利%, 扣非%, ROE)
a_rows = [
    ['002532.SZ','天山铝业','铝','84.0','78.8','57.2','83.9','92.6','77.7','6.36','8.9','1.91','100.4','107.4','13.5'],
    ['601339.SH','百隆东方','纺织','80.4','76.5','55.7','83.2','90.3','64.0','6.05','12.9','1.16','43.5','46.7','6.1'],
]
b_rows = [
    ['603444.SH','吉比特','互联网','77.6','68.3','50.1','56.7','97.9','90.4','5.20','12.1','4.46','69.3','67.8','18.9'],
    ['002170.SZ','芭田股份','农药化肥','76.9','79.7','57.8','72.8','75.5','80.7','6.39','10.5','2.89','32.2','24.4','15.9'],
    ['000612.SZ','焦作万方','铝','76.7','38.4','50.6','91.1','96.6','89.8','3.06','7.2','1.56','129.6','128.2','16.1'],
    ['002293.SZ','罗莱生活','纺织','75.9','89.2','79.7','65.9','79.8','58.1','6.50','15.4','2.19','34.4','34.9','6.0'],
    ['301219.SZ','腾远钴业','小金属','75.6','51.8','46.8','87.5','99.3','55.8','3.73','10.3','1.58','87.8','87.0','9.1'],
    ['600295.SH','鄂尔多斯','特种钢','74.8','74.1','62.6','77.8','79.9','61.3','5.23','13.4','1.75','43.2','38.3','6.6'],
    ['600989.SH','宝丰能源','化工原料','73.9','52.2','58.0','64.5','96.0','89.1','3.48','11.4','3.18','70.1','65.1','18.8'],
    ['002345.SZ','潮宏基','服饰','73.4','66.8','53.3','73.5','77.2','78.7','4.95','13.7','2.08','27.4','27.6','11.0'],
    ['002128.SZ','电投能源','煤炭开采','72.7','45.6','48.8','78.3','91.3','80.6','3.42','11.9','1.61','49.1','48.2','10.6'],
    ['600323.SH','瀚蓝环境','环境保护','71.7','53.3','58.2','85.7','85.5','57.8','3.55','10.9','1.59','24.8','29.3','8.1'],
    ['000858.SZ','五粮液','白酒','71.6','86.0','66.9','44.7','86.5','57.9','7.39','20.7','2.29','89.3','84.0','7.3'],
    ['603317.SH','天味食品','食品','71.2','64.5','61.0','47.7','95.5','75.4','4.34','17.6','3.14','100.4','118.2','8.7'],
    ['000685.SZ','中山公用','水务','70.1','42.8','38.2','89.6','84.7','63.3','3.45','6.2','0.84','104.0','106.5','7.7'],
]
c_rows = [
    ['600210.SH','紫江企业','广告包装','69.2','77.4','64.6','92.8','51.3','49.3','5.60','8.6','1.41','15.1','6.5','8.1'],
    ['601899.SH','紫金矿业','铜','67.2','39.1','47.9','60.1','91.6','86.4','3.15','12.7','4.25','68.2','75.9','20.0'],
    ['000848.SZ','承德露露','软饮料','67.2','82.5','68.7','64.0','58.8','58.5','6.17','12.4','2.56','15.3','14.5','8.9'],
    ['600938.SH','中国海油','石油开采','66.3','50.6','63.7','72.1','69.9','80.6','3.38','11.6','1.88','23.4','23.6','10.3'],
    ['603025.SH','大豪科技','纺织机械','64.9','50.6','56.0','41.0','88.2','86.7','3.46','17.2','5.26','33.4','33.5','19.4'],
    ['300773.SZ','拉卡拉','软件服务','64.6','35.9','34.1','69.0','92.8','58.4','3.22','10.2','3.54','191.7','50.3','15.0'],
    ['002415.SZ','海康威视','IT设备','64.3','61.0','63.9','42.9','85.2','65.0','3.91','18.5','3.61','39.6','40.5','9.4'],
    ['603233.SH','大参林','医药商业','63.4','67.8','60.7','62.4','63.1','57.2','4.66','14.8','2.67','16.1','18.1','12.3'],
    ['002216.SZ','三全食品','食品','63.3','69.1','57.3','56.4','64.4','61.3','4.96','17.0','2.34','16.5','14.4','8.1'],
    ['601225.SH','陕西煤业','煤炭开采','63.2','46.8','39.0','73.4','65.3','74.6','3.64','12.4','2.50','47.6','34.8','11.4'],
    ['300628.SZ','亿联网络','通信设备','63.1','64.5','56.3','34.7','74.7','84.2','4.46','17.7','5.55','22.9','25.0','16.4'],
    ['300573.SZ','兴齐眼药','化学制药','62.8','44.8','51.9','39.7','85.9','90.9','3.30','16.0','5.86','28.5','30.4','20.8'],
    ['601886.SH','江河集团','装修装饰','62.7','58.2','54.8','60.7','80.5','39.5','3.95','19.9','1.98','34.3','34.1','6.1'],
    ['000408.SZ','藏格矿业','农药化肥','62.6','40.8','35.1','30.7','96.0','92.3','3.43','20.0','6.63','102.1','104.9','21.8'],
    ['002001.SZ','新和成','化学制药','62.0','54.4','51.3','65.2','59.0','78.1','3.77','11.4','2.34','11.3','6.5','11.9'],
    ['002083.SZ','孚日股份','纺织','61.9','30.4','33.1','69.6','87.5','61.1','3.01','15.4','1.87','64.0','57.7','8.4'],
    ['600415.SH','小商品城','商品城','61.7','61.9','58.1','52.4','73.1','54.0','4.17','14.6','2.97','17.0','9.3','8.8'],
    ['002603.SZ','以岭药业','中成药','61.4','72.0','55.4','44.6','57.9','75.1','5.27','18.5','2.33','13.3','14.0','6.9'],
]

story.append(Paragraph('<b>A 级候选（2 只）</b>', H2))
story.append(make_result_table('A 级', colors.HexColor('#e67e22'), a_rows))
story.append(Spacer(1, 0.2*cm))

# B级摘要（精简）
story.append(Paragraph('<b>B 级候选（13 只）</b>', H2))
story.append(make_result_table('B 级', colors.HexColor('#27ae60'), b_rows))
story.append(Spacer(1, 0.2*cm))

story.append(Paragraph('<b>C 级候选（18 只）</b>', H2))
story.append(make_result_table('C 级', colors.HexColor('#7f8c8d'), c_rows))
story.append(PageBreak())

# ── Section 4: 重点关注 ───────────────────────────────────────────────────────
story.append(section_header('四、重点关注（前 12 只）'))
story.append(Spacer(1, 0.2*cm))

stocks = [
    ('002532.SZ 天山铝业', 'A', '84.0', '铝',
     '股息率6.36%·3年分红未中断·PE8.9·PB1.91·净利+100.4%·扣非+107.4%·ROE13.5%',
     '当期股息率＞3年均值1.5倍（疑一次性分红）'),
    ('601339.SH 百隆东方', 'A', '80.4', '纺织',
     '股息率6.05%·3年分红未中断·PE12.9·PB1.16·净利+43.5%·扣非+46.7%',
     '—'),
    ('603444.SH 吉比特', 'B', '77.6', '互联网',
     '股息率5.20%·PE12.1·净利+69.3%·扣非+67.8%·ROE18.9%·毛利率93.7%',
     '—'),
    ('002170.SZ 芭田股份', 'B', '76.9', '农药化肥',
     '股息率6.39%·PE10.5·净利+32.2%·扣非+24.4%·ROE15.9%·分红趋势3.30',
     '当期股息率＞3年均值1.5倍；经营现金流同比下滑'),
    ('000612.SZ 焦作万方', 'B', '76.7', '铝',
     'PE7.2（极低）·PB1.56·净利+129.6%·扣非+128.2%·ROE16.1%',
     '当期股息率＞3年均值1.5倍'),
    ('002293.SZ 罗莱生活', 'B', '75.9', '纺织',
     '股息率6.50%·PE15.4·净利+34.4%·ROE6.0%（接近边界）',
     '经营现金流同比下滑'),
    ('301219.SZ 腾远钴业', 'B', '75.6', '小金属',
     '净利+87.8%·扣非+87.0%·营收+72.4%（超强成长）·PE10.3·PB1.58',
     '经营现金流同比下滑'),
    ('600295.SH 鄂尔多斯', 'B', '74.8', '特种钢',
     '股息率5.23%·净利+43.2%·扣非+38.3%·ROE6.6%·毛利率24.1%',
     '分红水平同比下滑；近4期有负增长'),
    ('600989.SH 宝丰能源', 'B', '73.9', '化工原料',
     '净利+70.1%·扣非+65.1%·ROE18.8%·毛利率43.6%·分红趋势1.79',
     '当期股息率＞3年均值1.5倍（疑一次性）'),
    ('002345.SZ 潮宏基', 'B', '73.4', '服饰',
     '股息率4.95%·PE13.7·净利+27.4%·ROE11.0%·毛利率27.4%',
     '—'),
    ('002128.SZ 电投能源', 'B', '72.7', '煤炭开采',
     'PE11.9·净利+49.1%·扣非+48.2%·ROE10.6%·毛利率43.9%（高安全边际）',
     '—'),
    ('600323.SH 瀚蓝环境', 'B', '71.7', '环境保护',
     'PE10.9·净利+24.8%·扣非+29.3%·ROE8.1%·营收+34.2%',
     '负债率69.4%（偏高）'),
]

grade_colors = {'A': colors.HexColor('#e67e22'), 'B': colors.HexColor('#27ae60'), 'C': colors.HexColor('#7f8c8d')}
for code_name, grade, dlg, sector, metrics, risk in stocks:
    gc = grade_colors.get(grade, HDR_COL)
    # Title row
    title_text = f'<b>【{grade}级】</b> {code_name}  DLG={dlg}  行业：{sector}'
    title_p = Paragraph(title_text, S('stitle', fontSize=9, fontName='CN-Bold',
        textColor=colors.white, backColor=gc, alignment=TA_LEFT))
    risk_p = Paragraph(f'<font color="#c0392b">⚠ 风险：{risk}</font>' if risk != '—' else '',
        S('srisk', fontSize=8, textColor=colors.HexColor('#c0392b')))
    metric_p = Paragraph(f'<b>核心指标：</b>{metrics}', SMALL)
    block = KeepTogether([
        title_p, Spacer(1, 0.08*cm),
        metric_p,
        risk_p if risk != '—' else Paragraph('', SMALL),
        HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc')),
        Spacer(1, 0.1*cm),
    ])
    story.append(block)

story.append(PageBreak())

# ── Section 5: 行业分布 ───────────────────────────────────────────────────────
story.append(section_header('五、行业分布'))
story.append(Spacer(1, 0.2*cm))

ind_cols = ['行业', '只数', '平均DLG']
ind_data = [
    [th(c) for c in ind_cols],
    [td('纺织', TA_LEFT), td('3', TA_CENTER), td('72.7', TA_CENTER)],
    [td('铝', TA_LEFT), td('2', TA_CENTER), td('80.3', TA_CENTER)],
    [td('化学制药', TA_LEFT), td('2', TA_CENTER), td('62.4', TA_CENTER)],
    [td('食品', TA_LEFT), td('2', TA_CENTER), td('67.2', TA_CENTER)],
    [td('煤炭开采', TA_LEFT), td('2', TA_CENTER), td('68.0', TA_CENTER)],
    [td('农药化肥', TA_LEFT), td('2', TA_CENTER), td('69.8', TA_CENTER)],
    [td('中成药', TA_LEFT), td('1', TA_CENTER), td('61.4', TA_CENTER)],
    [td('IT设备', TA_LEFT), td('1', TA_CENTER), td('64.3', TA_CENTER)],
    [td('互联网', TA_LEFT), td('1', TA_CENTER), td('77.6', TA_CENTER)],
    [td('化工原料', TA_LEFT), td('1', TA_CENTER), td('73.9', TA_CENTER)],
    [td('广告包装', TA_LEFT), td('1', TA_CENTER), td('69.2', TA_CENTER)],
    [td('小金属', TA_LEFT), td('1', TA_CENTER), td('75.6', TA_CENTER)],
    [td('商品城', TA_LEFT), td('1', TA_CENTER), td('61.7', TA_CENTER)],
    [td('医药商业', TA_LEFT), td('1', TA_CENTER), td('63.4', TA_CENTER)],
    [td('特种钢', TA_LEFT), td('1', TA_CENTER), td('74.8', TA_CENTER)],
]
ind_tbl = Table(ind_data, colWidths=[10*cm, 4.5*cm, 4.5*cm])
ind_tbl.setStyle(tbl_style(colors.HexColor('#2c5f9e')))
story.append(ind_tbl)
story.append(Spacer(1, 0.3*cm))

# ── Section 6: 风险提示 ───────────────────────────────────────────────────────
story.append(section_header('六、使用说明与风险提示'))
story.append(Spacer(1, 0.2*cm))

tips = [
    ('<b>基本面筛选不含择时</b>：本策略是横向基本面筛选，不含买点信号；建议结合趋势/量价模块二次确认后再建仓。'),
    ('<b>高股息需核实来源</b>：一次性特别股息、资产处置收益推高的股息率不可持续（报告已自动标记）。'),
    ('<b>低估值可能是"价值陷阱"</b>：需确认行业景气与业绩下滑是否已止住（本策略已要求扣非同比不为负）。'),
    ('<b>单季加速项</b>：来自财报拆分，若为 Proxy 口径（q2_proxy=True）则参考权重应降低。'),
    ('<b>数据来源</b>：均来自本地缓存，使用前请确认缓存已更新至最新交易日。'),
]
for tip in tips:
    story.append(Paragraph(f'• {tip}', BODY))
    story.append(Spacer(1, 0.1*cm))

story.append(Spacer(1, 0.3*cm))
story.append(HRFlowable(width='100%', thickness=1, color=HDR_COL))
story.append(Spacer(1, 0.15*cm))

# Footer
footer_data = [[
    Paragraph('DLG 高股息·低估值·高成长选股 | 数据日期 2026-09-11 | AI 辅助，仅供参考，不构成投资建议', FOOT),
    Paragraph('QClaw', S('fc', fontSize=7, textColor=colors.grey, alignment=TA_RIGHT)),
]]
ft = Table(footer_data, colWidths=[14*cm, 5*cm])
ft.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
story.append(ft)

# ── Build ──────────────────────────────────────────────────────────────────────
doc.build(story)
print(f'Done: {OUT}')
import os
print(f'Size: {os.path.getsize(OUT)/1024:.0f} KB')
