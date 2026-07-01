"""
获取个股最新公告 - 东方财富网接口（免费）
"""
import requests
import json
from datetime import datetime, timedelta
import time

def get_announcements_eastmoney(ts_code):
    """
    从东方财富网获取个股最新公告
    ts_code: 股票代码（如 600522.SH）
    return: 公告列表
    """
    # 转换代码格式：600522.SH -> 600522
    code = ts_code.split('.')[0]
    
    # 东方财富网公告API
    url = f'https://datacenter-web.eastmoney.com/api/data/v1/getData'
    
    params = {
        'reportName': 'RPT_DISCLOSURE_NEWEST',
        'columns': 'SECURITY_CODE,SECURITY_NAME_ABBR,ANNOUNCEMENT_TYPE,NOTICE_DATE,NOTICE_TITLE',
        'filter': f'(SECURITY_CODE="{code}")',
        'pageNumber': '1',
        'pageSize': '10',
        'sortTypes': '-NOTICE_DATE',
        'sortColumns': 'NOTICE_DATE',
        'source': 'WEB',
        'client': 'WEB'
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.encoding = 'utf-8'
        
        if response.status_code == 200:
            data = response.json()
            
            if data and 'data' in data and data['data']:
                announcements = []
                for item in data['data']['data']:
                    announcements.append({
                        'datetime': item.get('NOTICE_DATE', ''),
                        'type': item.get('ANNOUNCEMENT_TYPE', ''),
                        'title': item.get('NOTICE_TITLE', ''),
                        'code': item.get('SECURITY_CODE', ''),
                        'name': item.get('SECURITY_NAME_ABBR', '')
                    })
                
                return announcements
    except Exception as e:
        print(f'  东方财富网接口失败: {e}')
    
    return []

def filter_important_announcements(announcements):
    """筛选重要公告"""
    important_keywords = ['重组', '收购', '定增', '股权激励', '高送转', 
                          '业绩预告', '业绩快报', '年报', '季报', '停牌', '复牌', '重大事项',
                          '减持', '增持', '回购']
    
    important = []
    for ann in announcements:
        title = ann.get('title', '')
        for keyword in important_keywords:
            if keyword in title:
                important.append(ann)
                break
    
    return important if important else announcements[:5]  # 如无重要公告，返回前5条

def main():
    print('测试东方财富网公告接口...')
    print()
    
    # 测试中天科技(600522.SH)
    test_code = '600522.SH'
    print(f'测试股票: {test_code}')
    
    announcements = get_announcements_eastmoney(test_code)
    
    if announcements:
        print(f'✅ 获取成功！共 {len(announcements)} 条公告')
        print()
        print('前5条公告:')
        for i, ann in enumerate(announcements[:5], 1):
            print(f'{i}. {ann["datetime"][:10]} [{ann["type"]}] {ann["title"]}')
        
        print()
        print('筛选重要公告:')
        important = filter_important_announcements(announcements)
        for i, ann in enumerate(important[:5], 1):
            print(f'{i}. {ann["datetime"][:10]} [{ann["type"]}] {ann["title"]}')
    else:
        print('❌ 未获取到公告')
    
    print()
    print('测试完成')

if __name__ == '__main__':
    main()
