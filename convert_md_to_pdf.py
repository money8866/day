#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将Markdown文件转换为PDF（支持中文）
使用方法：python convert_md_to_pdf.py <input_md_file> <output_pdf_file>
"""

import sys
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, ListFlowable, ListItem
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import platform

def setup_chinese_pdf():
    """
    注册系统中文字体并返回(cn_font, styles)
    """
    system = platform.system()
    
    if system == 'Darwin':  # macOS
        candidates = [
            ('/System/Library/Fonts/STHeiti Light.ttc', 'STHeiti', 0),
            ('/System/Library/Fonts/STHeiti Medium.ttc', 'STHeitiMedium', 0),
            ('/System/Library/Fonts/Supplemental/Songti.ttc', 'Songti', 0),
            ('/Library/Fonts/Arial Unicode MS.ttf', 'ArialUnicode', 0),
        ]
    elif system == 'Windows':
        candidates = []
        dirs = []
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        dirs.append(os.path.join(windir, 'Fonts'))
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            dirs.append(os.path.join(local, 'Microsoft', 'Windows', 'Fonts'))
        for d in dirs:
            for fname, name, idx in [
                ('msyh.ttc', 'MicrosoftYaHei', 0),
                ('msyhbd.ttc', 'MicrosoftYaHeiBold', 0),
                ('simhei.ttf', 'SimHei', 0),
                ('simsun.ttc', 'SimSun', 0),
                ('mingliu.ttc', 'MingLiU', 0),
            ]:
                candidates.append((os.path.join(d, fname), name, idx))
    else:  # Linux
        candidates = [
            ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 'NotoSansCJK', 0),
            ('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', 'NotoSansCJK', 0),
            ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 'WQYZenHei', 0),
            ('/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf', 'DroidSans', 0),
        ]
    
    cn_font = None
    for font_path, font_name, idx in candidates:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path, subfontIndex=idx))
                cn_font = font_name
                break
            except Exception as e:
                continue
    
    if cn_font is None:
        # 回退到Helvetica（不支持中文）
        print("警告：未找到中文字体，将使用默认字体（中文可能无法显示）")
        cn_font = 'Helvetica'
    
    # 创建样式表并配置中文字体
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        if hasattr(style, 'fontName'):
            style.fontName = cn_font
    
    return cn_font, styles

def parse_markdown_to_story(md_content, cn_font, styles):
    """
    将Markdown内容解析为reportlab的story对象
    """
    story = []
    
    # 定义样式
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontName=cn_font,
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=20,
    )
    
    heading1_style = ParagraphStyle(
        'CustomHeading1',
        parent=styles['Heading1'],
        fontName=cn_font,
        fontSize=16,
        leading=20,
        spaceAfter=12,
        spaceBefore=12,
    )
    
    heading2_style = ParagraphStyle(
        'CustomHeading2',
        parent=styles['Heading2'],
        fontName=cn_font,
        fontSize=14,
        leading=18,
        spaceAfter=10,
        spaceBefore=10,
    )
    
    heading3_style = ParagraphStyle(
        'CustomHeading3',
        parent=styles['Heading3'],
        fontName=cn_font,
        fontSize=12,
        leading=16,
        spaceAfter=8,
        spaceBefore=8,
    )
    
    body_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=cn_font,
        fontSize=10,
        leading=14,
        spaceAfter=6,
    )
    
    # 按行处理markdown内容
    lines = md_content.split('\n')
    i = 0
    in_code_block = False
    code_lines = []
    
    while i < len(lines):
        line = lines[i].rstrip()
        
        # 处理代码块
        if line.startswith('```'):
            if in_code_block:
                # 结束代码块
                code_text = '\n'.join(code_lines)
                story.append(Paragraph(code_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), body_style))
                story.append(Spacer(1, 6))
                code_lines = []
                in_code_block = False
            else:
                # 开始代码块
                in_code_block = True
            i += 1
            continue
        
        if in_code_block:
            code_lines.append(line)
            i += 1
            continue
        
        # 处理标题
        if line.startswith('# '):
            text = line[2:].strip()
            story.append(Paragraph(text, title_style))
            story.append(Spacer(1, 12))
        elif line.startswith('## '):
            text = line[3:].strip()
            story.append(Paragraph(text, heading1_style))
            story.append(Spacer(1, 10))
        elif line.startswith('### '):
            text = line[4:].strip()
            story.append(Paragraph(text, heading2_style))
            story.append(Spacer(1, 8))
        elif line.startswith('#### '):
            text = line[5:].strip()
            story.append(Paragraph(text, heading3_style))
            story.append(Spacer(1, 6))
        
        # 处理分隔线
        elif line.strip() == '---':
            story.append(Spacer(1, 12))
        
        # 处理列表项
        elif line.strip().startswith('- ') or line.strip().startswith('* '):
            text = line.strip()[2:].strip()
            # 转义HTML特殊字符
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            story.append(Paragraph('• ' + text, body_style))
        
        # 处理数字列表
        elif re.match(r'^\d+\.\s', line.strip()):
            text = re.sub(r'^\d+\.\s', '', line.strip())
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            story.append(Paragraph(text, body_style))
        
        # 处理空行
        elif line.strip() == '':
            story.append(Spacer(1, 6))
        
        # 处理普通段落
        else:
            if line.strip():
                # 转义HTML特殊字符
                text = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                # 处理粗体 **text** 或 __text__
                text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
                text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
                # 处理斜体 *text* 或 _text_
                text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
                text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)
                story.append(Paragraph(text, body_style))
        
        i += 1
    
    return story

def convert_md_to_pdf(input_file, output_file):
    """
    将Markdown文件转换为PDF
    """
    # 读取Markdown文件
    print(f"正在读取文件: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        md_content = f.read()
    
    print(f"文件大小: {len(md_content)} 字符")
    
    # 设置中文字体
    print("正在配置中文字体...")
    cn_font, styles = setup_chinese_pdf()
    print(f"使用字体: {cn_font}")
    
    # 创建PDF文档
    print(f"正在创建PDF: {output_file}")
    doc = SimpleDocTemplate(
        output_file,
        pagesize=A4,
        leftMargin=2*cm,
        rightMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    # 解析Markdown内容
    print("正在解析Markdown内容...")
    story = parse_markdown_to_story(md_content, cn_font, styles)
    
    # 生成PDF
    print("正在生成PDF文件...")
    doc.build(story)
    
    print(f"PDF生成成功: {output_file}")
    print(f"文件大小: {os.path.getsize(output_file)} 字节")

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("使用方法: python convert_md_to_pdf.py <input_md_file> <output_pdf_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在: {input_file}")
        sys.exit(1)
    
    convert_md_to_pdf(input_file, output_file)
