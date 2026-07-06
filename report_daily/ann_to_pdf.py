# -*- coding: utf-8 -*-
"""将公告分析转换为PDF（支持中文）"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# 注册中文字体
def register_chinese_font():
    """注册中文字体"""
    font_paths = [
        # Windows常见中文字体
        r'C:\Windows\Fonts\msyh.ttc',   # 微软雅黑
        r'C:\Windows\Fonts\SimHei.ttf',  # 黑体
        r'C:\Windows\Fonts\simsun.ttc',  # 宋体
        r'C:\Windows\Fonts\Simkai.ttf',  # 楷体
    ]
    
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
                print('已注册字体:', font_path)
                return 'ChineseFont'
            except:
                continue
    
    print('未找到中文字体，使用默认字体')
    return None

CHINESE_FONT = register_chinese_font()

def convert_to_pdf(md_file, pdf_file):
    """Markdown转PDF"""
    
    # 读取markdown
    with open(md_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 创建PDF
    doc = SimpleDocTemplate(
        pdf_file,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )
    
    # 自定义样式（使用中文字体）
    if CHINESE_FONT:
        title_style = ParagraphStyle(
            'CustomTitle',
            fontName=CHINESE_FONT,
            fontSize=16,
            spaceAfter=12,
            textColor=colors.darkblue,
        )
        
        h2_style = ParagraphStyle(
            'H2Style',
            fontName=CHINESE_FONT,
            fontSize=13,
            spaceAfter=8,
            spaceBefore=12,
            textColor=colors.darkgreen,
        )
        
        h3_style = ParagraphStyle(
            'H3Style',
            fontName=CHINESE_FONT,
            fontSize=11,
            spaceAfter=6,
            spaceBefore=8,
        )
        
        normal_style = ParagraphStyle(
            'NormalText',
            fontName=CHINESE_FONT,
            fontSize=10,
            leading=14,
        )
        
        bullet_style = ParagraphStyle(
            'BulletStyle',
            fontName=CHINESE_FONT,
            fontSize=10,
            leading=14,
            leftIndent=20,
            bulletIndent=10,
        )
    else:
        title_style = ParagraphStyle('CustomTitle', fontSize=16, spaceAfter=12)
        h2_style = ParagraphStyle('H2Style', fontSize=13, spaceAfter=8, spaceBefore=12)
        h3_style = ParagraphStyle('H3Style', fontSize=11, spaceAfter=6, spaceBefore=8)
        normal_style = ParagraphStyle('NormalText', fontSize=10, leading=14)
        bullet_style = ParagraphStyle('BulletStyle', fontSize=10, leading=14, leftIndent=20)
    
    # 构建内容
    content = []
    
    for line in lines:
        line = line.strip()
        
        if not line:
            content.append(Spacer(1, 0.3*cm))
            continue
        
        # 标题
        if line.startswith('# '):
            content.append(Paragraph(line[2:], title_style))
        
        # 二级标题
        elif line.startswith('## '):
            content.append(Paragraph(line[3:], h2_style))
        
        # 三级标题
        elif line.startswith('### '):
            content.append(Paragraph(line[4:], h3_style))
        
        # 列表项
        elif line.startswith('- '):
            line = line[2:]
            # 移除emoji避免乱码
            line = line.replace('📈', '[利好]').replace('📉', '[利空]').replace('**', '')
            content.append(Paragraph('• ' + line, bullet_style))
        
        # 普通文本
        else:
            line = line.replace('**', '')
            content.append(Paragraph(line, normal_style))
    
    # 生成PDF
    doc.build(content)
    print('PDF已生成:', pdf_file)

if __name__ == '__main__':
    md_file = r'D:\mystock\report_daily\announcement_analysis_20260706.md'
    pdf_file = r'D:\mystock\report_daily\announcement_analysis_20260706.pdf'
    convert_to_pdf(md_file, pdf_file)
