# -*- coding: utf-8 -*-
"""HTML 转 PDF（使用 weasyprint）"""
import sys, os

try:
    from weasyprint import HTML, CSS
except ImportError:
    print("安装 weasyprint: pip install weasyprint")
    sys.exit(1)

def html_to_pdf(html_path, pdf_path=None):
    if pdf_path is None:
        pdf_path = html_path.replace('.html', '.pdf')

    # 读取HTML
    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # 生成PDF
    HTML(string=html_content, base_url=os.path.dirname(html_path)).write_pdf(pdf_path)
    print(f"PDF生成完成: {pdf_path}")
    return pdf_path

if __name__ == '__main__':
    html_path = sys.argv[1] if len(sys.argv) > 1 else None
    pdf_path = sys.argv[2] if len(sys.argv) > 2 else None

    if html_path and os.path.exists(html_path):
        html_to_pdf(html_path, pdf_path)
    else:
        print("用法: python _html_to_pdf.py <html文件> [输出pdf]")
