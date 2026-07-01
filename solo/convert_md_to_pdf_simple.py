"""
将Markdown报告转换为PDF
"""
import os
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

def markdown_to_pdf(md_file, pdf_file):
    """将Markdown转换为PDF（简化版）"""
    # 读取Markdown
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 创建PDF
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
    # 处理内容
    lines = content.split('\n')
    
    y = height - 2*cm
    c.setFont(FONT_NAME, 10)
    
    for line in lines:
        # 跳过空行
        if not line.strip():
            y -= 0.3*cm
            continue
        
        # 标题处理
        if line.startswith('# '):
            c.setFont(FONT_NAME, 16)
            c.drawString(2*cm, y, line[2:])
            c.setFont(FONT_NAME, 10)
            y -= 0.8*cm
        elif line.startswith('## '):
            c.setFont(FONT_NAME, 14)
            c.drawString(2*cm, y, line[3:])
            c.setFont(FONT_NAME, 10)
            y -= 0.6*cm
        elif line.startswith('### '):
            c.setFont(FONT_NAME, 12)
            c.drawString(2*cm, y, line[4:])
            c.setFont(FONT_NAME, 10)
            y -= 0.5*cm
        else:
            # 普通文本（简化：去掉Markdown格式）
            text = line.replace('**', '').replace('*', '')
            c.drawString(2*cm, y, text[:80])  # 限制长度
            y -= 0.4*cm
        
        # 换页
        if y < 2*cm:
            c.showPage()
            y = height - 2*cm
            c.setFont(FONT_NAME, 10)
    
    c.save()
    print(f'PDF已生成: {pdf_file}')

def main():
    md_file = r'D:\mystock\solo\trend_feature_output\logged_stocks_top20_latest.md'
    pdf_file = r'D:\mystock\solo\trend_feature_output\logged_stocks_top20_latest.pdf'
    
    print('转换Markdown到PDF...')
    markdown_to_pdf(md_file, pdf_file)
    print('完成！')
    
    return pdf_file

if __name__ == '__main__':
    main()
