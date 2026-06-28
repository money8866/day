# -*- coding: utf-8 -*-
"""
基本面信息挖掘系统 v1.0
功能：从公告中挖掘新订单、新产品、新项目信息
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
import time

# Tushare token
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'

print('='*80)
print('基本面信息挖掘系统 v1.0')
print('='*80)

# 初始化Tushare
pro = ts.pro_api(TUSHARE_TOKEN)

class AnnouncementMiner:
    """公告信息挖掘器"""
    
    # 关键词字典
    KEYWORDS = {
        '新订单': {
            'keywords': ['中标', '合同', '订单', '供货', '签约', '意向书'],
            'weight': 10,
            'description': '新订单/合同'
        },
        '新产品': {
            'keywords': ['新产品', '发布', '量产', '下线', '上市', '推出'],
            'weight': 8,
            'description': '新产品发布'
        },
        '新项目': {
            'keywords': ['投资', '项目', '建设', '扩产', '开工', '投产'],
            'weight': 7,
            'description': '新项目/产能扩张'
        },
        '技术突破': {
            'keywords': ['专利', '认证', '突破', '研发', '通过认证'],
            'weight': 6,
            'description': '技术突破/专利'
        },
        '重大合同': {
            'keywords': ['重大合同', '重大订单', '战略合作'],
            'weight': 9,
            'description': '重大合同'
        },
    }
    
    def __init__(self, pro):
        self.pro = pro
        
    def get_latest_anns(self, ts_code, days=7):
        """获取最近N天的公告"""
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
        
        try:
            df = self.pro.anns(ts_code=ts_code, 
                                ann_date=start_date, 
                                end_date=end_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            pass
        
        return None
    
    def analyze_ann(self, ts_code, ann_title, ann_date, ann_content=''):
        """分析单条公告，返回匹配的关键信息"""
        results = []
        
        for category, config in self.KEYWORDS.items():
            keywords = config['keywords']
            weight = config['weight']
            desc = config['description']
            
            # 检查标题
            title_match = any(kw in ann_title for kw in keywords)
            
            # 检查内容（如果有）
            content_match = False
            if ann_content:
                content_match = any(kw in ann_content for kw in keywords)
            
            if title_match or content_match:
                results.append({
                    'ts_code': ts_code,
                    'title': ann_title,
                    'category': category,
                    'description': desc,
                    'weight': weight,
                    'ann_date': ann_date,
                })
        
        return results
    
    def mine_pool(self, ts_codes, days=7):
        """挖掘股票池的公告信息"""
        all_results = []
        
        print(f'\n开始挖掘 {len(ts_codes)} 只股票的公告信息...')
        print(f'时间范围：最近 {days} 天')
        print('-'*80)
        
        for i, ts_code in enumerate(ts_codes, 1):
            if i % 50 == 0:
                print(f'进度: {i}/{len(ts_codes)} ({i/len(ts_codes)*100:.1f}%)')
            
            # 获取公告
            df_anns = self.get_latest_anns(ts_code, days)
            
            if df_anns is not None:
                # 分析每条公告
                for idx, row in df_anns.iterrows():
                    ann_title = row.get('title', '')
                    ann_date = row.get('ann_date', '')
                    ann_content = row.get('content', '')
                    
                    results = self.analyze_ann(ts_code, ann_title, ann_date, ann_content)
                    
                    if results:
                        all_results.extend(results)
            
            # Tushare API限速
            time.sleep(0.05)
        
        print(f'\n挖掘完成！共发现 {len(all_results)} 条重要信息')
        
        return all_results
    
    def score_and_rank(self, results):
        """评分并排序"""
        if not results:
            return []
        
        # 转换为DataFrame
        df = pd.DataFrame(results)
        
        # 按股票代码分组，汇总评分
        grouped = df.groupby('ts_code').agg({
            'weight': 'sum',  # 评分累加
            'title': lambda x: list(x),  # 标题列表
            'category': lambda x: list(x),  # 类别列表
            'ann_date': 'max',  # 最新公告日期
        }).reset_index()
        
        # 评分排序
        grouped = grouped.sort_values('weight', ascending=False)
        
        return grouped

def main():
    """主函数"""
    
    # 1. 读取合格股池
    print('\n[1/4] 读取合格股池...')
    qualified_pool = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
    print(f'合格股池：{len(qualified_pool)}只')
    
    # 提取股票代码
    ts_codes = qualified_pool['ts_code'].tolist()
    
    # 2. 初始化挖掘器
    print('\n[2/4] 初始化公告挖掘器...')
    miner = AnnouncementMiner(pro)
    
    # 3. 挖掘公告信息
    print('\n[3/4] 开始挖掘公告信息...')
    results = miner.mine_pool(ts_codes, days=7)
    
    # 4. 评分排序
    print('\n[4/4] 评分排序...')
    ranked = miner.score_and_rank(results)
    
    if len(ranked) > 0:
        # 保存结果
        output_csv = r'D:\mystock\solo\multi_factor_picker\output\basic_info_mining_20260628.csv'
        ranked.to_csv(output_csv, index=False, encoding='utf-8-sig')
        
        print(f'\n结果已保存: {output_csv}')
        print(f'\nTOP10 重要信息：')
        print('-'*80)
        for i, (idx, row) in enumerate(ranked.head(10).iterrows(), 1):
            print(f'\n{i}. {row["ts_code"]} - 评分{row["weight"]}分')
            print(f'   最新公告: {row["ann_date"]}')
            print(f'   信息类别: {", ".join(set(row["category"]))}')
            print(f'   公告标题: {row["title"][0][:50]}...' if len(row['title']) > 0 else '')
        
        # 生成PDF报告
        print(f'\n生成PDF报告...')
        generate_pdf_report(ranked)
        
    else:
        print('\n未发现重要基本面信息')
        print('建议：')
        print('  1. 扩大时间范围（当前7天）')
        print('  2. 增加关键词')
        print('  3. 检查Tushare token是否有效')

def generate_pdf_report(ranked_df):
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
    pdf_path = r'D:\mystock\solo\multi_factor_picker\output\basic_info_mining_20260628.pdf'
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, 
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
    story.append(Paragraph('基本面信息挖掘报告', styles['CTitle']))
    story.append(Paragraph('2026-06-28', styles['CTitle']))
    story.append(Spacer(1, 0.5*cm))
    
    # 概览
    story.append(Paragraph('挖掘概览', styles['CH1']))
    story.append(Paragraph(f'• 扫描股票池：933只（BullScore ≥55分）', styles['CBullet']))
    story.append(Paragraph(f'• 时间范围：最近7天', styles['CBullet']))
    story.append(Paragraph(f'• 发现重要信息：{len(ranked_df)}条', styles['CBullet']))
    story.append(Spacer(1, 0.5*cm))
    
    # TOP20表格
    story.append(Paragraph('TOP20 重要信息', styles['CH1']))
    
    table_data = [['排名', '代码', '评分', '类别', '最新公告']]
    for i, (idx, row) in enumerate(ranked_df.head(20).iterrows(), 1):
        categories = ', '.join(set(row['category']))
        table_data.append([
            str(i), row['ts_code'], f"{row['weight']}分", 
            categories[:10], str(row['ann_date'])
        ])
    
    table = Table(table_data, colWidths=[1*cm, 2.5*cm, 1.5*cm, 3*cm, 2.5*cm])
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
    story.append(Paragraph('数据来源：Tushare公告接口', styles['CBody']))
    
    # 生成PDF
    doc.build(story)
    
    print(f'\nPDF报告已生成: {pdf_path}')
    
    return pdf_path

if __name__ == '__main__':
    main()
