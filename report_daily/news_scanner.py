# -*- coding: utf-8 -*-
"""获取final股票的最新公告和资讯"""
import os
import sys
import requests
import pandas as pd
import time
from datetime import datetime, timedelta
import re
from bs4 import BeautifulSoup

# 股票列表
STOCKS = [
    '002452', '002600', '002821', '002935', '002965', '003009',
    '300083', '300762', '300814', '301248',
    '603662', '605376', '688102', '688306', '688319', '688387'
]

def get_stock_news_cninfo(ts_code):
    """从巨潮资讯获取公告"""
    results = []
    
    # 提取数字代码
    code = ts_code.split('.')[0]
    
    # 判断交易所
    if code.startswith('6') or code.startswith('9'):
        exchange = 'szse_sh'  # 沪市
    elif code.startswith('00') or code.startswith('20'):
        exchange = 'szse_sz'  # 深市主板
    elif code.startswith('30'):
        exchange = 'szse_cy'  # 创业板
    else:
        exchange = 'szse_sz'
    
    try:
        # 巨潮资讯API
        url = 'http://www.cninfo.com.cn/new/hisAnnouncement/query'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        }
        
        data = {
            'stock': code,
            'tabName': 'fulltext',
            'pageSize': 10,
            'pageNum': 1,
            'column': exchange,
            'category': '',
            'plate': '',
            'seDate': '',
            'searchkey': '',
            'secid': '',
            'sortName': '',
            'sortType': '',
            'isHLtitle': 'true'
        }
        
        resp = requests.post(url, data=data, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            json_data = resp.json()
            announcements = json_data.get('announcements', [])
            
            for ann in announcements[:5]:  # 最近5条
                title = ann.get('announcementTitle', '')
                time_str = ann.get('announcementTime', '')
                
                # 转换时间戳
                if time_str:
                    pub_date = datetime.fromtimestamp(time_str / 1000).strftime('%Y-%m-%d')
                else:
                    pub_date = ''
                
                results.append({
                    'code': ts_code,
                    'title': title,
                    'date': pub_date,
                    'type': '公告'
                })
                
    except Exception as e:
        pass
    
    return results

def get_baostock_news(code):
    """使用baostock获取资讯（备用）"""
    try:
        import baostock as bs
        
        lg = bs.login()
        if lg.error_code != '0':
            return []
        
        # 获取近期新闻
        rs = bs.get_news_links(code=code, start_date='2026-06-20', end_date='2026-07-06')
        
        results = []
        while rs.error_code == '0' and rs.next():
            row = rs.get_row_data()
            if row:
                results.append({
                    'code': code,
                    'title': row[2] if len(row) > 2 else '',
                    'date': row[0] if len(row) > 0 else '',
                    'type': '新闻'
                })
        
        bs.logout()
        return results[:5]
        
    except Exception as e:
        return []

def analyze_sentiment(ann_list):
    """分析公告情绪"""
    positive_keywords = [
        '中标', '订单', '合同', '业绩预增', '净利润增长', '扭亏', '大幅增长',
        '回购', '增持', '战略合作', '新产品', '技术突破', '产能扩张',
        '获批', '认证', '专利', '行业领先', '市场份额'
    ]
    
    negative_keywords = [
        '减持', '业绩下滑', '亏损', '预警', '风险提示', '诉讼',
        '处罚', '监管', '调查', '问询函', '整改', '商誉减值',
        '终止', '取消', '违规', '资金占用'
    ]
    
    positive = []
    negative = []
    neutral = []
    
    for ann in ann_list:
        title = ann['title'].lower()
        
        is_positive = any(kw in title for kw in positive_keywords)
        is_negative = any(kw in title for kw in negative_keywords)
        
        if is_positive and not is_negative:
            positive.append(ann)
        elif is_negative:
            negative.append(ann)
        else:
            neutral.append(ann)
    
    return positive, negative, neutral

def main():
    print('=' * 70)
    print('获取Final股票最新公告和资讯')
    print('=' * 70)
    
    all_news = []
    
    for i, ts_code in enumerate(STOCKS):
        print('获取 %s ...' % ts_code, end=' ')
        
        news = get_stock_news_cninfo(ts_code)
        all_news.extend(news)
        
        print('%d条' % len(news))
        time.sleep(0.3)  # 避免请求过快
    
    print()
    print('=' * 70)
    print('共获取 %d 条公告' % len(all_news))
    print('=' * 70)
    
    # 按日期排序
    all_news.sort(key=lambda x: x['date'], reverse=True)
    
    # 按股票分组
    news_by_stock = {}
    for news in all_news:
        code = news['code']
        if code not in news_by_stock:
            news_by_stock[code] = []
        news_by_stock[code].append(news)
    
    # 生成报告
    report = []
    report.append('# 📢 Final股票最新公告分析')
    report.append('')
    report.append('**分析日期**: 2026-07-06')
    report.append('**监测股票**: %d只' % len(STOCKS))
    report.append('**获取公告**: %d条' % len(all_news))
    report.append('')
    
    # 统计
    all_titles = [n['title'] for n in all_news]
    positive_count = 0
    negative_count = 0
    
    for title in all_titles:
        title_lower = title.lower()
        if any(kw in title_lower for kw in ['中标', '订单', '合同', '业绩', '增长', '扭亏', '回购', '增持', '合作']):
            positive_count += 1
        if any(kw in title_lower for kw in ['减持', '亏损', '预警', '诉讼', '处罚', '调查']):
            negative_count += 1
    
    report.append('## 📊 情绪统计')
    report.append('')
    report.append('- 利好公告（业绩/订单/回购等）: **%d条**' % positive_count)
    report.append('- 利空公告（减持/亏损/调查等）: **%d条**' % negative_count)
    report.append('')
    
    # 重点关注
    report.append('## 🔥 重点公告')
    report.append('')
    
    # 利好
    if positive_count > 0:
        report.append('### 利好公告')
        report.append('')
        for news in all_news:
            title_lower = news['title'].lower()
            if any(kw in title_lower for kw in ['中标', '订单', '合同', '业绩', '增长', '扭亏', '回购', '增持', '合作']):
                report.append('- **%s** (%s) [%s]' % (
                    news['title'], news['code'], news['date']))
    
    # 利空
    if negative_count > 0:
        report.append('')
        report.append('### 利空公告')
        report.append('')
        for news in all_news:
            title_lower = news['title'].lower()
            if any(kw in title_lower for kw in ['减持', '亏损', '预警', '诉讼', '处罚', '调查']):
                report.append('- **%s** (%s) [%s]' % (
                    news['title'], news['code'], news['date']))
    
    # 完整列表
    report.append('')
    report.append('## 📋 完整公告列表')
    report.append('')
    
    for code in sorted(news_by_stock.keys()):
        reports = news_by_stock[code]
        report.append('### %s (%d条)' % (code, len(reports)))
        report.append('')
        for r in reports:
            report.append('- %s [%s]' % (r['title'], r['date']))
        report.append('')
    
    # 保存报告
    report_text = '\n'.join(report)
    
    output_file = r'D:\mystock\report_daily\announcement_analysis_20260706.md'
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print('报告已保存:', output_file)
    print()
    
    # 打印摘要
    print('=' * 70)
    print('摘要')
    print('=' * 70)
    print()
    print('利好公告: %d条' % positive_count)
    print('利空公告: %d条' % negative_count)
    print()
    
    # 打印重点
    print('重点利好:')
    for news in all_news:
        if any(kw in news['title'].lower() for kw in ['中标', '订单', '合同', '业绩', '增长', '扭亏', '回购', '增持', '合作']):
            print('  ✓ %s (%s) [%s]' % (news['title'][:40], news['code'], news['date']))
    
    print()
    print('重点利空:')
    for news in all_news:
        if any(kw in news['title'].lower() for kw in ['减持', '亏损', '预警', '诉讼', '处罚', '调查']):
            print('  ✗ %s (%s) [%s]' % (news['title'][:40], news['code'], news['date']))
    
    return report_text

if __name__ == '__main__':
    main()
