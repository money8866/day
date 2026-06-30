import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import json
from pathlib import Path
from datetime import datetime, timedelta
from data_fetcher import DataFetcher
from trend_picker import trend_scan, to_dataframe, get_daily_data, get_moneyflow_data, get_daily_basic, score_fundamental, score_capital, score_technical

def test_sddq():
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            token = _l.strip().split('=', 1)[1].strip().strip('"')
            break
    config = {
        'tushare': {'token': token, 'max_retry': 3, 'retry_delay': 5},
        'cache': {'enabled': True, 'dir': 'cache', 'expire_hours': 24}
    }
    fetcher = DataFetcher(token, config)
    
    test_stocks = [
        {'ts_code': '688187.SH', 'name': '时代电气', 'industry': '半导体'},
    ]
    
    import pandas as pd
    stocks_df = pd.DataFrame(test_stocks)
    
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
    
    print(f"=== 时代电气趋势选股分析 ===")
    print(f"日期范围: {start_date} ~ {end_date}\n")
    
    results = trend_scan(fetcher, stocks_df, start_date, end_date)
    
    if len(results) == 0:
        print("时代电气未通过趋势选股筛选（总分<7分）")
        print("\n=== 详细分析 ===")
        ts_code = '688187.SH'
        daily = get_daily_data(fetcher, ts_code, start_date, end_date)
        moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
        daily_basic = get_daily_basic(fetcher, ts_code, end_date)
        income = fetcher.get_income(ts_code)
        
        fund_score, fund_detail = score_fundamental(fetcher, ts_code, '半导体', income, daily_basic)
        cap_score, cap_detail = score_capital(fetcher, ts_code, moneyflow, daily)
        tech_score, tech_detail = score_technical(daily)
        
        total_score = fund_score + cap_score + tech_score
        
        print(f"\n总分: {total_score:.1f}/18")
        print(f"基本面: {fund_score:.2f}/7.2")
        print(f"资金面: {cap_score:.2f}/8.1")
        print(f"技术面: {tech_score:.2f}/2.7")
        
        print("\n=== 因子明细 ===")
        
        print("\n【基本面因子（40%）】")
        factors = ['F1赛道属性', 'F2业绩拐点', 'F3市值区间']
        for i, (name, detail) in enumerate([('F1', fund_detail.get('F1', {})), ('F2', fund_detail.get('F2', {})), ('F3', fund_detail.get('F3', {}))], 1):
            print(f"\nF{i} {factors[i-1]}:")
            print(f"  原始分: {detail.get('score', 0):.1f}/2")
            for k, v in detail.items():
                if k != 'score':
                    print(f"  {k}: {v}")
        
        print("\n【资金面因子（45%）】")
        factors = ['F4机构持仓', 'F5资金流向', 'F6换手率']
        for i, (name, detail) in enumerate([('F4', cap_detail.get('F4', {})), ('F5', cap_detail.get('F5', {})), ('F6', cap_detail.get('F6', {}))], 4):
            print(f"\nF{i} {factors[i-4]}:")
            print(f"  原始分: {detail.get('score', 0):.1f}/2")
            for k, v in detail.items():
                if k != 'score':
                    print(f"  {k}: {v}")
        
        print("\n【技术面因子（15%）】")
        factors = ['F7均线系统', 'F8成交量', 'F9技术指标']
        for i, (name, detail) in enumerate([('F7', tech_detail.get('F7', {})), ('F8', tech_detail.get('F8', {})), ('F9', tech_detail.get('F9', {}))], 7):
            print(f"\nF{i} {factors[i-7]}:")
            print(f"  原始分: {detail.get('score', 0):.1f}/2")
            for k, v in detail.items():
                if k != 'score':
                    print(f"  {k}: {v}")
        
        print("\n=== 最新价格数据 ===")
        if len(daily) > 0:
            latest = daily.iloc[-1]
            print(f"收盘价: {latest['close']:.2f}")
            print(f"涨跌幅: {latest.get('pct_chg', 0):.2f}%")
            print(f"换手率: {latest.get('turnover_rate', 0):.2f}%")
            
            if 'ma5' in daily.columns and 'ma10' in daily.columns and 'ma20' in daily.columns:
                ma5 = daily['ma5'].iloc[-1]
                ma10 = daily['ma10'].iloc[-1]
                ma20 = daily['ma20'].iloc[-1]
                print(f"MA5: {ma5:.2f}, MA10: {ma10:.2f}, MA20: {ma20:.2f}")
                print(f"均线状态: {'多头排列' if ma5 > ma10 > ma20 else '空头排列' if ma5 < ma10 < ma20 else '混乱'}")
    else:
        df = to_dataframe(results)
        
        print("\n" + "="*80)
        print(f"{'股票':<10s} {'总分':<8s} {'趋势':<10s} {'买点':<5s} {'止损价':<8s} {'基本面':<8s} {'资金面':<8s} {'技术面':<8s}")
        print("="*80)
        
        for _, row in df.iterrows():
            print(f"{row['name']:<10s} {row['总分']:<8.1f} {row['趋势强度']:<10s} {row['买点']:<5s} "
                  f"{row['止损价']:<8.2f} {row['基本面分']:<8.1f} {row['资金面分']:<8.1f} {row['技术面分']:<8.1f}")
        
        print("="*80)
        
        result = results[0]
        print("\n=== 因子明细 ===")
        for factor_name, factor in result.factors.items():
            print(f"\n{factor_name} ({factor.weight*100:.0f}%):")
            print(f"  原始分: {factor.raw_score:.1f}/2")
            print(f"  加权分: {factor.weighted_score:.2f}")
            if factor.detail:
                for k, v in factor.detail.items():
                    print(f"  {k}: {v}")

if __name__ == '__main__':
    test_sddq()
