# -*- coding: utf-8 -*-
"""深入分析巨化vs三美各子因子计算过程"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import load_config, extract_bull_data, get_fundamental_data, DataFetcher
from bull_scorer import BullScorer, _percentile_rank, _safe_div
import pandas as pd
from datetime import datetime

config = load_config()
fetcher = DataFetcher(config['token']['tushare'], config)

# 获取全市场股票
print("加载全市场股票...")
ts_code_list, all_stocks = get_fundamental_data(fetcher.pro, config)

# 筛选巨化、三美及同行业对比
juhua = [s for s in all_stocks if '巨化' in s.get('name', '')]
sanmei = [s for s in all_stocks if '三美' in s.get('name', '')]
print(f"找到巨化: {[s['name'] for s in juhua]}")
print(f"找到三美: {[s['name'] for s in sanmei]}")

# 提取因子数据
all_data = extract_bull_data(all_stocks, fetcher, config, datetime.now().strftime('%Y%m%d'))

# 找到巨化和三美的数据
jh = [d for d in all_data if '巨化' in getattr(d, 'name', '')]
sm = [d for d in all_data if '三美' in getattr(d, 'name', '')]
print(f"提取因子数据: 巨化={len(jh)}只, 三美={len(sm)}只")

if jh and sm:
    jh = jh[0]
    sm = sm[0]
    industry = jh.industry
    print(f"\n== 行业: {industry}")
    print(f"巨化股份 营收={jh.revenue/1e8:.1f}亿 净利={jh.net_profit/1e8:.1f}亿 ROE={jh.roe_current*100:.1f}%")
    print(f"三美股份 营收={sm.revenue/1e8:.1f}亿 净利={sm.net_profit/1e8:.1f}亿 ROE={sm.roe_current*100:.1f}%")
    print(f"巨化股份 市值={jh.market_cap/1e8:.1f}亿 毛利率={jh.gross_margin*100:.1f}% 研发率={jh.rd_expense_ratio*100:.1f}%")
    print(f"三美股份 市值={sm.market_cap/1e8:.1f}亿 毛利率={sm.gross_margin*100:.1f}% 研发率={sm.rd_expense_ratio*100:.1f}%")
    print(f"巨化股份 营收同比={jh.revenue_yoy*100:.1f}% 净利同比={jh.profit_yoy*100:.1f}%")
    print(f"三美股份 营收同比={sm.revenue_yoy*100:.1f}% 净利同比={sm.profit_yoy*100:.1f}%")
    print(f"巨化股份 contract_liability={jh.contract_liability_yoy} advance={jh.advance_payment_yoy}")
    print(f"三美股份 contract_liability={sm.contract_liability_yoy} advance={sm.advance_payment_yoy}")
    print(f"巨化股份 cashflow={jh.cashflow_growth*100:.1f}% inventory_turnover={jh.inventory_turnover_change*100:.1f}%")
    print(f"三美股份 cashflow={sm.cashflow_growth*100:.1f}% inventory_turnover={sm.inventory_turnover_change*100:.1f}%")
    print(f"巨化股份 北向净流入={jh.north_bound_daily_net} 持股变化={jh.north_bound_ratio_change}")
    print(f"三美股份 北向净流入={sm.north_bound_daily_net} 持股变化={sm.north_bound_ratio_change}")
    
    # 对比行业分位
    print("\n== 同行业对比 ==")
    industry_peers = [d for d in all_data if d.industry == industry]
    print(f"同行业股票数量: {len(industry_peers)}")
    
    # 各指标对比
    def compare(label, values_jh, values_sm, attr_getter):
        peers_vals = [attr_getter(d) for d in industry_peers]
        jv = attr_getter(jh)
        sv = attr_getter(sm)
        jp = _percentile_rank(pd.Series(peers_vals), jv) * 100
        sp = _percentile_rank(pd.Series(peers_vals), sv) * 100
        print(f"  {label:<18}: 巨化={jv:>10.3f} (行业分位{jp:>5.1f}%)  三美={sv:>10.3f} (行业分位{sp:>5.1f}%)")
    
    compare("合同负债增速", None, None, lambda d: d.contract_liability_yoy)
    compare("营收同比", None, None, lambda d: d.revenue_yoy)
    compare("净利润同比", None, None, lambda d: d.profit_yoy)
    compare("毛利率", None, None, lambda d: d.gross_margin)
    compare("ROE", None, None, lambda d: d.roe_current)
    compare("研发费用率", None, None, lambda d: d.rd_expense_ratio)
    compare("营收规模", None, None, lambda d: d.revenue)
    compare("净利润规模", None, None, lambda d: d.net_profit)
    compare("经营性现金流", None, None, lambda d: d.cashflow_growth)
    compare("北向单日净流入", None, None, lambda d: d.north_bound_daily_net)
    compare("北向持股变化", None, None, lambda d: d.north_bound_ratio_change)
    compare("市值", None, None, lambda d: d.market_cap)
    
    # 计算各子因子详细得分
    print("\n== BullScore 子因子详细得分 ==")
    scorer = BullScorer()
    
    # 构建group_series - 只针对同行业
    from collections import defaultdict
    group_series = defaultdict(pd.Series)
    group_data = {}
    group_data[industry] = industry_peers
    for d in industry_peers:
        ind = d.industry
        if f'revenue_yoy_{ind}' not in group_series or len(group_series[f'revenue_yoy_{ind}']) == 0:
            # 初始化行业group series
            pass
    
    # 直接用评分器
    results = scorer.score(all_data)
    for r in results:
        if '巨化' in r.name or '三美' in r.name:
            print(f"\n{r.name}: Bull={r.bull_score:.1f} Final={r.final_score:.1f} level={r.bull_level}")
            print(f"  产业景气={r.industry_demand_score:.1f} 技术壁垒={r.tech_barrier_score:.1f} 订单爆发={r.order_explosion_score:.1f}")
            print(f"  盈利质量={r.earnings_quality_score:.1f} 龙头地位={r.leader_score:.1f} 预期差={r.expectation_score:.1f}")
            print(f"  机构持仓={r.institution_score:.1f} 市值弹性={r.marketcap_score:.1f} 主题分={r.theme_score:.1f}")
            print(f"  analyst={r.analyst_count} np_growth={r.np_growth_current}% buy={r.buy_ratio}% exp={r.analyst_expectation_score}")
