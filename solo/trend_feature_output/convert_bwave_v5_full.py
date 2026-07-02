"""
B浪策略报告PDF生成 - 显示所有列和行（横向+分组）v5
"""
import os
import csv
import tushare as ts
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
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

def create_full_pdf(data, names, pdf_file):
    """生成显示所有列和行的PDF（横向+分组）"""
    print(f'创建完整版PDF: {pdf_file}')
    
    # 使用横向打印
    c = canvas.Canvas(pdf_file, pagesize=landscape(A4))
    width, height = landscape(A4)
    
    # 定义列分组（按逻辑分组）
    column_groups = [
        {
            'name': '基本信息',
            'columns': ['排名', 'ts_code', '名称', 'today', 'launch_date', 'signal_type', 'bwave_score']
        },
        {
            'name': 'A浪特征',
            'columns': ['a_start_date', 'a_end_date', 'a_gain', 'a_duration', 'a_vol_ratio']
        },
        {
            'name': 'B浪特征',
            'columns': ['b_start_date', 'b_low_date', 'b_drop', 'b_duration', 'b_time_ratio', 
                       'b_vol_shrink', 'b_atr_drop', 'b_ma60_dist']
        },
        {
            'name': '信号特征',
            'columns': ['launch_price', 'launch_pct_chg', 'launch_vol_ratio', 'launch_macd_golden',
                       'launch_ma5_crossing', 'launch_break_platform', 'launch_rsi6', 
                       'launch_b_recovery', 'launch_dist_to_a_high', 'launch_rsi_golden',
                       'launch_bottom_signal']
        },
        {
            'name': '评分',
            'columns': ['a_score', 'b_score', 't_score', 'l_score']
        },
        {
            'name': '未来收益',
            'columns': ['return_1d', 'return_5d', 'return_10d', 'return_20d']
        },
    ]
    
    # 页面设置
    left_margin = 1*cm
    right_margin = 1*cm
    top_margin = 2*cm
    bottom_margin = 2*cm
    
    # 表格字体大小
    HEADER_FONT_SIZE = 6
    DATA_FONT_SIZE = 5
    
    # 计算可用宽度
    available_width = width - left_margin - right_margin
    
    page_num = 1
    y = height - top_margin
    
    # 绘制页眉
    c.setFont(FONT_NAME, 10)
    c.drawString(left_margin, height - 1.2*cm, 'B浪策略合格股票池报告（完整版）')
    c.setFont(FONT_NAME, 8)
    today = data[0].get('today', '') if data else ''
    c.drawString(width - right_margin - 6*cm, height - 1.2*cm, f'分析日期: {today} | 股票数: {len(data)}')
    
    y -= 1*cm
    
    # 遍历每个分组
    for group_idx, group in enumerate(column_groups):
        # 检查是否需要换页
        if y < bottom_margin + 3*cm:
            c.showPage()
            page_num += 1
            y = height - top_margin
            # 新页页眉
            c.setFont(FONT_NAME, 10)
            c.drawString(left_margin, height - 1.2*cm, f'B浪策略合格股票池报告（完整版） - 第{page_num}页')
            y -= 1*cm
        
        # 分组标题
        c.setFont(FONT_NAME, 8)
        c.drawString(left_margin, y, f'【{group["name"]}】')
        y -= 0.5*cm
        
        # 计算本组列宽
        group_cols = group['columns']
        col_width = available_width / len(group_cols)
        
        # 绘制表头
        c.setFont(FONT_NAME, HEADER_FONT_SIZE)
        x = left_margin
        for col in group_cols:
            # 表头垂直显示（旋转90度）
            c.saveState()
            c.translate(x + col_width/2, y)
            c.rotate(90)
            c.drawString(0, 0, col[:15])  # 截断过长列名
            c.restoreState()
            x += col_width
        
        y -= 1.5*cm  # 表头占用更多垂直空间
        
        # 绘制数据行
        c.setFont(FONT_NAME_EN, DATA_FONT_SIZE)
        for i, row in enumerate(data, 1):
            if y < bottom_margin:
                # 换页
                c.showPage()
                page_num += 1
                y = height - top_margin
                # 新页页眉
                c.setFont(FONT_NAME, 10)
                c.drawString(left_margin, height - 1.2*cm, f'B浪策略合格股票池报告（完整版） - 第{page_num}页')
                y -= 1*cm
                
                # 重新绘制分组标题和表头
                c.setFont(FONT_NAME, 8)
                c.drawString(left_margin, y, f'【{group["name"]}】（续）')
                y -= 0.5*cm
                
                c.setFont(FONT_NAME, HEADER_FONT_SIZE)
                x = left_margin
                for col in group_cols:
                    c.saveState()
                    c.translate(x + col_width/2, y)
                    c.rotate(90)
                    c.drawString(0, 0, col[:15])
                    c.restoreState()
                    x += col_width
                y -= 1.5*cm
                c.setFont(FONT_NAME_EN, DATA_FONT_SIZE)
            
            # 获取数据
            x = left_margin
            for col in group_cols:
                if col == '排名':
                    value = str(i)
                elif col == '名称':
                    value = names.get(row.get('ts_code', ''), '')[:8]
                else:
                    value = row.get(col, '')[:8]  # 截断过长数据
                
                c.drawString(x + 0.1*cm, y, value)
                x += col_width
            
            y -= 0.4*cm
        
        y -= 0.5*cm  # 组间间隔
    
    # 页脚
    c.setFont(FONT_NAME_EN, 8)
    c.drawString(width/2 - 1*cm, 1*cm, f'- {page_num} -')
    
    # 保存PDF
    c.save()
    print(f'✓ 完整版PDF生成成功: {pdf_file}')

def main():
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_113912_qualified_v5_full.pdf'
    
    print('=' * 70)
    print('B浪策略报告PDF - 显示所有列和行（横向+分组）')
    print('=' * 70)
    print()
    
    if not os.path.exists(csv_file):
        print(f'错误: 文件不存在 - {csv_file}')
        return
    
    # 读取CSV
    print(f'读取CSV: {csv_file}')
    data = read_csv(csv_file)
    print(f'读取到 {len(data)} 条记录，{len(data[0])} 列')
    
    # 按B浪评分排序
    data.sort(key=lambda x: float(x.get('bwave_score', 0)), reverse=True)
    
    # 获取股票名称
    ts_codes = list(set([row['ts_code'] for row in data]))
    names = get_stock_names(ts_codes)
    
    # 生成完整版PDF
    create_full_pdf(data, names, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
