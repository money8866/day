# -*- coding: utf-8 -*-
"""生成估值分析PDF：空间>100%的41只个股AI分析"""
import pandas as pd
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

FONT = 'Chinese'
pdfmetrics.registerFont(TTFont(FONT, r'C:\Windows\Fonts\msyh.ttc'))

# ── 颜色 ──
C_BLUE  = colors.HexColor('#1a3a8a')
C_RED   = colors.HexColor('#c0392b')
C_GREEN = colors.HexColor('#27ae60')
C_ORANGE= colors.HexColor('#e67e22')
C_PURPLE= colors.HexColor('#8e44ad')
C_GREY  = colors.HexColor('#6c757d')
C_DARK  = colors.HexColor('#1a1a2e')
C_WHITE = colors.whitesmoke

def ps(name, **kw):
    base = dict(fontName=FONT, fontSize=9, leading=14, textColor=C_DARK)
    base.update(kw)
    return ParagraphStyle(name, **base)

# ── AI分析函数（基于数据的智能分析） ──

def analyze_stock(r):
    """对单只个股进行定性分析"""
    name = r['name']
    theme = r['theme'] if pd.notna(r['theme']) else '无主题'
    pe = r['pe_ttm']
    peg = r['peg']
    profit_yoy = r['net_profit_yoy']
    upside = r['realistic_upside_%']
    score = r['composite_score']

    lines = []

    # 成长性评价
    if profit_yoy > 100:
        growth_tag = f"爆发式增长（+{profit_yoy:.0f}%）"
        growth_note = "利润翻倍以上，高成长驱动估值修复预期强烈"
    elif profit_yoy > 50:
        growth_tag = f"高速增长（+{profit_yoy:.0f}%）"
        growth_note = "利润增速亮眼，基本面强劲支撑估值空间"
    elif profit_yoy > 20:
        growth_tag = f"稳健增长（+{profit_yoy:.0f}%）"
        growth_note = "利润稳定增长，估值空间主要来自低估修复"
    else:
        growth_tag = f"低速增长（+{profit_yoy:.0f}%）"
        growth_note = "增速偏低，需关注后续业绩改善动力"

    # PEG评价
    if peg < 0.3:
        peg_tag = f"极低PEG={peg:.2f}，性价比极高"
    elif peg < 0.5:
        peg_tag = f"低PEG={peg:.2f}，具备安全边际"
    elif peg < 1.0:
        peg_tag = f"合理PEG={peg:.2f}，估值合理偏低"
    else:
        peg_tag = f"PEG={peg:.2f}，中等偏高"

    # PE评价
    pe_note = f"PE={pe:.1f}x"

    # 主题热度
    hot_themes = ['AI芯片', 'AI应用', 'AI文娱内容', '固态电池', '创新药', '机器人', '氢能源']
    if theme in hot_themes:
        theme_tag = f"✅ 当前热点主题：{theme}，景气度向上"
    elif theme in ['医药产业链', '工业金属', '券商']:
        theme_tag = f"📊 顺周期/防御性主题：{theme}，板块轮动受益"
    else:
        theme_tag = f"📌 主题：{theme}"

    lines.append(f"• {name}（{theme}）")
    lines.append(f"  核心指标：PE={pe:.1f}x | PEG={peg:.2f} | 净利润增速={profit_yoy:.1f}% | 估值空间={upside:.1f}%")
    lines.append(f"  【成长性】{growth_tag}——{growth_note}")
    lines.append(f"  【估值评价】{pe_note}，{peg_tag}")
    lines.append(f"  【主题定位】{theme_tag}，综合评分{score:.0f}分")

    # 细化分析
    details = []
    if profit_yoy > 150 and peg < 0.2:
        details.append("高增长+低PEG双击，如果持续性验证，估值修复空间极大")
    elif profit_yoy > 80 and pe < 12:
        details.append("利润高增+传统估值区间，属于典型的低估成长股，市场尚未充分定价")
    elif profit_yoy > 20 and pe < 12:
        details.append("稳健增长+低估值的组合，防御性强，下行风险有限")
    elif profit_yoy < 15:
        details.append("增速缓慢，估值空间主要来自行业均值回归，需关注催化因素")
    if upside > 150:
        details.append("估值空间极大（>150%），但通常意味着当前处于极度低估状态或有重大预期差")
    if pe > 15 and profit_yoy < 20:
        details.append("PE偏高但增速有限，需警惕估值陷阱")

    if details:
        lines.append(f"  💡 详细解读：{'；'.join(details)}")
    lines.append("")

    return '\n'.join(lines)

# ── 主题分组分析函数 ──
def analyze_theme_group(theme_name, stocks):
    """对一组同主题个股进行综合分析"""
    total = len(stocks)
    avg_upside = stocks['realistic_upside_%'].mean()
    avg_pe = stocks['pe_ttm'].mean()
    avg_peg = stocks['peg'].mean()
    avg_yoy = stocks['net_profit_yoy'].mean()
    codes = ', '.join([str(c).zfill(6) if str(c).isdigit() and len(str(c))<6 else str(c) for c in stocks['code']])

    lines = []
    lines.append(f"【{theme_name}】{total}只 | 均值：PE={avg_pe:.1f}x | PEG={avg_peg:.2f} | 净利增速={avg_yoy:.1f}% | 空间={avg_upside:.1f}%")

    # 主题分析
    if total >= 5:
        lines.append(f"  该主题集中度高（{total}只），板块性低估明显，可能形成板块轮动机会")
    if avg_peg < 0.5:
        lines.append(f"  主题内个股PEG普遍偏低（均值{avg_peg:.2f}），整体性价比突出")
    if avg_yoy > 50:
        lines.append(f"  主题内个股平均增速>50%，高成长驱动估值修复")
    lines.append(f"  成分股：{codes}")
    lines.append("")

    return '\n'.join(lines)

# ── 加载数据 ──
df = pd.read_csv('D:/mystock/solo/report_daily/valuation_ge100_v2.csv', encoding='utf-8-sig')
high = df[df['realistic_upside_%'] >= 100].copy()
# 补齐代码
def fix_code(c):
    s = str(c).strip()
    if s.isdigit() and len(s) < 6:
        return s.zfill(6)
    return s
high['code_fixed'] = high['code'].apply(fix_code)
high = high.sort_values('realistic_upside_%', ascending=False)

today = '20260720'
out = f'D:/mystock/report_daily/valuation_analysis_detail_{today}.pdf'
doc = SimpleDocTemplate(
    out, pagesize=A4,
    leftMargin=18*mm, rightMargin=18*mm,
    topMargin=15*mm, bottomMargin=15*mm
)

story = []

# ═══════════ 标题页 ═══════════
story.append(Paragraph('BullScore 估值空间深度分析报告', ps('tit', fontSize=18, textColor=C_BLUE, alignment=TA_CENTER, spaceAfter=6)))
story.append(Paragraph('筛选条件：估值空间 ≥ 100%  |  共41只个股  |  2026-07-20', ps('sub', fontSize=9, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=4)))
story.append(Paragraph('数据来源：Tushare  |  BullScore v3.1 估值模型  |  AI 智能分析', ps('sub2', fontSize=8, textColor=C_GREY, alignment=TA_CENTER, spaceAfter=8)))
story.append(HRFlowable(width='100%', thickness=2, color=C_BLUE, spaceAfter=10))

# ═══════════ 第一页：概览 ═══════════
story.append(Paragraph('一、综合概览', ps('h1', fontSize=14, textColor=C_BLUE, spaceBefore=4, spaceAfter=6)))

# 汇总统计
total = len(high)
avg_up = high['realistic_upside_%'].mean()
avg_pe = high['pe_ttm'].mean()
avg_peg = high['peg'].mean()
avg_yoy = high['net_profit_yoy'].mean()
avg_score = high['composite_score'].mean()

stats_data = [
    ['股票数量', '平均估值空间', '平均PE', '平均PEG', '平均利润增速', '平均评分'],
    [f'{total}只', f'{avg_up:.1f}%', f'{avg_pe:.1f}x', f'{avg_peg:.2f}', f'{avg_yoy:.1f}%', f'{avg_score:.1f}'],
]
t = Table(stats_data, colWidths=[52, 58, 52, 52, 58, 52])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 9),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
]))
story.append(t)
story.append(Spacer(1, 5*mm))

# 主题分布
theme_dist = high['theme'].value_counts().head(10)
story.append(Paragraph('▶ 主题分布 TOP 10', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=6, spaceAfter=4)))
thm_data = [['排名', '主题', '数量', '平均空间%', '平均PE', '平均PEG', '平均利润增速%']]
for i, (thm, cnt) in enumerate(theme_dist.items(), 1):
    sub = high[high['theme'] == thm]
    thm_data.append([str(i), thm, str(cnt),
        f"{sub['realistic_upside_%'].mean():.1f}",
        f"{sub['pe_ttm'].mean():.1f}",
        f"{sub['peg'].mean():.2f}",
        f"{sub['net_profit_yoy'].mean():.1f}"])

cw = [22, 58, 28, 48, 40, 40, 52]
t = Table(thm_data, colWidths=cw)
ts = TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ALIGN', (2,0), (-1,-1), 'CENTER'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
])
# 对空间列着色
for i in range(1, len(thm_data)):
    v = float(thm_data[i][3])
    if v > 180: ts.add('TEXTCOLOR', (3,i), (3,i), C_RED)
    elif v > 150: ts.add('TEXTCOLOR', (3,i), (3,i), C_ORANGE)
    else: ts.add('TEXTCOLOR', (3,i), (3,i), C_GREEN)
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 4*mm))

# PEG/P/E分布
story.append(Paragraph('▶ 核心指标分布', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=6, spaceAfter=4)))
peg_bins = [0, 0.2, 0.5, 1.0, 5]
peg_l = [f'PEG<0.2:{len(high[high["peg"]<0.2])}只', f'0.2-0.5:{len(high[(high["peg"]>=0.2)&(high["peg"]<0.5)])}只', f'0.5-1.0:{len(high[(high["peg"]>=0.5)&(high["peg"]<1.0)])}只', f'PEG>1.0:{len(high[high["peg"]>=1.0])}只']
pe_bins = [f'PE<10:{len(high[high["pe_ttm"]<10])}只', f'10-15:{len(high[(high["pe_ttm"]>=10)&(high["pe_ttm"]<15)])}只', f'15-20:{len(high[(high["pe_ttm"]>=15)&(high["pe_ttm"]<20)])}只', f'PE>20:{len(high[high["pe_ttm"]>=20])}只']
yoy_bins = [f'增速>150%:{len(high[high["net_profit_yoy"]>150])}只', f'50-150%:{len(high[(high["net_profit_yoy"]>=50)&(high["net_profit_yoy"]<150)])}只', f'20-50%:{len(high[(high["net_profit_yoy"]>=20)&(high["net_profit_yoy"]<50)])}只', f'<20%:{len(high[high["net_profit_yoy"]<20])}只']

dist_data = [
    ['PEG分布', ' | '.join(peg_l)],
    ['PE(TTM)分布', ' | '.join(pe_bins)],
    ['净利润增速分布', ' | '.join(yoy_bins)],
]
t = Table(dist_data, colWidths=[58, 360])
ts = TableStyle([
    ('FONTNAME', (0,0), (-1,-1), FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (0,-1), C_BLUE),
    ('TEXTCOLOR', (0,0), (0,-1), C_WHITE),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 4),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
])
t.setStyle(ts)
story.append(t)
story.append(Spacer(1, 5*mm))

# 市场环境说明
story.append(Paragraph('▶ 市场环境说明', ps('h2', fontSize=11, textColor=C_BLUE, spaceBefore=6, spaceAfter=4)))
story.append(Paragraph(
    '本报告筛选标准为估值空间≥100%（即当前股价低于合理估值一倍以上）。当前市场处于7月17日大跌后的修复期，'
    '创业板指RSI14=32偏弱，中证1000超卖。在此背景下，大空间标的通常有两种特征：(1)周期底部——利润低基数但展望乐观；'
    '(2)业绩爆发——当期利润高速增长但市场尚未充分重估。投资者需区分"价值陷阱"与"业绩低估"两类情况，详见下方个股分析。',
    ps('body', fontSize=9, leading=15, textColor=C_DARK)))
story.append(Spacer(1, 3*mm))
story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#e0e0e0'), spaceAfter=8))

# ═══════════ 第二页起：个股深度分析（按空间从高到低） ═══════════
story.append(PageBreak())
story.append(Paragraph('二、个股深度分析（按估值空间从高到低）', ps('h1', fontSize=14, textColor=C_RED, spaceBefore=2, spaceAfter=8)))

for _, r in high.iterrows():
    analysis = analyze_stock(r)
    for line in analysis.split('\n'):
        if line.strip():
            text_color = C_RED if line.startswith('•') else C_DARK
            story.append(Paragraph(line, ps('ai', fontSize=8.5, leading=13, textColor=text_color, spaceBefore=1, spaceAfter=1)))

    story.append(Spacer(1, 2*mm))

# ═══════════ 尾部 ═══════════
story.append(Spacer(1, 6*mm))
story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#cccccc'), spaceBefore=4))
story.append(Paragraph(
    f'免责声明：本报告基于BullScore估值模型自动生成，模型假设基于公开财务数据和行业均值参考，不构成投资建议。'
    f'估值空间反映的是相对于合理估值的潜在修复幅度，不保证实际涨跌幅。市场有风险，投资需谨慎。'
    f'生成时间：{today}  |  数据来源：Tushare  |  QClaw量化系统',
    ps('foot', fontSize=7, textColor=C_GREY, alignment=TA_CENTER, spaceBefore=4)
))

doc.build(story)
print(f'PDF: {out}  size={os.path.getsize(out):,} bytes')
