"""
每日分析日志中提到个股的最新公告和资讯 - 完善版

功能：
1. 分析最近N天AI分析日志中提到的个股
2. 通过Tushare API获取最新公告
3. 通过爬虫获取公告（Tushare失败时）
4. 生成报告并保存
"""
import os
import json
import time
import re
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = r'D:\mystock'
NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
CACHE_DIR = r'D:\mystock\cache_daily\announcements'
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Tushare token (从MEMORY.md获取)
TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

def get_tushare_pro():
    """获取Tushare Pro API"""
    try:
        import tushare as ts
        ts.set_token(TUSHARE_TOKEN)
        return ts.pro_api()
    except Exception as e:
        print(f'Tushare初始化失败: {e}')
        return None

def extract_stocks_from_logs(days=7):
    """从日志中提取个股"""
    print(f'扫描最近 {days} 天的日志...')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        print('错误: 目录不存在!')
        return []
    
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
    print(f'找到 {len(files)} 个日志文件')
    
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    stocks = []
    for filename in files:
        try:
            date_str = filename.split('_')[-1].replace('.json', '')
            
            if date_str >= cutoff_date:
                filepath = os.path.join(NEWS_CACHE_DIR, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                code = data.get('code', '')
                name = data.get('name', '')
                response = data.get('response', '')
                
                # 提取评分
                score = 50
                if response:
                    lines = response.strip().split('\n')
                    for line in reversed(lines):
                        line = line.strip()
                        if line.isdigit() and 0 <= int(line) <= 100:
                            score = int(line)
                            break
                
                stocks.append({
                    'ts_code': code,
                    'name': name,
                    'date': date_str,
                    'score': score,
                    'response': response
                })
        except Exception as e:
            print(f'处理失败: {e}')
    
    print(f'提取到 {len(stocks)} 个股')
    
    # 去重
    seen = {}
    for stock in stocks:
        code = stock['ts_code']
        if code not in seen or stock['date'] > seen[code]['date']:
            seen[code] = stock
    
    stocks = list(seen.values())
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    print(f'去重后: {len(stocks)} 个股')
    return stocks

def load_cache(code):
    """加载缓存"""
    cache_file = os.path.join(CACHE_DIR, f'{code}.json')
    if os.path.exists(cache_file):
        # 检查缓存是否过期（1天）
        mtime = os.path.getmtime(cache_file)
        if time.time() - mtime < 86400:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    return None

def save_cache(code, data):
    """保存缓存"""
    cache_file = os.path.join(CACHE_DIR, f'{code}.json')
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_announcements_tushare(pro, ts_code):
    """通过Tushare获取公告"""
    try:
        # 检查缓存
        cached = load_cache(ts_code)
        if cached:
            print(f'  从缓存加载: {ts_code}')
            return cached
        
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')
        
        df = pro.anns(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and len(df) > 0:
            # 筛选重要公告
            important_keywords = ['重组', '收购', '定增', '股权激励', '高送转', 
                                  '业绩预告', '业绩快报', '年报', '季报', '停牌', '复牌', '重大事项']
            
            important = df[df['title'].str.contains('|'.join(important_keywords), na=False, regex=True)]
            
            result = []
            if len(important) > 0:
                result = important.head(10).to_dict('records')
            else:
                result = df.head(5).to_dict('records')
            
            # 保存缓存
            save_cache(ts_code, result)
            
            return result
    except Exception as e:
        print(f'  Tushare获取失败: {e}')
    
    return []

def get_announcements_web(ts_code):
    """通过爬虫获取公告（备用方案）"""
    # TODO: 实现东财/同花顺公告爬虫
    print(f'  爬虫获取公告: {ts_code} (未实现)')
    return []

def get_announcements(ts_code):
    """获取公告（混合方案）"""
    # 1. 尝试Tushare
    pro = get_tushare_pro()
    if pro:
        announcements = get_announcements_tushare(pro, ts_code)
        if announcements:
            return announcements
    
    # 2. 备用：爬虫
    announcements = get_announcements_web(ts_code)
    if announcements:
        return announcements
    
    # 3. 都失败：返回空
    return []

def generate_report(stocks_with_info):
    """生成报告"""
    report = f"""# 日志个股最新公告与资讯分析

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 概览

本次分析 {len(stocks_with_info)} 只个股，均为近期 AI 分析日志中提到的股票。

按情绪评分从高到低排序。

## 个股详情

"""
    
    for i, stock in enumerate(stocks_with_info, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        announcements = stock.get('announcements', [])
        response = stock.get('response', '')
        
        report += f"### {i}. {name} ({ts_code}) - 情绪评分: {score}\n\n"
        
        # AI分析摘要
        if response:
            summary = response.strip().split('\n')[0]
            report += f"**AI分析摘要:** {summary}\n\n"
        
        # 公告
        if announcements:
            report += "**最新公告:**\n\n"
            for ann in announcements[:5]:
                report += f"- {ann.get('datetime', '')[:10]} [{ann.get('type', '')}] {ann.get('title', '')}\n"
            report += "\n"
        else:
            report += "**最新公告:** 无重要公告\n\n"
        
        report += "---\n\n"
    
    return report

def main():
    print('=' * 70)
    print('每日分析日志中提到个股的最新公告和资讯')
    print('=' * 70)
    print()
    
    # 1. 提取个股
    stocks = extract_stocks_from_logs(days=7)
    
    if not stocks:
        print('无数据，退出')
        return
    
    print()
    print('=' * 70)
    print(f'将分析 {len(stocks)} 只个股')
    print('=' * 70)
    print()
    
    # 显示前10只
    print('Top 10 个股:')
    for i, stock in enumerate(stocks[:10], 1):
        print(f'  {i}. {stock["name"]} ({stock["ts_code"]}) - 评分: {stock["score"]}')
    print()
    
    # 2. 获取每只个股的最新公告
    stocks_with_info = []
    
    for i, stock in enumerate(stocks):
        ts_code = stock['ts_code']
        name = stock['name']
        
        print(f'[{i+1}/{len(stocks)}] 处理 {name} ({ts_code})...')
        
        # 获取公告
        announcements = get_announcements(ts_code)
        
        stocks_with_info.append({
            'ts_code': ts_code,
            'name': name,
            'score': stock['score'],
            'response': stock.get('response', ''),
            'announcements': announcements
        })
        
        # 限频
        time.sleep(0.1)
    
    # 3. 生成报告
    print()
    print('=' * 70)
    print('生成报告...')
    print('=' * 70)
    
    report = generate_report(stocks_with_info)
    
    # 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_with_ann_{timestamp}.md')
    
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
