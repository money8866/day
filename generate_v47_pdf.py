# -*- coding: utf-8 -*-
"""
V4.7 高胜率低回撤精选 PDF报告生成

用法：
  python generate_v47_pdf.py                        # 默认今天
  python generate_v47_pdf.py 20260619               # 指定日期
  python generate_v47_pdf.py v47_best_picks_20260619.json  # 指定文件
"""

import sys, os, json
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR   = r'D:\mystock'
OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'report_daily')

# ========== 注册中文字体 ==========
def setup_chinese_font():
    candidates = [
        ('SimHei', 'C:/Windows/Fonts/simhei.ttf'),
        ('SimSun', 'C:/Windows/Fonts/simsun.ttc'),
        ('MicrosoftYaHei', 'C:/Windows/Fonts/msyh.ttc'),
    ]
    for name, path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                return name
            except:
                continue
    return 'Helvetica'

FONT_NAME = setup_chinese_font()
print(f'[字体] 注册: {FONT_NAME}')

# ========== 样式 ==========
styles = getSampleStyleSheet()

title_style = ParagraphStyle('TitleCN', parent=styles['Title'],
    fontName=FONT_NAME, fontSize=20, spaceAfter=6,
    textColor=colors.HexColor('#1a1a1a'))

subtitle_style = ParagraphStyle('SubCN', parent=styles['Normal'],
    fontName=FONT_NAME, fontSize=11, spaceAfter=4,
    textColor=colors.HexColor('#666666'))

h1 = ParagraphStyle('H1CN', parent=styles['Heading2'],
    fontName=FONT_NAME, fontSize=15, spaceAfter=8, spaceBefore=14,
    textColor=colors.HexColor('#2c3e50'))

h2 = ParagraphStyle('H2CN', parent=styles['Heading3'],
    fontName=FONT_NAME, fontSize=12, spaceAfter=6, spaceBefore=10,
    textColor=colors.HexColor('#34495e'))

body = ParagraphStyle('BodyCN', parent=styles['Normal'],
    fontName=FONT_NAME, fontSize=9, leading=13,
    textColor=colors.HexColor('#444444'))

small = ParagraphStyle('SmallCN', parent=styles['Normal'],
    fontName=FONT_NAME, fontSize=8, leading=10,
    textColor=colors.HexColor('#888888'))

# ========== 颜色主题 ==========
GREEN  = colors.HexColor('#27ae60')
BLUE   = colors.HexColor('#2980b9')
ORANGE = colors.HexColor('#e67e22')
RED    = colors.HexColor('#c0392b')
GRAY   = colors.HexColor('#ecf0f1')
DARK   = colors.HexColor('#2c3e50')
WHITE  = colors.white


def build_pdf(json_data, date_str):
    candidates = json_data.get('top15', [])
    all_candidates = json_data.get('all_candidates', [])
    breakout_total = json_data.get('breakout_total', 0)
    filtered = json_data.get('filtered', 0)

    out_filename = f'v47_best_picks_{date_str}.pdf'
    out_path = os.path.join(OUTPUT_DIR, out_filename)

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            rightMargin=1.5*cm, leftMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    elements = []

    # ========== 标题 ==========
    elements.append(Paragraph('V4.7 高胜率低回撤精选', title_style))
    elements.append(Paragraph(
        f'报告日期：{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}  |  '
        f'突破股 {breakout_total}只 → 精选 {filtered}只', subtitle_style))
    elements.append(Spacer(1, 0.3*cm))

    # ========== 策略说明 ==========
    elements.append(Paragraph('📋 筛选策略', h1))
    strategy_data = [
        ['条件', '说明'],
        ['RSI14<75', '未极端超买'],
        ['量比<3.0', '未放巨量出货'],
        ['5日涨幅<20%', '未短期暴涨'],
        ['近20日最大回撤<-35%', '有足够安全边际'],
        ['仅BREAKOUT模式', '放量突破形态'],
    ]
    tbl = Table(strategy_data, colWidths=[3.5*cm, 10*cm])
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), FONT_NAME),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('BACKGROUND', (0,0), (-1,0), DARK),
        ('TEXTCOLOR', (0,0), (-1,0), WHITE),
        ('BACKGROUND', (0,1), (-1,-1), WHITE),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GRAY]),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 0.5*cm))

    # ========== TOP15 评分表 ==========
    elements.append(Paragraph(f'🏆 TOP{len(candidates)} 精选评分排名', h1))
    elements.append(Spacer(1, 0.2*cm))

    header = ['排名', '代码', '名称', '收盘', 'RSI14', '量比', '5日涨幅', '最大回撤', '基础分', '质量分', '信号']
    rows = [header]

    for i, c in enumerate(candidates, 1):
        quality = c.get('quality_score', 0)
        if quality > 70:
            sig = '✅ 强烈'
        elif quality > 60:
            sig = '🔍 关注'
        else:
            sig = '⚠️ 观察'

        rows.append([
            str(i),
            c.get('code', ''),
            c.get('name', '')[:8],
            f"{c.get('close', 0):.2f}",
            f"{c.get('rsi14', 0):.1f}",
            f"{c.get('vol_ratio', 0):.2f}",
            f"{c.get('pct_5d', 0):+.1f}%",
            f"{c.get('max_dd', 0):+.1f}%",
            f"{c.get('base_score', 0):.0f}",
            f"{quality:.0f}",
            sig,
        ])

    col_w = [0.8*cm, 2.2*cm, 1.8*cm, 1.2*cm, 1.2*cm, 1.0*cm, 1.5*cm, 1.5*cm, 1.2*cm, 1.2*cm, 2.0*cm]
    tbl = Table(rows, colWidths=col_w)
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), FONT_NAME),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('FONTSIZE', (0,1), (-1,-1), 7.5),
        ('BACKGROUND', (0,0), (-1,0), GREEN),
        ('TEXTCOLOR', (0,0), (-1,0), WHITE),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GRAY]),
        # 质量分高亮
        *([('TEXTCOLOR', (9,i), (9,i), GREEN) for i in range(1, len(rows)) if rows[i][9] and float(rows[i][9]) > 70]),
        *([('TEXTCOLOR', (5,i), (5,i), RED) for i in range(1, len(rows)) if rows[i][5] and float(rows[i][5]) > 2.5]),
    ]))
    elements.append(tbl)

    # ========== TOP5 交易计划 ==========
    elements.append(Spacer(1, 0.5*cm))
    elements.append(Paragraph('💰 TOP5 交易计划', h1))

    for i, c in enumerate(candidates[:5], 1):
        close = c.get('close', 0)
        entry  = close * 0.98
        stop   = close * 0.93
        target = close * 1.15
        ratio  = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0
        pos    = min(10, max(3, c.get('quality_score', 60) - 55))

        elements.append(Paragraph(
            f'{i}. <b>{c.get("name", "")}</b> ({c.get("code", "")})  '
            f'质量分 {c.get("quality_score", 0):.0f}  信号', h2))

        detail_data = [
            ['技术指标', 'RSI14', '量比', '5日涨幅', '最大回撤'],
            ['数值',
             f"{c.get('rsi14', 0):.1f}",
             f"{c.get('vol_ratio', 0):.2f}",
             f"{c.get('pct_5d', 0):+.1f}%",
             f"{c.get('max_dd', 0):+.1f}%"],
        ]
        dt = Table(detail_data, colWidths=[2.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3*cm])
        dt.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), FONT_NAME),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), WHITE),
            ('BACKGROUND', (0,1), (-1,1), WHITE),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ]))
        elements.append(dt)

        trade_data = [
            ['操作', '入场价', '止损价', '止盈价', '盈亏比', '建议仓位'],
            ['价格',
             f'{entry:.2f} (-2%)',
             f'{stop:.2f} (-7%)',
             f'{target:.2f} (+15%)',
             f'{ratio:.1f}:1',
             f'{pos:.0f}%'],
        ]
        tt = Table(trade_data, colWidths=[1.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.8*cm, 1.8*cm])
        tt.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), FONT_NAME),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BACKGROUND', (0,0), (-1,0), ORANGE),
            ('TEXTCOLOR', (0,0), (-1,0), WHITE),
            ('BACKGROUND', (0,1), (-1,1), WHITE),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ]))
        elements.append(Spacer(1, 0.2*cm))
        elements.append(tt)
        elements.append(Spacer(1, 0.3*cm))

    # ========== 页脚 ==========
    elements.append(Spacer(1, 0.5*cm))
    elements.append(Paragraph(
        f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}  |  '
        f'数据来源：D:\\mystock\\cache_daily  |  仅供策略参考，不构成投资建议',
        small))

    # 生成
    doc.build(elements)
    return out_path


def main():
    # 确定输入文件
    if len(sys.argv) >= 2:
        arg = sys.argv[1]
        # 如果参数是日期字符串（8位数字）
        if arg.isdigit() and len(arg) == 8:
            date_str = arg
            json_path = os.path.join(OUTPUT_DIR, f'v47_best_picks_{date_str}.json')
        # 如果参数是文件名或路径
        elif arg.endswith('.json'):
            if os.path.isabs(arg):
                json_path = arg
            else:
                json_path = os.path.join(OUTPUT_DIR, arg)
            # 从文件名提取日期
            import re
            m = re.search(r'(\d{8})', os.path.basename(arg))
            date_str = m.group(1) if m else datetime.now().strftime('%Y%m%d')
        else:
            print(f'用法：python generate_v47_pdf.py [日期|文件名]')
            sys.exit(1)
    else:
        date_str = datetime.now().strftime('%Y%m%d')
        json_path = os.path.join(OUTPUT_DIR, f'v47_best_picks_{date_str}.json')

    if not os.path.exists(json_path):
        print(f'❌ 找不到文件：{json_path}')
        print(f'   请先运行：python v47_best_picks.py {date_str}')
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f'📄 输入：{json_path}')
    out = build_pdf(data, date_str)
    print(f'✅ PDF 已生成：{out}')


if __name__ == '__main__':
    main()
