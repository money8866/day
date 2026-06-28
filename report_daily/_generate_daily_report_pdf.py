# -*- coding: utf-8 -*-
"""生成每日复盘PDF报告"""
import os
from datetime import datetime

# 注册中文字体
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_registered = False
for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            break
        except:
            continue

chinese_font = 'ChineseFont' if font_registered else 'Helvetica'

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# 创建PDF
pdf_path = r'D:\mystock\report_daily\Final_Self_20260626.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

# 样式
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=20, spaceAfter=20, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=14, spaceAfter=10, spaceBefore=15, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CH2', fontName=chinese_font, fontSize=12, spaceAfter=8, spaceBefore=10, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, spaceAfter=3, leftIndent=20, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CHighlight', fontName=chinese_font, fontSize=10, spaceAfter=4, leading=14, textColor=colors.HexColor('#e74c3c'), backColor=colors.HexColor('#fff5f5')))

story = []

# 标题
story.append(Paragraph('每日复盘报告', styles['CTitle']))
story.append(Paragraph('2026-06-26', styles['CTitle']))
story.append(Spacer(1, 0.5*cm))

# 1、大盘情绪
story.append(Paragraph('一、大盘情绪', styles['CH1']))
story.append(Paragraph('市场状态：<b>弱势</b>。全市场普跌，情绪退潮，炸板率高企。', styles['CBody']))
story.append(Paragraph('总仓位建议：严格控制在<b>25%</b>以内，以防守为主。', styles['CHighlight']))
story.append(Paragraph('操作要点：', styles['CBody']))
story.append(Paragraph('• 仅可轻仓在核心抱团主线（半导体产业链）中寻找分歧低吸机会', styles['CBullet']))
story.append(Paragraph('• 非主线及退潮板块应果断规避，切勿追高', styles['CBullet']))
story.append(Spacer(1, 0.3*cm))

# 2、今日主题分析
story.append(Paragraph('二、今日主题分析', styles['CH1']))
story.append(Paragraph('市场呈现极致的结构性撕裂，资金全面涌入半导体材料、设备为核心的科技自主链，形成抱团主升。权重蓝筹及周期性板块遭遇资金抽离，进入退潮期。', styles['CBody']))
story.append(Spacer(1, 0.2*cm))

story.append(Paragraph('主要关注主题及补涨中军：', styles['CH2']))
story.append(Paragraph('• 半导体材料：沪硅产业、雅克科技、立昂微', styles['CBullet']))
story.append(Paragraph('• 半导体设备：拓荆科技、长川科技、大族激光', styles['CBullet']))
story.append(Paragraph('• 光刻机链：大族激光、沃格光电', styles['CBullet']))
story.append(Paragraph('• 存储芯片：北京君正、兆易创新', styles['CBullet']))
story.append(Paragraph('• 先进封装材料：长电科技、晶方科技', styles['CBullet']))
story.append(Spacer(1, 0.2*cm))

story.append(Paragraph('明日主题预测：', styles['CH2']))
story.append(Paragraph('<b>明日最看好：半导体材料</b>（趋势与情绪分均排名第一，抱团主升最强，资金深度介入）', styles['CBullet']))
story.append(Paragraph('<b>明日次看好：半导体设备</b>（强趋势延续，情绪健康，与材料主题形成联动共振）', styles['CBullet']))
story.append(Spacer(1, 0.3*cm))

# 3、今日低吸股票池
story.append(Paragraph('三、今日低吸股票池（二波评分≥10分）', styles['CH1']))

low_buy_data = [
    ['排名', '名称', '代码', '评分', '形态', '入场价', '止损价', '目标价'],
    ['1', '银禧科技', '300221.SZ', '40分', 'V型急跌', '15.45', '13.60', '19.31'],
    ['2', '蔚蓝锂芯', '002245.SZ', '29分', '放量回调', '19.20', '16.90', '24.00'],
    ['3', '奥比中光', '688322.SH', '20分', '放量回调', '119.85', '105.47', '149.81'],
]

table = Table(low_buy_data, colWidths=[1*cm, 2.2*cm, 2.2*cm, 1.5*cm, 2*cm, 1.8*cm, 1.8*cm, 1.8*cm])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(table)
story.append(Spacer(1, 0.3*cm))

# 4、今日强势股票池
story.append(Paragraph('四、今日强势股票池', styles['CH1']))

strong_data = [
    ['排名', '名称', '代码', '评分', '失败率', '主题', '信号'],
    ['1', '三孚新科', '688359.SH', '89.9', '37.4%', 'PCB电子电路', '有效突破'],
    ['2', '华海诚科', '688535.SH', '81.7', '41.7%', '先进封装材料', '有效突破'],
    ['3', '立昂微', '605358.SH', '77.0', '40.3%', '功率半导体', '突破迹象'],
    ['4', '安洁科技', '002635.SZ', '52.6', '53.4%', '消费电子', '有效突破'],
    ['5', '天准科技', '688003.SH', '52.5', '60.3%', '人形机器人', '有效突破'],
]

table2 = Table(strong_data, colWidths=[1*cm, 2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 2.5*cm, 2*cm])
table2.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(table2)
story.append(Spacer(1, 0.3*cm))

# 5、今日趋势股池
story.append(Paragraph('五、今日趋势股池', styles['CH1']))

trend_data = [
    ['名称', '代码', '涨幅', '评分', '波段', '量比', '距MA20', '距MA60'],
    ['德龙激光', '688170.SH', '+5.2%', '85分', 'D2', '1.51', '+12.3%', '+28.0%'],
    ['珠海冠宇', '688772.SH', '+7.6%', '70分', 'D1', '1.48', '+15.8%', '+24.3%'],
]

table3 = Table(trend_data, colWidths=[2.2*cm, 2.2*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm])
table3.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#9b59b6')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(table3)
story.append(Spacer(1, 0.3*cm))

# 6、今日中线股池
story.append(Paragraph('六、今日中线股池（近20天共7个B浪信号）', styles['CH1']))

mid_data = [
    ['名称', '代码', '评分', '信号', 'A浪涨幅', 'B浪回调', '启动日'],
    ['德方纳米', '300769.SZ', '79分', '启动信号', '85.2%', '24.2%', '20260618'],
    ['株冶集团', '600961.SH', '76分', '启动信号', '80.4%', '25.8%', '20260617'],
    ['招商轮船', '601872.SH', '75分', '启动信号', '101.8%', '21.9%', '20260617'],
    ['卓易信息', '688258.SH', '72分', '底背离', '104.3%', '25.9%', '20260623'],
    ['美诺华', '603538.SH', '70分', '底背离', '142.8%', '31.2%', '20260622'],
    ['安靠智电', '300617.SZ', '66分', '底背离', '87.7%', '15.6%', '20260623'],
    ['泽宇智能', '301179.SZ', '65分', '底背离', '80.1%', '29.5%', '20260624'],
]

table4 = Table(mid_data, colWidths=[2.2*cm, 2.2*cm, 1.5*cm, 2*cm, 2*cm, 2*cm, 2*cm])
table4.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f39c12')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),
    ('FONTSIZE', (0, 0), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))
story.append(table4)
story.append(Spacer(1, 0.5*cm))

# 页脚
story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['CBody']))
story.append(Paragraph('版本：Final_Self_20260626', styles['CBody']))

# 生成PDF
doc.build(story)

print('='*80)
print('每日复盘PDF报告生成完成！')
print('='*80)
print(f'\n文件路径：{pdf_path}')
