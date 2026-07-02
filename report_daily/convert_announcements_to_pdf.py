"""
将股票公告汇总报告转换为PDF
"""
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm

# 注册中文字体
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    FONT_NAME = 'SimHei'
except:
    FONT_NAME = 'Helvetica'

def clean_html_tags(text):
    """去除HTML标签"""
    text = re.sub(r'<em>', '', text)
    text = re.sub(r'</em>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text

def markdown_to_pdf_simple(md_file, pdf_file):
    """简化版：将Markdown转换为PDF"""
    print(f'读取Markdown: {md_file}')
    
    # 读取Markdown
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 去除HTML标签
    content = clean_html_tags(content)
    
    print(f'创建PDF: {pdf_file}')
    
    # 创建PDF
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
    # 设置字体
    c.setFont(FONT_NAME, 10)
    
    # 处理内容
    y = height - 2*cm
    lines = content.split('\n')
    
    for line in lines:
        # 跳过空行
        if not line.strip():
            y -= 0.3*cm
            continue
        
        # 标题处理
        if line.startswith('# '):
            c.setFont(FONT_NAME, 16)
            c.drawString(2*cm, y, line[2:].strip())
            c.setFont(FONT_NAME, 10)
            y -= 0.8*cm
        elif line.startswith('## '):
            c.setFont(FONT_NAME, 14)
            c.drawString(2*cm, y, line[3:].strip())
            c.setFont(FONT_NAME, 10)
            y -= 0.6*cm
        elif line.startswith('### '):
            c.setFont(FONT_NAME, 12)
            c.drawString(2*cm, y, line[4:].strip())
            c.setFont(FONT_NAME, 10)
            y -= 0.5*cm
        # 列表处理
        elif line.startswith('- '):
            text = '• ' + line[2:].strip()
            if len(text) > 80:
                text = text[:80] + '...'
            c.drawString(2.5*cm, y, text)
            y -= 0.4*cm
        # 普通文本
        else:
            text = line.strip()
            if len(text) > 90:
                text = text[:90] + '...'
            try:
                c.drawString(2*cm, y, text)
            except:
                pass
            y -= 0.4*cm
        
        # 换页
        if y < 2*cm:
            c.showPage()
            y = height - 2*cm
            c.setFont(FONT_NAME, 10)
    
    c.save()
    print(f'PDF生成成功: {pdf_file}')

def main():
    md_file = r'D:\mystock\report_daily\stock_announcements_summary_20260702_075911.md'
    pdf_file = r'D:\mystock\report_daily\stock_announcements_summary_20260702_075911.pdf'
    
    print('=' * 70)
    print('公告汇总报告转PDF')
    print('=' * 70)
    print()
    
    if not os.path.exists(md_file):
        print(f'错误: 文件不存在 - {md_file}')
        return
    
    markdown_to_pdf_simple(md_file, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
