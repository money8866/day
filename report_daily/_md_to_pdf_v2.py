# -*- coding: utf-8 -*-
import re, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_PATH = r'C:\Windows\Fonts\msyh.ttc'
pdfmetrics.registerFont(TTFont('YaHei', FONT_PATH))
pdfmetrics.registerFont(TTFont('YaHeiBold', FONT_PATH))

def clean(text):
    text = re.sub(r'<span[^>]*>', '', text)
    text = re.sub(r'</span>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text

def draw_page_header(c, page_num, title_color):
    c.setFont('YaHei', 7.5)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(margin, page_height - 13*mm, 'A股每日复盘 2026-07-09 | QClaw 量化系统')
    c.drawRightString(page_width - margin, page_height - 13*mm, f'第{page_num}页')

def new_page(c, page_num):
    c.showPage()
    draw_page_header(c, page_num, HEADER_COLOR)
    return page_height - 22*mm

# ── 读取内容 ──
with open(r'D:\mystock\report_daily\Final_Self_20260709.md', 'r', encoding='utf-8') as f:
    raw = f.read()
content = clean(raw)

# ── PDF 初始化 ──
out_path = r'D:\mystock\report_daily\Final_Self_20260709.pdf'
page_width, page_height = A4
margin = 18 * mm
content_width = page_width - 2 * margin
HEADER_COLOR = (0.12, 0.22, 0.55)
ACCENT_RED    = (0.80, 0.12, 0.10)
ACCENT_GREEN  = (0.05, 0.45, 0.10)
ACCENT_BLUE   = (0.05, 0.30, 0.65)
TEXT_DARK     = (0.12, 0.12, 0.12)
TEXT_GRAY     = (0.38, 0.38, 0.38)
STOCK_BLUE    = (0.05, 0.28, 0.62)

c = canvas.Canvas(out_path, pagesize=A4)
c.setTitle('每日复盘 20260709')
c.setAuthor('QClaw')

# ── 封面 ──
c.setFillColorRGB(*HEADER_COLOR)
c.rect(0, page_height * 0.55, page_width, page_height * 0.45, fill=1, stroke=0)

c.setFillColorRGB(1, 1, 1)
c.setFont('YaHei', 26)
c.drawCentredString(page_width / 2, page_height - 65*mm, '每日复盘报告')
c.setFont('YaHei', 14)
c.drawCentredString(page_width / 2, page_height - 76*mm, '2026年7月9日 星期四')
c.setFont('YaHei', 9)
c.drawCentredString(page_width / 2, page_height - 84*mm, '量化多策略系统 | 仅供参考，不构成投资建议')

# 封面数据卡片
card_y = page_height - 110*mm
c.setFillColorRGB(0.95, 0.95, 0.97)
c.roundRect(margin, card_y - 30*mm, content_width, 28*mm, 4*mm, fill=1, stroke=0)
c.setFillColorRGB(*HEADER_COLOR)
c.setFont('YaHei', 10)
c.drawString(margin + 5*mm, card_y, '大盘状态')
c.setFont('YaHei', 8)
c.setFillColorRGB(*TEXT_GRAY)
c.drawString(margin + 5*mm, card_y - 6*mm, '仓位建议：40%   |   市场阶段：震荡   |   核心：半导体全线抱团')
c.setFillColorRGB(*ACCENT_RED)
c.setFont('YaHei', 9)
c.drawString(margin + 5*mm, card_y - 14*mm, '最强主线：先进封装 > 半导体设备 > AI芯片')
c.setFillColorRGB(*TEXT_GRAY)
c.setFont('YaHei', 7.5)
c.drawString(margin + 5*mm, card_y - 21*mm, '更新时间：2026-07-09 收盘  |  数据来源：通达信 + Tushare')

c.showPage()
page_num = 1
draw_page_header(c, page_num, HEADER_COLOR)
y = page_height - 22*mm

# ── 解析章节 ──
# 按 ## 数字/标题 分割
parts = re.split(r'(?=##\s)', content)
# 也按 **数字 分割
section_splits = re.split(r'(?=\*{0,2}[一二三四五六七八九十]+[、、.]\s)', content)

def parse_sections(text):
    """把内容拆成章节列表"""
    # 先按 ## 拆分
    sections = []
    # 尝试多种分隔
    for sep in ['\n## ', '\n**']:
        if sep in text:
            parts = text.split(sep)
            for i, p in enumerate(parts):
                if i == 0: 
                    if p.strip():
                        sections.append(('前言', p.strip()))
                else:
                    # 提取标题
                    m = re.match(r'([一二三四五六七八九十]+[、、.][^\n*]+)', p)
                    if m:
                        title = m.group(1).strip()
                        body = p[len(m.group(0)):].strip()
                        sections.append((clean(title), clean(body)))
                    else:
                        sections.append(('其他', p.strip()))
            break
    return sections

sections = parse_sections(content)

def get_section_color(title):
    if any(k in title for k in ['大盘', '情绪']):
        return HEADER_COLOR
    if any(k in title for k in ['主题', '板块']):
        return (0.60, 0.22, 0.10)
    if any(k in title for k in ['强势', '股票池', '企稳', '操作']):
        return ACCENT_RED
    if any(k in title for k in ['ETF', '仓位']):
        return ACCENT_GREEN
    if any(k in title for k in ['波浪', 'W3', '第3']):
        return (0.45, 0.10, 0.60)
    return TEXT_DARK

def draw_section_title(c, title, color, y):
    c.setFillColorRGB(*color)
    c.setFont('YaHei', 11)
    c.drawString(margin, y, title)
    y -= 3
    c.setStrokeColorRGB(*color)
    c.setLineWidth(1.2)
    c.line(margin, y, margin + content_width, y)
    return y - 8

def draw_para(c, text, y, x=margin, width=content_width, size=9.0, line_h=12.5, max_chars=None):
    """绘制一个段落，处理颜色和换行"""
    if max_chars is None:
        max_chars = int(width / (size * 0.58))
    c.setFont('YaHei', size)
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            y -= line_h * 0.4
            continue
        line = clean(line)
        if not line:
            continue
        # 颜色
        if '【重要提醒】' in line or '⚠' in line:
            c.setFillColorRGB(*ACCENT_RED)
        elif any(k in line for k in ['强买信号', '首选', '高优先', '⭐⭐⭐']):
            c.setFillColorRGB(0.75, 0.15, 0.05)
        elif any(k in line for k in ['观望', '谨慎', '低吸']):
            c.setFillColorRGB(*ACCENT_GREEN)
        elif re.search(r'\d{6}[.。](SZ|SH|BJ)', line):
            c.setFillColorRGB(*STOCK_BLUE)
        elif line.startswith('**') or re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', line):
            c.setFillColorRGB(0.1, 0.1, 0.1)
            c.setFont('YaHeiBold', size + 0.5)
        else:
            c.setFillColorRGB(*TEXT_DARK)
            c.setFont('YaHei', size)
        # 换行
        while len(line) > max_chars:
            if y < 40:
                return y, False
            c.drawString(x, y, line[:max_chars])
            y -= line_h
            line = line[max_chars:]
        if line:
            if y < 40:
                return y, False
            c.drawString(x, y, line)
            y -= line_h
    return y, True

def draw_stock_card(c, lines, y, color):
    """绘制个股卡片"""
    card_h = len(lines) * 12.5 + 10
    if y - card_h < 30:
        y = new_page(c, page_num := page_num + 1)
    c.setStrokeColorRGB(*color)
    c.setLineWidth(0.8)
    c.setFillColorRGB(0.97, 0.97, 0.99)
    c.roundRect(margin, y - card_h + 4, content_width, card_h - 4, 3*mm, fill=1, stroke=1)
    # 左侧色条
    c.setFillColorRGB(*color)
    c.rect(margin, y - card_h + 4, 3, card_h - 4, fill=1, stroke=0)
    ty = y
    for line in lines:
        ty, _ = draw_para(c, line, ty, margin + 6, content_width - 6, size=8.5, line_h=12)
    return ty - 3

for sec_title, sec_body in sections:
    if not sec_body or len(sec_body) < 5:
        continue
    
    color = get_section_color(sec_title)
    
    # 检查高度
    lines_est = sec_body.count('\n') + 8
    need_h = lines_est * 12.5 + 20
    if y - need_h < 30*mm:
        y = new_page(c, page_num := page_num + 1)
    
    # 章节标题
    y = draw_section_title(c, sec_title, color, y)
    
    # 分析内容，提取个股块
    paras = sec_body.split('\n')
    i = 0
    while i < len(paras):
        para = paras[i].strip()
        if not para:
            i += 1
            y -= 5
            continue
        para = clean(para)
        if not para:
            i += 1
            continue
        
        # 检测个股卡片（以**【...】开头或含有 6位代码）
        is_stock = re.search(r'\*{0,2}【[^】]+】', para) or re.search(r'\d{6}[.。](SZ|SH|BJ)', para)
        is_big = para.startswith('**') and len(para) < 100 and '：' in para
        
        if is_stock and i + 3 < len(paras):
            # 收集卡片内容
            card_lines = [para]
            j = i + 1
            while j < len(paras) and len(paras[j].strip()) > 0:
                pl = clean(paras[j].strip())
                if pl:
                    # 检测是否到下一个股票或新章节
                    if re.match(r'\*{0,2}[一二三四五六七八九十]+[、][^【]*$', pl):
                        break
                    if pl.startswith('##'):
                        break
                    card_lines.append(pl)
                j += 1
            # 选择颜色
            if '首选' in para or '首选' in sec_title:
                card_col = ACCENT_RED
            elif '强买' in para or '企稳' in sec_title:
                card_col = (0.75, 0.30, 0.0)
            elif 'ETF' in sec_title:
                card_col = ACCENT_GREEN
            else:
                card_col = (0.4, 0.4, 0.6)
            y = draw_stock_card(c, card_lines, y, card_col)
            i = j
        elif is_big:
            # 粗体标题行
            c.setFillColorRGB(*color)
            c.setFont('YaHeiBold', 9.5)
            c.drawString(margin + 3, y, para)
            y -= 13
            i += 1
        else:
            y, _ = draw_para(c, para, y)
            i += 1
    
    y -= 8

c.save()
print(f"PDF生成完成: {out_path}")
