"""
将Markdown报告转换为PDF - 简化版
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

def markdown_to_pdf_simple(md_file, pdf_file):
    """简化版：只转换纯文本（不做完整Markdown解析）"""
    # 读取Markdown
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 创建PDF
    c = canvas.Canvas(pdf_file, pagesize=A4)
    width, height = A4
    
    # 设置字体
    c.setFont(FONT_NAME, 10)
    
    # 简单处理：按行输出
    y = height - 2*cm
    lines = content.split('\n')
    
    for line in lines:
        # 跳过空行
        if not line.strip():
            y -= 0.3*cm
            continue
        
        # 限制行长度
        text = line[:90] if len(line) > 90 else line
        
        # 输出
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
    print(f'PDF已生成: {pdf_file}')

def main():
    md_file = r'D:\mystock\solo\trend_feature_output\logged_stocks_20260623_latest.md'
    pdf_file = r'D:\mystock\solo\trend_feature_output\logged_stocks_20260623_latest.pdf'
    
    print('转换Markdown到PDF...')
    markdown_to_pdf_simple(md_file, pdf_file)
    print('完成！')
    
    return pdf_file

if __name__ == '__main__':
    main()
