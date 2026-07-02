"""
批量抓取Final_Self_20260701.md中股票的最新公告 - 巨潮网版本
"""
import os
import sys
import json
from datetime import datetime

# 添加路径
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from basic_info_juchao_web import JuchaoWebCrawler

# 从Final_Self文件中提取的股票列表
STOCKS = [
    # 强势股票池 Top 10
    ('000776.SZ', '广发证券'),
    ('600877.SH', '电科芯片'),
    ('300497.SZ', '富祥股份'),
    ('688249.SH', '晶合集成'),
    ('603127.SH', '昭衍新药'),
    ('600160.SH', '巨化股份'),
    ('600961.SH', '株冶集团'),
    ('300059.SZ', '东方财富'),
    ('002925.SZ', '盈趣科技'),
    ('688710.SH', '益诺思'),
    
    # 低吸股票池
    ('605589.SH', '圣泉集团'),
    ('603256.SH', '宏和科技'),
    ('002008.SZ', '大族激光'),
    ('688167.SH', '炬光科技'),
    ('688312.SH', '燕麦科技'),
    ('688700.SH', '东威科技'),
    
    # 中线股池
    ('603906.SH', '龙蟠科技'),
    ('605060.SH', '联德股份'),
    
    # 量能爆发池
    ('300715.SZ', '凯伦股份'),
    ('688508.SH', '芯朋微'),
    ('002979.SZ', '雷赛智能'),
    ('301603.SZ', '乔锋智能'),
    ('300657.SZ', '弘信电子'),
    
    # 主题中军
    ('002409.SZ', '雅克科技'),
    ('688126.SH', '沪硅产业'),
    ('688981.SH', '中芯国际'),
    ('688008.SH', '澜起科技'),
    ('300223.SZ', '北京君正'),
    ('600378.SH', '昊华科技'),
    ('603259.SH', '药明康德'),
    ('600276.SH', '恒瑞医药'),
    ('600030.SH', '中信证券'),
    ('601688.SH', '华泰证券'),
    ('601336.SH', '新华保险'),
    ('601628.SH', '中国人寿'),
]

def main():
    print('=' * 70)
    print('批量抓取股票公告 - 巨潮网')
    print('=' * 70)
    print()
    
    crawler = JuchaoWebCrawler()
    results = []
    
    for i, (ts_code, name) in enumerate(STOCKS, 1):
        print(f'[{i}/{len(STOCKS)}] 处理: {name} ({ts_code})')
        
        try:
            # 转换代码格式：000776.SZ -> 000776
            stock_code = ts_code.split('.')[0]
            
            # 抓取公告（使用正确的方法名）
            announcements = crawler.get_announcements(ts_code)
            
            # 分析公告
            positive_count = 0
            negative_count = 0
            
            for ann in announcements:
                title = ann.get('title', '')
                # 简单关键词匹配
                if any(kw in title for kw in ['中标', '合同', '订单', '新产品', '扩产']):
                    positive_count += 1
                elif any(kw in title for kw in ['减持', '诉讼', '处罚', '亏损']):
                    negative_count += 1
            
            results.append({
                'ts_code': ts_code,
                'name': name,
                'announcements': announcements,
                'positive_count': positive_count,
                'negative_count': negative_count,
                'total_count': len(announcements)
            })
            
            print(f'  找到 {len(announcements)} 条公告 (利好:{positive_count}, 利空:{negative_count})')
            
        except Exception as e:
            print(f'  处理失败: {e}')
            results.append({
                'ts_code': ts_code,
                'name': name,
                'announcements': [],
                'error': str(e)
            })
    
    # 保存结果
    output_dir = r'D:\mystock\report_daily'
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    json_file = os.path.join(output_dir, f'stock_announcements_juchao_{timestamp}.json')
    
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print()
    print('=' * 70)
    print(f'完成！')
    print(f'JSON文件: {json_file}')
    print('=' * 70)
    
    # 生成汇总报告
    print()
    print('生成汇总报告...')
    generate_summary_report(results, output_dir, timestamp)
    
    return json_file

def generate_summary_report(results, output_dir, timestamp):
    """生成汇总报告"""
    report_lines = []
    report_lines.append('# 股票公告汇总报告')
    report_lines.append('')
    report_lines.append(f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    report_lines.append('')
    report_lines.append('## 概览')
    report_lines.append('')
    
    total_stocks = len(results)
    total_announcements = sum(r.get('total_count', 0) for r in results)
    total_positive = sum(r.get('positive_count', 0) for r in results)
    total_negative = sum(r.get('negative_count', 0) for r in results)
    
    report_lines.append(f'- **股票数量**: {total_stocks} 只')
    report_lines.append(f'- **公告总数**: {total_announcements} 条')
    report_lines.append(f'- **利好公告**: {total_positive} 条')
    report_lines.append(f'- **利空公告**: {total_negative} 条')
    report_lines.append('')
    report_lines.append('---')
    report_lines.append('')
    
    # 按利好数量排序
    results_sorted = sorted(results, key=lambda x: x.get('positive_count', 0), reverse=True)
    
    report_lines.append('## 个股公告详情（按利好数量排序）')
    report_lines.append('')
    
    for i, stock in enumerate(results_sorted, 1):
        ts_code = stock.get('ts_code', '')
        name = stock.get('name', '')
        pos = stock.get('positive_count', 0)
        neg = stock.get('negative_count', 0)
        total = stock.get('total_count', 0)
        announcements = stock.get('announcements', [])
        
        report_lines.append(f'### {i}. {name} ({ts_code})')
        report_lines.append('')
        report_lines.append(f'- 公告总数: {total}')
        report_lines.append(f'- 利好: {pos} 条')
        report_lines.append(f'- 利空: {neg} 条')
        report_lines.append('')
        
        if announcements:
            report_lines.append('**最新公告:**')
            report_lines.append('')
            for ann in announcements[:5]:  # 只显示前5条
                title = ann.get('title', '')
                date = ann.get('date', '')
                report_lines.append(f'- {date}: {title}')
            report_lines.append('')
        
        report_lines.append('---')
        report_lines.append('')
    
    # 保存报告
    report_content = '\n'.join(report_lines)
    
    # Markdown版本
    md_file = os.path.join(output_dir, f'stock_announcements_summary_{timestamp}.md')
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(report_content)
    print(f'Markdown报告: {md_file}')
    
    # 保存路径到全局变量，供后续PDF转换使用
    global LATEST_REPORT_FILE
    LATEST_REPORT_FILE = md_file
    
    return md_file

if __name__ == '__main__':
    main()
