# -*- coding: utf-8 -*-
"""测试 BullScore v2 在真实数据上的效果 — 修正ts_code"""
import os, sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

from bull_scorer import BullScoreResult
from bull_scorer_v2 import BullScorerV2
import pandas as pd
import time

def fix_ts_code(code_str):
    """数字代码 → Tushare格式: 600xxx.SH, 000xxx/002xxx/300xxx/301xxx.SZ, 688xxx.SH"""
    code_str = str(code_str).strip()
    if '.' in code_str:
        return code_str  # 已有后缀
    ic = int(code_str)
    if ic >= 600000 or ic >= 688000:
        return f"{code_str}.SH"
    elif ic >= 900000:
        return f"{code_str}.SH"  # B股
    else:
        return f"{code_str}.SZ"

df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\bullscore_20260622_142716.csv')
print(f'总股票数: {len(df)}')

results_v1 = []
v1_finals = {}
for _, row in df.head(10).iterrows():
    ts_code_raw = str(row['ts_code'])
    ts_code_full = fix_ts_code(ts_code_raw)
    
    chain_tag = str(row.get('theme', ''))
    v1_final = float(row['final_score'])
    v1_finals[ts_code_full] = v1_final
    
    def f(v, default=0):
        try: return float(str(v).replace('%','').replace(',',''))
        except: return default
    
    r = BullScoreResult(
        ts_code=ts_code_full,
        name=str(row['name']),
        industry=str(row.get('industry', '')),
        chain_tag=chain_tag,
        industry_demand_score=f(row.get('industry_demand_score', 0)),
        tech_barrier_score=f(row.get('tech_barrier_score', 0)),
        order_explosion_score=f(row.get('order_explosion_score', 0)),
        earnings_quality_score=f(row.get('earnings_quality_score', 0)),
        leader_score=f(row.get('leader_score', 0)),
        expectation_score=f(row.get('expectation_score', 0)),
        institution_score=f(row.get('institution_score', 0)),
        marketcap_score=f(row.get('marketcap_score', 0)),
        valuation_score=f(row.get('valuation_score', 0)),
        bull_score=f(row.get('bull_score', 0)),
        theme_score=f(row.get('theme_score', 0)),
        final_score=v1_final,
        revenue=f(row.get('revenue', 0)),
        net_profit=f(row.get('net_profit', 0)),
        roe=f(row.get('roe', 0)),
        gross_margin=f(row.get('gross_margin', 0)),
        rd_expense_ratio=f(row.get('rd_ratio', 0)),
        revenue_yoy=f(row.get('revenue_yoy', 0)),
        profit_yoy=f(row.get('profit_yoy', 0)),
        market_cap=f(row.get('market_cap', 0)),
        sub_details={},
    )
    results_v1.append(r)

print(f'基础评分加载: {len(results_v1)} 只')
for r in results_v1:
    print(f'  {r.name:<8} {r.ts_code:<15} final_v1={r.final_score:.1f} chain={r.chain_tag}')

# 运行 v2 (batch_size=1 避免限频)
scorer_v2 = BullScorerV2()
results_v2 = scorer_v2.batch_compute(results_v1, batch_size=1, delay=1.0)

print(f'\n{"="*90}')
print('BullScore v1 vs v2 对比 (TOP10)')
print(f'{"="*90}')
print(f"{'名称':<8} {'v1最终':>7} {'v2_Bull':>8} {'筹码':>6} {'安全':>6} {'主题v2':>7} {'v2最终':>7} {'值变化':>8}")
print('-'*75)
for r in results_v2:
    v1_f = v1_finals.get(r.ts_code, 0)
    diff = r.final_score - v1_f
    print(f"{r.name:<8} {v1_f:>7.1f} {r.bull_score_v2:>8.1f} {r.chip_score:>6.1f} {r.safety_score:>6.1f} {r.theme_score_v2:>7.1f} {r.final_score:>7.1f} {diff:>+8.1f}")

# 显示筹码面详情
print(f'\n{"="*90}')
print('筹码面详情 (TOP5)')
print(f'{"="*90}')
for r in results_v2[:5]:
    cd = r.sub_details.get('chip', {})
    mf = cd.get('moneyflow', {})
    hd = cd.get('holder', {})
    fd = cd.get('fund', {})
    print(f"  {r.name:<8} 资金流={mf.get('score','N/A'):.0f} 净流入={mf.get('net_inflow_b','N/A')}亿 "
          f"股东数变化={hd.get('change_pct','N/A')}% "
          f"公募持仓={fd.get('change_pct','N/A')}%")

print(f'\n主题详情:')
for r in results_v2[:5]:
    td = r.sub_details.get('theme_v2', {})
    method = 'chain_tag' if td.get('fallback') else 'fina_mainbz'
    matched = td.get('matched_themes', {})
    print(f"  {r.name:<8} 主题={r.theme} 方式={method} 匹配主题={list(matched.keys())[:3]}")
