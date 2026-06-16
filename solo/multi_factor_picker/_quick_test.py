"""快速验证: 用真实Tushare数据测试3只样本股的四因子判定"""
import sys, time, yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from main import load_config, get_token
from data_fetcher import DataFetcher
from factor_checker import FactorChecker, StockFactorData
from main import extract_factor_data

config = load_config()
token = get_token(config)
fetcher = DataFetcher(token, config)
checker = FactorChecker(config)

# 测试股票
test_codes = ['600519.SH', '000001.SZ', '300750.SZ']
stock_names = {'600519.SH': '贵州茅台', '000001.SZ': '平安银行', '300750.SZ': '宁德时代'}
stock_industries = {'600519.SH': '白酒', '000001.SZ': '银行', '300750.SZ': '电池'}

# 获取日线和资金流
trade_date = fetcher.get_last_trade_date()
daily = fetcher.get_daily(trade_date)
moneyflow = fetcher.get_moneyflow(trade_date)
print(f'交易日: {trade_date}, 日线行数: {len(daily) if daily is not None else 0}, 资金流行数: {len(moneyflow) if moneyflow is not None else 0}')

# 构造简单行业增速映射(用daily平均涨幅)
industry_growth_map = {}
if daily is not None and len(daily) > 0:
    # 取前100只股票大致估算行业增速
    for ind in stock_industries.values():
        industry_growth_map[ind] = 0.05  # 给个默认5%

for code in test_codes:
    print('\n' + '=' * 80)
    print(f'{code} {stock_names[code]} ({stock_industries[code]})')
    print('=' * 80)
    
    # 拉取财务数据
    start_year = str(int(trade_date[:4]) - 3)
    income = fetcher.get_income(code, start_year=start_year)
    balance = fetcher.get_balance_sheet(code, start_year=start_year)
    forecast = fetcher.get_forecast(code)
    time.sleep(0.15)
    
    print(f'income: {len(income)} 行, balance: {len(balance)} 行, forecast: {len(forecast)} 行')
    if len(income) > 0:
        latest = income.sort_values('end_date', ascending=False).iloc[0]
        print(f'最新报告期: {latest.get("end_date")}, revenue={latest.get("revenue"):,.0f}, n_income={latest.get("n_income"):,.0f}')
        print(f'rd_exp={latest.get("rd_exp")}, total_cogs={latest.get("total_cogs")}')
    
    # 提取因子
    row = {'ts_code': code, 'name': stock_names[code], 'industry': stock_industries[code]}
    import pandas as pd
    financial_data = {'income': income, 'balance': balance, 'forecast': forecast}
    data = extract_factor_data(fetcher, pd.Series(row), financial_data, daily, moneyflow, industry_growth_map)
    
    print(f'\n  ROE: {data.roe_current:.4f} (需>0.15), 历史: {[round(x,4) for x in data.roe_history[:5]]}')
    print(f'  毛利率: {data.gross_margin:.4f} (需>0.30)')
    print(f'  研发费用率: {data.rd_expense_ratio:.4f} (需>0.05)')
    print(f'  行业增速: {data.industry_growth:.4f}')
    print(f'  产能利用率: {data.capacity_utilization:.4f}')
    print(f'  净利润(本期): {data.quarterly_net_profit:,.0f}, 上期: {data.quarterly_net_profit_prev:,.0f}')
    if data.quarterly_net_profit_prev > 0:
        print(f'  净利润同比: {(data.quarterly_net_profit - data.quarterly_net_profit_prev) / data.quarterly_net_profit_prev * 100:.1f}%')
    print(f'  预告类型: {data.forecast_type}, 预告变动: {data.forecast_profit_change:.1f}%')
    print(f'  单日净买入: {data.north_bound_daily_net:,.0f} 元 (需>1亿)')
    print(f'  5日净买入变化: {data.north_bound_ratio_change:,.0f} 元')
    
    # 四因子检查
    results = checker.check_all_factors(data)
    print(f'\n  四因子判定:')
    all_pass = True
    for name, res in results.items():
        status = '✅ 通过' if res.passed else '❌ 未过'
        detail = f' ({res.reason})' if res.reason else ''
        print(f'    {name:<15}{status}{detail}')
        if not res.passed:
            all_pass = False
    print(f'\n  综合判定: {"✅ 满足全部四因子" if all_pass else "❌ 未满足"}')

print('\n=== 测试完成 ===')
