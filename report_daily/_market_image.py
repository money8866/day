# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw, ImageFont
import os

W = 600
PAD = 20

def lf(fp, size):
    if os.path.exists(fp):
        try: return ImageFont.truetype(fp, size)
        except: pass
    return ImageFont.load_default()

F_H1  = lf(r'C:\Windows\Fonts\msyh.ttc', 20)
F_H2  = lf(r'C:\Windows\Fonts\msyh.ttc', 16)
F_BODY= lf(r'C:\Windows\Fonts\msyh.ttc', 13)
F_NOTE= lf(r'C:\Windows\Fonts\msyh.ttc', 11)
F_FOOT= lf(r'C:\Windows\Fonts\msyh.ttc', 10)

BG      = (248, 250, 252)
HEADER  = (15, 23, 42)
RED     = (220, 38, 38)
GREEN   = (22, 163, 74)
BLUE    = (37, 99, 235)
ORANGE  = (234, 88, 12)
PURPLE  = (124, 58, 237)
WHT     = (255, 255, 255)
BLK     = (15, 23, 42)
GRY_DK  = (71, 85, 105)
GRY_MD  = (100, 116, 139)
GRY_LT  = (148, 163, 184)
LINE    = (226, 232, 240)
CW      = W - PAD * 2

def wrap(font, text):
    res, cur = [], ''
    for c in text:
        t = cur + c
        try: tw = font.getlength(t)
        except: tw = len(t) * font.size * 0.6
        if tw > CW and cur:
            res.append(cur); cur = c
        else: cur = t
    if cur: res.append(cur)
    return res

def rect(x, y, w, h, color, r=6):
    draw.rounded_rectangle([x, y, x+w, y+h], radius=r, fill=color)

def txt(text, x, y, font, color):
    draw.text((x, y), text, font=font, fill=color)

def chip(text, x, y, bg, fc, font):
    try: tw = font.getlength(text)
    except: tw = len(text) * font.size * 0.6
    rect(x, y, tw + 14, font.size + 8, bg, r=4)
    txt(text, x + 7, y + 4, font, fc)
    return tw + 18

# ── 收盘数据 ──
market = [
    ('创业板', '-7.15%', RED),
    ('沪指', '-3.05%', RED),
    ('深成指', '-5.40%', RED),
    ('成交额', '2.67万亿', GRY_MD),
    ('下跌个股', '4900+', RED),
    ('跌超9%', '660+只', RED),
]
down_sectors = '算力硬件、半导体芯片、医药医疗、贵金属、游戏传媒'
up_sectors   = '电力、银行、石油天然气、港口'

# ── 小作文 ──
articles = [
    {
        'num': '01',
        'title': '科技股"三撞头"杀估值',
        'body': 'A股这轮跌的不是指数，是信仰。涨太急 + 交易太拥挤 + 业绩还没证明自己，三个因素撞在一起。存储芯片、先进封装连续调整，资金跑向高压氧舱、猪肉等防御方向——市场不是没钱，是钱开始犹豫了。',
    },
    {
        'num': '02',
        'title': '量化多杀多，五日线成绞肉机',
        'body': '早盘科技股超预期高开，资金短暂达成反攻共识→反弹到五日线被量化模型捕捉→量化触发卖出信号→多杀多踩踏。资深玩家直言：量化一致性比2015年还强，完全没有人类"欲走还留"的复杂心态。',
    },
    {
        'num': '03',
        'title': '业绩窗口压力，证伪期来临',
        'body': '7月是中报预报密集期，科技股前期拔估值拔得太满，现在进入"业绩证伪"阶段——估值太贵、增速不达预期的个股开始被资金抛弃。',
    },
    {
        'num': '04',
        'title': '台积电期权到期 + 韩国KOSPI暴跌传导',
        'body': '7月16日台积电期权到期，加上韩国KOSPI暴跌6.4%触发熔断，日经-2.79%，亚太科技链全面崩塌，情绪传导到A股半导体。',
    },
    {
        'num': '05',
        'title': 'WAIC利好兑现出货',
        'body': '今日本身是WAIC大会开幕，华为昇腾Atlas 950首秀算力炸裂，但市场选择了"利好兑现是利空"——资金趁高开出逃。',
    },
]

# 散户吐槽
quotes = [
    ('散户A', '大A这种毫无人性的瀑布A杀，在历史上都是罕见的。这波科技股的A杀，杀伤力丝毫不亚于15年那波。'),
    ('散户B', '现在量化一致性比2015年还强，根本没有人类欲走还留的复杂心态。'),
]

# ── 预计算高度 ──
y = PAD + 80 + 10 + 3 + 20  # top + header + divider

# 收盘数据行
y += 20  # 标题
for name, val, col in market:
    y += 18 + 8  # 行高
y += 16  # gap

# 涨跌板块
y += 20 + 14
for line in wrap(F_BODY, down_sectors): y += F_BODY.size + 4
y += 8
for line in wrap(F_BODY, up_sectors): y += F_BODY.size + 4
y += 16  # gap

# 小作文
for art in articles:
    y += 22  # 编号+标题
    y += 4
    for line in wrap(F_BODY, art['body']): y += F_BODY.size + 5
    y += 10

# 散户吐槽
y += 16
for qname, qbody in quotes:
    y += 20
    for line in wrap(F_BODY, qbody): y += F_BODY.size + 5
    y += 8

# 结论
y += 16 + 60 + 16 + 36
H = y

img = Image.new('RGB', (W, H), BG)
draw = ImageDraw.Draw(img)
y = PAD

# ── 顶部 ──
rect(0, y, W, 80, HEADER, r=0)
txt('📉 7月17日收盘复盘', PAD, y + 12, F_H1, WHT)
txt('创业板-7.15% 沪指-3.05% 深成指-5.40%  |  成交2.67万亿  下跌4900+只', PAD, y + 50, F_NOTE, (148, 163, 184))
y += 90
draw.rectangle([0, y, W, y + 3], fill=RED)
y += 18

# ── 收盘数据 ──
rect(PAD, y, W - PAD * 2, 30, (254, 242, 242), r=6)
txt('关键数据', PAD + 12, y + 6, F_H2, RED)
y += 40

# 数据卡片 2x3
cols = 3
col_w = (W - PAD * 2 - 10) // cols
for i, (name, val, col) in enumerate(market):
    cx = PAD + (i % cols) * (col_w + 5)
    cy = y + (i // cols) * 34
    rect(cx, cy, col_w, 28, (255,255,255), r=5)
    draw.rectangle([cx, cy, cx+3, cy+28], fill=col, width=0)
    txt(name, cx + 8, cy + 2, F_NOTE, GRY_MD)
    txt(val, cx + 8, cy + 14, F_BODY, col)

y += (len(market) // cols + 1) * 34 + 16

# ── 涨跌板块 ──
rect(PAD, y, W - PAD * 2, 28, (239, 68, 68), r=5)
txt('重灾区: ' + down_sectors[:30] + '...', PAD + 10, y + 5, F_NOTE, WHT)
y += 38
rect(PAD, y, W - PAD * 2, 28, (22, 163, 74), r=5)
txt('逆势上涨: ' + up_sectors, PAD + 10, y + 5, F_NOTE, WHT)
y += 44

# ── 小作文 ──
rect(PAD, y, W - PAD * 2, 28, BLUE, r=5)
txt(' 市场小作文 / 原因分析', PAD + 10, y + 5, F_H2, WHT)
y += 38

for art in articles:
    # 编号圆点
    rect(PAD, y, 28, 28, (37, 99, 235), r=14)
    txt(art['num'], PAD + 6, y + 5, F_BODY, WHT)

    # 标题
    txt(art['title'], PAD + 36, y + 2, F_H2, BLK)
    y += 28

    # 正文
    lines = wrap(F_BODY, art['body'])
    for line in lines:
        txt(line, PAD + 36, y, F_BODY, GRY_DK)
        y += F_BODY.size + 5
    y += 10
    draw.line([(PAD + 36, y), (W - PAD, y)], fill=LINE, width=1)
    y += 10

# ── 散户吐槽 ──
y += 6
rect(PAD, y, W - PAD * 2, 28, (100, 116, 139), r=5)
txt(' 散户社区吐槽', PAD + 10, y + 5, F_H2, WHT)
y += 38

for qname, qbody in quotes:
    rect(PAD, y, W - PAD * 2, 4, (100, 116, 139), r=2)
    y += 8
    txt(qname, PAD, y, F_NOTE, GRY_MD)
    y += F_NOTE.size + 4
    lines = wrap(F_BODY, qbody)
    for line in lines:
        txt(line, PAD, y, F_BODY, GRY_DK)
        y += F_BODY.size + 5
    y += 8

# ── 结论 ──
y += 10
rect(PAD, y, W - PAD * 2, 55, (254, 245, 254), r=8)
draw.rectangle([PAD, y, PAD + 4, y + 55], fill=PURPLE)
txt('结论', PAD + 14, y + 8, F_H2, PURPLE)
txt('内因（涨多了、拥挤交易、业绩证伪）是主跌逻辑；外因（亚太科技股暴跌）是催化剂和情绪放大器。', PAD + 14, y + 30, F_BODY, GRY_DK)
y += 68

# ── Footer ──
draw.line([(PAD, y), (W - PAD, y)], fill=LINE, width=1)
y += 12
txt('仅供参考，不构成投资建议。市场有风险，投资需谨慎。', PAD, y, F_FOOT, GRY_LT)
y += F_FOOT.size + 4
txt('数据来源：腾讯新闻 / 钛媒体 / 微博 / 同花顺  |  2026-07-17 收盘  |  QClaw量化系统', PAD, y, F_FOOT, GRY_LT)

img = img.crop((0, 0, W, y + 24))
OUT = r'D:\mystock\report_daily\_market_20260717.png'
img.save(OUT, 'PNG', optimize=True)
print(f'IMG: {OUT}  size={img.size}  {os.path.getsize(OUT):,} bytes')
