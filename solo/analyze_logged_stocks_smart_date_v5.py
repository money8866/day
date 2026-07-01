"""
日志个股AI分析汇总 - 智能日期版本

功能：
1. 自动检测最新有日志的日期
2. 分析该日期的所有日志
3. 生成汇总报告（Markdown + PDF）
4. 推送微信

版本：v5（智能日期检测）
"""
import os
import json
from datetime import datetime, timedelta

NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def find_latest_log_date():
    """找到最新有日志的日期"""
    print('检测最新日志日期...')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        return None
    
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
    if not files:
        return None
    
    # 提取所有日期
    dates = set()
    for filename in files:
        try:
            date_str = filename.split('_')[-1].replace('.json', '')
            if len(date_str) == 8 and date_str.isdigit():
                dates.add(date_str)
        except:
            pass
    
    if not dates:
        return None
    
    # 返回最新日期
    latest_date = max(dates)
    print(f'最新日志日期: {latest_date}')
    return latest_date

def extract_stocks_by_date(target_date):
    """提取指定日期的日志"""
    print(f'提取 {target_date} 的日志...')
    
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json') and target_date in f]
    
    print(f'找到 {len(files)} 个日志文件')
    
    stocks = []
    for filename in files:
        try:
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
                'date': target_date,
                'score': score,
                'response': response,
                'theme': data.get('theme', '')
            })
        except Exception as e:
            print(f'处理失败 {filename}: {e}')
    
    print(f'提取到 {len(stocks)} 个股')
    
    # 按评分排序
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    return stocks

def generate_report(stocks, target_date):
    """生成报告"""
    if not stocks:
        return f'# 日志个股AI分析汇总 - {target_date}\n\n无数据'
    
    # 格式化日期
    date_formatted = f'{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}'
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    report = f"""# 日志个股AI分析汇总 - {date_formatted}

**生成时间**: {timestamp}

## 概览

本次汇总 {date_formatted} 的AI分析日志。

**数据来源**: `{NEWS_CACHE_DIR}\\ai_analysis_*.json`

**个股数量**: {len(stocks)} 只

---

## 个股列表（按评分排序）

"""
    
    for i, stock in enumerate(stocks, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        theme = stock.get('theme', '')
        response = stock.get('response', '')
        
        # 提取摘要
        summary = ''
        if response:
            summary = response.strip().split('\n')[0]
            if len(summary) > 120:
                summary = summary[:120] + '...'
        
        report += f"### {i}. {name} ({ts_code}) - 评分: {score}\n\n"
        if theme:
            report += f"- **主题**: {theme}\n"
        if summary:
            report += f"- **摘要**: {summary}\n"
        report += "\n"
    
    # 详细分析
    report += "\n---\n\n"
    report += "## 详细分析\n\n"
    
    for i, stock in enumerate(stocks, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        response = stock.get('response', '')
        
        report += f"### {i}. {name} ({ts_code}) - 评分: {score}\n\n"
        
        if response:
            report += "**AI分析:**\n\n"
            report += response + "\n\n"
        
        report += "---\n\n"
    
    return report

def main():
    print('=' * 70)
    print('日志个股AI分析汇总 - 智能日期版本')
    print('=' * 70)
    print()
    
    # 1. 找到最新日志日期
    target_date = find_latest_log_date()
    
    if not target_date:
        print('无日志文件')
        return
    
    print()
    print('=' * 70)
    print(f'分析日期: {target_date}')
    print('=' * 70)
    print()
    
    # 2. 提取该日期的个股
    stocks = extract_stocks_by_date(target_date)
    
    if not stocks:
        print('该日期无数据')
        return
    
    print()
    print('=' * 70)
    print(f'生成报告（{len(stocks)} 个股）...')
    print('=' * 70)
    print()
    
    # 3. 生成报告
    report = generate_report(stocks, target_date)
    
    # 4. 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 带时间戳版本
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_{target_date}_{timestamp}.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'报告已保存: {report_path}')
    
    # 最新版本（固定名称）
    latest_path = os.path.join(OUTPUT_DIR, f'logged_stocks_{target_date}_latest.md')
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'最新报告: {latest_path}')
    
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    
    return latest_path

if __name__ == '__main__':
    main()
