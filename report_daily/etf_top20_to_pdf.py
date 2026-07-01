"""
ETF持仓前20%转PDF报告

功能：
1. 读取 etf_constituents_20260630.csv
2. 按ETF分组
3. 提取每个ETF持仓的前20%（按cpr权重排序）
4. 生成每个ETF的PDF报告
"""
import os
import csv
from collections import defaultdict
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import warnings
warnings.filterwarnings('ignore')

# 注册中文字体
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    FONT_NAME = 'SimHei'
except:
    FONT_NAME = 'Helvetica'

INPUT_FILE = r'D:\mystock\report_daily\etf_constituents_20260630.csv'
OUTPUT_DIR = r'D:\mystock\report_daily\etf_pdfs'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def read_csv(filepath):
    """读取CSV文件"""
    data = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    return data

def group_by_etf(data):
    """按ETF分组"""
    etf_groups = defaultdict(list)
    for row in data:
        etf_code = row['etf_code']
        etf_groups[etf_code].append(row)
    return etf_groups

def get_top_20_percent(holdings):
    """获取前20%持仓（假设数据已按权重排序）"""
    total = len(holdings)
    top_n = max(1, int(total * 0.2))  # 至少保留1只
    
    # 直接取前20%（假设已按权重降序排列）
    return holdings[:top_n]

def generate_pdf(etf_name, etf_code, holdings, output_path):
    """生成PDF报告"""
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4
    
    # 标题
    c.setFont(FONT_NAME, 16)
    title = f'{etf_name} ({etf_code}) - 前20%持仓'
    c.drawString(2*cm, height - 2*cm, title)
    
    # 生成日期
    c.setFont(FONT_NAME, 10)
    c.drawString(2*cm, height - 2.8*cm, '生成日期: 2026-06-30')
    c.drawString(2*cm, height - 3.3*cm, f'持仓数量: {len(holdings)}')
    
    # 表头
    y = height - 4.5*cm
    c.setFont(FONT_NAME, 9)
    c.drawString(2*cm, y, '序号')
    c.drawString(4*cm, y, '股票代码')
    c.drawString(7*cm, y, '股票名称')
    c.drawString(11*cm, y, '持仓数量')
    c.drawString(14*cm, y, '权重(cpr)')
    
    # 持仓明细
    y -= 0.5*cm
    c.setFont(FONT_NAME, 8)
    
    for i, holding in enumerate(holdings, 1):
        if y < 2*cm:  # 换页
            c.showPage()
            y = height - 2*cm
            c.setFont(FONT_NAME, 8)
        
        con_code = holding.get('con_code', '')
        con_name = holding.get('con_name', '')
        qty = holding.get('qty', '')
        cpr = holding.get('cpr', '')
        
        c.drawString(2*cm, y, str(i))
        c.drawString(4*cm, y, con_code)
        c.drawString(7*cm, y, con_name)
        c.drawString(11*cm, y, qty)
        c.drawString(14*cm, y, cpr)
        
        y -= 0.4*cm
    
    c.save()
    print(f'  PDF已生成: {output_path}')

def main():
    print('=' * 70)
    print('ETF持仓前20%转PDF报告')
    print('=' * 70)
    print()
    
    # 1. 读取CSV
    print(f'读取文件: {INPUT_FILE}')
    data = read_csv(INPUT_FILE)
    print(f'总记录数: {len(data)}')
    print()
    
    # 2. 按ETF分组
    print('按ETF分组...')
    etf_groups = group_by_etf(data)
    print(f'ETF数量: {len(etf_groups)}')
    print()
    
    # 3. 生成每个ETF的PDF
    print('生成PDF报告...')
    for etf_code, holdings in etf_groups.items():
        etf_name = holdings[0].get('etf_name', etf_code)
        print(f'  处理 {etf_name} ({etf_code}) - {len(holdings)} 持仓')
        
        # 获取前20%
        top_holdings = get_top_20_percent(holdings)
        print(f'    前20%: {len(top_holdings)} 只')
        
        # 生成PDF
        output_filename = f'{etf_name}_{etf_code}_top20.pdf'
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        try:
            generate_pdf(etf_name, etf_code, top_holdings, output_path)
        except Exception as e:
            print(f'    生成失败: {e}')
    
    print()
    print('=' * 70)
    print(f'完成！PDF已保存到: {OUTPUT_DIR}')
    print('=' * 70)

if __name__ == '__main__':
    main()
