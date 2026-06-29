# -*- coding: utf-8 -*-
"""查询方正科技减持公告"""
import requests, re, json
from datetime import datetime, timedelta

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://www.cninfo.com.cn/',
})

params = {
    'searchkey': '600601',
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
print('方正科技(600601) 近期公告列表')
print('='*70)

# 搜索减持相关
jc_keywords = ['减持', '减仓']
# 其他重要公告
other_keywords = ['异动', '异常波动', '股东', '质押', '回购']
all_keywords = jc_keywords + other_keywords

for ann in anns:
    title = ann.get('announcementTitle', '')
    title_clean = re.sub(r'<[^>]+>', '', title)
    ann_time = ann.get('announcementTime', 0) / 1000
    ann_date = datetime.fromtimestamp(ann_time)
    
    if datetime.now() - timedelta(days=30) <= ann_date:
        if any(kw in title_clean for kw in all_keywords):
            aux = ann.get('adjunctUrl', '')
            date_str = ann_date.strftime('%Y-%m-%d')
            print(f'{date_str} | {title_clean[:80]}')
