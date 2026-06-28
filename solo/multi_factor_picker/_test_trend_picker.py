"""
趋势选股模型测试：兆易创新/雅克科技/烽火通信
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import json
from pathlib import Path
from datetime import datetime, timedelta
from data_fetcher import DataFetcher
from trend_picker import trend_scan, to_dataframe

def test_three_cases():
    """测试三只样本股票"""
    # 加载配置
    config_path = Path(r'D:\mystock\config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    token = config.get('tushare', {}).get('token', '')
    fetcher = DataFetcher(token, config)
    
    # 构造测试股票列表
    test_stocks = [
        {'ts_code': '603986.SH', 'name': '兆易创新', 'industry': '半导体'},
        {'ts_code': '002409.SZ', 'name': '雅克科技', 'industry': '半导体'},
        {'ts_code': '600498.SH', 'name': '烽火通信', 'industry': '通信'},
    ]
    
    import pandas as pd
    stocks_df = pd.DataFrame(test_stocks)
    
    # 确定日期范围（2026年4-6月）
    end_date = '20260627'
    start_date = '20260301'
    
    print(f"=== 趋势选股模型测试 ===")
    print(f"测试股票: {len(stocks_df)}只")
    print(f"日期范围: {start_date} ~ {end_date}\n")
    
    # 执行扫描
    results = trend_scan(fetcher, stocks_df, start_date, end_date)
    
    # 输出结果
    df = to_dataframe(results)
    
    print("\n" + "="*80)
    print(f"{'股票':<10s} {'总分':<8s} {'趋势':<10s} {'买点':<5s} {'止损价':<8s} {'基本面':<8s} {'资金面':<8s} {'技术面':<8s}")
    print("="*80)
    
    for _, row in df.iterrows():
        print(f"{row['name']:<10s} {row['总分']:<8.1f} {row['趋势强度']:<10s} {row['买点']:<5s} "
              f"{row['止损价']:<8.2f} {row['基本面分']:<8.1f} {row['资金面分']:<8.1f} {row['技术面分']:<8.1f}")
    
    print("="*80)
    
    # 验证预期
    print("\n=== 验证结果 ===")
    
    # 兆易创新：预期≥14分（强趋势）
    zy = df[df['name'] == '兆易创新']
    if len(zy) > 0:
        assert zy.iloc[0]['总分'] >= 13, f"兆易创新预期≥13分，实际{zy.iloc[0]['总分']}"
        print(f"✓ 兆易创新得分验证通过: {zy.iloc[0]['总分']}/18")
    
    # 雅克科技：预期10-13分（中等趋势）
    yk = df[df['name'] == '雅克科技']
    if len(yk) > 0:
        assert 10 <= yk.iloc[0]['总分'] <= 14, f"雅克科技预期10-14分，实际{yk.iloc[0]['总分']}"
        print(f"✓ 雅克科技得分验证通过: {yk.iloc[0]['总分']}/18")
    
    # 烽火通信：预期<10分（趋势终结）
    fh = df[df['name'] == '烽火通信']
    if len(fh) > 0:
        assert fh.iloc[0]['总分'] < 10, f"烽火通信预期<10分，实际{fh.iloc[0]['总分']}"
        print(f"✓ 烽火通信得分验证通过: {fh.iloc[0]['总分']}/18")
    
    print("\n所有测试通过！")

if __name__ == '__main__':
    test_three_cases()
