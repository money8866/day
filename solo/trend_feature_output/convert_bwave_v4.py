"""
B浪策略报告PDF生成 - 优化格式版本v4
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
from reportlab.lib import colors

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

def create_optimized_pdf(data, names, pdf_file):
    """生成优化格式的PDF"""
    print(f'创建优化版PDF: {pdf_file}')
    
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
    # 定义颜色
    HEADER_BG = colors.Color(0.2, 0.3, 0.5)  # 深蓝
    ROW_BG_1 = colors.Color(0.95, 0.95, 0.95)  # 浅灰
    ROW_BG_2 = colors.Color(1, 1, 1)  # 白色
    TEXT_COLOR = colors.white
    
    # 页面边距
    left_margin = 1.5*cm
    right_margin = 1.5*cm
    top_margin = 2.5*cm
    bottom_margin = 2*cm
    
    # 表格列定义（x位置，宽度，标题）
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
    
    def draw_header(y_pos):
        """绘制页眉"""
        c.setFont(FONT_NAME, 10)
        c.drawString(left_margin, height - 1.2*cm, 'B浪策略合格股票池报告')
        c.setFont(FONT_NAME, 8)
        today = data[0].get('today', '') if data else ''
        c.drawString(width - right_margin - 4*cm, height - 1.2*cm, f'分析日期: {today}')
        return y_pos
    
    def draw_footer(page_num, y_pos):
        """绘制页脚"""
        c.setFont(FONT_NAME_EN, 8)
        c.drawString(width/2 - 1*cm, 1*cm, f'- {page_num} -')
        return y_pos
    
    def draw_table_header(y_pos):
        """绘制表格标题行"""
        # 背景
        c.setFillColor(HEADER_BG)
        c.rect(left_margin, y_pos - 0.3*cm, width - left_margin - right_margin, 0.6*cm, fill=1, stroke=0)
        
        # 文字
        c.setFillColor(TEXT_COLOR)
        c.setFont(FONT_NAME, 8)
        for col in columns:
            c.drawString(col['x'], y_pos, col['title'])
        
        c.setFillColor(colors.black)
        return y_pos - 0.8*cm
    
    def draw_table_row(row_data, y_pos, row_num):
        """绘制表格数据行"""
        # 交替背景色
        if row_num % 2 == 0:
            c.setFillColor(ROW_BG_1)
        else:
            c.setFillColor(ROW_BG_2)
        c.rect(left_margin, y_pos - 0.25*cm, width - left_margin - right_margin, 0.5*cm, fill=1, stroke=0)
        
        # 数据
        c.setFillColor(colors.black)
        c.setFont(FONT_NAME_EN, 7)
        
        ts_code = row_data.get('ts_code', '')
        name = names.get(ts_code, ts_code)[:6]  # 截断名称
        launch_date = row_data.get('launch_date', '')
        bwave_score = row_data.get('bwave_score', '')[:6]
        signal_type = row_data.get('signal_type', '')[:8]
        a_gain = row_data.get('a_gain', '')[:6]
        b_drop = row_data.get('b_drop', '')[:6]
        return_1d = row_data.get('return_1d', '')[:6]
        
        col_values = [
            str(row_num),
            ts_code[:10],
            name,
            launch_date,
            bwave_score,
            signal_type,
            a_gain,
            b_drop,
            return_1d,
        ]
        
        for col, value in zip(columns, col_values):
            c.drawString(col['x'], y_pos, value)
        
        return y_pos - 0.5*cm
    
    # 开始绘制
    page_num = 1
    y = height - top_margin
    
    # 页眉
    y = draw_header(y)
    y -= 0.5*cm
    
    # 概览信息
    c.setFont(FONT_NAME, 10)
    c.drawString(left_margin, y, f'股票数量: {len(data)} 只')
    y -= 0.8*cm
    
    # 统计摘要
    c.setFont(FONT_NAME, 9)
    c.drawString(left_margin, y, '统计摘要:')
    y -= 0.5*cm
    
    c.setFont(FONT_NAME_EN, 8)
    # Top 5 评分
    top5_scores = [float(row.get('bwave_score', 0)) for row in data[:5]]
    avg_score = sum(top5_scores) / len(top5_scores) if top5_scores else 0
    c.drawString(left_margin + 0.5*cm, y, f'Top 5 平均B浪评分: {avg_score:.1f}')
    y -= 0.4*cm
    
    # 信号类型分布
    signal_types = {}
    for row in data:
        st = row.get('signal_type', 'unknown')
        signal_types[st] = signal_types.get(st, 0) + 1
    type_str = ', '.join([f'{k}:{v}' for k, v in signal_types.items()])
    c.drawString(left_margin + 0.5*cm, y, f'信号类型分布: {type_str}')
    y -= 0.8*cm
    
    # 表格标题
    y = draw_table_header(y)
    
    # 数据行
    for i, row in enumerate(data, 1):
        if y < bottom_margin + 0.5*cm:  # 换页
            draw_footer(page_num, y)
            c.showPage()
            page_num += 1
            y = height - top_margin
            y = draw_header(y)
            y -= 1.3*cm
            y = draw_table_header(y)
        
        y = draw_table_row(row, y, i)
    
    # 最后一页的页脚
    draw_footer(page_num, y)
    
    # 保存PDF
    c.save()
    print(f'✓ 优化版PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified_v4.pdf'
    
    print('=' * 70)
    print('B浪策略报告PDF - 优化格式版本v4')
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
    
    # 生成优化版PDF
    create_optimized_pdf(data, names, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
