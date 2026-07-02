"""
B浪策略分析PDF生成 - 按股票和信号日排序
"""
import os
import csv
import tushare as ts
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# Tushare token
TS_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

# 注册中文字体
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    pdfmetrics.registerFont(TTFont('SimSun', 'C:\\Windows\\Fonts\\simsun.ttc'))
    FONT_NAME = 'SimHei'
    FONT_NAME_EN = 'SimSun'
    print('✓ 中文字体注册成功')
except Exception as e:
    print(f'⚠ 字体注册失败: {e}')
    FONT_NAME = 'Helvetica'
    FONT_NAME_EN = 'Helvetica'

def get_stock_names(ts_codes):
    """批量获取股票名称"""
    print(f'获取 {len(ts_codes)} 只股票的名称...')
    names = {}
    try:
        df = pro.stock_basic(ts_code=','.join(ts_codes), fields='ts_code,name')
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                names[row['ts_code']] = row['name']
        print(f'✓ 获取到 {len(names)} 个股票名称')
    except Exception as e:
        print(f'⚠ 获取股票名称失败: {e}')
    return names

def read_csv(csv_file):
    """读取CSV文件"""
    data = []
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    return data

def sort_data(data):
    """按股票代码和信号日期排序"""
    print('按股票代码和信号日期排序...')
    
    def sort_key(row):
        ts_code = row.get('ts_code', '')
        launch_date = row.get('launch_date', '')
        
        # 确保launch_date是字符串，处理可能的None或数字
        if launch_date is None:
            launch_date = '00000000'
        else:
            launch_date = str(launch_date)
        
        return (ts_code, launch_date)
    
    sorted_data = sorted(data, key=sort_key)
    print(f'✓ 排序完成，共 {len(sorted_data)} 条记录')
    return sorted_data

def create_analysis_pdf(data, names, pdf_file):
    """创建分析PDF"""
    print(f'创建分析PDF: {pdf_file}')
    
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
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontName=FONT_NAME,
        fontSize=12,
        spaceAfter=6
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=FONT_NAME_EN,
        fontSize=9
    )
    
    # 构建内容
    story = []
    
    # 标题
    story.append(Paragraph('B浪策略分析报告', title_style))
    story.append(Spacer(1, 0.5*cm))
    
    # 概览信息
    today = data[0].get('today', '') if data else ''
    story.append(Paragraph(f'分析日期: {today}', normal_style))
    story.append(Paragraph(f'股票数量: {len(data)} 只', normal_style))
    story.append(Paragraph(f'排序方式: 股票代码 + 信号日期', normal_style))
    story.append(Spacer(1, 0.5*cm))
    
    # 统计摘要
    story.append(Paragraph('统计摘要', heading_style))
    
    # 计算统计数据
    bwave_scores = [float(row.get('bwave_score', 0)) for row in data]
    avg_score = sum(bwave_scores) / len(bwave_scores) if bwave_scores else 0
    max_score = max(bwave_scores) if bwave_scores else 0
    min_score = min(bwave_scores) if bwave_scores else 0
    
    signal_types = {}
    for row in data:
        st = row.get('signal_type', 'unknown')
        signal_types[st] = signal_types.get(st, 0) + 1
    
    # 统计表格
    stats_data = [
        ['指标', '数值'],
        ['股票数量', f'{len(data)} 只'],
        ['平均B浪评分', f'{avg_score:.2f}'],
        ['最高B浪评分', f'{max_score:.2f}'],
        ['最低B浪评分', f'{min_score:.2f}'],
        ['信号类型分布', ', '.join([f'{k}:{v}' for k, v in signal_types.items()])]
    ]
    
    stats_table = Table(stats_data, colWidths=[4*cm, 8*cm])
    stats_table.setStyle(TableStyle([
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
    ]))
    
    story.append(stats_table)
    story.append(Spacer(1, 0.8*cm))
    
    # 数据表格（按股票和信号日排序后）
    story.append(Paragraph('详细数据（按股票代码+信号日期排序）', heading_style))
    story.append(Spacer(1, 0.3*cm))
    
    # 表格列（选择重要列）
    headers = ['排名', '代码', '名称', '信号日期', 'B浪评分', '信号类型', 'A浪%', 'B浪%', '未来1d%', '未来5d%', '未来10d%']
    
    table_data = [headers]
    for i, row in enumerate(data, 1):
        ts_code = row.get('ts_code', '')
        name = names.get(ts_code, ts_code)
        launch_date = row.get('launch_date', '')
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
            name[:8],  # 截断名称
            launch_date,
            bwave_score,
            signal_type,
            a_gain,
            b_drop,
            return_1d,
            return_5d,
            return_10d
        ])
    
    # 创建表格
    col_widths = [1*cm, 2*cm, 2*cm, 2*cm, 1.5*cm, 1.5*cm, 1.2*cm, 1.2*cm, 1.2*cm, 1.2*cm, 1.2*cm]
    data_table = Table(table_data, repeatRows=1, colWidths=col_widths)
    
    data_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('FONTNAME', (0, 1), (-1, -1), FONT_NAME_EN),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    
    story.append(data_table)
    story.append(Spacer(1, 1*cm))
    
    # Top 5 详细分析
    story.append(Paragraph('Top 5 股票详细分析', heading_style))
    story.append(Spacer(1, 0.3*cm))
    
    for i, row in enumerate(data[:5], 1):
        ts_code = row.get('ts_code', '')
        name = names.get(ts_code, ts_code)
        bwave_score = row.get('bwave_score', '')
        a_gain = row.get('a_gain', '')
        b_drop = row.get('b_drop', '')
        signal_tags = row.get('signal_tags', '')
        
        story.append(Paragraph(f'{i}. {ts_code} ({name}) - B浪评分: {bwave_score}', normal_style))
        story.append(Paragraph(f'   A浪涨幅: {a_gain}% | B浪回调: {b_drop}%', normal_style))
        
        if signal_tags:
            story.append(Paragraph(f'   信号标签: {signal_tags}', normal_style))
        
        story.append(Spacer(1, 0.3*cm))
    
    # 生成PDF
    doc.build(story)
    print(f'✓ 分析PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_115514_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_115514_qualified_analysis.pdf'
    
    print('=' * 70)
    print('B浪策略分析PDF - 按股票和信号日排序')
    print('=' * 70)
    print()
    
    if not os.path.exists(csv_file):
        print(f'错误: 文件不存在 - {csv_file}')
        return
    
    # 读取CSV
    print(f'读取CSV: {csv_file}')
    data = read_csv(csv_file)
    print(f'读取到 {len(data)} 条记录')
    
    # 按股票代码和信号日期排序
    sorted_data = sort_data(data)
    
    # 获取股票名称
    ts_codes = list(set([row['ts_code'] for row in sorted_data]))
    names = get_stock_names(ts_codes)
    
    # 生成分析PDF
    create_analysis_pdf(sorted_data, names, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
