"""
获取Final_Self_20260701.md中所有股票的最新公告和资讯
"""
import os
import json
import tushare as ts
from datetime import datetime, timedelta

# Tushare token (从MEMORY.md获取，6000积分，2026-06生效)
TS_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

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

def fetch_announcements(ts_code, start_date='20260701'):
    """获取个股公告"""
    try:
        # 使用正确的Tushare接口调用方式
        df = pro.query('anns', ts_code=ts_code, start_date=start_date, 
                      fields='ts_code,ann_date,ann_type,title,href')
        if df is not None and not df.empty:
            return df.to_dict('records')
    except Exception as e:
        print(f'公告获取失败 {ts_code}: {e}')
    return []

def fetch_news(ts_code, start_date='20260701'):
    """获取个股新闻（使用东方财富接口）"""
    # 注：Tushare没有通用新闻接口，这里用公告代替
    # 实际应该调用东方财富/同花顺接口
    return []

def main():
    print('=' * 70)
    print('股票公告和资讯汇总')
    print('=' * 70)
    print()
    
    results = []
    
    for ts_code, name in STOCKS:
        print(f'处理: {name} ({ts_code})')
        
        # 获取公告
        announcements = fetch_announcements(ts_code)
        
        results.append({
            'ts_code': ts_code,
            'name': name,
            'announcements': announcements,
            'news': []
        })
        
        if announcements:
            print(f'  找到 {len(announcements)} 条公告')
        else:
            print(f'  无公告')
    
    # 保存结果
    output_file = r'D:\mystock\report_daily\stock_announcements_20260701.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print()
    print('=' * 70)
    print(f'结果已保存: {output_file}')
    print('=' * 70)
    
    return output_file

if __name__ == '__main__':
    main()
