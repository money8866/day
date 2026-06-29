# -*- coding: utf-8 -*-
"""获取方正科技减持公告详情"""
import requests, re, json
from datetime import datetime, timedelta
from urllib.parse import quote

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://www.cninfo.com.cn/',
})

# 先获取公告列表，找到减持结果公告的adjunctUrl
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

target_ann = None
for ann in anns:
    title = ann.get('announcementTitle', '')
    title_clean = re.sub(r'<[^>]+>', '', title)
    if '减持' in title_clean and '结果' in title_clean:
        target_ann = ann
        break

if target_ann:
    aux = target_ann.get('adjunctUrl', '')
    sec_code = target_ann.get('secCode', '600601')
    ann_time = target_ann.get('announcementTime', 0) / 1000
    ann_date = datetime.fromtimestamp(ann_time)
    
    print(f'公告标题: {re.sub(r"<[^>]+>", "", target_ann.get("announcementTitle", ""))}')
    print(f'公告日期: {ann_date.strftime("%Y-%m-%d")}')
    print(f'附件链接: http://www.cninfo.com.cn{aux}')
    print()
    
    # 尝试获取公告内容（PDF或HTML）
    if '.PDF' in aux.upper() or '.pdf' in aux:
        pdf_url = f'http://www.cninfo.com.cn{aux}'
        print(f'PDF链接: {pdf_url}')
        print()
        print('由于PDF格式限制，无法直接读取文本内容。')
        print('请通过以下链接查看完整公告：')
        print(f'http://www.cninfo.com.cn/new/disclosure/detail?stockCode={sec_code}&announcementId={target_ann.get("announcementId","")}')
    
    # 搜索更多详情
    print()
    print('='*70)
    print('已掌握的关键信息：')
    print('='*70)
    print()
    
    # 检查是否有权益变动公告
    for ann in anns:
        title = ann.get("announcementTitle", "")
        title_clean = re.sub(r"<[^>]+>", "", title)
        ann_time2 = ann.get("announcementTime", 0) / 1000
        ann_date2 = datetime.fromtimestamp(ann_time2)
        
        if "权益变动" in title_clean and ann_date2 >= datetime.now() - timedelta(days=30):
            msg1 = f"相关公告: {title_clean}"
            msg2 = f"日期: {ann_date2.strftime('%Y-%m-%d')}"
            aux_url = ann.get("adjunctUrl", "")
            msg3 = f"链接: http://www.cninfo.com.cn{aux_url}"
            print(msg1)
            print(msg2)
            print(msg3)

else:
    msg = "未找到方正科技股东减持股份结果公告"
    print(msg)
    
    # 列出所有近期公告
    print()
    print('近期所有公告：')
    for ann in anns:
        title = ann.get('announcementTitle', '')
        title_clean = re.sub(r'<[^>]+>', '', title)
        ann_time = ann.get('announcementTime', 0) / 1000
        ann_date = datetime.fromtimestamp(ann_time)
        if datetime.now() - timedelta(days=30) <= ann_date:
            print(f'  {ann_date.strftime("%m-%d")} {title_clean[:60]}')
