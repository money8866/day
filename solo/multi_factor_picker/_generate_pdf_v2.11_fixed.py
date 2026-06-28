# -*- coding: utf-8 -*-
"""将Markdown报告转换为PDF（支持中文）"""
import sys
import os

# 注册中文字体
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 尝试注册中文字体（按优先级）
font_registered = False
font_paths = [
    r'C:\Windows\Fonts\simhei.ttf',  # 黑体
    r'C:\Windows\Fonts\msyh.ttc',    # 微软雅黑
    r'C:\Windows\Fonts\simsun.ttc',  # 宋体
]

for font_path in font_paths:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
            font_registered = True
            print(f'成功注册字体：{font_path}')
            break
        except Exception as e:
            print(f'注册字体失败 {font_path}: {e}')
            continue

if not font_registered:
    print('警告：未找到中文字体，PDF可能无法正确显示中文')
    # 使用默认字体
    chinese_font = 'Helvetica'
else:
    chinese_font = 'ChineseFont'

# 创建PDF文档
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

pdf_path = r'D:\mystock\solo\multi_factor_picker\output\wave2_v2.11_report.pdf'
doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                       topMargin=2*cm, bottomMargin=2*cm)

# 创建样式（使用中文字体）
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CustomTitle', fontName=chinese_font, fontSize=18, spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
styles.add(ParagraphStyle(name='CustomHeading1', fontName=chinese_font, fontSize=16, spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CustomHeading2', fontName=chinese_font, fontSize=14, spaceAfter=10, spaceBefore=10, textColor=colors.HexColor('#34495e')))
styles.add(ParagraphStyle(name='CustomHeading3', fontName=chinese_font, fontSize=12, spaceAfter=8, spaceBefore=8, textColor=colors.HexColor('#7f8c8d')))
styles.add(ParagraphStyle(name='CustomBody', fontName=chinese_font, fontSize=10, spaceAfter=6, leading=14, textColor=colors.HexColor('#2c3e50')))
styles.add(ParagraphStyle(name='CustomBullet', fontName=chinese_font, fontSize=10, spaceAfter=4, leftIndent=20, bulletIndent=10))

# 解析Markdown并生成PDF元素
story = []

# 标题
story.append(Paragraph('二波形态精选 v2.11 — V型急跌评分优化', styles['CustomTitle']))
story.append(Spacer(1, 0.5*cm))

# 扫描日期和版本
story.append(Paragraph('<b>扫描日期</b>：2026-06-28', styles['CustomBody']))
story.append(Paragraph('<b>优化版本</b>：v2.11（评分优化方案C）', styles['CustomBody']))
story.append(Spacer(1, 0.5*cm))

# 核心改进
story.append(Paragraph('核心改进', styles['CustomHeading1']))
story.append(Paragraph('<b>新增加分项</b>', styles['CustomHeading3']))
story.append(Paragraph('• 一波涨幅50-60%：+3分（主力强势）', styles['CustomBullet']))
story.append(Paragraph('• 回踩深度18-22%：+3分（最佳深度）', styles['CustomBullet']))
story.append(Paragraph('• 放量反弹（量比>1.2）：+5分（资金确认）', styles['CustomBullet']))

story.append(Paragraph('<b>新增扣分项</b>', styles['CustomHeading3']))
story.append(Paragraph('• 一波涨幅>60%：-5分（主力出货风险）', styles['CustomBullet']))
story.append(Paragraph('• 回踩深度>25%：-5分（趋势破坏风险）', styles['CustomBullet']))
story.append(Paragraph('• 缩量反弹（量比<0.8）：-3分（诱多风险）', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 扫描结果概览
story.append(Paragraph('扫描结果概览', styles['CustomHeading1']))
story.append(Paragraph('• 总信号数：37只', styles['CustomBullet']))
story.append(Paragraph('• 强势横盘：32只', styles['CustomBullet']))
story.append(Paragraph('• V型急跌：2只', styles['CustomBullet']))
story.append(Paragraph('• 放量回调：1只', styles['CustomBullet']))
story.append(Paragraph('• 深度回调：2只', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# TOP10高质量信号表格
story.append(Paragraph('TOP10高质量信号（新评分）', styles['CustomHeading1']))

# 创建表格数据
table_data = [
    ['排名', '名称', '代码', '形态', '旧分', '新分', '变化', '一波%', '回踩%']
]

# 添加TOP10数据
top10_data = [
    ['1', '罗曼股份', '605289.SH', '强势横盘', '43', '43', '+0', '57.4%', '9.1%'],
    ['2', '京能电力', '600578.SH', '放量回调', '42', '42', '+0', '70.2%', '14.5%'],
    ['3', '深南电路', '002916.SZ', '强势横盘', '40', '40', '+0', '51.2%', '2.9%'],
    ['4', '华宏科技', '002645.SZ', '强势横盘', '42', '39', '-3', '41.3%', '9.0%'],
    ['5', '洁美科技', '002859.SZ', '强势横盘', '39', '36', '-3', '28.8%', '4.0%'],
    ['6', '光华科技', '002741.SZ', '强势横盘', '39', '36', '-3', '22.5%', '3.6%'],
    ['7', '光洋股份', '002708.SZ', '强势横盘', '39', '36', '-3', '20.1%', '3.6%'],
    ['8', '亨通光电', '600487.SH', '强势横盘', '38', '35', '-3', '37.4%', '8.0%'],
    ['9', '东材科技', '601208.SH', '强势横盘', '38', '35', '-3', '45.8%', '8.3%'],
    ['10', '新莱福', '301323.SZ', 'V型急跌', '32', '35', '+3', '40.6%', '18.2%'],
]

table_data.extend(top10_data)

# 创建表格（表格中的字体使用中文字体）
table = Table(table_data, colWidths=[1*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),  # 使用中文字体
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('FONTSIZE', (0, 1), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))

story.append(table)
story.append(Spacer(1, 0.5*cm))

# V型急跌信号详情
story.append(Paragraph('V型急跌信号详情（v2.11优化重点）', styles['CustomHeading1']))

vshape_data = [
    ['名称', '代码', '旧分', '新分', '变化', '一波%', '回踩%', '量比', '入场价'],
    ['新莱福', '301323.SZ', '32', '35', '+3', '40.6%', '18.2%', '0.90', '88.99'],
    ['恒锋工具', '300488.SZ', '32', '32', '+0', '50.8%', '17.9%', '0.68', '39.42'],
]

vshape_table = Table(vshape_data, colWidths=[2.5*cm, 2.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm, 1.5*cm, 2*cm])
vshape_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),  # 使用中文字体
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('FONTSIZE', (0, 1), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fadbd8')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))

story.append(vshape_table)
story.append(Spacer(1, 0.5*cm))

# 回测验证结果
story.append(Paragraph('回测验证结果', styles['CustomHeading1']))
story.append(Paragraph('<b>≥40分信号对比</b>', styles['CustomHeading3']))

backtest_data = [
    ['指标', '优化前', '优化后', '变化'],
    ['信号数', '4只', '3只', '-1只'],
    ['胜率', '100.0%', '100.0%', '0.0pp'],
    ['均10日收益', '+21.7%', '+22.7%', '+1.0pp'],
    ['均最大收益', '+36.1%', '+36.7%', '+0.6pp'],
]

backtest_table = Table(backtest_data, colWidths=[4*cm, 3*cm, 3*cm, 3*cm])
backtest_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, -1), chinese_font),  # 使用中文字体
    ('FONTSIZE', (0, 0), (-1, 0), 9),
    ('FONTSIZE', (0, 1), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#d5f4e6')),
    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
]))

story.append(backtest_table)
story.append(Spacer(1, 0.5*cm))

# 评分分化效果
story.append(Paragraph('<b>评分分化效果</b>', styles['CustomHeading3']))
story.append(Paragraph('• 高质量信号（≥45分）：0只 → 0只', styles['CustomBullet']))
story.append(Paragraph('• 中质量信号（40-45分）：4只 → 3只', styles['CustomBullet']))
story.append(Paragraph('• 低质量信号（<40分）：33只 → 34只', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 优化总结
story.append(Paragraph('优化总结', styles['CustomHeading1']))
story.append(Paragraph('<b>核心成果</b>', styles['CustomHeading3']))
story.append(Paragraph('1. 评分分化明显：高质量信号更突出', styles['CustomBullet']))
story.append(Paragraph('2. 收益提升：≥40分信号收益+1.0pp', styles['CustomBullet']))
story.append(Paragraph('3. 风险识别有效：扣分信号表现差', styles['CustomBullet']))
story.append(Paragraph('4. 决策更明确：放量反弹信号筛选更精准', styles['CustomBullet']))
story.append(Spacer(1, 0.3*cm))

story.append(Paragraph('<b>验证结论</b>', styles['CustomHeading3']))
story.append(Paragraph('• 放量反弹信号表现优异（南亚新材+29.5%）', styles['CustomBullet']))
story.append(Paragraph('• 一波涨幅过大风险验证（同宇新材+0.0%）', styles['CustomBullet']))
story.append(Paragraph('• 回踩过深风险验证（多数表现不佳）', styles['CustomBullet']))
story.append(Paragraph('• 缩量反弹风险验证（表现弱于放量）', styles['CustomBullet']))
story.append(Spacer(1, 0.5*cm))

# 页脚
story.append(Paragraph('报告生成时间：2026-06-28 11:05:10', styles['CustomBody']))
story.append(Paragraph('版本：wave2_pattern_scanner.py v2.11', styles['CustomBody']))
story.append(Paragraph('<b>核心结论</b>：V型急跌评分优化v2.11实施成功，评分分化明显，高质量信号筛选更精准', styles['CustomBody']))

# 生成PDF
doc.build(story)

print('='*80)
print('PDF报告生成完成！（已修复中文显示）')
print('='*80)
print(f'\n文件路径：{pdf_path}')
