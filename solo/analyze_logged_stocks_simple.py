"""每日分析日志中提到个股 - 简化版（不依赖Tushare）"""
import os
import json
from datetime import datetime, timedelta

BASE_DIR = r'D:\mystock'
NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_stocks_from_logs(days=30):
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
                    'response': response,
                    'theme': data.get('theme', ''),
                    'prompt': data.get('prompt', '')[:500]  # 只保留前500字符
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

def generate_report(stocks):
    """生成报告（包含AI分析详情）"""
    report = f"""# 日志个股AI分析汇总

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 概览

本次汇总 {len(stocks)} 只个股，均为最近30天 AI 分析日志中提到的股票。

按情绪评分从高到低排序。

## 个股详情

"""
    
    for i, stock in enumerate(stocks, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        response = stock.get('response', '')
        theme = stock.get('theme', '')
        
        report += f"### {i}. {name} ({ts_code}) - 评分: {score}\n\n"
        
        if theme:
            report += f"**主题:** {theme}\n\n"
        
        if response:
            report += f"**AI分析:**\n\n{response}\n\n"
        
        report += "---\n\n"
    
    return report

def main():
    print('=' * 70)
    print('每日分析日志中提到个股')
    print('=' * 70)
    print()
    
    stocks = extract_stocks_from_logs(days=30)
    
    if not stocks:
        print('无数据')
        return
    
    print()
    print(f'将生成报告 ({len(stocks)} 个股)')
    print()
    
    # 生成报告
    report = generate_report(stocks)
    
    # 保存
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_summary_{timestamp}.md')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'报告已保存: {report_path}')
    print()
    print('完成！')
    
    return report_path

if __name__ == '__main__':
    main()
