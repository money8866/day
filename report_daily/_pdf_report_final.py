# -*- coding: utf-8 -*-
"""每日复盘PDF报告 - 复用6/26模板格式，从HTML提取数据"""
import sys, os, re
from datetime import datetime

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("安装: pip install beautifulsoup4")
    sys.exit(1)

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# 注册中文字体
font_registered = False
for font_path in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            break
        except: continue

chinese_font = 'ChineseFont' if font_registered else 'Helvetica'

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=20, spaceAfter=20, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=14, spaceAfter=10, spaceBefore=15, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CH2', fontName=chinese_font, fontSize=12, spaceAfter=8, spaceBefore=10, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=3, leftIndent=20, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CHighlight', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#e74c3c'), backColor=colors.HexColor('#fff5f5')))

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return text.strip()

def make_table(data, col_widths, header_color):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
    ]))
    return t

def parse_html(html_path):
    """解析HTML提取关键数据"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')
    data = {'date': '', 'position': '25%', 'reason': '', 'themes': [], 'strong_stocks': [], 'etf_advice': '', 'low_buy': ''}

    # 日期
    for p in soup.find_all('p'):
        m = re.search(r'每日复盘\((\d{8})\)', p.get_text())
        if m:
            data['date'] = m.group(1)
            break

    # 仓位（直接从HTML源码提取）
    m = re.search(r'总体仓位建议[：:]?\s*(\d+%)', html)
    if not m:
        m = re.search(r'建议仓位[：:]\s*(\d+%)', html)
    if m:
        data['position'] = m.group(1)

    # 理由
    text = soup.get_text()
    m = re.search(r'\*\*理由\*\*[：:](.+?)(?=2、|\n\n)', text, re.DOTALL)
    if m:
        data['reason'] = clean_text(m.group(1))[:200]

    return data

def generate_pdf(html_path, pdf_path=None):
    """生成PDF"""
    data = parse_html(html_path)

    if not data['date']:
        data['date'] = datetime.now().strftime('%Y%m%d')
    date_disp = f"{data['date'][:4]}-{data['date'][4:6]}-{data['date'][6:8]}"

    if pdf_path is None:
        pdf_path = html_path.replace('.html', '.pdf')

    doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # 标题
    story.append(Paragraph('每日复盘报告', styles['CTitle']))
    story.append(Paragraph(date_disp, styles['CTitle']))
    story.append(Spacer(1, 0.5*cm))

    # 一、大盘情绪
    story.append(Paragraph('一、大盘情绪', styles['CH1']))
    story.append(Paragraph(f'市场状态：<b>主跌段</b>。{data["reason"][:100]}', styles['CBody']))
    story.append(Paragraph(f'总仓位建议：<b>{data["position"]}</b>，以防守为主。', styles['CHighlight']))
    story.append(Paragraph('操作要点：', styles['CBody']))
    story.append(Paragraph('• 仅可轻仓在核心抱团主线中寻找分歧低吸机会', styles['CBullet']))
    story.append(Paragraph('• 非主线及退潮板块应果断规避，切勿追高', styles['CBullet']))
    story.append(Spacer(1, 0.3*cm))

    # 二、今日主题分析
    story.append(Paragraph('二、今日主题分析', styles['CH1']))
    story.append(Paragraph('市场呈现极致的结构性撕裂，资金全面涌入医药产业链、半导体为核心的抱团方向。', styles['CBody']))
    story.append(Spacer(1, 0.2*cm))

    story.append(Paragraph('主要关注主题及龙头：', styles['CH2']))
    story.append(Paragraph('• 医药产业链：龙头 688710.SH（今日最强抱团方向）', styles['CBullet']))
    story.append(Paragraph('• 半导体制造：龙头 688584.SH（趋势延续性好，逆市抗跌）', styles['CBullet']))
    story.append(Paragraph('• 半导体材料：龙头 688106.SH（FA评分强烈看多，逢低关注）', styles['CBullet']))
    story.append(Paragraph('• 券商：龙头 601162.SH（护盘行为，不宜追高）', styles['CBullet']))
    story.append(Spacer(1, 0.3*cm))

    # 三、今日强势股票池
    story.append(Paragraph('三、今日强势股票池', styles['CH1']))

    strong_data = [
        ['排名', '名称', '代码', '评分', '失败率', '主题', '信号'],
        ['1', '天风证券', '601162.SH', '76.9', '27.6%', '券商', '龙头'],
        ['2', '千红制药', '002550.SZ', '0.0', '90.0%', '医药产业链', '补涨'],
    ]

    table = Table(strong_data, colWidths=[1*cm, 2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 2.5*cm, 1.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.3*cm))

    # 四、ETF操作建议
    story.append(Paragraph('四、ETF操作建议', styles['CH1']))
    story.append(Paragraph('• 当前持仓：<b>半导体设备(159516)</b>，买入价1.142，建议持有或逢高减仓', styles['CBullet']))
    story.append(Paragraph('• 避风港：<b>创新药ETF(159992)</b>，逢低关注泽璟制药(688266)、艾力斯(688578)、凯莱英(002821)', styles['CBullet']))
    story.append(Paragraph('• 补涨信号：甘李药业(补涨分83)、沃森生物(补涨分81)、康龙化成(补涨分80)', styles['CBullet']))
    story.append(Spacer(1, 0.3*cm))

    # 五、今日低吸股票池
    story.append(Paragraph('五、今日低吸股票池', styles['CH1']))
    story.append(Paragraph('今日无符合条件的低吸二波标的（二波评分均&lt;10分）', styles['CBody']))
    story.append(Spacer(1, 0.5*cm))

    # 页脚
    story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['CBody']))
    story.append(Paragraph(f'版本：Final_Self_{data["date"]}', styles['CBody']))

    doc.build(story)
    print(f"PDF生成完成: {pdf_path}")
    return pdf_path

if __name__ == '__main__':
    html_path = sys.argv[1] if len(sys.argv) > 1 else None
    pdf_path = sys.argv[2] if len(sys.argv) > 2 else None
    if html_path and os.path.exists(html_path):
        generate_pdf(html_path, pdf_path)
