import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import load_config, get_token, prepare_stock_data, extract_bull_data, bull_scan
from data_fetcher import DataFetcher
from bull_scorer import BullScorer

def debug_stock(ts_code):
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
    
    from main import calculate_industry_growth_map
    
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
    
    print(f"\n--- 核心财务数据 ---")
    print(f"  营收: {bull_data.revenue/1e8:.2f}亿")
    print(f"  净利润: {bull_data.net_profit/1e8:.2f}亿")
    print(f"  ROE: {bull_data.roe_current*100:.2f}%")
    print(f"  毛利率: {bull_data.gross_margin*100:.2f}%")
    print(f"  研发费用率: {bull_data.rd_expense_ratio*100:.2f}%")
    print(f"  营收同比: {bull_data.revenue_yoy*100:.2f}%")
    print(f"  净利润同比: {bull_data.profit_yoy*100:.2f}%")
    print(f"  合同负债同比: {bull_data.contract_liability_yoy*100:.2f}%")
    
    print(f"\n--- 机构持仓数据 ---")
    print(f"  北向持股比例: {bull_data.north_bound_holding_ratio:.2f}%")
    print(f"  外资持股比例: {bull_data.foreign_holding_ratio:.2f}%")
    print(f"  公募持仓比例: {bull_data.fund_holding_ratio:.2f}%")
    print(f"  公募持仓变化: {bull_data.fund_ratio_change:.2f}%")
    print(f"  持有基金数: {bull_data.fund_count}")
    print(f"  分析师覆盖数: {bull_data.analyst_count}")
    print(f"  买入评级占比: {bull_data.buy_ratio*100:.2f}%")
    
    print(f"\n--- 筹码面数据 ---")
    print(f"  股东人数变化: {bull_data.holder_num_change_ratio*100:.2f}%")
    print(f"  股东增减持: {bull_data.holder_trade_ratio*100:.4f}%")
    print(f"  净增持: {bull_data.holder_trade_netbuy}")
    print(f"  回购金额: {bull_data.repurchase_amount/1e4:.2f}万元")
    print(f"  有回购: {bull_data.has_repurchase}")
    
    print(f"\n--- 数据完整度 ---")
    print(f"  完整度: {bull_data.data_completeness:.1f}%")
    print(f"  缺失标记: {bull_data.data_missing_flags}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("用法: python debug_bull_single.py <ts_code>")
        sys.exit(1)
    debug_stock(sys.argv[1])