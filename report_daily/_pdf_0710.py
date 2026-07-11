# -*- coding: utf-8 -*-
"""每日复盘PDF - 20260710版"""
import os, re, datetime
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

FONT = 'Chinese'
for fp in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont(FONT, fp))
            print(f"字体: {fp}")
            break
        except:
            continue

S = {}
S['title'] = ParagraphStyle('T', fontName=FONT, fontSize=22, spaceAfter=6,
    alignment=TA_CENTER, textColor=colors.HexColor('#1a3a8a'))
S['sub']   = ParagraphStyle('Su', fontName=FONT, fontSize=11, spaceAfter=4,
    alignment=TA_CENTER, textColor=colors.HexColor('#555555'))
S['h1']    = ParagraphStyle('H1', fontName=FONT, fontSize=13, spaceAfter=8, spaceBefore=14,
    textColor=colors.HexColor('#1a3a8a'))
S['h2']    = ParagraphStyle('H2', fontName=FONT, fontSize=11, spaceAfter=5, spaceBefore=8,
    textColor=colors.HexColor('#2c5f9e'))
S['body']  = ParagraphStyle('B', fontName=FONT, fontSize=10, spaceAfter=4, leading=14,
    textColor=colors.HexColor('#222222'))
S['bullet']= ParagraphStyle('Bu', fontName=FONT, fontSize=10, spaceAfter=3,
    leftIndent=16, leading=14, textColor=colors.HexColor('#333333'))
S['hl_red']= ParagraphStyle('HLR', fontName=FONT, fontSize=10, spaceAfter=4, leading=14,
    textColor=colors.HexColor('#c0392b'), backColor=colors.HexColor('#fff5f5'))
S['warn']  = ParagraphStyle('W', fontName=FONT, fontSize=9.5, spaceAfter=3,
    leftIndent=16, leading=13, textColor=colors.HexColor('#c0392b'))
S['footer']= ParagraphStyle('F', fontName=FONT, fontSize=8, spaceAfter=2,
    textColor=colors.HexColor('#aaaaaa'))
S['tag']   = ParagraphStyle('Tg', fontName=FONT, fontSize=9, spaceAfter=2,
    textColor=colors.HexColor('#555555'))

def cl(t):
    t = re.sub(r'<[^>]+>', '', t)
    return t.strip()

def sp(h=0.3):
    return Spacer(1, h*cm)

def h1(t): return Paragraph(cl(t), S['h1'])
def h2(t): return Paragraph(cl(t), S['h2'])
def body(t): return Paragraph(cl(t), S['body'])
def bullet(t, style='bullet'): return Paragraph('• ' + cl(t), S.get(style, S['bullet']))

def T(data, widths, hdr_color, fs=8.5):
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), hdr_color),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME',   (0,0), (-1,-1), FONT),
        ('FONTSIZE',   (0,0), (-1,-1), fs),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ('RIGHTPADDING',  (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.HexColor('#f8f9fa'), colors.HexColor('#eef1f5')]),
    ]))
    return t

# ── 故事内容 ──
story = []
story.append(sp(0.5))
story.append(Paragraph('每日复盘报告', S['title']))
story.append(Paragraph('2026-07-10', S['sub']))
story.append(sp(0.3))

# ═══ 一、大盘情绪 ═══ #
story.append(h1('一、大盘情绪'))
story.append(Paragraph('市场整体处于弱势震荡，大盘经历中期调整，整体趋势偏弱，情绪退潮。成交量萎缩，炸板率高企，市场缺乏主线共识和追高意愿。', S['body']))
story.append(sp(0.15))

pos_data = [
    ['指标', '数值', '指标', '数值'],
    ['仓位建议', '25%（防守）', '市场状态', '弱势'],
    ['指数趋势', '偏弱（30-40分）', '情绪状态', '退潮'],
    ['操作策略', '防守为主、快进快出', '聚焦方向', '商业航天 + 医药'],
]
story.append(T(pos_data, [3*cm]*4, colors.HexColor('#1a3a8a'), 9))
story.append(sp(0.2))

# ═══ 二、今日主题分析 ═══ #
story.append(h1('二、今日主题分析'))
story.append(h2('核心主线：商业航天持续强势，医药产业链逆势防御'))
story.append(body('市场弱势震荡下主题快速轮动，商业航天表现出极强持续性，医药产业链作为防御性板块逆势走强。'))
story.append(sp(0.1))

theme_data = [
    ['主题', '核心标的', '状态'],
    ['商业航天', '航天电器、上海瀚讯、北斗星通', '连续2天启动确认'],
    ['医药产业链', '恒瑞医药、药明康德、信立泰', '防御+趋势双重属性'],
    ['军工', '关联商业航天，有望联动', '分歧转一致信号'],
]
story.append(T(theme_data, [3*cm, 6.5*cm, 3*cm], colors.HexColor('#2c5f9e')))
story.append(sp(0.2))

story.append(h2('明日主题预测：'))
story.append(bullet('🥇 明日最看好：<b>商业航天</b>（趋势分63.7，情绪分93.2，全市场最高，连续2天确认'))
story.append(bullet('🥈 明日次看好：<b>医药产业链</b>（趋势分63.9，防守+独立行情能力，避险资金流入'))
story.append(bullet('🥉 重点观察：<b>军工</b>（分歧转一致，与商业航天联动效应'))

# ═══ 三、今日强势股票池 ═══ #
story.append(h1('三、今日强势股票池'))
story.append(Paragraph('<b>【重要提醒】</b>大盘经历中期调整，关注中线股池B浪机会', S['hl_red']))
story.append(sp(0.15))

strong_data = [
    ['排名', '名称', '代码', '整合评分', '失败率', '主题', '信号'],
    ['1', '上海瀚讯', '300762.SZ', '34.1', '52.7%', '商业航天', '量能爆发+6.34%'],
]
story.append(T(strong_data, [1*cm, 2*cm, 2.2*cm, 1.8*cm, 1.5*cm, 2.5*cm, 3*cm],
    colors.HexColor('#e74c3c')))
story.append(sp(0.1))
story.append(body('上海瀚讯今日上涨<6.34%>，量能爆发<2.05倍>，成交额43亿。失败概率>50%，属高风险高弹性短线博弈，非中线稳健选择。'))

# ═══ 四、中军企稳股池 ═══ #
story.append(h1('四、中军企稳股池'))
story.append(body('低空经济方向，回调到位+均线支撑的稳健布局机会：'))
zhongjun_data = [
    ['名称', '代码', '主题', '买评分', '回调', '企稳依据'],
    ['祥鑫科技', '002965.SZ', '低空经济', '80分', '-5.3%', 'MA10精准支撑+MA60向上'],
]
story.append(T(zhongjun_data, [2.2*cm, 2.2*cm, 2.5*cm, 1.5*cm, 1.5*cm, 3*cm],
    colors.HexColor('#27ae60')))
story.append(sp(0.1))
story.append(bullet('强买信号：买分80，回调5.3%处于黄金区间，2个技术指标共振'))
story.append(bullet('回测T+3胜率80%，以MA60作为中期趋势生命线持股'))
story.append(bullet('止损：MA60下方', 'warn'))

# ═══ 五、今日低吸股票池 ═══ #
story.append(h1('五、今日低吸股票池'))
story.append(body('B浪底部共振信号，左侧布局机会：'))
lowbuy_data = [
    ['名称', '代码', '板块', '评分', '形态', '入场参考', '止损位'],
    ['铜冠铜箔', '301217.SZ', '主板', '32分', '深度回调', '现价131.09', '116.24(-11%)'],
]
story.append(T(lowbuy_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 2*cm, 2.5*cm, 2*cm],
    colors.HexColor('#8e44ad')))
story.append(sp(0.1))
story.append(body('无明确热门主题归属，左侧潜伏机会。注：低吸为左侧博弈，需严格止损，建议仓位不超过总仓位20%。'))

# ═══ 六、中线股池 ═══ #
story.append(h1('六、今日中线股池（B浪策略）'))
story.append(body('B浪末端见底信号，左侧轻仓潜伏机会：'))
mid_data = [
    ['名称', '代码', '板块', '评分', '信号', 'A浪涨幅', 'B浪回调', '距A高'],
    ['联德股份', '605060.SH', '主板', '76分', '底背离', '90.0%', '31.7%', '48.4%'],
    ['开山股份', '300257.SZ', '双创', '66分', '底背离', '132.8%', '22.3%', '26.2%'],
]
story.append(T(mid_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm, 1.8*cm, 1.5*cm],
    colors.HexColor('#8e44ad')))

# ═══ 七、ETF操作建议 ═══ #
story.append(h1('七、ETF操作建议'))
story.append(body('当前持仓及成份股关注：'))
etf_data = [
    ['ETF名称', '代码', '操作', '成份股关注'],
    ['半导体设备', '159516.SZ', '继续持有', '上海合晶、艾森股份（弱转强）；有研硅、华海清科（低吸）'],
    ['人工智能ETF', '159819.SZ', '关注启动', '三六零(补涨分76)、金山办公(75)'],
    ['创新药ETF', '159992.SZ', '观察反弹', '信立泰(补涨分82)'],
]
story.append(T(etf_data, [3*cm, 2.2*cm, 2.2*cm, 5*cm], colors.HexColor('#27ae60')))

# ═══ 八、量能爆发强势股 ═══ #
story.append(h1('八、量能爆发强势股'))
story.append(body('MACD刚红柱 + 浅回调 + 高评分 = 强买信号（回测74%胜率）：'))
burst_data = [
    ['名称', '代码', '评分', '形态', '量比', '区间涨幅', '区间振幅', 'MACD'],
    ['盈新发展', '000620.SZ', '79分', '浅回调', '1.46', '16.7%', '47.8%', '刚红柱'],
]
story.append(T(burst_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm, 1.8*cm, 1.8*cm],
    colors.HexColor('#e67e22')))
story.append(sp(0.1))
story.append(body('距MA20较远（+12%），追高有风险，等待回踩短期均线机会更佳。'))

# ═══ 九、波浪理论W3选股 ═══ #
story.append(h1('九、波浪理论第3浪选股（主升浪）'))
w3_data = [
    ['名称', '代码', '评分', 'W1涨幅', 'W2回调', 'W3目标', '空间', '操作'],
    ['创世纪', '300083.SZ', '86分', '73.7%', '60.9%', '17.81', '+33.8%', '回踩MA20@12.30'],
    ['江丰电子', '300666.SZ', '82分', '86.5%', '34.3%', '539.37', '+58.4%', '寻找买点'],
    ['上海新阳', '300236.SZ', '81分', '65.9%', '61.2%', '160.66', '+34.2%', '持有'],
    ['微导纳米', '688147.SH', '76分', '63.5%', '67.5%', '139.93', '+0.7%', '不建议'],
]
story.append(T(w3_data, [2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm, 1.5*cm, 2.8*cm],
    colors.HexColor('#8e44ad')))
story.append(sp(0.1))
story.append(body('<b>创世纪</b>介入：MA20@12.30 / 黄金回撤38.2%@11.24，止损8.79(-34%)，止盈17.81(+33.8%)。'))

# ═══ 十、回升买点策略 ═══ #
story.append(h1('十、回升买点策略（混合策略）'))
story.append(body('W2深度回调后回升企稳，符合左侧低吸条件：'))
recover_data = [
    ['代码', '信号', '评分', '现价', 'W1涨幅', 'W2回调', '距H1', '回升', '操作'],
    ['600150.SH', '低吸', '90分', '37.04', '44.7%', '70.7%', '+17.2%', '9.2%', '现价介入'],
    ['688099.SH', '低吸', '90分', '101.35', '62.7%', '77.1%', '+21.6%', '17.0%', '轻仓低吸'],
    ['300687.SZ', '低吸', '90分', '30.47', '104.3%', '76.7%', '+20.8%', '36.1%', '分时回调介入'],
    ['688200.SH', '突破', '100分', '505.00', '98.1%', '52.8%', '+12.9%', '19.9%', '追入止损H1-3%'],
    ['688107.SH', '突破', '75分', '43.18', '90.4%', '52.6%', '+15.5%', '15.5%', '现价介入'],
]
story.append(T(recover_data,
    [2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2.5*cm],
    colors.HexColor('#e67e22'), 8))
story.append(sp(0.1))
story.append(body('<b>688200.SH（信号分100）</b>：W2浅回调后强力突破H1，标准突破买入信号，止损设H1下方3%。'))

# ── 页脚 ──
story.append(sp(0.5))
story.append(Paragraph(
    f'报告生成：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} '
    '| QClaw 量化系统 | 仅供参考，不构成投资建议',
    S['footer']))
story.append(Paragraph(
    '数据来源：通达信+Tushare | 版本：Final_Self_20260710',
    S['footer']))

# ── 生成 ──
out_path = r'D:\mystock\report_daily\Final_Self_20260710.pdf'
doc = SimpleDocTemplate(out_path, pagesize=A4,
    rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
doc.build(story)
print(f'PDF完成: {out_path}')
