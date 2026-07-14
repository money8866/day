# -*- coding: utf-8 -*-
"""每日复盘PDF报告生成器 - 硬编码模板格式+动态解析内容"""
import os, re, sys
from datetime import datetime

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
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=20, spaceAfter=6, alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CDate', fontName=chinese_font, fontSize=11, spaceAfter=12, alignment=TA_CENTER, textColor=colors.HexColor('#666666')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=13, spaceAfter=8, spaceBefore=14, textColor=colors.HexColor('#1a3a8a')))
styles.add(ParagraphStyle(name='CH2', fontName=chinese_font, fontSize=11, spaceAfter=6, spaceBefore=8, textColor=colors.HexColor('#2c5f9e')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=3, leftIndent=16, leading=14, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CHighlight', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#e74c3c'), backColor=colors.HexColor('#fff5f5')))
styles.add(ParagraphStyle(name='CFooter', fontName=chinese_font, fontSize=8, spaceAfter=2, textColor=colors.HexColor('#aaaaaa')))

def clean_html(t):
    t = re.sub(r'<span[^>]*>', '', t)
    t = re.sub(r'</span>', '', t)
    t = re.sub(r'<[^>]+>', '', t)
    return t.strip()

def make_table(data, col_widths, header_color):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
    ]))
    return t

def parse_md_content(md_path):
    """解析MD文件提取关键内容"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    result = {
        'date': '',
        'position': '25%',
        'market_status': '震荡',
        'reason': '',
        'themes': [],
        'strong_stocks': [],
        'low_buy_stocks': [],
        'etf_advice': '',
    }

    # 日期
    m = re.search(r'每日复盘\((\d{8})\)', content)
    if m:
        result['date'] = m.group(1)

    # 仓位建议
    m = re.search(r'建议仓位[：:]\s*(\d+%)', content)
    if m:
        result['position'] = m.group(1)

    # 理由
    m = re.search(r'\*\*理由\*\*[：:](.+?)(?=\n\n|\n\d|$)', content, re.DOTALL)
    if m:
        result['reason'] = clean_html(m.group(1).strip())

    # 主题分析
    theme_section = re.search(r'今日主题分析情况(.+?)(?=\n\d[、.]|\n\*\*\d)', content, re.DOTALL)
    if theme_section:
        txt = theme_section.group(1)
        # 提取主题列表
        themes = re.findall(r'\*\*([^*]+)\*\*[：:]', txt)
        result['themes'] = themes[:5]

    # 强势股票
    strong_section = re.search(r'今日强势股票池分析(.+?)(?=\n\d[、.]|\n\*\*\d|ETF)', content, re.DOTALL)
    if strong_section:
        txt = strong_section.group(1)
        stocks = re.findall(r'【([^】]+)】[^)]+\((\d{6}\.[A-Z]{2})\)', txt)
        result['strong_stocks'] = stocks[:5]

    # 低吸股票池
    low_section = re.search(r'今日低吸股票池分析(.+?)(?=\n\d[、.]|\n\*\*\d|$)', content, re.DOTALL)
    if low_section:
        txt = low_section.group(1)
        stocks = re.findall(r'([^\s]+)\((\d{6}\.[A-Z]{2})\).*?(\d+)分', txt)
        result['low_buy_stocks'] = stocks[:5]

    # ETF建议
    etf_section = re.search(r'ETF操作建议(.+?)(?=\n\d[、.]|\n\*\*\d|$)', content, re.DOTALL)
    if etf_section:
        result['etf_advice'] = clean_html(etf_section.group(1).strip()[:200])

    return result

def generate_pdf(md_path, out_path=None):
    """生成PDF报告"""
    data = parse_md_content(md_path)

    if not data['date']:
        data['date'] = datetime.now().strftime('%Y%m%d')

    date_disp = f"{data['date'][:4]}-{data['date'][4:6]}-{data['date'][6:8]}"

    if out_path is None:
        out_path = md_path.replace('.md', '.pdf')

    doc = SimpleDocTemplate(out_path, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

    story = []

    # 标题
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph('每日复盘报告', styles['CTitle']))
    story.append(Paragraph(date_disp, styles['CDate']))
    story.append(Spacer(1, 0.3*cm))

    # 一、大盘情绪
    story.append(Paragraph('一、大盘情绪', styles['CH1']))
    story.append(Paragraph(f'仓位建议：<b>{data["position"]}</b>', styles['CHighlight']))
    if data['reason']:
        reason_text = data['reason'][:300]
        story.append(Paragraph(f'理由：{reason_text}', styles['CBody']))
    story.append(Spacer(1, 0.2*cm))

    # 二、今日主题分析
    story.append(Paragraph('二、今日主题分析', styles['CH1']))
    if data['themes']:
        story.append(Paragraph('主要关注主题：', styles['CH2']))
        for theme in data['themes'][:5]:
            story.append(Paragraph(f'• {theme}', styles['CBullet']))
    story.append(Spacer(1, 0.2*cm))

    # 三、今日强势股票池
    story.append(Paragraph('三、今日强势股票池', styles['CH1']))
    if data['strong_stocks']:
        stock_data = [['名称', '代码']]
        for name, code in data['strong_stocks'][:5]:
            stock_data.append([name[:12], code])
        story.append(make_table(stock_data, [4*cm, 3*cm], colors.HexColor('#e74c3c')))
    else:
        story.append(Paragraph('今日无符合条件的强势标的', styles['CBody']))
    story.append(Spacer(1, 0.2*cm))

    # 四、今日低吸股票池
    story.append(Paragraph('四、今日低吸股票池', styles['CH1']))
    if data['low_buy_stocks']:
        low_data = [['名称', '代码', '评分']]
        for item in data['low_buy_stocks'][:5]:
            if len(item) >= 3:
                low_data.append([item[0][:12], item[1], item[2] + '分'])
        story.append(make_table(low_data, [3*cm, 3*cm, 2*cm], colors.HexColor('#27ae60')))
    else:
        story.append(Paragraph('今日无符合条件的低吸标的', styles['CBody']))
    story.append(Spacer(1, 0.2*cm))

    # 五、ETF操作建议
    story.append(Paragraph('五、ETF操作建议', styles['CH1']))
    if data['etf_advice']:
        story.append(Paragraph(data['etf_advice'][:300], styles['CBody']))
    else:
        story.append(Paragraph('暂无ETF操作建议', styles['CBody']))
    story.append(Spacer(1, 0.3*cm))

    # 页脚
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['CFooter']))
    story.append(Paragraph('QClaw | 免责声明：本报告仅供参考，不构成投资建议', styles['CFooter']))

    doc.build(story)
    print(f"PDF生成完成: {out_path}")
    return out_path

if __name__ == '__main__':
    md_path = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    if md_path and os.path.exists(md_path):
        generate_pdf(md_path, out_path)
    else:
        print("用法: python _pdf_report_template.py <md文件路径> [输出pdf路径]")
