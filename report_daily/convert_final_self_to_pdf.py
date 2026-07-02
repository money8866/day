"""
将Final_Self Markdown报告转换为PDF
"""
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# 注册中文字体
try:
    pdfmetrics.registerFont(TTFont('SimHei', 'C:\\Windows\\Fonts\\simhei.ttf'))
    pdfmetrics.registerFont(TTFont('SimSun', 'C:\\Windows\\Fonts\\simsun.ttc'))
    FONT_NAME = 'SimHei'
    FONT_NAME_EN = 'SimSun'
except:
    FONT_NAME = 'Helvetica'
    FONT_NAME_EN = 'Helvetica'

def markdown_to_pdf(md_file, pdf_file):
    """将Markdown转换为PDF"""
    print(f'读取Markdown: {md_file}')
    
    # 读取Markdown
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print(f'创建PDF: {pdf_file}')
    
    # 创建PDF文档
    doc = SimpleDocTemplate(
        pdf_file,
        pagesize=A4,
        leftMargin=2*cm,
        rightMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )
    
    # 定义样式
    styles = getSampleStyleSheet()
    
    # 标题样式
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=FONT_NAME,
        fontSize=16,
        textColor='#000000',
        spaceAfter=12,
        alignment=TA_CENTER
    )
    
    # 一级标题
    h1_style = ParagraphStyle(
        'CustomH1',
        parent=styles['Heading1'],
        fontName=FONT_NAME,
        fontSize=14,
        textColor='#000000',
        spaceAfter=10,
        spaceBefore=10
    )
    
    # 二级标题
    h2_style = ParagraphStyle(
        'CustomH2',
        parent=styles['Heading2'],
        fontName=FONT_NAME,
        fontSize=12,
        textColor='#000080',
        spaceAfter=8,
        spaceBefore=8
    )
    
    # 正文样式
    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['BodyText'],
        fontName=FONT_NAME,
        fontSize=10,
        leading=14,
        textColor='#000000',
        spaceAfter=6
    )
    
    # 解析Markdown内容
    story = []
    lines = content.split('\n')
    
    for line in lines:
        line = line.strip()
        
        if not line:
            story.append(Spacer(1, 0.2*cm))
            continue
        
        # 标题处理
        if line.startswith('# '):
            text = line[2:].strip()
            story.append(Paragraph(text, title_style))
        elif line.startswith('## '):
            text = line[3:].strip()
            story.append(Paragraph(text, h1_style))
        elif line.startswith('### '):
            text = line[4:].strip()
            story.append(Paragraph(text, h2_style))
        # 列表处理
        elif line.startswith('* ') or line.startswith('- '):
            text = line[2:].strip()
            # 处理粗体
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            story.append(Paragraph(f'• {text}', body_style))
        # 引用处理
        elif line.startswith('>'):
            text = line[1:].strip()
            story.append(Paragraph(text, body_style))
        # 普通文本
        else:
            # 处理粗体
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
            # 处理换行
            text = text.replace('<br>', '<br/>')
            story.append(Paragraph(text, body_style))
    
    # 生成PDF
    doc.build(story)
    print(f'PDF生成成功: {pdf_file}')

def main():
    md_file = r'D:\mystock\report_daily\Final_Self_20260701.md'
    pdf_file = r'D:\mystock\report_daily\Final_Self_20260701.pdf'
    
    print('=' * 70)
    print('Markdown转PDF工具')
    print('=' * 70)
    print()
    
    if not os.path.exists(md_file):
        print(f'错误: 文件不存在 - {md_file}')
        return
    
    markdown_to_pdf(md_file, pdf_file)
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print(f'PDF文件: {pdf_file}')
    
    return pdf_file

if __name__ == '__main__':
    main()
