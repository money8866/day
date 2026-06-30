import sys
import os
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import load_config, get_token, prepare_stock_data, extract_bull_data, calculate_industry_growth_map
from data_fetcher import DataFetcher
from bull_scorer import BullScorer

def debug_plastic_stocks():
    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)
    
    stocks, daily, moneyflow, daily_basic, north_hold, concept_map = prepare_stock_data(config, fetcher)
    
    plastic_stocks = stocks[stocks['industry'] == '塑料']
    print(f"塑料行业股票数: {len(plastic_stocks)}")
    
    ts_codes = list(plastic_stocks['ts_code'])
    
    industry_growth_map = calculate_industry_growth_map(fetcher, stocks)
    
    financial_batch = fetcher.get_stock_financial_batch(ts_codes, start_year=2019, max_workers=8)
    
    report_rc_map = fetcher.get_report_rc_batch(stock_list=ts_codes)
    
    bull_data_list = []
    for _, row in plastic_stocks.iterrows():
        ts_code = row['ts_code']
        financial_data = financial_batch.get(ts_code, {})
        income = financial_data.get('income', pd.DataFrame())
        if len(income) == 0:
            continue
        
        chip_data = fetcher.get_chip_margin_batch(ts_code)
        main_bz = fetcher.get_fina_mainbz(ts_code)
        
        try:
            bull_data = extract_bull_data(row, financial_data, daily, daily_basic, moneyflow, north_hold, industry_growth_map, config, report_rc_map, chip_data, main_bz)
            if bull_data is not None:
                bull_data_list.append(bull_data)
        except Exception as e:
            print(f"提取失败 {ts_code}: {e}")
    
    print(f"有效数据: {len(bull_data_list)}")
    
    scorer = BullScorer(config)
    all_scores = scorer.compute_all_scores(bull_data_list)
    
    print(f"\n塑料行业评分结果:")
    print(f"{'代码':<10} {'名称':<12} {'最终分':>6} {'BullScore':>8} {'主题分':>6} {'产业景气':>6} {'技术壁垒':>6} {'订单爆发':>6} {'盈利质量':>6}")
    print(f"{'='*10} {'='*12} {'='*6} {'='*8} {'='*6} {'='*6} {'='*6} {'='*6} {'='*6}")
    
    for s in sorted(all_scores, key=lambda x: x.final_score, reverse=True):
        print(f"{s.ts_code:<10} {s.name:<12} {s.final_score:>6.2f} {s.bull_score:>8.2f} {s.theme_score:>6.2f} {s.industry_demand_score:>6.2f} {s.tech_barrier_score:>6.2f} {s.order_explosion_score:>6.2f} {s.earnings_quality_score:>6.2f}")

if __name__ == '__main__':
    debug_plastic_stocks()