"""
每日分析日志中提到个股的最新公告和资讯
功能：
1. 读取 tushare_quant.py 生成的 AI 分析日志（news_cache 目录）
2. 提取最近N天提到的个股代码
3. 获取这些个股的最新公告和资讯
4. 生成分析报告并推送
"""
import os
import json
import pandas as pd
from datetime import datetime, timedelta
import sqlite3

# 配置
BASE_DIR = r'D:\mystock'

# 尝试多个可能的路径
NEWS_CACHE_DIR = None
possible_dirs = [
    os.path.join(BASE_DIR, 'news_cache'),
    os.path.join(BASE_DIR, 'solo', 'news_cache'),
    os.path.join(BASE_DIR, 'data', 'news_cache')
]
for d in possible_dirs:
    if os.path.exists(d):
        NEWS_CACHE_DIR = d
        break

if NEWS_CACHE_DIR is None:
    # 默认使用第一个
    NEWS_CACHE_DIR = possible_dirs[0]

print(f'使用日志目录: {NEWS_CACHE_DIR}')

OUTPUT_DIR = os.path.join(BASE_DIR, 'solo', 'trend_feature_output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Tushare token
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'

def get_tushare_pro():
    """获取 Tushare Pro API"""
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()

def get_recent_logged_stocks(days=3):
    """
    获取最近N天日志中提到的个股
    
    Returns:
        list: [(ts_code, name, trade_date, score), ...]
    """
    print(f'扫描最近 {days} 天的日志文件...')
    print(f'日志目录: {NEWS_CACHE_DIR}')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        print(f'错误: 日志目录不存在!')
        return []
    
    stocks = []
    today = datetime.now()
    
    # 列出所有日志文件
    all_files = [f for f in os.listdir(NEWS_CACHE_DIR) if f.startswith('ai_analysis_') and f.endswith('.json')]
    print(f'找到 {len(all_files)} 个日志文件')
    
    for i in range(days):
        date = (today - timedelta(days=i)).strftime('%Y%m%d')
        print(f'  检查日期: {date}')
        
        # 查找该日期的所有日志文件
        count = 0
        for filename in all_files:
            if date in filename and filename.endswith('.json'):
                count += 1
                filepath = os.path.join(NEWS_CACHE_DIR, filename)
                
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                    code = data.get('code', '')
                    name = data.get('name', '')
                    trade_date = data.get('trade_date', date)
                    response = data.get('response', '')
                    
                    # 提取情绪评分
                    import re
                    found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', response)
                    if found:
                        score = int(found.group(1))
                    else:
                        # 从最后一行提取数字
                        lines = response.strip().split('\n')
                        score = 50
                        for line in reversed(lines):
                            nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                            if nums:
                                score = int(nums[-1])
                                break
                    
                    stocks.append({
                        'ts_code': code,
                        'name': name,
                        'trade_date': trade_date,
                        'score': score,
                        'log_date': date
                    })
                except Exception as e:
                    print(f'  读取 {filename} 失败: {e}')
    
    print(f'找到 {len(stocks)} 个股')
    return stocks

def get_latest_announcements(pro, ts_code, start_date=None):
    """
    获取个股最新公告
    
    Args:
        pro: Tushare Pro API
        ts_code: 股票代码
        start_date: 开始日期（默认30天前）
    
    Returns:
        list: 公告列表
    """
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
    
    try:
        # 获取公告
        df = pro.anns(ts_code=ts_code, start_date=start_date, 
                      end_date=datetime.now().strftime('%Y%m%d'))
        
        if df is not None and len(df) > 0:
            # 只保留重要公告
            important_keywords = ['重组', '收购', '定增', '股权激励', '高送转', 
                                  '业绩预告', '业绩快报', '年报', '季报', '停牌', '复牌']
            
            important = df[df['title'].str.contains('|'.join(important_keywords), na=False)]
            
            if len(important) > 0:
                return important.head(10).to_dict('records')
            else:
                return df.head(5).to_dict('records')
    except Exception as e:
        print(f'  获取 {ts_code} 公告失败: {e}')
    
    return []

def get_latest_news(pro, ts_code, start_date=None):
    """
    获取个股最新新闻/资讯
    
    Args:
        pro: Tushare Pro API
        ts_code: 股票代码
        start_date: 开始日期（默认7天前）
    
    Returns:
        list: 新闻列表
    """
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
    
    try:
        # 获取新闻资讯
        df = pro.news(ts_code=ts_code, start_date=start_date,
                     end_date=datetime.now().strftime('%Y%m%d'))
        
        if df is not None and len(df) > 0:
            return df.head(10).to_dict('records')
    except Exception as e:
        print(f'  获取 {ts_code} 新闻失败: {e}')
    
    return []

def generate_report(stocks_with_info):
    """
    生成分析报告
    
    Args:
        stocks_with_info: [{'ts_code', 'name', 'score', 'announcements', 'news'}, ...]
    
    Returns:
        str: Markdown 格式的报告
    """
    report = f"""# 日志个股最新公告与资讯分析

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 概览

本次分析 {len(stocks_with_info)} 只个股，均为近期 AI 分析日志中提到的股票。

## 个股详情

"""
    
    for i, stock in enumerate(stocks_with_info, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        announcements = stock.get('announcements', [])
        news = stock.get('news', [])
        
        report += f"### {i}. {name} ({ts_code}) - 情绪评分: {score}\n\n"
        
        # 公告
        if announcements:
            report += "**最新公告:**\n\n"
            for ann in announcements[:5]:  # 只显示前5条
                report += f"- {ann.get('datetime', '')[:10]} [{ann.get('type', '')}] {ann.get('title', '')}\n"
            report += "\n"
        else:
            report += "**最新公告:** 无\n\n"
        
        # 新闻
        if news:
            report += "**最新资讯:**\n\n"
            for n in news[:5]:  # 只显示前5条
                report += f"- {n.get('datetime', '')[:10]} {n.get('title', '')}\n"
            report += "\n"
        else:
            report += "**最新资讯:** 无\n\n"
        
        report += "---\n\n"
    
    return report

def main():
    print('=' * 70)
    print('每日分析日志中提到个股的最新公告和资讯')
    print('=' * 70)
    print()
    
    # 1. 获取最近3天日志中提到的个股
    stocks = get_recent_logged_stocks(days=3)
    
    if not stocks:
        print('未发现近期日志，退出')
        return
    
    # 按情绪评分排序
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    print()
    print('=' * 70)
    print(f'将分析 {len(stocks)} 只个股的最新公告和资讯')
    print('=' * 70)
    print()
    
    # 2. 获取 Tushare Pro API
    try:
        pro = get_tushare_pro()
        print('Tushare Pro API 连接成功')
    except Exception as e:
        print(f'Tushare Pro API 连接失败: {e}')
        return
    
    # 3. 获取每只个股的最新公告和资讯
    stocks_with_info = []
    
    for i, stock in enumerate(stocks):
        ts_code = stock['ts_code']
        name = stock['name']
        
        print(f'[{i+1}/{len(stocks)}] 处理 {name} ({ts_code})...')
        
        # 获取公告
        announcements = get_latest_announcements(pro, ts_code)
        
        # 获取新闻
        news = get_latest_news(pro, ts_code)
        
        stocks_with_info.append({
            'ts_code': ts_code,
            'name': name,
            'score': stock['score'],
            'announcements': announcements,
            'news': news
        })
        
        # 限频
        import time
        time.sleep(0.1)
    
    # 4. 生成报告
    print()
    print('=' * 70)
    print('生成报告...')
    print('=' * 70)
    
    report = generate_report(stocks_with_info)
    
    # 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_analysis_{timestamp}.md')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'报告已保存: {report_path}')
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    
    return report_path

if __name__ == '__main__':
    main()
