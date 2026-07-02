"""
B浪策略报告PDF生成 - 修复中文显示版本
"""
import os
import csv
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm

# 注册中文字体（使用系统字体）
try:
    # 尝试注册黑体
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    pdfmetrics.registerFont(TTFont('SimSun', 'C:\\Windows\\Fonts\\simsun.ttc'))
    FONT_NAME = 'SimHei'
    FONT_NAME_EN = 'SimSun'
    print('✓ 中文字体注册成功')
except Exception as e:
    print(f'⚠ 字体注册失败: {e}')
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

def create_pdf_simple(data, pdf_file):
    """简化版PDF生成（确保中文显示）"""
    print(f'创建PDF: {pdf_file}')
    
    # 创建PDF画布
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
    # 设置默认字体
    c.setFont(FONT_NAME, 12)
    
    # 标题
    y = height - 2*cm
    c.setFont(FONT_NAME, 16)
    c.drawString(2*cm, y, 'B浪策略合格股票池报告')
    y -= 1*cm
    
    # 概览信息
    c.setFont(FONT_NAME, 10)
    today = data[0].get('today', '') if data else ''
    c.drawString(2*cm, y, f'分析日期: {today}')
    y -= 0.5*cm
    c.drawString(2*cm, y, f'股票数量: {len(data)} 只')
    y -= 0.8*cm
    
    # 表格标题行
    c.setFont(FONT_NAME, 9)
    headers = ['排名', '代码', 'B浪评分', '信号', 'A浪%', 'B浪%', '未来1d%', '未来5d%', '未来10d%']
    x_positions = [2*cm, 4*cm, 6*cm, 7.5*cm, 9*cm, 10.5*cm, 12*cm, 13.5*cm, 15*cm]
    
    for i, (header, x) in enumerate(zip(headers, x_positions)):
        c.drawString(x, y, header)
    y -= 0.5*cm
    
    # 数据行
    c.setFont(FONT_NAME_EN, 8)
    for i, row in enumerate(data[:30], 1):  # 只显示前30条
        if y < 3*cm:  # 换页
            c.showPage()
            y = height - 2*cm
            c.setFont(FONT_NAME, 12)
        
        c.setFont(FONT_NAME_EN, 8)
        c.drawString(x_positions[0], y, str(i))
        c.drawString(x_positions[1], y, row.get('ts_code', '')[:10])
        c.drawString(x_positions[2], y, row.get('bwave_score', '')[:6])
        c.drawString(x_positions[3], y, row.get('signal_type', '')[:8])
        c.drawString(x_positions[4], y, row.get('a_gain', '')[:6])
        c.drawString(x_positions[5], y, row.get('b_drop', '')[:6])
        c.drawString(x_positions[6], y, row.get('return_1d', '')[:6])
        c.drawString(x_positions[7], y, row.get('return_5d', '')[:6])
        c.drawString(x_positions[8], y, row.get('return_10d', '')[:6])
        
        y -= 0.4*cm
    
    # 保存PDF
    c.save()
    print(f'✓ PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified_v2.pdf'
    
    print('=' * 70)
    print('B浪策略报告转PDF - 修复中文显示')
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
    create_pdf_simple(data, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
