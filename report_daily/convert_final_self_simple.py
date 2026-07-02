"""
将Final_Self Markdown报告转换为PDF - 简化版（去除HTML标签）
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
    # 去除<span style="...">标签，保留内容
    text = re.sub(r'<span[^>]*>', '', text)
    text = re.sub(r'</span>', '', text)
    # 去除其他HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    return text

def markdown_to_pdf_simple(md_file, pdf_file):
    """简化版：将Markdown转换为PDF（纯文本）"""
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
        
        # 标题处理（加粗）
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
        elif line.startswith('* ') or line.startswith('- '):
            text = '• ' + line[2:].strip()
            # 处理粗体
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
            c.drawString(2.5*cm, y, text[:80])
            y -= 0.4*cm
        # 普通文本
        else:
            # 处理粗体
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', line)
            # 限制长度
            if len(text) > 90:
                text = text[:90] + '...'
            try:
                c.drawString(2*cm, y, text)
            except:
                # 如果编码错误，跳过
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
    md_file = r'D:\mystock\report_daily\Final_Self_20260701.md'
    pdf_file = r'D:\mystock\report_daily\Final_Self_20260701.pdf'
    
    print('=' * 70)
    print('Markdown转PDF工具 - 简化版')
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
