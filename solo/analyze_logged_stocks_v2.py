"""
每日分析日志中提到个股的最新公告和资讯 - 简化版
"""
import os
import json
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
    print(f'扫描 {NEWS_CACHE_DIR} 最近 {days} 天的日志...')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        print('日志目录不存在!')
        return []
    
    # 获取所有日志文件
    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
    print(f'找到 {len(files)} 个日志文件')
    
    # 过滤最近N天的
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    stocks = []
    for filename in files:
        # 提取日期
        try:
            date_str = filename.split('_')[-1].replace('.json', '')
            if date_str >= cutoff_date:
                # 读取文件
                filepath = os.path.join(NEWS_CACHE_DIR, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                code = data.get('code', '')
                name = data.get('name', '')
                response = data.get('response', '')
                
                # 提取评分
                import re
                found = re.search(r'(\d{1,3})', response.strip().split('\n')[-1])
                score = int(found.group(1)) if found else 50
                
                stocks.append({
                    'ts_code': code,
                    'name': name,
                    'date': date_str,
                    'score': score
                })
        except Exception as e:
            print(f'  处理 {filename} 失败: {e}')
    
    print(f'提取到 {len(stocks)} 个股')
    return stocks

def get_announcements(pro, ts_code):
    """获取最新公告"""
    try:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        df = pro.anns(ts_code=ts_code, start_date=start_date,
                     end_date=datetime.now().strftime('%Y%m%d'))
        
        if df is not None and len(df) > 0:
            return df.head(10).to_dict('records')
    except:
        pass
    return []

def main():
    print('=' * 70)
    print('分析日志个股的最新公告和资讯')
    print('=' * 70)
    print()
    
    # 1. 提取个股
    stocks = extract_stocks_from_logs(days=7)
    
    if not stocks:
        print('无数据')
        return
    
    # 2. 连接Tushare
    try:
        pro = get_tushare_pro()
        print('Tushare连接成功')
    except Exception as e:
        print(f'Tushare连接失败: {e}')
        return
    
    # 3. 获取公告
    print()
    print(f'获取 {len(stocks)} 只个股的公告...')
    
    results = []
    for i, stock in enumerate(stocks):
        code = stock['ts_code']
        name = stock['name']
        
        print(f'[{i+1}/{len(stocks)}] {name} ({code})...')
        
        announcements = get_announcements(pro, code)
        
        results.append({
            'ts_code': code,
            'name': name,
            'score': stock['score'],
            'date': stock['date'],
            'announcements': announcements
        })
        
        import time
        time.sleep(0.1)
    
    # 4. 生成报告
    print()
    print('生成报告...')
    
    report = f"""# 日志个股最新公告分析

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

"""
    
    for i, r in enumerate(results, 1):
        report += f"## {i}. {r['name']} ({r['ts_code']}) - 评分: {r['score']}\n\n"
        
        if r['announcements']:
            report += "**公告:**\n\n"
            for ann in r['announcements'][:5]:
                report += f"- {ann.get('datetime', '')[:10]} {ann.get('title', '')}\n"
            report += "\n"
        else:
            report += "**公告:** 无\n\n"
    
    # 保存
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = os.path.join(OUTPUT_DIR, f'logged_stocks_{timestamp}.md')
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'报告已保存: {output_path}')
    print()
    print('完成！')

if __name__ == '__main__':
    main()
