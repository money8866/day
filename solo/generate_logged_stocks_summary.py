"""生成日志个股分析摘要（Top 20）"""
import os
import json
from datetime import datetime, timedelta

BASE_DIR = r'D:\mystock'
NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_top_stocks(days=30, top_n=20):
    """提取评分最高的N只个股"""
    print(f'扫描最近 {days} 天的日志，提取Top {top_n}...')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        return []
    
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
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
                    'response': response,
                    'theme': data.get('theme', '')
                })
        except Exception as e:
            pass
    
    # 去重
    seen = {}
    for stock in stocks:
        code = stock['ts_code']
        if code not in seen or stock['date'] > seen[code]['date']:
            seen[code] = stock
    
    stocks = list(seen.values())
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    return stocks[:top_n]

def generate_summary_report(stocks):
    """生成摘要报告"""
    report = f"""# 日志个股AI分析摘要 - Top {len(stocks)}

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 概览

本次汇总最近30天AI分析日志中，情绪评分最高的 {len(stocks)} 只个股。

## Top {len(stocks)} 个股

"""
    
    for i, stock in enumerate(stocks, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        response = stock.get('response', '')
        theme = stock.get('theme', '')
        
        # 提取AI分析第一行（摘要）
        summary = ''
        if response:
            summary = response.strip().split('\n')[0]
            if len(summary) > 100:
                summary = summary[:100] + '...'
        
        report += f"{i}. **{name}** ({ts_code}) - 评分: **{score}**\n"
        if theme:
            report += f"   - 主题: {theme}\n"
        if summary:
            report += f"   - 摘要: {summary}\n"
        report += "\n"
    
    report += "\n## 详细说明\n\n"
    
    for i, stock in enumerate(stocks, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        response = stock.get('response', '')
        
        report += f"### {i}. {name} ({ts_code}) - 评分: {score}\n\n"
        
        if response:
            report += f"**AI分析:**\n\n{response}\n\n"
        
        report += "---\n\n"
    
    return report

def main():
    print('=' * 70)
    print('生成日志个股分析摘要（Top 20）')
    print('=' * 70)
    print()
    
    # 提取Top 20
    stocks = extract_top_stocks(days=30, top_n=20)
    
    if not stocks:
        print('无数据')
        return
    
    print(f'提取到 {len(stocks)} 个股')
    print()
    
    # 生成报告
    report = generate_summary_report(stocks)
    
    # 保存
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_top20_{timestamp}.md')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'报告已保存: {report_path}')
    print()
    
    # 也保存一个固定名称（方便推送）
    latest_path = os.path.join(OUTPUT_DIR, 'logged_stocks_top20_latest.md')
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'最新报告: {latest_path}')
    print()
    print('完成！')
    
    return report_path

if __name__ == '__main__':
    main()
