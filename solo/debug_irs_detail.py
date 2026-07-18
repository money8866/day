"""查看IRS评分细节"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

targets = ["001872.SZ", "601825.SH"]

for code in targets:
    name = stock_basic.loc[stock_basic['ts_code'] == code, 'name'].values[0]
    print(f"\n{'='*60}")
    print(f"股票: {code} {name}")
    print('='*60)
    
    for theme_name in ['银行', '交通运输物流']:
        theme = themes.get(theme_name, {})
        result = theme_ts.compute_irs_score(code, theme, dc_df, stock_basic)
        print(f"\n【{theme_name}】评分: {result['score']:.1f}")
        print(f"  mainbiz: {result['detail']['mainbiz']}")
        print(f"  chain: {result['detail']['chain']}")
        print(f"  keyword: {result['detail']['keyword']}")
        print(f"  industry: {result['detail']['industry']}")
        print(f"  source: {result.get('source', '')}")
        print(f"  industry_match: {result.get('industry_match', False)}")
        print(f"  concept_overlap: {result.get('concept_overlap', 0)}")
        print(f"  keywords_hit: {result.get('keywords_hit', [])}")
        print(f"  exclude_hit: {result.get('exclude_hit', False)}")