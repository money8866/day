import json
import os
import sys
sys.path.insert(0, 'd:/mystock/solo')
import tushare as ts
from dotenv import load_dotenv

# 加载环境变量
load_dotenv('d:/mystock/config/.env')
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

# 加载主题配置
with open('d:/mystock/solo/theme.json', 'r', encoding='utf-8') as f:
    theme_cfg = json.load(f).get('HOT_THEMES', {})

# 假设的TOP主题（结合报告来看）
short_top = ['煤炭链', 'AI算力链', '华为鸿蒙', '电力链', '人形机器人']
mid_top = ['AI算力链', '电力链', 'AI终端', '半导体', '低空经济']
valid_themes = set(short_top + mid_top)
print(f"假设 valid_themes: {valid_themes}")
print()

# 收集允许的行业和概念
valid_industries = set()
valid_concepts = set()
for tname in valid_themes:
    cfg = theme_cfg.get(tname, {})
    for ind in cfg.get('industry', []):
        valid_industries.add(ind)
    for conc in cfg.get('concept', []):
        valid_concepts.add(conc)

print(f"valid_industries: {valid_industries}")
print()
print(f"valid_concepts: {valid_concepts}")
print()

# 获取泰和新材的信息
print("=== 查询泰和新材真实数据 ===")
ts_code = '002254.SZ'
basic = pro.stock_basic(ts_code=ts_code, fields='ts_code,name,industry')
stock_basic_industry = basic['industry'].values[0]
print(f"stock_basic_industry: {stock_basic_industry}")

# 获取最新交易日
cal = pro.trade_cal(exchange='', start_date='20260601', end_date='20260605')
trade_dates = cal[cal['is_open'] == 1]['cal_date'].tolist()
trade_date = trade_dates[-1] if trade_dates else '20260603'
print(f"trade_date: {trade_date}")

# 模拟 get_dc_members 并查看泰和新材的所有东财数据
import theme_trend_sentiment_score as theme_ts
dc_df = theme_ts.get_dc_members()
print(f"\n=== 泰和新材在 get_dc_members 中的记录 ===")
th_df = dc_df[dc_df['con_code'] == ts_code]
if th_df.empty:
    print("无记录")
else:
    print(th_df[['ts_code', 'concept_name']].to_string())

# 现在按我们的分类逻辑分类
stock_dc_industries = []
stock_concepts = []
for _, r in th_df.iterrows():
    concept_name = r['concept_name']
    is_industry = ('行业' in concept_name or 'Ⅱ' in concept_name or 'Ⅲ' in concept_name or 'Ⅰ' in concept_name)
    if is_industry:
        stock_dc_industries.append(concept_name)
    else:
        stock_concepts.append(concept_name)

print(f"\n=== 分类结果 ===")
print(f"stock_dc_industries: {stock_dc_industries}")
print(f"stock_concepts: {stock_concepts}")
print()

# 现在进行匹配
hit = False
hit_reason = ''

# 方式1：东财行业
for ind in stock_dc_industries:
    if ind in valid_industries or any(v in ind for v in valid_industries):
        hit = True
        hit_reason = f'方式1: 东财行业 "{ind}" 匹配'
        print(f"✅ {hit_reason}")
        print(f"   验证: ind in valid_industries? {ind in valid_industries}")
        print(f"   验证: any(v in ind for v in valid_industries):")
        for v in valid_industries:
            if v in ind:
                print(f"     '{v}' in '{ind}' = True")
        break

# 方式2：东财概念
if not hit:
    for c in stock_concepts:
        if c in valid_concepts:
            hit = True
            hit_reason = f'方式2: 东财概念 "{c}" 匹配'
            print(f"✅ {hit_reason}")
            break

# 方式3：stock_basic行业
if not hit:
    if stock_basic_industry in valid_industries:
        hit = True
        hit_reason = f'方式3: 交易所行业 "{stock_basic_industry}" 匹配'
        print(f"✅ {hit_reason}")

print(f"\n=== 最终结果 ===")
print(f"hit: {hit}")
if not hit:
    print("❌ 未匹配，不应出现在结果中!")
else:
    print(f"✅ 原因: {hit_reason}")
    
    # 进一步验证
    print("\n=== 验证所有 valid_industries 与 stock_dc_industries 的关系 ===")
    for v in valid_industries:
        for ind in stock_dc_industries:
            if v in ind:
                print(f"'{v}' in '{ind}' → True")
            if ind in v:
                print(f"'{ind}' in '{v}' → True")