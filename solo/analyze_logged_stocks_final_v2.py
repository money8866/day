"""
日志个股AI分析汇总 - 最终版（不含公告）

功能：
1. 分析最近N天AI分析日志中提到的个股
2. 提取评分并排序
3. 生成汇总报告（含AI分析摘要）
4. 推送微信

注：公告获取功能待Tushare接口修复后添加
"""
import os
import json
from datetime import datetime, timedelta

BASE_DIR = r'D:\mystock'
NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_stocks_from_logs(days=30):
    """从日志中提取个股（默认30天）"""
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
                    'theme': data.get('theme', '')
                })
        except Exception as e:
            print(f'处理失败: {e}')
    
    print(f'提取到 {len(stocks)} 个股')
    
    # 去重（保留最新日期）
    seen = {}
    for stock in stocks:
        code = stock['ts_code']
        if code not in seen or stock['date'] > seen[code]['date']:
            seen[code] = stock
    
    stocks = list(seen.values())
    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    print(f'去重后: {len(stocks)} 个股')
    return stocks

def generate_report(stocks, top_n=20):
    """生成报告（Top N）"""
    report = f"""# 日志个股AI分析汇总 - Top {top_n}

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 概览

本次汇总最近7天AI分析日志中，情绪评分最高的 {min(top_n, len(stocks))} 只个股。

## Top {top_n} 个股

"""
    
    for i, stock in enumerate(stocks[:top_n], 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        response = stock.get('response', '')
        theme = stock.get('theme', '')
        
        # 提取AI分析第一行（摘要）
        summary = ''
        if response:
            summary = response.strip().split('\n')[0]
            if len(summary) > 150:
                summary = summary[:150] + '...'
        
        report += f"{i}. **{name}** ({ts_code}) - 评分: **{score}**\n"
        if theme:
            report += f"   - 主题: {theme}\n"
        if summary:
            report += f"   - 摘要: {summary}\n"
        report += "\n"
    
    report += "\n## 详细说明\n\n"
    
    for i, stock in enumerate(stocks[:top_n], 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        response = stock.get('response', '')
        
        report += f"### {i}. {name} ({ts_code}) - 评分: {score}\n\n"
        
        if response:
            report += f"**AI分析:**\n\n{response}\n\n"
        
        report += "---\n\n"
    
    # 添加说明
    report += f"""
## 说明

1. **数据来源**: `D:\\mystock\\news_cache\\ai_analysis_*.json`
2. **评分提取**: AI分析最后一行数字（0-100）
3. **排序规则**: 按评分从高到低
4. **公告功能**: 待Tushare接口修复后添加

## 后续优化

- [ ] 修复Tushare公告接口调用
- [ ] 添加东方财富网公告爬虫
- [ ] 添加个股最新新闻
- [ ] 定时自动推送（每日7:00）

"""
    
    return report

def main():
    print('=' * 70)
    print('日志个股AI分析汇总 - 最终版')
    print('=' * 70)
    print()
    
    # 1. 提取个股
    stocks = extract_stocks_from_logs(days=7)
    
    if not stocks:
        print('无数据')
        return
    
    print()
    print(f'将生成Top 20报告 ({len(stocks)} 个股)')
    print()
    
    # 2. 生成报告
    report = generate_report(stocks, top_n=20)
    
    # 3. 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_top20_{timestamp}.md')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'报告已保存: {report_path}')
    print()
    
    # 4. 也保存一个固定名称（方便定时任务）
    latest_path = os.path.join(OUTPUT_DIR, 'logged_stocks_top20_latest.md')
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'最新报告: {latest_path}')
    print()
    print('=' * 70)
    print('完成！')
    print('=' * 70)
    print()
    print('📌 注意: 公告获取功能待完善')
    print('   当前版本仅汇总AI分析摘要')
    
    return report_path

if __name__ == '__main__':
    main()
