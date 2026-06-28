# -*- coding: utf-8 -*-
"""测试版：基本面信息挖掘 - 只扫描10只股票"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
import time

# Tushare token - 从.env文件读取
import os
ENV_PATH = r'D:\mystock\config\.env'
TUSHARE_TOKEN = None

if os.path.exists(ENV_PATH):
    with open(ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('TUSHARE_TOKEN='):
                TUSHARE_TOKEN = line.strip().split('=', 1)[1]
                break

if not TUSHARE_TOKEN:
    print('错误：无法读取Tushare token！')
    print('请检查 .env 文件')
    sys.exit(1)

print(f'Tushare token加载成功：{TUSHARE_TOKEN[:10]}...')

print('='*80)
print('测试版：基本面信息挖掘')
print('='*80)

# 初始化Tushare
pro = ts.pro_api(TUSHARE_TOKEN)

# 测试参数
KEYWORDS = {
    '新订单': ['中标', '合同', '订单', '供货', '签约'],
    '新产品': ['新产品', '发布', '量产', '下线'],
    '新项目': ['投资', '项目', '建设', '扩产'],
    '技术突破': ['专利', '认证', '突破'],
}

def test_mine_announcements(ts_code, days=7):
    """测试：挖掘单只股票的公告"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    print(f'\n扫描 {ts_code}...')
    print(f'  时间范围: {start_date} ~ {end_date}')
    
    try:
        df = pro.anns(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and not df.empty:
            print(f'  找到 {len(df)} 条公告')
            
            results = []
            for idx, row in df.iterrows():
                title = row.get('title', '')
                ann_date = row.get('ann_date', '')
                
                # 关键词匹配
                for category, keywords in KEYWORDS.items():
                    if any(kw in title for kw in keywords):
                        results.append({
                            'ts_code': ts_code,
                            'title': title,
                            'category': category,
                            'ann_date': ann_date,
                        })
                        print(f'    ✓ 发现: {title[:40]}...')
                        break
            
            return results
        else:
            print(f'  未找到公告')
            return []
            
    except Exception as e:
        print(f'  错误: {str(e)}')
        return []

# 主函数
def main():
    # 读取合格股池（前10只）
    print('\n[1/3] 读取合格股池（前10只）...')
    qualified_pool = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
    test_codes = qualified_pool['ts_code'].head(10).tolist()
    print(f'测试股票数：{len(test_codes)}只')
    
    # 扫描公告
    print('\n[2/3] 开始扫描公告...')
    all_results = []
    
    for i, ts_code in enumerate(test_codes, 1):
        print(f'\n[{i}/{len(test_codes)}] ', end='')
        results = test_mine_announcements(ts_code, days=7)
        if results:
            all_results.extend(results)
        
        time.sleep(0.1)  # API限速
    
    print(f'\n\n[3/3] 扫描完成！')
    print(f'共发现 {len(all_results)} 条重要信息')
    
    if all_results:
        # 转换为DataFrame
        df_results = pd.DataFrame(all_results)
        print('\n发现的重要信息：')
        print('-'*80)
        for i, (idx, row) in enumerate(df_results.iterrows(), 1):
            print(f"{i}. {row['ts_code']} - {row['category']}")
            print(f"   {row['title'][:50]}...")
            print(f"   公告日期: {row['ann_date']}")
        
        # 保存结果
        output_path = r'D:\mystock\solo\multi_factor_picker\output\basic_info_test_10stocks.csv'
        df_results.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f'\n结果已保存: {output_path}')
    else:
        print('\n未发现重要信息')
        print('建议：')
        print('  1. 检查Tushare token是否有效')
        print('  2. 扩大时间范围（当前7天）')
        print('  3. 增加关键词')

if __name__ == '__main__':
    main()
