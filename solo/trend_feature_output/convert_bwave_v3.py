"""
B浪策略报告PDF生成 - 添加信号日期和股票名称
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
        # 使用stock_basic接口获取股票名称
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

def create_pdf_with_names(data, names, pdf_file):
    """生成包含股票名称和信号日期的PDF"""
    print(f'创建PDF: {pdf_file}')
    
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
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
    c.setFont(FONT_NAME, 8)
    headers = ['排名', '代码', '名称', '信号日期', 'B浪评分', '信号', 'A浪%', 'B浪%', '未来1d%']
    x_positions = [1.5*cm, 3*cm, 4.5*cm, 6.5*cm, 8.5*cm, 10*cm, 11.5*cm, 13*cm, 14.5*cm]
    
    for header, x in zip(headers, x_positions):
        c.drawString(x, y, header)
    y -= 0.5*cm
    
    # 数据行
    c.setFont(FONT_NAME_EN, 7)
    for i, row in enumerate(data[:30], 1):  # 只显示前30条
        if y < 3*cm:  # 换页
            c.showPage()
            y = height - 2*cm
            c.setFont(FONT_NAME, 12)
        
        c.setFont(FONT_NAME_EN, 7)
        ts_code = row.get('ts_code', '')
        name = names.get(ts_code, ts_code)  # 如果没获取到名称，用代码代替
        launch_date = row.get('launch_date', '')
        bwave_score = row.get('bwave_score', '')
        signal_type = row.get('signal_type', '')
        a_gain = row.get('a_gain', '')
        b_drop = row.get('b_drop', '')
        return_1d = row.get('return_1d', '')
        
        c.drawString(x_positions[0], y, str(i))
        c.drawString(x_positions[1], y, ts_code[:10])
        c.drawString(x_positions[2], y, name[:8])  # 名称截断
        c.drawString(x_positions[3], y, launch_date)
        c.drawString(x_positions[4], y, bwave_score[:6])
        c.drawString(x_positions[5], y, signal_type[:8])
        c.drawString(x_positions[6], y, a_gain[:6])
        c.drawString(x_positions[7], y, b_drop[:6])
        c.drawString(x_positions[8], y, return_1d[:6])
        
        y -= 0.4*cm
    
    # 保存PDF
    c.save()
    print(f'✓ PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified_v3.pdf'
    
    print('=' * 70)
    print('B浪策略报告PDF - 添加信号日期和股票名称')
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
    
    # 获取股票名称
    ts_codes = list(set([row['ts_code'] for row in data]))
    names = get_stock_names(ts_codes)
    
    # 生成PDF
    create_pdf_with_names(data, names, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
