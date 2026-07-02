"""
将B浪策略CSV报告转换为PDF
"""
import os
import csv
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# 注册中文字体
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    pdfmetrics.registerFont(TTFont('SimSun', 'C:\\Windows\\Fonts\\simsun.ttc'))
    FONT_NAME = 'SimHei'
    FONT_NAME_EN = 'SimSun'
except:
    FONT_NAME = 'Helvetica'
    FONT_NAME_EN = 'Helvetica'

def read_csv(csv_file):
    """读取CSV文件"""
    data = []
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    return data

def create_pdf(data, pdf_file):
    """创建PDF报告"""
    print(f'创建PDF: {pdf_file}')
    
    # 创建PDF文档
    doc = SimpleDocTemplate(
        pdf_file,
        pagesize=A4,
        leftMargin=1.5*cm,
        rightMargin=1.5*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )
    
    # 样式
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=FONT_NAME,
        fontSize=16,
        alignment=TA_CENTER,
        spaceAfter=12
    )
    
    # 构建内容
    story = []
    
    # 标题
    story.append(Paragraph('B浪策略合格股票池报告', title_style))
    story.append(Spacer(1, 0.5*cm))
    
    # 概览信息
    today = data[0].get('today', '') if data else ''
    story.append(Paragraph(f'分析日期: {today}', styles['Normal']))
    story.append(Paragraph(f'股票数量: {len(data)} 只', styles['Normal']))
    story.append(Spacer(1, 0.5*cm))
    
    # 表格数据
    headers = ['排名', '代码', 'B浪评分', '信号类型', 'A浪涨幅%', 'B浪回调%', '未来1日%', '未来5日%', '未来10日%']
    
    table_data = [headers]
    for i, row in enumerate(data, 1):
        ts_code = row.get('ts_code', '')
        bwave_score = row.get('bwave_score', '')
        signal_type = row.get('signal_type', '')
        a_gain = row.get('a_gain', '')
        b_drop = row.get('b_drop', '')
        return_1d = row.get('return_1d', '')
        return_5d = row.get('return_5d', '')
        return_10d = row.get('return_10d', '')
        
        table_data.append([
            str(i),
            ts_code,
            bwave_score,
            signal_type,
            a_gain,
            b_drop,
            return_1d,
            return_5d,
            return_10d
        ])
    
    # 创建表格
    table = Table(table_data, repeatRows=1)
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('FONTNAME', (0, 1), (-1, -1), FONT_NAME_EN),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ])
    table.setStyle(table_style)
    
    story.append(table)
    story.append(Spacer(1, 1*cm))
    
    # 详细分析（前5名）
    story.append(Paragraph('Top 5 股票详细分析', styles['Heading2']))
    story.append(Spacer(1, 0.3*cm))
    
    for i, row in enumerate(data[:5], 1):
        ts_code = row.get('ts_code', '')
        bwave_score = row.get('bwave_score', '')
        a_gain = row.get('a_gain', '')
        b_drop = row.get('b_drop', '')
        signal_tags = row.get('signal_tags', '')
        
        detail = f'{i}. {ts_code} - B浪评分: {bwave_score}'
        story.append(Paragraph(detail, styles['Normal']))
        
        detail2 = f'   A浪涨幅: {a_gain}% | B浪回调: {b_drop}%'
        story.append(Paragraph(detail2, styles['Normal']))
        
        if signal_tags:
            detail3 = f'   信号: {signal_tags}'
            story.append(Paragraph(detail3, styles['Normal']))
        
        story.append(Spacer(1, 0.3*cm))
    
    # 生成PDF
    doc.build(story)
    print(f'PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified.pdf'
    
    print('=' * 70)
    print('B浪策略报告转PDF')
    print('=' * 70)
    print()
    
    if not os.path.exists(csv_file):
        print(f'错误: 文件不存在 - {csv_file}')
        return
    
    # 读取CSV
    print(f'读取CSV: {csv_file}')
    data = read_csv(csv_file)
    print(f'读取到 {len(data)} 条记录')
    
    # 按B浪评分排序
    data.sort(key=lambda x: float(x.get('bwave_score', 0)), reverse=True)
    
    # 生成PDF
    create_pdf(data, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
