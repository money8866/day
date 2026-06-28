# -*- coding: utf-8 -*-
"""
基本面信息挖掘系统 v1.1 - 网页爬虫版
数据源：巨潮资讯网（cninfo.com.cn）
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
import re

print('='*80)
print('基本面信息挖掘系统 v1.1 - 网页爬虫版')
print('='*80)

class CninfoCrawler:
    """巨潮资讯网爬虫"""
    
    BASE_URL = 'http://www.cninfo.com.cn/new/disclosure'
    
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
            'Referer': 'http://www.cninfo.com.cn/',
        })
        
    def convert_code(self, ts_code):
        """转换代码格式"""
        # 转换为字符串
        if isinstance(ts_code, int):
            ts_code = f"{ts_code:06d}"
        
        # ts_code格式：000001.SZ 或 600000.SH
        if isinstance(ts_code, str):
            if ts_code.endswith('.SZ'):
                return ts_code.replace('.SZ', '')
            elif ts_code.endswith('.SH'):
                return ts_code.replace('.SH', '')
            else:
                # 纯代码，判断交易所
                if ts_code.startswith('6'):
                    return ts_code  # 上海
                else:
                    return ts_code  # 深圳
        
        return str(ts_code)
    
    def get_announcements(self, ts_code, days=30):
        """获取公告列表"""
        stock_code = self.convert_code(ts_code)
        
        # 巨潮资讯网API
        url = 'http://www.cninfo.com.cn/new/hisAnnouncement/query'
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        params = {
            'stock': stock_code,
            'tabName': 'fulltext',
            'pageSize': 50,
            'pageNum': 1,
            'startDate': start_date,
            'endDate': end_date,
        }
        
        try:
            resp = self.session.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                
                if data and 'announcements' in data:
                    return data['announcements']
            
        except Exception as e:
            pass
        
        return []
    
    def analyze_ann(self, ts_code, ann_title, ann_date):
        """分析单条公告"""
        results = []
        
        for category, config in self.KEYWORDS.items():
            keywords = config['keywords']
            weight = config['weight']
            desc = config['description']
            
            if any(kw in ann_title for kw in keywords):
                results.append({
                    'ts_code': ts_code,
                    'title': ann_title,
                    'category': category,
                    'description': desc,
                    'weight': weight,
                    'ann_date': ann_date,
                })
        
        return results
    
    def mine_pool(self, ts_codes, days=30):
        """挖掘股票池的公告"""
        all_results = []
        all_anns = []  # 调试：保存所有公告
        
        print(f'\n开始挖掘 {len(ts_codes)} 只股票的公告信息...')
        print(f'时间范围：最近 {days} 天')
        print('-'*80)
        
        for i, ts_code in enumerate(ts_codes, 1):
            if i % 10 == 0:
                print(f'进度: {i}/{len(ts_codes)} ({i/len(ts_codes)*100:.1f}%)')
            
            # 获取公告
            anns = self.get_announcements(ts_code, days)
            
            if anns:
                # 调试：保存所有公告标题
                for ann in anns:
                    all_anns.append({
                        'ts_code': ts_code,
                        'title': ann.get('announcementTitle', ''),
                        'date': ann.get('announcementTime', ''),
                    })
                
                # 分析每条公告
                for ann in anns:
                    ann_title = ann.get('announcementTitle', '')
                    ann_date = ann.get('announcementTime', '')
                    
                    # 转换日期格式
                    if ann_date:
                        try:
                            ann_date = datetime.fromtimestamp(ann_date / 1000).strftime('%Y%m%d')
                        except:
                            ann_date = ''
                    
                    results = self.analyze_ann(ts_code, ann_title, ann_date)
                    
                    if results:
                        all_results.extend(results)
            
            # 限速
            time.sleep(0.2)
        
        # 调试：保存所有公告
        if all_anns:
            debug_df = pd.DataFrame(all_anns)
            debug_df.to_csv(r'D:\mystock\solo\multi_factor_picker\output\debug_all_anns.csv', index=False, encoding='utf-8-sig')
            print(f'\n调试：所有公告已保存至 debug_all_anns.csv ({len(all_anns)}条)')
        
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
    
    # 提取股票代码（前50只测试）
    ts_codes = qualified_pool['ts_code'].head(50).tolist()
    print(f'测试扫描：{len(ts_codes)}只')
    
    # 2. 初始化爬虫
    print('\n[2/4] 初始化巨潮资讯网爬虫...')
    crawler = CninfoCrawler()
    
    # 3. 挖掘公告信息
    print('\n[3/4] 开始挖掘公告信息...')
    results = crawler.mine_pool(ts_codes, days=7)
    
    # 4. 评分排序
    print('\n[4/4] 评分排序...')
    ranked = crawler.score_and_rank(results)
    
    if len(ranked) > 0:
        # 保存结果
        output_csv = r'D:\mystock\solo\multi_factor_picker\output\basic_info_cninfo_20260628.csv'
        ranked.to_csv(output_csv, index=False, encoding='utf-8-sig')
        
        print(f'\n结果已保存: {output_csv}')
        print(f'\nTOP10 重要信息：')
        print('-'*80)
        for i, (idx, row) in enumerate(ranked.head(10).iterrows(), 1):
            print(f'\n{i}. {row["ts_code"]} - 评分{row["weight"]}分')
            print(f'   最新公告: {row["ann_date"]}')
            print(f'   信息类别: {", ".join(set(row["category"]))}')
            if len(row['title']) > 0:
                print(f'   公告标题: {row["title"][0][:50]}...')
        
    else:
        print('\n未发现重要基本面信息')
        print('建议：')
        print('  1. 扩大时间范围（当前7天）')
        print('  2. 增加关键词')
        print('  3. 检查爬虫是否正常访问网站')

if __name__ == '__main__':
    main()
