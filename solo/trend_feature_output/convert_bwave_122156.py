"""
B浪策略CSV转PDF - 按股票和信号日排序
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
        
        # 确保launch_date是字符串
        if launch_date is None:
            launch_date = '00000000'
        else:
            launch_date = str(launch_date)
        
        return (ts_code, launch_date)
    
    sorted_data = sorted(data, key=sort_key)
    print(f'✓ 排序完成，共 {len(sorted_data)} 条记录')
    return sorted_data

def create_pdf(data, names, pdf_file):
    """创建PDF"""
    print(f'创建PDF: {pdf_file}')
    
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
    # 页面边距
    left_margin = 1.5*cm
    right_margin = 1.5*cm
    top_margin = 2*cm
    bottom_margin = 2*cm
    
    # 表格列定义
    columns = [
        {'x': left_margin, 'w': 1*cm, 'title': '排名'},
        {'x': left_margin + 1*cm, 'w': 2*cm, 'title': '代码'},
        {'x': left_margin + 3*cm, 'w': 2.5*cm, 'title': '名称'},
        {'x': left_margin + 5.5*cm, 'w': 2*cm, 'title': '信号日期'},
        {'x': left_margin + 7.5*cm, 'w': 1.5*cm, 'title': 'B浪评分'},
        {'x': left_margin + 9*cm, 'w': 1.5*cm, 'title': '信号类型'},
        {'x': left_margin + 10.5*cm, 'w': 1.5*cm, 'title': 'A浪%'},
        {'x': left_margin + 12*cm, 'w': 1.5*cm, 'title': 'B浪%'},
        {'x': left_margin + 13.5*cm, 'w': 1.5*cm, 'title': '未来1d%'},
    ]
    
    # 页码
    page_num = 1
    y = height - top_margin
    
    # 页眉
    c.setFont(FONT_NAME, 10)
    c.drawString(left_margin, height - 1.2*cm, 'B浪策略合格股票池报告')
    c.setFont(FONT_NAME, 8)
    today = data[0].get('today', '') if data else ''
    c.drawString(width - right_margin - 4*cm, height - 1.2*cm, f'分析日期: {today}')
    y -= 1*cm
    
    # 概览信息
    c.setFont(FONT_NAME, 10)
    c.drawString(left_margin, y, f'股票数量: {len(data)} 只')
    y -= 0.8*cm
    
    # 表格标题行
    c.setFont(FONT_NAME, 8)
    for col in columns:
        c.drawString(col['x'], y, col['title'])
    y -= 0.5*cm
    
    # 数据行
    c.setFont(FONT_NAME_EN, 7)
    for i, row in enumerate(data, 1):
        if y < bottom_margin + 0.5*cm:  # 换页
            # 页脚
            c.setFont(FONT_NAME_EN, 8)
            c.drawString(width/2 - 1*cm, 1*cm, f'- {page_num} -')
            
            c.showPage()
            page_num += 1
            y = height - top_margin
            
            # 新页页眉
            c.setFont(FONT_NAME, 10)
            c.drawString(left_margin, height - 1.2*cm, f'B浪策略合格股票池报告 - 第{page_num}页')
            y -= 1*cm
            
            # 重新绘制表格标题
            c.setFont(FONT_NAME, 8)
            for col in columns:
                c.drawString(col['x'], y, col['title'])
            y -= 0.5*cm
            c.setFont(FONT_NAME_EN, 7)
        
        # 绘制数据
        ts_code = row.get('ts_code', '')
        name = names.get(ts_code, ts_code)[:8]  # 截断名称
        launch_date = row.get('launch_date', '')
        bwave_score = row.get('bwave_score', '')[:6]
        signal_type = row.get('signal_type', '')[:8]
        a_gain = row.get('a_gain', '')[:6]
        b_drop = row.get('b_drop', '')[:6]
        return_1d = row.get('return_1d', '')[:6]
        
        c.drawString(columns[0]['x'], y, str(i))
        c.drawString(columns[1]['x'], y, ts_code[:10])
        c.drawString(columns[2]['x'], y, name)
        c.drawString(columns[3]['x'], y, launch_date)
        c.drawString(columns[4]['x'], y, bwave_score)
        c.drawString(columns[5]['x'], y, signal_type)
        c.drawString(columns[6]['x'], y, a_gain)
        c.drawString(columns[7]['x'], y, b_drop)
        c.drawString(columns[8]['x'], y, return_1d)
        
        y -= 0.4*cm
    
    # 最后一页的页脚
    c.setFont(FONT_NAME_EN, 8)
    c.drawString(width/2 - 1*cm, 1*cm, f'- {page_num} -')
    
    # 保存PDF
    c.save()
    print(f'✓ PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_122156_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_122156_qualified.pdf'
    
    print('=' * 70)
    print('B浪策略CSV转PDF')
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
    
    # 生成PDF
    create_pdf(sorted_data, names, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
