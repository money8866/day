"""
B浪策略分析 - 按股票输出信号日期和具体信号
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

TS_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    FONT_NAME = 'SimHei'
except:
    FONT_NAME = 'Helvetica'

def get_names(codes):
    names = {}
    try:
        df = pro.stock_basic(ts_code=','.join(codes), fields='ts_code,name')
        if df is not None:
            for _, r in df.iterrows():
                names[r['ts_code']] = r['name']
    except:
        pass
    return names

def main():
    # 使用最新的CSV文件
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_125355_qualified.csv'
    pdf_file = r'D:\mystock\solo\trend_feature_output\bwave_signal_analysis_20260702.pdf'
    
    print('=' * 70)
    print('B浪策略分析 - 按股票输出信号日期和具体信号')
    print('=' * 70)
    print()
    
    # 读取CSV
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        data = list(csv.DictReader(f))
    
    print(f'读取到 {len(data)} 条记录')
    
    # 按股票代码排序
    data.sort(key=lambda x: x['ts_code'])
    
    # 获取股票名称
    names = get_names(list(set([r['ts_code'] for r in data])))
    
    # 按股票分组
    stocks = {}
    for row in data:
        code = row['ts_code']
        if code not in stocks:
            stocks[code] = []
        stocks[code].append(row)
    
    print(f'共有 {len(stocks)} 只股票')
    print()
    
    # 打印分析结果到控制台
    print('=' * 70)
    print('按股票分组的信号分析')
    print('=' * 70)
    print()
    
    for code in sorted(stocks.keys()):
        rows = stocks[code]
        name = names.get(code, code)
        
        print(f'{code} ({name})')
        print('-' * 50)
        
        for i, row in enumerate(rows, 1):
            launch_date = row.get('launch_date', '')
            signal_type = row.get('signal_type', '')
            bwave_score = row.get('bwave_score', '')
            a_gain = row.get('a_gain', '')
            b_drop = row.get('b_drop', '')
            signal_tags = row.get('signal_tags', '')
            
            print(f'  信号{i}: 日期={launch_date}, 类型={signal_type}, 评分={bwave_score}')
            print(f'          A浪涨幅={a_gain}%, B浪回调={b_drop}%')
            if signal_tags:
                print(f'          信号标签: {signal_tags}')
            print()
        
        print()
    
    # 生成PDF
    c = canvas.Canvas(pdf_file, pagesize=A4)
    w, h = A4
    
    y = h - 2*cm
    
    # 标题
    c.setFont(FONT_NAME, 14)
    c.drawString(2*cm, y, 'B浪策略信号分析报告（按股票）')
    y -= 1*cm
    
    c.setFont(FONT_NAME, 9)
    c.drawString(2*cm, y, f'分析时间: 2026-07-02 | 股票数: {len(stocks)} | 信号数: {len(data)}')
    y -= 1*cm
    
    # 按股票输出
    for code in sorted(stocks.keys()):
        if y < 4*cm:  # 换页
            c.showPage()
            y = h - 2*cm
        
        rows = stocks[code]
        name = names.get(code, code)
        
        # 股票标题
        c.setFont(FONT_NAME, 10)
        c.drawString(2*cm, y, f'{code} ({name})')
        y -= 0.5*cm
        
        # 信号详情
        c.setFont(FONT_NAME, 8)
        for i, row in enumerate(rows, 1):
            if y < 3*cm:  # 换页
                c.showPage()
                y = h - 2*cm
            
            launch_date = row.get('launch_date', '')
            signal_type = row.get('signal_type', '')
            bwave_score = row.get('bwave_score', '')
            a_gain = row.get('a_gain', '')
            b_drop = row.get('b_drop', '')
            signal_tags = row.get('signal_tags', '')
            
            # 信号基本信息
            signal_info = f'  信号{i}: 日期={launch_date}, 类型={signal_type}, B浪评分={bwave_score}'
            c.drawString(2.5*cm, y, signal_info)
            y -= 0.4*cm
            
            # A浪B浪信息
            wave_info = f'       A浪涨幅={a_gain}%, B浪回调={b_drop}%'
            c.drawString(2.5*cm, y, wave_info)
            y -= 0.4*cm
            
            # 信号标签
            if signal_tags:
                if len(signal_tags) > 60:
                    signal_tags = signal_tags[:60] + '...'
                tag_info = f'       信号: {signal_tags}'
                c.drawString(2.5*cm, y, tag_info)
                y -= 0.4*cm
            
            y -= 0.2*cm
        
        y -= 0.5*cm
    
    c.save()
    print('=' * 70)
    print(f'✓ PDF生成成功: {pdf_file}')
    print('=' * 70)
    
    return pdf_file, stocks, names

if __name__ == '__main__':
    pdf_file, stocks, names = main()
