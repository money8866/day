# -*- coding: utf-8 -*-
"""每日复盘PDF报告 - 参考0626版Platypus结构"""
import os, re, datetime
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# 注册字体
for font_path in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('Chinese', font_path))
            break
        except:
            continue

FONT = 'Chinese'

# 读取md内容
with open(r'D:\mystock\report_daily\Final_Self_20260709.md', 'r', encoding='utf-8') as f:
    raw = f.read()

def clean(t):
    t = re.sub(r'<[^>]+>', '', t)
    return t.strip()

# ── 样式定义 ──
styles = getSampleStyleSheet()
S = {}

S['title'] = ParagraphStyle('Title2', fontName=FONT, fontSize=22, spaceAfter=6,
    alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a'))
S['subtitle'] = ParagraphStyle('Sub', fontName=FONT, fontSize=11, spaceAfter=4,
    alignment=TA_CENTER, textColor=colors.HexColor('#555555'))
S['h1'] = ParagraphStyle('H1', fontName=FONT, fontSize=13, spaceAfter=8, spaceBefore=14,
    textColor=colors.HexColor('#1a3a8a'),
    borderPad=(0,0,3,0))
S['h2'] = ParagraphStyle('H2', fontName=FONT, fontSize=11, spaceAfter=5, spaceBefore=8,
    textColor=colors.HexColor('#2c5f9e'))
S['body'] = ParagraphStyle('Body', fontName=FONT, fontSize=10, spaceAfter=4, leading=14,
    textColor=colors.HexColor('#222222'))
S['bullet'] = ParagraphStyle('Bullet', fontName=FONT, fontSize=10, spaceAfter=3,
    leftIndent=16, leading=14, textColor=colors.HexColor('#333333'))
S['highlight'] = ParagraphStyle('HL', fontName=FONT, fontSize=10, spaceAfter=4,
    leading=14, textColor=colors.HexColor('#c0392b'),
    backColor=colors.HexColor('#fff5f5'))
S['warn'] = ParagraphStyle('Warn', fontName=FONT, fontSize=9.5, spaceAfter=3,
    leftIndent=16, leading=13, textColor=colors.HexColor('#c0392b'))
S['green'] = ParagraphStyle('Green', fontName=FONT, fontSize=10, spaceAfter=3,
    leading=14, textColor=colors.HexColor('#27ae60'))
S['stock_bullet'] = ParagraphStyle('SB', fontName=FONT, fontSize=10, spaceAfter=2,
    leftIndent=20, leading=14, textColor=colors.HexColor('#1a3a8a'))
S['footer'] = ParagraphStyle('Footer', fontName=FONT, fontSize=8, spaceAfter=2,
    textColor=colors.HexColor('#aaaaaa'))
S['tag'] = ParagraphStyle('Tag', fontName=FONT, fontSize=9, spaceAfter=2,
    textColor=colors.HexColor('#7f8c8d'))

# ── 辅助函数 ──
def make_table(data, col_widths, header_color, font_size=8.5):
    """创建统一风格的表格"""
    n_col = len(data[0])
    if len(col_widths) != n_col:
        col_widths = [None] * n_col
    t = Table(data, colWidths=col_widths)
    style = [
        ('BACKGROUND', (0,0), (-1,0), header_color),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME', (0,0), (-1,-1), FONT),
        ('FONTSIZE', (0,0), (-1,-1), font_size),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
    ]
    t.setStyle(TableStyle(style))
    return t

def h1(text):
    return Paragraph(text, S['h1'])

def h2(text):
    return Paragraph(text, S['h2'])

def body(text):
    return Paragraph(clean(text), S['body'])

def bullet(text, style='bullet'):
    s = S.get(style, S['bullet'])
    return Paragraph('• ' + clean(text), s)

def sp(h=0.3):
    return Spacer(1, h*cm)

# ── 解析md内容 ──
content = clean(raw)

# 提取标题
title_match = re.search(r'每日复盘\((\d{8})\)', content)
report_date = title_match.group(1) if title_match else '20260709'
date_disp = f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:8]}"

story = []

# 封面区
story.append(Spacer(1, 0.5*cm))
story.append(Paragraph('每日复盘报告', S['title']))
story.append(Paragraph(date_disp, S['subtitle']))
story.append(Spacer(1, 0.3*cm))

# ═══════════════════════════════════════
# 一、大盘情绪
# ═══════════════════════════════════════
story.append(h1('一、大盘情绪'))
story.append(body('市场整体处于震荡格局，上证指数在4000-4100区间反复拉锯，创业板波动较大。'))

# 仓位摘要卡片
pos_data = [
    ['指标', '数值', '指标', '数值'],
    ['仓位建议', '40%', '市场状态', '震荡'],
    ['指数趋势', '偏弱（30-40分）', '主题趋势', '强（75分）'],
    ['操作策略', '轻指数、重个股', '聚焦方向', '半导体产业链'],
]
pos_table = make_table(pos_data, [3*cm, 3*cm, 3*cm, 3*cm],
    colors.HexColor('#1a3a8a'), 9)
story.append(pos_table)
story.append(sp(0.2))

story.append(body('<b>核心判断：</b>权重搭台、主题唱戏，结构性行情特征显著，操作上应锁定强势主线。'))

# ═══════════════════════════════════════
# 二、今日主题分析
# ═══════════════════════════════════════
story.append(h1('二、今日主题分析'))
story.append(h2('核心主线：半导体产业链全线抱团'))
story.append(body('资金深度聚焦半导体产业链，设备、制造、材料、先进封装及AI芯片形成清晰的抱团主升主线，情绪与趋势共振向上。'))

story.append(h2('主要关注主题及补涨中军：'))
theme_data = [
    ['主题', '核心标的'],
    ['半导体设备', '北方华创、拓荆科技'],
    ['先进封装', '长电科技、华天科技、芯原股份'],
    ['半导体制造', '中芯国际、晶合集成、豪威集团'],
    ['AI芯片', '海光信息、寒武纪、紫光国微'],
    ['半导体材料', '雅克科技、沪硅产业、江丰电子'],
    ['消费电子与AI终端', '欧菲光、信维通信'],
]
story.append(make_table(theme_data, [4*cm, 8*cm], colors.HexColor('#2c5f9e')))

story.append(sp(0.2))
story.append(h2('明日主题预测：'))
story.append(bullet('🥇 明日最看好：<b>先进封装</b>（情绪分94.5，涨停13家，龙头旗帜鲜明，启动确认第1天'))
story.append(bullet('🥈 明日次看好：<b>半导体设备</b>（唯一连续2天确认，稳定器，持续上攻动力强'))
story.append(bullet('🥉 第三看好：<b>AI芯片</b>（双中军逼近历史新高，辨识度高，对指数带动作用强'))

# ═══════════════════════════════════════
# 三、今日强势股票池
# ═══════════════════════════════════════
story.append(h1('三、今日强势股票池'))

# 解析强势股票池
stock_blocks = re.findall(
    r'\*{0,2}【[^】]*】[^\n]*\n([^\n]+)\n.*?(?=\*{0,2}【[^】]*】|\*{0,2}[一二三四五六七八九十]+[、]|\Z)',
    content, re.DOTALL)

strong_data = [
    ['排名', '名称', '代码', '整合评分', '失败率', '主题', '信号'],
    ['1', '欧菲光', '002456.SZ', '39.7', '46.4%', '消费电子/AI终端', '涨停/首选'],
    ['2', '雅创电子', '301099.SZ', '26.7', '61.4%', '消费电子/AI终端', '中军'],
]
story.append(make_table(strong_data, [1*cm, 2*cm, 2.2*cm, 1.8*cm, 1.5*cm, 3*cm, 2*cm],
    colors.HexColor('#e74c3c')))
story.append(sp(0.15))
story.append(body('<b>【重要提醒】</b>欧菲光今日属于强势涨停，但非标准突破形态，追高需谨慎，可关注分歧低吸机会。'))

story.append(sp(0.3))

# ═══════════════════════════════════════
# 四、中军企稳股池
# ═══════════════════════════════════════
story.append(h1('四、中军企稳股池'))
story.append(body('回调到位+均线支撑的稳健布局机会：'))

zhongjun_data = [
    ['名称', '代码', '主题', '买评分', '回调', '企稳依据'],
    ['双环传动', '002472.SZ', '人形机器人', '93分', '-9.6%', 'MA10精准支撑'],
]
story.append(make_table(zhongjun_data, [2.2*cm, 2.2*cm, 2.5*cm, 1.5*cm, 1.5*cm, 3*cm],
    colors.HexColor('#27ae60')))
story.append(sp(0.1))
story.append(bullet('强买信号：买分93分，共振4个信号，回测T+3胜率80%，平均涨幅+6.12%'))
story.append(bullet('止损建议：B浪低点下方', style='warn'))

# ═══════════════════════════════════════
# 五、低吸股票池
# ═══════════════════════════════════════
story.append(h1('五、今日低吸股票池'))
story.append(body('B浪底部共振信号，左侧布局机会：'))

lowbuy_data = [
    ['名称', '代码', '板块', '评分', '信号类型', '入场参考', '止损位'],
    ['龙蟠科技', '603906.SH', '主板', '80分', '见底+RSI金叉+MACD金叉', '回踩MA20附近', 'B浪低点-3%'],
    ['杰创智能', '301248.SZ', '双创', '74分', '见底+RSI金叉+MACD金叉', '回踩MA20附近', 'B浪低点-3%'],
    ['开山股份', '300257.SZ', '双创', '66分', '底背离', '试探性建仓', '底背离低点-3%'],
]
story.append(make_table(lowbuy_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 4*cm, 2*cm, 2*cm],
    colors.HexColor('#8e44ad')))
story.append(sp(0.2))
story.append(body('注：低吸为左侧博弈，需严格止损，建议仓位不超过总仓位20%。'))

# ═══════════════════════════════════════
# 六、ETF操作建议
# ═══════════════════════════════════════
story.append(h1('六、ETF操作建议'))

etf_data = [
    ['ETF名称', '代码', '操作', '理由'],
    ['半导体设备', '159516.SZ', '继续持有', '成份股有弱转强和启动标的，逢低加仓'],
    ['人工智能ETF', '159819.SZ', '关注启动', '景嘉微/昆仑万维/宝信软件补涨评分79/78/78'],
    ['创新药ETF', '159992.SZ', '观察反弹', '信立泰补涨评分74，板块或迎反弹'],
]
story.append(make_table(etf_data, [3*cm, 2.2*cm, 2.2*cm, 5*cm], colors.HexColor('#27ae60')))
story.append(sp(0.1))
story.append(bullet('成份股关注：有研硅(688432)、华海清科(688120)、有研新材(600206)'))

# ═══════════════════════════════════════
# 七、量能爆发强势股
# ═══════════════════════════════════════
story.append(h1('七、量能爆发+宽幅震荡强势股'))
story.append(body('MACD刚红柱 + 浅回调 + 高评分 = 强买信号（回测74%胜率）：'))

burst_data = [
    ['名称', '代码', '评分', '形态', '量比', '区间振幅', 'MACD', '信号'],
    ['华天科技', '002185.SZ', '90分', '浅回调', '0.99', '97.4%', '刚红柱', '强买'],
    ['清溢光电', '688138.SH', '79分', '浅回调', '1.03', '96.5%', '刚红柱', '强买'],
]
story.append(make_table(burst_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2*cm, 1.5*cm, 1.5*cm],
    colors.HexColor('#e67e22')))

# ═══════════════════════════════════════
# 八、波浪理论W3选股
# ═══════════════════════════════════════
story.append(h1('八、波浪理论第3浪选股（主升浪）'))

w3_data = [
    ['名称', '代码', '评分', 'W1涨幅', 'W2回调', 'W3目标', '空间', '操作'],
    ['创世纪', '300083.SZ', '91分', '73.7%', '60.9%', '17.81', '+31.8%', '回踩MA20'],
    ['长电科技', '600584.SH', '86分', '120.2%', '52.0%', '151.78', '+46.6%', '持有'],
    ['华天科技', '002185.SZ', '85分', '94.3%', '55.8%', '33.26', '+40.2%', '持有'],
    ['江丰电子', '300666.SZ', '82分', '86.5%', '34.3%', '539.37', '+48.0%', '持有'],
    ['埃斯顿', '002747.SZ', '81分', '50.6%', '38.6%', '43.12', '+3.8%', '持有'],
]
story.append(make_table(w3_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm, 1.5*cm, 2*cm],
    colors.HexColor('#8e44ad')))
story.append(sp(0.1))
story.append(bullet('操作原则：W3主升浪以持有为主，止损设前低下方，目标按波浪比例测算'))
story.append(bullet('创世纪介入位：MA20@12.30 / 黄金回撤38.2%@11.24，止损8.79(-35%)'))

# ── 页脚 ──
story.append(sp(0.5))
story.append(Paragraph(f'报告生成：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} | QClaw 量化系统 | 仅供参考，不构成投资建议', S['footer']))
story.append(Paragraph(f'数据来源：通达信+Tushare | 版本：Final_Self_{report_date}', S['footer']))

# ── 生成PDF ──
out_path = rf'D:\mystock\report_daily\Final_Self_{report_date}.pdf'
doc = SimpleDocTemplate(out_path, pagesize=A4,
    rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

doc.build(story)
print(f'PDF生成完成: {out_path}')
