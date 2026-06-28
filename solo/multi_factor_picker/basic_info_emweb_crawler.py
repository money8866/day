# -*- coding: utf-8 -*-
"""
基本面信息挖掘系统 v1.2 - 东方财富F10版
数据源：东方财富网F10新闻+公告
优势：无需Token、数据全面、更新快
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
import json

print('='*80)
print('基本面信息挖掘系统 v1.2 - 东方财富F10版')
print('='*80)

class EmwebF10Crawler:
    """东方财富F10爬虫"""
    
    # 东方财富F10接口
    BASE_URL = 'http://emweb.estic.com.cn/f10'
    
    # 关键词字典
    KEYWORDS = {
        '新订单': {
            'keywords': ['中标', '合同', '订单', '供货', '签约', '意向书', '中标通知书'],
            'weight': 10,
            'description': '新订单/合同'
        },
        '新产品': {
            'keywords': ['新产品', '发布', '量产', '下线', '上市', '推出', '研制成功'],
            'weight': 8,
            'description': '新产品发布'
        },
        '新项目': {
            'keywords': ['投资', '项目', '建设', '扩产', '开工', '投产', '增资'],
            'weight': 7,
            'description': '新项目/产能扩张'
        },
        '技术突破': {
            'keywords': ['专利', '认证', '突破', '研发', '通过认证', '核心技术'],
            'weight': 6,
            'description': '技术突破/专利'
        },
        '重大合同': {
            'keywords': ['重大合同', '重大订单', '战略合作协议'],
            'weight': 9,
            'description': '重大合同'
        },
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://emweb.estic.com.cn/',
        })
    
    def convert_code(self, ts_code):
        """转换代码格式"""
        # 移除后缀
        if isinstance(ts_code, str):
            ts_code = ts_code.replace('.SZ', '').replace('.SH', '')
        
        # 补齐6位
        return f"{int(ts_code):06d}"
    
    def get_f10_news(self, ts_code, days=7):
        """获取F10公司新闻"""
        stock_code = self.convert_code(ts_code)
        
        # 判断市场
        market = '0' if stock_code.startswith('6') else '1'
        
        # 东方财富F10新闻接口
        url = f'http://emweb.estic.com.cn/f10/News/{market}/{stock_code}.js'
        
        print(f'      DEBUG: 获取 {ts_code} 新闻，URL: {url}')
        
        try:
            resp = self.session.get(url, timeout=10)
            
            print(f'      DEBUG: 状态码 {resp.status_code}, 内容长度 {len(resp.text)}')
            
            if resp.status_code == 200:
                # 解析JS数据
                text = resp.text
                print(f'      DEBUG: 返回内容前100字符: {text[:100]}')
                
                # 去掉JS包装
                if text.startswith('News('):
                    text = text[5:-1]
                
                data = json.loads(text)
                
                if data and 'data' in data:
                    print(f'      DEBUG: 找到 {len(data["data"])} 条新闻')
                    
                    # 过滤最近N天的 news
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=days)
                    
                    results = []
                    for news in data['data']:
                        news_date = datetime.strptime(news['date'], '%Y-%m-%d')
                        
                        if start_date <= news_date <= end_date:
                            results.append({
                                'title': news.get('title', ''),
                                'date': news.get('date', ''),
                                'content': news.get('content', ''),
                            })
                    
                    print(f'      DEBUG: 过滤后 {len(results)} 条新闻')
                    return results
                else:
                    print(f'      DEBUG: 数据格式错误或无数据')
        
        except Exception as e:
            print(f'      DEBUG: 异常 - {str(e)}')
        
        return []
    
    def get_f10_announcements(self, ts_code, days=7):
        """获取F10公告"""
        stock_code = self.convert_code(ts_code)
        
        # 判断市场
        market = '0' if stock_code.startswith('6') else '1'
        
        # 东方财富F10公告接口
        url = f'http://emweb.estic.com.cn/f10/Announcement/{market}/{stock_code}.js'
        
        try:
            resp = self.session.get(url, timeout=10)
            
            if resp.status_code == 200:
                # 解析JS数据
                text = resp.text
                # 去掉JS包装
                if '(' in text and text.endswith(')'):
                    text = text[text.find('(')+1:-1]
                
                data = json.loads(text)
                
                if data and 'data' in data:
                    # 过滤最近N天的公告
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=days)
                    
                    results = []
                    for ann in data['data']:
                        ann_date = datetime.strptime(ann['date'], '%Y-%m-%d')
                        
                        if start_date <= ann_date <= end_date:
                            results.append({
                                'title': ann.get('title', ''),
                                'date': ann.get('date', ''),
                                'url': ann.get('url', ''),
                            })
                    
                    return results
        
        except Exception as e:
            pass
        
        return []
    
    def analyze_content(self, ts_code, title, content_date):
        """分析内容，返回匹配的关键信息"""
        results = []
        
        for category, config in self.KEYWORDS.items():
            keywords = config['keywords']
            weight = config['weight']
            desc = config['description']
            
            if any(kw in title for kw in keywords):
                results.append({
                    'ts_code': ts_code,
                    'title': title,
                    'category': category,
                    'description': desc,
                    'weight': weight,
                    'date': content_date,
                })
        
        return results
    
    def mine_pool(self, ts_codes, days=7):
        """挖掘股票池的信息"""
        all_results = []
        
        print(f'\n开始挖掘 {len(ts_codes)} 只股票的基本面信息...')
        print(f'时间范围：最近 {days} 天')
        print('数据源：东方财富F10（新闻+公告）')
        print('-'*80)
        
        for i, ts_code in enumerate(ts_codes, 1):
            if i % 10 == 0:
                print(f'进度: {i}/{len(ts_codes)} ({i/len(ts_codes)*100:.1f}%)')
            
            # 获取F10新闻
            news_list = self.get_f10_news(ts_code, days)
            
            if news_list:
                for news in news_list:
                    results = self.analyze_content(ts_code, news['title'], news['date'])
                    if results:
                        all_results.extend(results)
            
            # 获取F10公告
            ann_list = self.get_f10_announcements(ts_code, days)
            
            if ann_list:
                for ann in ann_list:
                    results = self.analyze_content(ts_code, ann['title'], ann['date'])
                    if results:
                        all_results.extend(results)
            
            # 限速
            time.sleep(0.3)
        
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
            'date': 'max',  # 最新日期
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
    
    # 提取股票代码（前20只测试）
    ts_codes = qualified_pool['ts_code'].head(20).tolist()
    print(f'测试扫描：{len(ts_codes)}只')
    
    # 2. 初始化爬虫
    print('\n[2/4] 初始化东方财富F10爬虫...')
    crawler = EmwebF10Crawler()
    
    # 3. 挖掘信息
    print('\n[3/4] 开始挖掘基本面信息...')
    results = crawler.mine_pool(ts_codes, days=7)
    
    # 4. 评分排序
    print('\n[4/4] 评分排序...')
    ranked = crawler.score_and_rank(results)
    
    if len(ranked) > 0:
        # 保存结果
        output_csv = r'D:\mystock\solo\multi_factor_picker\output\basic_info_emweb_20260628.csv'
        ranked.to_csv(output_csv, index=False, encoding='utf-8-sig')
        
        print(f'\n结果已保存: {output_csv}')
        print(f'\nTOP10 重要信息：')
        print('-'*80)
        for i, (idx, row) in enumerate(ranked.head(10).iterrows(), 1):
            print(f'\n{i}. {row["ts_code"]} - 评分{row["weight"]}分')
            print(f'   最新日期: {row["date"]}')
            print(f'   信息类别: {", ".join(set(row["category"]))}')
            if len(row['title']) > 0:
                print(f'   标题: {row["title"][0][:50]}...')
        
        # 生成PDF报告
        print(f'\n生成PDF报告...')
        generate_pdf_report(ranked)
        
    else:
        print('\n未发现重要基本面信息')
        print('建议：')
        print('  1. 扩大时间范围（当前7天）')
        print('  2. 增加关键词')
        print('  3. 检查东方财富F10接口是否正常')

def generate_pdf_report(ranked_df):
    """生成PDF报告（简化版）"""
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
    pdf_path = r'D:\mystock\solo\multi_factor_picker\output\basic_info_emweb_20260628.pdf'
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
    
    story = []
    
    # 标题
    story.append(Paragraph('基本面信息挖掘报告（东方财富F10）', styles['CTitle']))
    story.append(Paragraph(datetime.now().strftime('%Y-%m-%d'), styles['CTitle']))
    story.append(Spacer(1, 0.5*cm))
    
    # 概览
    story.append(Paragraph('挖掘概览', styles['CH1']))
    story.append(Paragraph(f'• 数据源：东方财富F10（新闻+公告）', styles['CBody']))
    story.append(Paragraph(f'• 时间范围：最近7天', styles['CBody']))
    story.append(Paragraph(f'• 发现重要信息：{len(ranked_df)}条', styles['CBody']))
    story.append(Spacer(1, 0.5*cm))
    
    # TOP20表格
    story.append(Paragraph('TOP20 重要信息', styles['CH1']))
    
    table_data = [['排名', '代码', '评分', '类别', '最新日期']]
    for i, (idx, row) in enumerate(ranked_df.head(20).iterrows(), 1):
        categories = ', '.join(set(row['category']))
        table_data.append([
            str(i), row['ts_code'], f"{row['weight']}分", 
            categories[:10], str(row['date'])
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
    
    # 生成PDF
    doc.build(story)
    
    print(f'\nPDF报告已生成: {pdf_path}')
    
    return pdf_path

if __name__ == '__main__':
    import os
    main()
