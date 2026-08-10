# -*- coding: utf-8 -*-
"""市场状态报告 market_report PDF 生成器"""
import os, re, datetime

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, HRFlowable, KeepTogether)
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

FONT = 'Chinese'
for fp in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont(FONT, fp)); break
        except: pass

C_MAIN   = colors.HexColor('#1a3a8a')
C_ACCENT = colors.HexColor('#2c5f9e')
C_OK     = colors.HexColor('#27ae60')
C_WARN   = colors.HexColor('#e67e22')
C_DANGER = colors.HexColor('#e74c3c')
C_FOOT   = colors.HexColor('#888888')

def S(name, **kw):
    d = dict(fontName=FONT, leading=14); d.update(kw)
    return ParagraphStyle(name, **d)

Ss = {
    'title':   S('T',  fontSize=17, alignment=TA_CENTER, textColor=C_MAIN, spaceAfter=4),
    'sub':     S('St', fontSize=9,  alignment=TA_CENTER, textColor=C_FOOT,  spaceAfter=8),
    'h3':      S('H3', fontSize=10, textColor=C_ACCENT, spaceBefore=6, spaceAfter=2, leading=13),
    'body':    S('B',  fontSize=9,  textColor=colors.HexColor('#222'), leading=13, spaceAfter=2),
    'key':     S('K',  fontSize=9,  textColor=C_MAIN, leading=13, fontName=FONT),
    'footer':  S('F',  fontSize=8,  textColor=C_FOOT, alignment=TA_CENTER),
}

def sp(h=0.2): return Spacer(1, h*cm)
def hr(c=C_MAIN, t=1): return HRFlowable(width='100%', thickness=t, color=c, spaceAfter=3)

def sec_bar(title, color=C_MAIN):
    t = Table([[Paragraph(title, S('SB', fontSize=12, textColor=colors.white,
                                  fontName=FONT, leading=16))]],
              colWidths=[18.4*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),color),
        ('TOPPADDING',(0,0),(-1,-1),6), ('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LEFTPADDING',(0,0),(-1,-1),10),
    ]))
    return t

def table2(data, cw, color=C_ACCENT, fs=8.5):
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),color),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('FONTNAME',(0,0),(-1,-1),FONT),
        ('FONTSIZE',(0,0),(-1,-1),fs),
        ('ALIGN',(0,0),(-1,-1),'CENTER'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
        ('LEFTPADDING',(0,0),(-1,-1),4), ('RIGHTPADDING',(0,0),(-1,-1),4),
        ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),
         [colors.HexColor('#f8f9fa'),colors.HexColor('#eef1f5')]),
    ]))
    return t

def kv4(data, color=C_ACCENT):
    """4列指标表格"""
    t = Table(data, colWidths=[3.2*cm]*4)
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),color),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('FONTNAME',(0,0),(-1,-1),FONT), ('FONTSIZE',(0,0),(-1,-1),8.5),
        ('ALIGN',(0,0),(-1,-1),'LEFT'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3),
        ('LEFTPADDING',(0,0),(-1,-1),5), ('RIGHTPADDING',(0,0),(-1,-1),3),
        ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),
         [colors.HexColor('#f8f9fa'),colors.HexColor('#eef1f5')]),
    ]))
    return t

def para(t, style='body'):
    return Paragraph(esc(str(t)), Ss.get(style, Ss['body']))

def esc(t):
    return t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def detect_color(title):
    t = title.lower()
    if any(k in t for k in ['指数','宽度','情绪','风格','主题','龙头','市场']): return C_ACCENT
    if any(k in t for k in ['仓位','风控','操作','买卖','position','entry']): return C_OK
    if any(k in t for k in ['风险','danger']): return C_DANGER
    if any(k in t for k in ['候选','标的','个股']): return C_WARN
    return C_MAIN

def parse_md_blocks(text):
    """把markdown文本拆成块：table / kv / prose"""
    blocks = []
    # 逐行扫描，合并连续的markdown表格行
    lines = text.strip().split('\n')
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        # 跳过分隔线
        if re.match(r'[-:|\s]+$', ln) or ln.startswith('---'):
            i += 1; continue
        # markdown表格行
        if ln.startswith('|'):
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                t = lines[i].strip()
                if not re.match(r'[-:|\s]+$', t):
                    tbl_lines.append(t)
                i += 1
            if tbl_lines:
                blocks.append(('table', tbl_lines))
            continue
        # 键值对行（bullet list）
        t = ln.lstrip('-*•:： ')
        if (':' in t or '：' in t) and len(t) < 200:
            sep = ':' if ':' in t else '：'
            k = t.split(sep)[0].strip()
            v = sep.join(t.split(sep)[1:]).strip()
            if k and v and not k.startswith('#'):
                blocks.append(('kv', (k, v)))
            else:
                blocks.append(('text', ln))
        else:
            blocks.append(('text', ln))
        i += 1
    return blocks

def render_blocks(blocks, color, story):
    """把块列表渲染成PDF元素"""
    # 合并连续的 kv
    kv_rows = []
    i = 0
    while i < len(blocks):
        typ, val = blocks[i]
        if typ == 'kv':
            kv_rows.append(list(val))
            # 看下一个是否还是kv
            j = i + 1
            while j < len(blocks) and blocks[j][0] == 'kv':
                kv_rows.append(list(blocks[j][1]))
                j += 1
            # 渲染kv（4列）
            if kv_rows:
                half = (len(kv_rows)+1)//2
                r1, r2 = kv_rows[:half], kv_rows[half:]
                while len(r2) < len(r1): r2.append((' ', ' '))
                data = [['指标','数值','指标','数值']]
                for (k1,v1),(k2,v2) in zip(r1, r2):
                    data.append([esc(k1), esc(v1), esc(k2), esc(v2)])
                story.append(kv4(data, color))
                kv_rows = []
            i = j
            continue
        elif typ == 'table':
            # 解析markdown表格
            hdr_cells = [c.strip() for c in val[0].strip('|').split('|')]
            data_rows = []
            for ln in val[1:]:
                cells = [c.strip() for c in ln.strip('|').split('|')]
                if len(cells) == len(hdr_cells):
                    data_rows.append([esc(c) for c in cells])
            if data_rows:
                cw = [18.4*cm/len(hdr_cells)] * len(hdr_cells)
                story.append(table2([hdr_cells]+data_rows, cw, color))
        else:
            t = val.strip()
            if t.startswith('#### '):
                story.append(para(t[5:].strip(), 'h3'))
            elif t.startswith('**') and t.endswith('**') and t.count(':') <= 1:
                story.append(para(t.strip('*'), 'key'))
            elif t:
                story.append(para(t))
        i += 1

def parse_md_table(lines):
    """解析markdown表格，返回 (header, [rows])"""
    data = []
    for ln in lines:
        t = ln.strip()
        if t.startswith('|') and not re.match(r'\|[-:\s|]+\|', t):
            data.append([c.strip() for c in t.strip('|').split('|')])
    if len(data) < 2: return None, None
    return data[0], data[1:]

def generate(md_path, pdf_path):
    if not os.path.exists(md_path):
        print(f'未找到: {md_path}'); return
    text = open(md_path, encoding='utf-8').read()

    story = []
    story.append(sp(0.3))
    # 标题
    m = re.search(r'#\s+(.+)', text)
    title = m.group(1).strip() if m else '市场状态报告'
    story.append(Paragraph(title, Ss['title']))
    dm = re.search(r'(\d{4})[年\-/](\d{2})[月\-/](\d{2})', text)
    date_str = f'{dm.group(1)}-{dm.group(2)}-{dm.group(3)}' if dm else '—'
    story.append(Paragraph(f'生成日期: {date_str}  |  Market Regime Engine V3', Ss['sub']))
    story.append(hr(C_MAIN, 2))
    story.append(sp(0.1))

    # 按 ## 一级标题分割
    parts = re.split(r'\n##\s+', text)
    for part in parts[1:]:
        lines = part.split('\n')
        sec_title = lines[0].strip()
        sec_body = '\n'.join(lines[1:]).strip()
        color = detect_color(sec_title)

        story.append(KeepTogether([sec_bar(sec_title, color), sp(0.1)]))

        # 个股深度分析（特殊处理）
        if '候选标的' in sec_title or '深度分析' in sec_title:
            render_stock_analysis(sec_body, story)
            continue

        # 普通章节：按块处理
        blocks = parse_md_blocks(sec_body)
        render_blocks(blocks, color, story)
        story.append(sp(0.1))

    # 页脚
    story.append(hr(C_MAIN, 1))
    story.append(Paragraph(
        f'Market Regime Engine V3 · 自动生成 · {datetime.datetime.now():%Y-%m-%d %H:%M}'
        '  |  仅供参考，不构成投资建议', Ss['footer']))

    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
        rightMargin=1.8*cm, leftMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    doc.build(story)
    sz = os.path.getsize(pdf_path)
    print(f'PDF: {pdf_path}  ({sz//1024} KB)')

def render_stock_analysis(text, story):
    """渲染个股深度分析"""
    blocks = re.split(r'\n---\n+', text)
    for blk in blocks:
        blk = blk.strip()
        if not blk: continue
        nm = re.search(r'###\s+[（(]?([^\n（(]+)', blk)
        if not nm: continue
        name = nm.group(1).strip()
        story.append(para(f'▶ {name}', 'key'))
        # 按 ### 分组
        sub_parts = re.split(r'\n####\s+', blk)
        for sub in sub_parts[1:]:
            sl = sub.split('\n')
            sub_title = sl[0].strip()
            sub_body = '\n'.join(sl[1:]).strip()
            story.append(para(sub_title, 'h3'))
            # 找其中的表格
            tbl_m = re.search(r'(\|.+\|\n(?:\|[-:|\s]+\|\n)?(?:\|.+\|\n?)+)', sub_body, re.DOTALL)
            if tbl_m:
                tbl_lines = [ln for ln in tbl_m.group(1).split('\n')
                             if ln.strip() and not re.match(r'\|[-:\s|]+\|', ln.strip())]
                hdr, rows = parse_md_table(tbl_lines)
                if hdr and rows:
                    n = len(hdr)
                    story.append(table2([hdr]+rows, [18.4*cm/n]*n, C_WARN, fs=8))
            # 键值对
            pairs = []
            for ln in sub_body.split('\n'):
                t = ln.strip().lstrip('-*•:： ')
                if (':' in t or '：' in t):
                    sep = ':' if ':' in t else '：'
                    k, v = t.split(sep, 1)
                    pairs.append((k.strip(), v.strip()))
            if pairs:
                half = (len(pairs)+1)//2
                r1, r2 = pairs[:half], pairs[half:]
                while len(r2) < len(r1): r2.append((' ', ' '))
                data = [['指标','数值','指标','数值']]
                for (k1,v1),(k2,v2) in zip(r1, r2):
                    data.append([esc(k1), esc(v1), esc(k2), esc(v2)])
                story.append(kv4(data, C_WARN))
            story.append(sp(0.05))
        story.append(sp(0.1))

if __name__ == '__main__':
    import sys
    md  = sys.argv[1] if len(sys.argv) > 1 else r'D:\mystock\solo\output\market_report_20260807.md'
    pdf = sys.argv[2] if len(sys.argv) > 2 else md.replace('.md', '.pdf')
    generate(md, pdf)
