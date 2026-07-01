"""
日志个股AI分析汇总 - 简化版（处理所有日志）

功能：
1. 处理news_cache目录下所有ai_analysis_*.json日志
2. 提取评分并排序
3. 生成Top20汇总报告
4. 保存为Markdown

版本：v3（简化版，无日期过滤）
"""
import os
import json
from datetime import datetime

NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_all_stocks():
    """提取所有日志中的个股（无日期过滤）"""
    print('扫描所有AI分析日志...')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        print('错误: 目录不存在!')
        return []
    
    # 获取所有日志文件
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
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
            
            # 提取评分（最后一行数字）
            score = 50
            if response:
                lines = response.strip().split('\n')
                for line in reversed(lines):
                    line = line.strip()
                    if line.isdigit() and 0 <= int(line) <= 100:
                        score = int(line)
                        break
            
            # 提取日期
            date_str = filename.split('_')[-1].replace('.json', '')
            
            stocks.append({
                'ts_code': code,
                'name': name,
                'date': date_str,
                'score': score,
                'response': response,
                'theme': data.get('theme', '')
            })
        except Exception as e:
            print(f'处理失败 {filename}: {e}')
    
    print(f'提取到 {len(stocks)} 个股')
    
    # 去重（保留最新）
    seen = {}
    for stock in stocks:
        code = stock['ts_code']
        if code not in seen or stock['date'] > seen[code]['date']:
            seen[code] = stock
    
    stocks = list(seen.values())
    
    # 按评分排序
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    print(f'去重后: {len(stocks)} 个股')
    return stocks

def generate_report(stocks, top_n=20):
    """生成报告"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    report = f"""# 日志个股AI分析汇总 - Top {top_n}

**生成时间**: {timestamp}

## 概览

本次汇总所有AI分析日志，按情绪评分排序，展示Top {top_n} 个股。

**数据来源**: `{NEWS_CACHE_DIR}\\ai_analysis_*.json`

**总计**: {len(stocks)} 只个股（去重后）

---

"""
    
    # Top N 列表
    report += f"## Top {top_n} 个股（按评分排序）\n\n"
    
    for i, stock in enumerate(stocks[:top_n], 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        theme = stock.get('theme', '')
        response = stock.get('response', '')
        
        # 提取摘要（第一行）
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
    
    for i, stock in enumerate(stocks[:top_n], 1):
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
    print('日志个股AI分析汇总 - 简化版')
    print('=' * 70)
    print()
    
    # 1. 提取所有个股
    stocks = extract_all_stocks()
    
    if not stocks:
        print('无数据')
        return
    
    print()
    print('=' * 70)
    print(f'生成Top 20报告...')
    print('=' * 70)
    print()
    
    # 2. 生成报告
    report = generate_report(stocks, top_n=20)
    
    # 3. 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 带时间戳版本
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_top20_{timestamp}.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'报告已保存: {report_path}')
    
    # 最新版本（固定名称）
    latest_path = os.path.join(OUTPUT_DIR, 'logged_stocks_top20_latest.md')
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
