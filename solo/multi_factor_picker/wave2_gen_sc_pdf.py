# -*- coding: utf-8 -*-
"""二波行情双创板专项报告 PDF 生成"""
import os, sys
sys.path.insert(0, r'D:\mystock')
OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor

W, H = A4
MARGIN = 15 * mm

# Register Chinese font
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
    pdfmetrics.registerFont(TTFont('SimSun', 'C:/Windows/Fonts/simsun.ttc'))
    FONT = 'SimHei'
    FONT_BODY = 'SimSun'
except:
    FONT = 'Helvetica'
    FONT_BODY = 'Helvetica'

doc = SimpleDocTemplate(
    os.path.join(OUT_DIR, '二波行情双创板专项研究报告.pdf'),
    pagesize=A4,
    leftMargin=MARGIN, rightMargin=MARGIN,
    topMargin=MARGIN, bottomMargin=MARGIN
)

styles = getSampleStyleSheet()
title_style = styles['Title']
title_style.fontName = FONT
title_style.fontSize = 16
title_style.leading = 20

h1_style = styles['Heading1']
h1_style.fontName = FONT
h1_style.fontSize = 13
h1_style.leading = 16
h1_style.textColor = HexColor('#1a3c6e')

h2_style = styles['Heading2']
h2_style.fontName = FONT
h2_style.fontSize = 11
h2_style.leading = 14

body_style = styles['Normal']
body_style.fontName = FONT_BODY
body_style.fontSize = 9
body_style.leading = 12

elements = []

# ── Header ──────────────────────────────────────────────
elements.append(Paragraph('二波行情双创板（创业板+科创板）量化研究报告', title_style))
elements.append(Spacer(1, 4*mm))
elements.append(Paragraph('数据范围：创业板（300xxx）+ 科创板（688xxx）全量上市股票 | 回测区间：2024-01-01 ~ 2026-06-20', body_style))
elements.append(Paragraph('样本：1,539只股票 · 52,949个拉升案例 | 对比基准：沪深300（312只 · 16,828案例）', body_style))
elements.append(Spacer(1, 3*mm))
elements.append(HRFlowable(width='100%', thickness=1.5, color=HexColor('#1a3c6e')))
elements.append(Spacer(1, 3*mm))

# ── 1. 核心发现 ──────────────────────────────────────────
elements.append(Paragraph('一、核心发现', h1_style))
elements.append(Spacer(1, 2*mm))

findings = [
    ['#', '发现', '双创板数据', '沪深300数据', '解读'],
    ['①', '强势横盘成功率', '84.3%（4,902样本）', '98.6%（—样本）', '双创板波动大，横盘难持久'],
    ['②', '深度回调成功率', '92.0%（8,774样本）', '86.2%（—样本）', '双创跌得更深→反弹更有力'],
    ['③', 'V型急跌成功率', '91.8%（10,682样本）', '94.9%（—样本）', '高波动市场V型更常见'],
    ['④', '放量回调成功率', '—（样本不足）', '90.4%（—样本）', '双创板量能数据不稳定'],
    ['⑤', '最强单一信号', 'V型+MACD金叉+MA20上方', '强势横盘+RSI<50+缩量', '双创板需MACD确认横盘'],
]
f_table = Table(findings, colWidths=[12*mm, 30*mm, 40*mm, 40*mm, 40*mm])
f_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), FONT_BODY),
    ('FONTSIZE', (0,0), (-1,0), 8), ('FONTSIZE', (0,1), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#1a3c6e')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [HexColor('#f0f4fa'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
    ('WORDWRAP', (0,0), (-1,-1), True),
]))
elements.append(f_table)
elements.append(Spacer(1, 4*mm))

# ── 2. 双创板形态统计表 ─────────────────────────────────
elements.append(Paragraph('二、双创板六种形态统计（52,949案例）', h1_style))
elements.append(Spacer(1, 2*mm))

sc_data = [
    ['形态', '样本', '成功率', '二波均涨', '60日最大', '平均回调', '调整天数', '盈亏比', 'MA20上方'],
    ['深度回调', '8,774', '92.0%', '+28.0%', '+61.0%', '-30.0%', '31.6天', '12.2x', '0%'],
    ['V型急跌', '10,682', '91.8%', '+34.0%', '+81.0%', '-18.0%', '5.6天', '16.1x', '58%'],
    ['三角收敛', '16,528', '86.8%', '+30.0%', '+71.0%', '-30.0%', '35.0天', '14.3x', '0%'],
    ['强势横盘', '4,902', '84.3%', '+33.0%', '+83.0%', '-6.0%', '5.3天', '16.6x', '77%'],
    ['缩量回调', '12,063', '82.8%', '+26.0%', '+72.0%', '-15.0%', '27.9天', '14.3x', '0%'],
    ['放量回调', '—', '—', '—', '—', '—', '—', '—', '—'],
]
sc_table = Table(sc_data, colWidths=[24*mm, 18*mm, 18*mm, 22*mm, 24*mm, 22*mm, 22*mm, 18*mm, 20*mm])
sc_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), FONT),
    ('FONTNAME', (0,1), (-1,-1), FONT_BODY),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#2e5fa3')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('BACKGROUND', (0,1), (-1,1), HexColor('#c8e6c9')),
    ('BACKGROUND', (0,2), (-1,2), HexColor('#a5d6a7')),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
    ('ROWBACKGROUNDS', (0,3), (-1,-1), [HexColor('#f5f5f5'), colors.white]),
]))
elements.append(sc_table)
elements.append(Spacer(1, 4*mm))

# ── 3. 创业板 vs 科创板 ─────────────────────────────────
elements.append(Paragraph('三、创业板 vs 科创板对比', h1_style))
elements.append(Spacer(1, 2*mm))

board_data = [
    ['形态', '创业板(31069样本)', '科创板(21880样本)', '差异'],
    ['深度回调', '92.0%  均涨30%', '91.8%  均涨30%', '基本持平'],
    ['V型急跌', '88.9%  均涨30%', '95.9%  均涨30%', '科创板+7.0pp ⭐'],
    ['三角收敛', '85.4%  均涨30%', '89.1%  均涨30%', '科创板+3.7pp'],
    ['强势横盘', '83.6%  均涨40%', '84.9%  均涨30%', '创业板均涨更高'],
    ['缩量回调', '82.4%  均涨30%', '83.4%  均涨30%', '基本持平'],
]
board_table = Table(board_data, colWidths=[24*mm, 48*mm, 48*mm, 40*mm])
board_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), FONT),
    ('FONTNAME', (0,1), (-1,-1), FONT_BODY),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#37474f')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('BACKGROUND', (0,2), (-1,2), HexColor('#fff9c4')),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
    ('ROWBACKGROUNDS', (0,3), (-1,-1), [HexColor('#eceff1'), colors.white]),
]))
elements.append(board_table)
elements.append(Spacer(1, 2*mm))
elements.append(Paragraph('⭐ 科创板V型急跌成功率95.9%，显著高于创业板的88.9%。科创板高估值高波动特性使急跌后反弹更强劲。', body_style))
elements.append(Spacer(1, 4*mm))

# ── 4. 最优入场条件 ─────────────────────────────────────
elements.append(Paragraph('四、双创板最优入场条件（TOP10组合）', h1_style))
elements.append(Spacer(1, 2*mm))

combo_data = [
    ['#', '形态', '入场条件', '样本', '成功率', '平均涨幅', '盈亏比'],
    ['1', '强势横盘', 'CCI<-100 + MA20上方', '18', '100.0%', '+50.0%', '13.7x'],
    ['2', 'V型急跌', 'MACD金叉 + MA20上方', '5,815', '94.1%', '+34.0%', '14.3x'],
    ['3', 'V型急跌', 'MACD金叉', '6,377', '93.9%', '+34.0%', '15.0x'],
    ['4', 'V型急跌', 'RSI<40 + MA20上方', '124', '93.5%', '+34.0%', '18.0x'],
    ['5', 'V型急跌', 'CCI<-100 + MA60上方', '952', '93.2%', '+37.0%', '17.9x'],
    ['6', '深度回调', 'RSI<30', '8,465', '92.4%', '+29.0%', '12.2x'],
    ['7', 'V型急跌', 'RSI<30', '1,394', '92.0%', '+33.0%', '16.0x'],
    ['8', '深度回调', '量能比<0.8 + RSI<50', '8,726', '91.9%', '+28.0%', '12.2x'],
    ['9', 'V型急跌', 'RSI<35 + MA60上方', '1,325', '91.8%', '+36.0%', '17.8x'],
    ['10', 'V型急跌', 'MA20上方 + RSI<50', '3,074', '91.8%', '+33.0%', '14.6x'],
]
combo_table = Table(combo_data, colWidths=[10*mm, 22*mm, 52*mm, 18*mm, 20*mm, 22*mm, 18*mm])
combo_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), FONT),
    ('FONTNAME', (0,1), (-1,-1), FONT_BODY),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#1a3c6e')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('BACKGROUND', (0,1), (-1,2), HexColor('#e8f5e9')),
    ('BACKGROUND', (0,3), (-1,5), HexColor('#c8e6c9')),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
]))
elements.append(combo_table)
elements.append(Spacer(1, 4*mm))

# ── 5. 与沪深300对比结论 ─────────────────────────────────
elements.append(Paragraph('五、双创板 vs 沪深300 策略差异', h1_style))
elements.append(Spacer(1, 2*mm))

compare_data = [
    ['对比维度', '沪深300结论', '双创板结论', '策略建议'],
    ['首选形态', '强势横盘（98.6%）', 'V型急跌（91.8%）', '双创板优先关注V型'],
    ['最强信号', 'RSI<50 + 缩量 + MA20', 'MACD金叉 + MA20上方', '双创板必须MACD确认'],
    ['深度回调', '86.2%（中等）', '92.0%（优秀）⭐', '双创板深度回调更安全'],
    ['强势横盘', '98.6%（极强）', '84.3%（较弱）', '双创板横盘不可靠！'],
    ['止损标准', '-5%', '-5%', '一致'],
    ['目标参考', '20日涨幅', '20日涨幅', '双创板60日均涨更高（+80%）'],
    ['适用板块', '主板价值/成长', '创业板+科创板', '科创板V型+95.9%最强'],
]
cmp_table = Table(compare_data, colWidths=[28*mm, 44*mm, 44*mm, 44*mm])
cmp_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), FONT),
    ('FONTNAME', (0,1), (-1,-1), FONT_BODY),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#37474f')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('BACKGROUND', (0,1), (-1,3), HexColor('#fff3e0')),
    ('BACKGROUND', (0,4), (-1,-1), HexColor('#f3e5f5')),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
    ('WORDWRAP', (0,0), (-1,-1), True),
]))
elements.append(cmp_table)
elements.append(Spacer(1, 4*mm))

# ── 6. 操作要点 ──────────────────────────────────────────
elements.append(Paragraph('六、双创板二波行情操作要点', h1_style))
elements.append(Spacer(1, 2*mm))

tips = [
    ['优先级', '形态', '入场条件', '止损', '目标', '信心度'],
    ['⭐⭐⭐', 'V型急跌', 'MACD金叉 + MA20上方', '-5%', '+20~35%', '94.1%（5,815样本）'],
    ['⭐⭐⭐', 'V型急跌', 'CCI<-100 + MA60上方', '-5%', '+25~37%', '93.2%（952样本）'],
    ['⭐⭐', '深度回调', 'RSI<30（超卖）', '-5%', '+20~30%', '92.4%（8,465样本）'],
    ['⭐⭐', '深度回调', '量能比<0.8 + RSI<50', '-5%', '+20~30%', '91.9%（8,726样本）'],
    ['⭐⭐', 'V型急跌', 'RSI<35 + MA60上方', '-5%', '+25~36%', '91.8%（1,325样本）'],
    ['⭐', '强势横盘', 'CCI<-100 + MA20上方', '-3%', '+30~50%', '100%（18样本）'],
    ['⭐', '三角收敛', 'RSI<30', '-5%', '+20~30%', '88.6%（14,350样本）'],
]
tips_table = Table(tips, colWidths=[16*mm, 22*mm, 50*mm, 16*mm, 22*mm, 36*mm])
tips_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), FONT),
    ('FONTNAME', (0,1), (-1,-1), FONT_BODY),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), HexColor('#b71c1c')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('BACKGROUND', (0,1), (-1,3), HexColor('#ffebee')),
    ('BACKGROUND', (0,4), (-1,5), HexColor('#fff8e1')),
    ('BACKGROUND', (0,6), (-1,7), HexColor('#e8f5e9')),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
    ('WORDWRAP', (0,0), (-1,-1), True),
]))
elements.append(tips_table)
elements.append(Spacer(1, 4*mm))

# ── Footer ──────────────────────────────────────────────
elements.append(HRFlowable(width='100%', thickness=1, color=HexColor('#999999')))
elements.append(Spacer(1, 2*mm))
from reportlab.lib.styles import ParagraphStyle
footer_style = ParagraphStyle('footer', fontName=FONT_BODY, fontSize=7, textColor=HexColor('#888888'))
import datetime as dt
elements.append(Paragraph(f'生成时间：{dt.datetime.now().strftime("%Y-%m-%d %H:%M")} | 数据来源：Tushare | 仅供参考，不构成投资建议', footer_style))

doc.build(elements)
print('PDF生成成功!')
