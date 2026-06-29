# -*- coding: utf-8 -*-
"""
基本面信息挖掘 - 巨潮网网页版
功能：自动挖掘利好+利空信息
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import requests
from datetime import datetime, timedelta
import time
import re

print('='*80)
print('基本面信息挖掘 v2 - 利好+利空监测')
print('='*80)

class JuchaoWebCrawler:
    """巨潮资讯网网页爬虫"""

    # 利好消息关键词
    POSITIVE_KEYWORDS = {
        '新订单': {'keywords': ['中标', '合同', '订单', '供货', '签约', '意向书', '中标通知书'], 'weight': 10},
        '新产品': {'keywords': ['新产品', '发布', '量产', '下线', '上市', '推出', '研制成功'], 'weight': 8},
        '新项目': {'keywords': ['投资', '项目', '建设', '扩产', '开工', '投产', '增资'], 'weight': 7},
        '技术突破': {'keywords': ['专利', '认证', '突破', '研发', '通过认证'], 'weight': 6},
        '重大合同': {'keywords': ['重大合同', '重大订单', '战略合作'], 'weight': 9},
    }

    # 利空消息关键词
    NEGATIVE_KEYWORDS = {
        '股东减持': {'keywords': ['减持', '减仓', '股权转让', '大宗交易', '减持计划'], 'weight': -8},
        '诉讼纠纷': {'keywords': ['诉讼', '仲裁', '纠纷', '起诉', '被起诉', '上诉'], 'weight': -7},
        '监管处罚': {'keywords': ['处罚', '警告', '罚款', '立案', '调查', '监管', '问询函', '关注函'], 'weight': -9},
        '业绩亏损': {'keywords': ['亏损', '下滑', '下降', '预警', '预亏', '业绩预告亏损'], 'weight': -8},
        '资产减值': {'keywords': ['减值', '计提', '坏账', '跌价', '损失'], 'weight': -7},
        '债务问题': {'keywords': ['债务', '违约', '逾期', '欠款', '担保风险'], 'weight': -9},
        '人事变动': {'keywords': ['辞职', '离职', '免职', '罢免', '董事长辞职'], 'weight': -5},
        '监管问询': {'keywords': ['问询', '关注函', '监管函', '警示函', '通报批评'], 'weight': -6},
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'http://www.cninfo.com.cn/',
        })

    def get_announcements(self, ts_code, days=7):
        """获取公告列表"""
        stock_code = re.sub(r'\..*', '', str(ts_code)).zfill(6)

        params = {
            'searchkey': stock_code,
            'sdate': '', 'edate': '',
            'isfulltext': 'false',
            'sortName': 'nothing',
            'sortType': 'desc',
            'pageNum': 1,
        }

        results = []
        try:
            resp = self.session.post(
                'http://www.cninfo.com.cn/new/fulltextSearch/full',
                data=params, timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                anns = data.get('announcements')
                if anns:
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=days)
                    for ann in anns:
                        ann_time = ann.get('announcementTime', 0) / 1000
                        ann_date = datetime.fromtimestamp(ann_time)
                        if start_date <= ann_date <= end_date:
                            results.append({
                                'ts_code': ts_code,
                                'title': ann.get('announcementTitle', ''),
                                'ann_date': ann_date.strftime('%Y%m%d'),
                            })
        except:
            pass
        return results

    def analyze_title(self, title):
        """分析标题：同时检测利好和利空"""
        clean = re.sub(r'<[^>]+>', '', title)
        results = []

        # 检测利好
        for cat, cfg in self.POSITIVE_KEYWORDS.items():
            if any(kw in clean for kw in cfg['keywords']):
                results.append({'category': cat, 'weight': cfg['weight'], 'type': 'positive'})

        # 检测利空
        for cat, cfg in self.NEGATIVE_KEYWORDS.items():
            if any(kw in clean for kw in cfg['keywords']):
                results.append({'category': cat, 'weight': cfg['weight'], 'type': 'negative'})

        return results, clean

    def mine_pool(self, ts_codes, days=7):
        """挖掘股票池（利好+利空）"""
        all_results = []
        print(f'\n开始挖掘 {len(ts_codes)} 只股票...')
        print(f'时间范围：最近 {days} 天')

        for i, ts_code in enumerate(ts_codes, 1):
            if i % 10 == 0:
                print(f'  进度: {i}/{len(ts_codes)}')
            anns = self.get_announcements(ts_code, days)

            for ann in anns:
                matched, clean_title = self.analyze_title(ann['title'])
                for m in matched:
                    all_results.append({
                        'ts_code': ann['ts_code'],
                        'title': clean_title,
                        'ann_date': ann['ann_date'],
                        'category': m['category'],
                        'weight': m['weight'],
                        'type': m['type'],
                    })
            time.sleep(0.3)
        print(f'\n挖掘完成！共发现 {len(all_results)} 条信息')
        return all_results

    def score_and_rank(self, results):
        """分组评分排序"""
        if not results:
            return [], pd.DataFrame()
        df = pd.DataFrame(results)

        # 分开利好和利空
        positive = df[df['type'] == 'positive'].copy()
        negative = df[df['type'] == 'negative'].copy()

        # 利好分组
        pos_grouped = positive.groupby('ts_code').agg({
            'weight': 'sum', 'title': lambda x: list(x),
            'category': lambda x: list(x), 'ann_date': 'max',
        }).reset_index().sort_values('weight', ascending=False) if len(positive) > 0 else pd.DataFrame()

        # 利空分组
        neg_grouped = negative.groupby('ts_code').agg({
            'weight': 'sum', 'title': lambda x: list(x),
            'category': lambda x: list(x), 'ann_date': 'max',
        }).reset_index().sort_values('weight', ascending=True) if len(negative) > 0 else pd.DataFrame()

        return pos_grouped, neg_grouped

def main():
    df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\top50_tracking_list.csv')
    ts_codes = df['ts_code'].tolist()
    print(f'\n[1/4] 读取TOP50股票：{len(ts_codes)}只')

    crawler = JuchaoWebCrawler()
    print('\n[2/4] 初始化爬虫完成')

    print('\n[3/4] 挖掘公告信息...')
    results = crawler.mine_pool(ts_codes, days=7)

    print('\n[4/4] 评分排序...')
    pos_df, neg_df = crawler.score_and_rank(results)

    # 保存结果
    if len(pos_df) > 0:
        pos_df.to_csv(r'D:\mystock\solo\multi_factor_picker\output\auto_positive.csv',
                      index=False, encoding='utf-8-sig')
        print(f'\n利好消息：{len(pos_df)}条')
    else:
        print('\n利好消息：0条')

    if len(neg_df) > 0:
        neg_df.to_csv(r'D:\mystock\solo\multi_factor_picker\output\auto_negative.csv',
                      index=False, encoding='utf-8-sig')
        print(f'利空消息：{len(neg_df)}条')
    else:
        print('利空消息：0条')

    # 打印TOP
    if len(pos_df) > 0:
        print(f'\n利好TOP5：')
        for i, (idx, row) in enumerate(pos_df.head(5).iterrows(), 1):
            title = re.sub(r'<[^>]+>', '', str(row['title'][0]))[:40]
            print(f'  {i}. {row["ts_code"]} +{row["weight"]}分 - {title}...')

    if len(neg_df) > 0:
        print(f'\n利空TOP5：')
        for i, (idx, row) in enumerate(neg_df.head(5).iterrows(), 1):
            title = re.sub(r'<[^>]+>', '', str(row['title'][0]))[:40]
            print(f'  {i}. {row["ts_code"]} {row["weight"]}分 - {title}...')

if __name__ == '__main__':
    main()
