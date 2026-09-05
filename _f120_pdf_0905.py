# -*- coding: utf-8 -*-
"""f120_report_20260901.md -> PDF"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle

FONT = 'Chinese'
pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))
pdfmetrics.registerFont(TTFont(FONT+'B', r'C:\Windows\Fonts\msyhbd.ttc'))
pdfmetrics.registerFont(TTFont('Mono', r'C:\Windows\Fonts\cour.ttf'))

# 样式
def S(name, **kw):
    return ParagraphStyle(name, **kw)

H1 = S('H1', fontName=FONT+'B', fontSize=14, leading=18,
        textColor=colors.HexColor('#1a3a8a'), spaceBefore=8, spaceAfter=4)
H2 = S('H2', fontName=FONT+'B', fontSize=11, leading=15,
        textColor=colors.HexColor('#1a3a8a'), spaceBefore=6, spaceAfter=3)
H3 = S('H3', fontName=FONT+'B', fontSize=9, leading=12,
        textColor=colors.HexColor('#2c5f9e'), spaceBefore=4, spaceAfter=2)
BODY = S('BODY', fontName=FONT, fontSize=8.5, leading=11,
        textColor=colors.black, spaceAfter=2)
SMALL = S('SMALL', fontName=FONT, fontSize=7.5, leading=9.5,
        textColor=colors.black, spaceAfter=1)
RED = S('RED', fontName=FONT+'B', fontSize=8.5, leading=11,
        textColor=colors.HexColor('#c0392b'))
GREEN = S('GREEN', fontName=FONT+'B', fontSize=8.5, leading=11,
        textColor=colors.HexColor('#27ae60'))
BLUE = S('BLUE', fontName=FONT+'B', fontSize=8.5, leading=11,
        textColor=colors.HexColor('#1a3a8a'))
MONO = S('MONO', fontName='Mono', fontSize=7, leading=9,
        textColor=colors.HexColor('#333333'))

# 颜色常量
NAVY = colors.HexColor('#1a3a8a')
BLUE2 = colors.HexColor('#2c5f9e')
RED_C = colors.HexColor('#e74c3c')
GREEN_C = colors.HexColor('#27ae60')
PURPLE = colors.HexColor('#8e44ad')
ORANGE = colors.HexColor('#e67e22')
GRAY_L = colors.HexColor('#f0f4f8')
GRAY = colors.HexColor('#cccccc')

def row(data, col_widths, hdr_color=NAVY, fs=8):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), hdr_color),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('FONTNAME', (0,0), (-1,-1), FONT),
        ('FONTSIZE', (0,0), (-1,-1), fs),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
        ('GRID', (0,0), (-1,-1), 0.4, GRAY),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    return t

src = r'D:\mystock\solo\sli\output\f120_report_20260901.md'
out = r'D:\mystock\solo\sli\output\f120_report_20260901.pdf'

doc = SimpleDocTemplate(out, pagesize=A4,
    leftMargin=15*mm, rightMargin=15*mm,
    topMargin=15*mm, bottomMargin=15*mm,
    title='F120 V1.1 中报TOP5报告 20260901')

story = []

def p(text, style=BODY):
    return Paragraph(text, style)

def hr(color=GRAY):
    return HRFlowable(width='100%', thickness=0.5, color=color, spaceAfter=4, spaceBefore=4)

def parse_md(text):
    """简单markdown解析"""
    lines = text.split('\n')
    in_table = False
    table_rows = []
    result = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯分隔线
        if stripped in ('---', '--', '***', '***'):
            continue
        # 标题
        if stripped.startswith('# '):
            result.append(p(stripped[2:], H1))
        elif stripped.startswith('## '):
            result.append(hr())
            result.append(p(stripped[3:], H2))
        elif stripped.startswith('### '):
            result.append(p(stripped[4:], H3))
        # 列表项
        elif stripped.startswith('- **'):
            # - **粗体**: 内容
            parts = stripped[2:].split('**:', 1)
            if len(parts) == 2:
                result.append(p('<b>'+parts[0].strip()+'</b>: '+parts[1].strip(), BODY))
            else:
                result.append(p(stripped, BODY))
        elif stripped.startswith('- '):
            result.append(p(stripped, BODY))
        # 引用/分隔
        elif stripped.startswith('>'):
            result.append(p(stripped[1:].strip(), SMALL))
        # 表格（简化处理）
        elif '|' in stripped:
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            if all(c in ('---', '--', '|') or c.startswith(':') for c in cells):
                continue  # 分隔行
            table_rows.append(cells)
        else:
            # 普通段落，去掉markdown符号
            t = stripped.replace('**', '').replace('*', '').replace('`', '')
            if t:
                # 高亮
                if 'PRIMARY' in t:
                    result.append(p(t, RED))
                elif 'CONDITIONAL' in t:
                    result.append(p(t, BLUE))
                elif 'WATCH' in t:
                    result.append(p(t, GREEN))
                elif 'AVOID' in t:
                    result.append(p(t, SMALL))
                else:
                    result.append(p(t, BODY))
    return result

with open(src, encoding='utf-8') as f:
    content = f.read()

# 按第一级标题分割
sections = content.split('\n## ')
story.append(p('F120 V1.1 中报TOP5 × T+1择时 × T+60~120 报告（20260901）', H1))
story.append(hr(NAVY))

# 第一行市场状态高亮
for line in content.split('\n')[:15]:
    ls = line.strip()
    if 'BEAR' in ls or 'BULL' in ls or 'NEUTRAL' in ls or '市场状态' in ls or '仓位' in ls or '策略' in ls:
        if ls.startswith('- **'):
            parts = ls[2:].split('**:', 1)
            if len(parts) == 2:
                if any(k in parts[1] for k in ['BEAR','BULL','NEUTRAL','15%','30%','50%','70%','90%']):
                    story.append(p('<b>'+parts[0]+'</b>: '+parts[1], RED if 'BEAR' in ls else BODY))
                else:
                    story.append(p('<b>'+parts[0]+'</b>: '+parts[1], BODY))
        elif ls.startswith('## '):
            story.append(hr())
            story.append(p(ls[3:], H2))
        elif ls.startswith('# '):
            story.append(p(ls[2:], H1))
        elif ls:
            story.append(p(ls, BODY))

# 关键部分：PRIMARY BUY
story.append(hr(NAVY))
primary_start = content.find('## 第三部分 PRIMARY BUY')
cond_start = content.find('## 第四部分 CONDITIONAL BUY')
watch_start = content.find('## 第五部分 WATCH TOP10')
avoid_start = content.find('## 第六部分 AVOID')
exec_start = content.find('## 第七部分 T+1 EXECUTION')

if primary_start > 0:
    end = cond_start if cond_start > 0 else (watch_start if watch_start > 0 else len(content))
    sec = content[primary_start:end]
    story.append(hr())
    story.append(p('第三部分 PRIMARY BUY', H2))
    for ln in sec.split('\n'):
        ls = ln.strip()
        if not ls or ls.startswith('##') or ls.startswith('---'):
            continue
        if '|' in ls:
            cells = [c.strip() for c in ls.split('|')[1:-1]]
            if len(cells) >= 4:
                story.append(p('  '.join(cells[:5]), SMALL if len(cells[0]) < 5 else BODY))
        elif ls.startswith('|'):
            continue
        else:
            t = ls.replace('**','').strip()
            if t:
                story.append(p(t, RED if 'BUY' in t else BODY))

if cond_start > 0:
    end = watch_start if watch_start > 0 else (avoid_start if avoid_start > 0 else len(content))
    sec = content[cond_start:end]
    story.append(hr())
    story.append(p('第四部分 CONDITIONAL BUY', H2))
    for ln in sec.split('\n'):
        ls = ln.strip()
        if not ls or ls.startswith('##') or ls.startswith('---'):
            continue
        if '|' in ls:
            cells = [c.strip() for c in ls.split('|')[1:-1]]
            if len(cells) >= 4:
                story.append(p('  '.join(cells[:4]), SMALL))
        elif ls.startswith('|'):
            continue
        else:
            t = ls.replace('**','').strip()
            if t:
                story.append(p(t, BLUE if any(k in t for k in ['CONDITIONAL','等待','买点']) else BODY))

# 第七部分 T+1 EXECUTION
if exec_start > 0:
    sec = content[exec_start:]
    story.append(hr())
    story.append(p('第七部分 T+1 EXECUTION', H2))
    blocks = sec.split('【F120 T+1 EXECUTION】')
    for block in blocks[1:]:
        stock_name = ''
        lines_b = block.strip().split('\n')
        # 找股票名
        for ln in lines_b[:10]:
            if '股票：' in ln:
                stock_name = ln.split('：',1)[1].split('（')[0].strip()
                break
        if not stock_name:
            stock_name = lines_b[0].split('\n')[0][:20]
        # 截取关键行
        key_lines = []
        capture = False
        for ln in lines_b:
            ls = ln.strip()
            if any(k in ls for k in ['Current', 'BUY ZONE', 'STOP', 'BUY TYPE', '仓位', '一句话结论', 'T+1操作']):
                t = ls.replace('**','').replace('*','').strip()
                if t:
                    key_lines.append(t)
        if key_lines:
            story.append(KeepTogether([
                p(f'<b>◆ {stock_name}</b>', H3),
                *[p('  ' + l, SMALL) for l in key_lines[:8]],
                Spacer(1, 3)
            ]))

# 风险提示页脚
story.append(hr(GRAY))
story.append(p('⚠️ 风险提示：以上内容仅供信息参考，不构成投资建议。市场有风险，投资需谨慎。F120系统为量化模型，存在模型风险。', SMALL))

doc.build(story)
print('PDF生成成功:', out, os.path.getsize(out), 'bytes')
