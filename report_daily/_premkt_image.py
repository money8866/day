# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw, ImageFont
import os

W = 600  # 手机阅读宽度
PAD = 20

def lf(fp, size):
    if os.path.exists(fp):
        try: return ImageFont.truetype(fp, size)
        except: pass
    return ImageFont.load_default()

F_TITLE = lf(r'C:\Windows\Fonts\msyh.ttc', 26)
F_DATE  = lf(r'C:\Windows\Fonts\msyh.ttc', 12)
F_SEC   = lf(r'C:\Windows\Fonts\msyh.ttc', 17)
F_ITEM  = lf(r'C:\Windows\Fonts\msyh.ttc', 15)
F_BODY  = lf(r'C:\Windows\Fonts\msyh.ttc', 14)
F_FOOT  = lf(r'C:\Windows\Fonts\msyh.ttc', 10)

BG      = (248, 250, 252)
HEADER  = (15, 23, 42)
ACCENT  = (220, 38, 38)
BLUE    = (37, 99, 235)
ORANGE  = (234, 88, 12)
PURPLE  = (124, 58, 237)
GREEN   = (22, 163, 74)
WHT     = (255, 255, 255)
BLK     = (15, 23, 42)
GRY     = (71, 85, 105)
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

sections = [
    {'icon': 'MACRO', 'title': '宏观 / 政策', 'color': BLUE,
     'items': [
         ('氦气临时禁止出口', '商务部、海关总署联合公告，对氦气实施临时禁止出口管理（保障国内供应），后续将适时调整。'),
         ('世界人工智能合作组织协定签署', '7月16日在上海签署，总部设于中国上海，王毅代表签署，推动AI国际合作与全球治理。'),
         ('海水淡化行动方案落地', '发改委等三部门印发方案：到2030年全国工程总规模达450万吨/日（新增150万吨/日），利好海水淡化装备/材料产业链。'),
         ('中荷经贸 / 安世半导体', '中荷双方同意推动企业协商解决安世半导体（Nexperia）纠纷，保障全球半导体产供链稳定。'),
     ]},
    {'icon': 'INDUS', 'title': '行业 / 板块', 'color': ORANGE,
     'items': [
         ('2026 WAIC 世界人工智能大会今日开幕', '7月17—20日上海举办，1100+企业参展、300+款全球首发产品。华为昇腾Atlas 950超节点真机首秀（可扩展至8192张芯片）。AI/国产算力板块事件催化密集。'),
         ('全球科技股剧烈震荡', '7月16日科创50跌4.02%，电子+通信主力净流出逾300亿元；韩国综指跌逾6%触发熔断、SK海力士跌逾11%、日经225跌2.79%；上证失守3900点，创业板指跌2.95%。前期涨幅较大板块需警惕兑现压力。', True),
         ('长鑫科技 IPO 中签率出炉', '国产存储龙头发行价8.66元/股，网上中签率0.4714%（创科创板纪录），募资约295亿（科创板历史第二高）。'),
         ('台积电 Q2 超预期', '净利润同比+77.4%，毛利率67.7%，上调全年营收增速至40%，资本支出加码至640亿美元，提振AI供应链信心。'),
         ('AI 手机逆势走强', '苹果牵手阿里、百度，格林精密、道明光学、福蓉科技等涨停。'),
         ('首批主动管理 ETF 试点 / DeepSeek IPO', '18家基金管理人入围（摩根、华泰柏瑞、易方达等）。DeepSeek启动科创板IPO筹备，最快年底申报。'),
     ]},
    {'icon': 'IPO', 'title': '新股 / 解禁', 'color': PURPLE,
     'items': [
         ('今日暂无新股申购', '—'),
         ('限售股解禁（7月17日）', '共8家公司解禁，合计2700.1万股，市值约6.31亿元。居前：智迪科技（1188万股/3.25亿）、特锐德（435.6万股/1.36亿）、信音电子（609.6万股/1.16亿）。'),
         ('临近解禁提醒', '技源集团6433.98万股（占总股本16.08%）将于7月23日解禁。'),
     ]},
    {'icon': 'GLOBAL', 'title': '外围市场', 'color': GREEN,
     'items': [
         ('美股 / 亚太', '美股三大指数集体收跌，纳指跌逾1%；费城半导体下挫。亚太科技股同步重挫（韩、日触发明显跌幅），隔夜情绪偏空。'),
         ('今日 A 股研判', '科技方向或承压，但WAIC事件催化与国产算力逻辑形成对冲；注意高低切换。'),
     ]},
]

# 预计算高度
y = PAD + 76 + 10 + 3 + 20
for sec in sections:
    y += 34 + 12
    for item in sec['items']:
        body = item[1]
        y += 20
        y += len(wrap(F_BODY, body)) * (F_BODY.size + 6) + 8
        y += 8
y += 16 + 120 + 16 + 36
H = y

img = Image.new('RGB', (W, H), BG)
draw = ImageDraw.Draw(img)
y = PAD

# 顶部
rect(0, y, W, 76, HEADER, r=0)
txt('A股盘前资讯速递', PAD, y + 12, F_TITLE, WHT)
txt('2026年7月17日（周五）  08:00  |  数据来源：证券时报 / 东方财富 / 腾讯财经', PAD, y + 50, F_DATE, (148, 163, 184))
y += 86
draw.rectangle([0, y, W, y + 3], fill=BLUE)
y += 18

for sec in sections:
    col = sec['color']
    rect(PAD, y, W - PAD * 2, 34, col, r=5)
    txt(sec['icon'], PAD + 10, y + 7, F_DATE, WHT)
    txt(sec['title'], PAD + 62, y + 5, F_SEC, WHT)
    y += 46

    for item in sec['items']:
        title = item[0]
        body = item[1]
        is_risk = item[2] if len(item) > 2 else False

        txt(title, PAD, y, F_ITEM, BLK)
        if is_risk:
            try: tw = F_ITEM.getlength(title)
            except: tw = len(title) * F_ITEM.size * 0.6
            rect(PAD + tw + 6, y + 1, 52, 18, (254, 226, 226), r=4)
            txt('!风险', PAD + tw + 10, y + 2, F_DATE, ACCENT)
        y += 22

        lines = wrap(F_BODY, body)
        for line in lines:
            txt(line, PAD, y, F_BODY, GRY)
            y += F_BODY.size + 6
        y += 8
        draw.line([(PAD, y), (W - PAD, y)], fill=LINE, width=1)
        y += 8

    y += 6

# 风险提示
y += 16
rect(PAD, y, W - PAD * 2, 110, (254, 242, 242), r=8)
draw.rectangle([PAD, y, PAD + 4, y + 110], fill=ACCENT)
txt('! 风险提示', PAD + 14, y + 10, F_SEC, ACCENT)
risks = [
    '上证指数跌破年线后尚未有效修复，技术面偏弱',
    '主力资金连续净流出，高位个股回调压力较大',
    '腾讯云8月起对MySQL本地盘超用部分计费，关注云计算板块成本压力',
    '融资客年内最大"卸杠杆"，注意高融资占比个股波动',
]
ry = y + 38
for r in risks:
    txt('- ' + r, PAD + 14, ry, F_BODY, GRY)
    ry += F_BODY.size + 7
y += 120

# Footer
y += 16
draw.line([(PAD, y), (W - PAD, y)], fill=LINE, width=1)
y += 12
txt('仅供参考，不构成投资建议。市场有风险，投资需谨慎。', PAD, y, F_FOOT, (148, 163, 184))
y += F_FOOT.size + 4
txt('数据来源：证券时报 / 新浪财经 / 东方财富 / 腾讯财经  |  整理时间：2026-07-17 08:00  |  QClaw量化系统', PAD, y, F_FOOT, (148, 163, 184))

img = img.crop((0, 0, W, y + 24))
OUT = r'D:\mystock\report_daily\_premkt_20260717.png'
img.save(OUT, 'PNG', optimize=True)
print(f'IMG: {OUT}  size={img.size}  {os.path.getsize(OUT):,} bytes')
