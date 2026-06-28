# -*- coding: utf-8 -*-
"""
基本面信息评分与报告生成系统
功能：读取手动录入的基本面信息，自动评分并生成PDF报告
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from datetime import datetime
import os

print('='*80)
print('基本面信息评分与报告生成系统')
print('='*80)

# 评分标准
SCORE_RULES = {
    '新订单': {
        '重大合同': 10,      # 金额>1亿
        '中等订单': 8,       # 金额1000万-1亿
        '小订单': 6,         # 金额<1000万
    },
    '新产品': {
        '战略级新产品': 8,   # 颠覆性技术/全新品类
        '重要新产品': 7,     # 性能提升>30%
        '一般新产品': 6,     # 性能提升<30%
    },
    '新项目': {
        '重大投资': 7,       # 金额>5亿
        '中等投资': 6,       # 金额1-5亿
        '小项目投资': 5,     # 金额<1亿
    },
    '技术突破': {
        '核心专利': 6,       # 发明专利/核心技术
        '产品认证': 5,       # 行业认证/客户认证
        '一般突破': 4,       # 技术改进
    },
}

def load_manual_data(csv_path):
    """读取手动录入的数据"""
    if not os.path.exists(csv_path):
        print(f'错误：文件不存在 - {csv_path}')
        return None
    
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    print(f'读取 {len(df)} 条记录')
    
    return df

def auto_score(row):
    """自动评分（基于信息类型和备注）"""
    info_type = row.get('info_type', '')
    amount = row.get('amount', 0)
    notes = row.get('notes', '')
    
    base_score = 5  # 基础分
    
    # 根据信息类型调整
    if info_type == '新订单':
        if pd.notna(amount) and amount > 100000000:  # >1亿
            base_score = 10
        elif pd.notna(amount) and amount > 10000000:  # >1000万
            base_score = 8
        else:
            base_score = 6
    
    elif info_type == '新产品':
        if '颠覆' in notes or '全新' in notes:
            base_score = 8
        elif '提升' in notes and ('30%' in notes or '30％' in notes):
            base_score = 7
        else:
            base_score = 6
    
    elif info_type == '新项目':
        if pd.notna(amount) and amount > 500000000:  # >5亿
            base_score = 7
        elif pd.notna(amount) and amount > 100000000:  # >1亿
            base_score = 6
        else:
            base_score = 5
    
    elif info_type == '技术突破':
        if '发明' in notes or '核心' in notes:
            base_score = 6
        elif '认证' in notes:
            base_score = 5
        else:
            base_score = 4
    
    return base_score

def generate_pdf_report(df, output_path):
    """生成PDF报告"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    
    # 注册中文字体
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
    
    # 创建PDF
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, 
                           topMargin=2*cm, bottomMargin=2*cm)
    
    # 样式
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CTitle', fontName=chinese_font, fontSize=18, 
                              spaceAfter=30, alignment=TA_CENTER, textColor=colors.HexColor('#1a1a1a')))
    styles.add(ParagraphStyle(name='CH1', fontName=chinese_font, fontSize=14, 
                              spaceAfter=12, spaceBefore=12, textColor=colors.HexColor('#2c3e50')))
    styles.add(ParagraphStyle(name='CBody', fontName=chinese_font, fontSize=10, 
                              spaceAfter=6, leading=14))
    styles.add(ParagraphStyle(name='CBullet', fontName=chinese_font, fontSize=10, 
                              spaceAfter=4, leftIndent=20))
    
    story = []
    
    # 标题
    story.append(Paragraph('基本面重要信息日报', styles['CTitle']))
    story.append(Paragraph(datetime.now().strftime('%Y-%m-%d'), styles['CTitle']))
    story.append(Spacer(1, 0.5*cm))
    
    # 概览
    story.append(Paragraph('信息概览', styles['CH1']))
    story.append(Paragraph(f'• 收录信息：{len(df)}条', styles['CBullet']))
    story.append(Paragraph(f'• 涉及股票：{df["ts_code"].nunique()}只', styles['CBullet']))
    
    # 按信息类型统计
    type_counts = df['info_type'].value_counts()
    for info_type, count in type_counts.items():
        story.append(Paragraph(f'• {info_type}：{count}条', styles['CBullet']))
    
    story.append(Spacer(1, 0.5*cm))
    
    # 按评分排序的表格
    story.append(Paragraph('重要信息列表（按评分排序）', styles['CH1']))
    
    # 评分（如果CSV中没有score列，自动计算）
    if 'score' not in df.columns:
        df['score'] = df.apply(auto_score, axis=1)
    
    # 排序
    df_sorted = df.sort_values('score', ascending=False)
    
    table_data = [['排名', '代码', '名称', '类型', '标题', '评分', '日期']]
    for i, (idx, row) in enumerate(df_sorted.iterrows(), 1):
        table_data.append([
            str(i),
            row['ts_code'],
            row.get('name', ''),
            row['info_type'],
            row['title'][:20] + '...' if len(row['title']) > 20 else row['title'],
            str(row['score']),
            str(row.get('ann_date', '')),
        ])
    
    table = Table(table_data, colWidths=[1*cm, 2*cm, 1.5*cm, 1.5*cm, 5*cm, 1*cm, 2*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), chinese_font),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
    ]))
    story.append(table)
    
    # 页脚
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(f'报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['CBody']))
    story.append(Paragraph('数据来源：手动录入 + 自动评分', styles['CBody']))
    
    # 生成PDF
    doc.build(story)
    
    print(f'\nPDF报告已生成: {output_path}')
    
    return output_path

def main():
    """主函数"""
    
    # 1. 读取手动录入的数据
    print('\n[1/3] 读取手动录入数据...')
    csv_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_manual.csv'
    df = load_manual_data(csv_path)
    
    if df is None:
        return
    
    # 2. 评分
    print('\n[2/3] 评分...')
    if 'score' not in df.columns:
        df['score'] = df.apply(auto_score, axis=1)
    
    print(f'评分完成，TOP3：')
    df_sorted = df.sort_values('score', ascending=False)
    for i, (idx, row) in enumerate(df_sorted.head(3).iterrows(), 1):
        print(f'  {i}. {row["ts_code"]} - {row["title"][:30]}... (评分{row["score"]})')
    
    # 3. 生成PDF报告
    print('\n[3/3] 生成PDF报告...')
    output_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_report_20260628.pdf'
    pdf_path = generate_pdf_report(df, output_path)
    
    print('\n完成！')
    print(f'PDF报告：{pdf_path}')
    print(f'CSV数据：{csv_path}')
    print('\n下一步：')
    print('  1. 手动填入更多股票的基本面信息到CSV')
    print('  2. 每天运行本脚本生成日报')
    print('  3. 推送到微信（集成OpenClaw message工具）')

if __name__ == '__main__':
    main()
