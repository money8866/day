# -*- coding: utf-8 -*-
"""使用东方财富接口获取公告和资讯"""
import requests
import pandas as pd
import time
from datetime import datetime
import re

# 股票列表
STOCKS = [
    ('002452', 'SZ'), ('002600', 'SZ'), ('002821', 'SZ'), ('002935', 'SZ'), 
    ('002965', 'SZ'), ('003009', 'SZ'), ('300083', 'SZ'), ('300762', 'SZ'), 
    ('300814', 'SZ'), ('301248', 'SZ'), ('603662', 'SH'), ('605376', 'SH'), 
    ('688102', 'SH'), ('688306', 'SH'), ('688319', 'SH'), ('688387', 'SH')
]

def get_em_announcements(code, market):
    """东方财富公告接口"""
    results = []
    
    # 转换market: 沪市=1, 深市=0
    em_market = 1 if market == 'SH' else 0
    
    try:
        url = 'http://np-anotice-stock.eastmoney.com/api/security/ann'
        
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'http://data.eastmoney.com/'
        }
        
        params = {
            'sr': '-1',
            'page_size': '10',
            'page_index': '1',
            'ann_type': 'A',
            'client_source': 'web',
            'stock_list': '%s,%s' % (code, em_market),
            'f_node': '0',
            's_node': '0'
        }
        
        # 简化请求
        url = 'http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list=%s,%s' % (code, em_market)
        
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            
            if data.get('data'):
                notices = data['data'].get('list', [])
                
                for notice in notices[:8]:
                    results.append({
                        'code': '%s.%s' % (code, market),
                        'title': notice.get('title', ''),
                        'date': notice.get('notice_date', '')[:10] if notice.get('notice_date') else '',
                        'type': notice.get('art_type_str', '公告')
                    })
                    
    except Exception as e:
        pass
    
    return results

def get_sina_news(code):
    """新浪财经资讯"""
    results = []
    
    try:
        # 新浪财经个股新闻
        url = 'http://vip.stock.finance.sina.com.cn/q/go.php/vNewsBulletinINF/Kind/All/search.phtml?symbol=%s' % code
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            resp.encoding = 'gbk'
            content = resp.text
            
            # 解析新闻列表
            pattern = r'<td[^>]*><a[^>]*>([^<]+)</a></td>\s*<td[^>]*>([^<]+)</td>'
            matches = re.findall(pattern, content)
            
            for title, date in matches[:5]:
                results.append({
                    'code': code,
                    'title': title.strip(),
                    'date': date.strip(),
                    'type': '新闻'
                })
                
    except Exception as e:
        pass
    
    return results

def get_juchao_news():
    """巨潮资讯新闻接口"""
    results = []
    
    try:
        # 巨潮资讯新闻
        url = 'http://www.cninfo.com.cn/new/commonUrl/pageOfSearch'
        
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'searchkey': '',
            'plate': '',
            'stockcode': '',
            'startTime': '2026-06-01',
            'endTime': '2026-07-06',
            'category': 'category_ndbg_szsh',
            'pageNum': 1,
            'pageSize': 20
        }
        
        resp = requests.post(url, data=data, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            json_data = resp.json()
            notices = json_data.get('announcements', [])
            
            for notice in notices:
                results.append({
                    'title': notice.get('announcementTitle', ''),
                    'date': notice.get('announcementTime', ''),
                    'code': notice.get('secCode', '')
                })
                
    except Exception as e:
        pass
    
    return results

def search_stock_news_baidu(code, name=''):
    """百度搜索新闻（模拟）"""
    results = []
    
    # 判断交易所
    if code.startswith('6') or code.startswith('9'):
        exchange = 'SH'
    else:
        exchange = 'SZ'
    
    full_code = '%s.%s' % (code, exchange)
    
    try:
        # 使用东方财富快讯接口
        url = 'https://np-listapi.eastmoney.com.comm.im/F9-2ZGYZ/token=J9YIQfM2/token=1/token=1?client=web&bPageSize=10&bPage=1&dtype=4&keyword=%s&orderby=1&order=desc&token=J9YIQfM2' % code
        
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://guba.eastmoney.com/'
        }
        
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            
            items = data.get('data', {}).get('list', [])
            
            for item in items[:5]:
                results.append({
                    'code': full_code,
                    'title': item.get('title', ''),
                    'date': item.get('ShowTime', '')[:10] if item.get('ShowTime') else '',
                    'type': '资讯'
                })
                
    except Exception as e:
        pass
    
    return results

def main():
    print('=' * 70)
    print('获取Final股票最新公告和资讯（东方财富接口）')
    print('=' * 70)
    
    all_news = []
    
    for code, market in STOCKS:
        print('获取 %s.%s ...' % (code, market), end=' ')
        
        news = get_em_announcements(code, market)
        all_news.extend(news)
        
        print('%d条' % len(news))
        time.sleep(0.2)
    
    print()
    print('=' * 70)
    print('共获取 %d 条公告' % len(all_news))
    print('=' * 70)
    
    # 情感分析
    positive_keywords = [
        '中标', '订单', '合同', '业绩预增', '净利润增长', '扭亏', '大幅增长',
        '回购', '增持', '战略合作', '新产品', '技术突破', '产能扩张',
        '获批', '认证', '专利', '行业领先', '突破', '签约', '合作'
    ]
    
    negative_keywords = [
        '减持', '业绩下滑', '亏损', '预警', '风险提示', '诉讼',
        '处罚', '监管', '调查', '问询函', '整改', '商誉减值',
        '终止', '取消', '违规', '资金占用', 'ST', '*ST', '退市'
    ]
    
    positive = []
    negative = []
    neutral = []
    
    for news in all_news:
        title = news['title'].lower()
        
        is_pos = any(kw in title for kw in positive_keywords)
        is_neg = any(kw in title for kw in negative_keywords)
        
        if is_pos and not is_neg:
            positive.append(news)
        elif is_neg:
            negative.append(news)
        else:
            neutral.append(news)
    
    # 生成报告
    report = []
    report.append('# 📢 Final股票最新公告与资讯分析')
    report.append('')
    report.append('**分析日期**: 2026-07-06')
    report.append('**监测股票**: %d只' % len(STOCKS))
    report.append('**获取公告**: %d条' % len(all_news))
    report.append('')
    
    report.append('## 📊 情绪统计')
    report.append('')
    report.append('- 利好公告: **%d条** 📈' % len(positive))
    report.append('- 利空公告: **%d条** 📉' % len(negative))
    report.append('- 中性公告: **%d条**' % len(neutral))
    report.append('')
    
    # 利好
    if positive:
        report.append('## 📈 利好公告')
        report.append('')
        for news in positive[:15]:
            report.append('- **[%s] %s** (%s)' % (news['date'], news['title'][:50], news['code']))
        report.append('')
    
    # 利空
    if negative:
        report.append('## 📉 利空公告')
        report.append('')
        for news in negative[:10]:
            report.append('- **[%s] %s** (%s)' % (news['date'], news['title'][:50], news['code']))
        report.append('')
    
    # 完整列表
    if neutral:
        report.append('## 📋 其他重要公告')
        report.append('')
        for news in neutral[:20]:
            report.append('- [%s] %s (%s)' % (news['date'], news['title'][:50], news['code']))
        report.append('')
    
    # 保存
    report_text = '\n'.join(report)
    
    today_str = datetime.now().strftime('%Y%m%d')
    output_file = r'D:\mystock\report_daily\announcement_analysis_%s.md' % today_str
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print()
    print('报告已保存:', output_file)
    
    # 打印摘要
    print()
    print('=' * 70)
    print('摘要')
    print('=' * 70)
    print('利好: %d条' % len(positive))
    print('利空: %d条' % len(negative))
    print()
    
    print('利好公告:')
    for n in positive[:10]:
        print('  ✓ %s [%s]' % (n['title'][:45], n['code']))
    
    print()
    print('利空公告:')
    for n in negative[:10]:
        print('  ✗ %s [%s]' % (n['title'][:45], n['code']))
    
    return report_text

if __name__ == '__main__':
    main()
