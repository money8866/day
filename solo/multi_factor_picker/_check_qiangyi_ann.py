# -*- coding: utf-8 -*-
"""查询强一股份对外投资公告详情"""
import requests, re, json
from datetime import datetime, timedelta

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://www.cninfo.com.cn/',
})

# 搜索强一股份（688809）最近公告
params = {
    'searchkey': '688809',
    'sdate': '', 'edate': '',
    'isfulltext': 'false',
    'sortName': 'nothing',
    'sortType': 'desc',
    'pageNum': 1,
}

resp = session.post('http://www.cninfo.com.cn/new/fulltextSearch/full', data=params, timeout=15)
data = resp.json()
anns = data.get('announcements', [])

print('='*70)
print('强一股份(688809) 近期公告列表')
print('='*70)

for ann in anns:
    title = ann.get('announcementTitle', '')
    title_clean = re.sub(r'<[^>]+>', '', title)
    ann_time = ann.get('announcementTime', 0) / 1000
    ann_date = datetime.fromtimestamp(ann_time)
    
    if datetime.now() - timedelta(days=30) <= ann_date:
        aux = ann.get('adjunctUrl', '')
        date_str = ann_date.strftime('%Y-%m-%d')
        print(f'{date_str} | {title_clean}')
        print(f'  链接: http://www.cninfo.com.cn{aux}')
        print()
