import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import load_config, get_token, prepare_stock_data, extract_bull_data, calculate_industry_growth_map
from data_fetcher import DataFetcher
from bull_scorer import BullScorer

def debug_stock_score(ts_code):
    config = load_config()
    token = get_token(config)
    fetcher = DataFetcher(token, config)
    
    stocks, daily, moneyflow, daily_basic, north_hold, concept_map = prepare_stock_data(config, fetcher)
    
    stock_row = stocks[stocks['ts_code'] == ts_code]
    if stock_row.empty:
        print(f"未找到股票: {ts_code}")
        return
    
    row = stock_row.iloc[0]
    print(f"\n股票: {ts_code} {row['name']} ({row.get('industry', '')})")
    
    industry_growth_map = calculate_industry_growth_map(fetcher, stocks)
    
    financial_batch = fetcher.get_stock_financial_batch([ts_code], start_year=2019, max_workers=4)
    financial_data = financial_batch.get(ts_code, {})
    
    report_rc_map = fetcher.get_report_rc_batch(stock_list=[ts_code])
    
    chip_data = fetcher.get_chip_margin_batch(ts_code)
    
    main_bz = fetcher.get_fina_mainbz(ts_code)
    
    bull_data = extract_bull_data(row, financial_data, daily, daily_basic, moneyflow, north_hold, industry_growth_map, config, report_rc_map, chip_data, main_bz)
    
    if bull_data is None:
        print("提取BullStockData失败")
        return
    
    scorer = BullScorer(config)
    
    all_scores = scorer.compute_all_scores([bull_data])
    
    for s in all_scores:
        print(f"\n=== BullScore 详细评分 ===")
        print(f"  综合得分: {s.final_score:.2f}")
        print(f"  BullScore: {s.bull_score:.2f}")
        print(f"  主题分: {s.theme_score:.2f}")
        print(f"  等级: {s.bull_level}")
        
        print(f"\n--- 各维度评分 ---")
        print(f"  产业景气(industry_demand): {s.industry_demand_score:.2f}")
        print(f"  技术壁垒(tech_barrier): {s.tech_barrier_score:.2f}")
        print(f"  订单爆发(order_explosion): {s.order_explosion_score:.2f}")
        print(f"  盈利质量(earnings_quality): {s.earnings_quality_score:.2f}")
        print(f"  龙头地位(leader): {s.leader_score:.2f}")
        print(f"  预期差(expectation): {s.expectation_score:.2f}")
        print(f"  机构认可(institution): {s.institution_score:.2f}")
        print(f"  市值弹性(marketcap): {s.marketcap_score:.2f}")
        print(f"  筹码面(chip): {s.chip_score:.2f}")
        print(f"  估值安全(valuation): {s.valuation_score:.2f}")
        
        print(f"\n--- 关键指标 ---")
        print(f"  营收: {s.revenue/1e8:.2f}亿")
        print(f"  净利润: {s.net_profit/1e8:.2f}亿")
        print(f"  ROE: {s.roe*100:.2f}%")
        print(f"  毛利率: {s.gross_margin*100:.2f}%")
        print(f"  研发费用率: {s.rd_expense_ratio*100:.2f}%")
        print(f"  营收同比: {s.revenue_yoy*100:.2f}%")
        print(f"  净利润同比: {s.profit_yoy*100:.2f}%")
        print(f"  合同负债同比: {s.contract_liability_yoy*100:.2f}%")
        print(f"  分析师数量: {s.analyst_count}")
        print(f"  买入评级占比: {s.buy_ratio*100:.2f}%")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("用法: python debug_bull_score.py <ts_code>")
        sys.exit(1)
    debug_stock_score(sys.argv[1])