"""
快速诊断工具 - 用少量样本检查:
1) 财务数据是否正常拉取
2) 各因子是否能正确判定
3) 阈值是否合理(可动态调整)
"""
import os
import sys
import time
import yaml
import pandas as pd
from datetime import datetime
from pathlib import Path

# 导入本地模块
sys.path.insert(0, str(Path(__file__).parent))
from main import load_config, get_token
from data_fetcher import DataFetcher
from factor_checker import FactorChecker, StockFactorData
from main import extract_factor_data, collect_stock_financial_data


def debug_single_stock(fetcher, checker, ts_code, name, industry, daily, moneyflow, industry_growth_map):
    """诊断单只股票"""
    print(f"\n{'='*60}")
    print(f"股票: {ts_code} {name} ({industry})")
    print(f"{'='*60}")

    # 拉取财务数据
    list_date = "20100101"
    financial_data = collect_stock_financial_data(fetcher, ts_code, list_date)

    income = financial_data.get('income', pd.DataFrame())
    balance = financial_data.get('balance', pd.DataFrame())
    forecast = financial_data.get('forecast', pd.DataFrame())

    print(f"  income 行数: {len(income)}")
    print(f"  balance 行数: {len(balance)}")
    print(f"  forecast 行数: {len(forecast)}")

    if len(income) > 0:
        # 查看字段
        print(f"  income 字段: {list(income.columns[:10])}")
        # 最新一期
        latest = income.iloc[0]
        print(f"  最新: ann_date={latest.get('ann_date','?')}, end_date={latest.get('end_date','?')}")
        print(f"  basic_eps: {latest.get('basic_eps','?')}")
        print(f"  revenue: {latest.get('revenue','?')}")
        print(f"  n_income: {latest.get('n_income','?')}")

    if len(forecast) > 0:
        print(f"  forecast 示例: {forecast.head(3)}")

    # 提取因子数据
    try:
        data = extract_factor_data(fetcher,
                                    pd.Series({'ts_code': ts_code, 'name': name, 'industry': industry}),
                                    financial_data, daily, moneyflow, industry_growth_map)
        print(f"\n  因子输入:")
        print(f"    ROE: {data.roe_current}, 历史: {data.roe_history}")
        print(f"    毛利率: {data.gross_margin}")
        print(f"    研发费用率: {data.rd_expense_ratio}")
        print(f"    行业增速: {data.industry_growth}")
        print(f"    产能利用率: {data.capacity_utilization}")
        print(f"    价格信号: {data.price_increase_signal}")
        print(f"    净利润环比: {data.quarterly_net_profit}, 上期: {data.quarterly_net_profit_prev}")
        print(f"    业绩预告类型: {data.forecast_type}, 预告内容: {data.forecast_details}")
        print(f"    北向资金变动: {data.north_bound_ratio_change}")
        print(f"    北向单日净买入: {data.north_bound_daily_net}")
    except Exception as e:
        print(f"  ❌ 因子提取异常: {e}")
        import traceback
        traceback.print_exc()
        return

    # 因子判定
    results = checker.check_all_factors(data)
    print(f"\n  因子判定:")
    for name, res in results.items():
        status = "✅ 通过" if res.passed else "❌ 未过"
        reason = f" - {res.reason}" if res.reason else ""
        detail = f" (details: {res.details})" if res.details else ""
        print(f"    {name:<15}{status}{reason}{detail}")

    print(f"\n  全部通过: {'✅ YES' if checker.all_passed(results) else '❌ NO'}")


def main():
    config = load_config()
    token = get_token(config)

    fetcher = DataFetcher(token, config)
    checker = FactorChecker(config)

    # 取少量股票做样本
    print("加载股票列表...")
    stocks = fetcher.get_stock_list(list_status='L')
    print(f"总股票数: {len(stocks)}")

    # 取日线和北向
    trade_date = fetcher.get_last_trade_date()
    daily = fetcher.get_daily(trade_date)
    moneyflow = fetcher.get_moneyflow(trade_date)
    print(f"交易日: {trade_date}, 日线行数: {len(daily) if daily is not None else 0}, 北向行数: {len(moneyflow) if moneyflow is not None else 0}")

    # 行业增速
    from main import calculate_industry_growth_map
    industry_growth_map = calculate_industry_growth_map(fetcher, stocks)
    print(f"行业增速示例: {dict(list(industry_growth_map.items())[:3])}")

    # 打印当前因子阈值
    print("\n=== 当前因子阈值 ===")
    thresholds = config.get('factors', {})
    for k, v in thresholds.items():
        print(f"  {k}: {v}")

    # 选10只不同行业的股票测试
    sample = stocks.head(10)
    for _, row in sample.iterrows():
        debug_single_stock(fetcher, checker, row['ts_code'], row['name'], row.get('industry', ''),
                          daily, moneyflow, industry_growth_map)
        time.sleep(0.2)


if __name__ == '__main__':
    main()
