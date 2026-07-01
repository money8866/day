"""
每日分析日志中提到个股的最新公告和资讯 - 修复版
"""
import os
import json
import re
from datetime import datetime, timedelta

BASE_DIR = r'D:\mystock'
NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'

def get_tushare_pro():
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()

def extract_stocks_from_logs(days=7):
    """从日志中提取个股"""
    print(f'扫描最近 {days} 天的日志...')
    print(f'目录: {NEWS_CACHE_DIR}')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        print('错误: 目录不存在!')
        return []
    
    # 获取所有日志文件
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
    print(f'找到 {len(files)} 个日志文件')
    
    # 过滤最近N天的
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    stocks = []
    for filename in files:
        try:
            # 提取日期
            date_str = filename.split('_')[-1].replace('.json', '')
            
            if date_str >= cutoff_date:
                # 读取文件
                filepath = os.path.join(NEWS_CACHE_DIR, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                code = data.get('code', '')
                name = data.get('name', '')
                response = data.get('response', '')
                
                # 提取评分（最后一行）
                score = 50  # 默认
                if response:
                    lines = response.strip().split('\n')
                    # 从最后一行提取数字
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
            print(f'  处理 {filename} 失败: {e}')
    
    print(f'提取到 {len(stocks)} 个股')
    
    # 去重（保留最新日期）
    seen = {}
    for stock in stocks:
        code = stock['ts_code']
        if code not in seen or stock['date'] > seen[code]['date']:
            seen[code] = stock
    
    stocks = list(seen.values())
    print(f'去重后: {len(stocks)} 个股')
    
    # 按评分排序
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    return stocks

def get_announcements(pro, ts_code):
    """获取最新公告（最近30天）"""
    try:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')
        
        df = pro.anns(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and len(df) > 0:
            # 筛选重要公告
            important_keywords = ['重组', '收购', '定增', '股权激励', '高送转', 
                                  '业绩预告', '业绩快报', '年报', '季报', '停牌', '复牌', '重大事项']
            
            important = df[df['title'].str.contains('|'.join(important_keywords), na=False, regex=True)]
            
            if len(important) > 0:
                return important.head(10).to_dict('records')
            else:
                return df.head(5).to_dict('records')
    except Exception as e:
        print(f'  获取公告失败: {e}')
    
    return []

def get_news(pro, ts_code):
    """获取最新新闻（最近7天）"""
    try:
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')
        
        df = pro.news(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and len(df) > 0:
            return df.head(10).to_dict('records')
    except Exception as e:
        print(f'  获取新闻失败: {e}')
    
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
        news = stock.get('news', [])
        
        report += f"### {i}. {name} ({ts_code}) - 情绪评分: {score}\n\n"
        
        # AI分析摘要
        response = stock.get('response', '')
        if response:
            # 取第一行作为摘要
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
        
        # 新闻
        if news:
            report += "**最新资讯:**\n\n"
            for n in news[:5]:
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
        announcements = get_announcements(pro, ts_code)
        
        # 获取新闻
        news = get_news(pro, ts_code)
        
        stocks_with_info.append({
            'ts_code': ts_code,
            'name': name,
            'score': stock['score'],
            'response': stock.get('response', ''),
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
