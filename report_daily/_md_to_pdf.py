# -*- coding: utf-8 -*-
import re, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader

FONT_PATH = r'C:\Windows\Fonts\msyh.ttc'  # 微软雅黑

# 注册中文字体
try:
    pdfmetrics.registerFont(TTFont('YaHei', FONT_PATH))
    print("字体注册成功")
except Exception as e:
    print("字体注册失败:", e)
    exit(1)

def clean_html(text):
    """清理HTML标签"""
    text = re.sub(r'<span[^>]*>', '', text)
    text = re.sub(r'</span>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def wrap_text(text, width_pt, font_name, font_size):
    """按字符换行"""
    lines = []
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            lines.append('')
            continue
        chars_per_line = int(width_pt / (font_size * 0.6))
        while len(para) > chars_per_line:
            lines.append(para[:chars_per_line])
            para = para[chars_per_line:]
        if para:
            lines.append(para)
    return lines

def draw_text_block(c, x, y, text, width_pt, font_name, font_size, line_height, color=(0,0,0)):
    """绘制文本块，返回新y位置"""
    c.setFont(font_name, font_size)
    c.setFillColorRGB(*color)
    current_y = y
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            current_y -= line_height * 0.5
            continue
        # 清理HTML
        para = clean_html(para)
        if not para:
            continue
        # 标题检测
        is_title = para.startswith('#') or para.startswith('**') or re.match(r'^[一二三四五六七八九十]+[、.]', para)
        if is_title and font_size <= 10:
            c.setFont(font_name, font_size + 1)
        else:
            c.setFont(font_name, font_size)
        # 颜色高亮
        if '【重要提醒】' in para or '⚠' in para or '🔴' in para:
            c.setFillColorRGB(0.85, 0.15, 0.15)
        elif '强买' in para or '首选' in para or '高优先' in para:
            c.setFillColorRGB(0.8, 0.2, 0.0)
        elif '低吸' in para or '观望' in para:
            c.setFillColorRGB(0.0, 0.4, 0.0)
        else:
            c.setFillColorRGB(0, 0, 0)
        chars_per_line = int(width_pt / (font_size * 0.58))
        while len(para) > chars_per_line:
            line_text = para[:chars_per_line]
            # 避免在中间截断股票代码
            if current_y < 50:
                return current_y
            c.drawString(x, current_y, line_text)
            current_y -= line_height
            para = para[chars_per_line:]
        if para:
            if current_y < 50:
                return current_y
            c.drawString(x, current_y, para)
            current_y -= line_height
    return current_y

# 读取md文件
with open(r'D:\mystock\report_daily\Final_Self_20260709.md', 'r', encoding='utf-8') as f:
    content = f.read()

# 清理HTML
content = clean_html(content)

# 生成PDF
out_path = r'D:\mystock\report_daily\Final_Self_20260709.pdf'
page_width, page_height = A4
margin = 20 * mm
content_width = page_width - 2 * margin

c = canvas.Canvas(out_path, pagesize=A4)
c.setTitle('每日复盘 20260709')
c.setAuthor('QClaw')
c.setSubject('A股每日复盘报告')

def new_page(c, page_num=1):
    c.showPage()
    c.setFont('YaHei', 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(margin, page_height - 15*mm, 'A股每日复盘 2026-07-09 | QClaw')
    c.drawRightString(page_width - margin, page_height - 15*mm, f'第{page_num}页')
    return page_height - 25*mm

# 封面
c.setFont('YaHei', 20)
c.setFillColorRGB(0.15, 0.25, 0.65)
c.drawString(margin, page_height - 50*mm, '每日复盘报告')
c.setFont('YaHei', 14)
c.setFillColorRGB(0.3, 0.3, 0.3)
c.drawString(margin, page_height - 60*mm, '2026年7月9日 星期四')
c.setFont('YaHei', 10)
c.drawString(margin, page_height - 70*mm, '基于量化多策略系统 | 仅供参考，不构成投资建议')
c.showPage()

# 分割章节
sections = re.split(r'\n(?=\*{0,2}[一二三四五六七八九十]+[、、]|\*{0,2}[0-9]+[.、])', content)
page_num = 1
y = new_page(c, page_num)

section_titles = []
font_size = 9.5
line_height = font_size + 3
section_pattern = re.compile(r'^(\*{0,2}[一二三四五六七八九十]+[、、.][^\n]+)')

for section in sections:
    section = section.strip()
    if not section:
        continue

    # 提取小标题
    title_match = section_pattern.match(section)
    if title_match:
        title = clean_html(title_match.group(1))
        # 检测颜色
        if '大盘' in title or '情绪' in title:
            title_color = (0.15, 0.25, 0.65)
        elif '主题' in title:
            title_color = (0.65, 0.25, 0.15)
        elif '强势' in title or '股票池' in title:
            title_color = (0.75, 0.15, 0.1)
        elif 'ETF' in title:
            title_color = (0.1, 0.5, 0.2)
        else:
            title_color = (0.2, 0.2, 0.2)
    else:
        title = None

    # 检查是否需要换页
    lines_count = section.count('\n') + section.count('  ') + 10
    estimated_height = lines_count * line_height

    if y - estimated_height < 40*mm:
        y = new_page(c, page_num + 1)
        page_num += 1

    # 绘制标题
    if title:
        c.setFont('YaHei', 11)
        c.setFillColorRGB(*title_color)
        c.drawString(margin, y, title)
        y -= line_height + 2
        # 分隔线
        c.setStrokeColorRGB(*title_color)
        c.setLineWidth(0.5)
        c.line(margin, y + 2, margin + content_width, y + 2)
        y -= line_height

    # 绘制正文
    for line in section.split('\n'):
        line = line.strip()
        if not line:
            y -= line_height * 0.3
            continue

        line = clean_html(line)
        if not line:
            continue

        # 判断行类型
        is_stock = re.search(r'\d{6}[.。](SZ|SH|BJ|HK)', line)
        is_bold = line.startswith('**') or re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', line)
        is_quote = line.startswith('>')

        # 颜色
        if '【重要提醒】' in line:
            c.setFillColorRGB(0.85, 0.1, 0.1)
            c.setFont('YaHei', font_size)
        elif '强买' in line or '首选' in line or '高优先' in line or '⭐' in line:
            c.setFillColorRGB(0.8, 0.2, 0.0)
            c.setFont('YaHei', font_size)
        elif '低吸' in line or '观望' in line or '谨慎' in line:
            c.setFillColorRGB(0.05, 0.45, 0.05)
            c.setFont('YaHei', font_size)
        elif is_stock:
            c.setFillColorRGB(0.0, 0.3, 0.6)
            c.setFont('YaHei', font_size)
        elif is_bold:
            c.setFillColorRGB(0.1, 0.1, 0.1)
            c.setFont('YaHei', font_size + 0.5)
        else:
            c.setFillColorRGB(0.15, 0.15, 0.15)
            c.setFont('YaHei', font_size)

        # 换行
        max_chars = int(content_width / (font_size * 0.58))
        while len(line) > max_chars:
            if y < 50:
                y = new_page(c, page_num + 1)
                page_num += 1
            c.drawString(margin, y, line[:max_chars])
            y -= line_height
            line = line[max_chars:]

        if line:
            if y < 50:
                y = new_page(c, page_num + 1)
                page_num += 1
            c.drawString(margin, y, line)
            y -= line_height

c.save()
print(f"PDF已生成: {out_path}")
